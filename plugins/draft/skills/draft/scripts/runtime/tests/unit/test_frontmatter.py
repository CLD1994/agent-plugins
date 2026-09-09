from pathlib import Path

import pytest

from draft.diagnostic_codes import (
    CheckCode,
    FrontmatterReason,
    PageNoteReason,
    PageParentReason,
    PageStatusReason,
    RelationDeclarationReason,
)
from draft.frontmatter import (
    FrontmatterMetadata,
    FrontmatterRelation,
    parse_frontmatter,
    rewrite_frontmatter,
    serialize_frontmatter,
)
from draft.models import SourceSpan, StatusValue
from draft.vocabulary import RelationCode, StatusCode


def test_frontmatter_diagnostic_codes_and_reasons_are_registered() -> None:
    assert {
        CheckCode.PAGE_FRONTMATTER_INVALID,
        CheckCode.PAGE_STATUS_INVALID,
        CheckCode.PAGE_NOTE_INVALID,
        CheckCode.PAGE_PARENT_INVALID,
        CheckCode.RELATION_DECLARATION_INVALID,
    } <= set(CheckCode)
    assert FrontmatterReason.UNSUPPORTED_FEATURE.value == "unsupported_feature"
    assert PageStatusReason.INVALID_TYPE.value == "invalid_type"
    assert PageNoteReason.CONTROL_CHARACTER.value == "control_character"
    assert PageParentReason.INVALID_PATH.value == "invalid_path"
    assert RelationDeclarationReason.TARGET_PATH_INVALID.value == "target_path_invalid"


def test_parse_valid_frontmatter_returns_metadata_and_body_boundary() -> None:
    text = """---
status: proposed
note: "短说明"
parent: index.md
relations:
  - type: depends_on
    target: pages/background.md
---
# 页面标题

正文。
"""

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert result.is_valid
    assert result.diagnostics == ()
    assert result.metadata is not None
    assert result.metadata.status.code is StatusCode.PROPOSED
    assert result.metadata.note == "短说明"
    assert result.metadata.parent == Path("index.md")
    assert result.metadata.relations[0].code is RelationCode.DEPENDS_ON
    assert result.metadata.relations[0].target == Path("pages/background.md")
    assert result.body_start is not None
    assert text[result.body_start :] == "# 页面标题\n\n正文。\n"


def test_missing_frontmatter_has_a_specific_diagnostic_and_no_body_boundary() -> None:
    result = parse_frontmatter("# 没有头部\n正文\n", Path("index.md"))

    assert not result.is_valid
    assert result.metadata is None
    assert result.body_start is None
    assert len(result.diagnostics) == 1
    diagnostic = result.diagnostics[0]
    assert diagnostic.code == CheckCode.PAGE_FRONTMATTER_INVALID.value
    assert diagnostic.reason == "missing"


def test_noncanonical_opening_delimiter_is_rejected_without_body_boundary() -> None:
    result = parse_frontmatter("--- # comment\nstatus: proposed\n---\n正文\n", Path("index.md"))

    assert not result.is_valid
    assert result.metadata is None
    assert result.body_start is None
    assert len(result.diagnostics) == 1
    diagnostic = result.diagnostics[0]
    assert diagnostic.code == CheckCode.PAGE_FRONTMATTER_INVALID.value
    assert diagnostic.reason == "delimiter_invalid"


def test_missing_closing_delimiter_does_not_guess_where_body_begins() -> None:
    result = parse_frontmatter("---\nstatus: proposed\n正文\n", Path("index.md"))

    assert not result.is_valid
    assert result.metadata is None
    assert result.body_start is None
    assert len(result.diagnostics) == 1
    assert result.diagnostics[0].reason == "delimiter_invalid"


def test_crlf_frontmatter_keeps_the_body_start_and_body_bytes_exact() -> None:
    text = (
        '---\r\nstatus: proposed\r\nnote: "说明"\r\nparent: index.md\r\n---\r\n# 标题\r\n正文  \r\n'
    )

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert result.is_valid
    assert result.body_start is not None
    assert text[result.body_start :] == "# 标题\r\n正文  \r\n"


def test_crlf_relation_spans_point_to_the_original_text() -> None:
    text = (
        "---\r\nstatus: proposed\r\nparent: index.md\r\nrelations:\r\n"
        "  - type: depends_on\r\n    target: pages/目标.md\r\n---\r\n正文\r\n"
    )

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert result.is_valid
    assert result.metadata is not None
    relation = result.metadata.relations[0]
    assert text[relation.type_span.start : relation.type_span.end] == "depends_on"
    assert text[relation.target_span.start : relation.target_span.end] == "pages/目标.md"
    assert relation.line == 5


def test_invalid_yaml_keeps_authenticated_body_boundary_but_no_metadata() -> None:
    text = "---\nstatus: [proposed\nparent: index.md\n---\n正文\n"

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert not result.is_valid
    assert result.metadata is None
    assert result.body_start is not None
    assert text[result.body_start :] == "正文\n"
    assert len(result.diagnostics) == 1
    diagnostic = result.diagnostics[0]
    assert diagnostic.code == CheckCode.PAGE_FRONTMATTER_INVALID.value
    assert diagnostic.reason == "yaml_invalid"


def test_missing_status_is_reported_without_returning_partial_metadata() -> None:
    text = '---\nnote: "说明"\nparent: index.md\n---\n正文\n'

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert not result.is_valid
    assert result.metadata is None
    assert result.body_start is not None
    assert len(result.diagnostics) == 1
    diagnostic = result.diagnostics[0]
    assert diagnostic.code == CheckCode.PAGE_STATUS_INVALID.value
    assert diagnostic.reason == "missing"


@pytest.mark.parametrize("yaml_body", ["- status\n", "plain\n", "\n"])
def test_non_mapping_yaml_root_blocks_field_diagnostics(yaml_body: str) -> None:
    text = f"---\n{yaml_body}---\n正文\n"

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert not result.is_valid
    assert result.metadata is None
    assert len(result.diagnostics) == 1
    diagnostic = result.diagnostics[0]
    assert diagnostic.code == CheckCode.PAGE_FRONTMATTER_INVALID.value
    assert diagnostic.reason == "not_mapping"
    assert result.body_start is not None
    assert text[result.body_start :] == "正文\n"


def test_non_mapping_root_blocks_nested_mapping_diagnostics() -> None:
    text = "---\n- status: proposed\n  status: closed\n---\n正文\n"

    result = parse_frontmatter(text, Path("index.md"))

    assert not result.is_valid
    assert result.metadata is None
    assert len(result.diagnostics) == 1
    diagnostic = result.diagnostics[0]
    assert diagnostic.code == CheckCode.PAGE_FRONTMATTER_INVALID.value
    assert diagnostic.reason == "not_mapping"


