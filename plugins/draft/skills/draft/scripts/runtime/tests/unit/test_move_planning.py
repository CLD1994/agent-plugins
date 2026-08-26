# pyright: reportPrivateUsage=false

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from draft.diagnostic_codes import OPERATION_PLAN_INVALID
from draft.errors import DraftError
from draft.graph import DraftGraph, MarkerSnapshot, MarkerState, resolve_local_destination
from draft.markdown import parse_page
from draft.models import (
    MarkdownLink,
    MutationKind,
    OperationKind,
    PageDocument,
    RootState,
    SourceSpan,
)
from draft.move_closure import MoveClosure, UnmanagedFile
from draft.move_operands import MoveRequest, MoveSource, MoveTarget, SourceKind, TargetKind
from draft.observations import EMPTY_OBSERVATIONS, ObservationManifest, graph_observations
from draft.resources import (
    ProbeState,
    ResourceReference,
    resolve_body_resource,
    resolve_page_resources,
)
from draft.structure import plan_move, plan_move_request
from draft.vocabulary import ConclusionsState, RelationCode

ROOT = Path("/draft")


def _present(_path: Path) -> ProbeState:
    return ProbeState.FILE


def _reference(source: Path, text: str, destination: str) -> tuple[str, ResourceReference]:
    """构造一条指向 destination 的链接引用；返回 (整段文本, 引用)。"""
    link_text = f"[目标]({destination})"
    full = text if text else f"前文 {link_text} 后文\n"
    start = full.index(destination)
    link = MarkdownLink(
        label="目标",
        destination=destination,
        destination_span=SourceSpan(start, start + len(destination)),
        whole_span=SourceSpan(full.index(link_text), full.index(link_text) + len(link_text)),
        line=1,
    )
    resource = resolve_body_resource(ROOT, source, destination, target_probe=_present)
    return full, ResourceReference(link=link, resource=resource)


# 局部内存图构造器：从 Markdown 文本构造一致的 DraftGraph，不访问文件系统
# （从 test_structure.py 复制，供 move 双类型批量编排用例复用）。
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
_CANDIDATE_BYTES = (
    b'{"format_version":1,"root_state":"candidate","conclusions_state":"not_generated"}'
)
_CANDIDATE_SNAPSHOT = MarkerSnapshot(
    state=MarkerState(
        version=1,
        root_state=RootState.CANDIDATE,
        conclusions_state=ConclusionsState.NOT_GENERATED,
    ),
    raw_bytes=_CANDIDATE_BYTES,
)


def _structure_observations(
    graph: DraftGraph, *, marker: MarkerSnapshot = _REGISTERED_SNAPSHOT
) -> ObservationManifest:
    return graph_observations(graph, marker=marker)


_INDEX = """# 根

> 状态：`已关闭：纯导航页`

## 子主题

- [结论汇总](conclusions.md)
- [甲](pages/a.md)
"""


def _graph_from_texts(texts: Mapping[Path, str], *, root: Path = ROOT) -> DraftGraph:
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


def test_plan_unmanaged_rebases_outbound_relative_links() -> None:
    from draft.structure import _plan_unmanaged

    source = Path("00-background.md")
    text, ref_local = _reference(source, "", "other.md")
    _ext = "[外](https://example.com/x)"
    full = text + _ext + "\n"
    # 手工链接位置仅 ref_local 参与断言，外链通过文本不变性验证
    closure = MoveClosure(unmanaged={source: UnmanagedFile(full, (ref_local,))}, blocking=())

    mutations, effects = _plan_unmanaged(
        ROOT, closure, {source: Path("pages/00-background.md")}, {}
    )

    writes = [m for m in mutations if m.kind.value == "write"]
    deletes = [m for m in mutations if m.kind.value == "delete"]
    assert len(writes) == 1 and len(deletes) == 1
    assert writes[0].path == Path("pages/00-background.md")
    assert deletes[0].path == source
    assert writes[0].content is not None
    rendered = writes[0].content.decode("utf-8")
    assert "[目标](../other.md)" in rendered
    assert _ext in rendered
    assert effects[0].from_path == source and effects[0].to_path == Path("pages/00-background.md")


