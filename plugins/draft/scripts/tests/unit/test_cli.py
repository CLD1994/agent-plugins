"""插件 CLI 入口单元测试：--json 归一化、hook/reset/bind/unbind 路由与 0/2/4 退出语义。

不启动子进程、不访问真实文件系统：入口模块经 load_plugin_entry() 逐测试全新加载，
bridge/bindings/hook 协作者以受真实接口约束的 autospec 替身替换，替身打在加载后
模块自身的命名空间上（入口在导入时已把协作者解析为模块级名称）；输出选择
（人类可读/JSON）经 capsys 验证。真实 PEP 723 入口、子进程与真实 bridge/binding
持久化行为保留集成测试。
"""

from __future__ import annotations

import argparse
import io
import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import BinaryIO, Protocol, cast
from unittest.mock import MagicMock

import pytest
from pytest_mock import MockerFixture

from adapter.bindings import Binding
from adapter.bridge import PluginContext
from adapter.errors import EXIT_RUNTIME, EXIT_USAGE, AdapterError
from tests.support.entry import load_plugin_entry

# 词法路径值：单元测试不访问真实文件系统，Path 仅作为不可变值参与断言。
_ENTRY_PATH = Path("/plugin/scripts/draft.py")
_RUNTIME_DIR = Path("/plugin/runtime")
_DATA_DIR = Path("/plugin-data")
_ROOT = Path("/draft-root")


class _EntryModule(Protocol):
    """load_plugin_entry() 加载的入口模块的被测接口。"""

    def dispatch_plugin_command(
        self,
        arguments: Sequence[str],
        *,
        entry_path: Path,
        environ: Mapping[str, str],
        stdin: BinaryIO,
        runtime_dir: Path | None,
    ) -> int: ...

    def _extract_json_for_ordinary(self, args: list[str]) -> tuple[bool, list[str]]: ...

    def _dispatch_ordinary(
        self,
        args: list[str],
        *,
        entry_path: Path,
        json_mode: bool,
        runtime_dir: Path | None,
    ) -> int: ...


def _cli(entry: ModuleType) -> _EntryModule:
    return cast(_EntryModule, entry)


def _run_cli(
    entry: ModuleType,
    argv: Sequence[str],
    capsys: pytest.CaptureFixture[str],
    *,
    environ: Mapping[str, str] | None = None,
    stdin: BinaryIO | None = None,
    runtime_dir: Path | None = None,
) -> tuple[int, str, str]:
    """进程内调用 dispatch_plugin_command，返回 (exit_code, stdout, stderr)。"""
    code = _cli(entry).dispatch_plugin_command(
        list(argv),
        entry_path=_ENTRY_PATH,
        environ={} if environ is None else environ,
        stdin=io.BytesIO(b"") if stdin is None else stdin,
        runtime_dir=runtime_dir,
    )
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@dataclass
class _EntryDoubles:
    module: ModuleType
    run_session_start: MagicMock
    reset_context: MagicMock
    load_context: MagicMock
    bind: MagicMock
    unbind: MagicMock


@pytest.fixture
def entry_doubles(mocker: MockerFixture) -> Iterator[_EntryDoubles]:
    """全新加载入口模块，并把 bridge/bindings/hook 协作者替换为 autospec 替身。

    session_id_is_valid、error_payload、render_error、render_diagnostic 是纯函数，
    保留真实实现。
    """
    entry = load_plugin_entry()
    run_hook = mocker.patch.object(entry, "run_session_start", autospec=True)
    reset_context = mocker.patch.object(entry, "reset_context", autospec=True)
    load_context = mocker.patch.object(entry, "load_context", autospec=True)
    bind_double = mocker.patch.object(entry, "bind", autospec=True)
    unbind_double = mocker.patch.object(entry, "unbind", autospec=True)
    yield _EntryDoubles(
        module=entry,
        run_session_start=run_hook,
        reset_context=reset_context,
        load_context=load_context,
        bind=bind_double,
        unbind=unbind_double,
    )


