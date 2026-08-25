# Draft 链接索引耐久维护规格

Status: ready-for-agent

## 问题陈述

Draft 以根内 Markdown 和目录事实为权威来源。链接索引只是从这些事实派生的查询缓存，
但结构写命令会使用它缩小链接事实闭包；因此，程序只有在能够证明索引与当前根一致时，
才能用索引证明“不存在其他 backlink”。

业务 operation 改变根内 Markdown 或索引覆盖的目录事实后，必须在释放写锁和进入成功
终态前，耐久证明以下结果之一：

- 与业务结果对应的新索引已经发布；
- 链接索引需要全根重建，既有索引不得再进入 fresh 路径。

如果这个结果不属于 operation journal，进程中断或普通 I/O 失败就可能留下已经改变的
用户内容和仍被接受的旧 backlink。索引故障还可能错误地迫使程序回滚已经验证的业务
结果，或者让调用者无法判断是否需要恢复。

Draft 还需要处理两种正常的索引维护来源：

- 调用方在 Draft 命令之外编辑了根内文件，并主动通知 Draft；
- `check` 或链接查询在严格验证时发现索引尚未建立、需要更新或要求重建。

这些入口必须共享同一套严格扫描、增量计算、耐久发布和恢复协议。索引只能减少重复
CommonMark 解析和反向边计算，不能以 inode、文件大小或时间戳代替正文摘要证明。

## 解决方案

链接索引采用“内容事实为权威、journal 记录索引结果、索引制品保存派生快照、存在性
flag 提供快速阻断”的模型：

1. 每个改变链接索引事实的业务 operation 在验证业务结果后进入链接索引阶段，并继续
   持有写锁和恢复材料。
2. operation journal 使用单调正整数 `link_index_revision` 记录最新索引结果；
   `published` 和 `rebuild_required` 都是权威结果。
3. 新索引通过 operation 专属 staging 文件耐久写入并原子发布。journal 只有在目标
   索引已经耐久发布后才记录 `published`。
4. 索引无法完整构建或发布时，程序保留已经验证的业务结果，并耐久记录
   `rebuild_required`。只有 journal 结果本身无法耐久记录时才保留锁并要求 operation
   recovery。
5. 任何索引进入 fresh 路径前，程序读取全部覆盖 Markdown 并比较 SHA-256，同时验证
   目录事实、schema、revision、索引文件摘要和反向表完整性。
6. 严格扫描只重新解析路径或 SHA-256 发生变化的 Markdown；未变化记录直接复用。
7. `draft notify files-changed --root ROOT` 允许调用方在外部编辑完成后主动触发同一
   维护流程。通知不携带路径或变更类型，也不把消费者结果暴露为命令失败。
8. `check`、`query backlinks`、`query page` 和 `adopt` 的完整预检查可以机会式耐久
   发布索引。它们检测到需要维护时自行处理，不提示调用方执行 notification。
9. `recover` 按 journal 证据机械收敛原 operation。进入链接索引阶段后只允许前向
   收敛，不因派生缓存故障撤销业务内容。

## 用户故事

1. 作为执行结构写命令的调用方，我希望业务内容验证完成后再由同一 operation 收敛链接
   索引结果，从而不会看到内容已改变但旧 backlink 仍被接受的状态。
2. 作为执行结构写命令的调用方，我希望链接索引发布失败时保留已经验证的业务结果，
   从而不会因为派生缓存故障丢失用户内容。
3. 作为执行结构写命令的调用方，我希望索引降级具有稳定 warning 和退出语义，从而能
   区分业务成功、缓存降级和 operation 耐久失败。
4. 作为恢复中断 operation 的调用方，我希望 `recover` 只根据耐久证据决定下一步，
   从而不依赖上一个进程中的异常历史。
5. 作为恢复中断 operation 的调用方，我希望重复执行 `recover` 得到相同结果，从而能
   安全重试。
6. 作为维护 Draft 的调用方，我希望所有会改变根内 Markdown 或目录事实的 operation
   自动进入索引阶段，从而不需要为每个命令记住独立的刷新步骤。
7. 作为维护 Draft 的调用方，我希望是否进入索引阶段由实际 mutation 决定，从而空操作
   和只修改 `.draft/` 内部状态的 operation 不产生无意义 revision。
8. 作为执行 `move` 的调用方，我希望 fresh 索引仍覆盖受管理页面和未受管理 Markdown
   的全部 backlink，从而不会因性能优化缩小链接事实闭包。
9. 作为执行 `move` 的调用方，我希望索引不能证明完整时自动使用全根事实，从而结构
   结果不依赖缓存健康程度。
10. 作为直接编辑 Draft 文件的调用方，我希望能在编辑完成后执行
    `draft notify files-changed`，从而主动触发派生状态维护。
11. 作为直接编辑 Draft 文件的调用方，我希望 notification 不要求枚举路径、重命名对
    或变更类型，从而不需要维护一份程序并不信任的变化清单。
