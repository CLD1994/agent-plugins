# 迁移参考

本文件只说明如何找到迁移所需的当前合同，不维护第二套迁移、机器区或命令行为规则。
候选根、完整 Check、`adopt` 与 recovery 的唯一 owner 是
[根生命周期](../../../docs/spec/draft/lifecycle.md#根生命周期)；页面路径与导航见
[页面角色与导航](../../../docs/spec/draft/pages-and-navigation.md#页面角色与导航)。

迁移中的 `create`、状态和关系改动必须按
[创建、状态与关系编辑](../../../docs/spec/draft/page-edits.md#创建状态与关系编辑) 形成完整计划；
路径变换、页面纳管、目录 source、`promote` 与 `collapse` 必须按
[结构操作](../../../docs/spec/draft/structure.md#move) 执行。检查、诊断和读操作的 source
domain 由 [读操作与查询](../../../docs/spec/draft/query-check-and-output.md#读操作与查询) 定义。

迁移不得在机器区使用 `产出` 作为标签、关系或结论成员条件。`产出` 形状的 parser 与
修复格式尚未裁决，属于 `unresolved`；不要手工转换、忽略或猜测兼容行为，应保留诊断并
按 [结论快照](../../../docs/spec/draft/conclusions.md#未决-parser-边界) 停止。

当结构 mapping 可证明时，`move`、`promote`、`collapse` 仅改写 `conclusions.md` 已有条目
的链接目标；成员、顺序、标签、质疑来源和语义只由 `generate-conclusions` 重算。无法证明
mapping、链接闭包或制品解析时零写，不能把旧链接或目录内容当作可接受的迁移近似。
