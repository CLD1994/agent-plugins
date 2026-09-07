# 11: 首条普通操作的提交与恢复闭环

Status-Schema: triage
Status: ready-for-agent
Tracker-State: open
Base-Spec: 4cf3b96b7d30e4c3f9d2374b88852e160d35dfa2
Affects:
  - plugins/draft/docs/spec/runtime/interface.md
  - plugins/draft/docs/spec/runtime/admission-and-effects.md
  - plugins/draft/docs/spec/runtime/authority-and-projections.md
  - plugins/draft/docs/spec/runtime/lifecycle-and-handoff.md
  - plugins/draft/docs/spec/runtime/recovery.md
  - plugins/draft/docs/spec/runtime/errors-and-observability.md
Parent: .scratch/draft-runtime-tracer-bullets/spec.md
Scope: child-issue-only
Current-Owner:
  - plugins/draft/docs/spec/runtime/interface.md
  - plugins/draft/docs/spec/runtime/admission-and-effects.md
  - plugins/draft/docs/spec/runtime/authority-and-projections.md
  - plugins/draft/docs/spec/runtime/lifecycle-and-handoff.md
  - plugins/draft/docs/spec/runtime/recovery.md
  - plugins/draft/docs/spec/runtime/errors-and-observability.md
Dispatch: blocked
Dispatch-Diagnostic: 等待第 09 票完成选定的 YAML 写入路径和第 10 票完成初始化边界；本票才把两者组合成第一条普通操作示范。

**What to build:** 让一条人类确认的普通操作从请求进入，到根目录和标记认证、纯计划、锁内重验、唯一事实来源提交、派生结果、清理、释放和公开结果，形成可重复演示的完整闭环；失败后只能由显式恢复继续处理。

**Blocked by:** 09: 首个经确认的 YAML 写入操作；10: 初始化及初始化中断恢复闭环。

**Status:** ready-for-agent

- [ ] 正常、无变化、预演、并发占用、待恢复、证据不足、损坏、外部变化和保存不确定都有唯一可观察结果。
- [ ] 操作记录、页面内容、唯一事实来源、派生索引和公开结果的先后关系在正常与失败路径都可验证。
- [ ] 进程终止后现场可由显式恢复处理；重复恢复不重复业务写，公开入口返回后不持有根目录排他锁。
- [ ] 这条路径明确列出尚未覆盖的其他操作，不把一次演示宣称为整个 runtime 已完成。

**停止条件:** 发现其他入口需要不同的请求、锁、恢复或错误协议时停止，先建立独立票据而不是在本票据中旁路。
