# 确定 v1 隔离开发与一次性切换边界

Type: prototype
Status-Schema: triage
Status: needs-triage
Tracker-State: open
Base-Spec: ce64ac30c52ca910fe39fcedd055dd28f0cfbda4
Affects:
  - plugins/draft/docs/spec/runtime/interface.md
  - plugins/draft/docs/spec/runtime/admission-and-effects.md
Parent: .scratch/draft-operation-runtime-execution-wayfinding/map.md
Scope: child-issue-only
Current-Owner:
  - plugins/draft/docs/spec/runtime/interface.md
  - plugins/draft/docs/spec/runtime/admission-and-effects.md
Dispatch: blocked
Dispatch-Diagnostic: this prototype proposes implementation namespace and cutover boundaries, but no approved child delta or current owner contract authorizes those choices.

Blocked by: None

## Question

v1 在不修改旧生产模块和旧 CLI 路由的阶段应通过什么 namespace、export graph 和测试入口提供目标公开 Interface？`Lifecycle`、`OperationRuntime`、`RuntimeFactory` 与内部 composer 怎样共存，才能让每个新增切片可通过目标边界验证，并在最终 cutover 中一次切换而不保留 facade、re-export、dual-read 或占位成功？
