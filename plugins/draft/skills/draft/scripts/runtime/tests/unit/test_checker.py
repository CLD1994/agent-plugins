from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from draft.checker import check_graph
from draft.diagnostic_codes import CheckCode, ReplacementStatusReason, WrongOutputReason
from draft.graph import ROOT_PAGE, DraftGraph
from draft.models import (
    CheckIssue,
    IssueSeverity,
    MarkdownLink,
    PageDocument,
    Relation,
    RelationLabel,
    SourceSpan,
    StatusPrefix,
    StatusValue,
)

ROOT = Path("/draft")
CONCLUSIONS = Path("conclusions.md")
A = Path("pages/a.md")
B = Path("pages/b.md")
SUB_INDEX = Path("pages/sub/index.md")
_PENDING = StatusValue(StatusPrefix.PENDING, None)


def _link(destination: str, *, line: int = 1, label: str = "x") -> MarkdownLink:
    span = SourceSpan(line * 100, line * 100 + 10)
    return MarkdownLink(
        label=label,
        destination=destination,
        destination_span=span,
        whole_span=span,
        line=line,
        is_image=False,
    )


def _relation(label: RelationLabel, destination: str, *, line: int = 1) -> Relation:
    return Relation(label=label, link=_link(destination, line=line))


def _page(
    path: Path,
    *,
    status: StatusValue = _PENDING,
    relations: tuple[Relation, ...] = (),
    links: tuple[MarkdownLink, ...] = (),
    text: str = "",
) -> PageDocument:
    return PageDocument(
        path=path, title="t", status=status, relations=relations, links=links, text=text
    )


def _graph(
    *,
    pages: Mapping[Path, PageDocument] | None = None,
    parents: Mapping[Path, Path] | None = None,
    children: Mapping[Path, tuple[Path, ...]] | None = None,
    inbound_links: Mapping[Path, tuple[tuple[Path, MarkdownLink], ...]] | None = None,
    relations: Mapping[RelationLabel, tuple[tuple[Path, Path, int], ...]] | None = None,
    root: Path = ROOT,
) -> DraftGraph:
    return DraftGraph(
        root=root,
        pages=dict(pages or {}),
        parents=dict(parents or {}),
        children=dict(children or {}),
        inbound_links=dict(inbound_links or {}),
        relations={label: () for label in RelationLabel} | dict(relations or {}),
    )


def _issue(code: str, path: Path | None, line: int | None = None, *details: str) -> CheckIssue:
    return CheckIssue(
        code=code, severity=IssueSeverity.ERROR, path=path, line=line, details=details
    )


# ---------------------------------------------------------------------------
# clean baseline: 父子互反正确时不报告问题
# ---------------------------------------------------------------------------


def test_clean_parent_child_graph_has_no_issues() -> None:
    graph = _graph(
        pages={
            ROOT_PAGE: _page(
                ROOT_PAGE,
                status=StatusValue(StatusPrefix.CLOSED, "导航"),
                links=(_link("pages/a.md"), _link("conclusions.md")),
            ),
            A: _page(
                A,
                status=StatusValue(StatusPrefix.PENDING, None),
                relations=(_relation(RelationLabel.PARENT, "../index.md"),),
                links=(_link("../index.md"),),
            ),
            CONCLUSIONS: _page(
                CONCLUSIONS,
                status=StatusValue(StatusPrefix.CONFIRMED, "汇总"),
                relations=(_relation(RelationLabel.PARENT, "../index.md"),),
                links=(_link("../index.md"),),
            ),
        },
        parents={A: ROOT_PAGE, CONCLUSIONS: ROOT_PAGE},
        children={ROOT_PAGE: (A, CONCLUSIONS)},
        relations={
            RelationLabel.PARENT: (
                (A, ROOT_PAGE, 1),
                (CONCLUSIONS, ROOT_PAGE, 1),
            ),
        },
    )
    assert check_graph(graph).issues == ()


