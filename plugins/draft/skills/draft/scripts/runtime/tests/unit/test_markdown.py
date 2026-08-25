from pathlib import Path

import pytest

from draft.diagnostic_codes import (
    CheckCode,
    InvalidRelationReason,
    InvalidStatusReason,
    ParseErrorReason,
)
from draft.errors import DraftError
from draft.markdown import (
    append_parent_navigation,
    extract_body_links,
    locate_status_value_span,
    parse_page,
    remove_link_markup,
    remove_parent_navigation,
    rewrite_link_destinations,
)
from draft.models import MarkdownLink, SourceSpan
from draft.vocabulary import RelationCode, StatusCode, status_code

VALID_PAGE = """# 请求身份

> 状态：`已关闭：旧方案，仅保留历史`
>
> 父主题：[根索引](../index.md)
>
> 依赖：[页面模型](page-model.md#边界)

正文中的 [普通链接](other.md) 保持可见。

```markdown
> 状态：`当前提案`
[示例断链](missing.md)
```
"""


def test_parse_page_reads_only_the_top_machine_block() -> None:
    page = parse_page(Path("pages/request.md"), VALID_PAGE)

    assert page.title == "请求身份"
    assert page.status.code is StatusCode.CLOSED
    assert page.status.explanation == "旧方案，仅保留历史"
    assert [relation.code for relation in page.relations] == [
        RelationCode.PARENT,
        RelationCode.DEPENDS_ON,
    ]
    assert {link.destination for link in page.links} == {
        "../index.md",
        "page-model.md#边界",
        "other.md",
    }


def test_parse_page_rejects_unknown_machine_label() -> None:
    text = VALID_PAGE.replace("> 依赖：", "> 状态依据：")

    with pytest.raises(DraftError, match=CheckCode.PAGE_UNKNOWN_LABEL.value):
        parse_page(Path("pages/request.md"), text)


def test_status_value_colon_is_not_confused_with_label_colon() -> None:
    page = parse_page(Path("pages/request.md"), VALID_PAGE)

    assert page.status.code is StatusCode.CLOSED
    assert page.status.explanation == "旧方案，仅保留历史"


def test_rewrite_changes_only_destination_and_preserves_anchor_and_examples() -> None:
    page = parse_page(Path("pages/request.md"), VALID_PAGE)
    target = next(link for link in page.links if link.destination == "other.md")

    rewritten = rewrite_link_destinations(VALID_PAGE, {target.destination_span: "renamed.md"})

    assert "[普通链接](renamed.md)" in rewritten
    assert "[示例断链](missing.md)" in rewritten
    assert rewritten.replace("renamed.md", "other.md", 1) == VALID_PAGE


def make_page(status_value: str) -> str:
    return "# 标题\n\n> 状态：" + status_value + "\n>\n> 父主题：[根](../index.md)\n\n正文。\n"


@pytest.mark.parametrize("label", ["待讨论", "当前提案", "已确认", "已取代", "已关闭"])
def test_exact_five_status_labels(label: str) -> None:
    page = parse_page(Path("pages/x.md"), make_page(f"`{label}`"))

    assert page.status.code is status_code(label)
    assert page.status.explanation is None


def test_status_explanation_must_be_nonempty_after_colon() -> None:
    with pytest.raises(DraftError, match=CheckCode.PAGE_INVALID_STATUS.value):
        parse_page(Path("pages/x.md"), make_page("`已关闭：`"))


def test_unknown_status_prefix_rejected() -> None:
    with pytest.raises(DraftError, match=CheckCode.PAGE_INVALID_STATUS.value):
        parse_page(Path("pages/x.md"), make_page("`进行中`"))


def test_duplicate_status_rejected() -> None:
    text = "# 标题\n\n> 状态：`待讨论`\n>\n> 状态：`已确认`\n>\n> 父主题：[根](../index.md)\n\nx\n"

    with pytest.raises(DraftError, match=CheckCode.PAGE_DUPLICATE_STATUS.value):
        parse_page(Path("pages/x.md"), text)


