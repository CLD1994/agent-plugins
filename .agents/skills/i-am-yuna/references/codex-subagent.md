# Codex Harness 交互

只在当前环境是 Codex Harness，且根级 Main Agent 或嵌套调度者要创建、联系、恢复、评审、整合或等待自己的 subagent 时阅读。本文件只定义 Harness 工具参数、消息留档和等待方式；角色、切片、评审和升级决定以 SKILL.md 为准。

## 角色加载

- 根级 Main Agent 在每次新会话开始和系统上下文压缩恢复后立即加载 `i-am-yuna`；随后按当前任务读取本文件和经验文档。
- 嵌套调度者在首次管理自己的下级（包括创建、联系、恢复、评审、整合或等待）前加载 `i-am-yuna`；上下文压缩恢复后，在继续管理下级前重新加载。涉及具体交互时，再按下列工具调用规则执行。
- 只完成父任务且不管理下级的纯执行者不加载 `i-am-yuna`。
- 嵌套调度者仍受父任务的目标、授权、范围、起始基线和共享约束限制；本 Harness 的交互参数不能扩大这些权限。需要改变继承条件时，先向父级报告。

## 创建与等待

- 每次调用 `spawn_agent` 都显式给出 `fork_turns` 和 `model`。通常使用 `fork_turns: "none"`，让任务单和精确绝对路径提供最小完整上下文。
- 执行级、思考级和顾问级的 `model` 都从本会话已经记录的模型映射取得，不能省略。顾问级的 `reasoning_effort` 为 `ultra`；Harness 支持时其他层级为 `max`。
- 当前层级调度者没有其他工作且仍有 subagent 运行时，只调用 `wait_agent`，并把 `timeout_ms` 设为允许的最大值 `3600000`。

## 消息留档

消息文件按接收该任务的 subagent 目录和该任务自己的回合编号保存。根级 Main 与每个嵌套调度者都为自己直接管理的任务维护独立消息序列；会话名、任务名和回合编号必须保持一致，例如：

- Main 的任务单或第一条指令：/tmp/agents/2026-09-03-api-migration/subagent/schema-audit/main-001.md
- 该 subagent 的回复：/tmp/agents/2026-09-03-api-migration/subagent/schema-audit/subagent-002.md
- 同一切片的第二条补充：/tmp/agents/2026-09-03-api-migration/subagent/schema-audit/main-003.md

每个单向消息都占用本切片的下一个本地回合。调用 `spawn_agent`、`send_message` 或 `followup_task` 前，负责该范围的调度者将 `message` 参数原样写入相应的 `main-{回合}.md`。任务单要求 subagent 在发送消息或给出最终回复前，将原文写入相应的 `subagent-{回合}.md`。回合编号使用三位十进制递增格式，例如 001。嵌套调度者只记录自己的子树消息，不读取父级或兄弟切片的过程消息。

大型材料已经写入文件时，消息只引用精确绝对路径，避免重复传输全文。希望目标 agent 阅读材料时，给出该材料的精确绝对路径，而不是只给主题或简称。

## 作用域声明

每份任务单都写明当前作用域明确禁止 subagent 加载的技能。不得一概禁止所有 skill。根目录规则禁止纯执行者加载 `i-am-yuna`；负责向下管理的嵌套调度者按 SKILL.md 的加载条件加载，其他 skill 是否可用由具体任务和作用域决定。
