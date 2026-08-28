# Draft operation runtime 基础规格

Status: ready-for-agent
Status-Schema: triage
Tracker-State: open
Base-Spec: b297f33
Affects:
  - plugins/draft/docs/spec/runtime/

## 1. 范围与权威性

本文是 Draft operation runtime v1 的唯一行为权威。它替代本 feature 范围内既有 runtime 格式、调用表面和测试表面；旧或未知 runtime schema 一律失败关闭，不迁移、不双读、不自动清理。页面、marker、链接和关系的领域含义以 `CONTEXT.md` 为准；本文规定持久状态、并发、恢复、生命周期、错误与可观察结果。

覆盖 `init`、普通写、trash、restore、read、notification、check、recover 与 CLI 投影。不覆盖非受协调写入者的原子防护、网络卷、后台任务、终态审计 GC/full audit。完整终态 operation 审计永久保留，直到独立规格另行授权清理。

## 2. 对外接口与所有权

```text
Lifecycle.initialize(root: AbsoluteRoot, root_state: RootState) -> InitOutcome

RuntimeFactory.for_root(root: AbsoluteRoot) -> root-bound OperationRuntime

OperationRuntime
  .perform(request: OrdinaryRequest, mode: Execute | DryRun) -> Outcome
  .trash(request: TrashRequest, mode: Execute | DryRun) -> Outcome
  .restore(request: RestoreRequest) -> Outcome
  .read(request: ReadRequest) -> ReadOutcome
  .maintain(ExternalFilesChanged) -> MaintenanceOutcome
  .recover() -> RecoveryOutcome
```

`Lifecycle.initialize` 是不经 RuntimeFactory 构造的公开静态/模块行为入口。CLI 的 `init` 路由只规范化显式 init root 后直接调用它；不通过 factory，不读取 cwd/env 选择 root，也不要求 root 已有 marker。`OperationRuntime` 是 root-bound 实例；构造不作 I/O，也不缓存跨调用 snapshot 或能力结论。其 `OperationRuntime(root, deps)` 构造签名仅是 RuntimeFactory 的内部装配合同，不是调用方 Interface。`RuntimeFactory` 是仅供 CLI 构造 root-bound `OperationRuntime` 的受限 seam，不返回或承诺 Lifecycle；其他受管理根命令按 `--root > DRAFT_ROOT > physical cwd ancestor` 选择规范绝对 root，最高优先级候选无效即失败且不回退，再且仅通过 `RuntimeFactory.for_root` 取得 OperationRuntime。CLI 不能传入、读取或替换 `RuntimeDeps`、`PlatformAdapter`、`HandoffAdapter`、worker、Clock、ID source、planner、运行期 root lock、recovery、index-settled 或其他内部协作者。`RuntimeFactory` implementation 在其 OperationRuntime 路径内装配这些协作者；Lifecycle.initialize 的依赖装配也只属于其 implementation。两条构造路径的装配一致性由 implementation 保证，可共享 private composer，但不得新增行为 Interface 或公开 seam。RuntimeFactory 不是额外的行为 Interface；对调用方的行为 Interface 仍只有 Lifecycle 与 OperationRuntime。CLI adapter 只负责上述 root 选择/规范化、封闭 request、调用接口并投影结果；核心不读取 cwd 或环境。

`OrdinaryRequest` 是 create、move、promote、collapse、status.set、relation.add/remove、generate-conclusions、adopt 的封闭值联合；调用方不能传函数、计划、snapshot 或 capability。create、move、promote、collapse、trash、generate-conclusions、status.set、relation.add、relation.remove 共九类 CLI 命令支持 `Execute` 与 `DryRun`；init、adopt、restore、recover、notification 不支持 `DryRun`。`init` 直接调用公开 `Lifecycle.initialize`；adopt 及其余受管理根命令先按既定优先级选择 root，再通过 RuntimeFactory 取得 OperationRuntime。planner、request gate、record fold、自动生命周期、扫描、索引和上述适配器均是实现内部 seam。`HandoffAdapter` 是唯一外部 seam。notification 只能调用 `maintain(ExternalFilesChanged)`，不得调用 `read`、自动生命周期或 `recover`。

## 3. 平台、根与持久布局

`PlatformAdapter` 分别为 Windows、macOS、Linux 实现。它在任何 target write 前证明当前本地卷能提供：逐节点 no-follow、create-only/no-replace 原子发布、live OS lock、文件与目录 durability、及可终止隔离调用的 hard timeout。它还必须提供 `replace_exact_regular(expected_target, staged_source)`：逐节点 no-follow；目标是已验证的 canonical regular file，且 identity 与 digest 精确等于 `expected_target`；staged source 是同目录、已验证 digest 的 canonical regular file；replace 原子可见；任何错误或不确定结果都重新读取目标；staged file 与 authority 父目录都能 fsync。若不能证明任一性质，capability 必须在任何相关 write 前返回 `runtime.platform_unsupported`，零写。POSIX 算法不是跨平台协议。

stable OS root lock 是同一根范围内、在运行期根锁记录存在或缺失时都使用的 OS lock 资源；记录路径缺失时不得另建第二把锁。除普通写入所需的稳定取得外，已发现运行期根锁记录的 DryRun probe 必须另行满足一次 non-blocking try-lock、identity/no-follow、release 与零持久写性质；这些性质无法证明时返回 `runtime.platform_unsupported`。

`replace_exact_regular` 不接受缺失、符号链接、目录、非规范路径或 identity/digest 已变化的 target；这些不是“替换成功”的变体。预期缺失的 schedule/cache projection 必须使用已认证的 create-only publish，而不是调用 replace。目标在 stage 后变化返回 `runtime.external_change`，稳定 target 类型/编码不合法返回 `runtime.corrupt`，两者均零替换。

持久 init artifact 只能位于目标根内。runtime v1 布局为：

```text
.draft/draft.json                         # marker：仅领域身份与状态
.draft/runtime/
  descriptor.json                          # InitDescriptor 的规范编码
  operations/<id>/manifest.json
  operations/<id>/progress/<sequence>.json
  operations/<id>/artifacts/...
  lifecycle/transitions/<generation>.json  # immutable catalog transition
  lifecycle/active.json                    # 原子替换的 active T / 阻塞 R 权威
  lifecycle/schedule.json                  # 带 catalog generation 的可重建投影
  index/epochs/<sequence>.json
  index/head.json                           # 原子替换的 immutable epoch head 权威
  index/cache.json                          # 带 head generation 的可重建投影
  trash/<id>/...
```

