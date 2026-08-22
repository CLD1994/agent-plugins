from __future__ import annotations

from pathlib import Path

import pytest

from draft.diagnostic_codes import CheckCode, RootCode
from draft.errors import DraftError
from draft.graph import MarkerState
from draft.models import RootState
from draft.paths import (
    RootSource,
    physical_parent,
    relative_markdown_destination,
    resolve_move_operand,
    select_managed_root,
)


class FakeProbe:
    """受真实签名约束的根探测 fake：目录、marker 与损坏事实由测试显式提供。"""

    def __init__(self) -> None:
        self.directories: set[Path] = set()
        self.managed: dict[Path, MarkerState] = {}
        self.broken: set[Path] = set()
        self.broken_symlink: set[Path] = set()
        self.inspected: list[Path] = []

    def is_directory(self, path: Path) -> bool:
        return path in self.directories

    def exists(self, path: Path) -> bool:
        return path in self.directories

    def managed_at(self, directory: Path) -> MarkerState | None:
        self.inspected.append(directory)
        if directory in self.broken_symlink:
            raise DraftError(CheckCode.PATH_SYMLINK_ESCAPE.value, directory / ".draft")
        if directory in self.broken:
            raise DraftError(CheckCode.FORMAT_INVALID.value, directory / ".draft/draft.json")
        return self.managed.get(directory)


def _registered() -> MarkerState:
    return MarkerState(version=1, root_state=RootState.REGISTERED)


@pytest.fixture
def root_probe() -> FakeProbe:
    return FakeProbe()


# ---------------------------------------------------------------------------
# select_managed_root：根选择决策表
# ---------------------------------------------------------------------------


def test_cli_root_takes_precedence_over_env_and_pwd(root_probe: FakeProbe) -> None:
    root_probe.directories.update({Path("/root-a")})
    root_probe.managed[Path("/root-a")] = _registered()

    managed = select_managed_root(
        explicit="/root-a",
        environment_value="/env-b",
        physical_cwd=Path("/work/inner"),
        probe=root_probe,
    )

    assert managed.path == Path("/root-a")
    assert managed.source is RootSource.CLI
    assert root_probe.inspected == [Path("/root-a")]


def test_higher_priority_invalid_does_not_fall_back_to_lower(
    root_probe: FakeProbe,
) -> None:
    # CLI 存在但指向不存在的路径：不回退到合法的 env/pwd 根。
    root_probe.managed[Path("/env-b")] = _registered()
    with pytest.raises(DraftError) as caught:
        select_managed_root(
            explicit="/missing-a",
            environment_value="/env-b",
            physical_cwd=Path("/work"),
            probe=root_probe,
        )
    assert caught.value.code == RootCode.NOT_FOUND.value
    assert caught.value.details == ("cli",)


def test_invalid_environment_root_does_not_fall_back_to_pwd(
    root_probe: FakeProbe,
) -> None:
    with pytest.raises(DraftError) as caught:
        select_managed_root(
            explicit=None,
            environment_value="relative/root",
            physical_cwd=Path("/work/inner"),
            probe=root_probe,
        )
    assert caught.value.code == RootCode.INVALID.value
    assert caught.value.paths == (Path("relative/root"),)
    assert caught.value.details == ()
    assert root_probe.inspected == []


def test_explicit_relative_root_resolved_against_physical_pwd(
    root_probe: FakeProbe,
) -> None:
    root_probe.directories.add(Path("/work/topic"))
    root_probe.managed[Path("/work/topic")] = _registered()

    managed = select_managed_root(
        explicit="topic",
        environment_value=None,
        physical_cwd=Path("/work"),
        probe=root_probe,
    )

    assert managed.path == Path("/work/topic")
    assert managed.source is RootSource.CLI


def test_environment_root_must_be_absolute(
    root_probe: FakeProbe,
) -> None:
    with pytest.raises(DraftError) as caught:
        select_managed_root(
            explicit=None,
            environment_value="",
            physical_cwd=Path("/work"),
            probe=root_probe,
        )
    assert caught.value.code == RootCode.INVALID.value


def test_nearest_damaged_draft_blocks_outer_root(root_probe: FakeProbe) -> None:
    root_probe.broken.add(Path("/work/inner"))
    root_probe.managed[Path("/work")] = _registered()

    with pytest.raises(DraftError) as caught:
        select_managed_root(
            explicit=None,
            environment_value=None,
            physical_cwd=Path("/work/inner"),
            probe=root_probe,
        )
    assert caught.value.code == CheckCode.FORMAT_INVALID.value


