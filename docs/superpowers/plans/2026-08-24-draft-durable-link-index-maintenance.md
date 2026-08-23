# Draft 链接索引耐久维护实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让全部相关业务 operation、外部文件变更通知和机会式读取共享严格、可恢复的链接索引维护协议。

**Architecture:** 根内 Markdown 和目录事实保持权威；operation journal 记录单调
`link_index_revision` 及 `published`/`rebuild_required` 结果。索引领域模块负责
schema、严格扫描和增量事实，维护模块负责 journal authority、耐久发布、flag 和独立
maintenance operation，业务 operation 协调器负责把索引阶段接入既有终态与 recovery。

**Tech Stack:** Python 3.12、标准库文件描述符与 `fsync`/原子替换、
`markdown-it-py`、pytest、pytest-mock、uv、ruff、pyright。

**Spec:** `.scratch/draft-durable-link-index-maintenance/spec.md`

## Global Constraints

- 本计划实现的行为契约以规格为准；任何需要改变 phase、字段、错误码、退出码或恢复
  方向的情况都必须停止并报告。
- 目标环境是本地文件系统、单个受协调 CLI 写者、普通 I/O 失败和已定义耐久步骤后的
  进程中断。
- 链接索引是派生缓存；根内 Markdown 和目录事实始终是权威来源。
- fresh 证明必须读取全部覆盖 Markdown 并比较 SHA-256；inode、size、mtime 不得替代
  正文摘要，也不进入索引 schema。
- 业务 mutation 验证完成后，索引失败不得触发业务 rollback。
- `link_index_updating` 之后只允许前向收敛。
- journal 和索引 schema 只接受新版本；不双读、不迁移、不填补默认值。
- notification 不接收变化路径，成功送达后固定退出码 `0` 且不公开消费者 warning。
- `check`、`query backlinks`、`query page` 和 adopt 预检查可以耐久写入索引；dry-run
  不得写入。
- 不实现 watcher、通用事件分发、journal sidecar 或索引状态专用 query。
- 单元测试不得访问真实文件系统；文件系统、同步、原子替换、中断和并发属于集成测试。
- 机器协议精确断言；普通人类文案验证事实、结构和特殊字符处理，不跨层重复逐字断言。
- 工作树可能包含用户的既有修改。执行前必须检查 diff，只提交本计划范围内的改动，
  不重置、覆盖或夹带无关文件。
- 下述模块和接口是基于当前代码的数据流作出的实施建议；已经确认的行为契约和项目规则
  是硬性要求。实施者若发现建议接口无法保持规格不变量，应停止并提交证据，不得用局部
  兼容分支绕过。

---

## 建议文件结构

### 新建

- `plugins/draft/skills/draft/scripts/runtime/src/draft/link_index_maintenance.py`
  - 读取最新 journal authority；
  - 校验索引制品和 rebuild flag；
  - 编排 `link-index.update` operation；
  - 发布 staging、写索引结果、处理 warning；
  - 为业务 operation 和 recovery 提供统一索引收敛接口。
- `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_link_index_maintenance.py`
  - authority 选择、revision、基线决策、warning 和并发令牌的纯契约测试。
- `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_link_index_maintenance.py`
  - 真实文件系统上的 staging、目标发布、flag、锁、恢复和机会式维护。
- `plugins/draft/skills/draft/scripts/runtime/benchmarks/link_index_maintenance.py`
  - 可复现的数据集生成、计数、wall time、内存、索引大小和 journal 写入基准。

### 修改

- `plugins/draft/skills/draft/scripts/runtime/src/draft/models.py`
  - 增加 phase、operation kind、索引结果和公开 warning 类型。
- `plugins/draft/skills/draft/scripts/runtime/src/draft/operations.py`
  - journal v4 字段、权威状态表、业务索引阶段、空目录顺序、终态和 recovery。
- `plugins/draft/skills/draft/scripts/runtime/src/draft/link_index.py`
  - schema v2、确定序列化、严格扫描输入、增量事实和反向表校验。
- `plugins/draft/skills/draft/scripts/runtime/src/draft/diagnostic_codes.py`
  - operation 版本错误和链接索引 warning 的单一机器代码来源。
- `plugins/draft/skills/draft/scripts/runtime/src/draft/cli_results.py`
  - notification result type 和统一 warning 信封。
- `plugins/draft/skills/draft/scripts/runtime/src/draft/cli.py`
  - notification 命令、warning 渲染、query/check 机会式维护路由。
- `plugins/draft/skills/draft/scripts/runtime/src/draft/checker.py`
  - 索引/flag 内部命名空间、索引 warning 和 operation 一致性检查。
- `plugins/draft/skills/draft/scripts/runtime/src/draft/query.py`
  - 可复用索引 backlink 事实的查询接口。
- `plugins/draft/skills/draft/scripts/runtime/src/draft/lifecycle.py`
  - adopt 预检查与独立 maintenance operation 的顺序。
- `plugins/draft/skills/draft/scripts/runtime/src/draft/structure.py`
  - move 使用通用 operation 索引阶段，不在 operation 返回后另行发布。
- `plugins/draft/skills/draft/scripts/runtime/src/draft/trash.py`
  - restore/trash 特殊终态与统一索引阶段的接缝。
- `plugins/draft/skills/draft/scripts/runtime/src/draft/trash_lifecycle.py`
  - 原 operation 的链接索引 recovery 和双 journal 清理顺序。
- 各生产模块对应的 runtime 单元/集成测试；
- `plugins/draft/skills/draft/SKILL.md`、迁移参考和 `docs/design.md`
  - notification、机会式维护和恢复语义。

---

### Task 1: 固定 journal v4 的链接索引状态模型

**Files:**

- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/models.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/operations.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/diagnostic_codes.py`
- Test: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_operations.py`

**Interfaces:**

- Consumes: 现有 `Journal`、`validate_journal_transition()`、`OperationKind`、
  `OperationPhase`。
- Produces:
  - `OperationKind.LINK_INDEX_UPDATE == "link-index.update"`；
  - `OperationPhase.LINK_INDEX_UPDATING`、
    `OperationPhase.LINK_INDEX_PUBLISHED`、
    `OperationPhase.LINK_INDEX_REBUILD_REQUIRED`；
  - `LinkIndexResult.PUBLISHED`、`LinkIndexResult.REBUILD_REQUIRED`；
  - `ResultWarning(code: str)`；
  - `Journal.link_index_revision: str | None`；
  - `Journal.link_index_result: LinkIndexResult | None`；
  - `Journal.link_index_sha256: str | None`；
  - `OperationResult.warnings: tuple[ResultWarning, ...]`；
  - `TrashResult.index_warnings: tuple[ResultWarning, ...]`，与既有回收生命周期 warning
    分离；
  - `CheckCode.OPERATION_JOURNAL_VERSION_UNSUPPORTED`；
  - `with_link_index_state()` 不可变转换辅助函数；
  - journal v4 的唯一解析和序列化协议。

- [ ] **Step 1: 为 revision 和字段矩阵写失败单元测试**

在现有 `_journal()`/`_journal_payload()` 测试辅助函数上增加参数，使测试能构造三个
索引字段。使用决策表逐行验证：

```python
@pytest.mark.parametrize(
    ("phase", "revision", "result", "sha256", "valid"),
    [
        (OperationPhase.VALIDATING, None, None, None, True),
        (OperationPhase.VALIDATING, "1", None, None, False),
        (OperationPhase.LINK_INDEX_UPDATING, "1", None, None, True),
        (
            OperationPhase.LINK_INDEX_PUBLISHED,
            "1",
            LinkIndexResult.PUBLISHED,
            "a" * 64,
            True,
        ),
        (
            OperationPhase.LINK_INDEX_REBUILD_REQUIRED,
            "2",
            LinkIndexResult.REBUILD_REQUIRED,
            None,
            True,
        ),
        (
            OperationPhase.ROLLED_BACK,
            "2",
            LinkIndexResult.REBUILD_REQUIRED,
            None,
            False,
        ),
    ],
)
def test_journal_link_index_field_matrix(
    phase: OperationPhase,
    revision: str | None,
    result: LinkIndexResult | None,
    sha256: str | None,
    valid: bool,
) -> None:
    journal = dataclasses.replace(
        _journal(),
        phase=phase,
        link_index_revision=revision,
        link_index_result=result,
        link_index_sha256=sha256,
    )
    if valid:
        validate_journal_kind_phase(journal, path=Path("journal.json"))
    else:
        with pytest.raises(DraftError) as caught:
            validate_journal_kind_phase(journal, path=Path("journal.json"))
        assert caught.value.code == CheckCode.OPERATION_JOURNAL_CORRUPT.value
```

