from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

import pytest

from draft.diagnostic_codes import CheckCode, QueryCode, RootCode
from draft.errors import DraftError
from draft.graph import (
    FORMAT_VERSION,
    MarkerState,
    is_external_destination,
    parse_marker_value,
    read_marker,
    read_marker_snapshot,
    resolve_local_destination,
)
from draft.models import RootState
from draft.vocabulary import ConclusionsState

MARKER = Path(".draft/draft.json")


# ---------------------------------------------------------------------------
# 受管根协议权威类型
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("root_state", tuple(RootState))
def test_root_state_round_trips_through_authority(root_state: RootState) -> None:
    assert RootState(root_state.value) is root_state


@pytest.mark.parametrize("code", tuple(RootCode))
def test_root_code_round_trips_through_authority(code: RootCode) -> None:
    assert RootCode(code.value) is code


@pytest.mark.parametrize("code", tuple(QueryCode))
def test_query_code_round_trips_through_authority(code: QueryCode) -> None:
    assert QueryCode(code.value) is code


# ---------------------------------------------------------------------------
# parse_marker_value（正式三键协议）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("root_state", tuple(RootState))
@pytest.mark.parametrize("conclusions_state", tuple(ConclusionsState))
def test_parse_marker_value_accepts_exact_managed_root_state(
    root_state: RootState, conclusions_state: ConclusionsState
) -> None:
    state = parse_marker_value(
        {
            "format_version": FORMAT_VERSION,
            "root_state": root_state.value,
            "conclusions_state": conclusions_state.value,
        },
        MARKER,
    )
    assert state.root_state is root_state
    assert state.conclusions_state is conclusions_state


def test_parse_marker_value_accepts_final_registered_marker() -> None:
    state = parse_marker_value(
        {
            "format_version": FORMAT_VERSION,
            "root_state": RootState.REGISTERED.value,
            "conclusions_state": ConclusionsState.NOT_GENERATED.value,
        },
        MARKER,
    )
    assert state == MarkerState(
        version=FORMAT_VERSION,
        root_state=RootState.REGISTERED,
        conclusions_state=ConclusionsState.NOT_GENERATED,
    )


def test_parse_marker_value_rejects_old_two_key_marker() -> None:
    """旧 {"format_version":1,"conclusions_state":...} 标记有意以 format.invalid 拒绝，无迁移。"""
    with pytest.raises(DraftError) as caught:
        parse_marker_value(
            {"format_version": FORMAT_VERSION, "conclusions_state": "not_generated"}, MARKER
        )
    assert caught.value.code == CheckCode.FORMAT_INVALID.value


def test_parse_marker_value_rejects_old_version_only_marker() -> None:
    with pytest.raises(DraftError) as caught:
        parse_marker_value({"format_version": FORMAT_VERSION}, MARKER)
    assert caught.value.code == CheckCode.FORMAT_INVALID.value


def test_parse_marker_value_rejects_unknown_root_state() -> None:
    with pytest.raises(DraftError) as caught:
        parse_marker_value(
            {
                "format_version": FORMAT_VERSION,
                "root_state": "adopting",
                "conclusions_state": "not_generated",
            },
            MARKER,
        )
    assert caught.value.code == CheckCode.FORMAT_INVALID.value


def test_parse_marker_value_rejects_non_string_root_state() -> None:
    with pytest.raises(DraftError) as caught:
        parse_marker_value(
            {
                "format_version": FORMAT_VERSION,
                "root_state": 1,
                "conclusions_state": "not_generated",
            },
            MARKER,
        )
    assert caught.value.code == CheckCode.FORMAT_INVALID.value


def test_parse_marker_value_rejects_extra_field() -> None:
    with pytest.raises(DraftError) as caught:
        parse_marker_value(
            {
                "format_version": FORMAT_VERSION,
                "root_state": "registered",
                "conclusions_state": "not_generated",
                "extra": 1,
            },
            MARKER,
        )
    assert caught.value.code == CheckCode.FORMAT_INVALID.value


