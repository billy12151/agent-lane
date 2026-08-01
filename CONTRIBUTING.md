# 贡献指南（Contributing）

AgentLane 目前是 alpha（内测）软件。请保持改动小而聚焦，保留严格的校验和持久化恢复行为，并为成功路径
**和**持久化的失败状态都补上测试。

## 本地环境

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

## 必需的检查

```bash
ruff format --check .
ruff check .
mypy agentlane
pytest --cov=agentlane --cov-report=term-missing
python -m build
python -m twine check dist/*
```

覆盖率必须保持在 90% 以上，并开启分支覆盖。测试应当验证**可观测的状态**，而不只是返回值——步骤状态、
错误文本、retry / visit 计数、快照、清理行为，这些对正在恢复一个失败运行的用户都很重要。

## 架构规则

- 规范实现统一放在 `agentlane/core` 下；根目录模块只是兼容性再导出。
- 只保留**一个** `AgentAdapter` 路由出口。不要引入第二个 adapter 注册表。
- 独立持久化保持基于 JSON，除非有一个单独审批过的迁移改变了设计。
- resolver、hook、sink、memory、ACP、TaskFlow 这些集成都是**有界的、注入式的接缝**。
- 不要为了方便而削弱未知字段、依赖图、secret、快照、访问上限这些校验。
- 改了语法时，要一起更新流程 schema、parser 测试、参考文档、以及本更新日志。
