"""CLI 稳定结果类型、预演操作代码与统一分页信封的单一权威来源。

`result_type` 与预演 `operation` 是稳定机器协议值，序列化器和命令分派都从
这些有约束类型取值，不在各分支手写同一组字面量。分页信封由所有集合查询和
预演共享同一个构造边界。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


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


def slice_payload(slice_: PageSlice[object], *, count_only: bool) -> dict[str, object]:
    """分页信封 JSON：``count_only`` 只把 items 置空，其余字段保持正常计算。"""
    return {
        "total": slice_.total,
        "offset": slice_.offset,
        "limit": slice_.limit,
        "has_more": slice_.has_more,
        "next_offset": slice_.next_offset,
        "items": [] if count_only else list(slice_.items),
    }
