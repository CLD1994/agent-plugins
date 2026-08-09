from __future__ import annotations

from pathlib import Path

import pytest

from draft.errors import DraftError
from draft.markdown import parse_page
from draft.page_metadata import replace_status
from draft.vocabulary import StatusCode

PAGE = "# 标题\n\n> 状态：`待讨论`\n>\n> 父主题：[根](../index.md)\n\n正文。\n"


def test_replace_status_changes_code_and_keeps_rest() -> None:
    result = replace_status(PAGE, StatusCode.CONFIRMED, None)
    parsed = parse_page(Path("pages/a.md"), result)
    assert parsed.status.code is StatusCode.CONFIRMED
    assert parsed.status.explanation is None
    assert "父主题：[根](../index.md)" in result
    assert "正文。" in result


def test_replace_status_sets_explanation() -> None:
    result = replace_status(PAGE, StatusCode.SUPERSEDED, "旧方案")
    parsed = parse_page(Path("pages/a.md"), result)
    assert parsed.status.code is StatusCode.SUPERSEDED
    assert parsed.status.explanation == "旧方案"


def test_replace_status_keeps_existing_explanation_when_none() -> None:
    text = PAGE.replace("`待讨论`", "`已关闭：历史记录`")
    result = replace_status(text, StatusCode.PENDING, None)
    parsed = parse_page(Path("pages/a.md"), result)
    assert parsed.status.code is StatusCode.PENDING
    assert parsed.status.explanation == "历史记录"


def test_replace_status_clear_note_removes_explanation() -> None:
    text = PAGE.replace("`待讨论`", "`已关闭：历史记录`")
    result = replace_status(text, StatusCode.PENDING, "")
    parsed = parse_page(Path("pages/a.md"), result)
    assert parsed.status.code is StatusCode.PENDING
    assert parsed.status.explanation is None


def test_replace_status_rejects_control_chars_in_explanation() -> None:
    with pytest.raises(DraftError, match="status.invalid_note"):
        replace_status(PAGE, StatusCode.CONFIRMED, "换行\n标题")
