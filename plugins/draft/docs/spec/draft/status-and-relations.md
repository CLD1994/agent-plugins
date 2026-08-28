# 状态与关系

## 行为合同

页面状态为 `pending`、`proposed`、`confirmed`、`superseded`、`closed`。正式关系为 `parent`、`depends_on`、`supersedes`、`superseded_by`、`challenges`、`challenged_by`；正反声明必须成对，`confirmed + challenged_by` 合法共存。`conclusions.md` 只允许固定 `parent`。

## 失败与恢复

重复、自引用、关系环、缺失反向声明或对结论页执行 `relation.add` 都拒绝；`relation.remove` 只能精确清理可认证的遗留行。

## 来源

- `33aeaec60a34833ce8b686bfdd7e50b8dc9932d0:.scratch/draft-authority-spec-migration/signed-decisions-transcript.md#已签署决定迁移摘要`
