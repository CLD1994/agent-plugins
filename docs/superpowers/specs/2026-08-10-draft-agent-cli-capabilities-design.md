# Draft 面向 Agent 的查询与安全维护能力设计

日期：2026-08-10

状态：已批准

## 1. 背景

Draft runtime 当前能够初始化、接管、检查和调整草稿结构，但 Agent 仍需直接读取大量
Markdown 或手工修改机器区域，才能回答或完成以下常见任务：

- 查看一个页面的结构位置、状态和引用数量；
- 查找正文链接、反向链接和正式关系；
- 查找仍可恢复的回收项；
- 安全创建页面；
- 修改状态和成对关系；
- 在真正写入前查看结构操作会影响哪些文件；
- 在讨论结束时生成供设计文档使用的结论汇总。

Obsidian CLI 证明了把这些能力拆成窄命令的价值，但 Draft 面向的是 Agent 驱动的递归
讨论，而不是通用笔记管理。直接引入 Obsidian Headless 不能替代 Draft 的状态、关系、
索引树、操作恢复和回收语义。本设计只借鉴“一个命令回答一个问题”的组织方式，不引入
Obsidian 运行时依赖，也不复制其完整命令体系。

## 2. 设计目标

本次设计要做到：

1. 为 Agent 提供窄而稳定的结构查询，避免一次输出整个 Draft。
2. 让查询、检查和写命令共享同一套状态、关系和链接规则。
3. 以稳定代码作为 CLI 和 JSON 契约，中文只存在于 Markdown 与人类输出边界。
4. 为常见状态和关系修改提供安全命令，减少手工维护成对关系和派生结构。
5. 让正式执行与 `--dry-run` 使用同一份修改计划。
6. 将 `conclusions.md` 改为讨论结束时按需生成的机器产物，而非实时维护的索引。
7. 保持 Markdown 文件是唯一事实来源，不引入数据库或跨命令持久缓存。
8. 控制 Agent 的输出 token 消耗，并让空结果、未知状态和失败含义明确可判定。

## 3. 非目标

首版不实现：

- Obsidian Headless、Obsidian CLI 或 NotesMD CLI 运行时集成；
- 持久查询缓存、数据库、后台索引或文件监视器；
- `inspect`、`query pages`、`children`、`orphans`、`deadends`；
- 正文全文搜索、标签查询、kind 查询或通用查询语言；
- Markdown 标题锚点验证；
- 外部网址联网可用性检查；
- 多语言切换和 locale catalog；
- 旧 Draft 格式迁移或向后兼容层；
- 候选模式下的结构修改和映射驱动的批量链接重写；
- 检查器建议动作和单页多解析错误聚合。

最后两项已经确认需要后续独立设计：候选迁移工具合并讨论候选结构操作与路径映射重写；
诊断改进合并可执行建议与安全的多错误收集。本设计不提前实现推测性的扩展接口。

## 4. 总体方案

采用“现有 Draft 核心内的共享能力”方案。

Markdown 文件继续作为唯一事实来源。每条命令只读取回答当前问题所需的页面，并在内存中
建立一次 `DraftSnapshot`。同一命令内的查询、校验和修改规划复用这份快照；命令结束后
快照丢弃，两次独立命令之间不共享缓存。

核心职责分成六部分：

1. **统一解析**：识别页面状态、正式关系、索引导航和正文资源链接。
2. **稳定领域代码**：用有明确取值范围的类型表达状态、关系、资源类型、范围和目标状态。
3. **查询服务**：把页面或快照转换成紧凑、稳定的查询结果。
4. **修改规划**：把 create、状态、关系和结构命令转换成统一的公开文件影响。
5. **结论生成器**：仅在显式命令调用时，从当前已确认页面重建结论页。
6. **安全执行**：继续使用现有锁、操作日志、原子替换、回滚和崩溃恢复能力。

查询服务不直接拼接 Markdown，CLI 不重新解释链接，检查器也不维护另一套字符串列表。
解析结果是三者共同使用的事实。

## 5. 稳定代码与本地化边界

### 5.1 状态代码

核心增加稳定的 `StatusCode`，作为解析结果、命令参数和 JSON 的唯一机器状态类型：

| 稳定代码 | 当前 Markdown 标签 |
|---|---|
| `pending` | `待讨论` |
| `proposed` | `当前提案` |
| `confirmed` | `已确认` |
| `superseded` | `已取代` |
| `closed` | `已关闭` |

Markdown 解析器在边界上把当前中文标签映射为 `StatusCode`；序列化时做反向映射。状态说明
仍是自由文本，不属于稳定代码。

### 5.2 关系代码

核心增加稳定的 `RelationCode`：

| 稳定代码 | 当前 Markdown 标签 |
|---|---|
| `parent` | `父主题` |
| `depends_on` | `依赖` |
| `supersedes` | `取代` |
| `superseded_by` | `被取代` |
| `challenges` | `质疑` |
| `challenged_by` | `被质疑` |