def test_parse_marker_value_rejects_provisional_keys() -> None:
    """正式 marker 解析器不接受 provisional 键；adopt 桥接走私有解析。"""
    with pytest.raises(DraftError) as caught:
        parse_marker_value(
            {
                "format_version": FORMAT_VERSION,
                "conclusions_state": "not_generated",
                "adopt_state": "provisional",
                "candidate_manifest": [],
            },
            MARKER,
        )
    assert caught.value.code == CheckCode.FORMAT_INVALID.value


@pytest.mark.parametrize("decoded", [[], "x", 1, None])
def test_parse_marker_value_rejects_non_dict(decoded: object) -> None:
    with pytest.raises(DraftError) as caught:
        parse_marker_value(decoded, MARKER)
    assert caught.value.code == CheckCode.FORMAT_INVALID.value


def test_parse_marker_value_rejects_bool_version() -> None:
    # bool 是 int 子类，但不得被接受为版本号。
    with pytest.raises(DraftError) as caught:
        parse_marker_value({"format_version": True}, MARKER)
    assert caught.value.code == CheckCode.FORMAT_INVALID.value


def test_parse_marker_value_rejects_non_integer_version() -> None:
    with pytest.raises(DraftError) as caught:
        parse_marker_value({"format_version": "1"}, MARKER)
    assert caught.value.code == CheckCode.FORMAT_INVALID.value


def test_parse_marker_value_rejects_unsupported_version() -> None:
    with pytest.raises(DraftError) as caught:
        parse_marker_value({"format_version": FORMAT_VERSION + 1}, MARKER)
    assert caught.value.code == CheckCode.FORMAT_UNSUPPORTED.value


# ---------------------------------------------------------------------------
# is_external_destination
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "destination",
    ["https://example.com", "http://example.com", "mailto:a@b.com", "scheme:x"],
)
def test_is_external_destination_recognizes_schemes(destination: str) -> None:
    assert is_external_destination(destination) is True


@pytest.mark.parametrize(
    "destination",
    ["pages/a.md", "/abs/path", "../up.md", "conclusions.md#anchor", "a b", "1abc:x"],
)
def test_is_external_destination_rejects_non_schemes(destination: str) -> None:
    assert is_external_destination(destination) is False


# ---------------------------------------------------------------------------
# resolve_local_destination
# ---------------------------------------------------------------------------


def test_resolve_local_destination_decodes_and_normalizes() -> None:
    assert resolve_local_destination(
        Path("pages/a.md"), "../conclusions%2Emd#结论", Path("/unused")
    ) == (Path("conclusions.md"), "结论")


def test_resolve_local_destination_same_directory() -> None:
    assert resolve_local_destination(Path("pages/a.md"), "b.md", Path("/unused")) == (
        Path("pages/b.md"),
        None,
    )


def test_resolve_local_destination_empty_target_returns_source() -> None:
    assert resolve_local_destination(Path("index.md"), "", Path("/unused")) == (
        Path("index.md"),
        None,
    )


def test_resolve_local_destination_keeps_anchor_only_when_separator_present() -> None:
    assert resolve_local_destination(Path("index.md"), "#section", Path("/unused")) == (
        Path("index.md"),
        "section",
    )


def test_resolve_local_destination_anchor_without_hash_is_not_an_anchor() -> None:
    # 没有 '#' 分隔符时，井号是路径的一部分，不当作锚点。
    target, anchor = resolve_local_destination(Path("index.md"), "page%23note.md", Path("/unused"))
    assert target == Path("page#note.md")
    assert anchor is None


def test_resolve_local_destination_rejects_external_scheme() -> None:
    with pytest.raises(DraftError) as caught:
        resolve_local_destination(Path("index.md"), "https://example.com", Path("/unused"))
    assert caught.value.code == "link.external"


def test_resolve_local_destination_rejects_absolute_path() -> None:
    with pytest.raises(DraftError) as caught:
        resolve_local_destination(Path("index.md"), "/etc/passwd", Path("/unused"))
    assert caught.value.code == CheckCode.LINK_ESCAPE.value