def test_missing_status_rejected() -> None:
    text = "# 标题\n\n> 父主题：[根](../index.md)\n\n正文。\n"

    with pytest.raises(DraftError, match=CheckCode.PAGE_MISSING_STATUS.value):
        parse_page(Path("pages/x.md"), text)


def test_missing_level_one_title_rejected() -> None:
    text = "正文先行。\n\n> 状态：`待讨论`\n"

    with pytest.raises(DraftError, match=CheckCode.PAGE_MISSING_TITLE.value):
        parse_page(Path("pages/x.md"), text)


def test_machine_block_after_body_text_rejected() -> None:
    text = "# 标题\n\n正文段落。\n\n> 状态：`待讨论`\n>\n> 父主题：[根](../index.md)\n"

    with pytest.raises(DraftError, match=CheckCode.PAGE_MISSING_MACHINE_BLOCK.value):
        parse_page(Path("pages/x.md"), text)


def test_relation_requires_exactly_one_link() -> None:
    two_links = make_page("`待讨论`").replace(
        "> 父主题：[根](../index.md)",
        "> 依赖：[甲](a.md) 与 [乙](b.md)",
    )
    with pytest.raises(DraftError, match=CheckCode.PAGE_INVALID_RELATION.value):
        parse_page(Path("pages/x.md"), two_links)

    image_link = make_page("`待讨论`").replace(
        "> 父主题：[根](../index.md)",
        "> 依赖：![图](a.md)",
    )
    with pytest.raises(DraftError, match=CheckCode.PAGE_INVALID_RELATION.value):
        parse_page(Path("pages/x.md"), image_link)

    no_link = make_page("`待讨论`").replace(
        "> 父主题：[根](../index.md)",
        "> 依赖：另一页",
    )
    with pytest.raises(DraftError, match=CheckCode.PAGE_INVALID_RELATION.value):
        parse_page(Path("pages/x.md"), no_link)


def test_repeated_relation_lines_are_preserved() -> None:
    text = make_page("`待讨论`").replace(
        "正文。\n",
        "> 依赖：[甲](a.md)\n>\n> 依赖：[乙](b.md)\n\n正文。\n",
    )
    # 依赖 lines must live inside the top machine block, not the body.
    text = (
        "# 标题\n\n> 状态：`待讨论`\n>\n> 父主题：[根](../index.md)\n>\n"
        "> 依赖：[甲](a.md)\n>\n> 依赖：[乙](b.md)\n\n正文。\n"
    )

    page = parse_page(Path("pages/x.md"), text)

    assert [relation.code for relation in page.relations] == [
        RelationCode.PARENT,
        RelationCode.DEPENDS_ON,
        RelationCode.DEPENDS_ON,
    ]
    assert [relation.link.destination for relation in page.relations] == [
        "../index.md",
        "a.md",
        "b.md",
    ]


def test_inline_destination_balanced_parens_and_angle_brackets() -> None:
    text = make_page("`待讨论`").replace(
        "正文。",
        "[甲](with(paren).md) 与 [乙](<with space.md>) 与 [丙](a(b(c)).md)。",
    )

    page = parse_page(Path("pages/x.md"), text)
    destinations = {link.destination for link in page.links}

    assert {"with(paren).md", "with space.md", "a(b(c)).md"} <= destinations


REF_PAGE = """# 标题

> 状态：`待讨论`
>
> 父主题：[根](../index.md)

[完整][ref] 与 [ref][] 与 [ref] 都指向同一个定义。

[ref]: target.md "标题"
"""


