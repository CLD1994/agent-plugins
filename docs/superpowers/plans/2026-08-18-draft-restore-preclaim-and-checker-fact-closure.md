# Draft restore 前认领恢复与 checker 事实完整性闭合 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 restore 在取得锁但尚未认领 T 的真实崩溃现场可重复恢复，并让 checker 只有在 owner 事实完整时才判定孤儿，同时闭合内部协议节点的稳定 I/O 边界。

**Architecture:** restore 侧先在唯一纯决策表中增加“严格 pre-claim bootstrap”维度与独立动作，再让异常清理和公开恢复共享同一 bootstrap 判定；checker 侧把目录扫描完整性、operation 索引完整性和 package ownership 三值结论显式建模，形状检查与依赖 owner 事实的检查分开。所有正向恢复测试使用真实 producer 写点，纯函数测试从状态表独立推导。

**Tech Stack:** Python 3.12、pytest、uv、Ruff、Pyright strict。

**Spec:** `docs/review-notes/2026-08-18-draft-candidate-root-incremental-migration-round-13.md`

## Global Constraints

- 已确认行为契约、状态表、失败语义和验收条件是硬性要求；下文建议的私有类型名、helper 名与文件内位置是实现预估，若实际数据流要求调整，可以调整，但不得改变协议。
- 当前威胁模型是本地协作式单写者、普通 I/O 失败和进程中断；不扩展到 NFS、机器掉电、恶意 `.draft/` 篡改或不可消除的 POSIX 调用间隙。
- 不在本分支拆分 `operations.py`，不进行与三个 finding 无关的重构。
- 每项先写可证伪红灯测试，再写最小实现；正例崩溃状态必须由真实 `restore_retained_trash` 生产，不得只手改最终 journal。
- UNKNOWN（事实未知）不得解释为 ORPHAN、MISSING、成功或无关；无法证明安全时保留现场和既有稳定诊断。
- checker 始终只读；修复不得发布 pending、恢复 journal、消费 evidence 或删除任何条目。
- 不新增错误码。若现有码无法准确表达，停止该任务并向评审者提交协议变更证据，不得自行选择。
- `operations.py` 的模块拆分是后续事项，本计划不再评审或实施。

## 行为契约一：restore pre-claim 状态表

`preclaim_bootstrap` 不是“mutation 当前都等于 before”的同义词。它只在 R 仍是严格初始日志时为
true：

```python
R.kind is OperationKind.RESTORE
R.phase is OperationPhase.ALLOCATING
R.observations.entries == ()
R.mutations == ()
R.remove_empty_directories == ()
R.metadata == {"restores_trash_operation": T.operation_id}
```

新增行与既有行的关系：

| R phase | T phase | link state | preclaim_bootstrap | proven_unapplied | 动作 |
|---|---|---|---:|---:|---|
| `allocating` | `trash_retained` | `TRASH_UNCLAIMED` | true | 不读取 | `ABANDON_UNCLAIMED_BOOTSTRAP` |
| `rolled_back` | `trash_retained` | `TRASH_UNCLAIMED` | false | 不读取 | 既有 `FINISH_ABANDON_CLEANUP` |
| `allocating` | `trash_retained` | `TRASH_UNCLAIMED` | false | 任意 | `INCONSISTENT` |
| `allocating/prepared` | `trash_retained` | `MATCHED` | 任意 | true | 既有 `ABANDON_UNAPPLIED` |
| 其他既有合法行 | 依第十二轮表 | 依第十二轮表 | false | 按 phase 采集 | 保持原动作 |
| 未列组合 | 任意 | 任意 | 任意 | 任意 | `INCONSISTENT` |

新动作的副作用顺序固定为：

```text
锁内重读并复核 R/T + bootstrap
  -> 耐久写 R=rolled_back
  -> 幂等清理 R 登记材料
  -> 释放 R 锁
```

它不得写 T、加载 payload、删除 package。R=`rolled_back` 耐久后中断，下一次公开恢复必须复用
既有 `FINISH_ABANDON_CLEANUP`。

## 行为契约二：checker package ownership 三值表

建议使用受约束枚举：

```python
class PackageOwnership(StrEnum):
    HELD = "held"
    ORPHAN = "orphan"
    UNKNOWN = "unknown"
```

