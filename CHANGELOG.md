# 更新日志（Changelog）

这里记录 AgentLane 所有值得注意的变更。格式遵循 [Keep a Changelog](https://keepachangelog.com/)，
项目在公开 API 稳定前采用语义化版本的 alpha 约定。

## [未发布]

### 新增

- 内置 `cross-review-trio` 模板：一个 agent 出初稿，两个不同的 agent 并行独立评审同一份初稿，
  第三步汇合共识 / 分歧 / 盲区——这是核心的"视角互补"模式。

### 变更

- 用户文档全面改为中文为主（README、docs/、CHANGELOG、CONTRIBUTING、内置模板描述），便于第一个用户
  读懂；英文版后续补。

### 修复

- `shell.py`：`os.getpgid` / `os.killpg` 是 POSIX-only，在 Windows 上会让超时 / 取消路径抛
  `AttributeError`。改为模块级能力探测，`start_new_session` 和信号逻辑都基于它。
- `jsonfile.py`：`run_lease` 每次创建一个 per-run 锁文件但从不清理。现在释放时会 unlink，避免长期
  累积垃圾文件。同时修了缺失的 `suppress` import。
- `async_utils.py`：有界 daemon 线程池之前是隐藏的模块级全局状态，违反架构文档的依赖注入原则。抽成
  可注入的 `WorkerPool`，`StepRunner` 和 `ResolverRegistry` 可接受，没传则用进程级默认池。
- `runner.py`：契约违反的重试之前会重发同一个 prompt，等于让 agent 盲目重跑。现在会把违反信息追加进
  下一次 prompt，让 agent 有机会修正输出。

## [0.1.0a1] - 2026-07-30

### 新增

- 严格的 V3.1 YAML 流程模型、图校验、稳定的并发分层、输出契约。
- shell、静态测试、可注入 ACP adapter，统一在一个路由边界背后。
- 步骤、分组步骤、环境变量、secret、memory resolver，全部带超时上限。
- 人工关卡、有界跳转、不可变决策、resume、retry、改 prompt、取消。
- 原子化 JSON、内存、可注入 TaskFlow 状态存储。
- CLI 的创建、运行、查看、可视化、恢复、日志、清理命令。
- JSONL 可观测性、运行摘要、生命周期 hooks、耗时指标、token 记账。
- 内置 agent 规格定义，以及 blank、cross-review、codegen-test 流程模板。
- Python 3.10-3.12 CI、严格的 lint / 类型检查、打包校验、90% 覆盖率门槛。
- 内置 codex / claude-code / gemini-cli 规格自带自主执行 flags，让 harness 在非交互流程里真正可用。
