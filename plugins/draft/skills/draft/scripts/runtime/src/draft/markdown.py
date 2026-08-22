from __future__ import annotations

import re
from bisect import bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import unquote

from markdown_it import MarkdownIt
from markdown_it.token import Token

if TYPE_CHECKING:
    from draft.graph import DraftGraph

from draft.diagnostic_codes import (
    CheckCode,
    InvalidRelationReason,
    InvalidStatusReason,
    ParseErrorReason,
)
from draft.errors import DraftError
from draft.models import (
    MarkdownLink,
    PageDocument,
    Relation,
    SourceSpan,
    StatusValue,
)
from draft.vocabulary import RelationCode, relation_code, relation_label, status_code

COMMONMARK = MarkdownIt("commonmark")

_STATUS_LABEL = "状态"
_KNOWN_LABELS = frozenset(relation_label(code) for code in RelationCode) | {_STATUS_LABEL}

_ESCAPED_PUNCT = re.compile(r"\\([!\"#$%&'()*+,\-./:;<=>?@\[\\\]^_`{|}~])")
_FENCE_OPEN = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_QUOTE_PREFIX = re.compile(r"^ {0,3}> ?")
_DEFINITION = re.compile(r"^ {0,3}(?:> ?)*\[((?:\\.|[^\]])+)\]:[ \t]*")
_AUTOLINK = re.compile(r"<([A-Za-z][A-Za-z0-9+.-]{1,31}:[^ <>\n]*)>")
_EMAIL_AUTOLINK = re.compile(
    r"<([A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*)>"
)


@dataclass(frozen=True)
class _Candidate:
    start: int
    end: int
    dest_start: int
    dest_end: int
    destination: str
    raw_label: str
    is_image: bool
    reference_label: str | None


@dataclass(frozen=True)
class _MachineLine:
    label: str | None
    value: str
    line: int
    content_start: int
    content_end: int


@dataclass(frozen=True)
class _Definition:
    destination: str
    dest_start: int
    dest_end: int


def _line_starts(text: str) -> list[int]:
    starts = [0]
    for match in re.finditer("\n", text):
        starts.append(match.end())
    return starts


def _line_of(starts: Sequence[int], offset: int) -> int:
    return bisect_right(starts, offset)


def _unescape(value: str) -> str:
    return _ESCAPED_PUNCT.sub(r"\1", value)


def _normalize_reference(label: str) -> str:
    return re.sub(r"\s+", " ", _unescape(label).strip()).lower()


def _fenced_line_ranges(lines: Sequence[str]) -> list[tuple[int, int]]:
    """Return half-open line-index ranges covered by fenced code blocks."""
    ranges: list[tuple[int, int]] = []
    index = 0
    while index < len(lines):
        opener = _FENCE_OPEN.match(lines[index].rstrip("\r\n"))
        if opener is None:
            index += 1
            continue
        fence = opener.group(1)
        char = fence[0]
        length = len(fence)
        closer = re.compile(rf"^ {{0,3}}{re.escape(char)}{{{length},}}[ \t]*$")
        cursor = index + 1
        while cursor < len(lines) and closer.match(lines[cursor].rstrip("\r\n")) is None:
            cursor += 1
        end = cursor + 1 if cursor < len(lines) else len(lines)
        ranges.append((index, end))
        index = end
    return ranges


def _parse_destination(text: str, pos: int) -> tuple[str, int, int, int] | None:
    """Parse a CommonMark link destination at pos.

    Returns (destination, dest_start, dest_end, next_pos) or None.
    The span excludes angle brackets when present.
    """
    if pos >= len(text):
        return None
    if text[pos] == "<":
        cursor = pos + 1
        while cursor < len(text):
            char = text[cursor]
            if char == "\\":
                cursor += 2
                continue
            if char == ">":
                return text[pos + 1 : cursor], pos + 1, cursor, cursor + 1
            if char == "\n" or char == "<":
                return None
            cursor += 1
        return None
    cursor = pos
    depth = 0
    while cursor < len(text):
        char = text[cursor]
        if char in " \t\r\n" or ord(char) < 0x20:
            break
        if char == "\\":
            cursor += 2
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            if depth == 0:
                break
            depth -= 1
        cursor += 1
    if depth != 0:
        return None
    return text[pos:cursor], pos, cursor, cursor


