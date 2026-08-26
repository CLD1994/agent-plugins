# Issue 01 实施计划：participant closure 的锁内材料 preflight

## 0. 基线、范围与证据

### 固定基线

- Git 基线：`a8d4542b65e44ee9b0f2f44c5dfcfb6409b796a7`（本计划写入前的 `HEAD`）。
- 计划对象：该基线之上的混合未提交 Draft runtime 变更中，Issue 01 尚未完成的
  participant material preflight。
- 规格来源：
  `.scratch/draft-coordinated-recovery-ownership/spec.md`、
  `.scratch/draft-coordinated-recovery-ownership/issues/01-converge-unlocked-recovery-candidates-under-lock.md`、
  `.scratch/draft-coordinated-recovery-ownership/issues/03-lock-owned-restore-trash-classification.md`、
  `.scratch/draft-coordinated-recovery-ownership/issues/03-round-3-plan.md`、
  `.scratch/draft-coordinated-recovery-ownership/issues/05-verify-recovery-refresh-and-fd-semantics.md`，以及
  `docs/review-notes/draft-recovery/2026-08-26-round-7.md`、第 10、11 轮记录。
- 标准来源：仓库根 `AGENTS.md` 和 `plugins/draft/AGENTS.md`；特别是失败关闭、唯一权威来源、
  单元/集成测试边界、真实文件系统验证和复杂修复先写计划的要求。

### 范围

本计划建立唯一的、只读的整组 `RecoveryPreflight`（名称仅为实现预估），并把它作为恢复与
Trash 生命周期所有现场副作用的 capability（能力凭据）。它消费 Issue 03 已提供的完整关系
facts，证明 matching lock、participant closure、current/pending、transition 及被本步消费的
材料都仍是当前安全事实。

不在本任务中重写 Issue 03 的全图 relation/owner/participant/action 分类模型，也不修改 journal
schema、合法 phase transition、稳定错误码或 CLI 退出码。Issue 05 独占文件描述符的
`fstat`/close/主异常优先级/exactly-once close 矩阵及其验收；本任务调用其已经定义的 no-follow
资源边界，不以自行吞掉 close 错误替代该契约。

### 已确认的行为契约

1. 未持锁 discovery 仅发现候选 ID；它既不携带可写授权，也不得发布 pending、写删 journal、
   清理 copies/目录/package/evidence、handoff、unlink、解关联或 release。
2. 成功取得 matching `operation.lock` 后，必须丢弃 discovery snapshot，从 Issue 03 的当前完整
   图重新取得 owner、按顺序且精确的 participant closure 与唯一 action。只有 Restore owner
   能处理 `PRECLAIM`、`MATCHED`、`POST_UNCLAIM_CLEANUP` 的 R/T closure。
3. 每个 participant 的 current 和 pending 都是独立的授权投影输入。两者均须经过 raw/type、
   operation ID/kind/关系绑定、合法 transition 和材料闭包证明；不得把 current/pending 的授权叶、
   摘要、目录或允许状态取 union，也不得让 pending 掩盖 current 的失败。
4. 所有将消费现场的动作之前都必须通过同一只读 preflight。包括 pending publish、journal/terminal
   写入、replace、unlink、copies/operation 目录/package/evidence/attempt/link-index staging 清理、
   解关联、handoff、purge 和 release。
5. 每次 durable transition 后，以及 pending publish、解关联、cleanup、handoff 或下一项副作用前，
   旧 facts、journal snapshot、材料授权和 capability 均立即失效；仍在同一 matching lock 内完整重读
   Issue 03 facts 和整组 preflight 才能继续。
6. preflight、关系或材料证明失败时，整组失败关闭：保留 matching lock、所有 participant 的
   current/pending 原始字节和节点、copies、attempt/evidence、package、关系和 operation 目录；
   不发布、不写、不清理、不 handoff、不解关联、不 release。损坏/非法事实使用其原有稳定错误，
   `operation.recovery_required` 只用于 lock 竞争或合法候选已由其他 owner 持有。
7. 已提交 handoff/purge 只可按当前 journal 证明的授权做幂等收敛，绝不再次调用外部 handoff。
   无关联 `trash_retained` 与具完整失败记录的 `trash_handoff_pending` 不由 recovery 代替
   lifecycle 推进。

### 项目规则

