# Draft restore 放弃恢复、checker 与 I/O 闭合实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 闭合第十二轮确认的 restore 放弃路径耐久状态、R 终态文件证明、真实 restore cleanup
checker 扫描、inode 身份错误语义和 checker 内部目录 I/O 诊断，使所有批准的进程中断和普通
I/O 失败都能稳定失败或重复恢复，不产生错误成功。

**Architecture:** R/T 恢复继续使用一个纯决策表，但把关联从布尔值提升为受约束状态，并增加
“T 已解关联、待完成 R 清理”动作。I/O 层按 R phase 只采集决策需要的事实，在释放锁前复用
通用终态证明。checker 先建立全操作目录只读上下文，再识别真正的 cleanup evidence 所有者；
底层 inode/目录读取只把 ENOENT 当作 missing，其他错误由各领域边界稳定化。

**Tech Stack:** Python 3.12、pytest、Ruff、Pyright strict、uv。

**Spec:**
`docs/review-notes/2026-08-18-draft-candidate-root-incremental-migration-round-12.md`

## Global Constraints

- 已确认行为契约、错误码、状态转换和测试分区是硬性要求；下述内部 helper 名称和参数签名是
  建议实现，可在保持同一数据流和责任边界的前提下调整。
- 仅覆盖本地协作式单写者、普通 I/O 失败和进程中断；不扩展到 NFS、机器掉电、恶意
  `.draft/` 篡改或无法消除的 POSIX 调用间隙。
- 本分支不拆分 `operations.py`，不进行与本轮契约无关的模块重构。
- 每个生产修复先写可证伪失败测试；正例由真实生产者创建，不能只手改最终 journal JSON。
- unit 预期从本计划的状态表独立列出，不调用生产映射生成 expected；真实文件系统、锁、崩溃点
  和公开恢复入口均属于 integration。
- 所有失败分区必须断言 R/T journal bytes、锁、权威包、恢复材料和受管页面中哪些保持不变；
  不能只断言错误码。
- 不新增错误码；使用现有 `operation.external_change`、`operation.inconsistent_state`、
  `operation.journal_corrupt`、`trash.package_corrupt` 等已登记语义。
- 网络测试可以不运行，但修复记录必须明确写“未运行”；不得写“全量门禁全部通过”。
- `AGENTS.md` 是用户未提交改动，执行 Agent不得纳入任何提交。

---

## 权威行为契约一：restore 关联状态与放弃动作

关联状态不是布尔值：

| 状态 | R→T | T 的 `restore_operation_id` | 含义 |
|---|---|---|---|
| `MATCHED` | 精确指向 T | 精确等于 R id | 双向认领仍有效 |
| `TRASH_UNCLAIMED` | 精确指向 T | 缺失 | ABANDON 已耐久提交解关联，待完成 R 清理 |
| `CONFLICT` | 缺失/非法/错 T | 指向其他 R、kind/id 不匹配或不可证明 | 不得猜测，零写入失败 |

在第十一轮完整 R phase × T phase 表上增加/修改以下有效分区；未列分区仍为
`INCONSISTENT`：

| R phase | T phase | link state | 附加事实 | 动作 |
|---|---|---|---|---|
| `APPLYING` / `VALIDATING` | `TRASH_RETAINED` | `MATCHED` | 不读取 unapplied proof | `RESUME_FORWARD` |
| `ROLLING_BACK` | `TRASH_RETAINED` | `MATCHED` | 不读取 unapplied proof | `RESUME_ROLLBACK` |
| `COMPLETED` | `TRASH_RETAINED` | `MATCHED` | 不读取 unapplied proof | `VERIFY_AND_COMMIT_CLEANUP` |
| `COMPLETED` | `ROLLED_BACK` | `MATCHED` | 不读取 unapplied proof | `RESUME_COMMITTED_CLEANUP` |
| `ROLLED_BACK` | `TRASH_RETAINED` | `MATCHED` | 释放锁前必须证明 target=before | `ABANDON_UNAPPLIED` |
| `ALLOCATING` / `PREPARED` | `TRASH_RETAINED` | `MATCHED` | `proven_unapplied=True` | `ABANDON_UNAPPLIED` |
| `ROLLED_BACK` | `TRASH_RETAINED` | `TRASH_UNCLAIMED` | 释放锁前必须证明 target=before | `FINISH_ABANDON_CLEANUP` |

动作顺序：

| 动作 | 持锁副作用顺序 |
|---|---|
| `ABANDON_UNAPPLIED` | 证明未应用或验证 R rolled_back 终态 → 必要时写 R rolled_back → 写 T unclaimed → 清理 R 登记材料 → 释放 R 锁 |
| `FINISH_ABANDON_CLEANUP` | 验证 R rolled_back 终态 → 幂等清理 R 登记材料 → 释放 R 锁；不再写 T |