def _skip_whitespace(text: str, pos: int) -> int:
    while pos < len(text) and text[pos] in " \t\r\n":
        pos += 1
    return pos


def _skip_title(text: str, pos: int) -> int | None:
    if pos >= len(text) or text[pos] not in "\"'(":
        return pos
    closer = ")" if text[pos] == "(" else text[pos]
    cursor = pos + 1
    while cursor < len(text):
        char = text[cursor]
        if char == "\\":
            cursor += 2
            continue
        if char == closer:
            return cursor + 1
        cursor += 1
    return None


def _scan_definitions(
    lines: Sequence[str], starts: Sequence[int], fenced: Sequence[tuple[int, int]]
) -> dict[str, _Definition]:
    definitions: dict[str, _Definition] = {}
    fenced_lines: set[int] = set()
    for begin, end in fenced:
        fenced_lines.update(range(begin, end))
    for index, raw in enumerate(lines):
        if index in fenced_lines:
            continue
        line = raw.rstrip("\r\n")
        match = _DEFINITION.match(line)
        if match is None:
            continue
        normalized = _normalize_reference(match.group(1))
        if normalized in definitions:
            continue
        base = starts[index] + match.end()
        parsed = _parse_destination(lines[index], match.end())
        if parsed is None:
            continue
        destination, dest_start, dest_end, _ = parsed
        definitions[normalized] = _Definition(
            destination=destination,
            dest_start=base + dest_start - match.end(),
            dest_end=base + dest_end - match.end(),
        )
    return definitions


def _try_inline_link(text: str, pos: int, is_image: bool) -> tuple[_Candidate, int] | None:
    """Try to parse [label](destination "title") starting at the opening bracket."""
    label_start = pos + 1
    cursor = label_start
    depth = 1
    while cursor < len(text) and depth > 0:
        char = text[cursor]
        if char == "\\":
            cursor += 2
            continue
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
        cursor += 1
    if depth != 0:
        return None
    label_end = cursor - 1
    raw_label = text[label_start:label_end]
    if cursor >= len(text) or text[cursor] != "(":
        return None
    cursor = _skip_whitespace(text, cursor + 1)
    parsed = _parse_destination(text, cursor)
    if parsed is None:
        return None
    destination, dest_start, dest_end, cursor = parsed
    cursor = _skip_whitespace(text, cursor)
    titled = _skip_title(text, cursor)
    if titled is None:
        return None
    cursor = _skip_whitespace(text, titled)
    if cursor >= len(text) or text[cursor] != ")":
        return None
    whole_start = pos - 1 if is_image else pos
    candidate = _Candidate(
        start=whole_start,
        end=cursor + 1,
        dest_start=dest_start,
        dest_end=dest_end,
        destination=destination,
        raw_label=raw_label,
        is_image=is_image,
        reference_label=None,
    )
    return candidate, cursor + 1


def _try_reference_link(
    text: str,
    pos: int,
    is_image: bool,
    label_end: int,
    raw_label: str,
    definitions: Mapping[str, _Definition],
) -> tuple[_Candidate, int] | None:
    whole_start = pos - 1 if is_image else pos
    cursor = label_end + 1
    reference_raw = raw_label
    if cursor < len(text) and text[cursor] == "[":
        closing = text.find("]", cursor + 1)
        if closing == -1:
            return None
        explicit = text[cursor + 1 : closing]
        if explicit:
            reference_raw = explicit
        cursor = closing + 1
    normalized = _normalize_reference(reference_raw)
    definition = definitions.get(normalized)
    if definition is None:
        return None
    candidate = _Candidate(
        start=whole_start,
        end=cursor,
        dest_start=definition.dest_start,
        dest_end=definition.dest_end,
        destination=definition.destination,
        raw_label=raw_label,
        is_image=is_image,
        reference_label=reference_raw,
    )
    return candidate, cursor


