# 03: 恢复与 Trash 清理只处理已证明归属的现场

Status-Schema: triage
Status: ready-for-agent
Tracker-State: open
Base-Spec: 4cf3b96b7d30e4c3f9d2374b88852e160d35dfa2
Affects:
  - plugins/draft/docs/spec/runtime/recovery.md
  - plugins/draft/docs/spec/runtime/authority-and-projections.md
  - plugins/draft/docs/spec/draft/trash-and-restore.md
Parent: .scratch/draft-runtime-tracer-bullets/spec.md
Scope: child-issue-only
Current-Owner:
  - plugins/draft/docs/spec/runtime/recovery.md
  - plugins/draft/docs/spec/runtime/authority-and-projections.md
  - plugins/draft/docs/spec/draft/trash-and-restore.md
Dispatch: blocked
Dispatch-Diagnostic: 等待第 02 票完成根目录排他锁及公开返回释放闭合。

**What to build:** 恢复（Restore）、回收区操作（Trash）和显式恢复只清理能够由持久证据准确证明属于本次操作的临时材料。活动写入者的现场、归属不明的现场和仅仅因为目录为空的现场都必须保留并给出可诊断结果。

**Blocked by:** 02: 写入和恢复共享根目录排他锁。

**Status:** ready-for-agent

- [ ] 恢复操作在创建任何新的准备材料之前先取得根目录排他锁；取锁失败时不创建材料，也不调用无锁清理。
- [ ] 空目录、缺少操作记录或记录刚刚消失都不会自动证明“无人使用”；证据不足时保留现场并报告恢复受阻。
- [ ] 只有锁内的所有权闭包能够认证某项材料属于本次操作时，才允许删除、结束记录或清理目录。
- [ ] 活动持锁者、已认证待恢复现场、归属不明现场、损坏现场和耐久性不确定分别得到规格负责人规定的结果，不能相互降级。
- [ ] 连续恢复、并发恢复、恢复中断和部分清理均有真实文件系统/进程集成测试。

**停止条件:** 无法从当前规格负责人页面证明某项材料的归属，或需要把未定义的临时材料类型加入清理白名单时停止并报告。
