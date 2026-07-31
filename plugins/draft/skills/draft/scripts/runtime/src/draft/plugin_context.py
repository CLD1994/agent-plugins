"""严格的两字段插件上下文桥接：安装键、运行目录、操作系统锁、原子发布与 reset。"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from collections.abc import Generator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from draft.errors import DraftError

SCHEMA_VERSION = 1

_EXPECTED_KEYS = {"schema_version", "data_dir"}


@dataclass(frozen=True)
class PluginContext:
    data_dir: Path


@dataclass(frozen=True)
class BridgePaths:
    json: Path
    conflict: Path
    lock: Path


def installation_key(entry_path: Path) -> str:
    canonical = entry_path.resolve(strict=True)
    return hashlib.sha256(str(canonical).encode("utf-8")).hexdigest()


def per_user_runtime_dir(environ: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    if os.name != "posix":
        return Path(tempfile.gettempdir())
    xdg = env.get("XDG_RUNTIME_DIR", "")
    if xdg:
        candidate = Path(xdg)
        try:
            status = candidate.lstat()
        except OSError:
            status = None
        if (
            status is not None
            and stat.S_ISDIR(status.st_mode)
            and status.st_uid == os.getuid()
            and status.st_mode & 0o077 == 0
        ):
            return candidate
    base = Path(tempfile.gettempdir()) / f"draft-runtime-{os.getuid()}"
    with suppress(FileExistsError):
        os.mkdir(base, 0o700)
        os.chmod(base, 0o700)
    try:
        status = base.lstat()
    except OSError as exc:
        raise DraftError("plugin.context_invalid", base) from exc
    if (
        not stat.S_ISDIR(status.st_mode)
        or status.st_uid != os.getuid()
        or status.st_mode & 0o077 != 0
    ):
        raise DraftError("plugin.context_invalid", base)
    return base


def bridge_paths(entry_path: Path, runtime_dir: Path | None = None) -> BridgePaths:
    root = runtime_dir if runtime_dir is not None else per_user_runtime_dir()
    key = installation_key(entry_path)
    base = root / "draft" / "bridges"
    return BridgePaths(
        json=base / f"{key}.json",
        conflict=base / f"{key}.conflict",
        lock=base / f"{key}.lock",
    )


def _invalid(path: Path) -> DraftError:
    return DraftError("plugin.context_invalid", path)


def _verify_owner_and_mode(path: Path, status: os.stat_result) -> None:
    if os.name == "posix" and (status.st_uid != os.getuid() or status.st_mode & 0o022):
        raise _invalid(path)


def _verify_bridge_dir(base: Path, status: os.stat_result) -> None:
    if not stat.S_ISDIR(status.st_mode):
        raise _invalid(base)
    _verify_owner_and_mode(base, status)


def _verify_regular_file(path: Path) -> bool:
    """不存在返回 False；存在且为安全的普通文件返回 True；其余状态拒绝且不跟随。"""
    try:
        status = path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise _invalid(path) from exc
    if not stat.S_ISREG(status.st_mode):
        raise _invalid(path)
    _verify_owner_and_mode(path, status)
    return True


def _lock_os(descriptor: int) -> None:
    if os.name == "posix":
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_EX)
    elif os.name == "nt":
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)


def _unlock_os(descriptor: int) -> None:
    if os.name == "posix":
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_UN)
    elif os.name == "nt":
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)


@contextmanager
def _bridge_lock(path: Path) -> Generator[None]:
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise _invalid(path) from exc
    try:
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode):
            raise _invalid(path)
        _verify_owner_and_mode(path, status)
        _lock_os(descriptor)
        try:
            yield
        finally:
            _unlock_os(descriptor)
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _write_json_atomic(path: Path, payload: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        with suppress(OSError):
            os.unlink(temporary)
        raise
    _fsync_directory(path.parent)


def _create_sentinel(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return
    except OSError as exc:
        raise _invalid(path) from exc
    os.close(descriptor)
    _fsync_directory(path.parent)


def _canonical_data_dir(data_dir: Path) -> Path:
    if not data_dir.is_absolute():
        raise _invalid(data_dir)
    try:
        canonical = data_dir.resolve(strict=True)
        status = canonical.lstat()
    except OSError as exc:
        raise _invalid(data_dir) from exc
    if not stat.S_ISDIR(status.st_mode) or not os.access(canonical, os.W_OK):
        raise _invalid(canonical)
    return canonical


def _parse_data_dir(raw: object, path: Path) -> Path:
    if not isinstance(raw, dict):
        raise _invalid(path)
    payload = cast("dict[str, object]", raw)
    if set(payload) != _EXPECTED_KEYS:
        raise _invalid(path)
    if payload["schema_version"] != SCHEMA_VERSION:
        raise _invalid(path)
    data_dir = payload["data_dir"]
    if not isinstance(data_dir, str):
        raise _invalid(path)
    return _canonical_data_dir(Path(data_dir))


def _lstat_bridge_dir(base: Path) -> os.stat_result | None:
    try:
        return base.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise _invalid(base) from exc


def publish(entry_path: Path, data_dir: Path, runtime_dir: Path | None = None) -> PluginContext:
    paths = bridge_paths(entry_path, runtime_dir)
    canonical = _canonical_data_dir(data_dir)
    base = paths.json.parent
    try:
        base.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as exc:
        raise _invalid(base) from exc
    status = _lstat_bridge_dir(base)
    if status is None:
        raise _invalid(base)
    _verify_bridge_dir(base, status)
    with _bridge_lock(paths.lock):
        if _verify_regular_file(paths.conflict):
            raise DraftError("plugin.context_conflict", paths.conflict)
        if _verify_regular_file(paths.json):
            try:
                raw = json.loads(paths.json.read_text(encoding="utf-8"))
            except OSError, json.JSONDecodeError:
                pass  # 普通文件仅 JSON 损坏：允许原子重写
            else:
                existing = _parse_data_dir(raw, paths.json)
                if existing != canonical:
                    _create_sentinel(paths.conflict)
                    raise DraftError("plugin.context_conflict", paths.json, paths.conflict)
        payload = {"schema_version": SCHEMA_VERSION, "data_dir": str(canonical)}
        _write_json_atomic(paths.json, (json.dumps(payload) + "\n").encode("utf-8"))
    return PluginContext(data_dir=canonical)


def load(entry_path: Path, runtime_dir: Path | None = None) -> PluginContext | None:
    paths = bridge_paths(entry_path, runtime_dir)
    base = paths.json.parent
    status = _lstat_bridge_dir(base)
    if status is None:
        return None
    _verify_bridge_dir(base, status)
    with _bridge_lock(paths.lock):
        if _verify_regular_file(paths.conflict):
            raise DraftError("plugin.context_conflict", paths.conflict)
        if not _verify_regular_file(paths.json):
            return None
        try:
            raw = json.loads(paths.json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise _invalid(paths.json) from exc
        return PluginContext(data_dir=_parse_data_dir(raw, paths.json))


def reset(entry_path: Path, runtime_dir: Path | None = None) -> None:
    paths = bridge_paths(entry_path, runtime_dir)
    base = paths.json.parent
    status = _lstat_bridge_dir(base)
    if status is None:
        return
    _verify_bridge_dir(base, status)
    with _bridge_lock(paths.lock):
        removed = False
        for target in (paths.json, paths.conflict):
            if _verify_regular_file(target):
                target.unlink()
                removed = True
        if removed:
            _fsync_directory(base)
