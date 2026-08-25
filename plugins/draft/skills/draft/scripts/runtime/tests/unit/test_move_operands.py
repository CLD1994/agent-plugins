from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath

import pytest

from draft.diagnostic_codes import CheckCode
from draft.errors import DraftError
from draft.graph import DraftGraph, resolve_local_destination
from draft.markdown import parse_page
from draft.models import MarkdownLink, PageDocument, SourceSpan
from draft.move_operands import (
    MOVE_OPERANDS_INVALID,
    MoveRequest,
    MoveSource,
    MoveTarget,
    SourceKind,
    TargetKind,
    classify_move_request,
    is_internal_territory,
    map_sources,
    validate_results,
)
from draft.observations import EntryKind
from draft.paths import ResolvedPath
from draft.resources import resolve_page_resources
from draft.vocabulary import RelationCode


def test_path_internal_is_registered_with_rendering() -> None:
    from draft.diagnostic_codes import CheckCode
    from draft.diagnostics import KNOWN_CHECK_CODES

    assert CheckCode.PATH_INTERNAL.value in KNOWN_CHECK_CODES


# ---------------------------------------------------------------------------
# 局部内存图构造器：从 Markdown 文本构造一致的 DraftGraph，不访问文件系统。
# 与 test_structure.py 同一实现；两份测试各自持有该辅助函数是既有测试树的做法。
# ---------------------------------------------------------------------------


ROOT = Path("/draft")


def _graph_from_texts(
    texts: Mapping[Path, str],
    *,
    root: Path = ROOT,
) -> DraftGraph:
    """从 Markdown 文本构造一致的内存 DraftGraph。

    镜像 graph._load 的派生逻辑（解析页面、解析链接、派生父子与关系），
    但不访问文件系统：页面文本即为唯一来源。
    """
    pages: dict[Path, PageDocument] = {}
    for path in sorted(texts):
        pages[path] = parse_page(path, texts[path])

    managed_set = set(pages)
    inbound: dict[Path, list[tuple[Path, MarkdownLink]]] = {}
    relation_edges: dict[RelationCode, list[tuple[Path, Path, int]]] = {
        label: [] for label in RelationCode
    }

    for source in sorted(pages):
        page = pages[source]
        resolved_links: dict[SourceSpan, Path | None] = {}
        for link in page.links:
            try:
                target, _anchor = resolve_local_destination(source, link.destination, root)
            except DraftError:
                resolved_links[link.whole_span] = None
                continue
            resolved_links[link.whole_span] = target
            if target in managed_set:
                inbound.setdefault(target, []).append((source, link))
        for relation in page.relations:
            target = resolved_links.get(relation.link.whole_span)
            if target is None or target not in managed_set:
                continue
            relation_edges[relation.code].append((source, target, relation.link.line))

    parents: dict[Path, Path] = {}
    for source in sorted(pages):
        declared = [r for r in pages[source].relations if r.code is RelationCode.PARENT]
        if len(declared) != 1:
            continue
        matches = [
            target for src, target, _line in relation_edges[RelationCode.PARENT] if src == source
        ]
        if len(matches) == 1:
            parents[source] = matches[0]

    children: dict[Path, list[Path]] = {}
    for child, parent in parents.items():
        children.setdefault(parent, []).append(child)

    body_resources = {path: resolve_page_resources(root, pages[path]) for path in sorted(pages)}
    return DraftGraph(
        root=root,
        pages={path: pages[path] for path in sorted(pages)},
        parents={path: parents[path] for path in sorted(parents)},
        children={parent: tuple(sorted(entries)) for parent, entries in sorted(children.items())},
        inbound_links={
            target: tuple(sorted(entries, key=lambda entry: (entry[0], entry[1].whole_span.start)))
            for target, entries in sorted(inbound.items())
        },
        relations={label: tuple(sorted(edges)) for label, edges in relation_edges.items()},
        body_resources=body_resources,
    )


# ---------------------------------------------------------------------------
# 页面文本：最小但合法的 Markdown，确保 parse_page 与 check_graph 一致。
# ---------------------------------------------------------------------------

_INDEX = """# 根

> 状态：`已关闭：纯导航页`

## 子主题

- [结论汇总](conclusions.md)
- [甲](pages/a.md)
"""

