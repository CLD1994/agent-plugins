# Draft subagent 派遣记录

本文件由 Main 维护，仅供调度者读取。记录派遣、任务版本、交付处理和资源归属；恢复上下文从 [checkpoints 目录](/workspaces/agent-plugins/tmp/agents/2026-09-09-draft-delivery/checkpoints/) 中编号最大的文件进入。技术分析与验证细节见各任务报告。

## 调度约定

- 根工作目录：`/workspaces/agent-plugins`。
- Main：`gpt-6-astra`。执行级：`gpt-5.6-luna/max`；思考级：`gpt-5.6-terra/high..max`；顾问级：`gpt-6-astra/high..max`。下表缩写 luna、terra、astra 均指此映射。
- 共同规则 v1：主工作区根/Draft AGENTS.md、i-am-yuna 及调度记录、worktree、Codex 参考、CONTEXT-MAP.md 路由下的现行设计；各任务指定精确来源和基线。
- 授权：继续 Draft 交付并使用 subagent，允许成组复用符合合同的代码和测试；已批准新建独立 frontmatter/根锁思考级候选。初始化边界、首条业务操作和新公共协议按对应决定处理。
- 初始主线：`ddcd8c71694f9c47825b5ee3bfc9034a5718716e`；当前根锁设计基线：`236b8b3a882a53f2b8c369c4ed87d4567870d4d5`。其他任务的固定设计与代码基线以任务说明为准。
- 每项任务使用自己的不可变递增消息文件；任务说明、消息、结果和 checkpoint 保存在任务工作区。无其他可推进工作时响应式调用 `wait_agent(3600000)`。
- 清理只处理用户明确指定的资源；未采用候选与其他历史工作区继续保留。

## 当前派遣

| 任务与材料 | 内容版本 | Agent | 角色 / 模型 | 状态与处理 |
| --- | --- | --- | --- | --- |
| 根锁后续执行 | lock v4；Main 接续 | `/root`，不派遣 subagent | Main 直接执行并向用户汇报 | 共享 I/O 清理已按用户批准范围交付，候选 HEAD bdd20a2；Main 接续根锁整体源码核验。任务与交付见 .scratch/draft-root-lock-followup/issues/01-recovery-result-and-evidence.md。未采用、未 rebase；原执行级三次失败保留。 |

## 已完成或停止的派遣

