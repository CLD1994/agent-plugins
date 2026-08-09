from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import cast

from draft import __version__
from draft.cli_results import ResultType
from draft.diagnostics import issue_payload, render_issue
from draft.errors import DraftError
from draft.models import (
    CheckReport,
    LifecycleWarning,
    OperationPhase,
    OperationResult,
    TrashResult,
)
from draft.query import LocalPageSnapshot, SnapshotView

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

    query = subparsers.add_parser(
        "query",
        parents=[shared],
        help="紧凑结构查询",
        description=(
            "以稳定 JSON 返回页面的结构摘要、正文资源、反向链接、正式关系、状态列表与可恢复项。"
        ),
    )
    query_sub = query.add_subparsers(dest="query_command", metavar="QUERY")

    query_page = query_sub.add_parser("page", parents=[shared], help="页面结构摘要")
    query_page.add_argument("root", metavar="ROOT", help="草稿根目录")
    query_page.add_argument("page", metavar="PAGE", help="严格受管理页面路径")

    query_links = query_sub.add_parser("links", parents=[shared], help="页面正文资源")
    query_links.add_argument("root", metavar="ROOT", help="草稿根目录")
    query_links.add_argument("page", metavar="PAGE", help="严格受管理页面路径")

    query_backlinks = query_sub.add_parser("backlinks", parents=[shared], help="正文反向链接来源")
    query_backlinks.add_argument("root", metavar="ROOT", help="草稿根目录")
    query_backlinks.add_argument("target", metavar="TARGET", help="宽松正文资源身份")

    query_relations = query_sub.add_parser("relations", parents=[shared], help="页面正式关系")
    query_relations.add_argument("root", metavar="ROOT", help="草稿根目录")
    query_relations.add_argument("page", metavar="PAGE", help="严格受管理页面路径")
    query_relations.add_argument(
        "--direction",
        choices=("incoming", "outgoing"),
        required=True,
        help="关系方向",
    )

    query_status = query_sub.add_parser("status", parents=[shared], help="按状态列页面")
    query_status.add_argument("root", metavar="ROOT", help="草稿根目录")
    query_status.add_argument("code", metavar="CODE", help="稳定状态代码")

    query_recoverable = query_sub.add_parser("recoverable", parents=[shared], help="可恢复回收项")
    query_recoverable.add_argument("root", metavar="ROOT", help="草稿根目录")

    for collection in (
        query_links,
        query_backlinks,
        query_relations,
        query_status,
        query_recoverable,
    ):
        collection.add_argument(
            "--limit", type=int, default=20, help="返回项数上限（默认 20，最大 100）"
        )
        collection.add_argument("--offset", type=int, default=0, help="跳过前 N 项")
        collection.add_argument("--count-only", action="store_true", help="只返回计数与分页元数据")

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
        "result_type": ResultType.CHECK.value,
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


def _query_items_payload(
    items: Sequence[Mapping[str, object]],
    *,
    limit: int,
    offset: int,
    count_only: bool,
) -> dict[str, object]:
    from draft.cli_results import paginate, slice_payload

    if limit < 1 or limit > 100 or offset < 0:
        raise DraftError("query.invalid_pagination")
    slice_ = paginate(tuple(dict(item) for item in items), offset=offset, limit=limit)
    return slice_payload(slice_, count_only=count_only)  # type: ignore[arg-type]