def _find_label_end(text: str, pos: int) -> int | None:
    cursor = pos + 1
    depth = 1
    while cursor < len(text) and depth > 0:
        char = text[cursor]
        if char == "\\":
            cursor += 2
            continue
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
        cursor += 1
    return cursor - 1 if depth == 0 else None


def _scan_candidates(
    text: str, starts: Sequence[int], definitions: Mapping[str, _Definition]
) -> list[_Candidate]:
    lines = text.split("\n")
    fenced = _fenced_line_ranges(lines)
    fenced_spans = [
        (starts[begin], (starts[end] if end < len(starts) else len(text))) for begin, end in fenced
    ]
    candidates: list[_Candidate] = []
    cursor = 0
    length = len(text)

    def inside_fence(position: int) -> bool:
        return any(begin <= position < end for begin, end in fenced_spans)

    while cursor < length:
        char = text[cursor]
        if inside_fence(cursor):
            cursor += 1
            continue
        if char == "\\":
            cursor += 2
            continue
        if char == "`":
            run_end = cursor
            while run_end < length and text[run_end] == "`":
                run_end += 1
            run = run_end - cursor
            closing = text.find("`" * run, run_end)
            cursor = closing + run if closing != -1 else run_end
            continue
        if char == "<":
            autolink = _AUTOLINK.match(text, cursor)
            email = _EMAIL_AUTOLINK.match(text, cursor) if autolink is None else None
            match = autolink or email
            if match is not None:
                inner = match.group(1)
                destination = inner if autolink is not None else f"mailto:{inner}"
                candidates.append(
                    _Candidate(
                        start=cursor,
                        end=match.end(),
                        dest_start=cursor + 1,
                        dest_end=cursor + 1 + len(inner),
                        destination=destination,
                        raw_label=inner,
                        is_image=False,
                        reference_label=None,
                    )
                )
                cursor = match.end()
                continue
            cursor += 1
            continue
        is_image = char == "!" and cursor + 1 < length and text[cursor + 1] == "["
        bracket = cursor + 1 if is_image else cursor
        if char == "[" or is_image:
            label_end = _find_label_end(text, bracket)
            if label_end is not None:
                raw_label = text[bracket + 1 : label_end]
                attempt = _try_inline_link(text, bracket, is_image)
                if attempt is None:
                    attempt = _try_reference_link(
                        text, bracket, is_image, label_end, raw_label, definitions
                    )
                if attempt is not None:
                    candidate, next_pos = attempt
                    candidates.append(candidate)
                    cursor = next_pos
                    continue
        cursor += 1
    return candidates


@dataclass(frozen=True)
class _TokenLink:
    is_image: bool
    destination: str
    label: str


def _token_links(tokens: Sequence[Token]) -> list[_TokenLink]:
    links: list[_TokenLink] = []
    for token in tokens:
        if token.type != "inline" or not token.children:
            continue
        children = token.children
        index = 0
        while index < len(children):
            child = children[index]
            if child.type == "link_open":
                href = child.attrGet("href")
                if not isinstance(href, str):
                    raise DraftError(
                        CheckCode.PAGE_PARSE_ERROR.value,
                        details=(ParseErrorReason.LINK_DESTINATION_MISSING.value,),
                    )
                depth = 1
                label_parts: list[str] = []
                index += 1
                while index < len(children) and depth > 0:
                    inner = children[index]
                    if inner.type == "link_open":
                        depth += 1
                    elif inner.type == "link_close":
                        depth -= 1
                    elif inner.type in ("text", "code_inline"):
                        label_parts.append(inner.content)
                    index += 1
                links.append(_TokenLink(False, href, "".join(label_parts)))
            elif child.type == "image":
                source = child.attrGet("src")
                if not isinstance(source, str):
                    raise DraftError(
                        CheckCode.PAGE_PARSE_ERROR.value,
                        details=(ParseErrorReason.IMAGE_DESTINATION_MISSING.value,),
                    )
                label = "".join(
                    part.content
                    for part in (child.children or [])
                    if part.type in ("text", "code_inline")
                )
                links.append(_TokenLink(True, source, label))
                index += 1
            else:
                index += 1
    return links


