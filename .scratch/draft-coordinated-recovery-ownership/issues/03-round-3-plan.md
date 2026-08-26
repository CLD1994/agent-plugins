# Issue 03 第 3 轮实施计划：全图关系事实与锁内恢复门

## 0. 基线、范围与权威来源

### 固定基线

- Git 基线：`4c25135 docs(draft): plan remaining worktree commits`（本计划开始时的
  `HEAD`）。
- 审查范围：该基线之上的未提交 Draft runtime 混合实现，及
  `docs/review-notes/draft-recovery/2026-08-26-round-8.md` 的三项缺陷。
- 对应 Issue 文件实际为
  `.scratch/draft-coordinated-recovery-ownership/issues/03-lock-owned-restore-trash-classification.md`；
  交接中给出的 `03-coordinate-restore-trash-recovery.md` 不存在。路径差异不改变本计划的
  行为契约。

### 已确认的行为契约

1. 完整 effective current/pending journal 图的错误分类严格按
   `raw/type -> R forward plan -> graph -> phase/action` 进行。每个阶段必须先覆盖完整图，
   结果不能由 operation ID、目录枚举、调用入口或 transition 检查时机决定。
2. 只有 `PRECLAIM`、`MATCHED`、`POST_UNCLAIM_CLEANUP` 三种关系授予唯一 Restore owner、
   有序 participant closure `(R, T)` 和唯一 action。T-only、R-only、dangling、错配、多重、
   未批准 phase 和普通局部快照都不授予 capability。
3. 未持锁 discovery 只能产生候选 ID。取得 matching lock 后，必须丢弃所有 discovery snapshot，
   重读当前目录、current/pending、完整图、lock 身份和材料；当前事实之外的 facts/action
   不得授权任何副作用。
4. 任何 participant 无法证明 current/pending、关系、材料、package、attempt 或 evidence
   安全时，必须在 pending 发布、journal/terminal 写入、replace/unlink、cleanup、解关联、
   handoff、purge 或 release 前失败关闭，保留整个 participant closure 的现场。
5. 每次 durable transition，以及 pending publish、解关联、cleanup、handoff 或下一项副作用前，
   都必须仍在 matching lock 内重新读取并重新 preflight。`operation.recovery_required` 仅表示
   lock 竞争或合法 owner 已被其他 owner 持有，不能表示损坏或非法关系。
6. `OPERATION_PLAN_INVALID` 是稳定机器码，唯一字面量定义在
   `draft/diagnostic_codes.py`；生产代码和测试消费者必须导入常量，literal-owner gate 必须登记它。

### 项目规则

- 单元测试只测试纯分类/状态契约，使用替身，不访问真实文件系统；真实目录、journal、pending、
  lock、descriptor、handoff 和故障注入属于集成测试。
- 测试按行为断言稳定机器码、现场和副作用；除 pending publish 次数这类协议副作用外，不复制私有
  调用图。所有稳定错误码精确断言；面向人的文案不逐字重复断言。
- 失败不得用删除测试、扩大允许成员、吞掉异常、把损坏降级为 `operation.recovery_required` 或先
  写入再回滚来掩盖。
- 当前 runtime 变更是来源混合且不满足可独立回退条件。本轮只提交本计划；后续代码在隔离
  worktree 中完成、独立复审后再决定可验证提交边界。

### 实现预估（非固定接口）

预计主要改动 `operations.py`、`trash.py`、`trash_lifecycle.py`、`checker.py`、
`diagnostic_codes.py`，以及 `test_operations.py`、`test_checker.py`、`test_trash.py`、
`test_trash_lifecycle.py`、`test_lifecycle.py`、`test_distribution_contract.py`。helper 名称、数据类
名称和模块归属可随实际数据流调整；下文的输入、输出、不变量和调用边界不得改变。

当前规格、顾问裁定和第 8 轮审查已定义所需语义，本计划没有需要用户裁定的阻塞。

## 1. 全图 effective reader 契约

### 输入、输出与纯度

