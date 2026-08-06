"""插件 CLI 测试：参数、分派、输出与退出码；bridge 加载与 reset 顺序；Hook 始终成功。

覆盖三条路径：
- bind/unbind 的 subprocess 与 in-process 调用、--json 位置等价性、退出码与输出；
- bridge 缺失/无效/冲突映射到 plugin.context_*；reset 先于 bridge 加载且不加载 bridge；
- hook session-start 始终返回 0，仅转发宿主与上下文；非 hook session-start 形态走 argparse 退出 2。
"""

from __future__ import annotations

import importlib
import io
import json
import os
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest

from adapter.bindings import Binding, binding_path
from adapter.bridge import bridge_paths, publish_context
from adapter.errors import EXIT_RUNTIME, EXIT_USAGE

# 以 importlib 导入入口模块，避免 `import draft` 语法与核心 draft 包同名冲突；
# conftest 已将 plugins/draft/scripts/ 插入 sys.path[0]，运行时解析到入口模块。
plugin_cli = importlib.import_module("draft")

HOSTS = ("codex", "claude")
# 真实入口路径，供 subprocess 测试使用。
ENTRY_FILE = Path(__file__).resolve().parent.parent / "draft.py"


# ---------------------------------------------------------------------------
# 固件
# ---------------------------------------------------------------------------


@dataclass
class Setup:
    entry: Path
    data_dir: Path
    root: Path
    runtime_dir: Path
    base: Path


@pytest.fixture
def bare_setup(tmp_path: Path) -> Setup:
    """创建入口、data_dir、root 与 runtime_dir，但不发布 bridge。"""
    base = tmp_path.resolve()
    entry = base / "plugin" / "scripts" / "draft.py"
    entry.parent.mkdir(parents=True)
    entry.write_text("# entry\n", encoding="utf-8")
    data_dir = (base / "data").resolve()
    data_dir.mkdir()
    root = (base / "root").resolve()
    root.mkdir()
    runtime_dir = (base / "xdg").resolve()
    runtime_dir.mkdir()
    runtime_dir.chmod(0o700)
    return Setup(entry=entry, data_dir=data_dir, root=root, runtime_dir=runtime_dir, base=base)


@pytest.fixture
def setup(bare_setup: Setup) -> Setup:
    """发布有效 bridge，使 load_context 返回 PluginContext(data_dir)。"""
    publish_context(bare_setup.entry, bare_setup.data_dir, bare_setup.runtime_dir)
    return bare_setup


@dataclass
class SubprocSetup:
    data_dir: Path
    root: Path
    runtime_dir: Path
    base: Path


@pytest.fixture
def subproc_setup(tmp_path: Path) -> SubprocSetup:
    """为真实入口发布 bridge，供 subprocess 测试。"""
    base = tmp_path.resolve()
    data_dir = (base / "data").resolve()
    data_dir.mkdir()
    root = (base / "root").resolve()
    root.mkdir()
    runtime_dir = (base / "xdg").resolve()
    runtime_dir.mkdir()
    runtime_dir.chmod(0o700)
    publish_context(ENTRY_FILE, data_dir, runtime_dir)
    return SubprocSetup(data_dir=data_dir, root=root, runtime_dir=runtime_dir, base=base)


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def call_main(
    args: Sequence[str],
    setup_obj: Setup,
    capsys: pytest.CaptureFixture[str],
    *,
    stdin: str = "",
    env: Mapping[str, str] | None = None,
    runtime_dir: Path | None = None,
) -> tuple[int, str, str]:
    """进程内调用 plugin_cli.main，返回 (exit_code, stdout, stderr)。"""
    # pyright 将 import draft 解析为核心 draft 包；运行时由 conftest 的 sys.path
    # 优先级确保 plugins/draft/scripts/draft.py 覆盖核心包。
    code = plugin_cli.main(  # type: ignore[reportAttributeAccessIssue]
        list(args),
        entry_path=setup_obj.entry,
        environ=dict(env) if env is not None else {},
        stdin=io.BytesIO(stdin.encode("utf-8")),
        runtime_dir=setup_obj.runtime_dir if runtime_dir is None else runtime_dir,
    )
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def run_subprocess(
    args: Sequence[str],
    subproc: SubprocSetup,
    *,
    stdin: str | None = None,
) -> tuple[int, str, str]:
    """以 uv run 执行真实入口，返回 (returncode, stdout, stderr)。"""
    env = {**os.environ, "XDG_RUNTIME_DIR": str(subproc.runtime_dir)}
    cmd = ["uv", "run", str(ENTRY_FILE), *args]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=env,
        input=stdin,
        timeout=120,
        check=False,
    )
    return result.returncode, result.stdout, result.stderr