`InitDescriptor` 是 runtime 内唯一完整 init 权威，规范编码精确涵盖 marker 预期字节、初始内容、上述 v1 布局、允许节点类型与精确 cleanup artifacts；marker 不含 `init_id`，也不重复 descriptor。所有 immutable record 采用 canonical encoding、create-only 发布、file fsync 和父目录 fsync；未知字段、tag、成员、非 regular 节点、符号链接、摘要或 hash 链错误都是 `runtime.corrupt`。`active.json` 与 `index/head.json` 是唯一可原子替换的 authority；`schedule.json` 与 `cache.json` 是可原子替换的 projection，绝不成为 authority。

## 4. Init 状态机

在任何 target write 前，`initialize` 取得目标根外的临时 OS init gate，并持续到 marker durable。它只接受严格空 target、空 `.draft`、或仅空 `.draft/runtime`；其他任何初始树失败关闭。gate 内按 descriptor 创建全部 runtime v1、初始内容和 marker，逐项耐久化，最后发布 marker。marker 发布前 runtime 已完整；marker durable 后，首次初始化必须进入与运行期恢复相同的 cleanup path：取得稳定 OS root lock，durable 发布 `holder_kind=recovery` 记录，按 `InitDescriptor` 精确清理，再按记录 unlink、`.draft/` 最终 fsync、释放 OS lock 的顺序完成。成功结果不暴露内部路径或中间状态。

| marker | target / runtime | descriptor | initialize 动作 | recover 动作 |
| --- | --- | --- | --- | --- |
| 不存在 | 严格允许的空形态 | 新 descriptor | 持 gate 完整创建并发布 marker | 不处理 |
| 不存在 | 有初始化现场 | 与请求完全相同且所有已存成员、字节、摘要匹配 | 持 gate 续建缺失的精确成员 | 不处理 |
| 不存在 | 有初始化现场 | 不同、未知、损坏或外部变化 | `runtime.init_conflict` 或 `runtime.corrupt`，零写 | 不处理 |
| 存在 | descriptor 与 marker/runtime 全匹配，且 descriptor cleanup artifacts 均已按清单收敛 | 任意 | 返回已初始化结果，零写 | 正常运行期恢复 |
| 存在 | descriptor 与 marker/runtime 全匹配，但仍有 descriptor cleanup artifacts | 任意 | `runtime.recovery_required`，零写 | 取得稳定 OS root lock，发布 recovery 记录后按清单幂等清理 |
| 存在 | 任一不匹配或节点异常 | 任意 | `runtime.corrupt`，保留 | `runtime.corrupt`，保留 |

同目标 init 由 gate 串行；不同 descriptor 冲突。没有产品清除接口，也没有首次 `Execute` 建立 runtime 的路径。普通 `recover` 不处理无 marker 的初始化现场。

marker durable 后，descriptor 所列 cleanup artifacts 只能由运行期 root lock（`.draft/operation.lock`）下的恢复路径按精确清单清理；已缺失的清理成员可幂等跳过，仍存在的成员必须逐节点 no-follow 且路径、regular 类型与摘要精确匹配。unknown member、符号链接、类型或 digest mismatch、以及外部变化都是 `runtime.corrupt`，不得删除。该路径绝不把无 marker 的初始化现场当作运行期可恢复现场，也不是可由 CLI、RuntimeFactory 调用方或测试直接导入的 seam。

## 5. 根锁与运行期 admission

运行期根锁记录是 `.draft/operation.lock`。记录的 `holder_kind` 可为 `operation`、`lifecycle`、`recovery`；不得假设必有 operation id。记录是完整 canonical regular 文件。直接写入、trash、restore、`maintain` 和 `recover()` 在产生根副作用前必须取得对应 stable OS root lock，再锁内重读记录、marker、runtime；无记录时，只有取得该锁并确认仍无记录后，当前调用才可 durable 发布自己的记录并继续，记录路径缺失不能创建独立第二把锁。`read`/`Check` 的稳定 preflight、final scan 和 projection 本身不取得 OS root lock，也不发布记录；其中的自动 `AdvanceDue`，以及 Page/Backlinks/Check 的机会式 index maintenance，才在各自写入子阶段尝试取得 stable OS root lock，无记录且取得成功后才发布该子阶段的记录，live lock 则按既有 busy 规则跳过子阶段并带 warning 继续稳定 projection。已发现有效记录但无 live owner、marker/runtime/descriptor 匹配且仍有 InitDescriptor cleanup artifacts，或记录损坏/无法认证时，`read`/`Check` 在 admission 阶段返回对应错误且不产生 projection。`notification` 只路由 `maintain(ExternalFilesChanged)`，其 busy 结果由 notification 映射为最小 `delivered`。

当前调用完成所有 authority/content/catalog/head/outcome commit 和不可延期的授权 cleanup 并使其 durable 后，才能结束 projection 处理：每个 projection 的结果要么已经 durable，要么其失败已安全分类为可重建/可降级；同时不得留下未认证 staging 或其他需要恢复的现场。满足这些条件后才能 unlink 运行期根锁记录并 fsync `.draft/`，随后释放 OS lock。unlink 或最终目录 fsync 失败或结果不确定不得返回成功；若 owner 仍持有 OS lock，后续调用即使看不到记录也必须返回 `runtime.busy`；owner 已退出且 OS lock 可取得时，后续写入可按上述稳定 admission 发布新记录。OS lock 的 FD、inode、handle 或其他 backing resource 算法由三平台 `PlatformAdapter` 内部实现，本规格只固定上述可观察顺序和认证结果。

运行期根锁记录的 no-replace 发布、record file fsync 或首次 `.draft/` fsync 失败或结果不确定时，当前调用返回 `runtime.durability_failed`，不得产生其他根副作用；若记录已可见则保留，后续按显式恢复要求处理。`maintain` 遇到 marker/runtime/descriptor 匹配、无运行期根锁记录但仍有 `InitDescriptor` cleanup artifacts 时，必须在 admission 直接返回 `runtime.recovery_required`；该 notification 路径不取得用于恢复的 stable OS root lock，不发布 record，不调用 `recover()`、`AdvanceDue` 或 index maintenance，也不清理 artifacts。

