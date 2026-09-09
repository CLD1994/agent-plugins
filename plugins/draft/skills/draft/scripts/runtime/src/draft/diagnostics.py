from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from draft.diagnostic_codes import (
    HEAD_DIAGNOSTIC_CODES,
    CheckCode,
    FrontmatterReason,
    InvalidRelationReason,
    InvalidStatusReason,
    LinkEscapeKind,
    PageNoteReason,
    PageParentReason,
    PageStatusReason,
    ParseErrorReason,
    RelationDeclarationReason,
    ReplacementStatusReason,
    RootMissingKind,
)
from draft.errors import DraftError
from draft.models import CheckIssue, IssueSeverity
from draft.vocabulary import RelationCode, relation_label


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


def _relation_text(value: str) -> str:
    """稳定关系码渲染为当前中文标签；未知值原样保留（仍经转义）。"""
    try:
        return relation_label(RelationCode(value))
    except ValueError:
        return value


def _location(issue: CheckIssue) -> tuple[LocationKind, str]:
    if issue.path is None:
        return LocationKind.ROOT, "<ROOT>"
    # Escape the path so a control character in it cannot break the line boundary.
    path = _safe(issue.path.as_posix())
    # line <= 0 is not a legal one-based source line; never fabricate one.
    if issue.line is None or issue.line <= 0:
        return LocationKind.FILE, path
    return LocationKind.LINE, f"{path}:{issue.line}"


def _fixed(count: int) -> DetailValidator:
    return lambda details: len(details) == count


def _variant(arities: Mapping[str, int]) -> DetailValidator:
    return lambda details: bool(details) and arities.get(details[0]) == len(details)


def _reason_only(reason_type: type[StrEnum]) -> DetailValidator:
    allowed = frozenset(member.value for member in reason_type)
    return lambda details: len(details) == 1 and details[0] in allowed


def _frontmatter_details(details: tuple[str, ...]) -> bool:
    if not details or details[0] not in {member.value for member in FrontmatterReason}:
        return False
    if details[0] == FrontmatterReason.UNKNOWN_KEY.value:
        return len(details) in (1, 2)
    return len(details) == 1


def _cycle(details: tuple[str, ...]) -> bool:
    # A closed cycle is the path with the first node repeated at the end; a
    # self-loop is the shortest legal form (a, a), so require length >= 2.
    return len(details) >= 2 and details[0] == details[-1]


def _text(message: str) -> MessageRenderer:
    return lambda _details: message


def _render_missing_file(details: tuple[str, ...]) -> str:
    kind, name = details
    if kind == RootMissingKind.DIRECTORY.value:
        return f"缺少 {_safe(name)} 目录"
    return f"缺少 {_safe(name)}"


def _render_invalid_status(details: tuple[str, ...]) -> str:
    branch = details[0]
    if branch == InvalidStatusReason.NOT_SINGLE_CODE_SPAN.value:
        return "状态值不是单一反引号值"
    if branch == InvalidStatusReason.EMPTY_EXPLANATION.value:
        return "状态说明为空"
    return f"状态前缀无效：{_safe(details[1])}"


def _render_invalid_relation(details: tuple[str, ...]) -> str:
    branch = details[0]
    label = _safe(details[1])
    if branch == InvalidRelationReason.LINK_CARDINALITY_OR_IMAGE.value:
        return f"关系必须恰有一个非图片链接：{label}"
    return f"关系链接外有额外内容：{label}"


def _render_parse_error(details: tuple[str, ...]) -> str:
    branch = details[0]
    if branch == ParseErrorReason.LINK_DESTINATION_MISSING.value:
        return "链接缺少字符串目标"
    if branch == ParseErrorReason.IMAGE_DESTINATION_MISSING.value:
        return "图片缺少字符串目标"
    if branch == ParseErrorReason.SOURCE_ALIGNMENT_FAILED.value:
        return f"无法在源码中定位链接：{_safe(details[1])}"
    if branch == ParseErrorReason.LAZY_CONTINUATION.value:
        return "机器区域出现懒惰延续行"
    return "机器区域缺少行映射"


