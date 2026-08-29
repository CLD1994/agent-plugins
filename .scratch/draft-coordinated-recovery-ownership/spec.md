Status: needs-triage
Status-Schema: triage
Tracker-State: closed
Resolution: superseded
Promoted-to: plugins/draft/docs/spec/runtime/recovery.md
Base-Spec: a694d1458335ed38900e31067b5f370ffe2c7e6a
Affects:
  - plugins/draft/docs/spec/runtime/recovery.md
Parent: none
Scope: feature-delta
Current-Owner: plugins/draft/docs/spec/runtime/recovery.md
Dispatch: blocked
Dispatch-Diagnostic: signed Q32 fully superseded this feature by the runtime recovery owner; retain the directory as historical evidence and do not dispatch its child briefs.

当前实施依赖为 Issue 03 的全图 relation/owner/participant/action 分类，随后由 Issue 01
对 participant closure 执行副作用前材料 preflight，最后由 Issue 05 验收完整恢复链的 FD
生命周期、刷新竞态和公开入口行为。

# Draft 协调恢复所有权

## Problem Statement

Draft 的恢复入口会扫描没有 `operation.lock` 的 operation 目录。现有实现把这次扫描得到的 journal、关联与目录事实直接用于清理恢复副本、删除 journal 或写入终态；在扫描和命名副作用之间，另一个受协调 CLI writer 可以取得同一 Draft 根的锁。这样恢复可能消费活跃 writer 的现场，也可能依据已经过期的 R/T（restore/trash）关系作出清理决定。

Draft 已确定只保障同一 Draft 根上的受协调 writer，而不承诺阻止同权限、绕过 lock 协议的本地进程改名或替换命名空间。恢复协议必须与这一保障边界一致：未持锁阶段只发现和分类，所有会改变恢复现场的行为都由 matching `operation.lock` 串行化，并以锁内的当前事实授权。

## Solution

将恢复拆分为未持锁候选扫描、候选分类和锁内收敛三个责任边界。未持锁扫描只读取和分类当前 operation 目录、journal、`.pending` 与 R/T 关系；它不发布 `.pending`、不写删 journal、不清理副本或目录，也不调用 handoff。需要改变现场的候选先取得该 operation ID 的 lock；若竞争失败，丢弃扫描快照并返回稳定的恢复未决错误。成功取得 lock 后重新读取目录成员、journal、`.pending` 和关联，再按当前事实完成恢复、回滚、授权清理或失败关闭。

恢复对未知、损坏、非法类型、符号链接、身份不一致和无法解释的 R/T 关系保留现场并报告稳定错误。恢复重复执行时只处理仍存在且由当前 journal 授权的材料；已提交的 Trash handoff 或 purge 清理不得重复调用外部 handoff。

### 未持锁候选分类

分类先按以下优先级执行；命中较高优先级后不得用更低优先级的关系推断覆盖结果：

1. **raw journal shape/type**：不可读 JSON、v4 字段类型错误，或 `metadata` 的任一 key/value
   不是 string（包括 R/T metadata 中的 `null`、number、list）返回
   `operation.journal_corrupt`；operation 内出现符号链接返回 `path.symlink_escape`。
2. **R 自身计划**：合法 `restore` journal 缺少 `restores_trash_operation`，或该值虽为
   string 但不是规范的 32 位小写 hex operation ID，返回 `operation.plan_invalid`。不得从
   T 的 claim 反推或补写 R 的 forward claim。
3. **图关系**：R 的 forward ID 合法后，T 不存在、T kind 错、T claim 是非法 ID、关系不是
   批准的单向窗口、双方错配或全体 journals 中出现多重认领，返回
   `operation.inconsistent_state`。
4. **phase**：图关系合法后，R 或 T 的 phase 不在该关系允许的 phase 集中，返回
   `operation.inconsistent_state`。

关系字段固定为 `R.metadata.restores_trash_operation`（R -> T）和
`T.metadata.restore_operation_id`（T -> R）；两者若存在，值必须按 operation ID 词法检查。
R -> T 唯一批准的单向窗口只有：