`output`/`产出` 从格式、解析、检查、命令和测试中删除。页面是否进入结论汇总，只由该页面
当前是否为 `confirmed` 决定。

### 5.3 结果类型与计划操作代码

成功和失败 JSON 的 `result_type` 是有限、稳定、会影响调用方分支的机器协议。首版合法值
为：

- `init`、`adopt`、`check`、`move`、`promote`、`collapse`、`trash`、`restore`、`recover`；
- `query.page`、`query.links`、`query.backlinks`、`query.relations`、`query.status`、
  `query.recoverable`；
- `plan`、`create`、`status.set`、`relation.add`、`relation.remove`、
  `generate-conclusions`、`error`。

预演信封的 `operation` 同样是稳定命令代码，只允许本设计中支持 `--dry-run` 的九种命令
值。状态、关系、资源事实、`result_type` 和 `operation` 的合法值都必须各自只有一个生产
代码权威来源；序列化器和命令分派从这些有约束的类型取值，不在各分支手写同一组字面量。

### 5.4 测试数据来源

状态、关系、结果类型和计划操作的完整集合只在生产类型中定义。测试遍历这些类型构造
用例，不再维护 `EXPECTED_CODES`、`VARIANT_CODES` 等平行字符串集合。测试可以精确断言
单个稳定协议值，但不得另建一份完整合法值清单；中文人类文案只验证渲染关系、事实、
顺序、计数、转义和单行安全。

## 6. 页面参数与正文链接是两类路径

受管理页面参数使用严格、稳定的机器路径：

- 相对于 Draft 根目录；
- 使用 `/`，与当前操作系统无关；
- 不能包含 `.`, `..`、空路径段或绝对路径；
- 必须解析为 Draft 内受管理页面。

正文链接不受这项限制。正文需要支持作者实际写下的本地或外部资源，包括 Draft 外路径。
正式关系仍只能指向同一 Draft 内的受管理页面。

本设计新增命令中，凡是要求操作数指向一个现存受管理页面，均使用稳定错误码
`page.not_found` 表示“路径语法本身合法，但没有标识当前受管理页面”，退出码为 2。适用
范围包括 `query page`、`query links`、`query relations` 的 `PAGE`，`status set` 的
`PAGE`，以及 `relation add/remove` 的 `SOURCE` 和 `TARGET`。绝对路径、反斜杠、`.`、
`..`、非 Markdown 等语法错误仍使用相应路径错误，不折叠为 `page.not_found`。

`query backlinks` 的 `TARGET` 是宽松资源身份，不要求目标存在，因此不存在
`page.not_found` 分支。现有结构命令的 `structure.unknown_page` 保持不变；新命令不得为了
复用实现而把该结构操作专用代码泄漏到查询、状态或关系协议。`page.not_found` 作为稳定
机器协议值必须只有一个生产代码权威来源。

## 7. 正文资源解析

### 7.1 支持的目标

普通正文链接和图片语法共同参与资源解析，代码块中的链接继续忽略。首版识别：

- POSIX 风格相对路径；
- Windows 风格相对路径，如 `..\\x`、`.\\x`、`dir\\x`；
- 混合使用 `/` 与 `\\` 的相对路径；
- Linux 绝对路径；
- Windows 盘符绝对路径；
- Windows UNC 路径；
- Windows 盘符相对路径，如 `C:foo`；
- 外部 URI。

Windows 风格相对路径按当前页面所在目录解析，并在当前平台检查时映射为平台分隔符。
Windows 盘符相对路径依赖进程的盘符工作目录，不能可靠解析，因此保留原链接并标记为
`unavailable`。

### 7.2 资源事实

`query links` 的每个资源项使用以下稳定字段：

```json
{
  "target": "../assets/image.png",
  "kind": "local",
  "scope": "outside",
  "target_state": "present"
}
```

字段取值：

- `kind`：`page`、`local`、`external`；
- `scope`：`draft`、`outside`、`unknown`；
- `target_state`：`present`、`missing`、`unavailable`、`unchecked`、`invalid`。

分类矩阵如下；先按词法目标确定 `kind` 和 `scope`，再探测 `target_state`：

| 词法目标类别 | `kind` | `scope` | `target_state` |
|---|---|---|---|
| 页面命名空间中的普通文件 | `page` | `draft` | `present` |
| 页面命名空间中不存在的目标 | `page` | `draft` | `missing` |
| 页面命名空间中含符号链接或不是普通文件的目标 | `page` | `draft` | `invalid` |
| 其他可解释的 Draft 内本地目标 | `local` | `draft` | `present` 或 `missing` |
| 其他可解释的 Draft 外本地目标 | `local` | `outside` | `present` 或 `missing` |
| 当前平台不能解释的本地目标 | `local` | `unknown` | `unavailable` |
| 写法本身不能形成合法本地目标 | `local` | `unknown` | `invalid` |
| 外部 URI | `external` | `unknown` | `unchecked` |

