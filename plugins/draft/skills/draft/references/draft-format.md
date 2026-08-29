# 草稿格式参考

本文件是 skill 的使用导航，不是 Draft 行为合同。遇到格式、状态、关系、资源或结论
语义时，以以下正式 owner 为唯一依据：

- [根生命周期](../../../docs/spec/draft/lifecycle.md#根生命周期)
- [页面角色与导航](../../../docs/spec/draft/pages-and-navigation.md#页面角色与导航)
- [页面状态与关系](../../../docs/spec/draft/status-and-relations.md#页面状态与关系)
- [资源身份与链接](../../../docs/spec/draft/resources-and-links.md#资源身份与链接)
- [结论快照](../../../docs/spec/draft/conclusions.md#结论快照)

使用结构命令时另读 [结构操作](../../../docs/spec/draft/structure.md)：只有在 before-to-after
mapping 可证明时，`move`、`promote`、`collapse` 才在同一提交中改写 `conclusions.md` 既有
条目的链接目标。它们不改变成员、顺序、标签、质疑来源或语义；这些只由
`generate-conclusions` 重算。

不要把 `产出` 写入机器区作为关系或结论成员条件。其精确 parser 行为仍为
`unresolved`；需要该判断时停止并转到 [结论快照](../../../docs/spec/draft/conclusions.md#未决-parser-边界)，
不得在本 reference 中选择兼容或迁移格式。