每个 package id 独立判定：

| operations/同 id T/相关 R/lock 事实 | T 状态与关系 | 结论 | orphan 诊断 |
|---|---|---|---|
| operations 明确缺失，或完整枚举且同 id operation 明确不存在 | — | ORPHAN | 是 |
| 同 id T 可读且为 `trash_retained` / `trash_handoff_pending` | — | HELD | 否 |
| 同 id T=`rolled_back`，lock、R、双向关系完整且证明 cleanup owner | matched cleanup | HELD | 否 |
| 完整事实证明同 id journal/phase 不拥有包 | — | ORPHAN | 是 |
| operations 根不可读/枚举失败 | 任意 | UNKNOWN | 否 |
| 同 id operation/journal 不可读或无法解析 | 任意 | UNKNOWN | 否 |
| T=`rolled_back` 且 lock 不可读，或 claim 指向的 R 不可读 | 可能是 cleanup owner | UNKNOWN | 否 |

UNKNOWN 仍保留导致未知的既有 operation/journal/lock 诊断。package 自身的非法名字、节点类型和
符号链接形状不依赖 owner 事实，必须继续报告。

---

### Task 1: 扩展 restore 纯状态模型与 bootstrap 唯一判定

**Files:**

- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/trash.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_trash.py`

**Interfaces:**

- Consumes: 现有 `Journal`、`RestoreLinkState`、`RestoreRecoveryAction`、`restore_recovery_decision`。
- Produces（建议签名）：
  `is_restore_preclaim_bootstrap(restore: Journal, trash_id: str) -> bool`；
  `RestoreRecoveryAction.ABANDON_UNCLAIMED_BOOTSTRAP`；
  `restore_recovery_decision(..., preclaim_bootstrap: bool, proven_unapplied: bool)`。

- [ ] **Step 1：写 bootstrap 形状纯函数红灯矩阵**

在 `tests/unit/test_trash.py` 构造一个最小合法 R，然后每次只改变一个维度：phase、kind、
observations、mutations、remove dirs、metadata 缺字段、metadata 多字段、错误 T id。预期独立写成：

```python
@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ("none", True),
        ("prepared", False),
        ("observation", False),
        ("mutation", False),
        ("remove_dir", False),
        ("missing_backref", False),
        ("extra_metadata", False),
        ("different_trash", False),
    ],
)
def test_restore_preclaim_bootstrap_shape(change: str, expected: bool) -> None:
    restore = make_restore_bootstrap(change)
    assert is_restore_preclaim_bootstrap(restore, TRASH_ID) is expected
```

测试 helper 只负责构造输入，不得调用被测 predicate 计算 expected。

- [ ] **Step 2：运行纯函数红灯**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_trash.py" -q -k 'preclaim_bootstrap_shape'
```

预期：因 predicate 尚不存在或返回错误而失败。

- [ ] **Step 3：实现严格 bootstrap predicate**

实现只读取 `Journal`，不访问文件系统；metadata 必须精确相等，不能用“包含 backref 即可”：

```python
def is_restore_preclaim_bootstrap(restore: Journal, trash_id: str) -> bool:
    return (
        restore.kind is OperationKind.RESTORE
        and restore.phase is OperationPhase.ALLOCATING
        and restore.observations.entries == ()
        and restore.mutations == ()
        and restore.remove_empty_directories == ()
        and restore.metadata == {"restores_trash_operation": trash_id}
    )
```

- [ ] **Step 4：写恢复决策表红灯**

扩展现有完整表，不只加一个例子。至少显式覆盖：

```python
cases = [
    # 新合法行
    (ALLOCATING, TRASH_RETAINED, TRASH_UNCLAIMED, True, False,
     ABANDON_UNCLAIMED_BOOTSTRAP),
    # 同 phase/link 但不再是 bootstrap，必须拒绝
    (ALLOCATING, TRASH_RETAINED, TRASH_UNCLAIMED, False, True, INCONSISTENT),
    # 既有放弃后半段
    (ROLLED_BACK, TRASH_RETAINED, TRASH_UNCLAIMED, False, False,
     FINISH_ABANDON_CLEANUP),
    # prepared/mid/completed unclaimed 均拒绝
    (PREPARED, TRASH_RETAINED, TRASH_UNCLAIMED, False, True, INCONSISTENT),
    (APPLYING, TRASH_RETAINED, TRASH_UNCLAIMED, False, False, INCONSISTENT),
    (COMPLETED, TRASH_RETAINED, TRASH_UNCLAIMED, False, False, INCONSISTENT),
]
```

