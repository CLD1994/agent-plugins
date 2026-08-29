# 研究 Linux 平台能力证据

Type: research
Status-Schema: triage
Status: needs-triage
Tracker-State: open
Base-Spec: ce64ac30c52ca910fe39fcedd055dd28f0cfbda4
Affects:
  - plugins/draft/docs/spec/runtime/initialization-and-platform.md
  - plugins/draft/docs/spec/runtime/interface.md
Parent: .scratch/draft-operation-runtime-execution-wayfinding/map.md
Scope: child-issue-only
Current-Owner: plugins/draft/docs/spec/runtime/initialization-and-platform.md
Dispatch: blocked
Dispatch-Diagnostic: this platform research brief has no normalized tracker state or approved evidence scope; platform capability conclusions must be triaged before dispatch.

Blocked by: None

## Question

基于 Linux、Python 和本地文件系统的一手资料，哪些可用机制能够证明逐节点 no-follow、create-only/no-replace 发布、记录路径缺失时仍稳定的 live OS root lock、文件与目录 durability、`replace_exact_regular` 的 identity/digest 条件，以及可终止隔离调用的 hard timeout？哪些卷、文件系统或错误结果必须归为 unsupported、external change、corrupt、durability failure 或 unknown，哪些结论仍需真实平台实验？