def test_non_string_status_is_reported_as_a_type_error() -> None:
    text = "---\nstatus: 3\nparent: index.md\n---\n正文\n"

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert not result.is_valid
    assert result.metadata is None
    assert len(result.diagnostics) == 1
    diagnostic = result.diagnostics[0]
    assert diagnostic.code == CheckCode.PAGE_STATUS_INVALID.value
    assert diagnostic.reason == "invalid_type"


def test_unknown_status_string_is_reported_as_an_invalid_value() -> None:
    text = "---\nstatus: unknown\nparent: index.md\n---\n正文\n"

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert not result.is_valid
    assert result.metadata is None
    assert len(result.diagnostics) == 1
    diagnostic = result.diagnostics[0]
    assert diagnostic.code == CheckCode.PAGE_STATUS_INVALID.value
    assert diagnostic.reason == "invalid_value"


def test_empty_note_is_rejected_without_returning_status_metadata() -> None:
    text = '---\nstatus: proposed\nnote: ""\nparent: index.md\n---\n正文\n'

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert not result.is_valid
    assert result.metadata is None
    assert len(result.diagnostics) == 1
    diagnostic = result.diagnostics[0]
    assert diagnostic.code == CheckCode.PAGE_NOTE_INVALID.value
    assert diagnostic.reason == "empty"


def test_non_string_non_null_note_uses_frontmatter_type_diagnostic() -> None:
    text = "---\nstatus: proposed\nnote: 7\nparent: index.md\n---\n正文\n"

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert not result.is_valid
    assert result.metadata is None
    assert len(result.diagnostics) == 1
    diagnostic = result.diagnostics[0]
    assert diagnostic.code == CheckCode.PAGE_FRONTMATTER_INVALID.value
    assert diagnostic.reason == "invalid_value_type"


def test_multiline_note_is_rejected_as_a_note_diagnostic() -> None:
    text = "---\nstatus: proposed\nnote: |\n  第一行\n  第二行\nparent: index.md\n---\n正文\n"

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert not result.is_valid
    assert result.metadata is None
    assert len(result.diagnostics) == 1
    diagnostic = result.diagnostics[0]
    assert diagnostic.code == CheckCode.PAGE_NOTE_INVALID.value
    assert diagnostic.reason == "multiline"


def test_folded_note_with_an_empty_line_retains_a_newline_and_is_rejected() -> None:
    text = "---\nstatus: proposed\nnote: >-\n  one\n\n  two\n---\n正文\n"

    result = parse_frontmatter(text, Path("index.md"))

    assert not result.is_valid
    assert [(item.code, item.reason) for item in result.diagnostics] == [
        (CheckCode.PAGE_NOTE_INVALID.value, "multiline")
    ]


def test_folded_note_uses_the_decoded_yaml_value() -> None:
    text = "---\nstatus: proposed\nnote: >-\n  one\n  two\n---\n正文\n"

    result = parse_frontmatter(text, Path("index.md"))

    assert result.is_valid
    assert result.metadata is not None
    assert result.metadata.note == "one two"


@pytest.mark.parametrize("line_ending", ["\n", "\r\n", "\r"])
def test_folded_scalar_values_are_stable_across_yaml_line_endings(
    line_ending: str,
) -> None:
    text = (
        line_ending.join(
            [
                "---",
                "status: proposed",
                "note: >-",
                "  one",
                "  two",
                "parent: >-",
                "  pages/a",
                "  b.md",
                "relations:",
                "  - type: depends_on",
                "    target: >-",
                "      pages/a",
                "      b.md",
                "---",
                "正文",
            ]
        )
        + line_ending
    )

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert result.is_valid
    assert result.metadata is not None
    assert result.metadata.note == "one two"
    assert result.metadata.parent == Path("pages/a b.md")
    assert result.metadata.relations[0].target == Path("pages/a b.md")
    assert result.body_start is not None
    assert text[result.body_start :] == "正文" + line_ending


def test_control_character_in_note_is_rejected() -> None:
    text = '---\nstatus: proposed\nnote: "含\\t制表符"\nparent: index.md\n---\n正文\n'

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert not result.is_valid
    assert result.metadata is None
    assert len(result.diagnostics) == 1
    diagnostic = result.diagnostics[0]
    assert diagnostic.code == CheckCode.PAGE_NOTE_INVALID.value
    assert diagnostic.reason == "control_character"


def test_literal_unicode_next_line_in_note_is_rejected_before_yaml_normalization() -> None:
    text = '---\nstatus: proposed\nnote: "含\u0085换行"\nparent: index.md\n---\n正文\n'

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert not result.is_valid
    assert result.metadata is None
    assert len(result.diagnostics) == 1
    diagnostic = result.diagnostics[0]
    assert diagnostic.code == CheckCode.PAGE_NOTE_INVALID.value
    assert diagnostic.reason == "multiline"


def test_quoted_yaml_indicator_characters_remain_string_content() -> None:
    text = '---\nstatus: proposed\nnote: "# literal & * !"\nparent: index.md\n---\n正文\n'

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert result.is_valid
    assert result.metadata is not None
    assert result.metadata.note == "# literal & * !"


def test_root_page_must_not_declare_parent() -> None:
    text = "---\nstatus: proposed\nparent: index.md\n---\n正文\n"

    result = parse_frontmatter(text, Path("index.md"))

    assert not result.is_valid
    assert result.metadata is None
    assert len(result.diagnostics) == 1
    diagnostic = result.diagnostics[0]
    assert diagnostic.code == CheckCode.PAGE_PARENT_INVALID.value
    assert diagnostic.reason == "unexpected"


def test_non_root_page_requires_parent() -> None:
    text = "---\nstatus: proposed\n---\n正文\n"

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert not result.is_valid
    assert result.metadata is None
    assert len(result.diagnostics) == 1
    diagnostic = result.diagnostics[0]
    assert diagnostic.code == CheckCode.PAGE_PARENT_INVALID.value
    assert diagnostic.reason == "missing"


def test_non_string_parent_uses_frontmatter_type_diagnostic() -> None:
    text = "---\nstatus: proposed\nparent: 3\n---\n正文\n"

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert not result.is_valid
    assert result.metadata is None
    assert len(result.diagnostics) == 1
    diagnostic = result.diagnostics[0]
    assert diagnostic.code == CheckCode.PAGE_FRONTMATTER_INVALID.value
    assert diagnostic.reason == "invalid_value_type"


def test_parent_must_be_a_normalized_root_relative_page_path() -> None:
    text = "---\nstatus: proposed\nparent: ../index.md\n---\n正文\n"

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert not result.is_valid
    assert result.metadata is None
    assert len(result.diagnostics) == 1
    diagnostic = result.diagnostics[0]
    assert diagnostic.code == CheckCode.PAGE_PARENT_INVALID.value
    assert diagnostic.reason == "invalid_path"


