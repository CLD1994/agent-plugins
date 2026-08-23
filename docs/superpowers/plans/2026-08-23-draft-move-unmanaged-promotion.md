# Draft move 未受管理操作数与提升 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `move` 在候选根中接受未受管理的 `.md` 文件与目录作为来源，一条命令完成提升与全部可证明链接的改写，并把入站链接证明范围统一为根内全部 Markdown 文件。

**Architecture:** 规格 §2 的双类型操作数在 `move_operands` 新模块中做词法+文件系统分类与结果校验；规格 §4 的统一闭包在 `move_closure` 新模块中做全根 `.md` 链接可判定性扫描；规划仍收在 `structure.py`——受管理成员复用现有 `plan_path_remap` 管线（经新增的 `extra_moved_targets` 参数），未受管理成员走新的轻语义渲染；执行、日志、恢复、门禁全部复用现状，不改 `operations.py`。

**Tech Stack:** Python 3（runtime 项目，`uv run --project "$RUNTIME" --locked`），pytest（unit/integration 分层），ruff，pyright，markdown-it（既有依赖）。

**Spec:** `docs/superpowers/specs/2026-08-23-draft-move-unmanaged-promotion-design.md`（下文以"规格 §N"引用）。计划中的文件清单与模块划分是实施预估，实施者可在保持行为契约的前提下调整，但规格的行为契约、决策表与错误码约定不可更改。

## Global Constraints

- 门禁命令（核心与适配层测试**严禁混入同一条 pytest 命令**）：

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime

uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit" -q
uv run --project "$RUNTIME" --locked pytest plugins/draft/scripts/tests/unit -q
uv run --project "$RUNTIME" --locked \
  pytest "$RUNTIME/tests/integration" -q -m 'not network'
uv run --project "$RUNTIME" --locked \
  pytest plugins/draft/scripts/tests/integration -q -m 'not network'
uv run --project "$RUNTIME" --locked \
  ruff check "$RUNTIME/src" "$RUNTIME/tests"
uv run --project "$RUNTIME" --locked pyright -p "$RUNTIME/pyproject.toml"
bash plugins/draft/scripts/check-adapters.sh
```

- 单元测试同进程、不访问真实文件系统、不启动子进程、不修改进程级环境；涉及这些边界的用例放 `integration/`。
- 每个任务先写失败测试并确认红，再写最小实现转绿；测试失败先定位根因，禁止用删除测试、放宽断言、吞错误的方式变绿。
- 提交信息：`feat(draft):`/`test(draft):`/`refactor(draft):`/`docs(draft):` 前缀 + 中文主题，尾行 `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`。
- 提交只包含本任务文件；工作区既有 `M AGENTS.md`、未跟踪的 `docs/review-notes/` 与其他 plans 文件**不得**进入任何提交。
- `path.internal` 一经登记进 `CheckCode`，生产与测试中必须使用 `CheckCode.PATH_INTERNAL.value`；`integration/test_distribution_contract.py` 会扫描裸字面量并失败。
- 错误抛出统一 `DraftError(code, *paths, details=(), line=None)`（`src/draft/errors.py`）。
- 链接 = CommonMark 链接（规格 §8）；编码协议保持严格 UTF-8，CLI 不做检测与转换（规格 §7）；CLI 不做模式展开（规格 §9）。

---

## File Structure

| 文件 | 动作 | 责任 |
|---|---|---|
| `src/draft/diagnostic_codes.py` | 修改 | `CheckCode` 登记 `path.internal` |
| `src/draft/diagnostics.py` | 修改 | `_SPECS` 增加 `path.internal` 渲染条目 |
| `src/draft/markdown.py` | 修改 | 新增 `extract_body_links`：无标题/机器块要求的链接提取 |
| `src/draft/paths.py` | 修改 | 新增 `resolve_move_operand`：不要求 `.md` 后缀的操作数规范化 |
| `src/draft/move_operands.py` | 新建 | 操作数分类决策表、目录枚举、前缀映射、结果校验（规格 §2） |
| `src/draft/move_closure.py` | 新建 | 全根 `.md` 链接可判定性扫描与阻碍合并（规格 §4） |
| `src/draft/structure.py` | 修改 | `plan_move_request`/`move_entries` 编排、未受管理渲染、`_render_pages` 增加 `extra_moved_targets`、`_load_clean_graph` 拆出 `_load_structure_facts` |
| `src/draft/cli.py` | 修改 | `move` 参数形态改为 `SOURCE... TARGET`、分派、帮助文本 |
| `tests/unit/test_move_operands.py` | 新建 | 分类决策、映射、结果校验的纯函数用例 |
| `tests/unit/test_move_closure.py` | 新建 | 解码/提取分类、阻碍合并的纯函数用例 |
| `tests/unit/test_markdown.py` | 修改 | `extract_body_links` 用例 |
| `tests/unit/test_move_planning.py` | 新建 | `plan_move_request` 各分支用例（合成图） |
| `tests/integration/test_move_promotion.py` | 新建 | 提升、树形迁移、阻碍、恢复端到端 |
| `tests/integration/test_cli.py` | 修改 | `SOURCE... TARGET` 形态与退出码用例 |
| `plugins/draft/skills/draft/SKILL.md`、`plugins/draft/skills/draft/references/migration.md` | 修改 | 文档化"只移动 `.md`"、转码步骤、平台展开行为、树形迁移示例 |

所有 `src/`、`tests/` 路径均相对于 `plugins/draft/skills/draft/scripts/runtime/`。

---

### Task 1: 登记 `path.internal` 错误码

**Files:**
- Modify: `src/draft/diagnostic_codes.py`（`CheckCode` 枚举，`PATH_SYMLINK_ESCAPE` 成员旁）
- Modify: `src/draft/diagnostics.py`（`_SPECS` 字典，`CheckCode.PATH_SYMLINK_ESCAPE` 条目旁）
- Test: `tests/unit/test_move_operands.py`（新建，本任务只放错误码用例）

**Interfaces:**
- Produces: `CheckCode.PATH_INTERNAL`，值 `"path.internal"`；后续任务一律用 `.value` 引用。

- [ ] **Step 1: 写失败测试**

新建 `tests/unit/test_move_operands.py`：

```python
from __future__ import annotations


def test_path_internal_is_registered_with_rendering() -> None:
    from draft.diagnostic_codes import CheckCode
    from draft.diagnostics import KNOWN_CHECK_CODES

    assert CheckCode.PATH_INTERNAL.value == "path.internal"
    assert "path.internal" in KNOWN_CHECK_CODES
```

- [ ] **Step 2: 运行确认红**

Run: `uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit/test_move_operands.py" -q`
Expected: FAIL（`AttributeError: PATH_INTERNAL`）

- [ ] **Step 3: 登记枚举成员**

`diagnostic_codes.py` 的 `CheckCode` 中，紧邻 `PATH_SYMLINK_ESCAPE` 成员：

```python
    PATH_INTERNAL = "path.internal"
```

- [ ] **Step 4: 运行确认渲染目录门禁转红**

Run: `uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit/test_diagnostics.py::test_catalog_covers_every_typed_check_code" -q`
Expected: FAIL（新成员缺 `_SPECS` 条目）

- [ ] **Step 5: 补渲染条目**

`diagnostics.py` 的 `_SPECS` 中，紧邻 `CheckCode.PATH_SYMLINK_ESCAPE` 条目，完全仿照其形状：

```python
    CheckCode.PATH_INTERNAL: DiagnosticSpec(
        _FILE, _fixed(0), _text("操作数指向内部协议领地")
    ),
```

- [ ] **Step 6: 运行确认绿**

Run: `uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit/test_move_operands.py" "$RUNTIME/tests/unit/test_diagnostics.py" -q`
Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add plugins/draft/skills/draft/scripts/runtime/src/draft/diagnostic_codes.py \
        plugins/draft/skills/draft/scripts/runtime/src/draft/diagnostics.py \
        plugins/draft/skills/draft/scripts/runtime/tests/unit/test_move_operands.py
git commit -m "feat(draft): 登记 path.internal 操作数错误码

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: `extract_body_links`——无页面协议要求的链接提取

**Files:**
- Modify: `src/draft/markdown.py`
- Test: `tests/unit/test_markdown.py`

**Interfaces:**
- Produces: `extract_body_links(text: str) -> tuple[MarkdownLink, ...]`。复用 `parse_page` 的对齐管线（`COMMONMARK.parse` → `_line_starts` → `_scan_definitions` → `_scan_candidates` → `_token_links` → `_align_candidates`），不检查标题与机器块；对齐失败照抛 `page.parse_error`。`move_closure` 与 `structure` 的未受管理渲染都消费它。

- [ ] **Step 1: 写失败测试**

在 `tests/unit/test_markdown.py` 追加（`extract_body_links` 加入该文件既有的 `from draft.markdown import (...)` 导入列表）：

```python
def test_extract_body_links_without_machine_block() -> None:
    text = "正文 [旧稿](00-background-and-evidence.md) 与 ![图](img/a.png)。\n"

    links = extract_body_links(text)

    assert [(link.destination, link.is_image) for link in links] == [
        ("00-background-and-evidence.md", False),
        ("img/a.png", True),
    ]
    assert links[0].destination_span == SourceSpan(
        text.index("00-background-and-evidence.md"),
        text.index("00-background-and-evidence.md") + len("00-background-and-evidence.md"),
    )


def test_extract_body_links_resolves_defined_reference_links() -> None:
    text = "看 [旧稿][legacy]。\n\n[legacy]: 00-bg.md \"标题\"\n"

    assert [link.destination for link in extract_body_links(text)] == ["00-bg.md"]


def test_extract_body_links_ignores_undefined_reference_forms() -> None:
    text = "看 [旧稿][legacy] 与 <00-bg.md>。\n"

    assert extract_body_links(text) == ()


def test_extract_body_links_alignment_failure_raises_parse_error() -> None:
    text = "看 [x](a\\.md)。\n"

    with pytest.raises(DraftError, match=CheckCode.PAGE_PARSE_ERROR.value):
        extract_body_links(text)
```

