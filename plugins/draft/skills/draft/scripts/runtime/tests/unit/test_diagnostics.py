from pathlib import Path

import pytest

from draft.diagnostics import KNOWN_CHECK_CODES, issue_payload, render_issue
from draft.models import CheckIssue, IssueSeverity

EXPECTED_CODES = {
    "candidate.already_registered",
    "candidate.internal_state",
    "format.missing",
    "format.invalid",
    "format.unsupported",
    "root.missing_file",
    "page.unmanaged_path",
    "page.missing_title",
    "page.missing_machine_block",
    "page.missing_status",
    "page.duplicate_status",
    "page.unknown_label",
    "page.invalid_status",
    "page.invalid_relation",
    "page.parse_error",
    "page.capacity",
    "link.missing",
    "link.escape",
    "path.symlink_escape",
    "relation.duplicate",
    "parent.root_declared",
    "parent.missing",
    "parent.multiple",
    "layout.parent_mismatch",
    "parent.cycle",
    "parent.unreachable",
    "parent.navigation_missing",
    "hub.empty",
    "dependency.self",
    "dependency.cycle",
    "replacement.pair_missing",
    "replacement.status_mismatch",
    "replacement.cycle",
    "challenge.pair_missing",
    "output.forbidden",
    "output.missing",
    "output.wrong_target",
    "output.backlink_missing",
    "operation.journal_corrupt",
    "operation.recovery_required",
    "operation.orphan_allocating",
    "operation.inconsistent_state",
    "trash.deadlines_invalid",
    "trash.package_missing",
    "trash.package_corrupt",
    "trash.retained",
    "trash.handoff_pending",
    "trash.pending_unexplained",
}

# 操作诊断用例的稳定路径与操作标识；仅用于缩短测试期望行，不参与被测逻辑。
_JOURNAL = ".draft/operations/abc/journal.json"
_OP_ID = "a" * 32
_RECOVERY_ID = "4" * 32


def test_catalog_covers_the_published_check_codes() -> None:
    assert KNOWN_CHECK_CODES == EXPECTED_CODES


def test_link_missing_is_a_complete_single_line() -> None:
    issue = CheckIssue(
        code="link.missing",
        severity=IssueSeverity.ERROR,
        path=Path("conclusions.md"),
        line=12,
        details=("pages/missing.md", "pages/missing.md"),
    )
    assert render_issue(issue) == (
        "conclusions.md:12: 错误：链接目标不存在：pages/missing.md"
    )


def test_link_missing_with_distinct_literal_and_normalized_target() -> None:
    issue = CheckIssue(
        code="link.missing",
        severity=IssueSeverity.ERROR,
        path=Path("conclusions.md"),
        line=12,
        details=("./missing.md", "pages/missing.md"),
    )
    assert render_issue(issue) == (
        "conclusions.md:12: 错误：./missing.md 解析到 pages/missing.md 后不存在"
    )


def test_file_location_omits_a_fake_line() -> None:
    issue = CheckIssue("parent.missing", IssueSeverity.ERROR, Path("pages/a.md"), None)
    assert render_issue(issue) == "pages/a.md: 错误：缺少“父主题”声明"


def test_root_location_renders_root_marker() -> None:
    issue = CheckIssue("future.code", IssueSeverity.WARNING, None, None, ("a\nb", "值"))
    assert render_issue(issue) == ('<ROOT>: 警告 future.code：未登记诊断参数：["a\\nb", "值"]')


def test_complex_issue_explicitly_points_to_json() -> None:
    issue = CheckIssue(
        "operation.inconsistent_state",
        IssueSeverity.ERROR,
        Path(".draft/operations/a/journal.json"),
        None,
        ("prepared",),
    )
    assert render_issue(issue).endswith("操作处于无锁不一致阶段：prepared；完整上下文请使用 --json")


def test_unknown_or_invalid_issue_fallback_is_one_line_and_lossless() -> None:
    issue = CheckIssue("future.code", IssueSeverity.WARNING, None, None, ("a\nb", "值"))
    assert render_issue(issue) == ('<ROOT>: 警告 future.code：未登记诊断参数：["a\\nb", "值"]')


def test_known_code_with_invalid_location_uses_the_same_fallback() -> None:
    issue = CheckIssue("link.missing", IssueSeverity.ERROR, Path("a.md"), None, ("x", "x"))
    assert "未登记诊断参数" in render_issue(issue)


