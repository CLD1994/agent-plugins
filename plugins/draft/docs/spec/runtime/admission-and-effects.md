# 运行时准入与操作计划

## 准入与操作计划

本合同规定请求从认证到计划、自动工作、最终重验和业务动作的顺序。它拥有 runtime
准入、运行期记录和副作用授权；Draft 页面、关系与闭包的领域含义仍由各 Draft
合同定义。

### 输入与公开输出

输入包括 root-bound 调用、封闭 request、`Execute` 或允许的 `DryRun`、marker/runtime/
descriptor/authority、运行期根锁记录、stable OS root lock、root state、请求所需闭包
和当前 operation/recovery 现场。输出恰为以下之一：

- 以 `Outcome` 投影的已认证 point-in-time `DryRun` plan；该 plan 表达本次可见的
  决策语义，不暴露内部 planner 对象；
- `Execute` 完成后的 `Outcome`；内部 planner 的操作计划对象不是公开输出；
- 稳定 read/projection 及 `ReadOutcome`；
- `runtime.request_invalid`、`runtime.root_invalid`、`runtime.platform_unsupported`、
  `runtime.schema_unsupported`、`runtime.busy`、`runtime.recovery_required`、`runtime.corrupt`、
  `runtime.external_change`、`runtime.durability_failed` 或
  `runtime.closure_incomplete`。

运行期根锁记录为 `.draft/operation.lock`，是完整 canonical regular file。其
`holder_kind` 只能是 `operation`、`lifecycle` 或 `recovery`，不得假定存在
operation id。stable OS root lock 在记录存在和缺失时都是同一 root 范围资源；记录
路径缺失不能创建第二把锁。

### 状态维度与通用不变量

| 维度 | 互斥状态 | 对准入的影响 |
| --- | --- | --- |
| request | 合法封闭形状；非法/未知 | 后者在任何自动副作用前以 `runtime.request_invalid` 失败。 |
| root/runtime 事实 | 可认证；root 无效；marker/runtime/descriptor/authority 不可认证 | 仅可认证状态可继续；不可认证状态不降级为不存在。 |
| runtime schema | 已认证 v1；标识可读但旧版/未知版；无法认证或自相矛盾 | 旧版/未知版为 `runtime.schema_unsupported`；无法认证或矛盾为 `runtime.corrupt`，两者均不迁移、不双读、不自动清理。 |
| root 健康性 | candidate 的许可局部/完整闭包；registered 的完整链接事实闭包；缺失/异常闭包 | 决定计划可否创建，缺失/异常为 `runtime.closure_incomplete`。 |
| 运行期记录 | 无记录；有效记录且 live owner；有效记录且无 live owner；消失；损坏/无法认证 | 决定锁竞争、recovery pending、重新读取或 `runtime.corrupt`。 |
| stable OS lock | 可取得；live；平台无法证明 | 决定可否发布记录或产生 write；无法证明为 `runtime.platform_unsupported`。 |
| mode/入口 | `Execute`、允许的 `DryRun`、read/Check、`maintain`、`recover` | 决定是否可锁、发布记录、自动 lifecycle 或建立 plan。 |
| operation/recovery | 无 pending；descriptor cleanup remnant；有效 stale record；正常执行中 | pending 只能由显式 `recover()` 收敛，普通入口不得隐式恢复。 |

除 `initialize` 的专用 init gate 外，所有受运行期 admission 的入口遵守同一顺序：

```text
request shape -> root / marker / runtime admission -> request-specific pure preflight
-> automatic AdvanceDue -> final locked reread / revalidation / business action
```

preflight 只读取请求需要的事实且不写。自动生命周期之后必须锁内重读并重验，不得
使用旧 snapshot；preflight 后发生的业务竞态失败不回滚已经 durable 的 lifecycle
变化。

### 完整准入决策表

下表按从上到下的优先级求值；每行隐含前面各行均不满足，因此分区互斥；每次调用命中
第一条满足的行，因而覆盖所有 root、lock、mode、健康性和 recovery 组合。`零写` 包括不发布运行期记录、不创建计划制品、不推进
自动 lifecycle、不维护索引、不调用 handoff。

