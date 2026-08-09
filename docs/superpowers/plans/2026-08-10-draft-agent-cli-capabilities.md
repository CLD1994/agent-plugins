# Draft Agent CLI Capabilities Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 Draft runtime 增加低 token 的结构查询、安全创建、状态与关系修改、按需结论生成、统一 dry-run 和静默回收生命周期，同时让所有命令共享类型化状态、关系与正文资源规则。

**Architecture:** Markdown 继续是唯一事实来源；每次命令按需建立一次内存页面或完整图，不保存跨命令缓存。新增聚焦模块分别拥有稳定词汇、正文资源解析、查询、公开计划影响、结论生成和页面元数据修改；现有 graph/checker/structure/trash/CLI 通过这些边界协作，所有正式写入继续走 `OperationPlan` 与崩溃恢复日志。

**Tech Stack:** Python `>=3.12,<3.15`、标准库 `pathlib`/`urllib.parse`/`enum.StrEnum`/`dataclasses`、markdown-it-py `>=4.2,<4.3`、Send2Trash `>=2.1,<2.2`、pytest、pytest-mock、Pyright strict、Ruff、uv。

**Approved Spec:** `docs/superpowers/specs/2026-08-10-draft-agent-cli-capabilities-design.md`

## Global Constraints

- 开始每个任务前依次读取根 `AGENTS.md`、`plugins/draft/AGENTS.md` 和批准规格；若实施过程中这些文件变化，重新读取后再继续。
- 不引入 Obsidian、数据库、后台索引、文件监视器、跨命令缓存或新第三方依赖；不得修改 `pyproject.toml` 依赖窗口与 `uv.lock`。
- 保持 `FORMAT_VERSION = 1`；项目仍在开发，不实现旧 `output`/`产出` 格式兼容、迁移命令或双读双写。
- CLI/JSON 使用稳定 `StatusCode`、`RelationCode`、资源类型、范围和目标状态；中文标签只存在于生产本地化映射、Markdown 与人类渲染边界。
- 正文资源链接允许 Draft 外路径、Linux/Windows 相对路径及两种绝对路径；正式关系和受管理页面参数仍只能指向同一 Draft 内页面。
- `conclusions.md` 不实时维护；只有 `generate-conclusions` 完整重建，普通结构命令不得逐条改写结论列表，也不得让旧结论链接阻止 move/trash。
- 所有已登记 Draft 命令，包括查询和 `--dry-run`，在业务逻辑前静默推进回收生命周期；临时移交失败隐藏并保留重试，损坏状态明确失败。
- 查询无法证明结果完整时整体失败；目标的 `missing`、`invalid`、`unavailable`、`unchecked` 是成功查询到的事实。
- 人类输出测试只验证动态事实、顺序、计数、单行和转义；不得复制完整中文文案。稳定 JSON 字段与 wire value 必须精确断言。
- 单元测试同进程、无真实文件系统、无子进程；用注入的 existence probe、时间和受真实接口约束的 stub/fake。真实文件、符号链接、操作日志、恢复和 CLI 组合属于 integration。
- 结构和依赖边界使用 AST、真实导入或运行行为验证，不以源码普通文本包含/排除代替语义检查。
- 每个任务按红—绿—重构执行并单独提交。门禁失败先定位根因，不删除测试、不放宽 Pyright/Ruff、不吞错误。
- 工作区可能包含用户或其他 Agent 的修改；任务开始记录 `git status --short`，只暂存本任务列出的文件，不清理或覆盖其他改动。

## File Structure

### 新建的生产模块

- `plugins/draft/skills/draft/scripts/runtime/src/draft/vocabulary.py`
  - 唯一拥有稳定状态/关系代码与当前中文 Markdown 标签映射。
- `plugins/draft/skills/draft/scripts/runtime/src/draft/resources.py`
  - 区分正式关系目标与正文资源，解析 Linux/Windows 路径、资源身份、范围、可用状态和移动后重写。
- `plugins/draft/skills/draft/scripts/runtime/src/draft/query.py`
  - 定义紧凑查询结果并从单页或完整 `DraftGraph` 计算 page/links/backlinks/relations/status。
- `plugins/draft/skills/draft/scripts/runtime/src/draft/plan_effects.py`
  - 定义 `PlanEffect`、公开 changed paths、统一分页和 JSON 影响序列化所需的纯逻辑。
- `plugins/draft/skills/draft/scripts/runtime/src/draft/conclusions.py`
  - 规划和执行按需结论生成，拥有固定文件形状、导航顺序和 `generated` 所有权收敛。
- `plugins/draft/skills/draft/scripts/runtime/src/draft/page_create.py`
  - 校验新叶子路径、生成页面骨架并原子更新父导航。
- `plugins/draft/skills/draft/scripts/runtime/src/draft/page_metadata.py`
  - 规划和执行状态、依赖、取代、质疑的幂等修改与最终图校验。
- `plugins/draft/skills/draft/scripts/runtime/src/draft/cli_results.py`
  - 将查询、计划和写结果转换为稳定 JSON 与最小人类输出；不读取 Draft。

### 主要修改模块

- `src/draft/models.py`：页面模型改用稳定代码；`OperationPlan` 携带公开 `effects`。
- `src/draft/markdown.py`：中文标签边界、机器区域编辑、普通文本转义、结论导航插入。
- `src/draft/graph.py`：构建页面图与正文资源反向索引，提供单页/完整快照加载边界，读取结论所有权状态。
- `src/draft/checker.py`：删除 output 规则，复用资源状态，检查懒生成结论结构并容忍结论列表过时。
- `src/draft/structure.py`、`src/draft/trash.py`：使用资源重写和公开 effects，跳过机器结论列表。
- `src/draft/lifecycle.py`：初始化/接管写入 `conclusions_state=not_generated`，不预建结论页。
- `src/draft/trash_lifecycle.py`：通用静默前置推进、只恢复中断操作、可恢复项查询。
- `src/draft/operations.py`：允许结论生成计划原子写指定 marker，验证公开 effects 与低层 mutation 一致。
- `src/draft/cli.py`：只保留 argparse、命令编排、前置推进、退出码和错误边界。
- `src/draft/diagnostic_codes.py`、`src/draft/diagnostics.py`：删除 output 诊断，增加资源与结论结构诊断。

### 新建测试文件

- `tests/unit/test_vocabulary.py`
- `tests/unit/test_resources.py`
- `tests/unit/test_query.py`
- `tests/unit/test_plan_effects.py`
- `tests/unit/test_conclusions.py`
- `tests/unit/test_page_create.py`
- `tests/unit/test_page_metadata.py`
- `tests/integration/test_queries.py`
- `tests/integration/test_page_commands.py`
- `tests/integration/test_conclusions.py`

现有测试文件只在其既有契约被本设计改变时修改；不要把全部新场景继续堆入已经很大的 `test_cli.py`、`test_checker.py` 或 `test_structure.py`。

---

### Task 1: 建立稳定状态/关系词汇并删除 output 协议

