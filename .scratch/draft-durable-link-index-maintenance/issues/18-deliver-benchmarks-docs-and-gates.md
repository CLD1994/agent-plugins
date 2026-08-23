# 18: 交付性能基准、使用文档和最终门禁

**What to build:** 交付可复现的链接索引性能证据、面向调用方的当前使用说明和完整质量门禁报告，证明严格 SHA-256 读取与增量解析的实际成本和收益。

**Blocked by:** 17: 验收耐久故障、恢复、并发和严格新鲜度.

**Status:** ready-for-agent

- [ ] 基准覆盖 100、1,000、5,000 个 Markdown 和宽目录，并使用固定种子生成可复现数据集。
- [ ] 场景至少包括首次构建、fresh backlink query、fresh `check`、无变化 notification、单文件变化、1% 文件变化、新增/删除/重命名、目录节点变化、rebuild-required 后恢复、`check` 维护和 journal 数量增长。
- [ ] 每个场景记录读取文件数和字节数、Markdown 解析文件数、wall time、峰值索引内存或可复现近似值、索引文件大小及 journal 写入次数和字节数。
- [ ] fresh 和无变化场景证明全部覆盖 Markdown 仍被读取，但摘要未变化的文件不重复解析；无变化 notification 的 journal 写入为零。
- [ ] 完整规模基准不进入默认测试门禁，不设置跨机器绝对耗时 SLA；默认门禁只运行小规模 schema 与计数 smoke test。
- [ ] 使用文档说明 notification 的可选后置通知语义、机会式维护、dry-run 无持久副作用、索引降级 warning 和 `recover` 只处理 recovery pending。
- [ ] 文档和输出不得把 notification 描述为索引错误的恢复步骤，也不得暴露内部 operation、revision 或路径作为调用方维护协议。
- [ ] 分别运行 runtime 单元测试、adapter 单元测试、runtime 集成测试、adapter 集成测试、格式检查、严格类型检查和 adapter 一致性检查。
- [ ] 最终报告列出实际命令与结果、网络排除项、基准摘要、提交清单和尚未核验的前提；测试全绿不能替代规格覆盖审计。
- [ ] 最终 diff 不包含多版本 schema 读取、mtime/size fresh 证明、notification 路径参数、随机临时索引、命令专用后置刷新或无关工作树改动。