另加参数化测试覆盖合法 revision `"1"`、`"9"`、`"10"`、超大十进制，以及非法
`"0"`、`"01"`、`"+1"`、`"-1"`、`"1.0"`、`"1e3"`、`" 1"`、JSON 数字。

- [ ] **Step 2: 运行定向测试并确认红灯**

```bash
DRAFT_RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$DRAFT_RUNTIME" --locked pytest \
  "$DRAFT_RUNTIME/tests/unit/test_operations.py" -q
```

Expected: 新枚举、Journal 字段或字段验证尚不存在导致 FAIL；既有测试不得被删除或放宽。

- [ ] **Step 3: 增加受约束枚举和 Journal 字段**

目标形状：

```python
class LinkIndexResult(StrEnum):
    PUBLISHED = "published"
    REBUILD_REQUIRED = "rebuild_required"


@dataclass(frozen=True)
class ResultWarning:
    code: str


class OperationKind(StrEnum):
    CREATE = "create"
    MOVE = "move"
    PROMOTE = "promote"
    COLLAPSE = "collapse"
    STATUS_SET = "status.set"
    RELATION_ADD = "relation.add"
    RELATION_REMOVE = "relation.remove"
    TRASH = "trash"
    RESTORE = "restore"
    RECOVER = "recover"
    GENERATE_CONCLUSIONS = "generate-conclusions"
    ADOPT = "adopt"
    LINK_INDEX_UPDATE = "link-index.update"


class OperationPhase(StrEnum):
    ALLOCATING = "allocating"
    PREPARED = "prepared"
    APPLYING = "applying"
    VALIDATING = "validating"
    LINK_INDEX_UPDATING = "link_index_updating"
    LINK_INDEX_PUBLISHED = "link_index_published"
    LINK_INDEX_REBUILD_REQUIRED = "link_index_rebuild_required"
    ROLLING_BACK = "rolling_back"
    TRASH_RETAINED = "trash_retained"
    TRASH_HANDOFF_PENDING = "trash_handoff_pending"
    TRASH_HANDOFF_SUCCEEDED = "trash_handoff_succeeded"
    TRASH_PURGE_COMMITTED = "trash_purge_committed"
    COMPLETED = "completed"
    ROLLED_BACK = "rolled_back"


@dataclass(frozen=True)
class TrashResult:
    operation_id: str
    phase: OperationPhase
    package: Path
    restore_expires_at: datetime | None = None
    purge_eligible_at: datetime | None = None
    warnings: tuple[LifecycleWarning, ...] = ()
    changed_paths: tuple[Path, ...] = ()
    index_warnings: tuple[ResultWarning, ...] = ()


@dataclass(frozen=True)
class Journal:
    operation_id: str
    kind: OperationKind
    phase: OperationPhase
    root_state: RootState
    observations: ObservationManifest
    mutations: tuple[JournalMutation, ...]
    remove_empty_directories: tuple[Path, ...]
    metadata: dict[str, str]
    link_index_revision: str | None
    link_index_result: LinkIndexResult | None
    link_index_sha256: str | None
    directory: Path


@dataclass(frozen=True)
class OperationResult:
    operation_id: str
    kind: str
    phase: OperationPhase
    changed_paths: tuple[Path, ...]
    warnings: tuple[ResultWarning, ...] = ()
```

把 `JOURNAL_VERSION` 设置为 `4`。序列化只输出当前 phase 允许且有值的索引键；禁止
字段必须缺失，不写 JSON `null`。解析器必须区分缺失和显式 `null`，并且只接受 v4。
版本不符使用 `operation.journal_version_unsupported`，当前版本字段非法使用
`operation.journal_corrupt`。两个代码都从 `CheckCode` 引用，不在解析器中保留裸
字面量。

- [ ] **Step 4: 把 phase 所有权和后继边集中到现有权威表**

普通结构 kind、trash、restore 和 `link-index.update` 分别使用规格中的边。关键断言：

```python
assert (
    OperationPhase.VALIDATING,
    OperationPhase.LINK_INDEX_UPDATING,
) in _EDGES_BY_KIND[OperationKind.MOVE]
assert (
    OperationPhase.LINK_INDEX_UPDATING,
    OperationPhase.ROLLING_BACK,
) not in _EDGES_BY_KIND[OperationKind.MOVE]
assert _PHASES_BY_KIND[OperationKind.LINK_INDEX_UPDATE] == frozenset(
    {
        OperationPhase.ALLOCATING,
        OperationPhase.LINK_INDEX_UPDATING,
        OperationPhase.LINK_INDEX_PUBLISHED,
        OperationPhase.LINK_INDEX_REBUILD_REQUIRED,
        OperationPhase.COMPLETED,
        OperationPhase.ROLLED_BACK,
    }
)
```

`with_phase()`、metadata/observation 辅助函数和全部 Journal 构造点必须原样传递三个
字段。字段变化只能发生在对应索引 phase 边。

- [ ] **Step 5: 补终态保留和 rollback 禁止测试**

测试 `published`/`rebuild_required` 到 `completed`、`trash_retained` 的字段原样保留，
以及任何 rollback phase 携带字段都被拒绝。测试 `link-index.update` 不能借用
`prepared`、`applying`、`validating` 或 trash phase。

- [ ] **Step 6: 运行 operation 单元套件**

```bash
DRAFT_RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$DRAFT_RUNTIME" --locked pytest \
  "$DRAFT_RUNTIME/tests/unit/test_operations.py" -q
```

Expected: PASS。

- [ ] **Step 7: 提交状态模型**

```bash
git add \
  plugins/draft/skills/draft/scripts/runtime/src/draft/models.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/operations.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/diagnostic_codes.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_operations.py
git commit -m "feat(draft): define durable link index journal states"
```

---

### Task 2: 实现索引 schema v2 与纯增量事实计算

**Files:**

- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/link_index.py`
- Test: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_link_index.py`

**Interfaces:**

- Consumes: `ResourceIdentity`、`MarkdownLink` 和 CommonMark 链接提取器。
- Produces:
  - `LinkIndex(revision, files, backlinks, directories)`；
  - `LinkIndexContent(files, backlinks, directories)`；
  - `LinkIndexScan(content, changed, stats)`；
  - `LinkIndexScanStats(read_files, read_bytes, parsed_files)`；
  - `build_index_content(documents, directories, baseline)` 纯计算接缝；
  - `materialize_index(revision, content)`；
  - schema v2 的确定序列化和严格解析。

- [ ] **Step 1: 用 schema v2 重写索引单元测试夹具**

测试目标对象不含绝对根、完整性、事件序号、解析状态或文件系统变化探针：

```python
def _index() -> LinkIndex:
    identity = ResourceIdentity(ResourceKind.PAGE, "pages/b.md")
    edge = OutboundEdge(identity, LinkSpan(6, 10))
    file = IndexedFile(
        path=Path("pages/a.md"),
        sha256="a" * 64,
        outbound=(edge,),
    )
    return LinkIndex(
        revision="3",
        files={file.path: file},
        backlinks={identity: (Backlink(file.path, edge.span),)},
        directories={Path("."): (DirectoryMember("pages", "directory"),)},
    )
```

精确断言序列化顶层键只有 `schema_version`、`revision`、`files`、`backlinks`、
`directories`，并验证相同逻辑对象产生相同 bytes。

- [ ] **Step 2: 增加纯增量反例测试**

用内存 documents 映射覆盖：

- 相同路径和 SHA-256 复用 outbound，`parsed_files == 0`；
- 一个文件变化只解析一个文件；
- 删除移除对应反向边；
- 新增产生新记录；
- 同 SHA-256 的新路径仍重新解析；
- 非 Markdown 正文不进入 documents；
- 目录成员变化使 `changed=True`；
- `files` 与 `backlinks` 任一方向不一致时拒绝解析。

代表性断言：