**Files:**
- Create: `plugins/draft/skills/draft/scripts/runtime/src/draft/vocabulary.py`
- Create: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_vocabulary.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/models.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/markdown.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/graph.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/checker.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/structure.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/diagnostic_codes.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/diagnostics.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_markdown.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_graph.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_checker.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_diagnostics.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_structure.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_trash.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_checker.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_cli.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_lifecycle.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_operations.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_structure.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_distribution_contract.py`

**Interfaces:**
- Produces: `StatusCode`, `RelationCode`, `EditableRelationCode`.
- Produces: `status_label(code) -> str`, `status_code(label) -> StatusCode`, `relation_label(code) -> str`, `relation_code(label) -> RelationCode`.
- Changes: `StatusValue.prefix` becomes `StatusValue.code`; `Relation.label` becomes `Relation.code`; `DraftGraph.relations` keys become `RelationCode`.
- Removes: `StatusPrefix`, `RelationLabel`, `RelationCode.OUTPUT` and four `CheckCode.OUTPUT_*` members.

- [ ] **Step 1: Write failing vocabulary and output-removal tests**

Create `test_vocabulary.py` with type-driven round trips and update checker tests so a confirmed page without `产出` is valid:

```python
from draft.vocabulary import (
    EditableRelationCode,
    RelationCode,
    StatusCode,
    relation_code,
    relation_label,
    status_code,
    status_label,
)


def test_every_status_code_round_trips_through_the_markdown_label() -> None:
    for code in StatusCode:
        assert status_code(status_label(code)) is code


def test_every_relation_code_round_trips_through_the_markdown_label() -> None:
    for code in RelationCode:
        assert relation_code(relation_label(code)) is code


def test_editable_relations_are_only_active_directions() -> None:
    assert tuple(EditableRelationCode) == (
        EditableRelationCode.DEPENDS_ON,
        EditableRelationCode.SUPERSEDES,
        EditableRelationCode.CHALLENGES,
    )


def test_removed_relation_is_not_published() -> None:
    assert "OUTPUT" not in RelationCode.__members__
```

Replace output-specific checker expectations with a case proving `confirmed` alone is sufficient. Delete tests whose only contract is `output.missing`, `output.forbidden`, `output.wrong_target` or output backlink maintenance.

- [ ] **Step 2: Run the narrow tests and verify the intended red state**

Run:

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_vocabulary.py" \
  "$RUNTIME/tests/unit/test_markdown.py" \
  "$RUNTIME/tests/unit/test_checker.py" -q
```

Expected: collection fails because `draft.vocabulary` and `StatusCode` do not exist; existing output tests may also fail until their obsolete fixtures are removed.

- [ ] **Step 3: Implement the single vocabulary owner**

Create `vocabulary.py` with these exact public types and mappings:

```python
from __future__ import annotations

from enum import StrEnum
from types import MappingProxyType
from typing import Mapping


class StatusCode(StrEnum):
    PENDING = "pending"
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    SUPERSEDED = "superseded"
    CLOSED = "closed"


class RelationCode(StrEnum):
    PARENT = "parent"
    DEPENDS_ON = "depends_on"
    SUPERSEDES = "supersedes"
    SUPERSEDED_BY = "superseded_by"
    CHALLENGES = "challenges"
    CHALLENGED_BY = "challenged_by"


class EditableRelationCode(StrEnum):
    DEPENDS_ON = RelationCode.DEPENDS_ON.value
    SUPERSEDES = RelationCode.SUPERSEDES.value
    CHALLENGES = RelationCode.CHALLENGES.value


_STATUS_LABELS: Mapping[StatusCode, str] = MappingProxyType(
    {
        StatusCode.PENDING: "待讨论",
        StatusCode.PROPOSED: "当前提案",
        StatusCode.CONFIRMED: "已确认",
        StatusCode.SUPERSEDED: "已取代",
        StatusCode.CLOSED: "已关闭",
    }
)
_RELATION_LABELS: Mapping[RelationCode, str] = MappingProxyType(
    {
        RelationCode.PARENT: "父主题",
        RelationCode.DEPENDS_ON: "依赖",
        RelationCode.SUPERSEDES: "取代",
        RelationCode.SUPERSEDED_BY: "被取代",
        RelationCode.CHALLENGES: "质疑",
        RelationCode.CHALLENGED_BY: "被质疑",
    }
)


def status_label(code: StatusCode) -> str:
    return _STATUS_LABELS[code]


def status_code(label: str) -> StatusCode:
    try:
        return next(code for code, value in _STATUS_LABELS.items() if value == label)
    except StopIteration as exc:
        raise ValueError(label) from exc


def relation_label(code: RelationCode) -> str:
    return _RELATION_LABELS[code]


def relation_code(label: str) -> RelationCode:
    try:
        return next(code for code, value in _RELATION_LABELS.items() if value == label)
    except StopIteration as exc:
        raise ValueError(label) from exc
```

In `markdown.py`, convert vocabulary `ValueError` to the existing stable parse error instead of leaking it. Use the mapping functions in parser and every Markdown renderer; do not duplicate Chinese labels in checker/structure/trash.

Change all consumers and tests to `.code` and enum identity. Remove `_check_outputs`, output-specific diagnostics and fixtures. Keep the existing always-present conclusions page only as a temporary valid page; Task 7 changes its lifecycle.

- [ ] **Step 4: Add an AST ownership contract for stable wire values**

Extend `tests/integration/test_distribution_contract.py` so raw stable status/relation wire values appear only in `vocabulary.py`. Parse Python AST constants; do not search source text. Exclude JSON examples in documentation because this contract covers production/test Python only.

- [ ] **Step 5: Run focused and full core unit gates**

Run:

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit" -q
(cd "$RUNTIME" && uv run --locked pyright)
uv run --project "$RUNTIME" --locked ruff check "$RUNTIME/src" "$RUNTIME/tests"
uv run --project "$RUNTIME" --locked ruff format --check "$RUNTIME/src" "$RUNTIME/tests"
```

Expected: all commands exit `0`; no output diagnostic code remains in `CheckCode` or `KNOWN_CHECK_CODES`.

- [ ] **Step 6: Commit the vocabulary boundary**

```bash
git add -- \
  plugins/draft/skills/draft/scripts/runtime/src/draft/vocabulary.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/models.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/markdown.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/graph.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/checker.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/structure.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/diagnostic_codes.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/diagnostics.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_vocabulary.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_markdown.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_graph.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_checker.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_diagnostics.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_structure.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_trash.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_checker.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_cli.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_lifecycle.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_operations.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_structure.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_distribution_contract.py
git commit -m "refactor(draft): stabilize status and relation codes"
```

---

### Task 2: 统一正文资源解析与检查状态

**Files:**
- Create: `plugins/draft/skills/draft/scripts/runtime/src/draft/resources.py`
- Create: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_resources.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/graph.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/checker.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/diagnostic_codes.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/diagnostics.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_graph.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_checker.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_diagnostics.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_checker.py`

**Interfaces:**
- Produces: `ResourceKind`, `ResourceScope`, `TargetState`, `HostOS`, `ResourceIdentity`, `ResolvedResource`, `ResourceReference`.
- Produces: `resolve_managed_destination(source, destination) -> tuple[Path, str | None]` for formal relations.
- Produces: `resolve_body_resource(root: Path, source: Path, destination: str, *, host_os: HostOS | None = None, exists: Callable[[str], bool] | None = None) -> ResolvedResource` for ordinary body links.
- Produces: `resolve_page_resources(root: Path, page: PageDocument, *, host_os: HostOS | None = None, exists: Callable[[str], bool] | None = None) -> tuple[ResourceReference, ...]`, excluding parsed relation spans.
- Produces: `resource_identity_from_operand(root: Path, value: str, *, host_os: HostOS | None = None) -> ResourceIdentity` for root-based backlinks operands.
- Changes: `DraftGraph` adds `body_resources` and `inbound_resources`; existing `inbound_links` remains the managed-page-only index used by structure/trash.

