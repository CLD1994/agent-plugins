"""宿主参数化的 SessionStart 状态矩阵：launcher 先发布 bridge，再无条件调用一次内层 uv。"""

from __future__ import annotations

import io
import json
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest
from test_launcher import load_launcher

from draft import session_hook
from draft.bindings import bind, binding_path, skill_digest
from draft.lifecycle import initialize
from draft.models import Binding

launcher = load_launcher()

HOSTS = ("codex", "claude")

SOURCES = {
    "codex": ("startup", "resume", "clear", "compact"),
    "claude": ("startup", "resume", "clear", "compact", "fork"),
}

HOST_ENV = {
    "codex": ("PLUGIN_ROOT", "PLUGIN_DATA"),
    "claude": ("CLAUDE_PLUGIN_ROOT", "CLAUDE_PLUGIN_DATA"),
}

VERSION = "0.1.0"


@dataclass
class Setup:
    tmp_path: Path
    entry: Path
    skill_path: Path
    plugin_root: Path
    data_dir: Path
    runtime_dir: Path
    version: str


class HookRunner:
    """替换内层 uv：记录调用，并把 `draft _session-start HOST` 在当前进程内执行。"""

    def __init__(self, *, fail_code: int | None = None) -> None:
        self.calls: list[tuple[list[str], dict[str, str]]] = []
        self.fail_code = fail_code

    def __call__(self, arguments: Sequence[str], env: Mapping[str, str]) -> int:
        args = list(arguments)
        self.calls.append((args, dict(env)))
        if self.fail_code is not None:
            return self.fail_code
        assert args[:3] == ["uv", "run", "--project"]
        assert args[-3:-1] == ["draft", "_session-start"]
        return session_hook.main([args[-1]], environ=env, stdin=sys.stdin)


def make_plugin(tmp_path: Path) -> tuple[Path, Path, Path]:
    plugin_root = tmp_path / "plugin"
    skill_dir = plugin_root / "skills" / "draft"
    runtime = skill_dir / "scripts" / "runtime"
    runtime.mkdir(parents=True)
    (runtime / "pyproject.toml").write_text(
        f'[project]\nname = "draft-runtime"\nversion = "{VERSION}"\n'
        'requires-python = ">=3.12,<3.15"\n',
        encoding="utf-8",
    )
    (runtime / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    entry = skill_dir / "scripts" / "draft.py"
    entry.write_text("# launcher\n", encoding="utf-8")
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text("测试 skill 内容\n", encoding="utf-8")
    return entry, skill_path, plugin_root


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Setup:
    entry, skill_path, plugin_root = make_plugin(tmp_path)
    data_dir = tmp_path / "host-data"
    data_dir.mkdir()
    runtime_dir = tmp_path / "xdg"
    runtime_dir.mkdir()
    runtime_dir.chmod(0o700)
    for root_variable, data_variable in HOST_ENV.values():
        monkeypatch.delenv(root_variable, raising=False)
        monkeypatch.delenv(data_variable, raising=False)
    return Setup(
        tmp_path=tmp_path,
        entry=entry,
        skill_path=skill_path,
        plugin_root=plugin_root,
        data_dir=data_dir,
        runtime_dir=runtime_dir,
        version=VERSION,
    )


def set_host_env(monkeypatch: pytest.MonkeyPatch, setup: Setup, host: str) -> None:
    root_variable, data_variable = HOST_ENV[host]
    monkeypatch.setenv(root_variable, str(setup.plugin_root))
    monkeypatch.setenv(data_variable, str(setup.data_dir))
    for other_host, (other_root, other_data) in HOST_ENV.items():
        if other_host != host:
            monkeypatch.setenv(other_root, str(setup.tmp_path / "decoy-root"))
            monkeypatch.setenv(other_data, str(setup.tmp_path / "decoy-data"))


def session_stdin(session: object = "abc123", source: str = "startup") -> str:
    payload: dict[str, object] = {
        "session_id": session,
        "transcript_path": "/not/read/transcript.jsonl",
        "cwd": "/workspace",
        "hook_event_name": "SessionStart",
        "source": source,
    }
    if session is _MISSING:
        del payload["session_id"]
    return json.dumps(payload, ensure_ascii=False)


class _Missing:
    pass


_MISSING = _Missing()


def run_hook(
    setup: Setup,
    host: str,
    stdin_text: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    runner: HookRunner | None = None,
) -> tuple[int, str, str, HookRunner]:
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin_text))
    active = HookRunner() if runner is None else runner
    code = launcher.main(
        ["_session-start", host],
        entry_path=setup.entry,
        environ=dict(os.environ),
        runtime_dir=setup.runtime_dir,
        runner=active,
    )
    captured = capsys.readouterr()
    return code, captured.out, captured.err, active