def test_folded_parent_uses_the_decoded_yaml_value() -> None:
    text = "---\nstatus: proposed\nparent: >-\n  pages/a\n  b.md\n---\n正文\n"

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert result.is_valid
    assert result.metadata is not None
    assert result.metadata.parent == Path("pages/a b.md")


def test_literal_parent_block_with_a_line_break_remains_invalid() -> None:
    text = "---\nstatus: proposed\nparent: |-\n  pages/a\n  b.md\n---\n正文\n"

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert not result.is_valid
    assert [(item.code, item.reason) for item in result.diagnostics] == [
        (CheckCode.PAGE_PARENT_INVALID.value, "invalid_path")
    ]


def test_literal_unicode_next_line_in_parent_is_not_normalized_to_a_valid_path() -> None:
    text = '---\nstatus: proposed\nparent: "pages/a\u0085b.md"\n---\n正文\n'

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert not result.is_valid
    assert result.metadata is None
    assert len(result.diagnostics) == 1
    diagnostic = result.diagnostics[0]
    assert diagnostic.code == CheckCode.PAGE_PARENT_INVALID.value
    assert diagnostic.reason == "invalid_path"


@pytest.mark.parametrize(
    "parent",
    [
        "/index.md",
        "./index.md",
        "pages//index.md",
        "pages/./index.md",
        "pages/../index.md",
        "pages\\index.md",
        "not-a-page",
        '""',
        '"bad\\u0000.md"',
    ],
)
def test_parent_rejects_noncanonical_root_path_lexemes(parent: str) -> None:
    text = f"---\nstatus: proposed\nparent: {parent}\n---\n正文\n"

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert not result.is_valid
    assert result.metadata is None
    assert len(result.diagnostics) == 1
    diagnostic = result.diagnostics[0]
    assert diagnostic.code == CheckCode.PAGE_PARENT_INVALID.value
    assert diagnostic.reason == "invalid_path"


def test_root_path_keeps_spaces_unicode_and_uri_punctuation_literal() -> None:
    text = (
        "---\nstatus: proposed\nparent: pages/空 格?#%.md\n"
        "relations:\n  - type: depends_on\n    target: pages/é.md\n---\n正文\n"
    )

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert result.is_valid
    assert result.metadata is not None
    assert result.metadata.parent == Path("pages/空 格?#%.md")
    assert result.metadata.relations[0].target == Path("pages/é.md")


def test_serialize_frontmatter_uses_stable_field_and_relation_order() -> None:
    metadata = FrontmatterMetadata(
        status=StatusValue(StatusCode.CONFIRMED, "状态说明"),
        parent=Path("index.md"),
        relations=(
            FrontmatterRelation(
                code=RelationCode.CHALLENGES,
                target=Path("pages/质疑.md"),
                span=SourceSpan(0, 0),
                type_span=SourceSpan(0, 0),
                target_span=SourceSpan(0, 0),
                line=1,
            ),
        ),
    )

    serialized = serialize_frontmatter(metadata, Path("pages/topic.md"), "# 正文\n")

    assert serialized == (
        "---\n"
        "status: confirmed\n"
        'note: "状态说明"\n'
        'parent: "index.md"\n'
        "relations:\n"
        "  - type: challenges\n"
        '    target: "pages/质疑.md"\n'
        "---\n"
        "# 正文\n"
    )


def test_rewrite_frontmatter_preserves_crlf_body_bytes_and_replaces_only_header() -> None:
    original = '---\r\nparent: "index.md"\r\nstatus: proposed\r\n---\r\n# 标题\r\n正文尾部  \r\n'
    metadata = FrontmatterMetadata(
        status=StatusValue(StatusCode.CLOSED, None),
        parent=Path("index.md"),
        relations=(),
    )

    rewritten = rewrite_frontmatter(original, Path("pages/topic.md"), metadata)

    assert rewritten == (
        '---\r\nstatus: closed\r\nparent: "index.md"\r\n---\r\n# 标题\r\n正文尾部  \r\n'
    )


def test_serialize_root_omits_optional_fields_when_they_are_absent() -> None:
    metadata = FrontmatterMetadata(
        status=StatusValue(StatusCode.PENDING, None),
        parent=None,
        relations=(),
    )

    serialized = serialize_frontmatter(metadata, Path("index.md"))

    assert serialized == "---\nstatus: pending\n---\n"


def test_serialize_generates_lf_header_without_normalizing_supplied_body() -> None:
    metadata = FrontmatterMetadata(
        status=StatusValue(StatusCode.PENDING, None),
        parent=None,
        relations=(),
    )

    serialized = serialize_frontmatter(metadata, Path("index.md"), "正文\r\n尾部\r\n")

    assert serialized == "---\nstatus: pending\n---\n正文\r\n尾部\r\n"


def test_serialized_frontmatter_can_be_read_back_without_changing_domain_facts() -> None:
    metadata = FrontmatterMetadata(
        status=StatusValue(StatusCode.PROPOSED, '说明含 "引号" 和 \\ 反斜线'),
        parent=Path("index.md"),
        relations=(
            FrontmatterRelation(
                code=RelationCode.DEPENDS_ON,
                target=Path("pages/目标?#%.md"),
                span=SourceSpan(0, 0),
                type_span=SourceSpan(0, 0),
                target_span=SourceSpan(0, 0),
                line=1,
            ),
        ),
    )
    body = "正文 # & *\n末尾空格  \n"

    serialized = serialize_frontmatter(metadata, Path("pages/topic.md"), body)
    parsed = parse_frontmatter(serialized, Path("pages/topic.md"))

    assert parsed.is_valid
    assert parsed.metadata is not None
    assert parsed.metadata.status == metadata.status
    assert parsed.metadata.parent == metadata.parent
    assert [(item.code, item.target) for item in parsed.metadata.relations] == [
        (RelationCode.DEPENDS_ON, Path("pages/目标?#%.md"))
    ]
    assert parsed.body_start is not None
    assert serialized[parsed.body_start :] == body


def test_relations_must_be_a_list() -> None:
    text = "---\nstatus: proposed\nparent: index.md\nrelations: nope\n---\n正文\n"

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert not result.is_valid
    assert result.metadata is None
    assert len(result.diagnostics) == 1
    diagnostic = result.diagnostics[0]
    assert diagnostic.code == CheckCode.PAGE_FRONTMATTER_INVALID.value
    assert diagnostic.reason == "invalid_value_type"


def test_empty_relations_list_is_not_a_valid_declaration() -> None:
    text = "---\nstatus: proposed\nparent: index.md\nrelations: []\n---\n正文\n"

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert not result.is_valid
    assert result.metadata is None
    assert len(result.diagnostics) == 1
    diagnostic = result.diagnostics[0]
    assert diagnostic.code == CheckCode.RELATION_DECLARATION_INVALID.value
    assert diagnostic.reason == "container_empty"


