"""页面状态与关系元数据的幂等修改与最终图验证。"""

from __future__ import annotations

import unicodedata
from pathlib import Path

from draft.errors import DraftError
from draft.graph import MARKER_PATH, DraftGraph
from draft.markdown import locate_status_value_span, parse_page, rewrite_link_destinations
from draft.models import (
    FileMutation,
    MutationKind,
    OperationPhase,
    OperationPlan,
    OperationResult,
    PageDocument,
    Relation,
    RootState,
    StatusCode,
)
from draft.observations import (
    EMPTY_OBSERVATIONS,
    ObservationManifest,
    file_digest_observation,
    graph_observations,
)
from draft.operations import sha256_bytes
from draft.plan_effects import PlanAction, PlanEffect
from draft.vocabulary import (
    EditableRelationCode,
    RelationCode,
    relation_label,
    status_label,
)


def _target_marker_observations(resolved: Path, page: Path, text: str) -> ObservationManifest:
    """status set 的自有事实闭包：目标页与 marker 所有权。"""
    entries = [file_digest_observation(page, sha256_bytes(text.encode("utf-8")))]
    marker = resolved / MARKER_PATH
    if marker.is_file() and not marker.is_symlink():
        entries.append(file_digest_observation(MARKER_PATH, sha256_bytes(marker.read_bytes())))
    return ObservationManifest(entries=tuple(entries))


def replace_status(text: str, code: StatusCode, explanation: str | None) -> str:
    """替换状态值；explanation 为 None 时保留，为空字符串时清除。"""
    if explanation is not None:
        for ch in explanation:
            if ch in ("\r", "\n") or unicodedata.category(ch).startswith("C"):
                raise DraftError("status.invalid_note")
    span = locate_status_value_span(text)
    current = text[span.start : span.end]
    inner = current[1:-1]
    has_explanation = "：" in inner
    old_explanation = inner.split("：", 1)[1] if has_explanation else None
    if explanation is not None:
        new_inner = f"{status_label(code)}：{explanation}" if explanation else status_label(code)
    elif has_explanation:
        new_inner = f"{status_label(code)}：{old_explanation}"
    else:
        new_inner = status_label(code)
    replacement = f"`{new_inner}`"
    return rewrite_link_destinations(text, {span: replacement})


def append_machine_relation(text: str, code: RelationCode, target: Path, *, source: Path) -> str:
    """在开头机器区域末尾插入一条引用关系行。"""
    from draft.markdown import parse_page
    from draft.paths import relative_markdown_destination

    document = parse_page(source, text)
    machine_lines = _machine_line_spans(text, document)
    if not machine_lines:
        raise DraftError("relation.machine_block_missing")
    _last_start, _last_end = machine_lines[-1]
    lines = text.split("\n")
    anchor_line = text[:_last_end].count("\n")
    label = relation_label(code)
    relative = relative_markdown_destination(document.path, target, None)
    entry = f"> {label}：[{label}]({relative})"
    lines.insert(anchor_line + 1, entry)
    return "\n".join(lines)


def _machine_line_spans(text: str, document: PageDocument) -> list[tuple[int, int]]:
    """返回机器块内所有非空引用行的 (start, end) 跨度。"""
    spans: list[tuple[int, int]] = []
    for relation in document.relations:
        whole = relation.link.whole_span
        line_start = text.rfind("\n", 0, whole.start) + 1
        line_end = text.find("\n", whole.end)
        if line_end == -1:
            line_end = len(text)
        spans.append((line_start, line_end))
    return spans


