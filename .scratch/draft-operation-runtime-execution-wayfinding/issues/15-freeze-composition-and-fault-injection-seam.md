# 冻结 composition 与 deterministic fault seam

Type: prototype
Status-Schema: triage
Status: needs-triage
Tracker-State: open
Base-Spec: ce64ac30c52ca910fe39fcedd055dd28f0cfbda4
Affects:
  - plugins/draft/docs/spec/runtime/interface.md
  - plugins/draft/docs/spec/runtime/admission-and-effects.md
  - plugins/draft/docs/spec/runtime/initialization-and-platform.md
  - plugins/draft/docs/spec/runtime/lifecycle-and-handoff.md
  - plugins/draft/docs/spec/runtime/recovery.md
Parent: .scratch/draft-operation-runtime-execution-wayfinding/map.md
Scope: child-issue-only
Current-Owner:
  - plugins/draft/docs/spec/runtime/interface.md
  - plugins/draft/docs/spec/runtime/admission-and-effects.md
  - plugins/draft/docs/spec/runtime/initialization-and-platform.md
  - plugins/draft/docs/spec/runtime/lifecycle-and-handoff.md
  - plugins/draft/docs/spec/runtime/recovery.md
Dispatch: blocked
Dispatch-Diagnostic: this prototype proposes internal composition and fault-injection seams across runtime owners; implementation seams are not an approved public or child behavior contract.

Blocked by: 02 确定 v1 隔离开发与一次性切换边界；08 综合 PlatformAdapter 可观察合同；11 冻结 init、root lock 与 operation records；12 冻结 catalog 与 index records；13 冻结 HandoffAdapter 与 isolated worker 协议；14 冻结 Trash、Restore 与 handoff records

## Question

在不扩大 `Lifecycle`、`OperationRuntime`、`RuntimeFactory` 和 `HandoffAdapter` 允许边界的前提下，runtime 怎样内部装配 PlatformAdapter、HandoffAdapter、Clock、ID source、planner、scanner 和 worker？unit/integration 怎样使用受真实接口约束的 fake/mock 以及真实平台 adapter，确定性触发 fsync、replace、lock、worker、external change 和 crash window，而不暴露 production injection API、全局可变 hook 或 private CLI seam？