def test_known_code_with_non_positive_line_does_not_render_a_fake_line() -> None:
    issue = CheckIssue("link.missing", IssueSeverity.ERROR, Path("a.md"), 0, ("x", "x"))
    rendered = render_issue(issue)
    assert "a.md:0" not in rendered
    assert "未登记诊断参数" in rendered


def test_issue_payload_adds_explicit_severity_and_keeps_raw_details() -> None:
    issue = CheckIssue(
        "page.capacity", IssueSeverity.REVIEW, Path("pages/a.md"), None, ("601", "70000")
    )
    assert issue_payload(issue) == {
        "code": "page.capacity",
        "severity": "review",
        "path": "pages/a.md",
        "line": None,
        "details": ["601", "70000"],
    }


def test_issue_payload_preserves_none_path_and_line() -> None:
    issue = CheckIssue("future.code", IssueSeverity.ERROR, None, None, ("a", "b"))
    assert issue_payload(issue) == {
        "code": "future.code",
        "severity": "error",
        "path": None,
        "line": None,
        "details": ["a", "b"],
    }


# 每条用例手写 CheckIssue、合法位置和预期完整中文事实；不引用 _SPECS 推导。
RENDER_CASES = [
    pytest.param(
        CheckIssue(
            "candidate.already_registered", IssueSeverity.ERROR, Path(".draft/draft.json"), None
        ),
        ".draft/draft.json: 错误："
        "目标已经登记，--candidate 仅适用于未登记候选",
        id="candidate.already_registered",
    ),
    pytest.param(
        CheckIssue("candidate.internal_state", IssueSeverity.ERROR, Path(".draft"), None),
        ".draft: 错误：候选目录包含 Draft 内部状态",
        id="candidate.internal_state",
    ),
    pytest.param(
        CheckIssue("format.missing", IssueSeverity.ERROR, Path(".draft/draft.json"), None),
        ".draft/draft.json: 错误：登记文件不存在",
        id="format.missing",
    ),
    pytest.param(
        CheckIssue("format.invalid", IssueSeverity.ERROR, Path(".draft/draft.json"), None),
        ".draft/draft.json: 错误：登记文件格式无效",
        id="format.invalid",
    ),
    pytest.param(
        CheckIssue("format.unsupported", IssueSeverity.ERROR, Path(".draft/draft.json"), None),
        ".draft/draft.json: 错误：登记文件版本不受支持",
        id="format.unsupported",
    ),
    pytest.param(
        CheckIssue("root.missing_file", IssueSeverity.ERROR, None, None, ("file", "index.md")),
        "<ROOT>: 错误：缺少 index.md",
        id="root.missing_file",
    ),
    pytest.param(
        CheckIssue("page.unmanaged_path", IssueSeverity.ERROR, Path("notes.md"), None),
        "notes.md: 错误：Markdown 文件不在受管理路径中",
        id="page.unmanaged_path",
    ),
    pytest.param(
        CheckIssue("page.missing_title", IssueSeverity.ERROR, Path("pages/a.md"), None),
        "pages/a.md: 错误：缺少一级标题",
        id="page.missing_title",
    ),
    pytest.param(
        CheckIssue("page.missing_machine_block", IssueSeverity.ERROR, Path("pages/a.md"), None),
        "pages/a.md: 错误：一级标题后缺少机器引用块",
        id="page.missing_machine_block",
    ),
    pytest.param(
        CheckIssue("page.missing_status", IssueSeverity.ERROR, Path("pages/a.md"), None),
        "pages/a.md: 错误：机器引用块缺少“状态”",
        id="page.missing_status",
    ),
    pytest.param(
        CheckIssue("page.duplicate_status", IssueSeverity.ERROR, Path("pages/a.md"), 5),
        "pages/a.md:5: 错误：重复声明“状态”",
        id="page.duplicate_status",
    ),
    pytest.param(
        CheckIssue("page.unknown_label", IssueSeverity.ERROR, Path("pages/a.md"), 7, ("状态依据",)),
        "pages/a.md:7: 错误：机器引用块包含未知标签：状态依据",
        id="page.unknown_label",
    ),
    pytest.param(
        CheckIssue(
            "page.invalid_status",
            IssueSeverity.ERROR,
            Path("pages/x.md"),
            3,
            ("not_single_code_span",),
        ),
        "pages/x.md:3: 错误：状态值不是单一反引号值",
        id="page.invalid_status.not_single_code_span",
    ),
    pytest.param(
        CheckIssue(
            "page.invalid_status",
            IssueSeverity.ERROR,
            Path("pages/x.md"),
            3,
            ("empty_explanation",),
        ),
        "pages/x.md:3: 错误：状态说明为空",
        id="page.invalid_status.empty_explanation",
    ),
    pytest.param(
        CheckIssue(
            "page.invalid_status",
            IssueSeverity.ERROR,
            Path("pages/x.md"),
            3,
            ("unknown_prefix", "进行中"),
        ),
        "pages/x.md:3: 错误：状态前缀无效：进行中",
        id="page.invalid_status.unknown_prefix",
    ),
    pytest.param(
        CheckIssue(
            "page.invalid_relation",
            IssueSeverity.ERROR,
            Path("pages/x.md"),
            5,
            ("link_cardinality_or_image", "依赖"),
        ),
        "pages/x.md:5: 错误：关系必须恰有一个非图片链接：依赖",
        id="page.invalid_relation.link_cardinality_or_image",
    ),
    pytest.param(
        CheckIssue(
            "page.invalid_relation",
            IssueSeverity.ERROR,
            Path("pages/x.md"),
            5,
            ("extra_content", "父主题"),
        ),
        "pages/x.md:5: 错误：关系链接外有额外内容：父主题",
        id="page.invalid_relation.extra_content",
    ),
    pytest.param(
        CheckIssue(
            "page.parse_error", IssueSeverity.ERROR, Path("pages/a.md"), 9, ("lazy_continuation",)
        ),
        "pages/a.md:9: 错误：机器区域出现懒惰延续行",
        id="page.parse_error.lazy_continuation",
    ),
    pytest.param(
        CheckIssue(
            "page.parse_error",
            IssueSeverity.ERROR,
            Path("pages/a.md"),
            9,
            ("source_alignment_failed", "missing.md"),
        ),
        "pages/a.md:9: 错误：无法在源码中定位链接：missing.md",
        id="page.parse_error.source_alignment_failed",
    ),
    pytest.param(
        CheckIssue(
            "page.parse_error",
            IssueSeverity.ERROR,
            Path("pages/a.md"),
            None,
            ("machine_block_map_missing",),
        ),
        "pages/a.md: 错误：机器区域缺少行映射",
        id="page.parse_error.machine_block_map_missing",
    ),
    pytest.param(
        CheckIssue(
            "page.parse_error",
            IssueSeverity.ERROR,
            Path("pages/a.md"),
            9,
            ("link_destination_missing",),
        ),
        "pages/a.md:9: 错误：链接缺少字符串目标",
        id="page.parse_error.link_destination_missing",
    ),
    pytest.param(
        CheckIssue(
            "page.parse_error",
            IssueSeverity.ERROR,
            Path("pages/a.md"),
            9,
            ("image_destination_missing",),
        ),
        "pages/a.md:9: 错误：图片缺少字符串目标",
        id="page.parse_error.image_destination_missing",
    ),
    pytest.param(
        CheckIssue(
            "page.capacity", IssueSeverity.REVIEW, Path("pages/a.md"), None, ("620", "70000")
        ),
        "pages/a.md: 提醒：页面容量为 620 行 / 70000 字节，建议审查是否拆分",
        id="page.capacity",
    ),
    pytest.param(
        CheckIssue(
            "link.missing",
            IssueSeverity.ERROR,
            Path("conclusions.md"),
            8,
            ("pages/a.md", "pages/a.md"),
        ),
        "conclusions.md:8: 错误：链接目标不存在：pages/a.md",
        id="link.missing.same",
    ),
    pytest.param(
        CheckIssue(
            "link.missing", IssueSeverity.ERROR, Path("conclusions.md"), 8, ("./a.md", "pages/a.md")
        ),
        "conclusions.md:8: 错误：./a.md 解析到 pages/a.md 后不存在",
        id="link.missing.distinct",
    ),
    pytest.param(
        CheckIssue(
            "link.escape",
            IssueSeverity.ERROR,
            Path("pages/a.md"),
            9,
            ("destination", "../../outside.md"),
        ),
        "pages/a.md:9: 错误：链接越出草稿根：../../outside.md",
        id="link.escape.destination",
    ),
    pytest.param(
        CheckIssue(
            "link.escape",
            IssueSeverity.ERROR,
            Path("pages/linked.md"),
            None,
            ("managed_path", "pages/linked.md"),
        ),
        "pages/linked.md: 错误：受管理路径是越界符号链接：pages/linked.md",
        id="link.escape.managed_path",
    ),
    pytest.param(
        CheckIssue("path.symlink_escape", IssueSeverity.ERROR, Path(".draft/operations/abc"), None),
        ".draft/operations/abc: 错误：路径包含越出受管理树的符号链接",
        id="path.symlink_escape",
    ),
    pytest.param(
        CheckIssue(
            "relation.duplicate",
            IssueSeverity.ERROR,
            Path("pages/question.md"),
            9,
            ("依赖", "pages/question.md"),
        ),
        "pages/question.md:9: 错误：依赖 重复指向 pages/question.md",
        id="relation.duplicate",
    ),
    pytest.param(
        CheckIssue("parent.root_declared", IssueSeverity.ERROR, Path("index.md"), 4),
        "index.md:4: 错误：根页面不得声明“父主题”",
        id="parent.root_declared",
    ),
    pytest.param(
        CheckIssue("parent.missing", IssueSeverity.ERROR, Path("pages/a.md"), None),
        "pages/a.md: 错误：缺少“父主题”声明",
        id="parent.missing",
    ),
    pytest.param(
        CheckIssue("parent.multiple", IssueSeverity.ERROR, Path("pages/a.md"), 5),
        "pages/a.md:5: 错误：声明了多个“父主题”",
        id="parent.multiple",
    ),
    pytest.param(
        CheckIssue(
            "layout.parent_mismatch",
            IssueSeverity.ERROR,
            Path("pages/sub/x.md"),
            4,
            ("index.md", "pages/sub/index.md"),
        ),
        "pages/sub/x.md:4: 错误："
        "父主题声明为 index.md，物理父主题应为 pages/sub/index.md",
        id="layout.parent_mismatch",
    ),
    pytest.param(
        CheckIssue(
            "parent.cycle",
            IssueSeverity.ERROR,
            Path("pages/a.md"),
            None,
            ("pages/a.md", "pages/b.md", "pages/a.md"),
        ),
        "pages/a.md: 错误：父主题形成环路：pages/a.md -> pages/b.md -> pages/a.md",
        id="parent.cycle",
    ),
    pytest.param(
        CheckIssue("parent.unreachable", IssueSeverity.ERROR, Path("pages/a.md"), None),
        "pages/a.md: 错误：父主题链无法到达根页面",
        id="parent.unreachable",
    ),
    pytest.param(
        CheckIssue(
            "parent.navigation_missing",
            IssueSeverity.ERROR,
            Path("index.md"),
            None,
            ("pages/a.md",),
        ),
        "index.md: 错误：正文缺少对子页面 pages/a.md 的导航链接",
        id="parent.navigation_missing",
    ),
    pytest.param(
        CheckIssue("hub.empty", IssueSeverity.ERROR, Path("pages/empty/index.md"), None),
        "pages/empty/index.md: 错误：非根枢纽没有子页面",
        id="hub.empty",
    ),
    pytest.param(
        CheckIssue("dependency.self", IssueSeverity.ERROR, Path("pages/a.md"), 7),
        "pages/a.md:7: 错误：页面不能依赖自己",
        id="dependency.self",
    ),
    pytest.param(
        CheckIssue(
            "dependency.cycle",
            IssueSeverity.ERROR,
            Path("pages/a.md"),
            None,
            ("pages/a.md", "pages/b.md", "pages/a.md"),
        ),
        "pages/a.md: 错误：依赖形成环路：pages/a.md -> pages/b.md -> pages/a.md",
        id="dependency.cycle",
    ),
    pytest.param(
        CheckIssue(
            "replacement.pair_missing",
            IssueSeverity.ERROR,
            Path("pages/new.md"),
            7,
            ("pages/question.md", "被取代"),
        ),
        "pages/new.md:7: 错误：pages/question.md 缺少对应的“被取代”关系",
        id="replacement.pair_missing",
    ),
    pytest.param(
        CheckIssue(
            "replacement.status_mismatch",
            IssueSeverity.ERROR,
            Path("pages/new.md"),
            7,
            ("relation_requires_status",),
        ),
        "pages/new.md:7: 错误：被取代关系要求已取代状态",
        id="replacement.status_mismatch.relation_requires_status",
    ),
    pytest.param(
        CheckIssue(
            "replacement.status_mismatch",
            IssueSeverity.ERROR,
            Path("pages/old.md"),
            None,
            ("status_requires_relation",),
        ),
        "pages/old.md: 错误：已取代状态要求被取代关系",
        id="replacement.status_mismatch.status_requires_relation",
    ),
    pytest.param(
        CheckIssue(
            "replacement.cycle",
            IssueSeverity.ERROR,
            Path("pages/a.md"),
            None,
            ("pages/a.md", "pages/b.md", "pages/a.md"),
        ),
        "pages/a.md: 错误：取代形成环路：pages/a.md -> pages/b.md -> pages/a.md",
        id="replacement.cycle",
    ),
    pytest.param(
        CheckIssue(
            "challenge.pair_missing",
            IssueSeverity.ERROR,
            Path("pages/challenger.md"),
            7,
            ("pages/question.md", "被质疑"),
        ),
        "pages/challenger.md:7: 错误："
        "pages/question.md 缺少对应的“被质疑”关系",
        id="challenge.pair_missing",
    ),
    pytest.param(
        CheckIssue("output.forbidden", IssueSeverity.ERROR, Path("pages/a.md"), 7),
        "pages/a.md:7: 错误：当前页面或状态不允许声明“产出”",
        id="output.forbidden",
    ),
    pytest.param(
        CheckIssue("output.missing", IssueSeverity.ERROR, Path("pages/a.md"), None),
        "pages/a.md: 错误：已确认页面缺少“产出”声明",
        id="output.missing",
    ),
    pytest.param(
        CheckIssue(
            "output.wrong_target", IssueSeverity.ERROR, Path("pages/a.md"), 7, ("duplicate",)
        ),
        "pages/a.md:7: 错误：重复产出",
        id="output.wrong_target.duplicate",
    ),
    pytest.param(
        CheckIssue(
            "output.wrong_target",
            IssueSeverity.ERROR,
            Path("pages/a.md"),
            7,
            ("not_conclusions", "../index.md"),
        ),
        "pages/a.md:7: 错误："
        "产出必须指向根级 conclusions.md，当前目标：../index.md",
        id="output.wrong_target.not_conclusions",
    ),
    pytest.param(
        CheckIssue(
            "output.backlink_missing",
            IssueSeverity.ERROR,
            Path("conclusions.md"),
            None,
            ("pages/a.md",),
        ),
        "conclusions.md: 错误：缺少对已确认页面 pages/a.md 的正文反向链接",
        id="output.backlink_missing",
    ),
    pytest.param(
        CheckIssue(
            "operation.journal_corrupt", IssueSeverity.ERROR, Path(".draft/operation.lock"), None
        ),
        ".draft/operation.lock: 错误：操作锁或日志损坏",
        id="operation.journal_corrupt",
    ),
    pytest.param(
        CheckIssue(
            "operation.recovery_required",
            IssueSeverity.ERROR,
            Path(".draft/operation.lock"),
            None,
            (_RECOVERY_ID,),
        ),
        f".draft/operation.lock: 错误：操作 {_RECOVERY_ID} 需要恢复",
        id="operation.recovery_required",
    ),
    pytest.param(
        CheckIssue(
            "operation.orphan_allocating",
            IssueSeverity.REVIEW,
            Path(_JOURNAL),
            None,
        ),
        f"{_JOURNAL}: 提醒：发现未持锁的 allocating 操作日志",
        id="operation.orphan_allocating",
    ),
    pytest.param(
        CheckIssue(
            "operation.inconsistent_state",
            IssueSeverity.ERROR,
            Path(_JOURNAL),
            None,
            ("prepared",),
        ),
        f"{_JOURNAL}: 错误：操作处于无锁不一致阶段：prepared；"
        "完整上下文请使用 --json",
        id="operation.inconsistent_state",
    ),
    pytest.param(
        CheckIssue(
            "trash.deadlines_invalid",
            IssueSeverity.ERROR,
            Path(_JOURNAL),
            None,
            (_OP_ID,),
        ),
        f"{_JOURNAL}: 错误：回收操作 {_OP_ID} 的期限字段无效",
        id="trash.deadlines_invalid",
    ),
    pytest.param(
        CheckIssue(
            "trash.package_missing",
            IssueSeverity.ERROR,
            Path(_JOURNAL),
            None,
            (_OP_ID,),
        ),
        f"{_JOURNAL}: 错误：回收操作 {_OP_ID} 的权威删除包不存在",
        id="trash.package_missing",
    ),
    pytest.param(
        CheckIssue(
            "trash.package_corrupt",
            IssueSeverity.ERROR,
            Path(_JOURNAL),
            None,
            (_OP_ID,),
        ),
        f"{_JOURNAL}: 错误：回收操作 {_OP_ID} 的权威删除包损坏",
        id="trash.package_corrupt",
    ),
    pytest.param(
        CheckIssue(
            "trash.retained",
            IssueSeverity.INFO,
            Path(_JOURNAL),
            None,
            (_OP_ID,),
        ),
        f"{_JOURNAL}: 信息：回收操作 {_OP_ID} 仍处于保留态",
        id="trash.retained",
    ),
    pytest.param(
        CheckIssue(
            "trash.handoff_pending",
            IssueSeverity.WARNING,
            Path(_JOURNAL),
            None,
            (_OP_ID,),
        ),
        f"{_JOURNAL}: 警告：回收操作 {_OP_ID} 的系统回收站移交等待重试",
        id="trash.handoff_pending",
    ),
    pytest.param(
        CheckIssue(
            "trash.pending_unexplained",
            IssueSeverity.ERROR,
            Path(_JOURNAL),
            None,
            (_OP_ID,),
        ),
        f"{_JOURNAL}: 错误：回收操作 {_OP_ID} "
        "处于无有效失败记录的待移交态",
        id="trash.pending_unexplained",
    ),
]


