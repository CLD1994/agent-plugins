from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest

from draft.entry import (
    RuntimeLayout,
    environment_key,
    environment_path,
    main,
    sync_environment,
)
from draft.errors import DraftError
from draft.plugin_context import PluginContext, publish


def make_layout(
    tmp_path: Path,
    pyproject: bytes = b"project",
    lock: bytes = b"lock",
) -> RuntimeLayout:
    runtime = tmp_path / "runtime"
    source = runtime / "src"
    source.mkdir(parents=True)
    (runtime / "pyproject.toml").write_bytes(pyproject)
    (runtime / "uv.lock").write_bytes(lock)
    entry = tmp_path / "draft"
    entry.write_text("#!/bin/sh\n", encoding="utf-8")
    skill = tmp_path / "SKILL.md"
    skill.write_text("# skill\n", encoding="utf-8")
    return RuntimeLayout(
        entry_path=entry,
        skill_path=skill,
        project_path=runtime,
        source_path=source,
    )


def internal_argv(layout: RuntimeLayout, *user_args: str) -> list[str]:
    return [
        "--entry-path",
        str(layout.entry_path),
        "--skill-path",
        str(layout.skill_path),
        "--runtime-path",
        str(layout.project_path),
        "--",
        *user_args,
    ]


@dataclass
class Call:
    args: list[str]
    env: dict[str, str]


class FakeRunner:
    def __init__(
        self,
        *,
        sync_code: int = 0,
        self_check_code: int = 0,
        domain_code: int = 0,
    ) -> None:
        self.calls: list[Call] = []
        self.sync_code = sync_code
        self.self_check_code = self_check_code
        self.domain_code = domain_code

    def __call__(self, arguments: Sequence[str], env: Mapping[str, str]) -> int:
        args = list(arguments)
        self.calls.append(Call(args, dict(env)))
        if args[:2] == ["uv", "sync"]:
            return self.sync_code
        if args[1:] == ["-m", "draft", "--version"]:
            return self.self_check_code
        return self.domain_code


@pytest.fixture
def runtime_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    xdg = tmp_path / "xdg"
    xdg.mkdir()
    xdg.chmod(0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(xdg))
    return xdg


def test_environment_key_uses_manifest_bytes(tmp_path: Path) -> None:
    layout = make_layout(tmp_path, pyproject=b"project", lock=b"lock")
    expected = hashlib.sha256(
        b"draft-environment-v1\0"
        + hashlib.sha256(b"project").digest()
        + hashlib.sha256(b"lock").digest()
    ).hexdigest()
    assert environment_key(layout) == expected


def test_environment_key_changes_only_with_manifest_bytes(tmp_path: Path) -> None:
    base = environment_key(make_layout(tmp_path / "base"))
    changed_project = environment_key(make_layout(tmp_path / "project", pyproject=b"project2"))
    changed_lock = environment_key(make_layout(tmp_path / "lock", lock=b"lock2"))
    assert changed_project != base
    assert changed_lock != base
    source_only = make_layout(tmp_path / "source")
    (source_only.source_path / "changed.py").write_text("x = 1\n", encoding="utf-8")
    assert environment_key(source_only) == base


def test_environment_path_switches_only_on_valid_context(tmp_path: Path) -> None:
    layout = make_layout(tmp_path)
    runtime_dir = tmp_path / "runtime-dir"
    data_dir = tmp_path / "plugin-data"
    data_dir.mkdir()
    key = environment_key(layout)
    assert environment_path(layout, None, runtime_dir) == runtime_dir / "draft/envs" / key
    assert environment_path(layout, PluginContext(data_dir), runtime_dir) == data_dir / "envs" / key


def test_sync_environment_invokes_uv_with_exact_arguments(tmp_path: Path) -> None:
    layout = make_layout(tmp_path)
    target = tmp_path / "env"
    runner = FakeRunner()
    assert sync_environment(layout, target, {"PATH": "/usr/bin"}, run=runner) == target
    assert len(runner.calls) == 1
    call = runner.calls[0]
    assert call.args == [
        "uv", "sync", "--project", str(layout.project_path),
        "--locked", "--no-install-project", "--no-dev",
    ]
    assert call.env["UV_PROJECT_ENVIRONMENT"] == str(target)
    assert call.env["PATH"] == "/usr/bin"