def _render_link_escape(details: tuple[str, ...]) -> str:
    branch = details[0]
    if branch == LinkEscapeKind.DESTINATION.value:
        return f"链接越出草稿根：{_safe(details[1])}"
    return f"受管理路径是越界符号链接：{_safe(details[1])}"


def _render_status_mismatch(details: tuple[str, ...]) -> str:
    if details[0] == ReplacementStatusReason.RELATION_REQUIRES_STATUS.value:
        return "被取代关系要求已取代状态"
    return "已取代状态要求被取代关系"


def _render_link_missing(details: tuple[str, ...]) -> str:
    literal_destination, normalized_target = details
    if literal_destination == normalized_target:
        return f"链接目标不存在：{_safe(normalized_target)}"
    return f"{_safe(literal_destination)} 解析到 {_safe(normalized_target)} 后不存在"


def _render_link_invalid(details: tuple[str, ...]) -> str:
    literal_destination, normalized_target = details
    return f"链接目标无效：{_safe(literal_destination)}（{_safe(normalized_target)}）"


def _render_link_unavailable(details: tuple[str, ...]) -> str:
    literal_destination, normalized_target = details
    return f"链接目标在当前平台不可用：{_safe(literal_destination)}（{_safe(normalized_target)}）"


def _render_cycle(prefix: str) -> MessageRenderer:
    def render(details: tuple[str, ...]) -> str:
        return f"{prefix}：{' -> '.join(_safe(path) for path in details)}"

    return render


def _render_frontmatter(details: tuple[str, ...]) -> str:
    reason = details[0]
    if reason == FrontmatterReason.MISSING.value:
        return "缺少 YAML frontmatter"
    if reason == FrontmatterReason.DELIMITER_INVALID.value:
        return "YAML frontmatter 分隔线无效"
    if reason == FrontmatterReason.YAML_INVALID.value:
        return "YAML frontmatter 语法无效"
    if reason == FrontmatterReason.NOT_MAPPING.value:
        return "YAML frontmatter 根值必须是映射"
    if reason == FrontmatterReason.DUPLICATE_KEY.value:
        return "YAML frontmatter 含重复键"
    if reason == FrontmatterReason.UNSUPPORTED_FEATURE.value:
        return "YAML frontmatter 使用了不支持的 YAML 特性"
    if reason == FrontmatterReason.UNKNOWN_KEY.value:
        if len(details) == 1:
            return "YAML frontmatter 包含未知键"
        return f"YAML frontmatter 包含未知键：{_safe(details[1])}"
    return "YAML frontmatter 字段类型无效"


def _render_page_status(details: tuple[str, ...]) -> str:
    reason = details[0]
    if reason == PageStatusReason.MISSING.value:
        return "缺少 status 字段"
    if reason == PageStatusReason.INVALID_TYPE.value:
        return "status 字段类型无效"
    return "status 字段值无效"


def _render_page_note(details: tuple[str, ...]) -> str:
    reason = details[0]
    if reason == PageNoteReason.EMPTY.value:
        return "note 不能为空"
    if reason == PageNoteReason.MULTILINE.value:
        return "note 不能包含换行"
    return "note 不能包含控制字符"


def _render_page_parent(details: tuple[str, ...]) -> str:
    reason = details[0]
    if reason == PageParentReason.UNEXPECTED.value:
        return "parent 字段出现位置无效"
    if reason == PageParentReason.MISSING.value:
        return "缺少 parent 字段"
    if reason == PageParentReason.INVALID_PATH.value:
        return "parent 字段路径无效"
    raise AssertionError(f"unregistered parent reason: {reason}")


def _render_relation_declaration(details: tuple[str, ...]) -> str:
    reason = details[0]
    if reason == RelationDeclarationReason.CONTAINER_EMPTY.value:
        return "relations 不能为空"
    if reason == RelationDeclarationReason.MEMBER_SHAPE.value:
        return "relations 成员形状无效"
    if reason == RelationDeclarationReason.TYPE_UNKNOWN.value:
        return "relations 成员的 type 无效"
    return "relations 成员的 target 路径无效"