- [ ] **Step 1: Write a pure resource-state matrix**

Create parameterized unit cases covering these exact classes:

| Destination | Host | Expected kind | scope | state |
|---|---|---|---|---|
| `b.md#one` from `pages/a.md` | linux | page（规范目标符合受管理页面路径） | draft | present |
| `../asset.png` | linux | local | draft | present |
| `../../shared.png` | linux | local | outside | missing |
| `..\\asset.png` | linux | local | draft | present |
| `C:\\docs\\a.pdf` | linux | local | unknown | unavailable |
| `C:docs\\a.pdf` | linux | local | unknown | unavailable |
| `\\\\server\\share\\a.pdf` | linux | local | unknown | unavailable |
| `/opt/shared/a.pdf` | linux | local | outside | present |
| `https://example.test/a#part` | linux | external | unknown | unchecked |
| malformed percent escape | linux | local | unknown | invalid |

Use an injected `exists: Callable[[str], bool]` fake keyed by normalized absolute strings; unit tests must not create files. Add a test proving image and plain links to the same identity deduplicate, and anchors do not affect identity.

- [ ] **Step 2: Run the matrix and verify missing module failure**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit/test_resources.py" -q
```

Expected: collection ERROR naming `draft.resources`.

- [ ] **Step 3: Implement the resource types and two resolvers**

Use these public shapes:

```python
class ResourceKind(StrEnum):
    PAGE = "page"
    LOCAL = "local"
    EXTERNAL = "external"


class ResourceScope(StrEnum):
    DRAFT = "draft"
    OUTSIDE = "outside"
    UNKNOWN = "unknown"


class TargetState(StrEnum):
    PRESENT = "present"
    MISSING = "missing"
    UNAVAILABLE = "unavailable"
    UNCHECKED = "unchecked"
    INVALID = "invalid"


class HostOS(StrEnum):
    LINUX = "linux"
    WINDOWS = "windows"


@dataclass(frozen=True, order=True)
class ResourceIdentity:
    kind: ResourceKind
    value: str


@dataclass(frozen=True)
class ResolvedResource:
    identity: ResourceIdentity
    target: str
    kind: ResourceKind
    scope: ResourceScope
    target_state: TargetState
    local_absolute: str | None
    was_relative: bool
    separator: str | None
    anchor: str | None


@dataclass(frozen=True)
class ResourceReference:
    link: MarkdownLink
    resource: ResolvedResource
```

Parse URI schemes before Windows drive paths, but treat a one-letter drive followed by `:` as Windows syntax. Reject malformed percent escapes as `invalid`; do not call `Path.resolve()` or `realpath()` for identity because symlink identity and missing targets must remain lexical.

`resolve_managed_destination` accepts only relative POSIX Markdown destinations that stay under the Draft root and returns root-relative `Path`; absolute, URI, Windows drive/UNC and root escape raise the existing stable formal-link error.

- [ ] **Step 4: Refactor graph/checker to keep body links and formal relations disjoint**

Implement relation-span exclusion once in `resolve_page_resources`. Graph loading calls that function for `body_resources`; formal relation targets separately use `resolve_managed_destination`. Query code in Task 5 reuses the same page-resource function instead of duplicating the distinction.

Add `CheckCode.LINK_UNAVAILABLE = "link.unavailable"`. Emit:

- ERROR `link.missing` for `missing`;
- ERROR `link.invalid` for `invalid` using a new `CheckCode.LINK_INVALID`;
- WARNING `link.unavailable` for `unavailable`;
- no issue for `present` or external `unchecked`.

Keep every `ResourceReference` even when state is not present. Build `inbound_resources` by `ResourceIdentity` and preserve `(source path, link position)` order.

- [ ] **Step 5: Prove real files, images, directories and non-native paths**

Add integration cases that create a Draft plus a file, directory and image outside the Draft; assert present/missing diagnostics and Linux handling of Windows absolute paths. Assert external URI causes no warning and a missing image is an error with the image source line.

- [ ] **Step 6: Run focused gates and commit**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_resources.py" \
  "$RUNTIME/tests/unit/test_graph.py" \
  "$RUNTIME/tests/unit/test_checker.py" \
  "$RUNTIME/tests/unit/test_diagnostics.py" \
  "$RUNTIME/tests/integration/test_checker.py" -q
(cd "$RUNTIME" && uv run --locked pyright)
uv run --project "$RUNTIME" --locked ruff check "$RUNTIME/src" "$RUNTIME/tests"
git add -- \
  plugins/draft/skills/draft/scripts/runtime/src/draft/resources.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/graph.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/checker.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/diagnostic_codes.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/diagnostics.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_resources.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_graph.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_checker.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_diagnostics.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_checker.py
git commit -m "feat(draft): model body resource targets"
```

---

### Task 3: 在结构变化中保持 Draft 内外正文资源目标

**Files:**
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/resources.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/structure.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/trash.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_resources.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_structure.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_structure.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash.py`

**Interfaces:**
- Produces: `rebase_body_destination(resource: ResolvedResource, *, new_source: Path, moved_targets: Mapping[ResourceIdentity, ResourceIdentity]) -> str`.
- Preserves: absolute local and external destinations byte-for-byte; only relative local destinations are rewritten.
- Consumes: `DraftGraph.body_resources` from Task 2.

- [ ] **Step 1: Write failing rebase tests independent of implementation math**

Add tests proving:

1. moving `pages/a.md` to `pages/sub/a.md` changes `../shared.png` to `../../shared.png` while preserving identity;
2. Windows relative `..\\shared.png` remains backslash-primary after the same move;
3. mixed separators preserve the first separator occurring in the original path;
4. Linux absolute, Windows absolute, UNC, drive-relative and external URI remain exactly unchanged;
5. a relative link to another moved page follows the target mapping rather than its old absolute location;
6. anchors are reattached unchanged after path recomputation.

The test oracle must resolve both old and new destinations independently and compare `ResourceIdentity`; it must not copy the production rebasing formula.

- [ ] **Step 2: Verify structure tests fail on outside-root and Windows-relative links**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_resources.py" \
  "$RUNTIME/tests/unit/test_structure.py" -q
```

Expected: new tests fail because current structure code skips or rejects root-external/Windows links.

- [ ] **Step 3: Implement resource-based rebasing**

`rebase_body_destination` accepts a mapping from old absolute resource identity values to final values for moved managed pages. For a relative native local target, compute a new relative path from `new_source.parent`, percent-encode with `safe="/-._~"`, convert `/` to the recorded primary `separator` when it is `\\`, then append the original anchor. Return the original destination for every absolute, unavailable or external target.

In `structure._render_pages`, handle formal relation links with `resolve_managed_destination`; handle non-relation body links by looking up the existing `ResourceReference`. Do not route both through one heuristic resolver.

Apply the same resource helper in trash restore/rebase paths. Keep protected root/conclusions rules unchanged until Task 7 adds the explicit stale-conclusions exception.

- [ ] **Step 4: Run unit and real move/trash gates**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_resources.py" \
  "$RUNTIME/tests/unit/test_structure.py" \
  "$RUNTIME/tests/integration/test_structure.py" \
  "$RUNTIME/tests/integration/test_trash.py" -q
