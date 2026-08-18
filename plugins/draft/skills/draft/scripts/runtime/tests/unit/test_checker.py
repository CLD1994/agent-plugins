from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from draft.checker import (
    InternalDirectoryState,
    OperationCheckIndex,
    PackageOwnership,
    check_graph,
    package_ownership_decision,
)
from draft.diagnostic_codes import CheckCode, ReplacementStatusReason
from draft.graph import ROOT_PAGE, DraftGraph
from draft.models import (
    CheckIssue,
    IssueSeverity,
    MarkdownLink,
    OperationKind,
    OperationPhase,
    PageDocument,
    Relation,
    RootState,
    SourceSpan,
    StatusValue,
)
from draft.operations import Journal
from draft.vocabulary import RelationCode, StatusCode

ROOT = Path("/draft")
CONCLUSIONS = Path("conclusions.md")
A = Path("pages/a.md")
B = Path("pages/b.md")
SUB_INDEX = Path("pages/sub/index.md")
_PENDING = StatusValue(StatusCode.PENDING, None)


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


def _relation(label: RelationCode, destination: str, *, line: int = 1) -> Relation:
    return Relation(code=label, link=_link(destination, line=line))


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
    relations: Mapping[RelationCode, tuple[tuple[Path, Path, int], ...]] | None = None,
    root: Path = ROOT,
) -> DraftGraph:
    return DraftGraph(
        root=root,
        pages=dict(pages or {}),
        parents=dict(parents or {}),
        children=dict(children or {}),
        inbound_links=dict(inbound_links or {}),
        relations={label: () for label in RelationCode} | dict(relations or {}),
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
                status=StatusValue(StatusCode.CLOSED, "导航"),
                links=(_link("pages/a.md"), _link("conclusions.md")),
            ),
            A: _page(
                A,
                status=StatusValue(StatusCode.PENDING, None),
                relations=(_relation(RelationCode.PARENT, "../index.md"),),
                links=(_link("../index.md"),),
            ),
            CONCLUSIONS: _page(
                CONCLUSIONS,
                status=StatusValue(StatusCode.CONFIRMED, "汇总"),
                relations=(_relation(RelationCode.PARENT, "../index.md"),),
                links=(_link("../index.md"),),
            ),
        },
        parents={A: ROOT_PAGE, CONCLUSIONS: ROOT_PAGE},
        children={ROOT_PAGE: (A, CONCLUSIONS)},
        relations={
            RelationCode.PARENT: (
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
                relations=(_relation(RelationCode.PARENT, "pages/a.md"),),
            ),
        },
        relations={RelationCode.PARENT: ((ROOT_PAGE, A, 1),)},
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
                    _relation(RelationCode.PARENT, "../index.md", line=1),
                    _relation(RelationCode.PARENT, "b.md", line=2),
                ),
            ),
        },
        relations={RelationCode.PARENT: ((A, ROOT_PAGE, 1), (A, B, 2))},
    )
    assert _issue(CheckCode.PARENT_MULTIPLE.value, A, 2) in check_graph(graph).issues


def test_declared_parent_mismatching_physical_parent_is_rejected() -> None:
    # pages/a.md 的物理父目录是 index.md，但声明指向 pages/b.md。
    graph = _graph(
        pages={
            ROOT_PAGE: _page(ROOT_PAGE),
            A: _page(
                A,
                relations=(_relation(RelationCode.PARENT, "b.md", line=3),),
                links=(_link("b.md"),),
            ),
            B: _page(B),
        },
        parents={A: B},
        children={B: (A,)},
        relations={RelationCode.PARENT: ((A, B, 3),)},
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
            A: _page(A, relations=(_relation(RelationCode.PARENT, "b.md", line=1),)),
            B: _page(B, relations=(_relation(RelationCode.PARENT, "a.md", line=1),)),
        },
        relations={
            RelationCode.PARENT: ((A, B, 1), (B, A, 1)),
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
            A: _page(A, relations=(_relation(RelationCode.PARENT, "../index.md"),)),
            B: _page(B, relations=(_relation(RelationCode.PARENT, "../index.md"),)),
        },
        parents={A: ROOT_PAGE, B: ROOT_PAGE},
        children={ROOT_PAGE: (A,)},  # B 不可达
        relations={
            RelationCode.PARENT: ((A, ROOT_PAGE, 1), (B, ROOT_PAGE, 1)),
        },
    )
    assert _issue(CheckCode.PARENT_UNREACHABLE.value, B) in check_graph(graph).issues


