from __future__ import annotations

import posixpath
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from draft.diagnostic_codes import CheckCode
from draft.errors import DraftError
from draft.graph import (
    CONCLUSIONS_PAGE,
    ROOT_PAGE,
    DraftGraph,
    MarkerSnapshot,
    resolve_local_destination,
)
from draft.markdown import (
    append_parent_navigation,
    canonical_parent_navigation,
    insert_navigation_before_conclusions,
    navigation_removal_edits,
    rewrite_link_destinations,
)
from draft.models import (
    FileMutation,
    MarkdownLink,
    MutationKind,
    OperationKind,
    OperationPlan,
    OperationResult,
    SourceSpan,
)
from draft.observations import ObservationManifest
from draft.operation_facts import OperationFacts
from draft.operations import execute_plan, read_lock, sha256_bytes
from draft.paths import physical_parent, resolve_draft_root
from draft.plan_effects import PlanAction, PlanEffect
from draft.resources import (
    ResourceIdentity,
    ResourceKind,
    rebase_body_destination,
)
from draft.vocabulary import ConclusionsState, RelationCode


def _is_hub(path: Path) -> bool:
    return path.name == "index.md" and path != ROOT_PAGE


def _quote_destination(destination: str) -> str:
    return quote(destination, safe="/-._~")


def _relative_destination(from_page: Path, to_page: Path, anchor: str | None) -> str:
    base = from_page.parent.as_posix()
    relative = posixpath.relpath(to_page.as_posix(), base if base != "." else ".")
    encoded = _quote_destination(relative)
    return f"{encoded}#{anchor}" if anchor is not None else encoded


def _expand_mapping(graph: DraftGraph, page_map: Mapping[Path, Path]) -> dict[Path, Path]:
    expanded = dict(page_map)
    queue = list(page_map)
    while queue:
        old = queue.pop()
        for child in graph.children.get(old, ()):
            if child in expanded:
                raise DraftError("structure.conflict", child)
            base = expanded[old].parent
            if child.name == "index.md":
                expanded[child] = base / child.parent.name / "index.md"
            else:
                expanded[child] = base / child.name
            queue.append(child)
    targets = list(expanded.values())
    if len(set(targets)) != len(targets):
        raise DraftError("structure.duplicate_basename")
    for target in expanded.values():
        if target in graph.pages and target not in expanded:
            raise DraftError("structure.target_exists", target)
    return expanded


def _final_parents(
    graph: DraftGraph,
    expanded: Mapping[Path, Path],
    parent_changes: Mapping[Path, Path],
) -> dict[Path, Path]:
    final: dict[Path, Path] = {}
    for page in graph.pages:
        if page == ROOT_PAGE:
            continue
        new_parent = parent_changes.get(page, graph.parents.get(page))
        if new_parent is None:
            raise DraftError("structure.dirty", page)
        final[page] = expanded.get(new_parent, new_parent)
    return final


def _collapse_empty_hubs(
    graph: DraftGraph,
    expanded: dict[Path, Path],
    final_parents: Mapping[Path, Path],
) -> None:
    for page in graph.pages:
        if not _is_hub(page) or not graph.children.get(page):
            continue
        final_hub = expanded.get(page, page)
        if any(parent == final_hub for parent in final_parents.values()):
            continue
        leaf = final_hub.parent.parent / f"{final_hub.parent.name}.md"
        if leaf in graph.pages or leaf in expanded.values():
            raise DraftError("structure.target_exists", leaf)
        expanded[page] = leaf