（`SourceSpan` 已在该测试文件的导入链中可用；若缺则从 `draft.models` 导入。）

- [ ] **Step 2: 运行确认红**

Run: `uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit/test_markdown.py" -q -k extract_body_links`
Expected: FAIL（`ImportError: cannot import name 'extract_body_links'`）

- [ ] **Step 3: 实现**

`markdown.py` 中，紧随 `parse_page` 之后。链接组装必须逐字段镜像 `parse_page` 中由 `aligned` 构造 `MarkdownLink` 的代码段（写实现前先读该段核对字段来源）：

```python
def extract_body_links(text: str) -> tuple[MarkdownLink, ...]:
    """提取正文 CommonMark 链接，不要求标题与机器块。

    供未受管理文件的链接可判定性判定与改写使用（规格 §4.1）：与
    parse_page 共享同一对齐管线，对齐失败同样抛 page.parse_error，
    由调用方归类为阻碍。
    """
    tokens = COMMONMARK.parse(text, {})
    starts = _line_starts(text)
    lines = text.split("\n")
    definitions = _scan_definitions(lines, starts, _fenced_line_ranges(lines))
    candidates = _scan_candidates(text, starts, definitions)
    token_links = _token_links(tokens)
    aligned = _align_candidates(candidates, token_links)
    links: list[MarkdownLink] = []
    for candidate, _token in aligned:
        links.append(
            MarkdownLink(
                label=candidate.raw_label,
                destination=candidate.destination,
                destination_span=SourceSpan(candidate.dest_start, candidate.dest_end),
                whole_span=SourceSpan(candidate.start, candidate.end),
                line=_line_of(starts, candidate.start),
                is_image=candidate.is_image,
                reference_label=candidate.reference_label,
            )
        )
    return tuple(links)
```

- [ ] **Step 4: 运行确认绿**

Run: `uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit/test_markdown.py" -q`
Expected: PASS（含既有全部用例）

- [ ] **Step 5: 提交**

```bash
git add plugins/draft/skills/draft/scripts/runtime/src/draft/markdown.py \
        plugins/draft/skills/draft/scripts/runtime/tests/unit/test_markdown.py
git commit -m "feat(draft): 提供无页面协议要求的正文链接提取

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: `resolve_move_operand`——不要求 `.md` 后缀的操作数规范化

**Files:**
- Modify: `src/draft/paths.py`
- Test: `tests/unit/test_paths.py`

**Interfaces:**
- Produces: `resolve_move_operand(root: Path, value: str) -> ResolvedPath`。与 `resolve_page_operand`（paths.py:186）同构，唯一差异是不做后缀检查——目录与非 `.md` 文件的类型分类是 `move_operands` 的职责（规格 §2.2 顺序 4—8）。

- [ ] **Step 1: 写失败测试**

在 `tests/unit/test_paths.py` 追加（沿用该文件既有的 `tmp_path` 用例风格）：

```python
def test_resolve_move_operand_accepts_directory_and_non_markdown(tmp_path: Path) -> None:
    (tmp_path / "legacy-notes").mkdir()
    (tmp_path / "img.png").write_bytes(b"x")

    directory = resolve_move_operand(tmp_path, "legacy-notes")
    image = resolve_move_operand(tmp_path, "img.png")

    assert directory.relative == Path("legacy-notes")
    assert image.relative == Path("img.png")


def test_resolve_move_operand_rejects_escape_and_symlink(tmp_path: Path) -> None:
    (tmp_path / "real.md").write_text("x", encoding="utf-8")
    (tmp_path / "link.md").symlink_to(tmp_path / "real.md")

    with pytest.raises(DraftError, match="path.escape"):
        resolve_move_operand(tmp_path, "../outside.md")
    with pytest.raises(DraftError, match=CheckCode.PATH_SYMLINK_ESCAPE.value):
        resolve_move_operand(tmp_path, "link.md")
```

- [ ] **Step 2: 运行确认红**

Run: `uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit/test_paths.py" -q -k resolve_move_operand`
Expected: FAIL（`ImportError`）

- [ ] **Step 3: 实现**

`paths.py` 中，紧随 `resolve_page_operand`。逐行复制该函数，删除后缀检查块：

```python
def resolve_move_operand(root: Path, value: str) -> ResolvedPath:
    """规范化 move 操作数；与页面操作数同规则，但不要求 .md 后缀。

    目录与非 .md 普通文件也是合法操作数形状（规格 §2.2），类型分类
    由调用方完成。
    """
    canonical_root = _expand_root(root).resolve(strict=False)
    posix = PurePosixPath(value)
    if posix.is_absolute():
        raise DraftError("path.escape", Path(value))
    normalized = Path(*posix.parts)
    candidate = canonical_root / normalized
    cursor = canonical_root
    for part in normalized.parts:
        cursor /= part
        if cursor.is_symlink():
            raise DraftError(CheckCode.PATH_SYMLINK_ESCAPE.value, cursor)
    canonical = candidate.resolve(strict=False)
    try:
        relative = canonical.relative_to(canonical_root)
    except ValueError as exc:
        raise DraftError("path.escape", candidate) from exc
    return ResolvedPath(root=canonical_root, relative=relative, absolute=canonical_root / relative)
```

注意：本函数**不**检查 `..`（`relative_to` 越界分支覆盖）也**不**检查 `.draft/` 领地（那是分类层的词法判定，规格 §2.2 顺序 2，先于符号链接检查执行）。

- [ ] **Step 4: 运行确认绿**

Run: `uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit/test_paths.py" -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add plugins/draft/skills/draft/scripts/runtime/src/draft/paths.py \
        plugins/draft/skills/draft/scripts/runtime/tests/unit/test_paths.py
git commit -m "feat(draft): move 操作数规范化不再要求 .md 后缀

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: `move_operands`——分类决策、映射与结果校验

**Files:**
- Create: `src/draft/move_operands.py`
- Test: `tests/unit/test_move_operands.py`（Task 1 已建）

**Interfaces:**
- Consumes: `CheckCode.PATH_INTERNAL`（Task 1）、`resolve_move_operand`（Task 3）、`physical_parent`（paths.py:208）、`DraftGraph`、`EntryKind`/`classify_node`（observations.py:39 / operations.py:660）。
- Produces:
  - `SourceKind`（`MANAGED_PAGE`/`UNMANAGED_FILE`/`DIRECTORY`）、`TargetKind`（`FILE`/`PREFIX`）；
  - `MoveSource(path: Path, kind: SourceKind, members: tuple[Path, ...])`、`MoveTarget(path: Path, kind: TargetKind)`、`MoveRequest(sources: tuple[MoveSource, ...], target: MoveTarget, results: Mapping[Path, Path])`；
  - `is_internal_territory(relative: Path) -> bool`；
  - `map_sources(sources, target) -> dict[Path, Path]`；
  - `validate_results(graph, results, directory_sources) -> None`；
  - `enumerate_directory(root: Path, directory: Path) -> tuple[Path, ...]`；
  - `classify_move_request(root: Path, graph: DraftGraph, source_values: Sequence[str], target_value: str) -> MoveRequest`。

- [ ] **Step 1: 写纯函数失败测试**

`tests/unit/test_move_operands.py` 追加（保留 Task 1 用例）。合成图只需 `pages` 成员关系，直接构造最小 `DraftGraph`——参照 `tests/unit/test_structure.py` 的 `_graph_from_texts` 模式；本任务只需要 `graph.pages` 键，用该辅助函数构造含 `index.md` 与 `pages/a.md` 的图：

```python
from pathlib import Path

import pytest

from draft.errors import DraftError
from draft.move_operands import (
    MoveTarget,
    TargetKind,
    is_internal_territory,
    map_sources,
    validate_results,
)


def test_is_internal_territory_only_matches_root_level_draft() -> None:
    assert is_internal_territory(Path(".draft/x.md"))
    assert not is_internal_territory(Path("legacy/.draft/x.md"))
    assert not is_internal_territory(Path("notes.md"))


def test_map_sources_file_takes_basename_directory_takes_own_name() -> None:
    from draft.move_operands import MoveSource, SourceKind

    sources = (
        MoveSource(Path("stray.md"), SourceKind.UNMANAGED_FILE, (Path("stray.md"),)),
        MoveSource(
            Path("legacy/notes"),
            SourceKind.DIRECTORY,
            (Path("legacy/notes/index.md"), Path("legacy/notes/a.md")),
        ),
    )
    target = MoveTarget(Path("pages"), TargetKind.PREFIX)

    assert map_sources(sources, target) == {
        Path("stray.md"): Path("pages/stray.md"),
        Path("legacy/notes/index.md"): Path("pages/notes/index.md"),
        Path("legacy/notes/a.md"): Path("pages/notes/a.md"),
    }
```

`validate_results` 用例（每个错误分支一个用例；`_graph_from_texts` 构造含 `index.md`、`pages/index.md` 形态的图——照抄 `test_structure.py` 顶部 `_INDEX` 等页面常量与其构造调用）：

```python
def test_validate_results_rejects_outside_namespace() -> None:
    graph = _graph_from_texts({Path("index.md"): _INDEX})

    with pytest.raises(DraftError) as caught:
        validate_results(graph, {Path("s.md"): Path("notes/s.md")}, ())
    assert caught.value.code == "structure.invalid_target"


def test_validate_results_rejects_existing_page_and_collision() -> None:
    graph = _graph_from_texts({Path("index.md"): _INDEX, Path("pages/b.md"): _PAGE_B})

    with pytest.raises(DraftError) as caught:
        validate_results(graph, {Path("s.md"): Path("pages/b.md")}, ())
    assert caught.value.code == "structure.target_exists"

    with pytest.raises(DraftError) as caught:
        validate_results(
            graph,
            {Path("s1.md"): Path("pages/x.md"), Path("s2.md"): Path("pages/x.md")},
            (),
        )
    assert caught.value.code == "structure.conflict"