def test_parent_navigation_missing_when_parent_does_not_link_child() -> None:
    graph = _graph(
        pages={
            ROOT_PAGE: _page(ROOT_PAGE),  # 没有指向 a.md 的正文链接
            A: _page(A, relations=(_relation(RelationCode.PARENT, "../index.md"),)),
        },
        parents={A: ROOT_PAGE},
        children={ROOT_PAGE: (A,)},
        relations={RelationCode.PARENT: ((A, ROOT_PAGE, 1),)},
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
                SUB_INDEX, relations=(_relation(RelationCode.PARENT, "../../index.md"),)
            ),
        },
        parents={SUB_INDEX: ROOT_PAGE},
        children={ROOT_PAGE: (SUB_INDEX,)},
        relations={RelationCode.PARENT: ((SUB_INDEX, ROOT_PAGE, 1),)},
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
                relations=(_relation(RelationCode.PARENT, "../index.md"),),
                links=(
                    _relation(RelationCode.DEPENDS_ON, "b.md", line=2).link,
                    _relation(RelationCode.DEPENDS_ON, "b.md", line=3).link,
                ),
            ),
            B: _page(B),
        },
        parents={A: ROOT_PAGE},
        children={ROOT_PAGE: (A,)},
        relations={
            RelationCode.PARENT: ((A, ROOT_PAGE, 1),),
            RelationCode.DEPENDS_ON: ((A, B, 2), (A, B, 3)),
        },
    )
    assert (
        _issue(CheckCode.RELATION_DUPLICATE.value, A, 3, RelationCode.DEPENDS_ON.value, str(B))
        in check_graph(graph).issues
    )


# ---------------------------------------------------------------------------
# _check_dependencies
# ---------------------------------------------------------------------------


def test_dependency_self_is_rejected() -> None:
    graph = _graph(
        pages={
            ROOT_PAGE: _page(ROOT_PAGE),
            A: _page(A, relations=(_relation(RelationCode.PARENT, "../index.md"),)),
        },
        parents={A: ROOT_PAGE},
        children={ROOT_PAGE: (A,)},
        relations={
            RelationCode.PARENT: ((A, ROOT_PAGE, 1),),
            RelationCode.DEPENDS_ON: ((A, A, 4),),
        },
    )
    assert _issue(CheckCode.DEPENDENCY_SELF.value, A, 4) in check_graph(graph).issues


def test_dependency_cycle_is_reported() -> None:
    graph = _graph(
        pages={
            ROOT_PAGE: _page(ROOT_PAGE),
            A: _page(A, relations=(_relation(RelationCode.PARENT, "../index.md"),)),
            B: _page(B, relations=(_relation(RelationCode.PARENT, "../index.md"),)),
        },
        parents={A: ROOT_PAGE, B: ROOT_PAGE},
        children={ROOT_PAGE: (A, B)},
        relations={
            RelationCode.PARENT: ((A, ROOT_PAGE, 1), (B, ROOT_PAGE, 1)),
            RelationCode.DEPENDS_ON: ((A, B, 1), (B, A, 1)),
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
            A: _page(A, relations=(_relation(RelationCode.PARENT, "../index.md"),)),
            B: _page(B, relations=(_relation(RelationCode.PARENT, "../index.md"),)),
        },
        parents={A: ROOT_PAGE, B: ROOT_PAGE},
        children={ROOT_PAGE: (A, B)},
        relations={
            RelationCode.PARENT: ((A, ROOT_PAGE, 1), (B, ROOT_PAGE, 1)),
            RelationCode.SUPERSEDES: ((A, B, 2),),
        },
    )
    assert (
        _issue(
            CheckCode.REPLACEMENT_PAIR_MISSING.value,
            A,
            2,
            str(B),
            RelationCode.SUPERSEDED_BY.value,
        )
        in check_graph(graph).issues
    )


