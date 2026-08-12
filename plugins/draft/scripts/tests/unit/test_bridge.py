"""bridge_paths 与 _parse_data_dir 单元测试：受接口约束的替身替换 FS 协作者。

bridge_paths 只替换 installation_key（与缺省路径分支的 per_user_runtime_dir），
比较完整 BridgePaths；_parse_data_dir 只替换 _canonical_data_dir，覆盖载荷解析矩阵。
installation_key 的真实路径解析与 publish/load/reset 路由保留在集成测试。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from adapter.bridge import (
    BridgePaths,
    _parse_data_dir,  # pyright: ignore[reportPrivateUsage]  # _parse_data_dir 为独立载荷解析契约，直接验证字段与类型分支
    bridge_paths,
)
from adapter.errors import AdapterError
from pytest_mock import MockerFixture

_BRIDGE_PATH = Path("/bridge.json")

# ---------------------------------------------------------------------------
# bridge_paths：显式 runtime_dir 分支（只替换 installation_key）
# ---------------------------------------------------------------------------


def test_bridge_paths_explicit_runtime_dir_uses_installation_key(mocker: MockerFixture) -> None:
    entry = Path("/entry/draft.py")
    runtime_dir = Path("/run")
    fixed_key = "abc123def456"
    key_fn = mocker.patch("adapter.bridge.installation_key", autospec=True)
    key_fn.return_value = fixed_key
    paths = bridge_paths(entry, runtime_dir)
    base = runtime_dir / "draft" / "bridges"
    assert paths == BridgePaths(
        json=base / f"{fixed_key}.json",
        conflict=base / f"{fixed_key}.conflict",
        lock=base / f"{fixed_key}.lock",
    )
    key_fn.assert_called_once_with(entry)


def test_bridge_paths_explicit_runtime_dir_distinct_keys_distinct_files(
    mocker: MockerFixture,
) -> None:
    entry = Path("/entry/draft.py")
    runtime_dir = Path("/run")
    key_fn = mocker.patch("adapter.bridge.installation_key", autospec=True)
    key_fn.side_effect = ["key-one", "key-two"]
    first = bridge_paths(entry, runtime_dir)
    second = bridge_paths(entry, runtime_dir)
    assert first.json != second.json
    assert first.conflict != second.conflict
    assert first.lock != second.lock
    assert first.json.parent == second.json.parent


# ---------------------------------------------------------------------------
# bridge_paths：缺省 runtime_dir 分支（替换 installation_key 与 per_user_runtime_dir）
# ---------------------------------------------------------------------------


def test_bridge_paths_default_runtime_dir_uses_per_user_runtime_dir(
    mocker: MockerFixture,
) -> None:
    entry = Path("/entry/draft.py")
    fixed_runtime = Path("/per-user-run")
    fixed_key = "def789ghi012"
    key_fn = mocker.patch("adapter.bridge.installation_key", autospec=True)
    key_fn.return_value = fixed_key
    per_user = mocker.patch("adapter.bridge.per_user_runtime_dir", autospec=True)
    per_user.return_value = fixed_runtime
    paths = bridge_paths(entry)
    base = fixed_runtime / "draft" / "bridges"
    assert paths == BridgePaths(
        json=base / f"{fixed_key}.json",
        conflict=base / f"{fixed_key}.conflict",
        lock=base / f"{fixed_key}.lock",
    )
    key_fn.assert_called_once_with(entry)
    per_user.assert_called_once_with()


def test_bridge_paths_default_branch_does_not_touch_installation_key_real_resolve(
    mocker: MockerFixture,
) -> None:
    """缺省分支只通过替身获得 runtime_dir 与 key，不触发真实路径解析。"""
    key_fn = mocker.patch("adapter.bridge.installation_key", autospec=True)
    key_fn.return_value = "k"
    per_user = mocker.patch("adapter.bridge.per_user_runtime_dir", autospec=True)
    per_user.return_value = Path("/run")
    paths = bridge_paths(Path("/entry/draft.py"))
    assert paths.json.name == "k.json"
    assert paths.json.parent == Path("/run/draft/bridges")


# ---------------------------------------------------------------------------
# _parse_data_dir：载荷解析矩阵（只替换 _canonical_data_dir）
# ---------------------------------------------------------------------------


@pytest.fixture
def canonical_data_dir_stub(mocker: MockerFixture) -> Iterator[MagicMock]:
    yield mocker.patch("adapter.bridge._canonical_data_dir", autospec=True)


@pytest.mark.parametrize("raw", [[1, 2], 42, "string", None, True, 3.14])
def test_parse_data_dir_rejects_non_object(raw: object, canonical_data_dir_stub: MagicMock) -> None:
    with pytest.raises(AdapterError, match="plugin.context_invalid") as caught:
        _parse_data_dir(raw, _BRIDGE_PATH)
    assert caught.value.exit_code == 4
    canonical_data_dir_stub.assert_not_called()


@pytest.mark.parametrize(
    "payload",
    [
        {"schema_version": 1},
        {"data_dir": "/d"},
        {"schema_version": 1, "data_dir": "/d", "extra": "x"},
        {},
    ],
)
def test_parse_data_dir_rejects_wrong_field_set(
    payload: dict[str, object], canonical_data_dir_stub: MagicMock
) -> None:
    with pytest.raises(AdapterError, match="plugin.context_invalid"):
        _parse_data_dir(payload, _BRIDGE_PATH)
    canonical_data_dir_stub.assert_not_called()


def test_parse_data_dir_rejects_bool_version(canonical_data_dir_stub: MagicMock) -> None:
    # bool 是 int 子类且 True == 1：必须先显式拒绝，不可借 int 比较放行。
    with pytest.raises(AdapterError, match="plugin.context_invalid") as caught:
        _parse_data_dir({"schema_version": True, "data_dir": "/data"}, _BRIDGE_PATH)
    assert caught.value.exit_code == 4
    canonical_data_dir_stub.assert_not_called()


@pytest.mark.parametrize("version", [0, 2, -1])
def test_parse_data_dir_rejects_wrong_version(
    version: int, canonical_data_dir_stub: MagicMock
) -> None:
    with pytest.raises(AdapterError, match="plugin.context_invalid"):
        _parse_data_dir({"schema_version": version, "data_dir": "/data"}, _BRIDGE_PATH)
    canonical_data_dir_stub.assert_not_called()


@pytest.mark.parametrize("data_dir_value", [42, None, True, ["/d"], 3.14])
def test_parse_data_dir_rejects_non_string_dir(
    data_dir_value: object, canonical_data_dir_stub: MagicMock
) -> None:
    with pytest.raises(AdapterError, match="plugin.context_invalid"):
        _parse_data_dir({"schema_version": 1, "data_dir": data_dir_value}, _BRIDGE_PATH)
    canonical_data_dir_stub.assert_not_called()


def test_parse_data_dir_valid_payload_returns_canonical_path(
    canonical_data_dir_stub: MagicMock,
) -> None:
    canonical = Path("/canonical-data")
    canonical_data_dir_stub.return_value = canonical
    result = _parse_data_dir({"schema_version": 1, "data_dir": "/data"}, _BRIDGE_PATH)
    assert result == canonical
    canonical_data_dir_stub.assert_called_once_with(Path("/data"))


def test_parse_data_dir_valid_payload_passes_path_value_to_canonical(
    canonical_data_dir_stub: MagicMock,
) -> None:
    """data_dir 字符串经 Path() 构造后交给 _canonical_data_dir，不做自身解析。"""
    canonical_data_dir_stub.return_value = Path("/canonical")
    _parse_data_dir({"schema_version": 1, "data_dir": "/some/where"}, _BRIDGE_PATH)
    canonical_data_dir_stub.assert_called_once_with(Path("/some/where"))
