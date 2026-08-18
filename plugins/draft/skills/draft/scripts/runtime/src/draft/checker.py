from __future__ import annotations

import os
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
    MARKER_PATH,
    ROOT_PAGE,
    DraftGraph,
    load_graph_tolerant,
    resolve_local_destination,
)
from draft.models import (
    CheckIssue,
    CheckReport,
    IssueSeverity,
    OperationKind,
    OperationPhase,
)
from draft.operations import (
    Journal,
    TrashDeadlines,
    has_handoff_failure,
    operation_journal_path,
    read_journal,
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


_OP_DIR_ALLOWED_ENTRIES = frozenset(
    {
        "journal.json",
        "journal.json.pending",
        "originals",
        "staged",
        "attempt",
        "cleanup-evidence.json",
        "cleanup-evidence.json.pending",
        "cleanup-evidence.json.quarantine",
    }
)
_OP_DIR_TERMINAL_ALLOWED = frozenset({"journal.json"})
_OP_DIR_ALLOCATING_ALLOWED = frozenset(
    {"journal.json", "journal.json.pending", "originals", "staged"}
)


def _operation_dir_relative(operation_id: str) -> Path:
    """操作目录的根内相对路径（L1）：``.draft/operations/<id>/...``。"""
    return Path(INTERNAL_DIR) / "operations" / operation_id


def _list_internal_directory(
    root: Path,
    directory: Path,
    *,
    code: CheckCode,
    issues: list[CheckIssue],
) -> tuple[Path, ...] | None:
    """稳定分类/枚举内部目录（round-12 M2）：失败追加一次 CheckIssue 并返回 None。

    ``classify_node_or_error``（lstat）把 EACCES/EIO 转 DraftError，不折叠成缺失/不
    存在；``os.scandir`` 直接枚举并在 OSError 时报告稳定诊断（Path.iterdir 在 3.12
    会静默吞掉 OSError，不能用来检测枚举失败）。缺失（MISSING）不报错；普通文件/
    链接按内部命名空间非法形状报告。返回 None 表示该子树无法枚举，调用者继续检查
    独立子树，不得在最外层捕获后停止全部检查。
    """
    from draft.observations import EntryKind
    from draft.operations import classify_node_or_error

    relative = directory.relative_to(root)
    try:
        node = classify_node_or_error(directory, code=code.value, code_path=directory)
    except DraftError as exc:
        issues.append(
            CheckIssue(code=exc.code, severity=IssueSeverity.ERROR, path=relative, line=None)
        )
        return None
    if node is not EntryKind.DIRECTORY:
        if node is EntryKind.SYMLINK:
            issues.append(_error(CheckCode.PATH_SYMLINK_ESCAPE, relative))
        elif node is not EntryKind.MISSING:
            issues.append(_error(CheckCode.INTERNAL_UNEXPECTED_ENTRY, relative))
        return None
    try:
        with os.scandir(directory) as iterator:
            entries = tuple(sorted(Path(entry.path) for entry in iterator))
    except OSError:
        issues.append(_error(code, relative))
        return None
    return entries


def _check_operation_directory_entries(
    root: Path,
    entry: Path,
    journal: Journal,
    issues: list[CheckIssue],
    *,
    active_ids: frozenset[str] = frozenset(),
) -> None:
    """按 phase 与活动角色校验操作目录允许的精确内部条目（随机 pending/错误 evidence/
    终态残留可见）。

    活动角色（锁持有者 R 与锁内 RESTORE R 关联的 cleanup owner T，round-12 H3）在
    完成清理前可能保留 originals/staged/attempt/evidence，使用完整允许集并总是运行
    evidence 联合分类；普通终态（completed/rolled_back）只允许 journal.json，任何
    originals/staged/attempt/evidence/pending/quarantine 都是残留并报告；allocating
    只允许 journal 与恢复副本目录；其余阶段允许全部登记内部条目。evidence 合法性由
    共享只读分类 ``classify_cleanup_evidence_artifacts`` 判定，再调用
    ``cleanup_evidence_protocol_corrupt``；任一损坏结果报告 trash.package_corrupt。
    checker 始终只读，不发布/恢复/消费任何 evidence 名；分类/枚举/读取的底层 OSError
    一律转为稳定诊断，不泄漏原始异常。所有 path 按根内相对协议输出（L1）。
    """
    from draft.trash_package import (
        classify_cleanup_evidence_artifacts,
        cleanup_evidence_protocol_corrupt,
    )

    phase = journal.phase
    if journal.operation_id in active_ids:
        allowed = _OP_DIR_ALLOWED_ENTRIES
    elif phase in (OperationPhase.COMPLETED, OperationPhase.ROLLED_BACK):
        allowed = _OP_DIR_TERMINAL_ALLOWED
    elif phase is OperationPhase.ALLOCATING:
        allowed = _OP_DIR_ALLOCATING_ALLOWED
    else:
        allowed = _OP_DIR_ALLOWED_ENTRIES
    operation_relative = _operation_dir_relative(journal.operation_id)
    try:
        children = sorted(entry.iterdir())
    except OSError:
        # 目录枚举失败：不能折叠成 missing 或成功，稳定诊断保留现场。
        issues.append(_error(CheckCode.OPERATION_JOURNAL_CORRUPT, operation_relative))
        return
    for child in children:
        if child.name not in allowed:
            issues.append(
                _error(CheckCode.INTERNAL_UNEXPECTED_ENTRY, operation_relative / child.name)
            )
    # evidence 联合分类在 evidence 名协议合法的角色运行（活动角色或非终态/非 allocating）；
    # 非 TRASH 操作目录出现 evidence 名也会被同一分类判为损坏（授权解析失败）。
    if allowed is _OP_DIR_ALLOWED_ENTRIES:
        evidence_relative = operation_relative / "cleanup-evidence.json"
        try:
            classified = classify_cleanup_evidence_artifacts(journal)
        except DraftError as error:
            # 共享分类的领域错误按原 code/path 生成 CheckIssue（M2：稳定诊断，不泄漏
            # 原始异常）；错误路径若在根内转根内相对，否则用预先计算的 evidence 路径
            # （L1：机器协议 path 不得依赖临时根）。
            error_path = error.paths[0] if error.paths else None
            if error_path is not None:
                try:
                    path = error_path.relative_to(root)
                except ValueError:
                    path = evidence_relative
            else:
                path = evidence_relative
            issues.append(
                CheckIssue(code=error.code, severity=IssueSeverity.ERROR, path=path, line=None)
            )
        except OSError:
            issues.append(_error(CheckCode.TRASH_PACKAGE_CORRUPT, evidence_relative))
        else:
            if classified is not None:
                final, pending, quarantine, same_pending, same_quarantine = classified
                if cleanup_evidence_protocol_corrupt(
                    final=final,
                    pending=pending,
                    quarantine=quarantine,
                    final_pending_same_node=same_pending,
                    final_quarantine_same_node=same_quarantine,
                ):
                    issues.append(_error(CheckCode.TRASH_PACKAGE_CORRUPT, evidence_relative))


def _cleanup_owner_ids(
    index: Mapping[str, Journal],
    locked_operation: str | None,
) -> frozenset[str]:
    """识别锁内 RESTORE R 所关联的 cleanup owner T（round-12 H3）。

    充分条件（缺一不可）：锁属于 R、R.kind=RESTORE、R.phase=COMPLETED、
    R.metadata[restores_trash_operation]==T.id、T.kind=TRASH、T.phase=ROLLED_BACK、
    T.metadata[restore_operation_id]==R.id。只有由锁与合法 R/T 关系证明的 T 才能在
    rolled_back 下拥有 cleanup evidence；条件不完整时不得放宽 terminal T 目录协议。
    """
    if locked_operation is None:
        return frozenset()
    restore_journal = index.get(locked_operation)
    if (
        restore_journal is None
        or restore_journal.kind is not OperationKind.RESTORE
        or restore_journal.phase is not OperationPhase.COMPLETED
    ):
        return frozenset()
    trash_id = restore_journal.metadata.get("restores_trash_operation")
    if not isinstance(trash_id, str):
        return frozenset()
    trash_journal = index.get(trash_id)
    if (
        trash_journal is None
        or trash_journal.kind is not OperationKind.TRASH
        or trash_journal.phase is not OperationPhase.ROLLED_BACK
    ):
        return frozenset()
    if trash_journal.metadata.get("restore_operation_id") != locked_operation:
        return frozenset()
    return frozenset({trash_id})


def _check_operation_state(root: Path) -> list[CheckIssue]:
    """Read-only diagnostics for lock and journal states; never mutates anything."""
    from draft.observations import EntryKind
    from draft.operations import classify_node_or_error, read_journal, read_lock

    issues: list[CheckIssue] = []
    operations = root / INTERNAL_DIR / "operations"
    lock_path = Path(INTERNAL_DIR) / "operation.lock"
    try:
        locked_operation = read_lock(root)
    except DraftError:
        issues.append(_error(CheckCode.OPERATION_JOURNAL_CORRUPT, lock_path))
        locked_operation = None

    # 第一遍：稳定枚举全部操作目录，建立 id→journal 索引与 cleanup owner 集合。
    # 锁存在时仍扫描全部操作目录与 trash tree（round-12 H3）：锁只影响诊断/上下文，
    # 不得成为全局 early return。解析/分类失败的目录由第二遍报告损坏。
    operations_children = _list_internal_directory(
        root, operations, code=CheckCode.OPERATION_JOURNAL_CORRUPT, issues=issues
    )
    index: dict[str, Journal] = {}
    if operations_children is not None:
        for entry in operations_children:
            try:
                kind = classify_node_or_error(
                    entry, code=CheckCode.OPERATION_JOURNAL_CORRUPT.value, code_path=entry
                )
            except DraftError:
                continue  # 第二遍报告
            if kind is not EntryKind.DIRECTORY:
                continue  # 第二遍报告
            try:
                index[entry.name] = read_journal(operation_journal_path(root, entry.name))
            except DraftError:
                continue  # 第二遍报告
    cleanup_owner_ids = _cleanup_owner_ids(index, locked_operation)
    active_ids: frozenset[str] = cleanup_owner_ids
    if locked_operation is not None:
        active_ids = active_ids | frozenset({locked_operation})

    if locked_operation is not None:
        locked_journal = index.get(locked_operation)
        if locked_journal is None:
            issues.append(_error(CheckCode.OPERATION_JOURNAL_CORRUPT, lock_path))
        else:
            # 有锁操作目录仍校验内部条目形状（回归 H4）：持锁时完成清理前可能有
            # originals/staged/attempt/evidence；活动角色使用完整允许集，但未知条目
            # 与错误 evidence 必须可见。锁目录只检查一次，不重复报告。
            locked_entry = operations / locked_operation
            _check_operation_directory_entries(
                root, locked_entry, locked_journal, issues, active_ids=active_ids
            )
            issues.append(
                _error(CheckCode.OPERATION_RECOVERY_REQUIRED, lock_path, None, locked_operation)
            )

    if operations_children is None:
        # operations 缺失/普通文件/不可读：缺失正常，其余已由 helper 报告。
        _check_trash_tree(
            root, issues, cleanup_owner_ids=cleanup_owner_ids, operations_children=None
        )
        return issues
    for entry in operations_children:
        relative = entry.relative_to(root)
        try:
            kind = classify_node_or_error(
                entry, code=CheckCode.OPERATION_JOURNAL_CORRUPT.value, code_path=entry
            )
        except DraftError as exc:
            issues.append(
                CheckIssue(code=exc.code, severity=IssueSeverity.ERROR, path=relative, line=None)
            )
            continue
        if kind is EntryKind.SYMLINK:
            # 链接条目必须报告，不能静默跳过（否则可能漏报反向关联或误判孤儿）。
            issues.append(_error(CheckCode.PATH_SYMLINK_ESCAPE, relative))
            continue
        if kind is not EntryKind.DIRECTORY:
            # 非目录条目不是合法操作目录：不能静默跳过。
            issues.append(_error(CheckCode.INTERNAL_UNEXPECTED_ENTRY, relative))
            continue
        if entry.name == locked_operation:
            continue  # 锁目录已在上方检查一次，不重复报告。
        journal = index.get(entry.name)
        if journal is None:
            # 目录无 journal、名称非法或日志损坏统一报 operation.journal_corrupt，
            # 检查保持只报告、不抛异常。
            issues.append(_error(CheckCode.OPERATION_JOURNAL_CORRUPT, relative / "journal.json"))
            continue
        journal_path = operation_journal_path(root, entry.name)
        relative = journal_path.relative_to(root)
        phase = journal.phase
        # 操作目录也按 phase/角色校验内部条目：终态残留/随机 pending/错误 evidence
        # 可见（回归 H4）；cleanup owner T 允许 evidence 并运行联合分类（round-12 H3）。
        _check_operation_directory_entries(root, entry, journal, issues, active_ids=active_ids)
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
    _check_trash_tree(
        root, issues, cleanup_owner_ids=cleanup_owner_ids, operations_children=operations_children
    )
    return issues


def _check_trash_tree(
    root: Path,
    issues: list[CheckIssue],
    *,
    cleanup_owner_ids: frozenset[str] = frozenset(),
    operations_children: tuple[Path, ...] | None = None,
) -> None:
    """闭合 .draft/trash 树形协议（candidate 与 registered 共享）：

    ``.draft/trash/`` 只允许 ``outgoing/``，``outgoing/`` 只允许合法 operation_id
    包目录；被持有日志引用或锁内 cleanup owner（round-12 H3：T=rolled_back 且被锁内
    R 关联，清理中途包仍合法存在）的包内部损坏由 trash.package_corrupt 报告，其余
    非法形状（未登记条目、非目录、非法包名、orphan）统一报 internal.unexpected_entry，
    任意路径组件符号链接报 path.symlink_escape。全部枚举/分类走稳定边界（M2），
    不可读子树报告一次稳定诊断后继续扫描独立子树。
    """
    from draft.observations import EntryKind
    from draft.operations import classify_node_or_error, validate_operation_id
    from draft.trash_package import OUTGOING_RELATIVE

    trash_children = _list_internal_directory(
        root,
        root / INTERNAL_DIR / "trash",
        code=CheckCode.INTERNAL_UNEXPECTED_ENTRY,
        issues=issues,
    )
    if trash_children is not None:
        for entry in trash_children:
            if entry.name == "outgoing":
                continue
            relative = entry.relative_to(root)
            try:
                kind = classify_node_or_error(
                    entry, code=CheckCode.INTERNAL_UNEXPECTED_ENTRY.value, code_path=entry
                )
            except DraftError as exc:
                issues.append(
                    CheckIssue(
                        code=exc.code, severity=IssueSeverity.ERROR, path=relative, line=None
                    )
                )
                continue
            if kind is EntryKind.SYMLINK:
                issues.append(_error(CheckCode.PATH_SYMLINK_ESCAPE, relative))
            else:
                issues.append(_error(CheckCode.INTERNAL_UNEXPECTED_ENTRY, relative))
    outgoing = root / OUTGOING_RELATIVE
    outgoing_children = _list_internal_directory(
        root, outgoing, code=CheckCode.INTERNAL_UNEXPECTED_ENTRY, issues=issues
    )
    if outgoing_children is None:
        return
    # 被持有日志引用或锁内 cleanup owner 的 operation_id；operations 由调用方枚举传入，
    # 避免重复扫描与重复报告（M2：每个不可读子树只报告一次）。
    held: set[str] = set()
    if operations_children is not None:
        for entry in operations_children:
            try:
                kind = classify_node_or_error(
                    entry, code=CheckCode.OPERATION_JOURNAL_CORRUPT.value, code_path=entry
                )
            except DraftError:
                continue
            if kind is not EntryKind.DIRECTORY:
                continue
            try:
                journal = read_journal(operation_journal_path(root, entry.name))
            except DraftError:
                continue
            if journal.kind is OperationKind.TRASH and journal.phase in (
                OperationPhase.TRASH_RETAINED,
                OperationPhase.TRASH_HANDOFF_PENDING,
            ):
                held.add(entry.name)
    held.update(cleanup_owner_ids)
    for package in outgoing_children:
        relative = package.relative_to(root)
        try:
            kind = classify_node_or_error(
                package, code=CheckCode.INTERNAL_UNEXPECTED_ENTRY.value, code_path=package
            )
        except DraftError as exc:
            issues.append(
                CheckIssue(code=exc.code, severity=IssueSeverity.ERROR, path=relative, line=None)
            )
            continue
        if kind is EntryKind.SYMLINK:
            issues.append(_error(CheckCode.PATH_SYMLINK_ESCAPE, relative))
            continue
        if kind is not EntryKind.DIRECTORY:
            issues.append(_error(CheckCode.INTERNAL_UNEXPECTED_ENTRY, relative))
            continue
        try:
            validate_operation_id(package.name)
        except DraftError:
            issues.append(_error(CheckCode.INTERNAL_UNEXPECTED_ENTRY, relative))
            continue
        if package.name not in held:
            issues.append(_error(CheckCode.INTERNAL_UNEXPECTED_ENTRY, relative))


_MID_WRITE_PHASES = (
    OperationPhase.PREPARED,
    OperationPhase.APPLYING,
    OperationPhase.VALIDATING,
    OperationPhase.ROLLING_BACK,
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
    if journal.kind is not OperationKind.TRASH:
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
    if journal.kind is not OperationKind.TRASH:
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


def _conclusions_unexpected(resolved: Path) -> bool:
    """not_generated 状态下磁盘是否意外出现普通结论页文件。"""
    candidate = resolved / CONCLUSIONS_PAGE
    return candidate.is_file() and not candidate.is_symlink()


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
        # 规格 §12.6：not_generated 时意外出现 conclusions.md 必须报告，不能被当作普通页。
        if _conclusions_unexpected(resolved):
            issues.append(_error(CheckCode.CONCLUSIONS_UNEXPECTED_FILE, CONCLUSIONS_PAGE, None))
        return issues
    document = graph.pages.get(CONCLUSIONS_PAGE)
    if document is not None and not is_conclusions_document(document, resolved):
        issues.append(_error(CheckCode.CONCLUSIONS_SHAPE_INVALID, CONCLUSIONS_PAGE, None))
    index = graph.pages.get(ROOT_PAGE)
    if index is not None and not is_conclusions_nav_canonical(index.text, resolved):
        issues.append(_error(CheckCode.CONCLUSIONS_NAVIGATION_INVALID, ROOT_PAGE, None))
    return issues


def _conclusions_state(resolved: Path) -> ConclusionsState | None:
    """读取所有权状态；标记缺失/无效时返回 None（不在此处报错）。"""
    from draft.graph import read_marker

    try:
        state = read_marker(resolved)
    except DraftError:
        return None
    return state.conclusions_state


def _conclusions_required(resolved: Path) -> bool:
    """conclusions.md 是否必须存在：标记有效时由所有权状态决定。

    标记缺失/无效时不在此处要求；对应诊断由加载期 marker issue 报告。
    """
    from draft.graph import read_marker

    try:
        state = read_marker(resolved)
    except DraftError:
        return False
    return state.conclusions_state is ConclusionsState.GENERATED


_ALLOWED_INTERNAL_ENTRIES = frozenset(
    {
        MARKER_PATH,
        Path(INTERNAL_DIR) / "operation.lock",
        Path(INTERNAL_DIR) / "operations",
        Path(INTERNAL_DIR) / "trash",
    }
)

# 内部协议目录：operations 与 trash 必须是目录，普通文件是非法形状。
_ALLOWED_INTERNAL_DIRECTORIES = frozenset(
    {
        Path(INTERNAL_DIR) / "operations",
        Path(INTERNAL_DIR) / "trash",
    }
)


def _check_internal_namespace(resolved: Path) -> list[CheckIssue]:
    """受管内部命名空间检查：candidate 与 registered 共享同一协议（规格 §5.4）。

    ``.draft/`` 顶层只允许 marker、operation.lock、operations 与 trash；未登记条目
    统一报告 internal.unexpected_entry。已知目录内部的非法形状由更具体的检查码报告，
    不在此处猜测。
    """
    issues: list[CheckIssue] = []
    internal = resolved / INTERNAL_DIR
    internal_children = _list_internal_directory(
        resolved, internal, code=CheckCode.INTERNAL_UNEXPECTED_ENTRY, issues=issues
    )
    if internal_children is None:
        return issues  # 缺失正常；普通文件/链接/不可读已由 helper 报告。
    for entry in internal_children:
        relative = entry.relative_to(resolved)
        if relative not in _ALLOWED_INTERNAL_ENTRIES:
            issues.append(_error(CheckCode.INTERNAL_UNEXPECTED_ENTRY, relative))
        elif entry.is_symlink():
            issues.append(_error(CheckCode.PATH_SYMLINK_ESCAPE, relative))
        elif relative in _ALLOWED_INTERNAL_DIRECTORIES and not entry.is_dir():
            # operations/trash 必须是目录：普通文件是内部命名空间非法形状。
            issues.append(_error(CheckCode.INTERNAL_UNEXPECTED_ENTRY, relative))
    return issues


def check_draft(
    root: Path,
    *,
    include_operation_state: bool = True,
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
    conclusions_required = _conclusions_required(resolved)
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

    result = load_graph_tolerant(resolved)

    ordered: list[CheckIssue] = []
    ordered.extend(sorted(layout, key=_sort_key))
    ordered.extend(sorted(_load_issues_in(result.issues, _LOAD_MARKER_ISSUES), key=_sort_key))
    # candidate 与 registered 共享操作日志、锁、trash 与结论所有权检查。
    if include_operation_state:
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
    ordered.extend(sorted(_check_internal_namespace(resolved), key=_sort_key))
    conclusions_state = _conclusions_state(resolved)
    if conclusions_state is ConclusionsState.NOT_GENERATED and _conclusions_unexpected(resolved):
        # not_generated 时意外出现的 conclusions.md 不是普通受管理页面：只报告
        # conclusions.unexpected_file，不把它的普通页面诊断（缺父主题/不可达等）当作噪声。
        ordered = [issue for issue in ordered if issue.path != CONCLUSIONS_PAGE]
    if conclusions_state is not None:
        ordered.extend(
            sorted(
                _check_conclusions_product(resolved, result.graph, conclusions_state),
                key=_sort_key,
            )
        )
    return CheckReport(issues=tuple(ordered))