_ROOT = frozenset({LocationKind.ROOT})
_LINE = frozenset({LocationKind.LINE})
_FILE = frozenset({LocationKind.FILE})
_LINE_OR_FILE = frozenset({LocationKind.LINE, LocationKind.FILE})

_SPECS: dict[CheckCode, DiagnosticSpec] = {
    CheckCode.INTERNAL_UNEXPECTED_ENTRY: DiagnosticSpec(
        _FILE, _fixed(0), _text("内部命名空间出现未登记条目")
    ),
    CheckCode.FORMAT_MISSING: DiagnosticSpec(_FILE, _fixed(0), _text("登记文件不存在")),
    CheckCode.FORMAT_INVALID: DiagnosticSpec(_FILE, _fixed(0), _text("登记文件格式无效")),
    CheckCode.FORMAT_UNSUPPORTED: DiagnosticSpec(_FILE, _fixed(0), _text("登记文件版本不受支持")),
    CheckCode.ROOT_MISSING_FILE: DiagnosticSpec(
        _ROOT,
        _variant({RootMissingKind.FILE.value: 2, RootMissingKind.DIRECTORY.value: 2}),
        _render_missing_file,
    ),
    CheckCode.PAGE_UNMANAGED_PATH: DiagnosticSpec(
        _FILE, _fixed(0), _text("Markdown 文件不在受管理路径中")
    ),
    CheckCode.PAGE_MISSING_TITLE: DiagnosticSpec(_FILE, _fixed(0), _text("缺少一级标题")),
    CheckCode.PAGE_MISSING_MACHINE_BLOCK: DiagnosticSpec(
        _FILE, _fixed(0), _text("一级标题后缺少机器引用块")
    ),
    CheckCode.PAGE_MISSING_STATUS: DiagnosticSpec(_FILE, _fixed(0), _text("机器引用块缺少“状态”")),
    CheckCode.PAGE_DUPLICATE_STATUS: DiagnosticSpec(_LINE, _fixed(0), _text("重复声明“状态”")),
    CheckCode.PAGE_UNKNOWN_LABEL: DiagnosticSpec(
        _LINE, _fixed(1), lambda details: f"机器引用块包含未知标签：{_safe(details[0])}"
    ),
    CheckCode.PAGE_INVALID_STATUS: DiagnosticSpec(
        _LINE,
        _variant(
            {
                InvalidStatusReason.NOT_SINGLE_CODE_SPAN.value: 1,
                InvalidStatusReason.EMPTY_EXPLANATION.value: 1,
                InvalidStatusReason.UNKNOWN_PREFIX.value: 2,
            }
        ),
        _render_invalid_status,
    ),
    CheckCode.PAGE_INVALID_RELATION: DiagnosticSpec(
        _LINE,
        _variant(
            {
                InvalidRelationReason.LINK_CARDINALITY_OR_IMAGE.value: 2,
                InvalidRelationReason.EXTRA_CONTENT.value: 2,
            }
        ),
        _render_invalid_relation,
    ),
    CheckCode.PAGE_PARSE_ERROR: DiagnosticSpec(
        _LINE_OR_FILE,
        _variant(
            {
                ParseErrorReason.LINK_DESTINATION_MISSING.value: 1,
                ParseErrorReason.IMAGE_DESTINATION_MISSING.value: 1,
                ParseErrorReason.SOURCE_ALIGNMENT_FAILED.value: 2,
                ParseErrorReason.LAZY_CONTINUATION.value: 1,
                ParseErrorReason.MACHINE_BLOCK_MAP_MISSING.value: 1,
            }
        ),
        _render_parse_error,
    ),
    CheckCode.PAGE_CAPACITY: DiagnosticSpec(
        _FILE,
        _fixed(2),
        lambda details: (
            f"页面容量为 {_safe(details[0])} 行 / {_safe(details[1])} 字节，建议审查是否拆分"
        ),
    ),
    CheckCode.PAGE_FRONTMATTER_INVALID: DiagnosticSpec(
        _LINE_OR_FILE, _frontmatter_details, _render_frontmatter
    ),
    CheckCode.PAGE_STATUS_INVALID: DiagnosticSpec(
        _LINE_OR_FILE, _reason_only(PageStatusReason), _render_page_status
    ),
    CheckCode.PAGE_NOTE_INVALID: DiagnosticSpec(
        _LINE_OR_FILE, _reason_only(PageNoteReason), _render_page_note
    ),
    CheckCode.PAGE_PARENT_INVALID: DiagnosticSpec(
        _LINE_OR_FILE, _reason_only(PageParentReason), _render_page_parent
    ),
    CheckCode.RELATION_DECLARATION_INVALID: DiagnosticSpec(
        _LINE_OR_FILE, _reason_only(RelationDeclarationReason), _render_relation_declaration
    ),
    CheckCode.LINK_MISSING: DiagnosticSpec(_LINE, _fixed(2), _render_link_missing),
    CheckCode.LINK_INVALID: DiagnosticSpec(_LINE, _fixed(2), _render_link_invalid),
    CheckCode.LINK_UNAVAILABLE: DiagnosticSpec(_LINE, _fixed(2), _render_link_unavailable),
    CheckCode.LINK_ESCAPE: DiagnosticSpec(
        _LINE_OR_FILE,
        _variant({LinkEscapeKind.DESTINATION.value: 2, LinkEscapeKind.MANAGED_PATH.value: 2}),
        _render_link_escape,
    ),
    CheckCode.PATH_SYMLINK_ESCAPE: DiagnosticSpec(
        _FILE, _fixed(0), _text("路径包含越出受管理树的符号链接")
    ),
    CheckCode.PATH_INTERNAL: DiagnosticSpec(_FILE, _fixed(0), _text("操作数指向内部协议领地")),
    CheckCode.RELATION_DUPLICATE: DiagnosticSpec(
        _LINE,
        _fixed(2),
        lambda details: f"{_safe(_relation_text(details[0]))} 重复指向 {_safe(details[1])}",
    ),
    CheckCode.PARENT_ROOT_DECLARED: DiagnosticSpec(
        _LINE, _fixed(0), _text("根页面不得声明“父主题”")
    ),
    CheckCode.PARENT_MISSING: DiagnosticSpec(_FILE, _fixed(0), _text("缺少“父主题”声明")),
    CheckCode.PARENT_MULTIPLE: DiagnosticSpec(_LINE, _fixed(0), _text("声明了多个“父主题”")),
    CheckCode.LAYOUT_PARENT_MISMATCH: DiagnosticSpec(
        _LINE,
        _fixed(2),
        lambda details: f"父主题声明为 {_safe(details[0])}，物理父主题应为 {_safe(details[1])}",
    ),
    CheckCode.PARENT_CYCLE: DiagnosticSpec(_FILE, _cycle, _render_cycle("父主题形成环路")),
    CheckCode.PARENT_UNREACHABLE: DiagnosticSpec(_FILE, _fixed(0), _text("父主题链无法到达根页面")),
    CheckCode.PARENT_NAVIGATION_MISSING: DiagnosticSpec(
        _FILE, _fixed(1), lambda details: f"正文缺少对子页面 {_safe(details[0])} 的导航链接"
    ),
    CheckCode.HUB_EMPTY: DiagnosticSpec(_FILE, _fixed(0), _text("非根枢纽没有子页面")),
    CheckCode.DEPENDENCY_SELF: DiagnosticSpec(_LINE, _fixed(0), _text("页面不能依赖自己")),
    CheckCode.DEPENDENCY_CYCLE: DiagnosticSpec(_FILE, _cycle, _render_cycle("依赖形成环路")),
    CheckCode.REPLACEMENT_PAIR_MISSING: DiagnosticSpec(
        _LINE,
        _fixed(2),
        lambda details: f"{_safe(details[0])} 缺少对应的“{_safe(_relation_text(details[1]))}”关系",
    ),
    CheckCode.REPLACEMENT_STATUS_MISMATCH: DiagnosticSpec(
        _LINE_OR_FILE,
        _variant(
            {
                ReplacementStatusReason.RELATION_REQUIRES_STATUS.value: 1,
                ReplacementStatusReason.STATUS_REQUIRES_RELATION.value: 1,
            }
        ),
        _render_status_mismatch,
    ),
    CheckCode.REPLACEMENT_CYCLE: DiagnosticSpec(_FILE, _cycle, _render_cycle("取代形成环路")),
    CheckCode.CHALLENGE_PAIR_MISSING: DiagnosticSpec(
        _LINE,
        _fixed(2),
        lambda details: f"{_safe(details[0])} 缺少对应的“{_safe(_relation_text(details[1]))}”关系",
    ),
    CheckCode.OPERATION_JOURNAL_CORRUPT: DiagnosticSpec(
        _FILE, _fixed(0), _text("操作锁或日志损坏")
    ),
    CheckCode.OPERATION_JOURNAL_VERSION_UNSUPPORTED: DiagnosticSpec(
        _FILE,
        _fixed(2),
        lambda details: (
            f"journal 版本 {_safe(details[0])} 不受支持，仅接受版本 {_safe(details[1])}"
        ),
    ),
    CheckCode.OPERATION_RECOVERY_REQUIRED: DiagnosticSpec(
        _FILE, _fixed(1), lambda details: f"操作 {_safe(details[0])} 需要恢复"
    ),
    CheckCode.OPERATION_ORPHAN_ALLOCATING: DiagnosticSpec(
        _FILE, _fixed(0), _text("发现未持锁的 allocating 操作日志")
    ),
    CheckCode.OPERATION_INCONSISTENT_STATE: DiagnosticSpec(
        _FILE,
        _fixed(1),
        lambda details: f"操作处于无锁不一致阶段：{_safe(details[0])}；完整上下文请使用 --json",
    ),
    CheckCode.OPERATION_DURABILITY_FAILED: DiagnosticSpec(
        _FILE,
        _fixed(1),
        lambda details: (
            f"持久状态同步失败，未确认耐久提交：{_safe(details[0])}；保留锁与现场，请勿继续删除或释放"
        ),
    ),
    CheckCode.TRASH_DEADLINES_INVALID: DiagnosticSpec(
        _FILE, _fixed(1), lambda details: f"回收操作 {_safe(details[0])} 的期限字段无效"
    ),
    CheckCode.TRASH_PACKAGE_MISSING: DiagnosticSpec(
        _FILE, _fixed(1), lambda details: f"回收操作 {_safe(details[0])} 的权威删除包不存在"
    ),
    CheckCode.TRASH_PACKAGE_CORRUPT: DiagnosticSpec(
        _FILE, _fixed(1), lambda details: f"回收操作 {_safe(details[0])} 的权威删除包损坏"
    ),
    CheckCode.TRASH_RETAINED: DiagnosticSpec(
        _FILE, _fixed(1), lambda details: f"回收操作 {_safe(details[0])} 仍处于保留态"
    ),
    CheckCode.TRASH_HANDOFF_PENDING: DiagnosticSpec(
        _FILE, _fixed(1), lambda details: f"回收操作 {_safe(details[0])} 的系统回收站移交等待重试"
    ),
    CheckCode.TRASH_PENDING_UNEXPLAINED: DiagnosticSpec(
        _FILE,
        _fixed(1),
        lambda details: f"回收操作 {_safe(details[0])} 处于无有效失败记录的待移交态",
    ),
    CheckCode.CONCLUSIONS_UNEXPECTED_FILE: DiagnosticSpec(
        _FILE,
        _fixed(0),
        _text("所有权为未生成时意外出现 conclusions.md，不能被自动覆盖"),
    ),
    CheckCode.CONCLUSIONS_SHAPE_INVALID: DiagnosticSpec(
        _FILE,
        _fixed(0),
        _text("结论页固定机器形状损坏，重新运行 generate-conclusions 修复"),
    ),
    CheckCode.CONCLUSIONS_NAVIGATION_INVALID: DiagnosticSpec(
        _FILE,
        _fixed(0),
        _text("根导航结论链接缺失、重复、位置或目标错误"),
    ),
}

