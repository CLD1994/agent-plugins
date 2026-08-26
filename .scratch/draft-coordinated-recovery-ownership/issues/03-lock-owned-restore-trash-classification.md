# 03: 将 R/T 分类和 Trash 恢复接入锁内所有权

**What to build:** 在锁内重读并处理 restore/trash 的双向 R↔T claim 关系，提供唯一的全图 relation、restore owner、participant closure、action 与 Trash 生命周期分类。

**Blocked by:** None (can start immediately).

**Status:** ready-for-human

- [x] 锁内重读按协调恢复 spec 的完整优先级分类 raw journal shape/type、metadata 类型、符号链接、R forward ID、缺失/错误 T、单向、错配、多重关系和合法图后的非法 phase，并返回对应稳定错误。
- [x] R -> T only 仅允许 PRECLAIM 和 POST_UNCLAIM_CLEANUP；其他单向关系失败关闭。T-only、R 缺失/错指向和 MULTIPLE 均基于全图分类，不从反向 claim 推断所有权。
- [x] 只有合法双向关系或批准窗口由 restore owner 前向完成或安全回滚，其他关系保留全部现场并返回稳定错误。
- [x] waiting、terminal、error 和审计保留状态保持区分：无关联 `trash_retained` 不推进生命周期，完整失败记录的 `trash_handoff_pending` 不由恢复发起新 handoff，已提交 handoff/purge 只按 journal 授权幂等清理。
- [x] R/T 关系、lock 竞争、journal 身份和 `.pending` 变化均在 matching lock 内丢弃旧快照并重新分类，副作用只由当前 journal 授权。
- [x] 分类错误失败关闭并保留现场；`operation.recovery_required` 仅用于 lock 竞争或合法候选被其他 owner 持有。
- [x] restore 与 Trash 恢复不重复调用外部 handoff；重复恢复后两个 journal、恢复材料、关系和终态保持一致。
- [x] 集成测试覆盖 R/T 关系分区、waiting/terminal/error、已提交清理、竞争后新增关联、重复恢复和 handoff 计数。

## Comments

- 2026-08-26 round-2：补充全图 raw/type -> R forward -> graph -> phase 的分阶段验证，交换
  operation ID 的 raw-vs-plan、plan-vs-reverse 反例均通过；effective current/pending reader
  在 transition 前调用同一全图分类器。恢复 `_restore_recovery_action()` 不再调用会发布
  pending 的 `validate_restore_link()`，而是消费 matching lock 内的 full-graph owner、participants
  与 action facts。`OPERATION_PLAN_INVALID` 已进入 distribution literal-owner gate，gate 的
  临时消费者反例通过。Issue 01 的 participant material closure 和每项副作用的整组
  preflight 使用本 Issue 产出的全图 capability，并在其自身范围验收。
- 2026-08-26：完成锁内 R/T 全图分类、restore 唯一 owner 与 Trash 已提交清理；补充无锁 `R=completed/T=rolled_back` 但权威包仍残留时重新取得 R lock 的回归。runtime 全量主体 `1997` 个测试通过，唯一网络依赖边界用例重试 `1 passed`（合计 `1998`）；Ruff、Pyright 和 `git diff --check` 通过。

- 2026-08-26：`restore_recovery_decision_from_facts()` 现在消费全图 reader 产生的唯一
  `ownership_action`，使用同一关系-action 映射校验批准组合，并只在该 capability 的允许
  phase 子集内选择 `RestoreRecoveryAction`；三种批准关系的纯单元对照和 relation/action
  错配稳定错误回归通过。公开恢复入口既有非法关系场景保持零副作用。runtime 文件按
  `worktree-commit-plan.md` 保持未暂存，等待混合来源整组复审后再决定提交边界。

- 2026-08-26：round 11 规格与标准独立审查均通过；全图 capability 的 Issue 03 验收已闭合。
  participant 材料门由 Issue 01 验收，所有 recovery descriptor 的完整 FD lifecycle 由 Issue 05 验收。
