from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from draft.errors import DraftError
from draft.graph import DraftGraph
from draft.markdown import parse_page
from draft.models import OperationPlan, PageDocument, StatusCode
from draft.page_create import (
    PlanAction,
    plan_create_page,
    resolve_new_leaf_operand,
)
from draft.vocabulary import status_label

ROOT = Path("/draft")
INDEX = Path("index.md")
A = Path("pages/a.md")


def _page(path: Path, *, title: str = "t", status: StatusCode = StatusCode.PENDING) -> PageDocument:
    return parse_page(
        path,
        (
            f"# {title}\n\n> 状态：`{status_label(status)}`\n>\n"
            "> 父主题：[根](../index.md)\n\n正文。\n"
        ),
    )


def _graph(
    pages: Mapping[Path, PageDocument] | None = None,
    parents: Mapping[Path, Path] | None = None,
) -> DraftGraph:
    return DraftGraph(
        root=ROOT,
        pages=dict(pages) if pages is not None else {INDEX: _page(INDEX, title="根")},
        parents=dict(parents or {}),
        children={},
        inbound_links={},
        relations={},
    )


def _create_plan(
    graph: DraftGraph,
    path: Path,
    *,
    title: str,
    status: StatusCode,
) -> OperationPlan:
    from draft.models import RootState
    from draft.observations import graph_observations

    return plan_create_page(
        graph,
        path,
        title=title,
        status=status,
        root_state=RootState.REGISTERED,
        observations=graph_observations(graph),
    )


def test_resolve_new_leaf_rejects_illegal_operands() -> None:
    bad = [
        "/abs/a.md",
        "pages\\a.md",
        "notes.md",
        "pages/a.txt",
        "pages/./a.md",
        "pages/../a.md",
        "pages//a.md",
        "pages/index.md",
        "index.md",
    ]
    for value in bad:
        with pytest.raises(DraftError):
            resolve_new_leaf_operand(ROOT, value)


def test_resolve_new_leaf_accepts_nested_pages() -> None:
    resolved = resolve_new_leaf_operand(ROOT, "pages/hub/child.md")
    assert resolved.relative == Path("pages/hub/child.md")


def test_plan_create_page_requires_existing_parent() -> None:
    graph = _graph(pages={INDEX: _page(INDEX, title="根")}, parents={})
    with pytest.raises(DraftError, match="structure.missing_parent"):
        _create_plan(graph, Path("pages/missing/child.md"), title="子", status=StatusCode.PENDING)


def test_plan_create_page_rejects_existing_target() -> None:
    graph = _graph(pages={INDEX: _page(INDEX, title="根"), A: _page(A, title="甲")})
    with pytest.raises(DraftError, match="structure.target_exists"):
        _create_plan(graph, A, title="甲", status=StatusCode.PENDING)


def test_plan_create_page_rejects_confirmed_or_superseded_or_closed() -> None:
    graph = _graph()
    for status in (StatusCode.CONFIRMED, StatusCode.SUPERSEDED, StatusCode.CLOSED):
        with pytest.raises(DraftError, match="create.invalid_status"):
            _create_plan(graph, Path("pages/b.md"), title="乙", status=status)


def test_plan_create_page_creates_leaf_with_create_and_modify() -> None:
    graph = _graph()
    plan = _create_plan(graph, Path("pages/b.md"), title="乙", status=StatusCode.PENDING)

    assert plan.kind == "create"
    actions = [effect.action for effect in plan.effects]
    assert actions == [PlanAction.CREATE, PlanAction.MODIFY]
    created = next(e for e in plan.effects if e.action is PlanAction.CREATE)
    assert created.path == Path("pages/b.md")


def test_plan_create_page_renders_title_with_escaping() -> None:
    graph = _graph()
    title = "乙：a[b]c (x) _y_"
    plan = _create_plan(graph, Path("pages/b.md"), title=title, status=StatusCode.PENDING)
    created = next(e for e in plan.effects if e.action is PlanAction.CREATE)
    content = next(m.content for m in plan.mutations if m.path == created.path)
    assert content is not None
    parsed = parse_page(Path("pages/b.md"), content.decode())
    assert parsed.title == title


def test_plan_create_page_rejects_empty_and_control_titles() -> None:
    graph = _graph()
    for title in ("", "换行\n标题", "控制\x00"):
        with pytest.raises(DraftError, match="create.invalid_title"):
            _create_plan(graph, Path("pages/b.md"), title=title, status=StatusCode.PENDING)