def test_plan_unmanaged_rewrites_inbound_links_from_stationary_files() -> None:
    from draft.resources import ResourceIdentity, ResourceKind
    from draft.structure import _plan_unmanaged

    mover = Path("00-background.md")
    linker = Path("notes/reading.md")
    text, ref = _reference(linker, "", "../00-background.md")
    closure = MoveClosure(
        unmanaged={
            mover: UnmanagedFile("无链接正文\n", ()),
            linker: UnmanagedFile(text, (ref,)),
        },
        blocking=(),
    )
    moved_targets = {
        ResourceIdentity(ResourceKind.LOCAL, "00-background.md"): ResourceIdentity(
            ResourceKind.PAGE, "pages/00-background.md"
        )
    }

    mutations, _effects = _plan_unmanaged(
        ROOT, closure, {mover: Path("pages/00-background.md")}, moved_targets
    )

    by_path = {m.path: m for m in mutations}
    linker_write = by_path[linker]
    assert linker_write.before_sha256 is not None
    assert linker_write.content is not None
    assert "[目标](../pages/00-background.md)" in linker_write.content.decode("utf-8")


def test_plan_move_request_rejects_rendered_text_that_lost_extractability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """故障注入替身经公开入口触发：改写结果不可提取 → operation.plan_invalid（round-1 L11）。"""
    from draft import structure

    graph = _graph_from_texts({Path("index.md"): _INDEX, Path("pages/a.md"): _LEAF_A})
    source = Path("00-background.md")
    text, ref = _reference(source, "", "other.md")
    closure = MoveClosure(unmanaged={source: UnmanagedFile(text, (ref,))}, blocking=())
    request = MoveRequest(
        sources=(MoveSource(source, SourceKind.UNMANAGED_FILE, (source,)),),
        target=MoveTarget(Path("pages"), TargetKind.PREFIX),
        results={source: Path("pages/00-background.md")},
    )

    def _broken_rewrite(text: str, replacements: Mapping[SourceSpan, str]) -> str:
        # 目标含转义序列，提取管线对齐必然失败（source_alignment_failed）
        return "看 [x](a\\.md)。\n"

    monkeypatch.setattr(structure, "rewrite_link_destinations", _broken_rewrite)

    with pytest.raises(DraftError) as caught:
        plan_move_request(
            graph, closure, request, marker=_CANDIDATE_SNAPSHOT, observations=EMPTY_OBSERVATIONS
        )

    assert caught.value.code == OPERATION_PLAN_INVALID


# ---------------------------------------------------------------------------
# 纯未受管理移动的入站链接统一闭合（规格 §4.1），经 plan_move_request 公开路径验证
# ---------------------------------------------------------------------------


def test_plan_move_request_unmanaged_move_rewrites_managed_linkers() -> None:
    """纯未受管理移动经公开入口：受管理链接者指向移动目标的正文链接被重写（round-1 L11）。"""
    page_a = (
        "# 页面 A\n\n> 状态：`已确认：说明`\n>\n"
        "> 父主题：[根索引](../index.md)\n\n"
        "参考 [旧稿](../00-bg.md)。\n"
    )
    graph = _graph_from_texts({Path("index.md"): _INDEX, Path("pages/a.md"): page_a})
    source = Path("00-bg.md")
    text, ref = _reference(source, "", "other.md")
    closure = MoveClosure(unmanaged={source: UnmanagedFile(text, (ref,))}, blocking=())
    request = MoveRequest(
        sources=(MoveSource(source, SourceKind.UNMANAGED_FILE, (source,)),),
        target=MoveTarget(Path("pages"), TargetKind.PREFIX),
        results={source: Path("pages/00-bg.md")},
    )

    plan = plan_move_request(
        graph, closure, request, marker=_CANDIDATE_SNAPSHOT, observations=EMPTY_OBSERVATIONS
    )

    by_path = {m.path: m for m in plan.mutations}
    linker_write = by_path[Path("pages/a.md")]
    assert linker_write.content is not None
    rewritten = linker_write.content.decode("utf-8")
    assert "[旧稿](00-bg.md)" in rewritten  # 按 pages/a.md 新基址重算
    assert "../00-bg.md" not in rewritten


# ---------------------------------------------------------------------------
# plan_move_request: 双类型批量的统一编排
# ---------------------------------------------------------------------------

