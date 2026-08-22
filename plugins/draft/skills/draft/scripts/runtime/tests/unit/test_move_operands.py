from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from draft.errors import DraftError
from draft.graph import DraftGraph, resolve_local_destination
from draft.markdown import parse_page
from draft.models import MarkdownLink, PageDocument, SourceSpan
from draft.move_operands import (
    MoveTarget,
    TargetKind,
    is_internal_territory,
    map_sources,
    validate_results,
)
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

    # 目录源位于 pages/ 之下时，结果落在其内部才真正命中 descendant 分支
    # （结果在 pages/ 之外会先被命名空间检查以 invalid_target 拒绝）。
    with pytest.raises(DraftError) as caught:
        validate_results(
            graph,
            {Path("pages/topic/index.md"): Path("pages/topic/index.md")},
            (Path("pages/topic"),),
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