12. 作为直接编辑 Draft 文件的调用方，我希望遗漏 notification 不破坏正确性，从而
    notification 只是性能维护入口而不是强制写入协议。
13. 作为 notification 的调用方，我希望有效 Draft 根收到通知后命令固定成功且不返回
    消费者 warning，从而通知是否送达不与当前索引维护结果耦合。
14. 作为 notification 的调用方，我希望参数错误、无有效 Draft 根和程序未正常执行仍
    按真实结果报告，从而不会收到虚假的送达确认。
15. 作为 notification 的调用方，我希望反复通知但根事实没有变化时不产生 revision 或
    operation journal，从而不会放大 journal 扫描成本。
16. 作为执行 `check` 的调用方，我希望发现索引需要维护时程序直接利用本次扫描结果
    尝试发布，从而后续命令可以复用缓存。
17. 作为执行 `check` 的调用方，我希望索引发布失败时仍得到完整检查事实，从而缓存写入
    不会丢失已经完成的页面检查。
18. 作为执行 `query backlinks` 的调用方，我希望查询在索引可用时减少重复 Markdown
    解析，从而降低大根的反向链接查询成本。
19. 作为执行 `query backlinks` 的调用方，我希望查询在索引不可用时从当前根完整计算，
    从而结果不依赖缓存。
20. 作为执行 `query page` 的调用方，我希望 backlink count 使用与
    `query backlinks` 相同的严格事实，从而两个入口不会产生矛盾计数。
21. 作为执行 `query links` 的调用方，我希望单页查询不为维护全根索引支付额外写入，
    从而窄查询保持窄成本。
22. 作为执行 relations、status 或 recoverable 查询的调用方，我希望这些不消费链接
    索引的查询不触发索引维护，从而无关命令不产生内部写入。
23. 作为执行 dry-run 的调用方，我希望命令不创建 journal、索引 revision 或 flag，
    从而 dry-run 保持无持久副作用。
24. 作为执行 `adopt` 的调用方，我希望索引性能降级不阻止候选根登记，从而派生缓存不
    成为页面合法性的权威来源。
25. 作为执行 `adopt` 的调用方，我希望无法解释的 journal 或索引制品矛盾阻止登记，
    从而 registered 根不会掩盖内部状态损坏。
26. 作为执行 `init` 的调用方，我希望初始化不必发布首次链接索引，从而可选缓存不会
    扩大新根创建的恢复协议。
27. 作为索引使用者，我希望每次证明 fresh 前比较全部覆盖 Markdown 的 SHA-256，
    从而相同文件大小、复用 inode 或时间戳量化不会产生错误 backlink。
28. 作为索引使用者，我希望目录成员名称和节点类型也属于快照，从而新增、删除、重命名
    和节点类型变化不会逃过覆盖证明。
29. 作为索引使用者，我希望符号链接不被当作普通 Markdown 读取或跟随，从而扫描不会
    越过 Draft 的词法边界。
30. 作为索引使用者，我希望无法读取、解码或提取链接的 Markdown 阻止完整索引发布，
    从而“没有 backlink”的证明不会遗漏不可判定文件。
31. 作为索引使用者，我希望路径不变且 SHA-256 相同的文件复用既有解析结果，从而严格
    正文读取不会退化成每次全根 CommonMark 解析。
32. 作为索引使用者，我希望相同字节移动到新路径后重新解析相对资源身份，从而相对链接
    按新的来源位置解释。
33. 作为索引使用者，我希望索引文件的反向表与出站边在读取时互相验证，从而损坏或部分
    写入的反向表不会被接受。
34. 作为维护者，我希望 revision 从 `"1"` 开始并以规范十进制字符串持久化，从而没有
    固定宽度溢出或字符串字典序比较错误。
35. 作为维护者，我希望最新 journal 结果是链接索引状态的唯一权威来源，从而 flag、
    索引制品和命令各自不会维护冲突状态。
36. 作为维护者，我希望索引 schema 只保存严格证明和查询所需事实，从而不把变化探针
    误当作正确性字段。
37. 作为维护者，我希望索引制品不保存绝对 Draft 根路径，从而整个根移动或复制后仍可
    按内容、journal 和 revision 验证。
38. 作为维护者，我希望索引 staging 文件属于唯一 operation，并位于其 operation 目录，
    从而崩溃恢复可以精确识别和清理。
39. 作为维护者，我希望发布目标或 flag 是符号链接、目录或其他异常节点时停止并保留
    证据，从而程序不会覆盖未知内部状态。
40. 作为维护者，我希望 flag 只表达存在性且不承载第二份结构化状态，从而 journal 继续
    是唯一权威来源。
41. 作为维护者，我希望 flag 普通 I/O 失败具有稳定 warning，从而快速路径持续退化时
    调用者能够观察到。
