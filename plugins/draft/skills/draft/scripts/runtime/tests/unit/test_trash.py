from __future__ import annotations

import dataclasses
import json
import uuid as uuid_module
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from draft.diagnostic_codes import CheckCode
from draft.errors import DraftError
from draft.graph import (
    CONCLUSIONS_PAGE,
    ROOT_PAGE,
    DraftGraph,
    MarkerSnapshot,
    MarkerState,
    resolve_local_destination,
)
from draft.markdown import parse_page
from draft.models import (
    MarkdownLink,
    MutationKind,
    OperationKind,
    OperationPhase,
    OperationPlan,
    PageDocument,
    RootState,
    SourceSpan,
)
from draft.observations import EMPTY_OBSERVATIONS
from draft.operations import (
    Journal,
    JournalMutation,
    RestoreTrashAction,
    RestoreTrashRelation,
    TrashDeadlines,
    sha256_bytes,
)
from draft.resources import resolve_page_resources
from draft.trash import (
    RestoreLinkState,
    RestoreRecoveryAction,
    RestoreRecoveryFacts,
    require_retained_restore_window,
    restore_recovery_decision,
    restore_recovery_decision_from_facts,
    trash_plan,
)
from draft.trash_package import OUTGOING_RELATIVE, PACKAGE_VERSION
from draft.vocabulary import ConclusionsState, RelationCode

ROOT = Path("/draft")

# 合成图无真实文件系统：marker 观察使用显式构造的 registered snapshot（round-15 H1）。
_REGISTERED_BYTES = (
    b'{"format_version":1,"root_state":"registered","conclusions_state":"not_generated"}'
)
_REGISTERED_SNAPSHOT = MarkerSnapshot(
    state=MarkerState(
        version=1,
        root_state=RootState.REGISTERED,
        conclusions_state=ConclusionsState.NOT_GENERATED,
    ),
    raw_bytes=_REGISTERED_BYTES,
)
FIXED_OPERATION_ID = "12345678123456781234567812345678"
FIXED_UUID = uuid_module.UUID("12345678-1234-5678-1234-567812345678")


# ---------------------------------------------------------------------------
# 局部内存图构造器：从 Markdown 文本构造一致的 DraftGraph，不访问文件系统。
# ---------------------------------------------------------------------------


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
# 页面文本
# ---------------------------------------------------------------------------

_INDEX = """# 根

> 状态：`已关闭：纯导航页`

## 子主题

- [结论汇总](conclusions.md)
- [甲](pages/a.md)
"""

_CONCLUSIONS = """# 结论汇总

> 状态：`已确认`
>
> 父主题：[根](index.md)
"""

_A = """# 甲

> 状态：`待讨论`
>
> 父主题：[根](../index.md)

甲的正文。
"""

# 带额外正文链接的根（父阻碍：根有非规范导航链接到甲）
_INDEX_EXTRA_LINK = """# 根

> 状态：`已关闭：纯导航页`

## 子主题

- [结论汇总](conclusions.md)
- [甲](pages/a.md)

另见 [甲补充](pages/a.md)。
"""

# 带其他页链接到甲的图（入站引用阻碍）
_INDEX_WITH_B = """# 根

> 状态：`已关闭：纯导航页`

## 子主题

- [结论汇总](conclusions.md)
- [甲](pages/a.md)
- [乙](pages/b.md)
"""

_B_LINKS_A = """# 乙

> 状态：`待讨论`
>
> 父主题：[根](../index.md)

提到 [甲](a.md)。
"""

# 依赖阻碍图
_B_DEPENDS_ON_A = """# 乙

> 状态：`待讨论`
>
> 父主题：[根](../index.md)
>
> 依赖：[甲](a.md)

乙的正文。
"""

# 取代阻碍图
_B_SUPERSEDES_A = """# 乙

> 状态：`待讨论`
>
> 父主题：[根](../index.md)
>
> 取代：[甲](a.md)

乙的正文。
"""

# 质疑阻碍图
_B_CHALLENGES_A = """# 乙

> 状态：`待讨论`
>
> 父主题：[根](../index.md)
>
> 质疑：[甲](a.md)

乙的正文。
"""

# 子页阻碍图：甲有子页
_INDEX_WITH_HUB = """# 根

> 状态：`已关闭：纯导航页`

## 子主题

- [结论汇总](conclusions.md)
- [枢纽](pages/hub/index.md)
"""

_HUB = """# 枢纽

> 状态：`已关闭：纯导航页`
>
> 父主题：[根](../../index.md)

## 子主题

- [甲](a.md)
"""

_HUB_CHILD = """# 甲

> 状态：`待讨论`
>
> 父主题：[枢纽](index.md)

甲的正文。
"""

# 单人枢纽图：回收最后一个子页时折叠
_SOLO_INDEX = """# 根

> 状态：`已关闭：纯导航页`

## 子主题

- [结论汇总](conclusions.md)
- [单人枢纽](pages/solo/index.md)
"""

_SOLO_HUB = """# 单人枢纽

> 状态：`已关闭：纯导航页`
>
> 父主题：[根](../../index.md)

## 子主题

- [唯一](only.md)
"""

_SOLO_ONLY = """# 唯一

> 状态：`待讨论`
>
> 父主题：[单人枢纽](index.md)

唯一的正文。
"""


# ---------------------------------------------------------------------------
# Journal 构造助手
# ---------------------------------------------------------------------------


def _trash_journal(
    *,
    created_at: datetime,
    phase: OperationPhase = OperationPhase.TRASH_RETAINED,
    kind: OperationKind = OperationKind.TRASH,
    operation_id: str = "a" * 32,
) -> Journal:
    deadlines = TrashDeadlines.from_created_at(created_at)
    return Journal(
        operation_id=operation_id,
        kind=kind,
        phase=phase,
        root_state=RootState.REGISTERED,
        observations=EMPTY_OBSERVATIONS,
        mutations=(),
        remove_empty_directories=(),
        metadata=deadlines.to_metadata(),
        directory=Path("/draft/.draft/operations") / operation_id,
        link_index_revision=None,
        link_index_result=None,
        link_index_sha256=None,
    )


