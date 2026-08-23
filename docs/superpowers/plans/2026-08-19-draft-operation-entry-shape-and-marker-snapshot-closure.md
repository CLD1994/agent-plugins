# Draft operation 条目形状与 marker 快照闭合实施计划

> **供 agentic workers：** REQUIRED SUB-SKILL：使用 `superpowers:subagent-driven-development`
>（推荐）或 `superpowers:executing-plans`，逐任务实施本计划。所有步骤使用 checkbox（`- [ ]`）跟踪，
> 不得跳过红灯、阶段门禁或横切审计。

**目标：** 闭合 operation 目录“名字×节点类型”协议，并让一次写计划中的 marker 状态、原始字节、
摘要和观察事实来自同一不可变快照。

**架构：** checker 以一个名称→节点类型协议表执行角色无关的顶层形状检查，phase/role 只决定名称
能否存在；marker 权威模块一次读取并返回不可变 snapshot，根事实加载和所有写 planner 传递该对象，
观察构造器不再自行访问 marker。结构命令直接消费 snapshot 的 `conclusions_state`，不再按 root state
重新解释结论所有权。

**技术栈：** Python 3.12、pytest、Ruff、Pyright strict、uv。

**规格：**
`docs/review-notes/2026-08-19-draft-candidate-root-incremental-migration-round-15.md`

## 全局约束

- 基线为 `aa6448a`。保留第十四轮已正确的 `OperationDirectoryRole`、`OperationCheckIndex` 和
  `_read_marker_file` 错误码语义；不得回退为 `active_ids` 或原始 `Path.is_*` 读取。
- 当前保障边界是本地协作式单写者、普通 I/O 失败与进程中断。不要扩展到 NFS、机器掉电、恶意
  `.draft/` 篡改或不可消除的 POSIX 系统调用间隙。
- 本轮不拆分 `operations.py`，不做无关重构，不引入依赖，不新增错误码。
- marker snapshot 后发生的 live 变化只能由既有观察复核拒绝，不能通过第二次读取更新 snapshot 或
  observation baseline（观察基线）。
- `root_state` 和 `conclusions_state` 正交；candidate 与 registered 的 `generated` 都表示机器拥有
  `conclusions.md`。
- checker 的 role 只决定名字是否允许；节点类型和角色无关。UNKNOWN 不得变成跳过 shape pass 的
  开关。
- unit 不访问真实文件系统；真实目录、I/O 故障、并发切换和完整命令入口放 integration。
- 每个修复先写能在 `aa6448a` 失败的最窄测试，再实现，再运行定向门禁。不得删除测试、放宽配置、
  吞掉异常或以测试全绿代替横切数据流审计。
- 行为表和错误码是硬约束；下面的内部类型名与签名是建议接口。若代码事实要求调整命名，可以改名，
  但必须保留“一次 snapshot”和“名字×类型正交”两个不变量。遇到未定义状态或需要改变机器协议时
  停止并报告，不自行选择语义。
- 不提交用户已有的 `AGENTS.md`、`docs/review-notes/` 或其它未跟踪计划。生产提交只包含当前任务的
  代码与测试。

---

## 行为契约一：operation 顶层条目协议

| 名字 | 预期顶层 `EntryKind` | 内容/深层验证所有者 |
|---|---|---|
| `journal.json` | `FILE` | `read_journal` |
| `journal.json.pending` | `FILE` | pending journal 协议 |
| `originals` | `DIRECTORY` | recovery copies 协议 |
| `staged` | `DIRECTORY` | recovery copies 协议 |
| `attempt` | `DIRECTORY` | attempt tree 协议 |
| `cleanup-evidence.json` | `FILE` | evidence 联合分类 |
| `cleanup-evidence.json.pending` | `FILE` | evidence 联合分类 |
| `cleanup-evidence.json.quarantine` | `FILE` | evidence 联合分类 |

phase/role 决定上述哪些 key 当前允许存在；存在后的类型由本表决定。没有专用分类器的 symlink 是
路径错误，不能因名字合法而通过。evidence 三名由现有共享分类器拥有类型与内容诊断（非文件继续
使用既有 `trash.package_corrupt`），通用 pass 不产生重复诊断。

## 行为契约二：marker snapshot 与状态转换

建议接口：

