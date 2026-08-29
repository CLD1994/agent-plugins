# 冻结公共 request/result 与 CLI 机器协议

Type: prototype
Status-Schema: triage
Status: needs-triage
Tracker-State: open
Base-Spec: ce64ac30c52ca910fe39fcedd055dd28f0cfbda4
Affects:
  - plugins/draft/docs/spec/runtime/interface.md
  - plugins/draft/docs/spec/runtime/admission-and-effects.md
  - plugins/draft/docs/spec/runtime/errors-and-observability.md
Parent: .scratch/draft-operation-runtime-execution-wayfinding/map.md
Scope: child-issue-only
Current-Owner:
  - plugins/draft/docs/spec/runtime/interface.md
  - plugins/draft/docs/spec/runtime/errors-and-observability.md
Dispatch: blocked
Dispatch-Diagnostic: this prototype asks for request/result and CLI ABI fields that are explicitly unresolved; it cannot freeze protocol or be dispatched as behavior.

Blocked by: 01 形成 Draft v1 领域行为合同；02 确定 v1 隔离开发与一次性切换边界

## Question

`AbsoluteRoot`、`RootState`、所有封闭 request union、`Lifecycle.initialize`、`OperationRuntime` 六方法、DryRun plan/outcome、Restore 独立错误、warning、notification、CLI lexical input、JSON envelope 与 exit code 的精确类型代数是什么？每个 tag 有哪些必需字段、禁止字段、未知值规则、排序和序列化边界，且怎样保持 CLI 只作 root 选择、request 构造和结果投影？