# ---------------------------------------------------------------------------
# _check_parents
# ---------------------------------------------------------------------------


def test_root_page_declaring_parent_is_rejected() -> None:
    graph = _graph(
        pages={
            ROOT_PAGE: _page(
                ROOT_PAGE,
                relations=(_relation(RelationLabel.PARENT, "pages/a.md"),),
            ),
        },
        relations={RelationLabel.PARENT: ((ROOT_PAGE, A, 1),)},
    )
    assert _issue(CheckCode.PARENT_ROOT_DECLARED.value, ROOT_PAGE, 1) in check_graph(graph).issues


def test_non_root_page_without_parent_is_missing() -> None:
    graph = _graph(pages={ROOT_PAGE: _page(ROOT_PAGE), A: _page(A)})
    assert _issue(CheckCode.PARENT_MISSING.value, A) in check_graph(graph).issues


def test_non_root_page_with_multiple_parents_is_rejected() -> None:
    graph = _graph(
        pages={
            ROOT_PAGE: _page(ROOT_PAGE),
            A: _page(
                A,
                relations=(
                    _relation(RelationLabel.PARENT, "../index.md", line=1),
                    _relation(RelationLabel.PARENT, "b.md", line=2),
                ),
            ),
        },
        relations={RelationLabel.PARENT: ((A, ROOT_PAGE, 1), (A, B, 2))},
    )
    assert _issue(CheckCode.PARENT_MULTIPLE.value, A, 2) in check_graph(graph).issues


def test_declared_parent_mismatching_physical_parent_is_rejected() -> None:
    # pages/a.md 的物理父目录是 index.md，但声明指向 pages/b.md。
    graph = _graph(
        pages={
            ROOT_PAGE: _page(ROOT_PAGE),
            A: _page(
                A,
                relations=(_relation(RelationLabel.PARENT, "b.md", line=3),),
                links=(_link("b.md"),),
            ),
            B: _page(B),
        },
        parents={A: B},
        children={B: (A,)},
        relations={RelationLabel.PARENT: ((A, B, 3),)},
    )
    assert (
        _issue(CheckCode.LAYOUT_PARENT_MISMATCH.value, A, 3, str(B), str(ROOT_PAGE))
        in check_graph(graph).issues
    )


def test_parent_cycle_is_reported_with_canonical_rotation() -> None:
    # a -> b -> a 构成父主题环。
    graph = _graph(
        pages={
            ROOT_PAGE: _page(ROOT_PAGE),
            A: _page(A, relations=(_relation(RelationLabel.PARENT, "b.md", line=1),)),
            B: _page(B, relations=(_relation(RelationLabel.PARENT, "a.md", line=1),)),
        },
        relations={
            RelationLabel.PARENT: ((A, B, 1), (B, A, 1)),
        },
    )
    issues = check_graph(graph).issues
    cycle = next(i for i in issues if i.code == CheckCode.PARENT_CYCLE.value)
    # 环规范化为从最小节点开始的闭合路径。
    assert cycle.path == A
    assert cycle.details == ("pages/a.md", "pages/b.md", "pages/a.md")


def test_unreachable_page_is_reported() -> None:
    graph = _graph(
        pages={
            ROOT_PAGE: _page(ROOT_PAGE),
            A: _page(A, relations=(_relation(RelationLabel.PARENT, "../index.md"),)),
            B: _page(B, relations=(_relation(RelationLabel.PARENT, "../index.md"),)),
        },
        parents={A: ROOT_PAGE, B: ROOT_PAGE},
        children={ROOT_PAGE: (A,)},  # B 不可达
        relations={
            RelationLabel.PARENT: ((A, ROOT_PAGE, 1), (B, ROOT_PAGE, 1)),
        },
    )
    assert _issue(CheckCode.PARENT_UNREACHABLE.value, B) in check_graph(graph).issues


