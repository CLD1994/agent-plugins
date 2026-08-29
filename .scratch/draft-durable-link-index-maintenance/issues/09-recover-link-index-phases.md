# 09: 实现链接索引 phase 的恢复解释器

**What to build:** 在协调恢复 spec 已选定 matching owner、完成 journal/R/T 分类并授权当前 operation 后，根据已经耐久记录的事实恢复链接索引 phase，使进程中断后的 maintenance 或业务 operation 能够机械、幂等地前向收敛。

**Blocked by:** 08: 实现已持有 operation 的索引前向提交内核.

Status-Schema: triage
Status: needs-triage
Tracker-State: closed
Resolution: superseded
Promoted-to: plugins/draft/docs/spec/runtime/authority-and-projections.md
Base-Spec: 1fb633320e1e692ac6e706d826d88e4ad49e3a68
Affects:
  - plugins/draft/docs/spec/runtime/authority-and-projections.md
Parent: .scratch/draft-durable-link-index-maintenance/spec.md
Scope: child-issue-only
Current-Owner: plugins/draft/docs/spec/runtime/authority-and-projections.md
Dispatch: blocked
Dispatch-Diagnostic: this index-phase recovery brief is retained history; the signed Q32 authority/projections owner supersedes the parent feature and this child cannot be dispatched.

- [ ] `link_index_updating` 恢复为同一 revision 的 `link_index_rebuild_required`，不根据 staging 或目标外观猜测发布成功，也不重新执行增量发布。
- [ ] `link_index_published` 必须验证索引目标的 revision 和 SHA-256；一致时允许补原 operation 终态，不一致时停止并保留现场。
- [ ] `link_index_rebuild_required` 尽力补建 rebuild flag，再允许补原 operation 终态。
- [ ] `link-index.update` 在 `allocating` 中断且没有索引副作用时可以进入 `rolled_back`；其他索引 phase 使用相同恢复解释器。
- [ ] 已有成功终态但残留锁或当前 operation staging 时执行幂等已知清理；调用方可以要求终态后继续保留锁，以支持 trash/restore 双 journal 协调。
- [ ] 恢复只清理当前 operation 精确拥有的 staging，不使用通配符，不删除其他 operation、索引目标、未知文件或未知目录。
- [ ] `recover` 恢复原 kind 和 operation ID，不创建独立持久 operation，也不分配新的链接索引 revision。
- [ ] 本 issue 不重新实现或覆盖协调恢复 spec 的 raw journal、R/T 图、phase 或 `recovery_required` 分类；所有错误仍按其优先级失败关闭并保留现场，`recovery_required` 只来自锁竞争/合法其他 owner。
- [ ] 每个恢复 phase 都测试执行一次和重复执行，断言 journal、revision、索引摘要、flag、锁和 staging 最终一致。
