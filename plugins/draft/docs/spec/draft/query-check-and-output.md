# 查询、检查与输出

## 读操作与查询

`D-QUERY` 定义七类读操作的来源域、认证边界和领域结果。它不定义 `ReadOutcome` 的
精确字段、token 编码、CLI JSON 形状或 runtime 自动维护算法。

### 输入、输出与状态维度

- 输入是查询种类、根/marker/authority 事实、root state、受管理和未受管理 Markdown
  来源、分页请求、统一 `as_of` 观察时刻，以及 runtime 提供的 snapshot/projection 事实。
- 输出是经认证的 `items`、独立的 `blockers`、适用的分页/快照语义，或可诊断失败；
  未认证时不能用空集合或 lower-bound 伪装成功。
- 每次读的语义 snapshot 覆盖根、content、活动成员、head 和相关 projection generation；
  任一相关 authority 改变即使该 token 失效。token 的字段名、编码和传输位置为
  `unresolved`。

| 读操作 | 领域 source domain | 成功时的领域结果 |
| --- | --- | --- |
| `page` | 受管理页面及其可认证页面事实；`backlink_count` 另受下方 source-domain 分区约束 | 返回被请求页面的可认证事实；不因未受管理 Markdown 自动创建页面。计数域未决时不得以任一 source domain 伪装完整计数。 |
| `links` | source kind 与 root state 的返回域未决；必须区分 managed/unmanaged，不得由结构闭包推定 query 域 | 在 source domain 已获批准前不能选择完整结果或 lower-bound；可认证事实需保留 source kind。 |
| `backlinks` | source kind 与 root state 的返回域未决；必须区分 managed/unmanaged，不得由结构闭包推定 query 域 | 在 source domain 已获批准前不能选择完整结果或 lower-bound；可认证事实需保留 source kind。 |
| `relations` | 受管理页面的正式关系 | 返回关系事实，不把普通链接提升为关系。 |
| `status` | 受管理页面状态 | 返回可认证状态事实。 |
| `recoverable` | Trash authority 与同一次 `as_of` | 以同一观察时刻完成恢复资格筛选和投影。 |
| `check` | 根、页面、导航、关系、资源与适用 runtime authority | 返回完整可诊断检查事实；`adopt` 需要无过滤、无分页且无 error 的完整 Check。 |

candidate 根中 managed/unmanaged 的 Links/Backlinks source domain、registered 根对
unmanaged source 的资格，以及 `page` 的 `backlink_count` 统计域均未由唯一 owner/签署
决定闭合。Q31-B 只批准 candidate 的 unmanaged Markdown 可经 `move` 纳管，Q31-C 只
批准结构命令的全根链接事实闭包；二者都不选择 query 返回域。formal §13 的 managed-only
最小合同与当前候选宽域表述也未形成逐 section 的取代关系，因此下表每一格都必须保持
具体 `unresolved`，不能擅自选择宽域、窄域或 managed-only lower-bound。

### query source-domain 未决分区

下表覆盖 `root_state × source_kind × query` 的全部 source-domain 组合。`backlink_count`
随 `backlinks` 的同一 source domain 判定；它不是可以独立采用另一统计域的例外。