42. 作为维护者，我希望 journal 耐久写持续失败时保留锁和现场，从而后续写入不会越过
    无法解释的 operation。
43. 作为维护者，我希望发布结果未知时统一进入 `rebuild_required`，从而恢复器不会
    猜测发布成功或重复增量发布。
44. 作为维护者，我希望 `link_index_published` 与索引制品不一致时报告内部状态错误，
    从而新的 revision 不会掩盖证据。
45. 作为维护者，我希望并发维护在取得锁后比较扫描开始时观察的 revision，从而较旧
    扫描不会覆盖另一个 operation 已发布的新结果。
46. 作为维护者，我希望回收保留态和 restore 的既有终态在索引阶段后继续成立，从而
    引入索引提交不会改变页面回收生命周期。
47. 作为维护者，我希望空目录清理在索引构建前完成并按实际结果建立目录快照，从而
    索引发布后不再发生未记录的目录变化。
48. 作为实现者，我希望 operation phase、字段合法性和转换边由一个权威模型约束，从而
    解析、写入、恢复和测试不会各自维护一套状态清单。
49. 作为实现者，我希望预期消费者故障与程序缺陷具有不同边界，从而 notification 可以
    吸收已分类维护结果而不会吞掉断言、类型错误或未知异常。
50. 作为测试者，我希望在真实文件系统上注入每个耐久步骤的失败和进程中断，从而成功
    路径不能掩盖提交窗口缺陷。
51. 作为测试者，我希望性能基准分别记录正文读取和 Markdown 解析，从而能够判断严格
    SHA-256 的成本以及索引实际节省的工作。
52. 作为测试者，我希望基准覆盖 100、1,000、5,000 文件和宽目录，从而优化决策有可复现
    数据而不是依赖小样本直觉。

## 实现决策

### 1. 保障范围

当前协议面向本地文件系统、单个受协调 CLI 写者、普通 I/O 失败和已定义耐久步骤后的
进程中断。索引文件发布依赖同一文件系统内的原子替换、文件同步和目录同步。

同一 Draft 根上的受协调写者只能通过唯一的 `.draft/operation.lock` 串行化。无锁扫描只
发现和分类候选，绝不授权 journal、恢复材料、权威删除包或 operation 目录的删除、替换、
硬链接或 phase 写入。任何需要这些副作用的恢复候选都必须先取得其 operation ID 的锁，
在锁内重新读取当前目录、journal 和关联后再按状态机处理。对绕过锁、直接改名、替换或
修改 `.draft/` 节点的同权限进程，协议不承诺 Python 标准库无法提供的原子命名空间防护；
但扫描或读取时可观察到的符号链接、非法类型、损坏和身份不一致仍必须失败关闭。

调用方在执行 notification 前应完成外部批量编辑并使根进入稳定状态。程序拒绝把观察到
的混合时点快照发布为完整索引；持续变化的根不会触发无限重试。

### 2. 权威来源和持久对象

链接索引状态具有以下单一权威链：

1. 当前版本 operation journal 中具有链接索引结果的合法记录；
2. 与最新 `published` 记录绑定的索引制品；
3. 由根内 Markdown 和目录事实进行的严格新鲜度证明。

`.draft/link-index-rebuild-required.flag` 是纯存在性 flag，不承载 JSON、原因文本、
revision 或其他结构化数据。flag 只用于快速阻止未经 journal 判定的旧索引进入 fresh
路径。flag 缺失时仍必须读取 journal；journal 始终是权威来源。

索引制品位于 `.draft/link-index.json`。候选制品位于当前 operation 的
`.draft/operations/<operation_id>/link-index.staged.json`，路径由 operation ID 唯一
推导。

### 3. revision

`link_index_revision` 表示链接索引状态的单调正整数修订号。规则如下：

- 首个结果使用 `"1"`；
- `published` 和 `rebuild_required` 都可以形成新的 revision；
- journal 和索引制品以匹配 `[1-9][0-9]*` 的规范十进制字符串持久化；
- 内存使用任意精度正整数解析和比较；
- 不接受前导零、符号、小数、指数、空白或 JSON 数字；
- 不按字符串字典序比较；
- `recover` 继续原 operation，不分配独立 revision；
- 严格证明根和索引事实没有变化时，不创建 maintenance operation，也不产生 revision；
- 无法证明事实未变化且需要耐久记录降级时，使用下一 revision。

读取 journal 时只收集合法终态或合法索引结果阶段中的链接索引结果，并以数值最大的
revision 作为最新权威结果。重复 revision、同一 revision 的冲突结果或非法字段组合是
内部状态错误。暂时接受发现最新 revision 的 `O(J)` journal 扫描。

### 4. operation journal 字段

journal 顶层字段使用完整限定名称：

```text
link_index_revision
link_index_result
link_index_sha256
```

字段合法性由 phase 决定：