def _restore_recovery_facts(
    *,
    restore_phase: OperationPhase,
    trash_phase: OperationPhase = OperationPhase.TRASH_RETAINED,
    relation: RestoreTrashRelation,
    ownership_action: RestoreTrashAction,
    trash_metadata: dict[str, str] | None = None,
) -> RestoreRecoveryFacts:
    restore_id = "a" * 32
    trash_id = "b" * 32
    restore = Journal(
        operation_id=restore_id,
        kind=OperationKind.RESTORE,
        phase=restore_phase,
        root_state=RootState.REGISTERED,
        observations=EMPTY_OBSERVATIONS,
        mutations=(),
        remove_empty_directories=(),
        metadata={"restores_trash_operation": trash_id},
        directory=Path("/draft/.draft/operations") / restore_id,
        link_index_revision=None,
        link_index_result=None,
        link_index_sha256=None,
    )
    trash = Journal(
        operation_id=trash_id,
        kind=OperationKind.TRASH,
        phase=trash_phase,
        root_state=RootState.REGISTERED,
        observations=EMPTY_OBSERVATIONS,
        mutations=(),
        remove_empty_directories=(),
        metadata=dict(trash_metadata or {}),
        directory=Path("/draft/.draft/operations") / trash_id,
        link_index_revision=None,
        link_index_result=None,
        link_index_sha256=None,
    )
    return RestoreRecoveryFacts(
        restore=restore,
        trash=trash,
        relation=relation,
        owner_id=restore_id,
        participant_ids=(restore_id, trash_id),
        ownership_action=ownership_action,
    )


# ===========================================================================
# trash_plan: 合法规划
# ===========================================================================


def _trash_plan(graph: DraftGraph, page: Path) -> tuple[OperationPlan, Path]:
    from draft.models import RootState
    from draft.observations import graph_observations

    return trash_plan(
        graph,
        page,
        root_state=RootState.REGISTERED,
        observations=graph_observations(graph, marker=_REGISTERED_SNAPSHOT),
    )


def test_trash_plan_valid_leaf_produces_complete_plan(mocker: MockerFixture) -> None:
    """合法叶页回收：生成完整删除包、父导航移除、页面删除，比较完整 OperationPlan。"""
    graph = _graph_from_texts(
        {
            Path("index.md"): _INDEX,
            Path("conclusions.md"): _CONCLUSIONS,
            Path("pages/a.md"): _A,
        }
    )

    mocker.patch("draft.trash.uuid.uuid4", autospec=True, return_value=FIXED_UUID)
    plan, package_relative = _trash_plan(graph, Path("pages/a.md"))

    assert plan.kind is OperationKind.TRASH
    assert plan.root == ROOT
    assert plan.root_state is RootState.REGISTERED
    assert plan.metadata == {"operation_id": FIXED_OPERATION_ID}
    assert plan.remove_empty_directories == ()

    # 变更项先写后删，各自按路径排序
    kinds = [m.kind for m in plan.mutations]
    writes = [m for m in plan.mutations if m.kind is MutationKind.WRITE]
    deletes = [m for m in plan.mutations if m.kind is MutationKind.DELETE]
    write_count = len(writes)
    assert kinds[:write_count] == [MutationKind.WRITE] * write_count
    assert kinds[write_count:] == [MutationKind.DELETE] * len(deletes)
    assert [str(m.path) for m in writes] == sorted(str(m.path) for m in writes)
    assert [str(m.path) for m in deletes] == sorted(str(m.path) for m in deletes)

    # package_relative 指向权威删除包目录
    assert package_relative == OUTGOING_RELATIVE / FIXED_OPERATION_ID

    # 验证 package.json 内容
    package_path = OUTGOING_RELATIVE / FIXED_OPERATION_ID / "package.json"
    package_mutation = next(m for m in writes if m.path == package_path)
    assert package_mutation.before_sha256 is None
    assert package_mutation.content is not None
    package_json = json.loads(package_mutation.content.decode("utf-8"))
    assert package_json["version"] == PACKAGE_VERSION
    assert package_json["operation_id"] == FIXED_OPERATION_ID
    assert package_json["page_path"] == "pages/a.md"
    assert package_json["parent_path"] == "index.md"
    assert package_json["collapsed"] is False
    assert package_json["hub_path"] is None
    assert package_json["payload"] == "payload/pages/a.md"
    assert package_json["page_sha256"] == sha256_bytes(_A.encode("utf-8"))

    # 验证 payload 写入
    payload_path = OUTGOING_RELATIVE / FIXED_OPERATION_ID / "payload" / "pages/a.md"
    payload_mutation = next(m for m in writes if m.path == payload_path)
    assert payload_mutation.content == _A.encode("utf-8")
    assert payload_mutation.before_sha256 is None

    # 验证父页导航移除
    index_mutation = next(m for m in writes if m.path == Path("index.md"))
    expected_index = _INDEX.replace("- [甲](pages/a.md)\n", "")
    assert index_mutation.content == expected_index.encode("utf-8")
    assert index_mutation.before_sha256 == sha256_bytes(_INDEX.encode("utf-8"))
    assert index_mutation.after_sha256 == sha256_bytes(expected_index.encode("utf-8"))

    # 验证页面删除
    assert len(deletes) == 1
    assert deletes[0].path == Path("pages/a.md")
    assert deletes[0].before_sha256 == sha256_bytes(_A.encode("utf-8"))
    assert deletes[0].after_sha256 is None
    assert deletes[0].content is None


# ===========================================================================
# trash_plan: 折叠单人枢纽
# ===========================================================================


def test_trash_plan_last_child_collapses_single_child_hub(mocker: MockerFixture) -> None:
    """回收最后一个子页时，单人枢纽折叠为叶页，其他页链接重定向。"""
    graph = _graph_from_texts(
        {
            Path("index.md"): _SOLO_INDEX,
            Path("conclusions.md"): _CONCLUSIONS,
            Path("pages/solo/index.md"): _SOLO_HUB,
            Path("pages/solo/only.md"): _SOLO_ONLY,
        }
    )

    mocker.patch("draft.trash.uuid.uuid4", autospec=True, return_value=FIXED_UUID)
    plan, _ = _trash_plan(graph, Path("pages/solo/only.md"))

    mutation_paths = {m.path for m in plan.mutations}
    # 折叠后的叶页写入
    assert Path("pages/solo.md") in mutation_paths
    # 旧枢纽删除
    assert Path("pages/solo/index.md") in mutation_paths
    # 被回收页删除
    assert Path("pages/solo/only.md") in mutation_paths
    # 权威删除包写入
    package_path = OUTGOING_RELATIVE / FIXED_OPERATION_ID / "package.json"
    assert package_path in mutation_paths
    # 空目录清理
    assert Path("pages/solo") in plan.remove_empty_directories

    # package.json 记录折叠信息
    writes = [m for m in plan.mutations if m.kind is MutationKind.WRITE]
    package_mutation = next(m for m in writes if m.path == package_path)
    assert package_mutation.content is not None
    package_json = json.loads(package_mutation.content.decode("utf-8"))
    assert package_json["collapsed"] is True
    assert package_json["hub_path"] == "pages/solo/index.md"
    assert package_json["parent_path"] == "pages/solo.md"


# ===========================================================================
# trash_plan: 导航和链接重写
# ===========================================================================


