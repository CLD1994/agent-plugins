# Draft 协调恢复所有权 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Draft recovery 仅以 `operation.lock` 串行化受协调 CLI writer，并使未持锁扫描不再执行任何命名副作用。

**Architecture:** 将现有无锁恢复拆为纯 discovery、纯分类和锁内 resolver 三层。discovery 只建立当前 operation、journal、`.pending` 与 R/T 关系事实；需要变更时先取得对应 operation 的 lock，丢弃旧快照并以既有锁内恢复器重新读取和收敛。`trash_lifecycle` 只提供其专属的锁内 Trash/restore 分派，不复制分类表。

**Tech Stack:** Python 3.12、标准库 `os`/`stat`/`errno`、pytest、uv、Ruff、Pyright。

**Spec:** `.scratch/draft-durable-link-index-maintenance/spec.md` §1、§12；`docs/design.md` §12、§13.3、§13.5；`docs/superpowers/plans/2026-08-25-draft-recover-lifecycle-boundary-fix.md`；`docs/review-notes/2026-08-25-durable-link-index-unlocked-recovery-round-7.md`。

## Global Constraints

- 保障范围仅为同一 Draft root 上的受协调 CLI writer；绕过 lock 的外部 namespace 改名不属于原子防护承诺。
- 未持锁 discovery 不得发布 `.pending`、写或删除 journal、清理恢复副本、硬链接、删除目录、调用 handoff 或改变受管内容。
- 任何改变恢复现场的候选必须先 `acquire_lock(root, operation_id)`；锁竞争返回 `operation.recovery_required`，不得继续使用竞争前快照。
- 成功取得 lock 后必须重新读取 journal、目录成员和 R/T 关系；读取、认证或写入失败时保留 lock、journal、材料与诊断现场。
- 保持 journal v4、合法 phase transition、错误码、退出码、链接索引 revision 和既有持锁 recovery 的业务语义。
- `.draft/` 与恢复目录的符号链接、非法节点、未知成员、损坏 journal 和非法关系均失败关闭；不得递归删除、glob 或用 `Journal.directory` 作为 recovery I/O 根。
- runtime 的 unit 与 integration、核心与适配层测试必须分开运行；真实文件系统、锁和子进程测试属于 integration。

## 修改边界

- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/operations.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/trash_lifecycle.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_operations.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash_lifecycle.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_checker.py`
- Modify when needed for shared integration helpers: `plugins/draft/skills/draft/scripts/runtime/tests/integration/support.py`

### Task 1: 锁定未持锁 discovery 的零副作用契约

**Files:**

- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_operations.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash_lifecycle.py`
- Modify when needed: `plugins/draft/skills/draft/scripts/runtime/tests/integration/support.py`

**Interfaces:**

- Consumes: `recover(root)`、`recover_interrupted_operations(root)`、`acquire_lock(root, operation_id)`、现有 `write_journal`/Trash journal helper。
- Produces: 两个公开入口共用的锁竞争与零副作用验收工具。

- [ ] **Step 1: 编写“扫描后 writer 抢锁”的失败集成测试。**

  在两个公开入口各建立一个合法 `allocating` operation 与授权的 `originals`/`staged`。
  在 discovery 完成、调用方取得候选 lock 前用 barrier 让第二个协调 writer 调用
  `acquire_lock(root, other_operation_id)`。断言第一入口返回
  `operation.recovery_required`，且 active lock、两个 journal、copy bytes 和 R/T 关系均不变：

  ```python
  with pytest.raises(DraftError, match="operation.recovery_required"):
      recover(root)
  assert read_lock(root) == other_operation_id
  assert snapshot_tree(root / ".draft") == before
  ```

- [ ] **Step 2: 运行新增测试，确认现有无锁 writer 会失败。**

  Run:

  ```bash
  RUNTIME=plugins/draft/skills/draft/scripts/runtime
  uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/integration/test_operations.py" "$RUNTIME/tests/integration/test_trash_lifecycle.py" -q -k 'unlocked and lock and competition'
  ```

  Expected: FAIL；当前 dispatcher 仍会按无锁快照清理或写 journal。

- [ ] **Step 3: 编写 discovery 对 `.pending` 的零副作用测试。**

  创建同时具有合法 `journal.json` 与可发布 `journal.json.pending` 的 operation。仅触发
  discovery，并用 `os.replace` spy 断言未调用；journal 与 pending bytes 必须不变。再让
  候选成功取得 lock，锁内恢复后断言 pending 按既有 transition 规则收敛。

- [ ] **Step 4: 编写 lifecycle 释放旧锁后的再竞争测试。**

  创建带崩溃残留 lock 的 `T=trash_retained`。在 lifecycle 释放该 lock 的紧后位置让第二
  writer 取得新 lock 并保留自己的 journal/package。断言 lifecycle 不依据旧 lock snapshot
  清理新 writer、不会调用 handoff，并以当前锁状态停止或交给当前锁内 resolver。

- [ ] **Step 5: 运行四个新增测试确认它们只在实现后通过。**

  Run: 使用 Step 2 的命令，另加 `-k 'pending or retained'`。

  Expected: FAIL，直到 Task 2 与 Task 3 完成。