运行期 admission 按下表分类；“变更前 authority 版本”是持久 authority 在本次变更前的 generation 与 digest，“本次唯一授权的下一 authority 版本”是 matching intent 精确授权的 generation 与 digest。普通入口不枚举 terminal progress，也不隐式执行 `recover()`。DryRun 无记录时不 probe OS lock；已发现记录后才允许 PlatformAdapter 做一次 non-blocking try-lock。

| 运行期根锁记录与 OS lock 观察 | direct write/trash/restore/maintain | read/Check 稳定 preflight、final projection | read/Check 的 AdvanceDue / 机会式 index maintenance | DryRun | `recover()` |
| --- | --- | --- | --- | --- | --- |
| 无记录，且 marker/runtime/authority 可认证、无 marker-ready cleanup remnants | 先取得 stable OS root lock；锁内重读仍无记录后发布自己的记录并继续 | 不取得 OS root lock、不发布记录；直接取得稳定事实供 projection | 仅在子阶段确有写入时取得 stable OS root lock；锁内重读仍无记录后发布该子阶段的记录并继续 | 不 probe OS lock；成功 preflight 返回 DryRun plan/outcome，仍严格零副作用 | 先取得 stable OS root lock；锁内重读仍无记录后检查需要恢复的持久现场，仅确认存在时发布 recovery 记录并继续 |
| 无记录，marker/runtime/descriptor 匹配但有 descriptor cleanup remnants | `runtime.recovery_required`；零写 | `runtime.recovery_required`，不产生 projection | 不进入维护子阶段 | 不 probe OS lock，返回 `runtime.recovery_required`，不产生 plan | 先取得 stable OS root lock；锁内重读仍无记录后 durable 发布 recovery 记录并按 descriptor 清理 |
| 无记录但 stable OS root lock live | `runtime.busy`；路径缺失不能绕过 stable lock | 不取得 OS root lock；按稳定事实继续 projection | 尝试取得 stable lock 发现 live 时跳过子阶段并带既有 busy warning | 不 probe OS lock；按稳定事实生成 DryRun plan/outcome | `runtime.busy` |
| 记录已发现，non-blocking try-lock 失败，说明 live owner | `runtime.busy` | 按既有 busy 规则跳过相关自动工作，重新取得稳定事实并继续 projection | 跳过维护并带既有 busy warning | 释放 probe 资源，返回 `runtime.busy`，不产生 plan | `runtime.busy` |
| 记录已发现，try-lock 成功；锁内重读为同一有效记录且无 live owner | `runtime.recovery_required`；保留记录，零 AdvanceDue、handoff、release、index maintenance | `runtime.recovery_required`，不产生 projection | 不进入维护子阶段 | 立即释放 probe lock，返回 `runtime.recovery_required`，不产生 plan | 保持该记录排他，枚举并收敛需要恢复的持久现场 |
| 记录已发现，try-lock 成功后记录已消失 | 释放 probe lock，并从 admission 重新读取一次；再次变化为 `runtime.external_change` | `runtime.external_change`，不产生 projection | 不进入维护子阶段 | 释放 probe lock，并从 admission 重新读取一次；再次变化为 `runtime.external_change` | 释放 probe lock，并从 admission 重新读取一次 |
| 记录损坏、无法认证、类型异常或 no-follow 失败 | `runtime.corrupt`，保留记录 | `runtime.corrupt`，保留记录，不产生 projection | 不进入维护子阶段 | `runtime.corrupt`，保留记录，不产生 plan | `runtime.corrupt`，保留记录 |

DryRun 对已发现的记录只允许一次 non-blocking try-lock。try-lock 失败表示 live owner；try-lock 成功后必须在短暂持锁期间逐节点 no-follow 重读并认证同一记录，立即释放，再按上表返回 recovery-required。probe 不发布记录、不持锁构造 plan、不产生持久写；三平台必须证明 nonblocking、identity/no-follow、release 与零持久写，否则返回 `runtime.platform_unsupported`。该 probe 只在已发现记录时发生，不能用启发式时间或目录状态替代。纯 read/Check projection 不因 DryRun probe 获得锁。

显式 `recover()` 在取得稳定 OS root lock 后锁内重读当前事实：若仍有有效记录且无 live owner，则保持该记录排他；若无记录，则先在锁内确认存在需要恢复的持久现场，仅在确认后 durable 发布 `holder_kind=recovery` 记录并继续收敛；无该现场时不例行发布记录，直接无副作用返回 settled 结果，也不枚举 terminal progress。随后按第 4、7、9、10 节枚举并收敛需要恢复的持久现场。只有所有应完成的 content/catalog/head/outcome commit 和不可延期的授权 cleanup 都 durable，且 projection 已 durable 或其失败已安全分类为可重建/可降级、没有未认证 staging 或其他需要恢复的现场，随后记录 unlink 与 `.draft/` 最终 fsync 成功，才释放 OS lock 并返回成功。没有记录表示之前受协调调用已经按该顺序完成，或从未开始根副作用；正常入口仍不扫描 terminal audit。read 的自动工作遇 live busy 可以跳过，但稳定扫描仍须返回 projection 与本次可行动 warning；扫描无法稳定时为 `runtime.external_change`。DryRun 不取锁，除已发现记录的上述 probe。

需要恢复的持久现场仅有两类，且只能由显式 `recover()` 按已认证事实收敛：

1. 有效运行期根锁记录仍存在，表示此前受协调调用尚未取得 durable completion proof；其关联的 operation、lifecycle、catalog、index、trash/restore 或 handoff 记录属于该现场的一部分。
2. marker/runtime/descriptor 匹配、无运行期根锁记录，但 `InitDescriptor` 精确列举的 cleanup artifacts 仍存在。

没有运行期根锁记录且没有上述 InitDescriptor cleanup artifacts 时，`recover()` 不创建 recovery 记录，也不枚举 terminal progress。

运行期 admission 的唯一顺序是：

```text
request shape -> root / marker / runtime admission -> request-specific pure preflight
-> automatic AdvanceDue -> final locked reread / revalidation / business action
```

