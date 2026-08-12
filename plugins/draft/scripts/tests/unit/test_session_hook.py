"""SessionStart 钩子适配层单元测试：载荷、渲染纯函数与八步控制流。

read_hook_payload 与渲染函数直接覆盖纯函数矩阵（io.BytesIO 载荷）；
run_session_start 的八步控制流用受真实接口约束的替身在稳定协作者边界验证。
真实插件布局、宿主环境变量、日志目标与标准输入/输出字节边界保留集成测试。
"""

from __future__ import annotations

import io
import json
import shlex
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, cast
from unittest.mock import MagicMock, call

import pytest
from pytest_mock import MockerFixture

from adapter.bindings import Binding
from adapter.bridge import PluginContext
from adapter.errors import AdapterError
from adapter.session_hook import (
    MAX_STDIN,
    HookPayload,
    HostContext,
    read_hook_payload,
    render_diagnostic,
    render_rebind,
    render_session_fact,
    run_session_start,
)

# ---------------------------------------------------------------------------
# read_hook_payload：载荷读取与校验矩阵（io.BytesIO，不经过真实 stdin）
# ---------------------------------------------------------------------------


def _stdin(payload: object) -> BinaryIO:
    return io.BytesIO(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def _session_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "hook_event_name": "SessionStart",
        "session_id": "abc123",
        "source": "startup",
    }
    payload.update(overrides)
    return payload


def _sized_stdin(size: int) -> BinaryIO:
    """构造恰好 size 字节的有效 SessionStart 载荷（ASCII 填充，字节数随字符数线性增长）。"""
    base = json.dumps({"hook_event_name": "SessionStart", "session_id": "", "source": "startup"})
    fill = size - len(base.encode("utf-8"))
    raw = json.dumps(
        {"hook_event_name": "SessionStart", "session_id": "x" * fill, "source": "startup"}
    )
    encoded = raw.encode("utf-8")
    assert len(encoded) == size
    return io.BytesIO(encoded)


def test_read_hook_payload_accepts_valid_json() -> None:
    payload = read_hook_payload(_stdin(_session_payload()), "codex")
    assert payload == HookPayload(session_id="abc123", source="startup")


def test_read_hook_payload_rejects_invalid_utf8() -> None:
    # 未超限的无效字节：严格 UTF-8 解码失败归一为 hook.stdin_invalid
    raw = b'{"hook_event_name": "SessionStart", "session_id": "s\xff", "source": "startup"}'
    with pytest.raises(AdapterError, match="hook.stdin_invalid") as caught:
        read_hook_payload(io.BytesIO(raw), "codex")
    assert caught.value.exit_code == 4


def test_read_hook_payload_rejects_lone_surrogate_session() -> None:
    # JSON 转义的孤立代理可解码为 str，但不得进入后续协议或文件系统操作
    raw = json.dumps(_session_payload(session_id="\ud800")).encode("utf-8")
    with pytest.raises(AdapterError, match="hook.stdin_invalid"):
        read_hook_payload(io.BytesIO(raw), "codex")


@pytest.mark.parametrize(
    ("raw_object", "expected_code"),
    [
        ([1, 2, 3], "hook.stdin_not_object"),
        ("plain string", "hook.stdin_not_object"),
        (
            {"hook_event_name": "PreCompact", "session_id": "s", "source": "startup"},
            "hook.unexpected_event",
        ),
        ({"session_id": "s", "source": "startup"}, "hook.unexpected_event"),
        (
            {"hook_event_name": "SessionStart", "session_id": 123, "source": "startup"},
            "binding.session_unavailable",
        ),
        (
            {"hook_event_name": "SessionStart", "source": "startup"},
            "binding.session_unavailable",
        ),
        (
            {"hook_event_name": "SessionStart", "session_id": "s", "source": 123},
            "hook.unexpected_source",
        ),
        (
            {"hook_event_name": "SessionStart", "session_id": "s"},
            "hook.unexpected_source",
        ),
    ],
)
def test_read_hook_payload_type_and_field_errors(raw_object: object, expected_code: str) -> None:
    with pytest.raises(AdapterError, match=expected_code.replace(".", r"\.")) as caught:
        read_hook_payload(_stdin(raw_object), "codex")
    assert caught.value.exit_code == 4


def test_read_hook_payload_accepts_exactly_max_stdin_bytes() -> None:
    payload = read_hook_payload(_sized_stdin(MAX_STDIN), "codex")
    assert payload.source == "startup"