| 任务与材料 | 内容版本 | Agent | 角色 / 模型 | 交付与采用处理 |
| --- | --- | --- | --- | --- |
| [root-lock-review](/workspaces/agent-plugins/.worktrees/draft-lock-run-20260909/tmp/agents/2026-09-09-draft-delivery/root-lock-direct-review/main-017.md) | lock v4 | `/root/root_lock_direct_review` | 独立评审 / terra/max | round-5 不通过；审计 false PASS/漏列 create、restore package 和同-token 验收缺口；历史 red 无原始证据。完整报告和 plan-009 已读，候选未采用。 |
| [root-lock-repair](/workspaces/agent-plugins/.worktrees/draft-lock-run-20260909/tmp/agents/2026-09-09-draft-delivery/root-lock-direct/main-045.md) | lock v4 | `/root/root_lock_direct_resume` | 执行 / luna/max | result-004/supplement-003 已交付；Main 全文读取，交 round-5；历史 red 与当前覆盖分别核验，未采用。 |
| [root-lock-plan](/workspaces/agent-plugins/.worktrees/draft-lock-run-20260909/tmp/agents/2026-09-09-draft-delivery/root-lock-direct-review/main-015.md) | lock v4 | `/root/root_lock_direct_review` | 计划核对与整理 / terra/max | scope-check-002、plan-008 已完成；Main 全文核对后批准交执行，时序解释不改变固定范围和失败计数。 |
| [root-lock-review](/workspaces/agent-plugins/.worktrees/draft-lock-run-20260909/tmp/agents/2026-09-09-draft-delivery/root-lock-direct-review/main-011.md) | lock v4 | `/root/root_lock_direct_review` | 独立评审 / terra/max | round-4 不通过，R3-03/R3-05/F04 通过；effect 覆盖、fresh-public 四格、AST 与历史 red 证据缺口保留。plan-006 已读，切点语义待核清。 |
| [root-lock-repair](/workspaces/agent-plugins/.worktrees/draft-lock-run-20260909/tmp/agents/2026-09-09-draft-delivery/root-lock-direct/main-043.md) | lock v4 | `/root/root_lock_direct_resume` | 执行 / luna/max | result-003、supplement-002 和实际审计脚本已交付；声明确定分区完成，Main 全文读取后交独立 round-4；未采用。 |
| [root-lock-plan](/workspaces/agent-plugins/.worktrees/draft-lock-run-20260909/tmp/agents/2026-09-09-draft-delivery/root-lock-direct-review/main-009.md) | lock v4 | `/root/root_lock_direct_review` | 修复计划 / terra/max | repair-plan-005 已交付；Main 全文核对后批准，证据规则澄清不改变范围及失败计数。 |
| [root-lock-review](/workspaces/agent-plugins/.worktrees/draft-lock-run-20260909/tmp/agents/2026-09-09-draft-delivery/root-lock-direct-review/main-007.md) | lock v4 | `/root/root_lock_direct_review` | 独立评审 / terra/max | round-3不通过；F04已通过，通用effect身份、fixture/类型与验收缺口仍需修复。Main已全文读取报告及plan004。 |
| [root-lock-repair](/workspaces/agent-plugins/.worktrees/draft-lock-run-20260909/tmp/agents/2026-09-09-draft-delivery/root-lock-direct/task-002.md) | lock v4 | `/root/root_lock_direct_resume` | 执行 / luna/max | execution-result-002与补充验收已交付；声明确定分区完成，wrapper停止，交独立评审。 |
| [root-lock-plan](/workspaces/agent-plugins/.worktrees/draft-lock-run-20260909/tmp/agents/2026-09-09-draft-delivery/root-lock-direct-review/main-005.md) | lock v4 | `/root/root_lock_direct_review` | 修复计划 / terra/max | repair-plan-003 已交付，Main全文核对后批准确定分区并交执行；wrapper有可达旧结果歧义，未批准新语义。 |
| [root-lock-scope](/workspaces/agent-plugins/.worktrees/draft-lock-run-20260909/tmp/agents/2026-09-09-draft-delivery/root-lock-direct-review/main-003.md) | lock v3 | `/root/root_lock_direct_review` | 范围核对与修复计划 / terra/max | scope-check-001 与 repair-plan-002 已交付；CLI 新错误路径可达已核实，需范围授权。F08 需按既有 root/layout 分类，F05 wrapper 仍需保持协议边界，计划未批准，不增加失败。 |
| [root-lock-direct-review](/workspaces/agent-plugins/.worktrees/draft-lock-run-20260909/tmp/agents/2026-09-09-draft-delivery/root-lock-direct-review/task.md) | lock v3 | `/root/root_lock_direct_review` | 独立评审 / terra/max | [round-2](/workspaces/agent-plugins/.worktrees/draft-lock-run-20260909/tmp/agents/review-notes/draft-root-lock-primitive/2026-09-09-round-2.md) 不通过；八项发现及 repair-plan-001 已交付，Main 已全文阅读，未批准范围外修改，未交修复执行。 |
| [root-lock-direct](/workspaces/agent-plugins/.worktrees/draft-lock-run-20260909/tmp/agents/2026-09-09-draft-delivery/root-lock-direct/main-021.md) | lock v3 | `/root/root_lock_direct_resume` | 执行 / luna/max | 恢复完成并交付 execution-result-001；候选冻结，未采用，交独立评审。执行 checkpoint 002/003 已建立。 |
| [root-lock-direct](/workspaces/agent-plugins/.worktrees/draft-lock-run-20260909/tmp/agents/2026-09-09-draft-delivery/root-lock-direct/main-001.md) | lock v3 | `/root/root_lock_direct` | 执行 / luna/max | 进程退出，未交付完整候选；消息 002–020 已按用户要求整理为执行目录 checkpoint 001，同级接续，不计失败。 |
| [root-lock-plan](/workspaces/agent-plugins/.worktrees/draft-lock-run-20260909/tmp/agents/2026-09-09-draft-delivery/root-lock-review/main-010.md) | lock v3 | `/root/root_lock_candidate_review` | 计划编写 / terra/max | [execution-plan-004](/workspaces/agent-plugins/.worktrees/draft-lock-run-20260909/tmp/agents/2026-09-09-draft-delivery/root-lock-review/execution-plan-004.md) 已完成，Main 全文核对后批准，交独立执行上下文。 |
| [root-lock-plan](/workspaces/agent-plugins/.worktrees/draft-lock-run-20260909/tmp/agents/2026-09-09-draft-delivery/root-lock-review/main-006.md) | lock v3 | `/root/root_lock_candidate_review` | 计划编写 / terra/max | [execution-plan-002](/workspaces/agent-plugins/.worktrees/draft-lock-run-20260909/tmp/agents/2026-09-09-draft-delivery/root-lock-review/execution-plan-002.md) 已交付；未作执行依据，后续派遣见当前任务。 |
| [frontmatter-review](/workspaces/agent-plugins/.worktrees/draft-frontmatter-run-20260909/tmp/agents/2026-09-09-draft-delivery/frontmatter-review/main-009.md) | 1 | `/root/frontmatter_candidate_review` | 独立评审 / terra/max | [round-3](/workspaces/agent-plugins/.worktrees/draft-frontmatter-run-20260909/tmp/agents/review-notes/draft-frontmatter-foundation/2026-09-09-round-3.md) 通过已定义基础合同；Main 已采用，未关闭 05，库失败分区保留。 |
| [frontmatter](/workspaces/agent-plugins/.worktrees/draft-frontmatter-run-20260909/tmp/agents/2026-09-09-draft-delivery/frontmatter/main-007.md) | frontmatter v1 | `/root/frontmatter_run` | 执行 / terra/max | result-003 第三份冻结候选经 round-3 通过并采用至主线 c02ffb6。 |
| [root-lock-review](/workspaces/agent-plugins/.worktrees/draft-lock-run-20260909/tmp/agents/2026-09-09-draft-delivery/root-lock-review/task.md) | 1 | `/root/root_lock_candidate_review` | 独立评审 / terra/max | [round-1](/workspaces/agent-plugins/.worktrees/draft-lock-run-20260909/tmp/agents/review-notes/draft-root-lock-primitive/2026-09-09-round-1.md) 不通过；五项缺陷已接受，范围扩展已获用户批准，转 lock v3。 |
| [frontmatter](/workspaces/agent-plugins/.worktrees/draft-frontmatter-run-20260909/tmp/agents/2026-09-09-draft-delivery/frontmatter/main-005.md) | frontmatter v1 | `/root/frontmatter_run` | 执行 / terra/max | result-002 第二份候选经 round-2 不通过；未采用，完整修复指导交原执行者。 |
| [root-lock](/workspaces/agent-plugins/.worktrees/draft-lock-run-20260909/tmp/agents/2026-09-09-draft-delivery/root-lock/task.md) | lock v2 | `/root/root_lock_run` | 执行 / terra/max | result-004 重新声明冻结阶段候选完成；单元 22、集成 14 分开通过，未采用，交独立评审。此前撤回声明不计失败。 |
| [frontmatter-review](/workspaces/agent-plugins/.worktrees/draft-frontmatter-run-20260909/tmp/agents/2026-09-09-draft-delivery/frontmatter-review/task.md) | 1 | `/root/frontmatter_candidate_review` | 独立评审 / terra/max | round-1 四项缺陷已接受；repair-plan-002 已交付，修复交原执行者；命令路径按执行消息核实。 |
| [request-foundation-review](/workspaces/agent-plugins/tmp/agents/2026-09-09-draft-delivery/request-foundation-review/main-005.md) | 2 | `/root/request_foundation_review` | 独立评审 / terra/high | [round-3](/workspaces/agent-plugins/tmp/agents/review-notes/request-foundation/2026-09-09-round-3.md) 完成；R1–R3 主问题闭合，嵌套分支及同阶段定位规则待用户确认，未派发实现。 |
| [request-boundary-plan-review](/workspaces/agent-plugins/tmp/agents/2026-09-09-draft-delivery/request-boundary-plan-review/main-007.md) | 2 | `/root/request_plan_review` | 计划编写 / terra/max | [repair-plan-003](/workspaces/agent-plugins/tmp/agents/2026-09-09-draft-delivery/request-boundary-plan-review/repair-plan-003.md) 经独立 round-3 复核；剩余嵌套及同阶段规则待用户确认，未派实现。 |
| [request-foundation-review](/workspaces/agent-plugins/tmp/agents/2026-09-09-draft-delivery/request-foundation-review/task.md) | 1 | `/root/request_foundation_review` | 独立评审 / terra/high | [正式报告](/workspaces/agent-plugins/tmp/agents/review-notes/request-foundation/2026-09-09-round-2.md) 已交付；用户已确定重复操作标识先报 repeated_field，完整计划待修订。 |
| [yaml-ticket-review](/workspaces/agent-plugins/tmp/agents/2026-09-09-draft-delivery/yaml-ticket-review/task.md) | 1 | `/root/yaml_ticket_review` | 独立评审 / terra/high | round-2 通过；六文档已采用，见下方采用决定。 |
| [page-read-facts](/workspaces/agent-plugins/tmp/agents/2026-09-09-draft-delivery/page-read-facts/) | 1 | `/root/page_read_facts` | 事实核对 / luna/max | 已完成，结果 仅作来源调查；历史或跨候选测试不作当前验收。 |
| [root-lock-facts](/workspaces/agent-plugins/tmp/agents/2026-09-09-draft-delivery/root-lock-facts/) | 1 | `/root/root_lock_facts` | 事实核对 / luna/max | 已完成，结果 用于确定原语和后续入口范围。 |
| [request-facts](/workspaces/agent-plugins/tmp/agents/2026-09-09-draft-delivery/request-facts/) | 1 | `/root/request_facts` | 事实核对 / luna/max | 停止继续派遣；结果 只保留源码入口，动态验证声明不采用，探针/资源证据缺失。 |
| [page-read-boundary](/workspaces/agent-plugins/tmp/agents/2026-09-09-draft-delivery/page-read-boundary/) | 1 | `/root/page_read_boundary` | 独立评审 / terra/max | 用户中断；round-1 为占位，无有效评审结论。 |
| [frontmatter](/workspaces/agent-plugins/.worktrees/draft-page-read-v2/tmp/agents/2026-09-09-draft-delivery/frontmatter/) | frontmatter v1 | `/root/frontmatter`；后续 Main 延续 | 执行 / luna/max 阶段 | 已停止；结果 未采用，失败记录见下表。 |
| [lock-primitive](/workspaces/agent-plugins/.worktrees/draft-root-lock-recovery/tmp/agents/2026-09-09-draft-delivery/lock-primitive/) | lock v1 → v2 | `/root/lock_primitive`；后续 Main 延续 | 执行 / luna/max 阶段 | 已停止；结果 未采用，失败记录见下表。 |
| [frontmatter-review](/workspaces/agent-plugins/.worktrees/draft-page-read-v2/tmp/agents/2026-09-09-draft-delivery/frontmatter-review/) | 1 | `/root/frontmatter_review` | 独立评审 / terra/max | 完成，frontmatter round-1 不通过。 |
| [frontmatter-review-2](/workspaces/agent-plugins/.worktrees/draft-page-read-v2/tmp/agents/2026-09-09-draft-delivery/frontmatter-review-2/) | 1 | 历史记录未保存 Agent 标识 | 独立评审 / terra/max | 完成，frontmatter round-2 不通过。 |
| [frontmatter-review-3](/workspaces/agent-plugins/.worktrees/draft-page-read-v2/tmp/agents/2026-09-09-draft-delivery/frontmatter-review-3/) | 1 | 沿用第 2 轮评审者 | 独立评审 / terra/max | 完成，frontmatter round-3 不通过。 |
| [lock-primitive-review](/workspaces/agent-plugins/.worktrees/draft-root-lock-recovery/tmp/agents/2026-09-09-draft-delivery/lock-primitive-review/) | 1 | `/root/lock_primitive_review` | 独立评审 / terra/max | 完成，lock round-1 不通过；[修复计划](/workspaces/agent-plugins/.worktrees/draft-root-lock-recovery/tmp/agents/2026-09-09-draft-delivery/lock-primitive-review/repair-plan.md) 已批准形成 v2 范围。 |
| [lock-primitive-review-2](/workspaces/agent-plugins/.worktrees/draft-root-lock-recovery/tmp/agents/2026-09-09-draft-delivery/lock-primitive-review-2/) | 1 | 历史记录未保存 Agent 标识 | 独立评审 / terra/max | 完成，lock round-2 不通过。 |
| [lock-primitive-review-3](/workspaces/agent-plugins/.worktrees/draft-root-lock-recovery/tmp/agents/2026-09-09-draft-delivery/lock-primitive-review-3/) | 1 | 沿用第 2 轮评审者 | 独立评审 / terra/max | 完成，lock round-3 不通过。 |
| [lock-primitive-review-4](/workspaces/agent-plugins/.worktrees/draft-root-lock-recovery/tmp/agents/2026-09-09-draft-delivery/lock-primitive-review-4/) | 1 | 沿用第 2 轮评审者 | 独立评审 / terra/max | 完成，lock round-4 不通过。 |
| [page-model-consumers](/workspaces/agent-plugins/tmp/agents/2026-09-09-draft-delivery/page-model-consumers/) | 1 | `/root/page_model_consumers` | 事实核对 / luna/max | 已完成，结果 作为页面边界整合输入。 |
| [page-ticket-ownership](/workspaces/agent-plugins/tmp/agents/2026-09-09-draft-delivery/page-ticket-ownership/) | 1 | `/root/page_ticket_ownership` | 事实核对 / luna/max | 已完成，结果 的票据观察交独立语义整合。 |
| [page-parser-seam](/workspaces/agent-plugins/tmp/agents/2026-09-09-draft-delivery/page-parser-seam/) | 1 | `/root/page_parser_seam` | 事实核对 / luna/max | 已完成，结果 作为页面边界整合输入。 |
| [request-boundary-facts](/workspaces/agent-plugins/tmp/agents/2026-09-09-draft-delivery/request-boundary-facts/) | 1 | `/root/request_boundary_facts` | 事实核对 / luna/max | 已完成，结果 仅证明静态实现事实。 |
| [page-read-boundary-2](/workspaces/agent-plugins/tmp/agents/2026-09-09-draft-delivery/page-read-boundary-2/) | 1 | 历史记录未保存 Agent 标识 | 独立评审 / terra/max | 已完成 round-2/3；当前采用范围见下方评审处理。 |
| [request-boundary-plan](/workspaces/agent-plugins/tmp/agents/2026-09-09-draft-delivery/request-boundary-plan/) | 1 | `/root/request_boundary_plan` | 计划编写 / terra/max | 结果 已交付，正式评审不通过，未作为执行依据。 |
| [root-lock-isolation-facts](/workspaces/agent-plugins/tmp/agents/2026-09-09-draft-delivery/root-lock-isolation-facts/) | 1 | `/root/root_lock_isolation_facts` | 事实核对 / luna/max | 已完成，结果 测试哈希因后续变动过时；不用作新候选采用依据。 |
| frontmatter 思考级候选 | frontmatter v1 | `/root/frontmatter_terra`、`/root/frontmatter_thought_1` | 执行 / terra/max | 用户取消；共用工作区已清理，无候选采用，不计有效失败。 |
| 根锁思考级候选 | lock v2 | `/root/root_lock_terra`、`/root/root_lock_thought_1` | 执行 / terra/max | 用户取消；共用工作区已清理，无候选采用，不计有效失败。 |
| [yaml-activation-authority](/workspaces/agent-plugins/tmp/agents/2026-09-09-draft-delivery/yaml-activation-authority/) | 1 | `/root/yaml_activation_authority` | 事实核对 / luna/max | 结果 已完成；接受来源事实，末尾决策清单待独立裁决。 |
| [yaml-activation-boundary](/workspaces/agent-plugins/tmp/agents/2026-09-09-draft-delivery/yaml-activation-boundary/) | 1 | `/root/yaml_activation_boundary` | 独立评审 / terra/max | round-4 及修订正文已交付；核心归属裁决采用。搜索曾回显禁止来源，不能作为零接触隔离证据；实际六文件改动另派独立核验。 |
| [frontmatter](/workspaces/agent-plugins/.worktrees/draft-frontmatter-run-20260909/tmp/agents/2026-09-09-draft-delivery/frontmatter/) | frontmatter v1 | `/root/frontmatter_run` | 执行 / terra/max | 首份完整候选正式评审不通过，未采用；等待完整修复依据。 |
| [request-boundary-plan-review](/workspaces/agent-plugins/tmp/agents/2026-09-09-draft-delivery/request-boundary-plan-review/) | 1 | `/root/request_plan_review` | 独立评审 / terra/max | round-1 和两份修订依据已交付；[repair-plan-002](/workspaces/agent-plugins/tmp/agents/2026-09-09-draft-delivery/request-boundary-plan-review/repair-plan-002.md) 收窄到私有 F1，交独立执行前核验。 |

