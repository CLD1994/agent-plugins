from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from draft.errors import DraftError
from draft.graph import (
    CONCLUSIONS_PAGE,
    INTERNAL_DIR,
    ROOT_PAGE,
    DraftGraph,
    LoadMode,
    load_graph_tolerant,
    resolve_local_destination,
)
from draft.models import (
    CheckIssue,
    CheckReport,
    IssueSeverity,
    OperationPhase,
    RelationLabel,
    StatusPrefix,
)
from draft.operations import (
    Journal,
    TrashDeadlines,
    has_handoff_failure,
    operation_journal_path,
    reject_symlink_components,
)
from draft.paths import physical_parent, resolve_draft_root
from draft.trash_package import OUTGOING_RELATIVE, load_verified_package

CAPACITY_LINES = 600
CAPACITY_BYTES = 60 * 1024


def _error(code: str, path: Path | None, line: int | None = None, *details: str) -> CheckIssue:
    return CheckIssue(
        code=code, severity=IssueSeverity.ERROR, path=path, line=line, details=details
    )


def _warning(code: str, path: Path | None, *details: str) -> CheckIssue:
    return CheckIssue(
        code=code, severity=IssueSeverity.WARNING, path=path, line=None, details=details
    )


def _info(code: str, path: Path | None, *details: str) -> CheckIssue:
    return CheckIssue(code=code, severity=IssueSeverity.INFO, path=path, line=None, details=details)


def _review(code: str, path: Path | None, *details: str) -> CheckIssue:
    return CheckIssue(
        code=code, severity=IssueSeverity.REVIEW, path=path, line=None, details=details
    )


def _sort_key(issue: CheckIssue) -> tuple[str, int, str]:
    return ("" if issue.path is None else str(issue.path), issue.line or 0, issue.code)


def _canonical_cycle(cycle: list[Path]) -> tuple[Path, ...]:
    smallest = min(range(len(cycle)), key=lambda index: cycle[index])
    return tuple(cycle[smallest:] + cycle[:smallest])


def _find_cycles(edges: Mapping[Path, tuple[Path, ...]]) -> list[tuple[Path, ...]]:
    """Iterative color-marked DFS returning each cycle once, canonically rotated."""
    color: dict[Path, int] = {}
    cycles: set[tuple[Path, ...]] = set()
    for start in sorted(edges):
        if color.get(start, 0) != 0:
            continue
        color[start] = 1
        stack: list[tuple[Path, int]] = [(start, 0)]
        path: list[Path] = [start]
        while stack:
            node, next_index = stack[-1]
            neighbors = edges.get(node, ())
            if next_index < len(neighbors):
                stack[-1] = (node, next_index + 1)
                neighbor = neighbors[next_index]
                state = color.get(neighbor, 0)
                if state == 0:
                    color[neighbor] = 1
                    stack.append((neighbor, 0))
                    path.append(neighbor)
                elif state == 1:
                    begin = path.index(neighbor)
                    cycles.add(_canonical_cycle(path[begin:]))
            else:
                color[node] = 2
                stack.pop()
                path.pop()
    return sorted(cycles)


def _multi_edges(graph: DraftGraph, label: RelationLabel) -> dict[Path, tuple[Path, ...]]:
    edges: dict[Path, set[Path]] = {}
    for source, target, _line in graph.relations.get(label, ()):
        edges.setdefault(source, set()).add(target)
    return {source: tuple(sorted(targets)) for source, targets in edges.items()}


def _check_relation_duplicates(graph: DraftGraph) -> list[CheckIssue]:
    """同一来源页的同一标签重复指向同一规范化目标（父主题由基数检查覆盖）。"""
    issues: list[CheckIssue] = []
    for label in RelationLabel:
        if label is RelationLabel.PARENT:
            continue
        seen: dict[tuple[Path, Path], list[int]] = {}
        for source, target, line in graph.relations.get(label, ()):
            seen.setdefault((source, target), []).append(line)
        for (source, target), lines in sorted(seen.items()):
            if len(lines) > 1:
                issues.append(
                    _error(
                        "relation.duplicate",
                        source,
                        sorted(lines)[1],
                        f"{label.value} 重复指向 {target}",
                    )
                )
    return issues


def _body_links_to(graph: DraftGraph, source: Path) -> set[Path]:
    """Ordinary (non-relation, non-image) resolved targets of one page's links."""
    page = graph.pages.get(source)
    if page is None:
        return set()
    relation_spans = {relation.link.whole_span for relation in page.relations}
    targets: set[Path] = set()
    for link in page.links:
        if link.is_image or link.whole_span in relation_spans:
            continue
        try:
            target, _anchor = resolve_local_destination(source, link.destination, graph.root)
        except DraftError:
            continue
        targets.add(target)
    return targets