已知无效 shape、根、marker、runtime 或闭包在自动副作用之前失败。preflight 仅捕获 request 所需事实且不写；自动生命周期之后再锁内重读并重验，不得采用旧 snapshot。preflight 后的竞态业务失败不回滚已经 durable 的生命周期变化。

## 6. 请求与副作用决策表

`DryRun` 在无已发现记录且成功 preflight 时使用与 Execute 相同的 planner 返回 point-in-time DryRun plan/outcome，但严格零副作用：不取锁、不发布运行期根锁记录、不自动生命周期、不索引维护、不建 operation、epoch 或 artifact。正式 Execute 必须锁内重读与 revalidate；DryRun plan 只描述该次稳定事实，不授权后续写入。已发现记录后的 probe 只按 §5 规则分类；live owner、无 live owner 的有效运行期根锁记录、corrupt 或其他 admission/preflight 失败时不产生 plan。最终 unlink 小窗口中 authority/business commit 已完成并满足 §5 的 release 前条件时，DryRun 仍可按稳定扫描生成 point-in-time plan。`restore` 签名不支持 DryRun。

| 请求 | preflight 所需事实 | 自动 AdvanceDue | 机会式索引维护 | 失败前零自动副作用 |
| --- | --- | --- | --- | --- |
| `initialize` | target、marker、descriptor、runtime 初始化现场 | 否 | 否 | init admission 失败 |
| `perform(ordinary, Execute)`（不含 adopt）/`trash(Execute)`/`restore` | marker、runtime、请求形状、所需完整/局部闭包 | 是 | 否 | shape/root/runtime/closure 失败 |
| `perform(adopt, Execute)` | marker、runtime、请求形状、完整闭包 | 否 | 否 | shape/root/runtime/closure 失败 |
| `perform(create|move|promote|collapse|generate-conclusions|status.set|relation.add|relation.remove, DryRun)`/`trash(DryRun)` | request shape 与 request-specific pure facts；无已发现记录且成功时用同一 planner 返回 point-in-time plan/outcome | 否 | 否 | busy/recovery-required/corrupt/admission/preflight 失败时不产生 plan；`adopt` 不支持 DryRun |
| `read(Page|Backlinks)` | 可完整稳定扫描的根事实 | 是 | 成功最终完整扫描后是 | admission/scan 失败 |
| `read(Check)` | 可完整稳定扫描的 root/current/active 事实 | 否 | 成功最终完整扫描后是 | admission/scan 失败 |
| `read(Links|Relations|Status|Recoverable)` | 对应稳定读事实 | 是 | 否 | admission/scan 失败 |
| `maintain(ExternalFilesChanged)` | marker、runtime、完整索引闭包 | 否 | 它自身 | admission/preflight 失败 |
| `recover()` | runtime 与 active/current 结构 | 否 | 否 | admission 失败 |

普通 request 的 root-state/closure 权威规则：candidate 仅允许 create、status.set 与 relation.add/remove 的可证明局部闭包；generate-conclusions 要完整受管理闭包；move/promote/collapse、所有链接改写、trash、restore 与 adopt 要完整链接事实闭包。registered 的全部普通写都要完整链接事实闭包。任何缺失、链接、异常节点、无法读取/解析/摘要的 required closure 是 `runtime.closure_incomplete`。adopt 只在 candidate、完整闭包、runtime settled 且 ledger 无矛盾时允许。cache 可分类降级并 warning；authority/ledger 矛盾或无法耐久已决定结果为 fatal。

## 7. Record、authority 与派生投影

manifest、progress、epoch 与 `lifecycle/transitions/<generation>.json` 都是 immutable create-only hash-linked record；manifest 在其 owned artifact 前发布，保存 immutable request、before/after closure digest、精确 owned artifacts 与 cleanup list。内容提交后绝不因派生失败回滚；release 前必须有一个 matching published 或 invalidated epoch。

`active.json` 是当前 active T 与阻塞 R 的唯一发现 authority。它以 canonical encoding 保存 `generation`、`predecessor_generation`、`predecessor_digest`、对应 transition digest 和按 operation id 排序的完整 active entries。每个 entry 带 operation id、kind、progress digest、due/retry deadline、R -> T 引用或阻塞原因。`index/head.json` 同样保存 generation、predecessor 与 immutable epoch digest；它是 ledger head 的唯一 authority。authority replace 使用同目录的 exact staged regular file：写完整字节、file fsync、原子 replace、父目录 fsync。不得在 memory、mtime 或目录枚举中推断 generation。

每个 T/R membership 或 deadline 变化由该 T 或 R 的 progress 承载 `catalog-transition-intent` 与 `catalog-transition-committed`；二者都绑定同一 transition id。index head 的发布使用 `index-transition-intent` 与 `index-transition-committed`，成员为 epoch head 而非 active entry。

| 序号 | durable 写入 | 必含认证事实 | 下一步前条件 |
| --- | --- | --- | --- |
| 1 | append `catalog-transition-intent` progress | transition id、expected generation/predecessor digest、精确旧/新 entry、candidate authority digest | record 与 progress 目录 fsync |
| 2 | create `transitions/<next-generation>.json` | 与 intent 字节绑定、predecessor、candidate digest | file 与 transitions 目录 fsync |
| 3 | 写 `active.<transition-id>.staged` | 完整 candidate authority、next generation、transition digest | file fsync，随后以 `replace_exact_regular` 重验 target |
| 4 | replace `active.json` | staged bytes 与 intent/transition、target identity/digest 精确匹配 | replace 成功且 lifecycle 父目录 fsync 成功；否则进入 authority 变更恢复表 |
| 5 | append `catalog-transition-committed` progress | observed next generation、authority digest | 仅步骤 4 已重新认证且父目录 durability 成功后；progress 目录 fsync |
| 6 | stage/replace `schedule.json` | active generation 与 digest | lifecycle 目录 fsync；失败可后续重建 |

对于 index，步骤 1 的 `index-transition-intent` 绑定 expected head、candidate epoch record 和 cache digest；步骤 2 创建 immutable epoch；步骤 3--4 以 `head.<intent>.staged` 经 `replace_exact_regular` replace `head.json` 并 fsync index 父目录；步骤 5 append `index-transition-committed`；最后才可 stage/replace `cache.json`。cache publish 失败是可分类降级，head durable 失败是 fatal。每个 content operation 的 settled proof 是 committed head 所指的 published/invalidated epoch，而非 cache。

