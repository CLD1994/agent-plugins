# 页面头部的读取、编辑和生成统一使用 YAML

Status: ready-for-agent
Tracker-State: open
Design-Refs:
  - plugins/draft/docs/spec/draft/pages-and-navigation.md
  - plugins/draft/docs/spec/draft/status-and-relations.md
  - plugins/draft/docs/spec/draft/page-edits.md
  - plugins/draft/docs/spec/draft/structure.md
  - plugins/draft/docs/spec/draft/resources-and-links.md
  - plugins/draft/docs/spec/draft/conclusions.md
  - plugins/draft/docs/spec/draft/trash-and-restore.md
  - plugins/draft/docs/spec/draft/query-check-and-output.md
  - plugins/draft/docs/spec/runtime/interface.md
  - plugins/draft/docs/spec/runtime/errors-and-observability.md

## 交付结果与范围

Draft 能读取符合现行合同的 YAML 页面头部，所有生成或修改头部的入口产出可再次读取的页面。
状态或关系编辑逐字保留头部结束行之后的正文；查询、导航、结论和诊断使用正确的头部／正文边界。

本任务是独立的领域实施入口，不属于 runtime 基础任务的子范围。覆盖页面格式、相关字段读取与写回、
原文位置以及这些变化的直接消费者；完成本任务不表示初始化状态机、整个查询协议或其他领域操作已全部实现。

## 权威来源

先按[上下文路由](../../../CONTEXT-MAP.md)、[Draft 词汇](../../../plugins/draft/CONTEXT.md)和
[当前规格入口](../../../plugins/draft/docs/spec/index.md)定位行为，再读取以下对应章节及其直接依赖：