def test_relation_member_must_be_a_mapping_with_exactly_two_fields() -> None:
    text = "---\nstatus: proposed\nparent: index.md\nrelations:\n  - nope\n---\n正文\n"

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert not result.is_valid
    assert result.metadata is None
    assert len(result.diagnostics) == 1
    diagnostic = result.diagnostics[0]
    assert diagnostic.code == CheckCode.RELATION_DECLARATION_INVALID.value
    assert diagnostic.reason == "member_shape"


def test_relation_member_non_scalar_extra_key_is_a_shape_error() -> None:
    text = (
        "---\nstatus: proposed\nparent: index.md\nrelations:\n"
        "  - ? [extra]\n    : value\n    type: depends_on\n"
        "    target: pages/target.md\n---\n正文\n"
    )

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert not result.is_valid
    assert result.metadata is None
    assert len(result.diagnostics) == 1
    diagnostic = result.diagnostics[0]
    assert diagnostic.code == CheckCode.RELATION_DECLARATION_INVALID.value
    assert diagnostic.reason == "member_shape"


def test_relation_member_mapping_key_is_a_shape_error() -> None:
    text = (
        "---\nstatus: proposed\nparent: index.md\nrelations:\n"
        "  - ? {extra: key}\n    : value\n    type: depends_on\n"
        "    target: pages/target.md\n---\n正文\n"
    )

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert not result.is_valid
    assert result.metadata is None
    assert len(result.diagnostics) == 1
    diagnostic = result.diagnostics[0]
    assert diagnostic.code == CheckCode.RELATION_DECLARATION_INVALID.value
    assert diagnostic.reason == "member_shape"


def test_relation_type_is_checked_after_member_shape() -> None:
    text = (
        "---\nstatus: proposed\nparent: index.md\nrelations:\n"
        "  - type: 7\n    target: pages/target.md\n---\n正文\n"
    )

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert not result.is_valid
    assert result.metadata is None
    assert len(result.diagnostics) == 1
    diagnostic = result.diagnostics[0]
    assert diagnostic.code == CheckCode.RELATION_DECLARATION_INVALID.value
    assert diagnostic.reason == "type_unknown"


def test_relation_target_must_be_a_root_relative_page_path() -> None:
    text = (
        "---\nstatus: proposed\nparent: index.md\nrelations:\n"
        "  - type: depends_on\n    target: ../target.md\n---\n正文\n"
    )

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert not result.is_valid
    assert result.metadata is None
    assert len(result.diagnostics) == 1
    diagnostic = result.diagnostics[0]
    assert diagnostic.code == CheckCode.RELATION_DECLARATION_INVALID.value
    assert diagnostic.reason == "target_path_invalid"


def test_folded_relation_target_uses_the_decoded_yaml_value() -> None:
    text = (
        "---\nstatus: proposed\nparent: index.md\nrelations:\n"
        "  - type: depends_on\n    target: >-\n"
        "      pages/a\n      b.md\n---\n正文\n"
    )

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert result.is_valid
    assert result.metadata is not None
    assert result.metadata.relations[0].target == Path("pages/a b.md")


def test_more_indented_folded_relation_target_retains_a_newline_and_is_rejected() -> None:
    text = (
        "---\nstatus: proposed\nparent: index.md\nrelations:\n"
        "  - type: depends_on\n    target: >-\n"
        "      pages/a\n        b.md\n---\n正文\n"
    )

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert not result.is_valid
    assert [(item.code, item.reason) for item in result.diagnostics] == [
        (CheckCode.RELATION_DECLARATION_INVALID.value, "target_path_invalid")
    ]


def test_literal_unicode_next_line_in_relation_target_is_rejected() -> None:
    text = (
        "---\nstatus: proposed\nparent: index.md\nrelations:\n"
        '  - type: depends_on\n    target: "pages/a\u0085b.md"\n---\n正文\n'
    )

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert not result.is_valid
    assert result.metadata is None
    assert len(result.diagnostics) == 1
    diagnostic = result.diagnostics[0]
    assert diagnostic.code == CheckCode.RELATION_DECLARATION_INVALID.value
    assert diagnostic.reason == "target_path_invalid"


def test_relation_type_and_target_are_reported_as_independent_node_errors() -> None:
    text = (
        "---\nstatus: proposed\nparent: index.md\nrelations:\n"
        "  - type: 7\n    target: ../target.md\n---\n正文\n"
    )

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert not result.is_valid
    assert result.metadata is None
    assert {(item.code, item.reason) for item in result.diagnostics} == {
        (CheckCode.RELATION_DECLARATION_INVALID.value, "type_unknown"),
        (CheckCode.RELATION_DECLARATION_INVALID.value, "target_path_invalid"),
    }


@pytest.mark.parametrize(
    "target",
    [
        "/target.md",
        "./target.md",
        "pages//target.md",
        "pages/./target.md",
        "pages/../target.md",
        "pages\\target.md",
        "target.txt",
        '"bad\\u0000.md"',
    ],
)
def test_relation_target_rejects_noncanonical_root_path_lexemes(target: str) -> None:
    text = (
        "---\nstatus: proposed\nparent: index.md\nrelations:\n"
        f"  - type: depends_on\n    target: {target}\n---\n正文\n"
    )

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert not result.is_valid
    assert result.metadata is None
    assert len(result.diagnostics) == 1
    diagnostic = result.diagnostics[0]
    assert diagnostic.code == CheckCode.RELATION_DECLARATION_INVALID.value
    assert diagnostic.reason == "target_path_invalid"


def test_yaml_anchor_is_rejected_as_an_unsupported_feature() -> None:
    text = "---\nstatus: &state proposed\nparent: index.md\n---\n正文\n"

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert not result.is_valid
    assert result.metadata is None
    assert len(result.diagnostics) == 1
    diagnostic = result.diagnostics[0]
    assert diagnostic.code == CheckCode.PAGE_FRONTMATTER_INVALID.value
    assert diagnostic.reason == "unsupported_feature"
    assert diagnostic.line == 2
    assert diagnostic.span is not None
    assert text[diagnostic.span.start : diagnostic.span.end] == "&state"


def test_yaml_alias_is_rejected_as_an_unsupported_feature() -> None:
    text = "---\nstatus: &state proposed\nparent: *state\n---\n正文\n"

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert not result.is_valid
    assert result.metadata is None
    assert [(diagnostic.code, diagnostic.reason) for diagnostic in result.diagnostics] == [
        (CheckCode.PAGE_FRONTMATTER_INVALID.value, "unsupported_feature"),
        (CheckCode.PAGE_FRONTMATTER_INVALID.value, "unsupported_feature"),
    ]
    assert all(diagnostic.span is not None for diagnostic in result.diagnostics)
    assert [
        text[diagnostic.span.start : diagnostic.span.end]
        for diagnostic in result.diagnostics
        if diagnostic.span
    ] == [
        "&state",
        "*state",
    ]