(cd "$RUNTIME" && uv run --locked pyright)
uv run --project "$RUNTIME" --locked ruff check "$RUNTIME/src" "$RUNTIME/tests"
```

Expected: exit `0`; every rewritten relative link resolves to the pre-move identity.

- [ ] **Step 5: Commit resource-preserving structure changes**

```bash
git add -- plugins/draft/skills/draft/scripts/runtime/src/draft/resources.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/structure.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/trash.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_resources.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_structure.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_structure.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash.py
git commit -m "feat(draft): preserve resource targets across moves"
```

---

### Task 4: 统一静默生命周期前置推进与可恢复项清单

**Files:**
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/trash_lifecycle.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/structure.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/trash.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/cli.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/models.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash_lifecycle.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_cli.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_cli.py`

**Interfaces:**
- Produces: `prepare_registered_command(root: Path, *, now: datetime | None = None, handoff: Handoff | None = None) -> None`.
- Produces: `recover_interrupted_operations(root: Path, *, now: datetime | None = None, handoff: Handoff | None = None) -> tuple[OperationResult | TrashResult, ...]`.
- Produces: `RecoverableItem(operation_id: str, page: Path, restore_expires_at: datetime)` and `list_recoverable(root: Path, *, as_of: datetime | None = None) -> tuple[RecoverableItem, ...]`.
- Removes from public results: collected lifecycle warnings and lifecycle results from ordinary commands/recover.

- [ ] **Step 1: Write lifecycle boundary tests**

Add integration tests for these existing commands against an expired retained item: registered `check`, `move`, `restore`, `recover`. Use an injected handoff spy at the lifecycle function boundary and assert each command calls it exactly once before its own collaborator. Task 5 adds the same proof for query commands; Task 6 adds it for `--dry-run`.

Add tests that:

- temporary `OSError` handoff failure leaves `trash_handoff_pending`, returns the main command result and emits no warning field/text;
- corrupt journal stops the main command with exit 3;
- `check --candidate`, `init`, pre-registration `adopt`, help and version do not call preflight;
- `as_of == restore_expires_at` excludes the item;
- one corrupt candidate journal makes `list_recoverable` fail instead of returning earlier valid items.

- [ ] **Step 2: Run focused lifecycle/CLI tests and confirm failures**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/integration/test_trash_lifecycle.py" \
  "$RUNTIME/tests/unit/test_cli.py" -q
```

Expected: failures show preflight currently exists only in structural write wrappers, warnings remain public, and recover combines recovery with lifecycle results.

- [ ] **Step 3: Split the three lifecycle responsibilities**

Implement:

```python
@dataclass(frozen=True)
class RecoverableItem:
    operation_id: str
    page: Path
    restore_expires_at: datetime


def prepare_registered_command(
    root: Path,
    *,
    now: datetime | None = None,
    handoff: Handoff | None = None,
) -> None:
    report = advance_trash_lifecycle(root, now=now, handoff=handoff)
    if report.error is not None:
        raise LifecycleAdvanceError(report)
```

`recover_interrupted_operations` contains the lock/orphan-operation recovery part currently in `recover_draft`, but does not enumerate retained/handoff-pending items after recovery. `list_recoverable` validates every trash journal and verified package, filters strictly with `as_of < restore_expires_at`, and sorts `(restore_expires_at, operation_id)`.

Remove `collect_lifecycle_warnings` and `_with_preflight_warnings`. Main command orchestration owns a single call to `prepare_registered_command`; domain planners and execution wrappers no longer call it a second time.

- [ ] **Step 4: Refactor CLI preflight selection**

Add a pure predicate `_registered_root_for(args) -> Path | None` with these results:

- `None`: version/help, init, adopt, `check --candidate`;
- `Path(args.root)`: registered check, query, all registered writes including dry-run, restore, generate-conclusions and recover.

Call preflight once after argparse succeeds and before check/dispatch. Discard successful report details. Render only a stable error on `LifecycleAdvanceError`.

Public `recover` calls `recover_interrupted_operations` and reports only repaired operation count/paths; lifecycle handoff activity remains hidden.

- [ ] **Step 5: Run lifecycle and CLI gates and commit**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_cli.py" \
  "$RUNTIME/tests/integration/test_cli.py" \
  "$RUNTIME/tests/integration/test_trash_lifecycle.py" -q
(cd "$RUNTIME" && uv run --locked pyright)
uv run --project "$RUNTIME" --locked ruff check "$RUNTIME/src" "$RUNTIME/tests"
git add -- \
  plugins/draft/skills/draft/scripts/runtime/src/draft/trash_lifecycle.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/structure.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/trash.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/cli.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/models.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash_lifecycle.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_cli.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_cli.py
git commit -m "refactor(draft): centralize silent lifecycle preflight"
```

---

### Task 5: 实现紧凑查询服务与 CLI 命令

**Files:**
- Create: `plugins/draft/skills/draft/scripts/runtime/src/draft/query.py`
- Create: `plugins/draft/skills/draft/scripts/runtime/src/draft/cli_results.py`
- Create: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_query.py`
- Create: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_queries.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/graph.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/cli.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_cli.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_cli.py`

**Interfaces:**
- Produces: `LocalPageSnapshot(document: PageDocument, body_resources: tuple[ResourceReference, ...])`, `load_registered_page(root: Path, page: Path) -> LocalPageSnapshot` and `load_complete_snapshot(root: Path) -> DraftGraph`.
- Produces query functions: `page_summary(graph: DraftGraph, page: Path) -> PageSummary`, `page_links(resources: tuple[ResourceReference, ...]) -> tuple[LinkItem, ...]`, `backlinks(graph: DraftGraph, target: ResourceIdentity) -> tuple[Path, ...]`, `outgoing_relations(page: PageDocument) -> tuple[RelationItem, ...]`, `incoming_relations(graph: DraftGraph, page: Path) -> tuple[RelationItem, ...]`, `pages_with_status(graph: DraftGraph, status: StatusCode) -> tuple[Path, ...]`.
- Produces result types: `LocalPageSnapshot`, `PageSummary`, `LinkItem`, `RelationItem`.
- Consumes: `list_recoverable` from Task 4.

- [ ] **Step 1: Write pure query behavior tests**

Use in-memory parsed pages, injected resource probes and `DraftGraph` fakes to prove:

- `page_links` removes relation spans, deduplicates by identity, preserves first declaration order and strips anchors from `target`;
- backlinks deduplicate source paths and sort by source path/position;
- outgoing relations preserve declaration order and include `parent`, `superseded_by`, `challenged_by` when present;
- incoming relations sort by source path/line and preserve the source declaration code;
- status returns only paths sorted by canonical POSIX value;
- `PageSummary.link_count` equals the unpaginated deduplicated link total and relation counts equal the two relation query totals.

Use the exact result shapes:

```python
@dataclass(frozen=True)
class LinkItem:
    target: str
    kind: ResourceKind
    scope: ResourceScope
    target_state: TargetState


@dataclass(frozen=True)
class RelationItem:
    relation: RelationCode
    page: Path


@dataclass(frozen=True)
class PageSummary:
    path: Path
    title: str
    status: StatusCode
    parent: Path | None
    child_count: int
    link_count: int
    backlink_count: int
    outgoing_relation_count: int
    incoming_relation_count: int
```