```python
@dataclass(frozen=True)
class MarkerSnapshot:
    state: MarkerState
    raw_bytes: bytes

    @property
    def sha256(self) -> str:
        return _sha256_hex(self.raw_bytes)


def read_marker_snapshot(root: Path) -> MarkerSnapshot:
    return _read_marker_snapshot_file(root / MARKER_PATH)


def read_marker(root: Path) -> MarkerState:
    return read_marker_snapshot(root).state
```

| snapshot 时事实 | snapshot 后 live 事实 | 计划/执行结果 |
|---|---|---|
| valid candidate/registered | 未变 | state、mutation before、digest、observation 全部用 snapshot |
| valid | 改成另一份 valid marker | observation 仍是旧 snapshot；锁内报 `operation.external_change`，不写业务 mutation |
| valid | missing/invalid/I/O unavailable | 同上；不得重新采信新基线 |
| ENOENT | 不适用 | `format.missing` |
| `.draft` 或 marker symlink | 不适用 | `path.symlink_escape` |
| 非普通 marker、非法 bytes/schema、EACCES/EIO | 不适用 | `format.invalid` |
| 未知整数 version | 不适用 | `format.unsupported` |

同一 snapshot 的 `state`、`raw_bytes` 和 `sha256` 不得由构造者分别传入，避免制造互相矛盾的对象。

---

### Task 1: 闭合 operation 名称×节点类型检查

**Files:**

- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/checker.py:446-758`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_checker.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_checker.py:1819-2370`

**Interfaces:**

- Consumes: `OperationDirectoryRole`、`OperationPhase`、`EntryKind`、
  `classify_node_or_error`、现有 evidence 联合分类器。
- Produces（建议）：单一 `_OPERATION_ENTRY_KINDS: Mapping[str, EntryKind]`；
  `_check_operation_directory_entries` 对每个枚举 child 同时完成名称授权与顶层类型检查。

- [ ] **Step 1：写纯协议表红灯**

在 `tests/unit/test_checker.py` 参数化每个名字的预期类型。测试预期必须自己列出，不能从生产 mapping
反向生成：

```python
@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("journal.json", EntryKind.FILE),
        ("journal.json.pending", EntryKind.FILE),
        ("originals", EntryKind.DIRECTORY),
        ("staged", EntryKind.DIRECTORY),
        ("attempt", EntryKind.DIRECTORY),
        ("cleanup-evidence.json", EntryKind.FILE),
        ("cleanup-evidence.json.pending", EntryKind.FILE),
        ("cleanup-evidence.json.quarantine", EntryKind.FILE),
    ],
)
def test_operation_entry_kind_protocol(name: str, expected: EntryKind) -> None:
    assert operation_entry_kind(name) is expected
```

`operation_entry_kind("not-registered.bin")` 应返回 `None`。如果不公开 helper，可测试一个等价的纯协议
对象，但不得在 unit 中访问文件系统。

- [ ] **Step 2：写真实 UNKNOWN 错误类型红灯**

复用 `_restore_cleanup_owner_scene`，让 lock `lstat` EIO 形成 UNKNOWN。按 pytest case 分别把一个
合法名字改成错误顶层类型；每个 case 只验证一个协议分区：

```python
@pytest.mark.parametrize(
    ("relative", "wrong_kind"),
    [
        (Path("originals"), "file"),
        (Path("staged"), "file"),
        (Path("attempt"), "file"),
        (Path("journal.json.pending"), "directory"),
    ],
)
def test_checker_unknown_role_rejects_allowed_name_with_wrong_kind(
    draft_factory: DraftFactory,
    monkeypatch: pytest.MonkeyPatch,
    relative: Path,
    wrong_kind: str,
) -> None:
    root, restore_id, _trash_id = _restore_cleanup_owner_scene(
        draft_factory, monkeypatch
    )
    operation = root / ".draft/operations" / restore_id
    replace_protocol_node(operation / relative, wrong_kind)
    report = check_with_lock_lstat_eio(root, monkeypatch)
    assert has_stable_issue(report, Path(f".draft/operations/{restore_id}") / relative)
    assert snapshot_tree(root) == before
```

测试 helper 只负责安排普通文件/目录状态；不要复制生产分类逻辑。另加一个合法名字 symlink case，
精确断言 `path.symlink_escape`，不测试根外文件内容。