| 观察到的 catalog/head 持久现场 | 仅允许的恢复动作 | 不变量 |
| --- | --- | --- |
| `catalog-transition-intent` 或 `index-transition-intent` 已 durable，authority 仍为变更前 authority 版本 | 验 transition/staged/candidate 摘要，按 authority 变更恢复表发布该记录唯一授权的下一 authority 版本 | 只接受与 owner、变更前版本和 candidate 完全匹配的记录；不扫描 terminal |
| 下一 authority 版本已 durable 且父目录已重新认证/fsync，commit progress 缺失 | 验 generation、变更前版本、transition digest 后 append 对应 committed progress | authority 已决定 membership/head |
| intent/transition/staged 已有但 authority 未变且 staging 损坏或缺失 | 从 intent/transition 重建 exact staged bytes，再发布下一 authority 版本 | 不能改变成员或 head |
| authority 已 durable，schedule/cache 缺失、旧 generation 或摘要不符 | 从 authority O(A) 重建 schedule；从 head/current content 重建 cache | projection 不改变 authority；成功静默 |
| committed progress 与 authority 不匹配，authority generation 跳跃，或无 matching intent 的中间记录 | `runtime.corrupt`，保留 | 稳定 catalog/head 损坏 fatal |

以下 authority 变更恢复表同时适用于 `active.json` 与 `head.json`。变更前 authority 版本是本次变更开始时已持久的 generation 与 digest；下一 authority 版本是 matching intent 唯一授权的 generation 与 digest。每行都必须精确匹配 owner id、变更前版本、下一版本、staged digest 和对应 intent；该表不适用于 schedule/cache projection：

| replace / fsync 观察 | 锁内或重启后可见 target | 唯一允许动作 | 结果 |
| --- | --- | --- | --- |
| replace 返回错误 | 变更前 authority 版本 | 保留 intent/staged，返回 `runtime.durability_failed`；后续 recover 才可重新尝试 | 不 commit、不 release、不参与 AdvanceDue |
| replace 返回错误 | 下一 authority 版本 | 重新认证 intent、owner、变更前版本、下一版本与 target；成功 fsync authority 父目录后才 commit | fsync 失败为 `runtime.durability_failed` |
| replace 返回成功、父目录 fsync 失败 | 变更前 authority 版本 | 保留 intent/staged，返回 `runtime.durability_failed` | 不 commit、不 release、不参与 AdvanceDue |
| replace 返回成功、父目录 fsync 失败 | 下一 authority 版本 | 重新认证全部事实并成功 fsync authority 父目录后才 commit | fsync 再失败为 `runtime.durability_failed` |
| 重启后未确认 durability | 变更前 authority 版本 | 仅 matching intent 可重新 stage/replace，再认证并 fsync | 不可把旧 progress 当 committed |
| 重启后未确认 durability | 下一 authority 版本 | 仅 matching intent 可重新认证并 fsync authority 父目录，再 append commit | 下一版本在此之前不参与候选或 release |
| 任一 replace/重启观察 | 缺失、符号链接、目录、其他 identity/digest/generation | `runtime.corrupt`，保留所有现场 | 零 commit、零 release、零 AdvanceDue |

只有此表完成 authority 父目录 fsync 并写入 matching committed progress 后，catalog membership 才能影响 AdvanceDue，head 才能证明 content settled 并 release。`schedule.json`/`cache.json` 的 projection 崩溃表不能代替该确认；projection 可以在安全分类后重建或降级，不改变 authority。

需要恢复的持久现场 discovery 不新建第二 authority。只有显式 `recover()` 可在只读 discovery 阶段、且已认证有效运行期根锁记录后枚举 `operations/*/progress`，寻找没有 matching committed tag 的 `catalog-transition-intent` 与 `index-transition-intent`，包括只有步骤 1 的 owner；normal AdvanceDue、check 与正常 read 只从 active/head authority 发现候选，绝不枚举 terminal operation progress。取得运行期 root lock 后，recover 以 owner operation id、intent payload、变更前 authority 版本、当前 authority 及已存在的 immutable transition/epoch 交叉认证；不能从 terminal projection、目录存在或 phase 名猜测。

| explicit recover 发现的 owner 持久现场 | authority / immutable record | 允许的锁内收敛 | 不变量 |
| --- | --- | --- | --- |
| 仅步骤 1 intent | 变更前 authority 版本，transition/epoch 缺失 | 创建与 intent 匹配的 immutable transition/epoch，随后走 authority 变更恢复表 | intent-only owner 可发现；不扫描解释其他 terminal record |
| 步骤 1--2 | 变更前 authority 版本，immutable transition/epoch 精确匹配 | stage intent 唯一授权的下一 authority 版本，随后走 authority 变更恢复表 | owner、变更前版本、record digest 三者一致 |
| authority replace 前后 | 变更前版本或下一版本，commit 缺失 | 仅按 authority 变更恢复表重试/认证/fsync，随后 append commit | 不得让未确认的下一版本影响 active discovery/release |
| committed progress 缺失 | 已 durable 的下一 authority 版本与 matching immutable record | append exact committed progress | 不创建第二 transition/epoch |
| 伪造/重复 intent，owner 不匹配，变更前/下一版本、transition、epoch 或 authority 不匹配 | 任意 | `runtime.corrupt`，零写 | 只 matching intent 可恢复 |

| projection 崩溃窗口 | schedule 规则 | cache 规则 |
| --- | --- | --- |
| authority/head 尚未 durable，只有 projection staging | 仅清理 intent 精确列举的 staging，或覆盖为当前 active generation | 仅清理 intent 精确列举的 staging；不得把它当作新 cache |
| staging file durable、replace 前 | 由 active authority 重建/replace 同 generation projection | 由 committed head 与 current content 重建/replace；head 未提交则丢弃 staging |
| replace 已返回、父目录 fsync 未完成 | 重读 authority；只有 projection generation/digest 与 authority 匹配才保留，否则重建 | 重读 head/current content；只有 head generation、content digest、cache digest 全匹配才保留，否则重建 |
| replace 与父目录 fsync 均完成 | 接受同代 projection；过期时静默重建 | 接受同代 cache；不能匹配时标为可分类降级并重建 |

