# 运行时结果、错误与可观测性

## 公共结果、错误与可观测性

本页是 runtime 公共结果、错误码、退出码和机器输出的唯一登记处。领域 owner 决定页面、
关系、Trash 和查询事实；R-ERRORS 只拥有公共结果/错误外壳、公共字段边界、退出码和输出
通道，非 Check 查询及 blocker 的排序归 D-QUERY，Check 排序归 P-DIAGNOSTICS。内部路径、
staging、锁句柄、worker、manifest、progress、摘要和适配器回执不在任何公开结果中。

已公布字段名、字段类型、机器错误码和具有业务意义的数组顺序保持语义兼容；未来仅可新增
可选字段，调用方必须忽略未知字段。JSON 对象成员顺序不构成协议。成员未适用时省略，
不输出 `null`；规格要求的数组即使为空也输出 `[]`。

使用 `--json` 时，所有已由 Draft CLI 处理的成功和错误都向 stdout 写入恰好一个 UTF-8、
无 BOM、紧凑单行 JSON 对象，末尾恰一个 LF，stderr 为空。未使用 `--json` 时，成功写 stdout，
错误写 stderr。人类文案必须表达相同的已认证事实和必要建议，但文字、布局和翻译不是机器
协议；首版没有 ANSI 颜色、日志或动态进度。

### 成功结果联合

`OperationOutcome` 恰有以下共同字段：

| 字段 | 类型和值域 | 存在条件 |
| --- | --- | --- |
| `result_type` | 固定为 `operation` | 必需 |
| `operation` | `create`、`move`、`promote`、`collapse`、`generate_conclusions`、`status_set`、`relation_add`、`relation_remove`、`adopt`、`trash`、`restore` | 必需 |
| `mode` | `execute`、`dry_run` | 必需 |
| `outcome` | `applied`、`no_change`、`would_apply`、`would_not_change` | 必需 |
| `operation_id` | 小写 UUID v4 | 仅 `outcome: applied` |
| `previous_operation_id` | 小写 UUID v4 | 仅 `applied` 且已识别直接前一逻辑操作 |

Execute 的 `applied` 表示请求的领域变更已在逻辑提交点完成；`no_change` 只用于领域 owner
明确允许的幂等空操作，不创建 operation、记录或新 authority 版本。DryRun 只使用
`would_apply` 或 `would_not_change`，也不创建它们。`previous_operation_id` 只指向直接前项，
不是历史链。普通成功没有顶层告警、自动生命周期结果、路径列表或内部计划。

`InitOutcome` 恰含下列三个字段：

```text
{ result_type: "initialization",
  outcome: created | resumed | already_initialized,
  root_state: candidate | registered }
```

它不含 `operation_id`。`created`、`resumed`、`already_initialized` 分别表示首次创建、
匹配的未完成初始化现场续建、原本已初始化；成功前 marker 与必需 cleanup 必须都已按合同
完成。

七类查询共享 `QueryOutcome`。完整查询和局部未知查询是互斥联合：

```text
complete query:
  { result_type: "query", query, complete: true,
    items: QueryItems,
    severity_counts?: { error: non-negative integer, warning: non-negative integer } }

incomplete query:
  { result_type: "query", query, complete: false,
    blockers: QueryBlockers }
```

