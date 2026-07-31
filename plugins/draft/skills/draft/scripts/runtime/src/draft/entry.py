"""仅依赖标准库的部署边界：解析桥接、选择清单键环境、同步、自检并执行领域 CLI。"""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from draft.errors import DraftError
from draft.plugin_context import PluginContext, load, per_user_runtime_dir

_EXIT_RUNTIME = 4


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


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        layout, user_args = _parse_layout(arguments)
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