def test_validate_results_rejects_result_inside_directory_source() -> None:
    graph = _graph_from_texts({Path("index.md"): _INDEX})

    with pytest.raises(DraftError) as caught:
        validate_results(
            graph,
            {Path("legacy/index.md"): Path("legacy/index.md")},
            (Path("legacy"),),
        )
    assert caught.value.code == "structure.descendant"


def test_validate_results_parent_occupied_by_existing_or_co_moved_page() -> None:
    graph = _graph_from_texts({Path("index.md"): _INDEX})

    # 父页 = 根 index.md（存在）→ 通过
    validate_results(graph, {Path("s.md"): Path("pages/s.md")}, ())

    # 父页 = pages/topic/index.md，由同批文件占据 → 通过
    validate_results(
        graph,
        {
            Path("t/index.md"): Path("pages/topic/index.md"),
            Path("t/a.md"): Path("pages/topic/a.md"),
        },
        (),
    )

    # 父页 pages/topic/index.md 无人占据 → missing_parent
    with pytest.raises(DraftError) as caught:
        validate_results(graph, {Path("t/a.md"): Path("pages/topic/a.md")}, ())
    assert caught.value.code == "structure.missing_parent"

    # 父页是受管理页面但被同批移走且无文件落回原位 → missing_parent
    graph_with_hub = _graph_from_texts(
        {Path("index.md"): _INDEX, Path("pages/topic/index.md"): _HUB}
    )
    with pytest.raises(DraftError) as caught:
        validate_results(
            graph_with_hub,
            {
                Path("pages/topic/index.md"): Path("pages/archive/topic/index.md"),
                Path("s.md"): Path("pages/topic/s.md"),
            },
            (),
        )
    assert caught.value.code == "structure.missing_parent"
```

（`_PAGE_B`、`_HUB` 等页面常量按 `test_structure.py` 既有模板补在该测试文件；`_graph_from_texts` 从 `test_structure.py` 复制其实现到本测试文件——两份测试各自持有该辅助函数是当前测试树的既有做法，不构成需要抽取的重复。）

- [ ] **Step 2: 运行确认红**

Run: `uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit/test_move_operands.py" -q`
Expected: FAIL（`ModuleNotFoundError: draft.move_operands`）

- [ ] **Step 3: 实现纯函数层**

新建 `src/draft/move_operands.py`：

```python
"""move 的双类型操作数：分类、映射与结果校验（规格 §2）。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Mapping, Sequence

from draft.diagnostic_codes import CheckCode
from draft.errors import DraftError
from draft.graph import DraftGraph
from draft.paths import physical_parent

_INTERNAL_DIR = ".draft"
_PAGES_DIR = "pages"


class SourceKind(StrEnum):
    MANAGED_PAGE = "managed_page"
    UNMANAGED_FILE = "unmanaged_file"
    DIRECTORY = "directory"


class TargetKind(StrEnum):
    FILE = "file"
    PREFIX = "prefix"


@dataclass(frozen=True)
class MoveSource:
    path: Path
    kind: SourceKind
    members: tuple[Path, ...]


@dataclass(frozen=True)
class MoveTarget:
    path: Path
    kind: TargetKind


@dataclass(frozen=True)
class MoveRequest:
    sources: tuple[MoveSource, ...]
    target: MoveTarget
    results: Mapping[Path, Path]


def is_internal_territory(relative: Path) -> bool:
    """内部协议领地是词法事实：第一个路径段为 .draft（规格 §2.2 顺序 2）。"""
    return bool(relative.parts) and relative.parts[0] == _INTERNAL_DIR


def target_kind_of(value: str) -> TargetKind:
    return TargetKind.FILE if PurePosixPath(value).suffix == ".md" else TargetKind.PREFIX


def map_sources(sources: Sequence[MoveSource], target: MoveTarget) -> dict[Path, Path]:
    """规格 §2.4 映射规则：文件取基名，目录取目录名 + 成员相对路径。"""
    results: dict[Path, Path] = {}
    for source in sources:
        if source.kind is SourceKind.DIRECTORY:
            for member in source.members:
                results[member] = target.path / source.path.name / member.relative_to(
                    source.path
                )
        else:
            results[source.path] = target.path / source.path.name
    return results


def validate_results(
    graph: DraftGraph, results: Mapping[Path, Path], directory_sources: Sequence[Path]
) -> None:
    """规格 §2.4 结果校验：命名空间、既有页、来源目录内部、碰撞、父页占据。"""
    result_paths = set(results.values())
    source_paths = set(results)
    seen: set[Path] = set()
    for source in sorted(results, key=lambda path: results[path].as_posix()):
        result = results[source]
        if not result.parts or result.parts[0] != _PAGES_DIR:
            raise DraftError("structure.invalid_target", result)
        if result in graph.pages:
            raise DraftError("structure.target_exists", result)
        for directory in directory_sources:
            if result == directory or result.is_relative_to(directory):
                raise DraftError("structure.descendant", result)
        if result in seen:
            raise DraftError("structure.conflict", source)
        seen.add(result)
        parent = physical_parent(result)
        if parent is None:
            raise DraftError("structure.missing_parent", result)
        if parent in result_paths:
            continue
        if parent in graph.pages and parent not in source_paths:
            continue
        raise DraftError("structure.missing_parent", result)
```

- [ ] **Step 4: 运行确认绿**

Run: `uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit/test_move_operands.py" -q`
Expected: PASS

- [ ] **Step 5: 实现文件系统层（枚举与分类编排）**

同文件追加。`enumerate_directory` 与 `classify_move_request` 是薄 IO 编排，其行为由 Task 8/10 的集成测试覆盖；此处实现的正确性由代码评审与后续红测保证：

```python
def enumerate_directory(root: Path, directory: Path) -> tuple[Path, ...]:
    """规格 §2.3：只收集 .md 普通文件；符号链接条目拒绝整批；空枚举 → unknown。"""
    from draft.observations import EntryKind
    from draft.operations import classify_node

    members: list[Path] = []
    base = root / directory
    for descendant in sorted(base.rglob("*"), key=lambda item: item.as_posix()):
        relative = descendant.relative_to(root)
        if _INTERNAL_DIR in relative.parts:
            continue
        if classify_node(descendant) is EntryKind.SYMLINK:
            raise DraftError(CheckCode.PATH_SYMLINK_ESCAPE.value, relative)
        if not descendant.is_file() or descendant.suffix != ".md":
            continue
        members.append(relative)
    if not members:
        raise DraftError("structure.unknown_page", directory)
    return tuple(members)


def classify_move_request(
    root: Path,
    graph: DraftGraph,
    source_values: Sequence[str],
    target_value: str,
) -> MoveRequest:
    """规格 §2.2 顺序 1—8 与 §2.1 形态校验；在事实加载之后调用（规格 §5）。"""
    from draft.observations import EntryKind
    from draft.operations import classify_node
    from draft.paths import resolve_move_operand

    from draft.graph import CONCLUSIONS_PAGE, ROOT_PAGE

    target_kind = target_kind_of(target_value)
    target_relative = _lexical_operand(target_value)
    sources: list[MoveSource] = []
    for value in source_values:
        relative = _lexical_operand(value)
        if is_internal_territory(relative):
            raise DraftError(CheckCode.PATH_INTERNAL.value, relative)
        resolved = resolve_move_operand(root, value)
        entry_kind = classify_node(resolved.absolute)
        if entry_kind is EntryKind.DIRECTORY:
            if relative == Path(_PAGES_DIR):
                raise DraftError("structure.protected", relative)
            members = enumerate_directory(root, relative)
            sources.append(MoveSource(relative, SourceKind.DIRECTORY, members))
            continue
        if entry_kind is EntryKind.FILE:
            if relative in graph.pages:
                if relative in (ROOT_PAGE, CONCLUSIONS_PAGE):
                    raise DraftError("structure.protected", relative)
                sources.append(MoveSource(relative, SourceKind.MANAGED_PAGE, (relative,)))
                continue
            if resolved.relative.suffix == ".md":
                sources.append(MoveSource(relative, SourceKind.UNMANAGED_FILE, (relative,)))
                continue
            raise DraftError("path.not_markdown", relative)
        raise DraftError("structure.unknown_page", relative)

    if target_kind is TargetKind.FILE:
        if len(sources) != 1 or sources[0].kind is SourceKind.DIRECTORY:
            raise DraftError(
                "cli.move_operands_invalid", details=("文件目标只允许一个文件 SOURCE",)
            )
        results: Mapping[Path, Path] = {sources[0].path: target_relative}
    else:
        results = map_sources(sources, MoveTarget(target_relative, target_kind))
    return MoveRequest(tuple(sources), MoveTarget(target_relative, target_kind), results)


def _lexical_operand(value: str) -> Path:
    """规格 §2.2 顺序 1：绝对路径与 .. 在任何文件系统探测之前拒绝。"""
    posix = PurePosixPath(value)
    if posix.is_absolute() or ".." in posix.parts:
        raise DraftError("path.escape", Path(value))
    return Path(*posix.parts)
```

注意 `classify_move_request` 的次序严格按规格 §2.2：逃逸（`_lexical_operand`）→ `.draft/`（词法）→ 符号链接（`resolve_move_operand` 逐组件）→ 目录/成员关系/后缀/存在。目标操作数只做词法检查与分类，不做 `resolve_move_operand`——目标不存在是合法的（将创建）。

- [ ] **Step 6: 静态门禁**

Run: `uv run --project "$RUNTIME" --locked ruff check "$RUNTIME/src/draft/move_operands.py" "$RUNTIME/tests/unit/test_move_operands.py" && uv run --project "$RUNTIME" --locked pyright -p "$RUNTIME/pyproject.toml"`
Expected: 全绿

- [ ] **Step 7: 提交**

