# 运行时准入与操作计划

## 准入与操作计划

本合同拥有 runtime 准入、运行期记录和副作用授权。页面、关系、链接、Trash package 和
Check 的领域含义由各自 Draft owner 定义；runtime 只在这些事实已认证后决定能否形成计划、
发布记录、执行写入或投影结果。

输入是 root-bound 公开入口、[运行时公共接口](interface.md#运行时公共接口)定义的封闭
request/mode、marker/runtime/descriptor/authority、root lock、请求需要的事实闭包以及当前
operation/recovery 现场。输出只能是对应的公共成功结果或统一 error envelope；内部 planner
对象、计划字段、record 和锁不公开。

除初始化专用 init gate 外，受本合同约束的入口顺序固定为：

```text
request shape -> root / marker / runtime admission -> request-specific pure preflight
-> eligible automatic AdvanceDue -> final locked reread / revalidation / business action
```

preflight 只读取本次请求需要的事实，不写入。自动阶段之后必须在锁内重新读取和验证，不能
继续使用旧观察；preflight 后的竞态不回滚此前已经 durable 的自动工作。自动工作本身不向
公共成功结果添加自动阶段字段、顶层告警成员或路径列表；只有它阻断当前请求时才返回
机器 error。

### 状态维度与分类边界

| 维度 | 互斥状态 | 准入结果 |
| --- | --- | --- |
| request | 合法封闭形状；非法/未知 | 非法/未知时返回 `runtime.request_invalid`；在 root 选择、读取或认证前终止，零写。 |
| root/runtime | request 合法后：可认证；root 无效；当前私有 authority 不可认证 | 只有可认证状态继续；后两者分别为 `runtime.root_invalid`、`runtime.corrupt`。 |
| schema | 已认证 v1；可读但旧/未知；无法认证或自相矛盾 | 依次为继续、`runtime.schema_unsupported`、`runtime.corrupt`。 |
| 页面与领域闭包 | 完整且领域合法；完整但已认证违规；无法完整稳定取得 | 依次继续、相应领域 code、`runtime.closure_incomplete`。 |
| operation record | 无记录；live owner；有效 stale record；消失；损坏 | 决定执行、`runtime.busy`、`runtime.recovery_required`、重读或 `runtime.corrupt`。 |
| stable OS lock | 可取得；live；能力不可证明 | 决定可否写；后两者为 `runtime.busy`、`runtime.platform_unsupported`。 |
| mode/入口 | Execute、允许 DryRun、read、maintain、recover | 决定是否可取得锁、发布记录、推进自动工作或形成计划。 |

“已认证领域违规”、“事实尚未完整”和“Draft 私有权威损坏”是三个不同分区：

- 页面、导航、关系、结论和资源已经读取且可证明违规时，返回其领域 code（如
  `page.frontmatter_invalid`、`relation.graph_invalid`）；不得伪装为
  `runtime.corrupt`。公共错误投影由[公共结果、错误与可观测性](errors-and-observability.md#公共结果错误与可观测性)
  负责。
- 请求所需文件或事实尚不能完整、稳定取得时，返回 `runtime.closure_incomplete`；不得把暂时
  未知当作空结果、内容违规或安全损坏。
- marker、descriptor、authority、record、catalog、head 或 ledger 自身无法认证或矛盾时，返回
  `runtime.corrupt`；不得以目录枚举、mtime、缓存或缺失路径猜测其含义。

### 通用准入决策表

下表按从上至下优先级求值；每行隐含前行不成立，故每次调用只命中一行。第一行只验证
request shape，不选择、读取或认证 root；第二行隐含 request shape 合法，只验证显式或选定 root。
这里的“零写”包括
不发布运行期 record、不创建 operation/制品、不推进自动生命周期、不维护索引、不调用
handoff，也不隐式恢复。

| 优先级事实 | 适用入口 | 唯一结果 | 副作用、重试与恢复 |
| --- | --- | --- | --- |
| request shape 无效 | 全部 | `runtime.request_invalid` | 零写；不选择、读取或认证 root，调用方修正 request 后重试。 |
| request shape 合法，显式或选定 root 无效 | 全部 | `runtime.root_invalid` | 零写；调用方修正 root 后重试。 |
| 平台不能证明本调用需要的 no-follow、atomic publish、live lock、durability 或 hard timeout | 可能写入或需要 probe 的入口 | `runtime.platform_unsupported` | 零写。 |
| schema 可认证但为旧/未知，其他已读节点无矛盾 | 全部 | `runtime.schema_unsupported` | 零写、零迁移、零双读、零 cleanup。 |
| marker/runtime/descriptor/authority/lock record 类型异常、no-follow 失败、损坏或无法认证 | 全部 | `runtime.corrupt` | 零写并保留现场。 |
| 无 record 且 descriptor cleanup artifacts 尚存 | 普通操作、read、DryRun、maintain | `runtime.recovery_required` | 零写；只有 `recover()` 可清理。 |
| 无 record 且 descriptor cleanup artifacts 尚存 | `recover()` | 进入 descriptor cleanup | 取得 stable lock 后重读，发布 recovery record 并按精确清单处理。 |
| 已发现有效 record，non-blocking try-lock 失败 | direct write、trash、restore、recover | `runtime.busy` | 零业务写。 |
| 已发现有效 record，non-blocking try-lock 失败 | `maintain({})` | 最小 notification 成功结果 | 零维护写；信号已接收，不表示维护完成。 |
| 已发现有效 record，non-blocking try-lock 失败 | DryRun | `runtime.busy` | 无计划、零持久写。 |
| 已发现有效 record，non-blocking try-lock 失败 | read | 跳过自动写子阶段，取得稳定读事实后投影查询 | 不向结果增加内部忙碌字段。 |
| 无 record 但 stable OS root lock live | direct write、trash、restore、recover | `runtime.busy` | 零业务写。 |
| 无 record 但 stable OS root lock live | `maintain({})` | 最小 notification 成功结果 | 零维护写；信号已接收，不表示维护完成。 |
| 无 record 但 stable OS root lock live | DryRun | 允许点时计划 | 不 probe、不取锁、不写入。 |
| 无 record 但 stable OS root lock live | read | 允许稳定查询 | 不取锁；跳过自动写子阶段。 |
| 有效 record 的 try-lock 成功，锁内仍是同一 record 且无 live owner | 普通操作、read、DryRun、maintain | `runtime.recovery_required` | 保留 record；零自动工作、release 和索引维护。 |
| 同上 | `recover()` | 进入恢复分类 | 保持排他性，只收敛该已认证现场。 |
| 已发现 record，try-lock 成功后锁内记录消失 | 有 probe 的入口 | 释放后重新 admission；再次变化为 `runtime.external_change` | 不用旧观察建立计划或写入。 |
| candidate 的请求所需局部闭包完整 | `create`、`status_set`、`relation_add`、`relation_remove` | 可形成计划 | 其余 candidate 写不得借局部闭包通过。 |
| candidate 的完整闭包请求不完整 | `generate_conclusions`、`move`、`promote`、`collapse`、链接改写、trash、restore、adopt | `runtime.closure_incomplete` | 零写。 |
| registered 业务写的完整计划 after-state Check 不完整 | 相应写入或 DryRun | `runtime.closure_incomplete` | 零写。 |
| registered 业务写的 after-state 完整 Check 有 error | 相应写入或 DryRun | `operation.check_failed` | 零写；公共错误投影由 R-ERRORS 负责。 |
| registered 的 `relation_remove` 只删除已定位遗留非法关系，错误减少且 after 诊断无新增 error | Execute 或 DryRun | 允许窄修复计划 | 不授权其他字段或关系变化。 |
| 闭包与领域门禁通过 | 允许 DryRun | 按 R-ERRORS 投影公共 DryRun 成功结果 | 不取锁、不发布 record、不运行自动工作、不维护索引。 |
| 闭包与领域门禁通过 | Execute、trash、restore | 允许执行 | 取得 stable lock，锁内重读并重算门禁后才发布 record。 |
| maintain 的 index/authority 闭包与权限通过 | `maintain({})` | 允许维护 | 不改变 Draft 领域事实，仍遵守同一 record 耐久边界。 |
| 稳定可认证事实 | read | 投影 `QueryOutcome` | 本身不取 OS lock、不发布 record；最终 scan 不稳定为 `runtime.external_change` 且无查询结果。 |
| 无 record、无 cleanup remnant、无其他 pending | `recover()` | `RecoveryOutcome/already_settled` | 不例行发布 recovery record、不枚举终态记录、不产生业务写。 |

`maintain({})` 有一个 notification 特例：如前述准入已通过、但只因 live record/lock 无法取得
维护写机会，它仍返回 R-ERRORS 定义的最小 notification 成功结果，因为该信号已被接收；这不
表示维护完成。stale record、pending、损坏、闭包不足、耐久失败和不安全外部变化仍按各自
error 行返回，错误结果不使用 notification 成功成员。

### 请求类别、自动工作与逻辑提交

| 请求 | pure preflight 所需事实 | 自动 `AdvanceDue` | 机会式索引维护 |
| --- | --- | --- | --- |
| `initialize` | target、marker、descriptor、初始化现场 | 否 | 否 |
| `perform`（不含 `adopt`）、`trash`、`restore` | marker、runtime、请求形状与所需局部/完整闭包 | 是 | 否 |
| `perform(adopt)` | marker、runtime、完整 Check/闭包 | 否 | 否 |
| 允许的 DryRun | request shape 与请求专属纯事实 | 否 | 否 |
| `read(page|links|backlinks|relations|status|recoverable)` | 可完整稳定扫描的 root 事实 | 是 | 最终 scan 成功后 |
| `read(check)` | 可完整稳定 scan 的 root/current/active 事实 | 否 | 最终 scan 成功后 |
| `maintain({})` | marker、runtime、完整 index/authority 闭包 | 否 | 自身职责 |
| `recover()` | runtime 与 active/current 结构 | 否 | 否 |

自动阶段一次 invocation 最多处理一个 eligible candidate、最多执行一次外部 handoff。schedule
缺失或过期时可在持锁后从 active authority 重建；成功重建只结束本自动阶段，下一次 eligible
invocation 才可选择 candidate。自动阶段不能耐久时为 `runtime.durability_failed`，稳定 authority
不可认证时为 `runtime.corrupt`。这些内部事件不改变 operation 的 `outcome`。

写入口的逻辑提交不是单次物理写。只有完整计划、最终锁内重验、authority/content/catalog/head/
outcome 所需 commit、不可延期授权 cleanup 均 durable，且派生资料已 durable 或安全分类后，
才可投影成功；随后才可 unlink record、fsync `.draft/` 并释放锁。record no-replace、record
fsync、首次 `.draft/` fsync、unlink 或最终目录 fsync 失败/不确定均为
`runtime.durability_failed`，不得投影成功。

cache 可安全降级，但 authority、ledger 或已决定结果的矛盾/耐久失败是 fatal。普通入口不得
枚举 terminal progress，不得自动调用 `recover()`。显式恢复是 pending 现场的唯一收敛入口。

### 可证伪验证

- 为通用准入表每一行构造 root、marker、record 和 OS-lock 夹具，验证唯一结果、零写与
  现场保留；特别覆盖旧/未知 schema、无记录、live owner、stale record、record 消失和 corrupt。
- 对 request shape 与显式或选定 root 同时无效的反例验证只返回 `runtime.request_invalid`，且
  不选择、读取或认证 root；request 合法而 root 无效时才返回 `runtime.root_invalid`。
- 验证已认证领域错误、闭包不足和私有 authority 损坏分别落入领域 code、
  `runtime.closure_incomplete`、`runtime.corrupt`，不互相降级。
- 验证 candidate 局部闭包许可、candidate 完整闭包请求、registered after-state Check、
  窄关系修复和 DryRun 的互斥结果。
- 在 record 发布、file fsync、authority 替换、cleanup、unlink 和最终目录 fsync 注入失败，
  验证不报告成功且只能显式恢复；验证 stable read 不泄露自动阶段内部状态。

## 来源

- `ce64ac30c52ca910fe39fcedd055dd28f0cfbda4:.scratch/draft-operation-runtime-foundation/spec.md#5-根锁与运行期-admission`
- `ce64ac30c52ca910fe39fcedd055dd28f0cfbda4:.scratch/draft-operation-runtime-foundation/spec.md#6-请求与副作用决策表`