def test_read_hook_payload_rejects_one_byte_over_max_stdin() -> None:
    with pytest.raises(AdapterError, match="hook.stdin_too_large") as caught:
        read_hook_payload(_sized_stdin(MAX_STDIN + 1), "codex")
    assert caught.value.exit_code == 4


def test_read_hook_payload_size_check_precedes_decoding() -> None:
    # 同时超限且无效 UTF-8：大小检查先于任何解码，报 too_large 而非 stdin_invalid
    with pytest.raises(AdapterError, match="hook.stdin_too_large"):
        read_hook_payload(io.BytesIO(b"\xff" * (MAX_STDIN + 1)), "codex")


class _BrokenStream:
    def read(self, size: int | None = -1) -> bytes:
        raise OSError("stream broken")


def test_read_hook_payload_stream_oserror_renders_stdin_unavailable() -> None:
    broken = cast("BinaryIO", _BrokenStream())
    with pytest.raises(AdapterError, match="hook.stdin_unavailable"):
        read_hook_payload(broken, "codex")


def test_read_hook_payload_codex_rejects_fork_source() -> None:
    with pytest.raises(AdapterError, match="hook.unexpected_source"):
        read_hook_payload(_stdin(_session_payload(source="fork")), "codex")


def test_read_hook_payload_claude_accepts_fork_source() -> None:
    payload = read_hook_payload(_stdin(_session_payload(source="fork")), "claude")
    assert payload == HookPayload(session_id="abc123", source="fork")


@pytest.mark.parametrize("host", ["codex", "claude"])
@pytest.mark.parametrize("source", ["bogus", "STARTUP", ""])
def test_read_hook_payload_rejects_source_not_legal_for_host(host: str, source: str) -> None:
    with pytest.raises(AdapterError, match="hook.unexpected_source"):
        read_hook_payload(_stdin(_session_payload(source=source)), host)


def test_read_hook_payload_unknown_host_rejects_every_source() -> None:
    with pytest.raises(AdapterError, match="hook.unexpected_source"):
        read_hook_payload(_stdin(_session_payload()), "bogus")


@pytest.mark.parametrize("session", ["", "会话-☃-123", "a b\tc", "../x\\y"])
def test_read_hook_payload_preserves_session_verbatim(session: str) -> None:
    # session_id 原样保留：不截断、不解释、不做 Unicode 规范化
    payload = read_hook_payload(_stdin(_session_payload(session_id=session)), "codex")
    assert payload.session_id == session


# ---------------------------------------------------------------------------
# 渲染纯函数：会话事实、恢复与诊断信封
# ---------------------------------------------------------------------------


def test_render_session_fact_envelope_is_byte_exact() -> None:
    entry = Path("/plug in/scripts/draft.py")
    text = render_session_fact("new-session", entry)
    quoted = shlex.quote(str(entry))
    assert text == "\n".join(
        [
            "Draft 会话未绑定",
            f"- 宿主 session_id：{json.dumps('new-session', ensure_ascii=False)}",
            f"- 插件入口：{entry}",
            f"- 绑定格式：uv run {quoted} bind ROOT --session SESSION_ID [--last-page-hint PAGE]",
            f"- 解除格式：uv run {quoted} unbind --session SESSION_ID",
        ]
    )


def test_render_session_fact_keeps_unicode_session_unescaped() -> None:
    text = render_session_fact("会话-1", Path("/plugin/scripts/draft.py"))
    assert '- 宿主 session_id："会话-1"' in text


def test_render_rebind_without_hint_is_byte_exact() -> None:
    binding = Binding("s", Path("/draft/root"), None)
    skill_path = Path("/plug in/skills/draft/SKILL.md")
    entry = Path("/plug in/scripts/draft.py")
    text = render_rebind(binding, "resume", None, skill_path, entry)
    runtime = skill_path.parent / "scripts" / "runtime"
    assert text == "\n".join(
        [
            "Draft 会话已绑定",
            "- 草稿根：/draft/root",
            "- 宿主触发原因：resume",
            f"- 当前 skill：{skill_path}",
            f"- 插件入口：{entry}",
            "- 继续受管理讨论前，重新完整阅读当前 SKILL.md。",
            "- 修改前运行核心检查：uvx --isolated --from "
            f"{shlex.quote(str(runtime))} draft check --root {shlex.quote(str(binding.root))}",
        ]
    )