```bash
git add plugins/draft/skills/draft/scripts/runtime/src/draft/move_operands.py \
        plugins/draft/skills/draft/scripts/runtime/tests/unit/test_move_operands.py
git commit -m "feat(draft): move 双类型操作数的分类映射与结果校验

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: `move_closure`——全根链接可判定性扫描

**Files:**
- Create: `src/draft/move_closure.py`
- Test: `tests/unit/test_move_closure.py`

**Interfaces:**
- Consumes: `extract_body_links`（Task 2）、`resolve_body_resource`（resources.py:252）、`DraftGraph`、graph 常量 `INTERNAL_DIR`/`PAGES_DIR`（graph.py:39-44）。
- Produces:
  - `UnmanagedFile(text: str, references: tuple[ResourceReference, ...])`；
  - `MoveClosure(unmanaged: Mapping[Path, UnmanagedFile], blocking: tuple[Path, ...])`；
  - `decode_and_extract(raw: bytes) -> tuple[str, tuple[MarkdownLink, ...]] | None`；
  - `scan_move_closure(root: Path, graph: DraftGraph) -> MoveClosure`；
  - `merged_blocking(facts_blocking: Sequence[Path], closure: MoveClosure) -> tuple[Path, ...]`。

- [ ] **Step 1: 写失败测试（纯函数层）**

```python
from __future__ import annotations

from pathlib import Path

from draft.move_closure import decode_and_extract, merged_blocking, MoveClosure


def test_decode_and_extract_rejects_non_utf8_bytes() -> None:
    raw = "参考 [旧稿](00-bg.md)。".encode("gb18030")

    assert decode_and_extract(raw) is None


def test_decode_and_extract_rejects_alignment_failure() -> None:
    raw = "看 [x](a\\.md)。\n".encode("utf-8")

    assert decode_and_extract(raw) is None


def test_decode_and_extract_returns_text_and_links() -> None:
    raw = "看 [旧稿](00-bg.md)。\n".encode("utf-8")

    parsed = decode_and_extract(raw)

    assert parsed is not None
    text, links = parsed
    assert [link.destination for link in links] == ["00-bg.md"]


def test_merged_blocking_unions_and_sorts() -> None:
    closure = MoveClosure(unmanaged={}, blocking=(Path("z.md"), Path("a.md")))

    assert merged_blocking([Path("m.md"), Path("a.md")], closure) == (
        Path("a.md"),
        Path("m.md"),
        Path("z.md"),
    )
```

- [ ] **Step 2: 运行确认红**

Run: `uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit/test_move_closure.py" -q`
Expected: FAIL（`ModuleNotFoundError: draft.move_closure`）

- [ ] **Step 3: 实现**

```python
"""move 专用事实闭包：根内全部 Markdown 的链接可判定性（规格 §4）。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from draft.errors import DraftError
from draft.graph import INTERNAL_DIR, PAGES_DIR, DraftGraph
from draft.markdown import extract_body_links
from draft.models import MarkdownLink
from draft.resources import ResourceReference, resolve_body_resource


@dataclass(frozen=True)
class UnmanagedFile:
    text: str
    references: tuple[ResourceReference, ...]


@dataclass(frozen=True)
class MoveClosure:
    unmanaged: Mapping[Path, UnmanagedFile]
    blocking: tuple[Path, ...]


def decode_and_extract(raw: bytes) -> tuple[str, tuple[MarkdownLink, ...]] | None:
    """不可解码或链接不可提取 → None（调用方计入阻碍，规格 §4.2）。"""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    try:
        return text, extract_body_links(text)
    except DraftError:
        return None


def merged_blocking(
    facts_blocking: Sequence[Path], closure: MoveClosure
) -> tuple[Path, ...]:
    return tuple(sorted(set(facts_blocking) | set(closure.blocking)))


def scan_move_closure(root: Path, graph: DraftGraph) -> MoveClosure:
    unmanaged: dict[Path, UnmanagedFile] = {}
    blocking: set[Path] = set()
    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        if entry.name in (INTERNAL_DIR, PAGES_DIR.name):
            # .draft 不是内容；pages/ 下 .md 全部是受管理页面，其可解析性
            # 已由事实加载的 blocking_paths 覆盖。
            continue
        _scan_entry(root, entry, graph, unmanaged, blocking)
    return MoveClosure(unmanaged=unmanaged, blocking=tuple(sorted(blocking)))


def _scan_entry(
    root: Path,
    absolute: Path,
    graph: DraftGraph,
    unmanaged: dict[Path, UnmanagedFile],
    blocking: set[Path],
) -> None:
    if absolute.is_symlink():
        # 符号链接目标由 check 的 link.escape 问题进入既有 blocking。
        return
    if absolute.is_dir():
        for descendant in sorted(absolute.rglob("*"), key=lambda item: item.as_posix()):
            if descendant.is_symlink() or not descendant.is_file():
                continue
            if descendant.suffix != ".md":
                continue
            _scan_unmanaged_file(root, descendant.relative_to(root), graph, unmanaged, blocking)
        return
    if not absolute.is_file() or absolute.suffix != ".md":
        return
    _scan_unmanaged_file(root, absolute.relative_to(root), graph, unmanaged, blocking)


def _scan_unmanaged_file(
    root: Path,
    relative: Path,
    graph: DraftGraph,
    unmanaged: dict[Path, UnmanagedFile],
    blocking: set[Path],
) -> None:
    if relative in graph.pages:
        return
    parsed = decode_and_extract((root / relative).read_bytes())
    if parsed is None:
        blocking.add(relative)
        return
    text, links = parsed
    references = tuple(
        ResourceReference(
            link=link, resource=resolve_body_resource(root, relative, link.destination)
        )
        for link in links
    )
    unmanaged[relative] = UnmanagedFile(text=text, references=references)
```

- [ ] **Step 4: 运行确认绿**

Run: `uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit/test_move_closure.py" -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add plugins/draft/skills/draft/scripts/runtime/src/draft/move_closure.py \
        plugins/draft/skills/draft/scripts/runtime/tests/unit/test_move_closure.py
git commit -m "feat(draft): move 全根 Markdown 链接可判定性闭包

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: 未受管理成员的渲染与移动集身份

**Files:**
- Modify: `src/draft/structure.py`
- Test: `tests/unit/test_move_planning.py`（新建）

**Interfaces:**
- Consumes: `MoveClosure`/`UnmanagedFile`（Task 5）、`rebase_body_destination`（resources.py:433）、`rewrite_link_destinations`（markdown.py:710）、`extract_body_links`（Task 2）、`resolve_body_resource`、`ResourceIdentity`/`ResourceKind`/`ProbeState`（resources.py）、`FileMutation`/`MutationKind`（models.py:143-149）、`PlanEffect`/`PlanAction`（plan_effects.py:14-33）、`sha256_bytes`（operations.py:117）。
- Produces:
  - `_unmanaged_moved_identities(root: Path, unmanaged_moves: Mapping[Path, Path]) -> dict[ResourceIdentity, ResourceIdentity]`；
  - `_plan_unmanaged(root, closure, unmanaged_moves, moved_targets) -> tuple[list[FileMutation], list[PlanEffect]]`。

  Task 7 的编排函数消费这两个单元。

- [ ] **Step 1: 写失败测试**

新建 `tests/unit/test_move_planning.py`。合成闭包不访问文件系统：`UnmanagedFile` 直接构造，`references` 用 `resolve_body_resource(ROOT, source, destination, target_probe=...)` 注入常亮探针生成（`ROOT = Path("/draft")`，与 `test_structure.py` 一致）：

```python
from __future__ import annotations

from pathlib import Path

import pytest

from draft.errors import DraftError
from draft.models import MarkdownLink, SourceSpan
from draft.move_closure import MoveClosure, UnmanagedFile
from draft.resources import ProbeState, ResourceReference, resolve_body_resource

ROOT = Path("/draft")
_PRESENT = lambda _path: ProbeState.FILE  # noqa: E731 —— 测试替身：目标恒存在


def _reference(source: Path, text: str, destination: str) -> tuple[str, ResourceReference]:
    """构造一条指向 destination 的链接引用；返回 (整段文本, 引用)。"""
    link_text = f"[目标]({destination})"
    full = f"前文 {link_text} 后文\n" if not text else text
    start = full.index(destination)
    link = MarkdownLink(
        label="目标",
        destination=destination,
        destination_span=SourceSpan(start, start + len(destination)),
        whole_span=SourceSpan(full.index(link_text), full.index(link_text) + len(link_text)),
        line=1,
    )
    resource = resolve_body_resource(ROOT, source, destination, target_probe=_PRESENT)
    return full, ResourceReference(link=link, resource=resource)
```

出站重算（来源移动后相对链接按新基址重表达，scheme 与锚点不动）：

```python
def test_plan_unmanaged_rebases_outbound_relative_links() -> None:
    from draft.structure import _plan_unmanaged

    source = Path("00-background.md")
    text, ref_local = _reference(source, "", "other.md")
    _ext = "[外](https://example.com/x)"
    full = text + _ext + "\n"
    # 手工链接位置仅 ref_local 参与断言，外链通过文本不变性验证
    closure = MoveClosure(unmanaged={source: UnmanagedFile(full, (ref_local,))}, blocking=())

    mutations, effects = _plan_unmanaged(
        ROOT, closure, {source: Path("pages/00-background.md")}, {}
    )

    writes = [m for m in mutations if m.kind.value == "write"]
    deletes = [m for m in mutations if m.kind.value == "delete"]
    assert len(writes) == 1 and len(deletes) == 1
    assert writes[0].path == Path("pages/00-background.md")
    assert deletes[0].path == source
    rendered = writes[0].content.decode("utf-8")
    assert "[目标](../other.md)" in rendered
    assert _ext in rendered
    assert effects[0].from_path == source and effects[0].to_path == Path("pages/00-background.md")