```python
scan = build_index_content(
    documents={Path("a.md"): b"[b](b.md)\n"},
    directories={Path("."): (DirectoryMember("a.md", "file"),)},
    baseline=_index_with_a(),
)
assert scan.stats.read_files == 1
assert scan.stats.parsed_files == 0
assert scan.changed is False
```

- [ ] **Step 3: 运行定向测试并确认红灯**

```bash
DRAFT_RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$DRAFT_RUNTIME" --locked pytest \
  "$DRAFT_RUNTIME/tests/unit/test_link_index.py" -q
```

Expected: v2 数据类型和纯计算入口尚不存在导致 FAIL。

- [ ] **Step 4: 实现最小 schema 和确定序列化**

目标核心类型：

```python
INDEX_SCHEMA_VERSION = 2


@dataclass(frozen=True)
class IndexedFile:
    path: Path
    sha256: str
    outbound: tuple[OutboundEdge, ...] = ()


@dataclass(frozen=True)
class LinkIndexContent:
    files: dict[Path, IndexedFile]
    backlinks: dict[ResourceIdentity, tuple[Backlink, ...]]
    directories: dict[Path, tuple[DirectoryMember, ...]]


@dataclass(frozen=True)
class LinkIndex:
    revision: str
    files: dict[Path, IndexedFile]
    backlinks: dict[ResourceIdentity, tuple[Backlink, ...]]
    directories: dict[Path, tuple[DirectoryMember, ...]]
    schema_version: int = INDEX_SCHEMA_VERSION
```

解析边界复用 Task 1 的 revision 规则。对路径、SHA-256、span、资源 kind、目录 member
kind 和重复项做严格验证；反向表必须精确等于 outbound 推导结果。

- [ ] **Step 5: 实现纯增量计算**

`build_index_content()` 接收已经读取的 Markdown bytes 和目录快照。比较
`path + sha256` 后只对变化文件调用链接提取和资源解析；新路径即使摘要相同也视为新
来源。统计值由生产路径真实累加，不从测试注入预期值。

- [ ] **Step 6: 运行索引单元测试**

```bash
DRAFT_RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$DRAFT_RUNTIME" --locked pytest \
  "$DRAFT_RUNTIME/tests/unit/test_link_index.py" -q
```

Expected: PASS。

- [ ] **Step 7: 提交 schema 和纯计算**

```bash
git add \
  plugins/draft/skills/draft/scripts/runtime/src/draft/link_index.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_link_index.py
git commit -m "feat(draft): define strict link index schema"
```

---

### Task 3: 建立严格文件系统扫描与 journal authority 读取

**Files:**

- Create: `plugins/draft/skills/draft/scripts/runtime/src/draft/link_index_maintenance.py`
- Create: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_link_index_maintenance.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_link_index.py`
- Test: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_link_index.py`

**Interfaces:**

- Consumes: Task 1 的 journal v4、Task 2 的 schema 和纯增量计算。
- Produces:
  - `LinkIndexAuthority(revision, result, sha256)`；
  - `LinkIndexBaseline(authority, index)`；
  - `select_link_index_authority(journals)`；
  - `read_link_index_authority(root)`；
  - `load_link_index_baseline(root)`；
  - `scan_link_index(root, baseline, known_documents=())`；
  - `next_link_index_revision(authority)`。

- [ ] **Step 1: 为 authority 选择写纯失败测试**

使用假的已解析 Journal 序列，不访问文件系统，覆盖：

```python
def test_selects_numeric_maximum_revision() -> None:
    authority = select_link_index_authority(
        (
            _terminal_journal(revision="9", result=LinkIndexResult.PUBLISHED),
            _terminal_journal(revision="10", result=LinkIndexResult.REBUILD_REQUIRED),
        )
    )
    assert authority.revision == "10"
    assert authority.result is LinkIndexResult.REBUILD_REQUIRED
```

另测无结果、超大 revision、重复 revision、冲突结果、非结果终态忽略和非法非终态拒绝。

- [ ] **Step 2: 为严格扫描写真实文件系统失败测试**

覆盖根内 managed/unmanaged Markdown、非 Markdown、目录、符号链接和 `.draft/`：

```python
def test_scan_hashes_every_markdown_without_following_symlinks(tmp_path: Path) -> None:
    _init_root(tmp_path)
    (tmp_path / "notes.md").write_text("[x](pages/a.md)\n", encoding="utf-8")
    (tmp_path / "asset.bin").write_bytes(b"x")
    (tmp_path / "linked.md").symlink_to(tmp_path / "notes.md")

    scan = scan_link_index(tmp_path, baseline=None)

    assert Path("notes.md") in scan.content.files
    assert Path("linked.md") not in scan.content.files
    assert scan.stats.read_files == len(scan.content.files)
```

增加非 UTF-8、读取失败、目录二次观察不稳定和 `.draft` 排除用例。

- [ ] **Step 3: 运行定向测试并确认红灯**

```bash
DRAFT_RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$DRAFT_RUNTIME" --locked pytest \
  "$DRAFT_RUNTIME/tests/unit/test_link_index_maintenance.py" \
  "$DRAFT_RUNTIME/tests/integration/test_link_index.py" -q
```

Expected: authority 和严格扫描入口尚不存在导致 FAIL。

- [ ] **Step 4: 实现 authority 扫描**

枚举 `.draft/operations/*/journal.json` 时：

- 只接受规范 operation ID 目录和当前 journal v4；
- 解析由 `read_journal()` 完成，不复制 schema；
- 用任意精度整数比较 revision；
- 无结果返回 absent；
- 重复或冲突 revision 返回 `operation.inconsistent_state`；
- 最新 `rebuild_required` 不读取索引制品；
- 最新 `published` 必须验证普通文件、制品 SHA-256、revision、schema 和反向表。

建议类型：

```python
@dataclass(frozen=True)
class LinkIndexAuthority:
    revision: str | None
    result: LinkIndexResult | None
    sha256: str | None


@dataclass(frozen=True)
class LinkIndexBaseline:
    authority: LinkIndexAuthority
    index: LinkIndex | None
```

- [ ] **Step 5: 实现严格 no-follow 扫描**

遍历时记录全部非 `.draft` 目录的直接成员名称和节点类型；只用 no-follow 文件描述符读取
普通 `.md`。每个文件读取并计算 SHA-256，向 Task 2 的纯增量函数传递 bytes。

`known_documents` 只允许复用“路径、SHA-256、已提取 links”均匹配的上层解析事实，不能
跳过正文读取或摘要比较。扫描期间观察到目录或文件变化时返回内部的“不稳定扫描”结果，
不得产生完整候选。

- [ ] **Step 6: 运行 authority 与扫描测试**

```bash
DRAFT_RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$DRAFT_RUNTIME" --locked pytest \
  "$DRAFT_RUNTIME/tests/unit/test_link_index_maintenance.py" \
  "$DRAFT_RUNTIME/tests/integration/test_link_index.py" -q
```

Expected: PASS。

- [ ] **Step 7: 提交严格读取边界**

```bash
git add \
  plugins/draft/skills/draft/scripts/runtime/src/draft/link_index_maintenance.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_link_index_maintenance.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_link_index.py
git commit -m "feat(draft): verify authoritative link index baselines"
```

---

### Task 4: 实现 staging、索引目标和 rebuild flag 的耐久存储

**Files:**

- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/link_index_maintenance.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/operations.py`
- Create: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_link_index_maintenance.py`
- Test: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_link_index_maintenance.py`

**Interfaces:**

- Consumes: `Journal.directory`、现有 anchored/no-follow 耐久 I/O 原语、Task 2 的确定 bytes。
- Produces:
  - `stage_link_index(journal, index) -> str`，返回制品 SHA-256；
  - `publish_staged_link_index(root, journal, expected_sha256)`；
  - `ensure_rebuild_flag(root)`；
  - `remove_rebuild_flag(root)`；
  - `cleanup_link_index_staging(journal)`。

- [ ] **Step 1: 写 staging 与目标分类的失败集成测试**

逐项断言：

- staging 精确位于 operation 目录；
- staging 写入后文件和 operation 目录均同步；
- 目标替换后 `.draft/` 和 operation 目录均同步；
- 首次发布只允许目标缺失；
- `rebuild_required` 允许缺失或普通旧目标；
- `published` 基线必须与 journal 完全匹配；
- 目标/staging/祖先为符号链接、目录或其他节点时报
  `operation.inconsistent_state`；
- 不出现 `.draft/.link-index.*` 随机文件。

使用 monkeypatch/spy 只在“同步顺序属于耐久协议”时断言交互顺序。

- [ ] **Step 2: 写 flag 决策表的失败集成测试**

测试零字节普通 flag 的幂等创建/删除、目录同步、普通 I/O warning，以及符号链接/目录/
其他节点的内部状态错误。精确文件名：

```python
REBUILD_FLAG_RELATIVE_PATH = Path(".draft/link-index-rebuild-required.flag")
```

- [ ] **Step 3: 运行定向集成测试并确认红灯**

```bash
DRAFT_RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$DRAFT_RUNTIME" --locked pytest \
  "$DRAFT_RUNTIME/tests/integration/test_link_index_maintenance.py" -q
```

Expected: 耐久存储接口尚不存在导致 FAIL。

- [ ] **Step 4: 暴露最窄的耐久 I/O 协作者边界**

优先复用 `operations.py` 已有的 anchored leaf、确定名发布和目录同步逻辑。只把
maintenance 真正需要的最小函数提升为受支持接口；不要复制一套较弱的
`Path.write_bytes()`/`os.replace()` 路径。

staging 流程必须是 create-only 或对当前 operation 自有普通文件进行精确摘要验证后的
幂等续写。目标替换使用同一文件系统内原子 rename/replace，并分别同步目标父目录和
staging 父目录。

- [ ] **Step 5: 实现 flag 的纯存在性协议**

flag 不写 JSON 或原因文本。创建成功后同步 `.draft/`；删除前先 lstat 分类，只删除
普通文件并同步 `.draft/`。普通 I/O 失败转换为内部 warning 结果，异常节点抛
`operation.inconsistent_state`。

- [ ] **Step 6: 注入每个耐久步骤的失败**

分别让 staging write、file fsync、operation-directory fsync、replace、
`.draft` fsync、flag create/delete/fsync 失败。每个测试同时断言目标、staging、flag
和异常代码，不用单一“任意 OSError”用例代替步骤矩阵。

- [ ] **Step 7: 运行 storage 集成测试**

```bash
DRAFT_RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$DRAFT_RUNTIME" --locked pytest \
  "$DRAFT_RUNTIME/tests/integration/test_link_index_maintenance.py" -q
```

Expected: PASS。

- [ ] **Step 8: 提交耐久存储**

```bash
git add \
  plugins/draft/skills/draft/scripts/runtime/src/draft/operations.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/link_index_maintenance.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_link_index_maintenance.py
git commit -m "feat(draft): publish link indexes durably"
```

---

### Task 5: 实现独立 `link-index.update` maintenance operation

**Files:**

- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/link_index_maintenance.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/operations.py`
- Test: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_link_index_maintenance.py`
- Test: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_link_index_maintenance.py`

**Interfaces:**

- Consumes: Tasks 1–4 的 authority、scan、storage 和 journal phase。
- Produces:
  - `PreparedLinkIndexUpdate(observed_revision, scan, requires_rebuild)`；
  - `prepare_link_index_update(root, known_documents=())`；
  - `commit_link_index_update(root, prepared) -> LinkIndexMaintenanceResult`；
  - `maintain_link_index(root, known_documents=()) -> LinkIndexMaintenanceResult`；
  - `LinkIndexMaintenanceResult(index, warnings, published)`。

- [ ] **Step 1: 写 maintenance 状态表失败测试**

覆盖 absent、fresh、stale、rebuild-required、published 制品矛盾：

```python
def test_no_change_does_not_allocate_operation(fake_source: FakeSource) -> None:
    prepared = prepare_link_index_update(fake_source.root)
    result = commit_link_index_update(fake_source.root, prepared)
    assert result.published is False
    assert list((fake_source.root / ".draft/operations").iterdir()) == []
```

另测首次 revision `"1"`、数值递增、扫描失败形成下一
`rebuild_required`、revision 在取锁前推进时放弃候选、不形成降级结果。

- [ ] **Step 2: 运行 maintenance 测试并确认红灯**

```bash
DRAFT_RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$DRAFT_RUNTIME" --locked pytest \
  "$DRAFT_RUNTIME/tests/unit/test_link_index_maintenance.py" \
  "$DRAFT_RUNTIME/tests/integration/test_link_index_maintenance.py" -q
```

Expected: maintenance 编排入口尚不存在导致 FAIL。

- [ ] **Step 3: 实现 scan-before-allocation 和并发令牌**

`prepare_link_index_update()` 先读取 authority 并严格扫描。fresh/no-change 直接返回无
写候选。无法得到完整索引但属于可降级的扫描结果时，不抛弃准备结果，而是返回
`scan=None, requires_rebuild=True`；journal/制品矛盾仍立即报内部状态错误。需要维护
时，`commit_link_index_update()`：

```python
@dataclass(frozen=True)
class PreparedLinkIndexUpdate:
    observed_revision: str | None
    scan: LinkIndexScan | None
    requires_rebuild: bool
```

1. 取得写锁；
2. 重读 authority；
3. 比较 `observed_revision`/absent；
4. 仅在未推进时预分配 `link-index.update` journal；
5. 写 `link_index_updating` 和下一 revision；
6. 发布或记录 `rebuild_required`；
7. 写 `completed`、清 staging、释放锁。

revision 已推进时不创建新结果；已经预分配的空 journal 若存在则写 `rolled_back`。
`maintain_link_index()` 在扫描前读取 operation lock；已有锁时直接返回未发布结果，不
读取一个正在变化的根。已经由 `check`/query 得到完整扫描事实的入口使用
`commit_link_index_update()`，并在取锁失败时保留进程内结果而不发布。

- [ ] **Step 4: 实现 maintenance 结果和 warning 去重**

使用 Task 1 的 `ResultWarning`，maintenance 结果类型为：

```python
@dataclass(frozen=True)
class LinkIndexMaintenanceResult:
    index: LinkIndex | None
    warnings: tuple[ResultWarning, ...]
    published: bool
```

`link_index.rebuild_required` 与 `link_index.rebuild_flag_update_failed` 按规格排序并各出现
一次。结果不得携带内部路径、operation ID 或底层异常文本。

- [ ] **Step 5: 实现 maintenance phase 中断测试**

在 `allocating`、`link_index_updating`、目标 replace 后、`link_index_published`、
`link_index_rebuild_required` 和 `completed` 各构造一次中断，暂时只断言持久现场；
Task 7 再接入统一 recovery。

- [ ] **Step 6: 运行 maintenance 套件**

```bash
DRAFT_RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$DRAFT_RUNTIME" --locked pytest \
  "$DRAFT_RUNTIME/tests/unit/test_link_index_maintenance.py" \
  "$DRAFT_RUNTIME/tests/integration/test_link_index_maintenance.py" -q
```

Expected: PASS。

- [ ] **Step 7: 提交 maintenance operation**

```bash
git add \
  plugins/draft/skills/draft/scripts/runtime/src/draft/link_index_maintenance.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/operations.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_link_index_maintenance.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_link_index_maintenance.py
git commit -m "feat(draft): add link index maintenance operation"
```

---

### Task 6: 把索引收敛接入通用业务 operation

**Files:**

- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/operations.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/structure.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/models.py`
- Test: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_operations.py`
- Test: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_operations.py`
- Test: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_structure.py`
- Test: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_link_index.py`

**Interfaces:**

- Consumes: `settle_link_index_for_operation(root, journal)`，返回已写入索引结果的 Journal
  和公开 warning。
- Produces:
  - `operation_changes_link_index(journal) -> bool`；
  - 通用 `_validate_and_complete()` 的索引阶段；
  - `finalize_operation()` 在终态保留索引字段和 warning；
  - 计划内空目录在索引阶段前收敛。

- [ ] **Step 1: 写实际 mutation 判定失败单元测试**

参数化覆盖 10 个已知 kind，并让判定只消费 mutation/目录事实：

```python
@pytest.mark.parametrize("kind", INDEX_AFFECTING_KINDS)
def test_markdown_mutation_requires_link_index(kind: OperationKind) -> None:
    journal = dataclasses.replace(
        _journal(),
        kind=kind,
        mutations=(_write_mutation(Path("pages/a.md")),),
    )
    assert operation_changes_link_index(journal) is True


