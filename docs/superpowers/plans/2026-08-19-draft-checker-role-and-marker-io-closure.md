# Draft checker 角色与 marker I/O 闭合实施计划

> **供执行 Agent：** 必须使用 `superpowers:subagent-driven-development`（推荐）或
> `superpowers:executing-plans`，按任务逐项实施。所有步骤使用 checkbox（`- [ ]`）跟踪；不得跳过
> 红灯、阶段门禁或横切审计。

**目标：** 让 checker 在 operation 角色事实不完整时不产生伪终态残留诊断，并让 marker 的普通
身份读取失败稳定转换为既有机器诊断。

**架构：** checker 从现有 `OperationCheckIndex` 派生 `ACTIVE / INACTIVE / UNKNOWN` 三值目录角色，
package ownership 与 operation 目录协议复用同一份事实；marker 在 `graph._read_marker_file` 内通过
一次受保护的身份分类建立共享稳定边界。两个子任务彼此独立，均先用可证伪测试固定行为，再修改
最窄共同责任边界。

**技术栈：** Python 3.12、pytest、Ruff、Pyright strict、uv。

**规格：**
`docs/review-notes/2026-08-19-draft-candidate-root-incremental-migration-round-14.md`

## 全局约束

- 当前保障边界是本地协作式单写者、普通 I/O 失败和进程中断；不得扩展到 NFS、机器掉电、恶意
  `.draft/` 篡改或不可消除的 POSIX 系统调用间隙。
- 本轮不拆分 `operations.py`，不做无关重构，不引入新依赖。
- 不新增错误码；复用 `operation.journal_corrupt`、`internal.unexpected_entry`、`format.missing`、
  `format.invalid` 与 `path.symlink_escape`。
- UNKNOWN 不得解释为 INACTIVE、ORPHAN、MISSING、成功或无关；同时不得成为关闭独立形状检查的
  全局开关。
- 只有 ENOENT 可以解释为 marker missing；EACCES/EIO 必须稳定失败，不能原样逃逸。
- 行为契约、状态表和项目规则是硬约束；下面的内部类型名和文件行号是实现建议。若实际数据流要求
  改名可以调整，但不得改变状态分区。遇到计划未定义状态或需要改变机器协议时停止并报告证据。
- 每项修复先写能在当前基线失败的最窄测试，再实现，再运行定向门禁；不得删除测试、放宽配置或
  吞掉错误。
- 不提交用户已有的 `AGENTS.md` 改动、`docs/review-notes/` 临时记录或现有未跟踪计划，除非用户另行
  授权。生产提交只包含本任务代码与测试。

---

## 行为契约一：终态 operation 目录角色表

建议新增：

```python
class OperationDirectoryRole(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    UNKNOWN = "unknown"
```

对已经成功解析的终态 journal，从同一 `OperationCheckIndex` 判定：

| `lock_known` | `locked_operation` | 当前 operation 与 R/T 事实 | 角色 |
|---|---|---|---|
| `False` | 任意 | 任意终态 operation 可能是锁持有者或 cleanup owner | `UNKNOWN` |
| `True` | `None` | 任意终态 operation | `INACTIVE` |
| `True` | 当前 id | 当前 operation 是锁持有者 | `ACTIVE` |
| `True` | R id | 当前 T=`rolled_back`，T→R、R=`completed restore`、R→T 完整 | `ACTIVE` |
| `True` | R id | 当前 T claim 指向 R，但 R id 在 `unresolved_ids` | `UNKNOWN` |
| `True` | 其他 id | 完整事实证明当前 operation 与锁及 cleanup 关系无关 | `INACTIVE` |

非终态 journal 继续按既有 phase 协议决定允许条目，不由该表放宽。

终态条目协议：

| 角色 | 允许集合 | 诊断规则 |
|---|---|---|
| `ACTIVE` | 既有 `_OP_DIR_ALLOWED_ENTRIES` | 登记恢复材料可存在；未知名字/链接/类型/内容损坏仍报告 |
| `INACTIVE` | 既有 `_OP_DIR_TERMINAL_ALLOWED` | active-only 材料属于终态残留并报告 |
| `UNKNOWN` | ACTIVE 与 INACTIVE 集合的并集 | 不因登记材料存在报告残留；仍报告两种角色都不允许的名字及与角色无关的形状/内容损坏 |

不得用“lock 不可读时跳过全部 operation 目录检查”实现 UNKNOWN。

## 行为契约二：marker 身份分类表

