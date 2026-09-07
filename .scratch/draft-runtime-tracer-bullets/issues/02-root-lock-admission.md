# 02: 写入和恢复共享根目录排他锁

Status-Schema: triage
Status: ready-for-agent
Tracker-State: open
Base-Spec: 4cf3b96b7d30e4c3f9d2374b88852e160d35dfa2
Affects:
  - plugins/draft/docs/spec/runtime/admission-and-effects.md
  - plugins/draft/docs/spec/runtime/recovery.md
  - plugins/draft/docs/spec/runtime/initialization-and-platform.md
  - plugins/draft/docs/spec/runtime/errors-and-observability.md
Parent: .scratch/draft-runtime-tracer-bullets/spec.md
Scope: child-issue-only
Current-Owner:
  - plugins/draft/docs/spec/runtime/admission-and-effects.md
  - plugins/draft/docs/spec/runtime/recovery.md
  - plugins/draft/docs/spec/runtime/initialization-and-platform.md
  - plugins/draft/docs/spec/runtime/errors-and-observability.md
Dispatch: eligible
Dispatch-Diagnostic: 根锁和恢复的产品规则已确认；本票只完成锁取得、锁内重验、主要写入/恢复入口及其可观察结果，不扩展为整个 runtime 重写。

**What to build:** 正常写入和显式恢复在接触同一根目录的临时材料、操作记录或业务内容前，使用同一把操作系统排他锁。另一个仍持锁的写入者或恢复者得到“当前正在使用”的结果，不能修改对方现场；调用返回前锁一定已经释放。

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

- [ ] 锁的活跃性只由当前操作系统锁的持有情况证明；不使用进程号、修改时间或锁文件存在性猜测持有者。
- [ ] 创建准备材料和发布操作记录之前先取锁；取得锁后重新读取事实并重新计算计划，不能把锁外的旧快照当作最终认证。
- [ ] 门禁失败、保存不确定、正常成功和异常返回都不留下锁；进程崩溃后由操作系统释放锁，现场按恢复规则保留。
- [ ] 旧的“写一个 JSON 锁文件”测试改为验证真实竞争、现场保留、显式恢复和错误分类，不删除测试来换取绿色结果。
- [ ] 跨进程竞争、进程终止后重新取得锁、符号链接/类型异常和平台能力不足都有独立集成证据。

**停止条件:** 需要改变页面、关系、结论或回收区操作（Trash）的业务语义，或需要新增未登记的公开错误时停止并报告。