def _labels_match(raw: str, rendered: str) -> bool:
    if raw == rendered or not raw or not rendered:
        return True
    unescaped = _ESCAPED_PUNCT.sub(r"\1", raw)
    if unescaped == rendered:
        return True
    simplified = re.sub(r"[*_`~]", "", unescaped)
    return simplified == rendered


def _align_candidates(
    candidates: Sequence[_Candidate], token_links: Sequence[_TokenLink]
) -> list[tuple[_Candidate, _TokenLink]]:
    aligned: list[tuple[_Candidate, _TokenLink]] = []
    cursor = 0
    for token_link in token_links:
        found = -1
        decoded_destination = unquote(token_link.destination)
        for index in range(cursor, len(candidates)):
            candidate = candidates[index]
            destination_matches = (
                candidate.destination == token_link.destination
                or candidate.destination == decoded_destination
            )
            if (
                candidate.is_image == token_link.is_image
                and destination_matches
                and _labels_match(candidate.raw_label, token_link.label)
            ):
                found = index
                break
        if found == -1:
            raise DraftError(
                CheckCode.PAGE_PARSE_ERROR.value,
                details=(ParseErrorReason.SOURCE_ALIGNMENT_FAILED.value, token_link.destination),
            )
        aligned.append((candidates[found], token_link))
        cursor = found + 1
    return aligned


def _machine_lines(
    text: str, starts: Sequence[int], blockquote_map: tuple[int, int]
) -> list[_MachineLine]:
    lines = text.split("\n")
    result: list[_MachineLine] = []
    for line_index in range(blockquote_map[0], blockquote_map[1]):
        raw = lines[line_index].rstrip("\r")
        prefix = _QUOTE_PREFIX.match(raw)
        if prefix is None:
            if raw.strip():
                raise DraftError(
                    CheckCode.PAGE_PARSE_ERROR.value,
                    details=(ParseErrorReason.LAZY_CONTINUATION.value,),
                    line=line_index + 1,
                )
            continue
        content = raw[prefix.end() :]
        content_start = starts[line_index] + prefix.end()
        content_end = starts[line_index] + len(raw)
        if not content.strip():
            continue
        label: str | None = None
        value = content
        if "：" in content:
            candidate_label, value = content.split("：", 1)
            if candidate_label in _KNOWN_LABELS:
                label = candidate_label
            else:
                raise DraftError(
                    CheckCode.PAGE_UNKNOWN_LABEL.value,
                    details=(candidate_label,),
                    line=line_index + 1,
                )
        result.append(
            _MachineLine(
                label=label,
                value=value,
                line=line_index + 1,
                content_start=content_start,
                content_end=content_end,
            )
        )
    return result


def _parse_status_value(value: str, path: Path, line: int) -> StatusValue:
    stripped = value.strip()
    if (
        len(stripped) < 2
        or not stripped.startswith("`")
        or not stripped.endswith("`")
        or "`" in stripped[1:-1]
    ):
        raise DraftError(
            CheckCode.PAGE_INVALID_STATUS.value,
            path,
            details=(InvalidStatusReason.NOT_SINGLE_CODE_SPAN.value,),
            line=line,
        )
    inner = stripped[1:-1]
    if "：" in inner:
        prefix_text, explanation = inner.split("：", 1)
        if not explanation.strip():
            raise DraftError(
                CheckCode.PAGE_INVALID_STATUS.value,
                path,
                details=(InvalidStatusReason.EMPTY_EXPLANATION.value,),
                line=line,
            )
    else:
        prefix_text, explanation = inner, None
    try:
        code = status_code(prefix_text)
    except ValueError as exc:
        raise DraftError(
            CheckCode.PAGE_INVALID_STATUS.value,
            path,
            details=(InvalidStatusReason.UNKNOWN_PREFIX.value, prefix_text),
            line=line,
        ) from exc
    return StatusValue(code=code, explanation=explanation)