| 优先级事实 | 适用入口/mode | 唯一结果 | 副作用、重试与恢复 |
| --- | --- | --- | --- |
| request 形状、显式 root 或选定 root 无效 | 全部 | `runtime.request_invalid` 或 `runtime.root_invalid` | 零写；调用方修正输入后重新调用。 |
| 平台无法证明所需 no-follow、atomic publish、live lock 或 durability | 任何可能写入/需 probe 的入口 | `runtime.platform_unsupported` | 零写；不得以 POSIX 特例绕过。 |
| runtime schema 标识可认证，但为旧版或未知版；其余已读节点没有认证矛盾 | 全部 | `runtime.schema_unsupported` | 零写、零迁移、零双读和零自动 cleanup；不得把可识别版本不兼容降级为 `runtime.corrupt`。 |
| marker、runtime、descriptor、authority 或锁记录损坏、类型异常、no-follow 失败或无法认证 | 全部 | `runtime.corrupt` | 零写并保留现场；不得猜测不存在或删除。 |
| 无记录但 marker/runtime/descriptor 匹配，且 descriptor cleanup artifacts 仍存在 | 普通写、`maintain`、read/Check、允许的 `DryRun` | `runtime.recovery_required` | 零写、不产生 plan/projection/自动工作；只有 `recover()` 可取得 lock 后清理。 |
| 无记录但 marker/runtime/descriptor 匹配，且 descriptor cleanup artifacts 仍存在 | `recover()` | 进入 cleanup | 取得 stable lock、锁内重读仍无记录后 durable 发布 recovery record，再按 descriptor 清理。 |
| 已发现有效记录，non-blocking try-lock 失败 | direct write/trash/restore/`maintain`/`recover` | `runtime.busy` | 零业务写；不得绕过缺失的记录路径另建锁。 |
| 已发现有效记录，non-blocking try-lock 失败 | read/Check 的稳定 preflight/final projection | 跳过相关自动工作后重新取得稳定事实并投影 | 仅自动子阶段带本次 busy warning；稳定 scan 成功时仍返回 projection。 |
| 已发现有效记录，non-blocking try-lock 失败 | 允许的 `DryRun` | `runtime.busy`，无 plan | 释放 probe 资源，零持久写。 |
| 无记录但 stable OS root lock live | direct write/trash/restore/`maintain`/`recover` | `runtime.busy` | 零业务写；不得绕过缺失的记录路径另建锁。 |
| 无记录但 stable OS root lock live | read/Check 的稳定 preflight/final projection | 允许稳定 projection | 不取 OS lock；自动写子阶段跳过并带本次 busy warning。 |
| 无记录但 stable OS root lock live | 允许的 `DryRun` | 允许 point-in-time plan/`Outcome` | 不 probe、不取 lock、不产生持久写。 |
| 已发现有效记录，try-lock 成功且锁内重读为同一记录、无 live owner | 普通写、read/Check、允许的 `DryRun`、`maintain` | `runtime.recovery_required` | 保留记录；零 AdvanceDue、handoff、release 与 index maintenance。 |
| 已发现有效记录，try-lock 成功且锁内重读为同一记录、无 live owner | `recover()` | 保持记录排他并进入恢复分类 | 只收敛该已认证 pending 现场；不得另发一个 operation record。 |
| 已发现记录，try-lock 成功后锁内记录消失 | 全部有 probe 的入口 | 释放 probe 后重新 admission；再次变化为 `runtime.external_change` | 不以旧观察建 plan 或写入；调用方可重新开始调用。 |
| 无记录且可认证；candidate 对 create/status.set/relation.add/remove 只有可证明局部闭包 | 相应 `Execute` 或允许 `DryRun` | 允许纯 preflight/计划 | 其他 candidate 写不得借局部闭包通过。 |
| 无记录且可认证；candidate 的 generate-conclusions、move/promote/collapse、链接改写、trash、restore 或 adopt 没有完整所需闭包 | 相应入口 | `runtime.closure_incomplete` | 零写；补足闭包后重试。 |
| 无记录且可认证；registered 普通写未具完整链接事实闭包，或 `adopt` 不满足 candidate、完整闭包、runtime settled、ledger 无矛盾 | 相应入口 | `runtime.closure_incomplete` 或认证错误 | 零写；不得把 cache 降级当 authority 健康。 |
| 无记录且可认证；candidate/registered 健康性满足该 request 的闭包要求 | 允许 `DryRun` | 返回同一 planner 生成的 point-in-time plan/`Outcome` | 严格零写、不取 lock、不发布记录、不运行 AdvanceDue 或 index maintenance；plan 不授权后续 Execute。 |
| 无记录且可认证；candidate/registered 健康性满足该 request 的闭包要求 | `Execute`、trash、restore、`maintain` | 允许执行 | 取得 stable lock、锁内重读仍无记录后 durable 发布本调用记录；其发布/file fsync/首次 `.draft/` fsync 失败为 `runtime.durability_failed`，不得有其他根副作用；可见记录保留给 recover。 |
| 可认证稳定事实 | read/Check 的 preflight/final projection | 允许稳定 projection | 本身不取 OS lock、不发布记录；live lock 只影响其自动写子阶段。最终 scan 不稳定为 `runtime.external_change` 且无 projection。 |
| 无记录且无 pending 现场 | `recover()` | 已收敛结果 | 不例行发布 recovery 记录、不枚举 terminal progress、不产生副作用。 |