## 权威行为契约二：I/O 事实语义

| 事实读取 | ENOENT | EACCES/EIO/其他 OSError |
|---|---|---|
| inode/name identity | `MISSING` | 稳定领域错误；不得返回 None/False 伪装 missing |
| R target unapplied proof | 无法证明（false） | 无法证明或稳定领域错误；不得泄漏原始异常 |
| checker 内部目录枚举 | 稳定的缺失/形状诊断 | 追加稳定 `CheckIssue`，继续扫描独立子树 |
| evidence/artifact 消费前身份复核 | 幂等缺失 | 停止，保留锁、材料和节点；不得返回成功 |

---

### Task 1: 扩展唯一 R/T 纯决策表

**Files:**

- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/trash.py:367-416`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_trash.py`

**Interfaces:**

- Consumes: `OperationPhase`、现有 `RestoreRecoveryAction`。
- Produces（建议）:

```python
class RestoreLinkState(StrEnum):
    MATCHED = "matched"
    TRASH_UNCLAIMED = "trash_unclaimed"
    CONFLICT = "conflict"

class RestoreRecoveryAction(StrEnum):
    VERIFY_AND_COMMIT_CLEANUP = "verify_and_commit_cleanup"
    RESUME_COMMITTED_CLEANUP = "resume_committed_cleanup"
    RESUME_FORWARD = "resume_forward"
    RESUME_ROLLBACK = "resume_rollback"
    ABANDON_UNAPPLIED = "abandon_unapplied"
    FINISH_ABANDON_CLEANUP = "finish_abandon_cleanup"
    INCONSISTENT = "inconsistent"

def restore_recovery_decision(
    *,
    restore_phase: OperationPhase,
    trash_phase: OperationPhase,
    link_state: RestoreLinkState,
    proven_unapplied: bool,
) -> RestoreRecoveryAction:
    if link_state is RestoreLinkState.CONFLICT:
        return RestoreRecoveryAction.INCONSISTENT
    pair = (restore_phase, trash_phase)
    if link_state is RestoreLinkState.TRASH_UNCLAIMED:
        if pair == (OperationPhase.ROLLED_BACK, OperationPhase.TRASH_RETAINED):
            return RestoreRecoveryAction.FINISH_ABANDON_CLEANUP
        return RestoreRecoveryAction.INCONSISTENT
    if pair in (
        (OperationPhase.APPLYING, OperationPhase.TRASH_RETAINED),
        (OperationPhase.VALIDATING, OperationPhase.TRASH_RETAINED),
    ):
        return RestoreRecoveryAction.RESUME_FORWARD
    if pair == (OperationPhase.ROLLING_BACK, OperationPhase.TRASH_RETAINED):
        return RestoreRecoveryAction.RESUME_ROLLBACK
    if pair == (OperationPhase.COMPLETED, OperationPhase.TRASH_RETAINED):
        return RestoreRecoveryAction.VERIFY_AND_COMMIT_CLEANUP
    if pair == (OperationPhase.COMPLETED, OperationPhase.ROLLED_BACK):
        return RestoreRecoveryAction.RESUME_COMMITTED_CLEANUP
    if pair == (OperationPhase.ROLLED_BACK, OperationPhase.TRASH_RETAINED):
        return RestoreRecoveryAction.ABANDON_UNAPPLIED
    if (
        restore_phase in (OperationPhase.ALLOCATING, OperationPhase.PREPARED)
        and trash_phase is OperationPhase.TRASH_RETAINED
        and proven_unapplied
    ):
        return RestoreRecoveryAction.ABANDON_UNAPPLIED
    return RestoreRecoveryAction.INCONSISTENT
```

- 硬性要求：关联状态必须能区分 MATCHED、T 未认领和冲突；具体类型/名称可调整。

- [ ] **Step 1：把独立期望表写成失败 unit**

在 `test_trash.py` 现有 restore 决策矩阵中显式增加 link state 维度。expected 用字面表维护，
不要调用任何生产 helper 推导：

