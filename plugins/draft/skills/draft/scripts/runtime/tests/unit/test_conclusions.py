from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from draft.conclusions import (
    ConclusionsState,
    GenerationPlan,
    GenerationReason,
    plan_generation,
)
from draft.errors import DraftError
from draft.markdown import parse_page
from draft.models import PageDocument, RelationCode, StatusCode
from draft.vocabulary import status_label

ROOT = Path("/draft")
INDEX = Path("index.md")
CONCLUSIONS = Path("conclusions.md")
A = Path("pages/a.md")
B = Path("pages/b.md")


def _page(
    path: Path, *, status: StatusCode, title: str = "t", parent: Path | None = None
) -> PageDocument:
    parent_line = (
        "\n>\n> 父主题：[根](../index.md)"
        if parent is None
        else f"\n>\n> 父主题：[{parent.name}]({parent})"
    )
    return parse_page(
        path,
        f"# {title}\n\n> 状态：`{status_label(status)}`{parent_line}\n\n正文。\n",
    )


def _graph_like(
    pages: Mapping[Path, PageDocument],
    *,
    relations: Mapping[RelationCode, tuple[tuple[Path, Path, int], ...]] | None = None,
    parents: Mapping[Path, Path] | None = None,
) -> SimpleGraph:
    return SimpleGraph(
        pages=dict(pages),
        relations=relations or {},
        parents=parents or {},
    )


class SimpleGraph:
    def __init__(
        self,
        *,
        pages: Mapping[Path, PageDocument],
        relations: Mapping[RelationCode, tuple[tuple[Path, Path, int], ...]],
        parents: Mapping[Path, Path],
    ) -> None:
        self.root = ROOT
        self.pages = dict(pages)
        self.relations = relations
        self.parents = parents
        children: dict[Path, list[Path]] = {}
        for child, parent in parents.items():
            children.setdefault(parent, []).append(child)
        self.children = {parent: tuple(sorted(entries)) for parent, entries in children.items()}
        self.body_resources: dict[Path, object] = {}


def _draft_with_confirmed(*, state: ConclusionsState) -> SimpleGraph:
    index = _page(INDEX, status=StatusCode.CLOSED, title="根")
    a = _page(A, status=StatusCode.PENDING)
    b = _page(B, status=StatusCode.CONFIRMED)
    pages = {INDEX: index, A: a, B: b}
    relations = {
        RelationCode.PARENT: ((A, INDEX, 5), (B, INDEX, 5)),
    }
    parents = {A: INDEX, B: INDEX}
    return _graph_like(pages, relations=relations, parents=parents)


def _draft_generated_with_nav() -> SimpleGraph:
    """已生成状态：根导航已含结论链接（且位于最后），结论页内容与生成结果一致。"""
    graph = _draft_with_confirmed(state=ConclusionsState.GENERATED)
    graph.pages[INDEX] = parse_page(
        INDEX,
        "# 根\n\n> 状态：`已关闭：纯导航页，不表达独立结论`\n\n## 子主题\n\n"
        "- [乙](pages/b.md)\n- [结论汇总](conclusions.md)\n",
    )
    graph.pages[CONCLUSIONS] = parse_page(
        CONCLUSIONS,
        "# 结论汇总\n\n> 状态：`当前提案`\n>\n> 父主题：[根索引](index.md)\n\n"
        "## 已确认结论\n\n- [t](pages/b.md)\n",
    )
    return graph


def test_not_generated_without_confirmed_is_no_op() -> None:
    graph = _draft_with_confirmed(state=ConclusionsState.NOT_GENERATED)
    graph.pages = {p: page for p, page in graph.pages.items() if p != B}
    plan = plan_generation(graph, state=ConclusionsState.NOT_GENERATED)
    assert isinstance(plan, GenerationPlan)
    assert plan.generated is False
    assert plan.reason is GenerationReason.NO_CONFIRMED_PAGES
    assert plan.operation is None


def test_not_generated_with_confirmed_creates_page_and_marks_owner() -> None:
    graph = _draft_with_confirmed(state=ConclusionsState.NOT_GENERATED)
    plan = plan_generation(graph, state=ConclusionsState.NOT_GENERATED)
    assert plan.generated is True
    assert plan.reason is None
    assert plan.operation is not None
    # 公开效果只包含结论页与根页面，不含 .draft/draft.json。
    operation = plan.operation
    assert operation is not None
    paths = {e.path for e in operation.effects}
    assert all(p is not None for p in paths)
    assert CONCLUSIONS in paths
    assert INDEX in paths
    assert all(not p.as_posix().startswith(".draft/") for p in paths)  # type: ignore[union-attr]


def test_generated_unchanged_produces_no_effect() -> None:
    graph = _draft_generated_with_nav()
    plan = plan_generation(graph, state=ConclusionsState.GENERATED)
    assert plan.generated is True
    assert plan.operation is None or plan.operation.effects == ()


def test_generated_confirmed_page_is_rebuilt_as_proposed() -> None:
    graph = _draft_with_confirmed(state=ConclusionsState.GENERATED)
    graph.pages[CONCLUSIONS] = _page(CONCLUSIONS, status=StatusCode.CONFIRMED, title="结论汇总")
    plan = plan_generation(graph, state=ConclusionsState.GENERATED)
    assert plan.generated is True
    operation = plan.operation
    assert operation is not None
    # 生成的结论页状态必须是 proposed。
    rendered = operation.mutations[0].content
    assert rendered is not None
    assert b"proposed" in rendered or "当前提案".encode() in rendered


def test_generated_without_confirmed_keeps_empty_list() -> None:
    graph = _draft_with_confirmed(state=ConclusionsState.GENERATED)
    graph.pages = {p: page for p, page in graph.pages.items() if p != B}
    graph.pages[CONCLUSIONS] = _page(CONCLUSIONS, status=StatusCode.PROPOSED, title="结论汇总")
    plan = plan_generation(graph, state=ConclusionsState.GENERATED)
    assert plan.generated is True
    operation = plan.operation
    assert operation is not None
    content = operation.mutations[0].content
    assert content is not None
    rendered = content.decode()
    assert "## 已确认结论" in rendered


def test_not_generated_with_unexpected_file_fails() -> None:
    from draft.diagnostic_codes import CheckCode

    graph = _draft_with_confirmed(state=ConclusionsState.NOT_GENERATED)
    graph.pages[CONCLUSIONS] = _page(CONCLUSIONS, status=StatusCode.PROPOSED, title="结论汇总")
    with pytest.raises(DraftError, match=CheckCode.CONCLUSIONS_UNEXPECTED_FILE.value):
        plan_generation(graph, state=ConclusionsState.NOT_GENERATED)
