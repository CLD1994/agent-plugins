from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from draft.models import CheckIssue, IssueSeverity


class LocationKind(StrEnum):
    LINE = "line"
    FILE = "file"
    ROOT = "root"


DetailValidator = Callable[[tuple[str, ...]], bool]
MessageRenderer = Callable[[tuple[str, ...]], str]


@dataclass(frozen=True)
class DiagnosticSpec:
    locations: frozenset[LocationKind]
    valid_details: DetailValidator
    render_message: MessageRenderer


_SEVERITY_NAMES = {
    IssueSeverity.ERROR: "错误",
    IssueSeverity.WARNING: "警告",
    IssueSeverity.INFO: "信息",
    IssueSeverity.REVIEW: "提醒",
}


def _safe(value: str) -> str:
    """Escape control characters so a parameter never breaks the single-line boundary."""
    return json.dumps(value, ensure_ascii=False)[1:-1]


def _location(issue: CheckIssue) -> tuple[LocationKind, str]:
    if issue.path is None:
        return LocationKind.ROOT, "<ROOT>"
    path = issue.path.as_posix()
    # line <= 0 is not a legal one-based source line; never fabricate one.
    if issue.line is None or issue.line <= 0:
        return LocationKind.FILE, path
    return LocationKind.LINE, f"{path}:{issue.line}"


def _fixed(count: int) -> DetailValidator:
    return lambda details: len(details) == count


def _variant(arities: Mapping[str, int]) -> DetailValidator:
    return lambda details: bool(details) and arities.get(details[0]) == len(details)


def _cycle(details: tuple[str, ...]) -> bool:
    return len(details) >= 3 and details[0] == details[-1]


def _text(message: str) -> MessageRenderer:
    return lambda _details: message


def _render_invalid_status(details: tuple[str, ...]) -> str:
    branch = details[0]
    if branch == "not_single_code_span":
        return "状态值不是单一反引号值"
    if branch == "empty_explanation":
        return "状态说明为空"
    return f"状态前缀无效：{_safe(details[1])}"


def _render_invalid_relation(details: tuple[str, ...]) -> str:
    branch = details[0]
    label = _safe(details[1])
    if branch == "link_cardinality_or_image":
        return f"关系必须恰有一个非图片链接：{label}"
    return f"关系链接外有额外内容：{label}"


def _render_parse_error(details: tuple[str, ...]) -> str:
    branch = details[0]
    if branch == "link_destination_missing":
        return "链接缺少字符串目标"
    if branch == "image_destination_missing":
        return "图片缺少字符串目标"
    if branch == "source_alignment_failed":
        return f"无法在源码中定位链接：{_safe(details[1])}"
    if branch == "lazy_continuation":
        return "机器区域出现懒惰延续行"
    return "机器区域缺少行映射"


def _render_link_escape(details: tuple[str, ...]) -> str:
    branch = details[0]
    if branch == "destination":
        return f"链接越出草稿根：{_safe(details[1])}"
    return f"受管理路径是越界符号链接：{_safe(details[1])}"


def _render_status_mismatch(details: tuple[str, ...]) -> str:
    if details[0] == "relation_requires_status":
        return "被取代关系要求已取代状态"
    return "已取代状态要求被取代关系"


def _render_wrong_target(details: tuple[str, ...]) -> str:
    if details[0] == "duplicate":
        return "重复产出"
    return f"产出必须指向根级 conclusions.md，当前目标：{_safe(details[1])}"


def _render_link_missing(details: tuple[str, ...]) -> str:
    literal_destination, normalized_target = details
    if literal_destination == normalized_target:
        return f"链接目标不存在：{_safe(normalized_target)}"
    return f"{_safe(literal_destination)} 解析到 {_safe(normalized_target)} 后不存在"


def _render_cycle(prefix: str) -> MessageRenderer:
    def render(details: tuple[str, ...]) -> str:
        return f"{prefix}：{' -> '.join(_safe(path) for path in details)}"

    return render


_LINE = frozenset({LocationKind.LINE})
_FILE = frozenset({LocationKind.FILE})
_LINE_OR_FILE = frozenset({LocationKind.LINE, LocationKind.FILE})

