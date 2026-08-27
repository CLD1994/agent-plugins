# Draft operation runtime v1 执行路线图

Label: wayfinder:map

## Destination

形成一套不依赖执行者临场发明协议的 runtime v1 计划体系：共享总控计划、单上下文 tracer-bullet 执行 tickets、真实 blocking graph，以及每票可机械使用的 C0、测试预算、评审证据和升级条件。到达终点时只发布规划产物，不实施生产代码。

## Notes

- 领域为 Draft operation runtime；每次 session 必须使用 `grilling`、`domain-modeling`，涉及模块边界时使用 `codebase-design`，涉及 subagent 调度时使用 `i-am-yuna`，路线闭合后使用 `to-tickets`。
- runtime 行为以 `../draft-operation-runtime-foundation/spec.md` 为唯一权威；另建立一个当前领域行为合同，承载页面、状态、关系和命令语义。`CONTEXT.md` 只保存领域词汇。
- runtime v1 是 breaking replacement：旧或未知 schema 失败关闭，不迁移、不双读、不自动清理。
- 生产实现开始前先完成文档权威整理 D0，再完成测试基础设施 T0；T0 引入已批准的五个 pytest 插件与分层门禁。
- D0/T0 后在独立 namespace 中构建 v1；切换前不修改旧生产模块，公共入口只在一次性 cutover 中改变。
- Linux 证据可解除开发阻塞；Windows 与 macOS 证据阻塞完成和发布。
- 不运行完整旧 integration baseline；只有实际触碰旧生产模块时才停止并执行受影响的旧测试。
- 每张执行 issue 内嵌局部执行计划；共享总控计划只保存跨票协议。dispatch packet 固定 `base_sha`、spec SHA，并携带所需 spec 原文的非权威快照。
- 每票先由独立执行级 reviewer 验收；在 authority、并发、durability、恢复、公共协议和里程碑接缝处增加思考级 reviewer。
- 本 effort 的执行级与思考级 subagent 一律使用 `max` reasoning。模型升级遵守 `i-am-yuna` 的有效尝试与重新固定 C0 规则。

## Decisions so far

<!-- 决策只在对应 child ticket resolved 后以一行上下文指针加入。 -->

## Not yet specified

- 最终 tracer-bullet 执行 tickets 的数量、标题、局部计划与 blocking graph。
- v1 私有模块和文件布局；除隔离开发与 cutover 决策外，不预先固定实现结构。
- D0、T0、平台验收、性能验收、cutover 与旧 surface retirement 的最终执行切片。
- 各里程碑 accumulated integration 的精确选择与运行预算。

## Out of scope

- 在本 map 内实现或修改生产代码。
- 旧 runtime schema 或旧 Draft 根的数据迁移、兼容读取和双写。
- 完整旧 integration baseline。
- 非受协调 writer 的原子防护、网络卷、后台任务、终态审计 GC/full audit。