```

入站改写（未移动链接者按自身位置重算指向移动集合的链接）：

```python
def test_plan_unmanaged_rewrites_inbound_links_from_stationary_files() -> None:
    from draft.resources import ResourceIdentity, ResourceKind
    from draft.structure import _plan_unmanaged

    mover = Path("00-background.md")
    linker = Path("notes/reading.md")
    text, ref = _reference(linker, "", "../00-background.md")
    closure = MoveClosure(
        unmanaged={
            mover: UnmanagedFile("无链接正文\n", ()),
            linker: UnmanagedFile(text, (ref,)),
        },
        blocking=(),
    )
    moved_targets = {
        ResourceIdentity(ResourceKind.LOCAL, "00-background.md"): ResourceIdentity(
            ResourceKind.PAGE, "pages/00-background.md"
        )
    }

    mutations, _effects = _plan_unmanaged(ROOT, closure, {mover: Path("pages/00-background.md")}, moved_targets)

    by_path = {m.path: m for m in mutations}
    linker_write = by_path[linker]
    assert linker_write.before_sha256 is not None
    assert "[目标](../pages/00-background.md)" in linker_write.content.decode("utf-8")
```

修改后文本必须仍可提取链接（规格 §4.3 提交前校验的计划期形态）。正常管线不会产生不可提取的渲染结果，用受真实接口约束的替身注入：

```python
def test_plan_unmanaged_rejects_rendered_text_that_lost_extractability(
    monkeypatch,
) -> None:
    from draft import structure
    from draft.structure import _plan_unmanaged

    source = Path("00-background.md")
    text, ref = _reference(source, "", "other.md")
    closure = MoveClosure(unmanaged={source: UnmanagedFile(text, (ref,))}, blocking=())

    def _broken_rewrite(text: str, replacements) -> str:
        return "损坏 [不可提取("

    monkeypatch.setattr(structure, "rewrite_link_destinations", _broken_rewrite)

    with pytest.raises(DraftError) as caught:
        _plan_unmanaged(ROOT, closure, {source: Path("pages/00-background.md")}, {})

    assert caught.value.code == "operation.plan_invalid"
```

（`_reference(source, "", "other.md")` 的目标是相对链接，来源移动必然触发改写，保证替身被执行到。）

- [ ] **Step 2: 运行确认红**

Run: `uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit/test_move_planning.py" -q`
Expected: FAIL（`ImportError: cannot import name '_plan_unmanaged'`）

- [ ] **Step 3: 实现**

`structure.py` 追加（导入 `extract_body_links`、`rewrite_link_destinations`、`rebase_body_destination`、`resolve_body_resource`、`ResourceIdentity`、`ResourceKind`、`ProbeState`、`sha256_bytes`、`MutationKind`、`PlanAction`、`PlanEffect` 等按现有导入块风格补齐）：

```python
def _unmanaged_moved_identities(
    root: Path, unmanaged_moves: Mapping[Path, Path]
) -> dict[ResourceIdentity, ResourceIdentity]:
    """未受管理旧路径的资源身份按词法分类解析（根级/非 pages 为 LOCAL），
    与链接者侧的解析保持一致，保证 moved_targets 命中。"""

    def _present(_path: Path) -> ProbeState:
        return ProbeState.FILE

    identities: dict[ResourceIdentity, ResourceIdentity] = {}
    for old, new in unmanaged_moves.items():
        old_identity = resolve_body_resource(
            root, ROOT_PAGE, old.as_posix(), target_probe=_present
        ).identity
        new_identity = resolve_body_resource(
            root, ROOT_PAGE, new.as_posix(), target_probe=_present
        ).identity
        identities[old_identity] = new_identity
    return identities


def _assert_link_extractable(path: Path, text: str) -> None:
    try:
        extract_body_links(text)
    except DraftError as error:
        raise DraftError("operation.plan_invalid", path, details=(error.code,)) from error


def _plan_unmanaged(
    root: Path,
    closure: MoveClosure,
    unmanaged_moves: Mapping[Path, Path],
    moved_targets: Mapping[ResourceIdentity, ResourceIdentity],
) -> tuple[list[FileMutation], list[PlanEffect]]:
    mutations: list[FileMutation] = []
    effects: list[PlanEffect] = []
    for path in sorted(closure.unmanaged, key=lambda item: item.as_posix()):
        file = closure.unmanaged[path]
        new_source = unmanaged_moves.get(path, path)
        edits: dict[SourceSpan, str] = {}
        for reference in file.references:
            destination = rebase_body_destination(
                reference.resource, new_source=new_source, moved_targets=moved_targets
            )
            if destination != reference.link.destination:
                edits[reference.link.destination_span] = destination
        rendered = rewrite_link_destinations(file.text, edits) if edits else file.text
        if path in unmanaged_moves:
            _assert_link_extractable(new_source, rendered)
            rendered_bytes = rendered.encode("utf-8")
            mutations.append(
                FileMutation(
                    new_source,
                    MutationKind.WRITE,
                    None,
                    sha256_bytes(rendered_bytes),
                    rendered_bytes,
                )
            )
            mutations.append(
                FileMutation(
                    path,
                    MutationKind.DELETE,
                    sha256_bytes(file.text.encode("utf-8")),
                    None,
                    None,
                )
            )
            effects.append(PlanEffect(action=PlanAction.MOVE, from_path=path, to_path=new_source))
        elif rendered != file.text:
            rendered_bytes = rendered.encode("utf-8")
            mutations.append(
                FileMutation(
                    path,
                    MutationKind.WRITE,
                    sha256_bytes(file.text.encode("utf-8")),
                    sha256_bytes(rendered_bytes),
                    rendered_bytes,
                )
            )
            effects.append(PlanEffect(action=PlanAction.MODIFY, path=path))
    return mutations, effects
```

- [ ] **Step 4: 运行确认绿**

Run: `uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit/test_move_planning.py" -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add plugins/draft/skills/draft/scripts/runtime/src/draft/structure.py \
        plugins/draft/skills/draft/scripts/runtime/tests/unit/test_move_planning.py
git commit -m "feat(draft): 未受管理文件的链接渲染与移动集身份

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: `plan_move_request`——编排、受管理目录来源与委托

**Files:**
- Modify: `src/draft/structure.py`（`_render_pages` 签名、`plan_path_remap` 签名、新增 `plan_move_request` 与碰撞校验）
- Test: `tests/unit/test_move_planning.py`

**Interfaces:**
- Consumes: Task 4 的 `MoveRequest`/`SourceKind`/`TargetKind`/`validate_results`；Task 5 的 `MoveClosure`；Task 6 的两个单元；既有 `_expand_mapping`、`_final_parents`、`_collapse_empty_hubs`、`_render_pages`、`plan_path_remap`、`plan_move`。
- Produces: `plan_move_request(graph, closure, request, *, marker, observations) -> OperationPlan`。Task 8/9 消费。

关键实现约束（违反即错）：

1. **委托分支**：文件目标 + 单个受管理文件来源 → 直接 `plan_move(...)`，行为与现状逐位一致。
2. **受管理目录来源不经过 `_expand_mapping`**：其成员已按物理完整枚举，枢纽与后代同时在显式映射中会触发 `_expand_mapping` 的 `structure.conflict`。受管理目录成员逐成员显式 `parent_changes[member] = physical_parent(result)`（物理语义，规格 §2.3）。
3. **受管理文件来源（混合批中）经过 `_expand_mapping`**：保留枢纽子树随行语义。
4. **`plan_path_remap` 增加两个可选参数**：
   - `extra_moved_targets: Mapping[ResourceIdentity, ResourceIdentity] = ()`——并入
     `_render_pages` 内现有 `moved_targets` 字典；
   - `pre_expanded: Mapping[Path, Path] | None = None`——非 None 时**跳过**
     内部的 `_expand_mapping`，直接以它为 `expanded`（原因：`plan_path_remap`
     现行实现的第一步就是对 `page_map` 调 `_expand_mapping`（structure.py:260），
     物理枚举的受管理目录成员不能走 BFS 展开）。
   `_render_pages` 同步增加 `extra_moved_targets` 参数。其余调用点
   （promote/collapse）不传新参数，行为不变。
5. 全局终路径碰撞：`expanded` ∪ 未受管理结果的值集合去重校验 → `structure.duplicate_basename`；`expanded` 值与未受管理结果落在既有受管页上 → `structure.target_exists`（`_expand_mapping` 已覆盖自身部分，补查交叉部分）。

- [ ] **Step 1: 写失败测试（`_render_pages` 扩展）**

`test_move_planning.py` 追加。用 `test_structure.py` 的 `_graph_from_texts` 构造：受管理页 `pages/a.md` 正文链接指向根级未受管理路径 `00-bg.md`（构造图时该目标按 `resolve_local_destination` 解析——照 `_graph_from_texts` 现有做法，链接事实会进入 `body_resources`）：

```python
def test_render_pages_rewrites_managed_links_to_unmanaged_moved_targets() -> None:
    page_a = (
        "# 页面 A\n\n> 状态：`已确认：说明`\n>\n"
        "> 父主题：[根索引](../index.md)\n\n"
        "参考 [旧稿](../00-bg.md)。\n"
    )
    graph = _graph_from_texts({Path("index.md"): _INDEX, Path("pages/a.md"): page_a})

    rendered = _render_pages(
        graph,
        {},
        {},
        extra_moved_targets={
            ResourceIdentity(ResourceKind.LOCAL, "00-bg.md"): ResourceIdentity(
                ResourceKind.PAGE, "pages/00-bg.md"
            )
        },
    )

    text = rendered[Path("pages/a.md")]
    assert "[旧稿](00-bg.md)" in text      # 按 pages/a.md 新基址重算
    assert "../00-bg.md" not in text
