# 02: 建立 operation journal v4 状态模型

**What to build:** 建立链接索引结果在 operation journal 中的唯一持久状态模型，使 journal 解析、写入、转换和恢复共享同一组 phase、字段和后继边规则。

**Blocked by:** 01: 固定链接索引维护的领域状态与依赖端口.

**Status:** ready-for-agent

- [ ] operation journal 只接受 v4；其他版本返回 `operation.journal_version_unsupported`，v4 中非法 phase、字段或转换返回 `operation.journal_corrupt`，不进行迁移、双读或默认值填补。
- [ ] 状态模型包含 `link_index_updating`、`link_index_published`、`link_index_rebuild_required` 和 `link-index.update` kind，并从一个权威定义派生 kind 所有权与允许后继边。
- [ ] `link_index_revision`、`link_index_result` 和 `link_index_sha256` 严格遵守规格字段矩阵；禁止字段必须缺失，不能用显式 `null` 代替。
- [ ] 进入 `link_index_updating` 后不存在通往 rollback 的边；`link_index_published` 和 `link_index_rebuild_required` 只能前向进入所属 operation 的既有成功终态。
- [ ] 经索引阶段进入 `completed` 或 `trash_retained` 时原样保留索引字段；任何 rollback phase 携带索引字段都会被拒绝。
- [ ] 所有 journal 构造和不可变转换都原样传递索引字段，只有相应索引 phase 转换能够引入或改变字段。
- [ ] 单元测试覆盖完整字段决策表、各 kind 的合法 phase、非法转换、超大 revision、终态字段保留和 rollback 禁止规则。