def _check_parents(graph: DraftGraph) -> list[CheckIssue]:
    issues: list[CheckIssue] = []
    parent_edges = _multi_edges(graph, RelationLabel.PARENT)
    single_edges = {source: targets for source, targets in parent_edges.items()}

    for path, page in graph.pages.items():
        declared = [
            relation for relation in page.relations if relation.label is RelationLabel.PARENT
        ]
        if path == ROOT_PAGE:
            if declared:
                issues.append(_error("parent.root_declared", path, declared[0].link.line))
            continue
        if not declared:
            issues.append(_error("parent.missing", path))
        elif len(declared) > 1:
            issues.append(_error("parent.multiple", path, declared[1].link.line))
        elif len(single_edges.get(path, ())) == 1:
            physical = physical_parent(path)
            declared_target = single_edges[path][0]
            if physical is not None and declared_target != physical:
                issues.append(
                    _error(
                        "layout.parent_mismatch",
                        path,
                        declared[0].link.line,
                        f"声明 {declared_target}，物理 {physical}",
                    )
                )

    cycles = _find_cycles({source: targets for source, targets in parent_edges.items()})
    cycle_members = {member for cycle in cycles for member in cycle}
    for cycle in cycles:
        issues.append(_error("parent.cycle", cycle[0], None, " -> ".join(map(str, cycle))))

    reachable: set[Path] = set()
    frontier = [ROOT_PAGE]
    while frontier:
        current = frontier.pop()
        if current in reachable:
            continue
        reachable.add(current)
        frontier.extend(graph.children.get(current, ()))
    for path in graph.pages:
        if path == ROOT_PAGE or path in reachable or path in cycle_members:
            continue
        issues.append(_error("parent.unreachable", path))

    for child, parent in sorted(graph.parents.items()):
        if parent not in graph.pages:
            continue
        if child not in _body_links_to(graph, parent):
            issues.append(_error("parent.navigation_missing", parent, None, str(child)))

    for path in graph.pages:
        if path.name == ROOT_PAGE.name and path != ROOT_PAGE and not graph.children.get(path):
            issues.append(_error("hub.empty", path))
    return issues


def _check_dependencies(graph: DraftGraph) -> list[CheckIssue]:
    issues: list[CheckIssue] = []
    edges = _multi_edges(graph, RelationLabel.DEPENDS_ON)
    for source, targets in edges.items():
        if source in targets:
            issues.append(_error("dependency.self", source))
    non_self = {
        source: tuple(target for target in targets if target != source)
        for source, targets in edges.items()
    }
    for cycle in _find_cycles(non_self):
        issues.append(_error("dependency.cycle", cycle[0], None, " -> ".join(map(str, cycle))))
    return issues


def _check_replacements(graph: DraftGraph) -> list[CheckIssue]:
    issues: list[CheckIssue] = []
    supersedes = {
        (source, target)
        for source, target, _line in graph.relations.get(RelationLabel.SUPERSEDES, ())
    }
    superseded_by = {
        (source, target)
        for source, target, _line in graph.relations.get(RelationLabel.SUPERSEDED_BY, ())
    }
    for source, target in sorted(supersedes):
        if (target, source) not in superseded_by:
            issues.append(_error("replacement.pair_missing", source, None, str(target)))
    for source, target in sorted(superseded_by):
        if (target, source) not in supersedes:
            issues.append(_error("replacement.pair_missing", source, None, str(target)))
    for path, page in graph.pages.items():
        declares_superseded_by = any(
            relation.label is RelationLabel.SUPERSEDED_BY for relation in page.relations
        )
        is_superseded = page.status.prefix is StatusPrefix.SUPERSEDED
        if declares_superseded_by and not is_superseded:
            issues.append(
                _error("replacement.status_mismatch", path, None, "被取代页面必须使用已取代状态")
            )
        if is_superseded and not declares_superseded_by:
            issues.append(
                _error("replacement.status_mismatch", path, None, "已取代页面必须声明被取代")
            )
    edges = _multi_edges(graph, RelationLabel.SUPERSEDES)
    for cycle in _find_cycles(edges):
        issues.append(_error("replacement.cycle", cycle[0], None, " -> ".join(map(str, cycle))))
    return issues


def _check_challenges(graph: DraftGraph) -> list[CheckIssue]:
    issues: list[CheckIssue] = []
    challenges = {
        (source, target)
        for source, target, _line in graph.relations.get(RelationLabel.CHALLENGES, ())
    }
    challenged_by = {
        (source, target)
        for source, target, _line in graph.relations.get(RelationLabel.CHALLENGED_BY, ())
    }
    for source, target in sorted(challenges):
        if (target, source) not in challenged_by:
            issues.append(_error("challenge.pair_missing", source, None, str(target)))
    for source, target in sorted(challenged_by):
        if (target, source) not in challenges:
            issues.append(_error("challenge.pair_missing", source, None, str(target)))
    return issues