- [ ] **Step 3：运行红灯并确认失败原因**

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
RUNTIME="$REPO_ROOT/plugins/draft/skills/draft/scripts/runtime"
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_checker.py" \
  "$RUNTIME/tests/integration/test_checker.py" -q \
  -k 'operation_entry_kind_protocol or unknown_role_rejects_allowed_name'
```

预期：当前实现因没有纯类型协议，且只比较 `child.name`，至少 wrong-kind integration case 失败。

- [ ] **Step 4：实现单一名称→类型协议**

建议在 `checker.py` 以只读 mapping 表达：

```python
_OPERATION_ENTRY_KINDS: Mapping[str, EntryKind] = MappingProxyType(
    {
        "journal.json": EntryKind.FILE,
        "journal.json.pending": EntryKind.FILE,
        "originals": EntryKind.DIRECTORY,
        "staged": EntryKind.DIRECTORY,
        "attempt": EntryKind.DIRECTORY,
        "cleanup-evidence.json": EntryKind.FILE,
        "cleanup-evidence.json.pending": EntryKind.FILE,
        "cleanup-evidence.json.quarantine": EntryKind.FILE,
    }
)


def operation_entry_kind(name: str) -> EntryKind | None:
    return _OPERATION_ENTRY_KINDS.get(name)
```

`_OP_DIR_ALLOWED_ENTRIES` 从 mapping keys 派生；terminal/allocating 仍是该权威集合的状态子集，不得
重新复制一份完整类型清单。

- [ ] **Step 5：在通用 pass 中稳定分类非 evidence child**

控制流必须先区分“名字不允许”和“名字允许但类型错误”，并保留 symlink 最具体诊断：

```python
for child in children:
    relative = operation_relative / child.name
    if child.name not in allowed:
        kind = stable_child_kind(child, issues, relative)
        issues.append(
            _error(
                CheckCode.PATH_SYMLINK_ESCAPE
                if kind is EntryKind.SYMLINK
                else CheckCode.INTERNAL_UNEXPECTED_ENTRY,
                relative,
            )
        )
        continue
    if child.name in _EVIDENCE_NAMES:
        continue  # 专用联合分类拥有类型与内容诊断。
    expected = operation_entry_kind(child.name)
    actual = stable_child_kind(child, issues, relative)
    if actual is None:  # 分类 I/O 已产生稳定诊断。
        continue
    if actual is EntryKind.SYMLINK:
        issues.append(_error(CheckCode.PATH_SYMLINK_ESCAPE, relative))
    elif actual is not expected:
        issues.append(_error(CheckCode.INTERNAL_UNEXPECTED_ENTRY, relative))
```

`stable_child_kind` 必须调用现有 `classify_node_or_error` 并把 `DraftError` 转为根内相对
`CheckIssue`；不能调用 `Path.is_*`/`DirEntry.is_*`。`journal.json` 已由 index reader 验证，可避免
重复诊断，但它的存在不能成为跳过其它 child 分类的理由。

- [ ] **Step 6：补 ACTIVE/INACTIVE/evidence 反例**

- ACTIVE/UNKNOWN 合法目录不报 residual；
- INACTIVE 的 `originals` 即使是目录仍报终态残留；
- evidence directory/link/invalid bytes 仍只由共享联合分类产生稳定 `trash.package_corrupt` 或
  `path.symlink_escape`，不产生重复等价 issue；
- 分类 EIO 返回稳定 issue，checker 前后树不变。

- [ ] **Step 7：运行 Task 1 定向门禁**

```bash
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_checker.py" \
  "$RUNTIME/tests/integration/test_checker.py" -q
```

- [ ] **Step 8：提交 Task 1**

```bash
git add \
  plugins/draft/skills/draft/scripts/runtime/src/draft/checker.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_checker.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_checker.py