def remove_machine_relation(text: str, code: RelationCode, target: Path, *, source: Path) -> str:
    """删除完整引用关系行（含其空引用行）；不删除正文中目标相同的普通链接。"""
    from draft.graph import resolve_local_destination
    from draft.markdown import parse_page, rewrite_link_destinations
    from draft.models import SourceSpan

    document = parse_page(source, text)
    edits: dict[SourceSpan, str] = {}
    for relation in document.relations:
        if relation.code.value != code.value:
            continue
        try:
            resolved, _anchor = resolve_local_destination(
                document.path, relation.link.destination, Path("/")
            )
        except DraftError:
            continue
        if resolved != target:
            continue
        # 删除整行引用关系：行首到行尾（含换行）。若该行是机器块内的引用行，
        # 把整行置空；如果因此留下孤立空引用行，一并删除由后续解析容忍。
        line_start = text.rfind("\n", 0, relation.link.whole_span.start) + 1
        line_end = text.find("\n", relation.link.whole_span.end)
        end = line_end + 1 if line_end != -1 else len(text)
        edits[SourceSpan(line_start, end)] = ""
    if not edits:
        return text
    return rewrite_link_destinations(text, edits)


def plan_set_status(
    root: Path,
    page: Path,
    code: StatusCode,
    *,
    note: str | None = None,
    clear_note: bool = False,
) -> OperationPlan:
    """构造状态修改计划；最终内存图验证通过才产生变更。

    candidate 根按操作级门禁：只拒绝目标页不可判定，不要求全根健康。
    """
    from draft.operation_facts import load_operation_facts
    from draft.paths import resolve_draft_root
    from draft.query import PAGE_NOT_FOUND

    if note is not None and clear_note:
        raise DraftError("status.note_conflict")
    resolved = resolve_draft_root(root)
    facts = load_operation_facts(resolved)
    graph = facts.graph
    if page in set(facts.blocking_paths):
        raise DraftError(PAGE_NOT_FOUND, page)
    document = graph.pages.get(page)
    if document is None:
        raise DraftError(PAGE_NOT_FOUND, page)
    if code is document.status.code and note is None and not clear_note:
        return OperationPlan(
            kind="status.set",
            root=resolved,
            mutations=(),
            root_state=facts.root_state,
            observations=EMPTY_OBSERVATIONS,
            effects=(),
        )

    if clear_note:
        explanation: str | None = ""
    else:
        explanation = note if note is not None else document.status.explanation
    new_text = replace_status(document.text, code, explanation)
    if new_text == document.text:
        return OperationPlan(
            kind="status.set",
            root=resolved,
            mutations=(),
            root_state=facts.root_state,
            observations=EMPTY_OBSERVATIONS,
            effects=(),
        )

    _validate_final_graph(resolved, page, new_text)

    before = document.text.encode("utf-8")
    after = new_text.encode("utf-8")
    plan = OperationPlan(
        kind="status.set",
        root=resolved,
        mutations=(
            FileMutation(
                page,
                MutationKind.WRITE,
                sha256_bytes(before),
                sha256_bytes(after),
                after,
            ),
        ),
        root_state=facts.root_state,
        observations=_target_marker_observations(resolved, page, document.text),
        effects=(PlanEffect(action=PlanAction.MODIFY, path=page),),
    )
    return plan


def _validate_final_graph(root: Path, page: Path, new_text: str) -> None:
    """验证目标页最终状态不变量：superseded 当且仅当声明 superseded_by。

    全根健康由 load_operation_facts（registered 严格 / candidate 操作级）把关；
    状态修改只拥有目标页自身的 superseded 一致性，不自动猜测或改写关系。
    """
    del root
    updated_document = parse_page(page, new_text)
    declares_superseded_by = any(
        relation.code is RelationCode.SUPERSEDED_BY for relation in updated_document.relations
    )
    is_superseded = updated_document.status.code is StatusCode.SUPERSEDED
    if declares_superseded_by != is_superseded:
        raise DraftError("status.invalid_final_state", page)


