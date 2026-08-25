from __future__ import annotations

import posixpath
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from urllib.parse import quote

from draft.diagnostic_codes import (
    OPERATION_EXTERNAL_CHANGE,
    OPERATION_INDEX_UPDATE_FAILED,
    CheckCode,
)
from draft.errors import DraftError
from draft.graph import (
    CONCLUSIONS_PAGE,
    INTERNAL_DIR,
    ROOT_PAGE,
    DraftGraph,
    MarkerSnapshot,
    resolve_local_destination,
)
from draft.link_index import LinkIndex
from draft.link_index_maintenance import load_link_index_baseline, scan_link_index
from draft.markdown import (
    append_parent_navigation,
    canonical_parent_navigation,
    extract_body_links,
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
from draft.move_closure import (
    MoveClosure,
    merged_blocking,
    scan_indexed_move_closure,
    scan_move_closure,
)
from draft.move_operands import (
    MoveRequest,
    SourceKind,
    classify_move_request,
    precheck_move_shape,
    validate_results,
)
from draft.observations import (
    ObservationManifest,
    directory_observation,
    file_digest_observation,
    file_observation,
    merge_observations,
)
from draft.operation_facts import OperationFacts, load_operation_facts
from draft.operations import execute_plan, read_lock, sha256_bytes
from draft.paths import physical_parent, resolve_draft_root
from draft.plan_effects import PlanAction, PlanEffect
from draft.resources import (
    ProbeState,
    ResourceIdentity,
    ResourceKind,
    rebase_body_destination,
    resolve_body_resource,
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
    if len(set(expanded.values())) != len(expanded):
        # 规格 §2.4：批量内目标唯一性碰撞以 structure.conflict 点名双方来源。
        seen: dict[Path, Path] = {}
        for old, new in expanded.items():
            if new in seen:
                raise DraftError(
                    "structure.conflict", new, details=(old.as_posix(), seen[new].as_posix())
                )
            seen[new] = old
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
    extra_moved_targets: Mapping[ResourceIdentity, ResourceIdentity] | None = None,
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
    if extra_moved_targets is not None:
        moved_targets.update(extra_moved_targets)

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
        # 非父正式关系（依赖/取代/质疑及反向）的链接目标：声明页或目标任一移动都必须
        # 重算相对目标，与正文链接同一规则（规格 §9.1 "全部可受影响链接一致"）。
        for relation in page.relations:
            if relation.code is RelationCode.PARENT:
                continue
            try:
                target, _anchor = resolve_local_destination(
                    path, relation.link.destination, graph.root
                )
            except DraftError:
                continue
            final_target = expanded.get(target, target)
            if final_path == path and final_target == target:
                continue
            destination = _relative_destination(final_path, final_target, None)
            if destination != relation.link.destination:
                edits.setdefault(relation.link.destination_span, destination)
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


def _assemble_managed_changes(
    graph: DraftGraph,
    expanded: Mapping[Path, Path],
    rendered: Mapping[Path, str],
) -> tuple[list[FileMutation], list[PlanEffect]]:
    """把渲染结果集与展开映射组装为受管理页 mutation/effect。

    plan_path_remap 与纯未受管理移动分支共用同一组装（M2）：``final_path != path``
    产生 WRITE+DELETE 移动对，原地仅产生 MODIFY；字节恒等者跳过。
    """
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
    return mutations, effects


def plan_path_remap(
    graph: DraftGraph,
    page_map: Mapping[Path, Path],
    parent_changes: Mapping[Path, Path],
    *,
    kind: OperationKind = OperationKind.MOVE,
    marker: MarkerSnapshot,
    observations: ObservationManifest,
    extra_moved_targets: Mapping[ResourceIdentity, ResourceIdentity] | None = None,
    pre_expanded: Mapping[Path, Path] | None = None,
) -> OperationPlan:
    # 结构变换不把全图 check_graph 结果当门禁：已可解析的无关全局错误不阻止本次
    # 操作（计划 Task 10 Step 1）。registered 的完整健康门禁由 load_operation_facts
    # 在规划前强制执行；candidate 只拒绝不可解析页（blocking_paths）。
    expanded = dict(pre_expanded) if pre_expanded is not None else _expand_mapping(graph, page_map)
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
        extra_moved_targets=extra_moved_targets,
    )

    mutations, effects = _assemble_managed_changes(graph, expanded, rendered)
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
    extra_moved_targets: Mapping[ResourceIdentity, ResourceIdentity] | None = None,
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
        extra_moved_targets=extra_moved_targets,
    )


