# 资源与链接

## 资源身份与链接观察

`D-RESOURCES` 定义链接事实的认证、目标分类、存在性观察和结构改写边界。资源身份是
领域内部用于比较目标的值；独立的 `ResourceIdentity` 对象、索引布局、编码和 span
存储不属于公共 ABI。查询通过扁平的链接项目公开已认证字段，资源 owner 不为 runtime
或 `ReadOutcome` 另造对象。

### 输入、输出与枚举

- 输入是 Markdown source bytes、CommonMark 普通链接或图片引用的 span、来源路径和来源
  类型，以及结构 owner 已认证的 before-to-after mapping；source bytes 是否能稳定解码为
  UTF-8 是下方资源事实的一部分。
- 输出是可认证的链接项目、目标身份和存在性 observation，或可诊断失败。链接项目不按
  目标去重；每个可认证的普通链接或图片引用各形成一项。
- `source_kind` 只能是 `managed` 或 `unmanaged`；`syntax` 只能是 `link` 或 `image`。
- `target_kind` 只能是 `page`、`local` 或 `external`；`target_scope` 只能是 `root`、
  `outside` 或 `unknown`；`target_state` 只能是 `present`、`missing`、`unavailable`、
  `unchecked` 或 `invalid`。
- fragment 存在时是 `{value, encoding_valid}` 对象。`value` 保留原始编码，
  `encoding_valid` 明确百分号编码及 UTF-8 是否合法；fragment 不参与目标 identity。

来源文件路径是规范 Draft 根相对 POSIX 路径。链接项目公开 `source`、`source_kind`、
`syntax`、从 `1` 开始的 `line`/`column`、`target_kind`、规范或原文 `target`、
`target_scope`、`target_state` 和可选 `fragment`；`column` 按 Unicode 字符计数。
根内目标用规范根相对路径，根外路径和外部 URI 保留原文。

### 目标分类算法

按以下顺序分类，每一步的结果是下一步的输入：

1. 从 CommonMark destination 分离路径和第一个字面 `#` 后的 fragment；`%23` 仍在路径
   中。source bytes 已稳定取得但不是合法 UTF-8 时，按下方稳定内容错误表选择
   `resource.link_invalid`；无法稳定取得 source bytes 或唯一 span 时，事实闭包不完整。
2. 在百分号解码前识别原始路径：先识别 Unix 绝对路径、Windows 盘符路径和 UNC 路径，
   它们都是 `local` 并保留原文；再对其余原始 destination 识别 URI scheme。scheme 必须
   匹配 `^[A-Za-z][A-Za-z0-9+.-]*:`；匹配的目标是 `external`，Draft 不探测或重写其路径。
   纯 `#fragment` 没有路径，直接指向 source 页面自身。