def test_trash_plan_navigation_removal_removes_canonical_parent_link(
    mocker: MockerFixture,
) -> None:
    """回收移除父页中的规范导航项（整行独占则去整行）。"""
    graph = _graph_from_texts(
        {
            Path("index.md"): _INDEX,
            Path("conclusions.md"): _CONCLUSIONS,
            Path("pages/a.md"): _A,
        }
    )

    mocker.patch("draft.trash.uuid.uuid4", autospec=True, return_value=FIXED_UUID)
    plan, _ = _trash_plan(graph, Path("pages/a.md"))

    writes = [m for m in plan.mutations if m.kind is MutationKind.WRITE]
    index_mutation = next(m for m in writes if m.path == Path("index.md"))
    # 导航项整行被移除（- [甲](pages/a.md)\n 消失）
    assert index_mutation.content is not None
    index_text = index_mutation.content.decode("utf-8")
    assert "- [甲](pages/a.md)" not in index_text
    assert "- [结论汇总](conclusions.md)" in index_text


# ===========================================================================
# trash_plan: 确定排序
# ===========================================================================


def test_trash_plan_deterministic_mutation_ordering(mocker: MockerFixture) -> None:
    """变更项先写后删，各自按路径字典序排序。"""
    graph = _graph_from_texts(
        {
            Path("index.md"): _INDEX,
            Path("conclusions.md"): _CONCLUSIONS,
            Path("pages/a.md"): _A,
        }
    )

    mocker.patch("draft.trash.uuid.uuid4", autospec=True, return_value=FIXED_UUID)
    plan, _ = _trash_plan(graph, Path("pages/a.md"))

    kinds = [m.kind for m in plan.mutations]
    writes = [m for m in plan.mutations if m.kind is MutationKind.WRITE]
    deletes = [m for m in plan.mutations if m.kind is MutationKind.DELETE]
    write_count = len(writes)
    assert kinds[:write_count] == [MutationKind.WRITE] * write_count
    assert kinds[write_count:] == [MutationKind.DELETE] * len(deletes)
    assert [str(m.path) for m in writes] == sorted(str(m.path) for m in writes)
    assert [str(m.path) for m in deletes] == sorted(str(m.path) for m in deletes)


# ===========================================================================
# trash_plan: 受保护页拒绝
# ===========================================================================


def test_trash_plan_protected_root_raises() -> None:
    graph = _graph_from_texts(
        {
            Path("index.md"): _INDEX,
            Path("conclusions.md"): _CONCLUSIONS,
            Path("pages/a.md"): _A,
        }
    )
    with pytest.raises(DraftError) as caught:
        _trash_plan(graph, ROOT_PAGE)
    assert caught.value.code == "trash.protected"


def test_trash_plan_protected_conclusions_raises() -> None:
    graph = _graph_from_texts(
        {
            Path("index.md"): _INDEX,
            Path("conclusions.md"): _CONCLUSIONS,
            Path("pages/a.md"): _A,
        }
    )
    with pytest.raises(DraftError) as caught:
        _trash_plan(graph, CONCLUSIONS_PAGE)
    assert caught.value.code == "trash.protected"


def test_trash_plan_unknown_page_raises() -> None:
    graph = _graph_from_texts(
        {
            Path("index.md"): _INDEX,
            Path("conclusions.md"): _CONCLUSIONS,
            Path("pages/a.md"): _A,
        }
    )
    with pytest.raises(DraftError) as caught:
        _trash_plan(graph, Path("pages/missing.md"))
    assert caught.value.code == "structure.unknown_page"


# ===========================================================================
# trash_plan: 子页阻碍
# ===========================================================================


def test_trash_plan_has_children_raises() -> None:
    graph = _graph_from_texts(
        {
            Path("index.md"): _INDEX_WITH_HUB,
            Path("conclusions.md"): _CONCLUSIONS,
            Path("pages/hub/index.md"): _HUB,
            Path("pages/hub/a.md"): _HUB_CHILD,
        }
    )
    with pytest.raises(DraftError) as caught:
        _trash_plan(graph, Path("pages/hub/index.md"))
    assert caught.value.code == "trash.has_children"


# ===========================================================================
# trash_plan: 入站引用阻碍
# ===========================================================================


def test_trash_plan_parent_non_canonical_link_blocks() -> None:
    """父页有非规范导航链接到被回收页时，该入站引用阻碍回收。"""
    graph = _graph_from_texts(
        {
            Path("index.md"): _INDEX_EXTRA_LINK,
            Path("conclusions.md"): _CONCLUSIONS,
            Path("pages/a.md"): _A,
        }
    )
    with pytest.raises(DraftError) as caught:
        _trash_plan(graph, Path("pages/a.md"))
    assert caught.value.code == "trash.inbound_reference"
    details = " ".join(caught.value.details)
    assert "index.md" in details


def test_trash_plan_other_page_inbound_link_blocks() -> None:
    """其他页有正文链接到被回收页时，入站引用阻碍回收。"""
    graph = _graph_from_texts(
        {
            Path("index.md"): _INDEX_WITH_B,
            Path("conclusions.md"): _CONCLUSIONS,
            Path("pages/a.md"): _A,
            Path("pages/b.md"): _B_LINKS_A,
        }
    )
    with pytest.raises(DraftError) as caught:
        _trash_plan(graph, Path("pages/a.md"))
    assert caught.value.code == "trash.inbound_reference"
    details = " ".join(caught.value.details)
    assert "pages/b.md" in details


def test_trash_plan_dependency_relation_blocks() -> None:
    """其他页有依赖关系指向被回收页时，关系阻碍回收。"""
    graph = _graph_from_texts(
        {
            Path("index.md"): _INDEX_WITH_B,
            Path("conclusions.md"): _CONCLUSIONS,
            Path("pages/a.md"): _A,
            Path("pages/b.md"): _B_DEPENDS_ON_A,
        }
    )
    with pytest.raises(DraftError) as caught:
        _trash_plan(graph, Path("pages/a.md"))
    assert caught.value.code == "trash.inbound_reference"
    details = " ".join(caught.value.details)
    assert "pages/b.md" in details


def test_trash_plan_supersede_relation_blocks() -> None:
    """其他页有取代关系指向被回收页时，关系阻碍回收。"""
    graph = _graph_from_texts(
        {
            Path("index.md"): _INDEX_WITH_B,
            Path("conclusions.md"): _CONCLUSIONS,
            Path("pages/a.md"): _A,
            Path("pages/b.md"): _B_SUPERSEDES_A,
        }
    )
    with pytest.raises(DraftError) as caught:
        _trash_plan(graph, Path("pages/a.md"))
    assert caught.value.code == "trash.inbound_reference"
    details = " ".join(caught.value.details)
    assert "pages/b.md" in details


def test_trash_plan_challenge_relation_blocks() -> None:
    """其他页有质疑关系指向被回收页时，关系阻碍回收。"""
    graph = _graph_from_texts(
        {
            Path("index.md"): _INDEX_WITH_B,
            Path("conclusions.md"): _CONCLUSIONS,
            Path("pages/a.md"): _A,
            Path("pages/b.md"): _B_CHALLENGES_A,
        }
    )
    with pytest.raises(DraftError) as caught:
        _trash_plan(graph, Path("pages/a.md"))
    assert caught.value.code == "trash.inbound_reference"
    details = " ".join(caught.value.details)
    assert "pages/b.md" in details