def plan_move_request(
    graph: DraftGraph,
    closure: MoveClosure,
    request: MoveRequest,
    *,
    marker: MarkerSnapshot,
    observations: ObservationManifest,
) -> OperationPlan:
    validate_results(graph, request)

    managed_pairs: dict[Path, Path] = {}
    physical_pairs: dict[Path, Path] = {}
    unmanaged_moves: dict[Path, Path] = {}
    for source in request.sources:
        for member in source.members:
            result = request.results[member]
            if member in graph.pages:
                if source.kind is SourceKind.DIRECTORY:
                    physical_pairs[member] = result
                else:
                    managed_pairs[member] = result
            else:
                unmanaged_moves[member] = result

    expanded = _expand_mapping(graph, managed_pairs) if managed_pairs else {}
    expanded = {**expanded, **physical_pairs}
    _validate_cross_collisions(graph, expanded, unmanaged_moves)

    parent_changes: dict[Path, Path] = {}
    for member, result in physical_pairs.items():
        parent = physical_parent(result)
        if parent is None:
            continue
        old_parent = graph.parents.get(member)
        moved_old_parent = expanded.get(old_parent, old_parent) if old_parent is not None else None
        if moved_old_parent != parent:
            parent_changes[member] = parent
    for member, result in managed_pairs.items():
        parent = physical_parent(result)
        if parent is None:
            continue
        old_parent = graph.parents.get(member)
        moved_old_parent = expanded.get(old_parent, old_parent) if old_parent is not None else None
        if moved_old_parent != parent:
            parent_changes[member] = parent

    moved_targets = {
        ResourceIdentity(ResourceKind.PAGE, old.as_posix()): ResourceIdentity(
            ResourceKind.PAGE, new.as_posix()
        )
        for old, new in expanded.items()
    }
    unmanaged_identities = _unmanaged_moved_identities(graph.root, unmanaged_moves)
    moved_targets.update(unmanaged_identities)

    if expanded or parent_changes:
        managed_plan = plan_path_remap(
            graph,
            {**managed_pairs, **physical_pairs},
            parent_changes,
            kind=OperationKind.MOVE,
            marker=marker,
            observations=observations,
            pre_expanded=expanded,
            extra_moved_targets=unmanaged_identities,
        )
        base_mutations = list(managed_plan.mutations)
        base_effects = list(managed_plan.effects)
        removable = managed_plan.remove_empty_directories
    elif unmanaged_moves:
        # 无受管理移动但存在未受管理来源移动：受管理链接者仍须重写指向移动目标的正文
        # 链接。此路径只重渲染正文，不经过 plan_path_remap 的父映射门禁——未声明父
        # 关系的受管理页仍可参与链接重写（纯未受管理移动不改变父结构）。展开为空时
        # _assemble_managed_changes 只产生原地 MODIFY，与受管理路径同一组装。
        conclusions_generated = marker.state.conclusions_state is ConclusionsState.GENERATED
        rendered = _render_pages(
            graph,
            {},
            {},
            skip_conclusions_links=conclusions_generated,
            extra_moved_targets=unmanaged_identities,
        )
        base_mutations, base_effects = _assemble_managed_changes(graph, {}, rendered)
        removable: tuple[Path, ...] = ()
    else:
        base_mutations = list[FileMutation]()
        base_effects = list[PlanEffect]()
        removable = ()

    unmanaged_mutations, unmanaged_effects = _plan_unmanaged(
        graph.root, closure, unmanaged_moves, moved_targets
    )
    writes = [m for m in [*base_mutations, *unmanaged_mutations] if m.kind is MutationKind.WRITE]
    deletes = [m for m in [*base_mutations, *unmanaged_mutations] if m.kind is MutationKind.DELETE]
    writes.sort(key=lambda m: m.path.as_posix())
    deletes.sort(key=lambda m: m.path.as_posix())
    closure_observations = ObservationManifest(
        entries=tuple(
            [
                *(
                    file_digest_observation(path, file.sha256)
                    for path, file in closure.unmanaged.items()
                    if file.sha256 is not None
                ),
                *(
                    directory_observation(path, tuple(name for name, _ in members), members)
                    for path, members in closure.directories.items()
                ),
            ]
        )
    )
    return OperationPlan(
        kind=OperationKind.MOVE,
        root=graph.root,
        mutations=tuple([*writes, *deletes]),
        root_state=marker.state.root_state,
        observations=merge_observations(observations, closure_observations),
        remove_empty_directories=removable,
        effects=tuple([*base_effects, *unmanaged_effects]),
    )