- 单元测试只验证纯分类、授权投影和状态转换，使用受真实接口约束的替身，不能访问真实文件系统。
- no-follow、inode/identity、目录成员、锁竞争、pending durability、descriptor 故障、package、
  handoff 和清理属于真实文件系统集成测试。测试按公开入口和可观察现场断言，不复制私有调用图。
- 不得通过扩大 allowed member、用会删除的 helper 充当 preflight、先写后回滚、将损坏降格为
  `operation.recovery_required`、catch-and-continue 或递归删除来获得绿色。
- 当前 runtime 改动来源混合，不能据此拆成可独立回退的实现提交。本计划之外的代码实现不直接提交；
  固定新基线、独立复审和整组边界确认后再决定提交策略。

### 实现预估（非固定接口）

预计影响 `operations.py` 的材料 preflight 边界，`trash.py` 的 Restore resolver，
`trash_lifecycle.py` 的生命周期和已提交 Trash 清理，以及必要时 `trash_package.py` 中已有授权解析的
纯读取投影。预计测试主要位于 `tests/unit/test_operations.py`、`tests/unit/test_trash.py` 和
`tests/integration/test_operations.py`、`test_trash.py`、`test_trash_lifecycle.py`。数据类、helper 名称
和模块落点可按实际数据流调整；下列输入、输出、失败语义和副作用顺序不可改变。

## 1. 权威输入、preflight 输出与材料闭包

### 1.1 唯一权威数据流

```text
公开入口
  -> 未持锁只读 discovery（候选 ID；无写授权）
  -> acquire matching owner lock
  -> 丢弃 discovery snapshot
  -> Issue 03 全图只读 reader（owner, closure, action, current/pending facts）
  -> Issue 01 整组只读 RecoveryPreflight（lock + transition + material closure）
  -> capability 授权的一项副作用
  -> 丢弃 capability 与全部快照
  -> 同一 matching lock 内重新 reader + 重新 preflight
  -> 下一项副作用或失败关闭
```

preflight 的输入必须来自同一轮锁内读取：matching lock 的 operation ID/身份、Issue 03 的
relation/owner/participant/action facts、closure 内每个 operation 的 current/pending 原始节点与解析结果，
以及 action 本步将消费的材料。它的成功输出至少包含：

- owner ID 与 matching lock 的一致性；
- 精确、去重、有序的 participant ID 元组；
- 未被重派生的 Issue 03 action；
- 每个 participant 的 current、pending、effective journal，以及经过 transition 验证的两个
  独立材料授权投影；
- 每个将访问节点的 no-follow 身份与摘要证明；
- 本 capability 唯一允许的下一项副作用类别。

它是纯读：不得调用 `resolve_journal_pending()`、`write_journal()`、清理 helper、handoff、
`release_lock()`，也不得将预检中读到的任何文件视为可跨下一次副作用复用的授权。

### 1.2 relation/action 与 closure 决策表

| Issue 03 当前全图结果 | matching owner / closure | action | preflight 与 resolver 结果 |
| --- | --- | --- | --- |
| 普通、无 R/T capability 的可恢复 operation | operation 自身 / `(owner,)` | 通用锁内恢复动作 | 证明该单 participant 的 current/pending 与材料后才可进入通用 resolver。 |
| `PRECLAIM` | Restore R / `(R, T)` | `RECOVER_PRECLAIM` | 两者都进入材料闭包；仅 action 对应的 bootstrap 安全收敛可继续。 |
| `MATCHED` | Restore R / `(R, T)` | `RECOVER_MATCHED` | 两者都进入材料闭包；只允许 capability phase 分区内的前向、回滚、提交或已提交清理。 |
| `POST_UNCLAIM_CLEANUP` | Restore R / `(R, T)` | `CLEANUP_POST_UNCLAIM` | 即使动作表面只清 R，T 的 journal、package、attempt/evidence 与身份仍必须预检。 |
| 无关联 `trash_retained` 或完整失败记录的 `trash_handoff_pending` | 无 recovery 推进 owner | 无 R/T capability | recovery 保留；仅既有 lifecycle 入口可在其自身 matching lock 下走相同材料门。 |
| raw/type、R forward plan、图关系或 phase/action 失败 | 无 | 无 | 返回 Issue 03 对应稳定错误，零副作用并保留全部现场。 |
| matching lock 缺失、不是 capability owner 或竞争失败 | 无 | 无 | `operation.recovery_required`；丢弃候选/快照，绝不使用其继续执行。 |

