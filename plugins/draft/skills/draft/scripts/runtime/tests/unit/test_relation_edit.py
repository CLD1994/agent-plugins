from __future__ import annotations

from pathlib import Path

from draft.markdown import parse_page
from draft.page_metadata import (
    append_machine_relation,
    remove_machine_relation,
)
from draft.vocabulary import RelationCode

PAGE = "# 标题\n\n> 状态：`待讨论`\n>\n> 父主题：[根](../index.md)\n\n正文。\n"


def test_append_machine_relation_adds_line_in_machine_block() -> None:
    result = append_machine_relation(
        PAGE, RelationCode.DEPENDS_ON, Path("pages/b.md"), source=Path("pages/a.md")
    )
    parsed = parse_page(Path("pages/a.md"), result)
    assert any(
        r.code.value == RelationCode.DEPENDS_ON.value and r.link.destination == "b.md"
        for r in parsed.relations
    )


def test_append_machine_relation_preserves_other_lines() -> None:
    result = append_machine_relation(
        PAGE, RelationCode.DEPENDS_ON, Path("pages/b.md"), source=Path("pages/a.md")
    )
    assert "父主题：[根](../index.md)" in result
    assert "正文。" in result


def test_remove_machine_relation_removes_only_target_line() -> None:
    text = PAGE.replace(
        "> 父主题：[根](../index.md)",
        "> 父主题：[根](../index.md)\n>\n> 依赖：[甲](a.md)\n>\n> 依赖：[乙](b.md)",
    )
    result = remove_machine_relation(
        text, RelationCode.DEPENDS_ON, Path("pages/a.md"), source=Path("pages/a.md")
    )
    parsed = parse_page(Path("pages/a.md"), result)
    deps = [r for r in parsed.relations if r.code.value == RelationCode.DEPENDS_ON.value]
    assert [r.link.destination for r in deps] == ["b.md"]
    assert "依赖：[甲](a.md)" not in result


def test_remove_machine_relation_does_not_touch_body_links() -> None:
    text = PAGE.replace("正文。", "见 [甲](a.md)。")
    result = remove_machine_relation(
        text, RelationCode.DEPENDS_ON, Path("pages/a.md"), source=Path("pages/a.md")
    )
    assert "见 [甲](a.md)。" in result
