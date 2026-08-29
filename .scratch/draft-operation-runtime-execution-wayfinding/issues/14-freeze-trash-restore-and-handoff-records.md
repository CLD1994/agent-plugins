# 冻结 Trash、Restore 与 handoff records

Type: prototype
Status-Schema: triage
Status: needs-triage
Tracker-State: open
Base-Spec: ce64ac30c52ca910fe39fcedd055dd28f0cfbda4
Affects:
  - plugins/draft/docs/spec/draft/trash-and-restore.md
  - plugins/draft/docs/spec/runtime/lifecycle-and-handoff.md
  - plugins/draft/docs/spec/runtime/recovery.md
Parent: .scratch/draft-operation-runtime-execution-wayfinding/map.md
Scope: child-issue-only
Current-Owner:
  - plugins/draft/docs/spec/draft/trash-and-restore.md
  - plugins/draft/docs/spec/runtime/lifecycle-and-handoff.md
  - plugins/draft/docs/spec/runtime/recovery.md
Dispatch: blocked
Dispatch-Diagnostic: this prototype asks for Trash/Restore and handoff record schema across domain and runtime owners; package and retry ABI remain unresolved.

Blocked by: 01 形成 Draft v1 领域行为合同；10 冻结 canonical encoding 与共享标量；12 冻结 catalog 与 index records；13 冻结 HandoffAdapter 与 isolated worker 协议

## Question

Trash package、T/R active entry、R→T 关系、consumption、receipt、rollback、handoff authorization、prepared、accepted/failed/unknown、retry deadline、purge authorization 和 cleanup 的完整 schema 是什么？怎样把 7/30/24 时间规则、active catalog membership、同一 Trash operation id、warning identity 与 recover 禁止新 Adapter 调用表达为可认证的持久事实？
