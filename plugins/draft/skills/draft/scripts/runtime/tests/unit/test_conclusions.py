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
from draft.models import PageDocument, RelationCode, RootState, StatusCode
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
    plan = plan_generation(graph, state=ConclusionsState.NOT_GENERATED, root_state=RootState.REGISTERED)
    assert isinstance(plan, GenerationPlan)
    assert plan.generated is False
    assert plan.reason is GenerationReason.NO_CONFIRMED_PAGES
    assert plan.operation is None


def test_not_generated_with_confirmed_creates_page_and_marks_owner() -> None:
    graph = _draft_with_confirmed(state=ConclusionsState.NOT_GENERATED)
    plan = plan_generation(graph, state=ConclusionsState.NOT_GENERATED, root_state=RootState.REGISTERED)
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
    plan = plan_generation(graph, state=ConclusionsState.GENERATED, root_state=RootState.REGISTERED)
    assert plan.generated is True
    assert plan.operation is None or plan.operation.effects == ()


def test_generated_confirmed_page_is_rebuilt_as_proposed() -> None:
    graph = _draft_with_confirmed(state=ConclusionsState.GENERATED)
    graph.pages[CONCLUSIONS] = _page(CONCLUSIONS, status=StatusCode.CONFIRMED, title="结论汇总")
    plan = plan_generation(graph, state=ConclusionsState.GENERATED, root_state=RootState.REGISTERED)
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
    plan = plan_generation(graph, state=ConclusionsState.GENERATED, root_state=RootState.REGISTERED)
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
        plan_generation(graph, state=ConclusionsState.NOT_GENERATED, root_state=RootState.REGISTERED)


# --- 固定机器形状谓词 is_conclusions_document（规格 §12.4/§12.5） ---


def _conclusions_document(text: str) -> PageDocument:
    return parse_page(CONCLUSIONS, text)


CANONICAL_CONCLUSIONS = (
    "# 结论汇总\n\n"
    "> 状态：`当前提案`\n"
    ">\n"
    "> 父主题：[根索引](index.md)\n\n"
    "## 已确认结论\n\n"
    "- [甲](pages/a.md)\n"
    "- [乙](pages/b.md)\n"
)


def test_is_conclusions_document_accepts_canonical_proposed() -> None:
    from draft.conclusions import is_conclusions_document

    assert is_conclusions_document(_conclusions_document(CANONICAL_CONCLUSIONS), ROOT)


def test_is_conclusions_document_accepts_confirmed() -> None:
    """规格 §12.5：生成后 Agent 用 status set 把状态推进为 confirmed 必须合法。"""
    from draft.conclusions import is_conclusions_document

    text = CANONICAL_CONCLUSIONS.replace("当前提案", "已确认")
    assert is_conclusions_document(_conclusions_document(text), ROOT)


def test_is_conclusions_document_rejects_other_statuses() -> None:
    from draft.conclusions import is_conclusions_document

    for label in ("待讨论", "已关闭", "已取代"):
        text = CANONICAL_CONCLUSIONS.replace("当前提案", label)
        assert not is_conclusions_document(_conclusions_document(text), ROOT), label


def test_is_conclusions_document_rejects_wrong_title() -> None:
    from draft.conclusions import is_conclusions_document

    text = CANONICAL_CONCLUSIONS.replace("# 结论汇总", "# 别的标题")
    assert not is_conclusions_document(_conclusions_document(text), ROOT)


def test_is_conclusions_document_rejects_parent_not_root() -> None:
    from draft.conclusions import is_conclusions_document

    text = CANONICAL_CONCLUSIONS.replace(
        "> 父主题：[根索引](index.md)", "> 父主题：[其他](pages/other.md)"
    )
    assert not is_conclusions_document(_conclusions_document(text), ROOT)


def test_is_conclusions_document_rejects_missing_parent() -> None:
    from draft.conclusions import is_conclusions_document

    text = CANONICAL_CONCLUSIONS.replace("\n>\n> 父主题：[根索引](index.md)", "")
    assert not is_conclusions_document(_conclusions_document(text), ROOT)


def test_is_conclusions_document_rejects_body_paragraph() -> None:
    """章节后出现任意段落即形状损坏（规格 §12.4 机器区域）。"""
    from draft.conclusions import is_conclusions_document

    text = CANONICAL_CONCLUSIONS.replace(
        "- [甲](pages/a.md)\n", "随便一段文字，不是列表。\n- [甲](pages/a.md)\n"
    )
    assert not is_conclusions_document(_conclusions_document(text), ROOT)


def test_is_conclusions_document_rejects_illegal_list_item() -> None:
    """列表项必须只含标题与链接；无链接的列表项即形状损坏。"""
    from draft.conclusions import is_conclusions_document

    text = CANONICAL_CONCLUSIONS.replace(
        "- [甲](pages/a.md)\n", "- 非法列表内容没有链接\n- [甲](pages/a.md)\n"
    )
    assert not is_conclusions_document(_conclusions_document(text), ROOT)


def test_is_conclusions_document_rejects_entry_with_extra_text() -> None:
    """条目只包含标题和链接；链接后出现正文即形状损坏。"""
    from draft.conclusions import is_conclusions_document

    text = CANONICAL_CONCLUSIONS.replace("- [甲](pages/a.md)\n", "- [甲](pages/a.md)：甲的结论。\n")
    assert not is_conclusions_document(_conclusions_document(text), ROOT)


def test_is_conclusions_document_rejects_extra_h3_section() -> None:
    """章节下出现额外三级小节即形状损坏。"""
    from draft.conclusions import is_conclusions_document

    text = CANONICAL_CONCLUSIONS + "### 额外三级小节\n\n- [丙](pages/c.md)\n"
    assert not is_conclusions_document(_conclusions_document(text), ROOT)