def test_trash_plan_blocker_details_are_deterministic_and_sorted() -> None:
    """多个阻碍来源按确定顺序排列，错误码稳定。"""
    graph = _graph_from_texts(
        {
            Path("index.md"): _INDEX_WITH_B,
            Path("conclusions.md"): _CONCLUSIONS,
            Path("pages/a.md"): _A,
            Path("pages/b.md"): _B_LINKS_A,
        }
    )
    with pytest.raises(DraftError) as caught:
        _trash_plan(graph, Path("pages/a.md"))
    assert caught.value.code == "trash.inbound_reference"
    # 阻碍详情按 (source, offset) 排序
    details = caught.value.details
    assert details == tuple(sorted(details))


# ===========================================================================
# require_retained_restore_window: 恢复窗口边界
# ===========================================================================


CREATED = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)
RESTORE_EXPIRES = CREATED + timedelta(days=7)


def test_require_retained_restore_window_before_expiry_returns_deadlines() -> None:
    """恢复窗口内：返回完整期限。"""
    journal = _trash_journal(created_at=CREATED)
    now = RESTORE_EXPIRES - timedelta(microseconds=1)

    result = require_retained_restore_window(journal, now)

    assert result.created_at == CREATED
    assert result.restore_expires_at == RESTORE_EXPIRES
    assert result.purge_eligible_at == CREATED + timedelta(days=37)


def test_require_retained_restore_window_at_boundary_raises() -> None:
    """恰好到达恢复期限边界：now == restore_expires_at 时拒绝恢复。"""
    journal = _trash_journal(created_at=CREATED)

    with pytest.raises(DraftError) as caught:
        require_retained_restore_window(journal, RESTORE_EXPIRES)

    assert caught.value.code == "trash.restore_expired"


def test_require_retained_restore_window_after_expiry_raises() -> None:
    """恢复期限过后：拒绝恢复。"""
    journal = _trash_journal(created_at=CREATED)
    now = RESTORE_EXPIRES + timedelta(microseconds=1)

    with pytest.raises(DraftError) as caught:
        require_retained_restore_window(journal, now)

    assert caught.value.code == "trash.restore_expired"


def test_require_retained_restore_window_wrong_phase_raises() -> None:
    """非 trash_retained 阶段：拒绝恢复。"""
    journal = _trash_journal(
        created_at=CREATED,
        phase=OperationPhase.TRASH_HANDOFF_PENDING,
    )
    with pytest.raises(DraftError) as caught:
        require_retained_restore_window(journal, CREATED)
    assert caught.value.code == "trash.restore_expired"


def test_require_retained_restore_window_wrong_kind_raises() -> None:
    """非 trash 类型日志：拒绝恢复。"""
    journal = _trash_journal(created_at=CREATED, kind=OperationKind.RESTORE)
    with pytest.raises(DraftError) as caught:
        require_retained_restore_window(journal, CREATED)
    assert caught.value.code == "trash.restore_expired"


def test_require_retained_restore_window_returns_same_deadlines_for_any_valid_now() -> None:
    """恢复窗口内不同时刻返回相同的期限对象（期限在创建时冻结）。"""
    journal = _trash_journal(created_at=CREATED)
    early = CREATED + timedelta(hours=1)
    late = RESTORE_EXPIRES - timedelta(hours=1)

    early_result = require_retained_restore_window(journal, early)
    late_result = require_retained_restore_window(journal, late)

    assert early_result == late_result
    assert early_result.restore_expires_at == RESTORE_EXPIRES


# ---------------------------------------------------------------------------
# parse_package_authorization：T 日志包授权的纯语义解析（回归 H6）
# ---------------------------------------------------------------------------


def _auth_mutation(
    relative: Path,
    *,
    kind: MutationKind = MutationKind.WRITE,
    before: str | None = None,
    after: str | None = None,
    original: Path | None = None,
    staged: Path | None = None,
) -> JournalMutation:
    return JournalMutation(
        path=relative,
        kind=kind,
        before_sha256=before,
        after_sha256=after,
        original=original,
        staged=staged,
        applied=False,
    )


def _package_auth_journal(*mutations: JournalMutation) -> Journal:
    return Journal(
        operation_id=FIXED_OPERATION_ID,
        kind=OperationKind.TRASH,
        phase=OperationPhase.TRASH_RETAINED,
        root_state=RootState.REGISTERED,
        observations=EMPTY_OBSERVATIONS,
        mutations=tuple(mutations),
        remove_empty_directories=(),
        metadata={},
        directory=Path("/draft/.draft/operations") / FIXED_OPERATION_ID,
        link_index_revision=None,
        link_index_result=None,
        link_index_sha256=None,
    )


def _valid_package_auth_mutations() -> tuple[JournalMutation, ...]:
    pkg = OUTGOING_RELATIVE / FIXED_OPERATION_ID
    return (
        _auth_mutation(pkg / "package.json", after="b" * 64, staged=Path("staged/0000.bin")),
        _auth_mutation(pkg / "payload/pages/x.md", after="c" * 64, staged=Path("staged/0001.bin")),
        _auth_mutation(
            Path("index.md"),
            before="f" * 64,
            after="g" * 64,
            original=Path("originals/0002.bin"),
            staged=Path("staged/0002.bin"),
        ),
        _auth_mutation(
            Path("pages/x.md"),
            kind=MutationKind.DELETE,
            before="d" * 64,
            original=Path("originals/0003.bin"),
        ),
    )


def test_package_authorization_valid_trash_journal() -> None:
    from draft.trash_package import parse_package_authorization

    auth = parse_package_authorization(
        _package_auth_journal(*_valid_package_auth_mutations()), Path("/draft")
    )
    pkg = OUTGOING_RELATIVE / FIXED_OPERATION_ID
    assert auth.package_json_path == pkg / "package.json"
    assert auth.package_json_digest == "b" * 64
    assert auth.payload_path == pkg / "payload/pages/x.md"
    assert auth.payload_relative == Path("payload/pages/x.md")
    assert auth.payload_digest == "c" * 64


def test_package_authorization_accepts_collapsed_hub_deletes_and_leaf_write() -> None:
    from draft.trash_package import parse_package_authorization

    pkg = OUTGOING_RELATIVE / FIXED_OPERATION_ID
    mutations = (
        _auth_mutation(pkg / "package.json", after="b" * 64, staged=Path("staged/0.bin")),
        _auth_mutation(pkg / "payload/pages/x.md", after="c" * 64, staged=Path("staged/1.bin")),
        # 折叠集线叶：包外 before=None WRITE 恰好一个。
        _auth_mutation(Path("pages.md"), after="g" * 64, staged=Path("staged/2.bin")),
        _auth_mutation(
            Path("pages/x.md"),
            kind=MutationKind.DELETE,
            before="d" * 64,
            original=Path("originals/3.bin"),
        ),
        _auth_mutation(
            Path("pages/topic/index.md"),
            kind=MutationKind.DELETE,
            before="e" * 64,
            original=Path("originals/4.bin"),
        ),
    )
    auth = parse_package_authorization(_package_auth_journal(*mutations), Path("/draft"))
    assert auth.payload_relative == Path("payload/pages/x.md")


