# AgentLane

AgentLane 是一个声明式工作流引擎，用来编排多个自主 CLI agent（如 codex、claude-code、gemini-cli）。
你用一个 YAML 文件定义任务依赖图，AgentLane 负责校验它、并行调度无依赖冲突的步骤、解析上游输出、
强制执行输出契约、在人工关卡处暂停，并把运行状态持久化下来——这样崩溃或暂停后可以恢复，而不会
悄悄改变原始流程的定义。

> 状态：`0.1.0a1` 是 alpha（内测）版本。流程模型和独立运行路径已经可用；在 `1.0` 正式版之前，接口仍可能调整。

## 核心价值：视角互补

多 agent 协作真正的价值不在于"能力互补"（厂商自己会补齐），而在于**视角互补**：不同的 agent 对
同一份产物给出不同视角的评审，能发现单个 agent 发现不了的盲区——哪怕它们背后是同一个模型。这是
一个逻辑死结：一个 agent 永远没法用自己的视角发现自己的盲区。

AgentLane 内置的 `cross-review-trio` 模板就体现了这个思路：一个 agent 出初稿，**两个不同的 agent
并行独立评审同一份初稿**（彼此看不到对方输出），第三步把它们汇合成"共识 / 分歧 / 盲区"三类结论。

## 已实现的能力

- 严格的 YAML 流程校验：未知字段、类型、依赖图、引用、环路都会被检查。
- 真正的分层级并发、可配置的重试与超时、可选的 fail-fast 快速失败清理。
- 统一的 agent 路由边界（adapter），内置 shell、静态测试、可注入的 ACP adapter。
- 步骤输出引用、环境变量、secret、以及可选的 memory-arbiter 记忆解析器（resolver）。
- 文本、Markdown、结构化 JSON 输出契约。
- 人工关卡（human gate），支持 `next_step`、有上限的 `goto_step`、以及 `terminate` 决策。
- 原子化的 JSON 运行持久化、不可变的流程快照、恢复（resume）、重试（retry）、改 prompt、清理历史。
- JSONL 事件日志、运行摘要、耗时与 token 指标、生命周期 hooks、ASCII / Mermaid 可视化。
- 为 OpenClaw 宿主集成预留的 TaskFlow 与 ACP 注入接缝（seam）。

## 从本仓库安装

AgentLane 要求 Python 3.10 或更高版本。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
agentlane --version
```

开发与发布检查：

```bash
python -m pip install -e ".[dev]"
ruff check .
mypy agentlane
pytest --cov=agentlane
```

## 快速开始

创建用户目录和一个已校验的示例流程：

```bash
agentlane quickstart
agentlane agent detect
```

AgentLane 自带 `codex`、`claude-code`、`gemini-cli` 三个 agent 的规格定义。`detect` 命令只会报告
每个可执行文件是否可用，不会安装或认证第三方工具。

创建或校验一个流程：

```bash
agentlane flow create --template cross-review --name my-review
agentlane flow validate ~/.agentlane/flows/my-review.agentlane.yml
agentlane flow visualize ~/.agentlane/flows/my-review.agentlane.yml
```

运行它并查看持久化的运行记录：

```bash
agentlane flow run ~/.agentlane/flows/my-review.agentlane.yml
agentlane flow list
agentlane flow status
```

内置模板：`blank`（空白）、`cross-review`（抽取 → 评审）、`cross-review-trio`（一个 agent 出初稿，
**两个不同 agent 并行独立评审同一份初稿**，第三步汇合共识 / 分歧 / 盲区——这是核心的"视角互补"模式）、
`codegen-test`（实现 → 评审测试）。

## 自主执行 flags（重要）

内置 agent 规格自带了每个 harness 在非交互流程里真正可用所需的自主执行 flags：

| harness | flags | 作用 |
| --- | --- | --- |
| `codex` | `--dangerously-bypass-approvals-and-sandbox --skip-git-repo-check` | 跳过审批询问；允许在 git 仓库外运行；prompt 从 stdin 读入（`-`） |
| `claude-code` | `--dangerously-skip-permissions` | 绕过逐工具审批，让 agent 能自主读文件 / 跑 Bash |
| `gemini-cli` | `--yolo --skip-trust -p ""` | 自动批准所有工具调用；跳过工作区信任询问；从 stdin 读 prompt |

**不加这些 flags，agent 只能对 prompt 字面文字做反应，读不了你的文件、跑不了命令**——这会让流程退化
成普通的 prompt 路由。**这些 flags 等于让 agent 拥有对工作区的全部、不受沙箱限制的控制权。** 请只对你
信任的目录运行流程，绝不要把不受信任的内容直接塞进流程 prompt。如果你装的 CLI 版本不认某个 flag，可以在
`~/.agentlane/config.yml` 里覆盖该命令（参考 `examples/config.example.yml`）。

## 流程示例

```yaml
name: implementation-review
version: 1
defaults:
  timeout: 300
  retry: 1
  max_visits: 3
  fail_fast: false

