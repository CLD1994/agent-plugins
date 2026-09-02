# 显式恢复

## 显式恢复与 RecoveryOutcome

本合同拥有 recovery-pending 现场的显式收敛。普通入口只认证并阻塞；只有 root-bound
`recover()` 可以取得运行期 root lock、读取持久证据并完成已授权的前向收敛、回退或清理。
它不定义新业务操作，也不把目录枚举、终态审计、mtime、内部 phase 或外部“可能已接受”当作
证据。

`recover()` 没有业务 request、operation 选择字段或 mode。它不支持 DryRun，不推进 due、不
新建 handoff，也不调用内部 handoff 适配器。公开成功结果、字段出现条件和禁止成员严格使用
[公共结果、错误与可观测性](errors-and-observability.md#公共结果错误与可观测性)定义的
`RecoveryOutcome`；本页只把已认证恢复事实映射到其中的领域 outcome、调用方动作和内部动作。
成功不公开内部 phase、路径、记录、authority version、恢复前后状态、是否曾重复发送，且不带
error、建议或顶层告警。任何需要阻断调用方的事实都返回机器 error，不返回不完整成功。

内部成功必须消除本次 recovery overlay；稳定存在的 Trash 阻塞仍是领域状态，不是 recovery
pending。重复调用已经收敛的现场只返回 `already_settled`，不新建 attempt、不重复业务写、
不调用 handoff 适配器。

### 调用方动作与公共 outcome

下表只拥有恢复事实到公共 outcome、调用方动作和内部动作的领域映射；公共字段的形状、出现
条件和禁止成员见 [RecoveryOutcome owner](errors-and-observability.md#公共结果错误与可观测性)。

| 已认证并收敛的事实 | 公共 `outcome` | 调用方动作 |
| --- | --- | --- |
| 外部发送前只完成 authorization；本次被取消并清理其 material | `cleaned_up` | `caller_action: retry_now` |
| 已发送或 prepared，但没有可证明的 durable outcome；本次已写入 unknown | `completed` | `caller_action: retry_later` 与 record 的 `retry_at` |
| durable accepted、purge 或 receipt 证据，只完成精确 cleanup | `cleaned_up` | 省略 |
| 普通 operation 向已授权 after authority 收敛 | `completed` | 省略 |
| 已认证 rollback direction 的回退完成 | `rolled_back` | 省略 |
| descriptor 或已证明无主临时 material 的内部清理完成 | `cleaned_up` | 省略 |
| 没有 pending 现场，或现场已收敛 | `already_settled` | 省略 |

已有 durable accepted/receipt 时绝不再调用适配器是 runtime 内部强制规则，不是额外公共动作值。
等待期限只通过 `retry_later` 与 `retry_at` 表达，不提供命令式重发或“不重发”字段。

### 状态维度与不变量

| 维度 | 互斥状态 | 恢复不变量 |
| --- | --- | --- |
| 运行期 record/lock | 无 record；有效 record 且 live owner；有效 record 且无 live owner；损坏/无法认证 | 不抢占 live owner；不删除损坏 record、不猜 owner。 |
| init 现场 | 无 marker；marker/runtime/descriptor 匹配且 cleanup remnant；已收敛 | 无 marker 初始化现场不属于 root-bound runtime；descriptor cleanup 只按精确清单处理。 |
| authority 证据 | 完整变更前；完整 durable after；已认证但未完整的 after；矛盾/无法认证 | 先认证，再分类；不从 phase、mtime 或目录猜测。 |
| D-MOVE eligibility snapshot | 由下方 `S` gate 分类 | 仅通用先验门禁已路由的 pending D-MOVE 进入该 gate。 |
| 外部副作用 | 无；已授权未有可证明结果；durable accepted/receipt | 没有 durable accepted 不得推断接受、cleanup 或 purge。 |
| staging | 精确授权 material；完整所有权闭包证明无主；证据不足；无法认证/矛盾 | 只有前两类可清理；后两类保留并报错。 |
| progress | matching committed；完整 after authority 的唯一缺口；其他缺失；伪造/不匹配 | 只有唯一缺口可补写；其他缺失保留并报错。 |

完整 durable after authority 只可向后收敛，已认证但未完整的新 authority 也只可向已授权 after
state 收敛。完整 before authority 且可证明无外部副作用时，只有已认证 durable rollback direction
可授权回退；独立且唯一授权的 cleanup evidence 只可授权精确清理。没有决定证据时保留现场并
返回 `runtime.recovery_blocked`。

### Recovery 通用先验门禁

[准入与操作计划](admission-and-effects.md#准入与操作计划)拥有 request、root 和平台准入。root
未能认证时，factory/入口返回 `runtime.root_invalid`，不创建 runtime recovery 现场。通过准入后，
以下行按从上至下顺序决定 `recover()` 能否进入专属或通用证据表：

| 顺序 | 已认证事实 | `recover()` 结果 | 后续 |
| ---: | --- | --- | --- |
| 1 | schema 标识不可认证，或任一已读非 `S` 节点不可认证/明确矛盾 | `runtime.corrupt`，零写 | 停止；锁和 operation kind 都不能覆盖。 |
| 2 | schema 可认证为旧/未知，其他已读非 `S` 节点无矛盾 | `runtime.schema_unsupported`，零写 | 零迁移、零双读、零 cleanup；不读取或合成 `S`。 |
| 3 | supported v1、其他已读事实可认证、有效 record 有 live owner | `runtime.busy`，零写 | 不进入 D-MOVE gate。 |
| 4 | supported v1、其他已读事实可认证、无 record 但 stable OS lock live | `runtime.busy`，零写 | 不进入 D-MOVE gate。 |
| 5 | supported v1、有效 pending record 无 live owner，通用事实无矛盾且已取得 stable lock | 按 operation kind 路由 | pending D-MOVE 进入 `S` gate；其他 pending 进入通用证据表。 |
| 6 | 其他已认证恢复现场 | 进入通用证据表 | 不额外要求 `S`。 |

### D-MOVE snapshot S 恢复门禁与 Q6-B 漂移分类

此 gate 只在通用先验门禁第 5 行已路由 pending `D-MOVE` 后求值；不再次判断 schema、其他
节点损坏、live owner 或 live lock。`S` 的领域载荷见
[D-MOVE snapshot S 语义载荷](../draft/structure.md#d-move-snapshot-s-语义载荷)。

| `S` 的互斥状态 | gate 结果 | 后续 |
| --- | --- | --- |
| immutable/完整，摘要与关联 authority 事实匹配，当前事实无漂移，锁内重认证成功 | pass | 只获得通用证据表继续资格；gate 本身不授权写或 cleanup。 |
| 纯缺失、不完整、资格未证明，或没有明确矛盾但锁内重认证未成功 | `runtime.recovery_blocked`（证据分类 `evidence_missing`） | 零 authority/projection/cleanup/业务写，保留现场。 |
| 摘要或关联 authority 事实不匹配，或当前事实明确漂移/矛盾 | `runtime.corrupt` | 零写并保留现场。 |

### 证据优先恢复表

下表只在通用先验门禁完成路由后求值：pending `D-MOVE` 必须先获得 `S` gate pass，其他现场
直接进入。本表不重新选择 schema、corrupt、busy 或 `S` 的结果。可认证且无矛盾、但不足以证明
安全动作的现场必须落入 `runtime.recovery_blocked`。

| 认证后的持久事实 | 外部副作用/进度事实 | 唯一内部动作或机器结果 | 结果与边界 |
| --- | --- | --- | --- |
| marker/runtime/descriptor 匹配、无运行期 record、descriptor cleanup artifacts 尚存 | 无其他矛盾 | 清理 descriptor material | 锁内确认仍无 record，发布 recovery record，精确幂等 cleanup；成功为 `cleaned_up`。 |
| 无 record、无 descriptor remnant、无其他 pending | 任意 | 不产生动作 | 成功为 `already_settled`；不枚举终态 progress。 |
| durable accepted 或 purge-committed，authority/catalog/head/progress/receipt 与 cleanup authorization 匹配 | 无其他 pending | 精确 cleanup | 不调适配器，成功为 `cleaned_up`。 |
| matching handoff prepared，无 durable outcome | 无 durable accepted/failed/unknown | durable 写 unknown | 不调适配器、不建 attempt；成功为 `completed`，返回 `retry_later/retry_at`。 |
| matching handoff authorized，尚无 prepared/outcome | 状态机证明适配器未被调用 | 取消并清理 authorization material | 保留 Trash active，不写 24 小时 deadline；成功为 `cleaned_up`，返回 `retry_now`。 |
| R/T consumption 或 receipt 只缺唯一下一 durable proof | 不满足 cleanup 条件 | 补写精确 proof/receipt | 保持 T 阻塞，不推进 T 或清理 package；成功映射为 `completed`。 |
| R receipt durable，双方 authority 与 cleanup authorization 匹配 | 无其他 pending | 精确双方 cleanup | 不 handoff、不 purge、不重建关系；成功为 `cleaned_up`。 |
| 所有 authority、有效 pending operation、manifest、intent、package 与 cleanup 授权完整认证，某临时 material 无引用 | 任意 | 精确删除 material | fsync 所在目录；成功为 `cleaned_up`。 |
| 临时 material 任一所有权来源缺失/不完整，或无矛盾但不足以证明无主 | 任意 | `runtime.recovery_blocked`（证据分类 `ownership_unproven`） | 零 cleanup/authority 写。 |
| 已认证精确授权 cleanup material，无竞争 pending | 任意 | 幂等 cleanup | 缺失成员可跳过；类型、路径或 digest 变化是 `runtime.corrupt`。 |
| 完整 durable after authority，matching progress durable | 无前述 handoff/R-T/cleanup pending | 向后收敛 | 不回退、不建立第二 authority；成功为 `completed`。 |
| 完整 durable after authority，owner/request/before-after/generation/digest 与其他 pending 一致 | 唯一缺 matching committed progress | 补写 matching progress | 耐久后继续投影/cleanup；成功为 `completed`。 |
| 已认证但未完整的新 authority | 已认证无矛盾，无前述 pending | 向后收敛 | 不推断新 membership/head 或扫描终态 record；成功为 `completed`。 |
| 仅完整 before authority | 已认证 durable rollback direction，且无外部副作用 | 精确回退 | 成功为 `rolled_back`。 |
| 仅完整 before authority | 已认证唯一 cleanup evidence 与精确 material，且无外部副作用 | 精确 cleanup | 不产生新 authority；成功为 `cleaned_up`。 |
| 任何未被前述行处理的 handoff/R-T/cleanup pending，或没有 rollback/cleanup 决定证据的完整 before | 任意 | `runtime.recovery_blocked`（证据分类 `action_ambiguous`） | 保留现场。 |

任何内部动作的逻辑提交都要求 authority/content/catalog/head/outcome commit、不可延期授权 cleanup
均 durable，且派生资料 durable 或安全分类，随后 recovery record unlink 和 `.draft/` 最终 fsync
成功。任一耐久失败为 `runtime.durability_failed`，不得返回成功。所有成功都必须清除本次
overlay；若仍有 pending，调用不能成功。

### 可证伪验证

- 构造完整 before 的 rollback direction、唯一 cleanup evidence、完整 after、已认证但未完整的 after、
  authorized-only、prepared 无 outcome、durable accepted、R/T receipt、无主 staging、所有权
  来源缺失、descriptor remnant、live lock、stale record 和无 pending，验证唯一结果。
- 验证损坏/矛盾事实零写保留；完整 after 唯一缺 progress 时精确补写；prepared 无 outcome
  必须 durable 写 unknown 且不调用适配器；authorized-only 可立即重试而不被误判为 prepared。
- 连续执行已收敛 recover，验证第二次没有新 attempt、业务写或 handoff 调用，只返回
  `already_settled`。
- 验证所有成功只含规定的 recovery 字段，阻断分支只返回机器 error；不泄露内部 authority
  版本、phase、路径或发送标志。

## 来源

- `ce64ac30c52ca910fe39fcedd055dd28f0cfbda4:.scratch/draft-operation-runtime-foundation/spec.md#10-崩溃恢复表`