3. 对本地路径（外部 URI 不由 Draft 解码）以及所有 fragment 验证百分号转义完整且解码
   字节为 UTF-8。本地目标路径编码已稳定证明无效时，按下方稳定内容错误表选择
   `resource.link_invalid`，目标 observation 为 `target_scope: unknown`、
   `target_state: invalid`；fragment 的编码合法性单独写入 `fragment.encoding_valid`，
   并按[诊断 code 登记](query-check-and-output.md#诊断-code-登记)中“fragment 的百分号或
   UTF-8 编码已稳定证明非法”这一认证事实选择 `resource.link_invalid`，但不改写已经认证
   的目标存在状态。无法把目标归入合法类别时，按同一登记中“编码合法但目标已稳定证明
   无法归入合法 `page`、`local` 或 `external` 类别”这一事实选择该 code，不能猜成普通
   本地文件。
4. 对相对本地路径，以 source Markdown 所在目录为基准；`/` 与 `\` 都是分隔符。消解
   空段、`.`、`..` 和重复分隔符，得到词法目标；大小写和 Unicode 原样参与 identity，
   不做大小写折叠或 Unicode normalization。
5. 对相对目标完成词法规范化后，目标位于 Draft 根内时，`index.md` 或 `pages/` 下的
   合法页面路径是 `page`；其他根内路径（包括页面命名空间外的 Markdown 和非 Markdown）
   是 `local`。保留的 `pages/index.md` 仍是 `target_kind: page`、`target_state: invalid`，
   不得降级为 local。
6. 规范化后目标的第一个组件若为 `.draft`，分类为受保护内部目标。检查该边界使用最终
   词法目标：`.draft/../pages/a.md` 先规范化为 `pages/a.md`，而 `./.draft/x` 仍受保护。
   受保护目标不进入链接项目、backlinks、计数、mapping、资源包、Trash 或 Restore，只
   形成 `resource.protected_target` Check error。
7. 移动 source 时保持纯 fragment 原文。fragment 只验证编码，不要求标题或锚点存在。

scheme 识别的可观测例子是：`https://x`、`mailto:a@example.com`、`a:b` 为 external；
`1a:b`、`./a:b`、`a%3Ab`、`C:\x` 为 local。百分号编码的冒号不创建 scheme。

### 稳定内容错误与事实不完整

下表逐行给出已经稳定认证的资源事实与机器 code/reason 的唯一配对；四个
`resource.link_invalid` 的 `details.reason` 及其 schema 仍由[诊断 code 登记](query-check-and-output.md#诊断-code-登记)
唯一拥有。本页不扩展该 reason 值域。

| 已认证事实 | 机器选择 | 领域结果与副作用 |
| --- | --- | --- |
| source bytes 已稳定读取，但内容不是合法 UTF-8 | [`resource.link_invalid`](query-check-and-output.md#诊断-code-登记) | 形成资源链接错误；不把内容当作可解析链接，结构写前零写。 |
| 本地目标路径的百分号或 UTF-8 编码已稳定证明非法 | [`resource.link_invalid`](query-check-and-output.md#诊断-code-登记) | 目标 scope/state 按路径编码错误处理，结构写前零写。 |
| fragment 的百分号或 UTF-8 编码已稳定证明非法 | [`resource.link_invalid`](query-check-and-output.md#诊断-code-登记) | 保留可认证的目标 identity 与 fragment 原文/合法性事实；结构写前零写。 |
| 编码合法，但目标已稳定证明无法归入合法 `page`、`local` 或 `external` 类别 | [`resource.link_invalid`](query-check-and-output.md#诊断-code-登记) | 不猜测目标类别，结构写前零写。 |
| 文件、span、source 对齐、mapping 或安全判断所需事实无法完整、稳定取得 | `runtime.closure_incomplete`；读查询仅在[查询错误与阻塞项](query-check-and-output.md#查询错误与阻塞项)中已认证的 `query.source_unavailable` 或 `query.target_unavailable` 条件满足时表达局部未知 | 不把无法取得的事实当作内容非法；所有依赖该闭包的结构写零写并保留现场。 |

### target_scope 与 target_state

身份与 observation 是独立维度；目标缺失不会抹掉已认证的 lexical identity，也不单独
使链接闭包失败。

| 分类 | `target_scope` | 可用 `target_state` | observation 触发事实 |
| --- | --- | --- | --- |
| 根内 `page` 或 `local` | `root` | `present`、`missing`、`unavailable` | 目标存在且类型稳定、已确认不存在、或存在性探测因权限拒绝/普通 I/O/反复变化无法稳定完成。 |
| 根外相对/绝对/盘符/UNC 本地目标 | `outside` | `unchecked` | 不以当前物理位置探测或改变分类。 |
| 外部 URI | `unknown` | `unchecked` | Draft 不探测或重写外部目标。 |
| 路径编码无效 | `unknown` | `invalid` | 百分号转义不完整或解码非 UTF-8，且该非法事实已稳定证明；由[诊断 code 登记](query-check-and-output.md#诊断-code-登记)中的该事实映射。 |
| 编码合法但目标已稳定证明无法归入合法类别 | `unknown` | `invalid` | 由[诊断 code 登记](query-check-and-output.md#诊断-code-登记)中的该事实映射；不能猜为 `local`。 |
| 受保护 `.draft` 目标 | 不形成公开链接项目 | 不形成 | 只产生 `resource.protected_target` Check error。 |

`unavailable` 只在链接身份、来源内容和查询范围都稳定认证，且单次存在性探测因
`permission_denied`、`io_error` 或 `unstable` 无法稳定得到存在/缺失时使用。来源本身
无法稳定读取属于查询的 `query.source_unavailable` blocker；根、authority 或 snapshot
无法认证则整体失败。根内已确认缺失的 `page`/`local` 目标产生 `resource.target_missing`
warning；外部 URI、根外路径和按规则不检查的目标不产生该诊断。

### 链接闭包、改写与副作用

结构操作的链接事实闭包覆盖根内全部可判定 Markdown，而不只覆盖受管理页面。对每个纳入
闭包的 span，必须能一一对应 source bytes、source kind、原目标、fragment 和 mapping
后的目标。mapping 只来自结构 owner 的同一逻辑计划；本页不重新计算第二套 mapping。

当 before path 已在 mapping 中认证、目标 identity 是 `page` 且命中 mapping 时，按 mapping
更新路径并保留可认证 fragment；入站、出站和同批交叉链接与 parent、navigation、collision
检查在同一领域提交中完成。目标 missing、不属于 page、是 local/external 或未命中 mapping
时，不得使用 basename、文本相似度、目录枚举、当前缺失文件或修改时间猜测替代路径。
根外本地目标、绝对路径、盘符和 UNC 永不参与页面 mapping。

| 链接事实 | 允许结果 |
| --- | --- |
| source bytes、CommonMark span、source kind、target identity 和 mapping 均可认证 | 产生链接事实；需要移动且 page 命中 mapping 时同一提交更新路径。 |
| identity 可认证、observation 为 `missing` | 保留 identity；可产生 `resource.target_missing` warning，不把 missing 变成闭包失败。 |
| 根内目标 observation 为 `unavailable` | links/backlinks 项目保留 `unavailable`；Check 以 `query.target_unavailable` blocker 表示不能确定是否应产生缺失诊断。 |
| 规范化后进入 `.draft` | 只产生 `resource.protected_target` Check error，不进入 links/backlinks items 或计数，也不参与任何 mapping/ownership。 |
| source/target/fragment 编码或目标分类已稳定证明非法 | 使用稳定内容错误表中逐项列出的 `resource.link_invalid` 机器 code；任何结构写前失败，零写。 |
| span、source 对齐、mapping 或安全判断所需事实无法完整稳定取得 | 使用 `runtime.closure_incomplete`；任何结构写前失败，零写并保留现场。 |
| 根内 Markdown 无法判定 | 闭包不完整，阻止结构写；不得按 lower-bound 集合继续。 |

结构写在完整闭包、mapping 和所有需改写 span 均认证前保持零写。提交后耐久性不确定时
runtime 保留 recovery 现场；重试重新读取闭包和 observation，不以缓存、索引或旧事实代替
认证。读操作不会借资源观察修改页面或链接。

## 资源所有权

`D-RESOURCE-OWNERSHIP` 定义页面子树、结构操作和 Trash/Restore 对物理节点的窄边界；它
不把邻接关系变成页面所有权。

### 输入、输出与成员分类

- 输入是受管理页面子树、相邻非 Markdown 节点、目录物理状态、结构 owner 已认证的
  mapping，以及 Trash/Restore 所需的授权事实。
- 输出只说明受管理 Markdown 的领域所有权、可证明的目录清理决定或拒绝；不输出附件
  清单、retained facts、计数、摘要或续读协议。
- 页面子树只拥有受管理 Markdown。图片、附件、普通文件、未知节点和目录不会因物理
  邻接转移到页面所有权；目录节点只有在 `D-MOVE` 已认证为 retained 的分区才原位保留。

| 物理事实 | 操作 | 领域结果与副作用 |
| --- | --- | --- |
| 受管理 Markdown 且 mapping 可证明 | 结构移动、Trash、Restore | 只处理该 Markdown 及已授权的页面头部、身份/摘要、关系和导航事实。 |
| D-MOVE 已认证 retained 的非 Markdown、附件或可认证存在但类型未知节点 | 目录 move 或页面所有权操作 | 原位保留；不进入页面 package、mapping 或成功输出。 |
| 非 Markdown、附件、无法分类节点作为 file source，或目录事实不可信 | D-MOVE source 分类 | 写前拒绝、零写；不得降级成 retained 或创建页面所有权。 |
| candidate 根的 unmanaged Markdown | D-MOVE candidate 纳管分支且 mapping/闭包/parent/nav 可证明 | 只按同一 move mapping 取得页面所有权；不由本节补齐页面协议字段。 |
| registered 根的 unmanaged source（directory 的 `file`/`prefix` target 也包括） | D-MOVE | 拒绝、零写；不得创建/改写页面、导航、catalog/head 或日志副作用。 |
| root state unknown、mixed 或不可认证，或 mapping/链接闭包不可认证 | D-MOVE 结构/所有权路径 | 写前拒绝、零写并保留诊断；不得解释为 candidate、registered 或空集合。 |
| 目录可证明物理为空 | 结构或恢复后清理 | 可按已证明的结构清理规则移除目录；平台调用由 runtime 处理。 |
| 目录非空，或存在 retained/unknown 节点 | 目录清理 | 保留目录；不能因页面已移动推断可删除。 |
| 目录边界、节点分类或物理空性无法认证 | 目录 move 或清理 | 写前拒绝、零写并保留诊断；不得先移动页面再决定保留。 |
| `.draft` 内部路径 | 任一页面所有权操作 | 不属于页面子树，不能作为页面 source/target 或被纳入页面所有权。 |

目录移除、Trash/Restore 授权清单、handoff 和 durable cleanup 只有对应 owner 完成证明后
才能发生；物理目录存在本身不证明 authority。所有不完整事实都在写前拒绝并保留现场。

### 恢复与验证边界

所有权成功转换的提交点与触发它的结构或 Trash/Restore 逻辑操作相同，不能创建第二个可见
operation。D-MOVE recovery 对 mapping/closure 只消费
[D-MOVE snapshot S 语义载荷](structure.md#d-move-snapshot-s-语义载荷) 与 runtime
[恢复门禁与漂移分类](../runtime/recovery.md#d-move-snapshot-s-恢复门禁与-q6-b-漂移分类)；
本页不独立分类 snapshot 或选择 recovery action。提交后耐久性不确定时保留现场并等待
显式 recovery；重试重新证明物理空目录和授权事实。

验证目录中混有 Markdown、图片、附件、未知节点、空目录和 `.draft` 路径的组合：只有受
管理 Markdown 可进入领域 after-state，只有已证明物理为空的目录可清理；成员级
`uniform-managed`/`uniform-unmanaged` 可进入结构矩阵，`mixed`/`unknown`/`unverifiable`
写前拒绝，`no-movable` 走既有错误分区。还必须验证 UTF-8、CommonMark、fragment、source
relative base、两种分隔符、dot segment、大小写/Unicode、managed/unmanaged source、
missing identity、根外/绝对本地目标、URI scheme、目标 invalid/unavailable、规范化前后
跨越 `.draft` 边界和任一闭包节点不可判定时的零写。

## 来源

- `33aeaec60a34833ce8b686bfdd7e50b8dc9932d0:.scratch/draft-authority-spec-migration/migration-evidence.md#迁移单元`
- `33aeaec60a34833ce8b686bfdd7e50b8dc9932d0:.scratch/draft-authority-spec-migration/signed-decisions-transcript.md#已签署决定迁移摘要`