def test_resolve_local_destination_rejects_root_escape() -> None:
    with pytest.raises(DraftError) as caught:
        resolve_local_destination(Path("index.md"), "../outside.md", Path("/unused"))
    assert caught.value.code == CheckCode.LINK_ESCAPE.value


def test_resolve_local_destination_rejects_self_escape_via_dotdot() -> None:
    # pages/a.md -> ../../top.md 逃逸到根之上。
    with pytest.raises(DraftError) as caught:
        resolve_local_destination(Path("pages/a.md"), "../../top.md", Path("/unused"))
    assert caught.value.code == CheckCode.LINK_ESCAPE.value


def test_resolve_local_destination_normalizes_internal_dotdot() -> None:
    target, _ = resolve_local_destination(Path("pages/sub/a.md"), "../b.md", Path("/unused"))
    assert target == Path("pages/b.md")


# ---------------------------------------------------------------------------
# 第十四轮 M2：marker 身份读取的稳定分类边界。单元测试不得访问真实文件系统：
# 假 root Path("/draft")，按 marker 精确路径 monkeypatch Path.lstat 这个稳定
# 协作者边界；EACCES/EIO 一律映射 format.invalid，不泄漏原始 OSError。
# missing/directory/symlink/非 UTF-8/非法 JSON/unsupported 由既有 integration/
# parser 测试覆盖。
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("error", ["eacces", "eio"])
def test_read_marker_identity_stat_failure_is_stable(
    monkeypatch: pytest.MonkeyPatch,
    error: str,
) -> None:
    """marker 身份读取 EACCES/EIO：read_marker 抛 format.invalid（round-14 M2）。

    修复前 ``_read_marker_file`` 的 ``Path.is_symlink()`` 不吞 OSError，EACCES/EIO
    会原样泄漏（红灯）；修复后一次受保护 ``os.lstat`` 分类映射稳定码。round-15 H1
    起 parent（.draft）分类先于 leaf，fake 让 parent 分类成功、只对 marker 注入失败。
    """
    root = Path("/draft")
    marker = root / ".draft/draft.json"

    def fail_lstat(path: str | bytes | os.PathLike[str], *args: object) -> os.stat_result:
        value = os.fspath(path)
        if value == os.fspath(root / ".draft"):
            return _stat_result(stat.S_IFDIR | 0o755)
        assert value == os.fspath(marker)
        if error == "eacces":
            raise PermissionError(13, "Permission denied")
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(os, "lstat", fail_lstat)
    with pytest.raises(DraftError) as caught:
        read_marker(root)
    assert caught.value.code == CheckCode.FORMAT_INVALID.value
    assert caught.value.paths == (marker,)


# ---------------------------------------------------------------------------
# 第十五轮 H1：marker 一次读取的不可变 snapshot。unit 不访问真实文件系统：假 root
# Path("/draft")，按精确路径 monkeypatch os.lstat 与 Path.read_bytes 这个稳定协作者
# 边界；parent（.draft）与 leaf（marker）各自分类，bytes 只读取一次。
# ---------------------------------------------------------------------------

CANDIDATE_BYTES = (
    b'{"format_version":1,"root_state":"candidate","conclusions_state":"not_generated"}'
)
CANDIDATE_REGISTERED_BYTES = (
    b'{"format_version":1,"root_state":"registered","conclusions_state":"not_generated"}'
)


def _stat_result(mode: int) -> os.stat_result:
    return os.stat_result((mode, 0, 0, 0, 0, 0, 0, 0, 0, 0))


def _marker_lstat(internal: Path, marker: Path) -> object:
    """parent 为目录、marker 为普通文件的受接口约束 lstat 替身。"""

    def fake(path: str | bytes | os.PathLike[str], *args: object) -> os.stat_result:
        value = os.fspath(path)
        if value == os.fspath(internal):
            return _stat_result(stat.S_IFDIR | 0o755)
        if value == os.fspath(marker):
            return _stat_result(stat.S_IFREG | 0o644)
        raise AssertionError(value)

    return fake