def test_render_rebind_with_hint_inserts_hint_line_after_root() -> None:
    binding = Binding("s", Path("/draft/root"), Path("pages/a.md"))
    skill_path = Path("/plugin/skills/draft/SKILL.md")
    entry = Path("/plugin/scripts/draft.py")
    text = render_rebind(binding, "compact", Path("pages/a.md"), skill_path, entry)
    lines = text.splitlines()
    assert lines[0] == "Draft 会话已绑定"
    assert lines[1] == "- 草稿根：/draft/root"
    assert lines[2] == "- 最后页面提示：pages/a.md"
    assert lines[3] == "- 宿主触发原因：compact"


def test_render_diagnostic_contains_exactly_one_header_and_omits_paths() -> None:
    error = AdapterError("plugin.context_invalid", Path("/secret/data"), exit_code=4)
    text = render_diagnostic(error)
    assert text.count("Draft 插件诊断") == 1
    assert "- 错误码：plugin.context_invalid" in text
    assert text.endswith("- 请检查插件安装或重新执行 bind。")
    # 路径与底层错误文本不得进入模型可见诊断
    assert "/secret/data" not in text
    assert "次要诊断码" not in text


def test_render_diagnostic_shows_only_whitelisted_cleanup_details_in_order() -> None:
    error = AdapterError(
        "plugin.context_invalid",
        details=(
            "cleanup.unlock_failed",
            "internal.not_whitelisted",
            "cleanup.close_failed",
            "/leaked/path",
            "cleanup.unlink_failed",
        ),
        exit_code=4,
    )
    text = render_diagnostic(error)
    assert text == "\n".join(
        [
            "Draft 插件诊断",
            "- 错误码：plugin.context_invalid",
            "- 次要诊断码：cleanup.unlock_failed",
            "- 次要诊断码：cleanup.close_failed",
            "- 次要诊断码：cleanup.unlink_failed",
            "- 请检查插件安装或重新执行 bind。",
        ]
    )


# ---------------------------------------------------------------------------
# run_session_start 八步控制流：受真实接口约束的编排替身
# ---------------------------------------------------------------------------

_CTX = HostContext(
    host="codex",
    plugin_root=Path("/plugin"),
    data_dir=Path("/data"),
    entry_path=Path("/plugin/scripts/draft.py"),
    skill_path=Path("/plugin/skills/draft/SKILL.md"),
)
_BOUND = Binding("abc123", Path("/draft/root"), Path("pages/a.md"))


@dataclass
class _HookDoubles:
    resolve_host_context: MagicMock
    publish_context: MagicMock
    read_hook_payload: MagicMock
    load_binding: MagicMock
    root_is_current: MagicMock
    hint_is_current: MagicMock
    render_session_fact: MagicMock
    render_rebind: MagicMock
    render_diagnostic: MagicMock
    log_hook: MagicMock
    write_stdout: MagicMock


@pytest.fixture
def hook_doubles(mocker: MockerFixture) -> Iterator[_HookDoubles]:
    """按真实签名替换八步控制流的协作者；只有 run_session_start 自身编排保持真实。"""
    resolve = mocker.patch("adapter.session_hook.resolve_host_context", autospec=True)
    resolve.return_value = _CTX
    publish = mocker.patch("adapter.session_hook.publish_context", autospec=True)
    publish.return_value = PluginContext(data_dir=_CTX.data_dir)
    read_payload = mocker.patch("adapter.session_hook.read_hook_payload", autospec=True)
    read_payload.return_value = HookPayload(session_id="abc123", source="startup")
    load = mocker.patch("adapter.session_hook.load_binding", autospec=True)
    load.return_value = None
    root_current = mocker.patch("adapter.session_hook.binding_root_is_current", autospec=True)
    root_current.return_value = True
    hint_current = mocker.patch("adapter.session_hook.binding_hint_is_current", autospec=True)
    hint_current.return_value = True
    session_fact = mocker.patch("adapter.session_hook.render_session_fact", autospec=True)
    session_fact.return_value = "SESSION-FACT"
    rebind = mocker.patch("adapter.session_hook.render_rebind", autospec=True)
    rebind.return_value = "REBIND"
    diagnostic = mocker.patch("adapter.session_hook.render_diagnostic", autospec=True)
    diagnostic.return_value = "DIAGNOSTIC"
    log = mocker.patch("adapter.session_hook.log_hook", autospec=True)
    log.return_value = None
    write = mocker.patch("adapter.session_hook._write_stdout", autospec=True)
    yield _HookDoubles(
        resolve_host_context=resolve,
        publish_context=publish,
        read_hook_payload=read_payload,
        load_binding=load,
        root_is_current=root_current,
        hint_is_current=hint_current,
        render_session_fact=session_fact,
        render_rebind=rebind,
        render_diagnostic=diagnostic,
        log_hook=log,
        write_stdout=write,
    )


