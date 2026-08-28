# 准入与副作用

## 行为合同

所有请求先取得 root lock、分类请求和副作用授权，再生成完整计划。DryRun 与 Execute 使用同一 admission 和最终不变量；DryRun 不创建 journal、revision、索引制品或 flag。

## 失败与恢复

请求闭包、锁、权限或副作用授权无法认证时零写；registered 根要求全局健康，candidate 只可保留闭包外诊断。

## 来源

- `80508340458f393435ae67669656141ec411ba1b:.scratch/draft-operation-runtime-foundation/spec.md#5. 请求准入`
- `80508340458f393435ae67669656141ec411ba1b:.scratch/draft-operation-runtime-foundation/spec.md#6. 副作用`