@pytest.mark.parametrize(("issue", "expected"), RENDER_CASES)
def test_render_issue_for_each_catalog_code(issue: CheckIssue, expected: str) -> None:
    assert render_issue(issue) == expected


CYCLE_FALLBACK_CASES = [
    pytest.param(
        CheckIssue(
            "parent.cycle",
            IssueSeverity.ERROR,
            Path("pages/a.md"),
            None,
            ("pages/a.md", "pages/b.md"),
        ),
        id="parent.cycle.unclosed",
    ),
    pytest.param(
        CheckIssue(
            "dependency.cycle",
            IssueSeverity.ERROR,
            Path("pages/a.md"),
            None,
            ("pages/a.md", "pages/b.md"),
        ),
        id="dependency.cycle.unclosed",
    ),
    pytest.param(
        CheckIssue(
            "replacement.cycle",
            IssueSeverity.ERROR,
            Path("pages/a.md"),
            None,
            ("pages/a.md", "pages/b.md"),
        ),
        id="replacement.cycle.unclosed",
    ),
]


@pytest.mark.parametrize(("issue",), CYCLE_FALLBACK_CASES)
def test_unclosed_cycle_details_fall_back_losslessly(issue: CheckIssue) -> None:
    rendered = render_issue(issue)
    assert "未登记诊断参数" in rendered
    assert rendered.endswith('：未登记诊断参数：["pages/a.md", "pages/b.md"]')