def _render_pages(
    graph: DraftGraph,
    expanded: Mapping[Path, Path],
    parent_changes: Mapping[Path, Path],
    *,
    skip_conclusions_links: bool = False,
) -> dict[Path, str]:
    rendered: dict[Path, str] = {}
    nav_removals: dict[Path, list[MarkdownLink]] = {}
    nav_appends: dict[Path, list[tuple[str, str]]] = {}

    for child, new_parent in parent_changes.items():
        old_parent = graph.parents.get(child)
        if old_parent is None or old_parent == new_parent:
            continue
        navigation = canonical_parent_navigation(graph, child)
        if navigation is not None:
            nav_removals.setdefault(old_parent, []).append(navigation)
        final_child = expanded.get(child, child)
        final_parent = expanded.get(new_parent, new_parent)
        original_parent = next(
            (original for original, mapped in expanded.items() if mapped == new_parent),
            new_parent,
        )
        parent_page = graph.pages[original_parent]
        relation_spans = {relation.link.whole_span for relation in parent_page.relations}
        still_linked = False
        for link in parent_page.links:
            if link.is_image or link.whole_span in relation_spans:
                continue
            try:
                target, _anchor = resolve_local_destination(
                    original_parent, link.destination, graph.root
                )
            except DraftError:
                continue
            if expanded.get(target, target) == final_child:
                still_linked = True
                break
        if not still_linked:
            title = graph.pages[child].title
            destination = _relative_destination(final_parent, final_child, None)
            nav_appends.setdefault(original_parent, []).append((title, destination))

    removed_spans = {
        (owner, link.whole_span) for owner, links in nav_removals.items() for link in links
    }
    moved_targets = {
        ResourceIdentity(ResourceKind.PAGE, old.as_posix()): ResourceIdentity(
            ResourceKind.PAGE, new.as_posix()
        )
        for old, new in expanded.items()
    }

    for path, page in graph.pages.items():
        final_path = expanded.get(path, path)
        edits: dict[SourceSpan, str] = {}
        parent_relation = next(
            (relation for relation in page.relations if relation.code is RelationCode.PARENT),
            None,
        )
        parent = parent_changes.get(path, graph.parents.get(path))
        if parent is not None and parent_relation is not None:
            final_parent = expanded.get(parent, parent)
            page_moved = final_path != path
            parent_moved = final_parent != parent
            if page_moved or parent_moved:
                destination = _relative_destination(final_path, final_parent, None)
                if destination != parent_relation.link.destination:
                    edits[parent_relation.link.destination_span] = destination
        # 机器结论列表允许过时：所有权为 generated 时结构命令不重写结论页正文链接。
        if not (skip_conclusions_links and path == CONCLUSIONS_PAGE):
            body_by_span = {
                reference.link.whole_span: reference
                for reference in graph.body_resources.get(path, ())
            }
            for link in page.links:
                if (path, link.whole_span) in removed_spans:
                    continue
                if (
                    parent_relation is not None
                    and link.whole_span == parent_relation.link.whole_span
                ):
                    continue
                reference = body_by_span.get(link.whole_span)
                if reference is None:
                    continue
                resource = reference.resource
                if final_path == path and resource.identity not in moved_targets:
                    continue
                destination = rebase_body_destination(
                    resource,
                    new_source=final_path,
                    moved_targets=moved_targets,
                )
                if destination != link.destination:
                    edits.setdefault(link.destination_span, destination)
        for link in nav_removals.get(path, []):
            edits.update(navigation_removal_edits(page.text, link))
        text = rewrite_link_destinations(page.text, edits)
        for title, destination in nav_appends.get(path, []):
            if path == ROOT_PAGE:
                # 根导航结论链接由生成器管理，必须保持最后（规格 §12.4）。
                inserted = insert_navigation_before_conclusions(
                    text, title, destination, conclusions_target=CONCLUSIONS_PAGE
                )
                if inserted != text:
                    text = inserted
                    continue
            text = append_parent_navigation(text, title, destination)
        rendered[path] = text
    return rendered