def test_reference_usages_point_to_definition_destination_span() -> None:
    page = parse_page(Path("pages/x.md"), REF_PAGE)
    usages = [link for link in page.links if link.destination == "target.md"]

    assert len(usages) == 3
    spans = {link.destination_span for link in usages}
    assert len(spans) == 1
    (definition_span,) = spans
    assert REF_PAGE[definition_span.start : definition_span.end] == "target.md"
    assert all(link.reference_label is not None for link in usages)

    rewritten = rewrite_link_destinations(REF_PAGE, {definition_span: "renamed.md"})

    assert "[ref]: renamed.md " in rewritten
    assert "[完整][ref]" in rewritten
    assert "target.md" not in rewritten


MIXED_PAGE = """# 标题

> 状态：`待讨论`
>
> 父主题：[根](../index.md)

行内代码 `[不是链接](fake.md)` 与 [真链接](real.md) 以及 <https://example.com/auto>。

![图片](image.png) 与 [外部](https://example.com/x)。
"""


def test_inline_code_images_autolinks_and_external_urls() -> None:
    page = parse_page(Path("pages/x.md"), MIXED_PAGE)
    by_destination = {link.destination: link for link in page.links}

    assert "fake.md" not in by_destination
    assert "real.md" in by_destination
    assert "https://example.com/x" in by_destination
    assert "https://example.com/auto" in by_destination
    assert by_destination["image.png"].is_image
    assert not by_destination["real.md"].is_image


def test_crlf_input_preserves_crlf_after_rewrite() -> None:
    text = (
        "# 标题\r\n\r\n> 状态：`待讨论`\r\n>\r\n> 父主题：[根](../index.md)\r\n"
        "\r\n[正文](body.md)\r\n"
    )
    page = parse_page(Path("pages/x.md"), text)
    target = next(link for link in page.links if link.destination == "body.md")

    rewritten = rewrite_link_destinations(text, {target.destination_span: "new.md"})

    assert "[正文](new.md)\r\n" in rewritten
    assert rewritten.count("\r\n") == text.count("\r\n")


def test_multiple_edits_apply_from_highest_offset_without_corruption() -> None:
    text = make_page("`待讨论`").replace("正文。", "[甲](a.md) 然后 [乙](b.md)。")
    page = parse_page(Path("pages/x.md"), text)
    replacements = {
        link.destination_span: link.destination.upper()
        for link in page.links
        if link.destination in {"a.md", "b.md"}
    }

    rewritten = rewrite_link_destinations(text, replacements)

    assert "[甲](A.MD)" in rewritten
    assert "[乙](B.MD)" in rewritten


def test_overlapping_edits_rejected() -> None:
    from draft.models import SourceSpan

    with pytest.raises(DraftError, match="markdown.overlapping_edits"):
        rewrite_link_destinations(
            "abcdef",
            {SourceSpan(0, 3): "x", SourceSpan(2, 4): "y"},
        )


def test_remove_link_markup_replaces_whole_link_with_visible_label() -> None:
    text = make_page("`待讨论`").replace(
        "正文。", "见 [导航页](pages/a.md) 与 [第二页](pages/b.md)。"
    )
    page = parse_page(Path("pages/x.md"), text)
    navigation = next(link for link in page.links if link.destination == "pages/a.md")

    stripped = remove_link_markup(text, [navigation])

    assert "见 导航页 与 [第二页](pages/b.md)。" in stripped


def test_unknown_label_preserves_only_the_label_operand() -> None:
    text = VALID_PAGE.replace("> 依赖：", "> 状态依据：")
    with pytest.raises(DraftError) as caught:
        parse_page(Path("pages/request.md"), text)
    assert caught.value.code == CheckCode.PAGE_UNKNOWN_LABEL.value
    assert caught.value.line == 7
    assert caught.value.details == ("状态依据",)


def test_removed_output_label_is_reported_as_unknown() -> None:
    """旧 `产出：` 不再被识别为关系，而是按未知机器标签报错；这不是兼容路径。"""
    text = VALID_PAGE.replace(
        "> 依赖：[页面模型](page-model.md#边界)", "> 产出：[结论汇总](../conclusions.md)"
    )
    with pytest.raises(DraftError) as caught:
        parse_page(Path("pages/request.md"), text)
    assert caught.value.code == CheckCode.PAGE_UNKNOWN_LABEL.value
    assert caught.value.details == ("产出",)


