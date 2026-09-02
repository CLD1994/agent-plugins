# 运行时公共接口

## 公开入口与 root

runtime 只有两个公开行为边界。`Lifecycle` 负责初始化；root-bound
`OperationRuntime` 负责已经可认证 Draft 根上的操作、查询、维护和恢复。
`RuntimeFactory` 只是 CLI 取得后者的受限构造 seam，不是第三个业务接口，也不承诺
返回 `Lifecycle`。

```text
Lifecycle.initialize(root: AbsoluteRoot, root_state: candidate | registered)
  -> InitOutcome | ErrorEnvelope

RuntimeFactory.for_root(root: AbsoluteRoot) -> OperationRuntime

OperationRuntime
  .perform(request: OrdinaryRequest, mode: execute | dry_run)
    -> OperationOutcome | ErrorEnvelope
  .trash(request: TrashRequest, mode: execute | dry_run)
    -> OperationOutcome | ErrorEnvelope
  .restore(request: RestoreRequest) -> OperationOutcome | ErrorEnvelope
  .read(request: ReadRequest) -> QueryOutcome | ErrorEnvelope
  .maintain(event: ExternalFilesChanged) -> MaintenanceOutcome | ErrorEnvelope
  .recover() -> RecoveryOutcome | ErrorEnvelope
```

`init ROOT [--candidate]` 是唯一初始化 CLI 入口。它把 `ROOT` 规范为显式
`AbsoluteRoot` 后直接调用 `Lifecycle.initialize`：有 `--candidate` 时传入
`candidate`，否则传入 `registered`。初始化不经 factory、不读取 cwd 或环境来选择
另一 root，也不支持 DryRun。

除 `init` 外，CLI 按 `--root`、`DRAFT_ROOT`、物理 cwd 的祖先顺序选择 root。`DRAFT_ROOT`
必须是绝对路径。最高优先级候选无效即返回 `runtime.root_invalid`，不得回退。`--root` 与 `init ROOT` 都可为
绝对或相对文件系统路径；相对路径以物理 cwd 为基准，已存在的符号链接按平台规则解析为
唯一规范绝对路径。自动祖先发现跳过只有旧登记文件或旧内部目录的目录；显式 `--root`
指向这种目录也是 `runtime.root_invalid`（`reason: not_draft_root`）。

CLI 只负责把命令行映射为下列封闭输入，并以同一校验规则调用 runtime。它不得另行推导
parent、导航、managedness、根状态、计划、闭包、Check 事实或任何内部协作者；反过来，
非 CLI 适配器也必须构造完全相同的 request。`--root`、`--json` 和 mode 不属于领域
request 成员。

### 共用值与输入校验

- `RootPath` 是规范 Draft 根相对 POSIX 路径。它不接受绝对路径、`./`、重复分隔符、
  `.`、`..`、反斜线、NUL 或控制字符；大小写、Unicode 和普通空格原样参与身份比较，
  不作 Unicode 归一化、裁剪、URL 解析或百分号解码。`?`、`#` 和 `%` 都是原始路径字符，
  不改变路径语义。
- `OperationId` 和 `trash_id` 都是 36 字符的小写 UUID v4。它们是不透明值，不含路径、
  时间或排序含义。
- 标题与说明文字是非空单行纯文本，不得有首尾空白或控制字符；runtime 不自动裁剪。标题
  写入 Markdown 时的必要转义由 runtime 完成，解析后的可见标题必须等于输入值。
- 一个 request 只允许所属联合成员列出的字段；缺少、未知、重复、`null`、错误类型、非法
  枚举、非规范路径和非法 UUID 都是 `runtime.request_invalid`。普通操作的无序选择集合先按
  调用方原始顺序检查，再对合法唯一集合按规范路径排序；`sources`、`siblings` 等普通列表的
  重复值拒绝。`ReadRequest.query=check` 的 `codes`、`severities`、`paths` 是唯一的重复值例外：
  接受重复，并在绑定逻辑 request 与 cursor 身份前规范化去重；`paths` 按规范路径排序，
  `codes` 与 `severities` 按稳定机器顺序规范化。排序后的集合才是逻辑 request。

