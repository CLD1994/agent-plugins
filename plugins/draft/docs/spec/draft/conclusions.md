# 结论快照

## 结论快照行为合同

`D-CONCLUSIONS` 定义 `conclusions.md` 的语义成员资格、显式生成与结构 mapping 的窄
维护边界。它不把快照变成页面状态或正式关系的权威来源。

### 输入、输出与不变量

- 输入分为调用方触发和 root-bound Runtime 取得的事实。结论生成状态、受管理页面的
  `confirmed` 状态、可认证的 `challenged_by`
  关系、现有 `conclusions.md` 制品、根导航、事件类型，以及结构操作已经认证的
  before-to-after mapping 均由 Runtime 读取或推导。
- 输出是结论生成状态和允许暂时 stale 的语义快照、对已有链接目标的窄改写，或可诊断的
  零写失败。结论生成状态的耐久编码和存储位置是私有实现，不进入公开结果，也不是公共合同。
- `confirmed` 是唯一成员来源。带 `challenged_by` 的 confirmed 页面仍属于“已确认结论”，
  并且同时出现在“受质疑结论”投影；质疑不会自动取消成员资格。
- 文件固定保留 `## 已确认结论` 与 `## 受质疑结论` 两个 section，零条目时也保留二者。
  受质疑顶层顺序跟随确认集合顺序，每个顶层的质疑来源按规范路径排序。
- 生成制品只允许固定 `parent`，不能参与可变正式关系；链接观察不产生关系，也不阻止
  Trash。

### 固定文件形状

显式生成使用以下唯一文件形状。示例包含一个已确认且受一项质疑的页面：

```markdown
---
status: proposed
parent: index.md
---
# 结论汇总

## 已确认结论

- [结论标题](pages/a.md)

## 受质疑结论

- [结论标题](pages/a.md)
  - [质疑来源标题](pages/challenge.md)
```

- YAML frontmatter 固定为 `status: proposed`、`parent: index.md`，省略 `note` 和
  `relations`。每次显式生成都恢复这一头部；先前经 `status set` 设为其他状态的制品也会
  重置为 `proposed`。
- 一级标题固定为 `# 结论汇总`。其后只允许按顺序出现 `## 已确认结论` 和
  `## 受质疑结论`，不得出现其他标题、段落、占位文字或列表。
- “已确认结论”中，每个当前 `confirmed` 页面恰有一个顶层列表项。列表项只含一个非图片、
  无 tooltip、无 fragment 的 Markdown 链接；标签解码后等于页面一级标题，目标解析后等于
  该页面当前路径。`conclusions.md` 自身不进入列表。
- “受质疑结论”中，每个同时具有一项或多项有效 `challenged_by` 的 confirmed 页面恰有一个
  同形顶层列表项；每个质疑来源恰有一个以两个空格缩进的同形子列表项。顶层页面仍完整
  保留在“已确认结论”中，子项链接指向提出质疑的页面。
- 两个 section 零条目时都只保留标题和空行，不写占位项目。已确认列表按父子导航的
  深度优先顺序；受质疑顶层沿用该顺序，每个顶层下的质疑来源按规范根相对路径排序。
- 解析器必须从 CommonMark 结构取得每个顶层和缩进列表项的唯一链接目标 source span，并
  映射到含 frontmatter 的原始文件位置。结构命令只能改写这些已认证目标 span；无法唯一
  识别 section、层级、链接或 span 时必须零写。