def _dispatch_query(
    args: argparse.Namespace, *, now: datetime | None = None
) -> tuple[dict[str, object], str, int]:
    root = Path(cast(str, args.root))
    limit = int(getattr(args, "limit", 20))
    offset = int(getattr(args, "offset", 0))
    count_only = bool(getattr(args, "count_only", False))
    if limit < 1 or limit > 100 or offset < 0:
        raise DraftError("query.invalid_pagination")
    q = cast(str, args.query_command)

    if q == "page":
        from draft.query import load_complete_snapshot, page_summary

        page = _page_operand(root, cast(str, args.page))
        graph = load_complete_snapshot(root)
        summary = page_summary(graph, page)
        return (
            {
                "result_type": ResultType.QUERY_PAGE.value,
                "path": summary.path.as_posix(),
                "title": summary.title,
                "status": summary.status.value,
                "parent": None if summary.parent is None else summary.parent.as_posix(),
                "child_count": summary.child_count,
                "link_count": summary.link_count,
                "backlink_count": summary.backlink_count,
                "outgoing_relation_count": summary.outgoing_relation_count,
                "incoming_relation_count": summary.incoming_relation_count,
            },
            f"{summary.path.as_posix()} {summary.title} {summary.status.value}",
            0,
        )
    if q == "links":
        from draft.query import load_registered_page, page_links

        page = _page_operand(root, cast(str, args.page))
        snapshot = load_registered_page(root, page)
        items = [
            {
                "target": item.target,
                "kind": item.kind.value,
                "scope": item.scope.value,
                "target_state": item.target_state.value,
            }
            for item in page_links(_graph_view_for_snapshot(snapshot), page)
        ]
        payload = _query_items_payload(items, limit=limit, offset=offset, count_only=count_only)
        return (
            {"result_type": ResultType.QUERY_LINKS.value, **payload},
            f"链接 {payload['total']} 项",
            0,
        )
    if q == "backlinks":
        from draft.query import backlinks, load_complete_snapshot
        from draft.resources import resource_identity_from_operand

        target = resource_identity_from_operand(root, cast(str, args.target))
        graph = load_complete_snapshot(root)
        items = [{"page": path.as_posix()} for path in backlinks(graph, target)]
        payload = _query_items_payload(items, limit=limit, offset=offset, count_only=count_only)
        return (
            {"result_type": ResultType.QUERY_BACKLINKS.value, **payload},
            f"反向链接 {payload['total']} 项",
            0,
        )
    if q == "relations":
        from draft.query import (
            incoming_relations,
            load_complete_snapshot,
            load_registered_page,
            outgoing_relations,
        )

        page = _page_operand(root, cast(str, args.page))
        direction = cast(str, args.direction)
        if direction == "outgoing":
            snapshot = load_registered_page(root, page)
            view = _graph_view_for_snapshot(snapshot)
            relations = outgoing_relations(view, page)
        else:
            graph = load_complete_snapshot(root)
            relations = incoming_relations(graph, page)
        items = [
            {"relation": item.relation.value, "page": item.page.as_posix()} for item in relations
        ]
        payload = _query_items_payload(items, limit=limit, offset=offset, count_only=count_only)
        return (
            {"result_type": ResultType.QUERY_RELATIONS.value, **payload},
            f"关系 {payload['total']} 项",
            0,
        )
    if q == "status":
        from draft.query import load_complete_snapshot, pages_with_status
        from draft.vocabulary import StatusCode

        code = StatusCode(cast(str, args.code))
        graph = load_complete_snapshot(root)
        items = [{"page": path.as_posix()} for path in pages_with_status(graph, code)]
        payload = _query_items_payload(items, limit=limit, offset=offset, count_only=count_only)
        return (
            {"result_type": ResultType.QUERY_STATUS.value, **payload},
            f"状态 {code.value} 共 {payload['total']} 项",
            0,
        )
    if q == "recoverable":
        from datetime import UTC, datetime

        from draft.trash_lifecycle import list_recoverable

        current = now if now is not None else datetime.now(UTC)
        items = [
            {
                "operation_id": item.operation_id,
                "page": item.page.as_posix(),
                "restore_expires_at": item.restore_expires_at.astimezone(UTC)
                .isoformat()
                .replace("+00:00", "Z"),
            }
            for item in list_recoverable(root, as_of=current)
        ]
        payload = _query_items_payload(items, limit=limit, offset=offset, count_only=count_only)
        as_of = current.isoformat().replace("+00:00", "Z")
        return (
            {"result_type": ResultType.QUERY_RECOVERABLE.value, **payload, "as_of": as_of},
            f"可恢复 {payload['total']} 项",
            0,
        )
    raise DraftError("cli.unknown_query")


def _graph_view_for_snapshot(snapshot: LocalPageSnapshot) -> SnapshotView:
    """把单页快照包装成查询函数需要的最小只读视图。"""
    from draft.query import SnapshotView

    return SnapshotView(snapshot=snapshot)