# ---------------------------------------------------------------------------
# --json 归一化（仅 bind/unbind）：纯函数直接验证，不触达任何协作者
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("argv", "expected_json", "expected_cleaned"),
    [
        # --json 在子命令紧前
        (["--json", "bind", "/r", "--session", "s"], True, ["bind", "/r", "--session", "s"]),
        (["--json", "unbind", "--session", "s"], True, ["unbind", "--session", "s"]),
        # --json 在子命令紧后
        (["bind", "--json", "/r", "--session", "s"], True, ["bind", "/r", "--session", "s"]),
        (["unbind", "--json", "--session", "s"], True, ["unbind", "--session", "s"]),
        # --json 在 argv 末尾
        (["bind", "/r", "--session", "s", "--json"], True, ["bind", "/r", "--session", "s"]),
        (["unbind", "--session", "s", "--json"], True, ["unbind", "--session", "s"]),
        # 仅有子命令与标志
        (["bind", "--json"], True, ["bind"]),
        # 三处等价位置重复出现：全部移除
        (
            ["--json", "bind", "--json", "/r", "--session", "s", "--json"],
            True,
            ["bind", "/r", "--session", "s"],
        ),
        # 非法位置：不提取，原样交由 argparse 拒绝
        (
            ["bind", "/r", "--session", "--json", "s"],
            False,
            ["bind", "/r", "--session", "--json", "s"],
        ),
        # 非 bind/unbind 子命令不归一化
        (
            ["--json", "plugin-context", "reset"],
            False,
            ["--json", "plugin-context", "reset"],
        ),
        # 空 argv
        ([], False, []),
    ],
)
def test_extract_json_for_ordinary_normalizes_equivalent_positions(
    argv: list[str], expected_json: bool, expected_cleaned: list[str]
) -> None:
    entry = _cli(load_plugin_entry())
    json_mode, cleaned = entry._extract_json_for_ordinary(argv)  # pyright: ignore[reportPrivateUsage]  # 直接验证 --json 归一化的独立行为契约
    assert json_mode is expected_json
    assert cleaned == expected_cleaned


# ---------------------------------------------------------------------------
# hook session-start 路由
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("host", ["codex", "claude"])
def test_hook_session_start_forwards_context_to_hook_layer_once(
    entry_doubles: _EntryDoubles, capsys: pytest.CaptureFixture[str], host: str
) -> None:
    doubles = entry_doubles
    doubles.run_session_start.return_value = 0
    stdin = io.BytesIO(b'{"hook_event_name": "SessionStart"}')
    environ = {"PLUGIN_ROOT": "/x"}

    code, out, err = _run_cli(
        doubles.module,
        ["hook", "session-start", host],
        capsys,
        environ=environ,
        stdin=stdin,
        runtime_dir=_RUNTIME_DIR,
    )

    assert code == 0
    assert out == ""
    assert err == ""
    doubles.run_session_start.assert_called_once_with(
        host,
        entry_path=_ENTRY_PATH,
        environ=environ,
        stdin=stdin,
        runtime_dir=_RUNTIME_DIR,
    )
    doubles.load_context.assert_not_called()
    doubles.reset_context.assert_not_called()


@pytest.mark.parametrize("rest", [[], ["codex", "claude"], ["--json", "codex"]])
def test_hook_session_start_bad_host_count_returns_zero_without_hook_layer(
    entry_doubles: _EntryDoubles, capsys: pytest.CaptureFixture[str], rest: list[str]
) -> None:
    doubles = entry_doubles

    code, out, err = _run_cli(doubles.module, ["hook", "session-start", *rest], capsys)

    assert code == 0
    assert "hook.invalid_arguments" in out
    assert err == ""
    doubles.run_session_start.assert_not_called()


# ---------------------------------------------------------------------------
# plugin-context reset 路由：先于任何 bridge 加载
# ---------------------------------------------------------------------------


def test_reset_routes_to_reset_context_without_bridge_load(
    entry_doubles: _EntryDoubles, capsys: pytest.CaptureFixture[str]
) -> None:
    doubles = entry_doubles

    code, out, err = _run_cli(
        doubles.module, ["plugin-context", "reset"], capsys, runtime_dir=_RUNTIME_DIR
    )

    assert code == 0
    assert out == ""
    assert err == ""
    doubles.reset_context.assert_called_once_with(_ENTRY_PATH, _RUNTIME_DIR)
    doubles.load_context.assert_not_called()


