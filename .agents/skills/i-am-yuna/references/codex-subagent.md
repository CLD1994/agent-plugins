# Codex subagent 使用指南

- Main Agent 手头上没有任务需要执行时，只需要等待 subagent 唤醒，`wait_agent` 的 `timeout_ms` 取最大值
- 每次调用 `spawn_agent` 都必须显式传入 `fork_turns`，通常使用 `"none"`；任务所需上下文应在 message 中用最少的自包含说明和精确文件路径提供，避免默认完整继承造成不必要的 token 消耗
- Main Agent 通过 `send_message` 和 `followup_task` 传递的消息目前在用户视图上不可见, `message` 参数传递的内容必须原样写入 `/tmp/agents/<session>/subagent/<subagent_name>/main-<trun>.md`
- subagent 通过 `send_message` 传递的消息或完成任务产生的回复目前也在用户视图上不可见， `message` 参数传递的内容和完成任务产生的回复必须原样写入 `/tmp/agents/<session>/subagent/<subagent_name>/subagent-<trun>.md`
- agent 之间的交互，可以通过引用文件路径的方式传递消息。不必将消息写入文件 `<agent-type>-<trun>.md` 后原样传输，这样会增加 token 消耗
- Main Agent 需要在对 subagent 的指令中明确要求需要将消息写入文件并以文件引用的方式传递，因为不保证 subagent 会阅读该指南
