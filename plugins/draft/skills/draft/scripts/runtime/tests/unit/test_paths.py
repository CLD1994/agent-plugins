from __future__ import annotations

from pathlib import Path

from draft.paths import physical_parent, relative_markdown_destination

# ---------------------------------------------------------------------------
# physical_parent
# ---------------------------------------------------------------------------


def test_physical_parent_root_index_has_no_parent() -> None:
    assert physical_parent(Path("index.md")) is None


def test_physical_parent_conclusions_points_to_root_index() -> None:
    assert physical_parent(Path("conclusions.md")) == Path("index.md")


def test_physical_parent_direct_page_under_pages_points_to_root_index() -> None:
    assert physical_parent(Path("pages/a.md")) == Path("index.md")


def test_physical_parent_sub_index_under_pages_points_to_root_index() -> None:
    assert physical_parent(Path("pages/sub/index.md")) == Path("index.md")


def test_physical_parent_deep_sub_index_points_to_parent_index() -> None:
    assert physical_parent(Path("pages/sub/deep/index.md")) == Path("pages/sub/index.md")


def test_physical_parent_regular_page_under_subdir_points_to_subdir_index() -> None:
    assert physical_parent(Path("pages/sub/deep.md")) == Path("pages/sub/index.md")


def test_physical_parent_regular_page_under_nested_dir_points_to_nested_index() -> None:
    assert physical_parent(Path("pages/a/b.md")) == Path("pages/a/index.md")


# ---------------------------------------------------------------------------
# relative_markdown_destination
# ---------------------------------------------------------------------------


def test_relative_markdown_destination_same_directory_no_anchor() -> None:
    assert relative_markdown_destination(Path("pages/a.md"), Path("pages/b.md"), None) == "b.md"


def test_relative_markdown_destination_same_directory_with_anchor() -> None:
    assert relative_markdown_destination(Path("pages/a.md"), Path("pages/b.md"), "结论") == (
        "b.md#结论"
    )


def test_relative_markdown_destination_up_to_conclusions() -> None:
    assert relative_markdown_destination(Path("pages/a.md"), Path("conclusions.md"), None) == (
        "../conclusions.md"
    )


def test_relative_markdown_destination_up_to_root_index() -> None:
    assert relative_markdown_destination(Path("pages/a.md"), Path("index.md"), None) == (
        "../index.md"
    )


def test_relative_markdown_destination_down_from_root() -> None:
    assert relative_markdown_destination(Path("index.md"), Path("pages/a.md"), None) == "pages/a.md"


def test_relative_markdown_destination_root_to_conclusions() -> None:
    assert relative_markdown_destination(Path("index.md"), Path("conclusions.md"), None) == (
        "conclusions.md"
    )


def test_relative_markdown_destination_deep_up_to_conclusions() -> None:
    assert (
        relative_markdown_destination(Path("pages/sub/index.md"), Path("conclusions.md"), None)
        == "../../conclusions.md"
    )


def test_relative_markdown_destination_percent_encodes_spaces() -> None:
    # 路径中的空格必须在链接目标中被百分号编码。
    assert (
        relative_markdown_destination(Path("pages/a.md"), Path("pages/b c.md"), None) == "b%20c.md"
    )


def test_relative_markdown_destination_preserves_safe_characters() -> None:
    # safe 集合 /-._~ 中的字符不被编码。
    assert (
        relative_markdown_destination(Path("index.md"), Path("pages/a-b_c.d~e.md"), None)
        == "pages/a-b_c.d~e.md"
    )


def test_relative_markdown_destination_anchor_not_encoded() -> None:
    # 锚点原样附加，不做百分号编码。
    assert (
        relative_markdown_destination(Path("pages/a.md"), Path("pages/b.md"), "锚 点")
        == "b.md#锚 点"
    )
