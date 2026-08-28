# Trash 与 Restore

## 行为合同

Trash 只处理受管理 Markdown 页面及其授权删除包，保留 7 天恢复窗口；结论快照不阻止回收。Restore 使用不透明 `TrashIdentity`，要求当前根与记录的 after-state 精确匹配，不覆盖、不猜测合并。

## 失败与恢复

lexical invalid、missing、expired、consumed、conflict、corrupt、recovery-required 七类现场独立处理。任何证据不完整或恢复冲突都保留现场并失败；只有显式 recover 收敛 recovery pending。

## 来源

- `33aeaec60a34833ce8b686bfdd7e50b8dc9932d0:.scratch/draft-authority-spec-migration/migration-evidence.md#迁移单元`