- [ ] **Step 2: Verify query module is absent**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit/test_query.py" -q
```

Expected: collection ERROR naming `draft.query`.

- [ ] **Step 3: Implement local and complete loading boundaries**

`load_registered_page` validates the registered marker and one strict page operand, reads/parses only that file, and calls Task 2's `resolve_page_resources` for the returned snapshot. It must allow `query links` and outgoing relations to succeed when another page is malformed. The pure `page_links` function consumes the snapshot's already resolved resources, so unit tests never touch the filesystem.

`load_complete_snapshot` discovers and parses every managed page. It fails if a managed page is unreadable/unparseable or marker ownership is invalid, but does not fail merely because a parsed body resource is `missing`, `invalid`, `unavailable` or `unchecked`. Keep checker diagnostics separate from parse completeness.

Do not hide partial graph construction behind `load_graph()`; name the two public boundaries explicitly and test that local loading does not call global discovery.

- [ ] **Step 4: Implement nested argparse and result rendering**

Add:

```text
draft query page ROOT PAGE
draft query links ROOT PAGE
draft query backlinks ROOT TARGET
draft query relations ROOT PAGE --direction incoming|outgoing
draft query status ROOT CODE
draft query recoverable ROOT
```

All collection commands accept `--limit` default 20, maximum 100, non-negative `--offset`, and `--count-only`; no parser exposes `--all`. `query page` has no pagination.

In `cli_results.py`, define a generic pure paginator:

```python
@dataclass(frozen=True)
class PageSlice[T]:
    items: tuple[T, ...]
    total: int
    offset: int
    limit: int
    has_more: bool
    next_offset: int | None


def paginate[T](items: tuple[T, ...], *, offset: int, limit: int) -> PageSlice[T]:
    selected = items[offset : offset + limit]
    next_offset = offset + len(selected)
    has_more = next_offset < len(items)
    return PageSlice(
        items=selected,
        total=len(items),
        offset=offset,
        limit=limit,
        has_more=has_more,
        next_offset=next_offset if has_more else None,
    )
```

`--count-only` renders `items: []` but retains the full `total`. JSON page summary uses exactly the fields in the approved spec. Human output contains only item facts plus one count/pagination summary; tests must use dynamic facts rather than full sentences.

- [ ] **Step 5: Add real CLI scope/failure tests**

Integration cases must prove:

- malformed unrelated page does not block links/outgoing for a valid target;
- the same malformed page blocks page summary, backlinks, incoming relations and status with no partial JSON;
- link problem states appear in successful JSON with exit 0;
- empty results return exit 0;
- `limit=101`, negative offset, missing direction and `status_label(StatusCode.PROPOSED)` used as a localized status argument fail with exit 2;
- `query backlinks` resolves CLI relative target from Draft root and source relative links from each page;
- recoverable output includes `as_of` and strict expiration ordering.

- [ ] **Step 6: Run query/CLI gates and commit**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_query.py" \
  "$RUNTIME/tests/unit/test_cli.py" \
  "$RUNTIME/tests/integration/test_queries.py" \
  "$RUNTIME/tests/integration/test_cli.py" -q
(cd "$RUNTIME" && uv run --locked pyright)
uv run --project "$RUNTIME" --locked ruff check "$RUNTIME/src" "$RUNTIME/tests"
git add -- \
  plugins/draft/skills/draft/scripts/runtime/src/draft/query.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/cli_results.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/graph.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/cli.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_query.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_cli.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_queries.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_cli.py
git commit -m "feat(draft): add compact query commands"
```

---

### Task 6: 为现有写命令增加语义 PlanEffect 与 dry-run

**Files:**
- Create: `plugins/draft/skills/draft/scripts/runtime/src/draft/plan_effects.py`
- Create: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_plan_effects.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/models.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/operations.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/structure.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/trash.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/cli.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/cli_results.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_structure.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_trash.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_cli.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_structure.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_cli.py`

**Interfaces:**
- Produces: `PlanAction`, `PlanEffect`, `public_changed_paths(effects) -> tuple[Path, ...]`.
- Changes: `OperationPlan.effects: tuple[PlanEffect, ...]` is required for every user-facing write plan.
- Produces: `prepare_move(root: Path, source: Path, target: Path) -> OperationPlan`, `prepare_promote(root: Path, page: Path, children: tuple[Path, ...]) -> OperationPlan`, `prepare_collapse(root: Path, hub: Path) -> OperationPlan`, `prepare_trash(root: Path, page: Path) -> OperationPlan`; formal execution wrappers call the same prepare function then execute.

- [ ] **Step 1: Write PlanEffect invariant and serialization tests**

Use this exact type:

```python
class PlanAction(StrEnum):
    CREATE = "create"
    MODIFY = "modify"
    MOVE = "move"
    REMOVE = "remove"


@dataclass(frozen=True)
class PlanEffect:
    action: PlanAction
    path: Path | None = None
    from_path: Path | None = None
    to_path: Path | None = None
```

`__post_init__` must require exactly `from_path/to_path` for MOVE and exactly `path` for all other actions. Tests assert invalid combinations raise `ValueError`, move JSON uses `from`/`to`, and no serialized path begins with `.draft/`.

Add structure tests proving one moved file is one MOVE effect rather than CREATE+REMOVE, while rewritten non-moved pages are MODIFY effects. Add trash tests proving public removal and parent navigation modification but no package path.

- [ ] **Step 2: Run tests and observe absent effects**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_plan_effects.py" \
  "$RUNTIME/tests/unit/test_structure.py" \
  "$RUNTIME/tests/unit/test_trash.py" -q
```

Expected: collection or assertion failures because `PlanEffect` and `OperationPlan.effects` do not exist.

- [ ] **Step 3: Populate effects in planners, never infer them from mutations**

`structure.plan_path_remap` already knows the old→new mapping, so it must emit MOVE effects there. Pages whose path is unchanged but bytes change emit MODIFY. Trash planner emits REMOVE for every publicly removed page and MODIFY for surviving navigation owners.

`public_changed_paths` preserves effect order while deduplicating: CREATE/MODIFY/REMOVE contribute `path`; MOVE contributes `from_path` then `to_path`. It excludes all internal mutations by construction.

Do not write a generic matcher that guesses moves by pairing low-level write/delete hashes; such guessing loses the semantic operation.

- [ ] **Step 4: Add dry-run parser and dispatch for existing commands**

Add `--dry-run`, `--limit`, `--offset` to move/promote/collapse/trash. Dry-run calls the same `prepare_*` as execution, paginates `plan.effects`, and never calls `execute_plan`, allocates a journal, creates a package or acquires a lock. The central lifecycle preflight from Task 4 still runs.

Formal success JSON for non-trash writes becomes `result_type` plus `changed_paths`; remove `phase`, ordinary `operation_id`, `warnings` and implicit `ok: true`. Trash retains `operation_id`, `restore_expires_at`, `changed_paths`; remove `package`, `purge_eligible_at`, `phase`, `warnings`.

- [ ] **Step 5: Prove dry-run/execute parity and non-mutation**

For each existing write command, integration tests snapshot public files and `.draft` after lifecycle preflight, run dry-run, assert business files/journals/packages unchanged, then run the real command from an equivalent fixture and assert its `changed_paths` equal paths derived from the dry-run effects. Test pagination at 20/100 and reject `--all`.

- [ ] **Step 6: Run gates and commit**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_plan_effects.py" \
  "$RUNTIME/tests/unit/test_structure.py" \
  "$RUNTIME/tests/unit/test_trash.py" \
  "$RUNTIME/tests/unit/test_cli.py" \
  "$RUNTIME/tests/integration/test_structure.py" \
  "$RUNTIME/tests/integration/test_trash.py" \
  "$RUNTIME/tests/integration/test_cli.py" -q
