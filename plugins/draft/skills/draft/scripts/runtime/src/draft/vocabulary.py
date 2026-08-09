"""稳定状态与关系代码，以及当前中文 Markdown 标签的单一权威来源。

机器协议值（`StatusCode`/`RelationCode`）只在这里定义；解析器、渲染器、检查器、
结构修改与命令分派都从这些有约束类型取值，不在消费者中复制同一组字面量。
中文标签映射同样只在这里维护，供 Markdown 解析与渲染边界使用。
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType


class StatusCode(StrEnum):
    PENDING = "pending"
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    SUPERSEDED = "superseded"
    CLOSED = "closed"


class ConclusionsState(StrEnum):
    """结论页所有权状态；只从 not_generated 前进到 generated，不回退。"""

    NOT_GENERATED = "not_generated"
    GENERATED = "generated"


class RelationCode(StrEnum):
    PARENT = "parent"
    DEPENDS_ON = "depends_on"
    SUPERSEDES = "supersedes"
    SUPERSEDED_BY = "superseded_by"
    CHALLENGES = "challenges"
    CHALLENGED_BY = "challenged_by"


class EditableRelationCode(StrEnum):
    """通过 CLI 可编辑的主动方向关系；反向关系由配对命令自动维护。"""

    DEPENDS_ON = RelationCode.DEPENDS_ON.value
    SUPERSEDES = RelationCode.SUPERSEDES.value
    CHALLENGES = RelationCode.CHALLENGES.value


_STATUS_LABELS: Mapping[StatusCode, str] = MappingProxyType(
    {
        StatusCode.PENDING: "待讨论",
        StatusCode.PROPOSED: "当前提案",
        StatusCode.CONFIRMED: "已确认",
        StatusCode.SUPERSEDED: "已取代",
        StatusCode.CLOSED: "已关闭",
    }
)
_RELATION_LABELS: Mapping[RelationCode, str] = MappingProxyType(
    {
        RelationCode.PARENT: "父主题",
        RelationCode.DEPENDS_ON: "依赖",
        RelationCode.SUPERSEDES: "取代",
        RelationCode.SUPERSEDED_BY: "被取代",
        RelationCode.CHALLENGES: "质疑",
        RelationCode.CHALLENGED_BY: "被质疑",
    }
)


def status_label(code: StatusCode) -> str:
    return _STATUS_LABELS[code]


def status_code(label: str) -> StatusCode:
    try:
        return next(code for code, value in _STATUS_LABELS.items() if value == label)
    except StopIteration as exc:
        raise ValueError(label) from exc


def relation_label(code: RelationCode) -> str:
    return _RELATION_LABELS[code]


def relation_code(label: str) -> RelationCode:
    try:
        return next(code for code, value in _RELATION_LABELS.items() if value == label)
    except StopIteration as exc:
        raise ValueError(label) from exc
