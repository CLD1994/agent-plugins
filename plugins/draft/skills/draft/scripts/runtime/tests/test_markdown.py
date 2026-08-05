from pathlib import Path

import pytest
from draft.errors import DraftError
from draft.markdown import (
    parse_page,
    remove_link_markup,
    rewrite_link_destinations,
)
from draft.models import RelationLabel, StatusPrefix

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
    assert page.status.prefix is StatusPrefix.CLOSED
    assert page.status.explanation == "旧方案，仅保留历史"
    assert [relation.label for relation in page.relations] == [
        RelationLabel.PARENT,
        RelationLabel.DEPENDS_ON,
    ]
    assert {link.destination for link in page.links} == {
        "../index.md",
        "page-model.md#边界",
        "other.md",
    }


def test_parse_page_rejects_unknown_machine_label() -> None:
    text = VALID_PAGE.replace("> 依赖：", "> 状态依据：")

    with pytest.raises(DraftError, match="page.unknown_label"):
        parse_page(Path("pages/request.md"), text)


def test_status_value_colon_is_not_confused_with_label_colon() -> None:
    page = parse_page(Path("pages/request.md"), VALID_PAGE)

    assert page.status.prefix is StatusPrefix.CLOSED
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


@pytest.mark.parametrize("prefix", ["待讨论", "当前提案", "已确认", "已取代", "已关闭"])
def test_exact_five_status_prefixes(prefix: str) -> None:
    page = parse_page(Path("pages/x.md"), make_page(f"`{prefix}`"))

    assert page.status.prefix is StatusPrefix(prefix)
    assert page.status.explanation is None


def test_status_explanation_must_be_nonempty_after_colon() -> None:
    with pytest.raises(DraftError, match="page.invalid_status"):
        parse_page(Path("pages/x.md"), make_page("`已关闭：`"))


def test_unknown_status_prefix_rejected() -> None:
    with pytest.raises(DraftError, match="page.invalid_status"):
        parse_page(Path("pages/x.md"), make_page("`进行中`"))


def test_duplicate_status_rejected() -> None:
    text = "# 标题\n\n> 状态：`待讨论`\n>\n> 状态：`已确认`\n>\n> 父主题：[根](../index.md)\n\nx\n"

    with pytest.raises(DraftError, match="page.duplicate_status"):
        parse_page(Path("pages/x.md"), text)


def test_missing_status_rejected() -> None:
    text = "# 标题\n\n> 父主题：[根](../index.md)\n\n正文。\n"

    with pytest.raises(DraftError, match="page.missing_status"):
        parse_page(Path("pages/x.md"), text)


def test_missing_level_one_title_rejected() -> None:
    text = "正文先行。\n\n> 状态：`待讨论`\n"

    with pytest.raises(DraftError, match="page.missing_title"):
        parse_page(Path("pages/x.md"), text)


def test_machine_block_after_body_text_rejected() -> None:
    text = "# 标题\n\n正文段落。\n\n> 状态：`待讨论`\n>\n> 父主题：[根](../index.md)\n"

    with pytest.raises(DraftError, match="page.missing_machine_block"):
        parse_page(Path("pages/x.md"), text)


def test_relation_requires_exactly_one_link() -> None:
    two_links = make_page("`待讨论`").replace(
        "> 父主题：[根](../index.md)",
        "> 依赖：[甲](a.md) 与 [乙](b.md)",
    )
    with pytest.raises(DraftError, match="page.invalid_relation"):
        parse_page(Path("pages/x.md"), two_links)

    image_link = make_page("`待讨论`").replace(
        "> 父主题：[根](../index.md)",
        "> 依赖：![图](a.md)",
    )
    with pytest.raises(DraftError, match="page.invalid_relation"):
        parse_page(Path("pages/x.md"), image_link)

    no_link = make_page("`待讨论`").replace(
        "> 父主题：[根](../index.md)",
        "> 依赖：另一页",
    )
    with pytest.raises(DraftError, match="page.invalid_relation"):
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

    assert [relation.label for relation in page.relations] == [
        RelationLabel.PARENT,
        RelationLabel.DEPENDS_ON,
        RelationLabel.DEPENDS_ON,
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
    assert caught.value.code == "page.unknown_label"
    assert caught.value.line == 7
    assert caught.value.details == ("状态依据",)


@pytest.mark.parametrize(
    ("value", "details"),
    [
        ("已关闭", ("not_single_code_span",)),
        ("`已关闭：`", ("empty_explanation",)),
        ("`进行中`", ("unknown_prefix", "进行中")),
    ],
)
def test_invalid_status_uses_stable_reason(value: str, details: tuple[str, ...]) -> None:
    with pytest.raises(DraftError) as caught:
        parse_page(Path("pages/x.md"), make_page(value))
    assert caught.value.code == "page.invalid_status"
    assert caught.value.details == details


def test_invalid_relation_cardinality_branch_keeps_label() -> None:
    text = make_page("`待讨论`").replace(
        "> 父主题：[根](../index.md)", "> 依赖：[甲](a.md) 与 [乙](b.md)"
    )
    with pytest.raises(DraftError) as caught:
        parse_page(Path("pages/x.md"), text)
    assert caught.value.code == "page.invalid_relation"
    assert caught.value.details == ("link_cardinality_or_image", "依赖")


def test_invalid_relation_extra_content_branch_keeps_label() -> None:
    text = make_page("`待讨论`").replace(
        "> 父主题：[根](../index.md)", "> 父主题：[根](../index.md) 额外"
    )
    with pytest.raises(DraftError) as caught:
        parse_page(Path("pages/x.md"), text)
    assert caught.value.code == "page.invalid_relation"
    assert caught.value.details == ("extra_content", "父主题")


def test_duplicate_status_uses_empty_details_with_line() -> None:
    text = "# 标题\n\n> 状态：`待讨论`\n>\n> 状态：`已确认`\n>\n> 父主题：[根](../index.md)\n\nx\n"
    with pytest.raises(DraftError) as caught:
        parse_page(Path("pages/x.md"), text)
    assert caught.value.code == "page.duplicate_status"
    assert caught.value.details == ()
    assert caught.value.line == 5


def test_machine_block_lazy_continuation_is_a_parse_error() -> None:
    text = "# 标题\n\n> 状态：`待讨论`\n普通文本\n>\n> 父主题：[根](../index.md)\n"
    with pytest.raises(DraftError) as caught:
        parse_page(Path("pages/x.md"), text)
    assert caught.value.code == "page.parse_error"
    assert caught.value.details == ("lazy_continuation",)
    assert caught.value.line == 4