根导航中的保留项由[导航语法与顺序](pages-and-navigation.md#导航语法与顺序)定义；它与
`conclusions.md`、结论生成状态在同一个生成提交中出现或修复。

### 调用方请求与公共结果边界

显式生成的 request 联合、公共字段和值域、CLI 映射、mode 及其校验由[运行时公共接口的普通操作 request](../runtime/interface.md#普通操作-request)统一定义。
`generate_conclusions` 仅作为本页领域行为标签；调用方不提供成员列表、生成状态、关系闭包、
根导航、制品内容或 mapping，这些事实由 Runtime 认证。本页不重复公共 request ABI。

成功、预演、无变化、操作身份、错误和输出通道消费[公共结果、错误与可观测性](../runtime/errors-and-observability.md#公共结果错误与可观测性)，
本页不重复定义字段。成员/关系/制品规则触发的机器码在下节登记；生成状态本身不可认证时，
本页只规定拒绝和零写，并将公开错误分类交给 runtime/公共结果 owner，不能把未知状态解释为
未生成、已生成、内容损坏或暂时无变化。

### 显式生成决策表

下表从上到下判定；第一条命中的行决定整个请求结果。

| 结论生成状态 | 文件与根导航 | 已确认页面 | 关系与其他事实 | 结果与副作用 |
| --- | --- | --- | --- | --- |
| 任意 | 任意 | 任意 | 任一可变正式关系以 `conclusions.md` 为 source 或 target | 拒绝，零写；生成器不删除关系，调用方先显式清理。 |
| 不可认证 | 任意 | 任意 | 任意 | 拒绝，零写；不得把未知状态当作未生成或已生成。 |
| 任意 | 任意 | 任意 | 成员状态、关系配对、端点、目标 confirmed 条件或重复事实不可认证 | 拒绝，零写；不得输出部分快照。 |
| 未生成 | `conclusions.md` 或保留根导航项任一已经存在 | 任意 | 其余事实可认证 | 拒绝冲突，零写；没有生成器所有权时不得覆盖、接管或删除同名内容。 |
| 未生成 | 文件与保留根导航项都不存在 | 零个 | 其余事实可认证 | 幂等空操作；保持未生成，不创建文件或根导航项。 |
| 未生成 | 文件与保留根导航项都不存在 | 一个或多个 | 其余事实可认证 | 按固定形状创建文件和根导航项，并在同一提交中变为已生成。 |
| 已生成 | 文件和根导航项存在、缺失或损坏 | 任意数量 | 其余事实可认证 | 按固定形状整体重建文件并确保唯一规范根导航项；保持已生成。零成员时保留两个空 section。 |

已生成状态授权显式生成覆盖其拥有的损坏制品或补回缺失制品；未生成状态不授权处理同名
碰撞。对同一已认证输入连续生成时，如果文件、根导航和生成状态已经等于规范 after-state，
第二次是零写成功。

### 其他事件与提交

| 事件 | 所需认证事实 | 允许的结果与副作用 |
| --- | --- | --- |
| `move`、`promote`、`collapse` | 现有制品可安全定位既有顶层/缩进链接，且 old-to-new mapping 可证明 | 在同一结构提交中只改写已有条目链接目标；不增删、不重排、不改标签、质疑来源或语义内容。 |
| `move`、`promote`、`collapse` | mapping 不可证明或生成制品不能安全解析 | 整个结构写在任何写前失败；不得按文本相似度猜链接。 |
| `create`、`status set`、`relation add/remove`、标题或正文变化 | 任意 | 不修改快照；快照可因此 stale。 |
| Trash / Restore | 任意 | 不阻止相应操作，且不删除、重建或改写快照。 |
| 显式生成的关系事实不完整、重复、端点缺失或状态不满足 | 不成立 | 拒绝生成，零写；遗留关系只能由精确 `relation.remove` 清理。 |

结构命令的窄链接改写消费其已认证 mapping，不另建 operation 或独立提交。显式生成是完整
语义重建，只采用本页固定文件形状。

在 `registered` 根中，显式生成和结构窄改写都必须对完整计划 after-state 通过
[Check 门禁与副作用](query-check-and-output.md#check-门禁与副作用)；生成器可以整体替换其拥有但
已损坏的结论制品，只要替换后的完整根零 error。`candidate` 只需成员、关系、制品 parser
和本次 mapping 的完整事实，不因无关 error 被阻止。

### 页面头部键与输出边界

页面 YAML frontmatter 不得出现名为 `产出` 的顶层键。无论该键的 YAML 值为空、标量、
列表、映射对象或看起来像 Markdown 链接，解析器都必须拒绝整个页面并返回
`page.frontmatter_invalid`。该键及其值不产生正式关系、正文资源、结论成员资格或迁移
事实。frontmatter 之外的正文可以正常使用“产出”一词，不触发这条规则。

普通解析和迁移使用同一规则，不自动删除或改写该键。`page.frontmatter_invalid` 是公共
Check/修改错误登记中的既有机器码；其 message、line、details 和 severity 的公共投影由[诊断 code 登记](query-check-and-output.md#诊断-code-登记)
定义，错误 envelope 和其他公共结果由[公共结果、错误与可观测性](../runtime/errors-and-observability.md#公共结果错误与可观测性)
定义，本页不另造结论专用错误或字段 schema。

### 生成领域错误投影

结论生成只消费[公共结果、错误与可观测性](../runtime/errors-and-observability.md#公共结果错误与可观测性)
与[诊断 code 登记](query-check-and-output.md#诊断-code-登记)已登记的错误；本页只保留生成
事实、机器码选择和零写后果，不复制 code、reason 或 severity 值域：

| 已认证事实 | 领域选择与副作用 |
| --- | --- |
| 尚未生成但 `conclusions.md` 已存在 | 使用 [`conclusions.conflict`](query-check-and-output.md#诊断-code-登记)；写前零写。 |
| 尚未生成但根导航保留项已存在 | 使用 [`conclusions.conflict`](query-check-and-output.md#诊断-code-登记)；写前零写。 |
| 已生成制品 frontmatter 不符合固定头部 | 使用 [`conclusions.invalid`](query-check-and-output.md#诊断-code-登记)；写前零写。 |
| 已生成制品标题、section 或列表结构不符合固定形状 | 使用 [`conclusions.invalid`](query-check-and-output.md#诊断-code-登记)；写前零写。 |
| 已生成制品缺少应有确认/质疑成员 | 使用 [`conclusions.invalid`](query-check-and-output.md#诊断-code-登记)；写前零写。 |
| 已生成制品含没有对应已认证成员的额外条目 | 使用 [`conclusions.invalid`](query-check-and-output.md#诊断-code-登记)；写前零写。 |
| 已生成制品含重复确认成员或质疑来源 | 使用 [`conclusions.invalid`](query-check-and-output.md#诊断-code-登记)；写前零写。 |
| 已生成制品链接含错误目标、fragment、tooltip 或其他非固定形状 | 使用 [`conclusions.invalid`](query-check-and-output.md#诊断-code-登记)；写前零写。 |
| 已生成制品成员顺序不符合固定领域顺序 | 使用 [`conclusions.invalid`](query-check-and-output.md#诊断-code-登记)；写前零写。 |
| 已生成状态下结论制品缺失 | 使用 [`conclusions.invalid`](query-check-and-output.md#诊断-code-登记)；写前零写。 |
| 已生成状态下根导航结论保留项缺失 | 使用 [`conclusions.invalid`](query-check-and-output.md#诊断-code-登记)；写前零写。 |
| 已生成制品的条目目标 span 无法唯一取得或映射 | 使用 [`conclusions.invalid`](query-check-and-output.md#诊断-code-登记)；写前零写。 |
| 任一可变正式关系以 `conclusions.md` 为 source 或 target | 使用 [`relation.endpoint_invalid`](query-check-and-output.md#诊断-code-登记)；生成器不代为删除关系。 |
| 页面头部事实不合法 | 复用 [`page.frontmatter_invalid`](query-check-and-output.md#诊断-code-登记)；不另造生成专用同义码，写前零写。 |
| 页面状态事实不合法 | 复用 [`page.status_invalid`](query-check-and-output.md#诊断-code-登记)；不另造生成专用同义码，写前零写。 |
| 关系端点事实不合法 | 复用 [`relation.endpoint_invalid`](query-check-and-output.md#诊断-code-登记)；不另造生成专用同义码，写前零写。 |
| 关系配对事实不合法 | 复用 [`relation.pair_invalid`](query-check-and-output.md#诊断-code-登记)；不另造生成专用同义码，写前零写。 |
| 生成所需事实、根导航或 mapping 无法完整稳定取得 | 使用 [`runtime.closure_incomplete`](../runtime/errors-and-observability.md#公共结果错误与可观测性)；拒绝并零写，不把未知生成状态或快照 stale 解释为未生成、已生成或 no-change。 |
| 生成状态的私有权威本身损坏 | 使用 [`runtime.corrupt`](../runtime/errors-and-observability.md#公共结果错误与可观测性)；本页拒绝并零写，不公开其存储编码。 |

正式根还必须满足完整 after-state Check；既有 error 使用 `operation.check_failed`，而 Check
  无法完整运行使用 `runtime.closure_incomplete`。一次失败只返回一个独立错误，建议仅在已
  认证事实足以支持时提供。

### 失败、零写、恢复与验证

- 生成前任一关系、端点、状态、生成状态或重复事实不可认证时零写。快照 stale 本身不是
  结构阻塞，但对结构命令而言不可安全解析的已生成制品是阻塞。
- 领域提交后耐久结果不确定时由 runtime 保留 recovery 现场；普通结构或生成重试不能
  自行覆盖制品。显式 recovery 收敛后必须重新认证成员事实和 mapping。
- 验证显式生成表的全部组合：未知生成状态、未生成碰撞、首次零成员、首次有成员、已生成
  的存在/缺失/损坏制品、零成员重建、非法关系阻断和幂等零写。
- 验证固定头部、一级标题、两个 section、顶层/缩进列表原文形状、标题标签、链接目标、
  confirmed-only 成员资格、`confirmed + challenged_by` 双投影、零/一/多个质疑和全部排序；
  任何多余文本、tooltip、fragment、层级或 span 歧义都阻止结构窄改写。
- 验证 stale 事件、Trash/Restore 非修改，以及 mapping 只改已有顶层/缩进链接目标。
- 分别验证 YAML frontmatter 中 `产出:` 的空值、标量、列表、映射对象和链接形文字值均拒绝
  整页；验证合法字符串值或正文中的同词不触发这条规则，且被拒绝值中的链接形文字不会
  进入资源或反向链接事实。中文冒号 `：` 只按字符串内容处理，不按 YAML 键分隔符处理；
  公共诊断投影按[诊断 code 登记](query-check-and-output.md#诊断-code-登记)核对。

## 来源

- `33aeaec60a34833ce8b686bfdd7e50b8dc9932d0:.scratch/draft-authority-spec-migration/signed-decisions-transcript.md#已签署决定迁移摘要`