steps:
  - id: draft
    agent: codex
    prompt: Produce a JSON implementation plan.
    output:
      format: json
      schema:
        plan: string

  - id: architecture-review
    agent: claude-code
    prompt: |
      Review this plan from an architecture perspective:
      {steps:draft.plan}
    depends_on: [draft]
    output:
      format: markdown

  - id: risk-review
    agent: gemini-cli
    prompt: |
      Review this plan for operational risks:
      {steps:draft.plan}
    depends_on: [draft]
    output:
      format: markdown

  - id: approval
    type: human_gate
    message: Continue after both independent reviews?
    depends_on: [architecture-review, risk-review]
    options:
      - label: approve
        action: next_step
      - label: stop
        action: terminate

  - id: implement
    agent: codex
    prompt: Implement the approved plan.
    depends_on: [approval]
```

`architecture-review` 和 `risk-review` 会并发执行，因为它们位于同一个依赖层。一个步骤只能引用已完成
的上游依赖。结构化字段访问（如 `{steps:draft.plan}`）要求上游是 JSON 输出契约。

完整的格式与解析器规则见 [流程格式参考](docs/flow-format.md)。打包的 JSON Schema 在
`agentlane/schema/flow-schema.json`，可用于编辑器自动补全。

## Agent 配置

默认配置文件是 `~/.agentlane/config.yml`；设置 `AGENTLANE_HOME` 可以迁移所有默认数据目录，或用
`--config PATH` 指定某次命令的配置。完整示例见 [`examples/config.example.yml`](examples/config.example.yml)。

```yaml
agents:
  commands:
    codex: [codex, exec, -]
    local-reviewer: [python, /absolute/path/reviewer.py]