```

（`_graph_from_texts`、`_INDEX` 取自 `test_structure.py` 复制到本文件的同名辅助；`_render_pages`、`ResourceIdentity`、`ResourceKind` 分别从 `draft.structure`、`draft.resources` 导入。本测试直接调用 `_render_pages` 并传 `expanded={}`，断言链接改写仅由 `extra_moved_targets` 触发——这正是规格 §4.1"统一入站闭包"消除既有缺口的行为。）

- [ ] **Step 2: 运行确认红并实现 `_render_pages`/`plan_path_remap` 参数扩展**

Run: `uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit/test_move_planning.py" -q -k extra_moved`
Expected: FAIL（参数不存在）

实现：`_render_pages` 签名追加 `extra_moved_targets: Mapping[ResourceIdentity, ResourceIdentity] = ()`，其内构造 `moved_targets` 处（现 structure.py:164-169）在字典推导后执行 `moved_targets.update(extra_moved_targets)`；`plan_path_remap` 签名追加 `extra_moved_targets: Mapping[ResourceIdentity, ResourceIdentity] = ()` 与 `pre_expanded: Mapping[Path, Path] | None = None`，函数体首行改为：

```python
    expanded = dict(pre_expanded) if pre_expanded is not None else _expand_mapping(graph, page_map)
```

并把 `extra_moved_targets` 透传给 `_render_pages`。

复跑确认绿；再跑 `pytest "$RUNTIME/tests/unit/test_structure.py" -q` 确认既有 promote/collapse/move 用例不受签名扩展影响。

- [ ] **Step 3: 写 `plan_move_request` 失败测试**

`test_move_planning.py` 追加分支用例（全部用合成图 + 合成 `MoveClosure`）：

1. **委托**：文件目标 + 单受管理文件来源 → 断言返回计划与直接调用 `plan_move` 的结果逐字段相等（mutations/effects/remove_empty_directories）。
2. **未受管理文件 + 前缀目标**：单个 `stray.md` → 断言与 Task 6 相同的 WRITE+DELETE 形态，且 `kind=OperationKind.MOVE`、`root_state` 取自 `marker.state.root_state`、`observations` 原样传入。
3. **受管理目录来源**：`pages/topic`（含 `index.md` + `a.md`，父声明指向 `pages/topic/index.md`）前缀目标 `pages/archive`（图含 `pages/archive/index.md`）→ 断言两成员映射为 `pages/archive/topic/index.md`、`pages/archive/topic/a.md`，且渲染后 `a.md` 与 `index.md` 的父声明/导航指向新物理父页（断言渲染字节含新相对目标）。
4. **混合批碰撞**：受管理文件来源的 BFS 展开目标与未受管理结果相同 → `structure.duplicate_basename`。
5. **结果校验穿透**：非法前缀（`notes`）→ `structure.invalid_target`（`validate_results` 已被调用）。

- [ ] **Step 4: 运行确认红**

Run: `uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit/test_move_planning.py" -q -k plan_move_request`
Expected: FAIL（`ImportError`）

- [ ] **Step 5: 实现 `plan_move_request`**

```python
def plan_move_request(
    graph: DraftGraph,
    closure: MoveClosure,
    request: MoveRequest,
    *,
    marker: MarkerSnapshot,
    observations: ObservationManifest,
) -> OperationPlan:
    if (
        request.target.kind is TargetKind.FILE
        and len(request.sources) == 1
        and request.sources[0].kind is SourceKind.MANAGED_PAGE
    ):
        source = request.sources[0].path
        return plan_move(
            graph, source, request.target.path, marker=marker, observations=observations
        )

    directory_sources = [s.path for s in request.sources if s.kind is SourceKind.DIRECTORY]
    validate_results(graph, request.results, directory_sources)

    managed_pairs: dict[Path, Path] = {}
    physical_pairs: dict[Path, Path] = {}
    unmanaged_moves: dict[Path, Path] = {}
    for source in request.sources:
        for member in source.members:
            result = request.results[member]
            if member in graph.pages:
                if source.kind is SourceKind.DIRECTORY:
                    physical_pairs[member] = result
                else:
                    managed_pairs[member] = result
            else:
                unmanaged_moves[member] = result

    expanded = _expand_mapping(graph, managed_pairs) if managed_pairs else {}
    expanded = {**expanded, **physical_pairs}
    _validate_cross_collisions(graph, expanded, unmanaged_moves)

    parent_changes: dict[Path, Path] = {}
    for member, result in physical_pairs.items():
        parent = physical_parent(result)
        if parent is not None and graph.parents.get(member) != parent:
            parent_changes[member] = parent
    for member, result in managed_pairs.items():
        parent = physical_parent(result)
        if parent is not None and parent != graph.parents.get(member):
            parent_changes[member] = parent

    moved_targets = {
        ResourceIdentity(ResourceKind.PAGE, old.as_posix()): ResourceIdentity(
            ResourceKind.PAGE, new.as_posix()
        )
        for old, new in expanded.items()
    }
    moved_targets.update(_unmanaged_moved_identities(graph.root, unmanaged_moves))

    if expanded or parent_changes:
        managed_plan = plan_path_remap(
            graph,
            {**managed_pairs, **physical_pairs},
            parent_changes,
            kind=OperationKind.MOVE,
            marker=marker,
            observations=observations,
            pre_expanded=expanded,
            extra_moved_targets=_unmanaged_moved_identities(graph.root, unmanaged_moves),
        )
        base_mutations = list(managed_plan.mutations)
        base_effects = list(managed_plan.effects)
        removable = managed_plan.remove_empty_directories
    else:
        base_mutations, base_effects, removable = [], [], ()

    unmanaged_mutations, unmanaged_effects = _plan_unmanaged(
        graph.root, closure, unmanaged_moves, moved_targets
    )
    writes = [m for m in [*base_mutations, *unmanaged_mutations] if m.kind is MutationKind.WRITE]
    deletes = [m for m in [*base_mutations, *unmanaged_mutations] if m.kind is MutationKind.DELETE]
    writes.sort(key=lambda m: m.path.as_posix())
    deletes.sort(key=lambda m: m.path.as_posix())
    return OperationPlan(
        kind=OperationKind.MOVE,
        root=graph.root,
        mutations=tuple([*writes, *deletes]),
        root_state=marker.state.root_state,
        observations=observations,
        remove_empty_directories=removable,
        effects=tuple([*base_effects, *unmanaged_effects]),
    )


def _validate_cross_collisions(
    graph: DraftGraph,
    expanded: Mapping[Path, Path],
    unmanaged_moves: Mapping[Path, Path],
) -> None:
    all_targets = [*expanded.values(), *unmanaged_moves.values()]
    if len(set(all_targets)) != len(all_targets):
        raise DraftError("structure.duplicate_basename")
    for target in unmanaged_moves.values():
        if target in graph.pages and target not in expanded:
            raise DraftError("structure.target_exists", target)
    for target in expanded.values():
        if target in unmanaged_moves.values():
            raise DraftError("structure.target_exists", target)
```

- [ ] **Step 6: 运行确认绿**

Run: `uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit/test_move_planning.py" "$RUNTIME/tests/unit/test_structure.py" -q`
Expected: PASS（既有 structure 用例零回归）

- [ ] **Step 7: 提交**

```bash
git add plugins/draft/skills/draft/scripts/runtime/src/draft/structure.py \
        plugins/draft/skills/draft/scripts/runtime/tests/unit/test_move_planning.py
git commit -m "feat(draft): move 双类型批量的统一计划编排

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: `move_entries` 执行入口与统一闭包拒绝

**Files:**
- Modify: `src/draft/structure.py`（拆 `_load_structure_facts`、新增 `plan_move_from_arguments`/`move_entries`）
- Test: `tests/integration/test_move_promotion.py`（新建）

**Interfaces:**
- Consumes: Task 4 `classify_move_request`、Task 5 `scan_move_closure`/`merged_blocking`、Task 7 `plan_move_request`、`load_operation_facts`、`read_lock`、`execute_plan`。
- Produces:
  - `_load_structure_facts(root, *, allow_empty_hub=None) -> OperationFacts`（`_load_clean_graph` 去掉 dirty 抛出的内层；`_load_clean_graph` 变为它加 `structure.dirty` 检查——promote/collapse 行为逐位不变）；
  - `plan_move_from_arguments(root, facts, source_values, target_value) -> OperationPlan`（分类 → 闭包扫描 → 合并阻碍 → 计划）；
  - `move_entries(root, source_values, target_value) -> OperationResult`。

- [ ] **Step 1: 写端到端失败测试（单文件提升）**

新建 `tests/integration/test_move_promotion.py`：

```python
from __future__ import annotations

from pathlib import Path

import pytest

from draft.checker import check_draft
from draft.errors import DraftError
from draft.models import RootState
from draft.structure import move_entries

PAGE_INDEX = """# 根

> 状态：`已确认：根索引`

正文。
"""


def _page(title: str, body: str = "正文。") -> str:
    return f"# {title}\n\n> 状态：`已确认：说明`\n\n{body}\n"


def test_move_promotes_unmanaged_file_and_rewrites_all_linkers(
    draft_factory,
) -> None:
    root = draft_factory(
        {
            "index.md": PAGE_INDEX,
            "pages/a.md": _page("页面 A", "参考 [旧稿](../00-background.md)。"),
        },
        root_state=RootState.CANDIDATE,
    )
    (root / "00-background.md").write_text(
        "旧稿 [站内](pages/a.md) 与 [本地](sibling.md)。\n", encoding="utf-8"
    )
    (root / "sibling.md").write_text("邻居 [旧稿](00-background.md)。\n", encoding="utf-8")

    move_entries(root, ["00-background.md"], "pages/00-background.md")

    assert not (root / "00-background.md").exists()
    promoted = (root / "pages/00-background.md").read_text(encoding="utf-8")
    assert "[站内](a.md)" in promoted          # 出站：新基址下 pages 内相对
    assert "[本地](../sibling.md)" in promoted  # 出站：指向留下的未受管理文件
    page_a = (root / "pages/a.md").read_text(encoding="utf-8")
    assert "[旧稿](00-background.md)" in page_a  # 受管理链接者
    sibling = (root / "sibling.md").read_text(encoding="utf-8")
    assert "[旧稿](pages/00-background.md)" in sibling  # 未受管理链接者
