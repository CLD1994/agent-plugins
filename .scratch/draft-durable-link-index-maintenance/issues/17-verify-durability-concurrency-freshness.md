# 17: 验收耐久故障、恢复、并发和严格新鲜度

**What to build:** 在真实文件系统和受协调多进程场景中系统验证索引事务的不变量，使正常路径、失败、重试、恢复、清理和下一次读取形成闭合证据链。

**Blocked by:** 16: 验收全部入口和 Draft 内部命名空间边界.

**Status:** ready-for-agent

- [ ] 建立受真实耐久 I/O 端口约束的故障注入器，分别覆盖 staging write/fsync、staging 目录同步、索引替换、索引目录同步、journal write/目录同步、flag create/delete/目录同步。
- [ ] 每个故障场景同时断言业务文件 before/after 摘要、journal phase 和索引字段、revision、索引制品摘要、flag、staging、锁、CLI 退出码/warning 及下一次命令是否接受索引。
- [ ] 覆盖 updating 已耐久但 staging 未写、索引目标已替换但 published 未写、published 已耐久但原成功终态未写三个中断窗口，并分别执行一次和重复 recovery。
- [ ] 两个维护者从同一 revision 扫描时，较晚取得锁者在发现 authority 已推进后放弃旧候选，不发布额外 revision，也不记录 rebuild-required。
- [ ] 扫描期间根再次变化时不发布混合时点索引、不无限重试；notification、`check` 和 query 分别保持自己的既定输出语义。
- [ ] 构造正文变化但 size、inode 探针值和 mtime 探针值相同的反例，证明 fresh 路径仍通过 SHA-256 识别变化并拒绝旧 backlink。
- [ ] 覆盖 Markdown 新增、删除、重命名、不可读、非 UTF-8、链接提取失败、目录节点类型变化以及索引目标/flag 异常节点。
- [ ] 测试预期根据独立的当前正文事实计算，不调用生产索引计算路径生成预期 backlink。
- [ ] 完成端到端契约闭合审计，沿根事实、候选、authority、提交、flag、终态、warning/error、recover 和下一次 fresh 证明追踪同一状态。
