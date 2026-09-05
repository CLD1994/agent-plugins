# Feature spec 与 tracker

## Feature spec 生命周期

`.scratch/<feature>/spec.md` 是相对 current Wiki 的 feature delta 和 tracker，不是当前行为 authority。只有 `Status-Schema: triage` 才进入本判定。机器判定分别读取 `Status`、`Tracker-State`、`Resolution` 和 `Promoted-to`；不得把其中任意两个拼成复合状态。

**输入**：feature 目录、`Status-Schema: triage`、`Status`、`Tracker-State`、`Base-Spec`、`Affects`，以及关闭条目的 `Resolution`/`Promoted-to`。

**输出**：可派遣、不可派遣或已关闭的 tracker 判定；不会因此改变 current Wiki。

**不变量**：

- `Status` 只可为 `needs-triage`、`needs-info`、`ready-for-agent`、`ready-for-human`、`wontfix` 五种 canonical triage role。`applied`、`closed`、`superseded` 不是 `Status` 值。
- `Tracker-State` 只可为 `open` 或 `closed`；`Resolution` 只可为 `applied`、`rejected` 或 `superseded`，且只在关闭后出现。四个字段彼此独立存储，不能合并为一个状态字符串。
- `Promoted-to` 不是状态值：`applied` 与 `superseded` 必须给出可解析的 current-spec Target；`rejected` 禁止声称已迁入；open 条目不得伪造 `Promoted-to`。
- Agent Brief 是 child issue 的局部执行约束，只能缩小父 feature spec 范围。与父 spec 或 current Wiki 冲突时，不能自行选择语义。

### 正交状态决策表

| `Status` | `Tracker-State` | `Resolution` | `Promoted-to` | 结果 |
| --- | --- | --- | --- | --- |
| `ready-for-agent` | `open` | 缺失 | 缺失 | 只有 `Base-Spec` 有效且 `Affects` 非空、可解析时可派遣。 |
| `needs-triage` / `needs-info` / `ready-for-human` / `wontfix` | `open` | 缺失 | 缺失 | 合法但不可派遣；保留 open 诊断，不能把 triage role 当完成结果。 |
| 任一合法 triage role（实施已同步） | `closed` | `applied` | 必填且解析到 current-spec Target | 已完成 delta；不成为 authority。 |
| `wontfix` | `closed` | `rejected` | 缺失 | 已拒绝；不得迁入 current Wiki。 |
| 任一合法 triage role | `closed` | `superseded` | 必填且解析到取代它的 current-spec Target | 已被完整取代；不成为 authority。 |
| `ready-for-agent` 等任一合法 role | `open` | 非空或非法 | 任意非空 | 非法组合，`unresolved`，不可派遣。 |
| 任一合法 role | `closed` | 缺失或非法 | 任意 | 非法组合，`unresolved`，不可派遣。 |
| 任一合法 role | `closed` | `rejected` | 非空，或 `Status` 不是 `wontfix` | 非法组合，`unresolved`，不可派遣。 |
| 任一合法 role | `closed` | `applied` / `superseded` | 缺失或目标无法解析 | 非法组合，`unresolved`，不可派遣。 |
| `Status-Schema` 缺失/非法、任一字段缺失/未知、Base-Spec/Affects 无法解析，或父子范围冲突 | 任意 | 任意 | 任意 | `unresolved`：保留字段级诊断，等待人工归一化；不得默认改成 open、applied 或 `needs-triage`。 |

**转换与副作用**：实施完成后才可按 applied 从 open 转 closed，并同时写入适用 Resolution 和 Promoted-to；拒绝和被取代分别使用 rejected 和 superseded。关闭条目默认保留，状态转换本身不触发删除。

用户明确要求清理历史材料时，可将已被当前 owner 覆盖的旧设计、计划和失效 tracker 从当前树移除；仍有效且未完成的实施入口继续保留。清理同时更新当前引用和消费者登记，保留完整 Git locator 供历史来源追溯。Tracker 的实际状态只在对应条目维护，不在正式规格中复制各 feature 的状态清单。

**失败与恢复**：Base-Spec 过期、Affects 为空或解析失败、父子范围冲突、字段非法或 legacy 缺字段时均不可派遣。诊断只能说明缺口；不能用实现、测试、issue 标题或 Agent Brief 填补字段。

**验证**：解析 feature spec 和 Agent Brief 头部；覆盖 open/ready-for-agent、open/不可派遣、closed/applied、closed/rejected、closed/superseded 与 legacy 非法组合；验证 Base-Spec、Affects 与 Promoted-to 链接，且 child 范围不扩张。

## 相关规格

- [来源资格与唯一权威](authority.md#来源资格与唯一权威)
- [迁移与原子切换](migration.md#迁移与原子切换)

## 来源

- `33aeaec60a34833ce8b686bfdd7e50b8dc9932d0:.scratch/draft-authority-spec-migration/signed-decisions-transcript.md#已签署决定迁移摘要`（signed-Q25、signed-Q26、signed-Q27、signed-Q32）