staging 的路径、类型和摘要由对应 intent 精确列举；任何未知 staging、无法验证的 predecessor 或 replace 目标异常都是 `runtime.corrupt`，不以扫描历史 record 修复。

`schedule.json` 是从 active catalog O(A) 重建的派生物；`cache.json` 只有与同代 head、内容和完整扫描匹配时可用。两个 projection 预期缺失时 create-only publish，预期存在时只经 `replace_exact_regular` 替换；错误/不确定结果重读并按 projection 崩溃表重建或降级。稳定 catalog 或 head 损坏为 fatal，禁止扫描 terminal N 猜测。第一版允许 O(A) active scan，不预设 B+ tree；sealed fold checkpoint 限制单个 T 的 retry tail。

active catalog 仍是 normal lifecycle candidate discovery 的唯一 authority。schedule、head、cache 以及 warning/handoff 的既有语义不因运行期根锁记录或显式恢复而改变；它们只能在前述认证和 durable 顺序完成后被消费。

`read` 是 Check、Page、Links、Backlinks、Relations(in/out)、Status、Recoverable 的联合。其三阶段规则如下，preflight snapshot 不直接产生 projection：

| ReadRequest | 阶段 1：preflight | 阶段 2：自动 lifecycle | 阶段 3：最终 projection / index 输入 |
| --- | --- | --- | --- |
| Page、Backlinks | admission 后取只读 token | no-change/change：按单 candidate 规则；schedule-rebuilt：0 candidate/0 handoff 后返回；busy：跳过；fatal：停止 | 在阶段 2 后取得最终完整稳定 scan；该同一次 scan 同时生成 projection 与机会索引输入 |
| Check | admission 后取只读 token | 不运行 | 取得最终完整稳定 scan；该同一次 scan 同时作 active 深检、projection 与机会索引输入 |
| Links、Relations、Status、Recoverable | admission 后取所需 token | no-change/change：按单 candidate 规则；schedule-rebuilt：0 candidate/0 handoff 后返回；busy：跳过；fatal：停止 | 阶段 2 后重新读取所需 current/active facts 并投影 |

阶段 2 的 no-change/change 都要求丢弃 preflight snapshot；busy 也必须重新取得稳定最终 scan/事实，成功时返回 projection 加本次 busy warning。阶段 2 fatal 或最终 scan 不稳定/节点异常时返回 error 且无 projection。只有锁内对 marker、runtime generation、active/head generation、全部所需成员类型和摘要作精确重验证、并证明完全未变时，才可复用 preflight bytes；该复用仍被视作阶段 3 的最终 snapshot。Page、Backlinks、Check 的机会索引维护仅能消费该最终完整 scan；维护前须在锁内重验证其 token，busy 时跳过维护，不改变 projection。

## 8. 自动生命周期与结果语义

自动 `AdvanceDue` 的触发者仅为普通写、trash、restore 和除 Check 外的 `read` 成员。排除 init、adopt、check、recover、notification、DryRun。每 invocation 最多处理一个 lifecycle candidate，最多一次外部 handoff。`recover` 收敛所有已有中断现场，不推进 due、不新建 handoff、不调用 Adapter。

| 自动 lifecycle 的 schedule 观察 | 本 invocation 动作 | candidate / handoff | 后续 |
| --- | --- | --- | --- |
| active authority 可认证且 schedule 同 generation/digest | 正常选择最多一个 eligible candidate | 最多 1 / 最多 1 | 按 lifecycle 状态机收敛 |
| schedule 缺失、旧 generation 或摘要不符 | 在运行期 root lock 下从 active authority O(A) 重建并耐久发布 schedule，随后立即返回 automatic result | 0 / 0 | 最终业务 action/read 可继续；下一 eligible invocation 才可选择 candidate |
| schedule rebuild 不能耐久 | `runtime.durability_failed` | 0 / 0 | fatal，停止本 invocation |
| active authority 不可认证 | `runtime.corrupt` | 0 / 0 | fatal，停止本 invocation |

常规成功、内部 schedule 重建、cleanup、receipt 与成功 handoff 都静默写 durable audit。failed、unknown、deferred、busy 只在本 invocation 新观察且可行动时产生 warning；failed/unknown 以 24 小时节流，purge eligibility crossing 可绕过旧节流一次。任何命令若自动生命周期已 durable 改变而后失败，error envelope 最多携带一个有序 `lifecycle_result` 和本次新 warning；若自动生命周期本身 fatal，另有 `stopped_at_operation_id`。fatal 不返回 projection。

| warning | 来源事实与是否 durable | next attempt | 去重身份与 actionability | code / 返回位置 | fatal 边界 |
| --- | --- | --- | --- | --- | --- |
| failed | 本 invocation 新 durable 写入 `handoff-failed(sequence, next_attempt_at)` | record 指定的 24 小时 deadline | 当前新 outcome；修复外部环境或在 deadline 后重试 | `lifecycle.handoff_failed`；成功结果的 `warnings[]` 或后续 error envelope | failed record/active transition 不能 durable 即 `runtime.durability_failed` |
| unknown | 本 invocation 新 durable 写入 `handoff-outcome-unknown(sequence, next_attempt_at)`，由 timeout 或 prepared 无 outcome 得出 | record 指定的 24 小时 deadline | 当前新 outcome；下一次可能重复投递同一 T | `lifecycle.handoff_unknown`；同上 | unknown record/active transition 不能 durable 即 fatal |
| deferred | 本 invocation 已处理一个 candidate，且在其后的同代 catalog 中实际发现另一 eligible candidate；不写新 record | 无；下一次 eligible invocation 可继续 | 当前实际 budget exhausted；调用方可再次触发 | `lifecycle.deferred`；成功结果的 `warnings[]` | catalog 不可认证为 `runtime.corrupt`，不降级 |
| busy | 本 invocation 自动步骤实际发现 live 运行期 root lock；本身非 durable | 无；锁释放后重试 | 当前自动步骤被跳过时可行动 | `runtime.busy`；稳定 read 的 `warnings[]`，或已有 lifecycle 后的 error envelope | lock record 损坏/no-follow 失败为 fatal；直接写请求竞争是 error 而非 warning |

