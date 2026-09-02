# 运行时生命周期与交接

## 自动生命周期与交接

本合同拥有自动 `AdvanceDue`、Trash/Restore handoff、外部交付 attempt 与授权 cleanup。它不
改变页面、关系、package 成员或 Restore 的领域 after-state；它只消费那些已经由领域 owner
认证的事实并决定 runtime 的耐久动作。

自动工作是 Draft 私有生命周期。它不向 `OperationOutcome`、`QueryOutcome`、
`RecoveryOutcome`、notification 成功或 error envelope 添加自动处理、顶层告警、候选、
等待时间、外部回执、cache 或内部 busy 字段。自动工作若不能让当前调用
安全完成，调用只返回相应机器 error；若不影响当前可靠结果，调用方看不到该内部事件。

### 入口与候选选择

| 入口类型 | 是否可运行 `AdvanceDue` | 约束 |
| --- | --- | --- |
| 普通写（不含 `adopt`）、`trash`、`restore` | 是 | 仅在准入和 pure preflight 成功后；之后必须最终重读/重验。 |
| `read(page|links|backlinks|relations|status|recoverable)` | 是 | 需要写入时才尝试 root lock；锁忙时跳过自动写子阶段，仍可返回稳定查询。 |
| `initialize`、`adopt`、`read(check)`、`recover()`、`maintain({})`、所有 DryRun | 否 | 不选择 candidate，不调用外部 handoff。 |

一次 eligible invocation 最多选择一个 candidate，最多调用一次外部 handoff。schedule 与
active authority 匹配且没有 due candidate、或 candidate 有有效 R 阻塞时，静默继续。schedule
缺失、旧 generation 或摘要不符时，runtime 在 root lock 下从 active authority 以 O(A) 重建并
durable 发布 schedule，然后结束本自动阶段；下一次 eligible invocation 才可选择 candidate。
authority 无法认证为 `runtime.corrupt`，schedule rebuild 不能耐久为
`runtime.durability_failed`。锁忙不创造 candidate、不调适配器。

### Trash 时间线与状态表

`t0` 是 Trash 完整逻辑提交可靠保存的 UTC 时刻。其时间线固定为：

```text
t0 ---------------------------------- retained，仍可 Restore
t0 + 7 × 24 hours -------------------- Restore 到期；开始允许 automatic handoff
t0 + 30 × 24 hours ------------------- 进入 purge eligibility
```

30 天从 `t0` 计算，不是从 Restore 到期后再计算。没有后续 eligible invocation 时，时间到达
本身不触发后台工作、重试、purge 或 cleanup。

| T 的 durable 事实与当前时间 | 当前自动动作 | durable 后继、重试与 cleanup |
| --- | --- | --- |
| retained，当前早于 `t0 + 7×24h`，无 R 阻塞 | 不调用外部适配器 | 保留，Restore 仍由领域期限决定。 |
| retained，已到 handoff 时刻，无 R 阻塞 | 写 authorized，copy/fsync，写 prepared，调用一次适配器 | 只记录 durable `accepted`、`failed` 或 `unknown`。 |
| authorized，尚无 prepared/outcome 的 pending 现场 | 普通自动阶段不处理 | 显式 recover 取消本次 attempt，只清理其精确 material，保留 T active；不写 retry deadline，下一 eligible 普通调用可立即重试。 |
| prepared，无 durable outcome | 普通自动阶段不再次调用适配器 | 显式 recover 写入 durable `unknown`；下一次 attempt 最早在记录的 24 小时 deadline。 |
| failed 或 unknown，尚未到 `next_attempt_at`，且未首次跨入 purge eligibility | 不调用适配器 | 保留；不因历史记录重复公开任何状态。 |
| failed 或 unknown，已到 `next_attempt_at` | 一次新完整 attempt | 只可成为 `accepted`、`failed` 或 `unknown`。 |
| 首次跨入 purge eligibility | 可忽略旧节流进行一次新 attempt | 只有该次实际交付明确得到 `failed` 时，才可 durable 写 `purge-committed` 并 cleanup；`unknown` 继续保留。 |
| durable `accepted` | 仅按授权精确 cleanup | 可立即 cleanup，此后绝不调用适配器。 |
| durable `purge-committed` | 仅按授权精确 cleanup | 此后绝不调用适配器。 |