def parse_page(path: Path, text: str) -> PageDocument:
    tokens = COMMONMARK.parse(text, {})
    starts = _line_starts(text)

    if not tokens or tokens[0].type != "heading_open" or tokens[0].tag != "h1" or len(tokens) < 3:
        raise DraftError(CheckCode.PAGE_MISSING_TITLE.value, path)
    title_token = tokens[1]
    title = title_token.content.strip()
    machine_open_index = 3
    if len(tokens) <= machine_open_index or tokens[machine_open_index].type != "blockquote_open":
        raise DraftError(CheckCode.PAGE_MISSING_MACHINE_BLOCK.value, path)
    blockquote_map = tokens[machine_open_index].map
    if blockquote_map is None:
        raise DraftError(
            CheckCode.PAGE_PARSE_ERROR.value,
            path,
            details=(ParseErrorReason.MACHINE_BLOCK_MAP_MISSING.value,),
        )

    lines = text.split("\n")
    definitions = _scan_definitions(lines, starts, _fenced_line_ranges(lines))
    candidates = _scan_candidates(text, starts, definitions)
    token_links = _token_links(tokens)
    aligned = _align_candidates(candidates, token_links)

    links: list[MarkdownLink] = []
    for candidate, token_link in aligned:
        links.append(
            MarkdownLink(
                label=token_link.label,
                destination=candidate.destination,
                destination_span=SourceSpan(candidate.dest_start, candidate.dest_end),
                whole_span=SourceSpan(candidate.start, candidate.end),
                line=_line_of(starts, candidate.start),
                is_image=candidate.is_image,
                reference_label=candidate.reference_label,
            )
        )

    try:
        machine = _machine_lines(text, starts, (blockquote_map[0], blockquote_map[1]))
    except DraftError as error:
        if not error.paths:
            raise DraftError(error.code, path, details=error.details, line=error.line) from error
        raise
    status: StatusValue | None = None
    relations: list[Relation] = []
    for machine_line in machine:
        if machine_line.label is None:
            continue
        if machine_line.label == _STATUS_LABEL:
            if status is not None:
                raise DraftError(
                    CheckCode.PAGE_DUPLICATE_STATUS.value,
                    path,
                    details=(),
                    line=machine_line.line,
                )
            status = _parse_status_value(machine_line.value, path, machine_line.line)
            continue
        try:
            code = relation_code(machine_line.label)
        except ValueError as exc:
            raise DraftError(
                CheckCode.PAGE_INVALID_RELATION.value,
                path,
                details=(InvalidRelationReason.EXTRA_CONTENT.value, machine_line.label),
                line=machine_line.line,
            ) from exc
        contained = [
            link
            for link in links
            if machine_line.content_start <= link.whole_span.start < machine_line.content_end
        ]
        leading = len(machine_line.value) - len(machine_line.value.lstrip())
        value_start = machine_line.content_start + len(machine_line.label) + 1 + leading
        if len(contained) != 1 or contained[0].is_image:
            raise DraftError(
                CheckCode.PAGE_INVALID_RELATION.value,
                path,
                details=(InvalidRelationReason.LINK_CARDINALITY_OR_IMAGE.value, machine_line.label),
                line=machine_line.line,
            )
        link = contained[0]
        before = text[value_start : link.whole_span.start]
        after = text[link.whole_span.end : machine_line.content_end]
        if before.strip() or after.strip():
            raise DraftError(
                CheckCode.PAGE_INVALID_RELATION.value,
                path,
                details=(InvalidRelationReason.EXTRA_CONTENT.value, machine_line.label),
                line=machine_line.line,
            )
        relations.append(Relation(code=code, link=link))

    if status is None:
        raise DraftError(CheckCode.PAGE_MISSING_STATUS.value, path)

    return PageDocument(
        path=path,
        title=title,
        status=status,
        relations=tuple(relations),
        links=tuple(links),
        text=text,
    )


def extract_body_links(text: str) -> tuple[MarkdownLink, ...]:
    """提取正文 CommonMark 链接，不要求标题与机器块。

    供未受管理文件的链接可判定性判定与改写使用（规格 §4.1）：与
    parse_page 共享同一对齐管线，对齐失败同样抛 page.parse_error，
    由调用方归类为阻碍。
    """
    tokens = COMMONMARK.parse(text, {})
    starts = _line_starts(text)
    lines = text.split("\n")
    definitions = _scan_definitions(lines, starts, _fenced_line_ranges(lines))
    candidates = _scan_candidates(text, starts, definitions)
    token_links = _token_links(tokens)
    aligned = _align_candidates(candidates, token_links)
    links: list[MarkdownLink] = []
    for candidate, _token in aligned:
        links.append(
            MarkdownLink(
                label=candidate.raw_label,
                destination=candidate.destination,
                destination_span=SourceSpan(candidate.dest_start, candidate.dest_end),
                whole_span=SourceSpan(candidate.start, candidate.end),
                line=_line_of(starts, candidate.start),
                is_image=candidate.is_image,
                reference_label=candidate.reference_label,
            )
        )
    return tuple(links)