not-due、有效 R 阻塞、没有 candidate 与成功 schedule 重建不产生 deferred 或其他 warning。warning 是 invocation-local：旧 catalog/progress、到达 `next_attempt_at`、purge crossing 或历史 live lock 均不得单独重播；只有本 invocation 新 sequence 写出 failed/unknown，或实际预算耗尽/实际锁竞争时才返回。deadline 只决定是否允许新 attempt，新 attempt 写出新 outcome时才是新 warning；不新增 warning delivery artifact。warning 不是 durable lifecycle result，也不泄露 package、epoch 或 consumer 内部事实。

## 9. Trash、Restore 与 Handoff

恢复窗口为 7 天；之后 30 天进入 purge eligibility；failed/unknown 的 retry 间隔为 24 小时。Trash T 与 Restore R 为单向 R -> T；T 不保存反向关系。active catalog 收录 T 及阻塞 R。

| R / T durable 状态 | 允许动作 | T 生命周期 | cleanup |
| --- | --- | --- | --- |
| 无 R，retained 且未到恢复期限 | 可建 R | 可按 due 候选处理 | 保留 package |
| R 建立至内容提交/epoch 未 settled | 只按 R 前向/回滚规则收敛 | 阻止 T | 保留 |
| R 已 settled、`consumption-required` | 重验 R/T/全图/epoch 后 append T consumption | 阻止 T | R 不得 clean |
| T consumption durable、R 未观察 | R append receipt | 不 handoff/purge | 保留 |
| R receipt durable | 精确清理双方授权物 | 不 handoff/purge | 仅列举成员可幂等清理 |
| R rollback/abort 且 T 未消费 | 清理 R 自有物 | T 回原期限 | 保留 T package |
| 关系/proof/摘要/节点异常 | `runtime.corrupt` | 阻止 T | 零清理 |

Restore 输入错误码不折叠：词法错误、missing、expired、consumed、conflict、corrupt、recovery-required 各有独立机器代码。新 R 仅在 retained、未过 7 天、无 active R、package 与完整闭包可证明时建立；T 已 accepted/purged/consumed 或超过恢复期明确拒绝。

每次 handoff 依以下耐久状态机，artifact 前必有精确 durable 授权：

```text
handoff-attempt-authorized(sequence, exact_copy_paths, members, digest)
-> copy + fsync
-> handoff-attempt-prepared
-> deliver in isolated 30s worker
-> accepted | failed | unknown
```

根锁从 authorized 连续持有到 worker 实际停止、outcome、catalog transition durable 与精确 cleanup 完成；不允许外部调用期间释放。30 秒仅覆盖可终止隔离的外部调用，本地 copy 不属于 timeout；timeout 为 unknown。`accepted` 只表示 `handoff-accepted` outcome record 已 durable，绝不表示仅外部已收到或已接受。外部已接受但该 record 未 durable 与 prepared 无 outcome 相同，先收敛为 unknown，绝不 cleanup/purge 推断。新 sequence 向 Adapter 发送同一 Trash operation id；Adapter 无 idempotency key 或查询回执，因此允许顺序重复外部包。仅 durable accepted 禁止之后任何 Adapter 调用并授权最终 cleanup。purge crossing 时允许一次新尝试；永久清理仅由该次 post-purge active call 的 explicit failed 授权。未知必须写新 24 小时，不得每次 query 重试。

| T durable 事实与时间 | 当前自动 lifecycle 动作 | durable 后继 |
| --- | --- | --- |
| retained、未 due、无阻塞 R | 不调用 Adapter | 保留 |
| retained、due、无阻塞 R | 写 authorized，copy/fsync，写 prepared，调用一次，外送 T 的 operation id | 仅 durable accepted、failed 或 unknown |
| prepared 无 outcome，含外部已接受但 accepted record 未 durable | 写 unknown；本次不调用 | 24 小时后才可用新 sequence 重试并允许重复包 |
| failed/unknown、未到 `next_attempt_at`、未 crossing purge | 不调用 | 保留；历史 record 零 warning |
| failed/unknown、到 `next_attempt_at` | 新 sequence 的一次完整尝试 | accepted、failed 或 unknown |
| 首次 crossing purge eligibility | 可无视旧节流作一次新尝试 | 仅该 active call 的 explicit failed 可写 purge-committed |
| durable accepted 或 purge-committed | 精确 cleanup | 永不再调用 |

## 10. 崩溃恢复表

| 需要恢复的持久现场 | `recover` 收敛 | 禁止 |
| --- | --- | --- |
| 有效运行期根锁记录但无 live owner | 取得该记录对应的 OS lock，按本表和第 7、9 节收敛全部需要恢复的持久现场；authority/content/catalog/head/outcome commit 与不可延期 cleanup durable，projection 已 durable 或安全分类且无未认证现场后 unlink 记录并 fsync `.draft/` | 普通入口删除记录、扫描 terminal 或隐式 recover |
| 有效运行期根锁记录且 OS lock live | 返回 `runtime.busy` | 抢占或删除记录 |
| 运行期根锁记录损坏或无法认证 | 返回 `runtime.corrupt`，保留记录 | 删除、猜测 owner 或继续写 |
| marker/runtime/descriptor 匹配、无运行期根锁记录、但 descriptor cleanup remnants 存在 | 先取得 stable OS root lock，锁内重读仍无记录后 durable 发布 `holder_kind=recovery` 记录，按 descriptor 精确幂等清理；完成最终 unlink/fsync/release | initialize、ordinary/read/DryRun 写入或隐式清理；无 marker 的初始化现场不在此列 |
| 无 marker 的初始化现场 | 不处理 | 创建、删除或猜测 init |
| active R/T consumption 现场 | 先按 R->T 表完成证明/receipt | 推进 T、清理 T package |
| normal manifest 未内容提交 | 依 durable direction 与精确 material 前向或回滚 | applied 位猜测 |
| 内容已提交未 epoch settled | 仅前向 settled | 回滚内容 |
| explicit recover 发现的 intent-only / catalog 或 index owner 现场 | 仅枚举 progress 的未 committed intent，并按 owner/id/predecessor/record/current authority 表交叉认证后收敛 | 在 AdvanceDue、check 或 normal read 枚举 terminal progress |
| index intent/epoch/head/cache 现场 | 仅按 matching intent 和 head crash 表前向 publish/commit；由 head/current content 重建 cache | cache 当权威或跳过 head 认证 |
| handoff authorized/prepared/accepted/purge 现场 | 收敛已写 record、unknown 或 durable accepted 后的精确 cleanup | 新建 attempt、Adapter 调用；不得由外部可能已接受推断 accepted |
| active catalog transition/authority/schedule 现场 | 仅按 matching intent、owner、变更前 authority 版本、下一 authority 版本与 generation 的认证表前向 publish/commit；重建 schedule | 扫 terminal audit 猜状态、接受无 intent 的中间态，或在 schedule rebuild invocation 选择 candidate |
| 任意不可认证节点/record | `runtime.corrupt`，保留 | 写、删、降级为成功 |