原完整 `R phase × T phase × link state` 表必须加上新维度后继续全部通过；不得用新特判改变
MATCHED 或 CONFLICT 既有结果。

- [ ] **Step 5：实现唯一纯决策新增行**

先拒绝 CONFLICT；再只在 `TRASH_UNCLAIMED` 分支读取 `preclaim_bootstrap`：

```python
if link_state is RestoreLinkState.TRASH_UNCLAIMED:
    if pair == (OperationPhase.ALLOCATING, OperationPhase.TRASH_RETAINED):
        return (
            RestoreRecoveryAction.ABANDON_UNCLAIMED_BOOTSTRAP
            if preclaim_bootstrap
            else RestoreRecoveryAction.INCONSISTENT
        )
    if pair == (OperationPhase.ROLLED_BACK, OperationPhase.TRASH_RETAINED):
        return RestoreRecoveryAction.FINISH_ABANDON_CLEANUP
    return RestoreRecoveryAction.INCONSISTENT
```

- [ ] **Step 6：更新所有调用点到可类型检查状态并运行 unit**

调用点暂时按当前 R 计算 predicate；不要在此任务加入副作用动作。

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit/test_trash.py" -q
uv run --project "$RUNTIME" --locked pyright -p "$RUNTIME/pyproject.toml"
```

预期：unit 与类型检查通过。

- [ ] **Step 7：提交 Task 1**

```bash
git add \
  plugins/draft/skills/draft/scripts/runtime/src/draft/trash.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_trash.py
git commit -m "fix(draft): model restore preclaim bootstrap state"
```

---

### Task 2: 让两个公开恢复入口收敛 pre-claim 崩溃现场

**Files:**

- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/trash.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/trash_lifecycle.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash.py`

**Interfaces:**

- Consumes: Task 1 的 `is_restore_preclaim_bootstrap` 与
  `ABANDON_UNCLAIMED_BOOTSTRAP`。
- Produces: 公开 `recover_draft` 与 `recover_interrupted_operations` 对 pre-claim 现场的幂等收敛。

- [ ] **Step 1：写真实 producer 的“认领前退出”红灯夹具**

复用真实 `trash_page` 和 `restore_retained_trash`。只 patch `draft.trash.write_journal`：当第一次
收到 kind=`TRASH` 且 metadata 新增 `restore_operation_id` 时，在调用真实 writer **之前**抛出
`BaseException` 子类。断言现场来自真实写序：

```python
assert read_lock(root) == restore_id
assert read_journal(r_path).phase is OperationPhase.ALLOCATING
assert read_journal(r_path).mutations == ()
assert read_journal(t_path).phase is OperationPhase.TRASH_RETAINED
assert "restore_operation_id" not in read_journal(t_path).metadata
```

测试不得手改 R/T 最终 JSON。

- [ ] **Step 2：参数化两个公开入口并确认红灯**

```python
@pytest.mark.parametrize("entry_name", ["recover_draft", "recover_interrupted_operations"])
def test_restore_preclaim_crash_converges(entry_name: str, ...) -> None:
    root, restore_id, trash_id = preclaim_crash_scene(...)
    before_t = operation_journal_path(root, trash_id).read_bytes()
    before_package = package_snapshot(root, trash_id)

    recover_through_public_entry(root, entry_name)

    assert read_lock(root) is None
    assert read_journal(operation_journal_path(root, restore_id)).phase is ROLLED_BACK
    assert operation_journal_path(root, trash_id).read_bytes() == before_t
    assert package_snapshot(root, trash_id) == before_package
```

