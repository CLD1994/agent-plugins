# Draft restore 恢复与 checker 协议闭合实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**目标：** 修复第十一轮评审确认的 restore 中段相位不可恢复、R/T 决策重复、cleanup evidence 检查语义漂移和 checker 原始文件系统异常泄漏，使公开恢复与完整检查共享同一套耐久状态判定。

**架构：** 用一个完整的 `R phase × T phase` 纯决策表作为 restore 动作的唯一权威；显式 recovery 入口复用通用 mutation 恢复，但保留 R 的终态锁和恢复材料，待双日志解决器提交或解关联 T 后再释放。checker 只复用 cleanup evidence 的只读联合分类和纯决策，不逐名另建合法性规则，也不执行任何收敛动作。

**技术栈：** Python 3.11+、`dataclass`/`StrEnum`、`pathlib`、pytest、Ruff、Pyright strict、uv、POSIX 本地文件系统原语。

**Spec:** `docs/superpowers/specs/2026-08-12-draft-candidate-root-incremental-migration-design.md`

**Review:** `docs/review-notes/2026-08-18-draft-candidate-root-incremental-migration-round-11.md`

## 全局约束

- 已批准规格是公开行为的权威来源；第十一轮评审记录提供缺陷证据和修复边界，本计划提供执行顺序，二者不能改变规格的安全边界。
- 支持本地文件系统、一个受根锁协调的 CLI 写者、普通 I/O 失败，以及进程在任意已定义耐久步骤后中断；本轮不扩展到非协作写者的系统调用间抢占、NFS、机器掉电目录链耐久或恶意 `.draft/` 篡改。
- restore 的 R/T 双向关联和根锁是一个协调事务。R 到达终态不等于可以立刻释放锁；只有 T 已提交或解关联、权威包/恢复副本已按授权处理后才能释放。
- 无法证明关联、方向、目标摘要、观察事实或 artifact 所有权时，停止并保留 R/T、包、锁和恢复材料；不得解释为成功、缺失或可放弃。
- checker 始终只读。它可以报告损坏，不能发布 pending、恢复 quarantine、删除 evidence、推进 phase 或释放锁。
- 不拆分或整体重构 `operations.py`；只增加闭合本轮契约所需的最小参数/内部接口。不得顺手处理此前已经明确延期的模块单一职责问题。
- 不引入新依赖，不改变来源零读取/零扫描/零写入，不改变 candidate/registered 的普通操作门禁。
- 已确认的行为契约、状态表和失败语义是硬要求；下文函数名、参数名和预计文件属于实现建议。若真实控制流要求改名，执行 Agent 必须保持同一类型与语义，并在修复记录中逐项给出映射。
- 严格 TDD：每项先写能够证伪现有实现的失败测试并记录失败原因，再写最小实现；测试预期必须从本计划状态表独立写出，不能调用生产决策函数生成自己的 expected。
- 单元测试不访问真实文件系统；真实锁、journal、artifact、进程中断、权限错误和两个公开恢复入口属于 integration。
- 开始 Task 1 前记录 `git status --short`。当前用户的 `AGENTS.md`、`docs/review-notes/` 和未提交计划属于基线，任何任务不得暂存、覆盖或要求它们消失。
- 每个任务只提交自己列出的生产文件与测试。不得提交根 `AGENTS.md`、历史评审记录或无关格式化变化。
- 除明确验证 cwd 无关性的入口外，所有命令从仓库根执行；最终门禁用 `git rev-parse --show-toplevel` 派生绝对路径，避免再次出现配置随 cwd 改义。

---

## 行为契约一：restore 双日志完整决策表

以下表是 Task 1–2 的权威预期。`links_match=False` 时，不论其他输入为何都返回
`INCONSISTENT`。`proven_unapplied` 只影响 `ALLOCATING/PREPARED`；其他 R phase 不得读取它来
改变动作。

| R phase | T phase | 附加条件 | `RestoreRecoveryAction` | 是否允许读取完整包 |
|---|---|---|---|---|
| `COMPLETED` | `TRASH_RETAINED` | links match | `VERIFY_AND_COMMIT_CLEANUP` | 必须 |
| `COMPLETED` | `ROLLED_BACK` | links match | `RESUME_COMMITTED_CLEANUP` | 不要求 |
| `APPLYING` | `TRASH_RETAINED` | links match | `RESUME_FORWARD` | 不提前读取 |
| `VALIDATING` | `TRASH_RETAINED` | links match | `RESUME_FORWARD` | 不提前读取 |
| `ROLLING_BACK` | `TRASH_RETAINED` | links match | `RESUME_ROLLBACK` | 不提前读取 |
| `ROLLED_BACK` | `TRASH_RETAINED` | links match | `ABANDON_UNAPPLIED` | 不要求 |
| `ALLOCATING` | `TRASH_RETAINED` | links match 且 proven unapplied | `ABANDON_UNAPPLIED` | 不要求 |
| `PREPARED` | `TRASH_RETAINED` | links match 且 proven unapplied | `ABANDON_UNAPPLIED` | 不要求 |
| 任意未列组合 | 任意 | — | `INCONSISTENT` | 不读取 |

动作语义：

