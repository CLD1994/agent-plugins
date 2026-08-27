# Codex subagent 使用指南

- Main Agent 手头上没有任务需要执行时，只需要等待 subagent 唤醒，`wait_agent` 的 `timeout_ms` 取最大值
- 每次调用 `spawn_agent` 都必须显式传入 `fork_turns`，通常使用 `"none"`；任务所需上下文应在 message 中用最少的自包含说明和精确文件路径提供，避免默认完整继承造成不必要的 token 消耗
- Main Agent 通过 `send_message` 和 `followup_task` 传递的消息目前在用户视图上不可见, `message` 参数传递的内容必须原样写入 `<workspace>/tmp/<session>/subagent/<subagent_name>/main-<trun>.md`
