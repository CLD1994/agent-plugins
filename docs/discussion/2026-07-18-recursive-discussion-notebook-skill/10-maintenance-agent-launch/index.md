# D10：维护 Agent 的启动方式

> 状态：`已确认，第一版范围已收敛`
>
> 产出：[结论汇总](../conclusions.md)
>
> 父主题：[递归讨论草稿方法论与 Skill 设计](../../2026-07-18-recursive-discussion-notebook-skill.tmp.md)
>
> 依赖：[D6.2 维护触发与压缩前子 Agent](../06-2-maintenance-triggers-and-hooks.md)、[D8 独立项目与跨平台插件结构](../08-project-and-platform-structure.md)、[D9 Python、uv 与首次环境搭建](../09-python-uv-and-bootstrap.md)

> 第一版路线已由 [D10.15](10-15-postcompact-context-cost.md)、[D10.18](10-18-semantic-maintenance-checkpoint.md) 和 [D10.19](10-19-precompact-necessity.md) 重新收敛：即时维护是正常路径，不保存逐轮可靠标记，也不注册 `PreCompact`。下文关于压缩前自动维护的内容只保留为历史推理。

## 第一版当前路线

- 重要语义在讨论发生时立即写入草稿；
- 多文件结构操作由耐久操作日志和内容摘要保护；
- 宿主上下文压缩不被插件阻塞；
- 压缩、恢复或适用的启动入口只重新绑定 skill 和草稿网络；
- 已知异常通过显式恢复处理，不自动计算未知语义缺口；
- 第一版不建立压缩前任务、等待、后台维护队列或任务接管协议。

## 历史问题

D6.2 已经确认压缩前维护应尽量放进独立上下文，避免维护过程继续挤占即将压缩的主上下文。D10 需要确定 Codex 与 Claude Code 怎样真正启动这次维护，以及共同协议和平台适配层在哪里分开。

## 历史条件边界

压缩前钩子本身是确定性的协调器，不是维护 Agent；维护 Agent 如需运行，只能由协调器间接启动或通过耐久任务移交。

这项边界已确认。它修正的是 D6.2 的具体实现方式，不改变“语义维护应尽量放进独立上下文”的目标。

## 历史平台限制

两个平台都提供 `PreCompact`（压缩前）事件，但目前都不能在这个事件上直接配置 Agent 类型处理器。

### Codex

- 支持 `PreCompact`，并允许命令处理器阻止或放行压缩；
- 当前实际执行的只有 `command`（普通命令）处理器；
- `prompt` 和 `agent` 虽然能够被配置解析，但会被跳过；
- 异步命令同样尚未执行。

### Claude Code

- 支持 `PreCompact`，命令可以阻止或放行压缩；
- Claude Code 虽然在部分生命周期事件上支持 `agent` 处理器，但 `PreCompact` 明确不在其中；
- `PreCompact` 只支持 `command`、`http` 和 `mcp_tool`，不支持 `prompt` 或 `agent`。

## 历史上对 D6.2 的修正

“压缩前钩子唤起专用维护 Agent”仍然是目标，但不能理解为“平台原生 Agent 钩子”。两个平台共同可依赖的入口只能先是一个确定性的普通命令。

如果仍要使用独立维护 Agent，就必须由普通命令间接启动另一次 Agent 运行，或者先耐久保存维护任务，再由其他时机或常驻消费者处理。具体选择尚未确认。

## 历史候选：平台间接启动入口

两个平台都提供面向脚本的无界面 Agent 入口：

- Codex 使用 `codex exec`，可以设置工作区写入权限、从标准输入接收完整任务，并输出机器可读事件；
- Claude Code 使用 `claude -p`，可以限制工具与回合数，并输出结构化结果。

协调器因此可以创建一次新的、独立的维护会话，而不需要恢复即将压缩的主会话。新会话只接收 D6.2 规定的有界交接材料。

## 历史候选路线

### A：同步启动并等待

协调器先耐久保存任务，再启动新的无界面 Agent 进程，并等待它完成。维护结果和机械检查通过后放行压缩；超时或失败时保留任务，明确警告后按应急路径放行。

优点是主会话在丢弃上下文前就能完成维护，最符合 D6.2。代价是压缩会变慢，还要处理超时、认证、重复触发和额外模型用量。

### B：只保存任务，延后处理

协调器只把交接材料保存到待处理区，立即放行压缩。下一次会话启动、下一次正常维护时机或其他明确触发点再运行维护 Agent。