建立唯一的只读全图 reader（下文称为“reader”）。它的输入是当前 Draft 根所有 operation
目录的 no-follow discovery 结果，以及每个 `journal.json` / `journal.json.pending` 的原始解析结果、
目录和 journal 身份；锁内调用还输入 matching lock 的 operation ID 与身份。它不调用
`resolve_journal_pending()`、不写 journal、不会创建/删除文件、不会 cleanup、handoff 或 release。

成功输出为：

- 每个 participant 的 current、pending 和 effective journal；
- 完整图 relation；
- 对批准关系的 `owner_id`、按 `(R, T)` 排列且去重的 `participant_ids`、唯一 action；
- 供后续整组 preflight 锚定的路径/身份事实。

失败输出为现有稳定 `DraftError`，并且 reader 自身零副作用：

| 优先阶段 | 完整图判定 | 输出 |
| --- | --- | --- |
| raw/type | 无法解析、schema/operation ID/kind 身份错误、metadata key/value 非 string、非法节点或 symlink | `operation.journal_corrupt`，或既有路径边界码 `path.symlink_escape` |
| R forward plan | 任一 current 或 pending Restore 缺少 `restores_trash_operation`，或值不是规范 32 位小写 hex ID | `operation.plan_invalid` |
| graph | 反向 claim 词法错误、T-only、R-only、dangling、kind mismatch、错配、多重 claim 或未批准单向关系 | `operation.inconsistent_state` |
| phase/action | 图合法但 R/T phase 对该 relation 不允许，或 current/pending transition、identity/kind/关系字段绑定不合法 | 既有稳定 journal/关系错误（当前 transition 错误为 `operation.journal_corrupt`；phase 为 `operation.inconsistent_state`） |

`operation_id` 参数若用于取得某个 operation 的 facts，仍必须先完成前三个阶段的完整图判定，不能只
检查该 ID 的局部 R/T。无关系的普通 operation 可返回“无 Restore/Trash capability”的 facts，不能由
reader 替它推导协调 owner。

### current/pending 决策表

| current | pending | 前置分类和 effective 语义 | 结果 |
| --- | --- | --- | --- |
| 合法 | 无 | 对 current 做 raw/type、R plan、graph、phase/action；effective=current | 可继续全图分类 |
| 合法 | 合法 | 两者均先进入 raw/type 和 R plan；identity、kind、关系字段与 transition 必须一致合法；仅在完整图/phase/action 成功后 effective=pending | 可继续；发布前仍需整组 preflight |
| 无 | 合法 pending | 仅 spec 明确允许的 create-only bootstrap 才可作为 pending-only effective；先经过完整 raw/type、R plan、graph、phase/action | 可继续但绝不由 reader 发布 |
| 无 | 无且目录严格为空 | allocation residue，不加入 journal 图 | 仅可作为锁内再次确认的清理候选 |
| 无 | 无且目录非空 | 无法证明授权 | `operation.inconsistent_state` |
| 任一非法或二者绑定不合法 | 任意 | 不得选择 effective、不得取 current/pending 或 R/T 授权 union | 对应上表优先稳定错误 |

“关系字段绑定”要求 current 与 pending 不能通过一个未定义的 transition 改变 R forward、T reverse、
participant closure 或 action。材料授权投影相同是 Issue 01 整组 preflight 的硬性验证，reader 只提供
两份可验证快照，不能用 effective journal 跳过另一份。

### 关系/授权决策表

| 全图 relation | owner | participant closure | action / 结果 |
| --- | --- | --- | --- |
| `PRECLAIM` | R | `(R, T)` | `RECOVER_PRECLAIM`；R lock 下可按当前事实安全收敛 |
| `MATCHED` | R | `(R, T)` | `RECOVER_MATCHED`；R 是唯一协调恢复 owner |
| `POST_UNCLAIM_CLEANUP` | R | `(R, T)` | `CLEANUP_POST_UNCLAIM`；即使表面只清 R，T 仍须进入 closure |
| 无关联普通 operation | 该普通 operation | `(owner,)` | 仅通用恢复可处理，不能获得 R/T action |
| 无关联 `trash_retained` / 完整失败记录的 `trash_handoff_pending` | 无恢复推进 owner | 无 R/T capability | 保留，生命周期按既有时间规则处理 |
| T-only、R-only、dangling、错配、多重、非法 phase | 无 | 无 capability | 返回 `operation.inconsistent_state`（R forward 缺失/非法优先为 `operation.plan_invalid`） |