## 任务版本与失败计数

只有声明完成的候选经正式评审不通过才记有效失败；中断、取消和缺失交付不计入。Main 延续的旧候选沿用当时固定阶段的计数，不重置、不当作当前思考级候选。

| 固定任务 | 版本范围 | 执行级有效失败 | 思考级有效失败 | 证据及处理 |
| --- | --- | ---: | ---: | --- |
| frontmatter v1 | 严格头部解析、直接诊断、相关测试及 YAML 依赖；不激活 reader | 3 | 2 | 执行级 [round-1](/workspaces/agent-plugins/.worktrees/draft-page-read-v2/tmp/agents/review-notes/draft-frontmatter-foundation/2026-09-09-round-1.md)、[round-2](/workspaces/agent-plugins/.worktrees/draft-page-read-v2/tmp/agents/review-notes/draft-frontmatter-foundation/2026-09-09-round-2.md)、[round-3](/workspaces/agent-plugins/.worktrees/draft-page-read-v2/tmp/agents/review-notes/draft-frontmatter-foundation/2026-09-09-round-3.md)；思考级 [round-1](/workspaces/agent-plugins/.worktrees/draft-frontmatter-run-20260909/tmp/agents/review-notes/draft-frontmatter-foundation/2026-09-09-round-1.md)、[round-2](/workspaces/agent-plugins/.worktrees/draft-frontmatter-run-20260909/tmp/agents/review-notes/draft-frontmatter-foundation/2026-09-09-round-2.md) 不通过；第 3 次尝试已通过并采用，不增加失败计数。 |
| lock v1 | 根锁原语及直接测试 | 1 | — | [round-1](/workspaces/agent-plugins/.worktrees/draft-root-lock-recovery/tmp/agents/review-notes/draft-root-lock-primitive/2026-09-09-round-1.md)；范围变化后保留历史，不并入 v2。 |
| lock v2 | 加入 operations 直接登记/释放/finally 及既有 runtime.internal 枚举 | 3 | 1 | 执行级 [round-2](/workspaces/agent-plugins/.worktrees/draft-root-lock-recovery/tmp/agents/review-notes/draft-root-lock-primitive/2026-09-09-round-2.md)、[round-3](/workspaces/agent-plugins/.worktrees/draft-root-lock-recovery/tmp/agents/review-notes/draft-root-lock-primitive/2026-09-09-round-3.md)、[round-4](/workspaces/agent-plugins/.worktrees/draft-root-lock-recovery/tmp/agents/review-notes/draft-root-lock-primitive/2026-09-09-round-4.md)；思考级 [round-1](/workspaces/agent-plugins/.worktrees/draft-lock-run-20260909/tmp/agents/review-notes/draft-root-lock-primitive/2026-09-09-round-1.md) 不通过；历史计数不并入 v3。 |
| lock v3 | v2 原语与接缝，加 link_index_maintenance、trash_lifecycle、trash 的直接锁所有权及测试 | 1 | 0 | 执行级首份候选经独立 round-2 不通过；计一次有效失败，进程中断与恢复本身不计失败。 |
| lock v4 | v3 直接接缝，加 cli.py 的已登记 runtime 退出码映射及相关测试 | 3 | 0 | result-002/003/004 分别经 round-3/4/5 不通过；达到执行级升级条件，不再派原执行级。原固定输入的源码快照未找到，变更基线须先确认；历史计数保留。 |