def test_symlink_broken_ancestor_propagates_symlink_escape(root_probe: FakeProbe) -> None:
    """表二（round-16）：中间祖先 .draft 是 symlink -> path.symlink_escape 原样传播。

    不跳过该祖先、不继续上溯到外层合法根；发现 walk 在损坏祖先即终止。
    """
    root_probe.managed[Path("/work")] = _registered()
    root_probe.broken_symlink.add(Path("/work/inner"))

    with pytest.raises(DraftError) as caught:
        select_managed_root(
            explicit=None,
            environment_value=None,
            physical_cwd=Path("/work/inner/deep"),
            probe=root_probe,
        )
    assert caught.value.code == CheckCode.PATH_SYMLINK_ESCAPE.value
    assert caught.value.paths == (Path("/work/inner/.draft"),)
    assert root_probe.inspected == [Path("/work/inner/deep"), Path("/work/inner")]


def test_stat_failure_ancestor_propagates_format_invalid(root_probe: FakeProbe) -> None:
    """表二（round-16）：中间祖先 stat 失败 -> format.invalid 原样传播，外层合法根不被采信。

    ``test_nearest_damaged_draft_blocks_outer_root`` 只断言码；本用例同时钉住
    fail-fast（walk 不越过损坏祖先）这一已确认语义。
    """
    root_probe.managed[Path("/work")] = _registered()
    root_probe.broken.add(Path("/work/inner"))

    with pytest.raises(DraftError) as caught:
        select_managed_root(
            explicit=None,
            environment_value=None,
            physical_cwd=Path("/work/inner/deep"),
            probe=root_probe,
        )
    assert caught.value.code == CheckCode.FORMAT_INVALID.value
    assert root_probe.inspected == [Path("/work/inner/deep"), Path("/work/inner")]


def test_two_legal_ancestor_roots_is_ambiguous(root_probe: FakeProbe) -> None:
    root_probe.managed[Path("/work/a")] = _registered()
    root_probe.managed[Path("/work")] = _registered()

    with pytest.raises(DraftError) as caught:
        select_managed_root(
            explicit=None,
            environment_value=None,
            physical_cwd=Path("/work/a/inner"),
            probe=root_probe,
        )
    assert caught.value.code == RootCode.AMBIGUOUS.value
    assert caught.value.paths == (Path("/work/a/inner"),)
    assert caught.value.details == ("/work/a", "/work")


def test_no_root_reports_not_found_from_pwd(root_probe: FakeProbe) -> None:
    with pytest.raises(DraftError) as caught:
        select_managed_root(
            explicit=None,
            environment_value=None,
            physical_cwd=Path("/work/inner"),
            probe=root_probe,
        )
    assert caught.value.code == RootCode.NOT_FOUND.value
    assert caught.value.paths == (Path("/work/inner"),)
    assert caught.value.details == (RootSource.PWD.value,)  # type: ignore[arg-type]


def test_selected_directory_without_marker_reports_format_missing(
    root_probe: FakeProbe,
) -> None:
    root_probe.directories.add(Path("/plain"))

    with pytest.raises(DraftError) as caught:
        select_managed_root(
            explicit="/plain",
            environment_value=None,
            physical_cwd=Path("/work"),
            probe=root_probe,
        )
    assert caught.value.code == CheckCode.FORMAT_MISSING.value


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


def test_resolve_move_operand_accepts_directory_and_non_markdown(tmp_path: Path) -> None:
    (tmp_path / "legacy-notes").mkdir()
    (tmp_path / "img.png").write_bytes(b"x")

    directory = resolve_move_operand(tmp_path, "legacy-notes")
    image = resolve_move_operand(tmp_path, "img.png")

    assert directory.relative == Path("legacy-notes")
    assert image.relative == Path("img.png")


def test_resolve_move_operand_rejects_escape_and_symlink(tmp_path: Path) -> None:
    (tmp_path / "real.md").write_text("x", encoding="utf-8")
    (tmp_path / "link.md").symlink_to(tmp_path / "real.md")

    with pytest.raises(DraftError, match="path.escape"):
        resolve_move_operand(tmp_path, "../outside.md")
    with pytest.raises(DraftError, match=CheckCode.PATH_SYMLINK_ESCAPE.value):
        resolve_move_operand(tmp_path, "link.md")