def test_parent_navigation_missing_when_parent_does_not_link_child() -> None:
    graph = _graph(
        pages={
            ROOT_PAGE: _page(ROOT_PAGE),  # 没有指向 a.md 的正文链接
            A: _page(A, relations=(_relation(RelationLabel.PARENT, "../index.md"),)),
        },
        parents={A: ROOT_PAGE},
        children={ROOT_PAGE: (A,)},
        relations={RelationLabel.PARENT: ((A, ROOT_PAGE, 1),)},
    )
    assert (
        _issue(CheckCode.PARENT_NAVIGATION_MISSING.value, ROOT_PAGE, None, str(A))
        in check_graph(graph).issues
    )


def test_empty_hub_index_is_reported() -> None:
    # pages/sub/index.md 名为 index.md 但不是根索引，且没有子页 -> hub.empty
    graph = _graph(
        pages={
            ROOT_PAGE: _page(ROOT_PAGE, links=(_link("pages/sub/index.md"),)),
            SUB_INDEX: _page(
                SUB_INDEX, relations=(_relation(RelationLabel.PARENT, "../../index.md"),)
            ),
        },
        parents={SUB_INDEX: ROOT_PAGE},
        children={ROOT_PAGE: (SUB_INDEX,)},
        relations={RelationLabel.PARENT: ((SUB_INDEX, ROOT_PAGE, 1),)},
    )
    assert _issue(CheckCode.HUB_EMPTY.value, SUB_INDEX) in check_graph(graph).issues


# ---------------------------------------------------------------------------
# _check_relation_duplicates
# ---------------------------------------------------------------------------


def test_duplicate_non_parent_relation_is_reported() -> None:
    graph = _graph(
        pages={
            ROOT_PAGE: _page(ROOT_PAGE),
            A: _page(
                A,
                relations=(_relation(RelationLabel.PARENT, "../index.md"),),
                links=(
                    _relation(RelationLabel.DEPENDS_ON, "b.md", line=2).link,
                    _relation(RelationLabel.DEPENDS_ON, "b.md", line=3).link,
                ),
            ),
            B: _page(B),
        },
        parents={A: ROOT_PAGE},
        children={ROOT_PAGE: (A,)},
        relations={
            RelationLabel.PARENT: ((A, ROOT_PAGE, 1),),
            RelationLabel.DEPENDS_ON: ((A, B, 2), (A, B, 3)),
        },
    )
    assert (
        _issue(CheckCode.RELATION_DUPLICATE.value, A, 3, RelationLabel.DEPENDS_ON.value, str(B))
        in check_graph(graph).issues
    )


# ---------------------------------------------------------------------------
# _check_dependencies
# ---------------------------------------------------------------------------


def test_dependency_self_is_rejected() -> None:
    graph = _graph(
        pages={
            ROOT_PAGE: _page(ROOT_PAGE),
            A: _page(A, relations=(_relation(RelationLabel.PARENT, "../index.md"),)),
        },
        parents={A: ROOT_PAGE},
        children={ROOT_PAGE: (A,)},
        relations={
            RelationLabel.PARENT: ((A, ROOT_PAGE, 1),),
            RelationLabel.DEPENDS_ON: ((A, A, 4),),
        },
    )
    assert _issue(CheckCode.DEPENDENCY_SELF.value, A, 4) in check_graph(graph).issues


def test_dependency_cycle_is_reported() -> None:
    graph = _graph(
        pages={
            ROOT_PAGE: _page(ROOT_PAGE),
            A: _page(A, relations=(_relation(RelationLabel.PARENT, "../index.md"),)),
            B: _page(B, relations=(_relation(RelationLabel.PARENT, "../index.md"),)),
        },
        parents={A: ROOT_PAGE, B: ROOT_PAGE},
        children={ROOT_PAGE: (A, B)},
        relations={
            RelationLabel.PARENT: ((A, ROOT_PAGE, 1), (B, ROOT_PAGE, 1)),
            RelationLabel.DEPENDS_ON: ((A, B, 1), (B, A, 1)),
        },
    )
    issues = check_graph(graph).issues
    cycle = next(i for i in issues if i.code == CheckCode.DEPENDENCY_CYCLE.value)
    assert cycle.path == A
    assert cycle.details == ("pages/a.md", "pages/b.md", "pages/a.md")