@pytest.mark.parametrize("rest", [["extra"], ["--json"]])
def test_reset_rejects_extra_arguments_before_any_collaborator(
    entry_doubles: _EntryDoubles, capsys: pytest.CaptureFixture[str], rest: list[str]
) -> None:
    doubles = entry_doubles

    code, out, err = _run_cli(doubles.module, ["plugin-context", "reset", *rest], capsys)

    assert code == EXIT_USAGE
    assert out == ""
    assert "plugin.invalid_arguments" in err
    doubles.reset_context.assert_not_called()
    doubles.load_context.assert_not_called()


def test_reset_maps_adapter_error_to_its_exit_code(
    entry_doubles: _EntryDoubles, capsys: pytest.CaptureFixture[str]
) -> None:
    doubles = entry_doubles
    doubles.reset_context.side_effect = AdapterError(
        "plugin.context_conflict", exit_code=EXIT_RUNTIME
    )

    code, out, err = _run_cli(doubles.module, ["plugin-context", "reset"], capsys)

    assert code == EXIT_RUNTIME
    assert out == ""
    assert "plugin.context_conflict" in err


# ---------------------------------------------------------------------------
# bind/unbind 路由与输出选择
# ---------------------------------------------------------------------------


def test_bind_routes_to_bind_with_context_data_dir(
    entry_doubles: _EntryDoubles, capsys: pytest.CaptureFixture[str]
) -> None:
    doubles = entry_doubles
    doubles.load_context.return_value = PluginContext(data_dir=_DATA_DIR)
    doubles.bind.return_value = Binding(session_id="s1", root=_ROOT, last_page_hint=None)

    code, out, err = _run_cli(
        doubles.module, ["bind", str(_ROOT), "--session", "s1"], capsys, runtime_dir=_RUNTIME_DIR
    )

    assert code == 0
    assert out == f"绑定成功 {_ROOT}\n"
    assert err == ""
    # bridge 加载恰好一次；重复加载会重复读取外部持久状态
    doubles.load_context.assert_called_once_with(_ENTRY_PATH, _RUNTIME_DIR)
    doubles.bind.assert_called_once_with(
        _DATA_DIR, Binding(session_id="s1", root=_ROOT, last_page_hint=None)
    )
    doubles.unbind.assert_not_called()


def test_bind_forwards_last_page_hint(
    entry_doubles: _EntryDoubles, capsys: pytest.CaptureFixture[str]
) -> None:
    doubles = entry_doubles
    doubles.load_context.return_value = PluginContext(data_dir=_DATA_DIR)
    doubles.bind.return_value = Binding(session_id="s", root=_ROOT, last_page_hint=Path("page.md"))

    code, _, _ = _run_cli(
        doubles.module,
        ["bind", str(_ROOT), "--session", "s", "--last-page-hint", "page.md"],
        capsys,
    )

    assert code == 0
    doubles.bind.assert_called_once_with(
        _DATA_DIR, Binding(session_id="s", root=_ROOT, last_page_hint=Path("page.md"))
    )


def test_bind_json_output_selection(
    entry_doubles: _EntryDoubles, capsys: pytest.CaptureFixture[str]
) -> None:
    doubles = entry_doubles
    doubles.load_context.return_value = PluginContext(data_dir=_DATA_DIR)
    doubles.bind.return_value = Binding(session_id="s1", root=_ROOT, last_page_hint=None)

    code, out, err = _run_cli(
        doubles.module, ["--json", "bind", str(_ROOT), "--session", "s1"], capsys
    )

    assert code == 0
    assert err == ""
    assert json.loads(out) == {"ok": True, "result_type": "bind", "root": str(_ROOT)}


@pytest.mark.parametrize("removed", [True, False])
def test_unbind_routes_to_unbind_and_selects_text_output(
    entry_doubles: _EntryDoubles, capsys: pytest.CaptureFixture[str], removed: bool
) -> None:
    doubles = entry_doubles
    doubles.load_context.return_value = PluginContext(data_dir=_DATA_DIR)
    doubles.unbind.return_value = removed

    code, out, err = _run_cli(doubles.module, ["unbind", "--session", "s1"], capsys)

    assert code == 0
    assert out == f"解除成功 removed={'true' if removed else 'false'}\n"
    assert err == ""
    doubles.load_context.assert_called_once_with(_ENTRY_PATH, None)
    doubles.unbind.assert_called_once_with(_DATA_DIR, "s1")
    doubles.bind.assert_not_called()