def plan_add_relation(
    root: Path,
    source: Path,
    code: EditableRelationCode,
    target: Path,
) -> OperationPlan:
    """构造关系添加计划：depends_on 单向，supersedes/challenges 双向原子。

    candidate 根按操作级门禁：两端可判定即可，不要求全根健康。
    """
    from draft.operation_facts import load_operation_facts
    from draft.paths import resolve_draft_root
    from draft.query import PAGE_NOT_FOUND

    resolved = resolve_draft_root(root)
    facts = load_operation_facts(resolved)
    graph = facts.graph
    blocking = set(facts.blocking_paths)
    if source not in graph.pages or source in blocking:
        raise DraftError(PAGE_NOT_FOUND, source)
    if target not in graph.pages or target in blocking:
        raise DraftError(PAGE_NOT_FOUND, target)
    if source == target:
        raise DraftError("relation.self_reference", source)

    if code is EditableRelationCode.DEPENDS_ON:
        return _single_relation_plan(
            resolved,
            graph,
            source,
            EditableRelationCode.DEPENDS_ON,
            target,
            root_state=facts.root_state,
        )
    if code is EditableRelationCode.CHALLENGES:
        return _pair_relation_plan(
            resolved,
            graph,
            source,
            EditableRelationCode.CHALLENGES,
            RelationCode.CHALLENGED_BY,
            target,
            root_state=facts.root_state,
        )
    if code is EditableRelationCode.SUPERSEDES:
        return _supersedes_plan(resolved, graph, source, target, root_state=facts.root_state)
    raise DraftError("relation.unsupported", source)


def _reject_dependency_cycle(graph: DraftGraph, source: Path, target: Path) -> None:
    """添加 source->target 依赖后是否会形成环。"""
    if _would_form_cycle(graph, RelationCode.DEPENDS_ON, source, target):
        raise DraftError("relation.dependency_cycle", source)


def _reject_replacement_cycle(graph: DraftGraph, source: Path, target: Path) -> None:
    if _would_form_cycle(graph, RelationCode.SUPERSEDES, source, target):
        raise DraftError("relation.replacement_cycle", source)


def _would_form_cycle(graph: DraftGraph, code: RelationCode, source: Path, target: Path) -> bool:
    """把 source->target 新边加入现有图中，判断是否出现有向环。"""
    adjacency: dict[Path, set[Path]] = {path: set() for path in graph.pages}
    for src, dst, _line in graph.relations.get(code, ()):
        adjacency[src].add(dst)
    # 新边是 source → target：加入该边后是否出现有向环。
    adjacency[source].add(target)
    color: dict[Path, int] = {}
    for start in sorted(adjacency):
        if color.get(start, 0) != 0:
            continue
        color[start] = 1
        stack: list[tuple[Path, int]] = [(start, 0)]
        while stack:
            node, next_index = stack[-1]
            neighbors = sorted(adjacency.get(node, ()))
            if next_index < len(neighbors):
                stack[-1] = (node, next_index + 1)
                neighbor = neighbors[next_index]
                state = color.get(neighbor, 0)
                if state == 0:
                    color[neighbor] = 1
                    stack.append((neighbor, 0))
                elif state == 1:
                    return True
            else:
                color[node] = 2
                stack.pop()
    return False


def _single_relation_plan(
    root: Path,
    graph: DraftGraph,
    source: Path,
    code: EditableRelationCode,
    target: Path,
    *,
    root_state: RootState,
) -> OperationPlan:
    """单向关系添加：若已存在则幂等无变化。"""
    document = graph.pages[source]
    existing = any(
        rel.code.value == code.value and _resolves_to_page(graph, source, rel, target)
        for rel in document.relations
    )
    if existing:
        return OperationPlan(
            kind="relation.add",
            root=root,
            mutations=(),
            root_state=RootState.REGISTERED,
            observations=EMPTY_OBSERVATIONS,
            effects=(),
        )
    if code is EditableRelationCode.DEPENDS_ON:
        _reject_dependency_cycle(graph, source, target)
    new_text = append_machine_relation(
        document.text, RelationCode(code.value), target, source=source
    )
    return _write_plan(
        root, "relation.add", source, document.text, new_text, graph, root_state=root_state
    )