def locate_status_value_span(text: str) -> SourceSpan:
    """Return the span of the backticked status value, including backticks."""
    tokens = COMMONMARK.parse(text, {})
    if len(tokens) < 4 or tokens[0].type != "heading_open":
        raise DraftError(CheckCode.PAGE_MISSING_TITLE.value)
    if tokens[3].type != "blockquote_open" or tokens[3].map is None:
        raise DraftError(CheckCode.PAGE_MISSING_MACHINE_BLOCK.value)
    starts = _line_starts(text)
    machine = _machine_lines(text, starts, (tokens[3].map[0], tokens[3].map[1]))
    for machine_line in machine:
        if machine_line.label != _STATUS_LABEL:
            continue
        value_offset = machine_line.value.find("`")
        if value_offset == -1:
            raise DraftError(CheckCode.PAGE_INVALID_STATUS.value)
        label_width = len(_STATUS_LABEL) + 1
        begin = machine_line.content_start + label_width + value_offset
        end = machine_line.content_start + label_width + machine_line.value.rfind("`") + 1
        return SourceSpan(begin, end)
    raise DraftError(CheckCode.PAGE_MISSING_STATUS.value)


def _apply_edits(text: str, edits: Mapping[SourceSpan, str]) -> str:
    for span in edits:
        if span.start < 0 or span.end < span.start or span.end > len(text):
            raise DraftError("markdown.invalid_edit")
    ordered = sorted(edits.items(), key=lambda item: item[0].start)
    for index in range(1, len(ordered)):
        previous, _ = ordered[index - 1]
        current, _ = ordered[index]
        if current.start < previous.end:
            raise DraftError("markdown.overlapping_edits")
    result = text
    for span, replacement in sorted(edits.items(), key=lambda item: item[0].start, reverse=True):
        result = result[: span.start] + replacement + result[span.end :]
    return result


def rewrite_link_destinations(text: str, replacements: Mapping[SourceSpan, str]) -> str:
    return _apply_edits(text, replacements)


def remove_link_markup(text: str, links: Sequence[MarkdownLink]) -> str:
    return _apply_edits(text, {link.whole_span: link.label for link in links})


_LIST_ITEM = re.compile(r"^(\s*(?:[-+*]|\d{1,9}[.)])\s+)(.*)$")
_SUBTOPIC_HEADING = re.compile(r"^## 子主题\s*$")


def list_item_content(line: str) -> str | None:
    """返回行首列表项标记之后的内容；不是列表项（``-``/``+``/``*``/有序）时返回 None。"""
    matched = _LIST_ITEM.match(line)
    if matched is None:
        return None
    return matched.group(2)


def canonical_parent_navigation(graph: DraftGraph, child: Path) -> MarkdownLink | None:
    """The earliest non-machine-block, non-image link in the declared parent to child."""
    from draft.graph import resolve_local_destination

    parent = graph.parents.get(child)
    if parent is None or parent not in graph.pages:
        return None
    page = graph.pages[parent]
    relation_spans = {relation.link.whole_span for relation in page.relations}
    best: MarkdownLink | None = None
    for link in page.links:
        if link.is_image or link.whole_span in relation_spans:
            continue
        try:
            target, _anchor = resolve_local_destination(parent, link.destination, graph.root)
        except DraftError:
            continue
        if target != child:
            continue
        if best is None or link.whole_span.start < best.whole_span.start:
            best = link
    return best