KNOWN_CHECK_CODES: frozenset[str] = frozenset(code.value for code in _SPECS)
_HEAD_DIAGNOSTIC_VALUES: frozenset[str] = frozenset(code.value for code in HEAD_DIAGNOSTIC_CODES)


def is_head_diagnostic(code: str) -> bool:
    """Return whether ``code`` belongs to the YAML page-header diagnostic owner."""
    return code in _HEAD_DIAGNOSTIC_VALUES


def _fallback(details: tuple[str, ...]) -> str:
    encoded = json.dumps(list(details), ensure_ascii=False)
    return f"未登记诊断参数：{encoded}"


def _spec_for(issue: CheckIssue) -> DiagnosticSpec | None:
    try:
        registered_code = CheckCode(issue.code)
    except ValueError:
        return None
    return _SPECS.get(registered_code)


def _is_valid(issue: CheckIssue, spec: DiagnosticSpec | None) -> bool:
    if spec is None:
        return False
    try:
        registered_code = CheckCode(issue.code)
    except ValueError:
        return False
    if registered_code in HEAD_DIAGNOSTIC_CODES and issue.severity is not IssueSeverity.ERROR:
        return False
    location_kind, _location_text = _location(issue)
    return (
        location_kind in spec.locations
        and (issue.line is None or issue.line > 0)
        and spec.valid_details(issue.details)
    )