资源类型按词法身份判定，不能依赖目标当前是否存在。Draft 内受管理页面命名空间精确定义
为规范根相对路径 `index.md`、`conclusions.md` 和 `pages/**/*.md`。解析到这个命名空间的
目标一律使用 `kind: "page"`、`scope: "draft"`：普通文件存在时为 `present`，不存在时为
`missing`，路径包含符号链接或目标存在但不是普通文件时为 `invalid`。因此，同一链接不会
因为目标文件稍后出现或消失而在 `page` 与 `local` 之间改变身份。

根目录中除 `index.md`、`conclusions.md` 外的 Markdown、`pages/` 外的 Draft 内 Markdown
以及 Draft 外 Markdown 都是 `kind: "local"`；其 `scope` 分别按词法位置为 `draft` 或
`outside`。图片、普通附件和目录也使用 `kind: "local"`，不增加文件类型细分。
`conclusions_state` 不改变资源的词法类型：尚未生成的 `conclusions.md` 仍可作为
`page`/`draft`/`missing` 的正文资源事实。

`target` 去掉锚点，机器输出中的分隔符统一为 `/`；身份比较采用词法解析后的资源位置，
不通过 `realpath` 合并符号链接。多个写法解析到同一资源时只返回一次，输出顺序和展示值
采用该资源第一次出现的位置及其规范化目标。

同一文件的不同锚点属于同一资源。首版不验证锚点，因为不同 Markdown 渲染器的标题转锚
规则不同，近似判断容易误报。

### 7.3 跨平台存在性

本机能够解释的本地路径才检查存在性：

- 能确认存在：`present`；
- 能确认不存在：`missing`；
- 链接写法不能形成合法目标：`invalid`；
- 当前平台不能可靠解释：`unavailable`；
- 外部 URI 不联网检查：`unchecked`。

例如 Linux 上的 Windows 绝对路径为 `unavailable`，不是 `missing`。`check` 将本机确认的
`missing` 和 `invalid` 作为错误，将 `unavailable` 作为警告；外部 URI 的 `unchecked` 不
产生警告。

### 7.4 结构移动时的正文链接

绝对路径在页面移动时保持原文不变。相对路径重新计算，使其继续指向移动前的同一词法
目标；如果必须改写，尽量保留原链接的主要分隔符风格。路径可以在 Draft 根目录之外，
不能因为越过根目录就拒绝正文链接。第 12.5 节定义的机器生成结论列表是例外：它允许
暂时过时，只在显式生成时整体重建，普通结构命令不逐条改写。

## 8. 查询命令

所有查询位于 `draft query` 下，不提供聚合式 `inspect`。

`query page`、`query links` 和 `query relations` 的 `PAGE` 使用第 6 节的严格受管理页面参数
规则，并要求目标页面存在。`query backlinks` 的 `TARGET` 使用正文资源身份规则，可以是
Draft 内受管理页面、普通本地资源、Draft 外路径或外部 URI。两类参数不得复用同一个严格
页面校验器。

### 8.1 `query page`

```bash
draft query page ROOT PAGE
```

返回一个用于路由的固定摘要，不返回正文或列表：

```json
{
  "result_type": "query.page",
  "path": "pages/a.md",
  "title": "标题",
  "status": "proposed",
  "parent": "index.md",
  "child_count": 0,
  "link_count": 3,
  "backlink_count": 2,
  "outgoing_relation_count": 1,
  "incoming_relation_count": 4
}
```

`link_count` 与 `query links` 的去重资源总数一致。关系计数与相应方向的关系查询总数一致，
包括 `parent` 等实际机器区域声明。根页面的 `parent` 为 `null`。

因为反向链接和入站关系计数必须查看其他页面，`query page` 扫描整个 Draft；不能可靠完成
全局扫描时整体失败。

### 8.2 `query links`

```bash
draft query links ROOT PAGE
```

只解析目标页面，返回第 7 节定义的去重正文资源。机器区域中的正式关系不进入此结果。

目标资源的 `missing`、`unavailable`、`unchecked` 或 `invalid` 是成功查询到的事实，不使
查询本身失败。

### 8.3 `query backlinks`

```bash
draft query backlinks ROOT TARGET
```

`TARGET` 可以是受管理页面、本地文件或目录、Draft 外本地路径或外部 URI。命令行中的
相对目标以 Draft 根目录为基准；每个来源页面中的相对链接仍先以该来源页面为基准解析，
再按资源身份比较。

结果项仅为去重后的来源页面规范路径。查询扫描整个 Draft；任一所需页面不可读或不可
解析时整体失败。

### 8.4 `query relations`

```bash
draft query relations ROOT PAGE --direction incoming
draft query relations ROOT PAGE --direction outgoing
```

`--direction` 必填，首版不提供关系类型筛选。单项结果为：

```json
{"relation":"depends_on","page":"pages/background.md"}
```

- `outgoing`：当前页面声明的关系，`page` 是声明指向的目标；
- `incoming`：其他页面指向当前页面的声明，`page` 是声明来源；
- `relation` 始终保留源码中该条声明的稳定方向，不在查询时改写。