### Task 2: 把 operations recovery 分解为 discovery、分类和锁内收敛

**Files:**

- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/operations.py:1212-1250,1450-1905,5497-5805`
- Test: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_operations.py`

**Interfaces:**

- Consumes: `acquire_lock(root, operation_id)`、`read_lock(root)`、`_recover_locked(root, operation_id)`、`resolve_journal_pending()`、`read_journal()`。
- Produces: 私有纯 `_discover_recovery_operation_fd()`、`_classify_recovery_candidates()` 与锁内候选 resolver；公开 `recover_unlocked_operations()` 仍由两个入口调用但不执行未持锁副作用。

- [ ] **Step 1: 实现纯 discovery reader。**

  从 `read_unlocked_operation_journal_fd()` 分离仅读取的 helper。它不得调用
  `_resolve_journal_pending_fd()`、`write_unlocked_journal_fd()`、`_fd_clean_recovery_copies()`
  或 `os.unlink`；`journal.json.pending` 只记录为“需锁内收敛”。它返回 operation ID、
  journal（若可读）、目录成员形状及 pending 状态，供同轮关系分类使用。

  ```python
  @dataclass(frozen=True)
  class _RecoveryDiscovery:
      operation_id: str
      journal: Journal | None
      pending_present: bool
      member_names: frozenset[str]
  ```

- [ ] **Step 2: 实现唯一的纯候选分类器。**

  输入全部 `_RecoveryDiscovery`，以 `R.metadata.restores_trash_operation` 与
  `T.metadata.restore_operation_id` 建立双向图。输出 `RETAIN`、`LOCK_AND_EMPTY_CLEANUP`、
  `LOCK_AND_GENERIC_RECOVER`、`LOCK_AND_RESTORE_RECOVER`、`LOCK_AND_COMMITTED_TRASH_CLEANUP`
  或稳定 `DraftError`。预认领 R、单向/错配/多重关系、`trash_handoff_pending` 失败记录
  缺失与无 journal 非空目录必须明确失败关闭。

- [ ] **Step 3: 在编排器中取得 lock 后丢弃 discovery 事实。**

  `recover_unlocked_operations()` 按 operation ID 排序处理分类结果。对每个 `LOCK_*` 动作
  先调用 `acquire_lock(root, operation_id)`；若抛 `operation.recovery_required`，立即向上
  传播。成功时只传 operation ID 与动作给锁内 resolver，resolver 必须重新调用
  `resolve_journal_pending()` 与 `read_journal()`，不得接受初扫 `Journal` 作为授权。

- [ ] **Step 4: 复用通用锁内恢复处理普通 allocating 与终态。**

  普通 `allocating` 和无关联、可由通用规则收敛的预认领 R 调用 `_recover_locked()`：它先
  耐久写 `rolled_back`，再清理当前 journal 授权副本并释放 lock。删除旧的无锁
  `_fd_clean_recovery_copies()`、`write_unlocked_journal_fd()` 与 `os.unlink("journal.json")`
  调用链。只为没有 journal 的空目录新增 `_recover_empty_allocation_residue_locked()`：锁内
  重新确认无成员后 `rmdir`，同步 `operations` 父目录，再释放 lock。

- [ ] **Step 5: 运行 Task 1 测试并新增空目录 checker 闭合测试。**

  对 `recover(root)` 建立“空目录”和“普通 allocating”两例：恢复后运行 `check_draft()`，
  再重复 recovery/check。断言目录已经锁内删除、checker 没有
  `operation.journal_corrupt`，且第二次无副作用。

  Run:

  ```bash
  RUNTIME=plugins/draft/skills/draft/scripts/runtime
  uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/integration/test_operations.py" -q
  ```

  Expected: PASS。

### Task 3: 将 Trash/restore 生命周期接入共享锁内所有权

**Files:**

- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/trash_lifecycle.py:543-762`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash_lifecycle.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_operations.py`

**Interfaces:**

- Consumes: Task 2 的分类动作与锁内 candidates；`_recover_locked_restore()`、`_recover_locked_trash()`、`recover_locked_operation()`。
- Produces: lifecycle 专属锁内 callback；它只从当前 lock 和锁内 journal 分派，不复制 discovery phase 表。

- [ ] **Step 1: 编写 R/T phase × 关系 × lock 决策表的失败测试。**

  参数化以下最小分区：无关联 `trash_retained`；带完整失败记录的
  `trash_handoff_pending`；无失败记录的 pending；预认领 R；有效双向 R/T；仅单向关联；
  `trash_handoff_succeeded`；`trash_purge_committed`；已完全解关联 terminal。分别在无锁和
  matching lock 下调用两个入口。断言 retained/waiting 无副作用、不可解释状态报
  `operation.inconsistent_state`、已提交清理不会再次调用 handoff、其余只在持锁后收敛。