git commit -m "fix(draft): validate operation entry node kinds"
```

---

### Task 2: 建立一次读取的 MarkerSnapshot

**Files:**

- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/graph.py:91-178`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_graph.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_lifecycle.py`

**Interfaces:**

- Produces（建议固定）：`MarkerSnapshot(state: MarkerState, raw_bytes: bytes)`、
  `read_marker_snapshot(root: Path) -> MarkerSnapshot`；现有
  `read_marker(root: Path) -> MarkerState` 成为包装。
- Task 3–5 只能消费 snapshot，不得自行重建 marker bytes/digest。

- [ ] **Step 1：写 snapshot 纯属性与单次内容读取红灯**

在 unit 中使用假路径和受接口约束的 monkeypatch，不访问真实 FS。让 `os.lstat` 对 `.draft` 返回目录、
对 marker 返回普通文件，让 `Path.read_bytes` 返回一份 candidate bytes 并记录次数：

```python
snapshot = read_marker_snapshot(Path("/draft"))
assert snapshot.state.root_state is RootState.CANDIDATE
assert snapshot.raw_bytes == candidate_bytes
assert snapshot.sha256 == sha256_bytes(candidate_bytes)
assert marker_read_count == 1
```

改变测试中的 live 返回变量不能改变已构造 snapshot，证明对象不可变且摘要不是再次读取所得。

- [ ] **Step 2：写 marker parent/final 状态矩阵红灯**

每个参数化 case 只验证一个分类：

| `.draft` | marker | 预期 |
|---|---|---|
| missing | 不读取 | `format.missing @ marker` |
| symlink | 不读取 | `path.symlink_escape @ .draft` |
| file/other | 不读取 | `format.invalid @ marker` |
| directory | missing | `format.missing @ marker` |
| directory | symlink | `path.symlink_escape @ marker` |
| directory | directory/other | `format.invalid @ marker` |
| directory lstat/read EACCES/EIO | 任意 | `format.invalid @ marker` |

现有 invalid UTF-8/JSON/schema/unsupported tests 改为走 `read_marker_snapshot`；`parse_marker_value` 的纯
parser tests 保留。

- [ ] **Step 3：运行 marker unit 红灯**

```bash
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_graph.py" -q -k 'marker and snapshot'
```

预期：`MarkerSnapshot/read_marker_snapshot` 尚不存在。

- [ ] **Step 4：实现 snapshot 读取器**

实现顺序必须是稳定分类 `.draft` → 稳定分类 marker → 读取 bytes 一次 → 从同一 bytes 解析：

```python
def read_marker_snapshot(root: Path) -> MarkerSnapshot:
    marker = root / MARKER_PATH
    classify_marker_parent(root / INTERNAL_DIR, marker)
    classify_marker_leaf(marker)
    try:
        raw = marker.read_bytes()
        decoded = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DraftError(CheckCode.FORMAT_INVALID.value, marker) from exc
    return MarkerSnapshot(state=parse_marker_value(decoded, marker), raw_bytes=raw)


def read_marker(root: Path) -> MarkerState:
    return read_marker_snapshot(root).state
```

`classify_marker_parent/leaf` 使用一次受保护 `os.lstat` 与 `stat.S_IS*`；只有
`FileNotFoundError` 是 missing，其它 `OSError` 是 invalid。不要从 `operations.py` 反向导入导致循环，
也不要在 snapshot 后再次 `read_text/read_bytes`。

- [ ] **Step 5：运行 Task 2 unit 与既有 graph/checker 门禁**

```bash
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_graph.py" \
  "$RUNTIME/tests/integration/test_checker.py" -q
```

- [ ] **Step 6：提交 Task 2**

```bash
git add \
  plugins/draft/skills/draft/scripts/runtime/src/draft/graph.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_graph.py
git commit -m "refactor(draft): capture marker facts in one snapshot"
```

---

### Task 3: 让 OperationFacts 与观察清单共享 snapshot

**Files:**

- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/observations.py:163-267`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/operation_facts.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_observations.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_observations.py`
- Create: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_operation_facts.py`
- Update callers/tests that construct `graph_observations` directly so they pass an explicit snapshot.

**Interfaces:**

- Consumes: Task 2 `MarkerSnapshot/read_marker_snapshot`。
- Produces（建议）：
  `marker_observation(snapshot: MarkerSnapshot) -> Observation`；
  `graph_observations(graph, *, marker: MarkerSnapshot) -> ObservationManifest`；
  `OperationFacts.marker: MarkerSnapshot`，`root_state` 为派生 property。

- [ ] **Step 1：写纯 marker observation 测试**

```python
snapshot = MarkerSnapshot(state=candidate_state, raw_bytes=candidate_bytes)
observation = marker_observation(snapshot)
assert observation.path == MARKER_PATH
assert observation.kind is EntryKind.FILE
assert observation.sha256 == sha256_bytes(candidate_bytes)
```

该 unit 不 patch FS。给 `graph_observations` 传 snapshot 后，manifest 中 marker observation 必须是同一
摘要。

