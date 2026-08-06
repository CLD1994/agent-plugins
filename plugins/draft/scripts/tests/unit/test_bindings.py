"""session_id 验证、binding 哈希/路径、词法校验、序列化与 bind 编排的单元测试。

纯值函数直接验证；bind 编排用受接口约束的替身替换适配层拥有的 FS 协作者，
保留 binding_path/_lexical_hint/_binding_json 的真实纯计算。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from unittest.mock import MagicMock, patch

import pytest

from adapter.bindings import (
    Binding,
    _binding_json,  # pyright: ignore[reportPrivateUsage]  # 序列化契约：验证三字段 JSON 与尾部换行
    _lexical_hint,  # pyright: ignore[reportPrivateUsage]  # 词法校验契约：验证空/绝对/越界/后缀分支
    bind,
    binding_key,
    binding_path,
    session_id_is_valid,
)
from adapter.errors import EXIT_USAGE, AdapterError

# ---------------------------------------------------------------------------
# 会话标识验证：合法、空值、控制字符、孤立代理码位、长度边界
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("session_id", "expected"),
    [
        ("", True),
        ("ordinary", True),
        ("会话-１２３", True),
        ("😀🚀", True),
        ("é", True),
        ("e\u0301", True),
        ("\x00\x01\x02", True),
        ("\ud800", False),
        ("abc\udc00", False),
        ("\ud800\udc00", False),
    ],
)
def test_session_id_is_valid_matrix(session_id: str, expected: bool) -> None:
    assert session_id_is_valid(session_id) is expected


def test_session_id_is_valid_accepts_long_string() -> None:
    assert session_id_is_valid("x" * 100_000) is True


def test_session_id_is_valid_rejects_lone_surrogate_code_point() -> None:
    # 孤立代理码位无法以严格 UTF-8 编码：必须拒绝，且不做 Unicode 规范化。
    assert session_id_is_valid("\ud800") is False
    assert session_id_is_valid("abc\udc00def") is False


def test_session_id_is_valid_treats_visually_identical_as_distinct() -> None:
    # 预组合 é 与 e+组合重音符是不同码点序列，均合法但不得被规范化为同一标识。
    precomposed = "é"
    decomposed = "e\u0301"
    assert session_id_is_valid(precomposed) is True
    assert session_id_is_valid(decomposed) is True
    assert precomposed != decomposed


# ---------------------------------------------------------------------------
# binding_key / binding_path：不透明哈希与词法路径
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("session", ["", "/", "\\", "..", "   ", "会话-１２３", "😀🚀"])
def test_binding_key_is_sha256_hex_of_utf8_session(session: str) -> None:
    assert binding_key(session) == hashlib.sha256(session.encode("utf-8")).hexdigest()


def test_binding_key_is_constant_length_64() -> None:
    assert len(binding_key("")) == 64
    assert len(binding_key("x" * 10_000)) == 64
    assert all(c in "0123456789abcdef" for c in binding_key("s"))


def test_binding_key_never_uses_session_as_a_path() -> None:
    session_id = "../斜线/和\\unicode"
    data_dir = Path("/data")
    assert binding_path(data_dir, session_id) == (
        data_dir / "bindings" / f"{hashlib.sha256(session_id.encode('utf-8')).hexdigest()}.json"
    )


def test_binding_path_distinct_for_distinct_codepoint_sequences() -> None:
    precomposed = "é"
    decomposed = "e\u0301"
    data_dir = Path("/data")
    assert binding_key(precomposed) != binding_key(decomposed)
    assert binding_path(data_dir, precomposed) != binding_path(data_dir, decomposed)


def test_binding_path_locates_under_bindings_dir() -> None:
    data_dir = Path("/var/draft")
    target = binding_path(data_dir, "s")
    assert target == data_dir / "bindings" / f"{binding_key('s')}.json"
    assert target.name.endswith(".json")


# ---------------------------------------------------------------------------
# _lexical_hint：纯词法校验（独立契约）
# ---------------------------------------------------------------------------


def test_lexical_hint_accepts_relative_markdown_path() -> None:
    assert _lexical_hint("pages/a.md") == PurePosixPath("pages/a.md")


def test_lexical_hint_accepts_nested_relative_markdown() -> None:
    assert _lexical_hint("a/b/c.md") == PurePosixPath("a/b/c.md")


@pytest.mark.parametrize("raw", ["", "   ", "\t\n"])
def test_lexical_hint_rejects_empty_or_whitespace(raw: str) -> None:
    with pytest.raises(AdapterError, match="binding.invalid_hint") as caught:
        _lexical_hint(raw)
    assert caught.value.exit_code == EXIT_USAGE


@pytest.mark.parametrize("raw", ["/etc/passwd", "/abs.md"])
def test_lexical_hint_rejects_posix_absolute(raw: str) -> None:
    with pytest.raises(AdapterError, match="binding.invalid_hint"):
        _lexical_hint(raw)


@pytest.mark.parametrize("raw", ["C:\\x.md", "D:\\abs\\file.md"])
def test_lexical_hint_rejects_windows_absolute(raw: str) -> None:
    with pytest.raises(AdapterError, match="binding.invalid_hint"):
        _lexical_hint(raw)


@pytest.mark.parametrize("raw", ["../outside.md", "pages/../../x.md", "a\\..\\b.md"])
def test_lexical_hint_rejects_dotdot_escape(raw: str) -> None:
    with pytest.raises(AdapterError, match="binding.invalid_hint"):
        _lexical_hint(raw)


@pytest.mark.parametrize("raw", ["pages/a.txt", "noext", "pages/a.md.bak"])
def test_lexical_hint_rejects_non_markdown_suffix(raw: str) -> None:
    with pytest.raises(AdapterError, match="binding.invalid_hint"):
        _lexical_hint(raw)


# ---------------------------------------------------------------------------
# _binding_json：三字段序列化（独立契约）
# ---------------------------------------------------------------------------


def test_binding_json_serializes_three_fields_with_trailing_newline() -> None:
    binding = Binding("s", Path("/root"), Path("pages/a.md"))
    payload = _binding_json(binding)
    assert json.loads(payload) == {
        "session_id": "s",
        "root": "/root",
        "last_page_hint": "pages/a.md",
    }
    assert payload.endswith(b"\n")


def test_binding_json_serializes_none_hint() -> None:
    binding = Binding("s", Path("/root"), None)
    payload = _binding_json(binding)
    assert json.loads(payload) == {
        "session_id": "s",
        "root": "/root",
        "last_page_hint": None,
    }


def test_binding_json_preserves_unicode_without_ascii_escape() -> None:
    binding = Binding("会话", Path("/根"), None)
    payload = _binding_json(binding)
    # ensure_ascii=False：非 ASCII 字符以 UTF-8 原字节出现，不转义为 \uXXXX
    assert "会话".encode() in payload
    assert "/根".encode() in payload
    assert json.loads(payload) == {"session_id": "会话", "root": "/根", "last_page_hint": None}


def test_binding_json_normalizes_hint_to_posix_form() -> None:
    binding = Binding("s", Path("/root"), Path("a/b.md"))
    payload = _binding_json(binding)
    assert json.loads(payload)["last_page_hint"] == "a/b.md"


# ---------------------------------------------------------------------------
# bind 编排：受接口约束的替身替换 FS 协作者
# ---------------------------------------------------------------------------


@dataclass
class _BindDoubles:
    require_data_dir: MagicMock
    canonical_root: MagicMock
    require_hint_current: MagicMock
    ensure_container: MagicMock
    require_safe_target: MagicMock
    write_atomic: MagicMock


@pytest.fixture
def bind_doubles() -> Iterator[_BindDoubles]:
    """精确替换 bind 的六个 FS 协作者；保留 binding_path/_lexical_hint/_binding_json 真实。"""
    with ExitStack() as stack:
        yield _BindDoubles(
            require_data_dir=stack.enter_context(
                patch("adapter.bindings._require_data_dir", autospec=True)
            ),
            canonical_root=stack.enter_context(
                patch("adapter.bindings._canonical_root", autospec=True)
            ),
            require_hint_current=stack.enter_context(
                patch("adapter.bindings._require_hint_current", autospec=True)
            ),
            ensure_container=stack.enter_context(
                patch("adapter.bindings._ensure_container", autospec=True)
            ),
            require_safe_target=stack.enter_context(
                patch("adapter.bindings._require_safe_target_for_overwrite", autospec=True)
            ),
            write_atomic=stack.enter_context(
                patch("adapter.bindings._write_binding_atomic", autospec=True)
            ),
        )


def test_bind_orchestration_with_hint_writes_payload_once_and_returns_canonical(
    bind_doubles: _BindDoubles,
) -> None:
    canonical_data_dir = Path("/canonical-data")
    canonical_root = Path("/canonical-root")
    data_dir = Path("/data")
    root = Path("/root")

    bind_doubles.require_data_dir.return_value = canonical_data_dir
    bind_doubles.canonical_root.return_value = canonical_root
    written: list[tuple[Path, bytes]] = []

    def record_write(path: Path, payload: bytes) -> None:
        written.append((path, payload))

    bind_doubles.write_atomic.side_effect = record_write

    result = bind(data_dir, Binding("s", root, Path("pages/a.md")))

    assert result == Binding("s", canonical_root, Path("pages/a.md"))
    # 恰好一次耐久写：重复写是可见副作用
    assert len(written) == 1
    target, payload = written[0]
    assert target == binding_path(canonical_data_dir, "s")
    assert json.loads(payload) == {
        "session_id": "s",
        "root": "/canonical-root",
        "last_page_hint": "pages/a.md",
    }
    assert payload.endswith(b"\n")
    bind_doubles.require_data_dir.assert_called_once_with(data_dir)
    bind_doubles.canonical_root.assert_called_once_with(root)
    bind_doubles.require_hint_current.assert_called_once_with(
        PurePosixPath("pages/a.md"), canonical_root
    )
    bind_doubles.ensure_container.assert_called_once_with(canonical_data_dir / "bindings")
    bind_doubles.require_safe_target.assert_called_once_with(target)


def test_bind_orchestration_without_hint_skips_hint_current(
    bind_doubles: _BindDoubles,
) -> None:
    canonical_data_dir = Path("/canonical-data")
    canonical_root = Path("/canonical-root")

    bind_doubles.require_data_dir.return_value = canonical_data_dir
    bind_doubles.canonical_root.return_value = canonical_root
    written: list[tuple[Path, bytes]] = []

    def record_write(path: Path, payload: bytes) -> None:
        written.append((path, payload))

    bind_doubles.write_atomic.side_effect = record_write

    result = bind(Path("/data"), Binding("s", Path("/root"), None))

    assert result == Binding("s", canonical_root, None)
    assert len(written) == 1
    _target, payload = written[0]
    assert json.loads(payload)["last_page_hint"] is None
    bind_doubles.require_hint_current.assert_not_called()


def test_bind_rejects_invalid_session_before_any_filesystem_side_effect(
    bind_doubles: _BindDoubles,
) -> None:
    with pytest.raises(AdapterError, match="binding.invalid_session") as caught:
        bind(Path("/data"), Binding("\ud800", Path("/root"), None))
    assert caught.value.exit_code == EXIT_USAGE
    # 会话验证是入口第一步：早于任何路径计算与 bindings/ 目录创建
    bind_doubles.require_data_dir.assert_not_called()
    bind_doubles.canonical_root.assert_not_called()
    bind_doubles.ensure_container.assert_not_called()
    bind_doubles.write_atomic.assert_not_called()


def test_bind_rejects_lexically_invalid_hint_before_container_and_write(
    bind_doubles: _BindDoubles,
) -> None:
    canonical_data_dir = Path("/canonical-data")
    canonical_root = Path("/canonical-root")

    bind_doubles.require_data_dir.return_value = canonical_data_dir
    bind_doubles.canonical_root.return_value = canonical_root
    with pytest.raises(AdapterError, match="binding.invalid_hint"):
        bind(Path("/data"), Binding("s", Path("/root"), Path("../escape.md")))

    # 词法校验（真实纯函数）先于 FS 协作者
    bind_doubles.require_hint_current.assert_not_called()
    bind_doubles.ensure_container.assert_not_called()
    bind_doubles.write_atomic.assert_not_called()


def test_bind_propagates_data_dir_failure_without_writing(
    bind_doubles: _BindDoubles,
) -> None:
    canonical_root = Path("/canonical-root")

    bind_doubles.require_data_dir.side_effect = AdapterError(
        "binding.data_dir_unavailable", Path("/data"), exit_code=4
    )
    bind_doubles.canonical_root.return_value = canonical_root
    with pytest.raises(AdapterError, match="binding.data_dir_unavailable"):
        bind(Path("/data"), Binding("s", Path("/root"), None))

    bind_doubles.canonical_root.assert_not_called()
    bind_doubles.write_atomic.assert_not_called()