def plan_path_remap(
    graph: DraftGraph,
    page_map: Mapping[Path, Path],
    parent_changes: Mapping[Path, Path],
    *,
    kind: OperationKind = OperationKind.MOVE,
    marker: MarkerSnapshot,
    observations: ObservationManifest,
) -> OperationPlan:
    # 结构变换不把全图 check_graph 结果当门禁：已可解析的无关全局错误不阻止本次
    # 操作（计划 Task 10 Step 1）。registered 的完整健康门禁由 load_operation_facts
    # 在规划前强制执行；candidate 只拒绝不可解析页（blocking_paths）。
    expanded = _expand_mapping(graph, page_map)
    final_parents = _final_parents(graph, expanded, parent_changes)
    for page, final_parent in final_parents.items():
        final_path = expanded.get(page, page)
        if physical_parent(final_path) != final_parent:
            raise DraftError("structure.parent_mismatch", page)
    _collapse_empty_hubs(graph, expanded, final_parents)
    # 降级改变枢纽最终路径后重新推导父映射；不级联再次降级。
    final_parents = _final_parents(graph, expanded, parent_changes)
    # 机器结论所有权只由 conclusions_state 判定：candidate 与 registered 的 generated
    # 都表示机器拥有 conclusions.md（round-15 M2），不得绑定 root_state。
    conclusions_generated = marker.state.conclusions_state is ConclusionsState.GENERATED
    rendered = _render_pages(
        graph,
        expanded,
        parent_changes,
        skip_conclusions_links=conclusions_generated,
    )

    mutations: list[FileMutation] = []
    effects: list[PlanEffect] = []
    for path in sorted(graph.pages, key=str):
        final_path = expanded.get(path, path)
        original_bytes = graph.pages[path].text.encode("utf-8")
        rendered_bytes = rendered[path].encode("utf-8")
        if final_path == path and rendered_bytes == original_bytes:
            continue
        if final_path != path:
            mutations.append(
                FileMutation(
                    final_path,
                    MutationKind.WRITE,
                    None,
                    sha256_bytes(rendered_bytes),
                    rendered_bytes,
                )
            )
            mutations.append(
                FileMutation(
                    path,
                    MutationKind.DELETE,
                    graph.page_digest(path),
                    None,
                    None,
                )
            )
            effects.append(PlanEffect(action=PlanAction.MOVE, from_path=path, to_path=final_path))
        else:
            mutations.append(
                FileMutation(
                    path,
                    MutationKind.WRITE,
                    graph.page_digest(path),
                    sha256_bytes(rendered_bytes),
                    rendered_bytes,
                )
            )
            effects.append(PlanEffect(action=PlanAction.MODIFY, path=path))
    writes = sorted(
        (mutation for mutation in mutations if mutation.kind is MutationKind.WRITE),
        key=lambda mutation: str(mutation.path),
    )
    deletes = sorted(
        (mutation for mutation in mutations if mutation.kind is MutationKind.DELETE),
        key=lambda mutation: str(mutation.path),
    )

    original_dirs = {directory for path in graph.pages for directory in _page_directories(path)}
    final_dirs = {directory for path in expanded.values() for directory in _page_directories(path)}
    untouched_dirs = {
        directory
        for path in graph.pages
        if path not in expanded
        for directory in _page_directories(path)
    }
    removable = sorted(
        original_dirs - final_dirs - untouched_dirs,
        key=lambda directory: len(directory.parts),
        reverse=True,
    )
    return OperationPlan(
        kind=kind,
        root=graph.root,
        mutations=tuple([*writes, *deletes]),
        root_state=marker.state.root_state,
        observations=observations,
        remove_empty_directories=tuple(removable),
        effects=tuple(effects),
    )


def _page_directories(path: Path) -> tuple[Path, ...]:
    directories: list[Path] = []
    current = path.parent
    while current.parts and current.parts[0] == "pages":
        directories.append(current)
        if current == Path("pages"):
            break
        current = current.parent
    return tuple(directories)


def _validate_operand(graph: DraftGraph, page: Path) -> Path:
    if page.is_absolute() or ".." in page.parts:
        raise DraftError("path.escape", page)
    if page.suffix != ".md":
        raise DraftError("path.not_markdown", page)
    if page not in graph.pages:
        raise DraftError("structure.unknown_page", page)
    return page


def _validate_target(graph: DraftGraph, target: Path) -> Path:
    if target.is_absolute() or ".." in target.parts:
        raise DraftError("path.escape", target)
    if target.suffix != ".md":
        raise DraftError("path.not_markdown", target)
    if not target.parts or target.parts[0] != "pages":
        raise DraftError("structure.invalid_target", target)
    if target in graph.pages:
        raise DraftError("structure.target_exists", target)
    parent = physical_parent(target)
    if parent is None or parent not in graph.pages:
        raise DraftError("structure.missing_parent", target)
    return target


def plan_move(
    graph: DraftGraph,
    source: Path,
    target: Path,
    *,
    marker: MarkerSnapshot,
    observations: ObservationManifest,
) -> OperationPlan:
    _validate_operand(graph, source)
    _validate_target(graph, target)
    if source in (ROOT_PAGE, CONCLUSIONS_PAGE):
        raise DraftError("structure.protected", source)
    if _is_hub(source):
        subtree_root = source.parent
        if target == subtree_root or target.is_relative_to(subtree_root):
            raise DraftError("structure.descendant", target)
    parent_changes: dict[Path, Path] = {}
    new_parent = physical_parent(target)
    if new_parent is not None and new_parent != graph.parents.get(source):
        parent_changes[source] = new_parent
    return plan_path_remap(
        graph,
        {source: target},
        parent_changes,
        kind=OperationKind.MOVE,
        marker=marker,
        observations=observations,
    )