def _run_hook(*, host: str = "codex", runtime_dir: Path | None = None) -> int:
    """environ 与 stdin 由替身在协作者边界接管，实参只验证转发。"""
    return run_session_start(
        host,
        entry_path=Path("/host/entry.py"),
        environ={},
        stdin=io.BytesIO(b""),
        runtime_dir=runtime_dir,
    )


def test_unbound_session_renders_session_fact_and_returns_zero(
    hook_doubles: _HookDoubles,
) -> None:
    code = _run_hook()
    assert code == 0
    hook_doubles.load_binding.assert_called_once_with(_CTX.data_dir, "abc123")
    hook_doubles.render_session_fact.assert_called_once_with("abc123", _CTX.entry_path)
    hook_doubles.write_stdout.assert_called_once_with("SESSION-FACT")
    hook_doubles.log_hook.assert_called_once_with(
        _CTX.data_dir, "codex", source="startup", branch="session_fact"
    )
    hook_doubles.root_is_current.assert_not_called()
    hook_doubles.render_rebind.assert_not_called()
    hook_doubles.render_diagnostic.assert_not_called()


def test_context_validation_precedes_bridge_and_bridge_precedes_payload(
    hook_doubles: _HookDoubles,
) -> None:
    steps: list[str] = []

    def record_context(*_args: object, **_kwargs: object) -> HostContext:
        steps.append("context")
        return _CTX

    def record_publish(*_args: object, **_kwargs: object) -> PluginContext:
        steps.append("bridge")
        return PluginContext(data_dir=_CTX.data_dir)

    def record_payload(*_args: object, **_kwargs: object) -> HookPayload:
        steps.append("payload")
        return HookPayload(session_id="abc123", source="startup")

    hook_doubles.resolve_host_context.side_effect = record_context
    hook_doubles.publish_context.side_effect = record_publish
    hook_doubles.read_hook_payload.side_effect = record_payload

    assert _run_hook(runtime_dir=Path("/run")) == 0
    assert steps == ["context", "bridge", "payload"]
    hook_doubles.publish_context.assert_called_once_with(
        _CTX.entry_path, _CTX.data_dir, Path("/run")
    )


def test_bound_session_with_current_root_and_hint_renders_rebind(
    hook_doubles: _HookDoubles,
) -> None:
    hook_doubles.load_binding.return_value = _BOUND
    code = _run_hook()
    assert code == 0
    hook_doubles.root_is_current.assert_called_once_with(_BOUND)
    hook_doubles.hint_is_current.assert_called_once_with(_BOUND)
    hook_doubles.render_rebind.assert_called_once_with(
        _BOUND, "startup", _BOUND.last_page_hint, _CTX.skill_path, _CTX.entry_path
    )
    hook_doubles.write_stdout.assert_called_once_with("REBIND")
    hook_doubles.log_hook.assert_called_once_with(
        _CTX.data_dir, "codex", source="startup", branch="rebind"
    )
    hook_doubles.render_session_fact.assert_not_called()


def test_bound_session_without_hint_renders_rebind_without_hint(
    hook_doubles: _HookDoubles,
) -> None:
    binding = Binding("abc123", Path("/draft/root"), None)
    hook_doubles.load_binding.return_value = binding
    assert _run_hook() == 0
    hook_doubles.render_rebind.assert_called_once_with(
        binding, "startup", None, _CTX.skill_path, _CTX.entry_path
    )


def test_bound_session_with_stale_hint_renders_rebind_without_hint(
    hook_doubles: _HookDoubles,
) -> None:
    hook_doubles.load_binding.return_value = _BOUND
    hook_doubles.hint_is_current.return_value = False
    assert _run_hook() == 0
    hook_doubles.render_rebind.assert_called_once_with(
        _BOUND, "startup", None, _CTX.skill_path, _CTX.entry_path
    )


