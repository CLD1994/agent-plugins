from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import cast

from draft import __version__
from draft.cli_results import CheckSlice, DryRunOperation, ResultType, project_check
from draft.diagnostic_codes import CheckCode
from draft.diagnostics import issue_payload, render_issue
from draft.errors import DraftError
from draft.models import (
    CheckReport,
    LifecycleWarning,
    OperationPhase,
    OperationPlan,
    OperationResult,
    RootState,
    TrashResult,
)
from draft.move_operands import MOVE_OPERANDS_INVALID, TargetKind, target_kind_of
from draft.operation_facts import OperationFacts
from draft.paths import FilesystemProbe, ManagedRoot, select_managed_root
from draft.plan_effects import PlanEffect
from draft.query import LocalPageSnapshot, SnapshotView, TolerantView
from draft.vocabulary import EditableRelationCode, StatusCode

_EXIT_DOMAIN = 2

_CREATE_STATUSES = (StatusCode.PENDING, StatusCode.PROPOSED)
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
    rooted = argparse.ArgumentParser(add_help=False, parents=[shared])
    rooted.add_argument(
        "--root",
        metavar="ROOT",
        help="草稿根目录（缺省回退到绝对 DRAFT_ROOT，再回退到 pwd 祖先发现）",
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
        description="在 ROOT 创建草稿结构并登记；--candidate 创建未登记候选根。",
    )
    init.add_argument("root", metavar="ROOT", help="草稿根目录（将在此创建草稿结构）")
    init.add_argument(
        "--candidate",
        action="store_true",
        help="创建 root_state=candidate 的候选根（允许渐进整理，adopt 前不登记）",
    )

    _adopt = subparsers.add_parser(
        "adopt",
        parents=[rooted],
        help="接管格式完整的候选根",
        description="把 root_state=candidate 的受管根原子推进为 registered。",
    )

    _check = subparsers.add_parser(
        "check",
        parents=[rooted],
        help="检查草稿一致性",
        description="按已登记布局检查草稿；candidate 与 registered 共享同一检查协议。",
    )
    _check.add_argument(
        "--code",
        action="append",
        metavar="CODE",
        choices=tuple(member.value for member in CheckCode),
        help="按稳定错误码过滤（可重复，取并集）",
    )
    _check.add_argument(
        "--path-prefix",
        action="append",
        metavar="PREFIX",
        type=_check_path_prefix,
        help="按根内路径前缀过滤（可重复，取并集；按路径段匹配）",
    )
    _check.add_argument("--limit", type=int, default=20, help="返回项数上限（默认 20，最大 100）")
    _check.add_argument("--offset", type=int, default=0, help="跳过前 N 项")
    _check.add_argument("--count-only", action="store_true", help="只返回计数与分页事实")

    move = subparsers.add_parser(
        "move",
        parents=[rooted],
        help="移动页面或未受管理文件",
        description=(
            "把一个或多个 SOURCE 移动到 TARGET，并重算全部可证明的相关链接。"
            "TARGET 以 .md 结尾时是文件目标，只允许单个文件 SOURCE；"
            "否则是前缀目标，来源按基名或目录名落入其下。"
            "目录 SOURCE 只移动其中的 .md 普通文件，其余内容原地留下。"
        ),
    )
    move.add_argument(
        "operands",
        metavar="OPERAND",
        nargs="+",
        help="SOURCE... TARGET（最后一个操作数是 TARGET）",
    )
    _add_dry_run_args(move)

    promote = subparsers.add_parser(
        "promote",
        parents=[rooted],
        help="升格页面为枢纽",
        description="把 PAGE 升格为枢纽，将列出的 CHILD 降级为它的子页。",
    )
    promote.add_argument("page", metavar="PAGE", help="要升格的页面相对路径")
    promote.add_argument(
        "children",
        metavar="CHILD",
        nargs="*",
        help="要降级为子页的页面相对路径（可多个）",
    )
    _add_dry_run_args(promote)

    collapse = subparsers.add_parser(
        "collapse",
        parents=[rooted],
        help="降级枢纽为普通页面",
        description="把 HUB 枢纽降级为普通页面并合并回父级，不留空枢纽。",
    )
    collapse.add_argument("hub", metavar="HUB", help="枢纽页面相对路径")
    _add_dry_run_args(collapse)

    trash = subparsers.add_parser(
        "trash",
        parents=[rooted],
        help="回收页面为保留态",
        description=(
            "把 PAGE 及子页回收为 7 天保留态，期间可 restore 恢复；存在未解除的入站引用时拒绝。"
        ),
    )
    trash.add_argument("page", metavar="PAGE", help="要回收的页面相对路径")
    _add_dry_run_args(trash)

    restore = subparsers.add_parser(
        "restore",
        parents=[rooted],
        help="恢复被回收的页面",
        description="按 OPERATION_ID 恢复保留期内被回收的操作对应的页面；恢复未完成时退出码为 3。",
    )
    restore.add_argument(
        "operation_id",
        metavar="OPERATION_ID",
        help="回收操作标识符（trash 返回）",
    )

    _recover = subparsers.add_parser(
        "recover",
        parents=[rooted],
        help="修复中断的写操作",
        description="修复崩溃或中断留下的未完成写操作；回收生命周期推进由所有已登记命令的前置静默完成。",
    )

    create = subparsers.add_parser(
        "create",
        parents=[rooted],
        help="创建叶子页面",
        description="在 pages/ 下原子创建新叶子页面并更新父导航。",
    )
    create.add_argument("page", metavar="PAGE", help="新页面相对路径（如 pages/topic.md）")
    create.add_argument("--title", required=True, help="页面标题（单行普通文本）")
    create.add_argument(
        "--status",
        choices=tuple(member.value for member in StatusCode if member in _CREATE_STATUSES),
        required=True,
        help="初始状态",
    )
    _add_dry_run_args(create)

    status_cmd = subparsers.add_parser(
        "status",
        parents=[rooted],
        help="修改页面状态",
        description="修改页面讨论状态，只要求最终结构满足不变量。",
    )
    status_sub = status_cmd.add_subparsers(dest="status_command", metavar="SUB")
    status_set = status_sub.add_parser("set", parents=[rooted], help="设置页面状态")
    status_set.add_argument("page", metavar="PAGE", help="严格受管理页面路径")
    status_set.add_argument(
        "code",
        metavar="CODE",
        choices=tuple(member.value for member in StatusCode),
        help="稳定状态代码",
    )
    status_set.add_argument("--note", help="替换状态说明")
    status_set.add_argument("--clear-note", action="store_true", help="删除状态说明")
    _add_dry_run_args(status_set)

    relation_cmd = subparsers.add_parser(
        "relation",
        parents=[rooted],
        help="修改正式关系",
        description="幂等添加或删除依赖/取代/质疑关系，必要时维护反向与状态。",
    )
    relation_sub = relation_cmd.add_subparsers(dest="relation_command", metavar="SUB")
    relation_choices = tuple(member.value for member in EditableRelationCode)
    for sub_name, sub_help in (("add", "添加关系"), ("remove", "删除关系")):
        sub = relation_sub.add_parser(sub_name, parents=[rooted], help=sub_help)
        sub.add_argument("source", metavar="SOURCE", help="来源页面相对路径")
        sub.add_argument("code", metavar="CODE", choices=relation_choices, help="主动关系代码")
        sub.add_argument("target", metavar="TARGET", help="目标页面相对路径")
        if sub_name == "remove":
            sub.add_argument(
                "--status",
                choices=tuple(
                    member.value for member in StatusCode if member not in (StatusCode.SUPERSEDED,)
                ),
                help="删除最后一条取代关系后恢复目标状态",
            )
        _add_dry_run_args(sub)

    generate_conclusions = subparsers.add_parser(
        "generate-conclusions",
        parents=[rooted],
        help="按需生成结论页",
        description="从当前已确认页面重建结论汇总并推进所有权状态为 generated。",
    )
    _add_dry_run_args(generate_conclusions)

    query = subparsers.add_parser(
        "query",
        parents=[rooted],
        help="紧凑结构查询",
        description=(
            "以稳定 JSON 返回页面的结构摘要、正文资源、反向链接、正式关系、状态列表与可恢复项。"
        ),
    )
    query_sub = query.add_subparsers(dest="query_command", metavar="QUERY", required=True)

    query_page = query_sub.add_parser("page", parents=[rooted], help="页面结构摘要")
    query_page.add_argument("page", metavar="PAGE", help="严格受管理页面路径")

    query_links = query_sub.add_parser("links", parents=[rooted], help="页面正文资源")
    query_links.add_argument("page", metavar="PAGE", help="严格受管理页面路径")

    query_backlinks = query_sub.add_parser("backlinks", parents=[rooted], help="正文反向链接来源")
    query_backlinks.add_argument("target", metavar="TARGET", help="宽松正文资源身份")

    query_relations = query_sub.add_parser("relations", parents=[rooted], help="页面正式关系")
    query_relations.add_argument("page", metavar="PAGE", help="严格受管理页面路径")
    query_relations.add_argument(
        "--direction",
        choices=("incoming", "outgoing"),
        required=True,
        help="关系方向",
    )

    query_status = query_sub.add_parser("status", parents=[rooted], help="按状态列页面")
    query_status.add_argument(
        "code",
        metavar="CODE",
        choices=tuple(member.value for member in StatusCode),
        help="稳定状态代码",
    )

    query_recoverable = query_sub.add_parser("recoverable", parents=[rooted], help="可恢复回收项")

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