@pytest.mark.parametrize(
    ("value", "details"),
    [
        ("已关闭", (InvalidStatusReason.NOT_SINGLE_CODE_SPAN.value,)),
        ("`已关闭：`", (InvalidStatusReason.EMPTY_EXPLANATION.value,)),
        ("`进行中`", (InvalidStatusReason.UNKNOWN_PREFIX.value, "进行中")),
    ],
)
def test_invalid_status_uses_stable_reason(value: str, details: tuple[str, ...]) -> None:
    with pytest.raises(DraftError) as caught:
        parse_page(Path("pages/x.md"), make_page(value))
    assert caught.value.code == CheckCode.PAGE_INVALID_STATUS.value
    assert caught.value.details == details


def test_invalid_relation_cardinality_branch_keeps_label() -> None:
    text = make_page("`待讨论`").replace(
        "> 父主题：[根](../index.md)", "> 依赖：[甲](a.md) 与 [乙](b.md)"
    )
    with pytest.raises(DraftError) as caught:
        parse_page(Path("pages/x.md"), text)
    assert caught.value.code == CheckCode.PAGE_INVALID_RELATION.value
    assert caught.value.details == (InvalidRelationReason.LINK_CARDINALITY_OR_IMAGE.value, "依赖")


def test_invalid_relation_extra_content_branch_keeps_label() -> None:
    text = make_page("`待讨论`").replace(
        "> 父主题：[根](../index.md)", "> 父主题：[根](../index.md) 额外"
    )
    with pytest.raises(DraftError) as caught:
        parse_page(Path("pages/x.md"), text)
    assert caught.value.code == CheckCode.PAGE_INVALID_RELATION.value
    assert caught.value.details == (InvalidRelationReason.EXTRA_CONTENT.value, "父主题")


def test_duplicate_status_uses_empty_details_with_line() -> None:
    text = "# 标题\n\n> 状态：`待讨论`\n>\n> 状态：`已确认`\n>\n> 父主题：[根](../index.md)\n\nx\n"
    with pytest.raises(DraftError) as caught:
        parse_page(Path("pages/x.md"), text)
    assert caught.value.code == CheckCode.PAGE_DUPLICATE_STATUS.value
    assert caught.value.details == ()
    assert caught.value.line == 5


def test_machine_block_lazy_continuation_is_a_parse_error() -> None:
    text = "# 标题\n\n> 状态：`待讨论`\n普通文本\n>\n> 父主题：[根](../index.md)\n"
    with pytest.raises(DraftError) as caught:
        parse_page(Path("pages/x.md"), text)
    assert caught.value.code == CheckCode.PAGE_PARSE_ERROR.value
    assert caught.value.details == (ParseErrorReason.LAZY_CONTINUATION.value,)
    assert caught.value.line == 4


# ---------------------------------------------------------------------------
# 导航字符串转换：append / remove / locate_status_value_span
# ---------------------------------------------------------------------------


def test_append_parent_navigation_creates_section_when_absent() -> None:
    text = "# 主题\n\n> 状态：`已确认`\n"
    assert append_parent_navigation(text, "子", "pages/sub.md") == (
        "# 主题\n\n> 状态：`已确认`\n\n## 子主题\n\n- [子](pages/sub.md)\n"
    )


def test_append_parent_navigation_appends_to_existing_section() -> None:
    text = "# 主题\n\n> 状态：`已确认`\n\n## 子主题\n\n- [a](a.md)\n"
    assert append_parent_navigation(text, "b", "b.md") == (
        "# 主题\n\n> 状态：`已确认`\n\n## 子主题\n\n- [a](a.md)\n- [b](b.md)\n"
    )


