from __future__ import annotations

import argparse
from collections.abc import Mapping
from pathlib import Path
from unittest.mock import MagicMock

from pytest_mock import MockerFixture

import draft.cli as cli
from draft.graph import DraftGraph
from draft.link_index import Backlink, LinkIndexContent, LinkSpan
from draft.link_index_maintenance import (
    LinkIndexMaintenanceResult,
    PreparedLinkIndexUpdate,
)
from draft.markdown import parse_page
from draft.models import (
    PageDocument,
    RelationCode,
    ResultWarning,
    RootState,
    StatusCode,
)
from draft.paths import ResolvedPath
from draft.query import (
    OpportunisticIndexResult,
    PageSummary,
    backlinks,
    backlinks_from_index,
    incoming_relations,
    known_documents_from_graph,
    maintain_link_index_for_query,
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


# ---------------------------------------------------------------------------
# Task 9：索引事实服务与机会式维护（spec §14/§16）
# ---------------------------------------------------------------------------


def _real_graph() -> DraftGraph:
    """受真实接口约束的 DraftGraph：page_digest 与 known_documents 复用需要。"""
    text = "# A\n\n> 状态：`待讨论`\n\n见 [乙](b.md)。\n"
    return DraftGraph(
        root=ROOT,
        pages={A: parse_page(A, text)},
        parents={},
        children={},
        inbound_links={},
        relations={},
    )


def test_backlinks_from_index_dedupes_and_sorts_sources() -> None:
    """反向表来源去重并按来源路径排序（spec §14 完整索引事实）。"""
    identity = ResourceIdentity(ResourceKind.PAGE, "pages/a.md")
    content = LinkIndexContent(
        files={},
        backlinks={
            identity: (
                Backlink(Path("pages/b.md"), LinkSpan(10, 20)),
                Backlink(Path("index.md"), LinkSpan(5, 9)),
                Backlink(Path("pages/b.md"), LinkSpan(30, 40)),
            ),
        },
        directories={},
    )
    sources = backlinks_from_index(content, identity)
    assert [path.as_posix() for path in sources] == ["index.md", "pages/b.md"]


def test_backlinks_from_index_missing_target_is_empty() -> None:
    content = LinkIndexContent(files={}, backlinks={}, directories={})
    assert (
        backlinks_from_index(content, ResourceIdentity(ResourceKind.PAGE, "pages/none.md"))
        == ()
    )


def test_page_summary_injects_backlink_count() -> None:
    graph = _make_graph()
    summary = page_summary(graph, A, backlink_count=7)
    assert summary.backlink_count == 7


def test_known_documents_from_graph_extracts_path_digest_and_outbound() -> None:
    """上层已解析页面成为可验证 scan seed：path + sha256 + 出站边（spec §8.6）。"""
    graph = _real_graph()
    documents = known_documents_from_graph(ROOT, graph)
    assert len(documents) == 1
    document = documents[0]
    assert document.path == A
    assert document.sha256 == graph.page_digest(A)
    identities = {edge.resource_identity for edge in document.outbound}
    assert ResourceIdentity(ResourceKind.PAGE, "pages/b.md") in identities


def _resolved(relative: str) -> ResolvedPath:
    return ResolvedPath(
        root=Path("/draft"),
        relative=Path(relative),
        absolute=Path("/draft") / relative,
    )


def _query_args(command: str, *operands: str) -> argparse.Namespace:
    return cli.build_parser().parse_args(["query", command, "--root", "/draft", *operands])


def _empty_view() -> MagicMock:
    view = MagicMock()
    view.first_blocking = None
    view.blocking_paths = ()
    return view


def test_query_backlinks_calls_opportunistic_maintenance(mocker: MockerFixture) -> None:
    """query backlinks 入口尝试机会式维护（spec §14 路由决策表）。"""
    mocker.patch("draft.cli._query_root_state", return_value=RootState.REGISTERED)
    mocker.patch(
        "draft.resources.resource_identity_from_operand",
        return_value=ResourceIdentity(ResourceKind.PAGE, "pages/a.md"),
    )
    view = _empty_view()
    mocker.patch("draft.query.load_tolerant_view", return_value=view)
    mocker.patch("draft.query.backlinks_from_root", return_value=())
    maintain = mocker.patch(
        "draft.query.maintain_link_index_for_query",
        return_value=OpportunisticIndexResult(None, ()),
    )
    args = _query_args("backlinks", "pages/a.md")
    payload, _human, exit_code = cli._dispatch(args, root=Path("/draft"))  # pyright: ignore[reportPrivateUsage]
    maintain.assert_called_once_with(Path("/draft"), view.graph, RootState.REGISTERED)
    assert exit_code == 0
    assert payload["result_type"] == "query.backlinks"
    assert "warnings" not in payload


def test_query_backlinks_uses_index_when_available(mocker: MockerFixture) -> None:
    """索引可用时从已验证反向表读取（spec §14）。"""
    mocker.patch("draft.cli._query_root_state", return_value=RootState.REGISTERED)
    mocker.patch(
        "draft.resources.resource_identity_from_operand",
        return_value=ResourceIdentity(ResourceKind.PAGE, "pages/a.md"),
    )
    view = _empty_view()
    mocker.patch("draft.query.load_tolerant_view", return_value=view)
    index = LinkIndexContent(files={}, backlinks={}, directories={})
    mocker.patch(
        "draft.query.maintain_link_index_for_query",
        return_value=OpportunisticIndexResult(index, ()),
    )
    from_index = mocker.patch(
        "draft.query.backlinks_from_index", return_value=(Path("pages/b.md"),)
    )
    from_root = mocker.patch("draft.query.backlinks_from_root")
    args = _query_args("backlinks", "pages/a.md")
    payload, _human, exit_code = cli._dispatch(args, root=Path("/draft"))  # pyright: ignore[reportPrivateUsage]
    from_index.assert_called_once()
    from_root.assert_not_called()
    assert payload["items"] == [{"page": "pages/b.md"}]
    assert "warnings" not in payload
    assert exit_code == 0


def test_query_backlinks_uses_full_closure_when_degraded(mocker: MockerFixture) -> None:
    """索引不可用（降级）时回退到全根 .md 完整闭包（spec §8/§14）。"""
    mocker.patch("draft.cli._query_root_state", return_value=RootState.REGISTERED)
    mocker.patch(
        "draft.resources.resource_identity_from_operand",
        return_value=ResourceIdentity(ResourceKind.PAGE, "pages/a.md"),
    )
    view = _empty_view()
    mocker.patch("draft.query.load_tolerant_view", return_value=view)
    mocker.patch(
        "draft.query.maintain_link_index_for_query",
        return_value=OpportunisticIndexResult(
            None, (ResultWarning("link_index.rebuild_required"),)
        ),
    )
    from_index = mocker.patch("draft.query.backlinks_from_index")
    from_root = mocker.patch("draft.query.backlinks_from_root", return_value=(Path("notes.md"),))
    args = _query_args("backlinks", "pages/a.md")
    payload, _human, exit_code = cli._dispatch(args, root=Path("/draft"))  # pyright: ignore[reportPrivateUsage]
    from_index.assert_not_called()
    from_root.assert_called_once()
    assert payload["items"] == [{"page": "notes.md"}]
    assert payload["warnings"] == [
        {"code": "link_index.rebuild_required", "severity": "warning"}
    ]
    assert exit_code == 0


def test_query_page_calls_opportunistic_maintenance(mocker: MockerFixture) -> None:
    """query page 入口为 backlink count 尝试机会式维护（spec §14 路由决策表）。"""
    mocker.patch("draft.cli._query_root_state", return_value=RootState.REGISTERED)
    mocker.patch("draft.paths.resolve_page_operand", return_value=_resolved("pages/a.md"))
    view = _empty_view()
    view.graph.pages = {Path("pages/a.md"): object()}
    mocker.patch("draft.query.load_tolerant_view", return_value=view)
    summary = PageSummary(
        path=Path("pages/a.md"),
        title="甲",
        status=StatusCode.PENDING,
        parent=None,
        child_count=0,
        link_count=0,
        backlink_count=0,
        outgoing_relation_count=0,
        incoming_relation_count=0,
    )
    page_summary_mock = mocker.patch("draft.query.page_summary", return_value=summary)
    mocker.patch("draft.query.backlinks_from_root", return_value=())
    maintain = mocker.patch(
        "draft.query.maintain_link_index_for_query",
        return_value=OpportunisticIndexResult(None, ()),
    )
    args = _query_args("page", "pages/a.md")
    payload, _human, exit_code = cli._dispatch(args, root=Path("/draft"))  # pyright: ignore[reportPrivateUsage]
    maintain.assert_called_once_with(Path("/draft"), view.graph, RootState.REGISTERED)
    page_summary_mock.assert_called_once()
    assert exit_code == 0
    assert payload["result_type"] == "query.page"


def test_query_links_relations_status_recoverable_do_not_maintain(
    mocker: MockerFixture,
) -> None:
    """不消费链接索引的查询不触发维护（spec §14 路由决策表）。"""
    mocker.patch("draft.cli._query_root_state", return_value=RootState.REGISTERED)
    maintain = mocker.patch("draft.query.maintain_link_index_for_query")

    mocker.patch("draft.paths.resolve_page_operand", return_value=_resolved("pages/a.md"))
    mocker.patch("draft.query.load_registered_page", return_value=MagicMock())
    mocker.patch("draft.query.page_links", return_value=())
    cli._dispatch(_query_args("links", "pages/a.md"), root=Path("/draft"))  # pyright: ignore[reportPrivateUsage]
    maintain.assert_not_called()

    mocker.patch("draft.query.outgoing_relations", return_value=())
    cli._dispatch(  # pyright: ignore[reportPrivateUsage]
        _query_args("relations", "pages/a.md", "--direction", "outgoing"),
        root=Path("/draft"),
    )
    maintain.assert_not_called()

    view = _empty_view()
    mocker.patch("draft.query.load_tolerant_view", return_value=view)
    mocker.patch("draft.query.pages_with_status", return_value=())
    cli._dispatch(_query_args("status", "pending"), root=Path("/draft"))  # pyright: ignore[reportPrivateUsage]
    maintain.assert_not_called()

    mocker.patch("draft.trash_lifecycle.list_recoverable", return_value=())
    cli._dispatch(_query_args("recoverable"), root=Path("/draft"))  # pyright: ignore[reportPrivateUsage]
    maintain.assert_not_called()


def test_maintain_link_index_for_query_fresh_reuses_published_baseline(
    mocker: MockerFixture,
) -> None:
    """fresh：严格扫描证明磁盘索引当前后复用已发布制品，不重复 commit。"""
    graph = _real_graph()
    mocker.patch(
        "draft.link_index_maintenance.prepare_link_index_update",
        return_value=PreparedLinkIndexUpdate(
            observed_revision="1", scan=None, requires_rebuild=False
        ),
    )
    baseline = MagicMock()
    baseline.index = LinkIndexContent(files={}, backlinks={}, directories={})
    mocker.patch(
        "draft.link_index_maintenance.load_link_index_baseline", return_value=baseline
    )
    commit = mocker.patch("draft.link_index_maintenance.commit_link_index_update")
    result = maintain_link_index_for_query(ROOT, graph, RootState.REGISTERED)
    assert result.index is baseline.index
    assert result.warnings == ()
    commit.assert_not_called()


def test_maintain_link_index_for_query_uses_published_result(
    mocker: MockerFixture,
) -> None:
    """维护成功：使用已发布制品。"""
    graph = _real_graph()
    scan = MagicMock()
    mocker.patch(
        "draft.link_index_maintenance.prepare_link_index_update",
        return_value=PreparedLinkIndexUpdate(
            observed_revision=None, scan=scan, requires_rebuild=False
        ),
    )
    index = MagicMock()
    commit = mocker.patch(
        "draft.link_index_maintenance.commit_link_index_update",
        return_value=LinkIndexMaintenanceResult(index=index, warnings=(), published=True),
    )
    result = maintain_link_index_for_query(ROOT, graph, RootState.REGISTERED)
    assert result.index is index
    commit.assert_called_once()


def test_maintain_link_index_for_query_keeps_full_scan_when_commit_unpublished(
    mocker: MockerFixture,
) -> None:
    """未发布（并发/锁持有）仍保留本次完整扫描事实（spec §14）。"""
    graph = _real_graph()
    scan = MagicMock()
    scan.content = LinkIndexContent(files={}, backlinks={}, directories={})
    mocker.patch(
        "draft.link_index_maintenance.prepare_link_index_update",
        return_value=PreparedLinkIndexUpdate(
            observed_revision="1", scan=scan, requires_rebuild=False
        ),
    )
    mocker.patch(
        "draft.link_index_maintenance.commit_link_index_update",
        return_value=LinkIndexMaintenanceResult(index=None, warnings=(), published=False),
    )
    result = maintain_link_index_for_query(ROOT, graph, RootState.REGISTERED)
    assert result.index is scan.content


def test_maintain_link_index_for_query_rebuild_required_returns_none_with_warnings(
    mocker: MockerFixture,
) -> None:
    """扫描失败（rebuild_required）：返回 None 与降级 warning，由调用方回退图内事实。"""
    graph = _real_graph()
    mocker.patch(
        "draft.link_index_maintenance.prepare_link_index_update",
        return_value=PreparedLinkIndexUpdate(
            observed_revision="1", scan=None, requires_rebuild=True
        ),
    )
    mocker.patch(
        "draft.link_index_maintenance.commit_link_index_update",
        return_value=LinkIndexMaintenanceResult(
            index=None,
            warnings=(ResultWarning("link_index.rebuild_required"),),
            published=False,
        ),
    )
    result = maintain_link_index_for_query(ROOT, graph, RootState.REGISTERED)
    assert result.index is None
    assert result.warnings == (ResultWarning("link_index.rebuild_required"),)