def test_read_marker_snapshot_single_read_and_immutable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """valid candidate：一次读取产生 state/bytes/digest 同源快照（round-15 H1）。

    摘要由同一 raw_bytes 计算，不是再次读取 live 所得；live 返回变化不改变已构造
    snapshot，证明对象不可变。
    """
    root = Path("/draft")
    internal = root / ".draft"
    marker = root / ".draft/draft.json"
    calls: list[Path] = []

    def counting_read_bytes(self: Path) -> bytes:
        calls.append(self)
        return CANDIDATE_BYTES

    monkeypatch.setattr(Path, "read_bytes", counting_read_bytes)
    monkeypatch.setattr(os, "lstat", _marker_lstat(internal, marker))

    snapshot = read_marker_snapshot(root)
    assert snapshot.state.root_state is RootState.CANDIDATE
    assert snapshot.state.conclusions_state is ConclusionsState.NOT_GENERATED
    assert snapshot.raw_bytes == CANDIDATE_BYTES
    assert snapshot.sha256 == hashlib.sha256(CANDIDATE_BYTES).hexdigest()
    assert calls == [marker]

    # live 返回变化：已构造 snapshot 不变，摘要不是再次读取所得。
    def registered_bytes(self: Path) -> bytes:
        return CANDIDATE_REGISTERED_BYTES

    monkeypatch.setattr(Path, "read_bytes", registered_bytes)
    assert snapshot.raw_bytes == CANDIDATE_BYTES
    assert snapshot.sha256 == hashlib.sha256(CANDIDATE_BYTES).hexdigest()


def test_read_marker_returns_snapshot_state_without_second_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """read_marker 是返回 snapshot.state 的兼容包装：一次读取，不维护第二套读取（round-15 H1）。"""
    root = Path("/draft")
    internal = root / ".draft"
    marker = root / ".draft/draft.json"
    calls: list[Path] = []

    def counting_read_bytes(self: Path) -> bytes:
        calls.append(self)
        return CANDIDATE_BYTES

    monkeypatch.setattr(Path, "read_bytes", counting_read_bytes)
    monkeypatch.setattr(os, "lstat", _marker_lstat(internal, marker))

    state = read_marker(root)
    assert state.root_state is RootState.CANDIDATE
    assert state.conclusions_state is ConclusionsState.NOT_GENERATED
    assert calls == [marker]


