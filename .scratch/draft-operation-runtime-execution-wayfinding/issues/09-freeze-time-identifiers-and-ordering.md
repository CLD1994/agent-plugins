# 冻结时间、ID 与确定性排序

Type: prototype
Status-Schema: triage
Status: needs-triage
Tracker-State: open
Base-Spec: ce64ac30c52ca910fe39fcedd055dd28f0cfbda4
Affects:
  - plugins/draft/docs/spec/runtime/interface.md
  - plugins/draft/docs/spec/runtime/authority-and-projections.md
  - plugins/draft/docs/spec/runtime/lifecycle-and-handoff.md
  - plugins/draft/docs/spec/runtime/recovery.md
Parent: .scratch/draft-operation-runtime-execution-wayfinding/map.md
Scope: child-issue-only
Current-Owner:
  - plugins/draft/docs/spec/runtime/interface.md
  - plugins/draft/docs/spec/runtime/lifecycle-and-handoff.md
Dispatch: blocked
Dispatch-Diagnostic: this prototype proposes time/ID/order encoding across four owners; the required public and durable ABI is unresolved and cannot be chosen here.

Blocked by: 01 形成 Draft v1 领域行为合同；03 建立 durable authority 与恢复所有权模型

## Question

Clock 在一次 invocation、自动 lifecycle、worker timeout 和 recovery 中何时采样，UTC、精度和规范化规则是什么？restore、retry 与 purge deadline 在相等边界如何分类？operation、generation、sequence、transition 和 attempt ID 需要哪些格式、唯一性或单调性；多个 eligible candidate 与多个 warning 怎样确定性排序并在重试后保持稳定？