@pytest.mark.parametrize(
    "mutations",
    [
        # 空 mutation：终态恢复不得接受并写成 completed。
        (),
        # 缺 package write。
        (
            _auth_mutation(
                OUTGOING_RELATIVE / FIXED_OPERATION_ID / "payload/pages/x.md",
                after="c" * 64,
                staged=Path("staged/0000.bin"),
            ),
            _auth_mutation(
                Path("pages/x.md"),
                kind=MutationKind.DELETE,
                before="d" * 64,
                original=Path("originals/0001.bin"),
            ),
        ),
        # 缺 payload。
        (
            _auth_mutation(
                OUTGOING_RELATIVE / FIXED_OPERATION_ID / "package.json",
                after="b" * 64,
                staged=Path("staged/0000.bin"),
            ),
            _auth_mutation(
                Path("pages/x.md"),
                kind=MutationKind.DELETE,
                before="d" * 64,
                original=Path("originals/0001.bin"),
            ),
        ),
        # 零个外部 DELETE（垃圾日志）。
        (
            _auth_mutation(
                OUTGOING_RELATIVE / FIXED_OPERATION_ID / "package.json",
                after="b" * 64,
                staged=Path("staged/0000.bin"),
            ),
            _auth_mutation(
                OUTGOING_RELATIVE / FIXED_OPERATION_ID / "payload/pages/x.md",
                after="c" * 64,
                staged=Path("staged/0001.bin"),
            ),
        ),
    ],
)
def test_package_authorization_rejects_structural_missing(
    mutations: tuple[JournalMutation, ...],
) -> None:
    from draft.trash_package import parse_package_authorization

    with pytest.raises(DraftError, match=CheckCode.TRASH_PACKAGE_CORRUPT.value):
        parse_package_authorization(_package_auth_journal(*mutations), Path("/draft"))


def _extra_payload_write(pkg: Path) -> JournalMutation:
    return _auth_mutation(pkg / "payload/extra.md", after="a" * 64, staged=Path("staged/0099.bin"))


def _extra_rewrite_package_json(pkg: Path) -> JournalMutation:
    return _auth_mutation(
        pkg / "package.json",
        before="f" * 64,
        after="b" * 64,
        original=Path("originals/0000.bin"),
        staged=Path("staged/0000.bin"),
    )


def _extra_delete_package_json(pkg: Path) -> JournalMutation:
    return _auth_mutation(
        pkg / "package.json",
        kind=MutationKind.DELETE,
        before="f" * 64,
        original=Path("originals/0000.bin"),
    )


def _extra_unexpected_package_write(pkg: Path) -> JournalMutation:
    return _auth_mutation(pkg / "unexpected.txt", after="a" * 64, staged=Path("staged/0099.bin"))


@pytest.mark.parametrize(
    "extra",
    [
        _extra_payload_write,
        _extra_rewrite_package_json,
        _extra_delete_package_json,
        _extra_unexpected_package_write,
    ],
)
def test_package_authorization_rejects_third_or_illegal_package_mutation(
    extra: Callable[[Path], JournalMutation],
) -> None:
    from draft.trash_package import parse_package_authorization

    pkg = OUTGOING_RELATIVE / FIXED_OPERATION_ID
    mutations = list(_valid_package_auth_mutations())
    mutations.append(extra(pkg))
    with pytest.raises(DraftError, match=CheckCode.TRASH_PACKAGE_CORRUPT.value):
        parse_package_authorization(_package_auth_journal(*mutations), Path("/draft"))


def test_package_authorization_rejects_two_outside_new_writes() -> None:
    """两个包外 before=None WRITE（合法折叠集线只允许一个）→ 损坏。"""
    from draft.trash_package import parse_package_authorization

    mutations = list(_valid_package_auth_mutations())
    mutations.append(
        _auth_mutation(Path("other.md"), after="h" * 64, staged=Path("staged/0100.bin"))
    )
    mutations.append(
        _auth_mutation(Path("another.md"), after="i" * 64, staged=Path("staged/0101.bin"))
    )
    with pytest.raises(DraftError, match=CheckCode.TRASH_PACKAGE_CORRUPT.value):
        parse_package_authorization(_package_auth_journal(*mutations), Path("/draft"))


def test_package_authorization_rejects_three_deletes() -> None:
    """三个 DELETE（合法组合只允许页面 + 可选折叠集线两个）→ 损坏。"""
    from draft.trash_package import parse_package_authorization

    mutations = list(_valid_package_auth_mutations())
    mutations.append(
        _auth_mutation(
            Path("pages/y.md"),
            kind=MutationKind.DELETE,
            before="e" * 64,
            original=Path("originals/0099.bin"),
        )
    )
    mutations.append(
        _auth_mutation(
            Path("pages/z.md"),
            kind=MutationKind.DELETE,
            before="e" * 64,
            original=Path("originals/0100.bin"),
        )
    )
    with pytest.raises(DraftError, match=CheckCode.TRASH_PACKAGE_CORRUPT.value):
        parse_package_authorization(_package_auth_journal(*mutations), Path("/draft"))


def test_package_authorization_rejects_non_trash_kind() -> None:
    from draft.trash_package import parse_package_authorization

    journal = _package_auth_journal(*_valid_package_auth_mutations())
    journal = dataclasses.replace(journal, kind=OperationKind.MOVE)
    with pytest.raises(DraftError, match=CheckCode.TRASH_PACKAGE_CORRUPT.value):
        parse_package_authorization(journal, Path("/draft"))


def _restore_decision(
    restore_phase: OperationPhase,
    trash_phase: OperationPhase,
    *,
    link_state: RestoreLinkState = RestoreLinkState.MATCHED,
    preclaim_bootstrap: bool = False,
    proven_unapplied: bool = False,
) -> RestoreRecoveryAction:
    return restore_recovery_decision(
        restore_phase=restore_phase,
        trash_phase=trash_phase,
        link_state=link_state,
        preclaim_bootstrap=preclaim_bootstrap,
        proven_unapplied=proven_unapplied,
    )