运行：

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/integration/test_trash.py" -q -k 'preclaim_crash'
```

预期：当前实现报 `operation.inconsistent_state`。

- [ ] **Step 3：让异常清理与公开恢复共享 bootstrap predicate**

修改 `abandon_unlinked_restore`：只有 Task 1 predicate 为 true 才能把未认领 R 转成 rolled_back；
不要继续保留一份较宽的 `phase is ALLOCATING and not mutations` 规则。若该 helper 无法取得 T id，
从 R metadata 经过 `validate_operation_id` 得到，并在形状不满足时零写入失败。

- [ ] **Step 4：在生命周期事实采集处传入 bootstrap 事实**

`_recover_locked_restore` 在 `load_restore_recovery_facts` 完成后，从同一份 `facts.restore` 与
`facts.trash.operation_id` 计算 predicate，传给纯决策。不要额外重读文件，也不要在纯决策函数
里做 I/O。

- [ ] **Step 5：实现新动作的固定副作用顺序**

在 `resolve_restore_execution` 增加独立分支：

```python
if action is RestoreRecoveryAction.ABANDON_UNCLAIMED_BOOTSTRAP:
    # resolver 已重读 journal；再次验证锁、R bootstrap 与 T unclaimed，防止 stale action。
    current_facts_valid = (
        read_lock(root) == restore_id
        and current_trash.kind is OperationKind.TRASH
        and current_trash.phase is OperationPhase.TRASH_RETAINED
        and current_trash.metadata.get("restore_operation_id") is None
        and is_restore_preclaim_bootstrap(terminal_restore, current_trash.operation_id)
    )
    if not current_facts_valid:
        raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, terminal_restore.directory)
    terminal_restore = with_phase(terminal_restore, OperationPhase.ROLLED_BACK)
    write_journal(terminal_restore)
    clean_recovery_copies(terminal_restore)
    release_lock(root, restore_id)
    return OperationResult(... phase=OperationPhase.ROLLED_BACK ...)
```

此分支不得调用 `without_metadata(current_trash, ...)`，不得调用 `load_verified_package` 或
`clean_authoritative_package`。写 R 后的任何失败都由现有 rolled_back+unclaimed FINISH 行收敛。

- [ ] **Step 6：写“R rolled_back 已耐久后再次退出”的红灯/绿灯测试**

在新动作的 `write_journal(R=rolled_back)` 调用真实 writer 后抛 `BaseException`。下一次通过两个
公开入口恢复，断言走现有 FINISH：T bytes 不变、包完整、R 材料清空、锁释放。重复 recover
再次调用应无副作用。

- [ ] **Step 7：写非 bootstrap 反例集成测试**

非法状态可以手工构造，但每个只改变一个维度：unclaimed R allocating 带 observation、带
mutation、带 remove dir、带额外 metadata。调用两个公开入口，断言：

```python
with pytest.raises(DraftError, match=CheckCode.OPERATION_INCONSISTENT_STATE.value):
    recover_entry(root)
assert snapshot_tree(root) == before
assert read_lock(root) == restore_id
```

- [ ] **Step 8：运行 restore 定向测试和核心 unit**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/integration/test_trash.py" \
  "$RUNTIME/tests/unit/test_trash.py" -q \
  -k 'preclaim or abandon or restore_recovery or bootstrap'
uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit" -q
```

- [ ] **Step 9：提交 Task 2**

```bash
git add \
  plugins/draft/skills/draft/scripts/runtime/src/draft/trash.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/trash_lifecycle.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash.py
git commit -m "fix(draft): recover restore preclaim crash state"
```

---

### Task 3: 显式传播 checker operation 事实完整性并作三值 ownership 决策

**Files:**

- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/checker.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_checker.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_checker.py`

**Interfaces:**

- Consumes: 现有 `_list_internal_directory`、operation journal/lock 解析、cleanup owner 关系。
- Produces（建议）：`InternalDirectoryState`、`OperationCheckIndex`、`PackageOwnership` 与纯
  `package_ownership_decision(package_id, index) -> PackageOwnership`；`_check_trash_tree` 只消费
  同一份 index，不重新读取 journals。

- [ ] **Step 1：写纯 ownership 决策矩阵红灯**

在 `tests/unit/test_checker.py` 独立构造最小 index facts，至少覆盖以下输入/输出：

```python
cases = [
    ("operations_missing", "no_same_id", ORPHAN),
    ("operations_unavailable", "unknown", UNKNOWN),
    ("complete", "retained_t", HELD),
    ("complete", "pending_t", HELD),
    ("complete", "no_same_id", ORPHAN),
    ("complete", "same_id_unreadable", UNKNOWN),
    ("complete", "rolled_back_cleanup_matched", HELD),
    ("complete", "rolled_back_lock_unreadable", UNKNOWN),
    ("complete", "rolled_back_r_unreadable", UNKNOWN),
    ("complete", "rolled_back_no_lock", ORPHAN),
    ("complete", "wrong_kind_or_phase", ORPHAN),
]
```

预期不得由生产函数反向生成。

- [ ] **Step 2：运行 unit 红灯**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_checker.py" -q -k 'package_ownership'
```

- [ ] **Step 3：实现显式扫描/索引数据结构**

建议结构（名称可调整，语义不可压回 `None`/空 set）：

```python
class InternalDirectoryState(StrEnum):
    MISSING = "missing"
    COMPLETE = "complete"
    INVALID = "invalid"
    UNAVAILABLE = "unavailable"

@dataclass(frozen=True)
class InternalDirectoryListing:
    state: InternalDirectoryState
    entries: tuple[Path, ...]

@dataclass(frozen=True)
class OperationCheckIndex:
    listing_state: InternalDirectoryState
    journals: Mapping[str, Journal]
    unresolved_ids: frozenset[str]
    locked_operation: str | None
    lock_known: bool
```

`_list_internal_directory` 必须保留已经追加的稳定 issue，同时返回状态；MISSING 与 I/O failure
不得再同为 None。

- [ ] **Step 4：建立一次性 operation index**

第一遍枚举时：

- 成功解析的 journal 只放入 `journals`；
- EACCES/EIO/无法读取或解析且目录名是合法 id 时放入 `unresolved_ids`；
- 已明确是普通文件/链接的 operation 条目是已知非法形状，不当作 I/O unknown；
- `read_lock` 失败时 `lock_known=False`，而不是只设置 `locked_operation=None`；
- cleanup owner 从该 index 派生，不重新读文件。

- [ ] **Step 5：实现纯三值 ownership 并让 trash tree 复用 index**

`_check_trash_tree` 对 package 先做名字/节点类型/链接检查；只有形状合法后才调用纯 ownership。
仅在 ORPHAN 时追加 orphan 的 `internal.unexpected_entry`：

```python
ownership = package_ownership_decision(package.name, index)
if ownership is PackageOwnership.ORPHAN:
    issues.append(_error(CheckCode.INTERNAL_UNEXPECTED_ENTRY, relative))
```

删除 `_check_trash_tree` 内第二遍 `read_journal` 循环。UNKNOWN 不新增码，也不吞掉第一遍已记录的
operation/journal/lock 错误。

- [ ] **Step 6：写真实 retained package 的 I/O 集成矩阵**

使用真实 `trash_page`，保留合法 T/package；分别对 operations 根 lstat/scandir、同 id operation
目录分类、同 id journal 读取注入 EACCES/EIO。断言：

```python
assert stable_operation_issue(report)
assert not package_has_orphan_issue(report, trash_id)
assert snapshot_tree(root) == before
```

不能只断言“report.errors 非空”。必须精确断言合法 package 没有 orphan 诊断。

- [ ] **Step 7：写 cleanup owner 未知与局部精度测试**

由真实 restore cleanup producer 创建 R 持锁、T rolled_back 的现场：

- lock 读取 EIO；
- R journal 读取 EIO；
- 两个 packages：一个同 id owner unknown，另一个没有任何同 id operation 且事实完整。

前两者不误报对应 package orphan；第三个用例只报告可证明 orphan 的 package，证明实现没有因
一个 unknown 全局关闭所有 orphan 检查。

- [ ] **Step 8：写独立形状检查测试**

在 operations facts unavailable 时，outgoing 下的非法 id 目录、普通文件、符号链接仍分别报告
既有形状诊断。不要把“无法判 owner”扩大成“不检查 package 自身”。

- [ ] **Step 9：运行 checker unit/integration**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_checker.py" \
  "$RUNTIME/tests/integration/test_checker.py" -q
