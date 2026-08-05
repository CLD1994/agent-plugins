from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from draft import __version__
from draft.diagnostics import issue_payload, render_issue
from draft.errors import DraftError
from draft.models import (
    CheckReport,
    LifecycleWarning,
    OperationPhase,
    OperationResult,
    TrashResult,
)
from draft.trash_lifecycle import LifecycleAdvanceReport, LifecycleFailure

_EXIT_DOMAIN = 2
_EXIT_OPERATION = 3
_EXIT_RUNTIME = 4


def build_parser() -> argparse.ArgumentParser:
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="以稳定 JSON 信封输出结果",
    )
    parser = argparse.ArgumentParser(prog="draft", parents=[shared])
    parser.add_argument("--version", action="store_true")
    subparsers = parser.add_subparsers(dest="command")

    init = subparsers.add_parser("init", parents=[shared])
    init.add_argument("root", metavar="ROOT")

    adopt = subparsers.add_parser("adopt", parents=[shared])
    adopt.add_argument("root", metavar="ROOT")

    check = subparsers.add_parser("check", parents=[shared])
    check.add_argument("root", metavar="ROOT")
    check.add_argument(
        "--candidate",
        action="store_true",
        help="按未登记候选布局检查（只读，要求不存在内部状态）",
    )

    move = subparsers.add_parser("move", parents=[shared])
    move.add_argument("root", metavar="ROOT")
    move.add_argument("source", metavar="SOURCE")
    move.add_argument("target", metavar="TARGET")

    promote = subparsers.add_parser("promote", parents=[shared])
    promote.add_argument("root", metavar="ROOT")
    promote.add_argument("page", metavar="PAGE")
    promote.add_argument("children", metavar="CHILD", nargs="*")

    collapse = subparsers.add_parser("collapse", parents=[shared])
    collapse.add_argument("root", metavar="ROOT")
    collapse.add_argument("hub", metavar="HUB")

    trash = subparsers.add_parser("trash", parents=[shared])
    trash.add_argument("root", metavar="ROOT")
    trash.add_argument("page", metavar="PAGE")

    restore = subparsers.add_parser("restore", parents=[shared])
    restore.add_argument("root", metavar="ROOT")
    restore.add_argument("operation_id", metavar="OPERATION_ID")

    recover = subparsers.add_parser("recover", parents=[shared])
    recover.add_argument("root", metavar="ROOT")

    bind_cmd = subparsers.add_parser("bind", parents=[shared])
    bind_cmd.add_argument("root", metavar="ROOT")
    bind_cmd.add_argument("--session", required=True, metavar="SESSION_ID")
    bind_cmd.add_argument("--last-page-hint", metavar="PAGE")

    unbind_cmd = subparsers.add_parser("unbind", parents=[shared])
    unbind_cmd.add_argument("--session", required=True, metavar="SESSION_ID")

    return parser


def _warning_payload(warning: LifecycleWarning) -> dict[str, object]:
    return {
        "code": warning.code,
        "operation_id": warning.operation_id,
        "details": list(warning.details),
    }


def _warning_text(warning: LifecycleWarning) -> str:
    detail = "：".join(warning.details)
    suffix = f"（{detail}）" if detail else ""
    return f"{warning.code}（{warning.operation_id}）{suffix}"


def _format_optional_datetime(value: object) -> str | None:
    from datetime import datetime

    from draft.operations import format_utc

    if value is None:
        return None
    if isinstance(value, datetime):
        return format_utc(value)
    return None


def _operation_payload(result: OperationResult) -> dict[str, object]:
    return {
        "operation_id": result.operation_id,
        "phase": result.phase.value,
        "changed_paths": [path.as_posix() for path in result.changed_paths],
        "warnings": [_warning_payload(warning) for warning in result.warnings],
    }


def _trash_payload(result: TrashResult) -> dict[str, object]:
    return {
        "operation_id": result.operation_id,
        "phase": result.phase.value,
        "package": result.package.as_posix(),
        "restore_expires_at": _format_optional_datetime(result.restore_expires_at),
        "purge_eligible_at": _format_optional_datetime(result.purge_eligible_at),
        "warnings": [_warning_payload(warning) for warning in result.warnings],
    }


def _emit(payload: dict[str, object], json_mode: bool, human: str) -> None:
    if json_mode:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(human)