def test_adopt_marker_only_does_not_require_link_index() -> None:
    journal = dataclasses.replace(
        _journal(),
        kind=OperationKind.ADOPT,
        mutations=(_write_mutation(Path(".draft/draft.json")),),
    )
    assert operation_changes_link_index(journal) is False
```

另测没有 mutation/目录变化的 operation 不进入索引阶段；未来 kind 产生同类事实时无需
加入另一份完整白名单。

- [ ] **Step 2: 写业务成功与索引结果顺序的失败集成测试**

对普通 write operation 注入：

- 发布成功：`validating → updating → published → completed`；
- 完整构建失败：`validating → updating → rebuild_required → completed`；
- journal 结果写失败：停在 recovery pending，退出码 3，业务 after 内容保留；
- 索引失败不会进入 `rolling_back`。

同时断言 `completed` 前锁和恢复材料仍存在。

- [ ] **Step 3: 运行定向测试并确认红灯**

```bash
DRAFT_RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$DRAFT_RUNTIME" --locked pytest \
  "$DRAFT_RUNTIME/tests/unit/test_operations.py" \
  "$DRAFT_RUNTIME/tests/integration/test_operations.py" \
  "$DRAFT_RUNTIME/tests/integration/test_structure.py" -q
```

Expected: 通用完成路径尚未进入索引 phase 导致 FAIL。

- [ ] **Step 4: 在验证后、终态前调用统一收敛**

目标控制流：

```python
validated = with_phase(journal, OperationPhase.VALIDATING)
write_journal(validated)
validate_business_after_state(root, validated)
validated = settle_planned_empty_directories(root, validated)
if operation_changes_link_index(validated):
    settlement = settle_link_index_for_operation(root, validated)
    validated = settlement.journal
    warnings = settlement.warnings
return finalize_for_kind(root, validated, warnings=warnings)
```

不要在各 CLI handler 或各业务模块分别调用索引钩子。`move_entries()` 和
`move_page()` 的正式执行以 `execute_plan()` 返回为边界，其后不得再发布索引。

- [ ] **Step 5: 把空目录收敛移到索引阶段前**

只处理 `journal.remove_empty_directories` 精确路径，深到浅执行非递归 `rmdir`。缺失视为
收敛；成功同步父目录；成员/类型变化返回 `operation.external_change`；普通 I/O 失败
保留目录并让索引读取实际状态；实际状态不可读则记录 `rebuild_required`。

- [ ] **Step 6: 让 OperationResult 携带公开 warning**

`OperationResult.warnings` 使用不含 operation ID 的稳定结果 warning。CLI 序列化在后续
Task 9 接入。`trash_page()` 把底层 `OperationResult.warnings` 投影到
`TrashResult.index_warnings`；trash 生命周期自己的 `TrashResult.warnings` 保持独立，
不把 handoff operation ID 或细节混入索引 warning。

- [ ] **Step 7: 运行通用 operation 与 structure 测试**

```bash
DRAFT_RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$DRAFT_RUNTIME" --locked pytest \
  "$DRAFT_RUNTIME/tests/unit/test_operations.py" \
  "$DRAFT_RUNTIME/tests/integration/test_operations.py" \
  "$DRAFT_RUNTIME/tests/integration/test_structure.py" \
  "$DRAFT_RUNTIME/tests/integration/test_link_index.py" -q
```

Expected: PASS。

- [ ] **Step 8: 提交通用业务接入**

```bash
git add \
  plugins/draft/skills/draft/scripts/runtime/src/draft/models.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/operations.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/structure.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_operations.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_operations.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_structure.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_link_index.py
git commit -m "feat(draft): settle indexes inside business operations"
```

---

### Task 7: 闭合 recovery、trash 和 restore 的特殊终态

**Files:**

- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/operations.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/link_index_maintenance.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/trash.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/trash_lifecycle.py`
- Test: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_operations.py`
- Test: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash.py`
- Test: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash_lifecycle.py`
- Test: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_link_index_maintenance.py`

**Interfaces:**

- Consumes: Task 1 phase、Task 4 staging、Task 6 业务终态。
- Produces:
  - `recover_link_index_phase(root, journal, *, retain_terminal_lock)`；
  - `link-index.update` 的完整 recovery；
  - trash 在索引结果后进入 `trash_retained`；
  - restore 在索引结果后进入 `completed` 再执行双 journal 清理。

- [ ] **Step 1: 写 phase-by-phase recovery 失败测试**

为每个 phase 建立真实持久现场并调用公开 recovery：

```python
@pytest.mark.parametrize(
    ("phase", "expected"),
    [
        (OperationPhase.LINK_INDEX_UPDATING, LinkIndexResult.REBUILD_REQUIRED),
        (OperationPhase.LINK_INDEX_PUBLISHED, LinkIndexResult.PUBLISHED),
        (
            OperationPhase.LINK_INDEX_REBUILD_REQUIRED,
            LinkIndexResult.REBUILD_REQUIRED,
        ),
    ],
)
def test_recover_link_index_phase(
    root: Path,
    phase: OperationPhase,
    expected: LinkIndexResult,
) -> None:
    operation_id = _persist_interrupted_operation(root, phase=phase)
    result = recover(root)
    journal = read_journal(operation_journal_path(root, operation_id))
    assert journal.link_index_result is expected
    assert result.phase is OperationPhase.COMPLETED
```

每个用例再调用一次 `recover`，断言结果、revision、flag 和制品不变。

- [ ] **Step 2: 写 staging 恢复所有权测试**

覆盖：

- `allocating` 不应有 staging；出现未知内部文件时报不一致；
- `updating` 下 staging 缺失、完整、部分写入或目标已替换均进入 `rebuild_required`；
- `published` 必须验证目标 SHA-256/revision；
- 只清当前 operation 的 `link-index.staged.json`；
- 不删除其他 operation 或 `.draft` 未知文件。

- [ ] **Step 3: 写 trash/restore 终态失败测试**

断言：

- trash：索引结果先于 `trash_retained`；
- restore：索引结果先于 restore `completed`，然后才解关联 trash journal；
- `retain_terminal_lock=True` 时索引终态已耐久但锁和材料仍由双 journal 协调层拥有；
- `recover` 不产生独立 revision。

- [ ] **Step 4: 运行恢复测试并确认红灯**

```bash
DRAFT_RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$DRAFT_RUNTIME" --locked pytest \
  "$DRAFT_RUNTIME/tests/integration/test_operations.py" \
  "$DRAFT_RUNTIME/tests/integration/test_trash.py" \
  "$DRAFT_RUNTIME/tests/integration/test_trash_lifecycle.py" \
  "$DRAFT_RUNTIME/tests/integration/test_link_index_maintenance.py" -q
```

Expected: recovery 尚不认识索引 phase 或特殊终态顺序错误导致 FAIL。

- [ ] **Step 5: 在统一锁内恢复分派中处理索引 phase**

在 adopt 特判之后、通用 mutation phase 之前分派三个索引 phase。`updating` 不读取
目标外观猜测发布成功，直接耐久记录同 revision 的 `rebuild_required`。`published`
验证 journal 绑定制品；`rebuild_required` 补 flag。三者最后按原 kind 进入正确终态。

- [ ] **Step 6: 接入 maintenance operation recovery**

`OperationKind.LINK_INDEX_UPDATE` 的 `allocating` 可以 `rolled_back`；三个索引 phase
复用同一恢复函数；`completed` 只做幂等 staging/锁清理。maintenance 不进入 mutation
artifact 恢复。

- [ ] **Step 7: 保持 trash/restore 锁所有权**

复用 `recover_locked_operation(..., retain_terminal_lock=True)`，不要释放后重新取得锁。
索引 phase 完成后由现有 restore 协调决策继续验证、解关联、清材料和解锁。

- [ ] **Step 8: 运行恢复和回收套件**

```bash
DRAFT_RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$DRAFT_RUNTIME" --locked pytest \
  "$DRAFT_RUNTIME/tests/integration/test_operations.py" \
  "$DRAFT_RUNTIME/tests/integration/test_trash.py" \
  "$DRAFT_RUNTIME/tests/integration/test_trash_lifecycle.py" \
  "$DRAFT_RUNTIME/tests/integration/test_link_index_maintenance.py" -q
```