def test_replacement_status_mismatch_relation_without_status() -> None:
    graph = _graph(
        pages={
            ROOT_PAGE: _page(ROOT_PAGE),
            A: _page(
                A,
                status=StatusValue(StatusCode.PENDING, None),
                relations=(
                    _relation(RelationCode.PARENT, "../index.md"),
                    _relation(RelationCode.SUPERSEDED_BY, "b.md", line=5),
                ),
            ),
            B: _page(B, relations=(_relation(RelationCode.PARENT, "../index.md"),)),
        },
        parents={A: ROOT_PAGE, B: ROOT_PAGE},
        children={ROOT_PAGE: (A, B)},
        relations={
            RelationCode.PARENT: ((A, ROOT_PAGE, 1), (B, ROOT_PAGE, 1)),
            RelationCode.SUPERSEDES: ((B, A, 5),),
            RelationCode.SUPERSEDED_BY: ((A, B, 5),),
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
                status=StatusValue(StatusCode.SUPERSEDED, "旧"),
                relations=(_relation(RelationCode.PARENT, "../index.md"),),
            ),
        },
        parents={A: ROOT_PAGE},
        children={ROOT_PAGE: (A,)},
        relations={RelationCode.PARENT: ((A, ROOT_PAGE, 1),)},
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
            A: _page(A, relations=(_relation(RelationCode.PARENT, "../index.md"),)),
            B: _page(B, relations=(_relation(RelationCode.PARENT, "../index.md"),)),
        },
        parents={A: ROOT_PAGE, B: ROOT_PAGE},
        children={ROOT_PAGE: (A, B)},
        relations={
            RelationCode.PARENT: ((A, ROOT_PAGE, 1), (B, ROOT_PAGE, 1)),
            RelationCode.CHALLENGES: ((A, B, 2),),
        },
    )
    assert (
        _issue(
            CheckCode.CHALLENGE_PAIR_MISSING.value, A, 2, str(B), RelationCode.CHALLENGED_BY.value
        )
        in check_graph(graph).issues
    )


# ---------------------------------------------------------------------------
# output 协议删除：confirmed 状态本身已经足够，无需产出声明
# ---------------------------------------------------------------------------


def test_confirmed_page_without_output_is_valid() -> None:
    """已确认页面不再要求产出声明：`confirmed` 状态本身决定结论归属。"""
    graph = _graph(
        pages={
            ROOT_PAGE: _page(ROOT_PAGE, links=(_link("pages/a.md"),)),
            A: _page(
                A,
                status=StatusValue(StatusCode.CONFIRMED, "ok"),
                relations=(_relation(RelationCode.PARENT, "../index.md"),),
            ),
        },
        parents={A: ROOT_PAGE},
        children={ROOT_PAGE: (A,)},
        relations={RelationCode.PARENT: ((A, ROOT_PAGE, 1),)},
    )
    assert check_graph(graph).issues == ()


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
                status=StatusValue(StatusCode.PENDING, None),
                relations=(_relation(RelationCode.PARENT, "../index.md"),),
                text=big_text,
            ),
        },
        parents={A: ROOT_PAGE},
        children={ROOT_PAGE: (A,)},
        relations={RelationCode.PARENT: ((A, ROOT_PAGE, 1),)},
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
                status=StatusValue(StatusCode.CLOSED, "导航"),
                links=(_link("pages/a.md"), _link("conclusions.md")),
            ),
            A: _page(
                A,
                status=StatusValue(StatusCode.CONFIRMED, "ok"),
                relations=(_relation(RelationCode.PARENT, "../index.md"),),
                links=(_link("../index.md"),),
            ),
            CONCLUSIONS: _page(
                CONCLUSIONS,
                status=StatusValue(StatusCode.CONFIRMED, "汇总"),
                relations=(_relation(RelationCode.PARENT, "../index.md"),),
                links=(_link("../index.md"),),
            ),
        },
        parents={A: ROOT_PAGE, CONCLUSIONS: ROOT_PAGE},
        children={ROOT_PAGE: (A, CONCLUSIONS)},
        relations={
            RelationCode.PARENT: ((A, ROOT_PAGE, 1), (CONCLUSIONS, ROOT_PAGE, 1)),
            # 依赖自环：来自依赖检查 pass
            RelationCode.DEPENDS_ON: ((A, A, 5),),
        },
    )
    assert check_graph(graph).issues == (_issue(CheckCode.DEPENDENCY_SELF.value, A, 5),)


# ---------------------------------------------------------------------------
# 第十三轮 M1：package ownership 三值决策（HELD / ORPHAN / UNKNOWN）。
# 期望矩阵从状态分类独立推导，不得由生产函数反向生成。
# ---------------------------------------------------------------------------


def _op_journal(
    operation_id: str,
    *,
    kind: OperationKind,
    phase: OperationPhase,
    metadata: dict[str, str] | None = None,
) -> Journal:
    from draft.observations import EMPTY_OBSERVATIONS

    return Journal(
        operation_id=operation_id,
        kind=kind,
        phase=phase,
        root_state=RootState.REGISTERED,
        observations=EMPTY_OBSERVATIONS,
        mutations=(),
        remove_empty_directories=(),
        metadata=dict(metadata or {}),
        directory=Path("/draft/.draft/operations") / operation_id,
    )