def _render_check(report: CheckReport, json_mode: bool) -> int:
    ok = not report.errors
    payload: dict[str, object] = {
        "ok": ok,
        "result_type": "check",
        "errors": [issue_payload(issue) for issue in report.errors],
        "warnings": [issue_payload(issue) for issue in report.warnings],
        "infos": [issue_payload(issue) for issue in report.infos],
        "reviews": [issue_payload(issue) for issue in report.reviews],
    }
    if json_mode:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        groups = (report.errors, report.warnings, report.infos, report.reviews)
        if not any(groups):
            print("检查通过：未发现客观错误。")
        else:
            for issues in groups:
                for issue in issues:
                    print(render_issue(issue))
            print(
                f"检查完成：{len(report.errors)} 个错误，{len(report.warnings)} 个警告，"
                f"{len(report.infos)} 个信息，{len(report.reviews)} 个提醒。"
            )
    return 0 if ok else 1


def _page_operand(root: Path, value: str) -> Path:
    from draft.paths import resolve_page_operand

    return resolve_page_operand(root, value).relative


def _dispatch(args: argparse.Namespace) -> tuple[dict[str, object], str, int]:
    command = cast(str, args.command)
    if command == "init":
        from draft.lifecycle import initialize

        root = Path(cast(str, args.root))
        initialize(root)
        return {"result_type": "init", "root": str(root)}, f"已初始化草稿：{root}", 0
    if command == "adopt":
        from draft.lifecycle import adopt

        root = Path(cast(str, args.root))
        adopt(root)
        return {"result_type": "adopt", "root": str(root)}, f"已接管草稿：{root}", 0
    if command == "move":
        from draft.structure import move_page

        root = Path(cast(str, args.root))
        source = _page_operand(root, cast(str, args.source))
        target = _page_operand(root, cast(str, args.target))
        result = move_page(root, source, target)
        payload: dict[str, object] = {"result_type": "move"}
        payload.update(_operation_payload(result))
        return payload, f"已移动 {source} → {target}", 0
    if command == "promote":
        from draft.structure import promote_page

        root = Path(cast(str, args.root))
        page = _page_operand(root, cast(str, args.page))
        children = [_page_operand(root, child) for child in cast(list[str], args.children)]
        result = promote_page(root, page, children)
        payload = {"result_type": "promote"}
        payload.update(_operation_payload(result))
        return payload, f"已升格 {page}（{len(children)} 个子页）", 0
    if command == "collapse":
        from draft.structure import collapse_hub

        root = Path(cast(str, args.root))
        hub = _page_operand(root, cast(str, args.hub))
        result = collapse_hub(root, hub)
        payload = {"result_type": "collapse"}
        payload.update(_operation_payload(result))
        return payload, f"已降级 {hub}", 0
    if command == "trash":
        from draft.trash import trash_page

        root = Path(cast(str, args.root))
        page = _page_operand(root, cast(str, args.page))
        result = trash_page(root, page)
        payload: dict[str, object] = {"result_type": "trash"}
        payload.update(_trash_payload(result))
        human = f"已回收 {page}（{result.phase.value}）"
        if result.warnings:
            human = f"{human}；警告：{_warning_text(result.warnings[0])}"
        return payload, human, 0
    if command == "restore":
        from draft.trash import restore_retained_trash

        root = Path(cast(str, args.root))
        operation_id = cast(str, args.operation_id)
        result = restore_retained_trash(root, operation_id)
        if result.phase is OperationPhase.ROLLED_BACK:
            payload = {"result_type": "restore", **_operation_payload(result), "ok": False}
            return (
                payload,
                "恢复未完成，操作已经回滚；权威删除包仍保留。",
                3,
            )
        payload = {"result_type": "restore", **_operation_payload(result)}
        return payload, f"已恢复操作 {operation_id} 的页面", 0
    if command == "recover":
        from draft.trash_lifecycle import recover_draft

        root = Path(cast(str, args.root))
        report = recover_draft(root)
        results_payload = [
            _trash_payload(result)
            if isinstance(result, TrashResult)
            else _operation_payload(result)
            for result in report.results
        ]
        if report.error is not None:
            failure = report.error
            payload: dict[str, object] = {
                "result_type": "recover",
                "results": results_payload,
                "error": {
                    "code": failure.code,
                    "operation_id": failure.operation_id,
                    "paths": [path.as_posix() for path in failure.paths],
                    "details": list(failure.details),
                },
                "stopped_at_operation_id": failure.operation_id,
                "ok": False,
            }
            return (
                payload,
                f"恢复停止在 {failure.operation_id}（{failure.code}）",
                _exit_for_error(DraftError(failure.code, *failure.paths, details=failure.details)),
            )
        payload = {
            "result_type": "recover",
            "results": results_payload,
        }
        return payload, f"恢复完成：{len(report.results)} 项结果", 0
    if command == "bind":
        import os

        from draft import bindings
        from draft.models import Binding

        data_value = os.environ.get("DRAFT_DATA")
        if data_value is None:
            raise DraftError("binding.plugin_context_unavailable")
        session = cast(str, args.session)
        root = Path(cast(str, args.root))
        hint_value = cast("str | None", args.last_page_hint)
        hint = _page_operand(root, hint_value) if hint_value else None
        skill_path = bindings.required_skill_path(os.environ.get("DRAFT_SKILL_PATH"))
        digest = bindings.skill_digest(skill_path)
        binding = bindings.bind(
            Path(data_value),
            Binding(
                session_id=session,
                root=root,
                last_page_hint=hint,
                skill_name=bindings.SKILL_NAME,
                skill_digest=digest,
            ),
        )
        return (
            {
                "result_type": "bind",
                "root": str(binding.root),
            },
            f"已绑定会话到 {binding.root}",
            0,
        )
    if command == "unbind":
        import os

        from draft import bindings

        data_value = os.environ.get("DRAFT_DATA")
        if data_value is None:
            raise DraftError("binding.plugin_context_unavailable")
        session = cast(str, args.session)
        removed = bindings.unbind(Path(data_value), session)
        return (
            {
                "result_type": "unbind",
                "removed": removed,
            },
            ("已解除绑定" if removed else "绑定不存在"),
            0,
        )
    raise DraftError("cli.unknown_command")