@pytest.mark.parametrize(
    ("facts", "expected"),
    [
        (
            _restore_recovery_facts(
                restore_phase=OperationPhase.ALLOCATING,
                relation=RestoreTrashRelation.PRECLAIM,
                ownership_action=RestoreTrashAction.RECOVER_PRECLAIM,
            ),
            RestoreRecoveryAction.ABANDON_UNCLAIMED_BOOTSTRAP,
        ),
        (
            _restore_recovery_facts(
                restore_phase=OperationPhase.APPLYING,
                relation=RestoreTrashRelation.MATCHED,
                ownership_action=RestoreTrashAction.RECOVER_MATCHED,
                trash_metadata={"restore_operation_id": "a" * 32},
            ),
            RestoreRecoveryAction.RESUME_FORWARD,
        ),
        (
            _restore_recovery_facts(
                restore_phase=OperationPhase.ROLLED_BACK,
                relation=RestoreTrashRelation.POST_UNCLAIM_CLEANUP,
                ownership_action=RestoreTrashAction.CLEANUP_POST_UNCLAIM,
            ),
            RestoreRecoveryAction.FINISH_ABANDON_CLEANUP,
        ),
    ],
)
def test_restore_recovery_decision_from_facts_consumes_capability_action(
    facts: RestoreRecoveryFacts,
    expected: RestoreRecoveryAction,
) -> None:
    assert restore_recovery_decision_from_facts(ROOT, facts) is expected


@pytest.mark.parametrize(
    ("relation", "ownership_action"),
    [
        (RestoreTrashRelation.PRECLAIM, RestoreTrashAction.RECOVER_MATCHED),
        (RestoreTrashRelation.MATCHED, RestoreTrashAction.CLEANUP_POST_UNCLAIM),
        (RestoreTrashRelation.POST_UNCLAIM_CLEANUP, RestoreTrashAction.RECOVER_PRECLAIM),
    ],
)
def test_restore_recovery_decision_from_facts_rejects_mismatched_capability_action(
    relation: RestoreTrashRelation,
    ownership_action: RestoreTrashAction,
) -> None:
    facts = _restore_recovery_facts(
        restore_phase=OperationPhase.APPLYING
        if relation is RestoreTrashRelation.MATCHED
        else OperationPhase.ALLOCATING
        if relation is RestoreTrashRelation.PRECLAIM
        else OperationPhase.ROLLED_BACK,
        relation=relation,
        ownership_action=ownership_action,
        trash_metadata={"restore_operation_id": "a" * 32}
        if relation is RestoreTrashRelation.MATCHED
        else None,
    )

    with pytest.raises(DraftError) as caught:
        restore_recovery_decision_from_facts(ROOT, facts)

    assert caught.value.code == CheckCode.OPERATION_INCONSISTENT_STATE.value


# 权威期望分区（计划"行为契约一"，第十二轮 H1/H2 扩展）：关联从布尔提升为三值状态，
# 只有这些 (R, T, link_state) 在满足附加事实时非 INCONSISTENT。其余全部组合一律
# INCONSISTENT（保留 R/T、锁、包与恢复材料，不猜测）。期望表独立于生产映射，不允许
# 读取生产 _PHASES_BY_KIND 或调用另一生产 helper 生成 expected。
_VALID_RESTORE_TRIPLES: dict[
    tuple[OperationPhase, OperationPhase, RestoreLinkState], RestoreRecoveryAction
] = {
    (
        OperationPhase.COMPLETED,
        OperationPhase.TRASH_RETAINED,
        RestoreLinkState.MATCHED,
    ): RestoreRecoveryAction.VERIFY_AND_COMMIT_CLEANUP,
    (
        OperationPhase.COMPLETED,
        OperationPhase.ROLLED_BACK,
        RestoreLinkState.MATCHED,
    ): RestoreRecoveryAction.RESUME_COMMITTED_CLEANUP,
    (
        OperationPhase.APPLYING,
        OperationPhase.TRASH_RETAINED,
        RestoreLinkState.MATCHED,
    ): RestoreRecoveryAction.RESUME_FORWARD,
    (
        OperationPhase.VALIDATING,
        OperationPhase.TRASH_RETAINED,
        RestoreLinkState.MATCHED,
    ): RestoreRecoveryAction.RESUME_FORWARD,
    (
        OperationPhase.LINK_INDEX_UPDATING,
        OperationPhase.TRASH_RETAINED,
        RestoreLinkState.MATCHED,
    ): RestoreRecoveryAction.RESUME_FORWARD,
    (
        OperationPhase.LINK_INDEX_PUBLISHED,
        OperationPhase.TRASH_RETAINED,
        RestoreLinkState.MATCHED,
    ): RestoreRecoveryAction.RESUME_FORWARD,
    (
        OperationPhase.LINK_INDEX_REBUILD_REQUIRED,
        OperationPhase.TRASH_RETAINED,
        RestoreLinkState.MATCHED,
    ): RestoreRecoveryAction.RESUME_FORWARD,
    (
        OperationPhase.ROLLING_BACK,
        OperationPhase.TRASH_RETAINED,
        RestoreLinkState.MATCHED,
    ): RestoreRecoveryAction.RESUME_ROLLBACK,
    (
        OperationPhase.ROLLED_BACK,
        OperationPhase.TRASH_RETAINED,
        RestoreLinkState.MATCHED,
    ): RestoreRecoveryAction.ABANDON_UNAPPLIED,
    # H1：ABANDON 已把 T 解关联并耐久写，R 材料待清理。这是程序自己写出的耐久中间
    # 状态，不能因 validate_restore_link 要求双向往返而把它拒绝为 plan_invalid。
    (
        OperationPhase.ROLLED_BACK,
        OperationPhase.TRASH_RETAINED,
        RestoreLinkState.TRASH_UNCLAIMED,
    ): RestoreRecoveryAction.FINISH_ABANDON_CLEANUP,
}
_PROVEN_GATED_TRIPLES = frozenset(
    {
        (
            OperationPhase.ALLOCATING,
            OperationPhase.TRASH_RETAINED,
            RestoreLinkState.MATCHED,
        ),
        (
            OperationPhase.PREPARED,
            OperationPhase.TRASH_RETAINED,
            RestoreLinkState.MATCHED,
        ),
    }
)