def test_yaml_merge_key_is_rejected_as_an_unsupported_feature() -> None:
    text = (
        "---\nstatus: proposed\nparent: index.md\nrelations:\n"
        "  - <<: {type: depends_on, target: pages/base.md}\n---\n正文\n"
    )

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert not result.is_valid
    assert result.metadata is None
    assert len(result.diagnostics) == 1
    diagnostic = result.diagnostics[0]
    assert diagnostic.code == CheckCode.PAGE_FRONTMATTER_INVALID.value
    assert diagnostic.reason == "unsupported_feature"


def test_yaml_comment_is_rejected_as_an_unsupported_feature() -> None:
    text = "---\n# 说明\nstatus: proposed\nparent: index.md\n---\n正文\n"

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert not result.is_valid
    assert result.metadata is None
    assert len(result.diagnostics) == 1
    diagnostic = result.diagnostics[0]
    assert diagnostic.code == CheckCode.PAGE_FRONTMATTER_INVALID.value
    assert diagnostic.reason == "unsupported_feature"
    assert diagnostic.line == 2
    assert diagnostic.raw_line == "# 说明"


def test_yaml_custom_tag_is_rejected_as_an_unsupported_feature() -> None:
    text = "---\nstatus: !proposal proposed\nparent: index.md\n---\n正文\n"

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert not result.is_valid
    assert result.metadata is None
    assert len(result.diagnostics) == 1
    diagnostic = result.diagnostics[0]
    assert diagnostic.code == CheckCode.PAGE_FRONTMATTER_INVALID.value
    assert diagnostic.reason == "unsupported_feature"


def test_duplicate_top_level_key_is_reported_as_a_duplicate_key() -> None:
    text = "---\nstatus: proposed\nstatus: closed\nparent: index.md\n---\n正文\n"

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert not result.is_valid
    assert result.metadata is None
    assert len(result.diagnostics) == 1
    diagnostic = result.diagnostics[0]
    assert diagnostic.code == CheckCode.PAGE_FRONTMATTER_INVALID.value
    assert diagnostic.reason == "duplicate_key"


def test_equivalent_integer_spellings_report_the_second_key_as_duplicate() -> None:
    text = "---\n1: first\n01: second\nstatus: proposed\nparent: index.md\n---\n正文\n"

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert not result.is_valid
    duplicate = [item for item in result.diagnostics if item.reason == "duplicate_key"]
    assert len(duplicate) == 1
    assert duplicate[0].span is not None
    assert text[duplicate[0].span.start : duplicate[0].span.end] == "01"
    assert sum(item.reason == "unknown_key" for item in result.diagnostics) == 1


@pytest.mark.parametrize(
    ("first_key", "second_key"),
    [
        ("0o13", "0xB"),
        ("true", "True"),
        ("null", "~"),
        (".nan", ".NaN"),
        (".inf", "+.Inf"),
        ("0.0", "-0.0"),
        ("1.0", "1.00"),
        ("1e3", "1000.0"),
        ("foo", '"foo"'),
        ("=", "="),
    ],
)
def test_equivalent_yaml_scalar_spellings_use_typed_key_identity(
    first_key: str, second_key: str
) -> None:
    text = (
        f"---\n{first_key}: first\n{second_key}: second\n"
        "status: proposed\nparent: index.md\n---\n正文\n"
    )

    result = parse_frontmatter(text, Path("pages/topic.md"))

    duplicate = [item for item in result.diagnostics if item.reason == "duplicate_key"]
    assert len(duplicate) == 1
    assert duplicate[0].span is not None
    assert text[duplicate[0].span.start : duplicate[0].span.end] == second_key
    assert sum(item.reason == "unknown_key" for item in result.diagnostics) == 1


@pytest.mark.parametrize(
    ("first_key", "second_key"),
    [("1", '"1"'), ("true", '"true"'), (".nan", '".nan"')],
)
def test_distinct_yaml_scalar_tags_are_not_duplicate_keys(first_key: str, second_key: str) -> None:
    text = (
        f"---\n{first_key}: first\n{second_key}: second\n"
        "status: proposed\nparent: index.md\n---\n正文\n"
    )

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert not any(item.reason == "duplicate_key" for item in result.diagnostics)
    assert sum(item.reason == "unknown_key" for item in result.diagnostics) == 2


@pytest.mark.parametrize(
    ("first_key", "second_key"),
    [
        ("2001-12-15T02:59:43.1Z", "2001-12-14t21:59:43.10-05:00"),
        ("2001-12-15", "2001-12-15T00:00:00Z"),
    ],
)
def test_equivalent_yaml_timestamps_use_utc_key_identity(first_key: str, second_key: str) -> None:
    text = (
        f"---\n{first_key}: first\n{second_key}: second\n"
        "status: proposed\nparent: index.md\n---\n正文\n"
    )

    result = parse_frontmatter(text, Path("pages/topic.md"))

    duplicate = [item for item in result.diagnostics if item.reason == "duplicate_key"]
    assert len(duplicate) == 1
    assert duplicate[0].span is not None
    assert text[duplicate[0].span.start : duplicate[0].span.end] == second_key


def test_timestamp_fraction_beyond_datetime_precision_remains_distinct() -> None:
    text = (
        "---\n2001-12-15T00:00:00.12345671Z: first\n"
        "2001-12-15T00:00:00.12345679Z: second\n"
        "status: proposed\nparent: index.md\n---\n正文\n"
    )

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert not any(item.reason == "duplicate_key" for item in result.diagnostics)
    assert sum(item.reason == "unknown_key" for item in result.diagnostics) == 2


def test_invalid_timestamp_cannot_fall_back_to_unknown_key_text() -> None:
    text = "---\n2025-99-99: value\nstatus: proposed\nparent: index.md\n---\n正文\n"

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert result.diagnostics[0].code == CheckCode.PAGE_FRONTMATTER_INVALID.value
    assert result.diagnostics[0].reason == "yaml_invalid"
    assert result.diagnostics[0].span is not None
    assert text[result.diagnostics[0].span.start : result.diagnostics[0].span.end] == "2025-99-99"


def test_equal_sequence_mapping_keys_keep_sequence_order_in_identity() -> None:
    text = (
        "---\n[a, 1, true]: first\n[a, 1, true]: second\n"
        "status: proposed\nparent: index.md\n---\n正文\n"
    )

    result = parse_frontmatter(text, Path("pages/topic.md"))

    duplicate = [item for item in result.diagnostics if item.reason == "duplicate_key"]
    assert len(duplicate) == 1
    assert duplicate[0].span is not None
    assert text[duplicate[0].span.start : duplicate[0].span.end] == "[a, 1, true]"