Expected: PASS。

- [ ] **Step 9: 提交恢复闭环**

```bash
git add \
  plugins/draft/skills/draft/scripts/runtime/src/draft/operations.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/link_index_maintenance.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/trash.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/trash_lifecycle.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_operations.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash_lifecycle.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_link_index_maintenance.py
git commit -m "feat(draft): recover durable link index states"
```

---

### Task 8: 增加外部文件变更 notification

**Files:**

- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/cli.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/cli_results.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/link_index_maintenance.py`
- Test: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_cli.py`
- Test: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_cli.py`
- Test: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_link_index_maintenance.py`

**Interfaces:**

- Consumes: `maintain_link_index()`。
- Produces:
  - CLI `draft notify files-changed --root ROOT`；
  - `ResultType.FILES_CHANGED_NOTIFICATION`；
  - `notify_files_changed(root) -> None`；
  - `is_expected_link_index_maintenance_error(error) -> bool`。

- [ ] **Step 1: 写 parser 和分派失败测试**

精确断言：

```python
def test_notify_files_changed_parser_has_no_path_operands() -> None:
    args = build_parser().parse_args(
        ["notify", "files-changed", "--root", "/draft", "--json"]
    )
    assert args.command == "notify"
    assert args.notify_command == "files-changed"
    assert not hasattr(args, "paths")
```

路径参数、stdin 清单和 `--dry-run` 均不是合法协议。

- [ ] **Step 2: 写 notification 结果矩阵失败集成测试**

candidate/registered、fresh、stale、无索引、rebuild-required、已有锁、扫描失败、flag
失败、journal 耐久失败和制品不一致都必须在 notification 已送达后返回：

```json
{"result_type":"files_changed_notification"}
```

退出码为 0，载荷没有 `warnings`、revision、路径或索引结果。参数错误、根不存在、marker
损坏仍按现有错误信封返回。

- [ ] **Step 3: 运行 CLI 测试并确认红灯**

```bash
DRAFT_RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$DRAFT_RUNTIME" --locked pytest \
  "$DRAFT_RUNTIME/tests/unit/test_cli.py" \
  "$DRAFT_RUNTIME/tests/integration/test_cli.py" \
  "$DRAFT_RUNTIME/tests/integration/test_link_index_maintenance.py" -q
```

Expected: notification 命令尚不存在导致 FAIL。

- [ ] **Step 4: 实现 notification 用例边界**

```python
def notify_files_changed(root: Path) -> None:
    try:
        if read_lock(root) is not None:
            return
        maintain_link_index(root)
    except DraftError as error:
        if not is_expected_link_index_maintenance_error(error):
            raise
        return
```

不要写 `except Exception`。`is_expected_link_index_maintenance_error()` 的单一代码集合
使用以下精确值，并由单元测试逐码枚举：

```python
_NOTIFICATION_ABSORBED_CODES = frozenset(
    {
        CheckCode.OPERATION_RECOVERY_REQUIRED.value,
        CheckCode.OPERATION_JOURNAL_CORRUPT.value,
        CheckCode.OPERATION_INCONSISTENT_STATE.value,
        CheckCode.OPERATION_DURABILITY_FAILED.value,
        CheckCode.PATH_SYMLINK_ESCAPE.value,
        "operation.journal_version_unsupported",
        "operation.external_change",
        "operation.index_update_failed",
    }
)
```

`operation.plan_invalid` 等程序契约错误不得被吸收。根选择和 marker 验证发生在调用该
函数之前。实施中若 maintenance 产生新的稳定领域错误，必须先按规格判断它属于消费者
结果还是程序契约错误，再更新这个单一集合及其测试；不得在 CLI 另建第二份清单。

- [ ] **Step 5: 绕过普通命令生命周期前置**

`main()` 对 notification 完成 root 选择后直接分派，不调用回收生命周期推进，不自动
recover。已有锁由消费者内部处理并被 notification 吸收。

- [ ] **Step 6: 实现最小输出**

`ResultType` 增加 `"files_changed_notification"`。普通输出只表达通知送达；JSON 只含
`result_type`。不得把消费者 warning 添加到 payload 或人类输出。

- [ ] **Step 7: 运行 notification 测试**

```bash
DRAFT_RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$DRAFT_RUNTIME" --locked pytest \
  "$DRAFT_RUNTIME/tests/unit/test_cli.py" \
  "$DRAFT_RUNTIME/tests/integration/test_cli.py" \
  "$DRAFT_RUNTIME/tests/integration/test_link_index_maintenance.py" -q
```

Expected: PASS。

- [ ] **Step 8: 提交 notification**

```bash
git add \
  plugins/draft/skills/draft/scripts/runtime/src/draft/cli.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/cli_results.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/link_index_maintenance.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_cli.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_cli.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_link_index_maintenance.py
git commit -m "feat(draft): accept external file change notifications"
```

---

### Task 9: 接入 `check`、query 和 adopt 的机会式维护

**Files:**

- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/checker.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/query.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/lifecycle.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/cli.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/cli_results.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/diagnostic_codes.py`
- Test: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_checker.py`
- Test: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_query.py`
- Test: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_checker.py`
- Test: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_queries.py`
- Test: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_candidate_migration.py`

**Interfaces:**

- Consumes: `prepare_link_index_update()`、`commit_link_index_update()` 和可选
  `known_documents`。
- Produces:
  - `check_draft()` 的索引 warning/error issue；
  - `backlinks_from_index()`；
  - `page_summary()` 可注入 backlink count；
  - query 成功信封的 `warnings`；
  - adopt 预检查先完成独立 maintenance operation；
  - `CheckCode.LINK_INDEX_REBUILD_REQUIRED`；
  - `CheckCode.LINK_INDEX_REBUILD_FLAG_UPDATE_FAILED`。

- [ ] **Step 1: 写入口路由决策表失败测试**

参数化断言：

| 入口 | 尝试维护 |
| --- | --- |
| check | 是 |
| query backlinks | 是 |
| query page | 是 |
| adopt 预检查 | 是 |
| query links | 否 |
| query relations/status/recoverable | 否 |
| dry-run | 否 |
| init | 否 |
| recover | 仅恢复已有 operation |

测试不能只搜索源码文本；通过 mock/spy 稳定维护用例边界验证真实调用。

- [ ] **Step 2: 写 check 状态决策表失败集成测试**

覆盖 absent、fresh、stale 后发布成功、维护后仍 rebuild-required、flag warning、制品
不一致、journal 损坏和 recovery pending。精确断言 warning/error severity 以及 adopt
是否被阻止。

- [ ] **Step 3: 写 query 等价性失败测试**

同一根分别在 fresh、absent、stale 和 rebuild-required 状态运行 `query backlinks` 与
`query page`，结果必须与完整事实计算相同。维护成功不返回 warning；耐久
rebuild-required 返回一次 warning；journal 耐久失败非零退出。

`query links` 和无关 query 运行前后 `.draft/operations`、索引和 flag 摘要必须不变。

- [ ] **Step 4: 运行机会式维护测试并确认红灯**

```bash
DRAFT_RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$DRAFT_RUNTIME" --locked pytest \
  "$DRAFT_RUNTIME/tests/unit/test_checker.py" \
  "$DRAFT_RUNTIME/tests/unit/test_query.py" \
  "$DRAFT_RUNTIME/tests/integration/test_checker.py" \
  "$DRAFT_RUNTIME/tests/integration/test_queries.py" \
  "$DRAFT_RUNTIME/tests/integration/test_candidate_migration.py" -q
```

Expected: 读取入口尚未调用 maintenance 或 warning 协议缺失导致 FAIL。

- [ ] **Step 5: 让上层解析事实成为可验证 scan seed**

从 `DraftGraph`/tolerant view 提取每个已解析页面的路径、正文 SHA-256 和
`MarkdownLink`。把它作为 `known_documents` 交给严格扫描；scanner 仍重新读取全部
Markdown 并比较 SHA-256，只有摘要匹配时才复用已解析 links。

这一步只减少重复 CommonMark 解析，不得跳过正文读取。