| 动作 | 唯一允许的副作用顺序 |
|---|---|
| `RESUME_FORWARD` | 保留 R 锁 → 通用正向 artifact/观察恢复 → R 耐久终态且仍持锁 → 重读 R/T 并重新决策 |
| `RESUME_ROLLBACK` | 保留 R 锁 → 通用回滚 artifact/目标恢复 → R=`ROLLED_BACK` 且仍持锁 → 重读 R/T 并重新决策 |
| `VERIFY_AND_COMMIT_CLEANUP` | 加载完整包 → 独立证明 restore after → T=`ROLLED_BACK` 耐久 → 安全清理包 → 清理 R 材料 → 释放锁 |
| `RESUME_COMMITTED_CLEANUP` | 不重新要求完整包 → 从已提交 T 派生 cleanup 授权 → 继续清理 → 清理 R 材料 → 释放锁 |
| `ABANDON_UNAPPLIED` | 必要时耐久 R=`ROLLED_BACK` → 删除 T 的 claim 并耐久 → 清理 R 材料 → 释放锁；权威包保持可恢复 |
| `INCONSISTENT` | 不写入、不清理、不解锁；返回 `operation.inconsistent_state` 或更具体的既有领域错误 |

正常 `restore_page` 调用中捕获到明确 `execution_error` 时，不得仅因当前 phase 对显式 recovery
可恢复就自动吞掉错误。`RESUME_FORWARD/RESUME_ROLLBACK` 只由用户调用的公开 recovery 路径
执行；正常调用按现有错误语义保留现场或完成安全放弃。

## 行为契约二：cleanup evidence 的 checker 语义

checker 必须先用 `classify_cleanup_evidence_artifacts(journal)` 一次性取得 final、pending、
quarantine 及 inode 关系，再调用 `cleanup_evidence_actions`。任何包含 `CORRUPT` 的结果都报告
`trash.package_corrupt`。以下不是三个独立文件检查，而是一个联合状态：

| final | pending | quarantine | inode 关系 | checker 结论 |
|---|---|---|---|---|
| missing | missing | missing | — | evidence 本身不损坏 |
| valid | missing | missing | — | 不损坏 |
| missing | valid | missing | — | 不损坏（恢复器可发布） |
| missing | missing | valid | — | 不损坏（恢复器可恢复/消费） |
| valid | valid | missing | 同 inode | 不损坏 |
| valid | valid | missing | 不同 inode | 损坏 |
| valid | missing | valid | 同 inode | 不损坏 |
| valid | missing | valid | 不同 inode | 损坏 |
| missing | valid | valid | 任意 | 损坏 |
| valid | valid | valid | 任意 | 损坏 |
| 任一 invalid/non-file | 任意 | 任意 | 任意 | 损坏 |

运行时 KEEP/REMOVE 意图可以产生不同动作，但对“该联合状态是否 `CORRUPT`”必须具有相同
结论。用一个纯函数分别调用 KEEP 和 REMOVE 决策，并以“任一意图判 CORRUPT 就视为协议
损坏”形成 checker 的保守结论；unit 穷举全部输入，锁定两个意图的损坏集合相等。未来若
两个集合确需分叉，必须先修改规格并显式增加 phase/package→intent 决策，不能让 checker
选择更宽松的一支。

---

## 预计影响范围

- 修改：`plugins/draft/skills/draft/scripts/runtime/src/draft/trash.py`
- 修改：`plugins/draft/skills/draft/scripts/runtime/src/draft/trash_lifecycle.py`
- 修改：`plugins/draft/skills/draft/scripts/runtime/src/draft/operations.py`
- 修改：`plugins/draft/skills/draft/scripts/runtime/src/draft/checker.py`
- 修改：`plugins/draft/skills/draft/scripts/runtime/src/draft/trash_package.py`（导出只读联合合法性辅助函数）
- 修改：`plugins/draft/skills/draft/scripts/runtime/tests/unit/test_trash.py`
- 修改：`plugins/draft/skills/draft/scripts/runtime/tests/unit/test_trash_package.py`
- 修改：`plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash.py`
- 修改：`plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash_lifecycle.py`
- 修改：`plugins/draft/skills/draft/scripts/runtime/tests/integration/test_checker.py`
- 新增：`docs/review-notes/2026-08-18-draft-candidate-root-incremental-migration-round-11-fix-record.md`

建议接口如下；名称可调整，语义不可改变：

```python
class RestoreRecoveryAction(StrEnum):
    VERIFY_AND_COMMIT_CLEANUP = "verify_and_commit_cleanup"
    RESUME_COMMITTED_CLEANUP = "resume_committed_cleanup"
    RESUME_FORWARD = "resume_forward"
    RESUME_ROLLBACK = "resume_rollback"
    ABANDON_UNAPPLIED = "abandon_unapplied"
    INCONSISTENT = "inconsistent"


def recover_locked_operation(
    root: Path,
    operation_id: str,
    *,
    retain_terminal_lock: bool = False,
) -> OperationResult:
    """恢复锁内通用 operation；retain=True 时终态日志耐久但不清材料、不解锁。"""


def resolve_restore_execution(
    root: Path,
    *,
    trash_id: str,
    restore_id: str,
    action: RestoreRecoveryAction,
    package: VerifiedTrashPackage | None,
    execution_error: DraftError | None,
) -> OperationResult:
    """只执行已决策动作，不自行建立第二套 phase 表。"""
```