```python
LEGAL = {
    (OperationPhase.APPLYING,
     OperationPhase.TRASH_RETAINED,
     RestoreLinkState.MATCHED): RestoreRecoveryAction.RESUME_FORWARD,
    (OperationPhase.VALIDATING,
     OperationPhase.TRASH_RETAINED,
     RestoreLinkState.MATCHED): RestoreRecoveryAction.RESUME_FORWARD,
    (OperationPhase.ROLLING_BACK,
     OperationPhase.TRASH_RETAINED,
     RestoreLinkState.MATCHED): RestoreRecoveryAction.RESUME_ROLLBACK,
    (OperationPhase.COMPLETED,
     OperationPhase.TRASH_RETAINED,
     RestoreLinkState.MATCHED): RestoreRecoveryAction.VERIFY_AND_COMMIT_CLEANUP,
    (OperationPhase.COMPLETED,
     OperationPhase.ROLLED_BACK,
     RestoreLinkState.MATCHED): RestoreRecoveryAction.RESUME_COMMITTED_CLEANUP,
    (OperationPhase.ROLLED_BACK,
     OperationPhase.TRASH_RETAINED,
     RestoreLinkState.MATCHED): RestoreRecoveryAction.ABANDON_UNAPPLIED,
    (OperationPhase.ROLLED_BACK,
     OperationPhase.TRASH_RETAINED,
     RestoreLinkState.TRASH_UNCLAIMED):
        RestoreRecoveryAction.FINISH_ABANDON_CLEANUP,
}

for restore_phase in RESTORE_PHASES:
    for trash_phase in TRASH_PHASES:
        for link_state in RestoreLinkState:
            expected = LEGAL.get(
                (restore_phase, trash_phase, link_state),
                RestoreRecoveryAction.INCONSISTENT,
            )
            assert restore_recovery_decision(
                restore_phase=restore_phase,
                trash_phase=trash_phase,
                link_state=link_state,
                proven_unapplied=False,
            ) is expected
```

另写独立测试证明 `proven_unapplied` 只改变
`ALLOCATING/PREPARED × TRASH_RETAINED × MATCHED`，不改变任何其他组合。

- [ ] **Step 2：运行 unit，确认因新状态/动作不存在或结果错误而红**

Run:

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_trash.py" -q -k 'restore_recovery_decision or restore_link_state'
```

Expected: FAIL；失败原因必须是缺少新状态/动作或旧 bool 表无法区分 unclaimed，不得先改 expected
迁就现状。

- [ ] **Step 3：最小实现受约束关联状态和新动作**

按权威表逐项显式返回，使用 Interfaces 中给出的完整函数体；不得把表改回 bool 关联。

```python
assert RestoreLinkState.TRASH_UNCLAIMED is not RestoreLinkState.CONFLICT
assert restore_recovery_decision(
    restore_phase=OperationPhase.ROLLED_BACK,
    trash_phase=OperationPhase.TRASH_RETAINED,
    link_state=RestoreLinkState.TRASH_UNCLAIMED,
    proven_unapplied=False,
) is RestoreRecoveryAction.FINISH_ABANDON_CLEANUP
```

不要在纯函数内读取 journal、文件或锁。

- [ ] **Step 4：运行定向 unit 和核心 unit**

Run:

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_trash.py" "$RUNTIME/tests/unit" -q
```

Expected: PASS。

- [ ] **Step 5：提交 Task 1**

```bash
git add \
  plugins/draft/skills/draft/scripts/runtime/src/draft/trash.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_trash.py
git commit -m "fix(draft): 建模 restore 已解关联清理状态"
```

---

### Task 2: 闭合 ABANDON 耐久中断与 R 终态证明

**Files:**

- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/trash.py:913-1026`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/trash_lifecycle.py:675-735`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/operations.py:2677-2745`（仅复用/暴露终态证明边界，不拆模块）
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash_lifecycle.py`（若第二公开入口既有覆盖位于此文件）

**Interfaces:**

- Consumes: Task 1 的 `RestoreLinkState`、`FINISH_ABANDON_CLEANUP`。
- Produces（建议签名）:

```python
@dataclass(frozen=True)
class RestoreRecoveryFacts:
    restore: Journal
    trash: Journal
    link_state: RestoreLinkState
```

- `load_restore_recovery_facts(root: Path, restore_id: str) -> RestoreRecoveryFacts`
- `verify_locked_terminal_operation(root: Path, operation_id: str, *, expected: OperationPhase) -> OperationResult`

- 硬性要求：恢复专用事实读取必须接受 R→T 正确且 T claim 缺失；消费 ABANDON 两个动作前必须
  在持锁状态验证 R rolled_back 的 target=before。

- [ ] **Step 1：写 T 解关联后中断的真实失败测试**

使用真实 trash + restore 冲突/回滚路径。不要手改 phase。把中断分别注入：

1. `write_journal(T without restore_operation_id)` 已返回后、`clean_recovery_copies` 前；
2. R 恢复副本清理中途；
3. 清理已完成、`release_lock` 前。

