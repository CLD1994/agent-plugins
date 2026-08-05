# /// script
# requires-python = ">=3.12,<3.15"
# dependencies = []
# ///

"""Draft 插件 CLI 入口：分派 bind/unbind/plugin-context reset/hook session-start 到适配层。

仅依赖 Python 标准库与 adapter 包。不导入核心 draft 运行时。适配层符号以模块级
名称导入，便于测试 monkeypatch。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import NoReturn, TextIO, cast

from adapter.bindings import Binding, bind, unbind
from adapter.bridge import load_context, reset_context
from adapter.errors import EXIT_RUNTIME, EXIT_USAGE, AdapterError, error_payload, render_error
from adapter.session_hook import render_diagnostic, run_session_start

_JSON_FLAG = "--json"


def main(
    argv: Sequence[str] | None = None,
    *,
    entry_path: Path | None = None,
    environ: Mapping[str, str] | None = None,
    stdin: TextIO | None = None,
    runtime_dir: Path | None = None,
) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    active_entry = Path(__file__) if entry_path is None else entry_path
    active_environ = os.environ if environ is None else environ
    active_stdin = sys.stdin if stdin is None else stdin
    return dispatch_plugin_command(
        arguments,
        entry_path=active_entry,
        environ=active_environ,
        stdin=active_stdin,
        runtime_dir=runtime_dir,
    )


def dispatch_plugin_command(
    arguments: Sequence[str],
    *,
    entry_path: Path,
    environ: Mapping[str, str],
    stdin: TextIO,
    runtime_dir: Path | None,
) -> int:
    args = list(arguments)

    # hook session-start 前置分派：即使参数计数失败也使用模型可见的 Hook 诊断并返回 0。
    if len(args) >= 2 and args[0] == "hook" and args[1] == "session-start":
        return _dispatch_hook_session_start(args[2:], entry_path, environ, stdin, runtime_dir)

    # plugin-context reset 前置分派：先于任何 bridge 加载，不调用 load_context。
    if len(args) >= 2 and args[0] == "plugin-context" and args[1] == "reset":
        return _dispatch_reset(args[2:], entry_path, runtime_dir)

    # 仅对 bind/unbind 归一化 --json，使三处等价位置一致。
    json_mode, cleaned = _extract_json_for_ordinary(args)

    return _dispatch_ordinary(
        cleaned,
        entry_path=entry_path,
        json_mode=json_mode,
        runtime_dir=runtime_dir,
    )


# ---------------------------------------------------------------------------
# --json 归一化（仅 bind/unbind）
# ---------------------------------------------------------------------------


def _extract_json_for_ordinary(args: list[str]) -> tuple[bool, list[str]]:
    """从 bind/unbind argv 中提取 --json 的等价位置：子命令紧前、紧后或末尾。

    --json 出现在其它位置时不提取，交由 argparse 拒绝为用法错误。
    """
    if not args:
        return False, args
    sub_index: int | None = None
    if args[0] in ("bind", "unbind"):
        sub_index = 0
    elif len(args) >= 2 and args[0] == _JSON_FLAG and args[1] in ("bind", "unbind"):
        sub_index = 1
    if sub_index is None:
        return False, args

    cleaned = list(args)
    json_mode = False
    # 位置 1：子命令紧前
    if sub_index == 1:
        cleaned.pop(0)
        json_mode = True
    # 位置 2：子命令紧后
    if len(cleaned) >= 2 and cleaned[1] == _JSON_FLAG:
        cleaned.pop(1)
        json_mode = True
    # 位置 3：argv 末尾
    if len(cleaned) >= 2 and cleaned[-1] == _JSON_FLAG:
        cleaned.pop()
        json_mode = True
    return json_mode, cleaned


# ---------------------------------------------------------------------------
# ArgumentParser 子类
# ---------------------------------------------------------------------------


class _PluginArgumentParser(argparse.ArgumentParser):
    """error() 抛出 AdapterError(plugin.invalid_arguments, exit 2)；--help 仍退出 0。"""

    def error(self, message: str) -> NoReturn:  # type: ignore[override]
        raise AdapterError(
            "plugin.invalid_arguments",
            details=(message,),
            exit_code=EXIT_USAGE,
        )


def _build_ordinary_parser() -> _PluginArgumentParser:
    parser = _PluginArgumentParser(prog="draft.py", add_help=True)
    sub = parser.add_subparsers(dest="command", required=True)

    p_bind = sub.add_parser("bind")
    p_bind.add_argument("root", type=Path)
    p_bind.add_argument("--session", required=True)
    p_bind.add_argument("--last-page-hint", type=Path, default=None)

    p_unbind = sub.add_parser("unbind")
    p_unbind.add_argument("--session", required=True)

    return parser


# ---------------------------------------------------------------------------
# 普通分派（bind/unbind）
# ---------------------------------------------------------------------------


def _dispatch_ordinary(
    args: list[str],
    *,
    entry_path: Path,
    json_mode: bool,
    runtime_dir: Path | None,
) -> int:
    parser = _build_ordinary_parser()
    try:
        namespace = parser.parse_args(args)
    except SystemExit as exc:
        # --help/-h：argparse 打印帮助后 SystemExit(0)。
        return exc.code if isinstance(exc.code, int) else 0
    except AdapterError as exc:
        _emit_error(exc, json_mode)
        return exc.exit_code

    try:
        # load_context 恰好调用一次；None 映射为 plugin.context_unavailable。
        context = load_context(entry_path, runtime_dir)
        if context is None:
            raise AdapterError("plugin.context_unavailable", exit_code=EXIT_RUNTIME)

        command = cast(str, namespace.command)
        if command == "bind":
            binding = Binding(
                session_id=cast(str, namespace.session),
                root=cast(Path, namespace.root),
                last_page_hint=cast("Path | None", namespace.last_page_hint),
            )
            result = bind(context.data_dir, binding)
            _emit_bind_success(result, json_mode)
        elif command == "unbind":
            removed = unbind(context.data_dir, cast(str, namespace.session))
            _emit_unbind_success(removed, json_mode)
        else:
            raise AdapterError(
                "plugin.invalid_arguments",
                details=(f"unknown command: {command}",),
                exit_code=EXIT_USAGE,
            )
        return 0
    except AdapterError as exc:
        _emit_error(exc, json_mode)
        return exc.exit_code


def _emit_bind_success(result: Binding, json_mode: bool) -> None:
    if json_mode:
        payload = {"ok": True, "result_type": "bind", "root": str(result.root)}
        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    else:
        sys.stdout.write(f"绑定成功 {result.root}\n")


def _emit_unbind_success(removed: bool, json_mode: bool) -> None:
    if json_mode:
        payload = {"ok": True, "result_type": "unbind", "removed": removed}
        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    else:
        sys.stdout.write(f"解除成功 removed={'true' if removed else 'false'}\n")


# ---------------------------------------------------------------------------
# reset 分派
# ---------------------------------------------------------------------------


def _dispatch_reset(rest: list[str], entry_path: Path, runtime_dir: Path | None) -> int:
    if rest:
        _emit_error(
            AdapterError(
                "plugin.invalid_arguments",
                details=("unexpected arguments for 'plugin-context reset'",),
                exit_code=EXIT_USAGE,
            ),
            json_mode=False,
        )
        return EXIT_USAGE
    try:
        reset_context(entry_path, runtime_dir)
        return 0
    except AdapterError as exc:
        _emit_error(exc, json_mode=False)
        return exc.exit_code


# ---------------------------------------------------------------------------
# hook session-start 分派
# ---------------------------------------------------------------------------


def _dispatch_hook_session_start(
    rest: list[str],
    entry_path: Path,
    environ: Mapping[str, str],
    stdin: TextIO,
    runtime_dir: Path | None,
) -> int:
    if len(rest) != 1:
        error = AdapterError("hook.invalid_arguments", exit_code=EXIT_RUNTIME)
        sys.stdout.write(render_diagnostic(error) + "\n")
        return 0
    host = rest[0]
    return run_session_start(
        host,
        entry_path=entry_path,
        environ=environ,
        stdin=stdin,
        runtime_dir=runtime_dir,
    )


# ---------------------------------------------------------------------------
# 错误输出
# ---------------------------------------------------------------------------


def _emit_error(error: AdapterError, json_mode: bool) -> None:
    if json_mode:
        sys.stdout.write(json.dumps(error_payload(error), ensure_ascii=False) + "\n")
    else:
        sys.stderr.write(render_error(error) + "\n")


if __name__ == "__main__":
    sys.exit(main())