如果实现选择让 `resolve_restore_execution` 在内部唯一调用 `restore_recovery_decision`，则不得
再由调用方传 `action`；两种方式二选一，禁止两层都判 phase。

---

### Task 1：闭合 restore 纯决策表并删除错误测试预期

**Files:**

- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/trash.py:367-401`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_trash.py:993-1095`

**Interfaces:**

- Consumes: `OperationPhase`、现有 `RestoreRecoveryAction`。
- Produces: 完整 `restore_recovery_decision`，新增 `RESUME_FORWARD` 和 `RESUME_ROLLBACK`；Task 2 只消费其动作，不重新解释 phase。

- [ ] **Step 1：记录工作区基线**

运行：

```bash
git status --short
```

预期：看到用户已有的 `AGENTS.md`、临时 review notes 和计划；将完整输出记入任务汇报，不暂存它们。

- [ ] **Step 2：先写独立的完整决策表测试**

删除/改写当前把 `APPLYING/VALIDATING/ROLLING_BACK` 固定断言为 inconsistent 的测试。新增显式
期望映射；期望不得读取生产 `_PHASES_BY_KIND` 或调用另一个生产 helper：

```python
@pytest.mark.parametrize(
    ("restore_phase", "trash_phase", "proven", "expected"),
    [
        (OperationPhase.COMPLETED, OperationPhase.TRASH_RETAINED, False,
         RestoreRecoveryAction.VERIFY_AND_COMMIT_CLEANUP),
        (OperationPhase.COMPLETED, OperationPhase.ROLLED_BACK, False,
         RestoreRecoveryAction.RESUME_COMMITTED_CLEANUP),
        (OperationPhase.APPLYING, OperationPhase.TRASH_RETAINED, False,
         RestoreRecoveryAction.RESUME_FORWARD),
        (OperationPhase.VALIDATING, OperationPhase.TRASH_RETAINED, False,
         RestoreRecoveryAction.RESUME_FORWARD),
        (OperationPhase.ROLLING_BACK, OperationPhase.TRASH_RETAINED, False,
         RestoreRecoveryAction.RESUME_ROLLBACK),
        (OperationPhase.ROLLED_BACK, OperationPhase.TRASH_RETAINED, False,
         RestoreRecoveryAction.ABANDON_UNAPPLIED),
        (OperationPhase.ALLOCATING, OperationPhase.TRASH_RETAINED, True,
         RestoreRecoveryAction.ABANDON_UNAPPLIED),
        (OperationPhase.PREPARED, OperationPhase.TRASH_RETAINED, True,
         RestoreRecoveryAction.ABANDON_UNAPPLIED),
    ],
)
def test_restore_recovery_decision_valid_partitions(
    restore_phase: OperationPhase,
    trash_phase: OperationPhase,
    proven: bool,
    expected: RestoreRecoveryAction,
) -> None:
    assert restore_recovery_decision(
        restore_phase=restore_phase,
        trash_phase=trash_phase,
        links_match=True,
        proven_unapplied=proven,
    ) is expected
```

再显式列出 restore 可能 phase 与 trash 可能 phase，用循环断言所有未列 `(R,T)` 均为
`INCONSISTENT`。另写两个独立测试：`links_match=False` 覆盖全部组合；`proven_unapplied` 对
非 allocating/prepared 不改变动作。

- [ ] **Step 3：运行测试，确认红灯理由正确**

运行：

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_trash.py" -q -k 'restore_recovery_decision'
```

预期：`RESUME_FORWARD/RESUME_ROLLBACK` 尚不存在，或中段相位仍返回 `INCONSISTENT`；失败不能来自夹具或导入错误。

- [ ] **Step 4：最小实现纯决策**

给枚举加入两个动作，按“行为契约一”直接表达合法分区。推荐先拒绝 link，再使用显式
`(restore_phase, trash_phase)` 分支；禁止使用“只看 R phase”的默认放弃：

```python
if not links_match:
    return RestoreRecoveryAction.INCONSISTENT
pair = (restore_phase, trash_phase)
if pair in {
    (OperationPhase.APPLYING, OperationPhase.TRASH_RETAINED),
    (OperationPhase.VALIDATING, OperationPhase.TRASH_RETAINED),
}:
    return RestoreRecoveryAction.RESUME_FORWARD
if pair == (OperationPhase.ROLLING_BACK, OperationPhase.TRASH_RETAINED):
    return RestoreRecoveryAction.RESUME_ROLLBACK
# 其余合法分区逐项写出；未列组合统一 INCONSISTENT。
```

- [ ] **Step 5：运行定向 unit 与核心 unit**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_trash.py" -q -k 'restore_recovery_decision'
uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit" -q
```

预期：定向测试和核心 unit 全部通过。

- [ ] **Step 6：提交 Task 1**

只暂存上述两个文件，建议提交信息：

```text
fix(draft): 闭合 restore 双日志恢复决策表
```

---

### Task 2：让公开 restore recovery 复用通用中段恢复并保持协调锁

**Files:**

- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/operations.py:2170-2288,2736-2765,3760-3824`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/trash.py:830-977`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/trash_lifecycle.py:674-708,711-738,829-859`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash_lifecycle.py`