def _pair_relation_plan(
    root: Path,
    graph: DraftGraph,
    source: Path,
    code: EditableRelationCode,
    reverse: RelationCode,
    target: Path,
    *,
    root_state: RootState,
) -> OperationPlan:
    """双向关系添加：补齐缺失的一边；双方都存在则幂等。"""
    src_doc = graph.pages[source]
    tgt_doc = graph.pages[target]
    forward = any(
        rel.code.value == code.value and _resolves_to_page(graph, source, rel, target)
        for rel in src_doc.relations
    )
    backward = any(
        rel.code.value == reverse.value and _resolves_to_page(graph, target, rel, source)
        for rel in tgt_doc.relations
    )
    edits: dict[Path, tuple[str, str]] = {}
    if not forward:
        edits[source] = (
            src_doc.text,
            append_machine_relation(src_doc.text, RelationCode(code.value), target, source=source),
        )
    if not backward:
        edits[target] = (
            tgt_doc.text,
            append_machine_relation(tgt_doc.text, reverse, source, source=target),
        )
    if not edits:
        return OperationPlan(
            kind="relation.add",
            root=root,
            mutations=(),
            root_state=RootState.REGISTERED,
            observations=EMPTY_OBSERVATIONS,
            effects=(),
        )
    return _multi_write_plan(root, "relation.add", edits, graph, root_state=root_state)


def _supersedes_plan(
    root: Path,
    graph: DraftGraph,
    source: Path,
    target: Path,
    *,
    root_state: RootState,
) -> OperationPlan:
    """取代：双向 supersedes/superseded_by + 目标状态 superseded。"""
    src_doc = graph.pages[source]
    tgt_doc = graph.pages[target]
    edits: dict[Path, tuple[str, str]] = {}
    forward = any(
        rel.code.value == RelationCode.SUPERSEDES.value
        and _resolves_to_page(graph, source, rel, target)
        for rel in src_doc.relations
    )
    if not forward:
        _reject_replacement_cycle(graph, source, target)
        edits[source] = (
            src_doc.text,
            append_machine_relation(src_doc.text, RelationCode.SUPERSEDES, target, source=source),
        )
    backward = any(
        rel.code.value == RelationCode.SUPERSEDED_BY.value
        and _resolves_to_page(graph, target, rel, source)
        for rel in tgt_doc.relations
    )
    if not backward:
        tgt_new = append_machine_relation(
            tgt_doc.text,
            RelationCode.SUPERSEDED_BY,
            source,
            source=target,
        )
        edits[target] = (tgt_doc.text, tgt_new)
    # 目标状态改为 superseded（保留说明）。
    target_text = edits[target][1] if target in edits else tgt_doc.text
    if tgt_doc.status.code is not StatusCode.SUPERSEDED:
        target_text = replace_status(target_text, StatusCode.SUPERSEDED, tgt_doc.status.explanation)
        edits[target] = (tgt_doc.text, target_text)
    if not edits:
        return OperationPlan(
            kind="relation.add",
            root=root,
            mutations=(),
            root_state=RootState.REGISTERED,
            observations=EMPTY_OBSERVATIONS,
            effects=(),
        )
    return _multi_write_plan(root, "relation.add", edits, graph, root_state=root_state)


def _resolves_to_page(graph: DraftGraph, source: Path, relation: Relation, target: Path) -> bool:
    from draft.graph import resolve_local_destination

    try:
        resolved, _anchor = resolve_local_destination(
            source,
            relation.link.destination,
            Path("/"),
        )
    except DraftError:
        return False
    return resolved == target


def _write_plan(
    root: Path,
    kind: str,
    path: Path,
    before: str,
    after: str,
    graph: DraftGraph,
    *,
    root_state: RootState,
) -> OperationPlan:
    return _multi_write_plan(root, kind, {path: (before, after)}, graph, root_state=root_state)