- [ ] **Step 2：写计划期状态切换集成红灯**

在真实 candidate 根中，让 `load_operation_facts` 完成第一次 snapshot 后、页面视图加载或观察构造
前把 live marker 改成 registered。不要 patch expected 生成路径；expected candidate digest 从测试
初始 bytes 独立计算：

```python
facts = load_operation_facts(root)
marker = facts.observations.for_path(MARKER_PATH)
assert facts.root_state is RootState.CANDIDATE
assert facts.marker.raw_bytes == candidate_bytes
assert marker is not None and marker.sha256 == sha256_bytes(candidate_bytes)
assert (root / MARKER_PATH).read_bytes() == registered_bytes
```

当前实现会得到 candidate root state + registered observation，该测试必须先红。

- [ ] **Step 3：运行 Task 3 红灯**

```bash
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_observations.py" \
  "$RUNTIME/tests/integration/test_observations.py" \
  "$RUNTIME/tests/integration/test_operation_facts.py" -q
```

- [ ] **Step 4：实现无 marker I/O 的 observation builder**

`graph_observations` 删除 marker 的 `is_file/is_symlink/read_bytes` 分支，直接追加
`marker_observation(marker)`。其它页面与目录观察本 Task 不改语义。

`internal_protocol_observations` 必须跳过 `MARKER_PATH`，因为 adopt 会从 snapshot 显式合并它：

```python
if relative == MARKER_PATH:
    continue
```

不要先读取 marker 后再依赖 `setdefault` 去重；“结果去重”不能证明“没有第二次读取”。

- [ ] **Step 5：让 OperationFacts 保存 snapshot 而非平行 root_state**

建议：

```python
@dataclass(frozen=True)
class OperationFacts:
    graph: DraftGraph
    marker: MarkerSnapshot
    blocking_paths: tuple[Path, ...]
    observations: ObservationManifest

    @property
    def root_state(self) -> RootState:
        return self.marker.state.root_state
```

`load_operation_facts` 入口只调用一次 `read_marker_snapshot` 作为本计划的根事实，所有返回分支都以该
snapshot 构建 observations。内部 `check_draft/load_graph_tolerant` 为自己的只读验证再次读取 marker
不改变本 snapshot；若 live 状态在此期间改变，后续锁内 observation 仍以 snapshot 拒绝。

- [ ] **Step 6：更新所有测试中的直接 graph_observations 调用**

- unit synthetic graph：显式构造 `MarkerSnapshot`，不得读取 `/draft`；
- integration real graph：先 `read_marker_snapshot(root)` 再传入；
- 不提供“marker 参数缺失时重新读 live 文件”的兼容默认值，否则生产调用仍可退回旧漏洞。

- [ ] **Step 7：运行 Task 3 定向门禁并提交**

```bash
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_observations.py" \
  "$RUNTIME/tests/integration/test_observations.py" \
  "$RUNTIME/tests/integration/test_operation_facts.py" -q

git add \
  plugins/draft/skills/draft/scripts/runtime/src/draft/observations.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/operation_facts.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_observations.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_page_create.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_structure.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_trash.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_observations.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_operation_facts.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_lifecycle.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_checker.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_operations.py
git commit -m "refactor(draft): propagate marker snapshots into observations"
```

提交前用 `git diff --cached --name-only` 确认没有暂存与本 Task 无关的测试文件。

---

### Task 4: 把普通写 planner 接到同一 snapshot

**Files:**

- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/page_create.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/structure.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/page_metadata.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/trash.py`
- Modify: 对应 `tests/unit/test_page_create.py`、`test_structure.py`、`test_page_metadata.py`、
  `test_trash.py`
- Modify: 对应 integration `test_page_commands.py`、`test_structure.py`、`test_trash.py`

**Interfaces:**

- Consumes: Task 3 `OperationFacts.marker/observations` 与 Task 2 `MarkerSnapshot`。
- Produces: create/move/promote/collapse/status/relation/trash/restore 的 `OperationPlan.root_state` 与
  marker observation 来自同一个 snapshot；普通 planner 不调用 `read_marker`/`graph_observations`
  重建 marker 事实。

- [ ] **Step 1：为每类 planner 写“传入即原样使用”unit 红灯**

用 synthetic graph、candidate snapshot 与一个带唯一 marker digest 的 sentinel manifest：

```python
plan = plan_move(
    graph,
    source,
    target,
    marker=candidate_snapshot,
    observations=sentinel_observations,
)
assert plan.root_state is RootState.CANDIDATE
assert plan.observations is sentinel_observations
```

同一模式分别覆盖 structure、relation multi-write 与 restore plan；create/trash 已接受 observations，只需
更新为从 `facts` 传递并保留现有 unit。不要 spy 内部函数调用图；断言公开计划中的状态和观察结果。

- [ ] **Step 2：写 candidate generated 结构命令红灯**

真实 candidate 根生成 conclusions 后执行 move，断言：

```python
before = (root / "conclusions.md").read_bytes()
move_page(root, Path("pages/a.md"), Path("pages/b.md"))
assert (root / "conclusions.md").read_bytes() == before
assert (root / "pages/b.md").is_file()
```

分别用 unit 覆盖 promote/collapse 共享 planner 的 `conclusions_state=generated` 分支；integration 不必
复制三个完整根。registered generated 既有行为继续通过。

- [ ] **Step 3：运行 Task 4 红灯**

```bash
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_page_create.py" \
  "$RUNTIME/tests/unit/test_structure.py" \
  "$RUNTIME/tests/unit/test_page_metadata.py" \
  "$RUNTIME/tests/unit/test_trash.py" \
  "$RUNTIME/tests/integration/test_page_commands.py" \
  "$RUNTIME/tests/integration/test_structure.py" \
  "$RUNTIME/tests/integration/test_trash.py" -q \
  -k 'marker or observation or candidate_generated or conclusions'
```

- [ ] **Step 4：structure 传递 facts，不再重读 marker/observations**

让 `_load_clean_graph` 返回 `OperationFacts`（或等价的单一对象），而不是丢弃 marker/observations 后只
返回 `(graph, root_state)`。`plan_move/plan_promote/plan_collapse/plan_path_remap` 接受 snapshot 与已构造
observations，最终 plan 直接使用；删除内部 `read_marker` 与 `graph_observations(graph)`。

结论所有权改为纯判定：

```python
conclusions_generated = (
    marker.state.conclusions_state is ConclusionsState.GENERATED
)
```

不得检查 `marker.state.root_state`；candidate/registered 语义相同。

- [ ] **Step 5：page metadata 复用 facts 的 marker 事实**

`_target_marker_observations` 接受 `MarkerSnapshot`，只组合目标页 digest 与
`marker_observation(snapshot)`；删除 marker `is_file/read_bytes`。relation helpers 接受调用方已构造的
facts observations，不再在 `_multi_write_plan` 调用 `graph_observations`。

- [ ] **Step 6：trash/restore 保持预分配日志与计划 snapshot 一致**

`trash_page` 继续使用 `facts`。`restore_retained_trash` 在 preallocate 前读取一个 snapshot：

```python
marker = read_marker_snapshot(resolved)
preallocate_journal(
    resolved,
    kind=OperationKind.RESTORE,
    operation_id=restore_id,
    root_state=marker.state.root_state,
    observations=EMPTY_OBSERVATIONS,
    metadata={"restores_trash_operation": operation_id},
)
plan = build_restore_plan(resolved, restore_id, package, marker=marker)
```

`build_restore_plan/_restore_observations` 只用传入 snapshot 的 state/digest；删除内部第二次
`read_marker` 和无参数 `graph_observations`。如果 marker 在 snapshot 后改变，锁内观察拒绝并按既有
restore resolver 解关联/终态化，不能把新状态写回预分配 journal。

- [ ] **Step 7：普通入口并发切换验收**

用一个共享 `load_operation_facts` 集成用例证明 snapshot 后 candidate→registered 时，create 或
status 的最终执行在 mutation 前得到 `operation.external_change`；页面、marker 不变，本次准备日志按
既有冲突协议终态化且锁释放。其它普通命令通过 unit 的“原样传递 manifest”证明共享边界，不复制
完整并发矩阵。

- [ ] **Step 8：运行 Task 4 全模块门禁并提交**

```bash
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_page_create.py" \
  "$RUNTIME/tests/unit/test_structure.py" \
  "$RUNTIME/tests/unit/test_page_metadata.py" \
  "$RUNTIME/tests/unit/test_trash.py" \
  "$RUNTIME/tests/integration/test_page_commands.py" \
  "$RUNTIME/tests/integration/test_structure.py" \
  "$RUNTIME/tests/integration/test_trash.py" -q