出站查询只解析目标页面。入站查询扫描整个 Draft，无法完整扫描时整体失败。

### 8.5 `query status`

```bash
draft query status ROOT proposed
```

每次只接受一个 `StatusCode`，结果项仅为规范页面路径。查询覆盖所有当前受管理页面，按
路径排序。首版不提供多状态并集、父路径或 kind 筛选。

所有权状态为 `generated` 时，`conclusions.md` 对查询是普通受管理页面：`query status`
按其页面讨论状态包含它，`query page` 返回的父页面是 `index.md`，其他查询也不设置特殊
排除。所有权状态只改变结论生成和检查规则。尚未生成且文件不存在时，它不是可查询的
现存 `PAGE`，但正文链接仍按第 7.2 节返回 `page`/`draft`/`missing`。

### 8.6 `query recoverable`

```bash
draft query recoverable ROOT
```

只返回当前仍可由 `restore` 恢复的回收项，不返回已过期历史、待移交项或其他操作日志：

```json
{
  "operation_id": "01…",
  "page": "pages/a.md",
  "restore_expires_at": "2026-08-17T12:00:00Z"
}
```

命令编排开始时只捕获一次 UTC 当前时间。同一个时间值必须同时传给生命周期前置推进和
可恢复项过滤，并原样序列化为结果信封的 `as_of`；不能让两个步骤各自读取时钟。排序先按
最早到期时间，再按 `operation_id`。当 `as_of` 恰好等于到期时间时，该项目已不可恢复，
不进入结果。

如果任何候选日志、权威删除包或期限字段损坏，以致无法证明结果完整，查询整体失败，
不返回部分项目。

### 8.7 集合查询的分页

`links`、`backlinks`、`relations`、`status` 和 `recoverable` 使用统一分页：

- 默认 `limit` 为 20；
- 最大 `limit` 为 100；
- 支持非负 `offset`；
- 支持 `--count-only`；
- 不提供 `--all`。

信封包含 `total`、`offset`、`limit`、`has_more`、`next_offset` 和 `items`。所有集合查询和
预演使用同一套字段计算规则与序列化边界。

`--count-only` 只把 `items` 替换为空数组；`total`、`offset`、`limit`、`has_more` 和
`next_offset` 与相同请求不加 `--count-only` 时完全一致。后两个字段描述请求窗口在完整
结果中的位置，不根据实际输出的空 `items` 重算。

### 8.8 稳定排序

- 状态：规范页面路径；
- 正文资源：目标第一次在页面出现的顺序；
- 反向链接：来源路径，再按声明位置；
- 出站关系：当前页面声明顺序；
- 入站关系：来源路径，再按声明位置；
- 可恢复项：到期时间，再按操作编号。

空结果成功并返回总数零。只有无法可靠回答请求时才使用非零退出码。

## 9. 安全创建页面

```bash
draft create ROOT PAGE --title TITLE --status pending
```

首版只创建叶子页面。`PAGE` 必须：

- 以 `pages/` 开头并以 `.md` 结尾；
- 使用 `/`；
- 不含 `.`, `..`、空路径段；
- 文件名不是 `index.md`；
- 不指向根级文件、`conclusions.md` 或 `.draft/`。

父页面只由路径推导：

- `pages/topic.md` 的父页面是 `index.md`；
- `pages/hub/child.md` 的父页面是 `pages/hub/index.md`。

父页面必须存在，目标页面必须不存在。命令不自动创建缺失枢纽，不提供 `--parent`、正文
模板、追加、覆盖或打开文件功能。

`--title` 是非空单行普通文本。换行和控制字符无效；自然语言、空格和常见标点允许。
CLI 在一级标题和父索引 Markdown 链接中正确转义语法字符，查询返回解析后的标题文本。

初始 `--status` 必填，只允许 `pending` 或 `proposed`。空白新页不能直接使用
`confirmed`、`superseded` 或 `closed`。

页面文件和父索引导航在同一次安全操作中更新。新链接追加到父页面 `## 子主题` 末尾；
如果根索引已有机器管理的结论链接，新页面插在该链接之前，使结论链接继续保持最后。

## 10. 修改页面状态

```bash
draft status set ROOT PAGE CODE
```

`CODE` 接受第 5.1 节的五个稳定状态代码。状态不是固定单向流程：已确认页面可以回到
提案，已关闭页面也可以重新待讨论。命令只要求修改后的最终结构满足不变量。

例如：

- `superseded` 页面必须至少存在一条 `superseded_by`；
- 仍有 `superseded_by` 的页面不能单独改为其他状态；
- 根索引和枢纽仍需满足其导航页面约束。

状态后的自由说明默认保留。命令提供互斥选项：

```text
--note TEXT
--clear-note
```

它们分别替换和删除说明。说明可以使用任何语言。

状态命令不推断或自动增删正式关系；需要建立或解除取代关系时使用关系命令。命令支持
`--dry-run`。