_PAGE_B = """# 乙

> 状态：`已确认：说明`
>
> 父主题：[根](../index.md)

乙的正文。
"""

_HUB = """# 主题枢纽

> 状态：`已确认：枢纽`
>
> 父主题：[根](../../index.md)

## 子主题

- [甲](a.md)
"""


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


def _request_for_validation(
    results: Mapping[Path, Path],
    *,
    directory_sources: Sequence[Path] = (),
    target: Path = Path("pages"),
) -> MoveRequest:
    """按结果键与目录来源构造 MoveRequest：目录来源携带其结果键成员，其余键作文件来源。

    validate_results 只依赖 results 与 DIRECTORY 来源的目录集，target 值不影响判定；
    members 以结果键表达，满足 MoveRequest 键不变量。
    """
    sources: list[MoveSource] = []
    claimed: set[Path] = set()
    for directory in directory_sources:
        members = tuple(
            path for path in results if path != directory and path.is_relative_to(directory)
        )
        sources.append(MoveSource(directory, SourceKind.DIRECTORY, members))
        claimed.update(members)
    for key in results:
        if key not in claimed:
            sources.append(MoveSource(key, SourceKind.UNMANAGED_FILE, (key,)))
    return MoveRequest(tuple(sources), MoveTarget(target, TargetKind.PREFIX), dict(results))


def test_validate_results_rejects_outside_namespace() -> None:
    graph = _graph_from_texts({Path("index.md"): _INDEX})

    with pytest.raises(DraftError) as caught:
        validate_results(graph, _request_for_validation({Path("s.md"): Path("notes/s.md")}))
    assert caught.value.code == "structure.invalid_target"


def test_validate_results_rejects_existing_page() -> None:
    graph = _graph_from_texts({Path("index.md"): _INDEX, Path("pages/b.md"): _PAGE_B})

    with pytest.raises(DraftError) as caught:
        validate_results(graph, _request_for_validation({Path("b.md"): Path("pages/b.md")}))
    assert caught.value.code == "structure.target_exists"


def test_validate_results_rejects_collision() -> None:
    graph = _graph_from_texts({Path("index.md"): _INDEX})

    with pytest.raises(DraftError) as caught:
        validate_results(
            graph,
            _request_for_validation(
                {Path("s1.md"): Path("pages/x.md"), Path("s2.md"): Path("pages/x.md")}
            ),
        )
    assert caught.value.code == "structure.conflict"
    assert caught.value.paths == (Path("s2.md"),)  # 点名第二个碰撞来源


def test_validate_results_rejects_result_inside_directory_source() -> None:
    graph = _graph_from_texts({Path("index.md"): _INDEX})

    # 目录源位于 pages/ 之下时，结果落在其内部才真正命中 descendant 分支
    # （结果在 pages/ 之外会先被命名空间检查以 invalid_target 拒绝）。
    with pytest.raises(DraftError) as caught:
        validate_results(
            graph,
            _request_for_validation(
                {Path("pages/topic/index.md"): Path("pages/topic/index.md")},
                directory_sources=(Path("pages/topic"),),
            ),
        )
    assert caught.value.code == "structure.descendant"


def test_validate_results_accepts_parent_occupied_by_root_index() -> None:
    graph = _graph_from_texts({Path("index.md"): _INDEX})

    # 父页 = 根 index.md（存在）→ 通过
    validate_results(graph, _request_for_validation({Path("s.md"): Path("pages/s.md")}))


def test_validate_results_accepts_parent_occupied_by_co_moved_file() -> None:
    graph = _graph_from_texts({Path("index.md"): _INDEX})

    # 父页 = pages/topic/index.md，由同批文件占据 → 通过
    validate_results(
        graph,
        _request_for_validation(
            {
                Path("t/index.md"): Path("pages/topic/index.md"),
                Path("t/a.md"): Path("pages/topic/a.md"),
            }
        ),
    )


def test_validate_results_rejects_missing_parent() -> None:
    graph = _graph_from_texts({Path("index.md"): _INDEX})

    # 父页 pages/topic/index.md 无人占据 → missing_parent
    with pytest.raises(DraftError) as caught:
        validate_results(graph, _request_for_validation({Path("t/a.md"): Path("pages/topic/a.md")}))
    assert caught.value.code == "structure.missing_parent"


