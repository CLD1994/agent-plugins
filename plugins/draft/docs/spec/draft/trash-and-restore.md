# Trash 与 Restore

## 行为合同

`D-TRASH-RESTORE` 定义受管理 Markdown 页面子树的回收包、活动成员资格、7 天恢复窗口
和恢复冲突边界。runtime 的 receipt、handoff 适配器、durability record、锁和清理算法
由 runtime owner 负责；本页只规定它们必须满足的领域前提和可观察结果。

### 请求、输入与输出

Trash/Restore 的请求成员、字段类型、路径/身份词法、模式与 CLI 映射由[运行时公共接口](../runtime/interface.md#trashrestore查询通知与恢复输入)
定义；公共结果、错误 envelope、退出码和输出通道由[运行时结果、错误与可观测性](../runtime/errors-and-observability.md#公共结果错误与可观测性)
定义。本页只规定通过公共校验后的领域事实、资格、提交和恢复语义。

输入事实包括根状态、活动成员、页面树、parent/导航、完整页面字节、页面头部信息、身份/摘要、
关系事实和适用的 runtime authority。操作在内部形成完整领域计划；失败不得用空 package、部分
页面或未认证的活动成员伪装成功，公共结果按[运行时结果、错误与可观测性](../runtime/errors-and-observability.md#公共结果错误与可观测性)
投影。

`page` 是回收子树的根，不是只选该文件的别名。根页 `index.md` 永远不能回收；叶页面
只能形成自身 package；hub 页面形成其稳定页面树中所有层级的受管理后代 package。页面
子树成员资格由页面树事实决定，不能按目录邻接、basename 或当前文件列表猜测。Restore 只用
经过公共校验的 `trash_id` 选择已保存回收包，不从身份值推导路径、时间或 package 成员。

### Package 与所有权不变量

| 事实 | 强制规则 |
| --- | --- |
| package 成员 | 仅包含回收根及递归受管理后代的 Markdown 完整字节、页面头部信息、身份/摘要和已授权的关系/导航事实。 |
| 叶页面 | package 只有叶自身。 |
| hub 页面 | package 包含该 hub 及同一稳定页面树中全部层级的受管理后代。 |
| 附件和非 Markdown | 不进入 package、不取得页面所有权，继续留在原处；物理邻接不扩大回收范围。 |
| `conclusions.md` | 不进入 package；结论快照不阻止 Trash，Trash/Restore 不删除、重建或改写快照。 |
| active membership | Trash 的完整提交点才把 package 页面从活动成员移出；Restore 的完整提交点才重新加入。 |
| package identity | 已保存回收包及其记录由不透明 `trash_id` 对应；Restore 只按该身份选择回收包，内部编码和摘要不是公共协议。 |

Trash 不处理非 Markdown、附件、未知节点或未经授权的目录事实。目录中的 retained 节点
仍由资源/结构 owner 认证并原位保留；页面 package 不输出 retained 清单。

### Trash 资格与零写决策表

下表按从上到下判定；第一条命中决定领域分区。公共请求校验已在
进入本表前完成；本表只读取并判断 Draft 当前领域事实。

| 请求/事实 | 结果与副作用 |
| --- | --- |
| `page` 不存在 | 选择 `operation.path_not_found`；拒绝且零写。 |
| `page` 存在但不是受管理页面 | 选择 `operation.path_not_managed`；拒绝且零写。 |
| `page` 为根页 `index.md` | 选择 `trash.root_not_allowed`；拒绝且零写。 |
| `page` 不属于活动页面 | 选择 `trash.target_not_active`；拒绝且零写；不猜测它是已回收、从未存在、已移动或现场异常。 |
| 页面树、parent、导航、页面字节或 package 成员事实无法完整稳定取得 | 选择 `runtime.closure_incomplete`；零写，不创建 package，不修改活动成员。 |
| 已读取事实明确违反页面、导航、关系或资源规则 | 复用已认证事实在 [Check 诊断 code 登记](query-check-and-output.md#诊断-code-登记) 中的 Check code；零写，不创建 package，不修改活动成员。 |
| registered 根的计划 after-state 不能通过无过滤、无分页、完整 Check | 选择 `operation.check_failed`；零写。 |
| candidate 根的请求闭包、关系/导航或 package after-state 无法完整稳定取得 | 选择 `runtime.closure_incomplete`；零写。已认证的领域违规已由上一行处理，candidate 不因闭包外的无关 error 被阻止。 |
| 上述事实均可认证，目标是叶或 hub，且无 recovery pending | 内部形成完整 Trash 计划；Execute 选择领域 `applied`，在单一领域提交中写 package、生命周期记录并移出 active；DryRun 选择领域 `would_apply`，零副作用，公共结果按 R-ERRORS 投影。 |

stale 的结论快照或可分类的链接观察本身不阻止 Trash；真正的 Check error 不能由“快照
stale”描述绕过。任何 parent、导航或页面树事实不完整，或事实明确矛盾，均在创建回收包
和修改 active membership 前失败。成功前不得先消费、删除或扩大 package。

### Restore 资格、时间与错误

Trash 的完整逻辑提交已经可靠保存的 UTC 时刻为 `t0`。`expires_at` 是 `t0 + 7 × 24` 小时，
并以 `Z` 结尾的 RFC 3339 UTC 时间表示。只有同一次可认证观察中的 `as_of < expires_at`
才允许 Restore；等于或晚于到期时刻都属于过期。Restore 必须在同一观察中认证 root、
authority、active membership、记录的 after-state、package、目标路径和完整 after-plan。

Restore 依次区分请求和记录状态；公共请求校验已在进入本表前完成：

| 请求/身份事实 | 领域结果、code 与副作用 |
| --- | --- |
| 没有可信保留记录或 package | 选择 `restore.not_found`；零写，保留可诊断事实。 |
| 记录或 package 的身份、摘要、关系、导航或 authority 证据无法认证/互相矛盾 | 选择 `restore.corrupt`；零写并保留现场；公共投影按 [R-ERRORS 登记](../runtime/errors-and-observability.md#错误登记)处理。 |
| package 已完整成功恢复并消费，或仅剩 `consumed` 终态记录 | 选择 `restore.already_restored`；零写；不因之后经过到期时间改成 expired。 |
| package 尚未恢复且 `as_of >= expires_at`，或仅剩 `expired` 终态记录 | 选择 `restore.expired`；零写；不恢复、不把过期折叠成 consumed，清理仍须 durable 授权。 |
| 当前 root/after-state、目标占用或 reverse-collapse mapping 与记录不精确匹配 | 选择 `restore.conflict`；公共投影按 [R-ERRORS 登记](../runtime/errors-and-observability.md#错误登记)处理；零写；不覆盖、不猜测合并，保留 package。 |
| 运行时存在 pending 或耐久结果未收敛现场 | 选择 `runtime.recovery_required`；零写；不做新的 Restore 写，只有显式 `recover()` 可收敛。 |
| package、after-state、时间、目标路径及完整 after-plan 均可认证 | Execute 选择领域 `applied`；在一个完整领域提交后恢复页面并加入 active；成功前不消费 package，成功后才可标记消费。 |

`restore.corrupt` 与 `restore.conflict` 不互换：reverse-collapse 无法证明唯一结果是
conflict，证据本身不可信或矛盾是 corrupt。冲突、损坏、缺失和过期都不产生局部恢复或
cleanup。registered 根 Restore 必须对完整计划 after-state 通过完整 Check；candidate
只认证 package、membership、关系/导航和本次请求闭包。

Restore 历史终态在 package 清理后只保留不透明身份和 `consumed` 或 `expired`，不保留
页面内容或路径清单；因此清理后的终态仍能分别投影为 `restore.already_restored` 或
`restore.expired`，不会退化为 `restore.not_found`。

### Handoff、Purge 与清理授权

以下时间线只描述领域所需的资格和保持规则；外部交付、receipt、attempt、锁和具体清单
由 runtime [自动生命周期与交接](../runtime/lifecycle-and-handoff.md) owner 负责。
下列 `cleanup`、`forward` 和 `rollback` 是 runtime 内部收敛动作名，不是公共
`RecoveryOutcome.outcome`；恢复成功的公共投影与调用方动作只由
[调用方动作与公共 outcome](../runtime/recovery.md#调用方动作与公共-outcome) 定义。

```text
t0: Trash 逻辑提交可靠保存
│
├─ t0 + 7 天：Restore 到期；同一时刻开始允许 handoff eligibility
│             仅后续合格普通调用推进，不存在后台自动推进
└─ t0 + 30 天：进入 purge eligibility（不是从 Restore 到期后另加 30 天）
```

- handoff 只有 durable authorization 且尚未 durable prepared 时，外部接收方尚未被调用。
  显式 `recover()` 取消该 attempt，只清理 authorization 明确拥有的临时 material，保留
  Trash 为 active；内部 `cleanup` 完成后，下一次 eligible 普通调用可立即重试，不增加
  24 小时等待。
- handoff 已 durable prepared 但没有 durable outcome 时，显式 `recover()` 写入
  `handoff-outcome-unknown` 并以内部 `forward` 收敛；不调用外部接收方、不新建 attempt，之后
  只有已有 24 小时 deadline 到达的 eligible 普通调用才可重试。
- `failed` 或 `unknown` handoff 记录在 24 小时 deadline 前不重试；到期后下一次合格普通
  调用才可建立新 attempt。没有后续调用时不自行推进。
- 首次 crossing `t0 + 30 天` 时可以忽略旧节流执行一次新 attempt；只有该 active call
  的明确 `failed` 结果才可 durable 写入 `purge-committed`。结果 unknown 或证据不足时
  继续保留 package 和现场。
- durable `accepted` 后可立即按精确授权 cleanup；`purge-committed` 也只能按同一精确
  授权 cleanup。accepted 或 purge-committed 后不再调用内部 handoff 适配器。
- 自动 handoff、等待、purge 和 cleanup 不进入公共结果；它们是 runtime 私有
  生命周期事实。哪些调用可以运行自动 `AdvanceDue`、自动失败何时阻止当前调用，均由
  runtime [入口与候选选择](../runtime/lifecycle-and-handoff.md#入口与候选选择)唯一
  定义；本页不维护入口名单。

cleanup 或 package consumption 的前提是最终 authority、catalog/head、matching progress/
receipt 和精确 cleanup authorization 全部 durable，且 R/T 阻塞关系已按 owner 收敛。未知、
失败、耐久性不确定、authority 不完整或清单所有权不足时保留现场；不得以“没有找到引用”
推断无主，不得为了释放空间扩大 package、删除附件、删除非 Markdown 或生成结论制品。

### 提交、失败与恢复

Trash 与 Restore 各自只有一个完整领域提交。提交前必须认证所有受影响页面、关系、导航、
package 成员和必要 mapping；提交后耐久结果不确定时不报告成功、不改变后续 active membership
的可观察结果，runtime 保留 recovery pending。重试先重新认证 identity、after-state、
package、authority 和 cleanup evidence；只有显式 `recover()` 可以按 runtime owner 选择
内部 `forward`、`rollback` 或精确 `cleanup`。

恢复过程不得依当前目录形状、缺失文件、页面名、修改时间或 basename 猜路径；也不得重建
被判定 corrupt 的 package。附件和 `conclusions.md` 在所有恢复分支中都不进入页面 package。

## 验证

- 覆盖单页、叶、hub、多层 hub、递归后代、非 Markdown/附件邻接、`conclusions.md`、根页
  `index.md`、非活动页面、candidate/registered 根及 active membership；验证 package 成员、
  retained 节点和零写边界。
- 覆盖 parent、导航、页面树事实缺失与明确矛盾，分别验证 `runtime.closure_incomplete`
  或对应内容错误，均不创建 package、不修改 active；验证 stale 快照和可分类链接观察不
  伪造 Check error。
- 覆盖 `t0`、到期前一瞬、恰好 `expires_at`、到期后一瞬、7 天 handoff eligibility、24
  小时 retry、30 天 purge eligibility、durable accepted、failed、unknown 和无后续调用，
  验证统一 UTC 时间线。
- 覆盖 Restore 的 `not_found`、`expired`、`already_restored`、`conflict`、`corrupt`、
  `recovery_required` 与成功分区；验证 conflict 不覆盖/不猜测，corrupt 保留现场，成功
  后才加入 active，终态记录不退化为 missing。
- 模拟 authority、catalog/head、matching progress/receipt 或 cleanup authorization 任一
  未 durable，验证不 cleanup、不消费 package；验证 authorized-only 与 prepared-without-
  outcome 的显式恢复、24 小时边界和不重复外部调用。
- 验证 registered 完整 after-state Check、candidate 请求闭包、DryRun 无写，以及整个领域提交
  不改写结论快照。

## 来源

- `33aeaec60a34833ce8b686bfdd7e50b8dc9932d0:.scratch/draft-authority-spec-migration/migration-evidence.md#迁移单元`
