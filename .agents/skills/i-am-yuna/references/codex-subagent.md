# Codex subagent 交互

在 Codex 中创建、联系、恢复或等待 subagent 时阅读。本文件只说明 Codex 的工具参数、消息传递和可选留档方式。

## 创建和联系

`spawn_agent` 创建下级，`send_message` 向已有下级送达消息，`followup_task` 还可唤醒空闲下级继续工作；`wait_agent` 等待消息。等待是响应式的：子任务消息或用户输入可以提前唤醒调度者。先区分“还有独立工作可做”与“只能等待结果”，再选择行动；后者不需要用短周期轮询保持响应。

- `spawn_agent` 通常使用 `fork_turns: "none"`，并传入已确认的模型和思考强度，让任务说明提供最小、完整、可复核的上下文。确有必要继承会话时，遵守当前工具对 `fork_turns` 和模型覆盖的限制。
- 第一条消息传递[参与者协议](participant-protocol.md#开始与停止)要求的完整启动信息：工作目录、任务说明与固定版协议绝对路径、版本定位。按协议的统一顺序读取，不在消息中重写规则。Codex 没有直接切换 subagent 工作目录的参数时，在每次文件或命令操作中显式使用该目录。
- 后续使用 `send_message` 或 `followup_task`。完整上下文写在文件中，工具消息只传递一个简短动作和文件路径，不重复生成相同正文。
- 调度者没有其他可做工作且 subagent 仍在运行时，只调用 `wait_agent`，并把 `timeout_ms` 设为接口允许的最大值 `3600000`。
- 不要把“避免长时间阻塞等待”当成了硬性 N 秒上限，这不准确。wait_agent 会被子任务消息或用户的新输入唤醒，所以响应式等待是最优解。

## 用户不可见对话时的消息留档

消息留档保存双方实际说过什么；checkpoint（检查点）保存工作当前做到哪里，二者用途不同。先判断用户能否直接查看完整对话，再决定是否需要额外保存消息，避免把通信记录当成恢复状态。

在用户不能直接查看完整 subagent 对话的 Codex 环境中，启用消息留档；如果未来环境已经让用户直接审阅完整对话，则普通进度交流保留在环境中，不重复写文件。checkpoint 继续负责上下文恢复，消息留档不承担恢复职责。

启用后，每个任务维护自己的任务消息序号，从 `001` 递增。每条单向消息使用一个新的不可变文件：

    <任务工作区>/tmp/agents/<会话名>/<任务名>/main-001.md
    <任务工作区>/tmp/agents/<会话名>/<任务名>/subagent-002.md
    <任务工作区>/tmp/agents/<会话名>/<任务名>/main-003.md

发送者先把完整消息写入对应文件，再通过协作工具只发送短封套和路径，例如：

    读取并处理此消息：<任务工作区>/tmp/agents/<会话名>/<任务名>/main-003.md

subagent 读取本适配后，在回复或最终交付前采用同样做法，无需任务说明重复要求。不同任务各自保持消息顺序，不共享编号，也不把普通进度消息合并到一个持续增长的文件。已经写入文件的大型材料只发送链接，不在消息中复制正文。

Codex 消息留档只解决人类无法直接审阅 agent 间交流的问题，不改变任务权限、来源边界、失败计数或 checkpoint 规则。

## Subagent 工具调用约束

direct collaboration tool call 指直接调用界面提供的 collaboration 工具，而不是在 `functions.exec` 的 JavaScript 中调用 `tools.*`。这两处可用工具集合不同，因此在后者找不到 collaboration 工具并不证明工具不可用。

- 管理 subagent 时，`spawn_agent`、`send_message`、`followup_task`、`wait_agent`、`interrupt_agent`、`list_agents` 必须作为 direct collaboration tool call 使用。
- 不得在 `functions.exec` 中调用 `tools.spawn_agent`、`tools.wait_agent`、`tools.list_agents` 等 collaboration tool，也不得通过 `ALL_TOOLS`、shell 或其他 namespace 间接查找、模拟或替代它们。
- collaboration tool 不存在于 `functions.exec` 的 `tools.*` namespace，不代表该 tool 不可用；应直接使用当前暴露的 collaboration tool 定义。
- 如果 direct collaboration tool 本身调用失败，只根据其 tool schema 修正参数或 recipient，不得退回 `functions.exec`。