def test_sequence_key_order_changes_identity() -> None:
    text = "---\n[a, b]: first\n[b, a]: second\nstatus: proposed\nparent: index.md\n---\n正文\n"

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert not any(item.reason == "duplicate_key" for item in result.diagnostics)
    assert sum(item.reason == "unknown_key" for item in result.diagnostics) == 2


def test_mapping_key_entry_order_does_not_change_identity() -> None:
    text = (
        "---\n{a: 1, b: 2}: first\n{b: 2, a: 1}: second\n"
        "status: proposed\nparent: index.md\n---\n正文\n"
    )

    result = parse_frontmatter(text, Path("pages/topic.md"))

    duplicate = [item for item in result.diagnostics if item.reason == "duplicate_key"]
    assert len(duplicate) == 1
    assert duplicate[0].span is not None
    assert text[duplicate[0].span.start : duplicate[0].span.end] == "{b: 2, a: 1}"


def test_mapping_key_value_changes_identity() -> None:
    text = (
        "---\n{a: 1, b: 2}: first\n{a: 1, b: 3}: second\n"
        "status: proposed\nparent: index.md\n---\n正文\n"
    )

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert not any(item.reason == "duplicate_key" for item in result.diagnostics)
    assert sum(item.reason == "unknown_key" for item in result.diagnostics) == 2


def test_unknown_top_level_key_is_reported_without_partial_metadata() -> None:
    text = "---\nstatus: proposed\nparent: index.md\nextra: value\n---\n正文\n"

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert not result.is_valid
    assert result.metadata is None
    assert len(result.diagnostics) == 1
    diagnostic = result.diagnostics[0]
    assert diagnostic.code == CheckCode.PAGE_FRONTMATTER_INVALID.value
    assert diagnostic.reason == "unknown_key"


def test_non_scalar_top_level_key_is_reported_as_an_unknown_key() -> None:
    text = "---\n? [extra]\n: value\nstatus: proposed\nparent: index.md\n---\n正文\n"

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert not result.is_valid
    assert result.metadata is None
    assert len(result.diagnostics) == 1
    diagnostic = result.diagnostics[0]
    assert diagnostic.code == CheckCode.PAGE_FRONTMATTER_INVALID.value
    assert diagnostic.reason == "unknown_key"
    assert diagnostic.key_name is None
    assert diagnostic.span is not None
    assert text[diagnostic.span.start : diagnostic.span.end] == "[extra]"


def test_unknown_key_keeps_its_authenticated_name_and_source_location() -> None:
    text = "---\nstatus: proposed\n产出: value\nparent: index.md\n---\n正文\n"

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert not result.is_valid
    assert len(result.diagnostics) == 1
    diagnostic = result.diagnostics[0]
    assert diagnostic.key_name == "产出"
    assert diagnostic.line == 3
    assert diagnostic.raw_line == "产出: value"
    assert diagnostic.span is not None
    assert text[diagnostic.span.start : diagnostic.span.end] == "产出"


def test_duplicate_nested_relation_key_is_reported_as_a_duplicate_key() -> None:
    text = (
        "---\nstatus: proposed\nparent: index.md\nrelations:\n"
        "  - type: depends_on\n    type: challenges\n    target: pages/t.md\n---\n正文\n"
    )

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert not result.is_valid
    assert result.metadata is None
    assert len(result.diagnostics) == 1
    diagnostic = result.diagnostics[0]
    assert diagnostic.code == CheckCode.PAGE_FRONTMATTER_INVALID.value
    assert diagnostic.reason == "duplicate_key"


def test_nested_mapping_duplicate_reports_inner_key_and_suppresses_member_fields() -> None:
    text = (
        "---\nstatus: proposed\nparent: index.md\nrelations:\n"
        "  - type: depends_on\n    target: pages/t.md\n"
        "    ? {a: 1, a: 2}\n    : value\n---\n正文\n"
    )

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert not result.is_valid
    duplicate = [item for item in result.diagnostics if item.reason == "duplicate_key"]
    assert len(duplicate) == 1
    assert duplicate[0].span is not None
    assert text[duplicate[0].span.start : duplicate[0].span.end] == "a"
    assert not any(item.reason == "member_shape" for item in result.diagnostics)
    assert not any(
        item.reason in {"type_unknown", "target_path_invalid"} for item in result.diagnostics
    )


def test_duplicate_non_scalar_top_level_key_is_detected() -> None:
    text = (
        "---\n? [extra]\n: first\n? [extra]\n: second\n"
        "status: proposed\nparent: index.md\n---\n正文\n"
    )

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert not result.is_valid
    assert result.metadata is None
    assert {(item.code, item.reason) for item in result.diagnostics} == {
        (CheckCode.PAGE_FRONTMATTER_INVALID.value, "duplicate_key"),
        (CheckCode.PAGE_FRONTMATTER_INVALID.value, "unknown_key"),
    }
    duplicate = next(item for item in result.diagnostics if item.reason == "duplicate_key")
    assert duplicate.key_name is None


def test_independent_top_level_nodes_keep_separate_diagnostics() -> None:
    text = '---\nextra: value\nnote: ""\n---\n正文\n'

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert not result.is_valid
    assert result.metadata is None
    assert {(item.code, item.reason) for item in result.diagnostics} == {
        (CheckCode.PAGE_FRONTMATTER_INVALID.value, "unknown_key"),
        (CheckCode.PAGE_STATUS_INVALID.value, "missing"),
        (CheckCode.PAGE_NOTE_INVALID.value, "empty"),
        (CheckCode.PAGE_PARENT_INVALID.value, "missing"),
    }


def test_authenticated_metadata_spans_keep_cr_only_and_utf8_source_identity() -> None:
    text = "---\rstatus: proposed\rparent: pages/父 ?#%.md\r---\r# 标题\r正文 café\r"

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert result.is_valid
    assert result.metadata is not None
    assert result.body_start is not None
    assert result.metadata.status_span is not None
    assert result.metadata.parent_span is not None
    status_text = text[result.metadata.status_span.start : result.metadata.status_span.end]
    parent_text = text[result.metadata.parent_span.start : result.metadata.parent_span.end]
    assert status_text == "proposed"
    assert parent_text == "pages/父 ?#%.md"
    assert parent_text.encode() == "pages/父 ?#%.md".encode()
    assert result.metadata.parent_line == 3
    body = text[result.body_start :]
    assert body == "# 标题\r正文 café\r"
    assert body.encode() == "# 标题\r正文 café\r".encode()