| `lstat` / 节点结果 | `read_marker` 结果 |
|---|---|
| ENOENT | `DraftError('format.missing', marker)` |
| symlink | `DraftError('path.symlink_escape', marker)` |
| directory 或其他非普通文件 | `DraftError('format.invalid', marker)` |
| EACCES/EIO 等其他 `OSError` | `DraftError('format.invalid', marker)` |
| 普通文件 + 非 UTF-8/非法 JSON/非法 schema | 既有 `format.invalid` / `format.unsupported` 语义 |
| 普通有效文件 | 返回既有 `MarkerState` |

---

### Task 1: 建立 operation 目录角色三值模型

**Files:**

- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/checker.py:469-880`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_checker.py`

**Interfaces:**

- Consumes: 现有 `OperationCheckIndex`、`InternalDirectoryState`、`OperationKind`、
  `OperationPhase`。
- Produces（建议固定）：
  `OperationDirectoryRole` 与
  `operation_directory_role(operation_id: str, index: OperationCheckIndex) -> OperationDirectoryRole`。
  Task 2 不依赖此接口；Task 3 横切审计会检查它的全部消费者。

- [ ] **Step 1：写纯角色决策红灯表**

在 `tests/unit/test_checker.py` 的 `draft.checker` import 中加入 Task 1 新类型与纯函数，复用现有
`_op_journal` / `_ownership_index` 夹具，独立列出预期，不得调用生产函数生成 expected。至少覆盖：

```python
trash_id = "a" * 32
restore_id = "b" * 32
other_id = "c" * 32
completed_restore = _op_journal(
    restore_id,
    kind=OperationKind.RESTORE,
    phase=OperationPhase.COMPLETED,
    metadata={"restores_trash_operation": trash_id},
)
rolled_back_trash = _op_journal(
    trash_id,
    kind=OperationKind.TRASH,
    phase=OperationPhase.ROLLED_BACK,
    metadata={"restore_operation_id": restore_id},
)
other_completed = _op_journal(
    other_id,
    kind=OperationKind.CREATE,
    phase=OperationPhase.COMPLETED,
)
lock_unknown = _ownership_index(
    journals={trash_id: rolled_back_trash, restore_id: completed_restore},
    lock_known=False,
)
known_no_lock = _ownership_index(
    journals={restore_id: completed_restore},
    locked_operation=None,
    lock_known=True,
)
complete_matched_pair = _ownership_index(
    journals={trash_id: rolled_back_trash, restore_id: completed_restore},
    locked_operation=restore_id,
    lock_known=True,
)
locked_r_unresolved = _ownership_index(
    journals={trash_id: rolled_back_trash},
    unresolved_ids=(restore_id,),
    locked_operation=restore_id,
    lock_known=True,
)
lock_is_restore = _ownership_index(
    journals={restore_id: completed_restore, other_id: other_completed},
    locked_operation=restore_id,
    lock_known=True,
)
cases = [
    ("lock_unknown_completed_r", restore_id, lock_unknown, OperationDirectoryRole.UNKNOWN),
    ("lock_unknown_rolled_back_t", trash_id, lock_unknown, OperationDirectoryRole.UNKNOWN),
    ("known_no_lock_terminal", restore_id, known_no_lock, OperationDirectoryRole.INACTIVE),
    ("locked_operation_itself", restore_id, lock_is_restore, OperationDirectoryRole.ACTIVE),
    ("matched_cleanup_t", trash_id, complete_matched_pair, OperationDirectoryRole.ACTIVE),
    ("claimed_r_unresolved", trash_id, locked_r_unresolved, OperationDirectoryRole.UNKNOWN),
    ("known_unrelated_terminal", other_id, lock_is_restore, OperationDirectoryRole.INACTIVE),
]
for label, operation_id, index, expected in cases:
    assert operation_directory_role(operation_id, index) is expected, label
```

每个 index 必须显式给出 `lock_known`、`locked_operation`、`journals` 与 `unresolved_ids`，不要用
一个含糊布尔值代替组合事实。

