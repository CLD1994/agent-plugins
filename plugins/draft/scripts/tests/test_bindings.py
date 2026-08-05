"""三字段 binding 协议测试：不验证 Draft，仅依赖标准库与 adapter.errors。"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import pytest
from adapter.bindings import (
    Binding,
    bind,
    binding_hint_is_current,
    binding_key,
    binding_path,
    binding_root_is_current,
    load_binding,
    unbind,
)
from adapter.errors import EXIT_RUNTIME, EXIT_USAGE, AdapterError

_PAYLOAD_KEYS = {"session_id", "root", "last_page_hint"}


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def write_payload(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def five_field_payload(root: Path, session: str = "s") -> dict[str, object]:
    return {
        "session_id": session,
        "root": str(root),
        "last_page_hint": None,
        "skill_name": "draft",
        "skill_digest": "old",
    }


# ---------------------------------------------------------------------------
# Schema 与不透明 session
# ---------------------------------------------------------------------------


def test_bind_accepts_unregistered_directory_and_writes_exact_schema(
    tmp_path: Path,
) -> None:
    data_dir = (tmp_path / "data").resolve()
    data_dir.mkdir()
    root = tmp_path / "ordinary"
    root.mkdir()

    result = bind(data_dir, Binding("../会话/\\opaque", root, None))

    assert result.root == root.resolve()
    payload = json.loads(binding_path(data_dir, result.session_id).read_text("utf-8"))
    assert payload == {
        "session_id": "../会话/\\opaque",
        "root": str(root.resolve()),
        "last_page_hint": None,
    }
    assert set(payload) == _PAYLOAD_KEYS


def test_binding_key_never_uses_session_as_a_path(tmp_path: Path) -> None:
    session_id = "../斜线/和\\unicode"
    data_dir = tmp_path / "data"
    assert binding_path(data_dir, session_id) == (
        data_dir / "bindings" / f"{hashlib.sha256(session_id.encode()).hexdigest()}.json"
    )


@pytest.mark.parametrize("session", ["", "/", "\\", "..", "   ", "会话-１２３"])
def test_binding_key_is_opaque_for_all_sessions(session: str) -> None:
    assert binding_key(session) == hashlib.sha256(session.encode("utf-8")).hexdigest()


def test_bind_does_not_create_draft_directory_or_call_core(
    workspace: tuple[Path, Path],
) -> None:
    data_dir, root = workspace
    bind(data_dir, Binding("s", root, None))
    assert not (root / ".draft").exists()
    assert not (root / "index.md").exists()
    assert list(root.iterdir()) == []


# ---------------------------------------------------------------------------
# 根矩阵
# ---------------------------------------------------------------------------


def test_bind_rejects_missing_root(workspace: tuple[Path, Path], tmp_path: Path) -> None:
    data_dir, _ = workspace
    missing = tmp_path / "missing"
    with pytest.raises(AdapterError, match="binding.root_unavailable") as caught:
        bind(data_dir, Binding("s", missing, None))
    assert caught.value.exit_code == EXIT_USAGE


def test_bind_rejects_file_root(workspace: tuple[Path, Path], tmp_path: Path) -> None:
    data_dir, _ = workspace
    file_root = tmp_path / "file"
    file_root.write_text("x", encoding="utf-8")
    with pytest.raises(AdapterError, match="binding.root_unavailable") as caught:
        bind(data_dir, Binding("s", file_root, None))
    assert caught.value.exit_code == EXIT_USAGE


def test_bind_rejects_symlink_root(workspace: tuple[Path, Path], tmp_path: Path) -> None:
    data_dir, _ = workspace
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    with pytest.raises(AdapterError, match="binding.root_unavailable") as caught:
        bind(data_dir, Binding("s", link, None))
    assert caught.value.exit_code == EXIT_USAGE


@pytest.mark.parametrize(
    "invalid_data_dir",
    ["relative", "symlink", "missing", "file"],
)
def test_bind_rejects_non_canonical_data_dir(tmp_path: Path, invalid_data_dir: str) -> None:
    root = tmp_path / "root"
    root.mkdir()
    real_data = (tmp_path / "real-data").resolve()
    real_data.mkdir()

    if invalid_data_dir == "relative":
        bad = Path("relative")
    elif invalid_data_dir == "symlink":
        bad = tmp_path / "link-data"
        bad.symlink_to(real_data)
    elif invalid_data_dir == "missing":
        bad = tmp_path / "missing"
    else:
        bad = tmp_path / "file"
        bad.write_text("x", encoding="utf-8")

    with pytest.raises(AdapterError, match="binding.data_dir_unavailable") as caught:
        bind(bad, Binding("s", root, None))
    assert caught.value.exit_code == EXIT_RUNTIME


# ---------------------------------------------------------------------------
# 页面提示矩阵
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "hint",
    ["", "   ", "/etc/passwd", "../outside.md", "pages/../../x.md", "C:\\x.md", "pages/a.txt"],
)
def test_hint_rejects_empty_absolute_escape_and_non_markdown(
    workspace: tuple[Path, Path], hint: str
) -> None:
    data_dir, root = workspace
    with pytest.raises(AdapterError, match="binding.invalid_hint") as caught:
        bind(data_dir, Binding("s", root, Path(hint)))
    assert caught.value.exit_code == EXIT_USAGE


def test_hint_requires_regular_file_without_symlink_segments(
    workspace: tuple[Path, Path],
) -> None:
    data_dir, root = workspace
    (root / "pages").mkdir()
    target = root / "pages/a.md"
    target.write_text("ordinary markdown\n", encoding="utf-8")
    assert bind(data_dir, Binding("ok", root, Path("pages/a.md"))).last_page_hint == Path(
        "pages/a.md"
    )
    target.unlink()
    target.symlink_to(root / "real.md")
    with pytest.raises(AdapterError, match="binding.invalid_hint"):
        bind(data_dir, Binding("link", root, Path("pages/a.md")))


def test_hint_rejects_symlink_in_intermediate_segment(
    workspace: tuple[Path, Path],
) -> None:
    data_dir, root = workspace
    (root / "pages").mkdir()
    (root / "pages/a.md").write_text("x\n", encoding="utf-8")
    # 把中间目录 pages 替换为符号链接
    real_pages = root / "real-pages"
    real_pages.mkdir()
    (root / "pages/a.md").unlink()
    (root / "pages").rmdir()
    (root / "pages").symlink_to(real_pages)
    with pytest.raises(AdapterError, match="binding.invalid_hint"):
        bind(data_dir, Binding("s", root, Path("pages/a.md")))


def test_hint_rejects_windows_backslash_dotdot(workspace: tuple[Path, Path]) -> None:
    """反斜杠路径仅在 PureWindowsPath 解析下含 `..`，必须被拒绝。"""
    data_dir, root = workspace
    with pytest.raises(AdapterError, match="binding.invalid_hint"):
        bind(data_dir, Binding("s", root, Path("a\\..\\b.md")))


def test_hint_rejects_missing_file(workspace: tuple[Path, Path]) -> None:
    data_dir, root = workspace
    (root / "pages").mkdir()
    with pytest.raises(AdapterError, match="binding.invalid_hint"):
        bind(data_dir, Binding("s", root, Path("pages/missing.md")))


def test_hint_rejects_directory_target(workspace: tuple[Path, Path]) -> None:
    data_dir, root = workspace
    (root / "pages").mkdir()
    (root / "pages/sub").mkdir()
    with pytest.raises(AdapterError, match="binding.invalid_hint"):
        bind(data_dir, Binding("s", root, Path("pages/sub")))


def test_hint_none_is_accepted(workspace: tuple[Path, Path]) -> None:
    data_dir, root = workspace
    result = bind(data_dir, Binding("s", root, None))
    assert result.last_page_hint is None


# ---------------------------------------------------------------------------
# 严格加载
# ---------------------------------------------------------------------------


def test_old_five_field_payload_is_invalid_but_rebind_overwrites_it(
    workspace: tuple[Path, Path],
) -> None:
    data_dir, root = workspace
    target = binding_path(data_dir, "s")
    target.parent.mkdir()
    target.write_text(json.dumps(five_field_payload(root)), encoding="utf-8")
    with pytest.raises(AdapterError, match="binding.invalid"):
        load_binding(data_dir, "s")
    bind(data_dir, Binding("s", root, None))
    assert load_binding(data_dir, "s") == Binding("s", root.resolve(), None)


def test_load_returns_none_when_missing(workspace: tuple[Path, Path]) -> None:
    data_dir, _ = workspace
    assert load_binding(data_dir, "never") is None


def test_load_returns_none_when_bindings_dir_missing(
    workspace: tuple[Path, Path],
) -> None:
    data_dir, _ = workspace
    assert load_binding(data_dir, "s") is None


def test_bind_and_load_round_trip(workspace: tuple[Path, Path]) -> None:
    data_dir, root = workspace
    (root / "pages").mkdir()
    (root / "pages/a.md").write_text("x\n", encoding="utf-8")
    original = Binding("session-1", root, Path("pages/a.md"))
    bind(data_dir, original)
    loaded = load_binding(data_dir, "session-1")
    assert loaded is not None
    assert loaded == Binding("session-1", root.resolve(), Path("pages/a.md"))


def test_bind_and_load_round_trip_empty_session(
    workspace: tuple[Path, Path],
) -> None:
    """空 session_id 是合法的不透明身份，bind 写入后 load 必须原样读回。"""
    data_dir, root = workspace
    bind(data_dir, Binding("", root, None))
    loaded = load_binding(data_dir, "")
    assert loaded == Binding("", root.resolve(), None)


def test_rebind_overwrites_same_session(workspace: tuple[Path, Path]) -> None:
    data_dir, root = workspace
    bind(data_dir, Binding("s", root, None))
    (root / "pages").mkdir()
    (root / "pages/a.md").write_text("x\n", encoding="utf-8")
    bind(data_dir, Binding("s", root, Path("pages/a.md")))
    loaded = load_binding(data_dir, "s")
    assert loaded is not None
    assert loaded.last_page_hint == Path("pages/a.md")


@pytest.mark.parametrize("missing", ["session_id", "root", "last_page_hint"])
def test_load_rejects_missing_field(workspace: tuple[Path, Path], missing: str) -> None:
    data_dir, root = workspace
    target = binding_path(data_dir, "s")
    payload: dict[str, object] = {
        "session_id": "s",
        "root": str(root.resolve()),
        "last_page_hint": None,
    }
    del payload[missing]
    write_payload(target, payload)
    with pytest.raises(AdapterError, match="binding.invalid"):
        load_binding(data_dir, "s")


def test_load_rejects_extra_field(workspace: tuple[Path, Path]) -> None:
    data_dir, root = workspace
    target = binding_path(data_dir, "s")
    payload: dict[str, object] = {
        "session_id": "s",
        "root": str(root.resolve()),
        "last_page_hint": None,
        "extra": "x",
    }
    write_payload(target, payload)
    with pytest.raises(AdapterError, match="binding.invalid"):
        load_binding(data_dir, "s")


@pytest.mark.parametrize(
    "field,value",
    [
        ("session_id", 42),
        ("root", None),
        ("root", 7),
        ("last_page_hint", 7),
        ("last_page_hint", [1, 2]),
    ],
)
def test_load_rejects_wrong_field_types(
    workspace: tuple[Path, Path], field: str, value: object
) -> None:
    data_dir, root = workspace
    target = binding_path(data_dir, "s")
    payload: dict[str, object] = {
        "session_id": "s",
        "root": str(root.resolve()),
        "last_page_hint": None,
    }
    payload[field] = value
    write_payload(target, payload)
    with pytest.raises(AdapterError, match="binding.invalid"):
        load_binding(data_dir, "s")


def test_load_rejects_non_object_json(workspace: tuple[Path, Path]) -> None:
    data_dir, _ = workspace
    target = binding_path(data_dir, "s")
    write_payload(target, {})  # placeholder to create parent
    target.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(AdapterError, match="binding.invalid"):
        load_binding(data_dir, "s")


def test_load_rejects_corrupt_json(workspace: tuple[Path, Path]) -> None:
    data_dir, _ = workspace
    target = binding_path(data_dir, "s")
    target.parent.mkdir()
    target.write_text("{broken", encoding="utf-8")
    with pytest.raises(AdapterError, match="binding.invalid"):
        load_binding(data_dir, "s")


def test_load_rejects_session_mismatch(workspace: tuple[Path, Path]) -> None:
    data_dir, root = workspace
    target = binding_path(data_dir, "expected")
    write_payload(
        target,
        {"session_id": "other", "root": str(root.resolve()), "last_page_hint": None},
    )
    with pytest.raises(AdapterError, match="binding.session_mismatch") as caught:
        load_binding(data_dir, "expected")
    assert caught.value.exit_code == EXIT_RUNTIME


def test_load_rejects_relative_persisted_root(workspace: tuple[Path, Path]) -> None:
    data_dir, _ = workspace
    target = binding_path(data_dir, "s")
    write_payload(
        target,
        {"session_id": "s", "root": "relative/path", "last_page_hint": None},
    )
    with pytest.raises(AdapterError, match="binding.invalid"):
        load_binding(data_dir, "s")


def test_load_rejects_non_normalized_persisted_root(
    workspace: tuple[Path, Path],
) -> None:
    data_dir, root = workspace
    target = binding_path(data_dir, "s")
    non_normalized = str(root.resolve().parent) + "/./" + root.name
    write_payload(
        target,
        {"session_id": "s", "root": non_normalized, "last_page_hint": None},
    )
    with pytest.raises(AdapterError, match="binding.invalid"):
        load_binding(data_dir, "s")


def test_load_rejects_dotdot_persisted_root(workspace: tuple[Path, Path]) -> None:
    data_dir, root = workspace
    target = binding_path(data_dir, "s")
    dotdot_root = str(root.resolve().parent) + "/../" + root.parent.name + "/" + root.name
    write_payload(
        target,
        {"session_id": "s", "root": dotdot_root, "last_page_hint": None},
    )
    with pytest.raises(AdapterError, match="binding.invalid"):
        load_binding(data_dir, "s")


def test_load_rejects_lexically_invalid_persisted_hint(
    workspace: tuple[Path, Path],
) -> None:
    data_dir, root = workspace
    target = binding_path(data_dir, "s")
    write_payload(
        target,
        {"session_id": "s", "root": str(root.resolve()), "last_page_hint": "../x.md"},
    )
    with pytest.raises(AdapterError, match="binding.invalid_hint"):
        load_binding(data_dir, "s")


def test_load_does_not_resolve_persisted_root(workspace: tuple[Path, Path]) -> None:
    """加载时不解析 root，保留 'root 后续缺失' 的独立状态。"""
    data_dir, _ = workspace
    target = binding_path(data_dir, "s")
    missing_root = "/nonexistent/absolute/path"
    write_payload(
        target,
        {"session_id": "s", "root": missing_root, "last_page_hint": None},
    )
    loaded = load_binding(data_dir, "s")
    assert loaded is not None
    assert loaded.root == Path(missing_root)


# ---------------------------------------------------------------------------
# 容器安全
# ---------------------------------------------------------------------------


def test_load_rejects_symlink_container(workspace: tuple[Path, Path], tmp_path: Path) -> None:
    data_dir, _ = workspace
    real_container = tmp_path / "real-bindings"
    real_container.mkdir()
    container = data_dir / "bindings"
    container.symlink_to(real_container)
    with pytest.raises(AdapterError, match="binding.data_dir_unavailable"):
        load_binding(data_dir, "s")


def test_load_rejects_non_directory_container(workspace: tuple[Path, Path]) -> None:
    data_dir, _ = workspace
    container = data_dir / "bindings"
    container.write_text("x", encoding="utf-8")
    with pytest.raises(AdapterError, match="binding.data_dir_unavailable"):
        load_binding(data_dir, "s")


def test_bind_rejects_symlink_container(workspace: tuple[Path, Path], tmp_path: Path) -> None:
    data_dir, root = workspace
    real_container = tmp_path / "real-bindings"
    real_container.mkdir()
    container = data_dir / "bindings"
    container.symlink_to(real_container)
    with pytest.raises(AdapterError, match="binding.data_dir_unavailable"):
        bind(data_dir, Binding("s", root, None))
    assert container.is_symlink()


def test_bind_rejects_non_directory_container(workspace: tuple[Path, Path]) -> None:
    data_dir, root = workspace
    container = data_dir / "bindings"
    container.write_text("x", encoding="utf-8")
    with pytest.raises(AdapterError, match="binding.data_dir_unavailable"):
        bind(data_dir, Binding("s", root, None))


# ---------------------------------------------------------------------------
# 记录安全
# ---------------------------------------------------------------------------


def test_load_rejects_symlink_record(workspace: tuple[Path, Path]) -> None:
    data_dir, _ = workspace
    target = binding_path(data_dir, "s")
    target.parent.mkdir()
    real = data_dir / "real.json"
    real.write_text("{}", encoding="utf-8")
    target.symlink_to(real)
    with pytest.raises(AdapterError, match="binding.invalid"):
        load_binding(data_dir, "s")


def test_load_rejects_non_regular_record(workspace: tuple[Path, Path]) -> None:
    data_dir, _ = workspace
    target = binding_path(data_dir, "s")
    target.mkdir(parents=True)
    with pytest.raises(AdapterError, match="binding.invalid"):
        load_binding(data_dir, "s")


def test_bind_never_follows_or_repairs_symlink_target(
    workspace: tuple[Path, Path],
) -> None:
    data_dir, root = workspace
    target = binding_path(data_dir, "s")
    target.parent.mkdir()
    target.symlink_to(data_dir / "attacker")
    with pytest.raises(AdapterError, match="binding.invalid"):
        bind(data_dir, Binding("s", root, None))
    assert target.is_symlink()


def test_bind_overwrites_safe_regular_file_without_parsing(
    workspace: tuple[Path, Path],
) -> None:
    data_dir, root = workspace
    target = binding_path(data_dir, "s")
    target.parent.mkdir()
    target.write_text("{broken old json", encoding="utf-8")
    bind(data_dir, Binding("s", root, None))
    loaded = load_binding(data_dir, "s")
    assert loaded == Binding("s", root.resolve(), None)


# ---------------------------------------------------------------------------
# 解除绑定
# ---------------------------------------------------------------------------


def test_unbind_removes_malformed_regular_payload_without_parsing(
    workspace: tuple[Path, Path],
) -> None:
    data_dir, _ = workspace
    target = binding_path(data_dir, "s")
    target.parent.mkdir()
    target.write_text("{broken", encoding="utf-8")
    assert unbind(data_dir, "s") is True
    assert unbind(data_dir, "s") is False


def test_unbind_returns_false_when_missing(workspace: tuple[Path, Path]) -> None:
    data_dir, _ = workspace
    assert unbind(data_dir, "s") is False


def test_unbind_returns_false_when_container_missing(
    workspace: tuple[Path, Path],
) -> None:
    data_dir, _ = workspace
    assert unbind(data_dir, "s") is False


def test_unbind_rejects_symlink_target(workspace: tuple[Path, Path]) -> None:
    data_dir, _ = workspace
    target = binding_path(data_dir, "s")
    target.parent.mkdir()
    real = data_dir / "real.json"
    real.write_text("{}", encoding="utf-8")
    target.symlink_to(real)
    with pytest.raises(AdapterError, match="binding.invalid"):
        unbind(data_dir, "s")
    assert target.is_symlink()


def test_unbind_rejects_non_regular_target(workspace: tuple[Path, Path]) -> None:
    data_dir, _ = workspace
    target = binding_path(data_dir, "s")
    target.mkdir(parents=True)
    with pytest.raises(AdapterError, match="binding.invalid"):
        unbind(data_dir, "s")


def test_unbind_rejects_symlink_container(workspace: tuple[Path, Path], tmp_path: Path) -> None:
    data_dir, _ = workspace
    real_container = tmp_path / "real-bindings"
    real_container.mkdir()
    container = data_dir / "bindings"
    container.symlink_to(real_container)
    with pytest.raises(AdapterError, match="binding.data_dir_unavailable"):
        unbind(data_dir, "s")


# ---------------------------------------------------------------------------
# 原子写失败映射
# ---------------------------------------------------------------------------


def test_atomic_write_tempfile_failure_is_data_dir_unavailable(
    workspace: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir, root = workspace

    def denied(*args: object, **kwargs: object) -> tuple[int, str]:
        raise OSError("mkstemp failed")

    monkeypatch.setattr(tempfile, "mkstemp", denied)
    with pytest.raises(AdapterError, match="binding.data_dir_unavailable") as caught:
        bind(data_dir, Binding("s", root, None))
    assert isinstance(caught.value.__cause__, OSError)


def test_atomic_write_fdopen_failure_is_data_dir_unavailable_and_cleans_temp(
    workspace: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir, root = workspace

    def denied(descriptor: int, mode: str = "r") -> Any:
        raise OSError("fdopen failed")

    monkeypatch.setattr(os, "fdopen", denied)
    with pytest.raises(AdapterError, match="binding.data_dir_unavailable") as caught:
        bind(data_dir, Binding("s", root, None))
    assert isinstance(caught.value.__cause__, OSError)
    container = data_dir / "bindings"
    leftovers = [p for p in container.iterdir() if p.name.startswith(".")]
    assert leftovers == []


def test_atomic_write_fsync_failure_is_data_dir_unavailable_and_cleans_temp(
    workspace: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir, root = workspace

    def denied(descriptor: int) -> None:
        raise OSError("fsync failed")

    monkeypatch.setattr(os, "fsync", denied)
    with pytest.raises(AdapterError, match="binding.data_dir_unavailable") as caught:
        bind(data_dir, Binding("s", root, None))
    assert isinstance(caught.value.__cause__, OSError)
    container = data_dir / "bindings"
    leftovers = [p for p in container.iterdir() if p.name.startswith(".")]
    assert leftovers == []


def test_atomic_write_replace_failure_is_data_dir_unavailable_and_cleans_temp(
    workspace: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir, root = workspace

    def denied(source: object, target: object) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", denied)
    with pytest.raises(AdapterError, match="binding.data_dir_unavailable") as caught:
        bind(data_dir, Binding("s", root, None))
    assert isinstance(caught.value.__cause__, OSError)
    container = data_dir / "bindings"
    leftovers = [p for p in container.iterdir() if p.name.startswith(".")]
    assert leftovers == []


def test_atomic_write_cleanup_failure_preserves_primary_error(
    workspace: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir, root = workspace
    real_unlink = os.unlink

    def failing_unlink(path: Any) -> None:
        p = Path(str(path))
        if p.name.startswith("."):
            raise OSError("cleanup unlink failed")
        real_unlink(path)

    def failing_fsync(descriptor: int) -> None:
        raise OSError("fsync failed")

    monkeypatch.setattr(os, "fsync", failing_fsync)
    monkeypatch.setattr(os, "unlink", failing_unlink)
    with pytest.raises(AdapterError, match="binding.data_dir_unavailable") as caught:
        bind(data_dir, Binding("s", root, None))
    assert isinstance(caught.value.__cause__, OSError)
    assert any("cleanup" in note for note in caught.value.__notes__)


def test_bind_mkdir_failure_is_data_dir_unavailable(
    workspace: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir, root = workspace
    real_mkdir = Path.mkdir

    def denied(
        self: Path, mode: int = 0o777, parents: bool = False, exist_ok: bool = False
    ) -> None:
        if self.name == "bindings":
            raise OSError("mkdir failed")
        real_mkdir(self, mode=mode, parents=parents, exist_ok=exist_ok)

    monkeypatch.setattr(Path, "mkdir", denied)
    with pytest.raises(AdapterError, match="binding.data_dir_unavailable") as caught:
        bind(data_dir, Binding("s", root, None))
    assert isinstance(caught.value.__cause__, OSError)


def test_unbind_unlink_failure_is_data_dir_unavailable(
    workspace: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir, root = workspace
    bind(data_dir, Binding("s", root, None))

    def denied(self: Path, missing_ok: bool = False) -> None:
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "unlink", denied)
    with pytest.raises(AdapterError, match="binding.data_dir_unavailable") as caught:
        unbind(data_dir, "s")
    assert isinstance(caught.value.__cause__, OSError)


# ---------------------------------------------------------------------------
# 谓词：binding_root_is_current / binding_hint_is_current
# ---------------------------------------------------------------------------


def test_binding_root_is_current_true(workspace: tuple[Path, Path]) -> None:
    data_dir, root = workspace
    binding = bind(data_dir, Binding("s", root, None))
    assert binding_root_is_current(binding) is True


def test_binding_root_is_current_false_when_root_deleted(
    workspace: tuple[Path, Path],
) -> None:
    data_dir, root = workspace
    binding = bind(data_dir, Binding("s", root, None))
    root.rmdir()
    assert binding_root_is_current(binding) is False


def test_binding_root_is_current_false_when_root_is_symlink(
    workspace: tuple[Path, Path], tmp_path: Path
) -> None:
    data_dir, root = workspace
    binding = bind(data_dir, Binding("s", root, None))
    real = tmp_path / "real"
    real.mkdir()
    root.rmdir()
    root.symlink_to(real)
    assert binding_root_is_current(binding) is False


def test_binding_root_is_current_false_when_root_is_file(
    workspace: tuple[Path, Path],
) -> None:
    data_dir, root = workspace
    binding = bind(data_dir, Binding("s", root, None))
    root.rmdir()
    root.write_text("x", encoding="utf-8")
    assert binding_root_is_current(binding) is False


def test_binding_hint_is_current_true(workspace: tuple[Path, Path]) -> None:
    data_dir, root = workspace
    (root / "pages").mkdir()
    (root / "pages/a.md").write_text("x\n", encoding="utf-8")
    binding = bind(data_dir, Binding("s", root, Path("pages/a.md")))
    assert binding_hint_is_current(binding) is True


def test_binding_hint_is_current_true_when_no_hint(
    workspace: tuple[Path, Path],
) -> None:
    data_dir, root = workspace
    binding = bind(data_dir, Binding("s", root, None))
    assert binding_hint_is_current(binding) is True


def test_binding_hint_is_current_false_when_hint_file_deleted(
    workspace: tuple[Path, Path],
) -> None:
    data_dir, root = workspace
    (root / "pages").mkdir()
    target = root / "pages/a.md"
    target.write_text("x\n", encoding="utf-8")
    binding = bind(data_dir, Binding("s", root, Path("pages/a.md")))
    target.unlink()
    assert binding_hint_is_current(binding) is False


def test_binding_hint_is_current_false_when_hint_becomes_symlink(
    workspace: tuple[Path, Path],
) -> None:
    data_dir, root = workspace
    (root / "pages").mkdir()
    target = root / "pages/a.md"
    target.write_text("x\n", encoding="utf-8")
    binding = bind(data_dir, Binding("s", root, Path("pages/a.md")))
    target.unlink()
    target.symlink_to(root / "other.md")
    assert binding_hint_is_current(binding) is False


def test_binding_hint_is_current_false_when_root_deleted(
    workspace: tuple[Path, Path],
) -> None:
    data_dir, root = workspace
    (root / "pages").mkdir()
    (root / "pages/a.md").write_text("x\n", encoding="utf-8")
    binding = bind(data_dir, Binding("s", root, Path("pages/a.md")))
    import shutil

    shutil.rmtree(root)
    assert binding_hint_is_current(binding) is False


def test_binding_hint_is_current_false_when_intermediate_becomes_symlink(
    workspace: tuple[Path, Path], tmp_path: Path
) -> None:
    data_dir, root = workspace
    (root / "pages").mkdir()
    (root / "pages/a.md").write_text("x\n", encoding="utf-8")
    binding = bind(data_dir, Binding("s", root, Path("pages/a.md")))
    real_pages = tmp_path / "real-pages"
    real_pages.mkdir()
    (root / "pages/a.md").unlink()
    (root / "pages").rmdir()
    (root / "pages").symlink_to(real_pages)
    assert binding_hint_is_current(binding) is False


# ---------------------------------------------------------------------------
# 退出码
# ---------------------------------------------------------------------------


def test_root_unavailable_uses_exit_two(workspace: tuple[Path, Path], tmp_path: Path) -> None:
    data_dir, _ = workspace
    with pytest.raises(AdapterError, match="binding.root_unavailable") as caught:
        bind(data_dir, Binding("s", tmp_path / "missing", None))
    assert caught.value.exit_code == EXIT_USAGE


def test_invalid_hint_uses_exit_two(workspace: tuple[Path, Path]) -> None:
    data_dir, root = workspace
    with pytest.raises(AdapterError, match="binding.invalid_hint") as caught:
        bind(data_dir, Binding("s", root, Path("bad.txt")))
    assert caught.value.exit_code == EXIT_USAGE


def test_invalid_uses_exit_four(workspace: tuple[Path, Path]) -> None:
    data_dir, _ = workspace
    target = binding_path(data_dir, "s")
    target.parent.mkdir()
    target.write_text("{broken", encoding="utf-8")
    with pytest.raises(AdapterError, match="binding.invalid") as caught:
        load_binding(data_dir, "s")
    assert caught.value.exit_code == EXIT_RUNTIME
