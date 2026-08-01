# 宿主集成指南

这份指南讲的是：**如何让另一个 agent（OpenClaw、workbuddy，或任何能跑 bash 的工具）驱动 agentlane**。

agentlane 本身是个 CLI。宿主 agent 调它，和调 `git`、`pytest` 没本质区别——只要处理好下面五件事，
集成就顺畅。每一件都附了"不处理的后果"，方便你判断要不要管。

## 总览：一次完整的宿主驱动调用

```bash
# 宿主 agent 在它的 bash 工具里执行这一条，就完成了"启动 + 关卡决策 + 取结果"
cd /目标项目 && agentlane flow run /path/to/review.yml \
  --gate-notify \
  --gate-option approval=approve \
  --output json
```

但这只是最简单的情况（关卡答案事先知道）。真实场景里关卡决策要问用户，得分两步。下面逐条讲。

## 1. 工作目录（cwd）——告诉 agentlane 操作哪个项目

**必须处理。** 这是集成时最容易踩的坑。

agentlane 的 `flow run` 用的是**调用进程的当前工作目录**作为所有 agent 的工作目录。也就是说，
agent 会去这个目录里读文件、改代码。宿主 agent 调用时必须先 `cd` 到目标项目：

```bash
# 正确：cd 到目标项目，agent 才能读到那里的代码
cd /Users/你/目标项目 && agentlane flow run review.yml

# 错误：不 cd，agent 会去宿主的默认工作目录找文件，读不到目标代码
agentlane flow run review.yml
```

**不处理的后果**：agent 读不到要 review 的代码，整个流程空转，产出无意义。

> 如果宿主的 bash 工具支持指定 cwd 参数（而不是 `cd &&` 连写），用那个更干净，避免 `cd` 在某些
> shell 里触发权限提示。

## 2. 超时——别让宿主把 agentlane 掐死

**必须处理。** agentlane 跑一条 flow 可能要几分钟。

`flow run` 是**阻塞**调用——一直等到整条 flow 跑完才返回。默认每个 agent 步骤超时 300 秒（5 分钟），
一条带两三家 agent 的 trio flow 跑 2-4 分钟很正常。

宿主 agent 的 bash 工具通常有自己的超时设置。**宿主超时必须大于 flow 的总时长**，否则宿主会中途
杀掉 agentlane 进程，留下一个 `running` 状态的半成品 run。

两种处理方式：

**方式 A：调大宿主的 bash 超时**
```bash
# 假设宿主支持 timeout 参数，给足时间
cd /项目 && agentlane flow run review.yml   # 宿主 bash timeout 设成 600s+
```

**方式 B：调小 flow 的步骤超时**（在 YAML 里）
```yaml
defaults:
  timeout: 120   # 每步最多 2 分钟，防止某步卡太久
  retry: 0
```

**不处理的后果**：宿主超时杀进程，run 卡在 `running`，后续 `resume` 会报"run 已在执行"（run_lease 锁）。
需要 `agentlane flow cancel RUN_ID` 清理后才能重跑。

## 3. 人工关卡（human_gate）——让决策流回你的对话框

**这是宿主集成最关键的一环。** 处理不好，宿主会卡死。

如果你的 flow 里有 `type: human_gate` 步骤，agentlane 默认会在自己的终端提示等输入。宿主 agent
跑命令时没有人在终端前，会**永久卡死**。三种处理方式，按场景选：

### 场景 A：关卡答案事先知道 → 预设

```bash
cd /项目 && agentlane flow run review.yml --gate-option approval=approve
```

适合"我知道这个关卡就是要 approve"的自动化场景。多个关卡可以重复 `--gate-option`：
`--gate-option gate1=yes --gate-option gate2=ship`。

### 场景 B：要问用户 → `--gate-notify`（推荐）

这是为宿主集成专门设计的。agentlane 遇到关卡会**暂停退出（不卡死）+ 写 JSON 通知文件**：

```bash
# 步骤 1：宿主启动，跑到关卡处暂停
cd /项目 && agentlane flow run review.yml --gate-notify
# 输出: run_id=abc123 status=paused

# 步骤 2：宿主读通知文件，知道要问什么
cat ~/.agentlane/logs/gate-abc123-approval.json
# {
#   "run_id": "abc123",
#   "step_id": "approval",
#   "message": "Ship it?",
#   "options": [
#     {"label":"approve","action":"next_step","target":""},
#     {"label":"stop","action":"terminate","target":""}
#   ],
#   "resume_hint": "agentlane flow resume abc123 --gate-option approval=<label>"
# }

# 步骤 3：宿主把这个问题抛给它对话框里的用户，用户选 approve
#         宿主执行 resume 把答案传回来
agentlane flow resume abc123 --gate-option approval=approve
# 如果后面还有关卡，resume 也带 --gate-notify 继续通知
```