非法图、owner 不一致、closure 缺成员、重复成员、未知 action 或 action/relation 组合不匹配都不产生
capability。不得从 T reverse claim、旧 `Journal`、`RestoreLinkState` 或 resolver 的局部 phase 表重新推断
owner、closure 或 action。

### 1.3 current/pending 授权投影决策表

| current | pending | preflight 必须证明 | 结果 |
| --- | --- | --- | --- |
| 合法 | 无 | current 的 identity、kind、关系绑定、transition 前置、材料投影和摘要 | 可产生只覆盖 current 的 capability。 |
| 合法 | 合法 | 两份节点均独立完成 raw/type、identity、kind、关系绑定、合法 transition、材料授权、成员与摘要；effective 只能在这些证明后选择 | 可继续；不得合并二者授权。 |
| 无 | 合法 pending | 仅规格明确允许的 create-only bootstrap；pending 本身完成上述证明，且 closure/action 合法 | 可继续但预检不发布 pending。 |
| 无 | 无且目录严格为空 | 不进入 journal 图；只作为 empty allocation residue 的锁内再次确认对象 | 仅验证仍为空后才允许对应清理。 |
| 无 | 无且目录非空 | 无法证明授权 | `operation.inconsistent_state`，保留目录与 lock。 |
| 任一节点损坏、类型/身份不符、关系字段改变或 transition 不合法 | 任意 | 不选择 effective，不借另一节点的材料授权 | 维持 Issue 03/现有 transition 的稳定错误，整组零副作用。 |

“材料授权投影”是从该节点自身 journal 和 action 直接派生的精确可消费集合，而非 current/pending
集合的并集。一个 material 只在本次将消费它的 current 或 pending 节点均明确授权时才可被消费；
无法表达这种交集或允许缺失规则时是阻塞，不能自行选择“以 effective 为准”或“任一节点授权即可”。

### 1.4 action 对应的材料闭包

对 closure 的每个 participant，preflight 必须先以 operation directory FD 锚定、no-follow 地验证：

| 材料类别 | 必须覆盖的事实 | 允许缺失的语义 |
| --- | --- | --- |
| `journal.json` / `journal.json.pending` | 精确节点类型、operation ID/kind、current/pending identity、字节/解析结果、合法 transition 与 relation 字段绑定 | 仅上表的 create-only bootstrap；其他缺失按既有稳定错误。 |
| `originals`、`staged`、mutation recovery copies | journal 登记叶、父目录闭包、节点类型、no-follow 身份与 before/after 摘要 | 仅既有 prepare/phase 明确允许的登记后未建或已提交后缺失；具体 phase 判断沿现有权威规则，不在本任务猜测。 |
| mutation 临时、pending、quarantine 与 link-index staging | action/phase 登记的精确叶、合法派生命名、摘要与 staging 发布状态 | 只允许既有协议已定义的 pending/quarantine 或 phase 缺失状态。 |
| Trash authoritative package | journal 派生的 package authorization、`package.json`/payload 精确成员、目录与摘要 | 仅已提交 cleanup 语义明确允许不再要求完整包时；不得把提交前缺包视为可清理。 |
| Trash `attempt` | 当前 Trash journal 与 package authorization 派生的精确 attempt 目录/叶/摘要，完整递归 no-follow 成员检查 | 仅协议明确允许的未建立或已提交后缺失叶；未知成员、链接、错误节点、未授权嵌套目录或摘要不符失败。 |
| cleanup evidence | `cleanup-evidence.json` 及 protocol 允许的 `.pending` / `.quarantine`，字段、operation/phase、授权叶、摘要和发布状态 | 仅现有 evidence 协议明确允许的状态；不得调用发布、quarantine 恢复或消费 evidence 的 helper 来判断。 |

目录遍历、读取、identity 比较和摘要检查必须使用 Issue 05 规定的 descriptor 所有权与关闭语义。
preflight 只读取、认证和收集事实；不能复用 `_clean_attempt_tree()`、`clean_authoritative_package()`、
`converge_cleanup_evidence()` 或任何会 unlink、发布、quarantine 恢复、hand off 或 release 的函数。

若当前 package/evidence/attempt helper 不能在不产生副作用的情况下提供其完整授权投影，则该缺口是
实现阻塞：先提取纯读取解析/验证边界并用现有协议测试证明等价，不能在 preflight 中复制、放宽或臆造
格式语义。

## 2. 副作用门、刷新和失败状态