def _validate_cross_collisions(
    graph: DraftGraph,
    expanded: Mapping[Path, Path],
    unmanaged_moves: Mapping[Path, Path],
) -> None:
    combined = {**expanded, **unmanaged_moves}
    if len(set(combined.values())) != len(combined):
        # 规格 §2.4：跨类型（受管理展开 ∪ 未受管理）结果碰撞以 structure.conflict 点名双方。
        seen: dict[Path, Path] = {}
        for old, new in combined.items():
            if new in seen:
                raise DraftError(
                    "structure.conflict", new, details=(old.as_posix(), seen[new].as_posix())
                )
            seen[new] = old
    for target in unmanaged_moves.values():
        if target in graph.pages and target not in expanded:
            raise DraftError("structure.target_exists", target)


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


def _load_structure_facts(root: Path, *, allow_empty_hub: Path | None = None) -> OperationFacts:
    if read_lock(root) is not None:
        raise DraftError(CheckCode.OPERATION_RECOVERY_REQUIRED.value, root)
    return load_operation_facts(root, allow_hub_empty=allow_empty_hub)


def _load_clean_graph(root: Path, *, allow_empty_hub: Path | None = None) -> OperationFacts:
    """旧入口（move_page/promote_page/collapse_hub）的事实加载：任何 blocking 即拒绝。

    blocking 即拒绝的根因：这些入口走 plan_move/plan_promote/plan_collapse，链接改写
    依赖整图可解析；blocking_paths 非空时以部分事实改写链接不可靠，故直接以
    structure.dirty 停止。统一 move 命令（move_entries）不在此列——它经 scan_move_closure
    把未受管理链接者纳入闭包后用 merged_blocking 一并判断（规格 §4），事实加载不设
    全局拒绝。
    """
    facts = _load_structure_facts(root, allow_empty_hub=allow_empty_hub)
    if facts.blocking_paths:
        details = tuple(path.as_posix() for path in facts.blocking_paths)
        raise DraftError("structure.dirty", root, details=details)
    return facts


def _indexed_closure_for_plan(root: Path, closure: MoveClosure) -> MoveClosure:
    """让索引闭包的根目录观察与严格扫描惯例一致（规格 §7/§8 组合边界）。

    v2 索引目录快照排除整个 ``.draft``（spec §7），而严格扫描闭包和
    ``graph_observations`` 的根目录成员都包含 ``.draft``；不补齐则读集合并按同路径
    冲突拒绝。按 ``scan_move_closure._record_directory`` 同一分类惯例补充该成员。
    """
    members = closure.directories.get(Path("."))
    if members is None or any(name == INTERNAL_DIR for name, _kind in members):
        return closure
    internal = root / INTERNAL_DIR
    try:
        # 与 scan_move_closure._record_directory 同一分类顺序（is_dir 起、跟随符号链接）。
        kind = (
            "directory"
            if internal.is_dir()
            else "file"
            if internal.is_file()
            else "symlink"
            if internal.is_symlink()
            else "other"
        )
    except OSError:
        return closure  # 无法分类时不补充：执行前读集复核按实际状态判定。
    patched = dict(closure.directories)
    patched[Path(".")] = tuple(sorted((*members, (INTERNAL_DIR, kind))))
    return replace(closure, directories=patched)