`conclusions.md` 进入 `generated` 所有权状态后，也可以通过该命令由 Agent 明确设为
`confirmed`。这表示 Agent 已经根据最新生成的结论页产出了设计文档，不表示结论页会被
实时同步。

## 11. 修改正式关系

```bash
draft relation add ROOT SOURCE CODE TARGET
draft relation remove ROOT SOURCE CODE TARGET
```

修改命令只接受三个主动方向：

- `depends_on`；
- `supersedes`；
- `challenges`。

`parent` 由页面路径和索引结构维护。`superseded_by`、`challenged_by` 仍是解析和查询所需
的稳定代码，但不能作为修改参数；调用者通过交换 SOURCE 与 TARGET 表达同一操作。

`depends_on` 只写在来源页面；哪些页面依赖目标由入站查询计算。`supersedes` 和
`challenges` 自动维护目标页面的反向声明。

关系命令是幂等的：

- 添加已经完整存在的关系成功且不修改文件；
- 删除已经不存在的关系成功且不修改文件；
- 双向关系只存在一边时，`add` 补齐，`remove` 清理残留；
- 不能得到合法最终状态时整体失败。

自引用、依赖环和取代环仍然禁止。

### 11.1 添加取代关系

```bash
draft relation add ROOT pages/new.md supersedes pages/old.md
```

该命令在一次操作中：

1. 在新页面加入 `supersedes`；
2. 在旧页面加入 `superseded_by`；
3. 将旧页面状态改为 `superseded`。

结论页不是实时索引，因此该命令不刷新结论条目。下一次显式生成会按当前状态重建。

### 11.2 删除取代关系

删除旧页面的最后一条取代关系时，CLI 不能猜测它应恢复成什么状态，调用者必须提供：

```bash
draft relation remove ROOT pages/new.md supersedes pages/old.md --status proposed
```

允许的新状态是 `pending`、`proposed`、`confirmed` 或 `closed`。如果旧页面仍被其他页面
取代，则保持 `superseded`，不需要 `--status`。

关系、反向关系和必要状态在同一次操作中更新。两个关系命令都支持 `--dry-run`。

## 12. 按需生成结论页

### 12.1 两套不同的状态

结论页存在两套不能混淆的状态：

1. `.draft/draft.json` 中的所有权状态 `conclusions_state`：
   `not_generated` 或 `generated`；
2. `conclusions.md` 页面自身的讨论状态：通常由生成命令写成 `proposed`，设计文档产出后
   由 Agent 明确改成 `confirmed`。

所有权状态只会从 `not_generated` 前进到 `generated`，不会回退。

### 12.2 初始化与接管

`init` 不创建 `conclusions.md`，登记状态为 `not_generated`。候选目录也不允许预先存在
`conclusions.md`；`adopt` 后同样从 `not_generated` 开始。候选目录可以已有
`confirmed` 页面，接管不会自动生成结论页。

正式登记标记的键集合精确为 `{"format_version","conclusions_state"}`。接管中的临时
标记键集合精确为
`{"format_version","conclusions_state","adopt_state","candidate_manifest"}`，其中
`conclusions_state` 只能是 `not_generated`。旧的 `{"format_version":1}` 标记有意按
`format.invalid` 拒绝；本次格式变化不提供迁移、双读或兼容分支，即使
`FORMAT_VERSION` 仍保持为 `1`。

### 12.3 显式生成

讨论结束并与用户确认后，Agent 必须执行：

```bash
draft generate-conclusions ROOT
```

如果状态为 `not_generated` 且当前没有 `confirmed` 页面，命令成功但不创建文件、不添加
根导航、不改变所有权状态：

```json
{
  "result_type": "generate-conclusions",
  "generated": false,
  "reason": "no_confirmed_pages",
  "changed_paths": []
}
```

存在已确认页面时，命令把所有权状态推进到 `generated`，创建结论页并维护根导航。成功
结果使用：

```json
{
  "result_type": "generate-conclusions",
  "generated": true,
  "changed_paths": ["conclusions.md", "index.md"]
}
```

`changed_paths` 只列出本次实际改变的公开文件；后续重建时可以只包含其中一个或为空。

如果所有权状态仍为 `not_generated`，但磁盘上意外出现 `conclusions.md`，生成命令必须
拒绝覆盖。此时 CLI 不能证明该文件由机器创建，应先由用户决定保留、改名或移除。

### 12.4 生成格式

每次生成都完整覆盖机器管理内容，并把页面状态写为 `proposed`：

```markdown
# 结论汇总

> 状态：`当前提案`
>
> 父主题：[根索引](index.md)

## 已确认结论

- [页面标题](pages/example.md)
```

条目只包含标题和链接，不复制正文，不生成摘要。结论页自身不进入列表。

排序按根索引和次级索引中的导航顺序做深度优先遍历，最终输出为扁平列表。导航结构不
合法时生成失败，并提示先运行 `check`；不能退回按路径排序的近似结果。

