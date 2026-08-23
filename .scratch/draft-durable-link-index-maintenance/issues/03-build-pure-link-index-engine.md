# 03: 建立纯链接索引领域引擎

**What to build:** 建立不依赖文件系统和 journal 的链接索引引擎，根据已经读取的 Markdown 内容、目录事实和可选可信基线计算完整索引内容，并提供严格、确定的 schema v2 编解码。

**Blocked by:** 01: 固定链接索引维护的领域状态与依赖端口.

**Status:** ready-for-agent

- [ ] schema v2 顶层只包含 `schema_version`、`revision`、`files`、`backlinks` 和 `directories`；不保存绝对 Draft 根、inode、size、mtime、完整性状态或解析状态。
- [ ] 相同逻辑索引产生完全相同的序列化 bytes；解析严格验证 revision、相对路径、SHA-256、span、资源类型、目录成员类型、重复项和未知字段。
- [ ] `backlinks` 必须与全部 `files[*].outbound` 精确双向一致；缺失、多余或部分反向边都使制品无效。
- [ ] 路径和 SHA-256 都未变化的 Markdown 复用既有出站事实；新增或摘要变化的文件重新解析；删除文件移除全部出站边和 backlink。
- [ ] 相同 bytes 出现在新路径时按新来源重新解释相对资源身份，不能只按摘要复用原路径的出站事实。
- [ ] 非 Markdown 正文不进入文件记录；目录成员名称或节点类型变化仍使索引内容发生变化。
- [ ] 统计值分别记录读取文件数、读取字节数和 Markdown 解析文件数，并由真实计算路径产生。
- [ ] 单元测试只使用内存输入，独立覆盖合法、非法、边界和增量反例。