| Phase | `link_index_revision` | `link_index_result` | `link_index_sha256` |
| --- | --- | --- | --- |
| 索引阶段前 | 禁止 | 禁止 | 禁止 |
| `link_index_updating` | 必须 | 禁止 | 禁止 |
| `link_index_published` | 必须 | 必须为 `published` | 必须 |
| `link_index_rebuild_required` | 必须 | 必须为 `rebuild_required` | 禁止 |
| 经索引阶段到达原有成功终态 | 原样保留 | 原样保留 | 仅 `published` 保留 |
| rollback 路径 | 禁止 | 禁止 | 禁止 |

“禁止”表示对应 JSON 键不得出现，不使用 `null` 代替缺失；“必须”表示键必须出现且值
满足该行约束。解析器必须区分键缺失与显式 `null`。

operation journal 的唯一支持版本为 v4。程序不双读、不迁移、不填补其他版本默认值。
非 v4 journal 返回 `operation.journal_version_unsupported`；v4 的非法 phase、字段或
转换组合返回 `operation.journal_corrupt`。

### 5. operation phase 和状态转换

通用结构 operation 在业务结果验证后进入索引阶段：

```text
applying
→ validating
→ link_index_updating
→ link_index_published | link_index_rebuild_required
→ 原有成功终态
```

约束如下：

- 在产生任何索引 staging、目标替换或 flag 副作用前，先耐久写
  `link_index_updating`；
- `link_index_updating` 表示业务结果已经验证，但索引结果尚未得到耐久证明；
- `link_index_updating` 之后不得进入 `rolling_back`；
- 发布结果未知时进入 `link_index_rebuild_required`，不猜测已发布，也不重复增量发布；
- `link_index_published` 和 `link_index_rebuild_required` 都不是 operation 最终终态；
- 普通结构 operation 随后进入 `completed`；
- `trash` 随后进入 `trash_retained`；
- `restore` 随后进入 `completed`，再执行既有双 journal 协调清理；
- `recover` 恢复原 kind 和 operation ID，不形成独立持久 operation；
- `adopt` 只修改 `.draft/` 内 marker，不进入索引阶段；
- `init` 不使用索引阶段。

专用 maintenance kind 为 `link-index.update`。它只记录实际的索引维护，不记录触发入口：

```text
allocating
→ link_index_updating
→ link_index_published | link_index_rebuild_required
→ completed
```

如果 maintenance 在产生索引副作用前中断，`allocating` 可以进入 `rolled_back`。严格
证明无需维护时不创建该 operation。

phase、kind 所有权、允许后继边和字段增量由同一个状态模型提供给解析、写入和恢复代码。
不增加能从任意阶段进入的通用失败 phase。

### 6. 哪些业务 operation 进入索引阶段

索引阶段由实际 mutation 决定，不按命令白名单决定。凡 operation 改变以下任一事实，
都进入索引阶段：

- `.draft/` 之外的普通 Markdown 内容或路径；
- 索引覆盖目录的成员名称或节点类型。

当前已知相关命令包括 create、move、promote、collapse、status set、relation add、
relation remove、generate-conclusions、trash 和 restore。未来 kind 只要产生相同事实
变化，就自动受到同一规则约束。

空目录清理在 `validating` 完成后、进入链接索引阶段前按深到浅执行。程序只对计划精确
登记的路径执行非递归目录删除；缺失视为已经收敛。删除成功后同步父目录。成员、符号
链接或节点类型发生未解释变化时返回 `operation.external_change` 并保留锁。普通 I/O
原因导致目录不能删除时可以保留，索引按实际目录状态构建；实际状态无法读取时进入
`rebuild_required`。索引发布后不再执行目录清理。

### 7. 索引 schema

索引制品的唯一支持 schema 为 v2。其他 schema 不参与读取或迁移。已发布索引必然完整，
因此制品只保存严格证明和查询所需事实：

```json
{
  "schema_version": 2,
  "revision": "4",
  "files": {
    "pages/a.md": {
      "sha256": "...",
      "outbound": []
    }
  },
  "backlinks": {},
  "directories": {}
}
```

字段语义：

- `revision` 使用索引文件自身上下文中的简洁名称，并与 journal 的
  `link_index_revision` 相等；
- `files` 覆盖根内除 `.draft/` 外的全部普通 `.md` 文件，保存 SHA-256 和出站链接；
- `backlinks` 是 `files[*].outbound` 的精确反向表；
- `directories` 保存除 `.draft/` 外全部遍历目录的直接成员名称和节点类型；
- 制品不保存绝对根路径；
- 制品不保存文件系统变化探针；
- 制品不保存不完整或不可解析状态。

序列化必须确定：相同逻辑索引产生相同字节。`link_index_sha256` 对最终发布的完整制品
字节计算；读取时同时验证 schema、revision、制品摘要和 `files`/`backlinks` 的双向
一致性。

### 8. 严格扫描和增量计算

严格扫描执行以下步骤：

