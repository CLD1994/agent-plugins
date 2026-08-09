"""页面状态与关系元数据的幂等修改与最终图验证。"""

from __future__ import annotations

import unicodedata
from pathlib import Path

from draft.errors import DraftError
from draft.markdown import locate_status_value_span, parse_page, rewrite_link_destinations
from draft.models import (
    FileMutation,
    MutationKind,
    OperationPhase,
    OperationPlan,
    OperationResult,
    StatusCode,
)
from draft.operations import sha256_bytes
from draft.plan_effects import PlanAction, PlanEffect
from draft.vocabulary import status_label


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


def plan_set_status(
    root: Path,
    page: Path,
    code: StatusCode,
    *,
    note: str | None = None,
    clear_note: bool = False,
) -> OperationPlan:
    """构造状态修改计划；最终内存图验证通过才产生变更。"""
    from draft.paths import resolve_draft_root
    from draft.query import PAGE_NOT_FOUND, load_complete_snapshot

    if note is not None and clear_note:
        raise DraftError("status.note_conflict")
    resolved = resolve_draft_root(root)
    graph = load_complete_snapshot(resolved)
    document = graph.pages.get(page)
    if document is None:
        raise DraftError(PAGE_NOT_FOUND, page)
    if code is document.status.code and note is None and not clear_note:
        return OperationPlan(kind="status.set", root=resolved, mutations=(), effects=())

    if clear_note:
        explanation: str | None = ""
    else:
        explanation = note if note is not None else document.status.explanation
    new_text = replace_status(document.text, code, explanation)
    if new_text == document.text:
        return OperationPlan(kind="status.set", root=resolved, mutations=(), effects=())

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
        effects=(PlanEffect(action=PlanAction.MODIFY, path=page),),
    )
    return plan


def _validate_final_graph(root: Path, page: Path, new_text: str) -> None:
    """用修改后的页面重建内存图并检查最终不变量；有错误时抛稳定领域错误。"""
    from dataclasses import replace

    from draft.graph import load_graph_tolerant
    from draft.resources import resolve_page_resources

    result = load_graph_tolerant(root)
    if result.issues:
        codes = tuple(issue.code for issue in result.issues if issue.severity.value == "error")
        raise DraftError("status.dirty", root, details=codes)
    graph = result.graph
    updated_document = parse_page(page, new_text)
    pages = dict(graph.pages)
    pages[page] = updated_document
    body_resources = dict(graph.body_resources)
    body_resources[page] = resolve_page_resources(root, updated_document)
    parents = dict(graph.parents)
    relations = dict(graph.relations)
    # 重算以 page 为来源的关系边（状态修改不影响关系，仅重建图对象）。
    graph = replace(
        graph,
        pages=pages,
        body_resources=body_resources,
        parents=parents,
        relations=relations,
    )
    from draft.checker import check_graph

    report = check_graph(graph)
    if report.errors:
        codes = tuple(issue.code for issue in report.errors)
        raise DraftError("status.invalid_final_state", page, details=codes)


def set_status(
    root: Path,
    page: Path,
    code: StatusCode,
    *,
    note: str | None = None,
    clear_note: bool = False,
) -> OperationResult:
    """执行状态修改；前置推进由 CLI 编排统一完成。"""
    from draft.operations import execute_plan
    from draft.paths import resolve_draft_root

    resolved = resolve_draft_root(root)
    plan = plan_set_status(resolved, page, code, note=note, clear_note=clear_note)
    if not plan.mutations:
        return OperationResult(
            operation_id="",
            kind="status.set",
            phase=OperationPhase.COMPLETED,
            changed_paths=(),
        )
    return execute_plan(plan)