`failed` 与 `unknown` 的普通重试间隔恰为 24 小时，且只由下一次 eligible 普通调用触发。最终
purge attempt 也没有后台推进；它只是第一次有 eligible 调用观察到进入 30 天资格时的一次
例外 attempt。`unknown` 绝不因可能已收到包、30 天到达或清理愿望而视为 accepted/purge。

Trash package 成员、active membership、Restore conflict 与 R -> T 阻塞由
[Trash 与 Restore](../draft/trash-and-restore.md#行为合同)定义。runtime 只消费
可认证 package digest、阻塞关系、authority/catalog/head、matching progress/receipt 与 cleanup
授权。R 建立到 content/epoch settled 前阻止 T；R 的 consumption/receipt 和双方 cleanup 必须按
认证关系收敛，不能推进 T 或清理其 package。

### Handoff attempt、逻辑提交与恢复

每个 handoff 在 artifact 前必须有精确 durable 授权，内部状态机如下：

```text
handoff-attempt-authorized(sequence, exact_copy_paths, members, digest)
-> copy + fsync
-> handoff-attempt-prepared
-> isolated 30-second delivery worker
-> accepted | failed | unknown
```

适配器的方法、包格式、回执格式、sequence 编码和 copy 目录是 Draft 私有实现，不是第三方
集成协议。root lock 从 `authorized` 持有到 worker 实际停止、outcome、catalog transition durable
与精确 cleanup 完成；外部调用期间不得释放。30 秒只覆盖可终止隔离的外部调用，本地 copy
不计入 timeout；timeout 是 `unknown`。`accepted` 只在 matching `handoff-accepted` record
durable 后成立，不能从外部可能收到推断。重试会以新 sequence 发送同一 Trash operation，当前
接收方没有 idempotency key 或回执查询时，许可重试可产生顺序重复包。

逻辑提交要求 matching authority、catalog/head、outcome progress、不可延期授权 cleanup 都
durable，且派生资料 durable 或安全分类；此前的 authorization、prepared 或外部观察都不是
公开成功。未知、失败、证据不足或 R/T 尚未 settled 时必须保留现场。

显式 recover 只收敛既有现场：authorized-only 取消并清理授权 material；prepared-without-outcome
写入 `unknown`；durable accepted/purge 只 cleanup。它不推进 due、不新建 handoff、不调用外部
适配器。其公共结果使用 recovery `outcome` 与条件性调用方动作，见
[显式恢复](recovery.md#显式恢复与-recoveryoutcome)。

### 可证伪验证

- 为每类入口验证是否运行自动阶段；notification、recover、Check、adopt 与 DryRun 必须是
  0 candidate/0 handoff。
- 覆盖 `t0` 前、恰到 7 天、失败/unknown 的 24 小时边界、恰到 30 天、durable accepted、
  explicit failed 的 purge 和 unknown 的 purge，验证状态表唯一命中。
- 在 authorize、copy、prepared、worker timeout、accepted/failed/unknown、catalog transition
  与 cleanup 注入失败，验证 lock 持有、现场保留、授权边界和不重复适配器调用。
- 验证没有后续调用时不发生重试或 cleanup，成功和错误结果都不泄露自动生命周期字段。

## 来源

- `ce64ac30c52ca910fe39fcedd055dd28f0cfbda4:.scratch/draft-operation-runtime-foundation/spec.md#8-自动生命周期与结果语义`
- `ce64ac30c52ca910fe39fcedd055dd28f0cfbda4:.scratch/draft-operation-runtime-foundation/spec.md#9-trashrestore-与-handoff`