一次无效 request 只返回一个错误，顺序固定为：操作或命令、该操作支持的 mode、未知
字段/参数、重复字段/参数、缺少必需字段、字段类型、字段值/路径/列表成员、重复列表成员（不
包括 Check 三种筛选列表的重复值例外）、跨字段组合。对象未知字段按字段名排序，未知 CLI 参数按原始出现顺序；同一 request
成员按下表字段顺序，数组错误使用从 0 开始的原始下标。校验失败不读取领域事实、不形成计划
或产生副作用。

`runtime.request_invalid.details.field` 指向最具体的点分逻辑字段；数组成员使用如
`sources.1` 的原始下标。无法映射到逻辑 request 字段的 CLI token 使用
`arguments.<index>`。跨字段组合指向使组合失效的字段：多 source 配单文件 `.md` target
指向 `target`，非 `supersedes` 关系带 `target_status` 指向 `target_status`，同时设置和清除
说明指向 `note_change`。

`details.reason` 的字段 ABI 由[公共结果、错误与可观测性](errors-and-observability.md#公共结果错误与可观测性)
登记；本输入边界按失败事实选择其值：缺少必需字段使用 `missing_field`，单值或列表成员重复
使用 `repeated_field`，容器或成员类型错误使用 `invalid_type`，枚举、UUID、文本或路径值非法
使用 `invalid_value`，未知字段或参数使用 `unsupported_field`，字段各自合法但组合不成立使用
`incompatible_fields`。

### 普通操作 request

`OrdinaryRequest` 是以必需字段 `operation` 区分的封闭对象。所有字段均为 JSON 成员；
表中的“无”表示该成员不能出现。

| `operation` | 必需字段 | 可选字段 | 支持 mode |
| --- | --- | --- | --- |
| `create` | `page: RootPath`、`title: string`、`status: pending | proposed` | 无 | `execute`、`dry_run` |
| `move` | `sources: RootPath[]`、`target: RootPath` | 无 | `execute`、`dry_run` |
| `promote` | `page: RootPath`、`siblings: RootPath[]` | 无 | `execute`、`dry_run` |
| `collapse` | `hub: RootPath` | 无 | `execute`、`dry_run` |
| `status_set` | `page: RootPath`、`status: pending | proposed | confirmed | superseded | closed` | `note_change` | `execute`、`dry_run` |
| `relation_add` | `source: RootPath`、`relation_type`、`target: RootPath` | 无 | `execute`、`dry_run` |
| `relation_remove` | `source: RootPath`、`relation_type`、`target: RootPath` | `target_status` | `execute`、`dry_run` |
| `generate_conclusions` | 无 | 无 | `execute`、`dry_run` |
| `adopt` | 无 | 无 | 仅 `execute` |

`sources` 与 `siblings` 都是非空无序选择集合；重复值拒绝。`relation_type` 只允许
`depends_on`、`supersedes`、`challenges`。`parent`、反向关系和路径 mapping 是领域 owner
推导的事实，不能作为 request 字段。

`move.target` 以字面 `.md` 后缀静态分类：以该后缀结尾的是精确单文件 `file` target，其他
值是 `prefix` target；调用方不得另传 `target_kind`。多个 `sources` 配 `file` target 使用
`runtime.request_invalid` 的 `incompatible_fields`，`details.field` 为 `target`，并在读取
Draft 当前事实前拒绝。单个 source 的实际节点类型及其领域资格由
[结构操作](../draft/structure.md#move)读取和判断。

在所有 `sources` 成员已通过 `RootPath` 词法校验和重复检查、且 target 已完成上述静态
`file | prefix` 分类后，R-INTERFACE 还使用规范化 request 字符串做跨字段 self-mapping
检查。它不读取 root、文件系统或领域事实，也不形成领域 mapping：

| target 类型 | 静态 after-root 公式 | self-mapping 判定 |
| --- | --- | --- |
| `file` | 唯一 source 的规范化 after path 等于 `target` | `target == source` |
| `prefix` | 每个 source 的规范化 after root 等于 `target / basename(source)` | 任一 after root 等于对应 source；目录成员在该 after root 下保留 source 下的相对后缀，因此同一判断同时覆盖文件、文件集合和目录来源。 |

任一 self-mapping 命中时返回既有 `runtime.request_invalid`，使用
`details.field: target`、`details.reason: incompatible_fields`；公共错误 envelope、退出码和
其他字段按[公共结果、错误与可观测性](errors-and-observability.md#公共结果错误与可观测性)登记。它发生在
选择或读取 root、认证 source 实际类型、形成领域 mapping 或建立计划之前，保持零写。该检查与
多个 source 配 `file` target 的既有静态拒绝并列，二者都不改变请求校验顺序或零写边界。

`note_change` 只能是恰含 `action: "set"` 和 `text` 的对象，或恰含
`action: "clear"` 的对象；省略表示保留既有说明。`target_status` 只可出现在
`relation_remove` 且 `relation_type` 为 `supersedes`，值域为 `pending`、`proposed`、
`confirmed`、`closed`。调用方可以预先提供合法值；它是否因当前关系事实而必需、是否应用及
目标状态如何变化，由[页面状态与关系](../draft/status-and-relations.md#决策表转换与提交)定义。

对应 CLI 形式是：

```text
draft create PAGE --title TITLE --status pending|proposed [--dry-run]
draft move SOURCE... TARGET [--dry-run]
draft promote PAGE SIBLING... [--dry-run]
draft collapse HUB [--dry-run]
draft status set PAGE STATUS [--note TEXT | --clear-note] [--dry-run]
draft relation add SOURCE RELATION TARGET [--dry-run]
draft relation remove SOURCE RELATION TARGET [--target-status STATUS] [--dry-run]
draft generate-conclusions [--dry-run]
draft adopt
draft trash PAGE [--dry-run]
draft restore TRASH_ID
```

修改命令不接受 `--limit`、`--offset` 或 `--count-only`。单值位置或选项重复、未知参数、
多余位置参数，以及不支持 DryRun 的命令收到 `--dry-run`，都在进入领域计划前以
`runtime.request_invalid` 拒绝。

命令、枚举和长选项区分大小写，只接受本页列出的 lowercase 值；首版没有短别名。带值长
选项接受 `--name value` 与 `--name=value`，二者重复仍拒绝；选项可在位置参数前后，`--`
终止选项解析，其后都按位置参数处理。`draft --help`、`draft COMMAND --help`、
`draft GROUP --help` 与根级 `draft --version` 退出 0，不选择 root 或读取仓库；除确定帮助
层级所需的命令/命令组外，它们不能与其他参数组合。帮助和版本文字仅供人类阅读。

### Trash、Restore、查询、通知与恢复输入

`TrashRequest` 恰为 `{page: RootPath}`，支持 `execute` 与 `dry_run`。`page` 如何选择受管理
页面子树、package 包含哪些成员以及哪些页面角色可回收，由
[Trash 与 Restore](../draft/trash-and-restore.md#行为合同)定义。

`RestoreRequest` 恰为 `{trash_id: OperationId}`，只支持执行。成功 Trash 的
`operation_id` 同时就是该回收包的 `trash_id`，成功结果不重复输出同值的 `trash_id`；
实际成功的 Restore 可以有自己的 `operation_id`。

`ReadRequest` 是以 `query` 区分的封闭对象：

| `query` | 领域字段 | 集合控制 |
| --- | --- | --- |
| `page` | `page: RootPath` | 不接受 `limit`、`cursor`、`count_only` |
| `links` | `source: RootPath` | `limit`、`cursor`、`count_only` |
| `backlinks` | `target: {kind: page | local | external, value: string}` | `limit`、`cursor`、`count_only` |
| `relations` | `page: RootPath`、`direction: incoming | outgoing` | `limit`、`cursor`、`count_only` |
| `status` | `status: pending | proposed | confirmed | superseded | closed` | `limit`、`cursor`、`count_only` |
| `recoverable` | 无 | `limit`、`cursor`、`count_only` |
| `check` | 可选非空数组 `codes`、`severities`、`paths` | `limit`、`cursor`、`count_only` |

集合控制的类型固定为：`limit` 是整数，`cursor` 是不透明字符串，`count_only` 是布尔值。
`backlinks.target` 恰含 `kind` 与 `value`：`page` 和 `local` 的 `value` 使用 `RootPath`；
`external` 的 `value` 是保留原文的外部 URI 字符串；fragment 不参与目标身份。首版不接受
无法脱离来源文件唯一解释的根外本地路径。

Check 的 `codes`、`severities`、`paths` 出现时都是非空数组；`codes` 的每项是已登记的
Check code，`paths` 的每项是 `RootPath`，`severities` 的值域见上表后的规则。三种筛选都只做
精确匹配，不接受模糊或前缀筛选，也不提供 `all` 或 `offset`。

`cursor` 是唯一 continuation 字段。它绑定 root、query、规范化过滤条件、来源范围、
当前语义快照和 `items` 或 `blockers` 其中一个集合，不能被其他命令或另一集合使用。
集合查询的 `limit` 默认 100，范围为 1 至 1000；`count_only: true` 时不得带 `limit`
或 `cursor`，`count_only: false` 表示普通集合查询。Check 的 `severities` 只允许 `error`、
`warning`；重复筛选值规范化去重，
同维度取并集、不同维度同时满足。

`ExternalFilesChanged` 是零业务字段信号，即唯一合法值是 `{}`。调用方不得传路径、变更
类型、重命名映射、stdin 清单、cursor、mode 或 DryRun；runtime 必须自行扫描并认证当前
根事实。通知不会成为查询或 Check 正确性的前提。

`recover()` 没有 request、身份选择字段或 mode；它是唯一显式收敛已认证 pending 现场的
入口。普通操作、读、DryRun 和 notification 都不得隐式调用它。

### mode、逻辑身份与私有边界

| 入口 | `execute` | `dry_run` | 结果联合 |
| --- | --- | --- | --- |
| `initialize` | 允许 | 不支持 | InitOutcome / ErrorEnvelope |
| `perform`（`adopt` 以外） | 允许 | 允许 | OperationOutcome / ErrorEnvelope |
| `perform(adopt)` | 允许 | 不支持 | OperationOutcome / ErrorEnvelope |
| `trash` | 允许 | 允许 | OperationOutcome / ErrorEnvelope |
| `restore`、`read`、`maintain`、`recover` | 各自固定入口语义 | 不支持 | 对应结果联合 / ErrorEnvelope |

DryRun 使用与 Execute 相同的 request 和事实准入。无已发现记录时它不探测或取得 root lock，
不发布记录、不创建 operation、authority 版本或制品、不推进自动生命周期、不维护索引；
公共成功投影按[公共结果、错误与可观测性](errors-and-observability.md#公共结果错误与可观测性)登记，
且不授权后续 Execute。已发现记录时最多进行一次 non-blocking probe，仍不得写入。

manifest、progress、内部摘要、attempt、phase、锁、路径列表、内部 worker、handoff 适配器、
descriptor、record、cache、catalog 与物理布局都是 Draft 私有实现，不能成为 request、结果
字段或第三方集成协议。公共结果、错误 envelope、退出码和输出通道见[公共结果、错误与可观测性](errors-and-observability.md#公共结果错误与可观测性)；
非 Check 查询及 blocker 的排序见[查询行为合同](../draft/query-check-and-output.md#分页游标与排序)，
Check Entry、severity、code-specific details 和排序见[Check 诊断合同](../draft/query-check-and-output.md#check-诊断合同)。

## 来源

- `ce64ac30c52ca910fe39fcedd055dd28f0cfbda4:.scratch/draft-operation-runtime-foundation/spec.md#2-对外接口与所有权`