**Interfaces:**

- Consumes: Task 1 的完整 `RestoreRecoveryAction`。
- Produces: 可选择保留终态锁的通用锁内恢复入口；唯一消费 restore action 的解决器；两个公开 recovery 入口在合法中段相位收敛。

- [ ] **Step 1：写真实 restore 中段崩溃测试辅助器**

在 integration 测试中用现有真实 `trash_page`/`restore_page` 建立 T/R，不手写空 mutation
journal。包装 `operations.write_journal`：先调用真实 writer，确认 R 的 kind/phase，再抛一个
不被生产 `except DraftError` 捕获的测试专用 `BaseException`，模拟相位已经耐久后的进程退出：

```python
class SimulatedProcessExit(BaseException):
    pass


def crash_after_restore_phase(monkeypatch, phase: OperationPhase) -> None:
    real_write = operations.write_journal

    def write_then_crash(journal: Journal) -> None:
        real_write(journal)
        if journal.kind is OperationKind.RESTORE and journal.phase is phase:
            raise SimulatedProcessExit

    monkeypatch.setattr(operations, "write_journal", write_then_crash)
```

如果相同 phase 会被多次写入，增加局部布尔闩锁保证只在第一次命中后退出；不要按调用次数猜
目标接缝。

- [ ] **Step 2：写 APPLYING/VALIDATING 的两个公开入口失败测试**

参数化 phase 和恢复入口：

```python
@pytest.mark.parametrize("phase", [OperationPhase.APPLYING, OperationPhase.VALIDATING])
@pytest.mark.parametrize("entry_name", ["recover_draft", "recover_interrupted_operations"])
def test_restore_forward_mid_phase_crash_recovers_through_public_entry(
    draft_factory: DraftFactory,
    monkeypatch: pytest.MonkeyPatch,
    phase: OperationPhase,
    entry_name: str,
) -> None:
    root = build_trashable_draft(draft_factory)
    pending = trash_page(root, Path("pages/obsolete.md"), now=NOW)
    crash_after_restore_phase(monkeypatch, phase)
    with pytest.raises(SimulatedProcessExit):
        restore_retained_trash(root, pending.operation_id, now=NOW)
    monkeypatch.undo()

    if entry_name == "recover_draft":
        recover_draft(root)
    else:
        recover_interrupted_operations(root)

    restore_id = _read_journal(root, pending.operation_id)["metadata"][
        "restore_operation_id"
    ]
    assert _read_journal(root, restore_id)["phase"] == "completed"
    assert _read_journal(root, pending.operation_id)["phase"] == "rolled_back"
    assert (root / "pages/obsolete.md").read_text(encoding="utf-8") == OBSOLETE
    assert not (root / ".draft/operation.lock").exists()
```

对 VALIDATING 的故障点必须发生在 `VALIDATING` journal 已耐久、R 尚未 `COMPLETED`；测试先
重读磁盘 journal 证明现场，再调用恢复入口。

- [ ] **Step 3：写 ROLLING_BACK 的两个公开入口失败测试**

通过真实 restore 的可控验证失败进入 `_rollback`，在 R=`ROLLING_BACK` 已耐久、首个回滚
mutation 前退出。恢复后断言：

```python
assert restore_journal.phase is OperationPhase.ROLLED_BACK
assert trash_journal.phase is OperationPhase.TRASH_RETAINED
assert "restore_operation_id" not in trash_journal.metadata
assert authoritative_package.is_dir()
assert read_lock(root) is None
```

页面和父导航必须回到 restore 开始前（即 trash 后）的摘要；权威包仍完整并可再次 restore。

- [ ] **Step 4：运行三相位测试，确认当前实现红灯**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/integration/test_trash.py" \
  "$RUNTIME/tests/integration/test_trash_lifecycle.py" -q \
  -k 'restore and (applying or validating or rolling_back) and public'
```

预期：公开入口报告 `operation.inconsistent_state` 或锁仍存在；不能接受测试没有真正写出目标 phase。

- [ ] **Step 5：给通用锁内恢复增加“保留终态锁”能力**

在现有 `_recover_locked` 控制流增加 `retain_terminal_lock: bool = False`，并传递给：

- `ROLLING_BACK` 分支的 `_rollback`；
- forward 分支的 `_finish_forward`；
- 必要的 `_recover_terminal`/allocating 放弃路径（如果调用可达）。

公开普通 `recover()` 保持默认 `False`，行为不变。提供一个供生命周期层调用的窄接口；该
接口必须验证 lock 仍等于 operation_id，且不得清理 recovery copies 或释放锁：

```python
def recover_locked_operation(
    root: Path,
    operation_id: str,
    *,
    retain_terminal_lock: bool = False,
) -> OperationResult:
    if read_lock(root) != operation_id:
        raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, root / LOCK_RELATIVE)
    return _recover_locked(
        root,
        operation_id,
        retain_terminal_lock=retain_terminal_lock,
    )