def test_bound_session_with_stale_root_renders_root_missing_diagnostic(
    hook_doubles: _HookDoubles,
) -> None:
    hook_doubles.load_binding.return_value = _BOUND
    hook_doubles.root_is_current.return_value = False
    rendered: list[object] = []

    def capture_diagnostic(error: AdapterError) -> str:
        rendered.append(error)
        return "DIAGNOSTIC"

    hook_doubles.render_diagnostic.side_effect = capture_diagnostic

    code = _run_hook()
    assert code == 0
    hook_doubles.hint_is_current.assert_not_called()
    hook_doubles.render_rebind.assert_not_called()
    assert len(rendered) == 1
    error = rendered[0]
    assert isinstance(error, AdapterError)
    assert error.code == "binding.root_missing"
    assert error.exit_code == 4
    hook_doubles.write_stdout.assert_called_once_with("DIAGNOSTIC")
    hook_doubles.log_hook.assert_called_once_with(
        _CTX.data_dir,
        "codex",
        source="startup",
        branch="diagnostic",
        error="binding.root_missing",
    )


def test_payload_error_after_bridge_publish_renders_diagnostic_and_returns_zero(
    hook_doubles: _HookDoubles,
) -> None:
    payload_error = AdapterError("hook.stdin_invalid", exit_code=4)
    hook_doubles.read_hook_payload.side_effect = payload_error
    code = _run_hook()
    assert code == 0
    # 载荷校验晚于 bridge 发布：载荷失败时发布必须已经发生
    hook_doubles.publish_context.assert_called_once_with(_CTX.entry_path, _CTX.data_dir, None)
    hook_doubles.load_binding.assert_not_called()
    hook_doubles.render_diagnostic.assert_called_once_with(payload_error)
    hook_doubles.write_stdout.assert_called_once_with("DIAGNOSTIC")
    hook_doubles.log_hook.assert_called_once_with(
        _CTX.data_dir, "codex", branch="diagnostic", error="hook.stdin_invalid"
    )


def test_context_failure_blocks_bridge_payload_and_binding(
    hook_doubles: _HookDoubles,
) -> None:
    context_error = AdapterError("hook.environment_missing", exit_code=4)
    hook_doubles.resolve_host_context.side_effect = context_error
    code = _run_hook()
    assert code == 0
    # 上下文校验早于 bridge：失败时发布、载荷与绑定读取都不得发生
    hook_doubles.publish_context.assert_not_called()
    hook_doubles.read_hook_payload.assert_not_called()
    hook_doubles.load_binding.assert_not_called()
    hook_doubles.render_diagnostic.assert_called_once_with(context_error)
    hook_doubles.write_stdout.assert_called_once_with("DIAGNOSTIC")
    hook_doubles.log_hook.assert_called_once_with(
        None, "codex", branch="diagnostic", error="hook.environment_missing"
    )


def test_primary_error_with_cleanup_details_is_passed_verbatim_to_render(
    hook_doubles: _HookDoubles,
) -> None:
    bridge_error = AdapterError(
        "plugin.context_invalid",
        Path("/data"),
        details=("cleanup.unlock_failed", "cleanup.close_failed", "internal.not_whitelisted"),
        exit_code=4,
    )
    hook_doubles.publish_context.side_effect = bridge_error
    code = _run_hook()
    assert code == 0
    # 主错误连同清理明细整体交给渲染函数；白名单过滤是渲染契约（见纯函数测试）
    hook_doubles.render_diagnostic.assert_called_once_with(bridge_error)
    hook_doubles.write_stdout.assert_called_once_with("DIAGNOSTIC")
    hook_doubles.read_hook_payload.assert_not_called()


def test_log_failure_appends_diagnostic_after_primary(hook_doubles: _HookDoubles) -> None:
    log_error = AdapterError("hook.log_unavailable", _CTX.data_dir, exit_code=4)
    hook_doubles.log_hook.return_value = log_error
    code = _run_hook()
    assert code == 0
    hook_doubles.render_diagnostic.assert_called_once_with(log_error)
    # 主信封先输出，日志诊断后追加；主分支不被丢弃
    assert hook_doubles.write_stdout.call_args_list == [
        call("SESSION-FACT"),
        call("DIAGNOSTIC"),
    ]


def test_output_failure_propagates_instead_of_returning_zero(
    hook_doubles: _HookDoubles,
) -> None:
    # 日志失败降级为追加诊断，输出失败不得静默吞掉：向调用方传播
    hook_doubles.write_stdout.side_effect = OSError("stdout closed")
    with pytest.raises(OSError, match="stdout closed"):
        _run_hook()