def bridge_record(setup: Setup) -> dict[str, object] | None:
    path = launcher.bridge_paths(setup.entry, setup.runtime_dir).json
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return cast("dict[str, object]", value)


def hook_log(setup: Setup) -> list[dict[str, object]]:
    path = setup.data_dir / "logs" / "hook.jsonl"
    if not path.exists():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


@pytest.fixture
def bound(setup: Setup) -> Binding:
    root = setup.tmp_path / "topic"
    initialize(root)
    return bind(
        setup.data_dir,
        Binding(
            session_id="abc123",
            root=root,
            last_page_hint=None,
            skill_name="draft",
            skill_digest=skill_digest(setup.skill_path),
        ),
    )


@pytest.fixture
def bound_with_hint(setup: Setup, bound: Binding) -> Binding:
    root = bound.root
    (root / "pages/a.md").write_text(
        "# A\n\n> 状态：`待讨论`\n>\n> 父主题：[根索引](../index.md)\n\nA。\n",
        encoding="utf-8",
    )
    index = root / "index.md"
    index.write_text(index.read_text(encoding="utf-8") + "- [A](pages/a.md)\n", encoding="utf-8")
    return bind(
        setup.data_dir,
        Binding(
            session_id="abc123",
            root=root,
            last_page_hint=Path("pages/a.md"),
            skill_name="draft",
            skill_digest=bound.skill_digest,
        ),
    )


def assert_single_inner_uv(setup: Setup, runner: HookRunner, host: str) -> None:
    assert len(runner.calls) == 1
    args, env = runner.calls[0]
    assert args[:3] == ["uv", "run", "--project"]
    assert args[-3:] == ["draft", "_session-start", host]
    assert env["UV_PROJECT_ENVIRONMENT"] == str(setup.data_dir.resolve() / "envs" / setup.version)
    assert env["DRAFT_DATA"] == str(setup.data_dir.resolve())


