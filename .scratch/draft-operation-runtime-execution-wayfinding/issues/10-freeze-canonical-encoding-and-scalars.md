# 冻结 canonical encoding 与共享标量

Type: prototype
Status-Schema: triage
Status: needs-triage
Tracker-State: open
Base-Spec: ce64ac30c52ca910fe39fcedd055dd28f0cfbda4
Affects:
  - plugins/draft/docs/spec/runtime/interface.md
  - plugins/draft/docs/spec/runtime/authority-and-projections.md
  - plugins/draft/docs/spec/runtime/errors-and-observability.md
Parent: .scratch/draft-operation-runtime-execution-wayfinding/map.md
Scope: child-issue-only
Current-Owner:
  - plugins/draft/docs/spec/runtime/authority-and-projections.md
  - plugins/draft/docs/spec/runtime/errors-and-observability.md
Dispatch: blocked
Dispatch-Diagnostic: this prototype asks for canonical encoding and unknown-field ABI choices; no signer-approved scalar contract permits dispatch.

Blocked by: 07 冻结公共 request/result 与 CLI 机器协议；08 综合 PlatformAdapter 可观察合同；09 冻结时间、ID 与确定性排序

## Question

持久 record 与需要 digest 的公共值采用什么精确 canonical encoding？UTF-8、键顺序、数组顺序、整数、时间、路径、ID、digest、换行、absent/null、未知字段/tag、schema version 和 hash-chain 的字节规则分别是什么；解码失败怎样区分 schema unsupported、corrupt、request invalid 与 external change？