| root_state | source_kind | query | 状态与缺失决定 |
| --- | --- | --- | --- |
| `candidate` | `managed` | `links` | `unresolved`：managed source 是否构成 candidate Links 的完整返回域，及与 unmanaged source 的组合规则未获签署；不能返回已选定的窄/宽域。 |
| `candidate` | `unmanaged` | `links` | `unresolved`：Q31-B/C 的纳管/结构闭包范围未批准 query 返回该 source；正式最小合同与候选宽域冲突，阻塞 source-domain 认证。 |
| `candidate` | `managed` | `backlinks` | `unresolved`：managed source 是否构成 candidate Backlinks 的完整返回域，及其与 unmanaged source 的组合规则未获签署。 |
| `candidate` | `unmanaged` | `backlinks` | `unresolved`：结构链接闭包不等于 query 返回资格；formal §13 与候选宽域冲突，不能选择是否纳入该 source。 |
| `registered` | `managed` | `links` | `unresolved`：registered root 的 managed source 返回资格及其与历史 managed-only 合同的当前 scope 未闭合。 |
| `registered` | `unmanaged` | `links` | `unresolved`：registered 对 unmanaged source 的资格没有唯一批准，不能从 candidate 纳管事实推定。 |
| `registered` | `managed` | `backlinks` | `unresolved`：registered managed Backlinks 的完整返回域及与 page count 的一致规则未闭合。 |
| `registered` | `unmanaged` | `backlinks` | `unresolved`：registered unmanaged Backlinks 的包含资格未获签署，不能返回 managed-only lower-bound。 |
| `candidate` | `managed` | `page.backlink_count` | `unresolved`：candidate count 是否统计仅 managed source 或与 unmanaged source 合并未获决定；影响页面可观察计数。 |
| `candidate` | `unmanaged` | `page.backlink_count` | `unresolved`：candidate unmanaged source 是否进入 count 未获决定；不能把 Links/Backlinks 的任一候选域折叠为 count 结果。 |
| `registered` | `managed` | `page.backlink_count` | `unresolved`：registered managed count 的完整统计域及历史最小合同是否仍适用未闭合。 |
| `registered` | `unmanaged` | `page.backlink_count` | `unresolved`：registered unmanaged count 的资格未获签署；必须阻塞而不能返回窄域计数。 |

### 分页、partial 与认证决策表

`items` 与 `blockers` 是独立集合：各自排序、各自计算 total 和 continuation，不能把
blocker 混入 item 排序或用一个集合的 continuation 继续另一个集合。具体 comparator、
continuation 语法、total 字段名和 UTC 时刻的编码均为 `unresolved`，但实现必须保持同一
请求内的一致 `as_of`。

| 根 / authority 与来源认证 | 分页与 source 结果 | 返回语义 |
| --- | --- | --- |
| 根、marker、活动成员、head 或请求所需 authority 不可认证 | 任意 | 可诊断失败；不返回 partial 成功、空集合或 lower-bound。 |
| 根 authority 可认证，所有请求 source 均可认证 | items 完整 | 返回完整结果，按各集合独立排序/分页。 |
| 根 authority 可认证，部分非权威 source 不能认证，其余 source 可认证 | 可认证 items 与不可认证原因可分开定位 | 返回 `partial`：items 只含已认证事实，blockers 说明未认证部分；二者独立分页。 |
| 请求的 source 域、root state 或 managed/unmanaged 包含规则本身 `unresolved` | 任意 | 返回可诊断阻塞，不能选择一侧来源域。 |
| recovery pending 或 runtime 指出读前状态不安全 | 任意 | 不自行 recovery；按 runtime public protocol 报告阻塞。 |