每个现场分别通过 `recover_draft` 和 `recover_interrupted_operations` 恢复，并断言：

```python
assert read_lock(root) is None
assert read_journal(t_path).phase is OperationPhase.TRASH_RETAINED
assert "restore_operation_id" not in read_journal(t_path).metadata
assert not (r_dir / "originals").exists()
assert not (r_dir / "staged").exists()
assert authoritative_package.exists()
```

重复调用公开入口，结果必须继续稳定且不删除 T 包。

- [ ] **Step 2：写 R rolled_back 终态被外部改变的失败矩阵**

通过真实 `ROLLING_BACK` 中断 + 通用恢复器形成“R rolled_back、T matched、R 锁仍在”的耐久
现场，再把目标分别置为：before、after、wrong bytes、missing、directory、symlink。

对非 before 分区断言：

```python
with pytest.raises(DraftError) as caught:
    recover_draft(root, now=NOW)
assert caught.value.code == "operation.external_change"
assert t_journal.read_bytes() == before_t
assert lock.read_bytes() == before_lock
assert authoritative_package_snapshot(root, trash_id) == before_package
```

同样覆盖 Task 1 的 `TRASH_UNCLAIMED` 现场；不得因为 T 已解关联就跳过 R 终态证明。

- [ ] **Step 3：运行定向 integration，确认当前分别报 plan_invalid 和错误成功**

Run:

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/integration/test_trash.py" \
  "$RUNTIME/tests/integration/test_trash_lifecycle.py" -q \
  -k 'abandon and (unlink or cleanup or terminal or external_change)'
```

Expected: 至少一类 unclaimed 现场报 `operation.plan_invalid`，终态改写现场错误返回成功。

- [ ] **Step 4：实现恢复专用关联事实分类**

事实读取顺序必须是：验证锁属于 R → 验证 R kind/id 和 R→T → 读取 T → 分类 T claim。参考：

```python
claim = trash.metadata.get("restore_operation_id")
if claim == restore.operation_id:
    state = RestoreLinkState.MATCHED
elif claim is None:
    state = RestoreLinkState.TRASH_UNCLAIMED
else:
    state = RestoreLinkState.CONFLICT
```

R→T 缺失/非法、T kind/id 错误直接归 `CONFLICT` 或稳定抛
`operation.inconsistent_state`；不能把这些情况误归 unclaimed。正常业务计划验证仍可继续要求严格
双向关联；不要放宽 `validate_preallocated_journal`。

- [ ] **Step 5：在两个 ABANDON 动作释放锁前复用终态证明**

对 R=`rolled_back`，调用现有 `_recover_terminal` 的保锁语义或抽出的公开窄 helper：

```python
verify_locked_terminal_operation(
    root,
    restore.operation_id,
    expected=OperationPhase.ROLLED_BACK,
)
```

验证必须只读 target 并保留锁/材料；验证失败不能先写 T、清副本或解锁。对
`ALLOCATING/PREPARED` 继续使用 Task 3 的未应用证明。

- [ ] **Step 6：实现 `FINISH_ABANDON_CLEANUP` 的幂等后半段**

```python
if action is RestoreRecoveryAction.FINISH_ABANDON_CLEANUP:
    verify_locked_terminal_operation(
        root,
        restore.operation_id,
        expected=OperationPhase.ROLLED_BACK,
    )
    clean_recovery_copies(restore)
    release_lock(root, restore.operation_id)
    return rolled_back_result
```

不得重写 T，不得加载或清理权威包。`resolve_restore_execution` 若当前无条件调用
`validate_restore_link`，必须改为消费 `RestoreRecoveryFacts` 或恢复专用分类结果，避免在新动作
分派前再次拒绝 unclaimed。

- [ ] **Step 7：运行定向测试、完整 trash integration 与核心 unit**

Run:

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/integration/test_trash.py" \
  "$RUNTIME/tests/integration/test_trash_lifecycle.py" \
  "$RUNTIME/tests/unit/test_trash.py" -q
```

Expected: PASS；失败分区的快照断言全部成立。

- [ ] **Step 8：提交 Task 2**

```bash
git add \
  plugins/draft/skills/draft/scripts/runtime/src/draft/trash.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/trash_lifecycle.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/operations.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash_lifecycle.py
git commit -m "fix(draft): 闭合 restore 放弃路径的耐久恢复"
```

---

### Task 3: 仅在决策需要时采集 unapplied proof

**Files:**

- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/trash.py:353-364,913-923`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/trash_lifecycle.py:691-714`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_trash.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash.py`

**Interfaces:**

- Consumes: Task 1 的 phase 决策表。
- Produces（建议）:

```python
def restore_unapplied_fact(root: Path, journal: Journal) -> bool:
    """仅供 ALLOCATING/PREPARED；无法证明返回 False，不泄漏原始 OSError。"""
```

- [ ] **Step 1：写“无关 phase 不读取 proof”的 spy unit**

参数化 `APPLYING/VALIDATING/ROLLING_BACK/COMPLETED/ROLLED_BACK`。把 proof helper 替换为一旦
调用就抛 `AssertionError`，调用 action 入口并断言没有触发。`ALLOCATING/PREPARED` 则断言恰好
读取一次。

- [ ] **Step 2：写真实目标类型/读取失败 integration**

真实创建 R 中段现场，把恢复目标置为 directory、symlink、wrong bytes；调用两个公开恢复入口。
断言返回 `DraftError`（通常 `operation.external_change`），不出现
`IsADirectoryError/PermissionError/OSError`，锁与 R/T 保留。另覆盖
`ALLOCATING/PREPARED` 无法读取时为“无法证明”，不得进入 ABANDON。

- [ ] **Step 3：运行定向测试确认红灯**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_trash.py" \
  "$RUNTIME/tests/integration/test_trash.py" -q \
  -k 'unapplied and (phase or directory or io_error)'
```

- [ ] **Step 4：实现 phase-scoped 事实采集**

两个调用点先读 R phase，再决定是否调用 proof：

```python
needs_proof = restore.phase in (OperationPhase.ALLOCATING, OperationPhase.PREPARED)
proven = restore_unapplied_fact(root, restore) if needs_proof else False
```

proof 内 `sha256_file` 的类型/读取失败不能逃逸原始异常；在该布尔事实边界安全地返回 false，
由纯表得到 INCONSISTENT。不要在纯决策函数内读取文件。

- [ ] **Step 5：运行定向测试和核心 unit**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit" "$RUNTIME/tests/integration/test_trash.py" -q
```

- [ ] **Step 6：提交 Task 3**

```bash
git add \
  plugins/draft/skills/draft/scripts/runtime/src/draft/trash.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/trash_lifecycle.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_trash.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash.py
git commit -m "fix(draft): 按 restore phase 采集恢复事实"
```

---

### Task 4: 让 checker 识别真实 cleanup owner 并统一根内路径

**Files:**

- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/checker.py:446-610`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_checker.py`
- Modify when useful for shared real producer: `plugins/draft/skills/draft/scripts/runtime/tests/integration/support.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_cli.py`（path-prefix/JSON 机器协议）

**Interfaces:**

- Consumes: `classify_cleanup_evidence_artifacts`、
  `cleanup_evidence_protocol_corrupt`、R/T journal metadata。
- Produces（建议类型/签名）：
  `OperationCheckContext(locked_operation_id: str | None, cleanup_owner_ids: frozenset[str])`；
  `_check_operation_directory_entries(root: Path, entry: Path, journal: Journal,
  issues: list[CheckIssue], *, allow_cleanup_evidence: bool) -> None`。

- 硬性要求：锁存在时仍扫描全部操作目录与 trash tree；只有由锁和合法 R/T 关系证明的 T 才能
  在 `rolled_back` 下拥有 cleanup evidence。

- [ ] **Step 1：用真实 restore producer 写 checker 失败矩阵**

通过 `trash_page` + `restore_retained_trash`，在 cleanup evidence 已发布、首个包叶删除前中断。
现场必须满足：R 锁存在、R=`completed`、T=`rolled_back`、T 下有 evidence。

在 T 目录构造：

- final only（合法）；
- final+pending 同 inode（合法过渡）；
- final+pending 不同 inode（损坏）；
- final+quarantine 同/不同 inode；
- pending+quarantine；
- 三名并存；
- 非法 bytes、directory、symlink。

断言 code/path 与树快照：

```python
report = check_draft(root)
evidence_path = Path(".draft/operations") / trash_id / "cleanup-evidence.json"
assert (CheckCode.TRASH_PACKAGE_CORRUPT.value, evidence_path) in issue_pairs(report)
assert snapshot_tree(root) == before
```

合法组合不应产生 `trash.package_corrupt`/evidence unexpected，但仍应有
`operation.recovery_required`。

- [ ] **Step 2：写“有锁仍扫描其他子树”的失败测试**

在同一真实现场另建一个损坏 operation 目录和一个非法 trash/outgoing 条目。断言一次
`check_draft` 同时报告 recovery required、operation 损坏和 trash tree 损坏；锁目录不重复扫描。

- [ ] **Step 3：写诊断 path 的机器协议测试**

对普通损坏与 injected EIO 各断言一次：

```python
assert issue.path == Path(".draft/operations") / operation_id / "cleanup-evidence.json"
assert not issue.path.is_absolute()
```

再通过 CLI `--path-prefix .draft/operations/<id>` 和 JSON 输出验证该诊断可过滤、输出不含绝对
临时根。

- [ ] **Step 4：运行定向测试确认 early return/terminal 规则/path 均红**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/integration/test_checker.py" \
  "$RUNTIME/tests/unit/test_cli.py" -q \
  -k 'restore_cleanup or cleanup_owner or evidence_path'
```