def _add_dry_run_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dry-run", action="store_true", help="只显示计划影响，不执行写入")
    parser.add_argument(
        "--limit", type=int, default=None, help="返回影响项数上限（默认 20，最大 100）"
    )
    parser.add_argument("--offset", type=int, default=None, help="跳过前 N 项")
    parser.add_argument("--count-only", action="store_true", help="只返回计数与分页元数据")


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


def _write_payload(result: OperationResult) -> dict[str, object]:
    """非回收写命令的正式成功载荷：result_type + 必要业务事实 + changed_paths。"""
    return {
        "changed_paths": [path.as_posix() for path in result.changed_paths],
    }


def _operation_payload(result: OperationResult) -> dict[str, object]:
    """restore 专用载荷：保留 operation_id 供后续关联。"""
    return {
        "operation_id": result.operation_id,
        "changed_paths": [path.as_posix() for path in result.changed_paths],
    }


def _trash_payload(result: TrashResult) -> dict[str, object]:
    return {
        "operation_id": result.operation_id,
        "restore_expires_at": _format_optional_datetime(result.restore_expires_at),
        "changed_paths": [path.as_posix() for path in result.changed_paths],
    }


def _emit(payload: dict[str, object], json_mode: bool, human: str) -> None:
    if json_mode:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(human)


