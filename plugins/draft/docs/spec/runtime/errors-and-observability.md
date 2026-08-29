# 运行时结果、错误与可观测性

## 公共结果与错误投影

本合同定义 runtime 的机器结果边界、错误、warning、notification 和退出码投影。文案
只说明已认证事实，不能成为机器协议；内部路径、staging 路径、OS lock handle、worker
实现和 consumer 内部状态不对外暴露。

### 输入与公开输出

输入是接口调用得到的已认证 root/request/authority、逻辑提交、read snapshot、
lifecycle、recovery 与 notification 事实。公开输出为 `Outcome`、`ReadOutcome`、
`RecoveryOutcome`、`InitOutcome`、`MaintenanceOutcome` 或错误 envelope；同一机器
事实由普通、JSON 和 notification 投影消费，不能各自维护另一套行为。

### 状态维度与不变量

| 维度 | 互斥状态 | 投影不变量 |
| --- | --- | --- |
| 调用结果 | 逻辑提交完成；pending；输入拒绝；runtime/lifecycle/external error；未分类错误 | 不能以文案、cache 或 partial state 把非成功投影成成功。 |
| read | 最终稳定 projection；自动 warning；fatal/不稳定 scan | 前两者按合同投影；后者无 projection。 |
| notification | delivered 允许行；error 行 | 只有允许行含最小 `delivered: true`；所有 error 行排除该字段。 |
| recovery | `forward`、`rollback`、`cleanup`、`already_settled`；认证失败 | 前四者需已认证证据；认证失败为 `runtime.corrupt`，不得伪装为 settled。 |
| 生命周期 | 本 invocation 新 warning；历史事实；fatal | 只有新的可行动事实产生 warning；fatal 不返回 projection。 |

### 结果类型的已批准边界

| 结果 | 已固定的公开语义 | 不变量 |
| --- | --- | --- |
| `Outcome` | 逻辑操作的稳定 `operation_id`；move/promote 共用 operation identity、`changed_paths`、lifecycle result、warnings 与 errors；`changed_paths` 去重并按规范化路径字典序。 | 相同 intent/authority snapshot 的 retry 复用 operation id、新增 attempt；改变任一者则新操作。未耐久逻辑提交不能投影为成功。 |
| `ReadOutcome` | 七类 read 共用一个 envelope，语义上包含适用的 snapshot token、projection、分页、warnings、blockers 与 partial。token 覆盖 root、content、active members、head 与相关 projection generation，相关 authority 改变即失效。 | 成功 read 返回 projection 与本次 actionable warnings，不带 routine lifecycle result；fatal 或最终 scan 不稳定时返回 error 且无 projection。 |
| `RecoveryOutcome` | 必须包含 `result_type`、`operation_id`、`disposition`、前后 phase、最终 authority epoch、cleanup 状态、`warnings`、`errors`；`disposition` 值域为 `forward`、`rollback`、`cleanup`、`already_settled`。 | 不暴露内部路径；已 settled 的重复 recover 不新建 attempt、不重复业务写或 Adapter 调用。 |
| `InitOutcome` | 表达 `Lifecycle.initialize` 的成功或机器失败，成功不泄露内部路径/中间状态。 | marker 未 durable 或 cleanup 未按合同完成时不得投影为首次初始化成功。 |
| notification `MaintenanceOutcome` | 只有允许的 delivered 行返回最小 `{result_type: "files_changed_notification", delivered: true}`。 | 最小 delivered 不带 warning、epoch、cache、consumer、operation 或 routine 状态；error 行绝不含 `delivered`。 |

除明确以 code literal 给出的字段外，以上表格是语义边界而非对字段名、可选性或
顺序的补造。`Outcome`、`ReadOutcome`、`RecoveryOutcome` 的完整 ABI 见本页
`unresolved` 表。

### 错误、投影与退出码决策表

notification 只调用 `maintain(ExternalFilesChanged)`。下表按从上到下的优先级，按
root/参数/maintain 观察给出完整 notification 投影；每行隐含前面各行均不满足，因而
每次通知命中且只命中一行。