- [ ] **Step 2：运行纯函数测试并确认红灯原因**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_checker.py" -q -k 'operation_directory_role'
```

预期：因角色枚举/函数尚不存在而失败，或当前 `active_ids` 无法表达 UNKNOWN；不能以夹具错误作为
红灯证据。

- [ ] **Step 3：实现纯角色判定**

在 `checker.py` 中从 `OperationCheckIndex` 派生角色。建议控制流如下，语义不得改变：

```python
def operation_directory_role(
    operation_id: str,
    index: OperationCheckIndex,
) -> OperationDirectoryRole:
    journal = index.journals.get(operation_id)
    if journal is None:
        return OperationDirectoryRole.UNKNOWN
    if not index.lock_known:
        return OperationDirectoryRole.UNKNOWN
    locked = index.locked_operation
    if locked is None:
        return OperationDirectoryRole.INACTIVE
    if locked == operation_id:
        return OperationDirectoryRole.ACTIVE
    if (
        journal.kind is OperationKind.TRASH
        and journal.phase is OperationPhase.ROLLED_BACK
        and journal.metadata.get("restore_operation_id") == locked
    ):
        if locked in index.unresolved_ids:
            return OperationDirectoryRole.UNKNOWN
        restore = index.journals.get(locked)
        if (
            restore is not None
            and restore.kind is OperationKind.RESTORE
            and restore.phase is OperationPhase.COMPLETED
            and restore.metadata.get("restores_trash_operation") == operation_id
        ):
            return OperationDirectoryRole.ACTIVE
    return OperationDirectoryRole.INACTIVE
```

该函数只对成功解析的 journal 被调用；缺 journal 返回 UNKNOWN 是防御性总函数语义，不得据此
扫描或写文件。

- [ ] **Step 4：运行纯函数测试并确认通过**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_checker.py" -q -k 'operation_directory_role'
```

- [ ] **Step 5：提交 Task 1**

```bash
git add \
  plugins/draft/skills/draft/scripts/runtime/src/draft/checker.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_checker.py
git commit -m "fix(draft): model checker operation directory roles"
```

---

### Task 2: 让 operation 目录检查消费三值角色

**Files:**

- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/checker.py:613-880`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_checker.py:1819-2310`

**Interfaces:**

- Consumes: Task 1 的 `OperationDirectoryRole` 与 `operation_directory_role`；现有一次性
  `OperationCheckIndex`。
- Produces: `_check_operation_directory_entries` 对终态 journal 显式消费三值角色；
  `_check_operation_state` 不再用 `active_ids` 折叠 UNKNOWN。

- [ ] **Step 1：扩展真实 cleanup lock-EIO 测试并确认红灯**

在现有 `test_checker_cleanup_owner_lock_unreadable_keeps_package` 中，保留真实
`_restore_cleanup_owner_scene` producer，并追加精确路径断言：

```python
unexpected = {
    issue.path
    for issue in report.errors
    if issue.code == CheckCode.INTERNAL_UNEXPECTED_ENTRY.value
}
assert Path(f".draft/operations/{restore_id}/originals") not in unexpected
assert Path(f".draft/operations/{restore_id}/staged") not in unexpected
assert Path(f".draft/operations/{trash_id}/cleanup-evidence.json") not in unexpected
```

调整 helper 调用以保留 `restore_id`。运行单例，预期当前实现失败并显示上述三个伪诊断。

- [ ] **Step 2：扩展 R-journal-EIO 与独立非法条目测试**

对现有 `test_checker_cleanup_owner_r_journal_unreadable_keeps_package` 追加断言：T 的
`cleanup-evidence.json` 不得被报为残留。再在 lock-EIO 现场向 R 或 T 目录加入
`not-registered.bin`，断言该路径仍得到 `internal.unexpected_entry`。两个测试分别证明 UNKNOWN
不会变成 INACTIVE，也不会关闭独立形状检查。

- [ ] **Step 3：运行新集成测试并确认红灯是角色折叠**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/integration/test_checker.py" -q \
  -k 'cleanup_owner_lock_unreadable or cleanup_owner_r_journal_unreadable or unknown_role'
```

- [ ] **Step 4：替换 `active_ids` 接缝**

修改 `_check_operation_directory_entries`：非终态 phase 保持现有允许集；终态 phase 根据显式 role
选择集合：

```python
if phase in (OperationPhase.COMPLETED, OperationPhase.ROLLED_BACK):
    allowed = (
        _OP_DIR_TERMINAL_ALLOWED
        if role is OperationDirectoryRole.INACTIVE
        else _OP_DIR_ALLOWED_ENTRIES
    )
elif phase is OperationPhase.ALLOCATING:
    allowed = _OP_DIR_ALLOCATING_ALLOWED
else:
    allowed = _OP_DIR_ALLOWED_ENTRIES
```

ACTIVE 与 UNKNOWN 都允许登记材料存在，但 UNKNOWN 不是 ACTIVE：变量和类型必须保留区别，便于未来
消费者不再次丢失完整性。现有 evidence 联合分类只保留与角色无关的 schema/节点验证；如果发现
某个分支必须先知道角色才能下结论，应在该分支显式区分 UNKNOWN，不得自行把它解释成损坏或成功。

在 `_check_operation_state` 中对每个已解析 journal 调用 Task 1 纯函数，并把 role 传给目录检查；
删除或停止使用 `_cleanup_owner_ids`/`active_ids` 作为目录协议的权威来源。package ownership 继续
消费同一 `OperationCheckIndex`，不得重读 lock/journal。

- [ ] **Step 5：验证已知 INACTIVE 终态残留仍被报告**

运行既有：

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/integration/test_checker.py" -q \
  -k 'terminal_operation_residual_pending or terminal_operation_leftover_originals'
```