def _multi_write_plan(
    root: Path,
    kind: str,
    edits: dict[Path, tuple[str, str]],
    graph: DraftGraph,
    *,
    root_state: RootState,
) -> OperationPlan:
    mutations = [
        FileMutation(
            path,
            MutationKind.WRITE,
            sha256_bytes(before.encode("utf-8")),
            sha256_bytes(after.encode("utf-8")),
            after.encode("utf-8"),
        )
        for path, (before, after) in sorted(edits.items(), key=lambda item: str(item[0]))
    ]
    effects = tuple(
        PlanEffect(action=PlanAction.MODIFY, path=path) for path in sorted(edits, key=str)
    )
    return OperationPlan(
        kind=kind,
        root=root,
        mutations=tuple(mutations),
        root_state=root_state,
        observations=graph_observations(graph),
        effects=effects,
    )


def plan_remove_relation(
    root: Path,
    source: Path,
    code: EditableRelationCode,
    target: Path,
    *,
    restored_status: StatusCode | None = None,
) -> OperationPlan:
    """构造关系删除计划：幂等删除，半对清理，取代关系恢复必要状态。"""
    from draft.operation_facts import load_operation_facts
    from draft.paths import resolve_draft_root
    from draft.query import PAGE_NOT_FOUND

    resolved = resolve_draft_root(root)
    facts = load_operation_facts(resolved)
    graph = facts.graph
    blocking = set(facts.blocking_paths)
    if source not in graph.pages or source in blocking:
        raise DraftError(PAGE_NOT_FOUND, source)
    if target not in graph.pages or target in blocking:
        raise DraftError(PAGE_NOT_FOUND, target)

    if code is EditableRelationCode.DEPENDS_ON:
        return _single_relation_remove(
            resolved,
            graph,
            source,
            RelationCode.DEPENDS_ON,
            target,
            root_state=facts.root_state,
        )
    if code is EditableRelationCode.CHALLENGES:
        return _pair_relation_remove(
            resolved,
            graph,
            source,
            RelationCode.CHALLENGES,
            RelationCode.CHALLENGED_BY,
            target,
            root_state=facts.root_state,
        )
    if code is EditableRelationCode.SUPERSEDES:
        return _supersedes_remove(
            resolved,
            graph,
            source,
            target,
            restored_status=restored_status,
            root_state=facts.root_state,
        )
    raise DraftError("relation.unsupported", source)


def _single_relation_remove(
    root: Path,
    graph: DraftGraph,
    source: Path,
    code: RelationCode,
    target: Path,
    *,
    root_state: RootState,
) -> OperationPlan:
    document = graph.pages[source]
    existing = any(
        rel.code.value == code.value and _resolves_to_page(graph, source, rel, target)
        for rel in document.relations
    )
    if not existing:
        return OperationPlan(
            kind="relation.remove",
            root=root,
            mutations=(),
            root_state=root_state,
            observations=EMPTY_OBSERVATIONS,
            effects=(),
        )
    new_text = remove_machine_relation(document.text, code, target, source=source)
    return _write_plan(
        root, "relation.remove", source, document.text, new_text, graph, root_state=root_state
    )


def _pair_relation_remove(
    root: Path,
    graph: DraftGraph,
    source: Path,
    code: RelationCode,
    reverse: RelationCode,
    target: Path,
    *,
    root_state: RootState,
) -> OperationPlan:
    src_doc = graph.pages[source]
    tgt_doc = graph.pages[target]
    edits: dict[Path, tuple[str, str]] = {}
    forward = any(
        rel.code.value == code.value and _resolves_to_page(graph, source, rel, target)
        for rel in src_doc.relations
    )
    backward = any(
        rel.code.value == reverse.value and _resolves_to_page(graph, target, rel, source)
        for rel in tgt_doc.relations
    )
    if forward:
        edits[source] = (
            src_doc.text,
            remove_machine_relation(src_doc.text, code, target, source=source),
        )
    if backward:
        edits[target] = (
            tgt_doc.text,
            remove_machine_relation(tgt_doc.text, reverse, source, source=target),
        )
    if not edits:
        return OperationPlan(
            kind="relation.remove",
            root=root,
            mutations=(),
            root_state=RootState.REGISTERED,
            observations=EMPTY_OBSERVATIONS,
            effects=(),
        )
    return _multi_write_plan(root, "relation.remove", edits, graph, root_state=root_state)


