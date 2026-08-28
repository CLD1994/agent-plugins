# Draft 当前规格

本页是 Draft 插件当前行为规格的唯一入口。正式规格页面只表达当前有效规范；历史 provenance 通过页面的 `来源` 区域按需读取。

## 治理

- [权威与成员资格](governance/authority.md)
- [迁移与原子切换](governance/migration.md)
- [Feature spec 与 tracker](governance/feature-specs.md)

## Draft 领域

- [生命周期](draft/lifecycle.md)
- [页面与导航](draft/pages-and-navigation.md)
- [状态与关系](draft/status-and-relations.md)
- [资源与链接](draft/resources-and-links.md)
- [页面编辑](draft/page-edits.md)
- [结构操作](draft/structure.md)
- [Trash 与 Restore](draft/trash-and-restore.md)
- [查询、检查与输出](draft/query-check-and-output.md)
- [结论快照](draft/conclusions.md)

## 交付与运行时

- [插件与入口](delivery/plugin-and-entrypoints.md)
- [运行时接口](runtime/interface.md)
- [初始化与平台](runtime/initialization-and-platform.md)
- [准入与副作用](runtime/admission-and-effects.md)
- [权威与投影](runtime/authority-and-projections.md)
- [生命周期与 handoff](runtime/lifecycle-and-handoff.md)
- [恢复](runtime/recovery.md)
- [错误与可观测性](runtime/errors-and-observability.md)

## 共同规则

每个操作先认证其状态维度和请求闭包；无法证明的事实进入失败分支。写操作必须完整计划后原子提交或零写。实现和测试不能反向改变本页及其子页定义的行为。
