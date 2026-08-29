# 03 普通内容提交、能力与派生闭合

Status-Schema: triage
Status: needs-triage
Tracker-State: open
Base-Spec: ce64ac30c52ca910fe39fcedd055dd28f0cfbda4
Affects:
  - plugins/draft/docs/spec/runtime/admission-and-effects.md
  - plugins/draft/docs/spec/runtime/authority-and-projections.md
  - plugins/draft/docs/spec/runtime/recovery.md
Parent: .scratch/draft-operation-runtime-foundation/spec.md
Scope: child-issue-only
Current-Owner:
  - plugins/draft/docs/spec/runtime/admission-and-effects.md
  - plugins/draft/docs/spec/runtime/authority-and-projections.md
  - plugins/draft/docs/spec/runtime/recovery.md
Dispatch: blocked
Dispatch-Diagnostic: content commit, capability lifetime and recovery ownership cross three runtime owners; the operation correlation and public outcome seam remain unresolved, so this ticket is not dispatchable.

Blocked by: 01, 02

## 确认合同

实施 spec 第 6--7、10 节中普通写的 manifest-before-artifact、一次性 capability、锁内重新验证、内容提交不可回滚和 matching epoch 规则。普通 request 包括 adopt；已知无效输入/闭包不触发自动生命周期。

## 建议实现与顺序

1. 先写 capability、record fold、before/after/material 分类与 content-committed 不变量单元测试。完成条件：每个 material 分类和 capability 失效原因都有断言，`content-committed` 后没有 rollback 转移；证据为测试名称与通过输出。
2. 写真实文件系统 integration，在每个写/rename/fsync 与锁后重读点中断、外部替换，再两次 recover。完成条件：每个窗口的两次 recover 收敛为相同树和 progress，未列举成员从未删除；证据为故障点矩阵。
3. 实现 internal planner route、writer 与 forward/rollback resolver；接入 runtime Implementation 内部的 index-settled dependency（Issue 04 完成前可保持受控未完成，不释放）。完成条件：普通请求均经 Issue 02 gate，release 只在 matching committed head 闭合后发生，index-settled 不是 public import 或 CLI seam；证据为接口集成和调用图审计。

## 禁止捷径与完成标准

禁止 mutation-applied 标志、复用 capability、外部变化解释为成功、未授权清理或派生失败回滚内容。完成时普通写均通过 Issue 02 gate，内容一旦 durable 只可前向，且每个失败窗口可机械收敛。
