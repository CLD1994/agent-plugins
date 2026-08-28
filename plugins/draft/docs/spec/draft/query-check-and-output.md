# 查询、检查与输出

## 行为合同

`page`、`links`、`backlinks`、`relations`、`status`、`recoverable` 和 `check` 从当前 Markdown、marker 与 runtime authority 读取。分页集合分别计算 total、排序和 continuation；Links/Backlinks 的来源域包含受管理页面和未受管理 Markdown，并区分 source kind；Relations 只读取受管理页面正式关系。

诊断层拥有事实，普通输出与 JSON 输出从同一诊断数据投影。`adopt` 要求无过滤、无分页的完整 Check 无 error；candidate 可保留闭包外诊断，registered 的业务写需全局健康。

## 失败与恢复

根扫描、节点类型、编码、marker、Trash authority 或时间快照不可认证时返回可诊断失败，不用 lower-bound 或空集合伪装成功。Recoverable 对同一 UTC `as_of` 完成资格筛选和投影。

## 来源

- `33aeaec60a34833ce8b686bfdd7e50b8dc9932d0:.scratch/draft-authority-spec-migration/migration-evidence.md#迁移单元`