@pytest.mark.parametrize(
    ("parent_kind", "leaf_kind", "read_error", "expected_code", "expected_path"),
    [
        (
            "missing",
            "missing",
            None,
            CheckCode.FORMAT_MISSING.value,
            Path("/draft/.draft/draft.json"),
        ),
        ("symlink", "unused", None, CheckCode.PATH_SYMLINK_ESCAPE.value, Path("/draft/.draft")),
        ("file", "unused", None, CheckCode.FORMAT_INVALID.value, Path("/draft/.draft/draft.json")),
        ("other", "unused", None, CheckCode.FORMAT_INVALID.value, Path("/draft/.draft/draft.json")),
        (
            "directory",
            "missing",
            None,
            CheckCode.FORMAT_MISSING.value,
            Path("/draft/.draft/draft.json"),
        ),
        (
            "directory",
            "symlink",
            None,
            CheckCode.PATH_SYMLINK_ESCAPE.value,
            Path("/draft/.draft/draft.json"),
        ),
        (
            "directory",
            "directory",
            None,
            CheckCode.FORMAT_INVALID.value,
            Path("/draft/.draft/draft.json"),
        ),
        (
            "directory",
            "other",
            None,
            CheckCode.FORMAT_INVALID.value,
            Path("/draft/.draft/draft.json"),
        ),
        (
            "eacces",
            "unused",
            None,
            CheckCode.FORMAT_INVALID.value,
            Path("/draft/.draft/draft.json"),
        ),
        ("eio", "unused", None, CheckCode.FORMAT_INVALID.value, Path("/draft/.draft/draft.json")),
        (
            "directory",
            "file",
            "eio",
            CheckCode.FORMAT_INVALID.value,
            Path("/draft/.draft/draft.json"),
        ),
    ],
)
def test_read_marker_snapshot_classification_matrix(
    monkeypatch: pytest.MonkeyPatch,
    parent_kind: str,
    leaf_kind: str,
    read_error: str | None,
    expected_code: str,
    expected_path: Path,
) -> None:
    """.draft 中间组件 × marker 最终节点分类矩阵：每个 case 只验证一个分区（round-15 H1）。

    parent 在分类失败/非目录时短路，leaf 不得被读取（"unused" 分支断言）；leaf 的
    missing/symlink/non-regular/EACCES/EIO 语义与 round-14 M2 一致，错误路径按各自
    节点报告。
    """
    root = Path("/draft")
    internal = root / ".draft"
    marker = root / ".draft/draft.json"
    kinds = {
        "symlink": stat.S_IFLNK | 0o777,
        "directory": stat.S_IFDIR | 0o755,
        "file": stat.S_IFREG | 0o644,
        "other": stat.S_IFIFO | 0o644,
    }

    def fake_lstat(path: str | bytes | os.PathLike[str], *args: object) -> os.stat_result:
        value = os.fspath(path)
        if value == os.fspath(internal):
            if parent_kind == "missing":
                raise FileNotFoundError(2, "No such file", value)
            if parent_kind == "eacces":
                raise PermissionError(13, "Permission denied")
            if parent_kind == "eio":
                raise OSError(5, "Input/output error")
            return _stat_result(kinds[parent_kind])
        assert value == os.fspath(marker)
        if leaf_kind == "missing":
            raise FileNotFoundError(2, "No such file", value)
        assert leaf_kind != "unused", "父组件分类失败后不得再读取 marker"
        return _stat_result(kinds[leaf_kind])

    monkeypatch.setattr(os, "lstat", fake_lstat)
    if read_error is not None:

        def fail_read_bytes(self: Path) -> bytes:
            raise OSError(5, "Input/output error")

        monkeypatch.setattr(Path, "read_bytes", fail_read_bytes)

    with pytest.raises(DraftError) as caught:
        read_marker_snapshot(root)
    assert caught.value.code == expected_code
    assert caught.value.paths == (expected_path,)


@pytest.mark.parametrize(
    ("raw", "expected_code"),
    [
        (b"\xff\xfe\x00 garbage", CheckCode.FORMAT_INVALID.value),
        (b"{not json", CheckCode.FORMAT_INVALID.value),
        (b"[]", CheckCode.FORMAT_INVALID.value),
        (
            b'{"format_version": 2, "root_state": "candidate", '
            b'"conclusions_state": "not_generated"}',
            CheckCode.FORMAT_UNSUPPORTED.value,
        ),
        (
            b'{"format_version": 1, "root_state": "adopting", '
            b'"conclusions_state": "not_generated"}',
            CheckCode.FORMAT_INVALID.value,
        ),
    ],
)
def test_read_marker_snapshot_content_errors(
    monkeypatch: pytest.MonkeyPatch,
    raw: bytes,
    expected_code: str,
) -> None:
    """内容错误经 snapshot 读取路径：非法 UTF-8/JSON/非 dict/schema 与版本语义保持既有。

    parse_marker_value 的纯 parser tests 保留；这里覆盖 snapshot 读取器自身从同一
    bytes 解析的内容错误（round-15 H1）。
    """
    root = Path("/draft")
    internal = root / ".draft"
    marker = root / ".draft/draft.json"

    def fixed_bytes(self: Path) -> bytes:
        return raw

    monkeypatch.setattr(Path, "read_bytes", fixed_bytes)
    monkeypatch.setattr(os, "lstat", _marker_lstat(internal, marker))

    with pytest.raises(DraftError) as caught:
        read_marker_snapshot(root)
    assert caught.value.code == expected_code
    assert caught.value.paths == (marker,)