# ---------------------------------------------------------------------------
# _check_replacements
# ---------------------------------------------------------------------------


def test_replacement_pair_missing_when_reverse_absent() -> None:
    graph = _graph(
        pages={
            ROOT_PAGE: _page(ROOT_PAGE),
            A: _page(A, relations=(_relation(RelationLabel.PARENT, "../index.md"),)),
            B: _page(B, relations=(_relation(RelationLabel.PARENT, "../index.md"),)),
        },
        parents={A: ROOT_PAGE, B: ROOT_PAGE},
        children={ROOT_PAGE: (A, B)},
        relations={
            RelationLabel.PARENT: ((A, ROOT_PAGE, 1), (B, ROOT_PAGE, 1)),
            RelationLabel.SUPERSEDES: ((A, B, 2),),
        },
    )
    assert (
        _issue(
            CheckCode.REPLACEMENT_PAIR_MISSING.value,
            A,
            2,
            str(B),
            RelationLabel.SUPERSEDED_BY.value,
        )
        in check_graph(graph).issues
    )


def test_replacement_status_mismatch_relation_without_status() -> None:
    graph = _graph(
        pages={
            ROOT_PAGE: _page(ROOT_PAGE),
            A: _page(
                A,
                status=StatusValue(StatusPrefix.PENDING, None),
                relations=(
                    _relation(RelationLabel.PARENT, "../index.md"),
                    _relation(RelationLabel.SUPERSEDED_BY, "b.md", line=5),
                ),
            ),
            B: _page(B, relations=(_relation(RelationLabel.PARENT, "../index.md"),)),
        },
        parents={A: ROOT_PAGE, B: ROOT_PAGE},
        children={ROOT_PAGE: (A, B)},
        relations={
            RelationLabel.PARENT: ((A, ROOT_PAGE, 1), (B, ROOT_PAGE, 1)),
            RelationLabel.SUPERSEDES: ((B, A, 5),),
            RelationLabel.SUPERSEDED_BY: ((A, B, 5),),
        },
    )
    assert (
        _issue(
            CheckCode.REPLACEMENT_STATUS_MISMATCH.value,
            A,
            5,
            ReplacementStatusReason.RELATION_REQUIRES_STATUS.value,
        )
        in check_graph(graph).issues
    )


def test_replacement_status_mismatch_status_without_relation() -> None:
    graph = _graph(
        pages={
            ROOT_PAGE: _page(ROOT_PAGE),
            A: _page(
                A,
                status=StatusValue(StatusPrefix.SUPERSEDED, "旧"),
                relations=(_relation(RelationLabel.PARENT, "../index.md"),),
            ),
        },
        parents={A: ROOT_PAGE},
        children={ROOT_PAGE: (A,)},
        relations={RelationLabel.PARENT: ((A, ROOT_PAGE, 1),)},
    )
    assert (
        _issue(
            CheckCode.REPLACEMENT_STATUS_MISMATCH.value,
            A,
            None,
            ReplacementStatusReason.STATUS_REQUIRES_RELATION.value,
        )
        in check_graph(graph).issues
    )


# ---------------------------------------------------------------------------
# _check_challenges
# ---------------------------------------------------------------------------