def test_validate_results_rejects_moved_away_parent_without_replacement() -> None:
    graph_with_hub = _graph_from_texts(
        {Path("index.md"): _INDEX, Path("pages/topic/index.md"): _HUB}
    )
    # 父页是受管理页面但被同批移走且无文件落回原位 → missing_parent
    with pytest.raises(DraftError) as caught:
        validate_results(
            graph_with_hub,
            _request_for_validation(
                {
                    Path("pages/topic/index.md"): Path("pages/archive/topic/index.md"),
                    Path("s.md"): Path("pages/topic/s.md"),
                }
            ),
        )
    assert caught.value.code == "structure.missing_parent"


# ---------------------------------------------------------------------------
# classify_move_request 决策表（round-1 M4）：词法判定与文件系统探测经注入探针
# 解耦，顺序 1—8 在「单元测试不访问真实 FS」约束下逐行可单测。
# ---------------------------------------------------------------------------


class FakeMoveProbe:
    """受 MoveOperandProbe 签名约束的探测 fake：分类与枚举由测试显式提供。

    ``resolve_operand`` 按词法构造 ResolvedPath；需模拟符号链接拒绝时由测试显式
    把值加入 ``symlink_values``（生产路径中该拒绝发生在真实 resolve_move_operand）。
    """

    def __init__(self) -> None:
        self.kinds: dict[Path, EntryKind] = {}
        self.members: dict[Path, tuple[Path, ...]] = {}
        self.symlink_values: set[str] = set()

    def resolve_operand(self, root: Path, value: str) -> ResolvedPath:
        if value in self.symlink_values:
            raise DraftError(CheckCode.PATH_SYMLINK_ESCAPE.value, root / value)
        relative = Path(*PurePosixPath(value).parts)
        return ResolvedPath(root=root, relative=relative, absolute=root / relative)

    def node_kind(self, absolute: Path) -> EntryKind:
        return self.kinds.get(absolute, EntryKind.MISSING)

    def enumerate_directory(self, root: Path, relative: Path) -> tuple[Path, ...]:
        return self.members.get(relative, ())


def test_classify_move_request_order1_rejects_escape_lexically() -> None:
    graph = _graph_from_texts({Path("index.md"): _INDEX})

    with pytest.raises(DraftError) as caught:
        classify_move_request(ROOT, graph, ["../outside.md"], "pages/", probe=FakeMoveProbe())
    assert caught.value.code == "path.escape"


def test_classify_move_request_order2_rejects_internal_territory_lexically() -> None:
    graph = _graph_from_texts({Path("index.md"): _INDEX})

    with pytest.raises(DraftError) as caught:
        classify_move_request(ROOT, graph, [".draft/x.md"], "pages/", probe=FakeMoveProbe())
    assert caught.value.code == CheckCode.PATH_INTERNAL.value


def test_classify_move_request_order3_rejects_symlink_operand() -> None:
    graph = _graph_from_texts({Path("index.md"): _INDEX})
    probe = FakeMoveProbe()
    probe.symlink_values.add("link.md")

    with pytest.raises(DraftError) as caught:
        classify_move_request(ROOT, graph, ["link.md"], "pages/", probe=probe)
    assert caught.value.code == CheckCode.PATH_SYMLINK_ESCAPE.value


def test_classify_move_request_order4_directory_source_enumerates_members() -> None:
    graph = _graph_from_texts({Path("index.md"): _INDEX})
    probe = FakeMoveProbe()
    probe.kinds[ROOT / "legacy"] = EntryKind.DIRECTORY
    probe.members[Path("legacy")] = (Path("legacy/a.md"), Path("legacy/index.md"))

    request = classify_move_request(ROOT, graph, ["legacy"], "pages/", probe=probe)

    assert request.sources[0].kind is SourceKind.DIRECTORY
    assert request.sources[0].members == (Path("legacy/a.md"), Path("legacy/index.md"))


def test_classify_move_request_rejects_pages_directory_source() -> None:
    graph = _graph_from_texts({Path("index.md"): _INDEX})
    probe = FakeMoveProbe()
    probe.kinds[ROOT / "pages"] = EntryKind.DIRECTORY

    with pytest.raises(DraftError) as caught:
        classify_move_request(ROOT, graph, ["pages"], "pages/out/", probe=probe)
    assert caught.value.code == "structure.protected"
    assert caught.value.paths == (Path("pages"),)


