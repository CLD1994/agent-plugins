# 建立 durable authority 与恢复所有权模型

Type: prototype
Status-Schema: triage
Status: needs-triage
Tracker-State: open
Base-Spec: ce64ac30c52ca910fe39fcedd055dd28f0cfbda4
Affects:
  - plugins/draft/docs/spec/runtime/authority-and-projections.md
  - plugins/draft/docs/spec/runtime/recovery.md
  - plugins/draft/docs/spec/runtime/lifecycle-and-handoff.md
Parent: .scratch/draft-operation-runtime-execution-wayfinding/map.md
Scope: child-issue-only
Current-Owner:
  - plugins/draft/docs/spec/runtime/authority-and-projections.md
  - plugins/draft/docs/spec/runtime/recovery.md
Dispatch: blocked
Dispatch-Diagnostic: this prototype spans authority, recovery and lifecycle ownership; its writer/recovery choices are not a signed page-level delta and remain unresolved.

Blocked by: None

## Question

怎样把 runtime lock holder、operation manifest/progress、content commit、active catalog、index head、Trash/Restore、handoff 与 recovery 表达为闭合状态模型？每个 authority 的唯一 writer、fold owner、recovery owner 和清理授权分别是谁；intent、immutable record、authority replace、commit 缺失、重复和不匹配时允许哪些转换，并用什么关联证明防止第二 authority 或第二恢复入口？