- `PRECLAIM`：R 为 `restore/allocating`，严格没有 observations、mutations、remove dirs，
  且 R metadata 除 `restores_trash_operation` 外精确为空；R owner 持锁时可以回滚。
- `POST_UNCLAIM_CLEANUP`：R 为 `rolled_back`，T 为 `trash_retained`，且 T 的 claim 已耐久
  删除；R owner 持锁时可以执行 R 的授权清理。

其他 R -> T only 一律为 `operation.inconsistent_state`。T-only 时，若 R 存在但缺少
forward claim，返回 `operation.plan_invalid`；若 R 不存在或 R 指向其他 T，返回
`operation.inconsistent_state`。MULTIPLE 必须基于全部 journals 判定，不能只看当前候选。

所有上述错误均失败关闭并保留全部现场。`operation.recovery_required` 只表示锁竞争或
合法候选已由其他 owner 持有，不表示 journal、节点、计划或关系损坏。

| 当前目录与 journal 事实 | 未持锁动作 | 锁内结果或失败语义 |
| --- | --- | --- |
| operation 目录不存在 | 不产生候选 | 返回空结果 |
| 合法 operation ID 的空目录，且没有 journal 或 `.pending` | 记录为待锁内确认的 allocation residue | 取得 matching lock 后再次确认为空才删除；否则按当前事实重新分类 |
| 非 restore 的 `allocating` journal | 记录为待通用锁内恢复 | 锁内耐久写入 `rolled_back`，仅清理当前 journal 授权材料，再释放 lock |
| `restore/allocating` 且关系为批准的 PRECLAIM 或合法双向关系 | 记录为待 restore 锁内恢复 | R 作为唯一 owner 锁内重读 R/T 后前向完成或安全回滚 |
| 任意 `restore` journal 缺少或含非法 `restores_trash_operation` | 失败关闭 | 返回 `operation.plan_invalid`；不取得 lock，不从 T→R 反推或补写 R→T，保留 journal、copies 与目录 |
| `prepared`、`applying`、`validating`、`rolling_back` 或链接索引阶段 | 不变更 | 失败关闭并保留现场 |
| `trash_handoff_succeeded`、`trash_purge_committed`，或终态仍有授权残留 | 记录为待锁内已提交清理 | 只完成当前 journal 已授权清理，绝不再次 handoff |
| 无关联的 `trash_retained` | 保留 | 无副作用，不推进到期生命周期 |
| 具有完整失败记录的 `trash_handoff_pending` | 保留 | 无副作用；仅生命周期入口按时间推进 |
| 已完整解关联且没有授权残留的 `completed` 或 `rolled_back` | 保留审计 | 无副作用 |
| 非空无 journal 目录、未知 phase、损坏 journal、非法 kind、未批准单向/错配/多重 R/T 关系，或缺少失败记录的 handoff pending | 不变更 | 按上述优先级返回稳定错误，保留全部可诊断现场 |

## User Stories

