# 流程格式参考

AgentLane 用 `yaml.safe_load` 读取 YAML，并拒绝未知字段。打包的 JSON Schema 在
`agentlane/schema/flow-schema.json`；运行时校验还会额外检查 JSON Schema 不方便表达的图和引用语义。

## 顶层字段

| 字段 | 必需 | 含义 |
| --- | --- | --- |
| `name` | 是 | 非空的流程名。 |
| `version` | 否 | 正整数；默认 `1`。 |
| `description` | 否 | 给人看的说明文字。 |
| `defaults` | 否 | `timeout`、`retry`、`max_visits`、`fail_fast`。 |
| `memory.workspace` | 否 | memory 元数据；默认 `default`。**不是**隔离边界。 |
| `secrets.required` | 否 | 唯一的 secret 名称列表，在创建运行前预检。 |
| `steps` | 是 | 非空的 agent 或人工关卡步骤列表。 |

`defaults` 的默认值是 `timeout: 300`、`retry: 1`、`max_visits: 3`、`fail_fast: false`。

## agent 步骤

```yaml
- id: review
  type: agent
  agent: claude-code
  prompt: "Review {steps:draft}"
  depends_on: [draft]
  timeout: 120
  retry: 2
  max_visits: 3
  group: reviewers
  output:
    format: json
    schema:
      verdict: string
      score: number
  terminal: false
```

`id` 必须匹配 `^[A-Za-z0-9][A-Za-z0-9._-]*$` 且唯一。agent 步骤必须填 `agent`。依赖必须存在、唯一、
且构成无环图。`group` 会创建一个 resolver 命名空间；它**不改变调度**。`terminal` 在这个 alpha 版本里
只是流程元数据，不会覆盖依赖执行语义。

`output.format` 接受 `text`、`markdown`、`json`。markdown 必须非空。json 必须能解析成一个对象。schema
是一张"必需字段 → 类型"的映射，类型可以是 `string`、`integer`、`number`、`boolean`、`object`、`array`、
`null`；额外字段仍然允许。

## 人工关卡（human gate）

```yaml
- id: approval
  type: human_gate
  message: Ship this result?
  depends_on: [review]
  max_visits: 2
  options:
    - label: approve
      action: next_step
    - label: revise
      action: goto_step
      target: draft
    - label: stop
      action: terminate
```

option 的 label 必须非空且唯一。一个 option 的契约就是 `label`、`action`、可选的 `target`；**没有**
option `id`。`goto_step` 要求 target 必须存在。一次跳转会重置 target 及其所有下游，然后从拓扑开头重新
开始执行。`max_visits` 对关卡和 agent 步骤都生效。

如果同一并行层里有多个关卡，允许多个暂停决策。但非暂停决策必须是该层里**唯一**的控制决策；冲突的
跳转 / 终止决策会以可见的方式失败。

## 引用（reference）

引用用 `{prefix:key}` 语法，在调用 adapter 之前**并发**解析。

| 语法 | 缺失时的行为 |
| --- | --- |
| `{steps:draft}` | 空文本 + `resolver_missing` 事件。target 必须是一个未分组的上游祖先。 |
| `{steps:draft.field}` | 同上；要求上游是已解析的 JSON，并沿嵌套对象键取值。 |
| `{team.steps:draft}` | 同上；target 必须声明了 `group: team`。 |
| `{env:NAME}` | 空文本 + 事件。 |
| `{secret:NAME}` | 步骤在 agent 执行前 fail-closed 失败。 |
| `{memory:query}` | memory 未启用 / 缺失时空文本 + 事件。 |
| `{memory:123}` | 读取一个确切的 memory ID，并沿有界的 superseded 链跟随。 |
| `{memory:get:draft}` | 读取某个上游步骤写入后记录的 memory ID。 |

被引用的步骤必须是一个上游依赖（直接或传递）。未知的 resolver 前缀和格式错误的 key 都会在创建运行前
被拒绝。resolver 调用有超时上限。

## 执行与恢复语义

图会被划分成稳定的依赖层。同一层里的步骤并发执行。在 `fail_fast: false` 时，AgentLane 会等同一层
所有兄弟步骤都完成，再在任一失败时让整个运行失败。在 `fail_fast: true` 时，第一个失败的步骤会取消
未完成的兄弟步骤并 await 它们的清理。

`retry` 计的是首次尝试之后的重试次数。`max_visits` 计的是跨跳转和恢复的拓扑访问次数。流程快照、
步骤状态、输出、错误、耗时、token 数、关卡决策、运行时上下文，都会在每次状态转移后持久化。

非交互关卡会暂停。恢复时会重新加载持久化的 YAML 快照；如果传入一个语义上不同的流程会被拒绝。显式的
重试或改 prompt 会重置所选步骤及其下游。

### 输出契约与重试

如果一个步骤声明了 output 契约，agent 的输出会先被校验。契约违反（比如要求 JSON 但解析失败、或缺了
必需字段）会**纳入重试预算**重试，而不是立即失败。关键是：重试时，**违反信息会被追加进下一次的 prompt**，
让 agent 有机会修正输出，而不是盲目地重跑同一个会再次失败的 prompt。重试预算耗尽后仍违反契约才会让
步骤失败。