- [页面头部与正文边界](../../../plugins/draft/docs/spec/draft/pages-and-navigation.md#页面头部与正文边界)：分隔行、解析次序、正文隔离与原始位置。
- [YAML 页面头部结构](../../../plugins/draft/docs/spec/draft/status-and-relations.md#yaml-页面头部结构)：字段形状、认证顺序、稳定写回与正文保留；同页的状态／关系规则约束字段语义。
- [创建、状态与关系编辑](../../../plugins/draft/docs/spec/draft/page-edits.md#创建状态与关系编辑)和[结构操作](../../../plugins/draft/docs/spec/draft/structure.md)：各入口拥有的字段变化、parent／关系目标与路径 mapping 的一致性。
- [资源与链接](../../../plugins/draft/docs/spec/draft/resources-and-links.md)、[查询与检查](../../../plugins/draft/docs/spec/draft/query-check-and-output.md)：头部信息不进入正文资源；诊断选择、抑制与定位。
- [结论快照](../../../plugins/draft/docs/spec/draft/conclusions.md)：结论制品的固定头部、解析和目标 span；本任务不重做结论成员资格或生成状态的持久化设计。
- [Trash 与 Restore](../../../plugins/draft/docs/spec/draft/trash-and-restore.md)：页面字节保留及父页维护的消费边界。
- [公共错误投影](../../../plugins/draft/docs/spec/runtime/errors-and-observability.md)：本任务涉及的页面诊断外壳、机器字段与输出通道。
- [页面头部使用 YAML 的理由](../../../plugins/draft/docs/adr/0012-page-metadata-uses-yaml-frontmatter.md)。

这些引用是行为来源；下文只组织实现与验收，不构成另一份字段或错误登记表。

## 实施顺序与覆盖

1. 追踪页面头部从读取、认证、领域字段、变更计划到写回和重新读取的完整链路；同时定位正文链接扫描及原文位置消费者。
   如需保持行为不变的预重构，先在本任务内完成并验证，再切换格式；模块、私有类型和文件安排由实际数据流决定。
2. 从 owner 的状态分类推导先失败的单元测试，完成严格解析、稳定写回和原文位置契约，再连接所有真实读写入口。
3. 以集成测试验证下列消费者，包含失败、零写及故障后再次读取／恢复的路径：相关入口在写前认证失败时零写，
   写后耐久失败不报告成功并保留 owner 规定的恢复现场；恢复或重试后重新读取不产生未经认证的页面事实。
   私有计划、记录和保存表示由实现者依据数据流选择，本任务只约束这些可观察结果。

| 入口或消费者 | 本任务必须验证的边界 |
| --- | --- |
| 页面创建 | 按页面编辑 owner 生成新叶页面的头部及页面字节，创建后能被页面读取及 Check 消费。 |
| 根页头部生成与读取 | 对既有根页生成器的头部序列化及其读取消费者验证 YAML 格式；本票不决定初始化内容集合、根页初始状态或固定页面模板。需要这些行为决定时停止相关部分，转入初始化边界讨论。 |
| 状态、说明和关系编辑 | 按 owner 修改相应字段，保持其余关系顺序和正文原字节；关系语义及配对约束继续成立。 |
| move、枢纽升格、折叠 | parent 与关系目标作为头部字段维护，并与同一结构 mapping、物理路径和导航一致；不能只改正文链接。 |
| 页面纳管 | move 使用 owner 规定的纳管资格和原文处理规则；不凭路径变化擅自补齐缺少的头部。 |
| 结论生成与结构窄改写 | 使用固定结论头部，原文位置包含头部偏移；结构操作只改已认证链接目标。 |
| Trash／Restore | 保存、写回的页面字节符合其合同；父页导航及头部消费者能读取恢复结果。 |
| 页面图、查询、Check、链接索引及链接闭包 | 头部与正文分别提供事实；头部里的链接形字符串不产生正文链接、反向链接、导航或结论内容。 |

## 验收条件

- [ ] 根、叶、枢纽和结论制品分别使用对应 owner 规定的头部；合法键顺序及引号变化不改变事实，写回遵守稳定顺序。
- [ ] 对分隔行、YAML 语法、文档形状、重复／未知键、不支持的 YAML 特性及字段类型／值，按 owner 的完整分类验证诊断；同一节点不重复归因，独立可认证问题不互相吞掉。
- [ ] 未通过头部认证的页面不提供虚假的状态、parent 或关系事实；依赖这些事实的写操作按其 owner 在写前拒绝。
- [ ] 状态或关系编辑前后的正文按原字节比较；包含非 ASCII 内容、多行头部和不同换行形式的原文位置测试能发现偏移错误。
- [ ] 上表所有入口均有相应单元或真实协作集成证据；验证写出后再次读取、头部形似 Markdown 的内容隔离，以及修改／恢复后的头部可读性。
- [ ] 机器 code、details、退出码按对应 owner 精确验证；人类文案验证输入事实与结构，不复制整段文案作为协议。
- [ ] 标题与导航、结论标签按[标题与链接文字](../../../plugins/draft/docs/spec/draft/pages-and-navigation.md#标题与链接文字)认证；纯文本请求经转义生成后，解析值与请求相同，且不会生成被禁止的内联结构。
- [ ] 按[插件测试规范](../../../plugins/draft/AGENTS.md)分别运行受影响的核心与适配层单元、集成及静态检查，列明执行结果和未运行项；完成差异检查及同类读写入口审计。

## 依赖、集成与停止条件

本任务与[无效请求在读取 Draft 根之前被拒绝](../../draft-operation-runtime-foundation/issues/01-request-validation.md)没有阻塞边。
两者可能接触共同的路径值或错误投影消费者；规则复用现有权威入口，不各自维护一套完整协议。

Blocked by:
  - [根锁与恢复前置任务](../../draft-operation-runtime-foundation/issues/04-root-lock-and-recovery.md)：实际写入接入等待根锁与恢复前置完成。

纯解析、读取和纯计划可独立继续；runtime 的完整持久状态机不是本任务的前置。这不免除真实写入、失败和恢复的回归验证。
如果实际实现证明必须改变持久记录协议、补充未定义的状态或扩大到其他完整行为，停止相关工作并报告具体 owner、
调用链与反例；不得用测试通过声明这些额外行为已获批准。

## Comments

2026-09-06：用户批准本任务粒度及与请求校验任务互不阻塞的安排。

2026-09-06：独立评审发现结论链接标签的原始 HTML 解码分区未定义。CommonMark 的普通文本、转义、实体、强调和行内代码可按已认证可见值处理；原始 HTML 的接受/拒绝需要用户明确，实施者不从当前 parser 行为反推规则。已暂停受影响的结论解析与接口采用，已有候选及验证记录保留。

2026-09-06：用户确认页面标题、导航和结论链接文字拒绝原始 HTML，允许普通 Markdown 强调、行内代码、转义及字符实体，比较解析后的文字。规则位于当前页面与导航 owner，结论和 request owner 引用同一规则；恢复实施资格。

2026-09-06：写入修复计划的独立前提核验确认，现有 acquire_lock 只有排他创建的 JSON 文件，没有 live OS lock；现有恢复入口会清理 matching 空 allocation，不能安全承载写前锁内重新计划。暂停 Execute 接入，向用户申请独立的 runtime 锁与恢复前置任务；不采用新的 lock 路径、持久格式或恢复状态作为未经批准的实现细节。

2026-09-06：用户批准根锁与崩溃恢复的独立前置修复，已登记到 runtime 任务 04；该依赖完成后接入实际写入，纯解析/读取/计划可继续。

2026-09-06：用户确认一级标题限定为单行文字，允许强调、加粗、行内代码、转义和实体，不允许链接、图片或换行；完整分类及诊断由当前页面 owner 和中央登记拥有，共享 Markdown 实施计划消费该规则。

2026-09-07：已批准的标题与链接文字规则已进入 current spec；本票 Base-Spec 更新为 4cf3b96b7d30e4c3f9d2374b88852e160d35dfa2。整票 Execute 仍等待 issue 04；纯解析、读取和纯计划可继续。
