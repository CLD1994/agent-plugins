from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from draft.diagnostic_codes import (
    CheckCode,
    ReplacementStatusReason,
    RootMissingKind,
)
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
from draft.vocabulary import ConclusionsState, RelationCode, StatusCode

CAPACITY_LINES = 600
CAPACITY_BYTES = 60 * 1024


def _error(
    code: CheckCode, path: Path | None, line: int | None = None, *details: str
) -> CheckIssue:
    return CheckIssue(
        code=code.value, severity=IssueSeverity.ERROR, path=path, line=line, details=details
    )


def _warning(code: CheckCode, path: Path | None, *details: str) -> CheckIssue:
    return CheckIssue(
        code=code.value, severity=IssueSeverity.WARNING, path=path, line=None, details=details
    )


def _info(code: CheckCode, path: Path | None, *details: str) -> CheckIssue:
    return CheckIssue(
        code=code.value, severity=IssueSeverity.INFO, path=path, line=None, details=details
    )


def _review(code: CheckCode, path: Path | None, *details: str) -> CheckIssue:
    return CheckIssue(
        code=code.value, severity=IssueSeverity.REVIEW, path=path, line=None, details=details
    )


def _sort_key(issue: CheckIssue) -> tuple[str, int, str]:
    return ("" if issue.path is None else str(issue.path), issue.line or 0, issue.code)


# 加载期（marker 与页面解析）issue 的报告顺序分组；分组顺序即报告顺序，
# 组内按 _sort_key 排序。未归入任何分组的加载期诊断不丢弃，追加保留在已知分组之后。
_LOAD_CANDIDATE_ISSUES = frozenset(
    {CheckCode.CANDIDATE_ALREADY_REGISTERED, CheckCode.CANDIDATE_INTERNAL_STATE}
)
_LOAD_MARKER_ISSUES = frozenset(
    {
        CheckCode.FORMAT_MISSING,
        CheckCode.FORMAT_INVALID,
        CheckCode.FORMAT_UNSUPPORTED,
        CheckCode.PATH_SYMLINK_ESCAPE,
    }
)
_LOAD_PAGE_ISSUES = frozenset(
    {
        CheckCode.PAGE_UNMANAGED_PATH,
        CheckCode.PAGE_MISSING_TITLE,
        CheckCode.PAGE_MISSING_MACHINE_BLOCK,
        CheckCode.PAGE_MISSING_STATUS,
        CheckCode.PAGE_DUPLICATE_STATUS,
        CheckCode.PAGE_UNKNOWN_LABEL,
        CheckCode.PAGE_INVALID_STATUS,
        CheckCode.PAGE_INVALID_RELATION,
        CheckCode.PAGE_PARSE_ERROR,
        CheckCode.PAGE_CAPACITY,
    }
)
_LOAD_LINK_ISSUES = frozenset(
    {
        CheckCode.LINK_MISSING,
        CheckCode.LINK_INVALID,
        CheckCode.LINK_UNAVAILABLE,
        CheckCode.LINK_ESCAPE,
    }
)
_LOAD_ISSUE_GROUPS = (
    _LOAD_CANDIDATE_ISSUES,
    _LOAD_MARKER_ISSUES,
    _LOAD_PAGE_ISSUES,
    _LOAD_LINK_ISSUES,
)
_LOAD_KNOWN_ISSUE_VALUES: frozenset[str] = frozenset(
    code.value for group in _LOAD_ISSUE_GROUPS for code in group
)


def _load_issues_in(
    issues: list[CheckIssue], group: frozenset[CheckCode] | None
) -> list[CheckIssue]:
    if group is None:
        return [issue for issue in issues if issue.code not in _LOAD_KNOWN_ISSUE_VALUES]
    values = {code.value for code in group}
    return [issue for issue in issues if issue.code in values]


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


def _multi_edges(graph: DraftGraph, label: RelationCode) -> dict[Path, tuple[Path, ...]]:
    edges: dict[Path, set[Path]] = {}
    for source, target, _line in graph.relations.get(label, ()):
        edges.setdefault(source, set()).add(target)
    return {source: tuple(sorted(targets)) for source, targets in edges.items()}


def _relation_lines(
    graph: DraftGraph, label: RelationCode
) -> dict[tuple[Path, Path], tuple[int, ...]]:
    """Group relation edges by (source, target) and retain each declaration line.

    Cycle detection only needs the edge set (``_multi_edges``); pair and self
    checks need the original source lines, so they read from this grouping.
    """
    grouped: dict[tuple[Path, Path], list[int]] = {}
    for source, target, line in graph.relations.get(label, ()):
        grouped.setdefault((source, target), []).append(line)
    return {edge: tuple(sorted(lines)) for edge, lines in grouped.items()}