(cd "$RUNTIME" && uv run --locked pyright)
uv run --project "$RUNTIME" --locked ruff check "$RUNTIME/src" "$RUNTIME/tests"
git add -- \
  plugins/draft/skills/draft/scripts/runtime/src/draft/plan_effects.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/models.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/operations.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/structure.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/trash.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/cli.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/cli_results.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_plan_effects.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_structure.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_trash.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_cli.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_structure.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_cli.py
git commit -m "feat(draft): expose semantic dry-run plans"
```

---

### Task 7: 将结论页改为确定、按需、显式生成

**Files:**
- Create: `plugins/draft/skills/draft/scripts/runtime/src/draft/conclusions.py`
- Create: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_conclusions.py`
- Create: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_conclusions.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/vocabulary.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/graph.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/lifecycle.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/checker.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/operations.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/structure.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/trash.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/cli.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/cli_results.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/support.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_lifecycle.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_graph.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_checker.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_cli.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_lifecycle.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_checker.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_structure.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_cli.py`

**Interfaces:**
- Produces: `ConclusionsState.NOT_GENERATED`, `ConclusionsState.GENERATED`.
- Changes: `MarkerState` includes `conclusions_state`; final/provisional markers require it while `FORMAT_VERSION` stays 1.
- Produces: `GenerationReason.NO_CONFIRMED_PAGES`, `GenerationPlan`, `plan_generate_conclusions(root: Path) -> GenerationPlan`, `generate_conclusions(root: Path) -> tuple[bool, GenerationReason | None, tuple[Path, ...]]`.
- Allows: `OperationPlan(kind="generate-conclusions")` to mutate only `.draft/draft.json` among internal paths.

- [ ] **Step 1: Write marker/init/adopt red tests**

Change lifecycle expectations so init creates only `index.md`, `pages/`, and a marker containing:

```json
{"format_version":1,"conclusions_state":"not_generated"}
```

Root index must not contain a conclusion link. Candidate fixtures must not contain `conclusions.md`; candidate check/adopt reject an unexpected file. Marker tests reject missing, unknown or extra conclusions state without accepting the old marker shape. Build expected ownership values from `ConclusionsState.NOT_GENERATED.value` rather than duplicating its wire string in test constants. Provisional adopt markers contain all four exact keys `format_version`, `conclusions_state`, `adopt_state`, `candidate_manifest`, and their conclusions state is always `ConclusionsState.NOT_GENERATED`.

- [ ] **Step 2: Write generator behavior tests**

Cover all state transitions:

- NOT_GENERATED + zero confirmed → `generated=False`, reason `no_confirmed_pages`, no plan/effects;
- NOT_GENERATED + confirmed pages → create conclusions, modify root, update marker, public effects hide marker;
- GENERATED + unchanged proposed content → empty effects;
- GENERATED + conclusions status confirmed → regenerate to proposed;
- GENERATED + zero confirmed → retain file/root link with empty list;
- GENERATED + missing/malformed file or root link drift → rebuild canonical output;
- NOT_GENERATED + unexpected file → fail without overwrite;
- invalid navigation graph → fail instead of path-sort fallback.

Build an in-memory navigation order whose path order differs from index declaration order, and assert the flat conclusion list follows depth-first navigation order.

- [ ] **Step 3: Run focused tests and verify current eager behavior fails**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_lifecycle.py" \
  "$RUNTIME/tests/unit/test_graph.py" \
  "$RUNTIME/tests/unit/test_conclusions.py" -q
```

Expected: failures show init still creates conclusions, marker lacks ownership state and generator module is absent.

- [ ] **Step 4: Implement ownership state and canonical generation plan**

Use:

```python
class ConclusionsState(StrEnum):
    NOT_GENERATED = "not_generated"
    GENERATED = "generated"


class GenerationReason(StrEnum):
    NO_CONFIRMED_PAGES = "no_confirmed_pages"


@dataclass(frozen=True)
class GenerationPlan:
    generated: bool
    reason: GenerationReason | None
    operation: OperationPlan | None
```

The generator filters `confirmed` pages excluding root and conclusions. Render exactly one H1, proposed status, parent relation, `## 已确认结论`, then one title/link item per page. Derive order from parent navigation link positions, not `Path` order or `DraftGraph.children` sorting.

Use a generator-specific tolerant load boundary: it must strictly parse marker, root and all discussion pages, but when ownership is GENERATED it may ignore a missing or malformed existing `conclusions.md` because that file is the replaceable target. It may repair root-link absence/duplication only when root Markdown itself still parses; an unparseable root remains a hard failure.

First successful generation atomically writes conclusion file, root navigation and marker through one `OperationPlan`. Extend `_validate_plan_paths` only for `plan.kind == "generate-conclusions"` and exact path `.draft/draft.json`; every other internal target remains rejected.

- [ ] **Step 5: Implement stale-content tolerance without weakening normal pages**

When marker is GENERATED:

- graph discovery requires `conclusions.md` but treats list item targets as queryable resource facts, not checker missing-link errors;
- checker validates fixed H1/status/parent/section/list shape and unique last root navigation link, but does not compare list membership/title/path with current confirmed pages;
- structure renderer skips body-link rewrites owned by the conclusion list;
- trash blocker calculation ignores inbound links from the generated conclusion list.

Do not globally ignore missing links from `conclusions.md` before ownership is GENERATED, and do not weaken missing-link checks for any other page.

- [ ] **Step 6: Add CLI and dry-run**

Add `generate-conclusions ROOT [--dry-run] [--limit N] [--offset N]`. Normal JSON returns `generated`, optional `reason`, and public `changed_paths`. Dry-run uses the Task 6 plan envelope. The command always writes proposed status when it generates; it never auto-confirms.

- [ ] **Step 7: Run conclusion/lifecycle/structure gates and commit**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_lifecycle.py" \
  "$RUNTIME/tests/unit/test_graph.py" \
  "$RUNTIME/tests/unit/test_checker.py" \
  "$RUNTIME/tests/unit/test_conclusions.py" \
  "$RUNTIME/tests/integration/test_lifecycle.py" \
  "$RUNTIME/tests/integration/test_checker.py" \
  "$RUNTIME/tests/integration/test_conclusions.py" \
  "$RUNTIME/tests/integration/test_structure.py" \
  "$RUNTIME/tests/integration/test_trash.py" -q
(cd "$RUNTIME" && uv run --locked pyright)
uv run --project "$RUNTIME" --locked ruff check "$RUNTIME/src" "$RUNTIME/tests"
git add -- \
  plugins/draft/skills/draft/scripts/runtime/src/draft/conclusions.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/vocabulary.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/graph.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/lifecycle.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/checker.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/operations.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/structure.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/trash.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/cli.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/cli_results.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_conclusions.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_lifecycle.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_graph.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_checker.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_cli.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/support.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_conclusions.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_lifecycle.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_checker.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_structure.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_cli.py