这两个用例必须继续通过，证明修复没有把终态残留检查全局关闭。

- [ ] **Step 6：运行 checker unit/integration 定向门禁**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_checker.py" \
  "$RUNTIME/tests/integration/test_checker.py" -q
```

- [ ] **Step 7：提交 Task 2**

```bash
git add \
  plugins/draft/skills/draft/scripts/runtime/src/draft/checker.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_checker.py
git commit -m "fix(draft): preserve unknown checker operation roles"
```

---

### Task 3: 稳定 marker 身份读取边界

**Files:**

- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/graph.py:141-154`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_graph.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_checker.py:2486-2540`

**Interfaces:**

- Consumes: 现有 `_read_marker_file(path: Path) -> MarkerState`、`read_marker`、`CheckCode`、
  `DraftError`。
- Produces: `_read_marker_file` 对 marker 身份查询只产生行为契约二中的稳定结果；公开签名不变。

- [ ] **Step 1：写 direct `read_marker` 身份错误红灯矩阵**

单元测试不得访问真实文件系统。在 `tests/unit/test_graph.py` 使用假 root `Path("/draft")`，按 marker
精确路径 monkeypatch `Path.lstat` 这个稳定协作者边界，分别抛 `PermissionError(EACCES)` 与
`OSError(EIO)`：

```python
root = Path("/draft")
marker = root / ".draft/draft.json"

def fail_lstat(path: Path) -> os.stat_result:
    assert path == marker
    raise OSError(5, "Input/output error")

monkeypatch.setattr(Path, "lstat", fail_lstat)
with pytest.raises(DraftError) as caught:
    read_marker(root)
assert caught.value.code == CheckCode.FORMAT_INVALID.value
assert caught.value.paths == (marker,)
```

参数化 EACCES/EIO 但保持“一种身份读取失败映射”为该测试的单一行为契约。missing、directory、
symlink、非 UTF-8、非法 JSON 与 unsupported version 继续由既有 integration/parser 测试验证；
若缺少某个分区，只在其所属测试层补最窄测试，不把真实文件系统引入 unit。

- [ ] **Step 2：写 `check_draft` marker I/O 集成红灯**

在 `tests/integration/test_checker.py`：

1. marker `os.lstat` EIO 时，`check_draft` 返回 `CheckReport`，包含 path 为
   `.draft/draft.json` 的 `format.invalid`，树快照不变；
2. 把现有 `_INTERNAL_PROTOCOL_CHILDREN` 扩为包含 `.draft/draft.json` 的第二次 `os.stat` 失败矩阵，
   证明健康 marker 不再通过原始 `Path.is_symlink/exists/is_file` 查询。该用例只要求函数稳定返回，
   不应虚构 I/O 诊断，因为修复后这次 raw `stat` 根本不应发生。

- [ ] **Step 3：运行 marker 定向测试并确认红灯**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_graph.py" \
  "$RUNTIME/tests/integration/test_checker.py" -q \
  -k 'marker and (io or stat or eacces or eio or invalid or missing or symlink)'
```

当前预期至少一个 EACCES/EIO 用例原样抛 `OSError`。

- [ ] **Step 4：用一次受保护的 `lstat` 分类 marker**

在 `graph.py::_read_marker_file` 中替换三次 `Path.is_*`。建议实现：

```python
import stat

def _read_marker_file(path: Path) -> MarkerState:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError as exc:
        raise DraftError(CheckCode.FORMAT_MISSING.value, path) from exc
    except OSError as exc:
        raise DraftError(CheckCode.FORMAT_INVALID.value, path) from exc
    if stat.S_ISLNK(mode):
        raise DraftError(CheckCode.PATH_SYMLINK_ESCAPE.value, path)
    if not stat.S_ISREG(mode):
        raise DraftError(CheckCode.FORMAT_INVALID.value, path)
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DraftError(CheckCode.FORMAT_INVALID.value, path) from exc
    return parse_marker_value(decoded, path)
```

不要用 `except OSError: format.missing`；不要借此把其它 root layout 查询纳入本轮；不要改变
`parse_marker_value` 的 schema/version 语义。