1. 作为 Draft CLI 使用者，我希望同一根目录的受协调 writer 被 `operation.lock` 串行化，以便恢复不会与另一个 CLI 写入者同时变更同一恢复现场。
2. 作为 Draft CLI 使用者，我希望未持锁候选扫描是只读的，以便扫描本身不会发布 `.pending`、改写或删除 journal、清理副本或删除 operation 目录。
3. 作为恢复调用者，我希望每个将要变更现场的候选先取得 matching operation lock，以便当前 writer 对 journal 与材料拥有明确的协调所有权。
4. 作为恢复调用者，我希望 lock 竞争时旧扫描快照被丢弃并返回 `operation.recovery_required`，以便不会消费获胜 writer 的 journal、材料或 lock。
5. 作为恢复调用者，我希望取得 lock 后重新读取 journal、目录成员、`.pending` 与 R/T 关系，以便副作用只由当前事实授权。
6. 作为恢复调用者，我希望普通 `allocating` operation 在锁内耐久地转为 `rolled_back` 后再清理其授权材料，以便崩溃后仍可诊断并可重复恢复。
7. 作为恢复调用者，我希望合法空 operation 目录被显式视为 allocation residue 并在锁内再次确认后清理，以便 `check` 不会永久报告 journal 损坏。
8. 作为恢复调用者，我希望非空但缺少 journal 的 operation 目录保持失败状态，以便未知材料不会被误认为可清理 residue。
9. 作为恢复调用者，我希望 `.pending` 只能在锁内按合法 transition 规则收敛，以便未持锁读取不会发布别的 writer 的耐久状态。
10. 作为 restore 使用者，我希望有效 R/T 关系只由 restore operation 取得 lock 后处理，以便前向完成或回滚的权威 owner 唯一。
11. 作为 restore 使用者，我希望预认领、单向、错配或多重 R/T 关系不会被静默回滚，以便关系协议损坏始终保留证据。
12. 作为 Trash 使用者，我希望无关联 `trash_retained` 和具有完整失败记录的 `trash_handoff_pending` 在恢复时保持不变，以便恢复不代替到期生命周期推进。
13. 作为 Trash 使用者，我希望已提交的 handoff 或 purge 清理可以在锁内重试收敛但不再次调用 handoff，以便外部副作用不会重复执行。
14. 作为生命周期调用者，我希望释放崩溃遗留 lock 后重新读取锁与 operation 事实，以便新取得 lock 的 writer 不会被旧快照覆盖或清理。
15. 作为 Draft 用户，我希望 terminal phase 只有在关系已完整解关联且无授权残留时才无副作用成功，以便 waiting、残留与不可解释状态不会被折叠。
16. 作为 Draft 用户，我希望符号链接、非法节点类型、journal 损坏、目录身份变化和未知成员均失败关闭，以便恢复不会跟随或修改根外内容。
17. 作为长期运行的 Draft 用户，我希望 recovery leaf、operation directory 和恢复上下文取得的文件描述符恰好关闭一次，以便失败路径不泄漏资源。
18. 作为诊断调用者，我希望成功路径的 close 失败成为稳定 `DraftError`，而已有主异常不被 close 错误遮蔽，以便故障可准确定位。
19. 作为自动化调用者，我希望重复运行三个恢复入口得到一致的可观察结果，以便重试不会扩大副作用。
20. 作为维护者，我希望 `check`、恢复与生命周期对空 allocation residue 采用同一分类，以便恢复后的根可通过一致的内部状态检查。

## Implementation Decisions

- Draft 的并发保障范围是同一 Draft 根上的受协调 CLI writer。受协调 writer 必须遵守 matching `operation.lock`；绕过协议的任意本地 namespace 改名不属于原子防护承诺。
- 恢复建立唯一的纯 discovery 和分类责任边界。分类的输入是当前 operation 目录成员、可读 journal、`.pending` 存在性和完整 R/T 关系；分类的输出只能是保留、失败，或“按 operation ID 取得 lock 后锁内重读”的动作，不能携带初扫 journal 作为后续写入授权。
- 未持锁 discovery 禁止一切恢复现场副作用，包括 journal/pending 发布、journal 写入或删除、恢复副本和目录清理、硬链接、handoff 调用以及受管内容修改。
- 对任何 `LOCK_*` 类动作，编排器必须先取得 matching operation lock。取得失败时返回 `operation.recovery_required`，不得改用旧快照继续恢复；取得成功时锁内 resolver 重新读取当前 journal、schema、合法 transition、目录成员与 R/T 关系。
- 普通 `allocating` operation 复用通用锁内恢复：先耐久转为 `rolled_back`，再只清理当前 journal 授权的材料。空 allocation residue 只在 matching lock 内再次确认为空时删除，并同步 operation 父目录状态。
- R/T 分类必须按本 spec 的优先级和决策表显式区分 raw journal 损坏、R 计划非法、图不一致和 phase 不合法；restore 是关联恢复的唯一 owner。terminal 与 waiting 状态必须区分“保留”“已提交但仍需授权清理”“完全解关联的审计保留”和“不可解释失败”。
- `restore` journal 的 `restores_trash_operation` 是恢复所有权的必要前向关联。缺失或不合法时统一为 `operation.plan_invalid`：未持锁 discovery 立即失败，已有 matching lock 或锁内重读同样失败并保留 lock；不得从 Trash 的反向 claim 推断或写入该键。其余 R/T 结果严格使用上面的 raw/type、图和 phase 优先级。
- Trash 生命周期在释放旧 lock 后不得继续使用旧 journal、operation ID 或 package 快照；它必须重新读取当前 lock 与当前事实，并将后续处理交给相应锁内 resolver。
- 恢复目录 I/O 继续拒绝符号链接、非法类型、未知成员、损坏 journal 和身份不一致。FD/inode 认证只用于输入安全和可诊断性，不被表述为对绕过 lock 改名的原子 compare-and-swap。
- 每个已打开的 descriptor 必须有唯一所有者。打开后 `fstat` 失败必须关闭 descriptor；无主异常的 close 失败映射为稳定 `DraftError`，有主异常时仍关闭其余 descriptor 但保留主异常。
- 保持现有 journal schema、合法 phase transition、错误和退出码、链接索引 revision 与既有持锁恢复的业务语义；不以递归删除、glob、重新解析显示路径或 catch-and-continue 替代当前授权和身份验证。