根索引 `## 子主题` 中的结论链接由生成器管理：首次生成时在末尾加入；以后保持唯一、
规范且始终位于最后。

一级标题、页面状态、父主题、章节标题、列表形状和根导航结论链接共同组成一套固定机器
产物形状。生成、检查、页面创建和结构操作必须依据同一套形状定义识别它，不能分别复制
标题或链接字面量后各自判断。

### 12.5 非实时维护

`conclusions.md` 只用于生成设计文档前的最终汇总，不承诺日常实时准确：

- create、move、promote、collapse、trash、restore、状态、关系和正文编辑不刷新条目；
- 结构命令不改写结论列表中的旧链接；
- 旧标题、旧路径、已删除页面或遗漏新结论都允许暂时存在；
- `check` 不因为结论条目与当前 Draft 不同步而报错；
- 结论页旧链接不阻止 move 或 trash；
- 下一次讨论结束时重新运行生成命令，统一覆盖为当前事实。

每次显式运行生成命令都会把结论页状态重置为 `proposed`。Agent 根据这份最新汇总生成
设计文档后，再用 `draft status set` 将它改为 `confirmed`。以后重新讨论时不需要立即降级
或维护；下次生成自然开始新的提案周期。

如果所有权状态已经是 `generated`，即使当前已确认页面数量变为零，生成命令仍保留结论
页和根导航，重建为空列表并写成 `proposed`。文件被误删时，生成命令按确定内容重建。

生成操作是幂等的：对同一输入状态连续执行两次，第二次不产生公开文件变化。若执行前
页面状态为 `confirmed`，第一次执行会按命令语义重置为 `proposed`，随后再次执行无变化。

### 12.6 检查边界

`check` 不比较结论条目是否与当前确认页面集合一致，但仍检查机器产物的基本结构：

- `not_generated` 时意外出现 `conclusions.md`；
- `generated` 时文件缺失；
- 根导航中的结论链接缺失、重复、位置或目标错误；
- 固定标题、机器区域或列表形状损坏。

`generated` 状态下的缺失、根导航漂移或文件结构损坏可以通过重新运行生成命令修复。
`not_generated` 状态下的意外文件不能自动覆盖，必须先由用户处理。结论列表内允许过时
的目标不进入普通正文断链错误，也不成为结构修改的入站阻碍；显式 `query links` 仍可按
文件当前内容返回其资源事实。

## 13. `--dry-run` 与公开修改计划

以下命令支持 `--dry-run`：

- `create`；
- `move`；
- `promote`；
- `collapse`；
- `trash`；
- `generate-conclusions`；
- `status set`；
- `relation add`；
- `relation remove`。

`init`、`adopt`、`restore` 和 `recover` 不支持。它们分别涉及首次登记、接管、恢复保留
数据或故障恢复，不承诺普通业务写命令的预演语义。

预演与正式执行调用同一个计划构造器。公开 `PlanEffect` 只有四类：

```json
{"action":"create","path":"pages/a.md"}
{"action":"modify","path":"index.md"}
{"action":"move","from":"pages/a.md","to":"pages/b.md"}
{"action":"remove","path":"pages/a.md"}
```

预演信封包含：

```json
{
  "result_type": "plan",
  "operation": "move",
  "effects": [],
  "total": 0,
  "offset": 0,
  "limit": 20,
  "has_more": false,
  "next_offset": null
}
```

影响项默认 20、最大 100，支持 `offset` 和 `--count-only`，不提供 `--all`。移动必须表现为
一个 `move`，不能显示成创建加删除。

写命令的 `--limit`、`--offset` 和 `--count-only` 只与 `--dry-run` 一起合法；正式执行携带
任一分页选项时以退出码 2 拒绝。预演使用 `--count-only` 时只把 `effects` 置为空数组，
`total`、`offset`、`limit`、`has_more` 和 `next_offset` 与相同请求不加该选项时完全一致，
命令必要的其他业务事实仍保留。

计划不公开文件内容、diff、哈希、锁、日志、回收包、低层临时文件或 `.draft/` 路径。
幂等无变化时 `effects` 为空；命令可以附带必要业务事实，例如
`reason: "no_confirmed_pages"`。

如果无法构造完整计划，预演整体失败，不返回部分计划。`--dry-run` 不执行当前业务计划，
但仍按第 15 节静默推进已登记 Draft 的回收生命周期。

`--dry-run` 不获取新的业务写锁，也不忽略、接管或恢复已有业务写锁。生命周期前置推进后
如果仍存在写锁或未完成操作，使当前快照和计划的完整性无法证明，预演以
`operation.recovery_required` 和退出码 3 整体失败，不返回部分效果。调用者必须先显式运行
恢复流程，再重新预演。

## 14. 正式成功输出

默认输出继续是简短中文，供人查看。Agent 应显式使用 `--json`。成功 JSON 只包含：

- `result_type`；
- 当前命令必要的业务事实；
- 公开受管理文件的 `changed_paths`。

