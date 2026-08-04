from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path
from types import ModuleType

import pytest

RUNTIME_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = RUNTIME_ROOT.parents[1]
LAUNCHER_PATH = SKILL_ROOT / "scripts/draft.py"


def load_launcher(path: Path = LAUNCHER_PATH) -> ModuleType:
    spec = importlib.util.spec_from_file_location("draft_test_launcher", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def make_layout(tmp_path: Path, version: str = "1.2.3") -> Path:
    skill = tmp_path / "skill"
    scripts = skill / "scripts"
    runtime = scripts / "runtime"
    runtime.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: draft\n---\n", encoding="utf-8")
    (runtime / "pyproject.toml").write_text(
        f'[project]\nname = "draft-runtime"\nversion = "{version}"\n'
        'requires-python = ">=3.12,<3.15"\n',
        encoding="utf-8",
    )
    (runtime / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    entry = scripts / "draft.py"
    entry.write_text(LAUNCHER_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    return entry


def test_layout_is_derived_from_launcher_protocol_position(tmp_path: Path) -> None:
    launcher = load_launcher()
    entry = make_layout(tmp_path)
    layout = launcher.resolve_layout(entry)
    assert layout.entry_path == entry.resolve()
    assert layout.skill_root == entry.parent.parent.resolve()
    assert layout.skill_path == layout.skill_root / "SKILL.md"
    assert layout.project_path == layout.skill_root / "scripts/runtime"


def test_environment_key_is_exact_runtime_version(tmp_path: Path) -> None:
    launcher = load_launcher()
    first = launcher.resolve_layout(make_layout(tmp_path / "first", "1.2.3"))
    second = launcher.resolve_layout(make_layout(tmp_path / "second", "1.2.3"))
    changed = launcher.resolve_layout(make_layout(tmp_path / "changed", "1.2.4"))
    assert launcher.environment_key(first) == launcher.environment_key(second) == "1.2.3"
    assert launcher.environment_key(changed) == "1.2.4"


@pytest.mark.parametrize("version", ["", ".", "..", "../x", r"x\\y", "x\x00y"])
def test_unsafe_runtime_version_is_rejected(tmp_path: Path, version: str) -> None:
    launcher = load_launcher()
    layout = launcher.resolve_layout(make_layout(tmp_path, version))
    with pytest.raises(launcher.LauncherError, match="runtime.version_invalid"):
        launcher.environment_key(layout)


@pytest.mark.parametrize(
    "missing",
    ["SKILL.md", "runtime", "pyproject.toml", "uv.lock"],
)
def test_layout_rejects_missing_protocol_files(tmp_path: Path, missing: str) -> None:
    launcher = load_launcher()
    entry = make_layout(tmp_path)
    target = (
        entry.parent.parent / "SKILL.md"
        if missing == "SKILL.md"
        else entry.parent / "runtime" / missing
    )
    if missing == "runtime":
        target = entry.parent / "runtime"
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    with pytest.raises(launcher.LauncherError, match="entry.layout_invalid"):
        launcher.resolve_layout(entry)


@pytest.mark.parametrize(
    "occupied",
    ["SKILL.md", "runtime", "pyproject.toml", "uv.lock"],
)
def test_layout_rejects_wrong_file_kind(tmp_path: Path, occupied: str) -> None:
    launcher = load_launcher()
    entry = make_layout(tmp_path)
    skill = entry.parent.parent
    if occupied == "SKILL.md":
        target = skill / "SKILL.md"
        target.unlink()
        target.mkdir()
    elif occupied == "runtime":
        target = entry.parent / "runtime"
        for child in target.iterdir():
            child.unlink()
        target.rmdir()
        target.write_text("x", encoding="utf-8")
    else:
        target = entry.parent / "runtime" / occupied
        target.unlink()
        target.mkdir()
    with pytest.raises(launcher.LauncherError, match="entry.layout_invalid"):
        launcher.resolve_layout(entry)


def test_layout_rejects_symlinked_protocol_files(tmp_path: Path) -> None:
    launcher = load_launcher()
    entry = make_layout(tmp_path)
    skill = entry.parent.parent
    real = tmp_path / "real-SKILL.md"
    real.write_text("---\nname: draft\n---\n", encoding="utf-8")
    (skill / "SKILL.md").unlink()
    (skill / "SKILL.md").symlink_to(real)
    with pytest.raises(launcher.LauncherError, match="entry.layout_invalid"):
        launcher.resolve_layout(entry)


def test_environment_path_is_data_dir_envs_version(tmp_path: Path) -> None:
    launcher = load_launcher()
    layout = launcher.resolve_layout(make_layout(tmp_path))
    data_dir = tmp_path / "data"
    assert launcher.environment_path(data_dir, layout) == data_dir / "envs" / "1.2.3"


def test_runtime_command_installs_project_non_editably(tmp_path: Path) -> None:
    launcher = load_launcher()
    layout = launcher.resolve_layout(make_layout(tmp_path))
    assert launcher.runtime_arguments(layout, ["check", "root"]) == [
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
        "check",
        "root",
    ]


def test_setup_maps_to_version_probe(tmp_path: Path) -> None:
    launcher = load_launcher()
    layout = launcher.resolve_layout(make_layout(tmp_path))
    assert launcher.runtime_arguments(layout, ["setup"])[-2:] == ["draft", "--version"]


def test_runtime_environment_overrides_all_authoritative_selectors(tmp_path: Path) -> None:
    launcher = load_launcher()
    layout = launcher.resolve_layout(make_layout(tmp_path))
    data_dir = tmp_path / "data"
    inherited = {
        "PATH": "bin",
        "UV_PROJECT_ENVIRONMENT": "wrong",
        "UV_NO_SYNC": "1",
        "UV_NO_INSTALL_PROJECT": "1",
        "DRAFT_DATA": "wrong",
        "DRAFT_SKILL_PATH": "wrong",
        "PYTHONPATH": "wrong",
        "VIRTUAL_ENV": "wrong",
    }
    env = launcher.runtime_environment(layout, data_dir, True, inherited)
    assert env["UV_PROJECT_ENVIRONMENT"] == str(data_dir / "envs/1.2.3")
    assert env["DRAFT_DATA"] == str(data_dir)
    assert env["DRAFT_SKILL_PATH"] == str(layout.skill_path)
    assert env["PATH"] == "bin"
    for name in ("UV_NO_SYNC", "UV_NO_INSTALL_PROJECT", "PYTHONPATH", "VIRTUAL_ENV"):
        assert name not in env


def test_standalone_environment_omits_draft_data(tmp_path: Path) -> None:
    launcher = load_launcher()
    layout = launcher.resolve_layout(make_layout(tmp_path))
    env = launcher.runtime_environment(layout, tmp_path / "data", False, {"DRAFT_DATA": "wrong"})
    assert "DRAFT_DATA" not in env


def test_run_command_propagates_exit_code(tmp_path: Path) -> None:
    launcher = load_launcher()
    assert launcher.run_command([sys.executable, "-c", "import sys; sys.exit(7)"], {}) == 7


def test_run_command_missing_uv_is_runtime_uv_unavailable(tmp_path: Path) -> None:
    launcher = load_launcher()
    with pytest.raises(launcher.LauncherError, match="runtime.uv_unavailable"):
        launcher.run_command(["definitely-missing-uv-binary"], {})


def test_validate_environment_target_rejects_symlink_and_file(tmp_path: Path) -> None:
    launcher = load_launcher()
    regular = tmp_path / "regular"
    regular.write_text("x", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(tmp_path)
    for target in (regular, link):
        with pytest.raises(launcher.LauncherError, match="runtime.environment_invalid"):
            launcher.validate_environment_target(target)
    launcher.validate_environment_target(tmp_path / "absent")
    launcher.validate_environment_target(tmp_path)


# ---- 普通 main() 的模式选择 ----


def make_main_setup(tmp_path: Path) -> tuple[Path, Path, Path]:
    entry = make_layout(tmp_path / "layout")
    data = tmp_path / "plugin-data"
    data.mkdir()
    runtime_dir = tmp_path / "runtime-dir"
    runtime_dir.mkdir(mode=0o700)
    return entry, data, runtime_dir


def record_calls() -> tuple[list[tuple[list[str], dict[str, str]]], object]:
    calls: list[tuple[list[str], dict[str, str]]] = []

    def runner(args: object, env: object) -> int:
        calls.append((list(args), dict(env)))  # type: ignore[arg-type]
        return 0

    return calls, runner


def test_normal_main_uses_published_plugin_data(tmp_path: Path) -> None:
    launcher = load_launcher()
    entry, data, runtime_dir = make_main_setup(tmp_path)
    launcher.publish_context(entry, data, runtime_dir)
    calls, runner = record_calls()
    code = launcher.main(
        ["check", "root"],
        entry_path=entry,
        environ={"PATH": "bin"},
        runtime_dir=runtime_dir,
        runner=runner,
    )
    assert code == 0
    assert calls[0][1]["UV_PROJECT_ENVIRONMENT"] == str(data.resolve() / "envs/1.2.3")
    assert calls[0][1]["DRAFT_DATA"] == str(data.resolve())


def test_main_without_bridge_uses_standalone_runtime_dir(tmp_path: Path) -> None:
    launcher = load_launcher()
    entry, _, runtime_dir = make_main_setup(tmp_path)
    calls, runner = record_calls()
    code = launcher.main(
        ["check", "root"],
        entry_path=entry,
        environ={"PATH": "bin"},
        runtime_dir=runtime_dir,
        runner=runner,
    )
    assert code == 0
    env = calls[0][1]
    assert env["UV_PROJECT_ENVIRONMENT"] == str(runtime_dir / "draft" / "envs" / "1.2.3")
    assert "DRAFT_DATA" not in env


def test_main_with_invalid_bridge_never_runs_uv(tmp_path: Path) -> None:
    launcher = load_launcher()
    entry, data, runtime_dir = make_main_setup(tmp_path)
    launcher.publish_context(entry, data, runtime_dir)
    other = tmp_path / "other-data"
    other.mkdir()
    with pytest.raises(launcher.LauncherError, match="plugin.context_conflict"):
        launcher.publish_context(entry, other, runtime_dir)
    calls, runner = record_calls()
    code = launcher.main(
        ["check", "root"],
        entry_path=entry,
        environ={"PATH": "bin"},
        runtime_dir=runtime_dir,
        runner=runner,
    )
    assert code == 4
    assert calls == []


def test_plugin_context_reset_runs_before_load(tmp_path: Path) -> None:
    launcher = load_launcher()
    entry, data, runtime_dir = make_main_setup(tmp_path)
    launcher.publish_context(entry, data, runtime_dir)
    calls, runner = record_calls()
    code = launcher.main(
        ["plugin-context", "reset"],
        entry_path=entry,
        environ={"PATH": "bin"},
        runtime_dir=runtime_dir,
        runner=runner,
    )
    assert code == 0
    assert calls == []
    assert launcher.load_context(entry, runtime_dir) is None


def test_main_bridge_oserror_reports_invalid_and_never_runs_uv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    launcher = load_launcher()
    entry, data, runtime_dir = make_main_setup(tmp_path)
    launcher.publish_context(entry, data, runtime_dir)

    def denied(descriptor: int) -> None:
        raise OSError("locking failed")

    monkeypatch.setattr(launcher, "_lock_os", denied)
    calls, runner = record_calls()
    code = launcher.main(
        ["check", "root"],
        entry_path=entry,
        environ={"PATH": "bin"},
        runtime_dir=runtime_dir,
        runner=runner,
    )
    assert code == 4
    err = capsys.readouterr().err
    assert "plugin.context_invalid" in err
    assert str(launcher.bridge_paths(entry, runtime_dir).lock) in err
    assert calls == []


@pytest.mark.parametrize("exit_code", [2, 3, 4, 5])
def test_main_propagates_runner_exit_code(tmp_path: Path, exit_code: int) -> None:
    launcher = load_launcher()
    entry, _, runtime_dir = make_main_setup(tmp_path)

    def runner(args: object, env: object) -> int:
        return exit_code

    assert (
        launcher.main(
            ["check"],
            entry_path=entry,
            environ={"PATH": "bin"},
            runtime_dir=runtime_dir,
            runner=runner,
        )
        == exit_code
    )