## 11. 输出、错误与验收

对外只给调用者判断或行动需要的信息。成功 read 返回 projection 与当次 actionable warnings，不带 routine lifecycle result。机器协议精确；文案不是多层逐字合同。

notification 的输入/输出为以下封闭表；只有表中明确允许的 delivered 行返回最小 `{result_type: "files_changed_notification", delivered: true}`，不带 warning、epoch、cache、consumer、operation 或 routine 状态。表中 error 行绝不带 `delivered`；不能以“非 fatal”将其投影为 delivered：

| root / 参数 / maintain 观察 | 输出 | 退出码 |
| --- | --- | --- |
| root 选择、marker、参数或 request shape 无效 | 对应 `runtime.root_invalid` 或 `runtime.request_invalid` error | 2 |
| live root lock busy | 最小 delivered；consumer 稍后由下一通知/命令维护 | 0 |
| `maintain` admission 返回 `runtime.recovery_required`：有效运行期根锁记录但无 live owner，或 marker/runtime/descriptor 匹配、无运行期根锁记录但仍有 `InitDescriptor` cleanup artifacts | `runtime.recovery_required` error，不带 `delivered`；cleanup-remnants 来源零 stable OS root lock/record/recover/AdvanceDue/index maintenance/cleanup，目录字节不变 | 3 |
| 稳定 scan 无变化 | 最小 delivered | 0 |
| cache stage/publish 分类降级，head 已 durable | 最小 delivered | 0 |
| scan token 因外部变化失效、但 runtime/ledger authority 仍可认证 | 最小 delivered；不写结果 | 0 |
| runtime schema/node/record corrupt，或 stable catalog/head/ledger contradiction | precise fatal error，零 delivered 字段 | 3 |
| 已决定 epoch/head/catalog result 无法 durable | `runtime.durability_failed` error，零 delivered 字段 | 3 |
| 未分类 I/O 或 worker/runtime failure | precise error，零 delivered 字段 | 4 |

notification 只在 adapter/输出处路由 `maintain(ExternalFilesChanged)`，不读投影、不调用自动 lifecycle 或 `recover()`；live busy 与无 live owner 的 recovery-required 是不同结果，不能折叠。busy/external-change 行仅说明信号被接收，不能被解释为维护成功。

`maintain` admission 的 notification 投影必须逐一审计：有效 stale record 与 marker-ready cleanup remnants 均为上述 `runtime.recovery_required` error；schema/node/record 或 authority contradiction 为 `runtime.corrupt`；已决定结果无法耐久化为 `runtime.durability_failed`；只有 live busy，以及 stable authority 仍可认证时 scan token 因 external change 失效，保留表中最小 delivered 合同。其他 external-change、corrupt 或 durability admission error 均不含 `delivered`。

错误码至少包含 `runtime.busy`、`runtime.platform_unsupported`、`runtime.init_conflict`、`runtime.root_invalid`、`runtime.request_invalid`、`runtime.schema_unsupported`、`runtime.corrupt`、`runtime.external_change`、`runtime.recovery_required`、`runtime.durability_failed`、`runtime.closure_incomplete`，以及独立 Restore 领域代码。退出码：0 成功或 delivered，2 输入/根拒绝，3 runtime/lifecycle/durability/external state，4 未分类错误。

离线基准至少包含 100,000 个 terminal operation、10,000 个 active catalog entry、10,000 个 handoff entry，证明正常 AdvanceDue、check 与 read 成本不随 terminal operation 数增长。报告 O(A) active scan、单 Trash checkpoint tail、全量页面扫描字节/解析数、内存和 wall time。integration 必须在 Windows、macOS、Linux 的合格本地文件系统验证 no-follow/no-replace、live lock、durability 失败和 worker timeout；不合格卷必须写前拒绝。核心与 adapter 的 unit/integration 按 scoped AGENTS 分开执行，旧实现与旧测试 replace-don't-layer 删除。

验收必须故障注入：Windows、macOS、Linux 三平台的 no-record stable OS root lock（available/live/unverifiable）、`replace_exact_regular` 的 missing、expected regular、symlink、directory、identity change、replace error、parent fsync error；DryRun 无 record 不 probe，以及已发现记录的 try-lock live owner、无 live owner、记录消失和 corrupt；marker publish 后无 record、recovery record publish、每个 cleanup member、记录 file fsync、`.draft/` fsync、unlink 和最终目录 fsync；catalog/head 的 intent-only、intent+immutable record、变更前 authority 版本、下一 authority 版本、replace 前后、commit 缺失、伪造/mismatch；authority staged file、authority replace 返回、每个 authority 父目录 fsync、projection replace、accepted outcome、worker timeout 与 lock competition。重复 `recover()` 后 active membership、next candidate、R block、retry deadline 与 warning 只能由已认证 authority 和当次新 result 决定；normal/check 不读取 terminal operation。另验证每个自动触发入口在缺失或过期 schedule 时本 invocation 为 0 candidate/0 handoff、下一 eligible invocation 最多一次 handoff；deadline 前后未新建 attempt 的 failed/unknown 零 warning，及 preflight 后 lifecycle change/busy/fatal 的 ReadRequest 三阶段表、notification 的每个输出行、无 live owner 记录的 `runtime.recovery_required`（退出码 3、无 `delivered`）、外部已接受但 accepted 未 durable 时的 unknown 重投与 durable accepted 后零调用。公开边界测试分别覆盖 `Lifecycle.initialize` 直达，以及 `RuntimeFactory.for_root` 返回的 OperationRuntime 六方法；类型与 export graph 断言 factory 不含、不返回且不承诺 Lifecycle，CLI 除该公开入口和 factory 外无 internal runtime import。
