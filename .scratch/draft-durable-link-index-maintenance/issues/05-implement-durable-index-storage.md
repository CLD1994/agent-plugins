# 05: 实现索引制品和 rebuild flag 的耐久存储

**What to build:** 实现链接索引制品存储端口，以 operation 专属 staging、原子替换和目录同步发布完整索引，并以纯存在性 flag 表示需要全根重建的快速阻断状态。

**Blocked by:** 02: 建立 operation journal v4 状态模型; 03: 建立纯链接索引领域引擎.

**Status:** ready-for-agent

- [ ] staging 只属于一个 operation，使用确定名称；首次写入是 create-only，幂等续写必须先证明现有普通文件的摘要与预期完全一致。
- [ ] staging 写入、文件同步、目标原子替换、目标父目录同步和 staging 父目录同步形成明确耐久顺序，发布摘要针对最终完整制品 bytes 计算。
- [ ] 首次发布、从 rebuild-required 发布以及替换现有 published 制品分别遵守规格中的目标分类矩阵。
- [ ] staging、索引目标、flag 或其受保护祖先是符号链接时返回 `path.symlink_escape`；目录或其他异常节点返回 `operation.inconsistent_state`，不得跟随、覆盖或递归清理。
- [ ] rebuild flag 是零字节普通存在性文件，不承载 JSON、原因、revision 或其他结构化数据；创建和删除成功后同步其父目录。
- [ ] flag 的普通 I/O 失败返回分类结果，异常节点返回内部状态错误；flag 本身不成为 authority。
- [ ] 清理只处理当前 operation 精确拥有的 staging，不使用通配符，也不删除其他 operation 或未知内部文件。
- [ ] 集成测试分别注入 staging write、文件同步、目录同步、原子替换、flag create、flag delete 和 flag 目录同步失败，并断言每一步留下的持久状态。
