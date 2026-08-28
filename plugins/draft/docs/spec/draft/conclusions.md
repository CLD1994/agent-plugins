# 结论快照

## 行为合同

`conclusions.md` 是显式生成、允许暂时过时的语义快照。文件固定包含 `## 已确认结论` 与 `## 受质疑结论` 两个 section；confirmed 页面是唯一成员来源，带 `challenged_by` 的页面同时进入受质疑投影，质疑来源按规范路径排序。

生成前必须认证关系成对、端点存在、目标已确认且无重复；失败零写。`move`、`promote`、`collapse` 只在能证明路径映射时改写已有顶层和缩进链接，不增删或重排条目；Trash/Restore 不修改快照。旧单 section 与 `产出` 字段不兼容、不双读、不迁移。

## 失败与恢复

生成器拥有的制品无法按固定格式解析时拒绝覆盖；关系残留必须由精确 `relation.remove` 清理后才能重试生成。快照 stale 不阻止结构操作，但显式生成必须整体重建当前格式。

## 来源

- `33aeaec60a34833ce8b686bfdd7e50b8dc9932d0:.scratch/draft-authority-spec-migration/signed-decisions-transcript.md#已签署决定迁移摘要`