```

（`draft_factory` 的 `root_state` 参数类型以 `integration/conftest.py:10-48` 的实际签名为准——`RootState` 枚举。）

- [ ] **Step 2: 运行确认红**

Run: `uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/integration/test_move_promotion.py" -q -m 'not network'`
Expected: FAIL（`ImportError: cannot import name 'move_entries'`）

- [ ] **Step 3: 实现入口三件套**

`structure.py`：

```python
def _load_structure_facts(root: Path, *, allow_empty_hub: Path | None = None) -> OperationFacts:
    if read_lock(root) is not None:
        raise DraftError(CheckCode.OPERATION_RECOVERY_REQUIRED.value, root)
    return load_operation_facts(root, allow_hub_empty=allow_empty_hub)


def _load_clean_graph(root: Path, *, allow_empty_hub: Path | None = None) -> OperationFacts:
    """（现行语义保持：事实加载 + blocking 即拒绝。）"""
    facts = _load_structure_facts(root, allow_empty_hub=allow_empty_hub)
    if facts.blocking_paths:
        details = tuple(path.as_posix() for path in facts.blocking_paths)
        raise DraftError("structure.dirty", root, details=details)
    return facts


def plan_move_from_arguments(
    root: Path,
    facts: OperationFacts,
    source_values: Sequence[str],
    target_value: str,
) -> OperationPlan:
    request = classify_move_request(root, facts.graph, source_values, target_value)
    closure = scan_move_closure(root, facts.graph)
    blocking = merged_blocking(facts.blocking_paths, closure)
    if blocking:
        raise DraftError(
            "structure.dirty", root, details=tuple(p.as_posix() for p in blocking)
        )
    return plan_move_request(
        facts.graph, closure, request, marker=facts.marker, observations=facts.observations
    )


def move_entries(
    root: Path, source_values: Sequence[str], target_value: str
) -> OperationResult:
    resolved = resolve_draft_root(root)
    facts = _load_structure_facts(resolved)
    plan = plan_move_from_arguments(resolved, facts, source_values, target_value)
    return execute_plan(plan)
```

- [ ] **Step 4: 运行确认绿（含既有 structure 集成零回归）**

Run: `uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/integration/test_move_promotion.py" "$RUNTIME/tests/integration/test_structure.py" -q -m 'not network'`
Expected: PASS

- [ ] **Step 5: 追加阻碍与恢复用例（同一集成文件）**

```python
def test_move_rejects_and_names_undecodable_unmanaged_file(draft_factory) -> None:
    root = draft_factory({"index.md": PAGE_INDEX}, root_state=RootState.CANDIDATE)
    (root / "stray.md").write_text("正文 [链接](pages/a.md)。\n", encoding="utf-8")
    (root / "gbk-legacy.md").write_bytes("旧稿。".encode("gb18030"))

    with pytest.raises(DraftError) as caught:
        move_entries(root, ["stray.md"], "pages/stray.md")

    assert caught.value.code == "structure.dirty"
    assert "gbk-legacy.md" in caught.value.details
    assert (root / "stray.md").exists()          # 拒绝后零写入
    assert not (root / "pages/stray.md").exists()


def test_move_blocking_merges_managed_and_unmanaged(draft_factory) -> None:
    root = draft_factory(
        {
            "index.md": PAGE_INDEX,
            "pages/broken.md": "没有标题与机器块的受管理页。\n",
        },
        root_state=RootState.CANDIDATE,
    )
    (root / "stray.md").write_text("正文。\n", encoding="utf-8")
    (root / "gbk-legacy.md").write_bytes("旧稿。".encode("gb18030"))

    with pytest.raises(DraftError) as caught:
        move_entries(root, ["stray.md"], "pages/stray.md")

    assert caught.value.code == "structure.dirty"
    assert sorted(caught.value.details) == ["gbk-legacy.md", "pages/broken.md"]


def test_move_promotion_recovers_after_interrupt(draft_factory, monkeypatch) -> None:
    root = draft_factory({"index.md": PAGE_INDEX}, root_state=RootState.CANDIDATE)
    (root / "stray.md").write_text("正文 [站内](index.md)。\n", encoding="utf-8")
    original_bytes = (root / "stray.md").read_bytes()

    from draft import operations

    real_apply_delete = operations._apply_delete

    def _interrupted_apply_delete(*args, **kwargs):
        raise OSError("注入中断")

    monkeypatch.setattr(operations, "_apply_delete", _interrupted_apply_delete)

    with pytest.raises(BaseException):
        move_entries(root, ["stray.md"], "pages/stray.md")

    monkeypatch.setattr(operations, "_apply_delete", real_apply_delete)
    operations.recover(root)

    # 中断发生在 WRITE 已应用、DELETE 未应用时：恢复回滚到移动前。
    assert (root / "stray.md").read_bytes() == original_bytes
    assert not (root / "pages/stray.md").exists()
    assert not (root / ".draft" / "operation.lock").exists()
```

（中断注入点 `_apply_delete` 与恢复入口 `operations.recover` 均已在
`operations.py` 中核实；若实施时 apply 阶段函数名有变，按
`integration/test_operations.py` 既有的中断注入模式等价替换，断言不变。）

- [ ] **Step 6: 运行确认绿**

Run: `uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/integration/test_move_promotion.py" -q -m 'not network'`
Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add plugins/draft/skills/draft/scripts/runtime/src/draft/structure.py \
        plugins/draft/skills/draft/scripts/runtime/tests/integration/test_move_promotion.py
git commit -m "feat(draft): move 提升入口与统一闭包拒绝

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 9: CLI `SOURCE... TARGET` 形态与分派

**Files:**
- Modify: `src/draft/cli.py`（move 解析器与分派分支）
- Modify: `tests/unit/test_cli.py`（既有 move 分派断言改为新调用形态）
- Test: `tests/integration/test_cli.py`（追加）

**Interfaces:**
- Consumes: `move_entries`、`plan_move_from_arguments`（Task 8）。
- Produces: `draft move SOURCE... TARGET [--dry-run] [--json]`；参数形态违例退出码 2（`cli.move_operands_invalid`，域错误）；帮助文本写明"只移动 `.md`，其余原地留下"（规格 §2.3）。

已知必然回归：`tests/unit/test_cli.py` 中 move 分支的用例以替身断言
`move_page` 的调用参数（`assert_called_once_with`），本任务把分派改为
`move_entries`/`plan_move_from_arguments` 后这些断言必须同步更新为
新形态——这是命令面变更的正当代价，不是放宽门禁。

- [ ] **Step 1: 写失败测试**

`integration/test_cli.py` 追加。该文件已有 `run_json(args, capsys) -> (code, payload)` 辅助（`--json` 输出解析）与 `draft_factory` 可用的 conftest；`PAGE_INDEX` 常量在本文件内定义（含机器块的合法根索引文本，与其他集成文件同形）：

```python
def test_move_shape_requires_source_and_target(
    draft_factory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = draft_factory({"index.md": PAGE_INDEX}, root_state=RootState.CANDIDATE)

    code, payload = run_json(
        ["move", "pages/a.md", "--root", str(root), "--json"], capsys
    )

    assert code == 2
    assert payload["error"]["code"] == "cli.move_operands_invalid"


def test_move_file_target_rejects_multiple_sources(
    draft_factory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = draft_factory({"index.md": PAGE_INDEX}, root_state=RootState.CANDIDATE)
    (root / "a.md").write_text("甲。\n", encoding="utf-8")
    (root / "b.md").write_text("乙。\n", encoding="utf-8")

    code, payload = run_json(
        ["move", "a.md", "b.md", "pages/c.md", "--root", str(root), "--json"], capsys
    )

    assert code == 2
    assert payload["error"]["code"] == "cli.move_operands_invalid"


def test_move_prefix_target_promotes_unmanaged_file(
    draft_factory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = draft_factory({"index.md": PAGE_INDEX}, root_state=RootState.CANDIDATE)
    (root / "stray.md").write_text("正文。\n", encoding="utf-8")

    code, payload = run_json(
        ["move", "stray.md", "pages", "--root", str(root), "--json"], capsys
    )

    assert code == 0
    assert payload["result_type"] == "move"
    assert (root / "pages/stray.md").is_file()
    assert not (root / "stray.md").exists()


def test_move_dry_run_reports_effects_without_writing(
    draft_factory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = draft_factory({"index.md": PAGE_INDEX}, root_state=RootState.CANDIDATE)
    (root / "stray.md").write_text("正文。\n", encoding="utf-8")

    code, _payload = run_json(
        ["move", "stray.md", "pages", "--root", str(root), "--dry-run", "--json"],
        capsys,
    )

    assert code == 0
    assert (root / "stray.md").is_file()            # 预演零写入
    assert not (root / "pages/stray.md").exists()

    code, _payload = run_json(
        ["move", "stray.md", "pages", "--root", str(root), "--json"], capsys
    )
    assert code == 0
    assert (root / "pages/stray.md").is_file()      # 正式执行后就位
```

- [ ] **Step 2: 运行确认红**

Run: `uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/integration/test_cli.py" -q -m 'not network' -k "move_shape or move_file_target_rejects or move_prefix or move_dry_run"`
Expected: FAIL

- [ ] **Step 3: 实现**

`cli.py` 的 `build_parser()` 中 move 解析器整体替换为：

```python
    move = subparsers.add_parser(
        "move",
        parents=[rooted],
        help="移动页面或未受管理文件",
        description=(
            "把一个或多个 SOURCE 移动到 TARGET，并重算全部可证明的相关链接。"
            "TARGET 以 .md 结尾时是文件目标，只允许单个文件 SOURCE；"
            "否则是前缀目标，来源按基名或目录名落入其下。"
            "目录 SOURCE 只移动其中的 .md 普通文件，其余内容原地留下。"
        ),
    )
    move.add_argument(
        "operands",
        metavar="OPERAND",
        nargs="+",
        help="SOURCE... TARGET（最后一个操作数是 TARGET）",
    )
    _add_dry_run_args(move)
```

