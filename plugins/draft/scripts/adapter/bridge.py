"""插件上下文桥接：两字段 JSON 协议与安全加固。仅依赖标准库。"""

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

from adapter.errors import AdapterError

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
    # 孤立代理码位无法安全编码为安装键：顶部可编码性检查与 _canonical_data_dir
    # 同型。resolve 对含孤立代理的路径同样抛 UnicodeEncodeError（非 OSError），
    # 必须先于 resolve 拦截。
    try:
        str(entry_path).encode("utf-8")
    except UnicodeEncodeError as exc:
        raise _invalid(entry_path) from exc
    try:
        canonical = entry_path.resolve(strict=True)
    except OSError as exc:
        raise _invalid(entry_path) from exc
    # 解析结果可能经 readlink/getcwd 引入非 UTF-8 字节（surrogateescape 码位），
    # 严格编码失败同样归一为 plugin.context_invalid
    try:
        return hashlib.sha256(str(canonical).encode("utf-8")).hexdigest()
    except UnicodeEncodeError as exc:
        raise _invalid(entry_path) from exc


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
    try:
        os.mkdir(base, 0o700)
        os.chmod(base, 0o700)
    except FileExistsError:
        pass
    except OSError as exc:
        raise AdapterError("plugin.context_invalid", base) from exc
    try:
        status = base.lstat()
    except OSError as exc:
        raise AdapterError("plugin.context_invalid", base) from exc
    if (
        not stat.S_ISDIR(status.st_mode)
        or status.st_uid != os.getuid()
        or status.st_mode & 0o077 != 0
    ):
        raise AdapterError("plugin.context_invalid", base)
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


def _invalid(path: Path) -> AdapterError:
    return AdapterError("plugin.context_invalid", path)


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
    primary: BaseException | None = None
    locked = False
    try:
        try:
            try:
                status = os.fstat(descriptor)
            except OSError as exc:
                raise _invalid(path) from exc
            if not stat.S_ISREG(status.st_mode):
                raise _invalid(path)
            _verify_owner_and_mode(path, status)
            try:
                _lock_os(descriptor)
            except OSError as exc:
                raise _invalid(path) from exc
            locked = True
            yield
        except BaseException as exc:
            primary = exc
            raise
    finally:
        # 清理阶段：主错误在途时保留主错误，清理失败以稳定明细码进入 AdapterError
        # 的 details（note 继续保留底层诊断，无渲染面）；
        # 无主错误时清理失败本身必须明确失败。
        if primary is None:
            cleanup_error: OSError | None = None
            try:
                if locked:
                    _unlock_os(descriptor)
            except OSError as exc:
                cleanup_error = exc
            try:
                os.close(descriptor)
            except OSError as exc:
                if cleanup_error is None:
                    cleanup_error = exc
            if cleanup_error is not None:
                raise _invalid(path) from cleanup_error
        else:
            actions = (
                ((_unlock_os, "cleanup.unlock_failed"), (os.close, "cleanup.close_failed"))
                if locked
                else ((os.close, "cleanup.close_failed"),)
            )
            for action, detail in actions:
                try:
                    action(descriptor)
                except OSError as exc:
                    if isinstance(primary, AdapterError):
                        primary.add_detail(detail)
                    primary.add_note(f"cleanup {action.__name__} failed for {path}: {exc}")


def _fsync_directory(path: Path) -> None:
    """尽力而为的目录 fsync：Windows 与部分文件系统不支持目录 fsync，
    打开、fsync、关闭的任何 OSError 都完整吸收。"""
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        with suppress(OSError):
            os.close(descriptor)


def _write_json_atomic(path: Path, payload: bytes) -> None:
    try:
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    except OSError as exc:
        raise _invalid(path) from exc
    handle = None
    try:
        handle = os.fdopen(descriptor, "wb")
        with handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException as exc:
        # 主错误：OSError 映射为 _invalid；非 OSError 原样重抛（清理失败仅留 note）
        primary = _invalid(path) if isinstance(exc, OSError) else None
        # fdopen 失败时描述符尚未托管，补关；fdopen 成功时描述符已由 with 块关闭，
        # 不重复 close（否则会误报 close 失败）
        if handle is None:
            try:
                os.close(descriptor)
            except OSError as cleanup_exc:
                if primary is not None:
                    primary.add_detail("cleanup.close_failed")
                else:
                    exc.add_note(f"cleanup close failed for {temporary}: {cleanup_exc}")
        try:
            os.unlink(temporary)
        except OSError as cleanup_exc:
            if primary is not None:
                primary.add_detail("cleanup.unlink_failed")
            else:
                exc.add_note(f"cleanup unlink failed for {temporary}: {cleanup_exc}")
        if primary is not None:
            raise primary from exc
        raise
    _fsync_directory(path.parent)


def _create_sentinel(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return
    except OSError as exc:
        raise _invalid(path) from exc
    try:
        os.close(descriptor)
    except OSError as exc:
        raise _invalid(path) from exc
    _fsync_directory(path.parent)


def _canonical_data_dir(data_dir: Path) -> Path:
    # 孤立代理码位无法安全进入协议或文件系统操作：
    # 顶部可编码性检查同时覆盖持久载荷与宿主变量两条来源路径。
    try:
        str(data_dir).encode("utf-8")
    except UnicodeEncodeError as exc:
        raise _invalid(data_dir) from exc
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
    version = payload["schema_version"]
    # bool 是 int 的子类且 True == 1，必须先显式拒绝
    if isinstance(version, bool) or version != SCHEMA_VERSION:
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


def publish_context(
    entry_path: Path, data_dir: Path, runtime_dir: Path | None = None
) -> PluginContext:
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
            raise AdapterError("plugin.context_conflict", paths.conflict)
        if _verify_regular_file(paths.json):
            try:
                raw = json.loads(paths.json.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                pass  # 普通文件仅 JSON 损坏或含无效 UTF-8：允许原子重写
            else:
                existing = _parse_data_dir(raw, paths.json)
                if existing != canonical:
                    _create_sentinel(paths.conflict)
                    raise AdapterError("plugin.context_conflict", paths.json, paths.conflict)
        payload = {"schema_version": SCHEMA_VERSION, "data_dir": str(canonical)}
        _write_json_atomic(paths.json, (json.dumps(payload) + "\n").encode("utf-8"))
    return PluginContext(data_dir=canonical)


def load_context(entry_path: Path, runtime_dir: Path | None = None) -> PluginContext | None:
    paths = bridge_paths(entry_path, runtime_dir)
    base = paths.json.parent
    status = _lstat_bridge_dir(base)
    if status is None:
        return None
    _verify_bridge_dir(base, status)
    with _bridge_lock(paths.lock):
        if _verify_regular_file(paths.conflict):
            raise AdapterError("plugin.context_conflict", paths.conflict)
        if not _verify_regular_file(paths.json):
            return None
        try:
            raw = json.loads(paths.json.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _invalid(paths.json) from exc
        return PluginContext(data_dir=_parse_data_dir(raw, paths.json))


def reset_context(entry_path: Path, runtime_dir: Path | None = None) -> None:
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
                try:
                    target.unlink()
                except OSError as exc:
                    raise _invalid(target) from exc
                removed = True
        if removed:
            _fsync_directory(base)