def _check_outputs(graph: DraftGraph) -> list[CheckIssue]:
    issues: list[CheckIssue] = []
    output_edges = _multi_edges(graph, RelationLabel.OUTPUT)
    for path, page in graph.pages.items():
        declared = [
            relation for relation in page.relations if relation.label is RelationLabel.OUTPUT
        ]
        targets = output_edges.get(path, ())
        if path == CONCLUSIONS_PAGE:
            if declared:
                issues.append(_error("output.forbidden", path, declared[0].link.line))
            continue
        is_confirmed = page.status.prefix is StatusPrefix.CONFIRMED
        if not is_confirmed:
            if declared:
                issues.append(_error("output.forbidden", path, declared[0].link.line))
            continue
        if not declared:
            issues.append(_error("output.missing", path))
            continue
        if len(declared) > 1:
            issues.append(_error("output.wrong_target", path, declared[1].link.line, "重复产出"))
        if targets != (CONCLUSIONS_PAGE,):
            line = declared[0].link.line
            issues.append(
                _error("output.wrong_target", path, line, "产出必须指向根级 conclusions.md")
            )
            continue
        if path not in _body_links_to(graph, CONCLUSIONS_PAGE):
            issues.append(_error("output.backlink_missing", CONCLUSIONS_PAGE, None, str(path)))
    return issues


def _check_capacity(graph: DraftGraph) -> list[CheckIssue]:
    issues: list[CheckIssue] = []
    for path, page in graph.pages.items():
        if path == CONCLUSIONS_PAGE:
            continue
        lines = page.text.count("\n") + 1 if page.text else 0
        size = len(page.text.encode("utf-8"))
        if lines > CAPACITY_LINES or size > CAPACITY_BYTES:
            issues.append(_review("page.capacity", path, f"{lines} 行 / {size} 字节，建议审查拆分"))
    return issues


def check_graph(graph: DraftGraph) -> CheckReport:
    issues: list[CheckIssue] = []
    for pass_issues in (
        _check_parents(graph),
        _check_relation_duplicates(graph),
        _check_dependencies(graph),
        _check_replacements(graph),
        _check_challenges(graph),
        _check_outputs(graph),
        _check_capacity(graph),
    ):
        issues.extend(sorted(pass_issues, key=_sort_key))
    return CheckReport(issues=tuple(issues))


def _trash_package_issue(root: Path, journal_path: Path, journal: Journal) -> CheckIssue | None:
    """只读校验权威删除包：缺失或无法由 T 锚定验证即为 ERROR，否则返回 None。"""
    relative = journal_path.relative_to(root)
    package_dir = root / OUTGOING_RELATIVE / journal.operation_id
    if not package_dir.is_dir():
        return _error("trash.package_missing", relative, None, journal.operation_id)
    try:
        load_verified_package(root, journal)
    except DraftError:
        return _error("trash.package_corrupt", relative, None, journal.operation_id)
    return None


def _check_operation_state(root: Path) -> list[CheckIssue]:
    """Read-only diagnostics for lock and journal states; never mutates anything."""
    from draft.operations import read_journal, read_lock

    issues: list[CheckIssue] = []
    operations = root / INTERNAL_DIR / "operations"
    lock_path = Path(INTERNAL_DIR) / "operation.lock"
    try:
        locked_operation = read_lock(root)
    except DraftError:
        issues.append(_error("operation.journal_corrupt", lock_path))
        locked_operation = None
    if locked_operation is not None:
        try:
            read_journal(operation_journal_path(root, locked_operation))
        except DraftError:
            issues.append(_error("operation.journal_corrupt", lock_path))
        else:
            issues.append(_error("operation.recovery_required", lock_path, None, locked_operation))
        return issues
    if not operations.is_dir():
        return issues
    reject_symlink_components(root, Path(INTERNAL_DIR) / "operations")
    for entry in sorted(operations.iterdir()):
        if entry.is_symlink():
            # 链接条目必须报告，不能静默跳过（否则可能漏报反向关联或误判孤儿）。
            issues.append(_error("path.symlink_escape", entry.relative_to(root)))
            continue
        if not entry.is_dir() or not (entry / "journal.json").is_file():
            continue
        journal_path = operation_journal_path(root, entry.name)
        try:
            journal = read_journal(journal_path)
        except DraftError:
            issues.append(_error("operation.journal_corrupt", journal_path.relative_to(root)))
            continue
        relative = journal_path.relative_to(root)
        phase = journal.phase
        if phase is OperationPhase.ALLOCATING:
            issues.append(_review("operation.orphan_allocating", relative))
        elif phase in _MID_WRITE_PHASES:
            issues.append(_error("operation.inconsistent_state", relative, None, phase.value))
        elif phase in (
            OperationPhase.TRASH_HANDOFF_SUCCEEDED,
            OperationPhase.TRASH_PURGE_COMMITTED,
        ):
            # 两类提交都必须持有匹配写锁；无锁出现即状态损坏。
            issues.append(_error("operation.inconsistent_state", relative, None, phase.value))
        elif phase in (OperationPhase.COMPLETED, OperationPhase.ROLLED_BACK):
            pass  # 终态审计事实，原样保留。
        elif phase is OperationPhase.TRASH_RETAINED:
            issues.extend(_check_trash_retained(root, journal_path, journal, relative))
        elif phase is OperationPhase.TRASH_HANDOFF_PENDING:
            issues.extend(_check_trash_handoff_pending(root, journal_path, journal, relative))
    return issues