@pytest.mark.parametrize("removed", [True, False])
def test_unbind_json_output_selection(
    entry_doubles: _EntryDoubles, capsys: pytest.CaptureFixture[str], removed: bool
) -> None:
    doubles = entry_doubles
    doubles.load_context.return_value = PluginContext(data_dir=_DATA_DIR)
    doubles.unbind.return_value = removed

    code, out, err = _run_cli(doubles.module, ["unbind", "--session", "s1", "--json"], capsys)

    assert code == 0
    assert err == ""
    assert json.loads(out) == {"ok": True, "result_type": "unbind", "removed": removed}


# ---------------------------------------------------------------------------
# 上下文/持久化错误与退出码
# ---------------------------------------------------------------------------


def test_missing_context_maps_to_context_unavailable_exit_4(
    entry_doubles: _EntryDoubles, capsys: pytest.CaptureFixture[str]
) -> None:
    doubles = entry_doubles
    doubles.load_context.return_value = None

    code, out, err = _run_cli(doubles.module, ["bind", str(_ROOT), "--session", "s"], capsys)

    assert code == EXIT_RUNTIME
    assert out == ""
    assert "plugin.context_unavailable" in err
    doubles.bind.assert_not_called()
    doubles.unbind.assert_not_called()


def test_load_context_error_propagates_its_exit_code_text(
    entry_doubles: _EntryDoubles, capsys: pytest.CaptureFixture[str]
) -> None:
    doubles = entry_doubles
    doubles.load_context.side_effect = AdapterError(
        "plugin.context_invalid", exit_code=EXIT_RUNTIME
    )

    code, out, err = _run_cli(doubles.module, ["bind", str(_ROOT), "--session", "s"], capsys)

    assert code == EXIT_RUNTIME
    assert out == ""
    assert "plugin.context_invalid" in err
    doubles.bind.assert_not_called()


def test_load_context_error_json_output_selection(
    entry_doubles: _EntryDoubles, capsys: pytest.CaptureFixture[str]
) -> None:
    doubles = entry_doubles
    doubles.load_context.side_effect = AdapterError(
        "plugin.context_conflict", exit_code=EXIT_RUNTIME
    )

    code, out, err = _run_cli(doubles.module, ["--json", "unbind", "--session", "s"], capsys)

    assert code == EXIT_RUNTIME
    assert err == ""
    assert json.loads(out) == {
        "ok": False,
        "result_type": "error",
        "error": {
            "code": "plugin.context_conflict",
            "severity": "error",
            "paths": [],
            "details": [],
        },
    }


def test_bind_persistence_error_maps_to_usage_exit(
    entry_doubles: _EntryDoubles, capsys: pytest.CaptureFixture[str]
) -> None:
    doubles = entry_doubles
    doubles.load_context.return_value = PluginContext(data_dir=_DATA_DIR)
    doubles.bind.side_effect = AdapterError("binding.root_unavailable", _ROOT, exit_code=EXIT_USAGE)

    code, out, err = _run_cli(doubles.module, ["bind", str(_ROOT), "--session", "s"], capsys)

    assert code == EXIT_USAGE
    assert out == ""
    assert "binding.root_unavailable" in err