```

Prompt 通过 stdin 发给配置好的进程。命令使用直接进程执行，不走 shell，因此 shell 展开和管道不是隐式的。
用户自定义的 agent 规格也可以放在 `~/.agentlane/agents/*.agent.yml`，会覆盖同名的内置 ID。

## 关卡与恢复

交互式执行时，遇到人工关卡会提示你做选择。在自动化场景下，`--non-interactive` 会把运行状态保存为
`paused`（暂停），而不是瞎猜一个选项：

```bash
agentlane flow run flow.yml --non-interactive
agentlane flow resume RUN_ID --gate-option approval=approve
```

### 让其他 agent / 宿主驱动关卡决策（`--gate-notify`）

如果你是用 OpenClaw、workbuddy 这类 agent 在驱动 agentlane（而不是自己在终端前操作），关卡处的
终端提示用户根本看不到。用 `--gate-notify`：agentlane 会在每个关卡处**暂停并写一个 JSON 通知文件**，
你的驱动 agent 读这个文件就能知道"现在需要决策、选项是什么"，然后在它和你的对话里问你，拿到答案后
再 `resume` 把决策传回来。

```bash
# 1. 驱动 agent 启动流程，遇到关卡会暂停退出（不卡死），并写通知文件
cd /目标项目 && agentlane flow run review.yml --gate-notify
# 输出: run_id=abc123 status=paused

# 2. 通知文件在 ~/.agentlane/logs/gate-<run_id>-<step_id>.json，内容形如：
# {
#   "run_id": "abc123", "step_id": "approval", "message": "Ship it?",
#   "options": [{"label":"approve","action":"next_step"}, {"label":"stop","action":"terminate"}],
#   "resume_hint": "agentlane flow resume abc123 --gate-option approval=<label>"
# }

# 3. 驱动 agent 把这个决策抛给你，你选了 approve，它执行：
agentlane flow resume abc123 --gate-option approval=approve
```

通知文件是简单的旁路状态：它把"暂停"（agentlane 内部）和"决策"（驱动 agent / 用户）解耦，驱动方
既不需要注入回调函数，也不需要轮询运行状态——读一次文件就够了。

恢复类命令都基于持久化的流程快照操作：

```bash
agentlane flow retry-step RUN_ID STEP_ID
agentlane flow resume RUN_ID --edit-step STEP_ID --prompt "replacement prompt"
agentlane flow cancel RUN_ID
agentlane flow log RUN_ID
agentlane flow delete RUN_ID --yes
```

重试或编辑某个步骤时，会重置该步骤及其所有下游步骤，同时保留已完成的上游证据。`goto_step` 可以
故意重做某段工作，但 `max_visits` 会给每个步骤和关卡设上限，让流程不会无限循环。`terminate` 会记录
一个操作者决策并把运行状态置为 `cancelled`。

默认情况下，状态保存在 `~/.agentlane/runs.json`，使用进程间锁和原子文件替换。事件日志追加到
`~/.agentlane/logs/events.jsonl`。Agent 的输出是持久化数据，可能含敏感信息，请妥善保护这些文件。

## 解析器（resolver）行为

- `{steps:step-id}` 读取一个已完成的、未分组的上游步骤输出。
- `{steps:step-id.field}` 读取上游 JSON 结果里的某个字段。
- `{group.steps:step-id}` 用于声明了 `group: group` 的步骤。
- `{env:NAME}` 读取环境变量。缺失时变成空文本并发一个事件。
- `{secret:NAME}` 读取配置好的 secret provider。缺失会让该步骤直接失败（fail-closed）。
- `{memory:query}`、`{memory:ID}`、`{memory:get:step-id}` 使用显式启用的 memory-arbiter 客户端。缺失是可观测的，但不致命。

`memory.workspace` 是转发给 memory-arbiter 的元数据；在 memory-arbiter `0.7.4+` 版本里它**不是**
搜索隔离边界。不要把它当作授权或租户控制手段。

## Python API

```python
import asyncio

from agentlane import StepRunner, parse_flow
from agentlane.adapters import StaticAgentAdapter

flow = parse_flow("""
name: example
steps:
  - id: draft
    agent: demo
    prompt: Write a draft.
""")

runner = StepRunner(adapter=StaticAgentAdapter({"demo": "done"}))
run = asyncio.run(runner.run(flow))
assert run.steps["draft"].output == "done"
```

生产环境的宿主会注入自己的 `AgentAdapter`、`StateStore`、resolver registry、gate driver，以及可选的
hooks/sinks。AgentLane **故意没有**全局 adapter 注册表：路由的归属始终在宿主选定的那一个 adapter 上。
详见 [架构说明](docs/architecture.md)。

## 明确的 alpha 边界

当前版本**不包含**：远程模板市场、全屏监控 UI、独立的通用 ACP daemon。`ACPAgentAdapter` 和
`TaskFlowStateStore` 是为 OpenClaw 这类宿主运行时准备的**依赖注入接缝**。内置的 cross-review 流程
用的是支持独立运行的 harness；`zcode` 不在支持列表里。

## License

Apache License 2.0. 见 [LICENSE](LICENSE)。