```

- [ ] **Step 10：提交 Task 3**

```bash
git add \
  plugins/draft/skills/draft/scripts/runtime/src/draft/checker.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_checker.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_checker.py
git commit -m "fix(draft): preserve checker ownership completeness"
```

---

### Task 4: 闭合 checker 内部节点的稳定分类边界

**Files:**

- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/checker.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_checker.py`

**Interfaces:**

- Consumes: Task 3 的显式目录扫描结果，以及现有 `classify_node_or_error`。
- Produces: `_trash_package_issue` 与 `_check_internal_namespace` 不再通过原始 `Path.is_*` 泄漏
  EACCES/EIO。

- [ ] **Step 1：写权威 package 节点分类红灯矩阵**

真实 retained T/package 上，分别注入 package 节点 stat 的 EACCES/EIO；再覆盖 package
MISSING、FILE、DIRECTORY、SYMLINK。精确预期：

```text
MISSING       -> trash.package_missing
DIRECTORY     -> load_verified_package 的现有验证结果
FILE/SYMLINK  -> trash.package_corrupt（symlink 若现有协议要求更精确码则沿既有权威码）
EACCES/EIO    -> 稳定 CheckIssue，不抛原始异常，不声称 package_missing
```

运行并确认当前 EACCES/EIO 从 `package_dir.is_dir()` 原样抛出。

- [ ] **Step 2：用共享分类替换 `_trash_package_issue` 的 raw Path 查询**

只让 ENOENT 进入 MISSING 分支。分类 DraftError 转成该 package/journal 边界的稳定、根内相对
CheckIssue；不要用 `except OSError: package_missing`。

- [ ] **Step 3：写 `.draft` child 第二次 stat 红灯矩阵**

让 `.draft` 本身稳定枚举成功，只在枚举后的 `draft.json`、`operations`、`trash` child 分类时注入
EACCES/EIO。当前应从 `entry.is_symlink/is_dir` 泄漏；修复后返回稳定根内相对诊断。

- [ ] **Step 4：让 `_check_internal_namespace` 一次稳定分类每个 child**

对每个 child 调用 `classify_node_or_error` 一次并根据 `EntryKind` 判断；不得再调用
`entry.is_symlink()` 或 `entry.is_dir()`：

```python
try:
    kind = classify_node_or_error(entry, code=..., code_path=entry)
except DraftError as exc:
    issues.append(issue_from_draft_error(root, exc, fallback=relative))
    continue
if kind is EntryKind.SYMLINK:
    ...
elif relative in _ALLOWED_INTERNAL_DIRECTORIES and kind is not EntryKind.DIRECTORY:
    ...
```

- [ ] **Step 5：完成 raw-call 审计并记录每个剩余调用的所有者**

```bash
rg -n '\.iterdir\(|\.is_dir\(|\.is_file\(|\.exists\(|\.is_symlink\(' \
  plugins/draft/skills/draft/scripts/runtime/src/draft/checker.py
```

对输出逐项分类：本任务内部协议调用必须消除或置于稳定领域边界；页面图/根布局调用若不修改，
在 round-13 fix record 写出函数名及“由哪个既有边界稳定化”的证据。不得用“已审计，无问题”代替
逐项记录。

- [ ] **Step 6：运行 checker 全集与 CLI check 定向测试**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/integration/test_checker.py" \
  "$RUNTIME/tests/unit/test_cli.py" -q
```

所有 I/O 用例还必须断言 checker 前后树快照不变。

- [ ] **Step 7：提交 Task 4**

```bash
git add \
  plugins/draft/skills/draft/scripts/runtime/src/draft/checker.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_checker.py