def _dispatch(
    args: argparse.Namespace, *, now: datetime | None = None
) -> tuple[dict[str, object], str, int]:
    command = cast(str, args.command)
    if command == "query":
        return _dispatch_query(args, now=now)
    if command == "init":
        from draft.lifecycle import initialize

        root = Path(cast(str, args.root))
        initialize(root)
        return {"result_type": ResultType.INIT.value, "root": str(root)}, f"已初始化草稿：{root}", 0
    if command == "adopt":
        from draft.lifecycle import adopt

        root = Path(cast(str, args.root))
        adopt(root)
        return {"result_type": ResultType.ADOPT.value, "root": str(root)}, f"已接管草稿：{root}", 0
    if command == "move":
        from draft.structure import move_page

        root = Path(cast(str, args.root))
        source = _page_operand(root, cast(str, args.source))
        target = _page_operand(root, cast(str, args.target))
        result = move_page(root, source, target)
        payload: dict[str, object] = {"result_type": ResultType.MOVE.value}
        payload.update(_operation_payload(result))
        return payload, f"已移动 {source} → {target}", 0
    if command == "promote":
        from draft.structure import promote_page

        root = Path(cast(str, args.root))
        page = _page_operand(root, cast(str, args.page))
        children = [_page_operand(root, child) for child in cast(list[str], args.children)]
        result = promote_page(root, page, children)
        payload = {"result_type": ResultType.PROMOTE.value}
        payload.update(_operation_payload(result))
        return payload, f"已升格 {page}（{len(children)} 个子页）", 0
    if command == "collapse":
        from draft.structure import collapse_hub

        root = Path(cast(str, args.root))
        hub = _page_operand(root, cast(str, args.hub))
        result = collapse_hub(root, hub)
        payload = {"result_type": ResultType.COLLAPSE.value}
        payload.update(_operation_payload(result))
        return payload, f"已降级 {hub}", 0
    if command == "trash":
        from draft.trash import trash_page

        root = Path(cast(str, args.root))
        page = _page_operand(root, cast(str, args.page))
        result = trash_page(root, page)
        payload: dict[str, object] = {"result_type": ResultType.TRASH.value}
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
            payload = {
                "result_type": ResultType.RESTORE.value,
                **_operation_payload(result),
                "ok": False,
            }
            return (
                payload,
                "恢复未完成，操作已经回滚；权威删除包仍保留。",
                3,
            )
        payload = {"result_type": ResultType.RESTORE.value, **_operation_payload(result)}
        return payload, f"已恢复操作 {operation_id} 的页面", 0
    if command == "recover":
        from draft.trash_lifecycle import recover_interrupted_operations

        root = Path(cast(str, args.root))
        results = recover_interrupted_operations(root)
        paths = sorted(
            {
                path
                for result in results
                for path in (
                    result.changed_paths
                    if isinstance(result, OperationResult)
                    else (result.package,)
                )
            }
        )
        payload = {
            "result_type": ResultType.RECOVER.value,
            "fixed_count": len(results),
            "paths": [path.as_posix() for path in paths],
        }
        return payload, f"恢复完成：{len(results)} 项结果", 0
    raise DraftError("cli.unknown_command")


def _exit_for_error(error: DraftError) -> int:
    if error.code.startswith("operation.") or error.code == "adopt.recovery_required":
        return _EXIT_OPERATION
    return _EXIT_DOMAIN


def _preflight_candidate_root_for(args: argparse.Namespace) -> Path | None:
    """从命令参数中找出可能需要前置推进的根；不声称该根已经登记。"""
    command = cast(str, args.command)
    if command in ("init", "adopt"):
        return None
    if command == "check" and bool(getattr(args, "candidate", False)):
        return None
    return Path(cast(str, args.root))


def _registered_root_for(candidate: Path) -> Path | None:
    """只读资格判定：只有正式登记标记精确有效才返回解析后的根，否则返回 None。

    根或标记缺失、无效、不支持或为临时接管形状时不推进，交由命令自身验证。
    本边界复用共享标记解析语义，不在这里复制登记键集合。
    """
    from draft.graph import read_marker
    from draft.paths import resolve_draft_root

    try:
        resolved = resolve_draft_root(candidate)
    except DraftError:
        return None
    try:
        read_marker(resolved)
    except DraftError:
        return None
    return resolved


def _advance_for_command(root: Path, *, now: datetime | None = None) -> None:
    from draft.trash_lifecycle import prepare_registered_command

    prepare_registered_command(root, now=now)


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

    from datetime import UTC, datetime

    captured_now = datetime.now(UTC)

    candidate_root = _preflight_candidate_root_for(args)
    if candidate_root is not None:
        registered = _registered_root_for(candidate_root)
        if registered is not None:
            try:
                _advance_for_command(registered, now=captured_now)
            except DraftError as error:
                _emit_error(error, json_mode)
                return _exit_for_error(error)

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
        payload, human, code = _dispatch(args, now=captured_now)
    except DraftError as error:
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
        "result_type": ResultType.ERROR.value,
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