### 2.1 唯一 side-effect gate

建立一个由 resolver 显式持有的 capability 参数（接口名称为预估）。下列每个动作都必须在调用点
立即验证 capability 的 owner、closure、action、lock 身份和对应材料授权；没有 capability、
capability action 不含该动作或 capability 已被前一步消耗时失败关闭：

| 动作 | 首次/再次执行前必须持有的新 preflight |
| --- | --- |
| `resolve_journal_pending()`、任意 pending publish | 包含会发布 participant 的 current/pending 全部 closure。 |
| `write_journal()`、phase/terminal/disposition 写入 | 包含写入 participant，且所有仍由 action 消费的 closure 材料。 |
| replace、unlink、copies/staging/attempt/evidence/package/operation-dir cleanup | 包含将被删除或替换的精确节点、其授权和身份。 |
| R/T 解关联 | R 与 T 的当前 relation/action、双方 current/pending 与本步后仍需保留的材料。 |
| handoff/purge | T action、attempt 与 package/evidence 认证；已提交状态不允许再次 handoff。 |
| `release_lock()` | terminal/已提交清理完成后的整个 closure，确认没有遗留未授权或未验证材料。 |

任何 durable transition、pending publish、解关联、cleanup 或 handoff 后，执行者必须废弃全部 `Journal`、
facts 和 preflight 对象，再通过同一 matching lock 按完整 reader -> preflight 顺序重读。不得只刷新
发生变化的 participant，也不得以旧 action 覆盖新的全图分类。

### 2.2 状态转换与现场保留决策表

| 状态/事件 | 允许后继 | 失败或竞争语义 |
| --- | --- | --- |
| standalone 普通 `allocating` | preflight -> durable `rolled_back` -> 刷新 preflight -> 仅授权材料清理 -> 刷新 -> release | 任一点不能证明时保留 journal/pending/material/lock；不降格为 empty residue。 |
| strict empty allocation residue | matching lock 内再次读取成员仍严格为空 -> 删除 residue/同步父目录 | 出现 journal、pending 或任意成员即按当前事实重新分类，不删除。 |
| `PRECLAIM` | R owner 的整组 preflight -> capability 允许的 bootstrap abandon/后续现有合法收敛 -> 每步刷新 | T 任一材料或关系失败同样阻止 R 写入/cleanup/release。 |
| `MATCHED` | R owner 的整组 preflight -> action 允许的 forward/rollback/commit/committed-cleanup 中一项 -> 刷新 | current/pending、关系、package/evidence 或 materials 变化时整组停止并保留 R/T/lock。 |
| `POST_UNCLAIM_CLEANUP` | R owner 的 R/T preflight -> 仅 action 授权的 R cleanup/终态 -> 刷新 -> release | T 虽未被直接删除仍是必检 participant；任何异常不得忽略它。 |
| 已提交 handoff/purge | preflight -> 当前 journal 授权的幂等 package/evidence/attempt cleanup -> 刷新 -> terminal/release | 材料异常时不清 package、不写 terminal、不 release、不 handoff。 |
| material/identity/summary/transition/graph 非法 | 无 | 返回对应稳定错误，所有 closure 节点、原始字节、目录成员、package、lock 保持。 |
| lock 竞争或合法 owner 被他人持有 | 无 | 仅 `operation.recovery_required`；不依据旧 discovery 或旧 preflight 继续。 |
| 重复 recovery | 只对仍由当前 capability 授权的现场执行 | 已完整终态为无副作用；已提交 branch 不重复 handoff/purge。 |

稳定错误必须保持现有优先级：raw/type 使用 `operation.journal_corrupt` 或路径边界错误，Restore
forward plan 为 `operation.plan_invalid`，图/phase/action/无法证明的状态为
`operation.inconsistent_state`，只将协调 lock 竞争表达为 `operation.recovery_required`。材料读取沿
既有材料/包错误码；本计划不授权新建或重映射机器码。

## 3. 实施任务与阶段门

### Task 1：先写红测试和无副作用现场快照

先新增最窄的纯单元测试，再写真实 FS 集成测试；生产实现不得先于这些反例改写。

1. 单元：对 preflight 的纯输入模型覆盖 closure/action/owner 错配、current-only、current+pending、
   bootstrap pending-only、empty residue 和禁止 current/pending 授权 union 的反例。验证输出仅为
   capability 或现有稳定错误，不模拟 I/O。