_LEAF_A = """# 甲

> 状态：`待讨论`
>
> 父主题：[根](../index.md)

甲的正文。
"""

_MOVE_DIR_INDEX = """# 根

> 状态：`已关闭：纯导航页`

## 子主题

- [归档](pages/archive/index.md)
- [主题](pages/topic/index.md)
"""

_MOVE_DIR_ARCHIVE = """# 归档

> 状态：`已关闭：纯导航页`
>
> 父主题：[根](../../index.md)
"""

_MOVE_DIR_TOPIC = """# 主题

> 状态：`已关闭：纯导航页`
>
> 父主题：[根](../../index.md)

## 子主题

- [甲](a.md)
"""

_MOVE_DIR_TOPIC_A = """# 甲

> 状态：`待讨论`
>
> 父主题：[主题](index.md)

甲的正文。
"""


def test_plan_move_request_delegates_single_managed_file_to_plan_move() -> None:
    """文件目标 + 单受管理文件来源：与直接调用 plan_move 逐字段一致。"""
    graph = _graph_from_texts({Path("index.md"): _INDEX, Path("pages/a.md"): _LEAF_A})
    marker = _REGISTERED_SNAPSHOT
    observations = _structure_observations(graph)
    request = MoveRequest(
        sources=(MoveSource(Path("pages/a.md"), SourceKind.MANAGED_PAGE, (Path("pages/a.md"),)),),
        target=MoveTarget(Path("pages/c.md"), TargetKind.FILE),
        results={Path("pages/a.md"): Path("pages/c.md")},
    )

    plan = plan_move_request(
        graph,
        MoveClosure(unmanaged={}, blocking=()),
        request,
        marker=marker,
        observations=observations,
    )
    expected = plan_move(
        graph, Path("pages/a.md"), Path("pages/c.md"), marker=marker, observations=observations
    )

    assert plan.mutations == expected.mutations
    assert plan.effects == expected.effects
    assert plan.remove_empty_directories == expected.remove_empty_directories


def test_plan_move_request_prefix_unmanaged_file_produces_write_delete() -> None:
    """未受管理文件 + 前缀目标：WRITE+DELETE 形态，状态与观察原样传递。"""
    graph = _graph_from_texts({Path("index.md"): _INDEX, Path("pages/a.md"): _LEAF_A})
    source = Path("stray.md")
    text, ref = _reference(source, "", "other.md")
    closure = MoveClosure(unmanaged={source: UnmanagedFile(text, (ref,))}, blocking=())
    request = MoveRequest(
        sources=(MoveSource(source, SourceKind.UNMANAGED_FILE, (source,)),),
        target=MoveTarget(Path("pages"), TargetKind.PREFIX),
        results={source: Path("pages/stray.md")},
    )

    plan = plan_move_request(
        graph, closure, request, marker=_CANDIDATE_SNAPSHOT, observations=EMPTY_OBSERVATIONS
    )

    writes = [m for m in plan.mutations if m.kind is MutationKind.WRITE]
    deletes = [m for m in plan.mutations if m.kind is MutationKind.DELETE]
    assert len(writes) == 1 and len(deletes) == 1
    assert writes[0].path == Path("pages/stray.md")
    assert deletes[0].path == source
    assert plan.kind is OperationKind.MOVE
    assert plan.root_state is RootState.CANDIDATE
    assert plan.observations is EMPTY_OBSERVATIONS