def test_append_parent_navigation_escapes_brackets_in_title() -> None:
    text = "# 主题\n\n> 状态：`已确认`\n"
    result = append_parent_navigation(text, "a[b]c", "x.md")
    assert "- [a\\[b\\]c](x.md)" in result


def test_remove_parent_navigation_removes_whole_list_item_line() -> None:
    text = "- [a](a.md)\n"
    link = MarkdownLink(
        label="a",
        destination="a.md",
        destination_span=SourceSpan(4, 9),
        whole_span=SourceSpan(2, 11),
        line=1,
    )
    assert remove_parent_navigation(text, link) == ""


def test_remove_parent_navigation_keeps_visible_label_when_not_list_item() -> None:
    text = "见 [a](a.md) 详情\n"
    link = MarkdownLink(
        label="a",
        destination="a.md",
        destination_span=SourceSpan(4, 9),
        whole_span=SourceSpan(2, 11),
        line=1,
    )
    assert remove_parent_navigation(text, link) == "见 a 详情\n"


def test_locate_status_value_span_includes_backticks() -> None:
    text = "# 标题\n\n> 状态：`已确认`\n"
    span = locate_status_value_span(text)
    assert text[span.start : span.end] == "`已确认`"


def test_locate_status_value_span_missing_status_is_rejected() -> None:
    text = "# 标题\n\n> 父主题：[根](../index.md)\n"
    with pytest.raises(DraftError) as caught:
        locate_status_value_span(text)
    assert caught.value.code == CheckCode.PAGE_MISSING_STATUS.value


def test_insert_navigation_before_conclusions_keeps_conclusions_last() -> None:
    from draft.markdown import insert_navigation_before_conclusions

    text = (
        "# 根\n\n> 状态：`已关闭`\n\n## 子主题\n\n"
        "- [甲](pages/a.md)\n- [结论汇总](conclusions.md)\n"
    )
    result = insert_navigation_before_conclusions(
        text, "乙", "pages/b.md", conclusions_target=Path("conclusions.md")
    )
    assert result.index("- [乙](pages/b.md)") < result.index("- [结论汇总](conclusions.md)")
    assert result.index("- [甲](pages/a.md)") < result.index("- [乙](pages/b.md)")


def test_insert_navigation_before_conclusions_no_conclusion_returns_unchanged() -> None:
    from draft.markdown import insert_navigation_before_conclusions

    text = "# 根\n\n> 状态：`已关闭`\n\n## 子主题\n\n- [甲](pages/a.md)\n"
    result = insert_navigation_before_conclusions(
        text, "乙", "pages/b.md", conclusions_target=Path("conclusions.md")
    )
    assert result == text


def test_extract_body_links_without_machine_block() -> None:
    text = "正文 [旧稿](00-background-and-evidence.md) 与 ![图](img/a.png)。\n"

    links = extract_body_links(text)

    assert [(link.destination, link.is_image) for link in links] == [
        ("00-background-and-evidence.md", False),
        ("img/a.png", True),
    ]
    assert links[0].destination_span == SourceSpan(
        text.index("00-background-and-evidence.md"),
        text.index("00-background-and-evidence.md") + len("00-background-and-evidence.md"),
    )


def test_extract_body_links_resolves_defined_reference_links() -> None:
    text = '看 [旧稿][legacy]。\n\n[legacy]: 00-bg.md "标题"\n'

    assert [link.destination for link in extract_body_links(text)] == ["00-bg.md"]


def test_extract_body_links_ignores_undefined_reference_forms() -> None:
    text = "看 [旧稿][legacy] 与 <00-bg.md>。\n"

    assert extract_body_links(text) == ()


def test_extract_body_links_alignment_failure_raises_parse_error() -> None:
    text = "看 [x](a\\.md)。\n"

    with pytest.raises(DraftError, match=CheckCode.PAGE_PARSE_ERROR.value):
        extract_body_links(text)