成功结果不重复 `ok: true`，不返回成功时恒定的 `phase`，不公开普通操作的内部编号、锁、
日志或静默生命周期信息。

所有公开 `changed_paths` 都从公开语义效果或等价的公开结果模型派生，不能从底层日志的
mutation 路径集合直接生成，也不能以过滤 `.draft/` 前缀作为权威规则。日志 mutation 是
原子执行、回滚和恢复的存储事实，可能包含登记标记、回收包和 payload 等内部路径。

对于支持 `--dry-run` 的命令，正式执行的 `changed_paths` 必须等于同一份未分页
`PlanEffect` 按顺序去重后的公开路径：`create`、`modify`、`remove` 贡献 `path`，`move`
依次贡献 `from` 和 `to`。预演分页只影响本次显示的 `effects`，不能改变正式结果。正式执行
和崩溃恢复如何携带或持久化这些公开语义属于实现选择，但不得回退到从内部路径猜测。

`trash` 是例外：保留 `operation_id` 和 `restore_expires_at`，因为后续 `restore` 必须使用。
如果故障恢复需要定位某次内部操作，错误结果再提供相应编号。

`check` 保留 `ok`，因为它会在完成检查后以退出码 1 返回包含错误的有效报告。

## 15. 静默推进回收生命周期

所有针对已登记 Draft 的命令在处理自身任务前静默推进回收项生命周期，包括：

- `check`；
- 所有 `query`；
- 所有写命令及其 `--dry-run`；
- `restore`；
- `generate-conclusions`；
- `recover`。

帮助、版本、`init`、`check --candidate` 和登记完成前的 `adopt` 不执行。

这里的“已登记”不是根据命令名称、`.draft/` 目录或残留操作日志推测，而是必须先以只读方式
确认第 12.2 节定义的正式登记标记有效。是否允许前置推进按下表判定：

| 根与登记标记状态 | 是否扫描或推进生命周期 | 后续行为 |
|---|---|---|
| 根有效，且正式登记标记精确有效 | 是，恰好一次 | 再执行命令自身逻辑 |
| 根或标记缺失、无效、不支持，或标记仍为临时接管形状 | 否 | 交给命令自身的既有根、格式或恢复验证产生结果 |
| 帮助、版本、`init`、`check --candidate`、登记前 `adopt` | 否 | 直接执行该命令原有逻辑 |

因此，未登记目录即使残留 `.draft/operations` 或 `.draft/trash` 也不得被前置推进读取、清理
或修改。资格检查本身不得把 `check` 的 `format.missing` 等有效检查报告改成另一种错误信封；
实现可以复用同一次标记解析结果，但不能为了避免重复读取而提前产生生命周期副作用。

推进语义：

1. 成功推进不出现在人类或 JSON 输出中。
2. 临时移交失败保留原状态，当前命令继续；下次命令重试。前置推进本身不向普通命令附加
   警告或进度。
3. 内部记录损坏、无法判断怎样安全推进时，停止当前命令并明确报错。
4. 查询和检查观察推进后的状态。
5. `--dry-run` 只阻止当前业务计划，不能阻止这项内部生命周期维护。

`check` 是诊断命令，因此在前置推进成功后仍独立检查当前持久化操作状态，并可以报告
`trash.retained`、`trash.handoff_pending` 等检查问题。这些是当前状态的检查事实，不是
生命周期前置推进的活动记录；删除旧生命周期 warning 汇总时不得一并删除。

任何命令（包括 `check`）的前置推进若遇到损坏或需要恢复的内部状态，都不进入自身业务
逻辑。JSON 只返回标准失败信封 `ok: false`、`result_type: "error"` 和结构化 `error` 事实，
退出码为 3；不得返回部分 `check` 报告、`lifecycle_results` 或此前已成功推进的内部明细。
只有诊断确实需要定位内部操作时，`error` 才携带相应 `operation_id`。

公开 `draft recover ROOT` 的职责收紧为修复崩溃或中断留下的未完成写操作。回收到期与移交
结果不进入它的公开输出；无法安全恢复的操作仍提供明确诊断。

## 16. 写入一致性和失败语义

写命令必须先完成必要读取、验证最终结果并构造完整计划，然后才开始写入。状态、关系、
导航、文件移动和生成器拥有的结构不能只完成一部分。并发修改、文件系统错误或崩溃继续
由现有锁、日志、回滚和恢复路径处理。

命令只要求完成自身任务所需的数据可靠：

- move 必须扫描所有可能引用被移动页面的来源；
- 关系命令必须能够验证配对、自引用和环路；
- create 必须可靠解析目标路径、父页面和父导航；
- generate-conclusions 必须可靠读取当前状态和完整导航树；
- 已知但不影响计划完整性的外部 URI 未检查、本地跨平台路径不可用等事实不阻止写入；
- 任何可能造成漏改、误改或非法最终结构的问题都阻止操作。

退出码保持：