_SPECS: dict[str, DiagnosticSpec] = {
    "candidate.already_registered": DiagnosticSpec(
        _FILE, _fixed(0), _text("目标已经登记，--candidate 仅适用于未登记候选")
    ),
    "candidate.internal_state": DiagnosticSpec(
        _FILE, _fixed(0), _text("候选目录包含 Draft 内部状态")
    ),
    "format.missing": DiagnosticSpec(_FILE, _fixed(0), _text("登记文件不存在")),
    "format.invalid": DiagnosticSpec(_FILE, _fixed(0), _text("登记文件格式无效")),
    "format.unsupported": DiagnosticSpec(_FILE, _fixed(0), _text("登记文件版本不受支持")),
    "root.missing_file": DiagnosticSpec(_FILE, _fixed(0), _text("必需文件或目录不存在")),
    "page.unmanaged_path": DiagnosticSpec(_FILE, _fixed(0), _text("Markdown 文件不在受管理路径中")),
    "page.missing_title": DiagnosticSpec(_FILE, _fixed(0), _text("缺少一级标题")),
    "page.missing_machine_block": DiagnosticSpec(
        _FILE, _fixed(0), _text("一级标题后缺少机器引用块")
    ),
    "page.missing_status": DiagnosticSpec(_FILE, _fixed(0), _text("机器引用块缺少“状态”")),
    "page.duplicate_status": DiagnosticSpec(_LINE, _fixed(0), _text("重复声明“状态”")),
    "page.unknown_label": DiagnosticSpec(
        _LINE, _fixed(1), lambda details: f"机器引用块包含未知标签：{_safe(details[0])}"
    ),
    "page.invalid_status": DiagnosticSpec(
        _LINE,
        _variant({"not_single_code_span": 1, "empty_explanation": 1, "unknown_prefix": 2}),
        _render_invalid_status,
    ),
    "page.invalid_relation": DiagnosticSpec(
        _LINE,
        _variant({"link_cardinality_or_image": 2, "extra_content": 2}),
        _render_invalid_relation,
    ),
    "page.parse_error": DiagnosticSpec(
        _LINE_OR_FILE,
        _variant(
            {
                "link_destination_missing": 1,
                "image_destination_missing": 1,
                "source_alignment_failed": 2,
                "lazy_continuation": 1,
                "machine_block_map_missing": 1,
            }
        ),
        _render_parse_error,
    ),
    "page.capacity": DiagnosticSpec(
        _FILE,
        _fixed(2),
        lambda details: (
            f"页面容量为 {_safe(details[0])} 行 / {_safe(details[1])} 字节，建议审查是否拆分"
        ),
    ),
    "link.missing": DiagnosticSpec(_LINE, _fixed(2), _render_link_missing),
    "link.escape": DiagnosticSpec(
        _LINE_OR_FILE,
        _variant({"destination": 2, "managed_path": 2}),
        _render_link_escape,
    ),
    "path.symlink_escape": DiagnosticSpec(
        _FILE, _fixed(0), _text("路径包含越出受管理树的符号链接")
    ),
    "relation.duplicate": DiagnosticSpec(
        _LINE, _fixed(2), lambda details: f"{_safe(details[0])} 重复指向 {_safe(details[1])}"
    ),
    "parent.root_declared": DiagnosticSpec(_LINE, _fixed(0), _text("根页面不得声明“父主题”")),
    "parent.missing": DiagnosticSpec(_FILE, _fixed(0), _text("缺少“父主题”声明")),
    "parent.multiple": DiagnosticSpec(_LINE, _fixed(0), _text("声明了多个“父主题”")),
    "layout.parent_mismatch": DiagnosticSpec(
        _LINE,
        _fixed(2),
        lambda details: f"父主题声明为 {_safe(details[0])}，物理父主题应为 {_safe(details[1])}",
    ),
    "parent.cycle": DiagnosticSpec(_FILE, _cycle, _render_cycle("父主题形成环路")),
    "parent.unreachable": DiagnosticSpec(_FILE, _fixed(0), _text("父主题链无法到达根页面")),
    "parent.navigation_missing": DiagnosticSpec(
        _FILE, _fixed(1), lambda details: f"正文缺少对子页面 {_safe(details[0])} 的导航链接"
    ),
    "hub.empty": DiagnosticSpec(_FILE, _fixed(0), _text("非根枢纽没有子页面")),
    "dependency.self": DiagnosticSpec(_LINE, _fixed(0), _text("页面不能依赖自己")),
    "dependency.cycle": DiagnosticSpec(_FILE, _cycle, _render_cycle("依赖形成环路")),
    "replacement.pair_missing": DiagnosticSpec(
        _LINE,
        _fixed(2),
        lambda details: f"{_safe(details[0])} 缺少对应的“{_safe(details[1])}”关系",
    ),
    "replacement.status_mismatch": DiagnosticSpec(
        _LINE_OR_FILE,
        _variant({"relation_requires_status": 1, "status_requires_relation": 1}),
        _render_status_mismatch,
    ),
    "replacement.cycle": DiagnosticSpec(_FILE, _cycle, _render_cycle("取代形成环路")),
    "challenge.pair_missing": DiagnosticSpec(
        _LINE,
        _fixed(2),
        lambda details: f"{_safe(details[0])} 缺少对应的“{_safe(details[1])}”关系",
    ),
    "output.forbidden": DiagnosticSpec(_LINE, _fixed(0), _text("当前页面或状态不允许声明“产出”")),
    "output.missing": DiagnosticSpec(_FILE, _fixed(0), _text("已确认页面缺少“产出”声明")),
    "output.wrong_target": DiagnosticSpec(
        _LINE,
        _variant({"duplicate": 1, "not_conclusions": 2}),
        _render_wrong_target,
    ),
    "output.backlink_missing": DiagnosticSpec(
        _FILE, _fixed(1), lambda details: f"缺少对已确认页面 {_safe(details[0])} 的正文反向链接"
    ),
    "operation.journal_corrupt": DiagnosticSpec(_FILE, _fixed(0), _text("操作锁或日志损坏")),
    "operation.recovery_required": DiagnosticSpec(
        _FILE, _fixed(1), lambda details: f"操作 {_safe(details[0])} 需要恢复"
    ),
    "operation.orphan_allocating": DiagnosticSpec(
        _FILE, _fixed(0), _text("发现未持锁的 allocating 操作日志")
    ),
    "operation.inconsistent_state": DiagnosticSpec(
        _FILE,
        _fixed(1),
        lambda details: f"操作处于无锁不一致阶段：{_safe(details[0])}；完整上下文请使用 --json",
    ),
    "trash.deadlines_invalid": DiagnosticSpec(
        _FILE, _fixed(1), lambda details: f"回收操作 {_safe(details[0])} 的期限字段无效"
    ),
    "trash.package_missing": DiagnosticSpec(
        _FILE, _fixed(1), lambda details: f"回收操作 {_safe(details[0])} 的权威删除包不存在"
    ),
    "trash.package_corrupt": DiagnosticSpec(
        _FILE, _fixed(1), lambda details: f"回收操作 {_safe(details[0])} 的权威删除包损坏"
    ),
    "trash.retained": DiagnosticSpec(
        _FILE, _fixed(1), lambda details: f"回收操作 {_safe(details[0])} 仍处于保留态"
    ),
    "trash.handoff_pending": DiagnosticSpec(
        _FILE, _fixed(1), lambda details: f"回收操作 {_safe(details[0])} 的系统回收站移交等待重试"
    ),
    "trash.pending_unexplained": DiagnosticSpec(
        _FILE,
        _fixed(1),
        lambda details: f"回收操作 {_safe(details[0])} 处于无有效失败记录的待移交态",
    ),
}

KNOWN_CHECK_CODES = frozenset(_SPECS)


def _fallback(details: tuple[str, ...]) -> str:
    encoded = json.dumps(list(details), ensure_ascii=False)
    return f"未登记诊断参数：{encoded}"


def render_issue(issue: CheckIssue) -> str:
    location_kind, location = _location(issue)
    spec = _SPECS.get(issue.code)
    valid = (
        spec is not None
        and location_kind in spec.locations
        and (issue.line is None or issue.line > 0)
        and spec.valid_details(issue.details)
    )
    message = (
        spec.render_message(issue.details)
        if valid and spec is not None
        else _fallback(issue.details)
    )
    return f"{location}: {_SEVERITY_NAMES[issue.severity]} {issue.code}：{message}"


def issue_payload(issue: CheckIssue) -> dict[str, object]:
    return {
        "code": issue.code,
        "severity": issue.severity.value,
        "path": None if issue.path is None else issue.path.as_posix(),
        "line": issue.line,
        "details": list(issue.details),
    }