七类读共用 token、projection、分页、warnings、blockers 与 partial 的语义 envelope。runtime
owner 已固定以下跨页事实：成功 read 返回 projection 和本次 actionable warnings，不带
routine lifecycle result；fatal 或最终 scan 不稳定时返回 error 且不带 projection；自动
阶段 busy 只在稳定 read 仍成功时成为本次 warning。其余真正未签字段逐项保持
`unresolved`，包括 snapshot token 的字段名/编码、projection/partial/blockers/warnings
字段的存在性与顺序、分页 comparator/continuation/total 的字段形状和 error envelope
排序；本页不从实现、测试或帮助文本补造。详见 [公共结果与错误投影](../runtime/errors-and-observability.md#公共结果与错误投影)。

### 副作用、失败、恢复与验证

读操作不改变页面、机器区、关系、导航、Trash active membership 或结论快照。runtime 可能
在自己的认证边界维护投影；该维护的阶段、warning 和领域读成功的先后不由本合同推断。
根扫描、节点类型、UTF-8、marker、Trash authority、snapshot 或 `as_of` 不可认证时保留
可诊断失败。只有显式 `recover()` 能收敛 recovery pending，读操作和重试不清理现场。

验证七类读的 source domain、上表全部 candidate/registered × managed/unmanaged ×
links/backlinks/count 分区、items/blockers 独立排序分页、同一 `as_of`、完整/partial/
不可认证失败，以及 authority 变化导致 snapshot 失效；在 source domain 未决时验证每个
分区均阻塞而非选取一侧。

## 诊断

`P-DIAGNOSTICS` 只定义诊断事实的所有权和投影边界。它不重复定义 runtime `Outcome`、
`ReadOutcome` 或 `RecoveryOutcome` 的 ABI。

### 输入、输出与不变量

- 输入是检测层从根、页面、导航、关系、资源、Trash 或 runtime authority 认证得到的事实，
  以及请求的普通输出、JSON 或 notification 投影目标。
- 检测层拥有机器事实。普通输出、JSON 和 notification 必须从同一组机器事实投影，不能
  各自重新扫描、翻译出不同结论或用人类文案传递机器语义。
- 机器字段与人类文案分层：字段/错误类别用于程序判定，文案只解释已认证事实，不能成为
  额外协议或反向修正机器结果。

| 检测事实 | 投影请求 | 允许结果 |
| --- | --- | --- |
| 所需事实均可认证 | 普通、JSON 或 notification | 从同一机器诊断结果投影对应呈现；成功 read 的 projection/presence 与 runtime owner 一致。 |
| 任一必需事实不可认证 | 任意 | 产生可诊断失败；不得输出“无问题”、空诊断或伪成功。fatal read 不带 projection。 |
| 同一事实同时请求多种呈现 | 多种 | 机器结论必须一致；文案可不同，但不得改变类别、数量或关联对象。 |

错误码、退出码及 notification 的 `delivered` 存在性由 [公共结果与错误投影](../runtime/errors-and-observability.md#公共结果与错误投影) 的 owner 决定：允许的 delivered 行才含该字段，error 行排除该字段；D-PDIAGNOSTICS 只消费这些事实，不重新选择。仍未签的部分仅是 diagnostics/JSON/notification 的精确字段名、字段顺序、可选性、编码和 warning/lifecycle envelope 排列，逐项为 `unresolved`；不得以本地样例替代。

| 字段级项目 | 状态 | 缺失决定与影响 |
| --- | --- | --- |
| diagnostics 普通/JSON 的事实字段名、顺序、可选性和编码 | `unresolved` | 没有统一公共 ABI；不得由文案、实现或测试夹具补造。 |
| notification error/delivered envelope 的字段顺序与可选性 | `unresolved` | runtime 已固定 `delivered` 的存在性和 code/exit 语义，但未签完整 envelope；不得重新定义错误行。 |
| warning 与 lifecycle result 的 envelope 字段和排列 | `unresolved` | runtime 已固定何时可观察 warning/result；缺少字段级 schema，不能把高层 presence 再开放为未决。 |

上表不把 runtime 已固定的成功 read、fatal read、错误 code/exit 或 `delivered` 条件列为
未决；它们是本页必须消费的 authority。source-domain 未决只属于上面的 query 组合表。

### 失败、恢复与验证

诊断投影不应吞掉不可认证事实，也不安排 notification 重试或 recovery。验证同一机器
事实在普通、JSON 和 notification 中保持一致，并验证编码、路径、root authority 或
runtime 认证失败始终成为可诊断失败而非成功的空输出；同时逐格核对 runtime owner 的
success/fatal read projection、error code/exit 和 `delivered` 存在性。

## 来源

- `33aeaec60a34833ce8b686bfdd7e50b8dc9932d0:.scratch/draft-authority-spec-migration/migration-evidence.md#迁移单元`
