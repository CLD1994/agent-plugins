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
| `page` | 受管理页面及其可认证页面事实 | 返回被请求页面的可认证事实；不因未受管理 Markdown 自动创建页面。 |
| `links` | candidate 根内可判定 Markdown，区分 managed 与 unmanaged source kind；registered 对 unmanaged 的包含资格未决 | 返回可认证出站链接事实及 source kind；candidate 中不得把未受管理 source 静默丢弃。 |
| `backlinks` | candidate 根内可判定 Markdown，区分 managed 与 unmanaged source kind；registered 对 unmanaged 的包含资格未决 | 返回可认证入站链接事实及 source kind；不得用只含 managed 的 lower-bound 替代完整结果。 |
| `relations` | 受管理页面的正式关系 | 返回关系事实，不把普通链接提升为关系。 |
| `status` | 受管理页面状态 | 返回可认证状态事实。 |
| `recoverable` | Trash authority 与同一次 `as_of` | 以同一观察时刻完成恢复资格筛选和投影。 |
| `check` | 根、页面、导航、关系、资源与适用 runtime authority | 返回完整可诊断检查事实；`adopt` 需要无过滤、无分页且无 error 的完整 Check。 |

candidate 根中 managed/unmanaged 的宽来源域是明确要求，不能将 Links/Backlinks 缩窄为
仅受管理页面。registered 根是否同样包含每一类 unmanaged source 的逐项资格尚未由唯一
owner/签署决定闭合，故为 `unresolved`。在该未决分区中不能擅自扩张权限，也不能返回
受管理 lower-bound 作为完整结果。

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

七类读共用 token、projection、分页、warnings、blockers 与 partial 的语义 envelope。精确
`ReadOutcome` ABI，包括字段存在性、顺序、projection 缺失、index maintenance busy 以及
成功 read 是否携带 lifecycle result，尚未获统一批准，逐项为 `unresolved`；本页不从
实现、测试或帮助文本补造。

### 副作用、失败、恢复与验证

读操作不改变页面、机器区、关系、导航、Trash active membership 或结论快照。runtime 可能
在自己的认证边界维护投影；该维护的阶段、warning 和领域读成功的先后不由本合同推断。
根扫描、节点类型、UTF-8、marker、Trash authority、snapshot 或 `as_of` 不可认证时保留
可诊断失败。只有显式 `recover()` 能收敛 recovery pending，读操作和重试不清理现场。

验证七类读的 source domain、candidate 的 managed/unmanaged 区分、registered 未决门槛、
items/blockers 独立排序分页、同一 `as_of`、完整/partial/不可认证失败，以及 authority
变化导致 snapshot 失效。

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
| 所需事实均可认证 | 普通、JSON 或 notification | 从同一机器诊断结果投影对应呈现。 |
| 任一必需事实不可认证 | 任意 | 产生可诊断失败；不得输出“无问题”、空诊断或伪成功。 |
| 同一事实同时请求多种呈现 | 多种 | 机器结论必须一致；文案可不同，但不得改变类别、数量或关联对象。 |

精确机器字段、字段顺序、错误码/退出码、notification 的 delivered/error 矩阵、warning
投影及其与 runtime 生命周期结果的组合，目前没有统一已批准 ABI，均为 `unresolved`。
这些缺口由 [runtime public protocol](../runtime/errors-and-observability.md) 闭合；在其未
闭合前，本节不得以本地 JSON、CLI 或文案样例替代。

### 失败、恢复与验证

诊断投影不应吞掉不可认证事实，也不安排 notification 重试或 recovery。验证同一机器
事实在普通、JSON 和 notification 中保持一致，并验证编码、路径、root authority 或
runtime 认证失败始终成为可诊断失败而非成功的空输出。

## 来源

- `33aeaec60a34833ce8b686bfdd7e50b8dc9932d0:.scratch/draft-authority-spec-migration/migration-evidence.md#迁移单元`