- [ ] **Step 5：先建立全目录只读上下文，再逐目录检查**

推荐两遍：第一遍安全解析 journal/锁并建立 id→journal 索引及 cleanup owner；第二遍执行目录条目、
phase 和 trash tree 诊断。删除有锁分支的全局 early return。

cleanup owner 的充分条件必须同时包括：

```text
lock == R.id
R.kind == RESTORE
R.phase == COMPLETED
R.metadata[restores_trash_operation] == T.id
T.kind == TRASH
T.phase == ROLLED_BACK
T.metadata[restore_operation_id] == R.id
```

条件不完整时不得放宽 terminal T 目录协议。

- [ ] **Step 6：按上下文选择 terminal T 的允许条目与联合分类**

普通 `completed/rolled_back` 仍只允许 `journal.json`；cleanup owner T 额外允许三个 evidence 名，
并无条件调用共享联合分类。不要为 checker 写新的 final/pending/quarantine 决策表。

- [ ] **Step 7：统一根内相对 path**

helper 显式接收 root。所有 `Path` 先尝试 `relative_to(root)`；若领域错误路径不在 root 内，使用
预先计算的根内 fallback。禁止 `entry.relative_to(entry.parents[1])` 和未经处理的绝对
`error.paths[0]` 进入 `CheckIssue`。

- [ ] **Step 8：运行 checker/CLI 定向、核心 unit 与 integration checker**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/integration/test_checker.py" \
  "$RUNTIME/tests/unit/test_cli.py" \
  "$RUNTIME/tests/unit" -q
```

- [ ] **Step 9：提交 Task 4**

```bash
git add \
  plugins/draft/skills/draft/scripts/runtime/src/draft/checker.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_checker.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/support.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_cli.py
git commit -m "fix(draft): 检查真实 restore cleanup evidence 状态"
```

若 `support.py` 未修改，不得为了匹配命令而把它加入提交。

---

### Task 5: 区分 inode 缺失与身份读取失败

**Files:**

- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/operations.py:679-694` 及全部 `_name_node/_same_name_node` 调用点
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/trash_package.py:940-1085`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_operations.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_trash_package.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_operations.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_checker.py`

**Interfaces:**

- Produces（建议）:

```python
def _name_node(
    parent_fd: int,
    name: str,
    *,
    code: str,
    code_path: Path,
) -> tuple[int, int] | None:
    try:
        st = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise DraftError(code, code_path) from exc
    return (st.st_ino, st.st_dev)
```

- `_same_name_node` 可以保留 bool 返回“同一/不同”，但必须让身份读取的 `DraftError` 传播；不得
  把读取失败返回 false 后在调用者处当作幂等完成。

- [ ] **Step 1：写 helper unit 的三分状态测试**

对同一名字分别模拟：存在、`FileNotFoundError`、`PermissionError`/EIO。断言只有 ENOENT 返回
None；其他失败按传入 code/path 抛 `DraftError` 并保留 `__cause__`。

- [ ] **Step 2：写真实 evidence 错误成功失败测试**

在真实 committed restore cleanup 中，分别在：

1. 联合分类首次 identity capture；
2. evidence 消费前 identity capture；
3. 内容验证后的 same-node recheck

注入 EACCES/EIO。调用 `recover_draft`，断言稳定 `trash.package_corrupt`（或批准的耐久错误），
锁不释放、evidence/包/材料保持可恢复；移除故障后再次 recover 能收敛。

该测试必须捕获当前“返回 completed + evidence 残留 + 锁释放”的错误成功。

- [ ] **Step 3：写 managed artifact 同类测试**

在真实普通操作恢复现场，对 `_consume_named_artifact` 的首次 identity capture 注入错误。断言
`operation.external_change`（或该边界已有稳定 code）、锁和 artifact 保留、没有错误成功；故障
移除后 recover 收敛。

- [ ] **Step 4：写 checker identity 读取失败测试**

让 evidence 的 `classify()` 成功，仅第二次 inode identity stat 失败。断言 checker 报稳定
`trash.package_corrupt`，不能只测试第一次 classify stat 失败。