- [ ] **Step 6: 用索引事实服务 backlinks**

`query backlinks` 和 `query page` 的 backlink count 在索引完整且严格验证后读取反向
表；否则使用本次完整扫描事实。保留现有 candidate/registered 阻碍、分页、count-only
和机器信封语义。

- [ ] **Step 7: 统一 warning 渲染**

写命令和 query 成功 payload 的 warning 使用：

```json
{"code": "link_index.rebuild_required", "severity": "warning"}
```

或：

```json
{"code": "link_index.rebuild_flag_update_failed", "severity": "warning"}
```

不含 operation ID、内部路径或异常文本。`check` 把同码投影为 `CheckIssue`。普通成功
省略 `warnings`；notification 永远不消费这套公开投影。

- [ ] **Step 8: 保持 dry-run 无持久副作用**

为所有支持 dry-run 的命令记录执行前后 `.draft` 树摘要，断言 journal、index、flag、
lock 均不变化。dry-run 可以严格扫描并使用进程内事实，但不调用 commit。

- [ ] **Step 9: 运行 checker/query/adopt 测试**

```bash
DRAFT_RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$DRAFT_RUNTIME" --locked pytest \
  "$DRAFT_RUNTIME/tests/unit/test_checker.py" \
  "$DRAFT_RUNTIME/tests/unit/test_query.py" \
  "$DRAFT_RUNTIME/tests/integration/test_checker.py" \
  "$DRAFT_RUNTIME/tests/integration/test_queries.py" \
  "$DRAFT_RUNTIME/tests/integration/test_candidate_migration.py" -q
```

Expected: PASS。

- [ ] **Step 10: 提交机会式维护**

```bash
git add \
  plugins/draft/skills/draft/scripts/runtime/src/draft/checker.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/query.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/lifecycle.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/cli.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/cli_results.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/diagnostic_codes.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_checker.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_query.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_checker.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_queries.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_candidate_migration.py
git commit -m "feat(draft): maintain link indexes during reads"
```

---

### Task 10: 审计全部 mutation 入口与内部命名空间

**Files:**

- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/checker.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_page_commands.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_structure.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_conclusions.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_link_index.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_checker.py`

**Interfaces:**

- Consumes: 通用 `execute_plan()` 索引阶段。
- Produces: 10 类当前 mutation kind 的端到端覆盖；`.draft` 对索引、flag 和 staging 的
  精确 allowlist。

- [ ] **Step 1: 写 10 类命令的参数化验收**

对 create、move、promote、collapse、status set、relation add、relation remove、
generate-conclusions、trash、restore 各构造最小有效根。每个用例断言：

- 实际 mutation 产生下一 revision；
- 索引制品 SHA-256 与 terminal journal 匹配；
- backlinks 与当前根一致；
- no-op 不产生 revision；
- 发布失败形成 `rebuild_required` 且业务结果保留。

不要在测试中复制生产“哪些 kind 相关”的完整集合；测试参数由用户可见入口独立列出，
生产判定从 mutation 事实推导。

- [ ] **Step 2: 写 `.draft` 命名空间失败测试**

允许：

- `draft.json`；
- `operation.lock`；
- `operations/`；
- `trash/`；
- `link-index.json`；
- `link-index-rebuild-required.flag`。

operation 目录只允许既有 journal/recovery 材料和精确
`link-index.staged.json`。未知索引形似文件、随机临时文件、错误类型和符号链接均报告
`internal.unexpected_entry` 或更具体 operation 不一致。

- [ ] **Step 3: 运行入口审计测试并确认红灯**

```bash
DRAFT_RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$DRAFT_RUNTIME" --locked pytest \
  "$DRAFT_RUNTIME/tests/integration/test_page_commands.py" \
  "$DRAFT_RUNTIME/tests/integration/test_structure.py" \
  "$DRAFT_RUNTIME/tests/integration/test_conclusions.py" \
  "$DRAFT_RUNTIME/tests/integration/test_trash.py" \
  "$DRAFT_RUNTIME/tests/integration/test_link_index.py" \
  "$DRAFT_RUNTIME/tests/integration/test_checker.py" -q
```

Expected: 尚未覆盖的入口或内部 allowlist 导致 FAIL。

- [ ] **Step 4: 修正共享责任边界**

所有入口都必须经过通用 operation 完成器；不得在入口尾部追加命令专用刷新。
future-like kind 反例必须由 `operation_changes_link_index()` 的 mutation/目录事实
判定统一覆盖，而不是扩充入口白名单。

- [ ] **Step 5: 更新 checker 的精确节点协议**

checker 读取 journal v4 和索引 authority 一次，复用同一索引供 operation、trash 和
索引检查使用。索引/flag/staging 的节点类型和所有权必须与 maintenance 读取边界一致，
不得复制另一套较宽判定。

- [ ] **Step 6: 运行入口审计套件**

```bash
DRAFT_RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$DRAFT_RUNTIME" --locked pytest \
  "$DRAFT_RUNTIME/tests/integration/test_page_commands.py" \
  "$DRAFT_RUNTIME/tests/integration/test_structure.py" \
  "$DRAFT_RUNTIME/tests/integration/test_conclusions.py" \
  "$DRAFT_RUNTIME/tests/integration/test_trash.py" \
  "$DRAFT_RUNTIME/tests/integration/test_link_index.py" \
  "$DRAFT_RUNTIME/tests/integration/test_checker.py" -q
```

Expected: PASS。

- [ ] **Step 7: 提交入口与 checker 闭环**

```bash
git add \
  plugins/draft/skills/draft/scripts/runtime/src/draft/checker.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_page_commands.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_structure.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_conclusions.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_link_index.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_checker.py
git commit -m "test(draft): cover every link index mutation entry"
```

---

### Task 11: 完成故障注入、并发和严格新鲜度反例

**Files:**

- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_link_index_maintenance.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_operations.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_cli.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_link_index.py`

**Interfaces:**

- Consumes: 已完成的公开 CLI、operation、maintenance 和 recovery 接缝。
- Produces: 规格中完整耐久步骤、并发和摘要碰撞验收矩阵。

- [ ] **Step 1: 建立可枚举的耐久故障注入器**

在测试支持代码中定义受真实 I/O 接口约束的 spy/fake，只允许以下命名步骤：

```python
class DurabilityStep(StrEnum):
    STAGING_WRITE = "staging_write"
    STAGING_FSYNC = "staging_fsync"
    STAGING_DIRECTORY_FSYNC = "staging_directory_fsync"
    INDEX_REPLACE = "index_replace"
    INDEX_DIRECTORY_FSYNC = "index_directory_fsync"
    JOURNAL_WRITE = "journal_write"
    JOURNAL_DIRECTORY_FSYNC = "journal_directory_fsync"
    FLAG_CREATE = "flag_create"
    FLAG_DELETE = "flag_delete"
    FLAG_DIRECTORY_FSYNC = "flag_directory_fsync"
```

若生产代码没有一个稳定耐久协作者边界，先在 Task 4 的最窄边界收敛；不要 monkeypatch
任意内部调用图。

- [ ] **Step 2: 参数化每个步骤的失败结果**

每个步骤同时断言：

- 业务文件 before/after 摘要；
- journal phase 和三个索引字段；
- revision；
- 索引制品 SHA-256；
- flag；
- staging；
- lock；
- CLI 退出码/warning；
- 下一次命令是否接受索引。

- [ ] **Step 3: 覆盖三个进程中断窗口**

精确构造：

1. `link_index_updating` 已耐久、staging 尚未写；
2. 目标 replace/目录同步完成、`link_index_published` 尚未写；
3. `link_index_published` 已耐久、原成功终态尚未写。

每个窗口执行一次和两次 recovery，断言幂等。

- [ ] **Step 4: 覆盖 revision 并发**

两个读取入口都从 revision `"7"` 扫描；第一个发布 `"8"`；第二个取得锁后必须放弃
候选，不发布 `"9"`，也不写 `rebuild_required`。另测持锁 operation 中断时 notification
仍固定成功而普通 query/check 显示 recovery 状态。

- [ ] **Step 5: 覆盖严格摘要碰撞反例**

在测试中直接控制元数据，构造内容变化但 size、inode 探针值和 mtime 探针值相同的
文件。断言 fresh 检查比较 SHA-256 并拒绝旧 backlink。测试预期必须使用独立计算的当前
正文链接，不调用生产索引计算路径生成预期。