SELF_LOOP_CASES = [
    pytest.param(
        CheckIssue(
            "parent.cycle",
            IssueSeverity.ERROR,
            Path("pages/a.md"),
            None,
            ("pages/a.md", "pages/a.md"),
        ),
        "pages/a.md: 错误：父主题形成环路：pages/a.md -> pages/a.md",
        id="parent.cycle.self_loop",
    ),
    pytest.param(
        CheckIssue(
            "dependency.cycle",
            IssueSeverity.ERROR,
            Path("pages/a.md"),
            None,
            ("pages/a.md", "pages/a.md"),
        ),
        "pages/a.md: 错误：依赖形成环路：pages/a.md -> pages/a.md",
        id="dependency.cycle.self_loop",
    ),
    pytest.param(
        CheckIssue(
            "replacement.cycle",
            IssueSeverity.ERROR,
            Path("pages/a.md"),
            None,
            ("pages/a.md", "pages/a.md"),
        ),
        "pages/a.md: 错误：取代形成环路：pages/a.md -> pages/a.md",
        id="replacement.cycle.self_loop",
    ),
]


@pytest.mark.parametrize(("issue", "expected"), SELF_LOOP_CASES)
def test_render_issue_for_self_loop_cycles(issue: CheckIssue, expected: str) -> None:
    assert render_issue(issue) == expected


def test_path_with_control_character_stays_one_line() -> None:
    issue = CheckIssue("link.missing", IssueSeverity.ERROR, Path("bad\nname.md"), 5, ("x", "x"))
    rendered = render_issue(issue)
    assert rendered.count("\n") == 0
    assert "bad\\nname.md:5" in rendered


def test_code_with_control_character_stays_one_line() -> None:
    issue = CheckIssue("bad\ncode", IssueSeverity.ERROR, Path("a.md"), None, ("x",))
    rendered = render_issue(issue)
    assert rendered.count("\n") == 0
    assert "bad\\ncode" in rendered
