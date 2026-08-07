"""SessionStart Hook 适配层测试：不导入核心、不调用子进程，仅依赖标准库与 adapter。"""

from __future__ import annotations

import io
import json
import os
import shlex
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, cast

import pytest

from adapter.bindings import Binding, bind, binding_path
from adapter.bridge import PluginContext, load_context
from adapter.errors import AdapterError
from adapter.session_hook import (
    HOST_ENV,
    MAX_STDIN,
    resolve_host_context,
    run_session_start,
)

HOSTS = ("codex", "claude")


# ---------------------------------------------------------------------------
# 固件
# ---------------------------------------------------------------------------


@dataclass
class Setup:
    entry: Path
    runtime_dir: Path
    data_dir: Path
    skill_path: Path
    plugin_root: Path
    base: Path


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Setup:
    """复制插件布局：<plugin>/scripts/draft.py、<plugin>/skills/draft/SKILL.md、<plugin-data>/。

    plugin_root 名字带空格，覆盖命令示例中的 shell 引用。
    """
    base = tmp_path.resolve()
    plugin_root = base / "plug in"
    entry = plugin_root / "scripts" / "draft.py"
    entry.parent.mkdir(parents=True)
    entry.write_text("# plugin entry\n", encoding="utf-8")
    skill_path = plugin_root / "skills" / "draft" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Draft skill\n", encoding="utf-8")
    data_dir = (base / "host-data").resolve()
    data_dir.mkdir()
    runtime_dir = (base / "xdg").resolve()
    runtime_dir.mkdir()
    runtime_dir.chmod(0o700)
    for root_var, data_var in HOST_ENV.values():
        monkeypatch.delenv(root_var, raising=False)
        monkeypatch.delenv(data_var, raising=False)
    return Setup(
        entry=entry,
        runtime_dir=runtime_dir,
        data_dir=data_dir,
        skill_path=skill_path,
        plugin_root=plugin_root,
        base=base,
    )


def default_env(setup: Setup, host: str) -> dict[str, str]:
    root_var, data_var = HOST_ENV[host]
    env: dict[str, str] = {
        root_var: str(setup.plugin_root),
        data_var: str(setup.data_dir),
    }
    # 另一宿主变量设为诱饵（不存在的路径），证明不被读取
    for other_host, (other_root, other_data) in HOST_ENV.items():
        if other_host != host:
            env[other_root] = str(setup.base / "decoy-root")
            env[other_data] = str(setup.base / "decoy-data")
    return env


def session_stdin(
    session: object = "abc123",
    source: str = "startup",
    *,
    event: str = "SessionStart",
) -> str:
    payload: dict[str, object] = {
        "session_id": session,
        "hook_event_name": event,
        "source": source,
        "transcript_path": "/not/read/transcript.jsonl",
        "cwd": "/workspace",
    }
    return json.dumps(payload, ensure_ascii=False)


def run_hook(
    setup: Setup,
    capsys: pytest.CaptureFixture[str],
    *,
    host: str = "codex",
    stdin: str | bytes | None = None,
    env: Mapping[str, str] | None = None,
    entry_path: Path | None = None,
) -> tuple[int, str]:
    """stdin 按契约 A 为二进制流：str 视为 UTF-8 编码前的文本，bytes 原样传入。"""
    if env is None:
        env = default_env(setup, host)
    if stdin is None:
        raw = session_stdin("abc123", "startup").encode("utf-8")
    elif isinstance(stdin, str):
        raw = stdin.encode("utf-8")
    else:
        raw = stdin
    code = run_session_start(
        host,
        entry_path=entry_path if entry_path is not None else setup.entry,
        environ=dict(env),
        stdin=io.BytesIO(raw),
        runtime_dir=setup.runtime_dir,
    )
    captured = capsys.readouterr()
    return code, captured.out


def bind_session(
    setup: Setup,
    *,
    session: str = "abc123",
    root: Path | None = None,
    hint: Path | None = None,
) -> Binding:
    if root is None:
        root = setup.base / "topic"
    root.mkdir(parents=True, exist_ok=True)
    return bind(setup.data_dir, Binding(session_id=session, root=root, last_page_hint=hint))


# ---------------------------------------------------------------------------
# Step 1: 宿主选择、布局与执行顺序
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("host", HOSTS)
def test_each_host_reads_only_its_own_variables(
    setup: Setup, host: str, capsys: pytest.CaptureFixture[str]
) -> None:
    code, output = run_hook(setup, capsys, host=host, stdin=session_stdin("new-session"))
    assert code == 0
    assert "Draft 会话未绑定" in output
    assert "new-session" in output
    # bridge 发布了当前宿主的 data_dir
    assert load_context(setup.entry, setup.runtime_dir) == PluginContext(setup.data_dir)