def plan_promote(
    graph: DraftGraph,
    page: Path,
    children: tuple[Path, ...],
    *,
    marker: MarkerSnapshot,
    observations: ObservationManifest,
) -> OperationPlan:
    _validate_operand(graph, page)
    if page in (ROOT_PAGE, CONCLUSIONS_PAGE) or _is_hub(page):
        raise DraftError("structure.invalid_target", page)
    if graph.children.get(page):
        raise DraftError("structure.invalid_target", page)
    if not children:
        raise DraftError("structure.missing_child", page)
    hub = page.parent / page.stem / "index.md"
    if hub in graph.pages:
        raise DraftError("structure.target_exists", hub)
    page_map: dict[Path, Path] = {page: hub}
    parent_changes: dict[Path, Path] = {}
    for child in children:
        _validate_operand(graph, child)
        if child == page or child in page_map:
            raise DraftError("structure.invalid_target", child)
        if child.name == "index.md":
            target = hub.parent / child.parent.name / "index.md"
        else:
            target = hub.parent / child.name
        if target in page_map.values():
            raise DraftError("structure.duplicate_basename", target)
        if target in graph.pages:
            raise DraftError("structure.target_exists", target)
        page_map[child] = target
        parent_changes[child] = hub
    return plan_path_remap(
        graph,
        page_map,
        parent_changes,
        kind=OperationKind.PROMOTE,
        marker=marker,
        observations=observations,
    )


def plan_collapse(
    graph: DraftGraph,
    hub: Path,
    *,
    marker: MarkerSnapshot,
    observations: ObservationManifest,
) -> OperationPlan:
    _validate_operand(graph, hub)
    if hub == ROOT_PAGE:
        raise DraftError("structure.protected", hub)
    if not _is_hub(hub):
        raise DraftError("structure.not_hub", hub)
    if graph.children.get(hub):
        raise DraftError("structure.hub_not_empty", hub)
    leaf = hub.parent.parent / f"{hub.parent.name}.md"
    if leaf in graph.pages:
        raise DraftError("structure.target_exists", leaf)
    return plan_path_remap(
        graph,
        {hub: leaf},
        {},
        kind=OperationKind.COLLAPSE,
        marker=marker,
        observations=observations,
    )


def _load_clean_graph(root: Path, *, allow_empty_hub: Path | None = None) -> OperationFacts:
    """结构变换的事实加载：registered 全绿；candidate 全受管页可解析即可。

    任一不可解析页面可能隐藏正文链接、正式关系或父导航，因此必须阻止结构变换；
    已可解析的无关全局错误（依赖环、状态/取代不匹配、容量提醒）不阻止本次操作。
    ``allow_empty_hub`` 供 collapse 容忍目标枢纽的 HUB_EMPTY。返回 OperationFacts：
    图、marker snapshot、阻碍与读集观察全部来自同一次根事实读取（round-15 H1）。
    """
    from draft.operation_facts import load_operation_facts

    resolved = resolve_draft_root(root)
    if read_lock(resolved) is not None:
        raise DraftError(CheckCode.OPERATION_RECOVERY_REQUIRED.value, resolved)
    facts = load_operation_facts(resolved, allow_hub_empty=allow_empty_hub)
    if facts.blocking_paths:
        details = tuple(path.as_posix() for path in facts.blocking_paths)
        raise DraftError("structure.dirty", resolved, details=details)
    return facts


def move_page(
    root: Path, source: Path, target: Path, *, now: datetime | None = None
) -> OperationResult:
    del now  # 前置推进由 CLI 编排统一完成；领域函数不再重复调用。
    resolved = resolve_draft_root(root)
    facts = _load_clean_graph(resolved)
    plan = plan_move(
        facts.graph,
        source,
        target,
        marker=facts.marker,
        observations=facts.observations,
    )
    return execute_plan(plan)


def promote_page(
    root: Path, page: Path, children: list[Path], *, now: datetime | None = None
) -> OperationResult:
    del now  # 前置推进由 CLI 编排统一完成；领域函数不再重复调用。
    resolved = resolve_draft_root(root)
    facts = _load_clean_graph(resolved)
    plan = plan_promote(
        facts.graph,
        page,
        tuple(children),
        marker=facts.marker,
        observations=facts.observations,
    )
    return execute_plan(plan)


def collapse_hub(root: Path, hub: Path, *, now: datetime | None = None) -> OperationResult:
    del now  # 前置推进由 CLI 编排统一完成；领域函数不再重复调用。
    resolved = resolve_draft_root(root)
    facts = _load_clean_graph(resolved, allow_empty_hub=hub)
    plan = plan_collapse(
        facts.graph,
        hub,
        marker=facts.marker,
        observations=facts.observations,
    )
    return execute_plan(plan)