def _render_lifecycle_result(result: OperationResult | TrashResult) -> dict[str, object]:
    if isinstance(result, TrashResult):
        return _trash_payload(result)
    return _operation_payload(result)


def _render_failure(failure: LifecycleFailure) -> dict[str, object]:
    return {
        "code": failure.code,
        "operation_id": failure.operation_id,
        "paths": [path.as_posix() for path in failure.paths],
        "details": list(failure.details),
    }


def _structure_preflight_payload(report: LifecycleAdvanceReport) -> dict[str, object]:
    failure = report.error
    assert failure is not None
    return {
        "ok": False,
        "result_type": "error",
        "lifecycle_results": [_render_lifecycle_result(result) for result in report.results],
        "error": _render_failure(failure),
        "stopped_at_operation_id": failure.operation_id,
    }


def _structure_preflight_human(report: LifecycleAdvanceReport) -> str:
    failure = report.error
    assert failure is not None
    completed = len(report.results)
    return f"结构写前置推进停止在 {failure.operation_id}（{failure.code}），已完成 {completed} 项"


def _exit_for_error(error: DraftError) -> int:
    if error.code.startswith("operation.") or error.code == "adopt.recovery_required":
        return _EXIT_OPERATION
    if error.code in (
        "binding.plugin_context_unavailable",
        "binding.data_dir_unavailable",
        "binding.skill_unavailable",
    ):
        return _EXIT_RUNTIME
    return _EXIT_DOMAIN


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments[:1] == ["_session-start"]:
        from draft import session_hook

        return session_hook.main(arguments[1:])
    parser = build_parser()
    args = parser.parse_args(arguments)
    if getattr(args, "version", False):
        print(__version__)
        return 0
    if args.command is None:
        parser.error("缺少子命令")
    json_mode = bool(getattr(args, "json", False))
    command = cast(str, args.command)
    if command == "check":
        from draft.checker import check_draft
        from draft.models import ValidationMode

        mode: ValidationMode = "candidate" if bool(args.candidate) else "registered"
        try:
            report = check_draft(Path(cast(str, args.root)), mode=mode)
        except DraftError as error:
            _emit_error(error, json_mode)
            return _exit_for_error(error)
        code = _render_check(report, json_mode)
        return code
    try:
        payload, human, code = _dispatch(args)
    except DraftError as error:
        from draft.trash_lifecycle import LifecycleAdvanceError

        if isinstance(error, LifecycleAdvanceError):
            report = error.report
            payload = _structure_preflight_payload(report)
            _emit(payload, json_mode, _structure_preflight_human(report))
            failure = report.error
            assert failure is not None
            return _exit_for_error(
                DraftError(failure.code, *failure.paths, details=failure.details)
            )
        _emit_error(error, json_mode)
        return _exit_for_error(error)
    except OSError as error:
        _emit_error(
            DraftError("runtime.os_error", details=(f"{type(error).__name__}: {error}",)),
            json_mode,
        )
        return _EXIT_RUNTIME
    payload.setdefault("ok", code == 0)
    _emit(payload, json_mode, human)
    return code


def _emit_error(error: DraftError, json_mode: bool) -> None:
    payload: dict[str, object] = {
        "ok": False,
        "result_type": "error",
        "error": {
            "code": error.code,
            "severity": "error",
            "paths": [path.as_posix() for path in error.paths],
            "details": list(error.details),
        },
    }
    if json_mode:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        paths = " ".join(str(path) for path in error.paths)
        details = "；".join(error.details)
        print(f"错误 {error.code} {paths} {details}".rstrip())
