# 运行时生命周期与交接

## 自动生命周期与交接

本合同拥有自动 `AdvanceDue`、Trash/Restore 交接、handoff attempt、warning 和 cleanup
授权。它不改变 Draft 的页面、关系、包成员或 Restore 领域 after-state；它只在这些
事实已由 request/authority 认证后决定 runtime 的耐久动作。

### 输入与公开输出

输入是一次 invocation 的入口类型、已认证 active authority/schedule、候选的 due/retry
事实、R/T 阻塞关系、运行期 root lock、`HandoffAdapter` 可调用性、handoff progress
与时间。输出是：

- 无自动变化或最多一个已处理 candidate 的普通结果；
- 本 invocation 新产生的 `failed`、`unknown`、`deferred` 或 `busy` actionable warning；
- durable handoff/cleanup 结果；或
- `runtime.durability_failed`、`runtime.corrupt`、`runtime.busy` 等 fatal/error。

成功 read 只返回 projection 和本次 actionable warnings，不带 routine lifecycle
result。若自动 lifecycle 已 durable 改变而后续业务动作失败，error envelope 最多带
一个有序 lifecycle result 和本次新 warning；自动 lifecycle 自身 fatal 时可带
`stopped_at_operation_id`，且不返回 projection。精确 envelope 字段与顺序见
`unresolved`。

### 状态维度与不变量

| 维度 | 互斥状态 | 不变量 |
| --- | --- | --- |
| 入口 | 自动触发入口；排除入口 | 只有表中允许的入口可运行 `AdvanceDue`。 |
| schedule/authority | 匹配；缺失/旧；不可认证；live busy | rebuild 只从 active authority 派生；不可认证 fatal；busy 不创造 candidate。 |
| candidate | 无 due；R 阻塞；一个 eligible；另有 eligible | 每 invocation 最多处理一个；另有 candidate 才能产生 deferred。 |
| handoff | 未开始、authorized、prepared、accepted、failed、unknown、purge-committed | artifact 前有 durable authorization；durable accepted 后永不再调用 Adapter。 |
| retry | 未到 deadline、到 deadline、首次 crossing purge | 未到 deadline 不创建 attempt；purge 仅允许一次例外 attempt。 |
| warning | 本 invocation 新观察且可行动；历史事实 | 只有前者可投影；warning 不是 durable lifecycle result。 |

### 入口、候选与 schedule 决策表

下表先按入口分类，再按 schedule 观察分类；每行隐含前面各行均不满足，因此分区互斥。一次 invocation 至多选择一个 eligible
candidate，并至多执行一次外部 handoff；任何后续候选留给下一次 eligible invocation。

| 入口类型 | 是否可运行自动 `AdvanceDue` | 约束 |
| --- | --- | --- |
| 普通写（不含 `adopt`）、`trash`、`restore` | 是 | 仅在准入和纯 preflight 成功后运行；之后必须最终重读/重验。 |
| `read(Page|Backlinks|Links|Relations|Status|Recoverable)` | 是 | busy 时跳过自动步骤，但稳定 final projection 仍可带本次 busy warning。 |
| `initialize`、`adopt`、`read(Check)`、`recover()`、notification、所有 `DryRun` | 否 | 不因该 invocation 选择 candidate 或调用 Adapter。 |

| 已认证的 schedule/authority 观察 | 本 invocation 唯一动作 | candidate / handoff 上限 | 结果、重试与副作用 |
| --- | --- | --- | --- |
| active authority 可认证，schedule generation/digest 匹配，且无 due candidate 或有有效 R 阻塞 | 不处理 candidate | 0 / 0 | 静默继续；不产生 deferred warning。 |
| active authority 可认证，schedule generation/digest 匹配，且有 eligible candidate | 选择一个 candidate 并按下表收敛 | 最多 1 / 最多 1 | 其余 eligible candidate 只能在下一 eligible invocation 处理。 |
| schedule 缺失、旧 generation 或摘要不符 | 在 runtime root lock 下由 active authority O(A) 重建并耐久发布 schedule，然后立即结束本自动阶段 | 0 / 0 | 最终业务 action/read 可继续；下一 eligible invocation 才可选择 candidate；成功 rebuild 不产生 warning。 |
| schedule rebuild 不能耐久 | 停止 | 0 / 0 | `runtime.durability_failed`，fatal，停止该 invocation。 |
| active authority 不可认证 | 停止 | 0 / 0 | `runtime.corrupt`，fatal，停止该 invocation。 |
| 自动写子阶段实际发现 live root lock | 不取得候选/不调用 Adapter | 0 / 0 | 对稳定 read 投影为本次 `runtime.busy` warning；直接写入竞争是 error，不是 warning。 |

### Handoff 状态机、逻辑提交与 cleanup

每次 handoff 在 artifact 前必须已有精确 durable 授权，且按下列状态机执行：

```text
handoff-attempt-authorized(sequence, exact_copy_paths, members, digest)
-> copy + fsync
-> handoff-attempt-prepared
-> deliver in isolated 30s worker
-> accepted | failed | unknown
```

root lock 从 `authorized` 连续持有到 worker 实际停止、outcome、catalog transition
durable 与精确 cleanup 完成；外部调用期间不得释放。30 秒只覆盖可终止隔离的外部
调用，本地 copy 不计入 timeout；timeout 为 `unknown`。`accepted` 只表示
`handoff-accepted` outcome record 已 durable，不能由“外部可能已经收到”推断。新
sequence 向 Adapter 发送同一 Trash `operation_id`；Adapter 没有 idempotency key 或
查询回执，因此在许可重试时允许顺序重复外部包。

对 handoff 而言，逻辑提交点是 matching authority、catalog/head、outcome progress 与
不可延期授权 cleanup 全部 durable，且 projection 已 durable 或安全分类之后；此前的
`authorized`、`prepared` 或外部交付观察都不能投影为完成。