## 2. 目标数据流和状态转换

### 正常路径

```text
公开入口（recover / recover_interrupted_operations / advance_trash_lifecycle）
  -> 未持锁只读 discovery，得到候选 ID
  -> 获取 relation 指定的 matching owner lock
  -> 丢弃 discovery snapshot
  -> reader：完整 current/pending 图 -> relation/owner/closure/action
  -> Issue 01 的整组 RecoveryPreflight：所有 participant 的快照、材料和 lock 身份
  -> 仅执行 preflight 授权的一步 durable transition 或 cleanup
  -> 丢弃 preflight、facts、action 和 Journal snapshot
  -> 在同一 matching lock 内重读 reader + 重新 preflight
  -> 下一步，直至合法 terminal / 已提交清理幂等收敛
```

`check()`、未持锁 candidate classification、三个公开 seam 和 lifecycle 内部入口必须都调用同一 reader；
它们只能投影该 reader 的结果，不能先单独调用 `validate_journal_transition()`、局部 relation validator
或按 ID 局部扫描后覆盖结果。

### 失败、重试与恢复路径

1. 未持锁 raw/type、R plan、graph 或 phase/action 失败：不取 lock、不发布 pending、不写入；返回表中
   稳定错误，保留发现到的全部现场。
2. lock 竞争或合法 owner 已被持有：丢弃候选和快照，返回 `operation.recovery_required`，绝不使用旧
   snapshot 继续执行。
3. 锁内 reader 或整组 preflight 失败：保留 matching lock、R/T current/pending 的字节与节点身份、
   originals/staged/mutation/copies/attempt/evidence/package/link-index 材料、关系和目录；不做“失败清理”。
4. 任一 durable transition、pending publish、解关联、cleanup 或 handoff 后中断：下次调用重新走
   discovery 和 matching-lock reader，不复用异常前 facts/action。合法 terminal 或已提交清理重复运行
   不重复 handoff/purge、不扩大 cleanup、不重写无关关系。
5. local namespace race、lock 身份或将消费节点的 no-follow identity 改变：失败关闭；不将其解释为
   可安全继续的无关系状态。Issue 03 的并发保障仍仅覆盖遵守 `operation.lock` 的协调 writer。

## 3. 实施任务与阶段门

### Task 1：先写可证伪的红测试和现场快照

在生产改动前新增最窄单元测试和真实 FS 集成测试。集成快照至少覆盖 R/T current/pending 原始字节、
inode/节点类型、operation 目录成员、copies/attempt/evidence/package/link-index、lock，以及 journal
write、pending publish、cleanup、解关联、handoff/purge、release 的计数。

红测试：

1. 两个 ID 排列：小 ID R forward 缺失/非法，大 ID metadata 非 string；两种排列及所有入口均为
   `operation.journal_corrupt`。
2. 两个 ID 排列：小 ID T reverse lexical invalid，大 ID R forward 缺失；两种排列及所有入口均为
   `operation.plan_invalid`。
3. 对含合法 current+pending 的同一图，从 reader、unlocked candidate、`check()`、`recover()`、
   `recover_interrupted_operations()` 和 `advance_trash_lifecycle()` 断言同码、零写入和现场不变。
4. 对 `MATCHED`、`PRECLAIM`、`POST_UNCLAIM_CLEANUP` 各给合法对照和第三方 claim / R 或 T pending
   非法反例。非法图必须零 pending publish、terminal、cleanup、handoff、claim 改写或 release。
5. T `attempt` 未授权/符号链接、cleanup evidence final/pending/quarantine 非法、package 缺失/错误
   类型/摘要、每个 link-index phase 的 T 材料非法、R/T 均有合法 pending 但 T 材料非法，以及
   `POST_UNCLAIM_CLEANUP` 的 T package/evidence 非法。反例均证明 R 不推进、整组现场不变。
