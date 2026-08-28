# Draft 生命周期

## 行为合同

根状态只有 `candidate` 与 `registered`。初始化创建根并设置状态；`adopt` 只允许完整 Check 无 error 的 candidate 根转为 registered，不改变页面字节或结论所有权。正式规格与 Draft 讨论空间始终分离。

## 失败与恢复

未认证 marker、完整检查错误或 recovery pending 阻止登记和业务写。恢复由 runtime 的显式 `recover` 处理，不创造第三种根状态。

## 来源

- `33aeaec60a34833ce8b686bfdd7e50b8dc9932d0:.scratch/draft-authority-spec-migration/migration-evidence.md#迁移单元`