def write_invalid_bridge(entry: Path, runtime_dir: Path) -> None:
    """写入含额外字段的 bridge JSON，使 load_context 报 plugin.context_invalid。"""
    paths = bridge_paths(entry, runtime_dir)
    paths.json.write_text(
        json.dumps({"schema_version": 1, "data_dir": "/tmp", "extra": 1}),
        encoding="utf-8",
    )


def write_conflict_bridge(entry: Path, runtime_dir: Path) -> None:
    """创建 conflict 哨兵，使 load_context 报 plugin.context_conflict。"""
    paths = bridge_paths(entry, runtime_dir)
    paths.conflict.write_text("", encoding="utf-8")


# ---------------------------------------------------------------------------
# Step 1: 参数、分派、输出与退出码
# ---------------------------------------------------------------------------


def test_bind_text_success(setup: Setup, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = call_main(["bind", str(setup.root), "--session", "s1"], setup, capsys)
    assert code == 0
    assert out == f"绑定成功 {setup.root}\n"
    assert err == ""


def test_bind_with_last_page_hint_succeeds(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    page = setup.root / "page.md"
    page.write_text("# page\n", encoding="utf-8")
    code, out, _ = call_main(
        ["bind", str(setup.root), "--session", "s", "--last-page-hint", "page.md"],
        setup,
        capsys,
    )
    assert code == 0
    assert out == f"绑定成功 {setup.root}\n"


@pytest.mark.parametrize(
    "argv",
    [
        ["--json", "bind", "ROOT", "--session", "s"],
        ["bind", "--json", "ROOT", "--session", "s"],
        ["bind", "ROOT", "--session", "s", "--json"],
    ],
)
def test_bind_json_all_positions_equivalent(
    setup: Setup, capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    args = [a.replace("ROOT", str(setup.root)) for a in argv]
    code, out, err = call_main(args, setup, capsys)
    assert code == 0
    assert err == ""
    payload = json.loads(out)
    assert payload == {"ok": True, "result_type": "bind", "root": str(setup.root)}


def test_bind_missing_root_arg_is_usage_error(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, err = call_main(["bind", "--session", "s"], setup, capsys)
    assert code == EXIT_USAGE
    assert "plugin.invalid_arguments" in err
    assert "Traceback" not in err


def test_bind_missing_session_is_usage_error(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = call_main(["bind", str(setup.root)], setup, capsys)
    assert code == EXIT_USAGE
    assert "plugin.invalid_arguments" in err


def test_bind_nonexistent_root_is_root_unavailable(
    setup: Setup, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    missing = tmp_path / "missing"
    code, _, err = call_main(["bind", str(missing), "--session", "s"], setup, capsys)
    assert code == EXIT_USAGE
    assert "binding.root_unavailable" in err
    assert "Traceback" not in err


def test_bind_file_root_is_root_unavailable(
    setup: Setup, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    file_root = tmp_path / "file"
    file_root.write_text("x", encoding="utf-8")
    code, _, err = call_main(["bind", str(file_root), "--session", "s"], setup, capsys)
    assert code == EXIT_USAGE
    assert "binding.root_unavailable" in err


def test_bind_json_error_uses_error_payload(
    setup: Setup, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    missing = tmp_path / "missing"
    code, out, err = call_main(["bind", "--json", str(missing), "--session", "s"], setup, capsys)
    assert code == EXIT_USAGE
    assert err == ""
    payload = json.loads(out)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "binding.root_unavailable"
    assert "Traceback" not in out


def test_bind_text_error_is_one_line_no_traceback(
    setup: Setup, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    missing = tmp_path / "missing"
    code, out, err = call_main(["bind", str(missing), "--session", "s"], setup, capsys)
    assert code == EXIT_USAGE
    assert out == ""
    lines = err.strip().splitlines()
    assert len(lines) == 1
    assert "Traceback" not in err


def test_unbind_text_success_present(setup: Setup, capsys: pytest.CaptureFixture[str]) -> None:
    from adapter.bindings import bind

    bind(setup.data_dir, Binding(session_id="s1", root=setup.root, last_page_hint=None))
    code, out, err = call_main(["unbind", "--session", "s1"], setup, capsys)
    assert code == 0
    assert out == "解除成功 removed=true\n"
    assert err == ""


def test_unbind_text_success_absent(setup: Setup, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = call_main(["unbind", "--session", "absent"], setup, capsys)
    assert code == 0
    assert out == "解除成功 removed=false\n"
    assert err == ""


@pytest.mark.parametrize(
    "argv",
    [
        ["--json", "unbind", "--session", "s"],
        ["unbind", "--json", "--session", "s"],
        ["unbind", "--session", "s", "--json"],
    ],
)
def test_unbind_json_success(
    setup: Setup, capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    from adapter.bindings import bind

    bind(setup.data_dir, Binding(session_id="s", root=setup.root, last_page_hint=None))
    code, out, err = call_main(argv, setup, capsys)
    assert code == 0
    assert err == ""
    payload = json.loads(out)
    assert payload == {"ok": True, "result_type": "unbind", "removed": True}


def test_unbind_json_absent_removed_false(setup: Setup, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = call_main(["--json", "unbind", "--session", "absent"], setup, capsys)
    assert code == 0
    payload = json.loads(out)
    assert payload == {"ok": True, "result_type": "unbind", "removed": False}


def test_unbind_missing_session_is_usage_error(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = call_main(["unbind"], setup, capsys)
    assert code == EXIT_USAGE
    assert "plugin.invalid_arguments" in err


def test_unknown_command_is_usage_error(setup: Setup, capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = call_main(["bogus"], setup, capsys)
    assert code == EXIT_USAGE
    assert "plugin.invalid_arguments" in err


def test_no_args_is_usage_error(setup: Setup, capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = call_main([], setup, capsys)
    assert code == EXIT_USAGE
    assert "plugin.invalid_arguments" in err


def test_help_exits_zero(setup: Setup, capsys: pytest.CaptureFixture[str]) -> None:
    # argparse 打印帮助后 SystemExit(0)，入口捕获并以 0 返回；不触达 bridge 加载。
    code, out, _ = call_main(["--help"], setup, capsys)
    assert code == 0
    assert "usage:" in out


def test_json_on_reset_is_usage_error(setup: Setup, capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = call_main(["plugin-context", "reset", "--json"], setup, capsys)
    assert code == EXIT_USAGE
    assert "plugin.invalid_arguments" in err


def test_json_before_reset_is_usage_error(setup: Setup, capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = call_main(["--json", "plugin-context", "reset"], setup, capsys)
    assert code == EXIT_USAGE
    assert "plugin.invalid_arguments" in err


def test_json_on_hook_outside_namespace_is_usage_error(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = call_main(["hook", "--json"], setup, capsys)
    assert code == EXIT_USAGE
    assert "plugin.invalid_arguments" in err


# ---------------------------------------------------------------------------
# Step 2: bridge 加载与 reset 顺序
# ---------------------------------------------------------------------------


def test_bind_missing_bridge_is_context_unavailable(
    bare_setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = call_main(["bind", str(bare_setup.root), "--session", "s"], bare_setup, capsys)
    assert code == EXIT_RUNTIME
    assert "plugin.context_unavailable" in err


def test_unbind_missing_bridge_is_context_unavailable(
    bare_setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = call_main(["unbind", "--session", "s"], bare_setup, capsys)
    assert code == EXIT_RUNTIME
    assert "plugin.context_unavailable" in err


def test_bind_invalid_bridge_is_context_invalid(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    write_invalid_bridge(setup.entry, setup.runtime_dir)
    code, _, err = call_main(["bind", str(setup.root), "--session", "s"], setup, capsys)
    assert code == EXIT_RUNTIME
    assert "plugin.context_invalid" in err


def test_unbind_invalid_bridge_is_context_invalid(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    write_invalid_bridge(setup.entry, setup.runtime_dir)
    code, _, err = call_main(["unbind", "--session", "s"], setup, capsys)
    assert code == EXIT_RUNTIME
    assert "plugin.context_invalid" in err


def test_bind_conflict_bridge_is_context_conflict(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    write_conflict_bridge(setup.entry, setup.runtime_dir)
    code, _, err = call_main(["bind", str(setup.root), "--session", "s"], setup, capsys)
    assert code == EXIT_RUNTIME
    assert "plugin.context_conflict" in err


def test_valid_bridge_allows_ordinary_directory_without_draft(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = call_main(["bind", str(setup.root), "--session", "s"], setup, capsys)
    assert code == 0
    assert out == f"绑定成功 {setup.root}\n"
    assert not (setup.root / ".draft").exists()
    assert not (setup.root / "index.md").exists()


def test_reset_removes_bridge_leaves_data_and_bindings(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    from adapter.bindings import bind

    bind(setup.data_dir, Binding(session_id="s", root=setup.root, last_page_hint=None))
    paths = bridge_paths(setup.entry, setup.runtime_dir)
    assert paths.json.exists()

    code, out, err = call_main(["plugin-context", "reset"], setup, capsys)

    assert code == 0
    assert out == ""
    assert err == ""
    assert not paths.json.exists()
    # data_dir、bindings 与日志不受影响
    assert setup.data_dir.exists()
    assert binding_path(setup.data_dir, "s").exists()


def test_reset_on_missing_bridge_is_silent_success(
    bare_setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, err = call_main(["plugin-context", "reset"], bare_setup, capsys)
    assert code == 0
    assert out == ""
    assert err == ""


def test_reset_with_extra_args_is_usage_error(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = call_main(["plugin-context", "reset", "extra"], setup, capsys)
    assert code == EXIT_USAGE
    assert "plugin.invalid_arguments" in err


def test_reset_precedes_and_avoids_bridge_load(
    setup: Setup, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def unexpected_call(*args: object, **kwargs: object) -> object:
        raise AssertionError("load_context must not be called during reset")

    monkeypatch.setattr(plugin_cli, "load_context", unexpected_call)
    code, out, err = call_main(["plugin-context", "reset"], setup, capsys)
    assert code == 0
    assert out == ""
    assert err == ""


def test_bind_calls_load_context_exactly_once(
    setup: Setup, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    real_load = plugin_cli.load_context  # type: ignore[reportAttributeAccessIssue]
    calls: list[int] = []

    def counting_load(*args: object, **kwargs: object) -> object:
        calls.append(1)
        return real_load(*cast(tuple, args), **cast(Mapping, kwargs))

    monkeypatch.setattr(plugin_cli, "load_context", counting_load)
    code, out, _ = call_main(["bind", str(setup.root), "--session", "s"], setup, capsys)
    assert code == 0
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# Step 3: Hook CLI 始终成功
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("host", HOSTS)
def test_hook_session_start_forwards_canonical_args_once(
    setup: Setup,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    host: str,
) -> None:
    calls: list[tuple[object, ...]] = []

    def fake(
        host_arg: str,
        *,
        entry_path: Path,
        environ: Mapping[str, str],
        stdin: io.BytesIO,
        runtime_dir: Path | None,
    ) -> int:
        calls.append((host_arg, entry_path, dict(environ), stdin.read(), runtime_dir))
        return 0

    monkeypatch.setattr(plugin_cli, "run_session_start", fake)
    env = {"PLUGIN_ROOT": "/x", "PLUGIN_DATA": "/y"}
    stdin_text = json.dumps(
        {"hook_event_name": "SessionStart", "session_id": "s", "source": "startup"}
    )
    code, out, err = call_main(
        ["hook", "session-start", host],
        setup,
        capsys,
        stdin=stdin_text,
        env=env,
    )
    assert code == 0
    assert err == ""
    assert len(calls) == 1
    forwarded_host, forwarded_entry, forwarded_env, forwarded_stdin, forwarded_runtime = calls[0]
    assert forwarded_host == host
    assert forwarded_entry == setup.entry
    assert forwarded_env == env
    assert forwarded_stdin == stdin_text.encode("utf-8")
    assert forwarded_runtime == setup.runtime_dir


@pytest.mark.parametrize("rest", [[], ["codex", "claude"], ["--json", "codex"]])
def test_hook_session_start_bad_host_count_returns_zero(
    setup: Setup,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    rest: list[str],
) -> None:
    def unexpected_call(*args: object, **kwargs: object) -> int:
        raise AssertionError("run_session_start must not be called for bad host count")

    monkeypatch.setattr(plugin_cli, "run_session_start", unexpected_call)
    code, out, _ = call_main(["hook", "session-start", *rest], setup, capsys, stdin="", env={})
    assert code == 0
    assert "hook.invalid_arguments" in out


def test_hook_session_start_returns_zero_on_adapter_diagnostic(
    setup: Setup,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """适配层报告任何失败分支时，CLI 仍返回 0 并透传诊断。"""

    def fake(*args: object, **kwargs: object) -> int:
        sys = __import__("sys")
        sys.stdout.write("Draft 插件诊断\n- 错误码：binding.root_missing\n")
        return 0

    monkeypatch.setattr(plugin_cli, "run_session_start", fake)
    code, out, _ = call_main(["hook", "session-start", "codex"], setup, capsys, stdin="", env={})
    assert code == 0
    assert "binding.root_missing" in out


@pytest.mark.parametrize(
    "argv",
    [
        ["hook"],
        ["hook", "foo"],
        ["hook", "session-startx", "codex"],
        ["bogus"],
    ],
)
def test_non_hook_session_start_shapes_exit_2(
    setup: Setup, capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    code, _, err = call_main(argv, setup, capsys)
    assert code == EXIT_USAGE
    assert "plugin.invalid_arguments" in err


# ---------------------------------------------------------------------------
# Subprocess 入口
# ---------------------------------------------------------------------------


def test_subprocess_bind_text_success(subproc_setup: SubprocSetup) -> None:
    code, out, err = run_subprocess(
        ["bind", str(subproc_setup.root), "--session", "s1"], subproc_setup
    )
    assert code == 0
    assert out.strip() == f"绑定成功 {subproc_setup.root}"
    assert err == ""


def test_subprocess_bind_json_success(subproc_setup: SubprocSetup) -> None:
    code, out, err = run_subprocess(
        ["--json", "bind", str(subproc_setup.root), "--session", "s1"], subproc_setup
    )
    assert code == 0
    assert err == ""
    payload = json.loads(out)
    assert payload == {
        "ok": True,
        "result_type": "bind",
        "root": str(subproc_setup.root),
    }


def test_subprocess_unbind_success(subproc_setup: SubprocSetup) -> None:
    from adapter.bindings import bind

    bind(
        subproc_setup.data_dir,
        Binding(session_id="s1", root=subproc_setup.root, last_page_hint=None),
    )
    code, out, err = run_subprocess(["unbind", "--session", "s1"], subproc_setup)
    assert code == 0
    assert out.strip() == "解除成功 removed=true"
    assert err == ""


def test_subprocess_missing_bridge_exit_4(tmp_path: Path) -> None:
    """未发布 bridge 时，subprocess bind 返回 4。"""
    base = tmp_path.resolve()
    root = (base / "root").resolve()
    root.mkdir()
    runtime_dir = (base / "xdg").resolve()
    runtime_dir.mkdir()
    runtime_dir.chmod(0o700)
    # 不发布 bridge
    env = {**os.environ, "XDG_RUNTIME_DIR": str(runtime_dir)}
    result = subprocess.run(
        ["uv", "run", str(ENTRY_FILE), "bind", str(root), "--session", "s"],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )
    assert result.returncode == EXIT_RUNTIME
    assert "plugin.context_unavailable" in result.stderr


def test_subprocess_root_unavailable_exit_2(subproc_setup: SubprocSetup) -> None:
    code, _, err = run_subprocess(
        ["bind", "/nonexistent-draft-root", "--session", "s"], subproc_setup
    )
    assert code == EXIT_USAGE
    assert "binding.root_unavailable" in err


# ---------------------------------------------------------------------------
# Step 4: bind/unbind 会话标识编码边界（Task 8）
# ---------------------------------------------------------------------------


def test_bind_lone_surrogate_session_is_invalid_session_text(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, err = call_main(["bind", str(setup.root), "--session", "\ud800"], setup, capsys)
    assert code == EXIT_USAGE
    assert out == ""
    assert "binding.invalid_session" in err
    assert "Traceback" not in err
    # 验证早于 bindings/ 目录创建
    assert not (setup.data_dir / "bindings").exists()


def test_bind_lone_surrogate_session_is_invalid_session_json(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, err = call_main(
        ["--json", "bind", str(setup.root), "--session", "\ud800"], setup, capsys
    )
    assert code == EXIT_USAGE
    assert err == ""
    payload = json.loads(out)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "binding.invalid_session"
    assert not (setup.data_dir / "bindings").exists()


def test_unbind_lone_surrogate_session_is_invalid_session_text(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, err = call_main(["unbind", "--session", "\ud800"], setup, capsys)
    assert code == EXIT_USAGE
    assert out == ""
    assert "binding.invalid_session" in err
    assert "Traceback" not in err
    assert not (setup.data_dir / "bindings").exists()


def test_unbind_lone_surrogate_session_is_invalid_session_json(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, err = call_main(["--json", "unbind", "--session", "\ud800"], setup, capsys)
    assert code == EXIT_USAGE
    assert err == ""
    payload = json.loads(out)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "binding.invalid_session"
    assert not (setup.data_dir / "bindings").exists()


def test_bind_unbind_unicode_session_round_trip(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    session = "会话-😀"
    code, out, _ = call_main(["bind", str(setup.root), "--session", session], setup, capsys)
    assert code == 0
    assert out == f"绑定成功 {setup.root}\n"
    assert binding_path(setup.data_dir, session).exists()
    code, out, _ = call_main(["unbind", "--session", session], setup, capsys)
    assert code == 0
    assert out == "解除成功 removed=true\n"


# ---------------------------------------------------------------------------
# Step 4.1: bind/unbind 的 session 校验先于 bridge 加载（双故障优先级）
# ---------------------------------------------------------------------------


# 无效 session：孤立高代理码位，无法严格 UTF-8 编码
_INVALID_SESSION = "\ud800"


def _prepare_bridge_state(setup: Setup, state: str) -> None:
    """在已发布有效 bridge 的 setup 上构造目标 bridge 状态。"""
    if state == "valid":
        return
    if state == "missing":
        bridge_paths(setup.entry, setup.runtime_dir).json.unlink()
    elif state == "invalid":
        write_invalid_bridge(setup.entry, setup.runtime_dir)
    elif state == "conflict":
        write_conflict_bridge(setup.entry, setup.runtime_dir)
    else:  # pragma: no cover - 防御
        raise AssertionError(f"unknown bridge state: {state}")


def _ordinary_args(command: str, setup: Setup, *, session: str) -> list[str]:
    if command == "bind":
        return [command, str(setup.root), "--session", session]
    return [command, "--session", session]


@pytest.mark.parametrize("bridge_state", ["missing", "invalid", "conflict", "valid"])
@pytest.mark.parametrize("command", ["bind", "unbind"])
def test_invalid_session_preempts_bridge_state_text(
    setup: Setup,
    bridge_state: str,
    command: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """无效 session 先于 bridge 加载：任意状态报 binding.invalid_session（退出 2）。"""
    _prepare_bridge_state(setup, bridge_state)
    code, out, err = call_main(
        _ordinary_args(command, setup, session=_INVALID_SESSION), setup, capsys
    )
    assert code == EXIT_USAGE
    assert out == ""
    assert "binding.invalid_session" in err
    # 双故障优先级：不应出现 bridge 状态错误
    assert "plugin.context_" not in err
    assert "Traceback" not in err
    # 不创建 bindings/ 或 binding 文件
    assert not (setup.data_dir / "bindings").exists()


@pytest.mark.parametrize("bridge_state", ["missing", "invalid", "conflict", "valid"])
@pytest.mark.parametrize("command", ["bind", "unbind"])
def test_invalid_session_preempts_bridge_state_json(
    setup: Setup,
    bridge_state: str,
    command: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--json 下无效 session 同样先于 bridge 状态：标准 error_payload，退出 2。"""
    _prepare_bridge_state(setup, bridge_state)
    args = ["--json", *_ordinary_args(command, setup, session=_INVALID_SESSION)]
    code, out, err = call_main(args, setup, capsys)
    assert code == EXIT_USAGE
    assert err == ""
    payload = json.loads(out)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "binding.invalid_session"
    assert not (setup.data_dir / "bindings").exists()


def test_invalid_session_does_not_load_bridge(
    setup: Setup, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """无效 session 时 load_context 不得被调用（先于 bridge 加载）。"""
    called = {"count": 0}
    real_load_context = plugin_cli.load_context

    def spy(*args: object, **kwargs: object) -> object:
        called["count"] += 1
        return real_load_context(*args, **kwargs)

    monkeypatch.setattr(plugin_cli, "load_context", spy)
    code, out, err = call_main(
        _ordinary_args("bind", setup, session=_INVALID_SESSION), setup, capsys
    )
    assert code == EXIT_USAGE
    assert "binding.invalid_session" in err
    assert called["count"] == 0


def test_empty_session_with_valid_bridge_binds_and_unbinds(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    """空字符串 session 合法：valid bridge 下 bind/unbind 正常往返，不被前置校验拒绝。"""
    code, out, err = call_main(_ordinary_args("bind", setup, session=""), setup, capsys)
    assert code == 0
    assert out == f"绑定成功 {setup.root}\n"
    assert err == ""
    assert binding_path(setup.data_dir, "").exists()
    code, out, err = call_main(_ordinary_args("unbind", setup, session=""), setup, capsys)
    assert code == 0
    assert out == "解除成功 removed=true\n"
    assert not binding_path(setup.data_dir, "").exists()
