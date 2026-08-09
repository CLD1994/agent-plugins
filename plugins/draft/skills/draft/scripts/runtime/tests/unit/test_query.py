from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from draft.markdown import parse_page
from draft.models import (
    PageDocument,
    RelationCode,
    StatusCode,
)
from draft.query import (
    PageSummary,
    backlinks,
    incoming_relations,
    outgoing_relations,
    page_links,
    page_summary,
    pages_with_status,
)
from draft.resources import (
    HostOS,
    ProbeState,
    ResourceIdentity,
    ResourceKind,
    ResourceReference,
    ResourceScope,
    TargetState,
    resolve_page_resources,
)

ROOT = Path("/draft")


class SimpleGraph:
    """受真实接口约束的查询图替身：只暴露查询函数需要的只读事实。"""

    def __init__(
        self,
        *,
        pages: Mapping[Path, PageDocument],
        body_resources: Mapping[Path, tuple[ResourceReference, ...]],
        relations: Mapping[RelationCode, tuple[tuple[Path, Path, int], ...]],
        parents: Mapping[Path, Path],
    ) -> None:
        self.root = ROOT
        self.pages = dict(pages)
        self.body_resources = dict(body_resources)
        self.relations = dict(relations)
        self.parents = dict(parents)


def _graph_like(
    pages: Mapping[Path, PageDocument],
    body_resources: Mapping[Path, tuple[ResourceReference, ...]],
    *,
    relations: Mapping[RelationCode, tuple[tuple[Path, Path, int], ...]] | None = None,
    parents: Mapping[Path, Path] | None = None,
) -> SimpleGraph:
    return SimpleGraph(
        pages=pages,
        body_resources=body_resources,
        relations=relations or {},
        parents=parents or {},
    )


def _probe(states: Mapping[Path, ProbeState] | None = None):
    resolved = dict(states or {})

    def probe(target: Path) -> ProbeState:
        return resolved.get(target, ProbeState.MISSING)

    return probe


A = Path("pages/a.md")
B = Path("pages/b.md")
INDEX = Path("index.md")


def _make_graph():
    a_text = (
        "# A\n\n> 状态：`待讨论`\n>\n> 父主题：[根](../index.md)\n\n"
        "见 [乙](b.md) 与 [图](../asset.png) 与 [外部](https://example.com/x)。\n"
    )
    b_text = (
        "# B\n\n> 状态：`当前提案`\n>\n> 父主题：[根](../index.md)\n>\n"
        "> 依赖：[甲](a.md)\n\n提到 [甲](a.md)。\n"
    )
    pages = {
        A: parse_page(A, a_text),
        B: parse_page(B, b_text),
        INDEX: parse_page(INDEX, "# 根\n\n> 状态：`已关闭`\n"),
    }
    states = {
        ROOT / "asset.png": ProbeState.FILE,
        ROOT / "pages/a.md": ProbeState.FILE,
        ROOT / "pages/b.md": ProbeState.FILE,
    }
    body_resources = {
        path: resolve_page_resources(ROOT, page, host_os=HostOS.LINUX, target_probe=_probe(states))
        for path, page in pages.items()
    }
    relations = {
        RelationCode.PARENT: ((A, INDEX, 5), (B, INDEX, 5)),
        RelationCode.DEPENDS_ON: ((B, A, 7),),
    }
    parents = {A: INDEX, B: INDEX}
    return _graph_like(pages, body_resources, relations=relations, parents=parents)


def test_page_links_excludes_relations_dedupes_and_keeps_order() -> None:
    graph = _make_graph()
    links = page_links(graph, A)
    # 父主题是关系位置，不进入正文资源；b.md 与图去重排序按首次出现。
    assert [(link.target, link.kind, link.scope, link.target_state) for link in links] == [
        ("pages/b.md", ResourceKind.PAGE, ResourceScope.DRAFT, TargetState.PRESENT),
        ("asset.png", ResourceKind.LOCAL, ResourceScope.DRAFT, TargetState.PRESENT),
        (
            "https://example.com/x",
            ResourceKind.EXTERNAL,
            ResourceScope.UNKNOWN,
            TargetState.UNCHECKED,
        ),
    ]


def test_page_links_removes_anchor_from_target() -> None:
    text = "# A\n\n> 状态：`待讨论`\n>\n> 父主题：[根](../index.md)\n\n见 [乙](b.md#节)。\n"
    page = parse_page(A, text)
    body = resolve_page_resources(ROOT, page, host_os=HostOS.LINUX, target_probe=_probe())
    graph = _graph_like({A: page}, {A: body})
    links = page_links(graph, A)
    assert links[0].target == "pages/b.md"


def test_backlinks_dedupes_and_sorts_by_source() -> None:
    graph = _make_graph()
    target = ResourceIdentity(ResourceKind.PAGE, "pages/a.md")
    incoming = backlinks(graph, target)
    assert [path.as_posix() for path in incoming] == ["pages/b.md"]


def test_outgoing_relations_keep_declaration_order() -> None:
    graph = _make_graph()
    out = outgoing_relations(graph, B)
    assert [(r.relation, r.page.as_posix()) for r in out] == [
        (RelationCode.PARENT, "index.md"),
        (RelationCode.DEPENDS_ON, "pages/a.md"),
    ]


def test_incoming_relations_sorted_by_source_path() -> None:
    graph = _make_graph()
    inc = incoming_relations(graph, A)
    assert [(r.relation, r.page.as_posix()) for r in inc] == [
        (RelationCode.DEPENDS_ON, "pages/b.md"),
    ]


def test_pages_with_status_sorted_by_path() -> None:
    graph = _make_graph()
    result = pages_with_status(graph, StatusCode.PENDING)
    assert [path.as_posix() for path in result] == ["pages/a.md"]


def test_page_summary_counts_match_links_and_relations() -> None:
    graph = _make_graph()
    summary = page_summary(graph, A)
    assert isinstance(summary, PageSummary)
    assert summary.path == A
    assert summary.status is StatusCode.PENDING
    assert summary.parent == INDEX
    assert summary.link_count == 3
    assert summary.outgoing_relation_count == 1  # parent
    assert summary.incoming_relation_count == 1  # B 的依赖
    assert summary.backlink_count == 1  # B 的正文链接