def _cycle_details(cycle: tuple[Path, ...]) -> tuple[str, ...]:
    """Ordered closed path as POSIX strings, with the first node repeated at the end."""
    return tuple(path.as_posix() for path in (*cycle, cycle[0]))


def _check_relation_duplicates(graph: DraftGraph) -> list[CheckIssue]:
    """同一来源页的同一标签重复指向同一规范化目标（父主题由基数检查覆盖）。"""
    issues: list[CheckIssue] = []
    for label in RelationCode:
        if label is RelationCode.PARENT:
            continue
        seen = _relation_lines(graph, label)
        for (source, target), lines in sorted(seen.items()):
            if len(lines) > 1:
                issues.append(
                    _error(
                        CheckCode.RELATION_DUPLICATE,
                        source,
                        lines[1],
                        label.value,
                        target.as_posix(),
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
    parent_edges = _multi_edges(graph, RelationCode.PARENT)
    single_edges = {source: targets for source, targets in parent_edges.items()}

    for path, page in graph.pages.items():
        declared = [relation for relation in page.relations if relation.code is RelationCode.PARENT]
        if path == ROOT_PAGE:
            if declared:
                issues.append(_error(CheckCode.PARENT_ROOT_DECLARED, path, declared[0].link.line))
            continue
        if not declared:
            issues.append(_error(CheckCode.PARENT_MISSING, path))
        elif len(declared) > 1:
            issues.append(_error(CheckCode.PARENT_MULTIPLE, path, declared[1].link.line))
        elif len(single_edges.get(path, ())) == 1:
            physical = physical_parent(path)
            declared_target = single_edges[path][0]
            if physical is not None and declared_target != physical:
                issues.append(
                    _error(
                        CheckCode.LAYOUT_PARENT_MISMATCH,
                        path,
                        declared[0].link.line,
                        declared_target.as_posix(),
                        physical.as_posix(),
                    )
                )

    cycles = _find_cycles({source: targets for source, targets in parent_edges.items()})
    cycle_members = {member for cycle in cycles for member in cycle}
    for cycle in cycles:
        issues.append(_error(CheckCode.PARENT_CYCLE, cycle[0], None, *_cycle_details(cycle)))

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
        issues.append(_error(CheckCode.PARENT_UNREACHABLE, path))

    for child, parent in sorted(graph.parents.items()):
        if parent not in graph.pages:
            continue
        if child not in _body_links_to(graph, parent):
            issues.append(
                _error(CheckCode.PARENT_NAVIGATION_MISSING, parent, None, child.as_posix())
            )

    for path in graph.pages:
        if path.name == ROOT_PAGE.name and path != ROOT_PAGE and not graph.children.get(path):
            issues.append(_error(CheckCode.HUB_EMPTY, path))
    return issues


def _check_dependencies(graph: DraftGraph) -> list[CheckIssue]:
    issues: list[CheckIssue] = []
    edges = _multi_edges(graph, RelationCode.DEPENDS_ON)
    depends_lines = _relation_lines(graph, RelationCode.DEPENDS_ON)
    for (source, target), lines in sorted(depends_lines.items()):
        if source == target:
            issues.append(_error(CheckCode.DEPENDENCY_SELF, source, min(lines)))
    non_self = {
        source: tuple(target for target in targets if target != source)
        for source, targets in edges.items()
    }
    for cycle in _find_cycles(non_self):
        issues.append(_error(CheckCode.DEPENDENCY_CYCLE, cycle[0], None, *_cycle_details(cycle)))
    return issues


def _check_replacements(graph: DraftGraph) -> list[CheckIssue]:
    issues: list[CheckIssue] = []
    supersedes = _relation_lines(graph, RelationCode.SUPERSEDES)
    superseded_by = _relation_lines(graph, RelationCode.SUPERSEDED_BY)
    for (source, target), lines in sorted(supersedes.items()):
        if (target, source) not in superseded_by:
            issues.append(
                _error(
                    CheckCode.REPLACEMENT_PAIR_MISSING,
                    source,
                    min(lines),
                    target.as_posix(),
                    RelationCode.SUPERSEDED_BY.value,
                )
            )
    for (source, target), lines in sorted(superseded_by.items()):
        if (target, source) not in supersedes:
            issues.append(
                _error(
                    CheckCode.REPLACEMENT_PAIR_MISSING,
                    source,
                    min(lines),
                    target.as_posix(),
                    RelationCode.SUPERSEDES.value,
                )
            )
    for path, page in graph.pages.items():
        superseded_by_relations = [
            relation for relation in page.relations if relation.code is RelationCode.SUPERSEDED_BY
        ]
        declares_superseded_by = bool(superseded_by_relations)
        is_superseded = page.status.code is StatusCode.SUPERSEDED
        if declares_superseded_by and not is_superseded:
            line = min(relation.link.line for relation in superseded_by_relations)
            issues.append(
                _error(
                    CheckCode.REPLACEMENT_STATUS_MISMATCH,
                    path,
                    line,
                    ReplacementStatusReason.RELATION_REQUIRES_STATUS.value,
                )
            )
        if is_superseded and not declares_superseded_by:
            issues.append(
                _error(
                    CheckCode.REPLACEMENT_STATUS_MISMATCH,
                    path,
                    None,
                    ReplacementStatusReason.STATUS_REQUIRES_RELATION.value,
                )
            )
    edges = _multi_edges(graph, RelationCode.SUPERSEDES)
    for cycle in _find_cycles(edges):
        issues.append(_error(CheckCode.REPLACEMENT_CYCLE, cycle[0], None, *_cycle_details(cycle)))
    return issues


def _check_challenges(graph: DraftGraph) -> list[CheckIssue]:
    issues: list[CheckIssue] = []
    challenges = _relation_lines(graph, RelationCode.CHALLENGES)
    challenged_by = _relation_lines(graph, RelationCode.CHALLENGED_BY)
    for (source, target), lines in sorted(challenges.items()):
        if (target, source) not in challenged_by:
            issues.append(
                _error(
                    CheckCode.CHALLENGE_PAIR_MISSING,
                    source,
                    min(lines),
                    target.as_posix(),
                    RelationCode.CHALLENGED_BY.value,
                )
            )
    for (source, target), lines in sorted(challenged_by.items()):
        if (target, source) not in challenges:
            issues.append(
                _error(
                    CheckCode.CHALLENGE_PAIR_MISSING,
                    source,
                    min(lines),
                    target.as_posix(),
                    RelationCode.CHALLENGES.value,
                )
            )
    return issues


def _check_capacity(graph: DraftGraph) -> list[CheckIssue]:
    issues: list[CheckIssue] = []
    for path, page in graph.pages.items():
        if path == CONCLUSIONS_PAGE:
            continue
        lines = page.text.count("\n") + 1 if page.text else 0
        size = len(page.text.encode("utf-8"))
        if lines > CAPACITY_LINES or size > CAPACITY_BYTES:
            issues.append(_review(CheckCode.PAGE_CAPACITY, path, str(lines), str(size)))
    return issues


def check_graph(graph: DraftGraph) -> CheckReport:
    issues: list[CheckIssue] = []
    for pass_issues in (
        _check_parents(graph),
        _check_relation_duplicates(graph),
        _check_dependencies(graph),
        _check_replacements(graph),
        _check_challenges(graph),
        _check_capacity(graph),
    ):
        issues.extend(sorted(pass_issues, key=_sort_key))
    return CheckReport(issues=tuple(issues))


def _trash_package_issue(root: Path, journal_path: Path, journal: Journal) -> CheckIssue | None:
    """只读校验权威删除包：缺失或无法由 T 锚定验证即为 ERROR，否则返回 None。"""
    relative = journal_path.relative_to(root)
    package_dir = root / OUTGOING_RELATIVE / journal.operation_id
    if not package_dir.is_dir():
        return _error(CheckCode.TRASH_PACKAGE_MISSING, relative, None, journal.operation_id)
    try:
        load_verified_package(root, journal)
    except DraftError:
        return _error(CheckCode.TRASH_PACKAGE_CORRUPT, relative, None, journal.operation_id)
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
        issues.append(_error(CheckCode.OPERATION_JOURNAL_CORRUPT, lock_path))
        locked_operation = None
    if locked_operation is not None:
        try:
            read_journal(operation_journal_path(root, locked_operation))
        except DraftError:
            issues.append(_error(CheckCode.OPERATION_JOURNAL_CORRUPT, lock_path))
        else:
            issues.append(
                _error(CheckCode.OPERATION_RECOVERY_REQUIRED, lock_path, None, locked_operation)
            )
        return issues
    if not operations.is_dir():
        return issues
    reject_symlink_components(root, Path(INTERNAL_DIR) / "operations")
    for entry in sorted(operations.iterdir()):
        if entry.is_symlink():
            # 链接条目必须报告，不能静默跳过（否则可能漏报反向关联或误判孤儿）。
            issues.append(_error(CheckCode.PATH_SYMLINK_ESCAPE, entry.relative_to(root)))
            continue
        if not entry.is_dir() or not (entry / "journal.json").is_file():
            continue
        journal_path = operation_journal_path(root, entry.name)
        try:
            journal = read_journal(journal_path)
        except DraftError:
            issues.append(
                _error(CheckCode.OPERATION_JOURNAL_CORRUPT, journal_path.relative_to(root))
            )
            continue
        relative = journal_path.relative_to(root)
        phase = journal.phase
        if phase is OperationPhase.ALLOCATING:
            issues.append(_review(CheckCode.OPERATION_ORPHAN_ALLOCATING, relative))
        elif phase in _MID_WRITE_PHASES:
            issues.append(
                _error(CheckCode.OPERATION_INCONSISTENT_STATE, relative, None, phase.value)
            )
        elif phase in (
            OperationPhase.TRASH_HANDOFF_SUCCEEDED,
            OperationPhase.TRASH_PURGE_COMMITTED,
        ):
            # 两类提交都必须持有匹配写锁；无锁出现即状态损坏。
            issues.append(
                _error(CheckCode.OPERATION_INCONSISTENT_STATE, relative, None, phase.value)
            )
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
        return _error(CheckCode.TRASH_DEADLINES_INVALID, relative, None, journal.operation_id)
    return None


def _check_trash_retained(
    root: Path, journal_path: Path, journal: Journal, relative: Path
) -> list[CheckIssue]:
    """trash_retained 诊断：期限与包有效即 INFO；任一无效即 ERROR。到期但未推进仍为合法 INFO。"""
    if journal.kind != "trash":
        return [_error(CheckCode.OPERATION_INCONSISTENT_STATE, relative, None, journal.phase.value)]
    deadline_issue = _deadline_issue(journal, relative)
    if deadline_issue is not None:
        return [deadline_issue]
    package_issue = _trash_package_issue(root, journal_path, journal)
    if package_issue is not None:
        return [package_issue]
    return [_info(CheckCode.TRASH_RETAINED, relative, journal.operation_id)]


def _check_trash_handoff_pending(
    root: Path, journal_path: Path, journal: Journal, relative: Path
) -> list[CheckIssue]:
    """trash_handoff_pending 诊断：期限有效且失败记录有效为 WARNING；其余为 ERROR。"""
    if journal.kind != "trash":
        return [_error(CheckCode.OPERATION_INCONSISTENT_STATE, relative, None, journal.phase.value)]
    deadline_issue = _deadline_issue(journal, relative)
    if deadline_issue is not None:
        return [deadline_issue]
    package_issue = _trash_package_issue(root, journal_path, journal)
    if package_issue is not None:
        return [package_issue]
    if has_handoff_failure(journal.metadata):
        return [_warning(CheckCode.TRASH_HANDOFF_PENDING, relative, journal.operation_id)]
    return [_error(CheckCode.TRASH_PENDING_UNEXPLAINED, relative, None, journal.operation_id)]


def _check_conclusions_product(
    resolved: Path, graph: DraftGraph, state: ConclusionsState
) -> list[CheckIssue]:
    """结论机器产物的结构检查（规格 §12.6）。

    - ``not_generated`` 时意外出现 ``conclusions.md``；
    - ``generated`` 时结论页固定形状损坏；
    - 根导航结论链接缺失、重复、位置或目标错误。
    """
    from draft.conclusions import (
        is_conclusions_document,
        is_conclusions_nav_canonical,
    )

    issues: list[CheckIssue] = []
    if state is ConclusionsState.NOT_GENERATED:
        return issues
    document = graph.pages.get(CONCLUSIONS_PAGE)
    if document is not None and not is_conclusions_document(document, resolved):
        issues.append(_error(CheckCode.CONCLUSIONS_SHAPE_INVALID, CONCLUSIONS_PAGE, None))
    index = graph.pages.get(ROOT_PAGE)
    if index is not None and not is_conclusions_nav_canonical(index.text, resolved):
        issues.append(_error(CheckCode.CONCLUSIONS_NAVIGATION_INVALID, ROOT_PAGE, None))
    return issues


def _conclusions_state(
    resolved: Path, mode: LoadMode, allow_provisional_adopt: bool
) -> ConclusionsState | None:
    """读取所有权状态；标记缺失/无效/非 registered 时返回 None（不在此处报错）。"""
    if mode != "registered":
        return None
    from draft.graph import read_marker

    try:
        state = read_marker(resolved, allow_provisional_adopt=allow_provisional_adopt)
    except DraftError:
        return None
    if state.provisional_manifest is not None:
        return None
    return state.conclusions_state


def _conclusions_required(resolved: Path, mode: LoadMode, allow_provisional_adopt: bool) -> bool:
    """conclusions.md 是否必须存在：registered 且标记有效时由所有权状态决定。

    标记缺失/无效/候选模式下不在此处要求；对应诊断由加载期 marker issue 报告。
    """
    if mode != "registered":
        return False
    from draft.graph import read_marker

    try:
        state = read_marker(resolved, allow_provisional_adopt=allow_provisional_adopt)
    except DraftError:
        return False
    if state.provisional_manifest is not None:
        return False
    return state.conclusions_state is ConclusionsState.GENERATED


def check_draft(
    root: Path,
    mode: LoadMode = "registered",
    *,
    include_operation_state: bool = True,
    allow_provisional_adopt: bool = False,
    exclude_resource_facts: bool = False,
) -> CheckReport:
    resolved = resolve_draft_root(root)
    layout: list[CheckIssue] = []
    for required in (ROOT_PAGE,):
        candidate = resolved / required
        if not candidate.is_file() or candidate.is_symlink():
            layout.append(
                _error(
                    CheckCode.ROOT_MISSING_FILE,
                    None,
                    None,
                    RootMissingKind.FILE.value,
                    required.as_posix(),
                )
            )
    # conclusions.md 只在所有权为 generated 时要求存在；not_generated 允许缺失。
    conclusions_required = _conclusions_required(resolved, mode, allow_provisional_adopt)
    if conclusions_required:
        candidate = resolved / CONCLUSIONS_PAGE
        if not candidate.is_file() or candidate.is_symlink():
            layout.append(
                _error(
                    CheckCode.ROOT_MISSING_FILE,
                    None,
                    None,
                    RootMissingKind.FILE.value,
                    CONCLUSIONS_PAGE.as_posix(),
                )
            )
    pages_dir = resolved / "pages"
    if not pages_dir.is_dir() or pages_dir.is_symlink():
        layout.append(
            _error(
                CheckCode.ROOT_MISSING_FILE, None, None, RootMissingKind.DIRECTORY.value, "pages"
            )
        )

    result = load_graph_tolerant(resolved, mode, allow_provisional_adopt=allow_provisional_adopt)

    ordered: list[CheckIssue] = []
    ordered.extend(sorted(layout, key=_sort_key))
    ordered.extend(sorted(_load_issues_in(result.issues, _LOAD_CANDIDATE_ISSUES), key=_sort_key))
    ordered.extend(sorted(_load_issues_in(result.issues, _LOAD_MARKER_ISSUES), key=_sort_key))
    if include_operation_state and mode == "registered":
        ordered.extend(sorted(_check_operation_state(resolved), key=_sort_key))
    ordered.extend(sorted(_load_issues_in(result.issues, _LOAD_PAGE_ISSUES), key=_sort_key))
    # 所有权为 generated 时，结论页自身的断链/无效链接是允许过时的机器列表事实，不报错。
    if conclusions_required:
        link_issues = [
            issue
            for issue in _load_issues_in(result.issues, _LOAD_LINK_ISSUES)
            if issue.path != CONCLUSIONS_PAGE
        ]
        ordered.extend(sorted(link_issues, key=_sort_key))
    else:
        ordered.extend(sorted(_load_issues_in(result.issues, _LOAD_LINK_ISSUES), key=_sort_key))
    ordered.extend(sorted(_load_issues_in(result.issues, None), key=_sort_key))
    ordered.extend(check_graph(result.graph).issues)
    conclusions_state = _conclusions_state(resolved, mode, allow_provisional_adopt)
    if conclusions_state is not None:
        ordered.extend(
            sorted(
                _check_conclusions_product(resolved, result.graph, conclusions_state),
                key=_sort_key,
            )
        )
    if exclude_resource_facts:
        facts = set(result.resource_facts)
        ordered = [issue for issue in ordered if issue not in facts]
    return CheckReport(issues=tuple(ordered))