## 评审与采用决定

| 产物 | 决定 | 依据 |
| --- | --- | --- |
| 文档同步 | 已采用，本地提交 `16f8f34465e0fc0055f80e6b925e126168334adc` | [核验报告](/workspaces/agent-plugins/tmp/agents/review-notes/scratch-status/2026-09-09-round-2.md)。 |
| YAML 能力交付与公开整合边界 | 已采用，本地提交 `104cae0`；不改变在途候选的固定任务材料 | [round-2](/workspaces/agent-plugins/tmp/agents/review-notes/yaml-ticket-boundaries/2026-09-09-round-2.md) 通过；六文档的 66 个本地链接/锚点及 diff 检查通过。 |
| 请求判别字段校验顺序 | 已采用，本地提交 `ee17a57`；仅更新请求任务权威 | 用户确认重复 operation/query 先报 repeated_field；权威已列完整次数与值类型决策表，diff 检查通过。 |
| frontmatter 基础阶段 | 已采用，候选提交 `c73b25d`，主线 `c02ffb6` | 思考级 round-3 通过；主线阶段单元 232、直接集成 3 分开验证通过。未接入公开路径，不关闭 05；库非 YAMLError 无 AST 分区仍未定义。 |
| 旧 frontmatter、根锁候选 | 未采用 | 对应正式评审不通过；新候选不得读取旧候选/评审/修复/恢复材料。 |
| 根锁思考级首份候选 | 不采用；用户批准扩展范围形成 lock v3 | SP-1–SP-4 与 ST-1 见当前根锁 round-1；新增三个文件仅处理直接锁所有权及测试，不改业务恢复协议。 |
| 页面边界 round-2/3 | 状态分类和消费者事实保留；按 round-4 由 YAML 总任务承担格式整合 | [round-2](/workspaces/agent-plugins/tmp/agents/review-notes/draft-page-read-boundary/2026-09-09-round-2.md)、[round-3](/workspaces/agent-plugins/tmp/agents/review-notes/draft-page-read-boundary/2026-09-09-round-3.md)；[round-4](/workspaces/agent-plugins/tmp/agents/review-notes/draft-page-read-boundary/2026-09-09-round-4.md) 裁定以任务分层和原子整合闭合，无需新增产品门禁。 |
| request-boundary-plan v1 | 不采用当前执行计划；接受 RB-01–RB-08，要求完整修订依据 | [独立评审 round-1](/workspaces/agent-plugins/tmp/agents/review-notes/request-boundary-plan/2026-09-09-round-1.md)；未批准 CLI/兼容新语义。 |