1. 遍历 Draft 根并排除整个 `.draft/`；
2. 记录目录成员名称和节点类型；
3. 只读取普通 `.md` 文件，不跟随符号链接；
4. 读取全部覆盖 Markdown 并计算 SHA-256；
5. 比较当前路径集合、目录事实和可信索引快照；
6. 路径与 SHA-256 均未变化的文件复用既有解析结果；
7. 新增或 SHA-256 变化的文件重新解析；
8. 删除文件的出站边和 backlinks 被移除；
9. 从完整文件记录产生自洽的反向表和目录快照。

inode、size 和 mtime 只能作为进程内变化探针，不能证明文件未变化，也不进入持久
schema。相同字节出现在新路径时按删除加新增处理；即使可以复用与路径无关的语法分析
结果，也必须根据新来源路径重新解析资源身份。

修改既有非 Markdown 普通文件的内容不改变索引。非 Markdown 节点的新增、删除、重命名
或节点类型变化会改变目录事实，但程序不读取其正文。

任一覆盖 Markdown 无法读取、求摘要、按 UTF-8 解码或提取 CommonMark 链接时，不发布
完整索引。本轮维护进入 `rebuild_required`。结构命令和查询仍按各自事实闭包决定是否
能够继续，不把不可判定文件解释为“没有链接”。

### 9. 可信增量基线

维护行为由最新权威状态决定：

| 最新状态 | 当前制品 | 行为 |
| --- | --- | --- |
| 没有链接索引结果 | 不要求存在 | 全根构建；成功时发布 revision `"1"` |
| `rebuild_required` | 不具有权威性 | 全根构建 |
| `published` | revision、摘要、schema、内部表均匹配 | 严格扫描后增量计算 |
| `published` | 制品缺失或 revision、摘要、schema、内部表不匹配 | `operation.inconsistent_state` |

索引制品有效但根内 Markdown 摘要或目录事实不同，表示正常的根变化，进入增量维护；它
不是索引制品损坏。

### 10. 发布顺序

发布新索引：

```text
耐久写 link_index_updating
→ 耐久写 operation 专属 staging
→ 原子替换 .draft/link-index.json
→ 同步 .draft/ 和 operation 目录
→ 耐久写 link_index_published
→ 尽力耐久删除 rebuild-required flag
→ 写原有成功终态
```

staging 的 SHA-256 必须等于 journal 的 `link_index_sha256`。发布目标的分类规则如下：

| 最新权威状态 | 当前索引目标 | 行为 |
| --- | --- | --- |
| 没有链接索引结果 | 缺失 | 允许首次发布 |
| 没有链接索引结果 | 普通文件 | `operation.inconsistent_state` |
| `rebuild_required` | 缺失或普通文件 | 允许发布并替换普通旧文件 |
| `published` | 与 journal 完全一致的普通文件 | 允许以更高 revision 替换 |
| `published` | 缺失或内容不一致 | `operation.inconsistent_state` |
| 任意状态 | 符号链接、目录或其他节点 | `operation.inconsistent_state` |

staging、目标及其祖先都使用 no-follow 边界。程序不得覆盖、递归删除或跟随异常内部
节点。

记录要求重建：

```text
耐久写 link_index_rebuild_required
→ 尽力耐久创建 rebuild-required flag
→ 写原有成功终态
```

flag 创建或删除成功后同步 `.draft/`。普通 flag I/O 失败不改变 journal 结果；符号
链接、目录或其他异常节点是内部状态错误。

### 11. 业务成功、错误和 warning

业务 mutation 已验证后，索引构建或发布失败不回滚业务内容。只要
`link_index_rebuild_required` 已耐久记录，业务 operation 进入原有成功终态，退出码
仍为 `0`。

成功降级的 JSON 结果使用：

```json
{
  "warnings": [
    {
      "code": "link_index.rebuild_required",
      "severity": "warning"
    }
  ]
}
```

普通成功省略 `warnings`。同一 warning 在一个结果中只出现一次。warning 不包含
operation ID、内部路径或底层异常文本。

flag 普通 I/O 失败使用：

```json
{
  "code": "link_index.rebuild_flag_update_failed",
  "severity": "warning"
}
```

创建 flag 失败时，它可以与 `link_index.rebuild_required` 各出现一次；删除失败时，
索引仍可以通过 journal、revision、摘要和严格扫描验证，但快速判定路径不可用。

journal 结果无法耐久写入时返回 `operation.durability_failed`，退出码 `3`，保留锁和
现场。调用方修复存储条件后执行 `recover`。journal、索引制品或内部节点出现无法解释
的矛盾时返回 `operation.inconsistent_state`，保留证据，不创建新 revision 掩盖状态。

### 12. 恢复