```

不要在生命周期层调用当前公开 `recover(root)` 后再“重新拿锁”；释放和重拿之间会丢失 R/T
协调所有权。

- [ ] **Step 6：让 `_recover_locked_restore` 按 action 执行中段恢复**

控制流必须是：

```python
trash_journal = validate_restore_link(root, restore_journal)
action = restore_recovery_decision(
    restore_phase=restore_journal.phase,
    trash_phase=trash_journal.phase,
    links_match=True,
    proven_unapplied=_proven_unapplied_restore(root, restore_journal),
)
if action in {RESUME_FORWARD, RESUME_ROLLBACK}:
    recover_locked_operation(
        root,
        restore_journal.operation_id,
        retain_terminal_lock=True,
    )
    restore_journal = read_journal(
        operation_journal_path(root, restore_journal.operation_id)
    )
    trash_journal = validate_restore_link(root, restore_journal)
    action = restore_recovery_decision(
        restore_phase=restore_journal.phase,
        trash_phase=trash_journal.phase,
        links_match=True,
        proven_unapplied=_proven_unapplied_restore(root, restore_journal),
    )
return resolve_restore_execution(
    root,
    trash_id=trash_journal.operation_id,
    restore_id=restore_journal.operation_id,
    action=action,
    package=None,
    execution_error=None,
)
```

中段恢复后只允许重新决策为 `VERIFY_AND_COMMIT_CLEANUP`、
`RESUME_COMMITTED_CLEANUP` 或 `ABANDON_UNAPPLIED`。若仍是 resume/inconsistent，保留现场并报
`operation.inconsistent_state`。

- [ ] **Step 7：消除 `resolve_restore_execution` 的第二套 phase 状态机**

删除未使用的 `execution_result` 参数和全部调用点。解决器只按 `RestoreRecoveryAction` 分派：

- VERIFY：加载/使用完整 package，证明 after，写 T rolled_back，再进入 committed cleanup；
- RESUME_COMMITTED：不加载完整包，凭 T cleanup authorization 继续；
- ABANDON：只在纯表批准后耐久 R rolled_back（如需要）、解关联 T、清 R 材料、解锁；
- INCONSISTENT：零副作用报错；
- RESUME_*：只能由 `_recover_locked_restore` 先执行，若传到终态解决器视为控制流错误并保留现场。

正常 `restore_page` 捕获 `execution_error` 后也先取得 action，但若 action 是 RESUME_*，必须重新
抛出原错误，不能在同一次业务调用中把明确失败自动改成成功。

- [ ] **Step 8：补写损坏现场测试**

对 APPLYING/ROLLING_BACK 各取一个真实现场，分别破坏：

- R/T 双向 metadata；
- staged/original 摘要或目标 before/after 摘要；
- 观察到的无关页面。

断言公开 recovery 返回 `operation.inconsistent_state`、`operation.journal_corrupt` 或
`operation.external_change` 中与现有边界匹配的稳定码，并且 R/T、包、锁和故障节点保持。
另加 `R=ROLLED_BACK,T=ROLLED_BACK` 与 `R=PREPARED,T=ROLLED_BACK`，断言在任何 journal 写入前
报 inconsistent。

- [ ] **Step 9：运行 Task 2 定向测试和核心门禁**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_trash.py" \
  "$RUNTIME/tests/integration/test_trash.py" \
  "$RUNTIME/tests/integration/test_trash_lifecycle.py" -q \
  -k 'restore or recovery_decision'
uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit" -q
uv run --project "$RUNTIME" --locked ruff format --check "$RUNTIME/src" "$RUNTIME/tests"
uv run --project "$RUNTIME" --locked ruff check "$RUNTIME/src" "$RUNTIME/tests"
uv run --project "$RUNTIME" --locked pyright -p "$RUNTIME/pyproject.toml"
```

预期：定向测试、核心 unit、Ruff、Pyright 全部通过。

- [ ] **Step 10：提交 Task 2**

只暂存本任务列出的生产文件与测试，建议提交信息：

```text
fix(draft): 恢复 restore 中段相位并保持协调锁
```

---

### Task 3：让 checker 共用 cleanup evidence 联合决策并稳定 I/O 错误

**Files:**

- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/checker.py:446-515`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/trash_package.py:542-655,899-965`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_checker.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_trash_package.py`

**Interfaces:**

- Consumes: `classify_cleanup_evidence_artifacts`、`cleanup_evidence_actions`、`CleanupEvidenceAction.CORRUPT`。
- Produces: `cleanup_evidence_protocol_corrupt` 纯函数；checker 的只读联合 evidence 验证边界；任何底层 OSError 都成为稳定 `CheckIssue`。

建议纯接口：

```python
def cleanup_evidence_protocol_corrupt(
    *,
    final: EvidenceNodeState,
    pending: EvidenceNodeState,
    quarantine: EvidenceNodeState,
    final_pending_same_node: bool,
    final_quarantine_same_node: bool,
) -> bool:
    """KEEP/REMOVE 任一判 CORRUPT 即返回 True；不访问文件系统。"""
