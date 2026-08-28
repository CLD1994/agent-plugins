# 页面编辑

## 行为合同

`create`、`status set`、`relation add/remove` 先认证目标、机器区、局部关系和最终不变量，再一次提交。DryRun 与 Execute 使用同一事实门禁和完整计划；DryRun 不写入任何领域或 runtime 状态。

## 失败与恢复

参数、状态、关系、span 或链接无法认证时零写并保留现场；不以缺失事实推断幂等成功。结论页关系按 [状态与关系](status-and-relations.md) 的窄清理规则处理。

## 来源

- `33aeaec60a34833ce8b686bfdd7e50b8dc9932d0:.scratch/draft-authority-spec-migration/migration-evidence.md#迁移单元`