def _check_path_prefix(value: str) -> Path:
    """校验 --path-prefix：必须是规范根内 POSIX 相对路径，绝对/..//空 都拒绝。"""
    posix = PurePosixPath(value)
    if not value or posix.is_absolute() or ".." in posix.parts:
        raise argparse.ArgumentTypeError(f"非法的根内路径前缀：{value!r}")
    return Path(*posix.parts)


def _render_check(report: CheckReport, args: argparse.Namespace, json_mode: bool) -> int:
    code_args = cast("list[str] | None", getattr(args, "code", None))
    codes = tuple(CheckCode(value) for value in (code_args or []))
    prefixes = tuple(getattr(args, "path_prefix", None) or [])
    limit = int(getattr(args, "limit", 20))
    offset = int(getattr(args, "offset", 0))
    if limit < 1 or limit > 100 or offset < 0:
        raise DraftError("check.invalid_pagination")
    count_only = bool(getattr(args, "count_only", False))
    slice_ = project_check(
        report,
        codes=codes,
        path_prefixes=prefixes,
        offset=offset,
        limit=limit,
        count_only=count_only,
    )
    payload = _check_slice_payload(slice_)
    if json_mode:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        groups = (slice_.errors, slice_.warnings, slice_.infos, slice_.reviews)
        if not any(groups) and slice_.ok:
            print("检查通过：未发现客观错误。")
        else:
            for issues in groups:
                for issue in issues:
                    print(render_issue(issue))
            # 人类摘要使用未过滤完整扫描计数，并包含过滤后总数（规格 §10）。
            severity = dict(slice_.severity_counts)
            print(
                f"检查完成：{severity['error']} 个错误，{severity['warning']} 个警告，"
                f"{severity['info']} 个信息，{severity['review']} 个提醒"
                f"（过滤后 {slice_.filtered_total} 项）。"
            )
    return 0 if slice_.ok else 1


def _check_slice_payload(slice_: CheckSlice) -> dict[str, object]:
    return {
        "ok": slice_.ok,
        "result_type": ResultType.CHECK.value,
        "severity_counts": dict(slice_.severity_counts),
        "code_counts": [[code, count] for code, count in slice_.code_counts],
        "filtered_total": slice_.filtered_total,
        "offset": slice_.offset,
        "limit": slice_.limit,
        "returned_count": slice_.returned_count,
        "has_more": slice_.has_more,
        "next_offset": slice_.next_offset,
        "errors": [issue_payload(issue) for issue in slice_.errors],
        "warnings": [issue_payload(issue) for issue in slice_.warnings],
        "infos": [issue_payload(issue) for issue in slice_.infos],
        "reviews": [issue_payload(issue) for issue in slice_.reviews],
    }