git commit -m "feat(draft): generate conclusions on demand"
```

---

### Task 8: 增加原子叶子页面创建

**Files:**
- Create: `plugins/draft/skills/draft/scripts/runtime/src/draft/page_create.py`
- Create: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_page_create.py`
- Create: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_page_commands.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/paths.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/markdown.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/cli.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/cli_results.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_cli.py`

**Interfaces:**
- Produces: `resolve_new_leaf_operand(root: Path, value: str) -> ResolvedPath`.
- Produces: `plan_create_page(root: Path, page: Path, *, title: str, status: StatusCode) -> OperationPlan` and `create_page(root: Path, page: Path, *, title: str, status: StatusCode) -> tuple[Path, ...]`.
- Consumes: `StatusCode.PENDING|PROPOSED`, `physical_parent`, Task 6 effects, Task 7 ownership state.

- [ ] **Step 1: Write path/title/status/navigation tests**

Reject exact categories: absolute path, backslash machine operand, non-`pages/`, non-`.md`, `.`/`..`, empty segment, any `index.md`, existing target, missing physical parent, `confirmed`/`superseded`/`closed`, empty title, newline and control characters.

Accept Unicode and Markdown punctuation in a single-line title. Parse the created page back through `parse_page` and assert `PageDocument.title` equals the input, proving escaping rather than asserting raw Markdown.

Test navigation insertion:

- append at end of ordinary parent `## 子主题`;
- insert immediately before the unique generated conclusions link at root;
- page and parent mutations form one OperationPlan with CREATE then MODIFY effects;
- failure leaves both page and parent unchanged.

- [ ] **Step 2: Run the new unit file and verify missing API**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit/test_page_create.py" -q
```

Expected: collection ERROR for `draft.page_create` or missing `resolve_new_leaf_operand`.

- [ ] **Step 3: Implement leaf validation and plain-text Markdown escaping**

Add `escape_markdown_text(value: str) -> str` to `markdown.py`; escape backslash and CommonMark punctuation so the parsed inline text remains the requested title. Reject Unicode control categories and any `\r`/`\n`.

Render the skeleton with current localized labels obtained through `status_label`/`relation_label`; do not repeat a Chinese status lookup table in `page_create.py`.

Use `append_parent_navigation` for ordinary parents and a new `insert_navigation_before_conclusions` helper for generated root. The latter identifies the canonical root link semantically through parsed destination, not by searching the text `conclusions.md`.

- [ ] **Step 4: Add CLI execution/dry-run integration**

Add:

```text
draft create ROOT PAGE --title TITLE --status pending|proposed
```

Support Task 6 dry-run/pagination fields. Test actual JSON `result_type=create`, created path/status and public changed paths; dry-run must not create directories or a journal after the shared lifecycle preflight.

- [ ] **Step 5: Run gates and commit**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_page_create.py" \
  "$RUNTIME/tests/unit/test_cli.py" \
  "$RUNTIME/tests/integration/test_page_commands.py" -q
(cd "$RUNTIME" && uv run --locked pyright)
uv run --project "$RUNTIME" --locked ruff check "$RUNTIME/src" "$RUNTIME/tests"
git add -- \
  plugins/draft/skills/draft/scripts/runtime/src/draft/page_create.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/paths.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/markdown.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/cli.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/cli_results.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_page_create.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_cli.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_page_commands.py
git commit -m "feat(draft): add atomic page creation"
```

---

### Task 9: 增加带最终状态验证的状态修改

**Files:**
- Create: `plugins/draft/skills/draft/scripts/runtime/src/draft/page_metadata.py`
- Create: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_page_metadata.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/markdown.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/graph.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/cli.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/cli_results.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_cli.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_page_commands.py`

**Interfaces:**
- Produces: `plan_set_status(root: Path, page: Path, code: StatusCode, *, note: str | None = None, clear_note: bool = False) -> OperationPlan`.
- Produces: `replace_status(text: str, code: StatusCode, explanation: str | None) -> str` using the existing located status span.
- Produces: `replace_graph_page(graph: DraftGraph, page: PageDocument) -> DraftGraph`, keyed by `page.path`, for Tasks 9–10.

- [ ] **Step 1: Write status transition matrix tests**

Cover every ordered pair of `StatusCode`; a pair succeeds whenever the final graph satisfies existing invariants. Add focused cases:

- confirmed → proposed and closed → pending succeed;
- proposed → superseded fails without `superseded_by`;
- superseded → proposed fails while `superseded_by` remains;
- same code with no note option is a no-op;
- omitted note preserves the current explanation;
- `--note` replaces it; `--clear-note` removes it; both together fail;
- note rejects newline/control characters but accepts any language;
- changing generated conclusions to confirmed changes only its status and never refreshes entries.

Tests assert parsed `StatusValue`, final check report and PlanEffect, not full Markdown strings.

- [ ] **Step 2: Verify missing status planner**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit/test_page_metadata.py" -q
```

Expected: collection ERROR for `draft.page_metadata`.

- [ ] **Step 3: Implement status planning through a final in-memory graph**

Load a graph whose existing non-derived structure is clean, rewrite exactly the status span, parse the new page, replace it in a rebuilt graph, and run `check_graph` on the final graph. On any final error raise a stable domain error with the blocking codes; never commit an intermediate invalid page.

Build a single MODIFY mutation/effect only when bytes change. `plan_set_status` does not add/remove relations and does not call the conclusions generator.

- [ ] **Step 4: Add nested status CLI and dry-run**

Add:

```text
draft status set ROOT PAGE CODE [--note TEXT | --clear-note]
```

Argparse accepts only stable codes. JSON returns `result_type=status.set`, path, final status and public changed paths; human output includes dynamic path/code. Use Task 6 dry-run.

- [ ] **Step 5: Run status gates and commit**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_page_metadata.py" \
  "$RUNTIME/tests/unit/test_cli.py" \
  "$RUNTIME/tests/integration/test_page_commands.py" -q
(cd "$RUNTIME" && uv run --locked pyright)
uv run --project "$RUNTIME" --locked ruff check "$RUNTIME/src" "$RUNTIME/tests"
git add -- \
  plugins/draft/skills/draft/scripts/runtime/src/draft/page_metadata.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/markdown.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/graph.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/cli.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/cli_results.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_page_metadata.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_cli.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_page_commands.py
git commit -m "feat(draft): add validated status mutation"
```

---

### Task 10: 增加幂等的依赖、取代与质疑修改

**Files:**
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/page_metadata.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/markdown.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_page_metadata.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/cli.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/cli_results.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_cli.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_page_commands.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_operations.py`

**Interfaces:**
- Produces: `plan_add_relation(root: Path, source: Path, code: EditableRelationCode, target: Path) -> OperationPlan`.
- Produces: `plan_remove_relation(root: Path, source: Path, code: EditableRelationCode, target: Path, *, restored_status: StatusCode | None = None) -> OperationPlan`.
- Produces Markdown helpers: `append_machine_relation`, `remove_machine_relation` that edit complete quoted machine lines and preserve unrelated text.
- Consumes only `EditableRelationCode`; inverse codes and parent are rejected at CLI/domain boundary.

- [ ] **Step 1: Extend unit tests with relation matrices**

Required cases:

- depends_on adds/removes only the source line and rejects self/cycle;
- challenges adds/removes `challenges` + `challenged_by` atomically;
- supersedes adds both directions, sets target `superseded`, preserves status explanation and rejects replacement cycle;
- adding a complete existing pair is no-op;
- adding a half pair repairs the missing side;
- removing an absent pair is no-op;
- removing a half pair clears the remaining side;
- removing one of several replacement pairs keeps target superseded and rejects unnecessary `--status`;
- removing the last replacement requires non-superseded `--status` and updates both relations/status in one plan;
- parent, superseded_by and challenged_by are rejected as mutation parameters; the removed relation is already proved absent from the published type in Task 1 and receives no compatibility test;
- any unrelated graph error that could invalidate final proof blocks the command.