def plan_move_from_arguments(
    root: Path,
    facts: OperationFacts,
    source_values: Sequence[str],
    target_value: str,
) -> OperationPlan:
    request = classify_move_request(root, facts.graph, source_values, target_value)
    moved_paths = {member for source in request.sources for member in source.members}
    moved_identities: set[ResourceIdentity] = set()
    for member in moved_paths:
        if member in facts.graph.pages:
            moved_identities.add(ResourceIdentity(ResourceKind.PAGE, member.as_posix()))
        else:
            moved_identities.add(resolve_body_resource(root, ROOT_PAGE, member.as_posix()).identity)
    # 闭包只消费 v2 索引事实：先验证发布制品的可信任基线，再严格扫描证明 fresh；
    # 无法证明 fresh 时使用严格全根扫描事实（spec §8/§9，用户故事 8/9）。不再依赖
    # v1 维护层（Task 6 迁移）。
    baseline = load_link_index_baseline(root)
    index: LinkIndex | None = None
    if baseline.index is not None:
        try:
            scan = scan_link_index(root, baseline)
        except DraftError as exc:
            if exc.code in (OPERATION_INDEX_UPDATE_FAILED, OPERATION_EXTERNAL_CHANGE):
                # 扫描失败或根不稳定：无法证明 fresh，回退到严格全根扫描。
                closure = scan_move_closure(root, facts.graph)
            else:
                raise
        else:
            if scan.changed:
                # 根事实已相对发布制品变化：本次规划按严格全根扫描事实执行；
                # 索引收敛由通用 operation 索引阶段完成（spec §9 增量维护）。
                closure = scan_move_closure(root, facts.graph)
            else:
                index = baseline.index
                closure = _indexed_closure_for_plan(
                    root,
                    scan_indexed_move_closure(
                        root, facts.graph, index, moved_identities, moved_paths
                    ),
                )
    else:
        closure = scan_move_closure(root, facts.graph)
    blocking = merged_blocking(facts.blocking_paths, closure)
    if blocking:
        raise DraftError("structure.dirty", root, details=tuple(p.as_posix() for p in blocking))
    plan = plan_move_request(
        facts.graph, closure, request, marker=facts.marker, observations=facts.observations
    )
    if index is not None:
        # 读集观察覆盖本次规划读取的索引制品：规划与执行之间制品变化即外部变化。
        return replace(
            plan,
            observations=merge_observations(
                plan.observations,
                ObservationManifest(
                    entries=(file_observation(Path(".draft/link-index.json"), index.to_json()),)
                ),
            ),
        )
    return plan


def move_entries(root: Path, source_values: Sequence[str], target_value: str) -> OperationResult:
    resolved = resolve_draft_root(root)
    precheck_move_shape(resolved, source_values, target_value)
    facts = _load_structure_facts(resolved)
    plan = plan_move_from_arguments(resolved, facts, source_values, target_value)
    return execute_plan(plan)


def move_page(root: Path, source: Path, target: Path) -> OperationResult:
    """程序化单页入口，保留单页校验并共享完整入站链接闭包。"""
    resolved = resolve_draft_root(root)
    precheck_move_shape(resolved, (source.as_posix(),), target.as_posix())
    facts = _load_clean_graph(resolved)
    closure = scan_move_closure(resolved, facts.graph)
    blocking = merged_blocking(facts.blocking_paths, closure)
    if blocking:
        raise DraftError(
            "structure.dirty", resolved, details=tuple(path.as_posix() for path in blocking)
        )
    moved_targets = {
        ResourceIdentity(ResourceKind.PAGE, source.as_posix()): ResourceIdentity(
            ResourceKind.PAGE, target.as_posix()
        )
    }
    plan = plan_move(
        facts.graph,
        source,
        target,
        marker=facts.marker,
        observations=facts.observations,
        extra_moved_targets=moved_targets,
    )
    unmanaged_mutations, unmanaged_effects = _plan_unmanaged(resolved, closure, {}, moved_targets)
    closure_observations = ObservationManifest(
        entries=tuple(
            [
                *(
                    file_digest_observation(path, file.sha256)
                    for path, file in closure.unmanaged.items()
                    if file.sha256 is not None
                ),
                *(
                    directory_observation(path, tuple(name for name, _ in members), members)
                    for path, members in closure.directories.items()
                ),
            ]
        )
    )
    final_plan = replace(
        plan,
        mutations=tuple([*plan.mutations, *unmanaged_mutations]),
        observations=merge_observations(plan.observations, closure_observations),
        effects=tuple([*plan.effects, *unmanaged_effects]),
    )
    # move 的正式执行以 execute_plan() 返回为边界：索引发布由通用 operation 索引阶段
    # 在锁内完成，返回后不再另行发布（spec §5/§6，Task 6 停用 _refresh_move_index）。
    return execute_plan(final_plan)