def _supersedes_remove(
    root: Path,
    graph: DraftGraph,
    source: Path,
    target: Path,
    *,
    restored_status: StatusCode | None,
    root_state: RootState,
) -> OperationPlan:
    src_doc = graph.pages[source]
    tgt_doc = graph.pages[target]
    edits: dict[Path, tuple[str, str]] = {}
    forward = any(
        rel.code.value == RelationCode.SUPERSEDES.value
        and _resolves_to_page(graph, source, rel, target)
        for rel in src_doc.relations
    )
    backward = any(
        rel.code.value == RelationCode.SUPERSEDED_BY.value
        and _resolves_to_page(graph, target, rel, source)
        for rel in tgt_doc.relations
    )
    if forward:
        edits[source] = (
            src_doc.text,
            remove_machine_relation(src_doc.text, RelationCode.SUPERSEDES, target, source=source),
        )
    if backward:
        edits[target] = (
            tgt_doc.text,
            remove_machine_relation(
                tgt_doc.text, RelationCode.SUPERSEDED_BY, source, source=target
            ),
        )
    # 目标是否还有其他 superseded_by：无则恢复状态。
    remaining = [
        rel
        for rel in tgt_doc.relations
        if rel.code.value == RelationCode.SUPERSEDED_BY.value
        and not _resolves_to_page(graph, target, rel, source)
    ]
    if not remaining and tgt_doc.status.code is StatusCode.SUPERSEDED:
        if restored_status is None:
            raise DraftError("relation.restored_status_required", target)
        if restored_status is StatusCode.SUPERSEDED:
            raise DraftError("relation.invalid_restored_status", target)
        target_text = edits[target][1] if target in edits else tgt_doc.text
        target_text = replace_status(target_text, restored_status, tgt_doc.status.explanation)
        edits[target] = (tgt_doc.text, target_text)
    if not edits:
        return OperationPlan(
            kind="relation.remove",
            root=root,
            mutations=(),
            root_state=RootState.REGISTERED,
            observations=EMPTY_OBSERVATIONS,
            effects=(),
        )
    return _multi_write_plan(root, "relation.remove", edits, graph, root_state=root_state)


def _execute_or_empty(plan: OperationPlan, kind: str) -> OperationResult:
    if not plan.mutations:
        return OperationResult(
            operation_id="",
            kind=kind,
            phase=OperationPhase.COMPLETED,
            changed_paths=(),
        )
    from draft.operations import execute_plan

    return execute_plan(plan)


def set_status(
    root: Path,
    page: Path,
    code: StatusCode,
    *,
    note: str | None = None,
    clear_note: bool = False,
) -> OperationResult:
    """执行状态修改；前置推进由 CLI 编排统一完成。"""
    from draft.paths import resolve_draft_root

    resolved = resolve_draft_root(root)
    plan = plan_set_status(resolved, page, code, note=note, clear_note=clear_note)
    return _execute_or_empty(plan, "status.set")


def add_relation(
    root: Path,
    source: Path,
    code: EditableRelationCode,
    target: Path,
) -> OperationResult:
    """执行关系添加；前置推进由 CLI 编排统一完成。"""
    from draft.paths import resolve_draft_root

    resolved = resolve_draft_root(root)
    plan = plan_add_relation(resolved, source, code, target)
    return _execute_or_empty(plan, "relation.add")


def remove_relation(
    root: Path,
    source: Path,
    code: EditableRelationCode,
    target: Path,
    *,
    restored_status: StatusCode | None = None,
) -> OperationResult:
    """执行关系删除；前置推进由 CLI 编排统一完成。"""
    from draft.paths import resolve_draft_root

    resolved = resolve_draft_root(root)
    plan = plan_remove_relation(resolved, source, code, target, restored_status=restored_status)
    return _execute_or_empty(plan, "relation.remove")