| root / 参数 / maintain 观察 | notification 输出 | 退出码 | 写入、重试与恢复 |
| --- | --- | --- | --- |
| root 选择、marker、参数或 request shape 无效 | 对应 `runtime.root_invalid` 或 `runtime.request_invalid` error；无 `delivered` | 2 | 不把输入错误投影为 delivered；调用方修正后重试。 |
| PlatformAdapter 无法证明本调用所需 no-follow、atomic publish、live lock 或 durability | `runtime.platform_unsupported` error；无 `delivered` | 3 | 零写；不得以平台特例降级或继续维护。 |
| runtime schema 标识可认证但为旧版或未知版；其余已读节点没有认证矛盾 | `runtime.schema_unsupported` error；无 `delivered` | 3 | 零写、零迁移、零双读和零 cleanup；不得归入 `runtime.corrupt` 或未分类错误。 |
| marker/runtime/descriptor/authority/record 的节点、类型、摘要或关系无法认证，或 stable catalog/head/ledger 矛盾 | `runtime.corrupt` error；无 `delivered` | 3 | 保留现场，零降级成功、零维护写。 |
| marker/runtime/descriptor 匹配、无记录但仍有 InitDescriptor cleanup artifacts；或有效记录锁内重读为同一记录且无 live owner | `runtime.recovery_required` error；无 `delivered` | 3 | 零 stable root lock/record/recover/AdvanceDue/index maintenance/cleanup；目录字节不变，只有显式 recover 可收敛。 |
| 已发现有效记录的 non-blocking try-lock 失败，或无记录但 stable OS root lock live | 最小 delivered | 0 | adapter 不安排后续重试；调用方或外部调度器可在后续通知/命令再次触发维护。 |
| 已发现记录，try-lock 成功但锁内记录消失；重新 admission 后仍发生变化 | `runtime.external_change` error；无 `delivered` | 3 | 零维护写、零自动 recovery；调用方重新发起通知。 |
| 前述 admission 事实均通过，但 `maintain` 所需的完整 index/authority 闭包缺失或异常 | `runtime.closure_incomplete` error；无 `delivered` | 3 | 零维护写；补足可认证闭包后才可再次通知。 |
| 前述 admission 事实均通过，稳定 scan 发现变化，本次所需 index/authority 更新全部 durable，且没有安全分类的 cache 降级 | 最小 delivered | 0 | 只表示通知和维护已按当前事实完成；不带 warning、epoch、cache、consumer、operation 或 routine 状态。 |
| 稳定 scan 无变化 | 最小 delivered | 0 | 不添加 routine 状态。 |
| cache stage/publish 已安全分类降级，head 已 durable | 最小 delivered | 0 | 不把可重建 cache 问题投影成业务失败。 |
| scan token 因外部变化失效，但 runtime/ledger authority 仍可认证 | 最小 delivered | 0 | 不写结果；这只表示信号被接收，不表示维护完成。 |
| 外部修改未满足上述安全 scan-token 条件，或稳定 final 事实无法保持 | `runtime.external_change` error；无 `delivered` | 3 | 零维护写、零自动 recovery；调用方重新发起通知。 |
| operation/lifecycle/recovery record 的 no-replace、record file fsync、首次 `.draft/` fsync 失败，或已决定 epoch/head/catalog 结果无法耐久 | `runtime.durability_failed` error；无 `delivered` | 3 | 不报告成功；可见 record 和其他可诊断现场保留给显式 recovery。 |
| 未分类 I/O、worker 或 runtime failure | precise error；无 `delivered` | 4 | 不把“非 fatal”折叠为 delivered。 |

普通接口的退出码语义为：0 表示成功或允许的 delivered；2 表示输入/root 拒绝；3
表示 runtime/lifecycle/durability/external state；4 表示未分类错误。稳定机器错误码
至少包括 `runtime.busy`、`runtime.platform_unsupported`、`runtime.init_conflict`、
`runtime.root_invalid`、`runtime.request_invalid`、`runtime.schema_unsupported`、
`runtime.corrupt`、`runtime.external_change`、`runtime.recovery_required`、
`runtime.durability_failed`、`runtime.closure_incomplete`，以及独立 Restore 领域代码。
Restore 领域错误保持各自独立的机器 code，具体错误分类与输入条件由
`D-TRASH-RESTORE` owner 定义；runtime 不把它们折叠为单一成功、warning 或 delivered
投影。

### warning、projection 与逻辑提交

| 可观察事实 | 成功/projection 关系 | 错误或 warning 投影 |
| --- | --- | --- |
| authority/content/catalog/head/outcome 所需 commit 与不可延期 cleanup 都 durable，projection 已 durable 或安全分类 | 可投影成功；然后才可 release lock | 无 projection 成功早于该逻辑提交点。 |
| `failed`、`unknown`、`deferred`、自动阶段 busy 在本 invocation 新观察且可行动 | 成功结果可带本次 warning | 历史 record、仅到达 deadline、历史 busy 或 schedule rebuild 不重播 warning。 |
| 自动 lifecycle 已 durable 改变，后续业务 action 失败 | 该业务 action 不成功 | error envelope 最多携带一个有序 lifecycle result 和本次新 warning。 |
| 自动 lifecycle fatal、stable catalog/head 不可认证、最终 scan 不稳定/节点异常 | 不返回 projection | 机器 error，不能由 cache 结果或文案降级。 |
| cache 可分类降级且 head durable | 仍可有成功投影/notification delivered | cache 不成为 authority，不能回滚 content/head。 |