- [ ] **Step 5：运行定向测试确认红灯**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_operations.py" \
  "$RUNTIME/tests/unit/test_trash_package.py" \
  "$RUNTIME/tests/integration/test_operations.py" \
  "$RUNTIME/tests/integration/test_trash.py" \
  "$RUNTIME/tests/integration/test_checker.py" -q \
  -k 'name_node or identity_stat or evidence_identity or artifact_identity'
```

- [ ] **Step 6：实现 error-aware identity helper 并逐调用点传领域语义**

逐项审计 `rg -n '_name_node\(|_same_name_node\(' runtime/src/draft` 的所有调用。至少按下表传递：

| 调用类别 | code/path |
|---|---|
| cleanup evidence classify/consume/publish | `trash.package_corrupt` + evidence path |
| mutation/managed temp artifact 身份复核 | `operation.external_change` +受管目标/临时路径 |
| journal/durable publish 的发布后身份证明 | 既有 `operation.durability_failed` 或该 helper 的既有 code_path；保持调用方原语义 |

禁止保留“`verified is None: return`”而不区分 ENOENT 与读取失败；新 helper 已保证 None 只代表
ENOENT，调用者注释也应改成该精确含义。

- [ ] **Step 7：运行全量相关测试与静态检查**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_operations.py" \
  "$RUNTIME/tests/unit/test_trash_package.py" \
  "$RUNTIME/tests/integration/test_operations.py" \
  "$RUNTIME/tests/integration/test_trash.py" \
  "$RUNTIME/tests/integration/test_checker.py" -q
uv run --project "$RUNTIME" --locked pyright -p "$RUNTIME/pyproject.toml"
uv run --project "$RUNTIME" --locked ruff check "$RUNTIME/src" "$RUNTIME/tests"
```

- [ ] **Step 8：提交 Task 5**

```bash
git add \
  plugins/draft/skills/draft/scripts/runtime/src/draft/operations.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/trash_package.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_operations.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_trash_package.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_operations.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_checker.py
git commit -m "fix(draft): 区分 inode 缺失与身份读取失败"
```

---

### Task 6: 闭合 checker 内部目录的稳定 I/O 边界

**Files:**

- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/checker.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_checker.py`

**Interfaces:**

- Consumes: Task 4 的全目录扫描结构和根内 path helper。
- Produces（建议签名）：`_list_internal_directory(root: Path, directory: Path, *,
  code: CheckCode, issues: list[CheckIssue]) -> tuple[Path, ...] | None`。

- [ ] **Step 1：写父目录故障矩阵**

分别对以下节点的 `lstat/iterdir` 注入 EACCES 与 EIO：

- `.draft/operations`；
- 单个 operation 目录；
- `.draft/trash`；
- `.draft/trash/outgoing`。

每个测试断言 `check_draft` 返回 `CheckReport`、包含稳定 code/path、树快照不变。再组合一个子树
不可读 + 另一个子树已损坏，断言后者仍被报告。

- [ ] **Step 2：运行测试，确认当前 operations 根等入口泄漏原始异常**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/integration/test_checker.py" -q \
  -k 'internal_directory and (permission or io_error)'
```

- [ ] **Step 3：实现每子树稳定化边界**

helper 只包住一次分类/枚举并追加一次 issue；返回 None 表示该子树无法继续。调用者继续检查独立
子树。不要用 `except OSError: return []`，因为这会把失败解释为空；也不要在 `check_draft`
最外层捕获后停止全部检查。

审计至少：

```bash
rg -n '\.iterdir\(|\.is_dir\(|\.is_file\(|\.exists\(|\.is_symlink\(' \
  plugins/draft/skills/draft/scripts/runtime/src/draft/checker.py
```

逐个决定是否已由稳定 helper/领域边界拥有。只修改 checker 的内部协议扫描，不借机重构页面图
加载器。

- [ ] **Step 4：运行完整 checker integration 与核心 unit**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/integration/test_checker.py" "$RUNTIME/tests/unit" -q
```

- [ ] **Step 5：提交 Task 6**

```bash
git add \
  plugins/draft/skills/draft/scripts/runtime/src/draft/checker.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_checker.py