def test_markdown_shaped_header_text_stays_outside_the_authenticated_body() -> None:
    text = (
        '---\nstatus: proposed\nnote: "[头部](pages/head.md)"\n---\n# 正文\n[正文](pages/body.md)\n'
    )

    result = parse_frontmatter(text, Path("index.md"))

    assert result.is_valid
    assert result.metadata is not None
    assert result.metadata.note == "[头部](pages/head.md)"
    assert result.body_start is not None
    assert text[: result.body_start].endswith("---\n")
    assert text[result.body_start :] == "# 正文\n[正文](pages/body.md)\n"


def test_feature_occurrences_accumulate_without_masking_independent_fields() -> None:
    text = (
        '---\n# 注释\nstatus: &state invalid\nparent: *state\nnote: ""\nextra: value\n---\n正文\n'
    )

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert result.metadata is None
    assert [(item.code, item.reason) for item in result.diagnostics] == [
        (CheckCode.PAGE_FRONTMATTER_INVALID.value, "unsupported_feature"),
        (CheckCode.PAGE_FRONTMATTER_INVALID.value, "unsupported_feature"),
        (CheckCode.PAGE_FRONTMATTER_INVALID.value, "unsupported_feature"),
        (CheckCode.PAGE_NOTE_INVALID.value, "empty"),
        (CheckCode.PAGE_FRONTMATTER_INVALID.value, "unknown_key"),
    ]
    feature_text = [
        text[item.span.start : item.span.end]
        for item in result.diagnostics
        if item.reason == "unsupported_feature" and item.span is not None
    ]
    assert feature_text[0].startswith("# 注释")
    assert feature_text[1:] == ["&state", "*state"]
    assert not any(item.reason == "invalid_value" for item in result.diagnostics)
    assert not any(item.reason == "missing" for item in result.diagnostics)


def test_relation_value_feature_blocks_only_that_value_and_keeps_target_validation() -> None:
    text = (
        "---\nstatus: proposed\nparent: index.md\nrelations:\n"
        "  - type: !unsupported unknown\n    target: not-a-page\n---\n正文\n"
    )

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert result.metadata is None
    assert [(item.code, item.reason) for item in result.diagnostics] == [
        (CheckCode.PAGE_FRONTMATTER_INVALID.value, "unsupported_feature"),
        (CheckCode.RELATION_DECLARATION_INVALID.value, "target_path_invalid"),
    ]
    assert result.diagnostics[0].span is not None
    assert text[result.diagnostics[0].span.start : result.diagnostics[0].span.end] == "!unsupported"
    assert not any(item.reason == "type_unknown" for item in result.diagnostics)


def test_complex_unknown_key_pair_order_keeps_first_unknown_and_second_duplicate_distinct() -> None:
    text = (
        "---\n? [extra]\n: first\n? [extra]\n: second\n"
        "status: proposed\nparent: index.md\n---\n正文\n"
    )

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert [(item.code, item.reason) for item in result.diagnostics] == [
        (CheckCode.PAGE_FRONTMATTER_INVALID.value, "unknown_key"),
        (CheckCode.PAGE_FRONTMATTER_INVALID.value, "duplicate_key"),
    ]
    assert all(item.span is not None for item in result.diagnostics)
    assert [text[item.span.start : item.span.end] for item in result.diagnostics if item.span] == [
        "[extra]",
        "[extra]",
    ]


def test_anchor_and_alias_occurrences_have_distinct_source_spans_without_recursion() -> None:
    text = "---\nstatus: &state proposed\nparent: *state\n---\n正文\n"

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert result.metadata is None
    assert [(item.code, item.reason) for item in result.diagnostics] == [
        (CheckCode.PAGE_FRONTMATTER_INVALID.value, "unsupported_feature"),
        (CheckCode.PAGE_FRONTMATTER_INVALID.value, "unsupported_feature"),
    ]
    assert all(item.span is not None for item in result.diagnostics)
    assert [text[item.span.start : item.span.end] for item in result.diagnostics if item.span] == [
        "&state",
        "*state",
    ]


def test_multiple_ordinary_field_errors_accumulate_before_metadata_is_withheld() -> None:
    text = '---\nstatus: unknown\nnote: ""\nparent: not-a-page\nrelations: []\n---\n正文\n'

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert result.metadata is None
    assert [(item.code, item.reason) for item in result.diagnostics] == [
        (CheckCode.PAGE_STATUS_INVALID.value, "invalid_value"),
        (CheckCode.PAGE_NOTE_INVALID.value, "empty"),
        (CheckCode.PAGE_PARENT_INVALID.value, "invalid_path"),
        (CheckCode.RELATION_DECLARATION_INVALID.value, "container_empty"),
    ]


def test_comment_is_trivia_and_does_not_block_independent_field_consumers() -> None:
    text = "---\n# 注释\nstatus: unknown\n---\n正文\n"

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert result.metadata is None
    assert {(item.code, item.reason) for item in result.diagnostics} == {
        (CheckCode.PAGE_FRONTMATTER_INVALID.value, "unsupported_feature"),
        (CheckCode.PAGE_STATUS_INVALID.value, "invalid_value"),
        (CheckCode.PAGE_PARENT_INVALID.value, "missing"),
    }


def test_nested_relation_structure_blocks_that_member_but_not_its_sibling() -> None:
    text = (
        "---\nstatus: proposed\nparent: index.md\nrelations:\n"
        "  - type: depends_on\n    target: pages/first.md\n"
        "    ? {a: 1, a: 2}\n    : value\n"
        "  - type: unknown\n    target: not-a-page\n---\n正文\n"
    )

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert result.metadata is None
    assert {(item.code, item.reason) for item in result.diagnostics} == {
        (CheckCode.PAGE_FRONTMATTER_INVALID.value, "duplicate_key"),
        (CheckCode.RELATION_DECLARATION_INVALID.value, "type_unknown"),
        (CheckCode.RELATION_DECLARATION_INVALID.value, "target_path_invalid"),
    }
    assert not any(item.reason == "member_shape" for item in result.diagnostics)


def test_deep_complex_unknown_key_uses_a_non_recursive_worklist() -> None:
    depth = 500
    text = (
        "---\n? "
        + "[" * depth
        + "extra"
        + "]" * depth
        + ": value\nstatus: proposed\nparent: index.md\n---\n正文\n"
    )

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert result.metadata is None
    assert [(item.code, item.reason) for item in result.diagnostics] == [
        (CheckCode.PAGE_FRONTMATTER_INVALID.value, "unknown_key")
    ]


def test_anchor_alias_cycle_terminates_with_each_source_feature_owned_once() -> None:
    text = "---\nloop: &loop {self: *loop}\nstatus: proposed\nparent: index.md\n---\n正文\n"

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert result.metadata is None
    assert {(item.code, item.reason) for item in result.diagnostics} == {
        (CheckCode.PAGE_FRONTMATTER_INVALID.value, "unknown_key"),
        (CheckCode.PAGE_FRONTMATTER_INVALID.value, "unsupported_feature"),
    }
    feature_text = [
        text[item.span.start : item.span.end]
        for item in result.diagnostics
        if item.reason == "unsupported_feature" and item.span is not None
    ]
    assert feature_text == ["&loop", "*loop"]