| 退出码 | 含义 |
|---|---|
| `0` | 成功，包括空查询、幂等无变化和没有可首次生成的结论 |
| `1` | 仅供 `check` 表示发现客观错误 |
| `2` | 参数、根、路径或领域前置条件无效 |
| `3` | 内部状态损坏、需要恢复或存在不安全冲突 |
| `4` | 文件系统等运行时错误 |

JSON 错误包含 `ok: false`、稳定错误码、相关路径和结构化事实。默认人类输出再把事实渲染
为中文说明。查询、预演或写入不能得到完整结果时整体失败，不返回部分结果或静默解释为
成功。

## 17. 测试设计

### 17.1 单元测试

单元测试按独立行为契约组织，使用受真实接口约束的替身，不访问真实文件系统、不启动
子进程。重点覆盖：

- 两种平台路径语法的分类、规范化和存在性状态；
- 受管理页面命名空间与文件存在性正交，包括缺失页面、非受管理 Markdown、符号链接和错误
  文件类型；
- 正文资源身份去重、图片与附件、锚点忽略；
- 局部与全局查询范围；
- 稳定排序、分页、`--count-only` 同元数据、空结果和完整失败；
- 严格现存页面操作数的 `page.not_found` 与宽松反向链接目标的缺失成功语义；
- 状态自由流转与最终不变量；
- 关系配对、幂等修复、自引用和环路；
- create 的路径、父页面、标题转义和导航位置；
- 结论首次生成、空集合、重建、排序、非实时容忍和幂等性；
- 同一业务计划驱动正式执行与 `--dry-run`；
- 公开效果驱动正式 `changed_paths`，且内部 mutation 不进入公开结果；
- 静默生命周期的成功、临时失败和损坏分类，以及 `check` 自身诊断与前置推进活动的分离；
- `query recoverable` 的前置推进、过滤和 `as_of` 共享一次时钟读数；
- JSON 精确协议和人类输出的事实关系。

稳定协议值可以精确断言。人类输出不比较完整中文句子；测试验证路径、计数、顺序、事实
包含关系、控制字符转义和单行约束。

### 17.2 集成测试

真实文件系统、符号链接、原子替换、锁、操作日志、崩溃恢复、回收包、CLI 组合和分发物
行为保留在集成测试。核心与适配层测试树分开运行，network 只作为集成测试执行属性。

重要组合至少覆盖：

- move 后 Draft 内外相对正文链接仍指向同一词法目标；
- Windows 风格链接在 Linux 上被保留并正确分类；
- relation add/remove 同时修改双方和必要状态；
- generate-conclusions 与状态确认的完整工作流；
- 结论条目过时不阻止结构命令，重新生成后收敛；
- dry-run 与正式计划的公开影响一致；
- dry-run 的 `--count-only` 只隐藏 `effects`，正式执行拒绝所有预演分页选项；
- dry-run 遇到既有业务写锁时不忽略或恢复它，而是以恢复必需失败；
- 所有已登记命令都执行静默生命周期前置步骤；
- 无有效正式登记标记时，即使残留内部操作目录也不执行生命周期前置推进；
- `check` 前置推进硬失败时只返回标准错误信封，不返回部分检查或生命周期结果；
- 回收边界 `now == restore_expires_at` 不可恢复。

### 17.3 结构和依赖边界测试

源码结构和依赖限制必须通过实际导入关系、语法树或运行行为验证，不能搜索源码是否包含
某段普通文字。这样不会因为注释、换行或导入别名造成误判。

## 18. 文档与格式更新

实现时同步更新：

- `plugins/draft/skills/draft/SKILL.md`；
- `plugins/draft/skills/draft/references/draft-format.md`；
- CLI `--help`；
- 初始化、接管和测试夹具；
- 机器协议与人类输出测试；
- 受本次行为变化影响的 `plugins/draft/AGENTS.md`，仅在其中记录长期硬约束。

Draft skill 必须明确写出最终工作流：讨论过程中维护页面状态和关系；与用户确认讨论结束后
运行 `draft generate-conclusions ROOT`；根据新生成的 `proposed` 结论页产出设计文档；产出
完成后再将结论页状态设为 `confirmed`。

格式文档必须明确记录登记标记精确键集合以及旧 `{"format_version":1}` 标记不兼容，避免
既有 Draft 在升级后以无迁移说明的 `format.invalid` 失败。

## 19. 实施顺序

建议按以下依赖顺序实施，但它们属于同一份设计：

1. 统一稳定代码、本地化映射和正文资源解析；
2. 建立每次命令的页面/快照读取边界；
3. 实现查询模型、分页和 CLI 输出；
4. 建立公开 `PlanEffect` 与 `--dry-run`；
5. 实现 create、状态和关系命令；
6. 删除 output，改造懒生成结论生命周期；
7. 将静默生命周期前置步骤覆盖所有已登记命令；
8. 更新文档、适配层和完整质量门禁。

项目仍在开发，不保留旧格式兼容分支，不提供自动迁移命令。后续候选迁移工具应以本设计
落地后的新格式和共享路径规则为基础另行设计。
