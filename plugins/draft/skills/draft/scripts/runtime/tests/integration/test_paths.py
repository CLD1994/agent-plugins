import errno
import os
import shutil
from pathlib import Path

import pytest

from draft.diagnostic_codes import CheckCode, RootCode
from draft.errors import DraftError
from draft.lifecycle import initialize
from draft.paths import (
    FilesystemProbe,
    ManagedRoot,
    RootSource,
    resolve_draft_root,
    resolve_page_operand,
    select_managed_root,
)


def _select(
    *,
    explicit: str | None = None,
    environment_value: str | None = None,
    physical_cwd: Path,
) -> ManagedRoot:
    return select_managed_root(
        explicit=explicit,
        environment_value=environment_value,
        physical_cwd=physical_cwd,
        probe=FilesystemProbe(),
    )


def test_pwd_discovery_from_deep_subdirectory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "topic"
    initialize(root)
    deep = root / "pages" / "sub" / "deep"
    deep.mkdir(parents=True)
    monkeypatch.chdir(deep)

    managed = _select(physical_cwd=Path.cwd())

    assert managed.path == root.resolve()
    assert managed.source is RootSource.PWD


def test_nested_legal_roots_is_ambiguous(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    outer = tmp_path / "outer"
    initialize(outer)
    inner = tmp_path / "outer" / "inner"
    initialize(inner)
    monkeypatch.chdir(inner / "pages")

    with pytest.raises(DraftError) as caught:
        _select(physical_cwd=Path.cwd())
    assert caught.value.code == RootCode.AMBIGUOUS.value
    assert caught.value.paths == (Path.cwd(),)
    assert caught.value.details == (
        inner.resolve().as_posix(),
        outer.resolve().as_posix(),
    )


def test_damaged_near_root_blocks_outer_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "topic"
    initialize(root)
    near = root / "pages" / "sub"
    near.mkdir(parents=True)
    (near / ".draft").mkdir()
    (near / ".draft/draft.json").write_text("not json\n", encoding="utf-8")
    monkeypatch.chdir(near)

    with pytest.raises(DraftError) as caught:
        _select(physical_cwd=Path.cwd())
    assert caught.value.code == CheckCode.FORMAT_INVALID.value


# ---------------------------------------------------------------------------
# round-16 M1：CLI 入口前置分类与 snapshot 读取器共享同一 .draft 身份权威。
# 经真实 FilesystemProbe/select_managed_root 走通，unit 替身不覆盖。
# ---------------------------------------------------------------------------


def test_managed_at_draft_symlink_reports_symlink_escape(tmp_path: Path) -> None:
    """`.draft` 是 symlink：CLI 入口与 snapshot 读取器给出同一码，@ .draft 自身。

    基线 managed_at 的三连分类报 format.invalid @ marker（与 H1 契约表不符，红）；
    修复后经 classify_marker_parent 报 path.symlink_escape @ .draft。
    """
    root = tmp_path / "topic"
    initialize(root)
    outside = tmp_path / "outside"
    outside.mkdir()
    shutil.rmtree(root / ".draft")
    (root / ".draft").symlink_to(outside, target_is_directory=True)

    with pytest.raises(DraftError) as caught:
        _select(explicit=str(root), physical_cwd=tmp_path)
    assert caught.value.code == CheckCode.PATH_SYMLINK_ESCAPE.value
    assert caught.value.paths == (root.resolve() / ".draft",)


def test_managed_at_draft_lstat_eio_reports_format_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`.draft` 无法 stat（EIO）不得折叠为缺失：报 format.invalid @ marker。

    注入同时命中 os.lstat 与 os.stat（真实的 .draft stat 失败会同时影响两个 syscall）：
    基线 managed_at 的 Path.is_symlink（经 os.stat）重新抛出原始 OSError（红），修复后
    由 classify_marker_parent（os.lstat）稳定映射 format.invalid。
    """
    root = tmp_path / "topic"
    initialize(root)

    real_lstat = os.lstat
    real_stat = os.stat

    def fail_draft_lstat(
        path: str | bytes | os.PathLike[str], *args: object, **kwargs: object
    ) -> os.stat_result:
        if os.fsdecode(path).endswith("/.draft"):
            raise OSError(errno.EIO, "Input/output error")
        return real_lstat(path, *args, **kwargs)  # type: ignore[return-value]

    def fail_draft_stat(
        path: str | bytes | os.PathLike[str], *args: object, **kwargs: object
    ) -> os.stat_result:
        if os.fsdecode(path).endswith("/.draft"):
            raise OSError(errno.EIO, "Input/output error")
        return real_stat(path, *args, **kwargs)  # type: ignore[return-value]

    monkeypatch.setattr(os, "lstat", fail_draft_lstat)
    monkeypatch.setattr(os, "stat", fail_draft_stat)

    with pytest.raises(DraftError) as caught:
        _select(explicit=str(root), physical_cwd=tmp_path)
    assert caught.value.code == CheckCode.FORMAT_INVALID.value
    assert caught.value.paths == (root.resolve() / ".draft/draft.json",)


def test_managed_at_draft_regular_file_reports_format_invalid(tmp_path: Path) -> None:
    """`.draft` 是普通文件（非目录）：format.invalid @ marker（保持用例，防回归）。"""
    root = tmp_path / "topic"
    initialize(root)
    shutil.rmtree(root / ".draft")
    (root / ".draft").write_text("not a directory\n", encoding="utf-8")

    with pytest.raises(DraftError) as caught:
        _select(explicit=str(root), physical_cwd=tmp_path)
    assert caught.value.code == CheckCode.FORMAT_INVALID.value
    assert caught.value.paths == (root.resolve() / ".draft/draft.json",)


def test_managed_at_without_draft_reports_format_missing(tmp_path: Path) -> None:
    """无 `.draft`：None -> select_managed_root 报 format.missing @ marker（保持用例）。"""
    root = tmp_path / "topic"
    root.mkdir()

    with pytest.raises(DraftError) as caught:
        _select(explicit=str(root), physical_cwd=tmp_path)
    assert caught.value.code == CheckCode.FORMAT_MISSING.value
    assert caught.value.paths == (root.resolve() / ".draft/draft.json",)


# ---------------------------------------------------------------------------
# round-16 表二：pwd 祖先发现的异常祖先 fail-fast 语义。外层合法根存在时，symlink
# 与 stat 失败祖先都以正确错误码立即失败，不跳过、不上溯、不折叠。
# ---------------------------------------------------------------------------


def test_pwd_discovery_symlink_ancestor_fails_fast(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """场景 A：`outer/mid/.draft` 是 symlink，cwd 在更深处 -> path.symlink_escape 立即失败。

    基线旧分类器报 format.invalid @ marker（红）；修复后由单一权威报
    path.symlink_escape @ `outer/mid/.draft`，且不落回外层合法根 outer。
    """
    outer = tmp_path / "outer"
    initialize(outer)
    mid = outer / "mid"
    mid.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (mid / ".draft").symlink_to(outside, target_is_directory=True)
    deep = mid / "deep"
    deep.mkdir(parents=True)
    monkeypatch.chdir(deep)

    with pytest.raises(DraftError) as caught:
        _select(physical_cwd=Path.cwd())
    assert caught.value.code == CheckCode.PATH_SYMLINK_ESCAPE.value
    assert caught.value.paths == (mid.resolve() / ".draft",)


def test_pwd_discovery_stat_failure_ancestor_fails_fast(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """场景 B：`outer/mid/.draft` 无法 stat（EIO），cwd 在更深处 -> format.invalid 立即失败。

    注入精确作用于 `outer/mid/.draft`（不误伤 outer/deep 的探测）。基线泄漏原始
    OSError（红）或跳过祖先落回 outer；修复后立即失败 format.invalid，outer 不被采信。
    """
    outer = tmp_path / "outer"
    initialize(outer)
    mid = outer / "mid"
    mid.mkdir()
    (mid / ".draft").mkdir()
    (mid / ".draft/draft.json").write_text(
        '{"format_version":1,"root_state":"registered","conclusions_state":"not_generated"}',
        encoding="utf-8",
    )
    deep = mid / "deep"
    deep.mkdir(parents=True)
    monkeypatch.chdir(deep)

    mid_draft = mid.resolve() / ".draft"
    real_lstat = os.lstat
    real_stat = os.stat

    def fail_mid_lstat(
        path: str | bytes | os.PathLike[str], *args: object, **kwargs: object
    ) -> os.stat_result:
        if os.fsdecode(path) == os.fspath(mid_draft):
            raise OSError(errno.EIO, "Input/output error")
        return real_lstat(path, *args, **kwargs)  # type: ignore[return-value]

    def fail_mid_stat(
        path: str | bytes | os.PathLike[str], *args: object, **kwargs: object
    ) -> os.stat_result:
        if os.fsdecode(path) == os.fspath(mid_draft):
            raise OSError(errno.EIO, "Input/output error")
        return real_stat(path, *args, **kwargs)  # type: ignore[return-value]

    monkeypatch.setattr(os, "lstat", fail_mid_lstat)
    monkeypatch.setattr(os, "stat", fail_mid_stat)

    with pytest.raises(DraftError) as caught:
        _select(physical_cwd=Path.cwd())
    assert caught.value.code == CheckCode.FORMAT_INVALID.value
    assert caught.value.paths == (mid.resolve() / ".draft/draft.json",)


def test_absolute_draft_root_operates_from_any_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "topic"
    initialize(root)
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.chdir(other)

    managed = _select(environment_value=str(root), physical_cwd=Path.cwd())

    assert managed.path == root.resolve()
    assert managed.source is RootSource.ENV


def test_explicit_root_overrides_environment(tmp_path: Path) -> None:
    a = tmp_path / "a"
    initialize(a)
    b = tmp_path / "b"
    initialize(b)

    managed = _select(explicit=str(a), environment_value=str(b), physical_cwd=tmp_path)

    assert managed.path == a.resolve()


def test_missing_explicit_root_reports_not_found(tmp_path: Path) -> None:
    with pytest.raises(DraftError) as caught:
        _select(explicit=str(tmp_path / "nope"), physical_cwd=tmp_path)
    assert caught.value.code == RootCode.NOT_FOUND.value
    assert caught.value.details == (RootSource.CLI.value,)


def test_non_directory_explicit_root_reports_invalid(tmp_path: Path) -> None:
    target = tmp_path / "file"
    target.write_text("x", encoding="utf-8")

    with pytest.raises(DraftError) as caught:
        _select(explicit=str(target), physical_cwd=tmp_path)
    assert caught.value.code == RootCode.INVALID.value


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

    with pytest.raises(DraftError, match=CheckCode.PATH_SYMLINK_ESCAPE.value):
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


def test_resolve_draft_root_maps_unresolvable_home_to_root_missing(
    unresolvable_home: None,
) -> None:
    value = Path("~/topic")

    with pytest.raises(DraftError) as excinfo:
        resolve_draft_root(value)

    assert excinfo.value.code == "root.missing"
    assert excinfo.value.paths == (value,)
    assert isinstance(excinfo.value.__cause__, RuntimeError)


def test_resolve_draft_root_maps_unresolvable_home_with_allow_missing(
    unresolvable_home: None,
) -> None:
    with pytest.raises(DraftError) as excinfo:
        resolve_draft_root(Path("~/topic"), allow_missing=True)

    assert excinfo.value.code == "root.missing"
    assert isinstance(excinfo.value.__cause__, RuntimeError)


def test_page_operand_maps_unresolvable_home_to_root_missing(
    unresolvable_home: None,
) -> None:
    with pytest.raises(DraftError) as excinfo:
        resolve_page_operand(Path("~/topic"), "pages/a.md")

    assert excinfo.value.code == "root.missing"
    assert isinstance(excinfo.value.__cause__, RuntimeError)