`_dispatch` 的 move 分支整体替换为：

```python
    if command == "move":
        from draft.structure import move_entries, plan_move_from_arguments

        operands = [cast(str, value) for value in args.operands]
        if len(operands) < 2:
            raise DraftError(
                "cli.move_operands_invalid", details=("至少需要一个 SOURCE 和一个 TARGET",)
            )
        sources, target = operands[:-1], operands[-1]
        if PurePosixPath(target).suffix == ".md" and len(sources) > 1:
            raise DraftError(
                "cli.move_operands_invalid", details=("文件目标只允许一个文件 SOURCE",)
            )
        if _is_dry_run(args):
            return _dry_run_write(
                args,
                root=root,
                operation=DryRunOperation.MOVE,
                build=lambda facts: plan_move_from_arguments(root, facts, sources, target),
            )
        _reject_pagination_without_dry_run(args)
        result = move_entries(root, sources, target)
        payload: dict[str, object] = {"result_type": ResultType.MOVE.value}
        payload.update(_write_payload(result))
        if len(sources) == 1:
            return payload, f"已移动 {sources[0]} → {target}", 0
        return payload, f"已移动 {len(sources)} 个来源 → {target}", 0
```

（`PurePosixPath` 加入文件顶部既有的 `pathlib` 导入。`_page_operand` 不再被 move 分支使用，但保留——其他命令仍用。）

- [ ] **Step 4: 更新单元测试的分派断言**

`tests/unit/test_cli.py` 中 move 用例：替身对象从 `move_page`/`plan_move`
改为 `move_entries`/`plan_move_from_arguments`（按该文件既有的
`monkeypatch`/`Mock` 手法），断言形如
`move_entries.assert_called_once_with(root, ["pages/a.md"], "pages/b.md")`
（root 为解析后的绝对根）。其余命令用例不动。

- [ ] **Step 5: 运行确认绿（含既有 CLI 全量）**

Run: `uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/integration/test_cli.py" "$RUNTIME/tests/unit/test_cli.py" -q -m 'not network'`
Expected: PASS（既有单对 `move SOURCE TARGET` 行为经委托分支逐位不变）

- [ ] **Step 6: 提交**

```bash
git add plugins/draft/skills/draft/scripts/runtime/src/draft/cli.py \
        plugins/draft/skills/draft/scripts/runtime/tests/unit/test_cli.py \
        plugins/draft/skills/draft/scripts/runtime/tests/integration/test_cli.py
git commit -m "feat(draft): move 命令面改为 SOURCE... TARGET 双类型形态

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 10: 端到端迁移场景集成套件

**Files:**
- Test: `tests/integration/test_move_promotion.py`（扩充）

覆盖规格 §11.2 中 Task 8 未覆盖的剩余场景。全部用真实文件系统。

- [ ] **Step 1: 树形提升用例**

```python
def test_move_directory_promotes_tree_and_leaves_non_markdown(draft_factory) -> None:
    root = draft_factory({"index.md": PAGE_INDEX}, root_state=RootState.CANDIDATE)
    legacy = root / "legacy-notes"
    (legacy / "topic").mkdir(parents=True)
    (legacy / "topic" / "index.md").write_text("枢纽正文 [图](img.png)。\n", encoding="utf-8")
    (legacy / "topic" / "a.md").write_text("子页 [枢纽](index.md)。\n", encoding="utf-8")
    (legacy / "topic" / "img.png").write_bytes(b"\x89PNG")

    move_entries(root, ["legacy-notes"], "pages")

    moved_hub = root / "pages/legacy-notes/topic/index.md"
    assert moved_hub.is_file()
    assert "[图](img.png)" in moved_hub.read_text(encoding="utf-8")  # 图片原地留下，相对形式不变
    assert (root / "pages/legacy-notes/topic/a.md").is_file()
    assert "[枢纽](index.md)" in (root / "pages/legacy-notes/topic/a.md").read_text(encoding="utf-8")
    assert (legacy / "topic" / "img.png").is_file()  # 非 .md 原地留下
    assert not (legacy / "topic" / "index.md").exists()
```

- [ ] **Step 2: 提升后 check 诊断形态用例**

```python
def test_promoted_file_reports_protocol_debts_and_not_unmanaged_path(draft_factory) -> None:
    root = draft_factory(
        {"index.md": PAGE_INDEX, "pages/a.md": _page("页面 A")},
        root_state=RootState.CANDIDATE,
    )
    (root / "legacy.md").write_text("只有正文，没有任何协议结构。\n", encoding="utf-8")
    (root / "other-stray.md").write_text("另一篇未受管理文件。\n", encoding="utf-8")

    move_entries(root, ["legacy.md"], "pages/legacy.md")

    report = check_draft(root)
    legacy_codes = {i.code for i in report.errors if i.path == Path("pages/legacy.md")}
    assert legacy_codes                       # 协议欠账被报告（如 page.missing_title）
    assert "page.unmanaged_path" not in legacy_codes
    assert any(
        i.path == Path("other-stray.md") and i.code == "page.unmanaged_path"
        for i in report.errors
    )
```

- [ ] **Step 3: registered 门禁次序用例**

```python
def test_registered_root_rejects_at_health_gate_before_classification(draft_factory) -> None:
    root = draft_factory({"index.md": PAGE_INDEX}, root_state=RootState.REGISTERED)
    (root / "stray.md").write_text("正文。\n", encoding="utf-8")

    with pytest.raises(DraftError) as caught:
        move_entries(root, ["stray.md"], "pages/stray.md")

    # 健康门禁先于操作数分类：错误码是检查错误（page.unmanaged_path），
    # 不是 structure.unknown_page/path.internal。
    assert caught.value.code == "page.unmanaged_path"
```

- [ ] **Step 4: 受管理目录来源用例（物理语义）**

```python
def test_move_managed_directory_reparents_physically(draft_factory) -> None:
    hub_topic = (
        "# 主题枢纽\n\n> 状态：`已确认：枢纽`\n>\n"
        "> 父主题：[根索引](../../index.md)\n\n正文。\n"
    )
    page_a = (
        "# 页面 A\n\n> 状态：`已确认：说明`\n>\n"
        "> 父主题：[主题枢纽](index.md)\n\n正文。\n"
    )
    hub_archive = (
        "# 归档枢纽\n\n> 状态：`已确认：枢纽`\n>\n"
        "> 父主题：[根索引](../index.md)\n\n正文。\n"
    )
    root = draft_factory(
        {
            "index.md": PAGE_INDEX,
            "pages/topic/index.md": hub_topic,
            "pages/topic/a.md": page_a,
            "pages/archive/index.md": hub_archive,
        },
        root_state=RootState.CANDIDATE,
    )

    move_entries(root, ["pages/topic"], "pages/archive")

    moved_hub = (root / "pages/archive/topic/index.md").read_text(encoding="utf-8")
    # 枢纽的父声明目标重写到新物理父页 pages/archive/index.md：
    # 原 ../../index.md（指向根）→ 新 ../index.md（指向 archive 枢纽）
    assert "> 父主题：[根索引](../index.md)" in moved_hub
    assert (root / "pages/archive/topic/a.md").is_file()
    assert not list((root / "pages/topic").rglob("*.md"))
    assert check_draft(root).errors == ()
```

- [ ] **Step 5: 运行确认绿**

Run: `uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/integration/test_move_promotion.py" -q -m 'not network'`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add plugins/draft/skills/draft/scripts/runtime/tests/integration/test_move_promotion.py
git commit -m "test(draft): move 提升的端到端迁移场景

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 11: 文档同步与全部门禁

**Files:**
- Modify: `src/draft/cli.py`（如 Task 9 帮助文本有遗漏措辞）
- Modify: `plugins/draft/skills/draft/SKILL.md`、`plugins/draft/skills/draft/references/migration.md`

- [ ] **Step 1: 迁移参考文档**

`references/migration.md` 增加（章节位置按现有结构插入）：

1. **编码归一化**：建立候选页面前把非 UTF-8 旧稿转为 UTF-8；现成手段示例 `iconv -f GB18030 -t UTF-8`；CLI 不检测、不转换，未转码文件会以 `structure.dirty` 点名阻碍。
2. **树形迁移示例**：`cp -r` 进根 → `draft move legacy-notes pages` → `draft check` 按类修复 → `draft adopt`。
3. **通配注意**：根部 `*.md` 展开会包含 `index.md` 并命中 `structure.protected`，命令点名拒绝整批；排除后重试（显式枚举或 shell extglob）。
4. **平台展开**：bash/PowerShell 原生展开通配；不展开的环境（如 cmd.exe）收到字面模式的 `structure.unknown_page`，改用显式枚举。

- [ ] **Step 2: SKILL.md 的 move 说明**

把 move 的命令说明更新为双类型形态：文件/前缀目标语义、目录来源"只移动 `.md`、其余原地留下"、未受管理来源提升后由 `check` 报告协议欠账。措辞与 cli.py 帮助文本一致（同一事实两个出处，保持逐句同义）。

- [ ] **Step 3: 全部门禁独立复跑**

按 Global Constraints 的七条命令逐条运行；网络集成测试如不运行，在汇报中如实记录被排除的数量。

Run: 七条门禁命令
Expected: 全绿

- [ ] **Step 4: 提交**

```bash
git add plugins/draft/skills/draft/SKILL.md \
        plugins/draft/skills/draft/references/migration.md
git commit -m "docs(draft): 迁移参考与技能文档同步 move 双类型形态

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

- [ ] **Step 5: 汇报**

汇报内容必须包含：各任务提交清单与门禁结果；规格未决事项 §12 的四条在本次实施中的状态；实施过程中发现的计划与代码事实冲突（如有）及处理方式。