def navigation_removal_edits(text: str, link: MarkdownLink) -> dict[SourceSpan, str]:
    """Edits removing one navigation link: whole list-item line, or markup only."""
    line_start = text.rfind("\n", 0, link.whole_span.start) + 1
    line_end = text.find("\n", link.whole_span.end)
    if line_end == -1:
        line_end = len(text)
    line = text[line_start:line_end]
    item = _LIST_ITEM.match(line)
    if item is not None:
        content_start = line_start + len(item.group(1))
        before = text[content_start : link.whole_span.start]
        after = text[link.whole_span.end : line_end]
        if not before.strip() and not after.strip():
            end = line_end + 1 if line_end < len(text) else line_end
            return {SourceSpan(line_start, end): ""}
    return {link.whole_span: link.label}


def remove_parent_navigation(text: str, link: MarkdownLink) -> str:
    return _apply_edits(text, navigation_removal_edits(text, link))


def remove_parent_navigation_many(text: str, links: Sequence[MarkdownLink]) -> str:
    """一次移除多个父导航链接，共享同一整行删除语义；跨度必须互不重叠。"""
    edits: dict[SourceSpan, str] = {}
    for link in links:
        edits.update(navigation_removal_edits(text, link))
    return _apply_edits(text, edits)


def escape_link_label(title: str) -> str:
    """把页面标题转义为 Markdown 链接标签文本：反斜杠、``[``、``]``。

    生成器、导航追加与插入共用同一转义边界，保证转义后可解析还原为原始标题。
    """
    return title.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def append_parent_navigation(text: str, title: str, destination: str) -> str:
    """Append '- [title](destination)' to an existing level-two 子主题 section, or create it."""
    escaped = escape_link_label(title)
    entry = f"- [{escaped}]({destination})"
    lines = text.split("\n")
    heading_index = next(
        (index for index, line in enumerate(lines) if _SUBTOPIC_HEADING.match(line)),
        None,
    )
    if heading_index is None:
        body = text if text.endswith("\n") else text + "\n"
        return body + f"\n## 子主题\n\n{entry}\n"
    section_end = len(lines)
    for index in range(heading_index + 1, len(lines)):
        if lines[index].startswith("#"):
            section_end = index
            break
    last_content = heading_index
    for index in range(heading_index + 1, section_end):
        if lines[index].strip():
            last_content = index
    lines.insert(last_content + 1, entry)
    return "\n".join(lines)


def insert_navigation_before_conclusions(
    text: str, title: str, destination: str, *, conclusions_target: Path
) -> str:
    """把新导航项插入到规范结论链接之前，使结论链接继续保持最后。

    结论链接通过解析后的目标识别，不搜索 ``conclusions.md`` 文本。结论链接
    必须是 ``## 子主题`` 小节中的最后一个列表项；找不到时不插入并保持原样。
    """
    escaped = escape_link_label(title)
    entry = f"- [{escaped}]({destination})"
    lines = text.split("\n")
    heading_index = next(
        (index for index, line in enumerate(lines) if _SUBTOPIC_HEADING.match(line)),
        None,
    )
    if heading_index is None:
        return text
    section_end = len(lines)
    for index in range(heading_index + 1, len(lines)):
        if lines[index].startswith("#"):
            section_end = index
            break
    # 找到小节中最后一个指向结论页的列表项行（从后往前，跳过空行）。
    conclusion_line: int | None = None
    for index in range(section_end - 1, heading_index, -1):
        if not lines[index].strip():
            continue
        if not _LIST_ITEM.match(lines[index]):
            break
        parsed = parse_page(
            Path("__nav__.md"),
            f"# x\n\n> 状态：`待讨论`\n\n{lines[index]}\n",
        )
        if any(_resolves_to(link.destination, conclusions_target) for link in parsed.links):
            conclusion_line = index
            break
    if conclusion_line is None:
        return text
    # 结论链接之后不允许再有非空行（必须是最后一个）。
    for index in range(conclusion_line + 1, section_end):
        if lines[index].strip():
            return text
    lines.insert(conclusion_line, entry)
    return "\n".join(lines)


def _resolves_to(destination: str, target: Path) -> bool:
    from draft.graph import resolve_local_destination

    try:
        resolved, _anchor = resolve_local_destination(Path("index.md"), destination, Path("/"))
    except DraftError:
        return False
    return resolved == target