## Testing Decisions

- 测试只验证公开行为：恢复报告或稳定错误、lock 所有权、journal/副本/目录/R/T 关系的可观察状态、handoff 是否发生，以及 `check` 的诊断结果；不把私有 helper 调用图作为断言对象。
- 以已确认的三个最高公开 seam 验收：`recover(root)` 验证通用恢复和未持锁候选；`recover_interrupted_operations(root)` 验证包含 Trash/restore 的中断恢复；`advance_trash_lifecycle(root, ...)` 验证释放旧 lock 后的再竞争和生命周期责任边界。
- 真实文件系统、目录 FD、符号链接、lock 竞争、journal durability、close 失败和 handoff 重试属于集成测试；局部纯分类和状态转换可以作为单元测试，与集成测试分开执行。
- 两个恢复入口均覆盖：扫描后另一受协调 writer 抢得 lock、`.pending` 存在、普通 allocating、空/非空无 journal 目录、R/T 关系分区、terminal/waiting 分区、已提交 handoff/purge、同 inode journal 语义变化、扫描后新增关联方，以及重复恢复。
- lock 竞争测试必须断言 active lock、获胜 writer 的 journal、恢复副本、目录与 R/T 关系不变；释放 retained lock 后的新竞争必须断言不调用 handoff 且不消费新 writer 材料。
- FD 故障测试注入 leaf `fstat`、leaf close、operation directory close、恢复上下文 close 与读取 close 失败；断言每个 descriptor 至多关闭一次、无主异常时返回稳定错误、已有主错误不被遮蔽，且没有 FD 增长。
- 空 allocation residue 和普通 allocating 的恢复后必须重复执行恢复与 `check`，证明不存在永久 `operation.journal_corrupt` 且第二次没有额外副作用。

## Out of Scope

- 对绕过 `operation.lock` 的任意同权限本地进程改名、替换或删除 namespace 节点提供原子防护。
- 网络环境与 Windows 平台上的恢复行为验证。
- 无关的链接索引协议、revision 语义、页面链接解析或查询行为改造。
- 更改 journal schema、合法 phase transition、稳定错误码或 CLI 退出码。
- 将恢复入口扩展为按当前时间推进 `trash_retained`、发起新的 handoff 或执行系统回收站最终清理。

## Further Notes

- `operation.lock` 是受协调 writer 的串行化协议，不是任意本地进程的强制隔离机制。
- “未持锁候选扫描”是只读 discovery，不是“无锁恢复”或“无锁清理”。
- 无法证明当前目录身份、journal 权威性、摘要、关系完整性或 lock 所有权时，恢复必须停止并保留诊断现场。