_MID_WRITE_PHASES = (
    OperationPhase.PREPARED,
    OperationPhase.APPLYING,
    OperationPhase.VALIDATING,
)


def _deadline_issue(journal: Journal, relative: Path) -> CheckIssue | None:
    """期限字段缺失/损坏即 ERROR：检查器必须能证明内部状态可恢复。"""
    try:
        TrashDeadlines.parse(journal.metadata)
    except DraftError:
        return _error("trash.deadlines_invalid", relative, None, journal.operation_id)
    return None


def _check_trash_retained(
    root: Path, journal_path: Path, journal: Journal, relative: Path
) -> list[CheckIssue]:
    """trash_retained 诊断：期限与包有效即 INFO；任一无效即 ERROR。到期但未推进仍为合法 INFO。"""
    if journal.kind != "trash":
        return [_error("operation.inconsistent_state", relative, None, journal.phase.value)]
    deadline_issue = _deadline_issue(journal, relative)
    if deadline_issue is not None:
        return [deadline_issue]
    package_issue = _trash_package_issue(root, journal_path, journal)
    if package_issue is not None:
        return [package_issue]
    return [_info("trash.retained", relative, journal.operation_id)]


def _check_trash_handoff_pending(
    root: Path, journal_path: Path, journal: Journal, relative: Path
) -> list[CheckIssue]:
    """trash_handoff_pending 诊断：期限有效且失败记录有效为 WARNING；其余为 ERROR。"""
    if journal.kind != "trash":
        return [_error("operation.inconsistent_state", relative, None, journal.phase.value)]
    deadline_issue = _deadline_issue(journal, relative)
    if deadline_issue is not None:
        return [deadline_issue]
    package_issue = _trash_package_issue(root, journal_path, journal)
    if package_issue is not None:
        return [package_issue]
    if has_handoff_failure(journal.metadata):
        return [_warning("trash.handoff_pending", relative, journal.operation_id)]
    return [_error("trash.pending_unexplained", relative, None, journal.operation_id)]


def check_draft(
    root: Path,
    mode: LoadMode = "registered",
    *,
    include_operation_state: bool = True,
    allow_provisional_adopt: bool = False,
) -> CheckReport:
    resolved = resolve_draft_root(root)
    layout: list[CheckIssue] = []
    for required in (ROOT_PAGE, CONCLUSIONS_PAGE):
        candidate = resolved / required
        if not candidate.is_file() or candidate.is_symlink():
            layout.append(_error("root.missing_file", required))
    pages_dir = resolved / "pages"
    if not pages_dir.is_dir() or pages_dir.is_symlink():
        layout.append(_error("root.missing_file", Path("pages")))

    result = load_graph_tolerant(resolved, mode, allow_provisional_adopt=allow_provisional_adopt)
    format_issues = [issue for issue in result.issues if issue.code.startswith("format.")]
    candidate_issues = [issue for issue in result.issues if issue.code.startswith("candidate.")]
    page_issues = [issue for issue in result.issues if issue.code.startswith("page.")]
    link_issues = [issue for issue in result.issues if issue.code.startswith("link.")]

    ordered: list[CheckIssue] = []
    ordered.extend(sorted(layout, key=_sort_key))
    ordered.extend(sorted(candidate_issues, key=_sort_key))
    ordered.extend(sorted(format_issues, key=_sort_key))
    if include_operation_state and mode == "registered":
        ordered.extend(sorted(_check_operation_state(resolved), key=_sort_key))
    ordered.extend(sorted(page_issues, key=_sort_key))
    ordered.extend(sorted(link_issues, key=_sort_key))
    ordered.extend(check_graph(result.graph).issues)
    return CheckReport(issues=tuple(ordered))