| T 的 durable 事实与时间 | 当前自动动作 | durable 后继、重试与 cleanup |
| --- | --- | --- |
| retained、未 due、无阻塞 R | 不调用 Adapter | 保留，不产生 warning。 |
| retained、due、无阻塞 R | 写 authorized，copy/fsync，写 prepared，调用 Adapter 一次 | 只能记录 durable `accepted`、`failed` 或 `unknown`。 |
| prepared 无 outcome，含外部已接受但 accepted record 未 durable | 写 `unknown`；本次不调用 Adapter | 24 小时后才能以新 sequence 重试，可能重复交付同一 T。 |
| failed/unknown，未到 `next_attempt_at` 且未首次 crossing purge | 不调用 Adapter | 保留；历史 record 不自行重播 warning。 |
| failed/unknown，已到 `next_attempt_at` | 一次新的完整 attempt | 只进入 `accepted`、`failed` 或 `unknown`。 |
| 首次 crossing purge eligibility | 可忽略旧节流执行一次新 attempt | 只有该 active call 的 explicit failed 可写 purge-committed。 |
| durable accepted 或 purge-committed | 仅按授权精确 cleanup | 此后绝不调用 Adapter。 |

Trash package 成员、active membership 与 Restore 的领域 after-state 由
`D-TRASH-RESTORE` owner 定义，本合同不重复枚举。runtime 只消费已认证的 package
digest、R -> T 阻塞关系、authority/catalog/head、matching progress/receipt 与 cleanup
授权。只有这些 runtime 事实都 durable，且具备对应授权，才可 cleanup；unknown、
failed、证据不足或 R/T 尚未 settled 时保留现场。R 建立至内容/epoch settled 前阻止
T；R 的 consumption/receipt 和双方 cleanup 必须按已认证关系收敛，不能推进 T 或清理
其 package。

### Warning、失败与重试拥有者

| warning | 本 invocation 的唯一触发事实 | 下次尝试 | 禁止的重播或降级 |
| --- | --- | --- |
| `lifecycle.handoff_failed` | 本 invocation 新 durable 写入 `handoff-failed(sequence, next_attempt_at)` | record 指定的 24 小时 deadline | failed record/active transition 无法耐久时必须为 `runtime.durability_failed`，不得只给 warning。 |
| `lifecycle.handoff_unknown` | 本 invocation 新 durable 写入 `handoff-outcome-unknown(sequence, next_attempt_at)` | record 指定的 24 小时 deadline | unknown record/active transition 无法耐久为 fatal；不得因外部可能接受而当作 accepted。 |
| `lifecycle.deferred` | 已处理一个 candidate，且同代 catalog 确有另一 eligible candidate | 下一 eligible invocation | 不写 record；not-due、R 阻塞、无 candidate 和成功 rebuild 不产生该 warning。 |
| `runtime.busy` | 自动步骤实际发现 live root lock | lock 释放后的下一 invocation | 历史 lock 或仅到达 deadline 不重播；record 损坏/no-follow 失败为 fatal。 |

warning 是 invocation-local。旧 catalog/progress、历史 live lock、仅到达
`next_attempt_at` 或 purge crossing 都不能单独产生新 warning；只有本次写出新的
failed/unknown outcome、耗尽实际预算或实际发生锁竞争时可投影。

notification adapter 只路由 `maintain(ExternalFilesChanged)`，不运行本状态机，也不
自行安排重试。重试由调用方或外部调度器触发；`recover()` 只收敛既有现场，不推进 due、
不新建 handoff、不调用 Adapter。调用方/外部调度器中究竟哪个组件拥有具体调度策略
没有已接受或已签署的单一字段或接口，保持 `unresolved`。

### 可证伪验证

- 为每类入口验证是否触发自动阶段；尤其验证 notification、`recover`、Check、
  `adopt` 与 `DryRun` 为 0 candidate/0 handoff。
- 构造 matching/stale schedule、无 due、R 阻塞、多个 due、live busy、fatal authority
  与 schedule durability failure，验证上表的 candidate/handoff 上限和结果。
- 在 authorize、copy、prepared、worker timeout、accepted/failed/unknown、catalog
  transition 和 cleanup 处故障注入，验证 lock 持有、24 小时节流、重复包边界与 durable
  accepted 后零 Adapter 调用。
- 验证 Trash/Restore cleanup 只在 authority/catalog/head/progress/receipt durable 后
  发生，且不包含非 Markdown 或 `conclusions.md`。

### `unresolved` 的跨页 ABI

| 项目 | 状态 | 阻断原因 |
| --- | --- | --- |
| handoff request/package 的字段、成员排序与 HandoffAdapter 方法 ABI | `unresolved` | 已接受或已签合同固定成员类别、durable 状态机和排除项，未定义外部调用 schema。 |
| warning 与 lifecycle result 在 `Outcome`/error envelope 中的精确字段与顺序 | `unresolved` | 已接受或已签合同只固定最多一个有序 result 和本次 warning 的语义。 |
| notification 的外部重试调度器身份、接口和退避策略 | `unresolved` | 已接受行为仅排除 adapter 自行调度，并要求调用方/外部调度器触发。 |
| cleanup 清单的具体字段名、R/T receipt 的公开字段 | `unresolved` | 已接受行为固定 durable 授权谓词，未给出公共/record ABI。 |

## 来源

- `80508340458f393435ae67669656141ec411ba1b:.scratch/draft-operation-runtime-foundation/spec.md#8. 自动生命周期与结果语义`
- `80508340458f393435ae67669656141ec411ba1b:.scratch/draft-operation-runtime-foundation/spec.md#9. Trash、Restore 与 Handoff`