git commit -m "fix(draft): 稳定 checker 内部目录读取失败"
```

---

### Task 7: 横切审计、完整门禁与修复记录

**Files:**

- Audit: `plugins/draft/skills/draft/scripts/runtime/src/draft/trash.py`
- Audit: `plugins/draft/skills/draft/scripts/runtime/src/draft/trash_lifecycle.py`
- Audit: `plugins/draft/skills/draft/scripts/runtime/src/draft/operations.py`
- Audit: `plugins/draft/skills/draft/scripts/runtime/src/draft/trash_package.py`
- Audit: `plugins/draft/skills/draft/scripts/runtime/src/draft/checker.py`
- Create: `docs/review-notes/2026-08-18-draft-candidate-root-incremental-migration-round-12-fix-record.md`

**Interfaces:**

- Consumes: Tasks 1–6 的状态表、恢复动作、checker 上下文和 I/O 边界。
- Produces: 可复现门禁证据和逐发现修复记录；只有审计先产生失败契约测试时才修改生产代码。

- [ ] **Step 1：按控制流复核 ABANDON 每个耐久写点**

从 R/T/锁的初始状态开始，依次在以下动作“已耐久后”停住并写下下一次 recover 的唯一动作：

```text
R rolled_back
T claim removed
first R recovery leaf removed
all R recovery leaves removed
lock removed
```

每一行必须能映射到 Task 1 的纯动作和 Task 2 的公开入口测试。若出现表外现场，先补失败测试，
再修表/入口；不要只在修复记录里解释。

- [ ] **Step 2：审计所有终态解锁入口是否先证明目标状态**

搜索 `release_lock` 的调用点，沿当前操作 kind/phase 回溯：终态页面事实由哪个 helper 证明、证明
失败保留什么。重点确认 R rolled_back matched/unclaimed 两条都经过同一 terminal verifier。

- [ ] **Step 3：审计 checker 的生产可达性**

对每个允许 evidence 的运行时阶段列出：锁 id、evidence 所属 operation id、journal phase、checker
选择的上下文。每一行必须有真实生产者 integration；不接受仅手改 phase 的夹具作为唯一证明。

- [ ] **Step 4：审计 missing/error 分离**

```bash
rg -n 'except OSError|except FileNotFoundError|return None|return False' \
  plugins/draft/skills/draft/scripts/runtime/src/draft/operations.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/trash_package.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/checker.py
```

只审计本轮改动经过的 inode identity、evidence/artifact 和 checker 内部目录边界。确认每个
None/False 只表示契约允许折叠的状态；不要扩散为全仓库重构。

- [ ] **Step 5：运行完整离线门禁**

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

uv run --project "$RUNTIME" --locked \
  ruff format --check "$RUNTIME/src" "$RUNTIME/tests"
uv run --project "$RUNTIME" --locked ruff check "$RUNTIME/src" "$RUNTIME/tests"
uv run --project "$RUNTIME" --locked pyright -p "$RUNTIME/pyproject.toml"
bash "$REPO_ROOT/plugins/draft/scripts/check-adapters.sh"
```

逐条记录退出码、passed/deselected 数量。任何失败先定位根因，不删测试、不放宽配置、不吞错。

- [ ] **Step 6：写第十二轮修复记录**

记录必须逐条包含：核验证据、违反的不变量、实际修复边界、真实回归测试、提交 id、完整门禁、
未运行网络测试和仍未验证事项。单独注明 H1/H2/H3 的计划责任，不写成执行 Agent独自引入。

至少给出三张最终表：

1. `R phase × T phase × link state × action`；
2. ABANDON 耐久写点 × 下一次 recover 动作；
3. runtime evidence owner × lock owner × checker context。

- [ ] **Step 7：检查工作树边界并提交记录（若约定记录不入版本控制，则不提交）**

```bash
git status --short
git diff --check
```

确认没有把用户的 `AGENTS.md`、既有 review notes 或无关文件纳入生产提交。按仓库当前约定，
`docs/review-notes/` 是临时未跟踪目录时，只保留本地记录，不强行 `git add -f`。

## Self-Review

- [x] 规格覆盖：H1/H2→Tasks 1–2；M1→Task 3；H3/L1→Task 4；H4→Task 5；M2→Task 6；
  横切与门禁→Task 7。
- [x] 没有把 `operations.py` 拆分列入本分支。
- [x] 没有扩展批准的故障/安全边界。
- [x] 每个生产任务都有先红后绿步骤、精确入口和失败现场断言。
- [x] 纯决策与真实 I/O 测试分层；真实 producer 测试不手改最终 journal 作为唯一正例。
- [x] 接口名称标为建议，硬性行为契约与实现预估已区分。
- [x] 未使用占位标记、空泛错误处理要求或未定义的后续步骤。

## 执行交接

该计划复杂且任务间存在顺序依赖，执行 Agent应使用
`superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans`，严格按
Task 1→7 执行。每个 Task 完成后先做契约复核再进入下一项；不得把后续任务的预期提前改成
当前实现行为。