git add \
  plugins/draft/skills/draft/scripts/runtime/src/draft/page_create.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/structure.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/page_metadata.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/trash.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_page_create.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_structure.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_page_metadata.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_trash.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_page_commands.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_structure.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash.py
git commit -m "fix(draft): keep write plans on one marker snapshot"
```

---

### Task 5: 闭合 adopt 与 conclusions 的 marker mutation

**Files:**

- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/lifecycle.py:100-161`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/conclusions.py:365-523`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/observations.py:218-267`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_conclusions.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_lifecycle.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_conclusions.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_cli.py`

**Interfaces:**

- Consumes: Task 2–3 snapshot 与 marker observation。
- Produces: adopt/generate-conclusions 的 marker mutation before、state、after 和 observation 同源；direct
  adopt 错误码符合规格 §5.3。

- [ ] **Step 1：写 adopt I/O 与身份红灯矩阵**

在真实 candidate 根分别注入：snapshot parent/final lstat EIO、marker bytes read EIO、missing、目录、
symlink、unknown version。精确断言 H1 表中的 code/path；所有失败均断言：

```python
assert snapshot_tree(root) == before
assert not (root / ".draft/operation.lock").exists()
assert not (root / ".draft/operations").exists()
```

已有 operations 目录时只断言未新增 operation id。CLI JSON 用一个 EIO case 验证无 traceback、code
稳定；不在 CLI 层复制完整矩阵。

- [ ] **Step 2：写 adopt 同源 snapshot 红灯**

在 snapshot 返回 candidate 后、`check_draft` 完成前/后把 live marker 切成 registered，执行 adopt：

- 不得生成 before==after mutation/journal；
- 应在锁内观察前拒绝，或在取得锁前检测到状态变化并安全终止；
- marker 保持外部切换后的 registered，不回滚；本次锁释放、准备状态可诊断且可重试。

测试读取 journal 时按返回/捕获的 operation id 精确定位，不得使用 `next(iterdir())`。

- [ ] **Step 3：改写 adopt 只消费 snapshot**

删除 `reject_symlink_components(target, MARKER_PATH)`、`final_marker.is_file()` 和
`final_marker.read_bytes()` 三个旁路：

```python
snapshot = read_marker_snapshot(target)
state = snapshot.state
if state.root_state is not RootState.CANDIDATE:
    raise DraftError("adopt.already_registered", target)
before_bytes = snapshot.raw_bytes
after_bytes = marker_bytes(RootState.REGISTERED, state.conclusions_state)
if sha256_bytes(before_bytes) == sha256_bytes(after_bytes):
    raise DraftError(CheckCode.FORMAT_INVALID.value, target / MARKER_PATH)
```

`graph_observations(graph, marker=snapshot)` 提供 marker；`internal_protocol_observations` 已在 Task 3 跳过
marker。合并 manifest 后断言 marker 路径恰好一项，不能靠重复读取后 `setdefault` 隐藏。

- [ ] **Step 4：让 conclusions planner 使用 snapshot bytes**

`generate_conclusions` 读取一个 snapshot，向 `plan_generation` 传递；后者：

- 用 `snapshot.state.conclusions_state/root_state`；
- marker mutation before=`snapshot.sha256`，after bytes 只更新同一 state 的 conclusions_state；
- observations 使用同一 snapshot；
- 删除 marker `is_file/read_bytes`。

纯 `plan_generation` unit 用 synthetic snapshot，无真实 FS。candidate 与 registered × not_generated/
generated 的参数化用例分别验证 root state 保留、conclusions 只向 generated 推进。

- [ ] **Step 5：运行 lifecycle/conclusions/CLI 定向门禁**

```bash
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_conclusions.py" \
  "$RUNTIME/tests/integration/test_lifecycle.py" \
  "$RUNTIME/tests/integration/test_conclusions.py" \
  "$RUNTIME/tests/integration/test_cli.py" -q
```

- [ ] **Step 6：提交 Task 5**

```bash
git add \
  plugins/draft/skills/draft/scripts/runtime/src/draft/lifecycle.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/conclusions.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/observations.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_conclusions.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_lifecycle.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_conclusions.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_cli.py
