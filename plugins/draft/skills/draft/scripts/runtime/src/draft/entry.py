"""仅依赖标准库的部署边界：解析桥接、选择清单键环境、同步、自检并执行领域 CLI。"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from draft.errors import DraftError
from draft.plugin_context import PluginContext, load, per_user_runtime_dir, publish

_EXIT_RUNTIME = 4

HOST_ENV = {
    "codex": ("PLUGIN_ROOT", "PLUGIN_DATA"),
    "claude": ("CLAUDE_PLUGIN_ROOT", "CLAUDE_PLUGIN_DATA"),
}

_MAX_STDIN = 64 * 1024


@dataclass(frozen=True)
class RuntimeLayout:
    entry_path: Path
    skill_path: Path
    project_path: Path
    source_path: Path


class Runner(Protocol):
    def __call__(self, arguments: Sequence[str], env: Mapping[str, str]) -> int: ...


class CommandFailed(DraftError):
    """保留子进程退出码的运行时失败。"""

    def __init__(self, code: str, exit_code: int, *paths: Path) -> None:
        super().__init__(code, *paths)
        self.exit_code = exit_code


def environment_key(layout: RuntimeLayout) -> str:
    project = hashlib.sha256((layout.project_path / "pyproject.toml").read_bytes()).digest()
    lock = hashlib.sha256((layout.project_path / "uv.lock").read_bytes()).digest()
    return hashlib.sha256(b"draft-environment-v1\0" + project + lock).hexdigest()


def environment_path(
    layout: RuntimeLayout,
    context: PluginContext | None,
    runtime_dir: Path | None = None,
) -> Path:
    key = environment_key(layout)
    if context is None:
        root = runtime_dir if runtime_dir is not None else per_user_runtime_dir()
        return root / "draft" / "envs" / key
    return context.data_dir / "envs" / key


def environment_python(target: Path) -> Path:
    relative = Path("Scripts/python.exe") if os.name == "nt" else Path("bin/python")
    return target / relative


def run_command(arguments: Sequence[str], env: Mapping[str, str]) -> int:
    completed = subprocess.run(list(arguments), env=dict(env), check=False)
    return completed.returncode


def sync_environment(
    layout: RuntimeLayout,
    target: Path,
    environ: Mapping[str, str],
    *,
    run: Runner | None = None,
) -> Path:
    runner = run_command if run is None else run
    try:
        status = target.lstat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise DraftError("runtime.environment_invalid", target) from exc
    else:
        if stat.S_ISLNK(status.st_mode) or not stat.S_ISDIR(status.st_mode):
            raise DraftError("runtime.environment_invalid", target)
    env = dict(environ)
    env["UV_PROJECT_ENVIRONMENT"] = str(target)
    try:
        code = runner(
            [
                "uv", "sync", "--project", str(layout.project_path),
                "--locked", "--no-install-project", "--no-dev",
            ],
            env,
        )
    except FileNotFoundError as exc:
        raise DraftError("runtime.uv_unavailable") from exc
    if code != 0:
        raise CommandFailed("runtime.sync_failed", code, target)
    return target


_INTERNAL_OPTIONS = {"--entry-path": "entry", "--skill-path": "skill", "--runtime-path": "runtime"}


def _regular_file(raw: str) -> Path:
    path = Path(raw)
    try:
        canonical = path.resolve(strict=True)
    except OSError as exc:
        raise DraftError("entry.layout_invalid", path) from exc
    if not canonical.is_file():
        raise DraftError("entry.layout_invalid", path)
    return canonical


def _real_directory(raw: str) -> Path:
    path = Path(raw)
    try:
        canonical = path.resolve(strict=True)
    except OSError as exc:
        raise DraftError("entry.layout_invalid", path) from exc
    if not canonical.is_dir():
        raise DraftError("entry.layout_invalid", path)
    return canonical


def _parse_layout(argv: list[str]) -> tuple[RuntimeLayout, list[str]]:
    values: dict[str, str] = {}
    user_args: list[str] = []
    index = 0
    while index < len(argv):
        argument = argv[index]
        if argument == "--":
            user_args = argv[index + 1 :]
            break
        name = _INTERNAL_OPTIONS.get(argument)
        if name is None or name in values or index + 1 >= len(argv):
            raise DraftError("entry.invalid_arguments", details=(argument,))
        values[name] = argv[index + 1]
        index += 2
    missing = [name for name in ("entry", "skill", "runtime") if name not in values]
    if missing:
        raise DraftError("entry.invalid_arguments", details=tuple(missing))
    runtime = _real_directory(values["runtime"])
    layout = RuntimeLayout(
        entry_path=_regular_file(values["entry"]),
        skill_path=_regular_file(values["skill"]),
        project_path=runtime,
        source_path=_real_directory(str(runtime / "src")),
    )
    return layout, user_args


def _self_check(layout: RuntimeLayout, python: Path, environ: Mapping[str, str]) -> None:
    env = dict(environ)
    env["PYTHONPATH"] = str(layout.source_path)
    try:
        code = run_command([str(python), "-m", "draft", "--version"], env)
    except FileNotFoundError as exc:
        raise DraftError("runtime.environment_invalid", python) from exc
    if code != 0:
        raise CommandFailed("runtime.self_check_failed", code, python)


def _domain_env(
    layout: RuntimeLayout,
    context: PluginContext | None,
    environ: Mapping[str, str],
) -> dict[str, str]:
    env = dict(environ)
    env["PYTHONPATH"] = str(layout.source_path)
    env["DRAFT_SKILL_PATH"] = str(layout.skill_path)
    if context is None:
        env.pop("DRAFT_DATA", None)
    else:
        env["DRAFT_DATA"] = str(context.data_dir)
    return env


def _report(error: DraftError) -> None:
    parts = [error.code, *(str(path) for path in error.paths), *error.details]
    print(": ".join(parts), file=sys.stderr)


def _read_hook_payload() -> tuple[dict[str, object] | None, str]:
    text = sys.stdin.read(_MAX_STDIN + 1)
    if len(text) > _MAX_STDIN:
        return None, "hook.stdin_oversized"
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None, "hook.stdin_invalid"
    if not isinstance(value, dict):
        return None, "hook.stdin_not_object"
    payload = cast("dict[str, object]", value)
    if payload.get("hook_event_name") != "SessionStart":
        return None, "hook.unexpected_event"
    return payload, ""


def _binding_present(path: Path) -> bool:
    """绑定文件是否以任何形态存在；不安全的形态留给环境解释器中的 load_binding 诊断。"""
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


def _session_start(
    layout: RuntimeLayout, arguments: Sequence[str], environ: Mapping[str, str]
) -> int:
    from draft import session_hook  # 该模块只依赖标准库；保持引导导入图最小

    if len(arguments) != 2 or arguments[1] not in HOST_ENV:
        raise DraftError("entry.invalid_arguments", details=tuple(arguments))
    host = arguments[1]
    root_variable, data_variable = HOST_ENV[host]
    raw_root = environ.get(root_variable)
    raw_data = environ.get(data_variable)
    missing = tuple(
        name for name, value in ((root_variable, raw_root), (data_variable, raw_data)) if not value
    )
    if missing:
        raise DraftError("hook.environment_missing", details=missing)
    assert raw_root is not None and raw_data is not None
    try:
        plugin_root = Path(raw_root).resolve(strict=True)
    except OSError as exc:
        raise DraftError("hook.plugin_root_invalid", Path(raw_root)) from exc
    expected_root = layout.skill_path.parents[2]
    if plugin_root != expected_root:
        raise DraftError("hook.plugin_root_mismatch", plugin_root, expected_root)
    context = publish(layout.entry_path, Path(raw_data))
    data_dir = context.data_dir
    payload, parse_error = _read_hook_payload()
    if payload is None:
        session_hook.log_hook(data_dir, host, branch="ignored", error=parse_error)
        return 0
    raw_source = payload.get("source")
    source = raw_source if isinstance(raw_source, str) else ""
    session_id = payload.get("session_id")
    if not isinstance(session_id, str):
        session_hook.log_hook(
            data_dir, host, branch="ignored", source=source, error="binding.session_unavailable"
        )
        return 0
    target = environment_path(layout, context)
    warmed = False
    standalone = environment_path(layout, None)
    try:
        status = standalone.lstat()
    except OSError:
        pass
    else:
        if stat.S_ISDIR(status.st_mode):
            # 独立环境只是预热信号：同步持久目标，不读取其包状态，也不移动它
            try:
                sync_environment(layout, target, environ)
            except DraftError as error:
                session_hook.log_hook(
                    data_dir, host, branch="warmup", source=source, error=error.code
                )
                raise
            warmed = True
    # 与 draft.bindings.binding_key 保持一致；引导阶段不能导入 bindings（第三方依赖链）
    key = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    if not _binding_present(data_dir / "bindings" / f"{key}.json"):
        print(session_hook.render_session_fact(session_id))
        session_hook.log_hook(
            data_dir, host, branch="session_fact", source=source, warmed=warmed
        )
        return 0
    try:
        if not warmed:
            sync_environment(layout, target, environ)
        python = environment_python(target)
        _self_check(layout, python, environ)
    except DraftError as error:
        session_hook.log_hook(data_dir, host, branch="bound", source=source, error=error.code)
        raise
    command = [
        str(python),
        "-m",
        "draft.session_hook",
        "--data-dir",
        str(data_dir),
        "--session-id",
        session_id,
        "--source",
        source,
        "--host",
        host,
    ]
    try:
        return run_command(command, _domain_env(layout, context, environ))
    except FileNotFoundError as exc:
        raise DraftError("runtime.environment_invalid", python) from exc


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        layout, user_args = _parse_layout(arguments)
        if user_args[:1] == ["_session-start"]:
            return _session_start(layout, user_args, os.environ)
        context = load(layout.entry_path)
        target = environment_path(layout, context)
        sync_environment(layout, target, os.environ)
        python = environment_python(target)
        _self_check(layout, python, os.environ)
        if user_args == ["setup"]:
            return 0
        try:
            command = [str(python), "-m", "draft", *user_args]
            return run_command(command, _domain_env(layout, context, os.environ))
        except FileNotFoundError as exc:
            raise DraftError("runtime.environment_invalid", python) from exc
    except CommandFailed as error:
        _report(error)
        return error.exit_code
    except DraftError as error:
        _report(error)
        return _EXIT_RUNTIME


if __name__ == "__main__":
    raise SystemExit(main())
