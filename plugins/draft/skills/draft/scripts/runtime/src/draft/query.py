"""紧凑结构查询：从单页快照或完整图计算稳定查询结果。

查询函数消费已经解析的内存快照，不访问文件系统；局部加载与完整加载是
明确的两类边界。`query page`/`links`/`relations` 的 PAGE 使用严格受管理
页面参数，`backlinks` 的 TARGET 使用宽松正文资源身份。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from draft.diagnostic_codes import CheckCode
from draft.errors import DraftError
from draft.graph import DraftGraph, MarkerState, load_graph
from draft.link_index import (
    IndexedFile,
    LinkIndex,
    LinkIndexContent,
    LinkSpan,
    OutboundEdge,
)
from draft.models import (
    CheckIssue,
    PageDocument,
    RelationCode,
    ResultWarning,
    RootState,
    StatusCode,
)
from draft.paths import resolve_draft_root, resolve_page_operand
from draft.resources import (
    HostOS,
    ProbeState,
    ResourceIdentity,
    ResourceKind,
    ResourceReference,
    ResourceScope,
    TargetState,
    resolve_body_resource,
    resolve_page_resources,
)

PAGE_NOT_FOUND = "page.not_found"


@dataclass(frozen=True)
class LocalPageSnapshot:
    document: PageDocument
    body_resources: tuple[ResourceReference, ...]
    relations: Mapping[RelationCode, tuple[tuple[Path, Path, int], ...]]


class GraphView(Protocol):
    """查询函数需要的只读图视图；DraftGraph 与单页快照视图都满足。"""

    @property
    def root(self) -> Path: ...

    @property
    def pages(self) -> Mapping[Path, PageDocument]: ...

    @property
    def parents(self) -> Mapping[Path, Path]: ...

    @property
    def relations(self) -> Mapping[RelationCode, tuple[tuple[Path, Path, int], ...]]: ...

    @property
    def body_resources(self) -> Mapping[Path, tuple[ResourceReference, ...]]: ...


class SnapshotView:
    """单页快照的查询视图：只有一页的正文资源与出站关系。"""

    def __init__(
        self,
        *,
        snapshot: LocalPageSnapshot,
    ) -> None:
        self._snapshot = snapshot
        self.root = Path("/")
        self.pages = {snapshot.document.path: snapshot.document}
        self.parents: dict[Path, Path] = {}
        self.relations = snapshot.relations
        self.body_resources = {snapshot.document.path: snapshot.body_resources}


@dataclass(frozen=True)
class LinkItem:
    target: str
    kind: ResourceKind
    scope: ResourceScope
    target_state: TargetState


@dataclass(frozen=True)
class RelationItem:
    relation: RelationCode
    page: Path


@dataclass(frozen=True)
class PageSummary:
    path: Path
    title: str
    status: StatusCode
    parent: Path | None
    child_count: int
    link_count: int
    backlink_count: int
    outgoing_relation_count: int
    incoming_relation_count: int


def _strict_page(root: Path, value: str) -> Path:
    """严格受管理页面操作数：路径语法合法但非现存受管理页面时抛 page.not_found。"""
    resolved = resolve_page_operand(root, value)
    return resolved.relative


def load_registered_page(root: Path, page: Path) -> LocalPageSnapshot:
    """验证登记标记与严格页面参数，只读取并解析该文件。"""
    from draft.graph import read_marker, resolve_local_destination
    from draft.markdown import parse_page

    resolved = resolve_draft_root(root)
    read_marker(resolved)
    relative = _strict_page(resolved, page.as_posix())
    path = resolved / relative
    if not path.is_file() or path.is_symlink():
        raise DraftError(PAGE_NOT_FOUND, relative)
    text = path.read_text(encoding="utf-8")
    document = parse_page(relative, text)
    body = resolve_page_resources(resolved, document, host_os=HostOS.LINUX)
    relations: dict[RelationCode, list[tuple[Path, Path, int]]] = {}
    for relation in document.relations:
        try:
            target, _anchor = resolve_local_destination(
                document.path, relation.link.destination, resolved
            )
        except DraftError:
            continue
        relations.setdefault(relation.code, []).append((document.path, target, relation.link.line))
    return LocalPageSnapshot(
        document=document,
        body_resources=body,
        relations={code: tuple(edges) for code, edges in relations.items()},
    )


def load_complete_snapshot(root: Path) -> DraftGraph:
    """发现并解析所有受管理页面；任一不可读/不可解析/登记无效时整体失败。"""
    resolved = resolve_draft_root(root)
    return load_graph(resolved)


_FATAL_QUERY_CODES = frozenset(
    code.value
    for code in (
        CheckCode.FORMAT_MISSING,
        CheckCode.FORMAT_INVALID,
        CheckCode.FORMAT_UNSUPPORTED,
        CheckCode.PATH_SYMLINK_ESCAPE,
    )
)


@dataclass(frozen=True)
class TolerantView:
    """容错全根视图：成功解析的图 + 去重排序阻碍路径 + 首个阻碍 issue。"""

    graph: DraftGraph
    blocking_paths: tuple[Path, ...]
    first_blocking: CheckIssue | None


def load_tolerant_view(
    root: Path, *, marker_state: MarkerState | None = None
) -> TolerantView:
    """容错全根加载：只有能归属到具体页面的解析失败才可成为候选部分结果。

    marker/格式等无法归属到具体页面的根级损坏继续整体失败；未管理路径不属于
    阻碍页面。``first_blocking`` 供 registered 根传播最具体的损坏代码。调用方
    已持有同根 marker 事实时必须传入，本函数不再二次读取。
    """
    from draft.graph import load_graph_tolerant

    resolved = resolve_draft_root(root)
    result = load_graph_tolerant(resolved, marker_state=marker_state)
    fatal = [issue for issue in result.issues if issue.code in _FATAL_QUERY_CODES]
    if fatal:
        first = fatal[0]
        raise DraftError(
            first.code,
            first.path if first.path is not None else resolved,
            details=first.details,
            line=first.line,
        )
    blocking_issues = [
        issue
        for issue in result.issues
        if issue.path is not None
        and issue.path not in result.graph.pages
        and issue.code != CheckCode.PAGE_UNMANAGED_PATH.value
    ]
    blocking_set: set[Path] = set()
    for issue in blocking_issues:
        if issue.path is not None:
            blocking_set.add(issue.path)
    blocking = sorted(blocking_set)
    return TolerantView(
        graph=result.graph,
        blocking_paths=tuple(blocking),
        first_blocking=blocking_issues[0] if blocking_issues else None,
    )


def page_links(graph: GraphView, page: Path) -> tuple[LinkItem, ...]:
    """正文资源：排除关系链接位置，按身份去重，保持首次声明顺序，去掉锚点。"""
    seen: set[tuple[object, ...]] = set()
    items: list[LinkItem] = []
    for reference in graph.body_resources.get(page, ()):
        resource = reference.resource
        key = (resource.kind, resource.target)
        if key in seen:
            continue
        seen.add(key)
        items.append(
            LinkItem(
                target=resource.target,
                kind=resource.kind,
                scope=resource.scope,
                target_state=resource.target_state,
            )
        )
    return tuple(items)


def backlinks(graph: GraphView, target: ResourceIdentity) -> tuple[Path, ...]:
    """按资源身份匹配的来源页面去重列表，按来源路径排序。"""
    sources: set[Path] = set()
    for source, references in graph.body_resources.items():
        for reference in references:
            if reference.resource.identity == target:
                sources.add(source)
    return tuple(sorted(sources))


def outgoing_relations(graph: GraphView, page: Path) -> tuple[RelationItem, ...]:
    """当前页面声明的关系，保持声明顺序，保留源码方向。"""
    ordered: list[tuple[int, RelationItem]] = []
    for code in RelationCode:
        for source, target, line in graph.relations.get(code, ()):
            if source == page:
                ordered.append((line, RelationItem(relation=code, page=target)))
    ordered.sort(key=lambda pair: pair[0])
    return tuple(item for _, item in ordered)


def incoming_relations(graph: GraphView, page: Path) -> tuple[RelationItem, ...]:
    """其他页面指向当前页面的声明，按来源路径再按声明位置排序。"""
    items: list[tuple[Path, int, RelationItem]] = []
    for code in RelationCode:
        for source, target, line in graph.relations.get(code, ()):
            if target == page:
                items.append((source, line, RelationItem(relation=code, page=source)))
    return tuple(item for _, _, item in sorted(items))


def pages_with_status(graph: GraphView, code: StatusCode) -> tuple[Path, ...]:
    """按规范 POSIX 路径排序的、讨论状态为 code 的页面。"""
    return tuple(sorted(path for path, page in graph.pages.items() if page.status.code is code))


def page_summary(
    graph: GraphView, page: Path, *, backlink_count: int | None = None
) -> PageSummary:
    document = graph.pages.get(page)
    if document is None:
        raise DraftError(PAGE_NOT_FOUND, page)
    parent = graph.parents.get(page)
    children = [child for child, declared in graph.parents.items() if declared == page]
    links = page_links(graph, page)
    outgoing = outgoing_relations(graph, page)
    incoming = incoming_relations(graph, page)
    page_identity = ResourceIdentity(ResourceKind.PAGE, page.as_posix())
    count = len(backlinks(graph, page_identity)) if backlink_count is None else backlink_count
    return PageSummary(
        path=page,
        title=document.title,
        status=document.status.code,
        parent=parent,
        child_count=len(children),
        link_count=len(links),
        backlink_count=count,
        outgoing_relation_count=len(outgoing),
        incoming_relation_count=len(incoming),
    )


def backlinks_from_index(
    index: LinkIndex | LinkIndexContent, target: ResourceIdentity
) -> tuple[Path, ...]:
    """从已验证索引的反向表读取去重排序的来源页面（spec §14）。

    与 ``backlinks``（图内正文资源）不同，索引覆盖全部覆盖 Markdown（含未受管理
    文件与关系位置），是查询可用的完整反向表事实。
    """
    sources = {edge.source_path for edge in index.backlinks.get(target, ())}
    return tuple(sorted(sources))


def _no_probe(_path: Path) -> ProbeState:
    """目标存在性探针：不访问文件系统（索引出站边身份与探针结果无关）。"""
    return ProbeState.MISSING


def known_documents_from_graph(root: Path, graph: DraftGraph) -> tuple[IndexedFile, ...]:
    """把上层已解析页面快照提取为可验证 scan seed（spec §8.6 / Task 9 Step 5）。

    seed 只提供 path + SHA-256 + 已提取 links；严格扫描仍重新读取全部 Markdown 并
    比较摘要，只有 path 与 SHA-256 均匹配才复用已解析 links（``build_index_content``
    契约），不跳过正文读取。出站边与索引 ``_index_v2_file`` 使用同一
    ``resolve_body_resource`` 词法解析，仅目标存在性探针不访问文件系统（身份与探针
    无关）。
    """
    documents: list[IndexedFile] = []
    for path, page in graph.pages.items():
        outbound: list[OutboundEdge] = []
        for link in page.links:
            resource = resolve_body_resource(root, path, link.destination, target_probe=_no_probe)
            outbound.append(
                OutboundEdge(
                    resource.identity,
                    LinkSpan(link.destination_span.start, link.destination_span.end),
                )
            )
        documents.append(IndexedFile(path, graph.page_digest(path), tuple(outbound)))
    return tuple(documents)


@dataclass(frozen=True)
class OpportunisticIndexResult:
    """机会式维护的查询可见结果（spec §14）：index 非 None 时其反向表可信。"""

    index: LinkIndex | LinkIndexContent | None
    warnings: tuple[ResultWarning, ...]


def maintain_link_index_for_query(
    root: Path,
    graph: DraftGraph,
    root_state: RootState,
) -> OpportunisticIndexResult:
    """查询入口的机会式维护：prepare + commit，保留本次完整扫描事实（spec §14）。

    - fresh（严格扫描证明无变化）：复用已经验证的已发布制品，不重复 commit；
    - absent/stale：commit 发布；未发布（并发令牌推进/锁持有）时仍返回本次完整
      扫描内容；
    - 扫描失败（rebuild_required）：commit 耐久记录降级，返回 ``None`` 与 warning，
      由调用方回退图内完整事实。

    journal 耐久失败、制品矛盾等按原错误传播（普通 query 非零退出，spec §14）。
    """
    from draft.link_index_maintenance import (
        commit_link_index_update,
        load_link_index_baseline,
        prepare_link_index_update,
    )

    prepared = prepare_link_index_update(
        root, known_documents=known_documents_from_graph(root, graph)
    )
    if prepared.scan is None and not prepared.requires_rebuild:
        baseline = load_link_index_baseline(root)
        return OpportunisticIndexResult(baseline.index, ())
    result = commit_link_index_update(root, prepared, root_state=root_state)
    if result.index is not None:
        return OpportunisticIndexResult(result.index, result.warnings)
    if prepared.scan is not None:
        return OpportunisticIndexResult(prepared.scan.content, result.warnings)
    return OpportunisticIndexResult(None, result.warnings)