2. 集成：为每个目标入口快照 R/T（或 standalone）的 current/pending 原始字节与节点类型、
   operation members、`originals`/`staged`/link-index staging、package、attempt、evidence、lock
   字节，并记录 journal write、pending publish、cleanup、unlink、解关联、handoff、release 计数。
3. `advance_trash_lifecycle()`：到期 `trash_retained` 的 attempt 注入未知叶、普通/悬空符号链接、
   未授权嵌套目录、错误类型和已登记叶摘要错误；断言在 `trash_handoff_pending` 前失败，所有快照
   不变且 handoff 为零。
4. `recover_interrupted_operations()` 与 `recover()`：matching lock + `trash_handoff_succeeded` 和
   `trash_purge_committed` 的 attempt/evidence 非法反例；断言 package/evidence 未在清理前被消费，
   无 completed、release、handoff。
5. `restore_retained_trash()` 的执行/耐久异常：构造可发布 R pending，分别破坏 R materials，并构造
   可发布 T pending；断言 `_restore_recovery_action()` 与后续 resolver 在首次 pending publish 前
   停止，R/T 两边全部快照及 R lock 不变。合法对照保持已有 ABANDON/COMMIT/主错误语义。
6. 每种批准 relation（`PRECLAIM`、`MATCHED`、`POST_UNCLAIM_CLEANUP`）和 standalone 均覆盖
   participant 材料变化、relation/第三方 claim 变化、pending 出现和重复恢复；证明每次 transition
   后必刷新且无额外 handoff。

阶段门：反例在旧实现上因“顶层类型检查”或“先发布 pending/phase 再验证”而失败；测试必须断言
每个禁止的副作用与现场，而不能只检查最终错误码。

### Task 2：形成纯的整组材料授权投影

在 Issue 03 全图 reader 之后、第一项副作用之前建立唯一 preflight builder：

1. 取 current facts 的 owner、exact participant closure 和 action，验证 matching lock 与 owner 一致；
   不接受 caller 传入的 participant ID、旧 journal 或局部 relation 作为替代权威输入。
2. 对 closure 的每个 participant 重读 current/pending，验证 node identity、kind、关系绑定和合法
   transition；为两份 journal 分别派生材料授权投影并按上文表格验证 no-follow 成员与摘要。
3. 从当前 Trash journal 复用/提取纯 package、attempt 和 evidence 授权解析；预检只能读。
   提交前和提交后对 package 完整性的差异必须由既有 journal phase/evidence 契约决定。
4. 只有所有 participant、所有 current/pending 和 action 所需材料同时通过，才返回 capability；
   任何失败立即抛出已有稳定错误，不返回部分 closure 或“可先处理 R”的 capability。

阶段门：在非法 attempt/evidence、R/T pending、link-index staging 与普通 copies 反例中，第一次
写/发布/cleanup/handoff/release 前均已失败，现场快照字节和节点集合完全相同。

### Task 3：把 capability 接到所有 resolver 与异常分支

审计并接入以下责任边界：`_recover_locked()`、`recover_locked_operation()`、
`_recover_unlocked_candidate_locked()`、`_recover_locked_restore()`、`_recover_locked_trash()`、
`_advance_one_locked()`、`_attempt_handoff_locked()`、`_restore_recovery_action()`、
`resolve_restore_execution()`、`_commit_restore_cleanup()` 及其 `recover()`、
`recover_interrupted_operations()`、`advance_trash_lifecycle()` 公开调用方。

1. 将“读取/分类 action”和“发布 pending/终态/cleanup”拆开：`_restore_recovery_action()` 保持纯读；
   `resolve_restore_execution()` 在 `resolve_journal_pending()` 前取得 capability。
2. 将所有 side effect 替换为“取得本步 capability -> 执行一项动作 -> 废弃 capability -> 重新读取”的
   顺序。现有 generic resolver、Trash lifecycle 和 Restore resolver 不得各自手工拼不同材料 gate。
3. 对 committed cleanup，capability 由当前已提交 journal 与 evidence 授权；禁止因 package 缺失时的
   早退绕过 attempt/evidence 验证，也禁止重调 handoff。
4. 对 `POST_UNCLAIM_CLEANUP`，强制 builder 接收 Issue 03 `(R,T)` closure，禁止将它缩为 `(R,)`。