def test_is_conclusions_document_rejects_extra_h2_section() -> None:
    """页面中只能有唯一 `## 已确认结论` 章节。"""
    from draft.conclusions import is_conclusions_document

    text = CANONICAL_CONCLUSIONS + "## 额外章节\n\n- [丙](pages/c.md)\n"
    assert not is_conclusions_document(_conclusions_document(text), ROOT)


def test_is_conclusions_document_rejects_extra_h1_heading() -> None:
    from draft.conclusions import is_conclusions_document

    text = CANONICAL_CONCLUSIONS.replace("## 已确认结论", "# 已确认结论")
    assert not is_conclusions_document(_conclusions_document(text), ROOT)


def test_is_conclusions_document_rejects_extra_machine_line() -> None:
    """机器块只允许状态行、空引用行与父主题行；额外引用行即损坏。"""
    from draft.conclusions import is_conclusions_document

    text = CANONICAL_CONCLUSIONS.replace(">\n", ">\n> 任意引用行\n")
    assert not is_conclusions_document(_conclusions_document(text), ROOT)


def test_is_conclusions_document_accepts_empty_list() -> None:
    """已生成但无已确认页面时重建为空列表，仍属合法形状。"""
    from draft.conclusions import is_conclusions_document

    text = "# 结论汇总\n\n> 状态：`当前提案`\n>\n> 父主题：[根索引](index.md)\n\n## 已确认结论\n"
    assert is_conclusions_document(_conclusions_document(text), ROOT)


def test_is_conclusions_document_rejects_entry_with_two_links() -> None:
    """条目必须只含一个标题与链接；一行两个链接即形状损坏。"""
    from draft.conclusions import is_conclusions_document

    text = CANONICAL_CONCLUSIONS.replace(
        "- [甲](pages/a.md)\n", "- [甲](pages/a.md) [乙](pages/b.md)\n"
    )
    assert not is_conclusions_document(_conclusions_document(text), ROOT)


def test_is_conclusions_document_rejects_entry_with_tooltip() -> None:
    """目标语法不允许 tooltip/属性；`(path "title")` 即形状损坏。"""
    from draft.conclusions import is_conclusions_document

    text = CANONICAL_CONCLUSIONS.replace("- [甲](pages/a.md)\n", '- [甲](pages/a.md "tooltip")\n')
    assert not is_conclusions_document(_conclusions_document(text), ROOT)


def test_is_canonical_conclusions_entry_rejects_non_dash_marker() -> None:
    """规范结论条目的列表标记必须恰为 ``-``；``*``/``+``/有序不是规范标记。"""
    from draft.conclusions import is_canonical_conclusions_entry

    for marker in ("*", "+", "1."):
        assert not is_canonical_conclusions_entry(f"{marker} [甲](pages/a.md)"), marker


def test_is_canonical_conclusions_entry_accepts_escaped_label() -> None:
    """标题含 ``[``/``]`` 时经转义后的条目仍是规范结论条目，解析标签还原为原始标题。"""
    from draft.conclusions import is_canonical_conclusions_entry

    assert is_canonical_conclusions_entry("- [甲\\]乙](pages/a.md)")
    assert is_canonical_conclusions_entry("- [甲\\[乙](pages/a.md)")


def test_is_conclusions_document_accepts_escaped_label_entry() -> None:
    from draft.conclusions import is_conclusions_document

    text = CANONICAL_CONCLUSIONS.replace("- [甲](pages/a.md)\n", "- [甲\\]乙](pages/a.md)\n")
    assert is_conclusions_document(_conclusions_document(text), ROOT)


def test_is_conclusions_nav_canonical_accepts_canonical() -> None:
    from draft.conclusions import is_conclusions_nav_canonical

    index = (
        "# 根\n\n> 状态：`已关闭：纯导航页，不表达独立结论`\n\n## 子主题\n\n"
        "- [甲](pages/a.md)\n- [结论汇总](conclusions.md)\n"
    )
    assert is_conclusions_nav_canonical(index, ROOT)


def test_is_conclusions_nav_canonical_rejects_wrong_label() -> None:
    """根导航结论链接必须使用规范标签，目标等价但标签错误即非规范。"""
    from draft.conclusions import is_conclusions_nav_canonical

    index = (
        "# 根\n\n> 状态：`已关闭：纯导航页，不表达独立结论`\n\n## 子主题\n\n"
        "- [错误名字](conclusions.md)\n"
    )
    assert not is_conclusions_nav_canonical(index, ROOT)


def test_is_conclusions_nav_canonical_rejects_literal_target_variant() -> None:
    """根导航结论链接必须使用字面目标 `conclusions.md`，`./` 等价目标即非规范。"""
    from draft.conclusions import is_conclusions_nav_canonical

    index = (
        "# 根\n\n> 状态：`已关闭：纯导航页，不表达独立结论`\n\n## 子主题\n\n"
        "- [结论汇总](./conclusions.md)\n"
    )
    assert not is_conclusions_nav_canonical(index, ROOT)


@pytest.mark.parametrize(
    "suffix",
    ["* [后续子页](pages/a.md)\n", "+ [后续子页](pages/a.md)\n", "1. [后续子页](pages/a.md)\n"],
)
def test_is_conclusions_nav_canonical_rejects_later_list_item(suffix: str) -> None:
    """结论链接之后出现任意 Markdown 列表项（-/+/*/有序）即非规范，不只看 `- [`。"""
    from draft.conclusions import is_conclusions_nav_canonical

    index = (
        "# 根\n\n> 状态：`已关闭：纯导航页，不表达独立结论`\n\n## 子主题\n\n"
        f"- [结论汇总](conclusions.md)\n{suffix}"
    )
    assert not is_conclusions_nav_canonical(index, ROOT)