优点是压缩很快、故障面小。代价是压缩后到维护完成前存在空窗，主会话可能继续写草稿，使待处理任务过时或产生并发冲突。

### C：常驻本地维护服务

协调器把任务放入队列，由一直运行的本地服务消费。它可以集中处理排队、重试和限流。

优点是钩子很轻，长期吞吐和重试能力最好。代价是要维护后台进程的安装、升级、认证、安全、日志和跨平台生命周期，第一版成本最高。

## 当时确认、现已退出第一版的运行路线

第一版以 A 为正常路径，同时把 B 作为 A 失败后的耐久退路；不在第一版建立 C。

这不是让钩子直接承担语义维护。钩子只负责保存任务、启动并等待独立 Agent、验证完成凭据和决定是否放行。

还需要防止维护会话再次触发同一启动流程。平台适配器启动子进程时应设置明确的“维护子进程”标记；子进程中的同一压缩前钩子看到标记后只允许正常压缩，不再创建第二个维护 Agent。

## 子主题

- [D10.1 复用专用维护会话](10-1-reusable-maintenance-session.md) — `历史条件方案，第一版不采用`
- [D10.2 Codex 维护会话的显式压缩](10-2-codex-explicit-compaction.md) — `历史条件方案，第一版不采用`
- [D10.3 主会话等待与失败放行](10-3-wait-and-release.md) — `历史方案，第一版不采用`
- [D10.4 维护 Agent 的最小权限与草稿目录边界](10-4-permissions-and-notebook-root.md) — `已确认`
- [D10.5 草稿结构变化如何安全落盘](10-5-structural-operations.md) — `已确认`
- [D10.6 多文件结构操作的崩溃恢复](10-6-structural-operation-recovery.md) — `已确认`
- [D10.7 回收站的权威位置与崩溃语义](10-7-trash-authority.md) — `已确认`
- [D10.8 系统回收站移交失败的放行边界](10-8-trash-handoff-failure.md) — `已确认`
- [D10.9 Agent 文件能力与内部目录隔离](10-9-file-capability-isolation.md) — `未来安全前置条件，第一版不实现`
- [D10.10 CLI 与 MCP 的共同核心和失败接续](10-10-cli-mcp-shared-core.md) — `已确认，第一版 CLI-only`
- [D10.11 待处理任务的占用与安全接管](10-11-task-claim-and-takeover.md) — `历史候选，第一版不采用`
- [D10.12 压缩与草稿维护的执行时序](10-12-compaction-maintenance-scheduling.md) — `历史候选，第一版不采用`
- [D10.13 压缩后由主 Agent 维护草稿](10-13-main-agent-after-compaction.md) — `历史候选，第一版不采用`
- [D10.14 压缩前的确定性增量捕获](10-14-deterministic-precompact-capture.md) — `已确认`
- [D10.15 压缩后谁承担语义整理的上下文成本](10-15-postcompact-context-cost.md) — `已确认`
- [D10.16 压缩与会话迁移后的 skill 重新绑定](10-16-skill-reactivation.md) — `已确认`
- [D10.17 全新会话怎样取得草稿网络绑定](10-17-new-session-binding.md) — `已确认`
- [D10.18 真的需要每轮可靠标记吗](10-18-semantic-maintenance-checkpoint.md) — `已确认`
- [D10.19 第一版还需要压缩前钩子吗](10-19-precompact-necessity.md) — `已确认`
- [D10.20 第一版还需要 MCP 与独立维护 Agent 吗](10-20-mcp-and-maintenance-agent-scope.md) — `已确认`

## 第一版最终边界

第一版只交付 skill、共同 Python 核心、CLI、压缩后重新绑定入口、结构操作日志、回收站和检查器。不交付 `PreCompact`、MCP、无界面维护 Agent、专用会话、后台任务或任务接管协议。

D10 第一版范围已收敛。下一项转入 [D11 第一版检查器范围](../11-checker-scope.md)。

## 官方资料

- [Codex Hooks：当前只执行 command 处理器](https://learn.chatgpt.com/docs/hooks)
- [Codex：使用 codex exec 进行非交互运行](https://learn.chatgpt.com/docs/non-interactive-mode)
- [Claude Code Hooks：PreCompact 不支持 prompt 或 agent 处理器](https://code.claude.com/docs/en/hooks#prompt-based-hooks)
- [Claude Code：使用 claude -p 进行非交互运行](https://code.claude.com/docs/en/headless)