def test_plan_move_request_directory_source_reparents_members_under_prefix() -> None:
    """受管理目录来源：成员逐成员显式映射并改父，渲染指向新物理父页。"""
    graph = _graph_from_texts(
        {
            Path("index.md"): _MOVE_DIR_INDEX,
            Path("pages/archive/index.md"): _MOVE_DIR_ARCHIVE,
            Path("pages/topic/index.md"): _MOVE_DIR_TOPIC,
            Path("pages/topic/a.md"): _MOVE_DIR_TOPIC_A,
        }
    )
    request = MoveRequest(
        sources=(
            MoveSource(
                Path("pages/topic"),
                SourceKind.DIRECTORY,
                (Path("pages/topic/index.md"), Path("pages/topic/a.md")),
            ),
        ),
        target=MoveTarget(Path("pages/archive"), TargetKind.PREFIX),
        results={
            Path("pages/topic/index.md"): Path("pages/archive/topic/index.md"),
            Path("pages/topic/a.md"): Path("pages/archive/topic/a.md"),
        },
    )

    plan = plan_move_request(
        graph,
        MoveClosure(unmanaged={}, blocking=()),
        request,
        marker=_REGISTERED_SNAPSHOT,
        observations=_structure_observations(graph),
    )

    by_path = {m.path: m for m in plan.mutations}
    assert Path("pages/archive/topic/index.md") in by_path
    assert Path("pages/archive/topic/a.md") in by_path
    hub_mutation = by_path[Path("pages/archive/topic/index.md")]
    assert hub_mutation.content is not None
    hub = hub_mutation.content.decode("utf-8")
    assert "父主题：[根](../index.md)" in hub  # 新物理父页 pages/archive/index.md
    assert "- [甲](a.md)" in hub  # 子导航随批保留：旧父随批移动，不判为改父
    leaf_mutation = by_path[Path("pages/archive/topic/a.md")]
    assert leaf_mutation.content is not None
    leaf = leaf_mutation.content.decode("utf-8")
    assert "父主题：[主题](index.md)" in leaf  # 新物理父页 pages/archive/topic/index.md


_MOVE_BATCH_INDEX = """# 根

> 状态：`已关闭：纯导航页`

## 子主题

- [归档](pages/out/index.md)
- [主题](pages/topic/index.md)
"""

_MOVE_BATCH_OUT = """# 归档

> 状态：`已关闭：纯导航页`
>
> 父主题：[根](../../index.md)
"""


def test_plan_move_request_mixed_batch_bfs_expansion_collides_with_unmanaged() -> None:
    """混合批：受管理文件来源 BFS 展开目标与未受管理结果相同 → structure.conflict 点名双方。"""
    graph = _graph_from_texts(
        {
            Path("index.md"): _MOVE_BATCH_INDEX,
            Path("pages/out/index.md"): _MOVE_BATCH_OUT,
            Path("pages/topic/index.md"): _MOVE_DIR_TOPIC,
            Path("pages/topic/a.md"): _MOVE_DIR_TOPIC_A,
        }
    )
    source = Path("a.md")  # 根级未受管理文件，与 BFS 展开的子页目标同名
    request = MoveRequest(
        sources=(
            MoveSource(
                Path("pages/topic/index.md"),
                SourceKind.MANAGED_PAGE,
                (Path("pages/topic/index.md"),),
            ),
            MoveSource(source, SourceKind.UNMANAGED_FILE, (source,)),
        ),
        target=MoveTarget(Path("pages/out/sub"), TargetKind.PREFIX),
        results={
            Path("pages/topic/index.md"): Path("pages/out/sub/index.md"),
            source: Path("pages/out/sub/a.md"),
        },
    )

    with pytest.raises(DraftError) as caught:
        plan_move_request(
            graph,
            MoveClosure(unmanaged={}, blocking=()),
            request,
            marker=_REGISTERED_SNAPSHOT,
            observations=_structure_observations(graph),
        )
    assert caught.value.code == "structure.conflict"
    assert caught.value.paths == (Path("pages/out/sub/a.md"),)
    assert set(caught.value.details) == {"a.md", "pages/topic/a.md"}


def test_plan_move_request_rejects_prefix_outside_pages() -> None:
    """结果校验穿透：非法前缀目标由 validate_results 拒绝。"""
    graph = _graph_from_texts({Path("index.md"): _INDEX, Path("pages/a.md"): _LEAF_A})
    request = MoveRequest(
        sources=(MoveSource(Path("pages/a.md"), SourceKind.MANAGED_PAGE, (Path("pages/a.md"),)),),
        target=MoveTarget(Path("notes"), TargetKind.PREFIX),
        results={Path("pages/a.md"): Path("notes/a.md")},
    )

    with pytest.raises(DraftError) as caught:
        plan_move_request(
            graph,
            MoveClosure(unmanaged={}, blocking=()),
            request,
            marker=_REGISTERED_SNAPSHOT,
            observations=_structure_observations(graph),
        )
    assert caught.value.code == "structure.invalid_target"