```

- [ ] **Step 1：写联合状态的 checker 失败测试**

使用一个真实合法 TRASH journal/evidence 生成器，创建以下节点组合；双名“同 inode”使用
`os.link`，不同 inode 使用相同合法 bytes 分别写入：

```python
def build_cleanup_evidence_checker_case(
    draft_factory: DraftFactory,
) -> tuple[Path, Path, bytes]:
    import base64
    import json

    from draft.operations import operation_journal_path, read_journal
    from draft.trash_package import parse_package_authorization

    root = draft_factory(_trashable_checker_files())
    result = trash_page(
        root,
        Path("pages/obsolete.md"),
        now=datetime(2026, 7, 21, 12, 0, tzinfo=UTC),
    )
    journal_path = operation_journal_path(root, result.operation_id)
    raw = json.loads(journal_path.read_text(encoding="utf-8"))
    raw["phase"] = OperationPhase.TRASH_HANDOFF_SUCCEEDED.value
    journal_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    journal = read_journal(journal_path)
    package = root / ".draft/trash/outgoing" / result.operation_id
    authorization = parse_package_authorization(journal, package)
    leaves: list[dict[str, object]] = []
    for relative, digest in authorization.leaves():
        content = (root / relative).read_bytes()
        leaves.append(
            {
                "relative": relative.as_posix(),
                "sha256": digest,
                "state": "present",
                "content": base64.b64encode(content).decode("ascii"),
            }
        )
    evidence = {
        "version": 2,
        "operation_id": result.operation_id,
        "cleanup_phase": journal.phase.value,
        "leaves": leaves,
    }
    return root, journal.directory, json.dumps(evidence, ensure_ascii=False).encode("utf-8")


def write_cleanup_evidence_names(
    operation_dir: Path,
    evidence_bytes: bytes,
    names: tuple[str, ...],
    *,
    same_inode: bool,
) -> None:
    protocol_names = {
        "final": "cleanup-evidence.json",
        "pending": "cleanup-evidence.json.pending",
        "quarantine": "cleanup-evidence.json.quarantine",
    }
    first = operation_dir / protocol_names[names[0]]
    first.write_bytes(evidence_bytes)
    for role in names[1:]:
        target = operation_dir / protocol_names[role]
        if same_inode:
            target.hardlink_to(first)
        else:
            target.write_bytes(evidence_bytes)


@pytest.mark.parametrize(
    ("names", "same_inode", "corrupt"),
    [
        (("final",), False, False),
        (("pending",), False, False),
        (("quarantine",), False, False),
        (("final", "pending"), True, False),
        (("final", "pending"), False, True),
        (("final", "quarantine"), True, False),
        (("final", "quarantine"), False, True),
        (("pending", "quarantine"), False, True),
        (("final", "pending", "quarantine"), False, True),
    ],
)
def test_checker_matches_cleanup_evidence_joint_protocol(
    draft_factory: DraftFactory,
    names: tuple[str, ...],
    same_inode: bool,
    corrupt: bool,
) -> None:
    root, operation_dir, evidence_bytes = build_cleanup_evidence_checker_case(draft_factory)
    write_cleanup_evidence_names(
        operation_dir,
        evidence_bytes,
        names,
        same_inode=same_inode,
    )
    before = snapshot_tree(root)
    report = check_draft(root)
    assert (CheckCode.TRASH_PACKAGE_CORRUPT.value in codes(report)) is corrupt
    assert snapshot_tree(root) == before
```

测试应只断言本操作目录是否出现 evidence 损坏；如果夹具因 T 生命周期产生其他已知诊断，
按 path+code 过滤，不把其他错误清空来伪造健康根。

- [ ] **Step 2：写公开 checker 的原始异常失败测试**

在 cleanup evidence 精确路径上给 `os.lstat`/锚定读取分别注入 `PermissionError` 和
`OSError(errno.EIO, "injected I/O error")`。调用 `check_draft(root)`，断言：

```python
report = check_draft(root)
assert any(issue.code == CheckCode.TRASH_PACKAGE_CORRUPT.value for issue in report.errors)
assert snapshot_tree(root) == before
```

测试必须失败于“当前抛原始异常/漏报”，不能用 `pytest.raises(PermissionError)` 作为新预期。

- [ ] **Step 3：运行定向测试确认红灯**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/integration/test_checker.py" -q \
  -k 'cleanup_evidence and (joint or permission or io_error)'
```

预期：不同 inode 双名未被报告，或 `PermissionError` 原样逃逸。

- [ ] **Step 4：先写并实现纯联合损坏判定**

在 unit 中穷举 `EvidenceNodeState³ × same_pending × same_quarantine` 的 256 个输入。对每个输入
分别调用完整参数的 `cleanup_evidence_actions(intent=KEEP)` 和 `intent=REMOVE`，独立断言二者是否
含 `CORRUPT` 完全相等；再断言 `cleanup_evidence_protocol_corrupt` 等于该值。当前红灯应是
helper 尚不存在，而不是状态表本身不一致。

实现 helper 时不得复制 `_keep_evidence_actions/_remove_evidence_actions`，只组合现有权威
决策：

```python
def actions(intent: CleanupEvidenceIntent) -> tuple[CleanupEvidenceAction, ...]:
    return cleanup_evidence_actions(
        intent=intent,
        final=final,
        pending=pending,
        quarantine=quarantine,
        final_pending_same_node=final_pending_same_node,
        final_quarantine_same_node=final_quarantine_same_node,
    )


keep_corrupt = CleanupEvidenceAction.CORRUPT in actions(CleanupEvidenceIntent.KEEP)
remove_corrupt = CleanupEvidenceAction.CORRUPT in actions(CleanupEvidenceIntent.REMOVE)
return keep_corrupt or remove_corrupt
```