- [ ] **Step 5：运行 graph/checker/生命周期 marker 消费者门禁**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_graph.py" \
  "$RUNTIME/tests/integration/test_checker.py" \
  "$RUNTIME/tests/integration/test_lifecycle.py" \
  "$RUNTIME/tests/unit/test_cli.py" -q
```

- [ ] **Step 6：提交 Task 3**

```bash
git add \
  plugins/draft/skills/draft/scripts/runtime/src/draft/graph.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_graph.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_checker.py
git commit -m "fix(draft): stabilize marker identity reads"
```

---

### Task 4: 横切审计、完整门禁与第十四轮修复记录

**Files:**

- Audit: `plugins/draft/skills/draft/scripts/runtime/src/draft/checker.py`
- Audit: `plugins/draft/skills/draft/scripts/runtime/src/draft/graph.py`
- Create (临时、不提交):
  `docs/review-notes/2026-08-19-draft-candidate-root-incremental-migration-round-14-fix-record.md`

**Interfaces:**

- Consumes: Tasks 1–3 的代码、测试和提交。
- Produces: 可独立复核的修复记录、完整离线门禁证据和干净的生产提交范围。

- [ ] **Step 1：沿 OperationCheckIndex 审计全部消费者**

列出 `OperationCheckIndex`、`lock_known`、`unresolved_ids`、`active_ids`、
`_cleanup_owner_ids`、`package_ownership_decision`、`_check_operation_directory_entries` 的所有引用。
逐项记录：

- package ownership 是否保留 HELD/ORPHAN/UNKNOWN；
- operation 目录是否保留 ACTIVE/INACTIVE/UNKNOWN；
- 哪些诊断依赖角色，哪些只依赖节点形状；
- 是否存在第二次 lock/journal 读取或把 UNKNOWN 变成空集合的消费者。

如仍有角色依赖消费者没有接入显式模型，停止 Task 4 并补回 Task 2，不得只在修复记录中列为
后置事项。

- [ ] **Step 2：审计 marker 的全部身份查询**

```bash
rg -n 'draft\.json|MARKER_PATH|_read_marker_file|read_marker|\.is_file\(|\.exists\(|\.is_symlink\(' \
  plugins/draft/skills/draft/scripts/runtime/src/draft/graph.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/checker.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/lifecycle.py
```

确认 marker 身份只在 `_read_marker_file` 的稳定边界分类；外层消费者可以捕获 `DraftError`，不得
重新 stat marker 再推断 missing。其它 root layout 查询不在本轮修改，但修复记录必须明确列为
后置范围，不能宣称所有 layout I/O 已稳定。

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

任何失败先定位根因。网络测试未运行就写“未运行”，不得写“全量门禁全部通过”。

- [ ] **Step 4：编写第十四轮修复记录**

修复记录逐条对应 M1/M2，必须包含：当前基线红灯、被违反不变量、实际实现边界、状态表是否逐行
覆盖、回归测试名、提交号、独立门禁数字、未验证事项和责任归因。另附：

- operation directory role 三值表及每个消费者；
- marker 身份分类表和所有 `read_marker` 消费者；
- 与本计划的任何偏差。若偏差改变已确认行为，必须先停止并报告，不能事后自行声明范围外。

- [ ] **Step 5：确认工作树与提交范围**

```bash
git status --short
git log --oneline --decorate -8
git diff --check e7ebd7d..HEAD
```

生产提交不得包含用户的 `AGENTS.md`、`docs/review-notes/` 或既有未跟踪计划。

## 自审结果

- **规格覆盖：** M1 的纯角色表、真实 UNKNOWN 场景、独立形状诊断和已知 INACTIVE 反例由 Tasks
  1–2 覆盖；M2 的 marker 身份表、direct reader 与 checker 端到端行为由 Task 3 覆盖；所有消费者
  与完整门禁由 Task 4 覆盖。
- **状态覆盖：** role 的 ACTIVE/INACTIVE/UNKNOWN 与 marker 的 missing/symlink/non-regular/
  I/O error/valid 分区均有明确结果；没有用空集合或 `None` 代替完整性。
- **类型一致：** `OperationDirectoryRole` 和 `operation_directory_role` 在 Task 1 定义、Task 2
  消费；`_read_marker_file` 公共返回类型不变。
- **禁止捷径：** 计划明确禁止 UNKNOWN 当 INACTIVE、lock 失败时跳过全部检查、二次读取
  lock/journal、EIO 当 missing、事后缩小已确认范围以及无关 root-layout 重构。
- **占位检查：** 所有步骤均给出具体输入、预期、实现边界与运行命令，没有待补内容或省略式引用。
