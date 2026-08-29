# 显式恢复

## 显式恢复与 RecoveryOutcome

本合同拥有 recovery-pending 现场的显式收敛。普通入口只认证并阻塞；只有 root-bound
`recover()` 可以取得运行期 root lock、读取持久证据并尝试 `forward`、`rollback`、
`cleanup` 或 `already_settled`。它不定义新的业务操作，也不把目录枚举、终态审计或
外部“可能已接受”当作证据。

### 输入与公开输出

`recover()` 无业务 request 输入，输入是当前 root 的 stable OS lock、运行期根锁记录、
marker/runtime/descriptor、operation manifest/progress、owner/intent、before/after
authority、immutable transition/epoch、staging、R/T/handoff 记录与 cleanup artifacts。
公开输出为 `RecoveryOutcome` 或机器错误。`RecoveryOutcome` 必须表达本次 disposition、
前后 phase、最终 authority epoch、cleanup 状态和 warnings/errors，但不暴露内部路径；
字段 ABI 的已知和未决边界见本页末尾及
[公共结果与错误投影](errors-and-observability.md#公共结果与错误投影)。

### 状态维度与不变量

| 维度 | 互斥状态 | 恢复不变量 |
| --- | --- | --- |
| 运行期记录/lock | 无记录；有效记录且 live owner；有效记录且无 live owner；损坏/无法认证 | live owner 不抢占；损坏记录不删除、不猜 owner。 |
| init 现场 | 无 marker；marker/runtime/descriptor 匹配且 cleanup remnant；已收敛 | 无 marker 的初始化现场不属于 runtime recover；descriptor cleanup 只能按精确清单处理。 |
| authority 证据 | 完整 before；完整 durable after；matching partial after；矛盾/不可认证 | 先认证，再分类；不能从 phase 名、mtime、目录或 terminal projection 猜测。 |
| 外部副作用 | 无；已授权但未可证明 outcome；durable accepted/receipt | 没有 durable accepted 不得推断接受或 cleanup/purge。 |
| staging | 已认证且已授权的精确 cleanup material；无法认证/不匹配 staging；需要 Q20 orphan 资格谓词的 staging | 无法认证时 fail-closed；Q20 的 orphan 资格谓词仍无同等级 signer locator。 |
| progress | matching committed；缺失；伪造/不匹配 | normal/read/Check 不枚举 terminal progress；精确 recovery discovery/补记细则未签。 |

不变量如下：

- 不能认证或彼此矛盾的事实一律 `runtime.corrupt`、零写并保留现场；不得删除、
  降级为成功或生成第二 authority。
- 完整 after authority durable 时仅可 `forward`；matching partial new authority 仅可
  `forward`。仅完整 before 且无外部副作用时，已认证的 durable rollback direction
  唯一决定 `rollback`，已认证且唯一授权 cleanup 的 durable evidence 与精确 material
  唯一决定 `cleanup`；没有这两种决定事实时保持 `unresolved`。这些是已接受的证据
  优先次序，不能以“通常成功”覆盖。
- 已收敛现场重复 `recover()` 必须给出确定的已收敛结果，不新建 attempt、不重复业务
  写、不调用 Adapter。
- `recover()` 不推进 due、不新建 handoff、不调用 Adapter；handoff prepared/unknown
  现场只能收敛已写 record，不能借恢复新建 attempt。

### 证据优先恢复决策表

下表按从上到下的优先级求值。每行均要求锁内重读；无法落入已接受或已签分区的事实停在
`unresolved` 行，不能由实现选择语义。

| 认证后的持久事实 | 外部副作用/进度事实 | 唯一 disposition 或机器结果 | 写入、零写、重试与恢复 |
| --- | --- | --- | --- |
| runtime schema 标识可认证，但为旧版或未知版；其余已读节点没有认证矛盾 | 任意 | `runtime.schema_unsupported` | 零写、零迁移、零双读和零 cleanup；不得把可识别的版本不兼容降级为 `runtime.corrupt`。 |
| 运行期记录损坏、owner/intent/authority/epoch/digest/staging 无法认证，或任何已认证事实互相矛盾 | 任意 | `runtime.corrupt` | 零写、保留所有现场；不得删除、猜 owner、扫描 terminal 或降级成功。 |
| 有效运行期记录且 stable OS lock live | 任意 | `runtime.busy` | 零写；不得抢占、删除记录或启动另一个 recovery。 |
| 无 marker 的初始化现场 | 任意 | 不属于此 runtime 恢复合同 | 零创建、零删除、零猜测；不得把它转为运行期现场。 |
| marker/runtime/descriptor 匹配、无运行期记录、精确 descriptor cleanup artifacts 尚存 | 无其他矛盾 | `cleanup` | 取得 stable lock，锁内确认仍无记录后 durable 发布 recovery 记录，按 descriptor 精确幂等 cleanup；完成 unlink/fsync/release 后 settled。 |
| 无运行期记录、无 descriptor cleanup remnant、无其他可认证 pending 现场 | 任意 | `already_settled` | 不例行发布 recovery record、不枚举 terminal progress、不产生业务写。 |
| durable `accepted` 或 `purge-committed` handoff record，且 authority/catalog/head/progress/receipt 与精确 cleanup authorization 均匹配 | 已无其他 pending | `cleanup` | 仅清理已授权精确 material；此后绝不调用 Adapter。任何缺失或矛盾事实由前面的 corrupt 行或后面的 `unresolved` 行处理。 |
| matching handoff `prepared`，但没有 durable outcome；包括外部已接受而 accepted record 未 durable | 无 durable `accepted`/`failed`/`unknown` outcome | `unresolved` | 已接受的耐久动作仅为写入 `handoff-outcome-unknown`；本次不调用 Adapter、不新建 attempt，之后按该 record 的 24 小时 deadline 由下一 eligible invocation 重试。该动作与 `RecoveryOutcome.disposition` 的公开映射未获同等级 ABI 来源，保持 `unresolved`。 |
| matching handoff `authorized`，但尚未 durable `prepared` 或 outcome | 任意 | `unresolved` | 不得新建 attempt、调用 Adapter、cleanup 或把它解释为 prepared；authorized-only 的精确恢复写入和公开结果没有同等级来源。 |
| R/T consumption 或 receipt record 匹配，且唯一缺少已认证的下一 durable proof | 不满足 cleanup 条件 | `forward` | 仅完成该关系的精确 proof/receipt；保持 T 的阻塞，不推进 T、也不清理 package。 |
| R receipt durable，双方 authority 与精确 cleanup authorization 匹配 | 已无其他 pending | `cleanup` | 仅清理双方已授权 material；不得 handoff、purge 或重建关系。 |
| staging 的 cleanup 资格只能通过 manifest/owner/intent/epoch/cleanup list 的 Q20 orphan predicate 决定 | 任意 | `unresolved` | Q16 仅接受 cleanup 的高层 disposition；Q20 的精确 orphan/unknown-staging 分区、资格和清理范围未获同等级接受，不得自行清理。 |
| 已认证且已授权的精确 cleanup material，且没有竞争的 authority、R/T 或 handoff pending | 任意 | `cleanup` | 只按精确清单幂等 cleanup；缺失成员可跳过，类型、路径或摘要变化转为 `runtime.corrupt`。 |
| 完整 durable after authority | matching 或尚缺 progress，且没有前述 handoff/R-T/cleanup pending | `forward` | 不得回滚或建立第二 authority。缺少 matching progress 的精确补记权限、记录和结果投影见下方 `unresolved`；在签署前不能宣布为 settled。 |
| matching partial new authority | 已认证且不矛盾，且没有前述 handoff/R-T/cleanup pending | `forward` | 只可向已授权的 after state 收敛；不得推断新的 membership/head 或扫描其他 terminal record。 |
| 仅完整 before authority，已认证 durable direction 为 rollback | 可证明无外部副作用 | `rollback` | 仅按该 direction 与其精确 material 回退；不得以 applied 位或目录存在猜测。 |
| 仅完整 before authority，存在已认证且唯一授权 cleanup 的 durable evidence，且精确 material 已获授权 | 可证明无外部副作用 | `cleanup` | 仅清理该清单列举的 material；不得产生新的 authority。 |
| 任何未被前述行处理的 handoff、R/T、cleanup pending；或仅完整 before authority 但没有可认证的 durable rollback direction 或唯一 cleanup evidence；或存在未被前述行覆盖的外部副作用或 receipt 证据 | 任意 | `unresolved` | 不得 rollback、cleanup、重试 Adapter 或报告成功；保留现场，缺少用于选择 `runtime.recovery_required`/其他结果的同等级授权。 |
| 已完成上述必要 authority/content/catalog/head/outcome commit、授权 cleanup，projection 已 durable 或安全分类，且无未认证 staging/pending | 无剩余现场 | `already_settled` | 仅此时 unlink record、fsync `.draft/`、释放 lock 并返回 settled。 |

### 允许转换与逻辑提交

```text
pending --handoff prepared + no outcome------------> durable unknown --> lifecycle retry state
pending --证据为完整 after 或 matching partial----> forward --> settled
pending --before + durable rollback direction-----> rollback --> settled
pending --before + unique cleanup evidence/material-> cleanup --> settled
pending --仅已授权 cleanup material--------------> cleanup --> settled
no pending ---------------------------------------> already_settled
untrusted / contradictory ------------------------> runtime.corrupt (现场保留)
```

`forward`、`rollback` 和 `cleanup` 都不是凭内存 phase 的转换。它们的逻辑提交点是：
所有应完成的 authority/content/catalog/head/outcome commit 与不可延期授权 cleanup 都
已 durable，projection 已 durable 或安全分类为可重建/可降级，且没有未认证 staging 或
其他 pending；随后 record unlink 与 `.draft/` 最终 fsync 成功。任一耐久性失败不返回
settled 成功，记录保留供下一次显式 recover。

恢复只在有效记录无 live owner，或 descriptor cleanup remnant 已按锁内重读确认时
发布/保持 recovery record。normal operation、read、Check、`DryRun` 和 notification
不删除记录、不隐式 recover，也不枚举 terminal progress。重复 settled recover 的
逻辑转换为 `already_settled`，并保持零 attempt、零业务写、零 Adapter 调用。

### 错误分类的已接受或已签边界与 `unresolved`

| 结果 | 当前可确认的边界 | 未决原因 |
| --- | --- | --- |
| `runtime.corrupt` | 无法认证或矛盾的 authority/record/staging 必须零写保留。 | 已接受 Q16 足以固定这一 fail-closed 分支。 |
| `runtime.recovery_required` | 普通入口遇到有效未完成记录必须阻塞，不得隐式清理。 | Q21 所述“已知未完成阶段”的完整证据分区未有同等级 acceptance/signer locator。 |
| `runtime.durability_failed` | 耐久性失败不得被报告为成功，现场必须保留。 | Q21 所述“完整旧 authority 且仅耐久失败”的精确判定和公开投影未有同等级 locator。 |
| authority 已 durable、progress 未补记 | 只允许高层 `forward`，不得回滚。 | Q21 的精确 progress 补记动作、条件和 `RecoveryOutcome` 投影未获同等级接受。 |
| orphan/unknown staging | 无法认证时落入 `runtime.corrupt`；已接受 Q16 允许已认证 cleanup 类别。 | Q20 的“合法 manifest/owner/intent/epoch/cleanup list”谓词、orphan/unknown 分区与清理范围未获同等级接受。 |

上述未决不是许可实现作出默认选择。缺少的来源是：`accepted-decisions.md` 只明确
Q1–Q19 有同等级接受元数据，且不存在把 runtime Q20/Q21 细分签入当前正式合同的
独立 signer locator。因此这些项继续阻断 cut-over，直到签署来源给出精确规则。

### 可证伪验证

- 构造完整 before 的 rollback direction、唯一 cleanup evidence、缺失 evidence，完整
  after、matching partial、authorized-only、prepared 无 outcome、durable accepted、R/T
  proof/receipt、Q20 orphan-candidate staging、已授权 exact cleanup、矛盾 authority、无
  marker init 现场、descriptor cleanup remnant、live lock、stale record 与无 pending
  现场，验证每次只命中决策表一行。
- 验证 unauthentic/contradictory 事实零写保留；after/partial 不回滚；prepared 无
  outcome 必须 durable 写入 `unknown` 且不调用 Adapter，但其公开 disposition 仍为
  `unresolved`；authorized-only 不被解释为 prepared；before 只能由其唯一认证的
  direction 进入 rollback 或 cleanup；任何外部可能接受不被当作 accepted。
- 对已 settled 现场连续执行两次 `recover()`，验证第二次没有新 attempt、业务写或
  Adapter 调用，且返回确定 `already_settled` 语义。
- 对 Q20/Q21 行只验证它们仍显式为 `unresolved`，不能用实现、测试、目录扫描或
  recovery fixture 充当缺失的 acceptance 证据。

### `RecoveryOutcome` ABI 边界

`RecoveryOutcome` 必须包含下列已接受的语义：`result_type`、`operation_id`、
`disposition`、前后 phase、最终 authority epoch、cleanup 状态、`warnings`、`errors`，
并且不暴露内部路径。`disposition` 的值域固定为 `forward`、`rollback`、`cleanup`、
`already_settled`。

| 项目 | 状态 | 阻断原因 |
| --- | --- | --- |
| `result_type`、`operation_id`、`disposition`、`warnings`、`errors` 的字段顺序及同结果的存在性组合 | `unresolved` | 已接受 Q17 列出字段语义，未签署统一排序/可选性 ABI。 |
| 前 phase、后 phase 的字段名和值域 | `unresolved` | Q17 规定必须表达前后 phase，未给出字段名或 phase enum。 |
| 最终 authority epoch 的字段名、编码与缺失规则 | `unresolved` | Q17 规定语义，未给出字段 ABI。 |
| cleanup 状态的字段名、值域和与 disposition 的组合 | `unresolved` | Q17 规定语义，未给出字段 ABI。 |

## 来源

- `ce64ac30c52ca910fe39fcedd055dd28f0cfbda4:.scratch/draft-operation-runtime-foundation/spec.md#10-崩溃恢复表`