6. 每个合法 durable transition 后注入关系、第三方 claim、lock identity 或 participant 材料变化；下一
   副作用必须重新分类并失败保留。相同 phase 的合法对照必须继续收敛并在重复恢复时幂等。

阶段门：这些测试在当前实现失败，且失败原因分别是 ID 顺序、局部 validator 旁路或缺失整组门，不能
以松化断言改为绿。

### Task 2：实现唯一的阶段化 effective graph reader

在 `operations.py` 集中 raw current/pending 采集、R forward 扫描、全图 relation、phase/action 与
pending 选择。`validate_restore_trash_relations()`、`_effective_recovery_journals()`、
`read_restore_trash_relation()`、`classify_restore_trash_ownership()` 和 unlocked candidate 的输入必须
收敛到该纯 reader；不得保留一条先 transition、另一条先图分类的读取路径。

完成后审计 `checker.py`、`recover()`、`recover_interrupted_operations()`、Trash lifecycle 和所有
relation reader：它们必须消费同一个阶段结果，不得各自重新排序或基于候选 journal 覆盖全图结论。

阶段门：Task 1 的 ID 交换与跨入口同码测试通过；所有 reader 失败时 current/pending 和 lock 均未改变。

### Task 3：消除恢复中的局部关系授权

`validate_restore_link()` 可以保留给计划创建/预分配等非恢复路径，但恢复调用图不得再到达它，也不得
通过它或相似局部 helper 隐式 `resolve_journal_pending()`。恢复只接收 Task 2 的 relation facts 中的
owner、closure 和 action；`RestoreLinkState` 可以继续作为纯 phase 算法内部输入，但不能承载 owner、
participant、关系授权或 pending 发布决定。

审计并改造以下已知入口和责任边界：`_restore_recovery_action()`、`restore_retained_trash()` 的异常
收敛、`_recover_locked_restore()`、`resolve_restore_execution()`、`_recover_locked_trash()`、
`_advance_one_locked()`、`recover_locked_operation()` 及其公开调用方。R/T 任一 pending 的发布只能发生在
当前全图 facts 和整组 preflight 已成功之后；发布后立即使旧 snapshot/facts/action 失效。

阶段门：静态导入/调用审计证明恢复路径不可达 `validate_restore_link()`；三种批准 relation 的 owner、
closure 和 action 从同一 reader 导出，非法第三方关系的 pending publish 计数为零。

### Task 4：由 Issue 01 接入唯一整组副作用门

Issue 03 负责交付不可伪造的 relation/owner/closure/action facts；Issue 01 负责建立并拥有
`RecoveryPreflight`（或等价 capability）：matching lock 身份、全图有效快照、current/pending transition、
所有 participant 的材料授权投影、no-follow 身份和本次唯一允许步骤必须同源。

将该 capability 接入所有 side-effecting resolver，而不是让每个 resolver 手工拼 R/T 检查。第一项
副作用前、每次 durable transition 后以及 pending publish、解关联、cleanup、handoff、release 前都必须
取得新的 capability。任何 resolver 缺 capability 或 capability 已失效时停止，不能回退为单 operation
模式；`POST_UNCLAIM_CLEANUP` 不得因 action 表面只涉及 R 而排除 T。

阶段门：Task 1 的材料、pending、transition 后事实变化反例均为零副作用；三种合法关系、合法
current-only/current+pending/bootstrap、已提交清理和重复恢复均可收敛。

### Task 5：完成 `OPERATION_PLAN_INVALID` 的 literal-owner 门禁

以 `diagnostic_codes.py` 的 `OPERATION_PLAN_INVALID` 为唯一常量来源，在 distribution contract 的
registered code 集合登记该常量，迁移 gate 扫描到的所有生产和测试消费者。用户输出层若确有机器协议
精确断言，只能通过导入常量表达，不新增裸字符串。