若穷举测试证明两个 intent 的 `CORRUPT` 集合已经不相等，停止 Task 3 并报告具体输入，不得
自行选择一个意图或删除断言。

- [ ] **Step 5：用一次联合分类替换逐名合法性判断**

在 `_check_operation_directory_entries` 中保留“未知名字是否允许”的目录协议检查，但 evidence
合法性改为每个操作目录只执行一次：

```python
classified = classify_cleanup_evidence_artifacts(journal)
if classified is not None:
    final, pending, quarantine, same_pending, same_quarantine = classified
    corrupt = cleanup_evidence_protocol_corrupt(
        final=final,
        pending=pending,
        quarantine=quarantine,
        final_pending_same_node=same_pending,
        final_quarantine_same_node=same_quarantine,
    )
    if corrupt:
        issues.append(_error(CheckCode.TRASH_PACKAGE_CORRUPT, evidence_relative))
```

删除 checker 对 evidence 的直接 `classify_node` 调用和 `_invalid_cleanup_evidence` 的逐文件
schema 路径；schema 已由共享分类验证。节点目录/链接会成为 `NON_FILE→CORRUPT`。

- [ ] **Step 6：稳定分类和读取错误**

捕获共享分类抛出的 `DraftError`，按其 code/path 生成 `CheckIssue`。若目录枚举本身抛
`OSError`，在 checker 边界转换为批准的内部协议诊断；不得把异常改成 missing，也不得修改
`classify_node` 去吞掉所有错误。

完成后运行结构级审计：checker 不再直接导入 `classify_node`。如果仍有其他生产调用者，逐个
判断其边界是否允许原始 OSError；只有必须稳定领域错误的入口才改用
`classify_node_or_error`，不要无关扩散重构。

- [ ] **Step 7：运行 Task 3 定向测试与核心 unit**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/integration/test_checker.py" \
  "$RUNTIME/tests/integration/test_trash_package.py" -q \
  -k 'cleanup_evidence or operation_directory'
uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit" -q
uv run --project "$RUNTIME" --locked ruff format --check "$RUNTIME/src" "$RUNTIME/tests"
uv run --project "$RUNTIME" --locked ruff check "$RUNTIME/src" "$RUNTIME/tests"
uv run --project "$RUNTIME" --locked pyright -p "$RUNTIME/pyproject.toml"
```

预期：定向测试、核心 unit、Ruff、Pyright 全部通过。

- [ ] **Step 8：提交 Task 3**

只暂存本任务文件，建议提交信息：

```text
fix(draft): 统一 checker 与 cleanup evidence 联合判定
```

---

### Task 4：横切审计、完整门禁与第十一轮修复记录

**Files:**

- Audit: `plugins/draft/skills/draft/scripts/runtime/src/draft/operations.py`
- Audit: `plugins/draft/skills/draft/scripts/runtime/src/draft/trash.py`
- Audit: `plugins/draft/skills/draft/scripts/runtime/src/draft/trash_lifecycle.py`
- Audit: `plugins/draft/skills/draft/scripts/runtime/src/draft/checker.py`
- Audit: `plugins/draft/skills/draft/scripts/runtime/src/draft/trash_package.py`
- Modify only when the audit first produces a failing contract test: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_trash.py`
- Modify only when the audit first produces a failing contract test: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash.py`
- Modify only when the audit first produces a failing contract test: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash_lifecycle.py`
- Modify only when the audit first produces a failing contract test: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_checker.py`
- Create: `docs/review-notes/2026-08-18-draft-candidate-root-incremental-migration-round-11-fix-record.md`

**Interfaces:**

- Consumes: Tasks 1–3 的 restore 单一决策、保锁恢复与 checker 联合分类。
- Produces: 跨入口验收证据、真实门禁结果和未验证事项；不新增生产抽象。

- [ ] **Step 1：逐入口审计 restore action 是否只有一个权威来源**

沿以下入口逐条记录“R/T 来源 → action → 是否加载包 → 写入顺序 → 清理 → 解锁 → 失败现场”：

1. `restore_page` 正常成功；
2. `restore_page` 规划/执行失败；
3. `recover_draft` 锁内 RESTORE；
4. `recover_interrupted_operations` 锁内 RESTORE；
5. 无锁 RESTORE 残留分类。

代码结构必须满足：phase 组合只在 `restore_recovery_decision`（或唯一等价函数）定义；I/O
入口只消费 action。发现复制分支时先补失败测试，再回到 Task 1/2 的共同边界修复。

- [ ] **Step 2：逐入口审计 cleanup evidence 是否共享联合分类**

检查：

- `converge_cleanup_evidence`；
- 权威包 cleanup 的 KEEP/REMOVE 路径；
- 终态 trash/restore recovery；
- `check_draft`。

运行时动作与 checker 只读结论必须来自同一个 `classify_cleanup_evidence_artifacts` 和
`cleanup_evidence_actions`。不得存在 checker 私有的逐名合法性列表。

- [ ] **Step 3：做结构级残留检查**

先用 `rg` 定位候选，再人工检查实际导入与调用；不要把字符串命中直接当结论：

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
rg -n 'restore_recovery_decision|RestoreRecoveryAction|classify_node|cleanup_evidence_actions|classify_cleanup_evidence_artifacts' \
  "$RUNTIME/src/draft"
```

