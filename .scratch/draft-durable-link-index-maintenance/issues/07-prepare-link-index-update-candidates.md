# 07: 实现无副作用的索引候选准备服务

**What to build:** 组合 authority repository、严格根事实来源和纯索引引擎，在不取得写锁或修改 Draft 的前提下，为后续提交产生带观察令牌的完整维护决策。

**Blocked by:** 04: 实现严格 Draft 根事实来源; 06: 实现链接索引 authority repository.

**Status:** ready-for-agent

- [ ] 准备开始时读取当前 authority，并把 absent 或观察到的 revision 作为提交时必须重新验证的并发令牌。
- [ ] absent 从当前根完整构建候选；合法 published 使用严格验证后的制品作为增量基线；rebuild-required 从当前根完整构建。
- [ ] 根事实与可信索引内容完全一致时返回无变化，并且该结果不要求分配 operation 或 revision。
- [ ] 正常变化返回完整、确定的候选内容；不可读取、不可解析或扫描不稳定返回要求重建的分类结果；journal 与制品矛盾返回内部状态错误。
- [ ] 准备服务不取得写锁、不创建 operation、不写 journal、索引、staging 或 flag，也不删除任何持久对象。
- [ ] 上层已知 Markdown 事实只作为可验证的解析复用输入，不改变严格正文读取和摘要证明。
- [ ] 单元测试使用受端口约束的 fake 穷举 authority 与扫描结果组合；集成测试证明准备前后 Draft 内部状态完全不变。
