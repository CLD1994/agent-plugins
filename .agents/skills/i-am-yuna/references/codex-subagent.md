# Codex Harness 交互

只在当前环境是 Codex Harness，且 Main Agent 要创建、联系或等待 subagent 时阅读。本文件只定义 Harness 工具参数、消息留档和等待方式；角色、切片、评审和升级决定以 SKILL.md 为准。

## 创建与等待

- 每次调用 spawn_agent 都显式给出 fork_turns 和 model。通常使用 fork_turns: "none"，让任务单和精确绝对路径提供最小完整上下文。
- 执行级、思考级和顾问级的 model 都从本会话已经记录的模型映射取得，不能省略。顾问级的 reasoning_effort 为 ultra；Harness 支持时其他层级为 max。
- Main 没有其他工作且仍有 subagent 运行时，只调用 wait_agent，并把 timeout_ms 设为允许的最大值 3600000。

## 消息留档

消息文件按接收该任务的 subagent 目录和该任务自己的回合编号保存。会话名、任务名和回合编号必须保持一致，例如：

- Main 的任务单或第一条指令：/tmp/agents/2026-09-03-api-migration/subagent/schema-audit/main-001.md
- 该 subagent 的回复：/tmp/agents/2026-09-03-api-migration/subagent/schema-audit/subagent-002.md
- 同一切片的第二条补充：/tmp/agents/2026-09-03-api-migration/subagent/schema-audit/main-003.md

每个单向消息都占用本切片的下一个本地回合。调用 spawn_agent、send_message 或 followup_task 前，Main 将 message 参数原样写入相应的 main-{回合}.md。任务单要求 subagent 在发送消息或给出最终回复前，将原文写入相应的 subagent-{回合}.md。回合编号使用三位十进制递增格式，例如 001。

大型材料已经写入文件时，消息只引用精确绝对路径，避免重复传输全文。希望目标 agent 阅读材料时，给出该材料的精确绝对路径，而不是只给主题或简称。

## 作用域声明

每份任务单都写明当前作用域明确禁止 subagent 加载的技能。不得一概禁止所有 skill。根目录当前规则只禁止 subagent 加载 i-am-yuna；其他 skill 是否可用，由具体任务和作用域决定。
