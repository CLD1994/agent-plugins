"""验证 parser 与中央诊断投影的真实协作边界。"""

from pathlib import Path

from draft.diagnostic_codes import CheckCode
from draft.diagnostics import issue_payload
from draft.frontmatter import parse_frontmatter


def test_unknown_output_key_projects_only_registered_public_details() -> None:
    text = "---\nstatus: proposed\n产出: value\nparent: index.md\n---\n正文\n"

    parsed = parse_frontmatter(text, Path("pages/topic.md"))

    assert parsed.metadata is None
    (diagnostic,) = parsed.diagnostics
    payload = issue_payload(diagnostic.as_check_issue())

    assert payload["code"] == CheckCode.PAGE_FRONTMATTER_INVALID.value
    assert payload["severity"] == "error"
    assert payload["path"] == "pages/topic.md"
    assert payload["line"] == 3
    assert payload["details"] == {"reason": "unknown_key"}
    assert "产出" in str(payload["message"])


def test_non_scalar_unknown_key_does_not_leak_untrusted_yaml_content() -> None:
    text = "---\n? [extra]\n: value\nstatus: proposed\nparent: index.md\n---\n正文\n"

    parsed = parse_frontmatter(text, Path("pages/topic.md"))

    assert parsed.metadata is None
    (diagnostic,) = parsed.diagnostics
    payload = issue_payload(diagnostic.as_check_issue())

    assert payload["code"] == CheckCode.PAGE_FRONTMATTER_INVALID.value
    assert payload["details"] == {"reason": "unknown_key"}
    assert "extra" not in str(payload["message"])
    assert "value" not in str(payload["message"])


def test_empty_input_and_empty_header_do_not_invent_public_source_locations() -> None:
    empty = parse_frontmatter("", Path("index.md"))
    empty_header = parse_frontmatter("---\n---\n正文\n", Path("index.md"))

    for parsed in (empty, empty_header):
        assert parsed.metadata is None
        (diagnostic,) = parsed.diagnostics
        assert diagnostic.line is None
        assert diagnostic.span is None
        assert diagnostic.raw_line is None
        payload = issue_payload(diagnostic.as_check_issue())
        assert "line" not in payload
