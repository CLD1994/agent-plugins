# /// script
# requires-python = ">=3.12,<3.15"
# dependencies = []
# ///

"""Draft 单文件启动入口：自推导布局、版本环境键、bridge 与内层 uv 分派。只依赖标准库。"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import cast

EXIT_RUNTIME = 4


class LauncherError(Exception):
    def __init__(
        self,
        code: str,
        *paths: Path,
        details: Sequence[str] = (),
        exit_code: int = EXIT_RUNTIME,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.paths = paths
        self.details = tuple(details)
        self.exit_code = exit_code


@dataclass(frozen=True)
class RuntimeLayout:
    entry_path: Path
    skill_root: Path
    skill_path: Path
    project_path: Path
    pyproject_path: Path
    lock_path: Path


def _require_regular(path: Path) -> None:
    try:
        status = path.lstat()
    except OSError as exc:
        raise LauncherError("entry.layout_invalid", path) from exc
    if not stat.S_ISREG(status.st_mode):
        raise LauncherError("entry.layout_invalid", path)


def _require_directory(path: Path) -> None:
    try:
        status = path.lstat()
    except OSError as exc:
        raise LauncherError("entry.layout_invalid", path) from exc
    if not stat.S_ISDIR(status.st_mode):
        raise LauncherError("entry.layout_invalid", path)


def resolve_layout(entry_path: Path | None = None) -> RuntimeLayout:
    entry = Path(__file__) if entry_path is None else entry_path
    try:
        canonical = entry.resolve(strict=True)
    except OSError as exc:
        raise LauncherError("entry.layout_invalid", entry) from exc
    scripts = canonical.parent
    skill_root = scripts.parent
    layout = RuntimeLayout(
        entry_path=canonical,
        skill_root=skill_root,
        skill_path=skill_root / "SKILL.md",
        project_path=scripts / "runtime",
        pyproject_path=scripts / "runtime/pyproject.toml",
        lock_path=scripts / "runtime/uv.lock",
    )
    if scripts.name != "scripts":
        raise LauncherError("entry.layout_invalid", canonical)
    _require_regular(layout.skill_path)
    _require_directory(layout.project_path)
    _require_regular(layout.pyproject_path)
    _require_regular(layout.lock_path)
    return layout


def runtime_version(layout: RuntimeLayout) -> str:
    # tomllib 局部导入：draft.py 位于 runtime 项目之外，不同调用方（IDE、门禁）的 ruff
    # 配置对 tomllib 的标准库归属判定不一致，顶层导入块会在其中一种配置下被判 I001。
    import tomllib

    try:
        raw = tomllib.loads(layout.pyproject_path.read_text(encoding="utf-8"))
        value = raw["project"]["version"]
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError) as exc:
        raise LauncherError("runtime.version_invalid", layout.pyproject_path) from exc
    if not isinstance(value, str):
        raise LauncherError("runtime.version_invalid", layout.pyproject_path)
    return value


def environment_key(layout: RuntimeLayout) -> str:
    value = runtime_version(layout)
    if not value or value in {".", ".."} or any(x in value for x in ("/", "\\", "\x00")):
        raise LauncherError("runtime.version_invalid", layout.pyproject_path)
    return value


def environment_path(data_dir: Path, layout: RuntimeLayout) -> Path:
    return data_dir / "envs" / environment_key(layout)


def runtime_arguments(layout: RuntimeLayout, arguments: Sequence[str]) -> list[str]:
    command = ["--version"] if list(arguments) == ["setup"] else list(arguments)
    return [
        "uv",
        "run",
        "--project",
        str(layout.project_path),
        "--locked",
        "--exact",
        "--no-dev",
        "--no-editable",
        "--no-env-file",
        "draft",
        *command,
    ]


def runtime_environment(
    layout: RuntimeLayout,
    data_dir: Path,
    plugin_mode: bool,
    environ: Mapping[str, str],
) -> dict[str, str]:
    env = dict(environ)
    for name in (
        "DRAFT_DATA",
        "DRAFT_SKILL_PATH",
        "PYTHONPATH",
        "VIRTUAL_ENV",
        "UV_NO_SYNC",
        "UV_NO_INSTALL_PROJECT",
    ):
        env.pop(name, None)
    env["UV_PROJECT_ENVIRONMENT"] = str(environment_path(data_dir, layout))
    env["DRAFT_SKILL_PATH"] = str(layout.skill_path)
    if plugin_mode:
        env["DRAFT_DATA"] = str(data_dir)
    return env


def validate_environment_target(target: Path) -> None:
    try:
        status = target.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise LauncherError("runtime.environment_invalid", target) from exc
    if (
        stat.S_ISLNK(status.st_mode)
        or not stat.S_ISDIR(status.st_mode)
        or (os.name == "posix" and status.st_uid != os.getuid())
        or not os.access(target, os.W_OK | os.X_OK)
    ):
        raise LauncherError("runtime.environment_invalid", target)


def run_command(arguments: Sequence[str], env: Mapping[str, str]) -> int:
    try:
        return subprocess.run(list(arguments), env=dict(env), check=False).returncode
    except FileNotFoundError as exc:
        raise LauncherError("runtime.uv_unavailable") from exc


# ---- 严格两字段插件上下文桥接 ----

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
    try:
        canonical = entry_path.resolve(strict=True)
    except OSError as exc:
        raise _invalid(entry_path) from exc
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
    try:
        os.mkdir(base, 0o700)
        os.chmod(base, 0o700)
    except FileExistsError:
        pass
    except OSError as exc:
        raise LauncherError("plugin.context_invalid", base) from exc
    try:
        status = base.lstat()
    except OSError as exc:
        raise LauncherError("plugin.context_invalid", base) from exc
    if (
        not stat.S_ISDIR(status.st_mode)
        or status.st_uid != os.getuid()
        or status.st_mode & 0o077 != 0
    ):
        raise LauncherError("plugin.context_invalid", base)
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


def _invalid(path: Path) -> LauncherError:
    return LauncherError("plugin.context_invalid", path)


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
        # 清理阶段：主错误在途时保留主错误、清理失败降级为 note；
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
            for action in (_unlock_os, os.close) if locked else (os.close,):
                try:
                    action(descriptor)
                except OSError as exc:
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
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException as exc:
        # fdopen 失败时描述符尚未托管，补关；已关闭时这次 close 被 suppress
        with suppress(OSError):
            os.close(descriptor)
        with suppress(OSError):
            os.unlink(temporary)
        if isinstance(exc, OSError):
            raise _invalid(path) from exc
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
            raise LauncherError("plugin.context_conflict", paths.conflict)
        if _verify_regular_file(paths.json):
            try:
                raw = json.loads(paths.json.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass  # 普通文件仅 JSON 损坏：允许原子重写
            else:
                existing = _parse_data_dir(raw, paths.json)
                if existing != canonical:
                    _create_sentinel(paths.conflict)
                    raise LauncherError("plugin.context_conflict", paths.json, paths.conflict)
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
            raise LauncherError("plugin.context_conflict", paths.conflict)
        if not _verify_regular_file(paths.json):
            return None
        try:
            raw = json.loads(paths.json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
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


def _report(error: LauncherError) -> None:
    parts = [error.code, *(str(path) for path in error.paths), *error.details]
    print(": ".join(parts), file=sys.stderr)


Runner = Callable[[Sequence[str], Mapping[str, str]], int]


HOST_ENV = {
    "codex": ("PLUGIN_ROOT", "PLUGIN_DATA"),
    "claude": ("CLAUDE_PLUGIN_ROOT", "CLAUDE_PLUGIN_DATA"),
}


def _canonical_plugin_root(raw: Path) -> Path:
    try:
        return raw.resolve(strict=True)
    except OSError as exc:
        raise LauncherError("hook.plugin_root_invalid", raw) from exc


def _session_start(
    layout: RuntimeLayout,
    arguments: Sequence[str],
    environ: Mapping[str, str],
    runtime_dir: Path | None,
    runner: Runner,
) -> int:
    if len(arguments) != 2 or arguments[1] not in HOST_ENV:
        raise LauncherError("entry.invalid_arguments", details=arguments)
    host = arguments[1]
    root_name, data_name = HOST_ENV[host]
    raw_root, raw_data = environ.get(root_name), environ.get(data_name)
    missing = tuple(
        name for name, value in ((root_name, raw_root), (data_name, raw_data)) if not value
    )
    if missing:
        raise LauncherError("hook.environment_missing", details=missing)
    assert raw_root is not None and raw_data is not None
    plugin_root = _canonical_plugin_root(Path(raw_root))
    expected_root = layout.skill_root.parent.parent
    if plugin_root != expected_root:
        raise LauncherError("hook.plugin_root_mismatch", plugin_root, expected_root)
    context = publish_context(layout.entry_path, Path(raw_data), runtime_dir)
    target = environment_path(context.data_dir, layout)
    validate_environment_target(target)
    return runner(
        runtime_arguments(layout, arguments),
        runtime_environment(layout, context.data_dir, True, environ),
    )


def standalone_data_dir(runtime_dir: Path | None = None) -> Path:
    root = per_user_runtime_dir() if runtime_dir is None else runtime_dir
    return root / "draft"


def main(
    argv: Sequence[str] | None = None,
    *,
    entry_path: Path | None = None,
    environ: Mapping[str, str] | None = None,
    runtime_dir: Path | None = None,
    runner: Runner = run_command,
) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    env = os.environ if environ is None else environ
    try:
        layout = resolve_layout(entry_path)
        if arguments == ["plugin-context", "reset"]:
            reset_context(layout.entry_path, runtime_dir)
            return 0
        if arguments[:1] == ["_session-start"]:
            return _session_start(layout, arguments, env, runtime_dir, runner)
        context = load_context(layout.entry_path, runtime_dir)
        data_dir = standalone_data_dir(runtime_dir) if context is None else context.data_dir
        plugin_mode = context is not None
        validate_environment_target(environment_path(data_dir, layout))
        return runner(
            runtime_arguments(layout, arguments),
            runtime_environment(layout, data_dir, plugin_mode, env),
        )
    except LauncherError as error:
        _report(error)
        return error.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