代码块中的 `QueryItems` 与 `QueryBlockers` 只是分别指向[查询行为合同的集合语义](../draft/query-check-and-output.md#查询集合与完整性)的命名占位，
不是本页新增的公共类型；集合成员和其余查询字段仍由该 owner 定义。

`query` 只允许 `page`、`links`、`backlinks`、`relations`、`status`、`recoverable`、`check`。
完整时只存在 `items`；不完整时只存在 `blockers`。`severity_counts` 只属于完整的 Check。
公共 wrapper 的其余集合语义、`Entry` 字段、分页、精确 `total`、`count_only`、`cursor` 绑定
和非 Check 排序由[查询行为合同](../draft/query-check-and-output.md#查询行为合同)拥有；Check
条目、severity、code-specific details 和排序由[Check 诊断合同](../draft/query-check-and-output.md#check-诊断合同)
拥有。本页只登记 wrapper 及其互斥的 `items`/`blockers` 外壳，不重复这些集合成员定义。

`Blocker` 恰含 `code`、`path`、`message`，并可有 `line`、`details`、`suggestions`。blocker
去重、未知范围判定、`query.source_unavailable`/`query.target_unavailable` 的专属 details、
集合排序和其他领域触发规则见[查询行为合同](../draft/query-check-and-output.md#分页游标与排序)。已
认证的内容违规仍是 Check 诊断，不混入 blocker。

`MaintenanceOutcome` 只有一个成功成员，恰为：

```json
{"result_type":"files_changed_notification","delivered":true}
```

它不含 operation、cache、consumer、内部例程状态、顶层告警或任何其他字段。错误
结果绝不含 `delivered`。

`RecoveryOutcome` 的公共字段只如下表所列：

| 字段 | 类型和值域 | 存在条件 |
| --- | --- | --- |
| `result_type` | 固定为 `recovery` | 必需 |
| `outcome` | `completed`、`rolled_back`、`cleaned_up`、`already_settled` | 必需 |
| `operation_id` | 小写 UUID v4 | 仅收敛的现场关联业务操作 |
| `caller_action` | `retry_now`、`retry_later` | 仅调用方仍需动作 |
| `retry_at` | RFC 3339 UTC 时间 | 当且仅当 `caller_action: retry_later` |

成功恢复没有 error、建议、顶层告警、内部 phase、路径、恢复前后状态、root version 或
authority version。`completed`、`rolled_back`、`cleaned_up` 和 `already_settled` 分别是调用方
需要区分的收敛结果；它们不表达稳定 Trash 条目是否交付或删除。已有可靠接受证据后不得再
调用内部交接适配器是 Draft 的内部强制规则，不成为公共动作值。

### 统一错误 envelope 与退出码

所有失败结果都恰为：

```text
{
  result_type: "error",
  error: {
    code: string,
    message: string,
    details?: object,
    suggestions?: string[],
    path?: RootPath,
    line?: positive integer
  }
}
```

`code` 是机器分类；`message` 只说明已认证事实，程序不得依赖逐字文案。`path` 仅在一个
文件直接造成错误时出现，`line` 仅在能定位到直接原文行时出现；多对象问题使用该 code
登记的 `details`，不任意选择一个主路径。`details` 只包含表中允许的安全字段。`suggestions`
是按推荐优先级排列的文字建议，只能由 code owner 的模板和已认证安全事实生成；它可以建议
CLI、人工处理、环境变更、外部协调或请求人类决定，但不授权操作，也不保证成功。没有可靠
建议时省略该成员，绝不把不可信文件内容、内部路径、摘要或 OS 异常原文拼入建议。

| 退出码 | 唯一含义 |
| ---: | --- |
| 0 | 请求成功；能够合法返回 blocker 的查询仍是成功。 |
| 1 | 请求合法，但当前领域状态拒绝执行：包括 Check error/blocker、Restore 领域拒绝和正式根 Check 门禁。 |
| 2 | request、root 或参数本身非法。 |
| 3 | 当前运行环境或持久状态不能安全完成：包括锁竞争、恢复要求、权威损坏、外部变化与耐久失败。 |
| 4 | 未分类的内部故障。 |

Check 完整且没有 `error` 诊断退出 0；有 `error` 诊断，或不完整并有 blocker，退出 1。其他
查询的 `complete: false` 仍退出 0。一个修改请求只返回一个独立错误；其因果顺序为 request
自身、根与 runtime 基础状态、必需路径的存在/管理状态/内容认证、命令资格、闭包完整性、
关系图/目标碰撞/最终状态、正式根 Check 门禁。

### 错误登记

下表是 runtime 自身 code 的唯一登记。`details` 的“无”表示该 code 不输出该成员；所有
未列 code 不得由 runtime 输出。error envelope 每次只有一个 error，因而不定义跨 error 排序；
非 Check 查询及 blocker 的排序见[查询行为合同](../draft/query-check-and-output.md#分页游标与排序)，
Check 诊断排序见[Check 诊断合同](../draft/query-check-and-output.md#check-诊断合同)。表中没有专属
可靠建议的 code 必须省略 `suggestions`，不能临时拼造处理步骤。

| code | 结果类别 / 退出码 | 触发事实 | `details` | 可靠建议边界 |
| --- | --- | --- | --- | --- |
| `runtime.request_invalid` | error / 2 | 封闭 request、CLI 参数、mode 或字段校验失败 | `field: string`（最具体点分逻辑路径）；`reason: missing_field | repeated_field | invalid_type | invalid_value | unsupported_field | incompatible_fields` | 仅建议补齐、删除、改正字段或选择已列合法值；不回显任意用户文本。 |
| `runtime.root_invalid` | error / 2 | root 找不到、不是目录、不是当前 Draft 根或选择失败 | `reason: not_found | not_directory | not_draft_root | selection_failed` | 可建议修正 root 选择。 |
| `runtime.init_target_not_empty` | error / 1 | 初始化 target 含旧身份或其他非空现场，不能原地接管 | 无 | 可建议使用空目录或先搬移内容。 |
| `runtime.init_conflict` | error / 1 | 可认证初始化现场与请求目标不兼容 | `existing_root_state: candidate | registered`、`requested_root_state: candidate | registered` | 可建议使用与既有现场一致的初始化目标或显式处理现场。 |
| `runtime.platform_unsupported` | error / 3 | 平台无法证明所需安全能力 | `capability: no_follow | atomic_publish | live_lock | durability | hard_timeout` | 只建议满足相应平台能力。 |
| `runtime.schema_unsupported` | error / 3 | schema 可认证但为旧版或未知版，且无认证矛盾 | `schema_version: string` | 只建议使用受支持版本或经批准迁移。 |
| `runtime.corrupt` | error / 3 | Draft 私有 marker、runtime、descriptor、authority、record、catalog、head 或 ledger 无法认证或矛盾 | `component: marker | runtime | descriptor | authority | record | catalog | head | ledger`；`reason: type_invalid | schema_invalid | digest_mismatch | relation_mismatch | unknown_field | no_follow_failed` | 仅建议修复可认证事实或寻求人工处理；不建议删除现场。 |
| `runtime.closure_incomplete` | error / 3 | 安全判断所需普通领域事实无法完整、稳定取得 | `scope: pages | navigation | relations | links | conclusions | trash | restore | index | after_state` | 只建议补足或稳定该范围的事实。 |
| `runtime.busy` | error / 3 | 需要排他 root lock 的调用遇到 live owner 或 live lock | 无 | 只可建议稍后重试，不承诺时间。 |
| `runtime.external_change` | error / 3 | 认证或最终重验期间相关外部事实变化，不能稳定完成 | 无 | 可建议重新发起同一请求。 |
| `runtime.recovery_required` | error / 3 | 初始化或其他普通入口认证到有效 recovery pending 现场（包括 descriptor cleanup artifact 或有效 operation/cleanup pending） | 无 | 可建议显式 `recover()`；普通入口不得代行恢复。 |
| `runtime.recovery_blocked` | error / 3 | 显式恢复现场可认证且无矛盾，但证据不足以证明安全动作 | `reason: evidence_missing | ownership_unproven | action_ambiguous` | 只建议补足或修复证据。 |
| `runtime.durability_failed` | error / 3 | 已决定的 record、authority、cleanup 或最终目录操作不能可靠保存 | 无 | 可建议在保持现场后显式恢复。 |
| `runtime.internal` | error / 4 | 不能归入其他稳定机器分类的内部 I/O、worker 或 runtime 故障 | 无 | 可给安全的人工诊断建议；不公开底层异常。 |

### 初始化认证事实的公共 details 映射

下表是 R-ERRORS 对初始化认证事实的唯一公共 `details` 映射。`component` 只能取上表已经
登记的值；`reason` 取与已认证损坏种类相符的一项，不新增枚举。初始化页只选择事实和
machine code，并链接本表，不复制公共字段。

| 已认证初始化事实 | `code` | 公共 `details` |
| --- | --- | --- |
| runtime 布局出现未登记成员（包括 descriptor 未列举的额外 cleanup member） | `runtime.corrupt` | `component: runtime` + `reason: unknown_field` |
| descriptor 自身发生已认证损坏 | `runtime.corrupt` | `component: descriptor` + `reason: type_invalid | schema_invalid | digest_mismatch | relation_mismatch | unknown_field | no_follow_failed`，按实际损坏种类取一项 |
| 其他已有组件的非 cleanup 节点发生已认证损坏 | `runtime.corrupt` | `component: 实际已有 component` + 上述与实际损坏种类相符的 `reason` |
| 无独立组件名的 cleanup material 发生稳定类型、摘要或 no-follow 损坏 | `runtime.corrupt` | `component: runtime` + `reason: type_invalid | digest_mismatch | no_follow_failed`，按实际损坏种类取一项 |
| 本身属于已登记组件的 cleanup material 发生稳定类型、摘要或 no-follow 损坏 | `runtime.corrupt` | `component: 该 material 的已有 component` + `reason: type_invalid | digest_mismatch | no_follow_failed`，按实际损坏种类取一项 |
| 初始化认证所需事实无法完整、稳定取得 | `runtime.closure_incomplete` | `scope: after_state` |

`runtime.corrupt` 只表示 Draft 私有权威事实损坏或矛盾；它不能替代已读取并认证的普通页面、
导航、关系、结论或资源违规。后者使用相应领域诊断 code、退出 1。事实尚未能完整稳定取得时
使用 `runtime.closure_incomplete`（或查询可合法表达的 blocker），不能把暂时未知降格成内容
违规或损坏。

查询与 Restore 的专有码如下。它们都是单一 error envelope；没有专属可靠建议的行省略
`suggestions`，不得把 cursor、回收包或当前文件内容回显到建议中：

| code | 结果类别 / 退出码 | 触发事实 | `details` |
| --- | --- | --- | --- |
| `page.not_found` | error / 2 | 请求的受管理页面不存在 | 无 |
| `query.source_not_found` | error / 2 | `links` 指定来源不存在 | 无 |
| `query.source_not_allowed` | error / 2 | 来源存在但当前 root state 不允许作为查询来源 | 无 |
| `query.cursor_invalid` | error / 2 | cursor 损坏或无法认证 | 无 |
| `query.cursor_mismatch` | error / 2 | cursor 属于其他查询、条件、root 或集合 | 无 |
| `query.snapshot_expired` | error / 3 | cursor 绑定的相关语义快照已变化 | 无 |
| `query.incomplete` | error / 3 | 查询无法完整完成且不能合法返回 blocker | 无 |
| `restore.not_found` | error / 1 | 没有可信终态或回收记录 | 无 |
| `restore.expired` | error / 1 | 回收包已过期 | `expires_at` |
| `restore.already_restored` | error / 1 | 可信终态记录为已成功恢复 | 无 |
| `restore.conflict` | error / 1 | 当前内容不允许安全恢复 | `reason: content_changed | target_occupied | mapping_ambiguous` |
| `restore.corrupt` | error / 3 | 回收包或关联领域证据损坏 | `reason: identity | package | digest | relation | navigation | authority` |

`query.source_unavailable` 与 `query.target_unavailable` 只作为 `Blocker.code`，不是 error
envelope；它们的触发事实、专属 `details`、出现条件和排序由[查询行为合同](../draft/query-check-and-output.md#查询错误与阻塞项)
唯一拥有。它们只在未知范围可能改变查询答案时出现。

### Check 诊断投影

Check 的 `code`、`severity`、`details` schema、排序字段和安全建议模板由
[诊断 code 登记](../draft/query-check-and-output.md#诊断-code-登记) 唯一拥有。本页不重复维护
Check code 表。Check 诊断阻止修改时，错误 envelope 仍遵守本页定义。

同一已认证领域违规在修改命令中阻止操作时，复用上述 owner 登记的 `code`、`details`、`path`
和适用的 `line`，不带 Check 专用的 `severity`，退出码为 `1`。`warning` 不阻止修改；正式
根 after-state 的完整 Check 门禁按[查询、检查与输出](../draft/query-check-and-output.md#check-门禁与副作用)
执行。

命令特有领域 code 也按本页 envelope 返回。每次只有一个 error；同一无序来源集合按规范
路径选第一个，同一文件沿用 Check 诊断顺序：

| code | 退出码 | `details` / 定位 |
| --- | ---: | --- |
| `operation.check_failed` | 1 | `error_count` |
| `operation.path_not_found` | 1 | 必有 `path` |
| `operation.path_not_managed` | 1 | 必有 `path` |
| `operation.path_conflict` | 1 | 必有冲突 `path`；可有 `source` |
| `move.sources_not_movable` | 1 | `reason: non_markdown | mixed_file_and_directory | multiple_directories | mixed_management | no_markdown_pages`；单一路径直接负责时有 `path` |
| `move.target_incompatible` | 1 | 无 `details`、`path` 或 `line` |
| `promote.candidate_not_leaf` | 1 | 必有 `path` |
| `promote.parent_mismatch` | 1 | 必有 `path`、`expected_parent`、`actual_parent` |
| `collapse.target_not_collapsible` | 1 | 必有 `path`；`reason: root | not_hub` |
| `collapse.hub_not_empty` | 1 | 必有 `path` |
| `relation.target_status_required` | 1 | 必有目标 `path` |
| `trash.target_not_active` | 1 | 必有请求 `path` |
| `trash.root_not_allowed` | 1 | 必有 `path: index.md` |

对于 `move.target_incompatible`，`message` 只描述已认证的 directory source 与 `file` target
不兼容事实，不回显具体 source/target。只有在已认证 source 为 directory 且 target 为 `file` 的事实
可靠时，才可建议改用 prefix target；不得给出具体路径或授权移动。

这些 code 的可靠建议只可分别建议：核对或建立路径、改用受管理页面、处理目标占用、按
`reason` 修正 move 集合、选择叶页面、先移动到共同父、选择空非根 hub、明确四个合法
`target_status`、核对 active 页面或查询可恢复项、以及回收具体顶层子树。建议不授权移动、回收
或替调用方选择状态。

### notification、自动工作与逻辑提交

notification 只调用 `maintain({})`。它不读取 query、不安排重试、不推进自动 lifecycle，
也不调用 `recover()`。自动 handoff、到期、重试等待、候选选择、cache 降级和不阻断当前
请求的内部锁竞争都保留在 Draft 内部；它们不形成普通结果、查询结果、恢复结果或错误的
顶层字段。若自动阶段实际阻断当前请求，调用只返回上表中的单一机器错误。

所有成功都要求该入口的 authority/content/catalog/head/outcome commit 与不可延期授权
cleanup 已 durable，且派生资料已 durable 或安全分类为可重建；否则返回
`runtime.durability_failed` 或更早的机器错误。fatal 或最终 scan 不稳定时不返回查询结果，
错误不授权额外业务写、cleanup、外部 handoff 或隐式恢复。

## 来源

- `ce64ac30c52ca910fe39fcedd055dd28f0cfbda4:.scratch/draft-operation-runtime-foundation/spec.md#11-输出错误与验收`