def test_challenge_pair_missing_when_reverse_absent() -> None:
    graph = _graph(
        pages={
            ROOT_PAGE: _page(ROOT_PAGE),
            A: _page(A, relations=(_relation(RelationLabel.PARENT, "../index.md"),)),
            B: _page(B, relations=(_relation(RelationLabel.PARENT, "../index.md"),)),
        },
        parents={A: ROOT_PAGE, B: ROOT_PAGE},
        children={ROOT_PAGE: (A, B)},
        relations={
            RelationLabel.PARENT: ((A, ROOT_PAGE, 1), (B, ROOT_PAGE, 1)),
            RelationLabel.CHALLENGES: ((A, B, 2),),
        },
    )
    assert (
        _issue(
            CheckCode.CHALLENGE_PAIR_MISSING.value, A, 2, str(B), RelationLabel.CHALLENGED_BY.value
        )
        in check_graph(graph).issues
    )


# ---------------------------------------------------------------------------
# _check_outputs
# ---------------------------------------------------------------------------


def test_confirmed_page_without_output_is_missing() -> None:
    graph = _graph(
        pages={
            ROOT_PAGE: _page(ROOT_PAGE, links=(_link("pages/a.md"),)),
            A: _page(
                A,
                status=StatusValue(StatusPrefix.CONFIRMED, "ok"),
                relations=(_relation(RelationLabel.PARENT, "../index.md"),),
            ),
        },
        parents={A: ROOT_PAGE},
        children={ROOT_PAGE: (A,)},
        relations={RelationLabel.PARENT: ((A, ROOT_PAGE, 1),)},
    )
    assert _issue(CheckCode.OUTPUT_MISSING.value, A) in check_graph(graph).issues


def test_non_confirmed_page_with_output_relation_is_forbidden() -> None:
    graph = _graph(
        pages={
            ROOT_PAGE: _page(ROOT_PAGE),
            A: _page(
                A,
                status=StatusValue(StatusPrefix.PENDING, None),
                relations=(
                    _relation(RelationLabel.PARENT, "../index.md"),
                    _relation(RelationLabel.OUTPUT, "../conclusions.md", line=6),
                ),
            ),
            CONCLUSIONS: _page(
                CONCLUSIONS,
                status=StatusValue(StatusPrefix.CONFIRMED, "汇总"),
                relations=(_relation(RelationLabel.PARENT, "../index.md"),),
            ),
        },
        parents={A: ROOT_PAGE, CONCLUSIONS: ROOT_PAGE},
        children={ROOT_PAGE: (A, CONCLUSIONS)},
        relations={
            RelationLabel.PARENT: ((A, ROOT_PAGE, 1), (CONCLUSIONS, ROOT_PAGE, 1)),
            RelationLabel.OUTPUT: ((A, CONCLUSIONS, 6),),
        },
    )
    assert _issue(CheckCode.OUTPUT_FORBIDDEN.value, A, 6) in check_graph(graph).issues


def test_confirmed_page_with_wrong_output_target_is_rejected() -> None:
    graph = _graph(
        pages={
            ROOT_PAGE: _page(ROOT_PAGE),
            A: _page(
                A,
                status=StatusValue(StatusPrefix.CONFIRMED, "ok"),
                relations=(
                    _relation(RelationLabel.PARENT, "../index.md"),
                    _relation(RelationLabel.OUTPUT, "b.md", line=7),
                ),
            ),
            B: _page(B, relations=(_relation(RelationLabel.PARENT, "../index.md"),)),
        },
        parents={A: ROOT_PAGE, B: ROOT_PAGE},
        children={ROOT_PAGE: (A, B)},
        relations={
            RelationLabel.PARENT: ((A, ROOT_PAGE, 1), (B, ROOT_PAGE, 1)),
            RelationLabel.OUTPUT: ((A, B, 7),),
        },
    )
    assert (
        _issue(
            CheckCode.OUTPUT_WRONG_TARGET.value,
            A,
            7,
            WrongOutputReason.NOT_CONCLUSIONS.value,
            "b.md",
        )
        in check_graph(graph).issues
    )