git commit -m "fix(draft): bind marker mutations to captured snapshots"
```

---

### Task 6: 横切审计、完整门禁与第十五轮修复记录

**Files:**

- Audit: `plugins/draft/skills/draft/scripts/runtime/src/draft/**/*.py`
- Create（临时，不提交）：
  `docs/review-notes/2026-08-19-draft-candidate-root-incremental-migration-round-15-fix-record.md`

**Interfaces:**

- Consumes: Tasks 1–5 的生产代码、测试和提交。
- Produces: 完整消费者表、可复现门禁证据、如实归因和干净生产提交范围。

- [ ] **Step 1：审计 operation 目录协议所有分区**

以 AST/实际调用关系和运行测试核对：

- `_OPERATION_ENTRY_KINDS` 是完整名称→类型的唯一权威；
- ACTIVE/INACTIVE/UNKNOWN 与 allocating/mid-write 都经过 shape pass；
- evidence 类型/内容只有共享联合分类器拥有，不重复诊断；
- 没有 `DirEntry.is_*`/`Path.is_*` 旁路重新折叠分类错误；
- 真实 producer 的合法材料不误报，错误类型不漏报，checker 仍严格只读。

- [ ] **Step 2：建立 marker 消费者完整表**

先发现候选，再人工沿数据流分类：

```bash
rg -n 'MARKER_PATH|read_marker(_snapshot)?|graph_observations|marker_observation|draft\.json' \
  "$RUNTIME/src/draft" -g '*.py'
```

修复记录必须按以下四类列出每个实际入口：state-only、snapshot producer、plan/log/mutation/observation
consumer、明确延期的 root-layout guard。逐项确认：

- 任何 plan/log/mutation/observation consumer 都接收 snapshot；
- 生产代码不存在 `MARKER_PATH` 配合 `is_file/is_symlink/read_bytes/read_text` 重建计划事实；
- `graph_observations` 不读取 marker FS；
- `OperationFacts.root_state`、marker observation、adopt/conclusions mutation before 同源；
- snapshot 后状态变化由 observation 拒绝，不被重新采信。

字符串搜索只用于发现位置；完成判定必须检查实际调用参数和运行行为。

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

- [ ] **Step 4：编写第十五轮修复记录**

记录必须包含：

- M1 名称×类型表以及 phase/role 消费者；
- H1 marker snapshot 状态表、所有实际消费者、故障注入与并发切换证据；
- M2 candidate/registered × conclusions_state 交叉矩阵；
- 对第十四轮“全树消费者”错误声明的明确更正；
- 每项实际提交、定向测试与完整离线门禁输出；
- 未运行的网络测试和任何未验证事项；
- 工作树中用户 `AGENTS.md` 与临时 docs 未提交的事实。

- [ ] **Step 5：最终自审与提交范围确认**

```bash
git status --short
git log --oneline aa6448a..HEAD
git diff --stat aa6448a..HEAD
git diff --check aa6448a..HEAD
```

生产提交不得包含 `AGENTS.md`、`docs/review-notes/` 或本计划。若本轮实现发现计划未定义的 marker
消费者或状态组合，停止完成声明并把证据交回评审者，不得写入“后置范围”绕过。

---

## 计划自审

- **规格覆盖：** M1 的名称×类型正交由 Task 1；H1 的 snapshot 生产、观察传播、普通写入口和
  marker mutation 分别由 Tasks 2–5；candidate generated 结构语义由 Task 4；审计声明更正由 Task 6。
- **状态覆盖：** operation 覆盖 ACTIVE/INACTIVE/UNKNOWN 与 allocating/mid-write；marker 覆盖
  identity/read 错误、valid 未变、snapshot 后变化；结论覆盖两种 root state × 两种 ownership state。
- **类型一致：** `MarkerSnapshot` 在 Task 2 定义，Task 3 放入 `OperationFacts`，Tasks 4–5 只消费；
  `graph_observations` 从 Task 3 起要求显式 snapshot，无兼容回读分支。
- **禁止捷径：** 计划明确禁止二次 live marker 读取、OSError 外包裹补丁、UNKNOWN 跳过 shape、用未知
  名字代替错误类型矩阵、registered 条件绑定 conclusions ownership，以及把未完成项写成后置范围。
- **范围控制：** 不拆 `operations.py`，不扩展非 marker 根布局 I/O，不重复实现 evidence/recovery 深层
  状态机。