def promote_page(root: Path, page: Path, children: list[Path]) -> OperationResult:
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


def collapse_hub(root: Path, hub: Path) -> OperationResult:
    resolved = resolve_draft_root(root)
    facts = _load_clean_graph(resolved, allow_empty_hub=hub)
    plan = plan_collapse(
        facts.graph,
        hub,
        marker=facts.marker,
        observations=facts.observations,
    )
    return execute_plan(plan)


def _unmanaged_moved_identities(
    root: Path, unmanaged_moves: Mapping[Path, Path]
) -> dict[ResourceIdentity, ResourceIdentity]:
    """未受管理旧路径的资源身份按词法分类解析（根级/非 pages 为 LOCAL），
    与链接者侧的解析保持一致，保证 moved_targets 命中。"""

    def _present(_path: Path) -> ProbeState:
        return ProbeState.FILE

    identities: dict[ResourceIdentity, ResourceIdentity] = {}
    for old, new in unmanaged_moves.items():
        old_identity = resolve_body_resource(
            root, ROOT_PAGE, old.as_posix(), target_probe=_present
        ).identity
        new_identity = resolve_body_resource(
            root, ROOT_PAGE, new.as_posix(), target_probe=_present
        ).identity
        identities[old_identity] = new_identity
    return identities


def _assert_link_extractable(path: Path, text: str) -> None:
    try:
        extract_body_links(text)
    except DraftError as error:
        raise DraftError("operation.plan_invalid", path, details=(error.code,)) from error


def _plan_unmanaged(
    root: Path,
    closure: MoveClosure,
    unmanaged_moves: Mapping[Path, Path],
    moved_targets: Mapping[ResourceIdentity, ResourceIdentity],
) -> tuple[list[FileMutation], list[PlanEffect]]:
    mutations: list[FileMutation] = []
    effects: list[PlanEffect] = []
    for path in sorted(closure.unmanaged, key=lambda item: item.as_posix()):
        file = closure.unmanaged[path]
        new_source = unmanaged_moves.get(path, path)
        edits: dict[SourceSpan, str] = {}
        for reference in file.references:
            destination = rebase_body_destination(
                reference.resource, new_source=new_source, moved_targets=moved_targets
            )
            if destination != reference.link.destination:
                edits[reference.link.destination_span] = destination
        rendered = rewrite_link_destinations(file.text, edits) if edits else file.text
        if path in unmanaged_moves:
            _assert_link_extractable(new_source, rendered)
            rendered_bytes = rendered.encode("utf-8")
            mutations.append(
                FileMutation(
                    new_source,
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
                    sha256_bytes(file.text.encode("utf-8")),
                    None,
                    None,
                )
            )
            effects.append(PlanEffect(action=PlanAction.MOVE, from_path=path, to_path=new_source))
        elif rendered != file.text:
            # 规格 §4.3：本命令"改写"的文件同样必须可被对应解析器读取，与移动分支一致。
            _assert_link_extractable(path, rendered)
            rendered_bytes = rendered.encode("utf-8")
            mutations.append(
                FileMutation(
                    path,
                    MutationKind.WRITE,
                    sha256_bytes(file.text.encode("utf-8")),
                    sha256_bytes(rendered_bytes),
                    rendered_bytes,
                )
            )
            effects.append(PlanEffect(action=PlanAction.MODIFY, path=path))
    return mutations, effects