def _ownership_index(
    *,
    listing_state: InternalDirectoryState | None = None,
    journals: dict[str, Journal] | None = None,
    unresolved_ids: tuple[str, ...] = (),
    locked_operation: str | None = None,
    lock_known: bool = True,
) -> OperationCheckIndex:
    return OperationCheckIndex(
        listing_state=listing_state or InternalDirectoryState.COMPLETE,
        journals=journals or {},
        unresolved_ids=frozenset(unresolved_ids),
        locked_operation=locked_operation,
        lock_known=lock_known,
    )


def test_package_ownership_decision_matrix() -> None:
    """三值 ownership 决策：HELD/ORPHAN/UNKNOWN 分区完整（round-13 M1）。"""
    trash_id = "a" * 32
    restore_id = "b" * 32
    retained = _op_journal(trash_id, kind=OperationKind.TRASH, phase=OperationPhase.TRASH_RETAINED)
    pending = _op_journal(
        trash_id, kind=OperationKind.TRASH, phase=OperationPhase.TRASH_HANDOFF_PENDING
    )
    rolled_back = _op_journal(
        trash_id,
        kind=OperationKind.TRASH,
        phase=OperationPhase.ROLLED_BACK,
        metadata={"restore_operation_id": restore_id},
    )
    cleanup_restore = _op_journal(
        restore_id,
        kind=OperationKind.RESTORE,
        phase=OperationPhase.COMPLETED,
        metadata={"restores_trash_operation": trash_id},
    )
    wrong_kind = _op_journal(trash_id, kind=OperationKind.CREATE, phase=OperationPhase.COMPLETED)
    cases: list[tuple[str, OperationCheckIndex, PackageOwnership]] = [
        # operations 明确缺失 -> 完整事实证明无 owner。
        (
            "operations_missing",
            _ownership_index(listing_state=InternalDirectoryState.MISSING),
            PackageOwnership.ORPHAN,
        ),
        # operations 不可枚举/不可 stat -> 无法证明 owner。
        (
            "operations_unavailable",
            _ownership_index(listing_state=InternalDirectoryState.UNAVAILABLE),
            PackageOwnership.UNKNOWN,
        ),
        (
            "operations_invalid",
            _ownership_index(listing_state=InternalDirectoryState.INVALID),
            PackageOwnership.UNKNOWN,
        ),
        # 完整枚举 + 同 id retained/pending T -> HELD。
        (
            "complete_retained_t",
            _ownership_index(journals={trash_id: retained}),
            PackageOwnership.HELD,
        ),
        (
            "complete_pending_t",
            _ownership_index(journals={trash_id: pending}),
            PackageOwnership.HELD,
        ),
        # 完整枚举 + 明确无同 id operation -> ORPHAN。
        ("complete_no_same_id", _ownership_index(), PackageOwnership.ORPHAN),
        # 同 id operation/journal 不可读 -> UNKNOWN。
        (
            "complete_same_id_unreadable",
            _ownership_index(unresolved_ids=(trash_id,)),
            PackageOwnership.UNKNOWN,
        ),
        # rolled_back + 完整 cleanup owner 证明 -> HELD。
        (
            "complete_rolled_back_cleanup_matched",
            _ownership_index(
                journals={trash_id: rolled_back, restore_id: cleanup_restore},
                locked_operation=restore_id,
                lock_known=True,
            ),
            PackageOwnership.HELD,
        ),
        # rolled_back + lock 不可读 -> 可能 cleanup owner，UNKNOWN。
        (
            "complete_rolled_back_lock_unreadable",
            _ownership_index(
                journals={trash_id: rolled_back},
                locked_operation=None,
                lock_known=False,
            ),
            PackageOwnership.UNKNOWN,
        ),
        # rolled_back + claim 指向的 R 不可读 -> UNKNOWN。
        (
            "complete_rolled_back_r_unreadable",
            _ownership_index(
                journals={trash_id: rolled_back},
                locked_operation=restore_id,
                unresolved_ids=(restore_id,),
                lock_known=True,
            ),
            PackageOwnership.UNKNOWN,
        ),
        # rolled_back + lock 可读但无锁 -> 完整事实证明无 owner，ORPHAN。
        (
            "complete_rolled_back_no_lock",
            _ownership_index(
                journals={trash_id: rolled_back},
                locked_operation=None,
                lock_known=True,
            ),
            PackageOwnership.ORPHAN,
        ),
        # 同 id journal/phase 不拥有包（错误 kind/相位）-> ORPHAN。
        (
            "complete_wrong_kind_or_phase",
            _ownership_index(journals={trash_id: wrong_kind}),
            PackageOwnership.ORPHAN,
        ),
    ]
    for label, index, expected in cases:
        actual = package_ownership_decision(trash_id, index)
        assert actual is expected, f"{label}: 期望 {expected}, 实际 {actual}"