def _message_for(issue: CheckIssue, spec: DiagnosticSpec | None, valid: bool) -> str:
    if valid and spec is not None:
        return spec.render_message(issue.details)
    # The five page-header codes are public protocol values.  Invalid internal details must not
    # turn their untrusted tuple into a second public schema or leak a raw YAML value.
    if is_head_diagnostic(issue.code):
        return "页面头部诊断事实无效"
    return _fallback(issue.details)


def render_issue(issue: CheckIssue) -> str:
    _location_kind, location = _location(issue)
    spec = _spec_for(issue)
    valid = _is_valid(issue, spec)
    message = _message_for(issue, spec, valid)
    if valid:
        # 已登记错误码只展示人类可读事实，不重复错误码；未知/未登记码在兜底分支保留。
        return f"{location}: {_SEVERITY_NAMES[issue.severity]}：{message}"
    # Escape the code too: every dynamic field must be single-line safe.
    return f"{location}: {_SEVERITY_NAMES[issue.severity]} {_safe(issue.code)}：{message}"


def issue_payload(issue: CheckIssue) -> dict[str, object]:
    if not is_head_diagnostic(issue.code):
        return {
            "code": issue.code,
            "severity": issue.severity.value,
            "path": None if issue.path is None else issue.path.as_posix(),
            "line": issue.line,
            "details": list(issue.details),
        }

    spec = _spec_for(issue)
    valid = _is_valid(issue, spec)
    payload: dict[str, object] = {
        "code": issue.code,
        "severity": IssueSeverity.ERROR.value,
        "message": _message_for(issue, spec, valid),
    }
    if issue.path is not None:
        payload["path"] = issue.path.as_posix()
    if issue.line is not None and issue.line > 0:
        payload["line"] = issue.line
    if valid and issue.details:
        payload["details"] = {"reason": issue.details[0]}
    return payload


def error_payload(error: DraftError) -> dict[str, object] | None:
    """Project a page-header ``DraftError`` to the public error object, if applicable."""
    if not is_head_diagnostic(error.code):
        return None
    path = error.paths[0] if len(error.paths) == 1 else None
    issue = CheckIssue(
        code=error.code,
        severity=IssueSeverity.ERROR,
        path=path,
        line=error.line,
        details=error.details,
    )
    projected = issue_payload(issue)
    projected.pop("severity", None)
    return projected