通知文件是**旁路状态**——它把"暂停"（agentlane 内部）和"决策"（宿主/用户）解耦。宿主读一次文件
就够了，不需要注入回调，也不需要轮询 run 状态。这对任何能读文件的宿主都适用。

### 场景 C：纯无人值守 → `--non-interactive`

```bash
cd /项目 && agentlane flow run review.yml --non-interactive
```

和 `--gate-notify` 一样会暂停退出，但**不写通知文件**。适合 flow 里没有关卡、或者你想自己轮询
`flow status` 的场景。

**不处理的后果**：场景 A 不预设会卡死；场景 B/C 不加 flag 也会卡死。总之只要 flow 有关卡且宿主
不交互，就必须用 `--gate-option` / `--gate-notify` / `--non-interactive` 三者之一。

## 4. 退出码与错误判断

**建议处理。** 让宿主知道 flow 是成功还是失败。

`flow run` 的退出码：
- `0`：flow 完成（`completed`）或暂停（`paused`）——暂停不算失败，是正常退出
- `1`：flow 失败（`failed`）

宿主 agent 可以据此决定下一步。但**退出码区分不了"完成"和"暂停"**——两者都是 0。要区分，看输出
文本里的 `status=`：

```bash
output=$(cd /项目 && agentlane flow run review.yml --gate-notify)
exit_code=$?
if echo "$output" | grep -q "status=paused"; then
  # 暂停了，去读通知文件、问用户
  run_id=$(echo "$output" | grep -oE 'run_id=[a-f0-9]+' | cut -d= -f2)
  # ... 读 gate-${run_id}-*.json，问用户，resume
elif [ $exit_code -eq 0 ]; then
  echo "flow 完成"
else
  echo "flow 失败"
fi
```

## 5. 取结构化结果 → `--output json`

**建议处理。** 比解析终端文本可靠得多。

```bash
cd /项目 && agentlane flow run review.yml --output json
```

输出是一个 JSON 对象，含 `run_id`、`status`、每个步骤的 `output`（agent 产出文本）、`error`、
`duration_ms` 等。宿主 agent 可以直接 `json.loads` 解析，精确拿到每步的产出，不用正则解析终端文本。

```python
# 宿主 agent 里（如果是 Python）
import json, subprocess
result = subprocess.run(
    ["agentlane", "flow", "run", "review.yml", "--output", "json"],
    capture_output=True, text=True, cwd="/目标项目",
)
data = json.loads(result.stdout)
draft_output = data["steps"]["draft"]["output"]   # 直接拿 draft 步骤的产出
```

## 推荐的集成模式

把上面五件事组合起来，对大多数宿主最稳的模式是：

```bash
cd /目标项目 && agentlane flow run /path/to/flow.yml \
  --gate-notify \
  --output json
```

- `cd` 解决工作目录
- 宿主 bash 超时调到 600s+ 解决阻塞
- `--gate-notify` 解决关卡（暂停 + 通知，不卡死）
- `--output json` 让宿主拿到结构化结果

宿主拿到 `status=paused` 后，读通知文件、问用户、`resume --gate-option` 传回答案；拿到
`status=completed` 就直接解析 JSON 取结果。

## 集成深度对照

| 集成方式 | 怎么做 | 适合场景 |
| --- | --- | --- |
| **轻量：bash 调用** | 宿主用 bash 工具跑 `agentlane flow run`，用 `--output json` 取结果 | workbuddy、任何能跑命令的 agent |
| **中度：bash + gate-notify** | 上面加上 `--gate-notify`，宿主处理关卡决策闭环 | OpenClaw skill、需要人工审批的流程 |
| **深度：库集成** | 宿主用 Python API 注入 `AgentAdapter`/`StateStore`/`GateDriver`，不走 CLI | 紧密耦合、需要完全控制执行 |

前两种不用改 agentlane 任何代码，纯 CLI 调用。第三种走 `agentlane.StepRunner` Python API，
`GateDriver` 接缝允许宿主直接用自己的对话框逻辑接收关卡决策——这是为 OpenClaw 这类深度集成准备的。

## 检查清单

集成前过一遍，避免踩坑：

- [ ] 宿主 agent 的 bash 工具能 `cd` 到目标项目目录
- [ ] 宿主 bash 超时 ≥ flow 预计总时长（或 flow YAML 里调小 `timeout`）
- [ ] flow 有关卡时，用了 `--gate-option` / `--gate-notify` / `--non-interactive` 之一
- [ ] 用 `--output json` 取结果，别解析终端文本
- [ ] 退出码 0 时检查 `status=paused` 还是 `completed`，两者处理不同
- [ ] `AGENTLANE_HOME` 环境变量在各宿主进程里一致（否则 run 状态存不同地方）