@pytest.mark.parametrize(
    ("restore_phase", "trash_phase", "link_state", "proven", "expected"),
    [
        (
            OperationPhase.COMPLETED,
            OperationPhase.TRASH_RETAINED,
            RestoreLinkState.MATCHED,
            False,
            RestoreRecoveryAction.VERIFY_AND_COMMIT_CLEANUP,
        ),
        (
            OperationPhase.COMPLETED,
            OperationPhase.ROLLED_BACK,
            RestoreLinkState.MATCHED,
            False,
            RestoreRecoveryAction.RESUME_COMMITTED_CLEANUP,
        ),
        (
            OperationPhase.APPLYING,
            OperationPhase.TRASH_RETAINED,
            RestoreLinkState.MATCHED,
            False,
            RestoreRecoveryAction.RESUME_FORWARD,
        ),
        (
            OperationPhase.VALIDATING,
            OperationPhase.TRASH_RETAINED,
            RestoreLinkState.MATCHED,
            False,
            RestoreRecoveryAction.RESUME_FORWARD,
        ),
        (
            OperationPhase.ROLLING_BACK,
            OperationPhase.TRASH_RETAINED,
            RestoreLinkState.MATCHED,
            False,
            RestoreRecoveryAction.RESUME_ROLLBACK,
        ),
        (
            OperationPhase.ROLLED_BACK,
            OperationPhase.TRASH_RETAINED,
            RestoreLinkState.MATCHED,
            False,
            RestoreRecoveryAction.ABANDON_UNAPPLIED,
        ),
        (
            OperationPhase.ROLLED_BACK,
            OperationPhase.TRASH_RETAINED,
            RestoreLinkState.TRASH_UNCLAIMED,
            False,
            RestoreRecoveryAction.FINISH_ABANDON_CLEANUP,
        ),
        (
            OperationPhase.ALLOCATING,
            OperationPhase.TRASH_RETAINED,
            RestoreLinkState.MATCHED,
            True,
            RestoreRecoveryAction.ABANDON_UNAPPLIED,
        ),
        (
            OperationPhase.PREPARED,
            OperationPhase.TRASH_RETAINED,
            RestoreLinkState.MATCHED,
            True,
            RestoreRecoveryAction.ABANDON_UNAPPLIED,
        ),
    ],
)
def test_restore_recovery_decision_valid_partitions(
    restore_phase: OperationPhase,
    trash_phase: OperationPhase,
    link_state: RestoreLinkState,
    proven: bool,
    expected: RestoreRecoveryAction,
) -> None:
    """行为契约一的合法分区逐项成立（第十二轮 H1/H2 扩展）。"""
    assert (
        restore_recovery_decision(
            restore_phase=restore_phase,
            trash_phase=trash_phase,
            link_state=link_state,
            preclaim_bootstrap=False,
            proven_unapplied=proven,
        )
        is expected
    ), f"{(restore_phase, trash_phase, link_state, proven)} 期望 {expected}"


def test_restore_recovery_decision_exhaustive_link_state_matrix() -> None:
    """穷举 R phase × T phase × link_state：未列组合一律 INCONSISTENT（proven=False 基线）。

    覆盖第十一轮 M1：R=rolled_back,T=rolled_back 与 R=prepared,T=rolled_back 等先前
    误判为可放弃/可提交的组合必须先报 INCONSISTENT，不得在后续 journal 写入时才失败。
    覆盖第十二轮 H1：unclaimed 只在 rolled_back/trash_retained 下进入
    FINISH_ABANDON_CLEANUP，其他任何 unclaimed 组合都不得猜测。
    """
    for restore_phase in OperationPhase:
        for trash_phase in OperationPhase:
            for link_state in RestoreLinkState:
                expected = _VALID_RESTORE_TRIPLES.get(
                    (restore_phase, trash_phase, link_state),
                    RestoreRecoveryAction.INCONSISTENT,
                )
                assert (
                    _restore_decision(restore_phase, trash_phase, link_state=link_state) is expected
                ), f"{(restore_phase, trash_phase, link_state)} 期望 {expected}"


def test_restore_recovery_decision_counterexamples_inconsistent() -> None:
    """评审给出的反例组合必须仍为零写入 INCONSISTENT（第十二轮 H1/H2）。"""
    # 终态 + 终态：T 已 rolled_back 时 R 不能单方面放弃。
    assert (
        _restore_decision(OperationPhase.ROLLED_BACK, OperationPhase.ROLLED_BACK)
        is RestoreRecoveryAction.INCONSISTENT
    )
    assert (
        _restore_decision(OperationPhase.PREPARED, OperationPhase.ROLLED_BACK)
        is RestoreRecoveryAction.INCONSISTENT
    )
    assert (
        _restore_decision(OperationPhase.COMPLETED, OperationPhase.ALLOCATING)
        is RestoreRecoveryAction.INCONSISTENT
    )
    # CONFLICT（T 指向其他 R / 身份不可证明）一律零写入失败，不得误归 unclaimed/matched。
    for restore_phase in OperationPhase:
        for trash_phase in OperationPhase:
            assert (
                _restore_decision(
                    restore_phase,
                    trash_phase,
                    link_state=RestoreLinkState.CONFLICT,
                )
                is RestoreRecoveryAction.INCONSISTENT
            ), f"conflict {(restore_phase, trash_phase)}"


def test_restore_recovery_decision_unclaimed_only_rolled_back_retained() -> None:
    """TRASH_UNCLAIMED 只对 rolled_back/trash_retained 合法，其他组合不得用 FINISH 掩盖。"""
    for restore_phase in OperationPhase:
        for trash_phase in OperationPhase:
            if (restore_phase, trash_phase) == (
                OperationPhase.ROLLED_BACK,
                OperationPhase.TRASH_RETAINED,
            ):
                continue
            assert (
                _restore_decision(
                    restore_phase,
                    trash_phase,
                    link_state=RestoreLinkState.TRASH_UNCLAIMED,
                )
                is RestoreRecoveryAction.INCONSISTENT
            ), f"unclaimed {(restore_phase, trash_phase)}"


def test_restore_recovery_decision_proven_only_gates_allocating_prepared() -> None:
    """proven_unapplied 只改变 allocating/prepared × trash_retained × matched。"""
    for restore_phase in OperationPhase:
        for trash_phase in OperationPhase:
            for link_state in RestoreLinkState:
                if (restore_phase, trash_phase, link_state) in _PROVEN_GATED_TRIPLES:
                    continue
                base = _restore_decision(restore_phase, trash_phase, link_state=link_state)
                assert (
                    _restore_decision(
                        restore_phase,
                        trash_phase,
                        link_state=link_state,
                        proven_unapplied=True,
                    )
                    is base
                ), f"{(restore_phase, trash_phase, link_state)}"


def test_restore_recovery_decision_not_proven_unapplied_is_inconsistent() -> None:
    """R=allocating/prepared 但不能证明未应用：不猜测，保留现场。"""
    for triple in _PROVEN_GATED_TRIPLES:
        restore_phase, trash_phase, link_state = triple
        assert (
            _restore_decision(restore_phase, trash_phase, link_state=link_state)
            is RestoreRecoveryAction.INCONSISTENT
        ), str(triple)


def test_restore_recovery_decision_proven_gates_abandon() -> None:
    """allocating/prepared × trash_retained × matched 且 proven 时进入 ABANDON。"""
    for triple in _PROVEN_GATED_TRIPLES:
        restore_phase, trash_phase, link_state = triple
        assert (
            _restore_decision(
                restore_phase,
                trash_phase,
                link_state=link_state,
                proven_unapplied=True,
            )
            is RestoreRecoveryAction.ABANDON_UNAPPLIED
        ), str(triple)