Create half-pair graphs directly from parsed Markdown so the planner proves it can repair the targeted inconsistency without treating every dirty graph as writable.

- [ ] **Step 2: Run tests and verify missing planners**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit/test_page_metadata.py" -q
```

Expected: new tests fail because relation planners/helpers do not exist.

- [ ] **Step 3: Implement exact machine-line edits and targeted dirty repair**

`append_machine_relation` inserts a new quoted relation line at the end of the initial machine block using `relation_label(code)` and a canonical relative managed-page destination. `remove_machine_relation` removes the full quote line identified by the parsed relation link span; it must not remove matching prose links.

For paired relations, compute desired final membership for both pages, apply all text changes in memory, parse both pages and validate the final graph. Initial loading may tolerate only the exact missing counterpart issue for the requested source/code/target pair; every other checker error remains blocking.

When add supersedes changes target status, reuse the status renderer from Task 9. When removing the final supersedes pair, require `restored_status` in the type-derived set `set(StatusCode) - {StatusCode.SUPERSEDED}`. Never infer the previous state or store hidden restoration history.

- [ ] **Step 4: Add relation CLI and dry-run**

Add:

```text
draft relation add ROOT SOURCE depends_on|supersedes|challenges TARGET
draft relation remove ROOT SOURCE depends_on|supersedes|challenges TARGET [--status CODE]
```

`--status` is accepted only by remove and only when the final replacement pair disappears. JSON returns `result_type=relation.add|relation.remove`, source/code/target and public changed paths. Add integration tests for rollback after injected second-page write failure.

- [ ] **Step 5: Run relation gates and commit**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_page_metadata.py" \
  "$RUNTIME/tests/unit/test_cli.py" \
  "$RUNTIME/tests/integration/test_page_commands.py" \
  "$RUNTIME/tests/integration/test_operations.py" -q
(cd "$RUNTIME" && uv run --locked pyright)
uv run --project "$RUNTIME" --locked ruff check "$RUNTIME/src" "$RUNTIME/tests"
git add -- \
  plugins/draft/skills/draft/scripts/runtime/src/draft/page_metadata.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/markdown.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/cli.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/cli_results.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_page_metadata.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_cli.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_page_commands.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_operations.py
git commit -m "feat(draft): add idempotent relation mutation"
```

---

### Task 11: 更新公开文档并运行全部分发门禁

**Files:**
- Modify: `plugins/draft/skills/draft/SKILL.md`
- Modify: `plugins/draft/skills/draft/references/draft-format.md`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/support.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_distribution_contract.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_distribution_smoke.py`

**Interfaces:**
- Documents every command, stable code, path rule, lazy conclusion workflow, dry-run envelope, pagination and lifecycle boundary implemented in Tasks 1–10.
- Preserves core/adapter dependency direction and self-contained runtime distribution.

- [ ] **Step 1: Rewrite format and skill workflow from implemented behavior**

`draft-format.md` must show an initial tree without `conclusions.md`, list stable machine codes separately from current Chinese Markdown labels, remove every output rule/example, document Draft-external body resources, and state that generated conclusion entries may be stale until explicit generation.

`SKILL.md` must include this mandatory final sequence:

1. 与用户确认讨论结束；
2. 执行 `draft generate-conclusions ROOT`；
3. 从生成后的 proposed 结论页产出设计文档；
4. 产出完成后执行 `draft status set ROOT conclusions.md confirmed`。

Also document query selection so Agent uses `query page` for routing, narrow collection commands with JSON/pagination for facts, and `rg` for body search. Do not tell Agent to maintain conclusions during ordinary edits.

- [ ] **Step 2: Add semantic distribution/help contracts**

Use argparse parser objects or run `draft --help`/subcommand help to verify command availability and option structure. Do not test help by searching production source text. Extend AST contracts so stable status/relation wire values have one production owner and core never imports plugin adapter modules.

Update shared integration fixtures to produce both NOT_GENERATED and GENERATED roots explicitly; fixture names must reveal which lifecycle state they create.

- [ ] **Step 3: Run all non-network quality gates**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit" -q
uv run --project "$RUNTIME" --locked pytest plugins/draft/scripts/tests/unit -q
uv run --project "$RUNTIME" --locked \
  pytest "$RUNTIME/tests/integration" -q -m 'not network'
uv run --project "$RUNTIME" --locked \
  pytest plugins/draft/scripts/tests/integration -q -m 'not network'
(cd "$RUNTIME" && uv run --locked pyright)
(cd plugins/draft/scripts && \
  uv run --project ../skills/draft/scripts/runtime --locked pyright)
uv run --project "$RUNTIME" --locked ruff check "$RUNTIME"
uv run --project "$RUNTIME" --locked ruff check plugins/draft/scripts
uv run --project "$RUNTIME" --locked ruff format --check "$RUNTIME"
uv run --project "$RUNTIME" --locked ruff format --check plugins/draft/scripts
uv lock --project "$RUNTIME" --check
```

Expected: every command exits `0`; core and adapter suites are collected separately.

- [ ] **Step 4: Run both network dependency-window gates**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked \
  pytest "$RUNTIME/tests/integration" -q -m network
uv run --project "$RUNTIME" --locked \
  pytest plugins/draft/scripts/tests/integration -q -m network
```

Expected: every command exits `0`. If external resolution fails, record the exact network/index failure and retry only after establishing it is environmental; do not alter dependency bounds or skip tests.

- [ ] **Step 5: Audit approved-spec coverage and dirty-worktree boundaries**

Run:

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_vocabulary.py" \
  "$RUNTIME/tests/integration/test_distribution_contract.py" -q
uv run --project "$RUNTIME" --locked python - <<'PY'
from draft.diagnostic_codes import CheckCode
from draft.vocabulary import RelationCode

assert "OUTPUT" not in RelationCode.__members__
assert all(not code.value.startswith("output.") for code in CheckCode)
PY
git status --short
git diff --check
```

Expected: the semantic contracts and runtime enum inspection pass, so neither the removed relation nor its diagnostics remain published. This deliberately does not search source text: unrelated variable names, comments and prose are not protocol evidence. Status contains only implementation changes plus the pre-recorded user baseline. `git diff --check` exits `0`.

Manually map every approved spec section 5–18 to at least one passing test file named in this plan. This is a requirements audit, not a substitute for test gates.

- [ ] **Step 6: Commit documentation and final contracts**

```bash
git add -- plugins/draft/skills/draft/SKILL.md \
  plugins/draft/skills/draft/references/draft-format.md \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/support.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_distribution_contract.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_distribution_smoke.py
git commit -m "docs(draft): publish agent cli workflow"
```

No `AGENTS.md` change is planned because Tasks 1–10 implement existing constraints. If implementation changes a long-lived constraint, stop, revise this task's file list, and then update and stage the affected `AGENTS.md` explicitly as required by the repository rules.

---

## Final Review Checklist

The implementing Agent must provide the reviewing Agent with:

- the 11 task commit hashes in order;
- the final `git status --short` compared with the recorded baseline;
- unit, non-network integration, network, Pyright, Ruff and lock outputs from Task 11;
- a command/result matrix for every new query and write command in human and JSON modes;
- proof that dry-run effects and actual public changed paths agree;
- proof that generated conclusions may be stale without blocking move/trash and converge after regeneration;
- proof that all stable status/relation code sets derive from production types rather than duplicated test strings;
- any remaining assumption or external failure, stated without claiming the full gate is green.
