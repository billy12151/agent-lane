"""Concurrent, resumable workflow state machine."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from contextlib import nullcontext, suppress
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .adapters import AgentAdapter
from .async_utils import WorkerPool, run_sync_with_timeout
from .engine import FlowEngine, dump_flow, parse_flow
from .errors import (
    FlowExecutionError,
    FlowValidationError,
    InvalidResumeError,
    ResolverError,
)
from .hooks import CompositeHook, FlowHook, NoOpHook
from .human_gate import GateDriver, PauseGateDriver
from .observability import CompositeSink, NullSink, ObservabilitySink
from .resolvers import ResolveContext, ResolverRegistry, default_registry
from .result import AgentResult
from .state import (
    FlowDefinition,
    FlowRun,
    FlowStatus,
    GateDecision,
    StepDefinition,
    StepStatus,
    utc_now,
)
from .state_store import InMemoryStateStore, StateStore


@dataclass(slots=True, frozen=True)
class _Control:
    kind: str
    step_id: str
    target: str = ""


class _StepFailed(FlowExecutionError):
    def __init__(self, step_id: str, message: str):
        super().__init__(message)
        self.step_id = step_id


class _SerializedStateStore:
    """Serialize calls to a store that does not declare concurrent safety."""

    concurrent_safe = True

    def __init__(self, inner: StateStore):
        self.inner = inner
        self._lock = threading.RLock()

    def __getattr__(self, name: str) -> Any:
        value = getattr(self.inner, name)
        if not callable(value):
            return value

        def locked(*args: Any, **kwargs: Any) -> Any:
            with self._lock:
                return value(*args, **kwargs)

        return locked


class StepRunner:
    def __init__(
        self,
        *,
        adapter: AgentAdapter,
        store: StateStore | None = None,
        sink: ObservabilitySink | None = None,
        resolver_registry: ResolverRegistry | None = None,
        memory_client: Any = None,
        secret_provider: Any = None,
        gate_driver: GateDriver | None = None,
        hook: FlowHook | None = None,
        cwd: str | Path | None = None,
        resolver_timeout: float = 10,
        memory_timeout: float = 10,
        worker_pool: WorkerPool | None = None,
    ):
        if adapter is None:
            raise ValueError("a real AgentAdapter is required")
        if resolver_timeout <= 0 or memory_timeout <= 0:
            raise ValueError("resolver_timeout and memory_timeout must be positive")
        self.adapter = adapter
        raw_store = store or InMemoryStateStore()
        self.store: StateStore = (
            raw_store if raw_store.concurrent_safe else _SerializedStateStore(raw_store)  # type: ignore[assignment]
        )
        configured_sink = sink or NullSink()
        self.sink = (
            configured_sink
            if isinstance(configured_sink, CompositeSink)
            else CompositeSink([configured_sink])
        )
        self.worker_pool = worker_pool
        self.registry = resolver_registry or default_registry(worker_pool=worker_pool)
        self.memory_client = memory_client
        self.secret_provider = secret_provider
        self.gate_driver = gate_driver or PauseGateDriver()
        configured_hook = hook or NoOpHook()
        self.hook = (
            configured_hook
            if isinstance(configured_hook, CompositeHook)
            else CompositeHook([configured_hook])
        )
        self.cwd = Path(cwd) if cwd is not None else None
        self.resolver_timeout = resolver_timeout
        self.memory_timeout = memory_timeout

    async def run(
        self,
        flow: FlowDefinition | None = None,
        *,
        run_id: str | None = None,
        resume_from: str | None = None,
        original_yaml: str | None = None,
    ) -> FlowRun:
        is_resume = run_id is not None
        if run_id is None:
            if flow is None:
                raise ValueError("flow is required for a new run")
            if resume_from is not None:
                raise InvalidResumeError("resume_from is only valid for an existing run")
            engine = FlowEngine()
            errors = engine.validate(flow) + engine.validate_resolvers(flow, self.registry)
            if errors:
                raise FlowValidationError("; ".join(errors))
            self._validate_required_secrets(flow)
            snapshot = original_yaml or flow.raw_yaml or dump_flow(flow)
            run_id = self.store.create_run(flow.name, [step.id for step in flow.steps], snapshot)
            self.store.set_context(run_id, "memory.workspace", flow.memory_workspace)
            self.store.update_flow_status(run_id, FlowStatus.RUNNING, current_step=None)
        else:
            run = self._require_run(run_id)
            snapshot_flow = parse_flow(run.flow_yaml_snapshot)
            persisted_workspace = run.context.get("memory.workspace")
            if isinstance(persisted_workspace, str) and persisted_workspace:
                snapshot_flow.memory_workspace = persisted_workspace
            if flow is not None and dump_flow(flow) != dump_flow(snapshot_flow):
                raise InvalidResumeError("supplied flow does not match the persisted snapshot")
            flow = snapshot_flow
            if run.status not in {FlowStatus.PAUSED, FlowStatus.RUNNING}:
                raise InvalidResumeError(
                    f"run {run_id} is {run.status.value}; only paused/running runs can resume"
                )
            if run.status == FlowStatus.PAUSED and not run.current_step:
                raise InvalidResumeError(f"run {run_id} is paused but current_step is empty")
            if resume_from is not None:
                self._validate_resume_target(flow, run, resume_from)
            self._validate_required_secrets(flow)
            self.store.update_flow_status(
                run_id,
                FlowStatus.RUNNING,
                current_step=resume_from or run.current_step,
            )

        assert flow is not None
        lease = self.store.run_lease(run_id) if is_resume else nullcontext()
        with lease:
            return await self._execute_loop(flow, run_id)

    async def _execute_loop(self, flow: FlowDefinition, run_id: str) -> FlowRun:
        self.sink.on_flow_start(run_id, flow.name)
        try:
            while True:
                control = await self._run_topology(flow, run_id)
                if control is None:
                    self.store.update_flow_status(run_id, FlowStatus.COMPLETED, current_step=None)
                    self.sink.on_flow_end(run_id, FlowStatus.COMPLETED)
                    return self._require_run(run_id)
                if control.kind == "pause":
                    self.store.update_flow_status(
                        run_id, FlowStatus.PAUSED, current_step=control.step_id
                    )
                    self.sink.on_flow_end(run_id, FlowStatus.PAUSED)
                    return self._require_run(run_id)
                if control.kind == "terminate":
                    self.store.update_flow_status(run_id, FlowStatus.CANCELLED, current_step=None)
                    self.sink.on_flow_end(run_id, FlowStatus.CANCELLED)
                    return self._require_run(run_id)
                if control.kind == "goto":
                    self._reset_from(flow, run_id, control.target)
                    self.store.update_flow_status(
                        run_id, FlowStatus.RUNNING, current_step=control.target
                    )
                    continue
                raise RuntimeError(f"unknown flow control: {control.kind}")
        except _StepFailed as exc:
            self.store.set_context(run_id, "__flow_error", str(exc))
            self.store.update_flow_status(run_id, FlowStatus.FAILED, current_step=exc.step_id)
            self.sink.on_flow_end(run_id, FlowStatus.FAILED)
            return self._require_run(run_id)
        except asyncio.CancelledError:
            self.store.set_context(run_id, "__flow_error", "run cancelled")
            self.store.update_flow_status(run_id, FlowStatus.CANCELLED, current_step=None)
            self.sink.on_flow_end(run_id, FlowStatus.CANCELLED)
            raise
        except Exception as exc:
            self.store.set_context(run_id, "__flow_error", f"internal error: {exc}")
            self.store.update_flow_status(run_id, FlowStatus.FAILED, current_step=None)
            self.sink.on_error(run_id, None, f"internal error: {exc}")
            self.sink.on_flow_end(run_id, FlowStatus.FAILED)
            raise

    async def _run_topology(self, flow: FlowDefinition, run_id: str) -> _Control | None:
        for layer in FlowEngine().layered_order(flow):
            run = self._require_run(run_id)
            runnable: list[str] = []
            for step_id in layer:
                snapshot = run.steps[step_id]
                if snapshot.status in {StepStatus.COMPLETED, StepStatus.SKIPPED}:
                    continue
                if snapshot.status == StepStatus.FAILED:
                    raise _StepFailed(step_id, snapshot.error or "step is failed")
                step = flow.step(step_id)
                incomplete = [
                    dep
                    for dep in step.depends_on
                    if run.steps[dep].status not in {StepStatus.COMPLETED, StepStatus.SKIPPED}
                ]
                if incomplete:
                    raise _StepFailed(
                        step_id,
                        f"upstream dependencies are incomplete: {', '.join(incomplete)}",
                    )
                runnable.append(step_id)
            if not runnable:
                continue
            control = await self._run_layer(flow, runnable, run_id)
            if control is not None:
                return control
        return None

    async def _run_layer(
        self, flow: FlowDefinition, step_ids: list[str], run_id: str
    ) -> _Control | None:
        tasks = {
            step_id: asyncio.create_task(self._run_step(flow, flow.step(step_id), run_id))
            for step_id in step_ids
        }
        if flow.defaults_fail_fast and len(tasks) > 1:
            done, pending = await asyncio.wait(tasks.values(), return_when=asyncio.FIRST_EXCEPTION)
            failed = any(
                task.done() and not task.cancelled() and task.exception() is not None
                for task in done
            )
            if failed:
                for task in pending:
                    task.cancel()
            await asyncio.gather(*tasks.values(), return_exceptions=True)
        else:
            await asyncio.gather(*tasks.values(), return_exceptions=True)

        failures: list[_StepFailed] = []
        controls: list[_Control] = []
        for step_id in step_ids:
            task = tasks[step_id]
            if task.cancelled():
                failures.append(_StepFailed(step_id, "step was cancelled"))
                continue
            exception = task.exception()
            if isinstance(exception, _StepFailed):
                failures.append(exception)
            elif exception is not None:
                failures.append(_StepFailed(step_id, f"unexpected step error: {exception}"))
            else:
                control = task.result()
                if control is not None:
                    controls.append(control)
        if failures:
            raise failures[0]
        if not controls:
            return None

        non_pause = [control for control in controls if control.kind != "pause"]
        if non_pause:
            unique = {(control.kind, control.target) for control in non_pause}
            if len(unique) != 1 or len(controls) != 1:
                step_id = non_pause[0].step_id
                message = "conflicting gate decisions in one parallel layer"
                await self._mark_failed(flow.step(step_id), run_id, message)
                raise _StepFailed(step_id, message)
            return non_pause[0]
        return controls[0]

    async def _run_step(
        self, flow: FlowDefinition, step: StepDefinition, run_id: str
    ) -> _Control | None:
        if step.type == "human_gate":
            return await self._run_gate(flow, step, run_id)

        snapshot = self._require_run(run_id).steps[step.id]
        max_visits = step.max_visits or flow.defaults_max_visits
        if snapshot.visit_count + 1 > max_visits:
            message = f"step visit limit reached ({max_visits})"
            await self._mark_failed(step, run_id, message)
            raise _StepFailed(step.id, message)
        self.store.update_step(
            run_id,
            step.id,
            StepStatus.RUNNING,
            output=None,
            error=None,
            visit_count=snapshot.visit_count + 1,
            retry_count=0,
        )
        self.sink.on_step_start(run_id, step.id)
        await self.hook.before_step(run_id, step, self._require_run(run_id))
        try:
            base_prompt = await self._render_prompt(flow, step, run_id)
            retry_budget = step.retry if step.retry is not None else flow.defaults_retry
            result: AgentResult | None = None
            violations: list[str] = []
            attempts = 0
            while attempts <= retry_budget:
                attempts += 1
                # On a contract-violating attempt, append the violation feedback
                # to the next attempt's prompt so the agent has a chance to fix
                # its output instead of blindly rerolling the same broken answer.
                effective_prompt = (
                    self._with_contract_feedback(base_prompt, violations)
                    if violations
                    else base_prompt
                )
                started = time.monotonic()
                try:
                    result = await self.adapter.execute(
                        step.agent,
                        effective_prompt,
                        timeout=step.timeout or flow.defaults_timeout,
                        cwd=self.cwd,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    result = AgentResult.failure(f"adapter raised {type(exc).__name__}: {exc}")
                    continue
                if result.duration_ms == 0:
                    result = replace(
                        result, duration_ms=max(1, int((time.monotonic() - started) * 1000))
                    )
                if not result.ok:
                    continue
                # Enforce the output contract inside the retry window so a
                # well-formed but contract-violating result is retried, not
                # accepted after a single attempt.
                result = self._normalize_parsed(step, result)
                violations = step.output.validate(result) if step.output is not None else []
                if not violations:
                    break
            assert result is not None
            retry_count = attempts - 1
            if not result.ok:
                message = result.error or "agent failed"
                await self._fail_step(step, run_id, result, message, retry_count)
                raise _StepFailed(step.id, message)
            if violations:
                message = "; ".join(violations)
                await self._fail_step(step, run_id, result, message, retry_count)
                raise _StepFailed(step.id, message)

            self.store.update_step(
                run_id,
                step.id,
                StepStatus.COMPLETED,
                output=result.output,
                error=None,
                retry_count=retry_count,
                duration_ms=result.duration_ms,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
            )
            if result.parsed is not None:
                self.store.set_context(run_id, f"{step.id}.parsed", result.parsed)
            self.sink.on_step_end(run_id, step.id, result)
            await self._write_memory(flow, step, result, run_id)
            await self.hook.after_step(run_id, step, result)
            return None
        except asyncio.CancelledError:
            message = "cancelled by fail_fast"
            self.store.update_step(run_id, step.id, StepStatus.FAILED, output=None, error=message)
            self.sink.on_error(run_id, step.id, message)
            await self.hook.on_error(run_id, step, message)
            raise
        except _StepFailed:
            raise
        except ResolverError as exc:
            message = str(exc)
            await self._mark_failed(step, run_id, message)
            raise _StepFailed(step.id, message) from exc
        except Exception as exc:
            message = f"step internal error: {type(exc).__name__}: {exc}"
            await self._mark_failed(step, run_id, message)
            raise _StepFailed(step.id, message) from exc

    async def _run_gate(
        self, flow: FlowDefinition, step: StepDefinition, run_id: str
    ) -> _Control | None:
        snapshot = self._require_run(run_id).steps[step.id]
        max_visits = step.max_visits or flow.defaults_max_visits
        if snapshot.visit_count + 1 > max_visits:
            message = f"step visit limit reached ({max_visits})"
            await self._mark_failed(step, run_id, message)
            raise _StepFailed(step.id, message)
        self.store.update_step(
            run_id,
            step.id,
            StepStatus.RUNNING,
            output=None,
            error=None,
            visit_count=snapshot.visit_count + 1,
        )
        self.sink.on_step_start(run_id, step.id)
        await self.hook.before_step(run_id, step, self._require_run(run_id))
        try:
            option = await self.gate_driver.ask(step)
        except Exception as exc:
            message = f"gate driver failed: {exc}"
            await self._mark_failed(step, run_id, message)
            raise _StepFailed(step.id, message) from exc
        if option is None:
            self.store.update_step(
                run_id, step.id, StepStatus.WAITING_HUMAN, output=None, error=None
            )
            # Notify hosts that a decision is pending so they can surface the
            # question to their own user and resume the run with the answer.
            await self.hook.on_gate_pending(run_id, step, step.options)
            return _Control("pause", step.id)
        if option not in step.options:
            message = f"gate driver returned an option not defined by step {step.id}"
            await self._mark_failed(step, run_id, message)
            raise _StepFailed(step.id, message)

        decision = GateDecision(
            step_id=step.id,
            label=option.label,
            action=option.action,
            target=option.target,
        )
        self.store.append_gate_decision(run_id, decision)
        result = AgentResult.success(option.label)
        self.store.update_step(
            run_id, step.id, StepStatus.COMPLETED, output=option.label, error=None
        )
        self.sink.on_step_end(run_id, step.id, result)
        await self.hook.on_gate_decision(run_id, step, decision)
        await self.hook.after_step(run_id, step, result)
        if option.action == "next_step":
            return None
        if option.action == "goto_step":
            return _Control("goto", step.id, option.target)
        if option.action == "terminate":
            return _Control("terminate", step.id)
        message = f"unsupported gate action: {option.action}"
        await self._mark_failed(step, run_id, message)
        raise _StepFailed(step.id, message)

    async def _render_prompt(self, flow: FlowDefinition, step: StepDefinition, run_id: str) -> str:
        run = self._require_run(run_id)
        edited = run.context.get("__edited_steps", {}).get(step.id)
        template = edited["prompt"] if edited else step.prompt
        outputs = {
            step_id: snapshot.output
            for step_id, snapshot in run.steps.items()
            if snapshot.status == StepStatus.COMPLETED
        }
        variables = {
            step_id: run.context[f"{step_id}.parsed"]
            for step_id in outputs
            if f"{step_id}.parsed" in run.context
        }
        context = ResolveContext(
            run_context=run.context,
            step_outputs=outputs,
            step_variables=variables,
            step_groups={item.id: item.group for item in flow.steps},
            flow=flow,
            current_step=step,
            memory_client=self.memory_client,
            secret_provider=self.secret_provider,
            workspace=flow.memory_workspace,
        )
        rendered, results = await self.registry.render(
            template, context, timeout=self.resolver_timeout
        )
        for result in results:
            if result.missing:
                self.sink.on_resolver_missing(run_id, step.id, result.source, result.error)
        missing_secrets = [
            result.error or f"secret reference failed: {result.source}"
            for result in results
            if result.missing and result.source.startswith("secret:")
        ]
        if missing_secrets:
            raise ResolverError("; ".join(missing_secrets))
        return rendered

    async def _write_memory(
        self,
        flow: FlowDefinition,
        step: StepDefinition,
        result: AgentResult,
        run_id: str,
    ) -> None:
        if self.memory_client is None:
            return

        def write() -> Any:
            for name in ("write", "memory_write"):
                method = getattr(self.memory_client, name, None)
                if method is not None:
                    return method(
                        subject=step.id,
                        content=str(result.output),
                        tags=[flow.name],
                        workspace=flow.memory_workspace,
                        metadata={
                            "run_id": run_id,
                            "duration_ms": result.duration_ms,
                            "input_tokens": result.input_tokens,
                            "output_tokens": result.output_tokens,
                        },
                    )
            raise AttributeError("memory client has no write method")

        try:
            response = await run_sync_with_timeout(
                write, timeout=self.memory_timeout, pool=self.worker_pool
            )
            memory_id = self._extract_memory_id(response)
            if memory_id is not None:
                self.store.set_context(run_id, f"{step.id}.memory_id", memory_id)
        except Exception as exc:
            self.sink.on_memory_write_failed(run_id, step.id, str(exc))

    @staticmethod
    def _extract_memory_id(response: Any) -> int | None:
        if isinstance(response, int):
            return response
        if isinstance(response, dict):
            for key in ("id", "memory_id"):
                if isinstance(response.get(key), int):
                    return response[key]
            data = response.get("data")
            if isinstance(data, dict):
                for key in ("id", "memory_id"):
                    if isinstance(data.get(key), int):
                        return data[key]
        return None

    async def _mark_failed(self, step: StepDefinition, run_id: str, message: str) -> None:
        self.store.update_step(run_id, step.id, StepStatus.FAILED, output=None, error=message)
        self.sink.on_error(run_id, step.id, message)
        await self.hook.on_error(run_id, step, message)

    async def _fail_step(
        self,
        step: StepDefinition,
        run_id: str,
        result: AgentResult,
        message: str,
        retry_count: int,
    ) -> None:
        self.store.update_step(
            run_id,
            step.id,
            StepStatus.FAILED,
            output=result.output,
            error=message,
            retry_count=retry_count,
            duration_ms=result.duration_ms,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )
        self.sink.on_error(run_id, step.id, message)
        await self.hook.on_error(run_id, step, message)

    @staticmethod
    def _normalize_parsed(step: StepDefinition, result: AgentResult) -> AgentResult:
        if step.output is not None and step.output.format == "json" and result.parsed is None:
            if isinstance(result.output, (dict, list)):
                return replace(result, parsed=result.output)
            with suppress(TypeError, ValueError, json.JSONDecodeError):
                return replace(result, parsed=json.loads(result.output))
        return result

    @staticmethod
    def _with_contract_feedback(prompt: str, violations: list[str]) -> str:
        """Append contract-violation feedback so a retry can fix the output.

        Without this the retry would resend the identical prompt and the agent
        would most likely reproduce the same contract-breaking answer.
        """

        joined = "; ".join(violations)
        return (
            f"{prompt}\n\n---\nYour previous attempt for this step violated its output "
            f"contract and was rejected. Please retry the SAME task and produce output "
            f"that satisfies the contract. Contract violations: {joined}"
        )

    def retry_step(self, run_id: str, step_id: str) -> FlowRun:
        run = self._require_run(run_id)
        flow = parse_flow(run.flow_yaml_snapshot)
        if step_id not in run.steps:
            raise InvalidResumeError(f"step does not exist: {step_id}")
        if run.steps[step_id].status != StepStatus.FAILED:
            raise InvalidResumeError(
                f"step {step_id} is {run.steps[step_id].status.value}; only failed steps can retry"
            )
        self._reset_from(flow, run_id, step_id)
        self.store.update_flow_status(run_id, FlowStatus.RUNNING, current_step=step_id)
        return self._require_run(run_id)

    def edit_step(self, run_id: str, step_id: str, prompt: str) -> FlowRun:
        run = self._require_run(run_id)
        flow = parse_flow(run.flow_yaml_snapshot)
        if step_id not in run.steps:
            raise InvalidResumeError(f"step does not exist: {step_id}")
        edits = dict(run.context.get("__edited_steps", {}))
        edits[step_id] = {"prompt": prompt, "edited_at": utc_now().isoformat()}
        self.store.set_context(run_id, "__edited_steps", edits)
        self._reset_from(flow, run_id, step_id)
        self.store.update_flow_status(run_id, FlowStatus.RUNNING, current_step=step_id)
        return self._require_run(run_id)

    def cancel(self, run_id: str) -> FlowRun:
        run = self._require_run(run_id)
        if run.status not in {FlowStatus.RUNNING, FlowStatus.PAUSED}:
            raise InvalidResumeError(
                f"run {run_id} is {run.status.value}; only running/paused runs can cancel"
            )
        self.store.update_flow_status(run_id, FlowStatus.CANCELLED, current_step=None)
        return self._require_run(run_id)

    def _reset_from(self, flow: FlowDefinition, run_id: str, step_id: str) -> None:
        targets = {step_id, *FlowEngine().descendants(flow, step_id)}
        for candidate in [step.id for step in flow.steps if step.id in targets]:
            self.store.reset_step(run_id, candidate)

    def _validate_resume_target(self, flow: FlowDefinition, run: FlowRun, step_id: str) -> None:
        if step_id not in run.steps:
            raise InvalidResumeError(f"step does not exist: {step_id}")
        if run.steps[step_id].status == StepStatus.FAILED:
            raise InvalidResumeError(
                f"step {step_id} is failed; reset it with retry-step before resuming"
            )
        incomplete = [
            dependency
            for dependency in FlowEngine().ancestors(flow)[step_id]
            if run.steps[dependency].status not in {StepStatus.COMPLETED, StepStatus.SKIPPED}
        ]
        if incomplete:
            raise InvalidResumeError(
                f"cannot resume {step_id}; upstream dependencies are incomplete: "
                + ", ".join(sorted(incomplete))
            )

    def _require_run(self, run_id: str) -> FlowRun:
        run = self.store.load_run(run_id)
        if run is None:
            raise InvalidResumeError(f"run does not exist: {run_id}")
        return run

    def _validate_required_secrets(self, flow: FlowDefinition) -> None:
        if not flow.required_secrets:
            return
        provider = self.secret_provider
        missing: list[str] = []
        for key in flow.required_secrets:
            value = self._resolve_secret(provider, key)
            if value is None or value == "":
                missing.append(key)
        if missing:
            raise FlowExecutionError("required secrets are unavailable: " + ", ".join(missing))

    @staticmethod
    def _resolve_secret(provider: Any, key: str) -> Any:
        if provider is None:
            return None
        getter = provider.get if hasattr(provider, "get") else provider
        try:
            return getter(key)
        except Exception:
            return None