def test_root_parent_alias_reports_parent_unexpected_without_consuming_the_alias_value() -> None:
    text = "---\nstatus: proposed\nnote: &parent note\nparent: *parent\n---\n正文\n"

    result = parse_frontmatter(text, Path("index.md"))

    assert result.metadata is None
    assert {(item.code, item.reason) for item in result.diagnostics} == {
        (CheckCode.PAGE_FRONTMATTER_INVALID.value, "unsupported_feature"),
        (CheckCode.PAGE_PARENT_INVALID.value, "unexpected"),
    }
    assert sum(item.reason == "unsupported_feature" for item in result.diagnostics) == 2
    assert not any(
        item.code == CheckCode.PAGE_PARENT_INVALID.value
        and item.reason in {"missing", "invalid_path"}
        for item in result.diagnostics
    )


def test_non_root_parent_alias_is_not_reclassified_as_missing_or_invalid_path() -> None:
    text = "---\nstatus: &parent proposed\nparent: *parent\n---\n正文\n"

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert result.metadata is None
    assert [(item.code, item.reason) for item in result.diagnostics] == [
        (CheckCode.PAGE_FRONTMATTER_INVALID.value, "unsupported_feature"),
        (CheckCode.PAGE_FRONTMATTER_INVALID.value, "unsupported_feature"),
    ]
    assert not any(item.code == CheckCode.PAGE_PARENT_INVALID.value for item in result.diagnostics)


def test_tag_and_anchor_on_one_relation_value_emit_only_the_earliest_feature() -> None:
    text = (
        "---\nstatus: proposed\nparent: index.md\nrelations:\n"
        "  - type: !kind &relation depends_on\n    target: pages/target.md\n---\n正文\n"
    )

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert result.metadata is None
    assert [(item.code, item.reason) for item in result.diagnostics] == [
        (CheckCode.PAGE_FRONTMATTER_INVALID.value, "unsupported_feature")
    ]
    (diagnostic,) = result.diagnostics
    assert diagnostic.span is not None
    assert text[diagnostic.span.start : diagnostic.span.end] == "!kind"


@pytest.mark.parametrize(
    ("page_path", "before", "after", "expected"),
    [
        (
            "pages/topic.md",
            "status: invalid\nparent: index.md\n",
            "!unsafe status: proposed\n",
            {
                (CheckCode.PAGE_FRONTMATTER_INVALID.value, "unsupported_feature"),
                (CheckCode.PAGE_STATUS_INVALID.value, "invalid_value"),
            },
        ),
        (
            "pages/topic.md",
            'status: proposed\nnote: ""\nparent: index.md\n',
            "!unsafe note: ignored\n",
            {
                (CheckCode.PAGE_FRONTMATTER_INVALID.value, "unsupported_feature"),
                (CheckCode.PAGE_NOTE_INVALID.value, "empty"),
            },
        ),
        (
            "pages/topic.md",
            "status: proposed\nparent: not-a-page\n",
            "!unsafe parent: index.md\n",
            {
                (CheckCode.PAGE_FRONTMATTER_INVALID.value, "unsupported_feature"),
                (CheckCode.PAGE_PARENT_INVALID.value, "invalid_path"),
            },
        ),
        (
            "pages/topic.md",
            "status: proposed\nparent: index.md\nrelations: []\n",
            "!unsafe relations: []\n",
            {
                (CheckCode.PAGE_FRONTMATTER_INVALID.value, "unsupported_feature"),
                (CheckCode.RELATION_DECLARATION_INVALID.value, "container_empty"),
            },
        ),
        (
            "index.md",
            "status: proposed\nparent: index.md\n",
            "!unsafe parent: ignored\n",
            {
                (CheckCode.PAGE_FRONTMATTER_INVALID.value, "unsupported_feature"),
                (CheckCode.PAGE_PARENT_INVALID.value, "unexpected"),
            },
        ),
    ],
)
def test_untrusted_same_named_key_does_not_mask_authenticated_field_diagnostics(
    page_path: str,
    before: str,
    after: str,
    expected: set[tuple[str, str]],
) -> None:
    for yaml_body in (before + after, after + before):
        result = parse_frontmatter(f"---\n{yaml_body}---\n正文\n", Path(page_path))

        assert result.metadata is None
        assert {(item.code, item.reason) for item in result.diagnostics} == expected


def test_anchor_key_does_not_mask_an_authenticated_status_diagnostic() -> None:
    text = "---\nstatus: invalid\n&unsafe status: proposed\nparent: index.md\n---\n正文\n"

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert result.metadata is None
    assert {(item.code, item.reason) for item in result.diagnostics} == {
        (CheckCode.PAGE_FRONTMATTER_INVALID.value, "unsupported_feature"),
        (CheckCode.PAGE_STATUS_INVALID.value, "invalid_value"),
    }


def test_only_untrusted_same_named_key_keeps_the_field_ambiguous_without_missing() -> None:
    text = "---\n!unsafe status: proposed\nparent: index.md\n---\n正文\n"

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert result.metadata is None
    assert {(item.code, item.reason) for item in result.diagnostics} == {
        (CheckCode.PAGE_FRONTMATTER_INVALID.value, "unsupported_feature"),
    }


def test_value_blocked_parent_remains_unexpected_after_an_untrusted_same_named_key() -> None:
    text = "---\nstatus: proposed\nparent: !unsafe index.md\n!other parent: ignored\n---\n正文\n"

    result = parse_frontmatter(text, Path("index.md"))

    assert result.metadata is None
    assert {(item.code, item.reason) for item in result.diagnostics} == {
        (CheckCode.PAGE_FRONTMATTER_INVALID.value, "unsupported_feature"),
        (CheckCode.PAGE_PARENT_INVALID.value, "unexpected"),
    }
    assert sum(item.reason == "unsupported_feature" for item in result.diagnostics) == 2


def test_authenticated_duplicate_remains_ambiguous_with_an_untrusted_same_named_key() -> None:
    text = (
        "---\nstatus: invalid\nstatus: proposed\n!unsafe status: closed\n"
        "parent: index.md\n---\n正文\n"
    )

    result = parse_frontmatter(text, Path("pages/topic.md"))

    assert result.metadata is None
    assert {(item.code, item.reason) for item in result.diagnostics} == {
        (CheckCode.PAGE_FRONTMATTER_INVALID.value, "duplicate_key"),
        (CheckCode.PAGE_FRONTMATTER_INVALID.value, "unsupported_feature"),
    }
    assert not any(item.code == CheckCode.PAGE_STATUS_INVALID.value for item in result.diagnostics)