## 临时资源

| 工作区 | 分支 / 代码基线 | 所属任务与保留状态 |
| --- | --- | --- |
| `/workspaces/agent-plugins/.worktrees/draft-frontmatter-run-20260909` | `task/draft-frontmatter-run-20260909` / `c73b25d` | frontmatter 基础阶段已采用至主线 c02ffb6；工作区与评审记录保留，未清理。 |
| `/workspaces/agent-plugins/.worktrees/draft-lock-run-20260909` | `task/draft-lock-run-20260909` / `670dbf210431badbdf68aa6e99f844417c35a6d4` | 当前根锁候选；保留，未采用。 |
| `/workspaces/agent-plugins/.worktrees/draft-page-read-v2` | `task/draft-page-read-v2` / `16f8f34465e0fc0055f80e6b925e126168334adc` | 旧 frontmatter 候选及评审；保留，不作当前采用来源。 |
| `/workspaces/agent-plugins/.worktrees/draft-root-lock-recovery` | `task/draft-root-lock-recovery` / `670dbf210431badbdf68aa6e99f844417c35a6d4` | 旧根锁/recovery 混合候选及评审；保留。 |
| `/workspaces/agent-plugins/.worktrees/draft-frontmatter-terra` | `task/draft-frontmatter-terra` | 用户授权清理已完成，取消的两个 frontmatter agent 共用；任务材料随工作区删除。 |
| `/workspaces/agent-plugins/.worktrees/draft-root-lock-terra` | `task/draft-root-lock-terra` | 用户授权清理已完成，取消的两个根锁 agent 共用；任务材料随工作区删除。 |

主工作区的 `/workspaces/agent-plugins/tmp/agents/2026-09-09-draft-delivery/`、各任务工作区的同名会话目录及 `tmp/agents/review-notes/` 下相关报告继续保留；其实际位置由上表链接定位。