期望：

- restore phase 分支没有第二份完整 R/T 表；
- checker 不直接调用 `classify_node` 处理 evidence；
- checker 与运行时都引用共享 evidence 分类/决策。

若项目已有 AST 依赖测试，优先在其中增加“checker 不从 operations 导入 classify_node”的真实
import 断言，而不是永久维护易误报的源码字符串测试。

- [ ] **Step 4：运行完整离线门禁**

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
RUNTIME="$REPO_ROOT/plugins/draft/skills/draft/scripts/runtime"

uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit" -q
uv run --project "$RUNTIME" --locked \
  pytest "$RUNTIME/tests/integration" -q -m 'not network'

uv run --project "$RUNTIME" --locked \
  pytest "$REPO_ROOT/plugins/draft/scripts/tests/unit" -q
uv run --project "$RUNTIME" --locked \
  pytest "$REPO_ROOT/plugins/draft/scripts/tests/integration" -q -m 'not network'

uv run --project "$RUNTIME" --locked ruff format --check "$RUNTIME/src" "$RUNTIME/tests"
uv run --project "$RUNTIME" --locked ruff check "$RUNTIME/src" "$RUNTIME/tests"
uv run --project "$RUNTIME" --locked pyright -p "$RUNTIME/pyproject.toml"
bash "$REPO_ROOT/plugins/draft/scripts/check-adapters.sh"
```

逐条记录退出码、passed/deselected 数量。网络测试若未运行，修复记录必须明确写“未运行”，
不得写“全量门禁全部通过”。

- [ ] **Step 5：回看独立契约覆盖**

逐项回答并写入修复记录：

- H1：三个中段 phase 是否都由真实生产者写出，并由两个公开入口恢复？
- H1：forward/rollback 是否在 R 终态后仍持锁，直到 T 提交/解关联？
- H2：所有合法/非法双名组合是否同时得到运行时与 checker 相同的损坏结论？
- M1：是否存在第二套 R/T phase 分支或未使用的 `execution_result`？
- M2：`check_draft` 是否可能泄漏 evidence 分类/读取的原始 OSError？
- Q1：测试是否验证了生产者—消费者接缝，而不仅是手工 JSON？

任一答案不是可引用的测试或源码事实时，不得声明对应评审项完成。

- [ ] **Step 6：创建第十一轮 fix record**

新文件必须至少包含：

```markdown
# 第十一轮评审修复记录（2026-08-18）

## 逐条核验与修复
| 发现 | 红灯证据 | 违反的不变量 | 修复边界 | 回归测试 | 提交 |

## 状态表实现映射
列出每个 RestoreRecoveryAction 的生产函数、消费入口、锁状态和失败现场。

## 横切入口审计
列出 restore 五个入口和 cleanup evidence 四个入口。

## 门禁证据
只记录本轮实际运行的命令与数量。

## 未验证事项
网络测试、未做的进程级故障矩阵和安全边界延期项如实列出。
```

不要改写第十一轮评审原文；fix record 是实施证据，不是重新定义发现。

- [ ] **Step 7：提交 Task 4**

只提交审计实际产生的测试/生产修正；`docs/review-notes/` 按项目约定保持临时未跟踪，不纳入
版本控制。若 Task 4 没有生产修正，则无需制造空提交。

建议提交信息（仅在有代码/测试修正时）：

```text
test(draft): 闭合 restore 与 evidence 跨入口验收
```

---

## 执行顺序与停止条件

1. Task 1 先固定正确状态表；未通过完整 pair 矩阵前不得进入 I/O 实现。
2. Task 2 只实现 restore 保锁恢复和单一 action 消费；不得同时重构 operations 模块。
3. Task 3 与 Task 2 在代码上独立，但在 Task 1–2 通过后执行，避免执行 Agent并行修改相邻
   checker/trash import 边界造成难以归因的失败。
4. Task 4 只做横切审计、补漏和完整门禁；若发现行为契约缺失，退回相应任务补红灯，不能在
   Task 4 临时增加未经状态表定义的分支。

出现以下任一情况应停止并向用户/评审 Agent 报告，不自行选择语义：

- 为保留 R 锁必须改变现有公开 CLI 返回协议；
- R 中段恢复后出现本表未定义的新 T phase；
- KEEP 与 REMOVE 对 evidence 的 `CORRUPT` 集合实际不一致，且无法从当前 T/package 状态唯一
  派生 intent；
- 修复要求更改已经批准的本地协作式安全边界；
- 完整门禁暴露与本轮无关且需要破坏性迁移的失败。

## 计划自检

- 规格覆盖：H1、H2、M1、M2、Q1 分别由 Task 1–4 覆盖；没有把模块拆分或更强并发模型夹带
  进本轮。
- 横切闭合：R/T 权威输入、决策、mutation 副作用、终态提交、cleanup、失败现场、重试和
  checker 可观察结果均有明确入口。
- 类型一致：`RestoreRecoveryAction` 六个动作在状态表、建议接口、Task 1/2 测试和解决器中
  名称一致。
- 无占位步骤：每项都给出输入状态、预期行为、测试位置、运行命令和停止条件；执行 Agent
  不需要自行猜测合法 R/T 组合。