`recover` 先处理入口时已经持有的 operation lock。入口无锁时，只读 discovery 必须完整
收集当前 operation 目录、journal、`.pending` 存在性和 R/T 关系；它不收敛 `.pending`，
也不清理材料。需要副作用的候选先尝试取得匹配 lock；竞争失败即停止，丢弃 discovery
快照，不得继续用旧事实清理。成功取得锁后，必须重读当前事实；读取、认证或写入失败时
保留 lock 与现场。

| discovery 当前事实 | 动作 |
| --- | --- |
| 空 operation 目录，无 journal 或 `.pending` | 取得匹配 lock，锁内再次确认为空后删除目录并同步父目录；不得复用该目录。 |
| 非 restore 的 `allocating` | 取得匹配 lock；锁内写 `rolled_back` 后，只清理当前 journal 授权的材料并释放锁。 |
| R/T 具有关联，或 terminal 有授权残留材料 | 只选择关系 owner 取得匹配 lock；锁内重读关系并调用相应恢复器。 |
| 无关联 `trash_retained` | 保留；`recover` 不推进生命周期。 |
| 有完整失败记录的无锁 `trash_handoff_pending` | 保留；仅生命周期入口在到期时重试。 |
| 中间写入 phase、未知 kind/phase、损坏或关系错配 | 失败关闭，保留现场。 |

R 到 T 的关联是 `R.metadata.restores_trash_operation == T.operation_id`，T 到 R 的关联是
`T.metadata.restore_operation_id == R.operation_id`。只有双方 kind 和 ID 匹配的双向关联
才是有效认领；唯一的预认领状态是 R 到 T 存在、T 到 R 尚未写入、且 R 仍为无 mutation 的
`allocating`。其他单向、错配、目标缺失或多重认领均为 `operation.inconsistent_state`。

`recover` 只在锁内收敛原 operation。链接索引阶段的规则如下：

| Phase | 恢复行为 |
| --- | --- |
| 索引阶段前 | 沿用该业务 operation 的前向完成或安全 rollback 规则 |
| `link_index_updating` | 进入 `link_index_rebuild_required`；不猜测已发布，不重复增量发布 |
| `link_index_published` | 验证目标 revision 和 SHA-256；一致则补终态，不一致则停止并保留现场 |
| `link_index_rebuild_required` | 尽力补建 flag，然后补原有成功终态 |
| 已有成功终态但残留锁或 staging | 幂等完成已知清理 |

进入 `link_index_updating` 后不再允许业务 rollback。

maintenance operation 的恢复规则（无锁发现后同样先取得匹配 lock）：

- `allocating` 中断且没有索引副作用：写 `rolled_back` 并释放锁；
- `link_index_updating`：进入 `rebuild_required`；
- `link_index_published`：验证制品后完成；
- `link_index_rebuild_required`：补 flag 后完成；
- `completed`：幂等清理锁和已知 staging。

恢复仅在持有匹配 lock、锁内重读并由当前 journal 授权后清理当前 operation 的
`link-index.staged.json` 或其他恢复材料；不得使用通配符、旧扫描快照或无锁路径触碰其他
operation 或 `.draft/` 下未知文件。

### 13. notification

公共命令为：

```text
draft notify files-changed --root ROOT
```

协议如下：

- candidate 和 registered 根都允许；
- 命令是外部编辑完成后的后置通知；
- 命令不接收路径、变更类型、重命名对或标准输入清单；
- 未列出的变化不存在，因为协议根本不接受变化清单；
- 当前用例函数同步调用链接索引维护模块；
- 不建立通用事件记录、消费者注册或待消费队列；
- 当前根没有索引事实变化时不创建 operation、不写 journal、不产生 revision；
- 不支持 `--dry-run`；
- 命令在验证 Draft 根后直接进入 notification 用例，不推进回收生命周期，不自动执行
  recovery；
- 已有 operation 持锁时，索引消费者不处理，notification 仍视为送达；
- 已分类的消费者失败由 notification 用例吸收；
- 参数错误、根选择失败、无有效 Draft 根以及未预期程序缺陷不属于成功送达。

成功 notification 固定退出码 `0`，不返回 warning、revision、变化文件数或索引结果。
普通输出为“已通知 Draft 根发生外部文件变化”。JSON 结果为：

```json
{
  "result_type": "files_changed_notification"
}
```

程序的其他输出不得建议调用方执行 notification。是否调用只由调用方对自己外部编辑
行为的认识决定。

### 14. 机会式耐久维护

允许触发维护的入口：

| 入口 | 行为 |
| --- | --- |
| notification | 对 absent、stale 或 `rebuild_required` 尝试维护 |
| 普通结构写命令 | 在业务 operation 内强制收敛索引结果 |
| `check` | 利用完整检查事实尝试维护 |
| `query backlinks` | 利用完整链接事实尝试维护 |
| `query page` | 为 backlink count 尝试维护 |
| `adopt` 的完整预检查 | 先结束独立 maintenance operation，再开始 adopt |
| `query links` | 不维护全根索引 |
| relations、status、recoverable 查询 | 不维护链接索引 |
| dry-run | 不创建 journal、索引或 flag |
| `init` | 不创建首次索引 |
| `recover` | 只恢复已有 operation |