# ---------------------------------------------------------------------------
# 无效 session 早于 bridge 加载：不读取外部持久状态
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["bind", str(_ROOT), "--session", "\ud800"],
        ["unbind", "--session", "\ud800"],
    ],
)
def test_invalid_session_errors_before_bridge_load_text(
    entry_doubles: _EntryDoubles, capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    doubles = entry_doubles

    code, out, err = _run_cli(doubles.module, argv, capsys)

    assert code == EXIT_USAGE
    assert out == ""
    assert "binding.invalid_session" in err
    doubles.load_context.assert_not_called()
    doubles.bind.assert_not_called()
    doubles.unbind.assert_not_called()


@pytest.mark.parametrize(
    "argv",
    [
        ["--json", "bind", str(_ROOT), "--session", "\ud800"],
        ["--json", "unbind", "--session", "\ud800"],
    ],
)
def test_invalid_session_errors_before_bridge_load_json(
    entry_doubles: _EntryDoubles, capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    doubles = entry_doubles

    code, out, err = _run_cli(doubles.module, argv, capsys)

    assert code == EXIT_USAGE
    assert err == ""
    assert json.loads(out) == {
        "ok": False,
        "result_type": "error",
        "error": {
            "code": "binding.invalid_session",
            "severity": "error",
            "paths": [],
            "details": [],
        },
    }
    doubles.load_context.assert_not_called()
    doubles.bind.assert_not_called()
    doubles.unbind.assert_not_called()


# ---------------------------------------------------------------------------
# 参数错误：退出 2 且不加载任何外部协作者
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["bind", "--session", "s"],  # 缺 root
        ["bind", str(_ROOT)],  # 缺 --session
        ["unbind"],  # 缺 --session
        ["bogus"],  # 未知子命令
        [],  # 空 argv
        ["hook"],  # 非 hook session-start 形态
        ["hook", "foo"],
        ["hook", "session-startx", "codex"],
        ["hook", "--json"],  # --json 不归一化 hook
        ["--json", "plugin-context", "reset"],  # --json 不归一化 reset
    ],
)
def test_argument_errors_exit_2_without_loading_collaborators(
    entry_doubles: _EntryDoubles, capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    doubles = entry_doubles

    code, out, err = _run_cli(doubles.module, argv, capsys)

    assert code == EXIT_USAGE
    assert out == ""
    assert "plugin.invalid_arguments" in err
    doubles.load_context.assert_not_called()
    doubles.reset_context.assert_not_called()
    doubles.run_session_start.assert_not_called()


def test_help_exits_zero_without_loading_bridge(
    entry_doubles: _EntryDoubles, capsys: pytest.CaptureFixture[str]
) -> None:
    doubles = entry_doubles

    code, _, _ = _run_cli(doubles.module, ["--help"], capsys)

    assert code == 0
    doubles.load_context.assert_not_called()


@pytest.mark.parametrize("json_mode", [False, True])
def test_dispatch_ordinary_selects_error_stream_by_json_mode(
    capsys: pytest.CaptureFixture[str], json_mode: bool
) -> None:
    """json_mode 直接决定错误输出流向：人类可读走 stderr，JSON 走 stdout。"""
    entry = _cli(load_plugin_entry())

    code = entry._dispatch_ordinary(  # pyright: ignore[reportPrivateUsage]  # 直接验证 json_mode 驱动错误输出选择的独立契约
        ["bogus"], entry_path=_ENTRY_PATH, json_mode=json_mode, runtime_dir=None
    )

    captured = capsys.readouterr()
    assert code == EXIT_USAGE
    if json_mode:
        assert captured.err == ""
        payload = json.loads(captured.out)
        assert payload["error"]["code"] == "plugin.invalid_arguments"
    else:
        assert captured.out == ""
        assert "plugin.invalid_arguments" in captured.err


# ---------------------------------------------------------------------------
# 防御性未知路由：argparse 只允许 bind/unbind，该分支防御未来解析器变更
# ---------------------------------------------------------------------------


def test_unknown_command_after_parse_is_defensive_usage_error(
    mocker: MockerFixture,
    entry_doubles: _EntryDoubles,
    capsys: pytest.CaptureFixture[str],
) -> None:
    doubles = entry_doubles
    parser = mocker.create_autospec(argparse.ArgumentParser, instance=True, spec_set=True)
    parser.parse_args.return_value = argparse.Namespace(command="bogus")
    doubles.load_context.return_value = PluginContext(data_dir=_DATA_DIR)

    mocker.patch.object(
        doubles.module, "_build_ordinary_parser", autospec=True, return_value=parser
    )
    code, out, err = _run_cli(doubles.module, ["bind", str(_ROOT), "--session", "s"], capsys)

    assert code == EXIT_USAGE
    assert out == ""
    assert "plugin.invalid_arguments" in err
    doubles.bind.assert_not_called()
    doubles.unbind.assert_not_called()