# ---------------------------------------------------------------------------
# 第十三轮 H1：严格 pre-claim bootstrap 形状判定与决策表新增行。
# 认领尚未发生（R=allocating、T unclaimed）与 ABANDON 已删除 claim（R=rolled_back、
# T unclaimed）是同值 TRASH_UNCLAIMED 的两个不同恢复阶段，必须结合 R 形状区分。
# ---------------------------------------------------------------------------


def _restore_bootstrap_journal(*, change: str = "none") -> tuple[Journal, str]:
    """构造最小合法 pre-claim bootstrap R，或每次只改变一个维度；不调用被测 predicate。

    返回 (journal, trash_id)。``metadata`` 必须精确等于既定字段，helper 只负责按
    change 构造输入，expected 由测试独立写出。
    """
    from draft.observations import EntryKind, Observation, ObservationManifest

    trash_id = "b" * 32
    journal = Journal(
        operation_id="a" * 32,
        kind=OperationKind.RESTORE,
        phase=OperationPhase.ALLOCATING,
        root_state=RootState.REGISTERED,
        observations=EMPTY_OBSERVATIONS,
        mutations=(),
        remove_empty_directories=(),
        metadata={"restores_trash_operation": trash_id},
        directory=Path("/draft/.draft/operations") / ("a" * 32),
        link_index_revision=None,
        link_index_result=None,
        link_index_sha256=None,
    )
    if change == "none":
        return journal, trash_id
    if change == "prepared":
        return dataclasses.replace(journal, phase=OperationPhase.PREPARED), trash_id
    if change == "observation":
        manifest = ObservationManifest(
            entries=(Observation(path=Path("pages/obsolete.md"), kind=EntryKind.FILE, sha256="x"),)
        )
        return dataclasses.replace(journal, observations=manifest), trash_id
    if change == "mutation":
        return (
            dataclasses.replace(
                journal,
                mutations=(
                    _auth_mutation(
                        Path("pages/obsolete.md"),
                    )
                ),
            ),
            trash_id,
        )
    if change == "remove_dir":
        return dataclasses.replace(
            journal, remove_empty_directories=(Path("pages/empty"),)
        ), trash_id
    if change == "missing_backref":
        return dataclasses.replace(journal, metadata={}), trash_id
    if change == "extra_metadata":
        return (
            dataclasses.replace(
                journal, metadata={"restores_trash_operation": trash_id, "extra": "x"}
            ),
            trash_id,
        )
    if change == "different_trash":
        return dataclasses.replace(
            journal, metadata={"restores_trash_operation": "c" * 32}
        ), trash_id
    raise AssertionError(f"unknown change: {change}")


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ("none", True),
        ("prepared", False),
        ("observation", False),
        ("mutation", False),
        ("remove_dir", False),
        ("missing_backref", False),
        ("extra_metadata", False),
        ("different_trash", False),
    ],
)
def test_restore_preclaim_bootstrap_shape(change: str, expected: bool) -> None:
    """严格 bootstrap 每次只改变一个维度：任何偏离都必须是 False（round-13 H1）。"""
    from draft.trash import is_restore_preclaim_bootstrap

    restore, trash_id = _restore_bootstrap_journal(change=change)
    assert is_restore_preclaim_bootstrap(restore, trash_id) is expected


def test_restore_recovery_decision_preclaim_bootstrap_rows() -> None:
    """决策表新增 preclaim_bootstrap 维度的合法/拒绝行（round-13 H1）。"""
    cases = [
        # 新合法行：R 仍是严格前认领初始日志。
        (
            OperationPhase.ALLOCATING,
            OperationPhase.TRASH_RETAINED,
            RestoreLinkState.TRASH_UNCLAIMED,
            True,
            False,
            RestoreRecoveryAction.ABANDON_UNCLAIMED_BOOTSTRAP,
        ),
        # 同 phase/link 但不再是 bootstrap，必须拒绝（不猜测）。
        (
            OperationPhase.ALLOCATING,
            OperationPhase.TRASH_RETAINED,
            RestoreLinkState.TRASH_UNCLAIMED,
            False,
            True,
            RestoreRecoveryAction.INCONSISTENT,
        ),
        # 既有放弃后半段不变。
        (
            OperationPhase.ROLLED_BACK,
            OperationPhase.TRASH_RETAINED,
            RestoreLinkState.TRASH_UNCLAIMED,
            False,
            False,
            RestoreRecoveryAction.FINISH_ABANDON_CLEANUP,
        ),
        # prepared/mid/completed unclaimed 均拒绝。
        (
            OperationPhase.PREPARED,
            OperationPhase.TRASH_RETAINED,
            RestoreLinkState.TRASH_UNCLAIMED,
            False,
            True,
            RestoreRecoveryAction.INCONSISTENT,
        ),
        (
            OperationPhase.APPLYING,
            OperationPhase.TRASH_RETAINED,
            RestoreLinkState.TRASH_UNCLAIMED,
            False,
            False,
            RestoreRecoveryAction.INCONSISTENT,
        ),
        (
            OperationPhase.COMPLETED,
            OperationPhase.TRASH_RETAINED,
            RestoreLinkState.TRASH_UNCLAIMED,
            False,
            False,
            RestoreRecoveryAction.INCONSISTENT,
        ),
    ]
    for restore_phase, trash_phase, link_state, bootstrap, proven, expected in cases:
        assert (
            _restore_decision(
                restore_phase,
                trash_phase,
                link_state=link_state,
                preclaim_bootstrap=bootstrap,
                proven_unapplied=proven,
            )
            is expected
        ), f"{(restore_phase, trash_phase, link_state, bootstrap, proven)} 期望 {expected}"


def test_restore_recovery_decision_preclaim_bootstrap_only_affects_one_row() -> None:
    """新维度只改变 allocating × trash_retained × unclaimed 一行，其余全表不变。"""
    for restore_phase in OperationPhase:
        for trash_phase in OperationPhase:
            for link_state in RestoreLinkState:
                base = _restore_decision(
                    restore_phase,
                    trash_phase,
                    link_state=link_state,
                    preclaim_bootstrap=False,
                    proven_unapplied=True,
                )
                with_bootstrap = _restore_decision(
                    restore_phase,
                    trash_phase,
                    link_state=link_state,
                    preclaim_bootstrap=True,
                    proven_unapplied=True,
                )
                if (
                    restore_phase,
                    trash_phase,
                    link_state,
                ) == (
                    OperationPhase.ALLOCATING,
                    OperationPhase.TRASH_RETAINED,
                    RestoreLinkState.TRASH_UNCLAIMED,
                ):
                    assert base is RestoreRecoveryAction.INCONSISTENT
                    assert with_bootstrap is RestoreRecoveryAction.ABANDON_UNCLAIMED_BOOTSTRAP
                else:
                    assert with_bootstrap is base, f"{(restore_phase, trash_phase, link_state)}"
