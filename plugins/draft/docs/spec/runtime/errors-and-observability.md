# 错误与可观测性

## 行为合同

错误代码、退出码、JSON `result_type`、notification 和公开结果从稳定机器协议投影；文案只说明已认证事实。错误 envelope 最多携带一个有序 lifecycle result 和本次新 warning。

## 失败与恢复

`runtime.durability_failed`、`runtime.corrupt`、`runtime.recovery_required` 等稳定错误必须保留现场并明确恢复要求；索引 rebuild warning 不等同于业务失败。

## 来源

- `80508340458f393435ae67669656141ec411ba1b:.scratch/draft-operation-runtime-foundation/spec.md#11. 错误与可观测性`