- [ ] **Step 2: 实现 lifecycle 的锁内 callback。**

  对 `LOCK_AND_RESTORE_RECOVER`，以 R 为唯一 owner，锁内重读 R/T 后调用
  `_recover_locked_restore()`；对已提交 Trash cleanup 调用 `_recover_locked_trash()`；其他
  journal 调用 Task 2 的通用 resolver。`_recover_locked_trash()` 不得在 callback 内调用
  顶层 `recover(root)`；改为直接调用持锁通用接口，避免重入 discovery 与丢失所有权。

- [ ] **Step 3: 修正释放旧 lock 后的控制流。**

  `recover_interrupted_operations()` 处理入口已有 lock 后，重新读取 lock 状态再 discovery。
  lifecycle 释放 retained lock 后不得携带旧 journal、operation ID 或 package snapshot 继续
  扫描；Task 1 的竞争测试必须通过。

- [ ] **Step 4: 运行 lifecycle 集成测试。**

  Run:

  ```bash
  RUNTIME=plugins/draft/skills/draft/scripts/runtime
  uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/integration/test_trash_lifecycle.py" -q
  ```

  Expected: PASS。

### Task 4: 闭合 FD 所有权、刷新竞态和最终验收

**Files:**

- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/operations.py:900-930,1450-1670`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_operations.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash_lifecycle.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_checker.py`

**Interfaces:**

- Consumes: Task 2 discovery handles与 Task 3 lifecycle callback。
- Produces: 每个 recovery descriptor 恰好关闭一次；成功路径不吞掉 close 错误，已有主异常不被 close 错误遮蔽。

- [ ] **Step 1: 编写同 inode journal 语义变化与扫描后新增 T 的失败测试。**

  两个入口各覆盖 R=`restore/allocating`：初始为预认领或无关联的合法事实；在分类后、
  取得 lock 前原地改写 R journal 为带 mutation、不同 kind/phase 或新 R→T 键，或新增
  T→R journal。断言不清副本、不写 `rolled_back`、不删除 R/T；重试给出相同稳定错误。

- [ ] **Step 2: 修复 descriptor 所有权与 close 错误。**

  `_RecoveryLeaf.open()` 在 `os.open()` 成功而 `os.fstat()` 失败时关闭 descriptor 一次；
  `open_recovery_operation_directories()` 和 `_read_name_bytes()` 使用 close 聚合：已有主
  异常时继续关闭但不遮蔽它；无主异常时传播第一个映射为稳定 `DraftError` 的 close 错误。
  所有 owner 在尝试 close 前先将 fd 字段置空，避免 double-close。

- [ ] **Step 3: 编写 fstat 与 close 失败测试。**

  注入 leaf `fstat`、leaf close、operation directory close、context close 和
  `_read_name_bytes` close 失败。断言每个 descriptor 的 close 尝试至多一次；无主异常的
  成功路径收到稳定 `DraftError`，已有 journal/identity 主错误仍是最终异常，且没有 FD
  增长。

- [ ] **Step 4: 运行 targeted、全量和静态门禁。**

  Run:

  ```bash
  RUNTIME=plugins/draft/skills/draft/scripts/runtime
  uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit" -q
  uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/integration" -q -m 'not network'
  uv run --project "$RUNTIME" --locked pytest plugins/draft/scripts/tests/unit -q
  uv run --project "$RUNTIME" --locked pytest plugins/draft/scripts/tests/integration -q -m 'not network'
  uv run --project "$RUNTIME" --locked ruff check "$RUNTIME/src/draft/operations.py" "$RUNTIME/src/draft/trash_lifecycle.py"
  uv run --project "$RUNTIME" --locked ruff format --check "$RUNTIME/src" "$RUNTIME/tests"
  (cd "$RUNTIME" && uv run --locked pyright)
  bash plugins/draft/scripts/check-adapters.sh
  git diff --check
  ```

  Expected: 全部 PASS；记录四个 pytest 计数。network integration 与 Windows 验证标为未运行。

- [ ] **Step 5: 创建下一轮独立复审记录。**

  在 `docs/review-notes/2026-08-26-durable-link-index-coordinated-recovery-round-8.md`
  写入本轮 Standards 与 Spec 结论。逐项核验：未持锁路径没有命名副作用、锁竞争不消费
  active writer 的材料、R/T 决策表闭合、空目录与 checker 一致、close 失败可诊断；每个
  发现单独给出根因、修复、影响范围和回归验证。

## 自审

- 规格覆盖：Task 1 覆盖协调 writer 竞争与 pending；Task 2 覆盖 discovery/lock/普通
  allocating/空目录；Task 3 覆盖 R/T、terminal 与 lifecycle；Task 4 覆盖语义刷新、FD
  失败和全部验收。
- 横切闭合：每个副作用从 discovery 事实、lock 取得、锁内重读、journal transition、材料
  清理、lock release 到 `check` 的可观察结果均有测试。
- 占位符扫描：任务包含确切文件、符号、状态分区、预期结果与执行命令；没有待定语义。
- 类型一致性：Task 2 的 discovery/classifier 只产生动作与 operation ID；Task 3 和 Task 4
  的 resolver 都必须重新读取当前 journal，而不消费旧快照。
