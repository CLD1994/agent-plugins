# 恢复

## 行为合同

只有显式 `recover()` 可收敛 recovery pending。它取得运行期 root lock，认证 owner、intent、变更前/后 authority、immutable transition/epoch 与当前 authority，再执行前向、回滚或清理。

## 失败与恢复

owner 不匹配、重复或伪造 intent、未知 staging、authority 不匹配和无法证明的现场均为 `runtime.corrupt`，零写并保留现场；普通入口遇到有效未完成记录返回 `runtime.recovery_required`。

## 来源

- `80508340458f393435ae67669656141ec411ba1b:.scratch/draft-operation-runtime-foundation/spec.md#10. 恢复`