- [ ] **Step 6: 运行完整故障矩阵**

```bash
DRAFT_RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$DRAFT_RUNTIME" --locked pytest \
  "$DRAFT_RUNTIME/tests/integration/test_link_index_maintenance.py" \
  "$DRAFT_RUNTIME/tests/integration/test_operations.py" \
  "$DRAFT_RUNTIME/tests/integration/test_cli.py" \
  "$DRAFT_RUNTIME/tests/integration/test_link_index.py" -q
```

Expected: PASS。

- [ ] **Step 7: 提交故障验收**

```bash
git add \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_link_index_maintenance.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_operations.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_cli.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_link_index.py
git commit -m "test(draft): verify link index crash recovery"
```

---

### Task 12: 交付性能基准

**Files:**

- Create: `plugins/draft/skills/draft/scripts/runtime/benchmarks/link_index_maintenance.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/pyproject.toml`
- Test: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_link_index_maintenance.py`

**Interfaces:**

- Consumes: `LinkIndexScanStats`、公开 maintenance/query/check 入口。
- Produces: 可复现 JSON benchmark 结果；不进入默认单元或集成门禁。

- [ ] **Step 1: 写 benchmark 参数和结果 schema 的失败测试**

基准输出至少包含：

```python
@dataclass(frozen=True)
class BenchmarkResult:
    scenario: str
    markdown_files: int
    width: int
    read_files: int
    read_bytes: int
    parsed_files: int
    wall_seconds: float
    peak_index_bytes: int
    index_file_bytes: int
    journal_write_count: int
    journal_write_bytes: int
```

集成 smoke test 只运行 10 个文件，验证固定种子、结果字段和计数不变量；不对 wall time
设阈值。

- [ ] **Step 2: 运行 smoke test 并确认红灯**

```bash
DRAFT_RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$DRAFT_RUNTIME" --locked pytest \
  "$DRAFT_RUNTIME/tests/integration/test_link_index_maintenance.py" \
  -q -k benchmark_smoke
```

Expected: benchmark 入口尚不存在导致 FAIL。

- [ ] **Step 3: 实现数据集和场景**

使用固定种子生成 100、1,000、5,000 个 Markdown 以及宽目录，覆盖：

- 首次构建；
- fresh `query backlinks`；
- fresh `check`；
- no-op notification；
- 修改 1 个 Markdown；
- 修改 1% Markdown；
- 新增、删除、重命名；
- 目录成员/节点类型变化；
- rebuild-required 后恢复；
- check 机会式维护；
- journal 数量增长。

基准不得安装新依赖；用 `time.perf_counter()`、`tracemalloc`、文件大小和生产 stats。

- [ ] **Step 4: 增加显式 benchmark 命令**

在 `pyproject.toml` 只登记必要的脚本入口，或直接提供以下稳定运行方式：

```bash
DRAFT_RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$DRAFT_RUNTIME" --locked python \
  "$DRAFT_RUNTIME/benchmarks/link_index_maintenance.py" \
  --files 100 1000 5000 \
  --output .scratch/link-index-benchmark.json
```

不要把 5,000 文件基准加入默认 pytest。

- [ ] **Step 5: 运行完整基准并保存摘要**

运行上面的命令。检查每个 fresh/no-op 场景：

- `read_files == markdown_files`；
- `parsed_files == 0`；
- no-op notification 的 journal 写入为 0。

变化场景检查 `parsed_files` 等于实际变化文件数；记录结果，不声明跨机器绝对 SLA。

- [ ] **Step 6: 运行 benchmark smoke test**

```bash
DRAFT_RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$DRAFT_RUNTIME" --locked pytest \
  "$DRAFT_RUNTIME/tests/integration/test_link_index_maintenance.py" \
  -q -k benchmark_smoke
```

Expected: PASS。

- [ ] **Step 7: 提交基准工具**

```bash
git add \
  plugins/draft/skills/draft/scripts/runtime/benchmarks/link_index_maintenance.py \
  plugins/draft/skills/draft/scripts/runtime/pyproject.toml \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_link_index_maintenance.py
git commit -m "perf(draft): benchmark strict link index maintenance"
```

---

### Task 13: 同步使用文档并运行全部门禁

**Files:**

- Modify: `plugins/draft/skills/draft/SKILL.md`
- Modify: `plugins/draft/skills/draft/references/migration.md`
- Modify: `docs/design.md`
- Modify: `CONTEXT.md`（仅在实现形成新的领域词汇时）
- Test: runtime 与 adapter 全部门禁。

**Interfaces:**

- Consumes: 完整 CLI 和恢复协议。
- Produces: 当前使用文档、迁移工作流、门禁证据和实现交付摘要。

- [ ] **Step 1: 更新命令与工作流文档**

文档只描述当前行为：

- 外部批量编辑稳定后，调用方可以主动运行
  `draft notify files-changed --root ROOT`；
- notification 不接收路径且不返回消费者状态；
- `check` 和相关 query 可以维护内部索引；
- dry-run 不写内部状态；
- `recover` 只处理 recovery pending；
- 索引降级 warning 不阻止业务成功或 adopt；
- 程序输出不把 notification 描述为错误恢复步骤。

- [ ] **Step 2: 检查领域词汇和 Agent 规则**

`CONTEXT.md` 只保留领域术语，不加入 schema 字段表、文件同步步骤或测试矩阵。若实现
没有产生新领域概念，不修改它。检查作用域内是否存在受影响的 `AGENTS.md`；只有项目
规则发生变化时才更新。

- [ ] **Step 3: 运行 runtime 单元测试**

```bash
DRAFT_RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$DRAFT_RUNTIME" --locked pytest \
  "$DRAFT_RUNTIME/tests/unit" -q
```

Expected: PASS。

- [ ] **Step 4: 运行 adapter 单元测试**

```bash
uv run --project plugins/draft --locked pytest \
  plugins/draft/scripts/tests/unit -q
```

Expected: PASS。

- [ ] **Step 5: 运行 runtime 集成测试**

```bash
DRAFT_RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$DRAFT_RUNTIME" --locked pytest \
  "$DRAFT_RUNTIME/tests/integration" -q -m "not network"
```

Expected: PASS；记录 deselected 数量。

- [ ] **Step 6: 运行 adapter 集成测试**

```bash
uv run --project plugins/draft --locked pytest \
  plugins/draft/scripts/tests/integration -q -m "not network"
```

Expected: PASS；记录 deselected 数量。

- [ ] **Step 7: 运行格式、类型和 adapter 一致性门禁**

```bash
DRAFT_RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$DRAFT_RUNTIME" --locked ruff check \
  "$DRAFT_RUNTIME/src" "$DRAFT_RUNTIME/tests"
uv run --project "$DRAFT_RUNTIME" --locked pyright \
  -p "$DRAFT_RUNTIME/pyproject.toml"
bash plugins/draft/scripts/check-adapters.sh
```

Expected: 全部退出码 0。

- [ ] **Step 8: 做端到端契约闭合审计**

沿以下链逐项核对实际代码和测试，不以“各任务通过”代替接缝审查：

```text
根内事实
→ 严格扫描与增量候选
→ journal authority 与 revision
→ 业务/maintenance operation
→ staging 和目标发布
→ published/rebuild-required
→ flag
→ 原有成功终态
→ warning/错误
→ recover
→ 下一次 fresh 证明
```

特别核对 normal、failure、retry、recovery 和 cleanup 是否消费同一字段与状态表。

- [ ] **Step 9: 检查最终 diff**

```bash
git diff --check
git status --short
```

逐项确认没有无关文件、随机临时索引、多版本 schema 读取、notification 路径参数、
mtime/size fresh 证明或命令专用索引后置钩子。

- [ ] **Step 10: 提交文档和最终门禁改动**

```bash
git add \
  plugins/draft/skills/draft/SKILL.md \
  plugins/draft/skills/draft/references/migration.md \
  docs/design.md
git commit -m "docs(draft): document durable link index maintenance"
```

最终交付报告必须列出提交、实际测试命令与结果、网络排除项、基准摘要，以及任何尚未
核验的前提。测试全绿不能替代规格覆盖审计。
