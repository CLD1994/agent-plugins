"""解析、诊断并稳定序列化页面 YAML frontmatter。

解析结果中的 ``SourceSpan`` 使用原始 Python 字符串的半开字符偏移；行号从 1 开始，
并按原文识别 LF、CRLF 与 CR。解析器不规范化输入文本，写回只替换已认证的头部。
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Protocol, cast

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError
from ruamel.yaml.events import (
    AliasEvent,
    MappingEndEvent,
    MappingStartEvent,
    ScalarEvent,
    SequenceEndEvent,
    SequenceStartEvent,
)
from ruamel.yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode
from ruamel.yaml.tokens import AliasToken, AnchorToken, CommentToken, TagToken

from draft.diagnostic_codes import (
    CheckCode,
    FrontmatterReason,
    PageNoteReason,
    PageParentReason,
    PageStatusReason,
    RelationDeclarationReason,
)
from draft.models import CheckIssue, IssueSeverity, SourceSpan, StatusValue
from draft.vocabulary import RelationCode, StatusCode

FRONTMATTER_INVALID = CheckCode.PAGE_FRONTMATTER_INVALID.value
STATUS_INVALID = CheckCode.PAGE_STATUS_INVALID.value
NOTE_INVALID = CheckCode.PAGE_NOTE_INVALID.value
PARENT_INVALID = CheckCode.PAGE_PARENT_INVALID.value
RELATION_DECLARATION_INVALID = CheckCode.RELATION_DECLARATION_INVALID.value

_FRONTMATTER_MISSING = FrontmatterReason.MISSING.value
_FRONTMATTER_DELIMITER_INVALID = FrontmatterReason.DELIMITER_INVALID.value
_FRONTMATTER_YAML_INVALID = FrontmatterReason.YAML_INVALID.value
_FRONTMATTER_NOT_MAPPING = FrontmatterReason.NOT_MAPPING.value
_FRONTMATTER_DUPLICATE_KEY = FrontmatterReason.DUPLICATE_KEY.value
_FRONTMATTER_UNSUPPORTED_FEATURE = FrontmatterReason.UNSUPPORTED_FEATURE.value
_FRONTMATTER_UNKNOWN_KEY = FrontmatterReason.UNKNOWN_KEY.value
_FRONTMATTER_INVALID_VALUE_TYPE = FrontmatterReason.INVALID_VALUE_TYPE.value
_STATUS_MISSING = PageStatusReason.MISSING.value
_STATUS_INVALID_TYPE = PageStatusReason.INVALID_TYPE.value
_STATUS_INVALID_VALUE = PageStatusReason.INVALID_VALUE.value
_NOTE_EMPTY = PageNoteReason.EMPTY.value
_NOTE_MULTILINE = PageNoteReason.MULTILINE.value
_NOTE_CONTROL_CHARACTER = PageNoteReason.CONTROL_CHARACTER.value
_PARENT_UNEXPECTED = PageParentReason.UNEXPECTED.value
_PARENT_MISSING = PageParentReason.MISSING.value
_PARENT_INVALID_PATH = PageParentReason.INVALID_PATH.value
_RELATION_CONTAINER_EMPTY = RelationDeclarationReason.CONTAINER_EMPTY.value
_RELATION_MEMBER_SHAPE = RelationDeclarationReason.MEMBER_SHAPE.value
_RELATION_TYPE_UNKNOWN = RelationDeclarationReason.TYPE_UNKNOWN.value
_RELATION_TARGET_PATH_INVALID = RelationDeclarationReason.TARGET_PATH_INVALID.value
_YAML_LINE_BREAKS = "\r\n\u0085\u2028\u2029"
_YAML_NORMALIZED_PATH_CHARACTERS = "\u0085"
_TAG_PREFIX = "tag:yaml.org,2002:"
_TAG_STR = f"{_TAG_PREFIX}str"
_TAG_NULL = f"{_TAG_PREFIX}null"
_TAG_BOOL = f"{_TAG_PREFIX}bool"
_TAG_INT = f"{_TAG_PREFIX}int"
_TAG_FLOAT = f"{_TAG_PREFIX}float"
_TAG_TIMESTAMP = f"{_TAG_PREFIX}timestamp"
_TAG_VALUE = f"{_TAG_PREFIX}value"
_TIMESTAMP_DATE = re.compile(r"^(?P<year>[0-9]{4})-(?P<month>[0-9]{2})-(?P<day>[0-9]{2})$")
_TIMESTAMP_DATETIME = re.compile(
    r"^(?P<year>[0-9]{4})[ \t]*-(?P<month>[0-9]{1,2})[ \t]*-"
    r"(?P<day>[0-9]{1,2})(?:(?:[Tt])|[ \t]+)"
    r"(?P<hour>[0-9]{1,2})[ \t]*:[ \t]*(?P<minute>[0-9]{2})"
    r"[ \t]*:[ \t]*(?P<second>[0-9]{2})"
    r"(?:\.(?P<fraction>[0-9]*))?"
    r"(?:[ \t]*(?P<timezone>Z|[-+][0-9]{1,2}(?::[0-9]{2})?))?$"
)
_BASE_DIGITS = "0123456789abcdef"


@dataclass(frozen=True)
class FrontmatterRelation:
    code: RelationCode
    target: Path
    span: SourceSpan
    type_span: SourceSpan
    target_span: SourceSpan
    line: int


@dataclass(frozen=True)
class FrontmatterMetadata:
    status: StatusValue
    parent: Path | None
    relations: tuple[FrontmatterRelation, ...]
    status_span: SourceSpan | None = None
    parent_span: SourceSpan | None = None
    parent_line: int | None = None

    @property
    def note(self) -> str | None:
        return self.status.explanation


@dataclass(frozen=True)
class FrontmatterDiagnostic:
    code: str
    reason: str
    path: Path
    line: int | None = None
    span: SourceSpan | None = None
    raw_line: str | None = None
    key_name: str | None = None

    @property
    def details(self) -> tuple[str, ...]:
        if self.reason == _FRONTMATTER_UNKNOWN_KEY and self.key_name is not None:
            return (self.reason, self.key_name)
        return (self.reason,)

    @property
    def source_span(self) -> SourceSpan | None:
        return self.span

    def as_check_issue(self) -> CheckIssue:
        return CheckIssue(
            code=self.code,
            severity=IssueSeverity.ERROR,
            path=self.path,
            line=self.line,
            details=self.details,
        )


@dataclass(frozen=True)
class FrontmatterParseResult:
    metadata: FrontmatterMetadata | None
    diagnostics: tuple[FrontmatterDiagnostic, ...]
    body_start: int | None
    header_span: SourceSpan | None = None

    @property
    def is_valid(self) -> bool:
        return self.metadata is not None and not self.diagnostics

    @property
    def body_offset(self) -> int | None:
        return self.body_start


@dataclass(frozen=True)
class _Line:
    start: int
    end: int
    content: str
    line_ending: str


class _Mark(Protocol):
    line: int
    column: int


class _NodeShape(Protocol):
    tag: str
    start_mark: _Mark
    end_mark: _Mark


class _MappingShape(_NodeShape, Protocol):
    value: list[tuple[Node, Node]]


class _SequenceShape(_NodeShape, Protocol):
    value: list[Node]


class _ScalarShape(_NodeShape, Protocol):
    value: str


class _TokenShape(Protocol):
    start_mark: _Mark
    end_mark: _Mark


def _mapping_pairs(node: MappingNode) -> list[tuple[Node, Node]]:
    return cast(_MappingShape, node).value


def _sequence_values(node: SequenceNode) -> list[Node]:
    return cast(_SequenceShape, node).value


def _scalar_value(node: ScalarNode) -> str:
    return cast(_ScalarShape, node).value


def _node_tag(node: Node) -> str:
    return cast(_NodeShape, node).tag


def _node_mark(node: Node, name: str) -> _Mark:
    return cast(_Mark, getattr(cast(object, node), name))


def _scan_tokens(header: str) -> tuple[object, ...]:
    loader = YAML(typ="rt")
    return tuple(cast(Iterable[object], _yaml_call(loader, "scan", header)))


def _token_comment(token: object) -> object:
    attribute = "comment"
    return cast(object, getattr(token, attribute, None))


def _yaml_call(loader: object, method_name: str, header: str) -> object:
    method = cast(Callable[[str], object], getattr(loader, method_name))
    return method(header)


def _compose_node(header: str) -> Node | None:
    # Compose with the safe YAML resolver so scalar ``value`` contains the
    # decoded YAML value.  Round-trip composition keeps a BEL placeholder in
    # folded block scalars; that placeholder is parser bookkeeping, not page
    # metadata.  Source marks are still retained by safe composition for
    # diagnostics.
    loader = YAML(typ="safe")
    composed = _yaml_call(loader, "compose", header)
    return composed if isinstance(composed, Node) else None


class _CanonicalizationError(ValueError):
    """A parsed YAML node has no authenticated canonical identity."""


def _canonical_int(value: str) -> str:
    compact = value.replace("_", "")
    sign = ""
    if compact.startswith(("+", "-")):
        sign, compact = compact[0], compact[1:]
    if not compact:
        raise _CanonicalizationError("integer has no digits")

    base = 10
    if compact.startswith("0b"):
        base, compact = 2, compact[2:]
    elif compact.startswith("0o"):
        base, compact = 8, compact[2:]
    elif compact.startswith("0x"):
        base, compact = 16, compact[2:]
    if not compact:
        raise _CanonicalizationError("integer has no digits")

    decimal_digits: list[int] = [0]
    for character in compact.lower():
        digit = _BASE_DIGITS.find(character)
        if digit < 0 or digit >= base:
            raise _CanonicalizationError("integer contains an invalid digit")
        carry = digit
        for index in range(len(decimal_digits) - 1, -1, -1):
            total = decimal_digits[index] * base + carry
            decimal_digits[index] = total % 10
            carry = total // 10
        while carry:
            decimal_digits.insert(0, carry % 10)
            carry //= 10

    digits = "".join(str(digit) for digit in decimal_digits).lstrip("0") or "0"
    return digits if digits == "0" or sign != "-" else f"-{digits}"


def _canonical_float(value: str) -> tuple[object, ...]:
    compact = value.replace("_", "")
    folded = compact.casefold()
    if folded == ".nan":
        return ("nan",)
    if folded in {".inf", "+.inf"}:
        return ("infinity", 1)
    if folded == "-.inf":
        return ("infinity", -1)

    try:
        number = Decimal(compact)
    except (InvalidOperation, ValueError):
        raise _CanonicalizationError("float has no canonical decimal form") from None
    if number.is_nan():
        return ("nan",)
    if number.is_infinite():
        return ("infinity", -1 if number.is_signed() else 1)

    sign, digits_tuple, raw_exponent = number.as_tuple()
    if not isinstance(raw_exponent, int):
        raise _CanonicalizationError("float has no canonical decimal form")
    exponent = raw_exponent
    digits = list(digits_tuple)
    while digits and digits[-1] == 0:
        digits.pop()
        exponent += 1
    if not digits:
        return ("finite", 0)
    return ("finite", sign, tuple(digits), exponent)


def _is_leap_year(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _days_in_month(year: int, month: int) -> int:
    if month == 2:
        return 29 if _is_leap_year(year) else 28
    if month in {4, 6, 9, 11}:
        return 30
    return 31


def _shift_date(year: int, month: int, day: int, days: int) -> tuple[int, int, int]:
    while days < 0:
        day -= 1
        if day == 0:
            month -= 1
            if month == 0:
                year -= 1
                month = 12
            day = _days_in_month(year, month)
        days += 1
    while days > 0:
        day += 1
        if day > _days_in_month(year, month):
            day = 1
            month += 1
            if month == 13:
                year += 1
                month = 1
        days -= 1
    return year, month, day


def _canonical_timestamp(value: str) -> tuple[object, ...]:
    match = _TIMESTAMP_DATE.fullmatch(value)
    if match is not None:
        year = int(match.group("year"))
        month = int(match.group("month"))
        day = int(match.group("day"))
        hour = minute = second = 0
        fraction = ""
        timezone = None
    else:
        match = _TIMESTAMP_DATETIME.fullmatch(value)
        if match is None:
            raise _CanonicalizationError("timestamp has no canonical form")
        year = int(match.group("year"))
        month = int(match.group("month"))
        day = int(match.group("day"))
        hour = int(match.group("hour"))
        minute = int(match.group("minute"))
        second = int(match.group("second"))
        fraction = (match.group("fraction") or "").rstrip("0")
        timezone = match.group("timezone")

    if not 1 <= year <= 9999 or not 1 <= month <= 12:
        raise _CanonicalizationError("timestamp date is out of range")
    if not 1 <= day <= _days_in_month(year, month):
        raise _CanonicalizationError("timestamp date is out of range")
    if not 0 <= hour <= 23 or not 0 <= minute <= 59 or not 0 <= second <= 59:
        raise _CanonicalizationError("timestamp time is out of range")

    offset_minutes = 0
    if timezone is not None and timezone != "Z":
        sign = -1 if timezone.startswith("-") else 1
        offset_text = timezone[1:]
        if ":" in offset_text:
            offset_hour_text, offset_minute_text = offset_text.split(":", 1)
        else:
            offset_hour_text, offset_minute_text = offset_text, "0"
        offset_hour = int(offset_hour_text)
        offset_minute = int(offset_minute_text)
        if not 0 <= offset_hour <= 23 or not 0 <= offset_minute <= 59:
            raise _CanonicalizationError("timestamp timezone is out of range")
        offset_minutes = sign * (offset_hour * 60 + offset_minute)

    total_seconds = hour * 3600 + minute * 60 + second - offset_minutes * 60
    day_delta, utc_seconds = divmod(total_seconds, 24 * 60 * 60)
    year, month, day = _shift_date(year, month, day, day_delta)
    if not 1 <= year <= 9999:
        raise _CanonicalizationError("timestamp date is out of range")
    utc_hour, remainder = divmod(utc_seconds, 3600)
    utc_minute, utc_second = divmod(remainder, 60)
    return (year, month, day, utc_hour, utc_minute, utc_second, fraction)


def _canonical_scalar_identity(node: ScalarNode) -> tuple[object, ...]:
    tag = _node_tag(node)
    value = _scalar_value(node)
    if tag == _TAG_STR:
        return ("scalar", tag, value)
    if tag == _TAG_NULL:
        return ("scalar", tag, None)
    if tag == _TAG_BOOL:
        if value.casefold() not in {"true", "false"}:
            raise _CanonicalizationError("boolean has no canonical form")
        return ("scalar", tag, value.casefold() == "true")
    if tag == _TAG_INT:
        return ("scalar", tag, _canonical_int(value))
    if tag == _TAG_FLOAT:
        return ("scalar", tag, _canonical_float(value))
    if tag == _TAG_TIMESTAMP:
        return ("scalar", tag, _canonical_timestamp(value))
    if tag == _TAG_VALUE:
        return ("scalar", tag, "=")
    raise _CanonicalizationError(f"unsupported scalar tag: {tag}")


def _valid_page_path(value: str) -> bool:
    if not value or value.startswith("/") or "\\" in value or not value.endswith(".md"):
        return False
    if any(unicodedata.category(character).startswith("C") for character in value):
        return False
    return all(part and part not in {".", ".."} for part in value.split("/"))


def _lines(text: str) -> tuple[_Line, ...]:
    lines: list[_Line] = []
    cursor = 0
    while cursor < len(text):
        if text.startswith("\r\n", cursor):
            content_end, end, line_ending = cursor, cursor + 2, "\r\n"
        elif text[cursor] in "\r\n":
            content_end, end, line_ending = cursor, cursor + 1, text[cursor]
        else:
            candidates = [
                index for index in (text.find("\r", cursor), text.find("\n", cursor)) if index >= 0
            ]
            content_end = min(candidates, default=len(text))
            if content_end == len(text):
                end, line_ending = content_end, ""
            elif text.startswith("\r\n", content_end):
                end, line_ending = content_end + 2, "\r\n"
            else:
                end, line_ending = content_end + 1, text[content_end]
        lines.append(_Line(cursor, end, text[cursor:content_end], line_ending))
        cursor = end
        if end == len(text):
            break
    return tuple(lines)


def _boundary(
    text: str, path: Path
) -> tuple[_Line, _Line, tuple[_Line, ...]] | FrontmatterDiagnostic:
    lines = _lines(text)
    first = lines[0] if lines else None
    if first is None:
        return FrontmatterDiagnostic(FRONTMATTER_INVALID, _FRONTMATTER_MISSING, path)
    if first.content != "---":
        reason = (
            _FRONTMATTER_DELIMITER_INVALID
            if first.content.startswith("---") or first.content.strip() == "---"
            else _FRONTMATTER_MISSING
        )
        return FrontmatterDiagnostic(
            FRONTMATTER_INVALID,
            reason,
            path,
            1,
            SourceSpan(first.start, first.end),
            first.content,
        )
    if not first.line_ending:
        return FrontmatterDiagnostic(
            FRONTMATTER_INVALID,
            _FRONTMATTER_DELIMITER_INVALID,
            path,
            1,
            SourceSpan(first.start, first.end),
            first.content,
        )
    for line in lines[1:]:
        if line.content == "---":
            return first, line, lines
    last = lines[-1]
    return FrontmatterDiagnostic(
        FRONTMATTER_INVALID,
        _FRONTMATTER_DELIMITER_INVALID,
        path,
        len(lines),
        SourceSpan(first.start, last.end),
        last.content,
    )


def _token_span(token: object, header: str) -> SourceSpan:
    shape = cast(_TokenShape, token)
    starts = _line_starts(header)
    return SourceSpan(
        _offset(starts, shape.start_mark.line, shape.start_mark.column, 0, header),
        _offset(starts, shape.end_mark.line, shape.end_mark.column, 0, header),
    )


def _line_starts(text: str) -> tuple[int, ...]:
    starts = [0]
    index = 0
    while index < len(text):
        if text.startswith("\r\n", index):
            starts.append(index + 2)
            index += 2
        elif text[index] in "\r\n":
            starts.append(index + 1)
            index += 1
        elif text[index] in "\u0085\u2028\u2029":
            # YAML marks treat these Unicode breaks as a new logical line even
            # though page source locations only give LF/CRLF/CR line semantics.
            starts.append(index + 1)
            index += 1
        else:
            index += 1
    return tuple(starts)


def _offset(starts: tuple[int, ...], line: int, column: int, base: int, text: str) -> int:
    if line < 0:
        line = 0
    if line >= len(starts):
        return base + len(text)
    return min(base + starts[line] + max(column, 0), base + len(text))


def _node_span(node: Node, starts: tuple[int, ...], base: int, header: str) -> SourceSpan:
    start = _node_mark(node, "start_mark")
    end = _node_mark(node, "end_mark")
    return SourceSpan(
        _offset(starts, start.line, start.column, base, header),
        _offset(starts, end.line, end.column, base, header),
    )


@dataclass
class _SourceOccurrence:
    """一个 YAML 源码位置的节点槽位。

    ruamel 的 compose 图会把 alias 指向 anchor 的同一个 ``Node``；因此不能仅用
    ``Node.start_mark`` 表示用户实际写下的 occurrence。这个表示保留每个 mapping
    key/value 与 sequence member 的原始位置，alias 自身没有 ``node``。
    """

    kind: str
    span: SourceSpan
    pairs: list[tuple[_SourceOccurrence, _SourceOccurrence]] = field(
        default_factory=lambda: cast(list[tuple[_SourceOccurrence, _SourceOccurrence]], [])
    )
    values: list[_SourceOccurrence] = field(
        default_factory=lambda: cast(list[_SourceOccurrence], [])
    )
    node: Node | None = None
    feature_spans: list[SourceSpan] = field(default_factory=lambda: cast(list[SourceSpan], []))


@dataclass
class _SourceFrame:
    occurrence: _SourceOccurrence
    pending_key: _SourceOccurrence | None = None


@dataclass(frozen=True)
class _IdentityAnalysis:
    identity: tuple[object, ...] | None
    canonical_error_node: Node | None
    has_back_edge: bool = False


@dataclass(frozen=True)
class _PairAnalysis:
    state: str
    key: _SourceOccurrence
    value: _SourceOccurrence


@dataclass
class _FieldCandidate:
    state: str = "absent"
    key: _SourceOccurrence | None = None
    value: _SourceOccurrence | None = None


def _event_span(event: object, starts: tuple[int, ...], header: str) -> SourceSpan:
    shape = cast(_TokenShape, event)
    return SourceSpan(
        _offset(starts, shape.start_mark.line, shape.start_mark.column, 0, header),
        _offset(starts, shape.end_mark.line, shape.end_mark.column, 0, header),
    )


def _source_occurrence(header: str, starts: tuple[int, ...]) -> _SourceOccurrence | None:
    """用 parser event 建立无共享边的 source-occurrence 树。"""

    # ``safe`` parser events use the same logical-line accounting as the
    # ``safe`` compose graph. Round-trip events normalize U+0085 differently,
    # which would make a later occurrence impossible to pair with its Node.
    loader = YAML(typ="safe")
    events = cast(Iterable[object], _yaml_call(loader, "parse", header))
    root: _SourceOccurrence | None = None
    stack: list[_SourceFrame] = []

    def attach(occurrence: _SourceOccurrence) -> None:
        nonlocal root
        if not stack:
            if root is not None:
                raise RuntimeError("YAML event stream contains more than one document root")
            root = occurrence
            return
        parent = stack[-1]
        if parent.occurrence.kind == "mapping":
            if parent.pending_key is None:
                parent.pending_key = occurrence
            else:
                parent.occurrence.pairs.append((parent.pending_key, occurrence))
                parent.pending_key = None
        else:
            parent.occurrence.values.append(occurrence)

    for event in events:
        if isinstance(event, MappingStartEvent):
            occurrence = _SourceOccurrence("mapping", _event_span(event, starts, header))
            attach(occurrence)
            stack.append(_SourceFrame(occurrence))
        elif isinstance(event, SequenceStartEvent):
            occurrence = _SourceOccurrence("sequence", _event_span(event, starts, header))
            attach(occurrence)
            stack.append(_SourceFrame(occurrence))
        elif isinstance(event, ScalarEvent):
            attach(_SourceOccurrence("scalar", _event_span(event, starts, header)))
        elif isinstance(event, AliasEvent):
            attach(_SourceOccurrence("alias", _event_span(event, starts, header)))
        elif isinstance(event, (MappingEndEvent, SequenceEndEvent)):
            if not stack:
                raise RuntimeError("YAML event stream closes an absent collection")
            frame = stack.pop()
            if frame.pending_key is not None:
                raise RuntimeError("YAML event stream leaves a mapping key without a value")
            closing = _event_span(event, starts, header)
            frame.occurrence.span = SourceSpan(frame.occurrence.span.start, closing.end)

    if stack:
        raise RuntimeError("YAML event stream leaves a collection open")
    return root


def _occurrence_children(occurrence: _SourceOccurrence) -> tuple[_SourceOccurrence, ...]:
    if occurrence.kind == "mapping":
        children: list[_SourceOccurrence] = []
        for key, value in occurrence.pairs:
            children.extend((key, value))
        return tuple(children)
    return tuple(occurrence.values)


def _walk_occurrences(root: _SourceOccurrence) -> tuple[_SourceOccurrence, ...]:
    """按源码顺序无递归遍历 source occurrence。"""

    ordered: list[_SourceOccurrence] = []
    stack = [root]
    seen: set[int] = set()
    while stack:
        occurrence = stack.pop()
        occurrence_id = id(occurrence)
        if occurrence_id in seen:
            continue
        seen.add(occurrence_id)
        ordered.append(occurrence)
        stack.extend(reversed(_occurrence_children(occurrence)))
    return tuple(ordered)


def _node_kind(node: Node) -> str:
    if isinstance(node, MappingNode):
        return "mapping"
    if isinstance(node, SequenceNode):
        return "sequence"
    if isinstance(node, ScalarNode):
        return "scalar"
    raise RuntimeError(f"YAML compose returned an unsupported node: {_node_tag(node)}")


def _node_sources(
    root: Node, starts: tuple[int, ...], header: str
) -> dict[tuple[str, int, int], list[Node]]:
    """以 mark 索引 compose Node，遍历本身也不依赖递归。"""

    sources: dict[tuple[str, int, int], list[Node]] = {}
    stack = [root]
    seen: set[int] = set()
    while stack:
        node = stack.pop()
        node_id = id(node)
        if node_id in seen:
            continue
        seen.add(node_id)
        span = _node_span(node, starts, 0, header)
        sources.setdefault((_node_kind(node), span.start, span.end), []).append(node)
        if isinstance(node, MappingNode):
            for key, value in reversed(_mapping_pairs(node)):
                stack.append(value)
                stack.append(key)
        elif isinstance(node, SequenceNode):
            stack.extend(reversed(_sequence_values(node)))
    return sources


def _associate_source_nodes(
    root: _SourceOccurrence, composed: Node, starts: tuple[int, ...], header: str
) -> dict[int, tuple[_SourceOccurrence, ...]]:
    """关联 normal occurrence 与 compose Node，alias 保持为独立 source 槽位。"""

    by_span = _node_sources(composed, starts, header)
    by_node: dict[int, list[_SourceOccurrence]] = {}
    for occurrence in _walk_occurrences(root):
        if occurrence.kind == "alias":
            continue
        candidates = by_span.get((occurrence.kind, occurrence.span.start, occurrence.span.end), [])
        if len(candidates) != 1:
            raise RuntimeError("YAML source occurrence cannot be matched to its compose node")
        occurrence.node = candidates[0]
        by_node.setdefault(id(occurrence.node), []).append(occurrence)
    return {node_id: tuple(occurrences) for node_id, occurrences in by_node.items()}


def _comment_tokens(value: object) -> tuple[CommentToken, ...]:
    """从 token 的嵌套 comment payload 中无递归提取全部 comment。"""

    comments: list[CommentToken] = []
    stack = [value]
    seen: set[int] = set()
    while stack:
        current = stack.pop()
        if isinstance(current, CommentToken):
            comments.append(current)
        elif isinstance(current, (list, tuple)):
            container = cast(list[object] | tuple[object, ...], current)
            current_id = id(container)
            if current_id in seen:
                continue
            seen.add(current_id)
            stack.extend(reversed(container))
    return tuple(comments)


def _collect_source_features(
    header: str, root: _SourceOccurrence, starts: tuple[int, ...]
) -> tuple[tuple[SourceSpan, ...], tuple[SourceSpan, ...]]:
    """收集 source occurrence feature 与不归属节点的 comment。

    anchor、alias、tag 由 scanner 精确定位；merge 由 compose 后的 key tag 识别。comment 是
    trivia，不占用 value occurrence，因此单独返回。
    """

    occurrences = _walk_occurrences(root)
    standalone: list[SourceSpan] = []
    direct_features: list[SourceSpan] = []
    for token in _scan_tokens(header):
        if isinstance(token, (AnchorToken, AliasToken, TagToken)):
            direct_features.append(_token_span(token, header))
        for comment in _comment_tokens(_token_comment(token)):
            standalone.append(_token_span(comment, header))

    for span in direct_features:
        candidates = [
            occurrence
            for occurrence in occurrences
            if occurrence.span.start <= span.start and span.end <= occurrence.span.end
        ]
        if not candidates:
            standalone.append(span)
            continue
        owner = min(candidates, key=lambda occurrence: occurrence.span.end - occurrence.span.start)
        owner.feature_spans.append(span)

    for occurrence in occurrences:
        if occurrence.kind != "mapping":
            continue
        for key, _value in occurrence.pairs:
            if key.node is not None and _node_tag(key.node) == "tag:yaml.org,2002:merge":
                key.feature_spans.append(key.span)

    return tuple(standalone), tuple(direct_features)


def _absolute_span(span: SourceSpan, base: int) -> SourceSpan:
    return SourceSpan(base + span.start, base + span.end)


def _earliest_feature(occurrence: _SourceOccurrence) -> SourceSpan | None:
    if not occurrence.feature_spans:
        return None
    return min(occurrence.feature_spans, key=lambda span: (span.start, span.end))


def _has_feature_in_subtree(root: _SourceOccurrence) -> bool:
    stack = [root]
    seen: set[int] = set()
    while stack:
        occurrence = stack.pop()
        occurrence_id = id(occurrence)
        if occurrence_id in seen:
            continue
        seen.add(occurrence_id)
        if occurrence.feature_spans:
            return True
        stack.extend(_occurrence_children(occurrence))
    return False


def _node_children(node: Node) -> tuple[Node, ...]:
    if isinstance(node, MappingNode):
        children: list[Node] = []
        for key, value in _mapping_pairs(node):
            children.extend((key, value))
        return tuple(children)
    if isinstance(node, SequenceNode):
        return tuple(_sequence_values(node))
    return ()


def _analyze_node_identities(root: Node) -> dict[int, _IdentityAnalysis]:
    """以 active/done worklist 计算 typed identity，显式结束共享与循环图。"""

    states: dict[int, str] = {}
    analyses: dict[int, _IdentityAnalysis] = {}
    back_edges: set[int] = set()
    stack: list[tuple[Node, bool]] = [(root, False)]
    while stack:
        node, exiting = stack.pop()
        node_id = id(node)
        if not exiting:
            state = states.get(node_id)
            if state == "done":
                continue
            if state == "active":
                continue
            states[node_id] = "active"
            stack.append((node, True))
            for child in reversed(_node_children(node)):
                child_state = states.get(id(child))
                if child_state == "active":
                    back_edges.add(node_id)
                elif child_state != "done":
                    stack.append((child, False))
            continue

        if node_id in back_edges:
            analyses[node_id] = _IdentityAnalysis(None, None, True)
        elif isinstance(node, ScalarNode):
            try:
                analyses[node_id] = _IdentityAnalysis(_canonical_scalar_identity(node), None)
            except _CanonicalizationError:
                analyses[node_id] = _IdentityAnalysis(None, node)
        elif isinstance(node, SequenceNode):
            children = _node_children(node)
            child_analyses = [analyses[id(child)] for child in children]
            canonical_error = next(
                (
                    analysis.canonical_error_node
                    for analysis in child_analyses
                    if analysis.canonical_error_node is not None
                ),
                None,
            )
            has_back_edge = any(analysis.has_back_edge for analysis in child_analyses)
            if (
                canonical_error is not None
                or has_back_edge
                or any(analysis.identity is None for analysis in child_analyses)
            ):
                analyses[node_id] = _IdentityAnalysis(None, canonical_error, has_back_edge)
            else:
                analyses[node_id] = _IdentityAnalysis(
                    (
                        "sequence",
                        _node_tag(node),
                        tuple(
                            cast(tuple[object, ...], analysis.identity)
                            for analysis in child_analyses
                        ),
                    ),
                    None,
                )
        elif isinstance(node, MappingNode):
            pairs = _mapping_pairs(node)
            pair_analyses = [(analyses[id(key)], analyses[id(value)]) for key, value in pairs]
            all_analyses = [analysis for pair in pair_analyses for analysis in pair]
            canonical_error = next(
                (
                    analysis.canonical_error_node
                    for analysis in all_analyses
                    if analysis.canonical_error_node is not None
                ),
                None,
            )
            has_back_edge = any(analysis.has_back_edge for analysis in all_analyses)
            if (
                canonical_error is not None
                or has_back_edge
                or any(analysis.identity is None for analysis in all_analyses)
            ):
                analyses[node_id] = _IdentityAnalysis(None, canonical_error, has_back_edge)
            else:
                seen: set[tuple[object, ...]] = set()
                identity_pairs: list[tuple[tuple[object, ...], tuple[object, ...]]] = []
                duplicate = False
                for key_analysis, value_analysis in pair_analyses:
                    key_identity = cast(tuple[object, ...], key_analysis.identity)
                    value_identity = cast(tuple[object, ...], value_analysis.identity)
                    if key_identity in seen:
                        duplicate = True
                        break
                    seen.add(key_identity)
                    identity_pairs.append((key_identity, value_identity))
                analyses[node_id] = (
                    _IdentityAnalysis(None, None)
                    if duplicate
                    else _IdentityAnalysis(
                        ("mapping", _node_tag(node), frozenset(identity_pairs)), None
                    )
                )
        else:
            analyses[node_id] = _IdentityAnalysis(None, node)
        states[node_id] = "done"
    return analyses


def _append_diagnostic(
    diagnostics: list[FrontmatterDiagnostic],
    seen: set[tuple[int | None, int | None, str, str]],
    code: str,
    reason: str,
    path: Path,
    source: str,
    span: SourceSpan | None,
    *,
    key_name: str | None = None,
) -> None:
    identity = (
        None if span is None else span.start,
        None if span is None else span.end,
        code,
        reason,
    )
    if identity in seen:
        return
    seen.add(identity)
    diagnostics.append(_diagnostic(code, reason, path, source, span, key_name=key_name))


def _classify_mapping_pairs(
    root: _SourceOccurrence,
    analyses: dict[int, _IdentityAnalysis],
    node_sources: dict[int, tuple[_SourceOccurrence, ...]],
    base: int,
    path: Path,
    source: str,
    diagnostics: list[FrontmatterDiagnostic],
    seen_diagnostics: set[tuple[int | None, int | None, str, str]],
) -> dict[int, _PairAnalysis]:
    """按每个 mapping 的源码 pair 顺序登记 feature/canonical/duplicate owner。"""

    pairs: dict[int, _PairAnalysis] = {}
    for mapping in _walk_occurrences(root):
        if mapping.kind != "mapping":
            continue
        seen_identities: set[tuple[object, ...]] = set()
        for key, value in mapping.pairs:
            if _has_feature_in_subtree(key):
                pairs[id(key)] = _PairAnalysis("blocked", key, value)
                continue
            if key.node is None:
                raise RuntimeError("YAML key occurrence has no compose node or source feature")
            analysis = analyses[id(key.node)]
            if analysis.canonical_error_node is not None:
                error_sources = node_sources.get(id(analysis.canonical_error_node), ())
                if not error_sources:
                    raise RuntimeError("YAML canonicalization error has no source occurrence")
                error_occurrence = min(
                    error_sources,
                    key=lambda occurrence: (occurrence.span.start, occurrence.span.end),
                )
                _append_diagnostic(
                    diagnostics,
                    seen_diagnostics,
                    FRONTMATTER_INVALID,
                    _FRONTMATTER_YAML_INVALID,
                    path,
                    source,
                    _absolute_span(error_occurrence.span, base),
                )
                pairs[id(key)] = _PairAnalysis("blocked", key, value)
                continue
            if analysis.has_back_edge:
                raise RuntimeError("YAML compose graph contains an unowned cycle")
            if analysis.identity is None:
                # A nested duplicate makes a complex key unusable. Its direct duplicate
                # owner is registered while that nested mapping is processed below.
                pairs[id(key)] = _PairAnalysis("blocked", key, value)
                continue
            if analysis.identity in seen_identities:
                _append_diagnostic(
                    diagnostics,
                    seen_diagnostics,
                    FRONTMATTER_INVALID,
                    _FRONTMATTER_DUPLICATE_KEY,
                    path,
                    source,
                    _absolute_span(key.span, base),
                    key_name=_simple_scalar_name(key),
                )
                pairs[id(key)] = _PairAnalysis("duplicate", key, value)
                continue
            seen_identities.add(analysis.identity)
            pairs[id(key)] = _PairAnalysis("usable", key, value)
    return pairs


def _simple_scalar_name(occurrence: _SourceOccurrence) -> str | None:
    node = occurrence.node
    if not isinstance(node, ScalarNode) or _node_tag(node) != _TAG_STR:
        return None
    return _scalar_value(node)


def _apparent_known_name(occurrence: _SourceOccurrence) -> str | None:
    node = occurrence.node
    if not isinstance(node, ScalarNode):
        return None
    value = _scalar_value(node)
    if value in {"status", "note", RelationCode.PARENT.value, "relations"}:
        return value
    return None


def _authenticated_known_name(occurrence: _SourceOccurrence) -> str | None:
    """返回已认证为字符串的顶层字段名，而非仅原文看似字段名。"""

    value = _simple_scalar_name(occurrence)
    if value in {"status", "note", RelationCode.PARENT.value, "relations"}:
        return value
    return None


def _is_descendant(root: _SourceOccurrence, target: _SourceOccurrence) -> bool:
    stack = [root]
    seen: set[int] = set()
    while stack:
        occurrence = stack.pop()
        occurrence_id = id(occurrence)
        if occurrence_id in seen:
            continue
        seen.add(occurrence_id)
        if occurrence is target:
            return True
        stack.extend(_occurrence_children(occurrence))
    return False


def _member_has_structural_problem(
    member: _SourceOccurrence, pair_analyses: dict[int, _PairAnalysis]
) -> bool:
    if member.feature_spans:
        return True
    return any(
        analysis.state != "usable" and _is_descendant(member, analysis.key)
        for analysis in pair_analyses.values()
    )


def _source_line(text: str, position: int) -> int:
    """按原始文本计算一基行号，兼容 LF、CRLF 和 CR。"""

    position = max(0, min(position, len(text)))
    line = 1
    index = 0
    while index < position:
        if text.startswith("\r\n", index):
            line += 1
            index += 2
        elif text[index] in "\r\n":
            line += 1
            index += 1
        else:
            index += 1
    return line


def _raw_line(text: str, position: int) -> str:
    position = max(0, min(position, len(text)))
    line_start = position
    while line_start > 0 and text[line_start - 1] not in "\r\n":
        line_start -= 1
    line_end = position
    while line_end < len(text) and text[line_end] not in "\r\n":
        line_end += 1
    return text[line_start:line_end]


def _raw_contains(text: str, span: SourceSpan, characters: str) -> bool:
    return any(character in characters for character in text[span.start : span.end])


def _diagnostic(
    code: str,
    reason: str,
    path: Path,
    source: str,
    span: SourceSpan | None = None,
    *,
    key_name: str | None = None,
    line: int | None = None,
) -> FrontmatterDiagnostic:
    actual_line = line if line is not None else None
    raw_line = None
    if span is not None:
        actual_line = _source_line(source, span.start)
        raw_line = _raw_line(source, span.start)
    return FrontmatterDiagnostic(
        code=code,
        reason=reason,
        path=path,
        line=actual_line,
        span=span,
        raw_line=raw_line,
        key_name=key_name,
    )


def _frontmatter_result(
    metadata: FrontmatterMetadata | None,
    diagnostics: list[FrontmatterDiagnostic],
    body_start: int,
    header_span: SourceSpan,
) -> FrontmatterParseResult:
    return FrontmatterParseResult(metadata, tuple(diagnostics), body_start, header_span)


def parse_frontmatter(text: str, page_path: Path | str) -> FrontmatterParseResult:
    """读取并认证规范分隔线包围的 YAML 页面头部。"""

    path_text = page_path.as_posix() if isinstance(page_path, Path) else page_path
    path = Path(path_text)
    boundary = _boundary(text, path)
    if isinstance(boundary, FrontmatterDiagnostic):
        return FrontmatterParseResult(None, (boundary,), None)

    opening, closing, _lines_value = boundary
    header = text[opening.end : closing.start]
    header_span = SourceSpan(opening.end, closing.start)
    starts = _line_starts(header)
    try:
        source_root = _source_occurrence(header, starts)
        composed = _compose_node(header)
    except YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        span = None
        if mark is not None:
            point = _offset(starts, int(mark.line), int(mark.column), opening.end, header)
            span = SourceSpan(point, point)
        issue = _diagnostic(FRONTMATTER_INVALID, _FRONTMATTER_YAML_INVALID, path, text, span)
        return _frontmatter_result(None, [issue], closing.end, header_span)

    if composed is None:
        issue = _diagnostic(
            FRONTMATTER_INVALID,
            _FRONTMATTER_NOT_MAPPING,
            path,
            text,
            None if not header else header_span,
        )
        return _frontmatter_result(None, [issue], closing.end, header_span)
    if not isinstance(composed, MappingNode):
        issue = _diagnostic(FRONTMATTER_INVALID, _FRONTMATTER_NOT_MAPPING, path, text, header_span)
        return _frontmatter_result(None, [issue], closing.end, header_span)
    if source_root is None or source_root.kind != "mapping":
        raise RuntimeError("YAML compose and parser events disagree about the document root")

    node_sources = _associate_source_nodes(source_root, composed, starts, header)
    standalone_features, _direct_features = _collect_source_features(header, source_root, starts)
    diagnostics: list[FrontmatterDiagnostic] = []
    seen_diagnostics: set[tuple[int | None, int | None, str, str]] = set()

    for occurrence in _walk_occurrences(source_root):
        feature_span = _earliest_feature(occurrence)
        if feature_span is not None:
            _append_diagnostic(
                diagnostics,
                seen_diagnostics,
                FRONTMATTER_INVALID,
                _FRONTMATTER_UNSUPPORTED_FEATURE,
                path,
                text,
                _absolute_span(feature_span, opening.end),
            )
    for feature_span in standalone_features:
        _append_diagnostic(
            diagnostics,
            seen_diagnostics,
            FRONTMATTER_INVALID,
            _FRONTMATTER_UNSUPPORTED_FEATURE,
            path,
            text,
            _absolute_span(feature_span, opening.end),
        )

    node_analyses = _analyze_node_identities(composed)
    pair_analyses = _classify_mapping_pairs(
        source_root,
        node_analyses,
        node_sources,
        opening.end,
        path,
        text,
        diagnostics,
        seen_diagnostics,
    )

    fields = {
        "status": _FieldCandidate(),
        "note": _FieldCandidate(),
        RelationCode.PARENT.value: _FieldCandidate(),
        "relations": _FieldCandidate(),
    }
    for key, value in source_root.pairs:
        pair = pair_analyses[id(key)]
        if pair.state == "usable":
            known_name = _authenticated_known_name(key)
            if known_name is None:
                _append_diagnostic(
                    diagnostics,
                    seen_diagnostics,
                    FRONTMATTER_INVALID,
                    _FRONTMATTER_UNKNOWN_KEY,
                    path,
                    text,
                    _absolute_span(key.span, opening.end),
                    key_name=_simple_scalar_name(key),
                )
            else:
                fields[known_name] = _FieldCandidate(
                    "value_blocked" if _earliest_feature(value) is not None else "usable",
                    key,
                    value,
                )
        elif pair.state == "duplicate":
            known_name = _authenticated_known_name(key)
            if known_name is not None:
                fields[known_name] = _FieldCandidate("ambiguous", key, value)
        else:
            apparent_name = _apparent_known_name(key)
            if apparent_name is not None and fields[apparent_name].state == "absent":
                # A structural failure can only establish that the field is not
                # safely absent. It cannot erase an earlier authenticated
                # candidate; a real duplicate above remains the sole owner of
                # that field's ambiguous state.
                fields[apparent_name] = _FieldCandidate("ambiguous", key, value)

    status: StatusCode | None = None
    status_span: SourceSpan | None = None
    status_field = fields["status"]
    if status_field.state == "absent":
        _append_diagnostic(
            diagnostics,
            seen_diagnostics,
            STATUS_INVALID,
            _STATUS_MISSING,
            path,
            text,
            None,
        )
    elif status_field.state == "usable":
        assert status_field.value is not None and status_field.value.node is not None
        status_node = status_field.value.node
        status_span = _absolute_span(status_field.value.span, opening.end)
        status_value = (
            _scalar_value(status_node)
            if isinstance(status_node, ScalarNode) and _node_tag(status_node) == _TAG_STR
            else None
        )
        if status_value is None:
            _append_diagnostic(
                diagnostics,
                seen_diagnostics,
                STATUS_INVALID,
                _STATUS_INVALID_TYPE,
                path,
                text,
                status_span,
            )
        else:
            try:
                status = StatusCode(status_value)
            except ValueError:
                _append_diagnostic(
                    diagnostics,
                    seen_diagnostics,
                    STATUS_INVALID,
                    _STATUS_INVALID_VALUE,
                    path,
                    text,
                    status_span,
                )

    note: str | None = None
    note_field = fields["note"]
    if note_field.state == "usable":
        assert note_field.value is not None and note_field.value.node is not None
        note_node = note_field.value.node
        note_span = _absolute_span(note_field.value.span, opening.end)
        note_value = (
            _scalar_value(note_node)
            if isinstance(note_node, ScalarNode) and _node_tag(note_node) == _TAG_STR
            else None
        )
        if isinstance(note_node, ScalarNode) and (
            _node_tag(note_node) == _TAG_NULL or note_value == ""
        ):
            _append_diagnostic(
                diagnostics,
                seen_diagnostics,
                NOTE_INVALID,
                _NOTE_EMPTY,
                path,
                text,
                note_span,
            )
        elif note_value is None:
            _append_diagnostic(
                diagnostics,
                seen_diagnostics,
                FRONTMATTER_INVALID,
                _FRONTMATTER_INVALID_VALUE_TYPE,
                path,
                text,
                note_span,
            )
        elif _raw_contains(text, note_span, _YAML_NORMALIZED_PATH_CHARACTERS) or any(
            character in _YAML_LINE_BREAKS for character in note_value
        ):
            _append_diagnostic(
                diagnostics,
                seen_diagnostics,
                NOTE_INVALID,
                _NOTE_MULTILINE,
                path,
                text,
                note_span,
            )
        elif any(unicodedata.category(character).startswith("C") for character in note_value):
            _append_diagnostic(
                diagnostics,
                seen_diagnostics,
                NOTE_INVALID,
                _NOTE_CONTROL_CHARACTER,
                path,
                text,
                note_span,
            )
        else:
            note = note_value

    parent: Path | None = None
    parent_span: SourceSpan | None = None
    parent_line: int | None = None
    parent_field = fields[RelationCode.PARENT.value]
    is_root = path_text == "index.md"
    if is_root and parent_field.state in {"usable", "value_blocked"}:
        assert parent_field.key is not None
        _append_diagnostic(
            diagnostics,
            seen_diagnostics,
            PARENT_INVALID,
            _PARENT_UNEXPECTED,
            path,
            text,
            _absolute_span(parent_field.key.span, opening.end),
        )
    elif not is_root and parent_field.state == "absent":
        _append_diagnostic(
            diagnostics,
            seen_diagnostics,
            PARENT_INVALID,
            _PARENT_MISSING,
            path,
            text,
            None,
        )
    elif parent_field.state == "usable":
        assert parent_field.value is not None and parent_field.value.node is not None
        parent_node = parent_field.value.node
        parent_span = _absolute_span(parent_field.value.span, opening.end)
        parent_value = (
            _scalar_value(parent_node)
            if isinstance(parent_node, ScalarNode) and _node_tag(parent_node) == _TAG_STR
            else None
        )
        if parent_value is None:
            _append_diagnostic(
                diagnostics,
                seen_diagnostics,
                FRONTMATTER_INVALID,
                _FRONTMATTER_INVALID_VALUE_TYPE,
                path,
                text,
                parent_span,
            )
        elif _raw_contains(
            text, parent_span, _YAML_NORMALIZED_PATH_CHARACTERS
        ) or not _valid_page_path(parent_value):
            _append_diagnostic(
                diagnostics,
                seen_diagnostics,
                PARENT_INVALID,
                _PARENT_INVALID_PATH,
                path,
                text,
                parent_span,
            )
        else:
            parent = Path(parent_value)
            parent_line = _source_line(text, parent_span.start)

    relation_values: list[FrontmatterRelation] = []
    relations_field = fields["relations"]
    if relations_field.state == "usable":
        assert relations_field.value is not None and relations_field.value.node is not None
        relation_occurrence = relations_field.value
        relation_node = relation_occurrence.node
        relation_span = _absolute_span(relation_occurrence.span, opening.end)
        if not isinstance(relation_node, SequenceNode):
            _append_diagnostic(
                diagnostics,
                seen_diagnostics,
                FRONTMATTER_INVALID,
                _FRONTMATTER_INVALID_VALUE_TYPE,
                path,
                text,
                relation_span,
            )
        elif not relation_occurrence.values:
            _append_diagnostic(
                diagnostics,
                seen_diagnostics,
                RELATION_DECLARATION_INVALID,
                _RELATION_CONTAINER_EMPTY,
                path,
                text,
                relation_span,
            )
        else:
            for member in relation_occurrence.values:
                member_span = _absolute_span(member.span, opening.end)
                if _member_has_structural_problem(member, pair_analyses):
                    continue
                if member.kind != "mapping" or not isinstance(member.node, MappingNode):
                    _append_diagnostic(
                        diagnostics,
                        seen_diagnostics,
                        RELATION_DECLARATION_INVALID,
                        _RELATION_MEMBER_SHAPE,
                        path,
                        text,
                        member_span,
                    )
                    continue
                member_fields: dict[str, _SourceOccurrence] = {}
                if len(member.pairs) != 2:
                    member_fields = {}
                else:
                    for key, value in member.pairs:
                        if pair_analyses[id(key)].state != "usable":
                            member_fields = {}
                            break
                        key_name = _simple_scalar_name(key)
                        if key_name is None or key_name in member_fields:
                            member_fields = {}
                            break
                        member_fields[key_name] = value
                if set(member_fields) != {"type", "target"}:
                    _append_diagnostic(
                        diagnostics,
                        seen_diagnostics,
                        RELATION_DECLARATION_INVALID,
                        _RELATION_MEMBER_SHAPE,
                        path,
                        text,
                        member_span,
                    )
                    continue

                type_occurrence = member_fields["type"]
                target_occurrence = member_fields["target"]
                type_span = _absolute_span(type_occurrence.span, opening.end)
                target_span = _absolute_span(target_occurrence.span, opening.end)
                type_code: RelationCode | None = None
                raw_type: str | None = None
                if _earliest_feature(type_occurrence) is None:
                    assert type_occurrence.node is not None
                    type_node = type_occurrence.node
                    raw_type = (
                        _scalar_value(type_node)
                        if isinstance(type_node, ScalarNode) and _node_tag(type_node) == _TAG_STR
                        else None
                    )
                    if raw_type is not None:
                        try:
                            candidate_type = RelationCode(raw_type)
                            if candidate_type is not RelationCode.PARENT:
                                type_code = candidate_type
                        except ValueError:
                            pass
                    if type_code is None:
                        _append_diagnostic(
                            diagnostics,
                            seen_diagnostics,
                            RELATION_DECLARATION_INVALID,
                            _RELATION_TYPE_UNKNOWN,
                            path,
                            text,
                            type_span,
                        )

                raw_target: str | None = None
                target_ok = False
                if _earliest_feature(target_occurrence) is None:
                    assert target_occurrence.node is not None
                    target_node = target_occurrence.node
                    raw_target = (
                        _scalar_value(target_node)
                        if isinstance(target_node, ScalarNode)
                        and _node_tag(target_node) == _TAG_STR
                        else None
                    )
                    target_ok = (
                        raw_target is not None
                        and not _raw_contains(text, target_span, _YAML_NORMALIZED_PATH_CHARACTERS)
                        and _valid_page_path(raw_target)
                    )
                    if not target_ok:
                        _append_diagnostic(
                            diagnostics,
                            seen_diagnostics,
                            RELATION_DECLARATION_INVALID,
                            _RELATION_TARGET_PATH_INVALID,
                            path,
                            text,
                            target_span,
                        )
                if type_code is not None and target_ok and raw_target is not None:
                    relation_values.append(
                        FrontmatterRelation(
                            code=type_code,
                            target=Path(raw_target),
                            span=member_span,
                            type_span=type_span,
                            target_span=target_span,
                            line=_source_line(text, type_span.start),
                        )
                    )

    diagnostics.sort(
        key=lambda diagnostic: (
            diagnostic.span is None,
            0 if diagnostic.span is None else diagnostic.span.start,
            0 if diagnostic.span is None else diagnostic.span.end,
            diagnostic.code,
            diagnostic.reason,
        )
    )
    if diagnostics or status is None or (not is_root and parent is None):
        return _frontmatter_result(None, diagnostics, closing.end, header_span)
    metadata = FrontmatterMetadata(
        status=StatusValue(status, note),
        parent=parent,
        relations=tuple(relation_values),
        status_span=status_span,
        parent_span=parent_span,
        parent_line=parent_line,
    )
    return _frontmatter_result(metadata, diagnostics, closing.end, header_span)


def _validate_metadata(metadata: FrontmatterMetadata, page_path: Path | str) -> str:
    if not isinstance(cast(object, metadata.status.code), StatusCode):
        raise ValueError("status must be a StatusCode")
    if metadata.note is not None:
        if not isinstance(cast(object, metadata.note), str) or not metadata.note:
            raise ValueError("note must be a non-empty string")
        if any(character in "\r\n\u0085\u2028\u2029" for character in metadata.note):
            raise ValueError("note must be single-line")
        if any(unicodedata.category(character).startswith("C") for character in metadata.note):
            raise ValueError("note must not contain control characters")
    path_text = page_path.as_posix() if isinstance(page_path, Path) else page_path
    if path_text != "index.md" and (
        metadata.parent is None or not _valid_page_path(metadata.parent.as_posix())
    ):
        raise ValueError("non-root metadata requires a valid parent")
    for relation in metadata.relations:
        if not isinstance(cast(object, relation.code), RelationCode) or (
            relation.code is RelationCode.PARENT
        ):
            raise ValueError("relation type is not mutable")
        if not _valid_page_path(relation.target.as_posix()):
            raise ValueError("relation target must be a valid page path")
    return path_text


def _yaml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _serialize_frontmatter(
    metadata: FrontmatterMetadata,
    page_path: Path | str,
    body: str = "",
    *,
    line_ending: str,
) -> str:
    """按稳定字段顺序生成 YAML frontmatter，并原样追加正文。"""

    path_text = _validate_metadata(metadata, page_path)
    if line_ending not in {"\n", "\r\n", "\r"}:
        raise ValueError("line_ending must be a supported line ending")
    lines = ["---", f"status: {metadata.status.code.value}"]
    if metadata.note is not None:
        lines.append(f"note: {_yaml_string(metadata.note)}")
    if path_text != "index.md":
        assert metadata.parent is not None
        lines.append(f"parent: {_yaml_string(metadata.parent.as_posix())}")
    if metadata.relations:
        lines.append("relations:")
        for relation in metadata.relations:
            lines.append(f"  - type: {relation.code.value}")
            lines.append(f"    target: {_yaml_string(relation.target.as_posix())}")
    lines.append("---")
    return line_ending.join(lines) + line_ending + body


def serialize_frontmatter(
    metadata: FrontmatterMetadata,
    page_path: Path | str,
    body: str = "",
) -> str:
    """用 LF 按稳定字段顺序生成 YAML frontmatter，并原样追加正文。"""

    return _serialize_frontmatter(metadata, page_path, body, line_ending="\n")


def rewrite_frontmatter(
    text: str,
    page_path: Path | str,
    metadata: FrontmatterMetadata,
) -> str:
    """只替换已有头部，结束分隔线之后的正文保持逐字不变。"""

    parsed = parse_frontmatter(text, page_path)
    if not parsed.is_valid or parsed.body_start is None:
        raise ValueError("cannot rewrite invalid frontmatter")
    lines = _lines(text)
    if not lines:
        raise ValueError("cannot rewrite missing frontmatter")
    body = text[parsed.body_start :]
    return _serialize_frontmatter(
        metadata,
        page_path,
        body,
        line_ending=lines[0].line_ending or "\n",
    )