def test_unknown_host_does_not_probe_either_pair(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    # 两组宿主变量都设为合法值，未知宿主仍必须报 unknown_host
    env: dict[str, str] = {}
    for root_var, data_var in HOST_ENV.values():
        env[root_var] = str(setup.plugin_root)
        env[data_var] = str(setup.data_dir)
    code, output = run_hook(setup, capsys, host="bogus", env=env)
    assert code == 0
    assert "hook.unknown_host" in output
    assert output.count("Draft 插件诊断") == 1
    assert load_context(setup.entry, setup.runtime_dir) is None


@pytest.mark.parametrize("host", HOSTS)
def test_missing_root_variable_renders_environment_missing(
    setup: Setup, host: str, capsys: pytest.CaptureFixture[str]
) -> None:
    env = default_env(setup, host)
    root_var, _ = HOST_ENV[host]
    del env[root_var]
    code, output = run_hook(setup, capsys, host=host, env=env)
    assert code == 0
    assert "hook.environment_missing" in output
    assert output.count("Draft 插件诊断") == 1
    assert load_context(setup.entry, setup.runtime_dir) is None


@pytest.mark.parametrize("host", HOSTS)
def test_missing_data_variable_renders_environment_missing(
    setup: Setup, host: str, capsys: pytest.CaptureFixture[str]
) -> None:
    env = default_env(setup, host)
    _, data_var = HOST_ENV[host]
    del env[data_var]
    code, output = run_hook(setup, capsys, host=host, env=env)
    assert code == 0
    assert "hook.environment_missing" in output
    assert output.count("Draft 插件诊断") == 1
    assert load_context(setup.entry, setup.runtime_dir) is None


def test_relative_data_dir_renders_environment_missing(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    env = default_env(setup, "codex")
    _, data_var = HOST_ENV["codex"]
    env[data_var] = "relative/data"
    code, output = run_hook(setup, capsys, host="codex", env=env)
    assert code == 0
    assert "hook.environment_missing" in output
    assert load_context(setup.entry, setup.runtime_dir) is None


def test_non_canonical_absolute_data_dir_is_canonicalized(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    # 绝对但含 .. 的 data_dir 被词法规范化，不视为 environment_missing；
    # 中间段 <base>/a 无需存在，resolve(strict=False) 仅做词法规范化。
    raw_data = setup.base / "a" / ".." / "host-data"
    assert raw_data != setup.data_dir  # 原始形式确实非规范
    env = {**default_env(setup, "codex"), "PLUGIN_DATA": str(raw_data)}

    ctx = resolve_host_context("codex", setup.entry, env)
    assert ctx.data_dir == setup.data_dir
    assert ctx.data_dir == setup.data_dir.resolve()

    code, output = run_hook(
        setup, capsys, host="codex", env=env, stdin=session_stdin("x", "startup")
    )
    assert code == 0
    assert "Draft 会话未绑定" in output
    # bridge 发布到规范路径，而非原始 .. 形式
    assert load_context(setup.entry, setup.runtime_dir) == PluginContext(setup.data_dir)


@pytest.mark.parametrize("host", HOSTS)
def test_symlink_plugin_root_renders_plugin_root_mismatch(
    setup: Setup, host: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    real = tmp_path / "real-plugin"
    real.mkdir()
    (real / "scripts").mkdir()
    (real / "scripts" / "draft.py").write_text("#\n", encoding="utf-8")
    link = setup.base / "plugin-link"
    link.symlink_to(real)
    env = default_env(setup, host)
    root_var, _ = HOST_ENV[host]
    env[root_var] = str(link)
    # 入口仍指向真实插件，确保唯一错误来源是 plugin_root 是符号链接
    code, output = run_hook(setup, capsys, host=host, env=env, entry_path=setup.entry)
    assert code == 0
    assert "hook.plugin_root_mismatch" in output
    assert output.count("Draft 插件诊断") == 1
    assert load_context(setup.entry, setup.runtime_dir) is None


@pytest.mark.parametrize("host", HOSTS)
def test_entry_outside_plugin_scripts_renders_plugin_root_mismatch(
    setup: Setup, host: str, capsys: pytest.CaptureFixture[str]
) -> None:
    # plugin_root 合法，但传入的 entry 不在 plugin_root/scripts/draft.py
    other_entry = setup.base / "other-entry.py"
    other_entry.write_text("#\n", encoding="utf-8")
    code, output = run_hook(setup, capsys, host=host, entry_path=other_entry)
    assert code == 0
    assert "hook.plugin_root_mismatch" in output
    assert output.count("Draft 插件诊断") == 1
    assert load_context(other_entry, setup.runtime_dir) is None


@pytest.mark.parametrize("host", HOSTS)
def test_missing_skill_md_renders_layout_invalid(
    setup: Setup, host: str, capsys: pytest.CaptureFixture[str]
) -> None:
    setup.skill_path.unlink()
    code, output = run_hook(setup, capsys, host=host)
    assert code == 0
    assert "hook.layout_invalid" in output
    assert output.count("Draft 插件诊断") == 1
    assert load_context(setup.entry, setup.runtime_dir) is None


@pytest.mark.parametrize("host", HOSTS)
def test_non_regular_skill_md_renders_layout_invalid(
    setup: Setup, host: str, capsys: pytest.CaptureFixture[str]
) -> None:
    setup.skill_path.unlink()
    setup.skill_path.mkdir()
    code, output = run_hook(setup, capsys, host=host)
    assert code == 0
    assert "hook.layout_invalid" in output
    assert load_context(setup.entry, setup.runtime_dir) is None


@pytest.mark.parametrize("host", HOSTS)
def test_symlink_skill_md_renders_layout_invalid(
    setup: Setup, host: str, capsys: pytest.CaptureFixture[str]
) -> None:
    real = setup.base / "real-skill.md"
    real.write_text("#\n", encoding="utf-8")
    setup.skill_path.unlink()
    setup.skill_path.symlink_to(real)
    code, output = run_hook(setup, capsys, host=host)
    assert code == 0
    assert "hook.layout_invalid" in output
    assert load_context(setup.entry, setup.runtime_dir) is None


def test_bridge_is_published_before_payload_validation(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    code, output = run_hook(setup, capsys, host="codex", stdin="{bad json")
    assert code == 0
    assert "hook.stdin_invalid" in output
    # 布局成功 -> bridge 已发布；载荷错误发生在发布之后
    assert load_context(setup.entry, setup.runtime_dir) == PluginContext(setup.data_dir)


def test_layout_failure_occurs_before_bridge_publication(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    setup.skill_path.unlink()
    code, output = run_hook(setup, capsys, host="codex", stdin=session_stdin("abc123"))
    assert code == 0
    assert "hook.layout_invalid" in output
    assert load_context(setup.entry, setup.runtime_dir) is None


# ---------------------------------------------------------------------------
# Step 2: 载荷协议矩阵与不透明 session
# ---------------------------------------------------------------------------


def test_stdin_at_64kib_boundary_is_accepted(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    boundary = session_stdin("x" * 10, "startup")
    padding = " " * (MAX_STDIN - len(boundary))
    code, output = run_hook(setup, capsys, host="codex", stdin=boundary + padding)
    assert code == 0
    assert "Draft 会话未绑定" in output


def test_multibyte_stdin_at_64kib_boundary_is_accepted(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    # 字节边界按字节而非字符计算：字符数少而字节数大的载荷恰好 65,536 字节必须被接受
    fill = MAX_STDIN - len(session_stdin("").encode("utf-8"))
    session = "中" * (fill // 3) + "x" * (fill % 3)
    stdin = session_stdin(session).encode("utf-8")
    assert len(stdin) == MAX_STDIN
    code, output = run_hook(setup, capsys, host="codex", stdin=stdin)
    assert code == 0
    assert "Draft 会话未绑定" in output


def test_stdin_one_byte_over_renders_too_large(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    code, output = run_hook(setup, capsys, host="codex", stdin=" " * (MAX_STDIN + 1))
    assert code == 0
    assert "hook.stdin_too_large" in output
    assert output.count("Draft 插件诊断") == 1
    assert load_context(setup.entry, setup.runtime_dir) == PluginContext(setup.data_dir)


def test_multibyte_stdin_one_byte_over_renders_too_large(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    # 同形多字节载荷 65,537 字节：旧实现按字符计长会错误接受，必须报 too_large
    fill = MAX_STDIN + 1 - len(session_stdin("").encode("utf-8"))
    session = "中" * (fill // 3) + "x" * (fill % 3)
    stdin = session_stdin(session).encode("utf-8")
    assert len(stdin) == MAX_STDIN + 1
    code, output = run_hook(setup, capsys, host="codex", stdin=stdin)
    assert code == 0
    assert "hook.stdin_too_large" in output
    assert output.count("Draft 插件诊断") == 1


def test_oversized_and_invalid_utf8_prefers_too_large(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    # 大小检查先于任何解码：同时超限且无效 UTF-8 时报 too_large 而非 stdin_invalid
    code, output = run_hook(setup, capsys, host="codex", stdin=b"\xff" * (MAX_STDIN + 1))
    assert code == 0
    assert "hook.stdin_too_large" in output
    assert output.count("Draft 插件诊断") == 1


def test_stream_read_oserror_renders_stdin_unavailable(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    class _BadStream:
        def read(self, size: int | None = -1) -> bytes:
            raise OSError("stream broken")

    bad_stream: BinaryIO = cast("BinaryIO", _BadStream())
    code = run_session_start(
        "codex",
        entry_path=setup.entry,
        environ=default_env(setup, "codex"),
        stdin=bad_stream,
        runtime_dir=setup.runtime_dir,
    )
    captured = capsys.readouterr()
    assert code == 0
    assert "hook.stdin_unavailable" in captured.out
    assert captured.err == ""


def test_invalid_utf8_under_limit_renders_stdin_invalid(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    # 未超限的无效字节：严格 UTF-8 解码失败归一为 hook.stdin_invalid，不得逃逸 traceback
    raw = b'{"hook_event_name": "SessionStart", "session_id": "s\xff", "source": "startup"}'
    code = run_session_start(
        "codex",
        entry_path=setup.entry,
        environ=default_env(setup, "codex"),
        stdin=io.BytesIO(raw),
        runtime_dir=setup.runtime_dir,
    )
    captured = capsys.readouterr()
    assert code == 0
    assert "hook.stdin_invalid" in captured.out
    assert captured.out.count("Draft 插件诊断") == 1
    assert captured.err == ""


def test_hook_lone_surrogate_session_renders_stdin_invalid(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    # JSON 转义的孤立代理可解码为 str，但不得进入 binding 路径操作；
    # ensure_ascii=True 将其转义为 \ud800，字节流本身是合法 UTF-8
    payload = {"hook_event_name": "SessionStart", "session_id": "\ud800", "source": "startup"}
    stdin = json.dumps(payload).encode("utf-8")
    code, output = run_hook(setup, capsys, host="codex", stdin=stdin)
    assert code == 0
    assert "hook.stdin_invalid" in output
    assert output.count("Draft 插件诊断") == 1


def test_invalid_json_renders_stdin_invalid(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    code, output = run_hook(setup, capsys, host="codex", stdin="{not json")
    assert code == 0
    assert "hook.stdin_invalid" in output
    assert output.count("Draft 插件诊断") == 1


def test_non_object_json_renders_stdin_not_object(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    code, output = run_hook(setup, capsys, host="codex", stdin="[1, 2, 3]")
    assert code == 0
    assert "hook.stdin_not_object" in output
    assert output.count("Draft 插件诊断") == 1


def test_wrong_event_renders_unexpected_event(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    code, output = run_hook(
        setup, capsys, host="codex", stdin=session_stdin("abc123", "startup", event="PreCompact")
    )
    assert code == 0
    assert "hook.unexpected_event" in output
    assert output.count("Draft 插件诊断") == 1


@pytest.mark.parametrize("bad_session", [None, 123, [1, 2], {"id": "x"}])
def test_non_string_or_missing_session_renders_session_unavailable(
    setup: Setup, bad_session: object, capsys: pytest.CaptureFixture[str]
) -> None:
    payload: dict[str, object] = {
        "hook_event_name": "SessionStart",
        "source": "startup",
    }
    if bad_session is not _MISSING:
        payload["session_id"] = bad_session
    code, output = run_hook(
        setup, capsys, host="codex", stdin=json.dumps(payload, ensure_ascii=False)
    )
    assert code == 0
    assert "binding.session_unavailable" in output
    assert output.count("Draft 插件诊断") == 1


class _Missing:
    pass


_MISSING = _Missing()


@pytest.mark.parametrize("bad_source", [123, None, ["startup"]])
def test_non_string_source_renders_unexpected_source(
    setup: Setup, bad_source: object, capsys: pytest.CaptureFixture[str]
) -> None:
    payload: dict[str, object] = {
        "session_id": "abc123",
        "hook_event_name": "SessionStart",
        "source": bad_source,
    }
    code, output = run_hook(
        setup, capsys, host="codex", stdin=json.dumps(payload, ensure_ascii=False)
    )
    assert code == 0
    assert "hook.unexpected_source" in output
    assert output.count("Draft 插件诊断") == 1


def test_codex_rejects_fork_source(setup: Setup, capsys: pytest.CaptureFixture[str]) -> None:
    code, output = run_hook(setup, capsys, host="codex", stdin=session_stdin("abc123", "fork"))
    assert code == 0
    assert "hook.unexpected_source" in output


def test_claude_accepts_fork_source(setup: Setup, capsys: pytest.CaptureFixture[str]) -> None:
    code, output = run_hook(setup, capsys, host="claude", stdin=session_stdin("abc123", "fork"))
    assert code == 0
    assert "Draft 会话未绑定" in output


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("source", ["bogus", "STARTUP", ""])
def test_source_not_legal_for_host_renders_unexpected_source(
    setup: Setup, host: str, source: str, capsys: pytest.CaptureFixture[str]
) -> None:
    code, output = run_hook(setup, capsys, host=host, stdin=session_stdin("abc123", source))
    assert code == 0
    assert "hook.unexpected_source" in output


@pytest.mark.parametrize("session", ["", "abc123", "会话-☃-123", "a b\tc", "../x\\y"])
def test_every_string_is_an_opaque_session_identity(
    setup: Setup, session: str, capsys: pytest.CaptureFixture[str]
) -> None:
    code, output = run_hook(setup, capsys, host="codex", stdin=session_stdin(session, "startup"))
    assert code == 0
    assert json.dumps(session, ensure_ascii=False) in output
    assert "SESSION_ID" in output
    # session 原样作为 JSON 字符串独占一行，未被插入路径或 shell 片段
    assert f"session_id：{json.dumps(session, ensure_ascii=False)}" in output


# ---------------------------------------------------------------------------
# Step 3: 未绑定、恢复与损坏状态信封
# ---------------------------------------------------------------------------


def test_unbound_session_byte_identical_output(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    session = "new-session"
    code, output = run_hook(setup, capsys, host="codex", stdin=session_stdin(session, "startup"))
    assert code == 0
    expected = (
        "Draft 会话未绑定\n"
        f"- 宿主 session_id：{json.dumps(session, ensure_ascii=False)}\n"
        f"- 插件入口：{setup.entry}\n"
        f"- 绑定格式：uv run {shlex.quote(str(setup.entry))} bind ROOT --session SESSION_ID "
        "[--last-page-hint PAGE]\n"
        f"- 解除格式：uv run {shlex.quote(str(setup.entry))} unbind --session SESSION_ID\n"
    )
    assert output == expected


@pytest.mark.parametrize("host", HOSTS)
def test_bound_valid_root_without_hint(
    setup: Setup, host: str, capsys: pytest.CaptureFixture[str]
) -> None:
    bound = bind_session(setup)
    code, output = run_hook(setup, capsys, host=host, stdin=session_stdin("abc123", "resume"))
    assert code == 0
    assert "Draft 会话已绑定" in output
    assert f"- 草稿根：{bound.root}" in output
    assert "最后页面提示" not in output
    assert "- 宿主触发原因：resume" in output
    assert f"- 当前 skill：{setup.skill_path}" in output
    assert f"- 插件入口：{setup.entry}" in output
    assert "重新完整阅读当前 SKILL.md" in output
    skill_root = setup.skill_path.parent
    assert (
        f"uvx --isolated --from {shlex.quote(str(skill_root / 'scripts' / 'runtime'))} "
        f"draft check {shlex.quote(str(bound.root))}"
    ) in output


@pytest.mark.parametrize("host", HOSTS)
def test_bound_valid_root_with_hint(
    setup: Setup, host: str, capsys: pytest.CaptureFixture[str]
) -> None:
    root = setup.base / "topic"
    root.mkdir()
    (root / "pages").mkdir()
    (root / "pages" / "a.md").write_text("# A\n", encoding="utf-8")
    bound = bind(
        setup.data_dir,
        Binding(session_id="abc123", root=root, last_page_hint=Path("pages/a.md")),
    )
    code, output = run_hook(setup, capsys, host=host, stdin=session_stdin("abc123", "compact"))
    assert code == 0
    assert f"- 草稿根：{bound.root}" in output
    assert "- 最后页面提示：pages/a.md" in output


def test_hint_later_missing_omits_hint_retains_root(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    root = setup.base / "topic"
    root.mkdir()
    (root / "pages").mkdir()
    (root / "pages" / "a.md").write_text("# A\n", encoding="utf-8")
    bound = bind(
        setup.data_dir,
        Binding(session_id="abc123", root=root, last_page_hint=Path("pages/a.md")),
    )
    (bound.root / "pages" / "a.md").unlink()
    code, output = run_hook(setup, capsys, host="codex", stdin=session_stdin("abc123", "compact"))
    assert code == 0
    assert f"- 草稿根：{bound.root}" in output
    assert "最后页面提示" not in output


def test_hint_replaced_by_symlink_omits_hint(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    root = setup.base / "topic"
    root.mkdir()
    (root / "pages").mkdir()
    target = root / "pages" / "a.md"
    target.write_text("# A\n", encoding="utf-8")
    bound = bind(
        setup.data_dir,
        Binding(session_id="abc123", root=root, last_page_hint=Path("pages/a.md")),
    )
    target.unlink()
    real = root / "real.md"
    real.write_text("#\n", encoding="utf-8")
    target.symlink_to(real)
    code, output = run_hook(setup, capsys, host="codex", stdin=session_stdin("abc123", "compact"))
    assert code == 0
    assert f"- 草稿根：{bound.root}" in output
    assert "最后页面提示" not in output


def test_root_later_missing_renders_root_missing(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    bound = bind_session(setup)
    import shutil

    shutil.rmtree(bound.root)
    code, output = run_hook(setup, capsys, host="codex", stdin=session_stdin("abc123", "compact"))
    assert code == 0
    assert "binding.root_missing" in output
    assert output.count("Draft 插件诊断") == 1
    assert str(bound.root) not in output


def test_root_replaced_by_symlink_renders_root_missing(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    bound = bind_session(setup)
    bound.root.rmdir()
    real = setup.base / "real-topic"
    real.mkdir()
    bound.root.symlink_to(real)
    code, output = run_hook(setup, capsys, host="codex", stdin=session_stdin("abc123", "compact"))
    assert code == 0
    assert "binding.root_missing" in output
    assert output.count("Draft 插件诊断") == 1


def test_root_replaced_by_non_directory_renders_root_missing(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    bound = bind_session(setup)
    bound.root.rmdir()
    bound.root.write_text("x", encoding="utf-8")
    code, output = run_hook(setup, capsys, host="codex", stdin=session_stdin("abc123", "compact"))
    assert code == 0
    assert "binding.root_missing" in output


def test_malformed_binding_renders_invalid(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    bind_session(setup)
    target = binding_path(setup.data_dir, "abc123")
    target.write_text("{损坏", encoding="utf-8")
    code, output = run_hook(setup, capsys, host="codex", stdin=session_stdin("abc123", "compact"))
    assert code == 0
    assert "binding.invalid" in output
    assert output.count("Draft 插件诊断") == 1


def test_old_schema_binding_renders_invalid(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    root = setup.base / "topic"
    root.mkdir()
    target = binding_path(setup.data_dir, "abc123")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "session_id": "abc123",
                "root": str(root),
                "last_page_hint": None,
                "skill_name": "draft",
                "skill_digest": "old",
            }
        ),
        encoding="utf-8",
    )
    code, output = run_hook(setup, capsys, host="codex", stdin=session_stdin("abc123", "compact"))
    assert code == 0
    assert "binding.invalid" in output
    assert output.count("Draft 插件诊断") == 1


def test_payload_session_mismatch_renders_session_mismatch(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    root = setup.base / "topic"
    root.mkdir()
    target = binding_path(setup.data_dir, "abc123")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "session_id": "other-session",
                "root": str(root),
                "last_page_hint": None,
            }
        ),
        encoding="utf-8",
    )
    code, output = run_hook(setup, capsys, host="codex", stdin=session_stdin("abc123", "compact"))
    assert code == 0
    assert "binding.session_mismatch" in output
    assert output.count("Draft 插件诊断") == 1


def test_repeated_invocation_with_identical_state_is_byte_identical(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    root = setup.base / "topic"
    root.mkdir()
    (root / "pages").mkdir()
    (root / "pages" / "a.md").write_text("# A\n", encoding="utf-8")
    bind(
        setup.data_dir,
        Binding(session_id="abc123", root=root, last_page_hint=Path("pages/a.md")),
    )
    stdin_text = session_stdin("abc123", "compact")
    _, first = run_hook(setup, capsys, host="codex", stdin=stdin_text)
    _, second = run_hook(setup, capsys, host="codex", stdin=stdin_text)
    assert first == second

    _, third = run_hook(setup, capsys, host="codex", stdin=session_stdin("unbound", "startup"))
    _, fourth = run_hook(setup, capsys, host="codex", stdin=session_stdin("unbound", "startup"))
    assert third == fourth


def test_skill_contents_changed_binding_remains_valid(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    bound = bind_session(setup)
    setup.skill_path.write_text("# 全新 skill 内容\n", encoding="utf-8")
    code, output = run_hook(setup, capsys, host="codex", stdin=session_stdin("abc123", "resume"))
    assert code == 0
    assert "Draft 会话已绑定" in output
    assert f"- 草稿根：{bound.root}" in output
    assert f"- 当前 skill：{setup.skill_path}" in output
    # skill 内容变化不使 binding 失效，不出现摘要/digest 分支
    assert "摘要" not in output
    assert "digest" not in output
    assert "skill_mismatch" not in output


def test_paths_with_spaces_and_single_quotes_are_shell_quoted(
    setup: Setup, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # plugin_root 已带空格；bound root 带单引号
    root = (tmp_path / "top'ic").resolve()
    root.mkdir()
    bind(setup.data_dir, Binding(session_id="abc123", root=root, last_page_hint=None))
    code, output = run_hook(setup, capsys, host="codex", stdin=session_stdin("abc123", "resume"))
    assert code == 0
    # 核心命令中的路径经 shlex.quote 处理
    skill_root = setup.skill_path.parent
    assert (
        f"uvx --isolated --from {shlex.quote(str(skill_root / 'scripts' / 'runtime'))} "
        f"draft check {shlex.quote(str(root))}"
    ) in output


def test_unbound_command_examples_quote_plugin_entry(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    code, output = run_hook(setup, capsys, host="codex", stdin=session_stdin("x", "startup"))
    assert code == 0
    quoted = shlex.quote(str(setup.entry))
    assert f"uv run {quoted} bind ROOT --session SESSION_ID" in output
    assert f"uv run {quoted} unbind --session SESSION_ID" in output


# ---------------------------------------------------------------------------
# Step 4: Hook 失败归一化与日志降级
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "case,expected_code,setup_binding",
    [
        ("unknown_host", "hook.unknown_host", False),
        ("environment_missing", "hook.environment_missing", False),
        ("plugin_root_mismatch", "hook.plugin_root_mismatch", False),
        ("layout_invalid", "hook.layout_invalid", False),
        ("stdin_invalid", "hook.stdin_invalid", False),
        ("bridge_publication", "plugin.context_invalid", False),
        ("binding_invalid", "binding.invalid", True),
        ("session_mismatch", "binding.session_mismatch", True),
        ("root_missing", "binding.root_missing", True),
    ],
)
def test_failure_normalization(
    setup: Setup,
    case: str,
    expected_code: str,
    setup_binding: bool,
    capsys: pytest.CaptureFixture[str],
) -> None:
    env = default_env(setup, "codex")
    stdin = session_stdin("abc123", "compact")
    root: Path | None = None
    host = "bogus" if case == "unknown_host" else "codex"

    if case == "environment_missing":
        del env["PLUGIN_DATA"]
    elif case == "plugin_root_mismatch":
        env["PLUGIN_ROOT"] = str(setup.base / "decoy-root")
    elif case == "layout_invalid":
        setup.skill_path.unlink()
    elif case == "stdin_invalid":
        stdin = "{bad"
    elif case == "bridge_publication":
        # 绝对但无效的 data_dir：bridge 发布失败归一为 plugin.context_invalid
        env["PLUGIN_DATA"] = str(setup.base / "nonexistent-data-dir")
    elif case == "binding_invalid":
        root = setup.base / "topic"
        root.mkdir()
        target = binding_path(setup.data_dir, "abc123")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{broken", encoding="utf-8")
    elif case == "session_mismatch":
        root = setup.base / "topic"
        root.mkdir()
        target = binding_path(setup.data_dir, "abc123")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps({"session_id": "other", "root": str(root), "last_page_hint": None}),
            encoding="utf-8",
        )
    elif case == "root_missing":
        root = setup.base / "topic"
        root.mkdir()
        bind(setup.data_dir, Binding("abc123", root, None))
        import shutil

        shutil.rmtree(root)

    code, output = run_hook(setup, capsys, host=host, env=env, stdin=stdin)
    assert code == 0
    assert output.count("Draft 插件诊断") == 1
    assert expected_code in output


def test_hook_diagnostic_shows_cleanup_codes_alongside_primary(
    setup: Setup, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """主错误附带清理失败明细时，Hook 诊断同时显示主码与白名单清理码。

    bridge 发布产生主错误并伴随 unlock/close 清理失败：返回 0，stdout 含主错误码与
    清理码（保序），stderr 为空，不出现 traceback、路径或原始系统错误。
    """
    import adapter.session_hook as hook_mod

    sentinel_path = Path("/sentinel/bridge.json")

    def failing_publish(*_args: object, **_kwargs: object) -> None:
        error = AdapterError("plugin.context_invalid", sentinel_path, exit_code=4)
        error.add_detail("cleanup.unlock_failed")
        error.add_detail("cleanup.close_failed")
        # 非白名单 detail 不得进入诊断
        error.add_detail("internal.not_whitelisted")
        raise error

    monkeypatch.setattr(hook_mod, "publish_context", failing_publish)
    code = run_session_start(
        "codex",
        entry_path=setup.entry,
        environ=dict(default_env(setup, "codex")),
        stdin=io.BytesIO(session_stdin("abc123", "startup").encode("utf-8")),
        runtime_dir=setup.runtime_dir,
    )
    captured = capsys.readouterr()
    assert code == 0
    assert captured.err == ""
    assert captured.out.count("Draft 插件诊断") == 1
    assert "plugin.context_invalid" in captured.out
    # 白名单清理码按执行顺序可见
    assert "cleanup.unlock_failed" in captured.out
    assert "cleanup.close_failed" in captured.out
    assert captured.out.index("cleanup.unlock_failed") < captured.out.index("cleanup.close_failed")
    # 非白名单 detail、路径、traceback 不得可见
    assert "internal.not_whitelisted" not in captured.out
    assert str(sentinel_path) not in captured.out
    assert "Traceback" not in captured.out


@pytest.mark.parametrize(
    "degrade",
    ["mkdir", "open", "write", "non_directory"],
)
def test_log_degradation_appends_log_unavailable_without_discarding_primary(
    setup: Setup,
    degrade: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # 主分支为未绑定 session fact（不含“Draft 插件诊断”）
    if degrade == "mkdir":
        real_mkdir = Path.mkdir

        def failing_mkdir(
            self: Path, mode: int = 0o777, parents: bool = False, exist_ok: bool = False
        ) -> None:
            if self.name == "logs":
                raise OSError("mkdir failed")
            real_mkdir(self, mode=mode, parents=parents, exist_ok=exist_ok)

        monkeypatch.setattr(Path, "mkdir", failing_mkdir)
    elif degrade == "non_directory":
        (setup.data_dir / "logs").write_text("x", encoding="utf-8")
    elif degrade == "open":
        logs = setup.data_dir / "logs"
        logs.mkdir()
        (logs / "hook.jsonl").mkdir()
    elif degrade == "write":

        class _BadHandle:
            def __enter__(self) -> _BadHandle:
                return self

            def __exit__(self, *_a: object) -> bool:
                return False

            def write(self, _data: str) -> int:
                raise OSError("write failed")

        real_open = Path.open

        def failing_open(self: Path, *args: object, **kwargs: object) -> object:
            if self.name == "hook.jsonl":
                return _BadHandle()
            return real_open(self, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(Path, "open", failing_open)

    code, output = run_hook(
        setup, capsys, host="codex", stdin=session_stdin("unbound-session", "startup")
    )
    assert code == 0
    # 主分支保留
    assert "Draft 会话未绑定" in output
    assert "unbound-session" in output
    # 追加一条日志诊断
    assert "hook.log_unavailable" in output
    assert output.count("Draft 插件诊断") == 1


def test_log_degradation_preserves_rebind_branch(
    setup: Setup, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bound = bind_session(setup)
    real_mkdir = Path.mkdir

    def failing_mkdir(
        self: Path, mode: int = 0o777, parents: bool = False, exist_ok: bool = False
    ) -> None:
        if self.name == "logs":
            raise OSError("mkdir failed")
        real_mkdir(self, mode=mode, parents=parents, exist_ok=exist_ok)

    monkeypatch.setattr(Path, "mkdir", failing_mkdir)
    code, output = run_hook(setup, capsys, host="codex", stdin=session_stdin("abc123", "resume"))
    assert code == 0
    assert "Draft 会话已绑定" in output
    assert f"- 草稿根：{bound.root}" in output
    assert "hook.log_unavailable" in output
    assert output.count("Draft 插件诊断") == 1


def test_log_degradation_preserves_diagnostic_primary(
    setup: Setup, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import shutil

    bound = bind_session(setup)
    shutil.rmtree(bound.root)  # 主分支为 binding.root_missing 诊断
    real_mkdir = Path.mkdir

    def failing_mkdir(
        self: Path, mode: int = 0o777, parents: bool = False, exist_ok: bool = False
    ) -> None:
        if self.name == "logs":
            raise OSError("mkdir failed")
        real_mkdir(self, mode=mode, parents=parents, exist_ok=exist_ok)

    monkeypatch.setattr(Path, "mkdir", failing_mkdir)
    code, output = run_hook(setup, capsys, host="codex", stdin=session_stdin("abc123", "compact"))
    assert code == 0
    # 主诊断与日志诊断同时保留
    assert "binding.root_missing" in output
    assert "hook.log_unavailable" in output
    assert output.count("Draft 插件诊断") == 2


# ---------------------------------------------------------------------------
# Step 5: 日志目标类型安全（lstat 拒绝符号链接/目录/FIFO 等异常类型，不打开）
# ---------------------------------------------------------------------------

LOG_TYPE_CASES = ("logs_symlink", "logs_file", "jsonl_symlink", "jsonl_fifo", "jsonl_dir")


def prepare_log_type_case(setup: Setup, case: str) -> Path | None:
    """布置异常日志目标状态；返回必须保持未被写入的哨兵目标（无则为 None）。"""
    logs = setup.data_dir / "logs"
    if case == "logs_symlink":
        outside = setup.base / "outside-logs"
        outside.mkdir()
        logs.symlink_to(outside)
        return outside
    if case == "logs_file":
        logs.write_text("", encoding="utf-8")
        return logs
    logs.mkdir()
    target = logs / "hook.jsonl"
    if case == "jsonl_symlink":
        outside_file = setup.base / "outside-hook.jsonl"
        outside_file.write_text("", encoding="utf-8")
        target.symlink_to(outside_file)
        return outside_file
    if case == "jsonl_fifo":
        os.mkfifo(target)
        return None
    # jsonl_dir
    target.mkdir()
    return None


def assert_sentinel_not_written(sentinel: Path | None) -> None:
    if sentinel is None:
        return
    status = sentinel.lstat()
    if stat.S_ISDIR(status.st_mode):
        assert list(sentinel.iterdir()) == []
    else:
        assert sentinel.read_text(encoding="utf-8") == ""


@pytest.mark.parametrize("case", LOG_TYPE_CASES)
def test_log_target_type_violation_renders_log_unavailable(
    setup: Setup, case: str, capsys: pytest.CaptureFixture[str]
) -> None:
    sentinel = prepare_log_type_case(setup, case)
    code, output = run_hook(
        setup, capsys, host="codex", stdin=session_stdin("unbound-session", "startup")
    )
    assert code == 0
    # 无 binding 主分支保留 + 追加日志诊断
    assert "Draft 会话未绑定" in output
    assert "unbound-session" in output
    assert "hook.log_unavailable" in output
    assert output.count("Draft 插件诊断") == 1
    # 异常目标不得被打开：数据目录外目标与哨兵均未被写入
    assert_sentinel_not_written(sentinel)
    if case == "jsonl_fifo":
        # 仍然存在的 FIFO 证明没有在 append 模式打开（否则会阻塞或写入）
        assert stat.S_ISFIFO((setup.data_dir / "logs" / "hook.jsonl").lstat().st_mode)
    elif case == "jsonl_dir":
        assert list((setup.data_dir / "logs" / "hook.jsonl").iterdir()) == []


def test_log_target_symlink_preserves_rebind_branch(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    # 有效 binding 分支：主输出保留，外部目标未被写入
    outside_file = setup.base / "outside-hook.jsonl"
    outside_file.write_text("", encoding="utf-8")
    logs = setup.data_dir / "logs"
    logs.mkdir()
    (logs / "hook.jsonl").symlink_to(outside_file)
    bound = bind_session(setup)
    code, output = run_hook(setup, capsys, host="codex", stdin=session_stdin("abc123", "resume"))
    assert code == 0
    assert "Draft 会话已绑定" in output
    assert f"- 草稿根：{bound.root}" in output
    assert "hook.log_unavailable" in output
    assert output.count("Draft 插件诊断") == 1
    assert outside_file.read_text(encoding="utf-8") == ""


def test_log_target_symlink_preserves_diagnostic_primary(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    # 已有主要诊断分支：主诊断与日志诊断同时保留，外部目录未被写入
    import shutil

    outside = setup.base / "outside-logs"
    outside.mkdir()
    (setup.data_dir / "logs").symlink_to(outside)
    bound = bind_session(setup)
    shutil.rmtree(bound.root)
    code, output = run_hook(setup, capsys, host="codex", stdin=session_stdin("abc123", "compact"))
    assert code == 0
    assert "binding.root_missing" in output
    assert "hook.log_unavailable" in output
    assert output.count("Draft 插件诊断") == 2
    assert list(outside.iterdir()) == []


def test_log_is_recorded_on_success(setup: Setup, capsys: pytest.CaptureFixture[str]) -> None:
    run_hook(setup, capsys, host="codex", stdin=session_stdin("abc123", "startup"))
    log_path = setup.data_dir / "logs" / "hook.jsonl"
    assert log_path.exists()
    record = json.loads(log_path.read_text(encoding="utf-8").splitlines()[-1])
    assert record["host"] == "codex"
    assert record["branch"] == "session_fact"
    assert record["source"] == "startup"


def test_never_imports_subprocess_draft_or_core() -> None:
    import adapter.session_hook as mod

    source = Path(mod.__file__).read_text(encoding="utf-8")
    for forbidden in [
        "import subprocess",
        "from subprocess",
        "import draft",
        "from draft",
        "check_draft",
        "load_graph",
        ".draft/",
    ]:
        assert forbidden not in source, forbidden


def test_run_session_start_always_returns_zero_on_binding_load_error(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    bind_session(setup)
    target = binding_path(setup.data_dir, "abc123")
    target.write_text("{broken", encoding="utf-8")
    code, _ = run_hook(setup, capsys, host="codex", stdin=session_stdin("abc123", "compact"))
    assert code == 0