def test_confirmed_output_without_backlink_is_rejected() -> None:
    graph = _graph(
        pages={
            ROOT_PAGE: _page(ROOT_PAGE),
            A: _page(
                A,
                status=StatusValue(StatusPrefix.CONFIRMED, "ok"),
                relations=(
                    _relation(RelationLabel.PARENT, "../index.md"),
                    _relation(RelationLabel.OUTPUT, "../conclusions.md", line=6),
                ),
            ),
            CONCLUSIONS: _page(
                CONCLUSIONS,
                status=StatusValue(StatusPrefix.CONFIRMED, "汇总"),
                relations=(_relation(RelationLabel.PARENT, "../index.md"),),
                # 没有指向 a.md 的正文链接
            ),
        },
        parents={A: ROOT_PAGE, CONCLUSIONS: ROOT_PAGE},
        children={ROOT_PAGE: (A, CONCLUSIONS)},
        relations={
            RelationLabel.PARENT: ((A, ROOT_PAGE, 1), (CONCLUSIONS, ROOT_PAGE, 1)),
            RelationLabel.OUTPUT: ((A, CONCLUSIONS, 6),),
        },
    )
    assert (
        _issue(CheckCode.OUTPUT_BACKLINK_MISSING.value, CONCLUSIONS, None, str(A))
        in check_graph(graph).issues
    )


# ---------------------------------------------------------------------------
# _check_capacity
# ---------------------------------------------------------------------------


def test_oversized_page_is_a_review_not_an_error() -> None:
    # 超过行数上限（600）的页面产生 REVIEW，而非 ERROR。
    big_text = "\n".join("line" for _ in range(700))
    graph = _graph(
        pages={
            ROOT_PAGE: _page(ROOT_PAGE),
            A: _page(
                A,
                status=StatusValue(StatusPrefix.PENDING, None),
                relations=(_relation(RelationLabel.PARENT, "../index.md"),),
                text=big_text,
            ),
        },
        parents={A: ROOT_PAGE},
        children={ROOT_PAGE: (A,)},
        relations={RelationLabel.PARENT: ((A, ROOT_PAGE, 1),)},
    )
    report = check_graph(graph)
    review = next(i for i in report.issues if i.code == CheckCode.PAGE_CAPACITY.value)
    assert review.severity is IssueSeverity.REVIEW
    assert review.path == A


# ---------------------------------------------------------------------------
# 稳定排序：两个独立错误比较完整 CheckIssue 序列
# ---------------------------------------------------------------------------


def test_two_independent_errors_preserve_pass_order_and_sort() -> None:
    graph = _graph(
        pages={
            ROOT_PAGE: _page(
                ROOT_PAGE,
                status=StatusValue(StatusPrefix.CLOSED, "导航"),
                links=(_link("pages/a.md"), _link("conclusions.md")),
            ),
            A: _page(
                A,
                status=StatusValue(StatusPrefix.CONFIRMED, "ok"),
                relations=(_relation(RelationLabel.PARENT, "../index.md"),),
                links=(_link("../index.md"),),
            ),
            CONCLUSIONS: _page(
                CONCLUSIONS,
                status=StatusValue(StatusPrefix.CONFIRMED, "汇总"),
                relations=(_relation(RelationLabel.PARENT, "../index.md"),),
                links=(_link("../index.md"),),
            ),
        },
        parents={A: ROOT_PAGE, CONCLUSIONS: ROOT_PAGE},
        children={ROOT_PAGE: (A, CONCLUSIONS)},
        relations={
            RelationLabel.PARENT: ((A, ROOT_PAGE, 1), (CONCLUSIONS, ROOT_PAGE, 1)),
            # 依赖自环：来自依赖检查 pass
            RelationLabel.DEPENDS_ON: ((A, A, 5),),
        },
    )
    # a.md 已确认但无产出声明 -> output.missing（产出 pass 在依赖 pass 之后）
    assert check_graph(graph).issues == (
        _issue(CheckCode.DEPENDENCY_SELF.value, A, 5),
        _issue(CheckCode.OUTPUT_MISSING.value, A),
    )