阶段门：静态调用审计与集成计数证明没有任何恢复/生命周期写、发布、清理、handoff、unlink、解关联或
release 能绕开该 capability；非法材料路径没有“先改后失败”的可观察变化。

### Task 4：刷新竞态、重复恢复与入口闭合

在每个 durable transition、pending publish、解关联、cleanup 和 handoff 后注入当前事实改变：R/T
relation 或第三方 claim、current/pending、材料摘要/身份、lock identity、package/evidence/attempt 成员。
从每个公开入口证明下一项副作用前重新 reader 和重新 preflight，变化后失败关闭；同 phase 的合法对照
成功收敛。覆盖扫描后 lock 竞争和 retained lock 释放后的新 writer，断言获胜方 journal、copies、目录、
package、R/T relation 与 lock 完全不变。

阶段门：三入口一次与重复运行后达到同一可观察终态；只允许仍经当前 capability 授权的幂等清理，且
handoff/purge 不重复。

### Task 5：验证、独立复审和提交边界

先按受影响的独立测试树运行，再运行完整 runtime 门禁：

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit/test_operations.py" "$RUNTIME/tests/unit/test_trash.py" -q
uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/integration/test_operations.py" -q -m 'not network'
uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/integration/test_trash.py" "$RUNTIME/tests/integration/test_trash_lifecycle.py" "$RUNTIME/tests/integration/test_lifecycle.py" "$RUNTIME/tests/integration/test_checker.py" "$RUNTIME/tests/integration/test_distribution_contract.py" -q -m 'not network'
uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit" -q
uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/integration" -q -m 'not network'
uv run --project "$RUNTIME" --locked ruff check "$RUNTIME/src/draft" "$RUNTIME/tests"
uv run --project "$RUNTIME" --locked pyright "$RUNTIME/src/draft"
git diff --check HEAD
```

根据 `plugins/draft/AGENTS.md`，network integration 另行运行和报告，不能与上述树混合。Issue 05
负责在实际 FS 上补齐 descriptor close/fstat/context-close 的矩阵；其完成前，本任务不能将资源
生命周期验收宣称为闭合。

实现完成后必须固定新基线并由独立评审核验：规格契约、所有 writer/cleanup/handoff/release gate、
三个公开入口、完整 failure preservation 和 Issue 05 的资源边界。当前 18 个 runtime 文件的混合未提交
改动不适合从中抽取 Issue 01 commit；不得 `git add -p`、按行号挑选或把整文件误称为本任务产物。仅在
来源、依赖、构建、测试与回退边界已由整组确认后，创建如实描述协调恢复协议的提交。

## 4. 阻塞与验收

### 仅在下列证据缺失时阻塞

- 若现有 journal schema 或 package/evidence/attempt 协议不能为某 action 给出纯读取的精确授权集合、
  合法派生命名、摘要或允许缺失状态，停止该 action 的实现并记录具体路径/phase；不得通过猜测或
  “effective journal 授权全部材料”补全语义。
- 若某 side-effecting 入口无法取得 Issue 03 的当前 owner/closure/action facts，停止该入口改造；
  不得回退到局部 R/T 判断或单 operation shortcut。
- 若 Issue 05 的 descriptor 资源接口无法表达 preflight 所需的所有权/close 失败语义，停止涉及该接口
  的实现并交由 Issue 05 明确契约；不得忽略 close 失败或重复关闭。

上述阻塞之外，helper/数据类/文件拆分是实现选择，不是新增语义。

### 最终验收

1. Issue 03 facts 是所有协调恢复的唯一 owner、participant closure 与 action 来源；未持锁 discovery
   无任何现场副作用。
2. 所有 action 的 closure，包含 `POST_UNCLAIM_CLEANUP` 的 T，均以 current/pending 的独立投影完成
   no-follow、精确成员、摘要、package/attempt/evidence/staging 验证；无授权 union。
3. 每个 writer、pending publisher、cleanup、handoff、unlink、解关联和 release 都有首次与刷新后的
   preflight，非法材料/关系/竞争路径保持现场并返回正确稳定错误。
4. standalone、`PRECLAIM`、`MATCHED`、`POST_UNCLAIM_CLEANUP`、empty/illegal directory、
   current/pending、损坏材料、竞争和重复恢复均满足上述决策表，并由公开入口集成测试验证。
5. Issue 05 的 FD close matrix 由其负责人独立验收；本任务的运行记录清楚列出未运行测试与环境限制，
   不以局部绿色替代整组验证或独立复审。