git commit -m "fix(draft): close checker internal node io boundaries"
```

---

### Task 5: 横切审计、完整门禁与第十三轮修复记录

**Files:**

- Audit: `plugins/draft/skills/draft/scripts/runtime/src/draft/trash.py`
- Audit: `plugins/draft/skills/draft/scripts/runtime/src/draft/trash_lifecycle.py`
- Audit: `plugins/draft/skills/draft/scripts/runtime/src/draft/checker.py`
- Create: `docs/review-notes/2026-08-18-draft-candidate-root-incremental-migration-round-13-fix-record.md`

**Interfaces:**

- Consumes: Tasks 1–4 的最终代码与测试。
- Produces: 可独立复核的修复记录和全量离线门禁证据。

- [ ] **Step 1：沿真实 restore producer 审计每个 claim 前后耐久点**

从 `preallocate_journal` 开始，依次记录：R journal、锁、T claim pending/final、R plan/phase、R
rolled_back、T unclaim、R 材料清理、解锁。为每个点写出“下一次公开 recover 的唯一动作”和现有
测试名。至少必须包含：

```text
R allocating + T unclaimed + lock R -> ABANDON_UNCLAIMED_BOOTSTRAP
R allocating/prepared + T matched + proven before -> ABANDON_UNAPPLIED
R rolled_back + T unclaimed + lock R -> FINISH_ABANDON_CLEANUP
```

任何没有唯一动作的程序写出状态都不是“后置事项”，应回到 Task 1/2 修复。

- [ ] **Step 2：审计 checker 依赖图而非只审计目录树**

在修复记录列出每项诊断所需事实：operation 根列表、同 id T、相关 R、lock、package 节点、package
内容。标明事实不完整时哪些诊断仍独立可判、哪些必须 UNKNOWN。确认 trash tree 没有第二次读取
journal 建立另一份 owner 清单。

- [ ] **Step 3：运行完整离线门禁**

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
RUNTIME="$REPO_ROOT/plugins/draft/skills/draft/scripts/runtime"

uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit" -q
uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/integration" -q -m 'not network'
uv run --project "$RUNTIME" --locked pytest \
  "$REPO_ROOT/plugins/draft/scripts/tests/unit" -q
uv run --project "$RUNTIME" --locked pytest \
  "$REPO_ROOT/plugins/draft/scripts/tests/integration" -q -m 'not network'
uv run --project "$RUNTIME" --locked ruff format --check "$RUNTIME/src" "$RUNTIME/tests"
uv run --project "$RUNTIME" --locked ruff check "$RUNTIME/src" "$RUNTIME/tests"
uv run --project "$RUNTIME" --locked pyright -p "$RUNTIME/pyproject.toml"
bash "$REPO_ROOT/plugins/draft/scripts/check-adapters.sh"
git diff --check
```

任何失败先定位根因，不删测试、不放宽配置、不吞错误。网络测试未运行就明确写“未运行”，不得写
“全量门禁全部通过”。

- [ ] **Step 4：编写第十三轮修复记录**

修复记录必须逐条对应 H1/M1/M2，包含：红灯证据、被违反不变量、实际修复边界、回归测试名、
提交号、独立门禁数字、未验证事项和归因。另附：

- pre-claim/abandon 完整耐久写点表；
- package ownership 三值表及 UNKNOWN 的诊断行为；
- checker raw-call 审计逐项结果。

不要把本实施计划全文复制进 fix record。

- [ ] **Step 5：确认工作树只含用户已有文件与评审临时记录**

```bash
git status --short
git log --oneline --decorate -12
```

不得提交用户的 `AGENTS.md` 改动或既有未跟踪计划/评审文件，除非用户另行授权。

## 自审结果

- **规格覆盖：** H1 由 Tasks 1–2 覆盖；M1 由 Task 3 覆盖；M2 由 Task 4 覆盖；横切写点、
  依赖事实与门禁由 Task 5 覆盖。
- **状态覆盖：** pre-claim 与 post-abandon 的同值 `TRASH_UNCLAIMED` 已由 phase+bootstrap 分开；
  checker 的 HELD/ORPHAN/UNKNOWN 与扫描 MISSING/COMPLETE/INVALID/UNAVAILABLE 均有明确结果。
- **类型一致：** `preclaim_bootstrap`、`ABANDON_UNCLAIMED_BOOTSTRAP`、`PackageOwnership` 在生产与
  测试中的名称一致；它们是建议内部名，若调整必须整份计划同步调整后再执行。
- **禁止捷径：** 计划明确禁止复用宽泛 `proven_unapplied`、UNKNOWN 当 ORPHAN、二次重读 owner
  journals、raw `Path.is_*` 逃逸、手改正例 journal 和新增未批准错误码。