def test_sync_environment_rejects_symlink_and_non_directory(tmp_path: Path) -> None:
    layout = make_layout(tmp_path)
    regular = tmp_path / "regular"
    regular.write_text("x", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(tmp_path)
    runner = FakeRunner()
    for target in (regular, link):
        with pytest.raises(DraftError) as captured:
            sync_environment(layout, target, {}, run=runner)
        assert captured.value.code == "runtime.environment_invalid"
    assert runner.calls == []


def test_main_sync_failure_stops_before_self_check_and_domain(
    tmp_path: Path,
    runtime_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = make_layout(tmp_path)
    runner = FakeRunner(sync_code=7)
    monkeypatch.setattr("draft.entry.run_command", runner)
    assert main(internal_argv(layout, "check", "x")) == 7
    assert len(runner.calls) == 1
    assert runner.calls[0].args[:2] == ["uv", "sync"]


def test_main_self_check_uses_current_source_path(
    tmp_path: Path,
    runtime_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = make_layout(tmp_path)
    runner = FakeRunner()
    monkeypatch.setattr("draft.entry.run_command", runner)
    assert main(internal_argv(layout, "check", "x")) == 0
    target = runtime_env / "draft" / "envs" / environment_key(layout)
    python = target / "bin" / "python"
    sync_call, self_check_call, _ = runner.calls
    assert sync_call.env["UV_PROJECT_ENVIRONMENT"] == str(target)
    assert self_check_call.args == [str(python), "-m", "draft", "--version"]
    assert self_check_call.env["PYTHONPATH"] == str(layout.source_path)


def test_main_self_check_failure_blocks_domain_command(
    tmp_path: Path,
    runtime_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = make_layout(tmp_path)
    runner = FakeRunner(self_check_code=3)
    monkeypatch.setattr("draft.entry.run_command", runner)
    assert main(internal_argv(layout, "check", "x")) == 3
    assert len(runner.calls) == 2


def test_setup_never_invokes_domain_cli(
    tmp_path: Path,
    runtime_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = make_layout(tmp_path)
    runner = FakeRunner()
    monkeypatch.setattr("draft.entry.run_command", runner)
    assert main(internal_argv(layout, "setup")) == 0
    target = runtime_env / "draft" / "envs" / environment_key(layout)
    python = str(target / "bin" / "python")
    assert [call.args[:2] for call in runner.calls] == [["uv", "sync"], [python, "-m"]]


def test_domain_command_runs_after_checks_and_propagates_exit_code(
    tmp_path: Path,
    runtime_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = make_layout(tmp_path)
    runner = FakeRunner(domain_code=5)
    monkeypatch.setattr("draft.entry.run_command", runner)
    monkeypatch.setenv("DRAFT_DATA", "/stale")
    assert main(internal_argv(layout, "check", "x")) == 5
    target = runtime_env / "draft" / "envs" / environment_key(layout)
    domain_call = runner.calls[-1]
    assert domain_call.args == [str(target / "bin" / "python"), "-m", "draft", "check", "x"]
    assert domain_call.env["PYTHONPATH"] == str(layout.source_path)
    assert domain_call.env["DRAFT_SKILL_PATH"] == str(layout.skill_path)
    assert "DRAFT_DATA" not in domain_call.env


def test_plugin_mode_sets_draft_data_and_uses_data_dir_environment(
    tmp_path: Path,
    runtime_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = make_layout(tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    publish(layout.entry_path, data_dir)
    runner = FakeRunner()
    monkeypatch.setattr("draft.entry.run_command", runner)
    monkeypatch.delenv("DRAFT_DATA", raising=False)
    assert main(internal_argv(layout, "check", "x")) == 0
    target = data_dir.resolve() / "envs" / environment_key(layout)
    sync_call = runner.calls[0]
    assert sync_call.env["UV_PROJECT_ENVIRONMENT"] == str(target)
    domain_call = runner.calls[-1]
    assert domain_call.env["DRAFT_DATA"] == str(data_dir.resolve())
    assert domain_call.env["DRAFT_SKILL_PATH"] == str(layout.skill_path)


def test_main_reports_invalid_layout(
    tmp_path: Path,
    runtime_env: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    argv = [
        "--entry-path", str(tmp_path / "missing-entry"),
        "--skill-path", str(tmp_path / "missing-skill"),
        "--runtime-path", str(tmp_path / "missing-runtime"),
        "--", "setup",
    ]
    assert main(argv) == 4
    assert "entry.layout_invalid" in capsys.readouterr().err


def test_main_reports_missing_uv(
    tmp_path: Path,
    runtime_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    layout = make_layout(tmp_path)

    def missing_uv(arguments: Sequence[str], env: Mapping[str, str]) -> int:
        raise FileNotFoundError("uv")

    monkeypatch.setattr("draft.entry.run_command", missing_uv)
    assert main(internal_argv(layout, "setup")) == 4
    assert "runtime.uv_unavailable" in capsys.readouterr().err


def test_session_start_rejects_unknown_host(
    tmp_path: Path,
    runtime_env: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    layout = make_layout(tmp_path)
    assert main(internal_argv(layout, "_session-start", "other")) == 4
    assert "entry.invalid_arguments" in capsys.readouterr().err


def test_session_start_requires_host_variables(
    tmp_path: Path,
    runtime_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    layout = make_layout(tmp_path)
    for variable in ("PLUGIN_ROOT", "PLUGIN_DATA", "CLAUDE_PLUGIN_ROOT", "CLAUDE_PLUGIN_DATA"):
        monkeypatch.delenv(variable, raising=False)
    assert main(internal_argv(layout, "_session-start", "codex")) == 4
    assert "hook.environment_missing" in capsys.readouterr().err
    assert main(internal_argv(layout, "_session-start", "claude")) == 4
    assert "hook.environment_missing" in capsys.readouterr().err
