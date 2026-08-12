"""CLI 稳定结果类型、预演操作代码与统一分页信封的单一权威来源。

`result_type` 与预演 `operation` 是稳定机器协议值，序列化器和命令分派都从
这些有约束类型取值，不在各分支手写同一组字面量。分页信封由所有集合查询和
预演共享同一个构造边界。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from draft.diagnostic_codes import CheckCode
from draft.models import CheckIssue, CheckReport, IssueSeverity


class ResultType(StrEnum):
    """成功与失败 JSON 的稳定 ``result_type``。"""

    INIT = "init"
    ADOPT = "adopt"
    CHECK = "check"
    MOVE = "move"
    PROMOTE = "promote"
    COLLAPSE = "collapse"
    TRASH = "trash"
    RESTORE = "restore"
    RECOVER = "recover"
    QUERY_PAGE = "query.page"
    QUERY_LINKS = "query.links"
    QUERY_BACKLINKS = "query.backlinks"
    QUERY_RELATIONS = "query.relations"
    QUERY_STATUS = "query.status"
    QUERY_RECOVERABLE = "query.recoverable"
    PLAN = "plan"
    CREATE = "create"
    STATUS_SET = "status.set"
    RELATION_ADD = "relation.add"
    RELATION_REMOVE = "relation.remove"
    GENERATE_CONCLUSIONS = "generate-conclusions"
    ERROR = "error"


class DryRunOperation(StrEnum):
    """预演信封的稳定 ``operation``：只允许支持 ``--dry-run`` 的命令。"""

    CREATE = "create"
    MOVE = "move"
    PROMOTE = "promote"
    COLLAPSE = "collapse"
    TRASH = "trash"
    GENERATE_CONCLUSIONS = "generate-conclusions"
    STATUS_SET = "status.set"
    RELATION_ADD = "relation.add"
    RELATION_REMOVE = "relation.remove"


@dataclass(frozen=True)
class PageSlice[T]:
    items: tuple[T, ...]
    total: int
    offset: int
    limit: int
    has_more: bool
    next_offset: int | None


def paginate[T](items: tuple[T, ...], *, offset: int, limit: int) -> PageSlice[T]:
    """统一的集合分页：字段计算与 ``--count-only`` 共用同一序列化边界。"""
    selected = items[offset : offset + limit]
    next_offset = offset + len(selected)
    has_more = next_offset < len(items)
    return PageSlice(
        items=selected,
        total=len(items),
        offset=offset,
        limit=limit,
        has_more=has_more,
        next_offset=next_offset if has_more else None,
    )


def slice_payload(
    slice_: PageSlice[object], *, count_only: bool, items_key: str = "items"
) -> dict[str, object]:
    """分页信封 JSON：``count_only`` 只把结果字段置空，其余字段保持正常计算。

    ``items_key`` 区分集合查询（``items``）与预演（``effects``），字段计算规则
    共享同一个分页边界。
    """
    return {
        "total": slice_.total,
        "offset": slice_.offset,
        "limit": slice_.limit,
        "has_more": slice_.has_more,
        "next_offset": slice_.next_offset,
        items_key: [] if count_only else list(slice_.items),
    }


def collection_payload(
    items: Sequence[Mapping[str, object]],
    *,
    blocking_paths: Sequence[Path],
    complete: bool,
    offset: int,
    limit: int,
    count_only: bool,
) -> dict[str, object]:
    """集合查询双分页信封：items 与去重排序的 blocking_paths 应用同一窗口。

    ``complete=True`` 时后三个阻碍字段为 0/false/null（规格 §11）。``count_only``
    同时置空 items 与 blocking_paths，但两套计数与分页事实保持正常计算。
    """
    items_slice = paginate(tuple(dict(item) for item in items), offset=offset, limit=limit)
    blockers = () if complete else tuple(path.as_posix() for path in sorted(set(blocking_paths)))
    blockers_slice = paginate(blockers, offset=offset, limit=limit)
    return {
        "complete": complete,
        **slice_payload(items_slice, count_only=count_only),
        "blocking_paths": [] if count_only else list(blockers_slice.items),
        "blocking_total": blockers_slice.total,
        "blocking_has_more": blockers_slice.has_more,
        "blocking_next_offset": blockers_slice.next_offset,
    }


@dataclass(frozen=True)
class CheckSlice:
    """完整检查的有界结果投影：parser、人类渲染与 JSON 渲染共享同一信封。"""

    errors: tuple[CheckIssue, ...]
    warnings: tuple[CheckIssue, ...]
    infos: tuple[CheckIssue, ...]
    reviews: tuple[CheckIssue, ...]
    severity_counts: tuple[tuple[str, int], ...]
    code_counts: tuple[tuple[str, int], ...]
    filtered_total: int
    offset: int
    limit: int
    returned_count: int
    has_more: bool
    next_offset: int | None
    ok: bool


def _path_prefix_matches(issue: CheckIssue, prefixes: tuple[Path, ...]) -> bool:
    if issue.path is None:
        return False
    return any(issue.path.parts[: len(prefix.parts)] == prefix.parts for prefix in prefixes)


def project_check(
    report: CheckReport,
    *,
    codes: tuple[CheckCode, ...] = (),
    path_prefixes: tuple[Path, ...] = (),
    offset: int = 0,
    limit: int = 20,
    count_only: bool = False,
) -> CheckSlice:
    """先过滤、再分页、最后按严重级别分组；计数与 ok 只读未过滤完整扫描。

    ``codes`` 取并集、``path_prefixes`` 取并集，两组之间取交集。``count_only``
    保留全部计数与分页事实，但四个分组数组为空。
    """
    code_values = frozenset(code.value for code in codes)
    unfiltered = report.issues
    filtered = [
        issue
        for issue in unfiltered
        if (not code_values or issue.code in code_values)
        and (not path_prefixes or _path_prefix_matches(issue, path_prefixes))
    ]
    filtered_total = len(filtered)
    page = filtered[offset : offset + limit]
    returned_count = len(page)
    next_offset = offset + returned_count
    has_more = next_offset < filtered_total

    def _group(severity: IssueSeverity) -> tuple[CheckIssue, ...]:
        if count_only:
            return ()
        return tuple(issue for issue in page if issue.severity is severity)

    severity_counts = tuple(
        (
            severity.value,
            sum(1 for issue in unfiltered if issue.severity is severity),
        )
        for severity in (
            IssueSeverity.ERROR,
            IssueSeverity.WARNING,
            IssueSeverity.INFO,
            IssueSeverity.REVIEW,
        )
    )
    code_totals: dict[str, int] = {}
    for issue in unfiltered:
        code_totals[issue.code] = code_totals.get(issue.code, 0) + 1
    code_counts = tuple(sorted(code_totals.items()))
    return CheckSlice(
        errors=_group(IssueSeverity.ERROR),
        warnings=_group(IssueSeverity.WARNING),
        infos=_group(IssueSeverity.INFO),
        reviews=_group(IssueSeverity.REVIEW),
        severity_counts=severity_counts,
        code_counts=code_counts,
        filtered_total=filtered_total,
        offset=offset,
        limit=limit,
        returned_count=returned_count,
        has_more=has_more,
        next_offset=next_offset if has_more else None,
        ok=not report.errors,
    )
