# 分诊标签

工程 skills 使用五个标准分诊角色。本地 Markdown feature/spec/issue/map/Agent Brief 通过 `Status:` 字段保存对应字符串；`Status` 不是完成状态，不能写入 `applied`、`closed` 或 `superseded`。

| Skill 中的角色 | 本仓库的 `Status:` 值 | 含义 |
| --- | --- | --- |
| `needs-triage` | `needs-triage` | 等待维护者评估；不可派遣 |
| `needs-info` | `needs-info` | 等待报告者补充信息；不可派遣 |
| `ready-for-agent` | `ready-for-agent` | 仅表示角色候选；还必须通过正交 tracker 判定才能派遣 |
| `ready-for-human` | `ready-for-human` | 需要人类实施或决策；不可派遣 |
| `wontfix` | `wontfix` | 当前不会处理；关闭时使用 `Resolution: rejected` |

## 正交机器判定

`Status-Schema: triage`、canonical `Status`、`Tracker-State`、`Resolution`、`Promoted-to`、`Base-Spec`、`Affects`、`Parent`、`Scope` 和 `Current-Owner` 分别解析，不能拼成复合状态。`Tracker-State` 只能是 `open`/`closed`；open 条目不写 `Resolution`/`Promoted-to`，closed 条目必须使用 `applied`、`rejected` 或 `superseded`，并遵守对应的 `Promoted-to` 规则。

只有 `open` + `ready-for-agent` + 有效 `Base-Spec` + 非空可解析 `Affects` + 合法父子范围的条目才可标记 `Dispatch: eligible`。缺少 schema、任一必需字段、current owner、链接或父子范围证明，或存在冲突/未签署语义时，结果为 `unresolved`、`Dispatch: blocked`；不得默认 open、applied、ready-for-agent，也不得用 Type、标题、实现、测试、checklist 或 parent 状态补全。

Agent Brief 只能缩小父 spec 或 current Wiki owner 的范围。父条目的 `superseded` 不会隐式关闭 child；每个 child 都必须有自己的 tracker 结果、不可派遣诊断和 current owner。已完整取代的条目逐项写 `Tracker-State: closed`、`Resolution: superseded` 和可解析 `Promoted-to`。关闭条目默认保留；用户明确要求清理且当前 owner 已覆盖时，按 [Feature spec 生命周期](../../plugins/draft/docs/spec/governance/feature-specs.md#feature-spec-生命周期) 处理历史材料。

当 skill 使用标准角色或要求应用某个分诊标签时，把对应字符串写入 issue 的 `Status:` 字段，并同步完整正交字段；不能把 `claimed` 或 `resolved` 作为 `Status` 值。
