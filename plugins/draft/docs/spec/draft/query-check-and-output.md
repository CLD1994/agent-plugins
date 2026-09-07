# 查询、检查与输出

## 查询行为合同

`D-QUERY` 定义七类读操作在公共请求校验完成后的来源范围、结果集合和认证边界。页面与链接事实
由 Draft 页面和资源 owner 认证；查询不把索引、缓存或 projection 变成权威来源。

### 请求的公共边界

查询 request 的联合形状、公共字段及类型/基数、集合控制、筛选值、路径词法、未知或重复
成员处理和 `runtime.request_invalid` 的唯一校验顺序，统一由[运行时公共接口的共用值与输入校验](../runtime/interface.md#共用值与输入校验)
和[Trash、Restore、查询、通知与恢复输入](../runtime/interface.md#trashrestore查询通知与恢复输入)定义。根选择及其
`--root`、`DRAFT_ROOT` 处理也由该 owner 定义。本页只描述经过公共校验的 query 如何取得
领域事实、决定完整性并投影结果；以下七个 query 名称仅作为 D-QUERY 的领域行为标签。

本页不再次定义路径词法或目标值编码；`backlinks` 的目标身份和资源分类只在完成公共
request 校验后，按资源 owner 的认证事实解释。

集合控制的输入形状与组合由[运行时公共接口](../runtime/interface.md#trashrestore查询通知与恢复输入)定义；本页后文只说明这些已认证输入如何作用于查询集合、Check 诊断和分页结果。

Check 筛选如何作用于已认证诊断集合由[运行时公共接口](../runtime/interface.md#trashrestore查询通知与恢复输入)定义；本页只保留筛选后的领域结果、完整性和排序语义。

### 来源范围与项目集合

根状态决定查询可读取的 Markdown 来源。候选根正在整理未纳管内容，因此链接和反向链接
同时纳入两种来源；正式根只承诺受管理页面网络，未受管理来源既不扫描后混入，也不计入
总数。`page.backlink_count` 与 `backlinks` 使用完全相同的已认证来源范围。

| 查询 | 来源/目标 | 结果要求 |
| --- | --- | --- |
| `page` | 一个受管理页面 | 返回一个完整摘要；不因未受管理 Markdown 自动创建页面。 |
| `links` | 候选根：managed + unmanaged；正式根：managed | 每个普通 CommonMark 链接或图片引用各一项，并保留来源类型。 |
| `backlinks` | 候选根：managed + unmanaged；正式根：managed | 返回范围内指向目标的每个链接/图片引用，并保留来源类型。 |
| `relations` | 受管理页面的正式关系 | 只返回关系事实，不把普通链接提升为关系。 |
| `status` | 受管理页面状态 | 返回匹配请求状态的页面路径。 |
| `recoverable` | Trash authority 与程序固定的同一 UTC 观察时刻 | 只返回当前可恢复且证据完整的回收包。 |
| `check` | 根、页面、导航、关系、资源及适用 runtime authority | 返回完整 Check 诊断，或只返回可精确定位的 blocker。 |

`backlinks.target` 可以指向不存在的根内页面或本地资源，也可以指向外部 URI；合法但
不存在的目标产生完整空集合，不产生错误。显式请求正式根不允许的未受管理 `source` 是
`query.source_not_allowed`，不能伪装成“该文件没有链接”的空成功。

来源范围的每个组合如下；`page.backlink_count` 逐项复用 `backlinks` 的对应口径：

| root state | source kind | `links` | `backlinks` | `page.backlink_count` |
| --- | --- | --- | --- | --- |
| `candidate` | `managed` | 纳入并标记 `managed` | 纳入并标记 `managed` | 纳入 |
| `candidate` | `unmanaged` | 纳入并标记 `unmanaged` | 纳入并标记 `unmanaged` | 纳入 |
| `registered` | `managed` | 纳入并标记 `managed` | 纳入并标记 `managed` | 纳入 |
| `registered` | `unmanaged` | 排除，不计入结果或 total | 排除，不计入结果或 total | 排除，不计入 count |

### 查询集合与完整性

公共 `QueryOutcome` 的最外层联合，以及完整/不完整时 `items` 与 `blockers` 的互斥出现和
公共字段省略规则，由[公共结果、错误与可观测性](../runtime/errors-and-observability.md#公共结果错误与可观测性)
唯一拥有。本页只定义查询集合内部的 `entries`、`total` 和游标语义；完整性触发仍由本页
按各查询的认证事实判定，并按该公共联合投影。

`items` 和 `blockers` 的集合内部都使用 `entries`、`total` 和可选 `next_cursor`。`total` 是
当前语义 snapshot 应用来源范围和筛选条件后、分页前该集合的精确数量，不是本页数量或
剩余数量。完整集合写入 `items`，不完整结果写入 `blockers`；`blockers.entries` 只包含
阻碍本次答案的阻塞项，不返回任何 `items`。两种集合的互斥出现由上面链接的公共结果 owner
保证。

`count_only: true` 仍遵循公共查询结果联合：完整时 `items.entries` 必须为空且 `items.total` 是
精确项目总数；不完整时只返回 `blockers.entries: []` 与精确 blocker 总数。此模式不返回
`next_cursor`。`page` 成功时 `items.total` 固定为 `1`，恰有一个 entry，不返回
`next_cursor`；`page` 结果始终完整，所需摘要事实无法认证时整体失败。

查询结果不公开单独的 snapshot 标识，也不公开 runtime projection、索引或缓存的编码。每次
查询仍以一个语义 snapshot 计算根、content、活动成员、head 和相关 projection generation；
相关 authority 改变即使该 snapshot 失效。读操作不隐式 recovery，也不把失败事实降级为空
集合或 lower-bound 集合。

### 分页、游标与排序

`items` 与 `blockers` 是两个不同的集合；本合同只在不完整结果中使用 blockers，不能用一个
集合的游标继续另一个集合。游标是不透明值，绑定 root identity、query、规范化 filters、
root state 对应的来源范围、语义 snapshot、集合种类和最后一项完整 sort tuple。它只能继续
同一请求、同一集合和同一 snapshot，并只返回严格位于上一页末项之后的项目。请求或游标
无法认证时按[查询错误与阻塞项](#查询错误与阻塞项)中的 `query.cursor_invalid`、
`query.cursor_mismatch` 或 `query.snapshot_expired` 选择错误；snapshot 已变化不得在新
snapshot 上续页。

所有字节比较均使用规范机器顺序，不使用 locale、展示文案、扫描顺序、mtime 或内部记录
位置。路径先按规范根相对路径的 UTF-8 字节比较；各集合的完整公开排序如下：

| 集合/查询 | 完整 sort tuple |
| --- | --- |
| `page` | `path`；只有一项，仍按路径定位。 |
| `links`、`backlinks` | `source` UTF-8 字节、`line`、`column`；若 span 仍并列，依次用 `syntax`、`target_kind`、`target`、`target_scope`、`target_state` 和 `fragment` 的公开值补足。 |
| `relations` outgoing | YAML 声明顺序；`source` 固定为请求页面，游标私下保存声明位置。 |
| `relations` incoming | 来源 `source` 路径 UTF-8 字节，再按该来源 YAML 声明顺序；同一声明仍不公开序号。 |
| `status` | `path` UTF-8 字节。 |
| `recoverable` | `page` UTF-8 字节、`expires_at` 时间值、`trash_id` UTF-8 字节。 |
| 所有 blocker | `path` UTF-8 字节、`code`、适用的 `line`、`reason`，再按该 blocker code 登记的公开 `details` 字段顺序。缺失的可选分量排在适用分量之前。 |

所有非 Check 查询集合和 blocker 中如仍出现并列，也只能使用该结果已经公开且由 owner 登记
的稳定事实补足，不能使用私有索引位置。数组顺序和跨页无重复/遗漏是协议；JSON 对象成员
排列顺序不是协议。

### 项目字段

每个公开字段都是已认证事实；不适用的可选字段省略，不输出 `null`。路径是规范根相对
POSIX 路径，`line` 与 `column` 从 `1` 开始，`column` 按 Unicode 字符计数；计数字段是
非负整数。

| 查询 | 每个 `entries` 项目的字段 |
| --- | --- |
| `page` | `path`、`title`、`status`、`role`、非根页必需的 `parent`、`child_count`、`link_count`、`backlink_count`、`outgoing_relation_count`、`incoming_relation_count`。`role` 只能为 `root`、`leaf`、`hub`。 |
| `links`、`backlinks` | `source`、`source_kind`、`syntax`、`line`、`column`、`target_kind`、`target`、`target_scope`、`target_state`；`source_kind` 只能为 `managed`、`unmanaged`，`syntax` 只能为 `link`、`image`；有 fragment 时增加 `fragment: {value, encoding_valid}`。 |
| `relations` | 只含 `source`、`target`、`type`。`type` 是正式关系事实；不加入声明序号，也不加入结构 `parent`。 |
| `status` | 只含 `path`；请求已经给出状态，因此不重复 `status` 或 `note`。 |
| `recoverable` | 不透明 `trash_id`、回收子树根页面 `page`、以 `Z` 结尾的 RFC 3339 UTC `expires_at`。 |

链接的 `target_kind`、`target_scope` 和 `target_state` 的认证规则由[资源与链接](resources-and-links.md)
定义。进入 `.draft` 的受保护内部目标不形成链接项目或计数；它只形成 Check 诊断。

Check 诊断项不属于本节的非 Check query entry；其字段与完整性见[Check 诊断合同](#check-诊断合同)。

### 查询错误与阻塞项

查询错误的公共结果投影由[公共结果、错误与可观测性](../runtime/errors-and-observability.md#公共结果错误与可观测性)
唯一拥有；本页不复制其公共字段。请求形状、公共字段和值域及校验顺序按[运行时公共接口](../runtime/interface.md#共用值与输入校验)
处理。以下只登记 D-QUERY 读取领域事实后的错误触发、机器码和领域后果。

| 触发事实 | code | 领域后果 |
| --- | --- | --- |
| 要求的受管理页面不存在 | `page.not_found` | 不返回残缺项目。 |
| `links.source` 不存在 | `query.source_not_found` | 不返回查询结果。 |
| 来源存在但当前根状态不允许它 | `query.source_not_allowed` | 不返回查询结果。 |
| 游标损坏或无法认证 | `query.cursor_invalid` | 不在新 snapshot 上继续。 |
| 游标属于其他根、查询、条件、来源范围、集合或分页请求 | `query.cursor_mismatch` | 不返回集合。 |
| 游标固定的 snapshot 已变化 | `query.snapshot_expired` | 重新发起查询，不继续旧游标。 |
| runtime 准入已经通过，但 D-QUERY 不能建立本次查询的完整结果范围且不能合法形成 blocker | `query.incomplete` | 不返回部分成功或空集合。 |
| 显式或选定的 root 不能认证为合法的当前 Draft 根 | `runtime.root_invalid` | 不返回查询 projection。 |
| Draft 私有 marker、runtime 或 authority 已稳定证明损坏或矛盾 | `runtime.corrupt` | 不返回查询 projection。 |
| 已认证有效 recovery overlay 为 pending | `runtime.recovery_required` | 不返回查询 projection。 |
| runtime 准入所需普通事实无法完整、稳定取得 | `runtime.closure_incomplete` | 不返回查询 projection。 |

能够合法返回部分结果的查询通过 `complete: false` 暴露 blockers。Check 的完整性与 blocker
结果按下节的领域触发判定；`query.target_unavailable` 和 `query.source_unavailable` 是下节
的 blocker，不是上述 error。

阻塞项的公共项目形状由[公共结果、错误与可观测性](../runtime/errors-and-observability.md#公共结果错误与可观测性)
唯一拥有；本页只保留 D-QUERY 的 blocker 触发事实、code-specific details 和查询排序：

| blocker code | 触发事实与 details |
| --- | --- |
| `query.source_unavailable` | 本次查询已认证来源属于范围，但无法取得稳定内容；`details.reason` 只能是 `permission_denied`、`io_error`、`unstable`。来源稳定不存在不使用该 blocker。 |
| `query.target_unavailable` | 链接身份、来源内容和查询范围均稳定，但根内目标存在性探测无法稳定完成；`path` 与可选 `line` 指向来源链接，`details` 必含 `reason`（`permission_denied`、`io_error` 或 `unstable`）、`target_kind` 和规范 `target`。 |
| 已登记的 Check code | 内容已经认证为违反规则，但该问题使本次查询范围无法完整解析；只使用[诊断 code 登记](#诊断-code-登记)中该机器 code 的 `details` schema，不增加 `severity`。 |

`query.target_unavailable` 的排序是 `path`、`code`、可选 `line`、`details.reason`、
`details.target_kind`、`details.target`；完全相同项去重。`links` 和 `backlinks` 自身仍可
返回完整项目，并将目标 observation 写为 `unavailable`；只有 Check 因无法判断是否应产生
目标缺失诊断而返回不完整结果，且只返回 `query.target_unavailable` blockers，不返回诊断
项目；公共结果字段按 R-ERRORS 投影。

## Check 诊断合同

### 完整与不完整结果

Check 先在同一语义 snapshot 完成所需全量扫描，再应用精确 filters 和分页。所有可认证的
诊断集合只使用两种 severity：`error` 表示违反机器可验证不变量，`warning` 表示事实
完整但可能不符合预期。

已通过公共接口校验的精确 Check 筛选若没有命中诊断，仍返回完整空结果，不转换为错误或
blocker；筛选命中与分页后的 `total` 仍按本页的完整性和集合合同计算。

完整 Check 的计数基数是未过滤、未分页的完整诊断集合；`items.total` 的计数基数仍是应用
filters 后、分页前的诊断集合。`count_only` 不改变这两个计数基数。公共计数字段的名称、
对象形状、值键及出现/省略条件由[公共结果、错误与可观测性](../runtime/errors-and-observability.md#公共结果错误与可观测性)
唯一拥有。Check 遇到 `query.target_unavailable`、来源不可用或其他会改变答案的未知范围时，
按该公共 owner 的不完整结果分支只返回 blockers，不能提供任何低估计数。

Check 的诊断项必含 `code`、`severity`、`path`、`message`。`line` 仅在能定位直接负责
原文行时出现；结构化 `details` 由 code owner 登记，`suggestions` 只有可靠且直接相关时
出现，不返回空数组。上游内容无法解析时只报告能够直接证明的首个问题，不推断依赖该
内容的下游问题；彼此独立且都可认证的问题分别报告。

### 诊断 code 登记

每个 code 只有一个 owner，负责触发事实、severity、`details` schema、排序字段和安全
建议模板。表中每一行只登记一个 `reason` 或一个无 `reason` 的触发范围，并在最后一列
给出该机器选择所依赖的已认证事实或唯一领域入口；实施者不得根据英文名称或列表顺序
推断机器 code。以下是当前公开登记表：

| code | severity | `details.reason` 或触发范围 | 已认证事实或唯一领域入口 |
| --- | --- | --- | --- |
| `page.title_invalid` | `error` | `raw_html` | 页面一级标题含 CommonMark 原始 HTML 内联结构；[标题与链接文字](pages-and-navigation.md#标题与链接文字)。`line` 指向该一级标题，`details` 只含 `reason`。 |
| `page.title_invalid` | `error` | `inline_structure` | 页面一级标题不含原始 HTML，但含 CommonMark 链接、图片、软/硬换行或其他不允许的内联结构；[标题与链接文字](pages-and-navigation.md#标题与链接文字)。`line` 指向该一级标题，`details` 只含 `reason`。 |
| `page.frontmatter_invalid` | `error` | `missing` | 受管理页面缺少必需 YAML frontmatter；[页面头部与正文边界](pages-and-navigation.md#页面头部与正文边界)。 |
| `page.frontmatter_invalid` | `error` | `delimiter_invalid` | frontmatter 首行或结束行不是只含 `---`；[页面头部与正文边界](pages-and-navigation.md#页面头部与正文边界)。 |
| `page.frontmatter_invalid` | `error` | `yaml_invalid` | frontmatter 不是可解析的 YAML 文档；[YAML 页面头部结构](status-and-relations.md#yaml-页面头部结构)。 |
| `page.frontmatter_invalid` | `error` | `not_mapping` | YAML 文档根值不是映射对象；[YAML 页面头部结构](status-and-relations.md#yaml-页面头部结构)。 |
| `page.frontmatter_invalid` | `error` | `duplicate_key` | 顶层、关系成员或嵌套映射出现重复键；[YAML 页面头部结构](status-and-relations.md#yaml-页面头部结构)。 |
| `page.frontmatter_invalid` | `error` | `unsupported_feature` | 使用 YAML 注释、anchor、alias、merge key 或自定义 tag；[YAML 页面头部结构](status-and-relations.md#yaml-页面头部结构)。 |
| `page.frontmatter_invalid` | `error` | `unknown_key` | 顶层键不在 `status`、`note`、`parent`、`relations` 四键集合；键 `产出` 的专门事实见[页面头部键与输出边界](conclusions.md#页面头部键与输出边界)，其余键见[YAML 页面头部结构](status-and-relations.md#yaml-页面头部结构)。 |
| `page.frontmatter_invalid` | `error` | `invalid_value_type` | `note` 是非 null 的非字符串值、`parent` 不是字符串，或 `relations` 外层不是列表；不用于 `status`、空 `note`、合法外层列表中的关系成员或其他已有专门分区；[YAML 页面头部结构](status-and-relations.md#yaml-页面头部结构)。 |
| `page.status_invalid` | `error` | `missing` | 受管理页面缺少必需 `status` 键；[YAML 页面头部结构](status-and-relations.md#yaml-页面头部结构)。 |
| `page.status_invalid` | `error` | `invalid_type` | `status` 值不是字符串；[YAML 页面头部结构](status-and-relations.md#yaml-页面头部结构)。 |
| `page.status_invalid` | `error` | `invalid_value` | `status` 字符串不属于 `pending`、`proposed`、`confirmed`、`superseded`、`closed`；[状态、关系与不变量](status-and-relations.md#状态关系与不变量)。 |
| `page.note_invalid` | `error` | `empty` | 存在 `note`，但值为 YAML null 或空字符串；[YAML 页面头部结构](status-and-relations.md#yaml-页面头部结构)。 |
| `page.note_invalid` | `error` | `multiline` | `note` 值含换行；[YAML 页面头部结构](status-and-relations.md#yaml-页面头部结构)。 |
| `page.note_invalid` | `error` | `control_character` | `note` 值含控制字符；[YAML 页面头部结构](status-and-relations.md#yaml-页面头部结构)。 |
| `page.role_invalid` | `error` | `reserved_path` | 路径是保留且非法的 `pages/index.md`；[路径角色与不变量](pages-and-navigation.md#路径角色与不变量)。 |
| `page.role_invalid` | `error` | `root_invalid` | 根页不是唯一合法的 `index.md` 根角色；[路径角色与不变量](pages-and-navigation.md#路径角色与不变量)。 |
| `page.role_invalid` | `error` | `leaf_has_children` | 叶页面出现子页面命名空间或保留 `## 子主题`；[路径角色与不变量](pages-and-navigation.md#路径角色与不变量)。 |
| `page.role_invalid` | `error` | `empty_non_root_hub` | 非根 hub 为空且尚未由 collapse 收敛；[路径角色与不变量](pages-and-navigation.md#路径角色与不变量)。 |
| `page.parent_invalid` | `error` | `unexpected` | 根页出现不允许的 `parent`，或 parent 出现在不应有的页面；[YAML 页面头部结构](status-and-relations.md#yaml-页面头部结构)。 |
| `page.parent_invalid` | `error` | `missing` | 非根受管理页面缺少必需 `parent`；[路径角色与不变量](pages-and-navigation.md#路径角色与不变量)。 |
| `page.parent_invalid` | `error` | `invalid_path` | `parent` 已认证为字符串，但不是规范的 Draft 根相对受管理页面路径；[YAML 页面头部结构](status-and-relations.md#yaml-页面头部结构)。 |
| `page.parent_invalid` | `error` | `mismatch` | `parent` 与物理父页或父页直接子导航不一致；[路径角色与不变量](pages-and-navigation.md#路径角色与不变量)。 |
| `navigation.invalid` | `error` | `section_missing` | 页面缺少必需的唯一 `## 子主题` 小节；[导航语法与顺序](pages-and-navigation.md#导航语法与顺序)。 |
| `navigation.invalid` | `error` | `section_duplicate` | 页面出现重复 `## 子主题` 小节；[导航语法与顺序](pages-and-navigation.md#导航语法与顺序)。 |
| `navigation.invalid` | `error` | `content_invalid` | 导航小节含空行以外的段落、非规范列表项或嵌套内容；[导航语法与顺序](pages-and-navigation.md#导航语法与顺序)。 |
| `navigation.invalid` | `error` | `target_invalid` | 导航链接不是无 fragment 的直接子页面目标；[导航语法与顺序](pages-and-navigation.md#导航语法与顺序)。 |
| `navigation.invalid` | `error` | `child_missing` | 已认证直接子页面未在父导航中出现一次；[导航语法与顺序](pages-and-navigation.md#导航语法与顺序)。 |
| `navigation.invalid` | `error` | `child_duplicate` | 同一直接子页面在父导航中出现多次；[导航语法与顺序](pages-and-navigation.md#导航语法与顺序)。 |
| `navigation.invalid` | `error` | `unexpected_child` | 导航列出不属于该父页面直接子集合的页面；[导航语法与顺序](pages-and-navigation.md#导航语法与顺序)。 |
| `navigation.invalid` | `error` | `title_mismatch` | 导航标签解码值不等于子页面一级标题；[导航语法与顺序](pages-and-navigation.md#导航语法与顺序)。 |
| `navigation.invalid` | `error` | `conclusions_entry_invalid` | 结论制品生成状态与根导航保留项的存在、位置或固定形状不一致；[导航语法与顺序](pages-and-navigation.md#导航语法与顺序)。 |
| `relation.declaration_invalid` | `error` | `container_empty` | `relations` 出现空列表；[YAML 页面头部结构](status-and-relations.md#yaml-页面头部结构)。 |
| `relation.declaration_invalid` | `error` | `member_shape` | `relations` 列表成员不是恰含 `type`、`target` 的映射；命中后不再从该成员推断嵌套值问题；[YAML 页面头部结构](status-and-relations.md#yaml-页面头部结构)。 |
| `relation.declaration_invalid` | `error` | `type_unknown` | 形状合法的关系成员中，`type` 不是五个允许字符串之一，包括非字符串值；[YAML 页面头部结构](status-and-relations.md#yaml-页面头部结构)。 |
| `relation.declaration_invalid` | `error` | `target_path_invalid` | 形状合法的关系成员中，`target` 不是规范的 Draft 根相对 POSIX 受管理页面路径，包括非字符串值；[YAML 页面头部结构](status-and-relations.md#yaml-页面头部结构)。 |
| `relation.endpoint_invalid` | `error` | `target_missing` | 关系 target 页面不存在；[决策表、转换与提交](status-and-relations.md#决策表转换与提交)。 |
| `relation.endpoint_invalid` | `error` | `target_unmanaged` | 关系 target 虽有路径事实但不是受管理页面；[决策表、转换与提交](status-and-relations.md#决策表转换与提交)。 |
| `relation.endpoint_invalid` | `error` | `conclusions_endpoint` | 任一可变正式关系把 `conclusions.md` 作为 source 或 target；[状态、关系与不变量](status-and-relations.md#状态关系与不变量)。 |
| `relation.pair_invalid` | `error` | `reverse_missing` | 成对关系缺少所需反向声明；[状态、关系与不变量](status-and-relations.md#状态关系与不变量)。 |
| `relation.pair_invalid` | `error` | `reverse_mismatch` | 反向声明的 type 或端点不能与主动声明相反对应；[状态、关系与不变量](status-and-relations.md#状态关系与不变量)。 |
| `relation.pair_invalid` | `error` | `reverse_duplicate` | 所需反向声明重复；[决策表、转换与提交](status-and-relations.md#决策表转换与提交)。 |
| `relation.graph_invalid` | `error` | `duplicate` | 同一正式关系声明重复；[状态、关系与不变量](status-and-relations.md#状态关系与不变量)。 |
| `relation.graph_invalid` | `error` | `self_reference` | 正式关系的 source 与 target 相同；[状态、关系与不变量](status-and-relations.md#状态关系与不变量)。 |
| `relation.graph_invalid` | `error` | `cycle` | 正式关系图形成环；[状态、关系与不变量](status-and-relations.md#状态关系与不变量)。 |
| `relation.status_mismatch` | `error` | `supersedes` 目标状态不正确；不增加 `reason`。 | `supersedes` 关系的 target 未处于要求的 `superseded` 状态；[决策表、转换与提交](status-and-relations.md#决策表转换与提交)。 |
| `conclusions.conflict` | `error` | `file_exists` | 未生成状态下 `conclusions.md` 已存在；[显式生成决策表](conclusions.md#显式生成决策表)。 |
| `conclusions.conflict` | `error` | `navigation_entry_exists` | 未生成状态下根导航已有结论保留项；[显式生成决策表](conclusions.md#显式生成决策表)。 |
| `conclusions.invalid` | `error` | `frontmatter` | 已生成结论制品的 frontmatter 不是固定 `status: proposed`、`parent: index.md`；[固定文件形状](conclusions.md#固定文件形状)。 |
| `conclusions.invalid` | `error` | `structure` | 已生成结论制品缺少或增加固定标题、section、段落或列表结构；[固定文件形状](conclusions.md#固定文件形状)。 |
| `conclusions.invalid` | `error` | `member_missing` | 已认证应出现的确认或质疑成员缺失；[固定文件形状](conclusions.md#固定文件形状)。 |
| `conclusions.invalid` | `error` | `member_extra` | 结论制品出现没有对应已认证成员的条目；[固定文件形状](conclusions.md#固定文件形状)。 |
| `conclusions.invalid` | `error` | `member_duplicate` | 同一确认成员或质疑来源重复出现；[固定文件形状](conclusions.md#固定文件形状)。 |
| `conclusions.invalid` | `error` | `link_invalid` | 结论列表链接含图片、tooltip、fragment、错误目标或其他非固定链接形状；[固定文件形状](conclusions.md#固定文件形状)。 |
| `conclusions.invalid` | `error` | `order_invalid` | 确认成员、受质疑顶层或质疑来源不符合固定领域顺序；[固定文件形状](conclusions.md#固定文件形状)。 |
| `conclusions.invalid` | `error` | `artifact_missing` | 已生成状态缺少应存在的 `conclusions.md` 制品；[显式生成决策表](conclusions.md#显式生成决策表)。 |
| `conclusions.invalid` | `error` | `navigation_entry_missing` | 已生成状态缺少根导航最后的结论保留项；[导航语法与顺序](pages-and-navigation.md#导航语法与顺序)。 |
| `conclusions.invalid` | `error` | `span_unusable` | 无法从 CommonMark 结构唯一取得并映射结论条目的目标 span；[固定文件形状](conclusions.md#固定文件形状)。 |
| `resource.link_invalid` | `error` | `source_encoding` | source bytes 已稳定读取但不是合法 UTF-8；[稳定内容错误与事实不完整](resources-and-links.md#稳定内容错误与事实不完整)。 |
| `resource.link_invalid` | `error` | `target_encoding` | 本地 target 路径的百分号或 UTF-8 编码已稳定证明非法；[稳定内容错误与事实不完整](resources-and-links.md#稳定内容错误与事实不完整)。 |
| `resource.link_invalid` | `error` | `fragment_encoding` | fragment 的百分号或 UTF-8 编码已稳定证明非法；[稳定内容错误与事实不完整](resources-and-links.md#稳定内容错误与事实不完整)。 |
| `resource.link_invalid` | `error` | `target_unclassifiable` | 编码合法但 target 已稳定证明无法归入 `page`、`local` 或 `external`；[稳定内容错误与事实不完整](resources-and-links.md#稳定内容错误与事实不完整)。 |
| `resource.protected_target` | `error` | 规范化后目标进入 `.draft`；不增加 `reason`。 | 规范化 target 的第一个组件是 `.draft`；[目标分类算法](resources-and-links.md#目标分类算法)。 |
| `resource.target_missing` | `warning` | 根内页面或本地资源已确认不存在；不增加 `reason`。 | 根内 `page` 或 `local` target 的存在性已稳定证明为缺失；[target_scope 与 target_state](resources-and-links.md#target_scope-与-target_state)。 |

结论制品诊断描述被检查内容，不授权 Check 修复文件或导航，也不直接决定显式生成能否
重建其已拥有的输出。Check、显式生成和结构窄改写对既有制品的不同处理，统一消费
[既有制品的检查与使用](conclusions.md#既有制品的检查与使用)；正式根写入仍检查完整计划的
after-state，不能用待替换旧输出的诊断代替重建后的检查结果。
同一扫描若发现[生成输入事实集合](conclusions.md#显式生成决策表)中的来源页面、必需导航
或关系违规，仍保留直接事实 owner 的 code；不能归入旧输出诊断或降级为事实不可取得。

同一个 YAML 节点只使用上表中最具体的一个触发分区，不再为同一根因增加通用诊断。解析或
成员形状不成立时，只报告能够直接认证的结构问题，不推断依赖它的嵌套值问题；不同节点上
彼此独立且可单独认证的问题仍分别报告。`status` 的缺失、类型和值域始终使用其三个专门分区；
`note`、`parent` 与 `relations` 按各登记行写明的外层类型、内容、路径和成员顺序分类。

在 `page.frontmatter_invalid` 的 `unknown_key` 分支中，若已认证顶层键为 `产出`，Check
投影的 `message` 必须包含键名 `产出`；原始位置使用顶层 `line`；`details` 只含
`reason: unknown_key`，不得增加 `details.key`，severity 保持既有 `error`。

Check `items` 中的诊断按 `path`、`code`、可选 `line`、`reason` 及该 code 登记的其余公开
details 字段排序；完全相同的已认证 Check 诊断（包括其作为 blocker 投影的同一诊断）只
保留一项。`message` 和 `suggestions` 只投影已认证事实，逐字文案不是机器协议。未知
frontmatter 键统一使用 `page.frontmatter_invalid`/`reason: unknown_key`。

### Check 门禁与副作用

`adopt` 只能消费由[运行时公共接口](../runtime/interface.md#trashrestore查询通知与恢复输入)规定的无筛选、无分页 Check 请求取得的完整结果；只有 blockers 为空且完整诊断集合中不存在
`severity: error` 诊断才可通过。正式根的普通写必须先对完整计划的虚拟 after-state 运行同一
无过滤、无分页 Check；只有无 blocker、结果完整且不存在 `severity: error` 诊断才允许领域
提交。warning 不阻止 adopt 或正式根普通写。可认证的完整 after-state 仍含 error 诊断时使用
`operation.check_failed`；Check 无法完整运行或闭包事实不能稳定取得时使用
`runtime.closure_incomplete`。这两个错误的公共字段、`details` schema 和结果投影由[公共结果、错误与可观测性](../runtime/errors-and-observability.md#公共结果错误与可观测性)定义；过滤后的零 error、单页零 error、错误数量未增加或只修复部分事实都不能通过门禁。

Check、其他查询和错误都不返回自动生命周期字段。读操作不修改页面头部、正文、关系、导航、
Trash active membership 或结论快照；runtime 可在私有认证边界维护 projection，但不会把
routine lifecycle、索引维护或自动 recovery 作为公共 warning。fatal 或最终 scan 不稳定时
只按 R-ERRORS 的公共错误投影返回，不带 projection。

## 验证

- 请求形状、公共值域和 `runtime.request_invalid` 选择顺序按[运行时公共接口](../runtime/interface.md#共用值与输入校验)验证；本页验证公共校验通过后七类查询各自的领域资格、来源范围、完整性、错误与 blocker 分支。
- 在 candidate/registered 两种根状态分别验证 managed/unmanaged links、backlinks 和
  `page.backlink_count` 的来源范围、source 资格错误、不存在 target 的完整空结果，以及
  所有项目字段和枚举。
- 验证完整/不完整结果互斥、items/blockers 独立集合、精确 total、count-only 空 entries、
  page total=1、游标绑定和 snapshot 失效、无重复无遗漏及逐类完整排序。
- 验证五种 target observation、资源 invalid/protected/unavailable、`query.target_unavailable`
  的 path/line/reason/target 详情和排序；验证 Check blocker 结果不含诊断项目或计数。
- 覆盖全部 Check code、两种 severity、完整诊断计数与过滤 total 的差异、message
  必有、suggestions 可靠时才有、上游错误抑制、重复诊断去重和 `adopt`/正式根 after-state
  门禁；验证未知 frontmatter 键统一落入 `page.frontmatter_invalid`。
- 验证查询零领域副作用、recovery pending 不被读操作收敛；公共结果、错误和 CLI 输出按
  [R-ERRORS](../runtime/errors-and-observability.md#公共结果错误与可观测性)核对。

## 来源

- `33aeaec60a34833ce8b686bfdd7e50b8dc9932d0:.scratch/draft-authority-spec-migration/migration-evidence.md#迁移单元`