def _page_operand(root: Path, value: str) -> Path:
    from draft.paths import resolve_page_operand

    return resolve_page_operand(root, value).relative


def _collection_payload(
    items: Sequence[Mapping[str, object]],
    *,
    blocking_paths: Sequence[Path],
    complete: bool,
    limit: int,
    offset: int,
    count_only: bool,
) -> dict[str, object]:
    from draft.cli_results import collection_payload

    if limit < 1 or limit > 100 or offset < 0:
        raise DraftError("query.invalid_pagination")
    return collection_payload(
        items,
        blocking_paths=blocking_paths,
        complete=complete,
        offset=offset,
        limit=limit,
        count_only=count_only,
    )


def _query_root_state(root: Path) -> RootState:
    from draft.graph import read_marker

    return read_marker(root).root_state


def _raise_for_blocking(root: Path, view: TolerantView, root_state: RootState) -> None:
    """registered 根的全根查询发现阻碍时传播最具体的损坏；候选根返回以便 complete=false。"""
    if root_state is RootState.REGISTERED and view.first_blocking is not None:
        issue = view.first_blocking
        raise DraftError(
            issue.code,
            issue.path if issue.path is not None else root,
            details=issue.details,
            line=issue.line,
        )


def _dispatch_query(
    args: argparse.Namespace, *, root: Path, now: datetime | None = None
) -> tuple[dict[str, object], str, int]:
    limit = int(getattr(args, "limit", 20))
    offset = int(getattr(args, "offset", 0))
    count_only = bool(getattr(args, "count_only", False))
    if limit < 1 or limit > 100 or offset < 0:
        raise DraftError("query.invalid_pagination")
    q = cast(str, args.query_command)
    root_state = _query_root_state(root)

    if q == "page":
        from draft.diagnostic_codes import QueryCode
        from draft.query import PAGE_NOT_FOUND, load_tolerant_view, page_summary

        page = _page_operand(root, cast(str, args.page))
        view = load_tolerant_view(root)
        if page not in view.graph.pages:
            raise DraftError(PAGE_NOT_FOUND, page)
        if view.blocking_paths:
            _raise_for_blocking(root, view, root_state)
            paths = [path.as_posix() for path in view.blocking_paths]
            raise DraftError(
                QueryCode.INCOMPLETE.value,
                page,
                details=(str(len(paths)), *paths[:20]),
            )
        summary = page_summary(view.graph, page)
        return (
            {
                "result_type": ResultType.QUERY_PAGE.value,
                "complete": True,
                "blocking_paths": [],
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
        payload = _collection_payload(
            items,
            blocking_paths=(),
            complete=True,
            limit=limit,
            offset=offset,
            count_only=count_only,
        )
        return (
            {"result_type": ResultType.QUERY_LINKS.value, **payload},
            f"链接 {payload['total']} 项",
            0,
        )
    if q == "backlinks":
        from draft.query import backlinks, load_tolerant_view
        from draft.resources import resource_identity_from_operand

        target = resource_identity_from_operand(root, cast(str, args.target))
        view = load_tolerant_view(root)
        _raise_for_blocking(root, view, root_state)
        items = [{"page": path.as_posix()} for path in backlinks(view.graph, target)]
        payload = _collection_payload(
            items,
            blocking_paths=view.blocking_paths,
            complete=not view.blocking_paths,
            limit=limit,
            offset=offset,
            count_only=count_only,
        )
        return (
            {"result_type": ResultType.QUERY_BACKLINKS.value, **payload},
            f"反向链接 {payload['total']} 项",
            0,
        )
    if q == "relations":
        from draft.query import (
            PAGE_NOT_FOUND,
            incoming_relations,
            load_registered_page,
            load_tolerant_view,
            outgoing_relations,
        )

        page = _page_operand(root, cast(str, args.page))
        direction = cast(str, args.direction)
        if direction == "outgoing":
            snapshot = load_registered_page(root, page)
            view = _graph_view_for_snapshot(snapshot)
            relations = outgoing_relations(view, page)
            complete = True
            blocking: tuple[Path, ...] = ()
        else:
            tolerant = load_tolerant_view(root)
            _raise_for_blocking(root, tolerant, root_state)
            if page not in tolerant.graph.pages:
                raise DraftError(PAGE_NOT_FOUND, page)
            relations = incoming_relations(tolerant.graph, page)
            complete = not tolerant.blocking_paths
            blocking = tolerant.blocking_paths
        items = [
            {"relation": item.relation.value, "page": item.page.as_posix()} for item in relations
        ]
        payload = _collection_payload(
            items,
            blocking_paths=blocking,
            complete=complete,
            limit=limit,
            offset=offset,
            count_only=count_only,
        )
        return (
            {"result_type": ResultType.QUERY_RELATIONS.value, **payload},
            f"关系 {payload['total']} 项",
            0,
        )
    if q == "status":
        from draft.query import load_tolerant_view, pages_with_status
        from draft.vocabulary import StatusCode

        code = StatusCode(cast(str, args.code))
        view = load_tolerant_view(root)
        _raise_for_blocking(root, view, root_state)
        items = [{"page": path.as_posix()} for path in pages_with_status(view.graph, code)]
        payload = _collection_payload(
            items,
            blocking_paths=view.blocking_paths,
            complete=not view.blocking_paths,
            limit=limit,
            offset=offset,
            count_only=count_only,
        )
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
        payload = _collection_payload(
            items,
            blocking_paths=(),
            complete=True,
            limit=limit,
            offset=offset,
            count_only=count_only,
        )
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


def _is_dry_run(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "dry_run", False))


def _plan_effects_payload(
    operation: DryRunOperation,
    effects: Sequence[PlanEffect],
    args: argparse.Namespace,
) -> tuple[dict[str, object], str]:
    """把已构造计划的效果格式化为分页预演载荷；分页参数只与预演一起合法。"""
    limit = int(getattr(args, "limit", 20) or 20)
    offset = int(getattr(args, "offset", 0) or 0)
    count_only = bool(getattr(args, "count_only", False))
    if limit < 1 or limit > 100 or offset < 0:
        raise DraftError("query.invalid_pagination")
    from draft.cli_results import paginate, slice_payload

    items: list[dict[str, object]] = []
    for effect in effects:
        if effect.path is not None:
            items.append({"action": effect.action.value, "path": effect.path.as_posix()})
        else:
            assert effect.from_path is not None and effect.to_path is not None
            items.append(
                {
                    "action": effect.action.value,
                    "from": effect.from_path.as_posix(),
                    "to": effect.to_path.as_posix(),
                }
            )
    slice_ = paginate(tuple(items), offset=offset, limit=limit)
    payload = slice_payload(slice_, count_only=count_only, items_key="effects")  # type: ignore[arg-type]
    return (
        {
            "result_type": ResultType.PLAN.value,
            "operation": operation.value,
            **payload,
        },
        f"预演 {operation.value}：{len(effects)} 项影响",
    )


def _dry_run_write(
    args: argparse.Namespace,
    *,
    root: Path,
    operation: DryRunOperation,
    build: Callable[[OperationFacts], OperationPlan],
) -> tuple[dict[str, object], str, int]:
    """预演：与正式执行共享同一事实加载与计划构造边界，分页输出公开效果。"""
    from draft.operation_facts import load_operation_facts
    from draft.operations import read_lock
    from draft.paths import resolve_draft_root

    resolved = resolve_draft_root(root)
    if read_lock(resolved) is not None:
        from draft.diagnostic_codes import CheckCode

        raise DraftError(CheckCode.OPERATION_RECOVERY_REQUIRED.value, resolved)
    facts = load_operation_facts(resolved)
    plan = build(facts)
    payload, human = _plan_effects_payload(operation, plan.effects, args)
    return payload, human, 0


def _reject_pagination_without_dry_run(args: argparse.Namespace) -> None:
    """写命令的分页选项只与 --dry-run 一起合法；正式执行携带任一选项时退出码 2。"""
    if _is_dry_run(args):
        return
    explicit = (
        getattr(args, "limit", None) is not None
        or getattr(args, "offset", None) is not None
        or bool(getattr(args, "count_only", False))
    )
    if explicit:
        raise DraftError("cli.pagination_requires_dry_run")


def _dispatch(
    args: argparse.Namespace,
    *,
    root: Path | None = None,
    now: datetime | None = None,
) -> tuple[dict[str, object], str, int]:
    command = cast(str, args.command)
    if command == "init":
        from draft.lifecycle import initialize
        from draft.models import RootState

        root = Path(cast(str, args.root))
        root_state = RootState.CANDIDATE if bool(args.candidate) else RootState.REGISTERED
        initialize(root, root_state=root_state)
        return {"result_type": ResultType.INIT.value, "root": str(root)}, f"已初始化草稿：{root}", 0
    if command == "query":
        assert root is not None
        return _dispatch_query(args, root=root, now=now)
    assert root is not None
    if command == "adopt":
        from draft.lifecycle import adopt

        adopt(root)
        return {"result_type": ResultType.ADOPT.value, "root": str(root)}, f"已接管草稿：{root}", 0
    if command == "move":
        from draft.structure import move_entries, plan_move_from_arguments

        operands = [cast(str, value) for value in args.operands]
        if len(operands) < 2:
            raise DraftError(
                MOVE_OPERANDS_INVALID, details=("至少需要一个 SOURCE 和一个 TARGET",)
            )
        sources, target = operands[:-1], operands[-1]
        if target_kind_of(target) is TargetKind.FILE and len(sources) > 1:
            raise DraftError(
                MOVE_OPERANDS_INVALID, details=("文件目标只允许一个文件 SOURCE",)
            )
        if _is_dry_run(args):
            return _dry_run_write(
                args,
                root=root,
                operation=DryRunOperation.MOVE,
                build=lambda facts: plan_move_from_arguments(root, facts, sources, target),
            )
        _reject_pagination_without_dry_run(args)
        result = move_entries(root, sources, target)
        payload: dict[str, object] = {"result_type": ResultType.MOVE.value}
        payload.update(_write_payload(result))
        if len(sources) == 1:
            return payload, f"已移动 {sources[0]} → {target}", 0
        return payload, f"已移动 {len(sources)} 个来源 → {target}", 0
    if command == "promote":
        from draft.structure import plan_promote, promote_page

        page = _page_operand(root, cast(str, args.page))
        children = [_page_operand(root, child) for child in cast(list[str], args.children)]
        if _is_dry_run(args):
            return _dry_run_write(
                args,
                root=root,
                operation=DryRunOperation.PROMOTE,
                build=lambda facts: plan_promote(
                    facts.graph,
                    page,
                    tuple(children),
                    marker=facts.marker,
                    observations=facts.observations,
                ),
            )
        _reject_pagination_without_dry_run(args)
        result = promote_page(root, page, children)
        payload = {"result_type": ResultType.PROMOTE.value}
        payload.update(_write_payload(result))
        return payload, f"已升格 {page}（{len(children)} 个子页）", 0
    if command == "collapse":
        from draft.structure import collapse_hub, plan_collapse

        hub = _page_operand(root, cast(str, args.hub))
        if _is_dry_run(args):
            return _dry_run_write(
                args,
                root=root,
                operation=DryRunOperation.COLLAPSE,
                build=lambda facts: plan_collapse(
                    facts.graph,
                    hub,
                    marker=facts.marker,
                    observations=facts.observations,
                ),
            )
        _reject_pagination_without_dry_run(args)
        result = collapse_hub(root, hub)
        payload = {"result_type": ResultType.COLLAPSE.value}
        payload.update(_write_payload(result))
        return payload, f"已降级 {hub}", 0
    if command == "trash":
        from draft.trash import trash_page, trash_plan

        page = _page_operand(root, cast(str, args.page))
        if _is_dry_run(args):

            def build(facts: OperationFacts) -> OperationPlan:
                plan, _pkg = trash_plan(
                    facts.graph, page, root_state=facts.root_state, observations=facts.observations
                )
                return plan

            return _dry_run_write(args, root=root, operation=DryRunOperation.TRASH, build=build)
        _reject_pagination_without_dry_run(args)
        result = trash_page(root, page)
        payload: dict[str, object] = {"result_type": ResultType.TRASH.value}
        payload.update(_trash_payload(result))
        human = f"已回收 {page}（{result.phase.value}）"
        if result.warnings:
            human = f"{human}；警告：{_warning_text(result.warnings[0])}"
        return payload, human, 0
    if command == "relation":
        from draft.page_metadata import (
            add_relation,
            plan_add_relation,
            plan_remove_relation,
            remove_relation,
        )
        from draft.vocabulary import StatusCode

        source = _page_operand(root, cast(str, args.source))
        target = _page_operand(root, cast(str, args.target))
        code = EditableRelationCode(cast(str, args.code))
        sub = cast(str, args.relation_command)
        restored = getattr(args, "status", None)
        restored_status = StatusCode(cast(str, restored)) if restored is not None else None
        if _is_dry_run(args):
            if sub == "add":

                def build(facts: OperationFacts) -> OperationPlan:
                    return plan_add_relation(facts.graph.root, source, code, target)

                operation = DryRunOperation.RELATION_ADD
            else:

                def build(facts: OperationFacts) -> OperationPlan:
                    return plan_remove_relation(
                        facts.graph.root, source, code, target, restored_status=restored_status
                    )

                operation = DryRunOperation.RELATION_REMOVE
            return _dry_run_write(args, root=root, operation=operation, build=build)
        _reject_pagination_without_dry_run(args)
        if sub == "add":
            result = add_relation(root, source, code, target)
            result_type = ResultType.RELATION_ADD.value
        else:
            result = remove_relation(root, source, code, target, restored_status=restored_status)
            result_type = ResultType.RELATION_REMOVE.value
        payload: dict[str, object] = {
            "result_type": result_type,
            "source": source.as_posix(),
            "code": code.value,
            "target": target.as_posix(),
            "changed_paths": [path.as_posix() for path in result.changed_paths],
        }
        return payload, f"已{sub} {code.value} {source} → {target}", 0
    if command == "status":
        from draft.page_metadata import plan_set_status, set_status
        from draft.vocabulary import StatusCode

        page = _page_operand(root, cast(str, args.page))
        code = StatusCode(cast(str, args.code))
        note = getattr(args, "note", None)
        clear_note = bool(getattr(args, "clear_note", False))
        if _is_dry_run(args):

            def build(facts: OperationFacts) -> OperationPlan:
                return plan_set_status(
                    facts.graph.root, page, code, note=note, clear_note=clear_note
                )

            return _dry_run_write(
                args,
                root=root,
                operation=DryRunOperation.STATUS_SET,
                build=build,
            )
        _reject_pagination_without_dry_run(args)
        result = set_status(root, page, code, note=note, clear_note=clear_note)
        payload: dict[str, object] = {
            "result_type": ResultType.STATUS_SET.value,
            "path": page.as_posix(),
            "status": code.value,
            "changed_paths": [path.as_posix() for path in result.changed_paths],
        }
        return payload, f"已将 {page} 状态设为 {code.value}", 0
    if command == "create":
        from draft.page_create import create_page, plan_create_page
        from draft.vocabulary import StatusCode

        page = _page_operand(root, cast(str, args.page))
        title = cast(str, args.title)
        status = StatusCode(cast(str, args.status))
        if _is_dry_run(args):

            def build(facts: OperationFacts) -> OperationPlan:
                return plan_create_page(
                    facts.graph,
                    page,
                    title=title,
                    status=status,
                    root_state=facts.root_state,
                    observations=facts.observations,
                )

            return _dry_run_write(
                args,
                root=root,
                operation=DryRunOperation.CREATE,
                build=build,
            )
        _reject_pagination_without_dry_run(args)
        result = create_page(root, page, title=title, status=status)
        payload: dict[str, object] = {
            "result_type": ResultType.CREATE.value,
            "path": page.as_posix(),
            "status": status.value,
            "changed_paths": [path.as_posix() for path in result.changed_paths],
        }
        return payload, f"已创建 {page}", 0
    if command == "generate-conclusions":
        from draft.conclusions import generate_conclusions

        if _is_dry_run(args):
            from draft.cli_results import paginate, slice_payload
            from draft.conclusions import (
                CONCLUSIONS_PAGE,
                ConclusionsState,
                load_generation_graph,
                plan_generation,
            )
            from draft.graph import read_marker_snapshot
            from draft.operations import read_lock
            from draft.paths import resolve_draft_root

            resolved = resolve_draft_root(root)
            if read_lock(resolved) is not None:
                from draft.diagnostic_codes import CheckCode

                raise DraftError(CheckCode.OPERATION_RECOVERY_REQUIRED.value, resolved)
            # 预演与正式执行调用同一个门禁与计划构造器。
            from draft.query import load_tolerant_view

            marker = read_marker_snapshot(resolved)
            state = marker.state.conclusions_state
            view = load_tolerant_view(resolved, marker_state=marker.state)
            blocking = [
                path
                for path in view.blocking_paths
                if not (state is ConclusionsState.GENERATED and path == CONCLUSIONS_PAGE)
            ]
            if blocking:
                details = tuple(path.as_posix() for path in blocking)
                raise DraftError("conclusions.incomplete", resolved, details=details)
            graph = load_generation_graph(resolved, marker_state=marker.state)
            plan = plan_generation(graph, marker=marker)
            if plan.operation is None:
                # 无已确认页面或幂等无变化：成功返回空 effects 与业务事实。
                limit = int(getattr(args, "limit", 20) or 20)
                offset = int(getattr(args, "offset", 0) or 0)
                count_only = bool(getattr(args, "count_only", False))
                if limit < 1 or limit > 100 or offset < 0:
                    raise DraftError("query.invalid_pagination")
                slice_ = paginate((), offset=offset, limit=limit)
                payload = {
                    "result_type": ResultType.PLAN.value,
                    "operation": DryRunOperation.GENERATE_CONCLUSIONS.value,
                    "generated": plan.generated,
                    **slice_payload(slice_, count_only=count_only, items_key="effects"),
                }
                if plan.reason is not None:
                    payload["reason"] = plan.reason.value
                return payload, "预演 generate-conclusions：无变化影响", 0
            assert plan.operation is not None  # 上面已处理无计划分支。
            payload, human = _plan_effects_payload(
                DryRunOperation.GENERATE_CONCLUSIONS, plan.operation.effects, args
            )
            return payload, human, 0
        _reject_pagination_without_dry_run(args)
        result = generate_conclusions(root)
        payload: dict[str, object] = {
            "result_type": ResultType.GENERATE_CONCLUSIONS.value,
            "generated": result.generated,
        }
        if result.reason is not None:
            payload["reason"] = result.reason.value
        if result.operation is not None:
            from draft.plan_effects import public_changed_paths

            payload["changed_paths"] = [
                path.as_posix() for path in public_changed_paths(result.operation.effects)
            ]
        else:
            payload["changed_paths"] = []
        human = f"结论生成：{'已生成' if result.generated else '无已确认页面'}"
        return payload, human, 0
    if command == "restore":
        from draft.trash import restore_retained_trash

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

        results = recover_interrupted_operations(root)
        paths = sorted({path for result in results for path in result.changed_paths})
        payload = {
            "result_type": ResultType.RECOVER.value,
            "fixed_count": len(results),
            "paths": [path.as_posix() for path in paths],
        }
        return payload, f"恢复完成：{len(results)} 项结果", 0
    raise DraftError("cli.unknown_command")


def _exit_for_error(error: DraftError) -> int:
    if error.code.startswith("operation."):
        return _EXIT_OPERATION
    return _EXIT_DOMAIN


def _advance_for_command(root: Path, *, now: datetime | None = None) -> None:
    from draft.trash_lifecycle import prepare_managed_command

    prepare_managed_command(root, now=now)


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    get_cwd: Callable[[], Path] | None = None,
) -> int:
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
    env = os.environ if environ is None else environ
    cwd = Path(os.getcwd()) if get_cwd is None else get_cwd()

    # 只解析一次受管根：init 不用选择器（位置 ROOT）；其余命令统一经 select_managed_root
    # 定位并验证 marker，规范绝对根传给后续层。
    managed: ManagedRoot | None = None
    root: Path | None = None
    if command != "init":
        explicit = getattr(args, "root", None)
        probe = FilesystemProbe()
        try:
            managed = select_managed_root(
                explicit=explicit,
                environment_value=env.get("DRAFT_ROOT"),
                physical_cwd=cwd,
                probe=probe,
            )
            root = managed.path
        except DraftError as error:
            _emit_error(error, json_mode)
            return _exit_for_error(error)

    # check/adopt/init 不推进回收生命周期（规格 §8.1）；其余受管命令在任一受管根上
    # 于业务前推进恰好一次。
    if command not in ("check", "adopt") and managed is not None:
        try:
            _advance_for_command(managed.path, now=captured_now)
        except DraftError as error:
            _emit_error(error, json_mode)
            return _exit_for_error(error)

    if command == "check":
        from draft.checker import check_draft

        assert root is not None
        try:
            report = check_draft(root)
        except DraftError as error:
            _emit_error(error, json_mode)
            return _exit_for_error(error)
        try:
            code = _render_check(report, args, json_mode)
        except DraftError as error:
            _emit_error(error, json_mode)
            return _exit_for_error(error)
        return code
    try:
        payload, human, code = _dispatch(args, root=root, now=captured_now)
    except DraftError as error:
        _emit_error(error, json_mode)
        return _exit_for_error(error)
    except OSError as error:
        _emit_error(
            DraftError("runtime.os_error", details=(f"{type(error).__name__}: {error}",)),
            json_mode,
        )
        return _EXIT_RUNTIME
    # 成功 JSON 不重复 ok: true（规格 §14）；check 与错误信封各自显式携带 ok。
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
