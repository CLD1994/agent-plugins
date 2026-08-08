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
    parser = argparse.ArgumentParser(
        prog="draft",
        parents=[shared],
        description="递归讨论草稿维护工具：以页面网络为单一事实源，提供登记、检查、结构维护与回收生命周期推进命令。",
    )
    parser.add_argument("--version", action="store_true", help="显示版本号后退出")
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    init = subparsers.add_parser(
        "init",
        parents=[shared],
        help="初始化新草稿",
        description="在 ROOT 创建草稿结构并登记，作为后续结构维护的入口。",
    )
    init.add_argument("root", metavar="ROOT", help="草稿根目录（将在此创建草稿结构）")

    adopt = subparsers.add_parser(
        "adopt",
        parents=[shared],
        help="接管格式完整的未登记目录",
        description="把已符合草稿格式的未登记目录登记为草稿（可先用 check --candidate 验证）。",
    )
    adopt.add_argument("root", metavar="ROOT", help="已符合草稿格式的目录")

    check = subparsers.add_parser(
        "check",
        parents=[shared],
        help="检查草稿一致性",
        description="按已登记布局检查草稿，报告错误、警告、信息与提醒；默认模式要求存在内部登记。",
    )
    check.add_argument("root", metavar="ROOT", help="草稿根目录")
    check.add_argument(
        "--candidate",
        action="store_true",
        help="按未登记候选布局检查（只读，要求不存在内部状态）",
    )

    move = subparsers.add_parser(
        "move",
        parents=[shared],
        help="移动页面",
        description="把 SOURCE 页面连同其子页移动到 TARGET 位置，并更新网络链接。",
    )
    move.add_argument("root", metavar="ROOT", help="草稿根目录")
    move.add_argument("source", metavar="SOURCE", help="源页面相对路径（如 pages/a.md）")
    move.add_argument("target", metavar="TARGET", help="目标页面相对路径")

    promote = subparsers.add_parser(
        "promote",
        parents=[shared],
        help="升格页面为枢纽",
        description="把 PAGE 升格为枢纽，将列出的 CHILD 降级为它的子页。",
    )
    promote.add_argument("root", metavar="ROOT", help="草稿根目录")
    promote.add_argument("page", metavar="PAGE", help="要升格的页面相对路径")
    promote.add_argument(
        "children",
        metavar="CHILD",
        nargs="*",
        help="要降级为子页的页面相对路径（可多个）",
    )

    collapse = subparsers.add_parser(
        "collapse",
        parents=[shared],
        help="降级枢纽为普通页面",
        description="把 HUB 枢纽降级为普通页面并合并回父级，不留空枢纽。",
    )
    collapse.add_argument("root", metavar="ROOT", help="草稿根目录")
    collapse.add_argument("hub", metavar="HUB", help="枢纽页面相对路径")

    trash = subparsers.add_parser(
        "trash",
        parents=[shared],
        help="回收页面为保留态",
        description=(
            "把 PAGE 及子页回收为 7 天保留态，期间可 restore 恢复；存在未解除的入站引用时拒绝。"
        ),
    )
    trash.add_argument("root", metavar="ROOT", help="草稿根目录")
    trash.add_argument("page", metavar="PAGE", help="要回收的页面相对路径")

    restore = subparsers.add_parser(
        "restore",
        parents=[shared],
        help="恢复被回收的页面",
        description="按 OPERATION_ID 恢复保留期内被回收的操作对应的页面；恢复未完成时退出码为 3。",
    )
    restore.add_argument("root", metavar="ROOT", help="草稿根目录")
    restore.add_argument(
        "operation_id",
        metavar="OPERATION_ID",
        help="回收操作标识符（trash 返回）",
    )

    recover = subparsers.add_parser(
        "recover",
        parents=[shared],
        help="推进回收生命周期",
        description="推进回收生命周期：到期项移交系统回收站，已到清理期限的项完成最终清理。",
    )
    recover.add_argument("root", metavar="ROOT", help="草稿根目录")

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
    return _EXIT_DOMAIN


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
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
