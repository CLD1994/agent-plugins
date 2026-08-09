from __future__ import annotations

import posixpath
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from draft.checker import check_draft, check_graph
from draft.diagnostic_codes import CheckCode
from draft.errors import DraftError
from draft.graph import (
    CONCLUSIONS_PAGE,
    ROOT_PAGE,
    DraftGraph,
    load_graph,
    resolve_local_destination,
)
from draft.markdown import (
    append_parent_navigation,
    canonical_parent_navigation,
    navigation_removal_edits,
    rewrite_link_destinations,
)
from draft.models import (
    FileMutation,
    MarkdownLink,
    MutationKind,
    OperationPlan,
    OperationResult,
    RelationLabel,
    SourceSpan,
)
from draft.operations import execute_plan, read_lock, sha256_bytes
from draft.paths import physical_parent, resolve_draft_root
from draft.trash_lifecycle import collect_lifecycle_warnings, prepare_structural_write


def _with_preflight_warnings(result: OperationResult, report: object) -> OperationResult:
    warnings = collect_lifecycle_warnings(report)  # type: ignore[arg-type]
    if not warnings:
        return result
    return OperationResult(
        operation_id=result.operation_id,
        kind=result.kind,
        phase=result.phase,
        changed_paths=result.changed_paths,
        warnings=warnings,
    )


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

    for path, page in graph.pages.items():
        final_path = expanded.get(path, path)
        edits: dict[SourceSpan, str] = {}
        parent_relation = next(
            (relation for relation in page.relations if relation.label is RelationLabel.PARENT),
            None,
        )
        if path in parent_changes and parent_relation is not None:
            new_parent = parent_changes[path]
            final_parent = expanded.get(new_parent, new_parent)
            destination = _relative_destination(final_path, final_parent, None)
            if destination != parent_relation.link.destination:
                edits[parent_relation.link.destination_span] = destination
        for link in page.links:
            if (path, link.whole_span) in removed_spans:
                continue
            if (
                path in parent_changes
                and parent_relation is not None
                and link.whole_span == parent_relation.link.whole_span
            ):
                continue
            try:
                target, anchor = resolve_local_destination(path, link.destination, graph.root)
            except DraftError:
                continue
            final_target = expanded.get(target, target)
            if final_target == target and final_path == path:
                continue
            destination = _relative_destination(final_path, final_target, anchor)
            if destination != link.destination:
                edits.setdefault(link.destination_span, destination)
        for link in nav_removals.get(path, []):
            edits.update(navigation_removal_edits(page.text, link))
        text = rewrite_link_destinations(page.text, edits)
        for title, destination in nav_appends.get(path, []):
            text = append_parent_navigation(text, title, destination)
        rendered[path] = text
    return rendered


def plan_path_remap(
    graph: DraftGraph,
    page_map: Mapping[Path, Path],
    parent_changes: Mapping[Path, Path],
    *,
    kind: str = "move",
    allow_empty_hub: Path | None = None,
) -> OperationPlan:
    blocking = [
        issue
        for issue in check_graph(graph).errors
        if not (
            allow_empty_hub is not None
            and issue.code == CheckCode.HUB_EMPTY.value
            and issue.path == allow_empty_hub
        )
    ]
    if blocking:
        raise DraftError("structure.dirty", graph.root)
    expanded = _expand_mapping(graph, page_map)
    final_parents = _final_parents(graph, expanded, parent_changes)
    for page, final_parent in final_parents.items():
        final_path = expanded.get(page, page)
        if physical_parent(final_path) != final_parent:
            raise DraftError("structure.parent_mismatch", page)
    _collapse_empty_hubs(graph, expanded, final_parents)
    # 降级改变枢纽最终路径后重新推导父映射；不级联再次降级。
    final_parents = _final_parents(graph, expanded, parent_changes)
    rendered = _render_pages(graph, expanded, parent_changes)

    mutations: list[FileMutation] = []
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
                    sha256_bytes(original_bytes),
                    None,
                    None,
                )
            )
        else:
            mutations.append(
                FileMutation(
                    path,
                    MutationKind.WRITE,
                    sha256_bytes(original_bytes),
                    sha256_bytes(rendered_bytes),
                    rendered_bytes,
                )
            )
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
        validation_mode="registered",
        remove_empty_directories=tuple(removable),
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


def plan_move(graph: DraftGraph, source: Path, target: Path) -> OperationPlan:
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
    return plan_path_remap(graph, {source: target}, parent_changes, kind="move")


def plan_promote(graph: DraftGraph, page: Path, children: tuple[Path, ...]) -> OperationPlan:
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
    return plan_path_remap(graph, page_map, parent_changes, kind="promote")


def plan_collapse(graph: DraftGraph, hub: Path) -> OperationPlan:
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
    return plan_path_remap(graph, {hub: leaf}, {}, kind="collapse", allow_empty_hub=hub)


def _load_clean_graph(root: Path, *, allow_empty_hub: Path | None = None) -> DraftGraph:
    resolved = resolve_draft_root(root)
    if read_lock(resolved) is not None:
        raise DraftError(CheckCode.OPERATION_RECOVERY_REQUIRED.value, resolved)
    report = check_draft(resolved)
    errors = [
        issue
        for issue in report.errors
        if not (
            allow_empty_hub is not None
            and issue.code == CheckCode.HUB_EMPTY.value
            and issue.path == allow_empty_hub
        )
    ]
    if errors:
        codes = tuple(issue.code for issue in errors)
        raise DraftError("structure.dirty", resolved, details=codes)
    return load_graph(resolved)


def move_page(
    root: Path, source: Path, target: Path, *, now: datetime | None = None
) -> OperationResult:
    resolved = resolve_draft_root(root)
    preflight = prepare_structural_write(resolved, now=now)
    graph = _load_clean_graph(resolved)
    plan = plan_move(graph, source, target)
    return _with_preflight_warnings(execute_plan(plan), preflight)


def promote_page(
    root: Path, page: Path, children: list[Path], *, now: datetime | None = None
) -> OperationResult:
    resolved = resolve_draft_root(root)
    preflight = prepare_structural_write(resolved, now=now)
    graph = _load_clean_graph(resolved)
    plan = plan_promote(graph, page, tuple(children))
    return _with_preflight_warnings(execute_plan(plan), preflight)


def collapse_hub(root: Path, hub: Path, *, now: datetime | None = None) -> OperationResult:
    resolved = resolve_draft_root(root)
    preflight = prepare_structural_write(resolved, now=now)
    graph = _load_clean_graph(resolved, allow_empty_hub=hub)
    plan = plan_collapse(graph, hub)
    return _with_preflight_warnings(execute_plan(plan), preflight)