def test_classify_move_request_order5_managed_file_source() -> None:
    graph = _graph_from_texts({Path("index.md"): _INDEX, Path("pages/a.md"): _PAGE_B})
    probe = FakeMoveProbe()
    probe.kinds[ROOT / "pages/a.md"] = EntryKind.FILE

    request = classify_move_request(ROOT, graph, ["pages/a.md"], "pages/out/", probe=probe)

    assert request.sources[0].kind is SourceKind.MANAGED_PAGE


def test_classify_move_request_root_index_and_conclusions_always_protected() -> None:
    """候选根（无 conclusions.md，not_generated）根级 conclusions.md 仍必须保护（H1 回归）。"""
    graph = _graph_from_texts({Path("index.md"): _INDEX})
    probe = FakeMoveProbe()
    probe.kinds[ROOT / "conclusions.md"] = EntryKind.FILE
    probe.kinds[ROOT / "index.md"] = EntryKind.FILE

    for name in ("conclusions.md", "index.md"):
        with pytest.raises(DraftError) as caught:
            classify_move_request(ROOT, graph, [name], "pages/x.md", probe=probe)
        assert caught.value.code == "structure.protected"
        assert caught.value.paths == (Path(name),)


def test_classify_move_request_order6_unmanaged_file_source() -> None:
    graph = _graph_from_texts({Path("index.md"): _INDEX})
    probe = FakeMoveProbe()
    probe.kinds[ROOT / "stray.md"] = EntryKind.FILE

    request = classify_move_request(ROOT, graph, ["stray.md"], "pages/", probe=probe)

    assert request.sources[0].kind is SourceKind.UNMANAGED_FILE


def test_classify_move_request_order7_rejects_non_markdown_file_source() -> None:
    graph = _graph_from_texts({Path("index.md"): _INDEX})
    probe = FakeMoveProbe()
    probe.kinds[ROOT / "img.png"] = EntryKind.FILE

    with pytest.raises(DraftError) as caught:
        classify_move_request(ROOT, graph, ["img.png"], "pages/", probe=probe)
    assert caught.value.code == "path.not_markdown"


def test_classify_move_request_order8_reports_unknown_page_for_missing() -> None:
    graph = _graph_from_texts({Path("index.md"): _INDEX})

    with pytest.raises(DraftError) as caught:
        classify_move_request(ROOT, graph, ["ghost.md"], "pages/", probe=FakeMoveProbe())
    assert caught.value.code == "structure.unknown_page"
    assert caught.value.paths == (Path("ghost.md"),)


def test_classify_move_request_rejects_empty_and_dot_source_operand() -> None:
    """round-1 L9：根本身作为来源显式以 structure.protected 拒绝，不做全根枚举。"""
    graph = _graph_from_texts({Path("index.md"): _INDEX})

    for value in ("", "."):
        with pytest.raises(DraftError) as caught:
            classify_move_request(ROOT, graph, [value], "pages/", probe=FakeMoveProbe())
        assert caught.value.code == "structure.protected"


def test_classify_move_request_file_target_requires_single_file_source() -> None:
    graph = _graph_from_texts({Path("index.md"): _INDEX})
    probe = FakeMoveProbe()
    probe.kinds[ROOT / "a.md"] = EntryKind.FILE
    probe.kinds[ROOT / "b.md"] = EntryKind.FILE

    with pytest.raises(DraftError) as caught:
        classify_move_request(ROOT, graph, ["a.md", "b.md"], "pages/x.md", probe=probe)
    assert caught.value.code == MOVE_OPERANDS_INVALID


def test_classify_move_request_file_target_rejects_directory_source() -> None:
    graph = _graph_from_texts({Path("index.md"): _INDEX})
    probe = FakeMoveProbe()
    probe.kinds[ROOT / "legacy"] = EntryKind.DIRECTORY
    probe.members[Path("legacy")] = (Path("legacy/a.md"),)

    with pytest.raises(DraftError) as caught:
        classify_move_request(ROOT, graph, ["legacy"], "pages/x.md", probe=probe)
    assert caught.value.code == MOVE_OPERANDS_INVALID
