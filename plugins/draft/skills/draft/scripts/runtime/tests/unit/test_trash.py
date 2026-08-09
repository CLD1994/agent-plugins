from __future__ import annotations

import json
import uuid as uuid_module
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from draft.errors import DraftError
from draft.graph import (
    CONCLUSIONS_PAGE,
    ROOT_PAGE,
    DraftGraph,
    resolve_local_destination,
)
from draft.markdown import parse_page
from draft.models import (
    MarkdownLink,
    MutationKind,
    OperationPhase,
    PageDocument,
    SourceSpan,
)
from draft.operations import (
    Journal,
    TrashDeadlines,
    sha256_bytes,
)
from draft.trash import require_retained_restore_window, trash_plan
from draft.trash_package import OUTGOING_RELATIVE, PACKAGE_VERSION
from draft.vocabulary import RelationCode

ROOT = Path("/draft")
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
    kind: str = "trash",
    operation_id: str = "a" * 32,
) -> Journal:
    deadlines = TrashDeadlines.from_created_at(created_at)
    return Journal(
        operation_id=operation_id,
        kind=kind,
        phase=phase,
        validation_mode="registered",
        mutations=(),
        remove_empty_directories=(),
        metadata=deadlines.to_metadata(),
        directory=Path("/draft/.draft/operations") / operation_id,
    )


# ===========================================================================
# trash_plan: 合法规划
# ===========================================================================


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
    plan, package_relative = trash_plan(graph, Path("pages/a.md"))

    assert plan.kind == "trash"
    assert plan.root == ROOT
    assert plan.validation_mode == "registered"
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
    plan, _ = trash_plan(graph, Path("pages/solo/only.md"))

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
    plan, _ = trash_plan(graph, Path("pages/a.md"))

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
    plan, _ = trash_plan(graph, Path("pages/a.md"))

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
        trash_plan(graph, ROOT_PAGE)
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
        trash_plan(graph, CONCLUSIONS_PAGE)
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
        trash_plan(graph, Path("pages/missing.md"))
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
        trash_plan(graph, Path("pages/hub/index.md"))
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
        trash_plan(graph, Path("pages/a.md"))
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
        trash_plan(graph, Path("pages/a.md"))
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
        trash_plan(graph, Path("pages/a.md"))
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
        trash_plan(graph, Path("pages/a.md"))
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
        trash_plan(graph, Path("pages/a.md"))
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
        trash_plan(graph, Path("pages/a.md"))
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
    journal = _trash_journal(created_at=CREATED, kind="restore")
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