`check` 和相关 query 对页面事实保持语义透明，但它们可以写 operation journal、索引和
flag，因此不是文件系统意义上的只读命令。

从未建立索引不是 warning。stale 是维护触发条件，不是公开 warning。允许写入的入口在
维护成功后正常返回；dry-run 使用完整扫描结果但不持久化，也不报告内部缓存状态。

机会式维护失败的分类：

| 失败点 | 当前命令 |
| --- | --- |
| 无法取得完整链接事实 | 按当前命令自身读取、解析或检查协议报告 |
| 写锁由另一 operation 持有 | 不发布；使用完整扫描结果；既有 recovery 状态仍可见 |
| 取得锁后发现 revision 已推进 | 放弃旧候选，不记录降级，使用完整扫描结果 |
| 发布失败但耐久记录 `rebuild_required` | 使用完整扫描结果并返回降级 warning |
| flag 普通 I/O 失败 | 使用完整扫描结果并返回 flag warning |
| journal 耐久失败 | `check` 保留已计算事实并报告 operation error；阻止 adopt；普通 query 非零退出 |
| 发布成功 | 使用当前事实，无索引 warning |

### 15. 并发维护

严格扫描开始时记录进程内观察到的最新 revision 状态。准备耐久发布时取得 Draft 写锁，
重新读取最新合法 journal 结果；只有 revision 或 absent 状态仍与扫描开始时相同，才
分配下一 revision 并发布。

revision 已推进表示另一个受协调 operation 已经发布更新结果。本次旧候选被放弃，不
覆盖新结果，也不记录 `rebuild_required`。这个并发令牌只存在于进程内，不增加 journal
字段。

扫描期间观察到根再次变化时，程序不发布混合时点索引，不无限重试。若能够耐久记录，
维护进入 `rebuild_required`；当前读取命令继续按其完整事实协议处理。

### 16. `check` 和 `adopt`

`check` 对索引状态的分类：

| 状态 | `check` 结果 | 是否阻止 `adopt` |
| --- | --- | --- |
| 从未建立 | 完整检查，无索引 issue；可以机会式首次发布 | 否 |
| fresh | 复用等价索引事实 | 否 |
| stale 且维护成功 | 完整检查，无索引 issue | 否 |
| `rebuild_required` 且维护成功 | 完整检查，无索引 issue | 否 |
| 维护后仍为 `rebuild_required` | 完整检查并报告降级 warning | 否 |
| flag 普通 I/O 降级 | 完整检查并报告 flag warning | 否 |
| journal 与 `published` 制品矛盾 | 内部一致性 error | 是 |
| journal 损坏或 recovery pending | operation error | 是 |

索引性能降级不是页面合法性事实，不能阻止 candidate 登记。内部状态无法解释时必须
阻止 `adopt`。

### 17. `init`

`init` 面对空目标，不发布首次索引，也不需要失效旧索引。第一次需要索引的业务写命令、
notification 或允许机会式维护的读取入口可以发布 revision `"1"`。

## 测试决策

### 1. 测试接缝

测试优先使用两个已有的最高层接缝：

1. 纯状态和数据协议接缝：验证 phase 转换、kind 所有权、字段矩阵、revision 解析、
   schema 解析和反向表完整性。单元测试只调用具有独立语义契约的生产函数，并使用受
   真实接口约束的 stub、fake、mock 或 spy 隔离持久化协作者。
2. CLI 与真实文件系统接缝：验证业务 mutation、索引制品、journal、flag、锁、退出码、
   JSON 结果和 recovery 的端到端组合。真实文件系统、进程中断、文件同步、原子替换和
   多进程竞争都属于集成测试。

如需增加故障注入接缝，应位于“耐久写入/替换/同步”这一稳定协作者边界，允许测试选择
具体耐久步骤失败；不得为每个内部辅助函数建立测试专用入口。

### 2. 单元测试

单元测试从行为分类独立推导，至少覆盖：

- revision 合法与非法字符串、任意精度递增和数值排序；
- journal 当前版本的 phase/字段决策表；
- 每个 kind 的合法 phase 集和允许后继边；
- rollback 路径禁止链接索引字段；
- 实际 mutation 到“需要索引阶段”的判定；
- 索引 schema、确定序列化和制品摘要；
- `files` 与 `backlinks` 完整双向校验；
- absent、published、rebuild-required 和内部不一致的基线决策表；
- 路径与 SHA-256 差异计算；
- 不变文件复用解析结果，新增/变化文件重新解析，删除文件移除反向边；
- 相同 SHA-256 的新路径重新解析资源身份；
- 非 Markdown 内容变化和目录成员变化的区分；
- notification 参数形态和稳定结果信封；
- warning 去重、顺序、机器代码和退出码；
- 机会式维护入口路由；
- revision 并发令牌变化时放弃候选。