`runtime.recovery_required`、`runtime.durability_failed` 和 `runtime.corrupt` 均保留
现场，但含义不同：前者表示普通入口认证到需要显式 recover 的 pending；第二者表示
已决定结果不能可靠耐久，不能报告成功；第三者表示事实无法认证或相互矛盾，必须零写
保留。其 Q20/Q21 级别的精确证据分区仍按下文 `unresolved`，不得由这段摘要补足。

### 副作用、零写、重试与恢复

错误不会授权额外的业务写、cleanup、Adapter 调用或自动 recovery。已发现 root lock
record 的 live owner 是 busy；有效但无 live owner 的记录是 recovery pending，二者
不能互换。notification adapter 不读 projection、不调自动 lifecycle 或 `recover()`，
更不自行安排重试。重试由调用方/外部调度器触发；显式 `recover()` 是收敛 pending 的
唯一入口。

对读操作，preflight 后 lifecycle 的 change/no-change/busy 都必须取得最终稳定事实后
投影；fatal 或最终 scan 不稳定时零 projection。对写操作，durability failure、
corrupt 或 recovery pending 均不得包装为 partial success。对 recover，settled 结果的
重复调用保持零 attempt、零业务写、零 Adapter 调用。

### 可证伪验证

- 为 notification 决策表每一行验证 `result_type`/`delivered` 的存在性、退出码和
  零写要求，特别区分 `platform_unsupported`、`schema_unsupported`、
  `closure_incomplete`、安全 scan-token 的 delivered 与其余 `external_change`、live
  busy、无 live owner 的 `recovery_required`，以及 record 发布/file fsync/首次
  `.draft/` fsync 的 `durability_failed`；同时为闭包缺失叠加 busy、stale record、
  record 消失或 corrupt 的组合验证前述 admission 行优先命中，并覆盖“发现变化且
  维护全部 durable”的正常 delivered 行。
- 验证成功 read、lifecycle warning、fatal lifecycle、最终 scan 失效和 cache 降级的
  projection/错误互斥关系。
- 验证所有列出的 runtime code 在相应条件下稳定出现，Restore 的七类错误不折叠，
  文案变化不破坏机器协议。
- 验证重复 recover、durable accepted 后 handoff 与 notification 不产生额外 Adapter
  调用或内部路径泄露。

### `unresolved` 的公共 ABI 与跨页接缝

| 接缝或字段 | 状态 | 缺少的正式 source / 阻断原因 |
| --- | --- | --- |
| S1：`operation_id` 的生成、intent/snapshot 比较、旧 operation 关联、attempt/phase 的公开位置 | `unresolved` | 已接受 identity/retry 语义未提供 signer-approved 的字段 schema 与顺序。 |
| S2：每个领域入口进入同一逻辑提交的 machine-zone/导航成员，以及 authority 已替换、progress 缺失时的 `Outcome` | `unresolved` | 已接受原子性未逐入口给 payload，Q21 progress 细分没有同等级 acceptance/signer locator。 |
| S3：snapshot token 编码、`ReadOutcome` token/projection/pagination/warnings/blockers/partial 的字段名、可选性和顺序 | `unresolved` | Q13/Q14 给出统一语义，未给出公共 ABI。 |
| S4：notification 的外部重试拥有者、warning/lifecycle result 的 envelope 顺序 | `unresolved` | Q8 只禁止 adapter 自行重试；没有唯一调度接口或结果 ABI。 |
| S5：Trash/Restore package、receipt、attempt、catalog membership 的精确成员、排序与 cleanup 记录字段 | `unresolved` | 已接受包边界/耐久授权谓词，未给出跨 owner record schema。 |
| S6：manifest mapping、自动 collapse 子阶段和 `changed_paths` 与领域 effects 的完整公共关联字段 | `unresolved` | Q11 固定去重/排序与同 operation，不定义 manifest/Outcome 的完整 schema。 |
| S7：`Outcome`/`ReadOutcome` 的完整字段集合、错误 envelope 与普通 JSON 的字段顺序 | `unresolved` | 已接受或已签语义和 code/exit 规则未形成统一 public protocol section。 |
| S8：`RecoveryOutcome` 前后 phase、epoch、cleanup state 字段和 Q20/Q21 的 orphan/progress 证据分区 | `unresolved` | Q17 只列语义；`accepted-decisions.md` 只有 Q1–Q19 同等级接受元数据，缺少 Q20/Q21 的独立 signer locator。 |

`RecoveryOutcome` 的以下字段边界必须同时成立：`result_type`、`operation_id`、
`disposition`、`warnings`、`errors` 是已命名的语义字段；前 phase、后 phase、最终
authority epoch 与 cleanup 状态必须存在但其精确字段名、值域、可选性和顺序均为
`unresolved`。不得为了填表发明诸如 `before_phase`、`after_phase`、`authority_epoch`
或 `cleanup_state` 的 ABI。

## 来源

- `ce64ac30c52ca910fe39fcedd055dd28f0cfbda4:.scratch/draft-operation-runtime-foundation/spec.md#11-输出错误与验收`
