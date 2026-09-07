# 页面与导航

## 页面角色与导航

`D-PAGES` 定义受管理页面的路径角色、物理父关系和可见导航。它消费结构操作已经
认证的 before-to-after mapping，不在此页重新定义 mapping 或 runtime 提交算法。公开结果
不提供 source/destination 路径列表；本页只定义页面角色、parent 和导航的一致性。

### 输入与输出

- 输入分为已经通过 [R-INTERFACE 普通操作 request](../runtime/interface.md#普通操作-request)
  公共校验的调用方字段，以及 root-bound Runtime 取得的事实。页面角色与导航只消费根状态、
  当前页面树、物理 parent、导航、managedness、目录成员分类和完整链接闭包；这些事实均由
  Runtime 读取或推导。`move` 的页面纳管还必须先通过[来源决策矩阵](structure.md#来源决策矩阵)
  的 source/managedness/target 分类、目录成员级分类和完整链接闭包门禁。
- 输出是路径角色、唯一物理父页、相应导航 after-state，或可诊断的拒绝；本页只提供这些
  页面领域事实。`create`、`status set`、`relation add/remove` 的实际操作 owner 见[页面编辑](page-edits.md#创建状态与关系编辑)，
  `move`、`promote`、`collapse` 的实际操作 owner 分别见[move](structure.md#move)、[promote](structure.md#promote)
  和[collapse](structure.md#collapse)，由它们选择领域 outcome；公共结果外壳、操作身份和
  错误投影由[公共结果、错误与可观测性](../runtime/errors-and-observability.md#公共结果错误与可观测性)
  统一定义，本页不另造字段或路径列表。
- 结构操作成功时，路径、父关系和导航必须从同一已证明 mapping 派生，不能分别按
  basename、文本相似度或目录枚举猜测。目标碰撞由该 mapping 在写前统一判定；本页不
  创建第二套 collision 或路径推导规则。

### 路径角色与不变量

| 路径形状 | 角色 | 允许性与不变量 |
| --- | --- | --- |
| `index.md` | 唯一根页 | 根没有 `parent` 页面；它是唯一根级 index，并始终保留唯一 `## 子主题` 小节。 |
| `pages/<...>.md`（不含保留 `pages/index.md`） | 叶页面 | 不拥有子页面命名空间，也不得出现保留的 `## 子主题` 小节。 |
| `pages/<topic>/index.md` | 枢纽页面 | 拥有该目录的直接子页面命名空间，并恰有一个 `## 子主题` 小节。空的非根 hub 由 collapse 收敛。 |
| `pages/index.md` | 保留且非法 | create、页面纳管、move 的目标 mapping 均不得产生它；既存节点由 Check 报告路径角色错误。 |
| 根内其他 Markdown | 未受管理 Markdown 或非页面制品 | 不是受管理页面；是否可在 candidate 中经 `move` 纳管由 [结构操作](structure.md#move) 定义。 |

每个非根受管理页面恰有一个物理父页；其 `parent` 事实、物理路径和父页中可见的直接
子页面导航必须一致。直接子页面在父导航中恰出现一次，不得重复、错指或以叶/枢纽两种
路径同时表示。根没有子页面时，仍恰好保留一个空的 `## 子主题`；这不是可省略的格式
分支。

### 页面头部与正文边界

每个受管理页面都从文件第一行开始使用 YAML frontmatter。第一行和结束行都必须只包含
`---`；两行之间是页面头部信息，结束行之后才是 Markdown 正文。YAML 键和值之间使用
ASCII 冒号 `:`；中文冒号 `：` 只是字符串内容，不能充当 YAML 键的分隔符。Draft 只把
这种头部结构解释为页面头部信息；具体键、值类型和基数见
[YAML 页面头部结构](status-and-relations.md#yaml-页面头部结构)。

页面头部信息先于 CommonMark 正文解析，并且不属于正文。即使 YAML 字符串看起来像
Markdown 链接、标题或导航项，也不得进入正文链接、反向链接、父子导航或结论内容。
诊断中的 source span 和行号始终指向包含 frontmatter 的原始文件；实现若单独解析正文，
必须把正文位置映射回原始字节与行号。runtime 的耐久记录是另一类私有数据，不能借用
页面 frontmatter 成为公开页面协议。

### 标题与链接文字

页面一级标题、父子导航链接标签和结论列表链接标签使用同一可见文字规则。输入是正文中
相应位置的 CommonMark 内联结构，输出是用于标题显示、生成和相等比较的文字，或该位置
无法认证的诊断。比较文字，不比较原文转义形式或强调样式；不得通过 HTML 渲染、删除标签
或裁剪文字，把不合规输入变成合规标题。

先按 CommonMark 识别语法，再按下表处理各内联部分；任一部分是原始 HTML，就拒绝整个
标题或标签，不返回删除该部分后的文字。没有原始 HTML 时，各部分按原顺序组成可见文字，
并继续满足该位置既有的单行文字、链接数量和结构规则。

| 内联内容 | 可见文字与处理 |
| --- | --- |
| 普通文字、Markdown 反斜杠转义、字符实体 | 使用 CommonMark 解码后的文字；普通标点不必强制写成生成器采用的转义形式。 |
| 强调、加粗及其嵌套组合 | 按原顺序读取内部文字，格式标记不贡献字符。 |
| 行内代码 | 使用 CommonMark 解析后的代码文字；代码中的尖括号和标签形字符串是文字，不是 HTML。 |
| 原始 HTML 标签、注释或其他原始 HTML 内联结构 | 拒绝整个标题或标签；不执行、渲染、剥离或忽略 HTML。 |

页面一级标题必须是单行文字，只允许上表中的普通文字、转义、字符实体、强调、加粗和
行内代码。按 CommonMark 识别为链接、图片、软换行、硬换行或其他内联结构时，整个标题
不可认证；不能只取链接文字、图片替代文字或把换行压成空格后接受。代码、转义或实体
形成的链接形字符串仍是普通文字。标题包含多个不允许的部分时，先判定是否含原始 HTML，
否则按不允许的内联结构判定；机器投影由[诊断 code 登记](query-check-and-output.md#诊断-code-登记)
唯一规定。

例如，`**结论**` 与 `结论` 的可见文字相等；`\<b\>`、`&lt;b&gt;` 和行内代码
`` `<b>` `` 的可见文字都是字面上的 `<b>`，解码结果不再次作为 HTML 解析。
`<b>结论</b>` 则含原始 HTML，不是可认证的标题或标签。

这不是对所有 Markdown 正文或 YAML 字符串的 HTML 限制。其他位置继续使用其既有规则；
导航和结论列表也不会因允许内联强调而获得嵌套列表、图片或附加链接的许可。公共 request
仍输入纯文本，runtime 负责必要的 Markdown 转义，不能要求调用方预先提供 Markdown 源码。

### 导航语法与顺序

`## 子主题` 是保留的二级标题。只有该小节内的规范列表项表达父子导航；页面其他位置指向
子页面的链接只是正文资源，不参与 parent、直接子页面集合或导航顺序。规范列表项的原文
形状为 `- [标题](相对目标)`，并满足：

- 列表项除一个非图片 Markdown 链接外没有其他文字、图片、tooltip 或嵌套内容；目标没有
  fragment，按 source 所在目录解析后恰指向一个直接子页面。
- 链接标签与子页面一级标题按[标题与链接文字](#标题与链接文字)解码后恰相等；实现可以
  使用必要的 Markdown 转义表达该标题，但不能用别名代替。
- 每个直接子页面恰出现一次；列表声明顺序是导航树和结论遍历的领域顺序，无关编辑必须
  保持，结构操作只在其 mapping 明确要求的位置删除、插入或移动条目。
- 小节中除空行和这些规范列表项外不得有其他内容。重复小节、普通段落、非规范列表项、
  错误目标或重复子页面都使导航不可认证。
- 结论制品已生成时，根页在全部直接子页面之后保留唯一的结论汇总列表项；它必须是小节
  最后一个非空项，原文形状为 `- [结论汇总](conclusions.md)`，且不进入直接子页面集合。
  结论未生成时不得出现该保留项。

### 决策表、转换与提交

| 请求后的路径角色 | 物理父与导航事实 | 根状态 / recovery | 结果 |
| --- | --- | --- | --- |
| 合法根、叶或 hub | 可认证且三者一致 | 可写根且无 pending | 可纳入完整计划；在同一领域提交中更新路径、`parent` 和必要导航。成功投影由[公共结果、错误与可观测性](../runtime/errors-and-observability.md#公共结果错误与可观测性)提供。 |
| 合法根、叶或 hub | 缺失、重复、错指或双表示 | 任意 | 拒绝；零写；按直接事实选择 `navigation.invalid`、`page.parent_invalid` 或 `page.role_invalid`。 |
| `pages/index.md` | 任意 | 任意 | 拒绝、零写，并由 Check 报告 `page.role_invalid`（`reserved_path`）。 |
| 未受管理 Markdown 进入页面命名空间 | mapping、父关系、导航与完整闭包均可证明，且满足 move 的 candidate 门槛 | 由 move 合同认证 | 只允许按 `D-MOVE` 的页面纳管分支提交；不凭路径自动补齐标题、页面头部信息、状态或关系。 |
| registered 根中的未受管理 source（包括 directory 的 `file`/`prefix` target） | 任意 | 已认证根但不满足 candidate 纳管 | 拒绝，零写；不得借页面角色或 parent/nav 规则创建所有权。 |
| 非 Markdown、`.draft` 内节点或路径逃逸 | 任意 | 任意 | 不是页面输入；拒绝，零写；已认证页面角色错误复用 `page.role_invalid`，不可分类事实使用 `runtime.closure_incomplete`。 |
| 文件 source 含附件/非 Markdown/无法分类节点，或目录 retained 节点未能认证 | 任意 | 任意 | 不是页面输入；按 `D-MOVE` 矩阵外门禁拒绝，零写；来源集合错误使用 `move.sources_not_movable`，不可稳定取得事实使用 `runtime.closure_incomplete`。 |
| root state 为 unknown、mixed 或不可认证，或 mapping/链接闭包不可认证 | 任意 | 任意 | 写前拒绝，零写并保留诊断；不可稳定取得的范围使用 `runtime.closure_incomplete`，私有权威损坏使用 `runtime.corrupt`；不得把未知根、不完整闭包或 lower-bound mapping 解释为合法页面树。 |
| 任一需要恢复的根 | 任意 | pending | `runtime.recovery_required`；阻止转换，保留现场，等待显式 recovery。 |

提交点在所有受影响页面、关系和必要导航都已进入同一个完整计划之后；路径、parent、
导航、入站/出站链接和自动 collapse（固定 after-path 为 `pages/topic/index.md ->
pages/topic.md`）均消费同一 mapping，不能拆成多个可见成功。candidate 的
Check 或协议欠账只表示可观察的现有事实，不反向拒绝已经满足 `D-MOVE` candidate
纳管门槛的 Markdown；若欠账使 mapping、parent、nav 或闭包无法证明，则按上表写前失败。
页面头部和正文都不得改变本节已经固定的导航小节、列表项和声明顺序。

对 pending `D-MOVE`，页面 owner 只消费
[D-MOVE snapshot S 语义载荷](structure.md#d-move-snapshot-s-语义载荷) 与 runtime recovery
owner 的
[D-MOVE snapshot S 恢复门禁与 Q6-B 漂移分类](../runtime/recovery.md#d-move-snapshot-s-恢复门禁与-q6-b-漂移分类)。
其他操作恢复中只完成部分写入的现场只消费
[证据优先恢复表](../runtime/recovery.md#证据优先恢复表)。本页不独立分类 snapshot 或
选择 recovery 结果。

### 失败、零写与恢复

- 导航无法解析、父页不存在、角色冲突、目标碰撞或 mapping 不可证明时，不写任何受
  管理页面、关系或导航。已认证内容分别使用 `navigation.invalid`、`page.parent_invalid`、
  `page.role_invalid` 或 `operation.path_conflict`；错误只返回一个直接负责的问题。
- runtime 报告耐久不确定时不返回结构成功；保留 recovery 现场。重试前重新认证受影响树、
  mapping 与导航事实，并按上列 recovery owner links 消费恢复路由。
- 外部手工移动没有可证明 mapping 时，不能由本合同猜测 parent 或导航；必须失败并由
  Check 诊断。

### 公共请求事实边界

公共 request 的字段、类型、基数、枚举、CLI 映射、路径词法、mode、通用参数、禁止组合和
request-invalid 校验顺序，直接由 [R-INTERFACE 共用值与输入校验](../runtime/interface.md#共用值与输入校验)、
[普通操作 request](../runtime/interface.md#普通操作-request)及[mode、逻辑身份与私有边界](../runtime/interface.md#mode逻辑身份与私有边界)
唯一规定。页面角色与导航不是
调用方可提交的独立字段；`create`、`move`、`promote`、`collapse` 的操作数在进入本领域后
只按各自领域 owner 的认证规则使用。物理 parent、导航条目、页面角色、结论保留项和链接闭包
始终由 Runtime 从根内事实推导。页面 owner 只认证它收到的 after-tree，不根据 basename、文本
相似度或调用方提供的导航猜测缺失事实。

### 领域错误投影

本页只保留页面与导航已认证事实到机器码的选择：导航事实使用 `navigation.invalid`，物理
父事实使用 `page.parent_invalid`，路径角色事实使用 `page.role_invalid`；目标碰撞这一已
认证事实使用上方决策表明确列出的 `operation.path_conflict`，来源集合不满足 move 门槛时
使用该表明确列出的 `move.sources_not_movable`。根、mapping 或链接闭包无法稳定取得时使用
`runtime.closure_incomplete`，私有权威损坏时使用 `runtime.corrupt`，恢复 pending 时使用
`runtime.recovery_required`。各 code 的 `severity`、`details.reason`、定位、建议和公共
结果结构只由[诊断 code 登记](query-check-and-output.md#诊断-code-登记)与[公共结果、错误与可观测性](../runtime/errors-and-observability.md#公共结果错误与可观测性)
拥有，本页不复制值域或错误外壳；未知、不可取得和私有权威损坏不折叠为同一页面错误。

### 验证

- 覆盖根、叶、hub、空根、`pages/index.md`、缺失/重复 `## 子主题`、小节外同目标链接、
  非列表链接、附加文字/tooltip/fragment、标题不匹配、重复导航、错指导航、声明顺序与
  叶/hub 双表示；结论生成和未生成时分别验证根导航最后一项。
- 对一个多路径结构变更，证明路径、parent 和导航从同一 mapping 同时变化；任一事实
  缺失时验证零写。
- 验证 candidate 页面纳管后仅获得路径角色，协议欠账仍可由 Check 观察；registered
  unmanaged source、目录成员 `mixed`/`unknown`/`unverifiable`、unknown/mixed/unverifiable
  root、非 Markdown/附件 source 和不完整闭包均验证写前拒绝零写。验证路径、parent、导航
  和 collapse 均从一个 mapping 派生，并验证 D-MOVE 与 generic 非 D-MOVE 的 recovery decision
  都经上列 owner links，本页不保留独立分类。

## 来源

- `33aeaec60a34833ce8b686bfdd7e50b8dc9932d0:.scratch/draft-authority-spec-migration/signed-decisions-transcript.md#已签署决定迁移摘要`