补充门禁的反证测试：临时非 owner Python 文件包含 `"operation.plan_invalid"` 时，
`_literal_owner_offenders()`（或等价公开的 gate helper）必须报告该文件与行号；真实消费者集合必须
为空。审计其他不属于 `CheckCode` 的稳定码，确认 registered 集合不会再次按枚举边界漏项。

阶段门：distribution contract 同时证明真实集合为零 offender、临时反例失败；不通过缩小扫描范围或
从 registered 集合移除机器码来获得绿色。

## 4. 入口与异常分支验收

| 入口/责任边界 | 必须验证的行为 |
| --- | --- |
| `check()` | 读同一 reader；ID 交换和 current/pending 双错误得到相同稳定码；绝不写入 |
| unlocked discovery / `recover()` | 仅产生候选；非法图零 lock/零写；竞争返回 `operation.recovery_required` |
| `recover_interrupted_operations()` | R owner matching lock 内重新读取全图与 closure；R/T pending、第三方关系和材料异常均保留整组现场 |
| `advance_trash_lifecycle()` / `_advance_one_locked()` | lifecycle 不从 T reverse/旧快照推 owner；合法 R owner 不被 T 路径越权推进；handoff 不重复 |
| `_recover_locked_restore()` / `resolve_restore_execution()` | action 来自当前 capability；每个 transition 后刷新；terminal、解关联、package/evidence/copies cleanup 和 release 前均有新 preflight |
| `_recover_locked_trash()` / `recover_locked_operation()` | 普通 operation 的 `(owner,)` closure 与协调 `(R,T)` closure 都走同一副作用门；不能用 generic resolver 绕开 capability |
| pending/错误分支 | raw、plan、graph、phase、材料、lock identity 或 close 故障不遮蔽主错误；不发布、不清理、不释放；重试不扩大现场 |

## 5. 责任边界

- Issue 01：定义并验证 participant 材料闭包、current/pending 材料授权投影、`RecoveryPreflight`、
  所有副作用前的整组门与零副作用现场断言。不得把 package、attempt、evidence、link-index 材料
  宣布为 Issue 03 范围外后跳过。
- Issue 03：提供唯一且纯的全图 relation/owner/closure/action 事实；禁止局部 R/T validator、反向
  claim 或旧 `RestoreLinkState` 充当第二授权来源；协调 Issue 01 capability 的输入与刷新时机。
- Issue 05：拥有 descriptor 打开、`fstat`、close、主错误不被 close 错误遮蔽和 exactly-once close
  的契约与测试。Issue 01/03 的 preflight 只能遵从该资源所有权；不得用 Issue 05 测试全绿替代
  participant material 或关系授权验收。

## 6. 最终验证和提交策略

按模块先跑新增单元测试，再分别运行受影响 runtime 集成测试（不混入适配层）：

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit/test_operations.py" -q
uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/integration/test_operations.py" -q -m 'not network'
uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/integration/test_checker.py" "$RUNTIME/tests/integration/test_trash.py" "$RUNTIME/tests/integration/test_trash_lifecycle.py" "$RUNTIME/tests/integration/test_lifecycle.py" "$RUNTIME/tests/integration/test_distribution_contract.py" -q -m 'not network'
uv run --project "$RUNTIME" --locked ruff check "$RUNTIME/src/draft" "$RUNTIME/tests"
uv run --project "$RUNTIME" --locked pyright "$RUNTIME/src/draft"
git diff --check
```

若当前仓库约定要求，还要分别补跑 runtime unit、non-network integration 与 network integration；任何
未运行项目和环境原因必须记录，不能以局部绿色声称闭合。

当前已有 runtime 文件含 3,000+ 行未提交的 link-index/recovery 混合改动，不能用 `git add -p`、
行号补丁或整文件暂存伪造来源。代码修复应在隔离 worktree 中完成，并在固定新基线后进行独立复审；
只有共享 hunk 已具备来源、依赖、构建、测试和回退边界时才拆提交，否则用一个如实描述协调恢复和
link-index 协议的整组提交。本轮文档提交仅含本计划文件；不更新 Issue checklist/status，也不暂存
`docs/tmp` 交接索引或任何 runtime 文件。