`read`/Check 的自动 `AdvanceDue` 与 Page/Backlinks/Check 的机会式 index maintenance
是独立写子阶段：需要写入时才尝试 stable lock，无记录且取得成功后发布对应记录；
live lock 时跳过该子阶段并按结果合同返回本次 busy warning 与稳定 projection。直接
write 的竞争是 error，不是 warning。

### 请求类别、副作用与逻辑提交

| 请求 | 纯 preflight 所需事实 | 自动 `AdvanceDue` | 机会式索引维护 | 失败前必须为零的自动副作用 |
| --- | --- | --- | --- | --- |
| `initialize` | target、marker、descriptor、初始化现场 | 否 | 否 | init admission 失败 |
| `perform(ordinary, Execute)`（不含 adopt）、`trash(Execute)`、`restore` | marker、runtime、request 形状、所需局部/完整闭包 | 是 | 否 | shape/root/runtime/closure 失败 |
| `perform(adopt, Execute)` | marker、runtime、request 形状、完整闭包 | 否 | 否 | shape/root/runtime/closure 失败 |
| 允许 `DryRun` 的 perform/trash | request shape 与 request-specific pure facts | 否 | 否 | busy/recovery-required/corrupt/admission/preflight 失败时不产生 plan |
| `read(Page|Backlinks)` | 可完整稳定扫描的 root 事实 | 是 | 最终完整 scan 成功后 | admission/scan 失败 |
| `read(Check)` | 可完整稳定 scan 的 root/current/active 事实 | 否 | 最终完整 scan 成功后 | admission/scan 失败 |
| `read(Links|Relations|Status|Recoverable)` | 对应稳定读事实 | 是 | 否 | admission/scan 失败 |
| `maintain(ExternalFilesChanged)` | marker、runtime、完整 index 闭包 | 否 | 自身职责 | admission/preflight 失败 |
| `recover()` | runtime 与 active/current 结构 | 否 | 否 | admission 失败 |

逻辑提交只在完整计划、最终锁内重验、authority/content/catalog/head/outcome 所需 commit
及不可延期授权 cleanup 均 durable 后发生。每个 projection 要么已 durable，要么其
失败已安全分类为可重建/可降级，且不存在未认证 staging 或待恢复现场，才可 unlink
记录、fsync `.draft/` 并释放 lock。unlink 或最终目录 fsync 失败/不确定时不得成功。

### 失败、零写、重试与恢复

cache 可以按结果合同分类降级并带 warning；authority/ledger 矛盾或已决定结果无法
耐久是 fatal。`maintain` 发现 marker-ready cleanup remnants 时在 admission 直接
返回 `runtime.recovery_required`，不取得恢复 lock、不发布 record、不调用 recover、
AdvanceDue、index maintenance 或 cleanup。正常入口不枚举 terminal progress，也不
隐式 `recover()`。

### 可证伪验证

- 对完整准入表的每行构造 root、marker、record 和 OS-lock 夹具，验证唯一结果和
  零写/保留现场要求；特别覆盖已认证 v1、可读但旧/未知 schema、无法认证 schema、
  无记录、live owner、stale record、记录消失与 corrupt。
- 验证 candidate 的局部闭包许可、candidate 的完整闭包请求、registered 的完整闭包
  与 `adopt` settled/ledger 条件互不折叠。
- 验证无记录 `DryRun` 不 probe lock，已发现记录只 probe 一次且永不产生持久写。
- 在记录 no-replace、record fsync、首次 `.draft/` fsync、unlink 和最终目录 fsync
  注入失败，验证 `runtime.durability_failed` 不被投影为成功，并只能进入显式恢复。

### `unresolved` 的跨页 ABI

| 项目 | 状态 | 阻断原因 |
| --- | --- | --- |
| 各领域请求的完整 machine-zone/导航事实集合 | `unresolved` | 已接受逻辑原子性未为每个领域入口列出 payload 成员。 |
| DryRun plan 的精确字段、排序与 Outcome 关联字段 | `unresolved` | 已接受或已签来源规定零副作用与时点语义，未给出 plan ABI。 |
| `runtime.recovery_required`/`runtime.durability_failed` 在公共 Outcome 中的精确字段投影 | `unresolved` | 公共 envelope ABI 尚未签署。 |

## 来源

- `80508340458f393435ae67669656141ec411ba1b:.scratch/draft-operation-runtime-foundation/spec.md#5. 根锁与运行期 admission`
- `80508340458f393435ae67669656141ec411ba1b:.scratch/draft-operation-runtime-foundation/spec.md#6. 请求与副作用决策表`