机器协议字段、错误码、warning code、phase 和退出码精确断言。普通人类文案只验证事实
呈现、结构和特殊字符处理，不在多个测试层逐字复制。

### 3. 集成测试

真实文件系统集成测试至少覆盖：

- 10 类已知业务 mutation 的正常索引收敛；
- 每类 mutation 的 no-op 不产生 revision；
- `trash`、`restore` 和 `recover` 的原有终态；
- notification 的首次构建、增量更新、无变化和已有锁；
- notification 对每个已分类消费者失败固定返回 `0` 且没有 warning；
- `check`、`query backlinks`、`query page` 和 adopt 预检查的机会式维护；
- `query links`、无关 query、dry-run、init 不写索引；
- fresh 使用前完整读取覆盖 Markdown 并比较 SHA-256；
- 单文件修改、新增、删除、重命名和目录节点类型变化；
- 相同大小、inode 复用和时间戳量化不能让旧 backlink 进入 fresh 路径；
- Markdown 不可读、非 UTF-8 和链接提取失败；
- 索引目标和 flag 的符号链接、目录及其他异常节点；
- journal 当前版本拒绝旧版本和非法字段组合；
- 重复 revision 和冲突结果；
- staging 写入、文件同步、原子替换、目标目录同步分别失败；
- `link_index_updating` 前、目标替换后、`link_index_published` 后分别中断；
- flag 创建、删除和目录同步分别失败；
- journal 写入和 journal 目录同步持续失败；
- 扫描期间 revision 被另一个受协调 operation 推进；
- 扫描期间观察到外部文件再次变化；
- 每个恢复状态执行一次和重复执行；
- recovery 后业务文件、journal、索引、flag、锁和 staging 一致。

故障测试必须同时断言业务文件摘要、journal phase 和字段、revision、索引文件摘要、
flag、锁、staging、退出码及下一次命令是否接受索引。只断言异常类型或最终文件存在
不足以证明协议。

### 4. 性能基准

基准使用 100、1,000、5,000 个 Markdown 和宽目录，至少覆盖：

- 无索引的首次构建；
- fresh 索引上的 `query backlinks`；
- fresh 索引上的 `check`；
- 无变化 notification；
- 修改一个 Markdown；
- 修改 1% Markdown；
- 新增、删除和重命名 Markdown；
- 只改变目录成员或节点类型；
- `rebuild_required` 后成功恢复；
- `check` 机会式维护；
- journal 数量增长时发现最新 revision。

每个场景记录正文读取文件数和字节数、Markdown 解析文件数、wall time、峰值索引内存
或可复现近似值、索引文件大小，以及 journal 写入次数和字节数。

计数不变量必须证明 fresh 路径仍读取全部覆盖 Markdown，但不重复解析摘要未变化的
文件；变化场景的解析数随变化文件数增长。当前不设置跨机器绝对耗时 SLA。

### 5. 完整门禁

实现完成前分别运行 runtime 单元测试、runtime 集成测试、适配层单元测试、适配层
集成测试、格式检查、严格类型检查和 adapter 一致性检查。网络测试未运行时必须明确
记录，不得把排除项描述为通过。

## 范围外

- 不建立 watcher、文件系统事件队列或后台 daemon。
- 不建立通用 notification 消费者注册、投递确认或重试框架。
- notification 不接收路径清单或重命名映射。
- 不建立 journal 最新 revision 的派生摘要或 sidecar；只有基准证明 `O(J)` 是实际
  瓶颈后才另行设计。
- 不提供链接索引状态专用 query。
- 不让 dry-run 持久化索引。
- 不兼容、迁移或双读其他 journal 和索引 schema 版本。
- 不为其他链接改写命令扩展未受管理 Markdown 入站闭包；本规格只统一维护这些命令
  已经产生的链接索引事实。
- 不增加为提升后页面生成初始机器块的命令。
- 不改变 CommonMark 链接提取对当前不可判定源文本的处理。
- 不覆盖网络文件系统、同步盘特殊语义、非协作写者竞争、恶意修改内部状态，或机器
  掉电后的完整祖先目录链耐久。
- 不因为派生索引故障回滚已经验证的业务 mutation。

## 补充说明

- `CONTEXT.md` 中的“链接索引”“链接索引修订号”“索引降级”“链接索引更新阶段”
  “索引重建标志”“外部文件变更通知”和“操作恢复”是本规格使用的规范领域术语。
- 规格固定外部行为、状态转换、不变量和失败语义。生产模块划分、内部函数签名和任务
  拆分由实施计划提出建议，实施者可以在保持本规格的前提下根据实际数据流调整。
- 实施中发现代码事实无法满足状态表、需要改变机器协议或出现本规格未定义状态时，
  必须停止相关任务并提交证据，不得自行选择语义。
