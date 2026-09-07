# 07: 维护过程使用一致的根目录状态快照

Status-Schema: triage
Status: ready-for-agent
Tracker-State: open
Base-Spec: 4cf3b96b7d30e4c3f9d2374b88852e160d35dfa2
Affects:
  - plugins/draft/docs/spec/runtime/authority-and-projections.md
  - plugins/draft/docs/spec/runtime/admission-and-effects.md
  - plugins/draft/docs/spec/draft/query-check-and-output.md
Parent: .scratch/draft-runtime-tracer-bullets/spec.md
Scope: child-issue-only
Current-Owner:
  - plugins/draft/docs/spec/runtime/authority-and-projections.md
  - plugins/draft/docs/spec/runtime/admission-and-effects.md
  - plugins/draft/docs/spec/draft/query-check-and-output.md
Dispatch: blocked
Dispatch-Diagnostic: 等待第 02 票的锁内维护边界，以及第 05、06 票的统一页面事实。

**What to build:** 一次维护从开始到结束使用同一份根目录状态快照。快照包含判断根目录和结论制品状态所需的标记及摘要；如果扫描期间状态改变，系统报告外部变化并放弃混合版本的持久写入。

**Blocked by:** 02: 写入和恢复共享根目录排他锁；05: 页面读取正确区分 YAML 头部和正文；06: 导航、查询和结论读取使用同一页面事实。

**Status:** ready-for-agent

- [ ] 扫描、检查、计划和维护写入使用同一份快照，不把扫描前后的不同版本拼成一个结果。
- [ ] 快照在扫描中发生变化时返回已登记的外部变化结果，不发布混合操作记录、不进行不安全的索引重建。
- [ ] 需要写入或清理时先取得根目录排他锁，并在锁内重新确认快照和计划；返回前释放锁。
- [ ] 维护失败、重复通知和并发维护都有确定的结果，且不会改变唯一事实来源的业务内容。

**停止条件:** 规格负责人页面没有定义某种标记变化的公开结果，或需要把缓存/目录存在性当作权威时停止并报告。
