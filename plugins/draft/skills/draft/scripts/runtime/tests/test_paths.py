from pathlib import Path

import pytest
from draft.errors import DraftError
from draft.paths import resolve_draft_root, resolve_page_operand


def test_page_operand_rejects_absolute_and_parent_escape(tmp_path: Path) -> None:
    root = tmp_path / "topic"
    root.mkdir()

    for value in (str(tmp_path / "outside.md"), "../outside.md", "pages/../../outside.md"):
        with pytest.raises(DraftError, match="path.escape"):
            resolve_page_operand(root, value)


def test_page_operand_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "topic"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "pages").symlink_to(outside, target_is_directory=True)

    with pytest.raises(DraftError, match="path.symlink_escape"):
        resolve_page_operand(root, "pages/a.md")


def test_page_operand_returns_normalized_markdown_path(tmp_path: Path) -> None:
    root = resolve_draft_root(tmp_path / "topic", allow_missing=True)
    root.mkdir()

    resolved = resolve_page_operand(root, "pages/section/../a.md")

    assert resolved.relative == Path("pages/a.md")
    assert resolved.absolute == root / "pages/a.md"


def test_page_operand_works_with_relative_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "topic"
    root.mkdir()
    monkeypatch.chdir(tmp_path)

    from draft.paths import resolve_page_operand

    resolved = resolve_page_operand(Path("topic"), "pages/a.md")

    assert resolved.relative == Path("pages/a.md")
    assert resolved.absolute == root / "pages/a.md"


def test_relative_and_absolute_root_give_same_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "topic"
    root.mkdir()
    monkeypatch.chdir(tmp_path)

    from draft.paths import resolve_page_operand

    relative_result = resolve_page_operand(Path("topic"), "pages/a.md")
    absolute_result = resolve_page_operand(root, "pages/a.md")

    assert relative_result.relative == absolute_result.relative
    assert relative_result.absolute == absolute_result.absolute


def test_relative_root_still_rejects_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "topic").mkdir()
    monkeypatch.chdir(tmp_path)

    from draft.paths import resolve_page_operand

    with pytest.raises(DraftError, match="path.escape"):
        resolve_page_operand(Path("topic"), "../outside.md")