@pytest.mark.parametrize("host", HOSTS)
def test_bridge_is_published_before_session_fields_are_checked(
    setup: Setup,
    host: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    set_host_env(monkeypatch, setup, host)
    code, output, _, runner = run_hook(setup, host, session_stdin(_MISSING), monkeypatch, capsys)

    assert code == 0
    assert output == ""
    record = bridge_record(setup)
    assert record == {"schema_version": 1, "data_dir": str(setup.data_dir.resolve())}
    assert hook_log(setup)[-1]["error"] == "binding.session_unavailable"
    assert not (setup.data_dir / "bindings").exists()
    assert_single_inner_uv(setup, runner, host)


@pytest.mark.parametrize("host", HOSTS)
def test_host_reads_only_its_own_variables(
    setup: Setup,
    host: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """兼容别名指向不存在的路径；成功即证明别名未参与选择。"""
    set_host_env(monkeypatch, setup, host)
    code, output, _, runner = run_hook(setup, host, session_stdin("abc123"), monkeypatch, capsys)

    assert code == 0
    assert output == session_hook.render_session_fact("abc123") + "\n"
    record = bridge_record(setup)
    assert record is not None
    assert record["data_dir"] == str(setup.data_dir.resolve())
    assert_single_inner_uv(setup, runner, host)


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("bad_session", [_MISSING, 123, None, ["x"], {"id": "x"}])
def test_missing_or_non_string_session_id_reports_session_unavailable(
    setup: Setup,
    host: str,
    bad_session: object,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    set_host_env(monkeypatch, setup, host)
    code, output, _, runner = run_hook(setup, host, session_stdin(bad_session), monkeypatch, capsys)

    assert code == 0
    assert output == ""
    record = bridge_record(setup)
    assert record == {"schema_version": 1, "data_dir": str(setup.data_dir.resolve())}
    entry = hook_log(setup)[-1]
    assert entry["error"] == "binding.session_unavailable"
    assert entry["host"] == host
    assert not (setup.data_dir / "bindings").exists()
    assert_single_inner_uv(setup, runner, host)


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("session", ["", "abc123", "会话-☃-123", "a b\tc"])
def test_every_string_is_accepted_as_session_identity(
    setup: Setup,
    host: str,
    session: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    set_host_env(monkeypatch, setup, host)
    code, output, _, runner = run_hook(setup, host, session_stdin(session), monkeypatch, capsys)

    assert code == 0
    assert output == session_hook.render_session_fact(session) + "\n"
    assert_single_inner_uv(setup, runner, host)


@pytest.mark.parametrize("host", HOSTS)
def test_unbound_session_outputs_only_the_session_fact(
    setup: Setup,
    host: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    set_host_env(monkeypatch, setup, host)
    code, output, _, runner = run_hook(
        setup, host, session_stdin("new-session"), monkeypatch, capsys
    )

    assert code == 0
    assert output == session_hook.render_session_fact("new-session") + "\n"
    assert "活动草稿根" not in output
    assert "/bin/" not in output
    assert "envs" not in output
    assert str(setup.data_dir) not in output
    assert_single_inner_uv(setup, runner, host)
    assert hook_log(setup)[-1]["branch"] == "session_fact"


@pytest.mark.parametrize("host", HOSTS)
def test_bound_session_outputs_rebind_context(
    setup: Setup,
    host: str,
    bound_with_hint: Binding,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    set_host_env(monkeypatch, setup, host)
    for source in SOURCES[host]:
        code, output, _, runner = run_hook(
            setup, host, session_stdin("abc123", source), monkeypatch, capsys
        )

        assert code == 0
        assert f"活动草稿根：{bound_with_hint.root}" in output
        assert "最后页面提示：pages/a.md" in output
        assert f"必须使用的 skill：draft（摘要 {bound_with_hint.skill_digest}）" in output
        assert f"宿主触发原因：{source}" in output
        assert_single_inner_uv(setup, runner, host)
        _, hook_env = runner.calls[0]
        assert hook_env["DRAFT_SKILL_PATH"] == str(setup.skill_path)
        assert "PYTHONPATH" not in hook_env
        assert hook_log(setup)[-1]["branch"] == "rebind"


@pytest.mark.parametrize("host", HOSTS)
def test_stale_page_hint_is_omitted(
    setup: Setup,
    host: str,
    bound_with_hint: Binding,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    set_host_env(monkeypatch, setup, host)
    (bound_with_hint.root / "pages/a.md").unlink()
    code, output, _, runner = run_hook(
        setup, host, session_stdin("abc123", "compact"), monkeypatch, capsys
    )

    assert code == 0
    assert f"活动草稿根：{bound_with_hint.root}" in output
    assert "最后页面提示" not in output
    assert_single_inner_uv(setup, runner, host)


@pytest.mark.parametrize("host", HOSTS)
def test_missing_bound_root_outputs_diagnostic_without_exposing_root(
    setup: Setup,
    host: str,
    bound: Binding,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import shutil

    set_host_env(monkeypatch, setup, host)
    shutil.rmtree(bound.root)
    code, output, _, runner = run_hook(
        setup, host, session_stdin("abc123", "compact"), monkeypatch, capsys
    )

    assert code == 0
    assert "活动草稿根" not in output
    assert str(bound.root) not in output
    assert hook_log(setup)[-1]["error"] == "binding.root_missing"
    assert_single_inner_uv(setup, runner, host)


@pytest.mark.parametrize("host", HOSTS)
def test_skill_digest_mismatch_outputs_diagnostic_without_exposing_root(
    setup: Setup,
    host: str,
    bound: Binding,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    set_host_env(monkeypatch, setup, host)
    setup.skill_path.write_text("已被替换的 skill 内容\n", encoding="utf-8")
    code, output, _, runner = run_hook(
        setup, host, session_stdin("abc123", "compact"), monkeypatch, capsys
    )

    assert code == 0
    assert "活动草稿根" not in output
    assert str(bound.root) not in output
    assert hook_log(setup)[-1]["error"] == "binding.skill_mismatch"
    assert_single_inner_uv(setup, runner, host)


@pytest.mark.parametrize("host", HOSTS)
def test_repeated_invocation_with_identical_state_is_byte_identical(
    setup: Setup,
    host: str,
    bound_with_hint: Binding,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    set_host_env(monkeypatch, setup, host)
    stdin_text = session_stdin("abc123", "compact")
    _, first, _, _ = run_hook(setup, host, stdin_text, monkeypatch, capsys)
    _, second, _, _ = run_hook(setup, host, stdin_text, monkeypatch, capsys)
    assert first == second

    _, third, _, _ = run_hook(setup, host, session_stdin("unbound", "startup"), monkeypatch, capsys)
    _, fourth, _, _ = run_hook(
        setup, host, session_stdin("unbound", "startup"), monkeypatch, capsys
    )
    assert third == fourth


@pytest.mark.parametrize("host", HOSTS)
def test_failure_kinds_remain_distinguishable(
    setup: Setup,
    host: str,
    bound: Binding,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    set_host_env(monkeypatch, setup, host)
    target = binding_path(setup.data_dir, "abc123")

    target.write_text("{损坏", encoding="utf-8")
    code, output, _, _ = run_hook(
        setup, host, session_stdin("abc123", "compact"), monkeypatch, capsys
    )
    assert code == 0
    assert "binding.invalid" in output
    assert str(bound.root) not in output
    assert hook_log(setup)[-1]["error"] == "binding.invalid"

    target.write_text(
        json.dumps(
            {
                "session_id": "other-session",
                "root": str(bound.root),
                "last_page_hint": None,
                "skill_name": "draft",
                "skill_digest": bound.skill_digest,
            }
        ),
        encoding="utf-8",
    )
    code, output, _, _ = run_hook(
        setup, host, session_stdin("abc123", "compact"), monkeypatch, capsys
    )
    assert code == 0
    assert "binding.session_mismatch" in output
    assert str(bound.root) not in output
    assert hook_log(setup)[-1]["error"] == "binding.session_mismatch"

    _, data_variable = HOST_ENV[host]
    other_data = setup.tmp_path / "other-data"
    other_data.mkdir()
    monkeypatch.setenv(data_variable, str(other_data))
    code, _, stderr, runner = run_hook(
        setup, host, session_stdin("abc123", "compact"), monkeypatch, capsys
    )
    assert code == 4
    assert "plugin.context_conflict" in stderr
    assert runner.calls == []

    launcher.reset_context(setup.entry, setup.runtime_dir)
    monkeypatch.setenv(data_variable, str(setup.data_dir))
    code, _, _, runner = run_hook(
        setup,
        host,
        session_stdin("abc123", "compact"),
        monkeypatch,
        capsys,
        HookRunner(fail_code=7),
    )
    assert code == 7
    assert len(runner.calls) == 1
    record = bridge_record(setup)
    assert record is not None
    assert record["data_dir"] == str(setup.data_dir.resolve())

    for code_text in ("binding.invalid", "binding.session_mismatch"):
        assert code_text != "plugin.context_conflict"


@pytest.mark.parametrize("host", HOSTS)
def test_plugin_root_mismatch_stops_before_publish(
    setup: Setup,
    host: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root_variable, _ = HOST_ENV[host]
    wrong_root = setup.tmp_path / "wrong-root"
    wrong_root.mkdir()
    set_host_env(monkeypatch, setup, host)
    monkeypatch.setenv(root_variable, str(wrong_root))

    code, output, stderr, runner = run_hook(setup, host, session_stdin(), monkeypatch, capsys)

    assert code == 4
    assert output == ""
    assert "hook.plugin_root_mismatch" in stderr
    assert bridge_record(setup) is None
    assert runner.calls == []


@pytest.mark.parametrize("host", HOSTS)
def test_wrong_event_and_invalid_stdin_are_silent_but_logged(
    setup: Setup,
    host: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    set_host_env(monkeypatch, setup, host)

    wrong_event = json.dumps({"hook_event_name": "PreCompact", "session_id": "abc123"})
    code, output, _, runner = run_hook(setup, host, wrong_event, monkeypatch, capsys)
    assert code == 0
    assert output == ""
    assert hook_log(setup)[-1]["error"] == "hook.unexpected_event"
    assert_single_inner_uv(setup, runner, host)

    code, output, _, _ = run_hook(setup, host, "{not json", monkeypatch, capsys)
    assert code == 0
    assert output == ""
    assert hook_log(setup)[-1]["error"] == "hook.stdin_invalid"

    code, output, _, _ = run_hook(setup, host, " " * (64 * 1024 + 1), monkeypatch, capsys)
    assert code == 0
    assert output == ""
    assert hook_log(setup)[-1]["error"] == "hook.stdin_oversized"

    record = bridge_record(setup)
    assert record == {"schema_version": 1, "data_dir": str(setup.data_dir.resolve())}
