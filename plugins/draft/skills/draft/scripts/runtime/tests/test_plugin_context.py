from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import shutil
import stat
import tempfile
from multiprocessing.queues import Queue
from multiprocessing.synchronize import Barrier
from pathlib import Path

import pytest

from draft.errors import DraftError
from draft.plugin_context import (
    BridgePaths,
    PluginContext,
    bridge_paths,
    installation_key,
    load,
    per_user_runtime_dir,
    publish,
    reset,
)


def make_paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    entry = tmp_path / "skill" / "scripts" / "draft"
    entry.parent.mkdir(parents=True)
    entry.write_text("#!/bin/sh\n", encoding="utf-8")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    return entry, data_dir, runtime_dir


def target_path(paths: BridgePaths, target: str) -> Path:
    return {
        "json": paths.json,
        "conflict": paths.conflict,
        "lock": paths.lock,
        "parent": paths.json.parent,
    }[target]


def write_bridge(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


# 步骤 1：安装键、路径和模式


def test_installation_key_hashes_the_resolved_posix_entry(tmp_path: Path) -> None:
    entry = tmp_path / "skill/scripts/draft"
    entry.parent.mkdir(parents=True)
    entry.write_text("#!/bin/sh\n", encoding="utf-8")
    expected = hashlib.sha256(str(entry.resolve()).encode("utf-8")).hexdigest()
    assert installation_key(entry) == expected


def test_bridge_paths_live_under_runtime_dir(tmp_path: Path) -> None:
    entry, _, runtime_dir = make_paths(tmp_path)
    key = installation_key(entry)
    paths = bridge_paths(entry, runtime_dir)
    base = runtime_dir / "draft" / "bridges"
    assert paths == BridgePaths(
        json=base / f"{key}.json",
        conflict=base / f"{key}.conflict",
        lock=base / f"{key}.lock",
    )


def test_publish_writes_exact_two_field_schema(tmp_path: Path) -> None:
    entry, data_dir, runtime_dir = make_paths(tmp_path)
    publish(entry, data_dir, runtime_dir)
    payload = json.loads(bridge_paths(entry, runtime_dir).json.read_text(encoding="utf-8"))
    assert payload == {"schema_version": 1, "data_dir": str(data_dir.resolve())}


def test_publish_returns_context_with_canonical_data_dir(tmp_path: Path) -> None:
    entry, data_dir, runtime_dir = make_paths(tmp_path)
    context = publish(entry, data_dir, runtime_dir)
    assert context == PluginContext(data_dir=data_dir.resolve())


def test_load_returns_none_without_bridge(tmp_path: Path) -> None:
    entry, _, runtime_dir = make_paths(tmp_path)
    assert load(entry, runtime_dir) is None


def test_load_round_trips_published_context(tmp_path: Path) -> None:
    entry, data_dir, runtime_dir = make_paths(tmp_path)
    publish(entry, data_dir, runtime_dir)
    assert load(entry, runtime_dir) == PluginContext(data_dir=data_dir.resolve())


def test_unknown_bridge_field_is_invalid(tmp_path: Path) -> None:
    entry, data_dir, runtime_dir = make_paths(tmp_path)
    publish(entry, data_dir, runtime_dir)
    path = bridge_paths(entry, runtime_dir).json
    path.write_text(
        json.dumps({"schema_version": 1, "data_dir": str(data_dir), "runtime_version": "1"}),
        encoding="utf-8",
    )
    with pytest.raises(DraftError, match="plugin.context_invalid"):
        load(entry, runtime_dir)


def test_unknown_schema_version_is_invalid(tmp_path: Path) -> None:
    entry, data_dir, runtime_dir = make_paths(tmp_path)
    publish(entry, data_dir, runtime_dir)
    path = bridge_paths(entry, runtime_dir).json
    write_bridge(path, {"schema_version": 2, "data_dir": str(data_dir)})
    with pytest.raises(DraftError, match="plugin.context_invalid"):
        load(entry, runtime_dir)


def test_relative_data_dir_is_invalid(tmp_path: Path) -> None:
    entry, data_dir, runtime_dir = make_paths(tmp_path)
    publish(entry, data_dir, runtime_dir)
    path = bridge_paths(entry, runtime_dir).json
    write_bridge(path, {"schema_version": 1, "data_dir": "relative/data"})
    with pytest.raises(DraftError, match="plugin.context_invalid"):
        load(entry, runtime_dir)


def test_missing_data_dir_target_is_invalid(tmp_path: Path) -> None:
    entry, data_dir, runtime_dir = make_paths(tmp_path)
    publish(entry, data_dir, runtime_dir)
    path = bridge_paths(entry, runtime_dir).json
    write_bridge(path, {"schema_version": 1, "data_dir": str(tmp_path / "gone")})
    with pytest.raises(DraftError, match="plugin.context_invalid"):
        load(entry, runtime_dir)


def test_data_dir_target_that_is_a_file_is_invalid(tmp_path: Path) -> None:
    entry, data_dir, runtime_dir = make_paths(tmp_path)
    publish(entry, data_dir, runtime_dir)
    impostor = tmp_path / "impostor"
    impostor.write_text("x", encoding="utf-8")
    path = bridge_paths(entry, runtime_dir).json
    write_bridge(path, {"schema_version": 1, "data_dir": str(impostor)})
    with pytest.raises(DraftError, match="plugin.context_invalid"):
        load(entry, runtime_dir)


def test_corrupt_json_is_invalid_on_load(tmp_path: Path) -> None:
    entry, data_dir, runtime_dir = make_paths(tmp_path)
    publish(entry, data_dir, runtime_dir)
    path = bridge_paths(entry, runtime_dir).json
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(DraftError, match="plugin.context_invalid"):
        load(entry, runtime_dir)


def test_non_object_json_is_invalid(tmp_path: Path) -> None:
    entry, data_dir, runtime_dir = make_paths(tmp_path)
    publish(entry, data_dir, runtime_dir)
    path = bridge_paths(entry, runtime_dir).json
    path.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(DraftError, match="plugin.context_invalid"):
        load(entry, runtime_dir)


def test_publish_rejects_relative_data_dir(tmp_path: Path) -> None:
    entry, _, runtime_dir = make_paths(tmp_path)
    with pytest.raises(DraftError, match="plugin.context_invalid"):
        publish(entry, Path("relative/data"), runtime_dir)


# 步骤 2：文件状态和冲突


def test_different_data_dirs_create_sticky_conflict(tmp_path: Path) -> None:
    entry, first, runtime_dir = make_paths(tmp_path)
    second = tmp_path / "data-two"
    second.mkdir()
    publish(entry, first, runtime_dir)

    with pytest.raises(DraftError, match="plugin.context_conflict"):
        publish(entry, second, runtime_dir)

    paths = bridge_paths(entry, runtime_dir)
    assert paths.conflict.read_bytes() == b""
    with pytest.raises(DraftError, match="plugin.context_conflict"):
        load(entry, runtime_dir)


def test_conflict_preserves_original_json(tmp_path: Path) -> None:
    entry, first, runtime_dir = make_paths(tmp_path)
    second = tmp_path / "data-two"
    second.mkdir()
    publish(entry, first, runtime_dir)
    with pytest.raises(DraftError, match="plugin.context_conflict"):
        publish(entry, second, runtime_dir)
    payload = json.loads(bridge_paths(entry, runtime_dir).json.read_text(encoding="utf-8"))
    assert payload == {"schema_version": 1, "data_dir": str(first.resolve())}


def test_existing_conflict_sentinel_blocks_republish(tmp_path: Path) -> None:
    entry, data_dir, runtime_dir = make_paths(tmp_path)
    publish(entry, data_dir, runtime_dir)
    bridge_paths(entry, runtime_dir).conflict.touch()
    with pytest.raises(DraftError, match="plugin.context_conflict"):
        publish(entry, data_dir, runtime_dir)


def test_reset_removes_only_json_and_conflict(tmp_path: Path) -> None:
    entry, data_dir, runtime_dir = make_paths(tmp_path)
    publish(entry, data_dir, runtime_dir)
    paths = bridge_paths(entry, runtime_dir)
    paths.conflict.touch()
    reset(entry, runtime_dir)
    assert not paths.json.exists()
    assert not paths.conflict.exists()
    assert paths.lock.exists()


def test_reset_without_bridge_is_a_no_op(tmp_path: Path) -> None:
    entry, _, runtime_dir = make_paths(tmp_path)
    reset(entry, runtime_dir)
    assert load(entry, runtime_dir) is None


@pytest.mark.parametrize("target", ["json", "conflict", "lock", "parent"])
def test_load_rejects_symlink(tmp_path: Path, target: str) -> None:
    entry, data_dir, runtime_dir = make_paths(tmp_path)
    publish(entry, data_dir, runtime_dir)
    paths = bridge_paths(entry, runtime_dir)
    if target == "conflict":
        paths.conflict.touch()
    victim = target_path(paths, target)
    replacement = tmp_path / f"replacement-{target}"
    if target == "parent":
        replacement.mkdir()
        shutil.rmtree(victim)
    else:
        replacement.write_text("x", encoding="utf-8")
        victim.unlink()
    victim.symlink_to(replacement)
    with pytest.raises(DraftError, match="plugin.context_invalid"):
        load(entry, runtime_dir)
    assert victim.is_symlink()


@pytest.mark.parametrize("target", ["json", "conflict", "lock", "parent"])
def test_load_rejects_non_regular_object(tmp_path: Path, target: str) -> None:
    entry, data_dir, runtime_dir = make_paths(tmp_path)
    publish(entry, data_dir, runtime_dir)
    paths = bridge_paths(entry, runtime_dir)
    if target == "conflict":
        paths.conflict.touch()
    victim = target_path(paths, target)
    if target == "parent":
        shutil.rmtree(victim)
        victim.write_text("x", encoding="utf-8")
    else:
        victim.unlink()
        victim.mkdir()
    with pytest.raises(DraftError, match="plugin.context_invalid"):
        load(entry, runtime_dir)
    if target == "parent":
        assert victim.is_file() and not victim.is_symlink()
    else:
        assert victim.is_dir()


@pytest.mark.parametrize("target", ["json", "conflict", "lock", "parent"])
def test_load_rejects_wrong_owner(
    tmp_path: Path, target: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry, data_dir, runtime_dir = make_paths(tmp_path)
    publish(entry, data_dir, runtime_dir)
    paths = bridge_paths(entry, runtime_dir)
    if target == "conflict":
        paths.conflict.touch()
    victim = target_path(paths, target)
    foreign_uid = victim.lstat().st_uid + 1
    monkeypatch.setattr(os, "getuid", lambda: foreign_uid)
    with pytest.raises(DraftError, match="plugin.context_invalid"):
        load(entry, runtime_dir)
    assert victim.lstat().st_uid == foreign_uid - 1


@pytest.mark.parametrize("target", ["json", "conflict", "lock", "parent"])
def test_load_rejects_group_writable(tmp_path: Path, target: str) -> None:
    entry, data_dir, runtime_dir = make_paths(tmp_path)
    publish(entry, data_dir, runtime_dir)
    paths = bridge_paths(entry, runtime_dir)
    if target == "conflict":
        paths.conflict.touch()
    victim = target_path(paths, target)
    victim.chmod(victim.lstat().st_mode | 0o020)
    with pytest.raises(DraftError, match="plugin.context_invalid"):
        load(entry, runtime_dir)
    assert victim.lstat().st_mode & 0o020


def test_publish_never_follows_or_repairs_symlink(tmp_path: Path) -> None:
    entry, data_dir, runtime_dir = make_paths(tmp_path)
    publish(entry, data_dir, runtime_dir)
    paths = bridge_paths(entry, runtime_dir)
    paths.json.unlink()
    paths.json.symlink_to(tmp_path / "attacker")
    with pytest.raises(DraftError, match="plugin.context_invalid"):
        publish(entry, data_dir, runtime_dir)
    assert paths.json.is_symlink()


def test_reset_never_deletes_symlink(tmp_path: Path) -> None:
    entry, data_dir, runtime_dir = make_paths(tmp_path)
    publish(entry, data_dir, runtime_dir)
    paths = bridge_paths(entry, runtime_dir)
    paths.json.unlink()
    paths.json.symlink_to(tmp_path / "attacker")
    with pytest.raises(DraftError, match="plugin.context_invalid"):
        reset(entry, runtime_dir)
    assert paths.json.is_symlink()


# 运行目录选择


def test_per_user_runtime_dir_accepts_secure_xdg(tmp_path: Path) -> None:
    xdg = tmp_path / "xdg"
    xdg.mkdir()
    xdg.chmod(0o700)
    assert per_user_runtime_dir({"XDG_RUNTIME_DIR": str(xdg)}) == xdg


def test_per_user_runtime_dir_rejects_loose_xdg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    xdg = tmp_path / "xdg"
    xdg.mkdir()
    xdg.chmod(0o755)
    temp = tmp_path / "tmp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(temp))
    result = per_user_runtime_dir({"XDG_RUNTIME_DIR": str(xdg)})
    assert result == temp / f"draft-runtime-{os.getuid()}"
    assert stat.S_IMODE(result.lstat().st_mode) == 0o700


def test_per_user_runtime_dir_rejects_xdg_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = tmp_path / "real"
    real.mkdir()
    real.chmod(0o700)
    link = tmp_path / "link"
    link.symlink_to(real)
    temp = tmp_path / "tmp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(temp))
    result = per_user_runtime_dir({"XDG_RUNTIME_DIR": str(link)})
    assert result == temp / f"draft-runtime-{os.getuid()}"


def test_per_user_runtime_dir_falls_back_without_xdg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    temp = tmp_path / "tmp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(temp))
    result = per_user_runtime_dir({})
    assert result == temp / f"draft-runtime-{os.getuid()}"
    assert stat.S_IMODE(result.lstat().st_mode) == 0o700


def test_per_user_runtime_dir_rejects_insecure_existing_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    temp = tmp_path / "tmp"
    temp.mkdir()
    fallback = temp / f"draft-runtime-{os.getuid()}"
    fallback.mkdir()
    fallback.chmod(0o755)
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(temp))
    with pytest.raises(DraftError, match="plugin.context_invalid"):
        per_user_runtime_dir({})


def test_per_user_runtime_dir_surfaces_mkdir_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    temp = tmp_path / "tmp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(temp))

    def denied(path: object, mode: int = 0o777) -> None:
        raise PermissionError("read-only filesystem")

    monkeypatch.setattr(os, "mkdir", denied)
    with pytest.raises(DraftError, match="plugin.context_invalid"):
        per_user_runtime_dir({})


# 步骤 7：并发发布串行化


def _publish_worker(
    entry: str, data_dir: str, runtime_dir: str, barrier: Barrier, queue: Queue[str]
) -> None:
    barrier.wait(timeout=15)
    try:
        publish(Path(entry), Path(data_dir), Path(runtime_dir))
    except DraftError as exc:
        queue.put(exc.code)
    else:
        queue.put("ok")


def run_concurrent_publish(entry: Path, first: Path, second: Path, runtime_dir: Path) -> list[str]:
    context = multiprocessing.get_context("fork")
    barrier = context.Barrier(2)
    queue: Queue[str] = context.Queue()
    workers = [
        context.Process(
            target=_publish_worker,
            args=(str(entry), str(data_dir), str(runtime_dir), barrier, queue),
        )
        for data_dir in (first, second)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=30)
    for worker in workers:
        if worker.is_alive():
            worker.terminate()
            worker.join()
            raise AssertionError("concurrent publish timed out")
    return sorted(queue.get(timeout=10) for _ in workers)


def test_concurrent_publish_same_data_dir(tmp_path: Path) -> None:
    entry, data_dir, runtime_dir = make_paths(tmp_path)
    results = run_concurrent_publish(entry, data_dir, data_dir, runtime_dir)
    assert results == ["ok", "ok"]
    assert load(entry, runtime_dir) == PluginContext(data_dir=data_dir.resolve())


def test_concurrent_publish_different_data_dirs_conflict(tmp_path: Path) -> None:
    entry, first, runtime_dir = make_paths(tmp_path)
    second = tmp_path / "data-two"
    second.mkdir()
    results = run_concurrent_publish(entry, first, second, runtime_dir)
    assert "plugin.context_conflict" in results
    paths = bridge_paths(entry, runtime_dir)
    assert paths.conflict.read_bytes() == b""
    with pytest.raises(DraftError, match="plugin.context_conflict"):
        load(entry, runtime_dir)
