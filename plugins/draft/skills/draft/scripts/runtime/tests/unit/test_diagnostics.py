import json
from dataclasses import dataclass
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

# 操作诊断用例的稳定路径与操作标识；仅用于缩短测试输入，不参与被测逻辑。
_JOURNAL = ".draft/operations/abc/journal.json"
_OP_ID = "a" * 32
_RECOVERY_ID = "4" * 32


@dataclass(frozen=True)
class RenderCase:
    issue: CheckIssue
    case_id: str
    visible_detail_indexes: tuple[int, ...] = ()


def _escaped_fact(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)[1:-1]


def _assert_single_line(rendered: str) -> None:
    assert rendered
    assert rendered.splitlines() == [rendered]


def _assert_valid_render(case: RenderCase) -> str:
    issue = case.issue
    rendered = render_issue(issue)
    _assert_single_line(rendered)
    assert issue.code not in rendered
    if issue.path is None:
        assert "<ROOT>" in rendered
    else:
        assert _escaped_fact(issue.path.as_posix()) in rendered
    if issue.line is not None and issue.line > 0:
        assert str(issue.line) in rendered
    for index in case.visible_detail_indexes:
        assert _escaped_fact(issue.details[index]) in rendered
    return rendered


def test_catalog_covers_the_published_check_codes() -> None:
    assert KNOWN_CHECK_CODES == EXPECTED_CODES


# 每条用例手写合法 CheckIssue 与需要展示的动态详情索引；期望全部来自结构化输入，
# 不复制具体人类可读文案。
VALID_RENDER_CASES = [
    RenderCase(
        CheckIssue(
            "candidate.already_registered", IssueSeverity.ERROR, Path(".draft/draft.json"), None
        ),
        "candidate.already_registered",
    ),
    RenderCase(
        CheckIssue("candidate.internal_state", IssueSeverity.ERROR, Path(".draft"), None),
        "candidate.internal_state",
    ),
    RenderCase(
        CheckIssue("format.missing", IssueSeverity.ERROR, Path(".draft/draft.json"), None),
        "format.missing",
    ),
    RenderCase(
        CheckIssue("format.invalid", IssueSeverity.ERROR, Path(".draft/draft.json"), None),
        "format.invalid",
    ),
    RenderCase(
        CheckIssue("format.unsupported", IssueSeverity.ERROR, Path(".draft/draft.json"), None),
        "format.unsupported",
    ),
    RenderCase(
        CheckIssue("root.missing_file", IssueSeverity.ERROR, None, None, ("file", "index.md")),
        "root.missing_file.file",
        (1,),
    ),
    RenderCase(
        CheckIssue("root.missing_file", IssueSeverity.ERROR, None, None, ("directory", "pages")),
        "root.missing_file.directory",
        (1,),
    ),
    RenderCase(
        CheckIssue("page.unmanaged_path", IssueSeverity.ERROR, Path("notes.md"), None),
        "page.unmanaged_path",
    ),
    RenderCase(
        CheckIssue("page.missing_title", IssueSeverity.ERROR, Path("pages/a.md"), None),
        "page.missing_title",
    ),
    RenderCase(
        CheckIssue("page.missing_machine_block", IssueSeverity.ERROR, Path("pages/a.md"), None),
        "page.missing_machine_block",
    ),
    RenderCase(
        CheckIssue("page.missing_status", IssueSeverity.ERROR, Path("pages/a.md"), None),
        "page.missing_status",
    ),
    RenderCase(
        CheckIssue("page.duplicate_status", IssueSeverity.ERROR, Path("pages/a.md"), 5),
        "page.duplicate_status",
    ),
    RenderCase(
        CheckIssue("page.unknown_label", IssueSeverity.ERROR, Path("pages/a.md"), 7, ("状态依据",)),
        "page.unknown_label",
        (0,),
    ),
    RenderCase(
        CheckIssue(
            "page.invalid_status",
            IssueSeverity.ERROR,
            Path("pages/x.md"),
            3,
            ("not_single_code_span",),
        ),
        "page.invalid_status.not_single_code_span",
    ),
    RenderCase(
        CheckIssue(
            "page.invalid_status",
            IssueSeverity.ERROR,
            Path("pages/x.md"),
            3,
            ("empty_explanation",),
        ),
        "page.invalid_status.empty_explanation",
    ),
    RenderCase(
        CheckIssue(
            "page.invalid_status",
            IssueSeverity.ERROR,
            Path("pages/x.md"),
            3,
            ("unknown_prefix", "进行中"),
        ),
        "page.invalid_status.unknown_prefix",
        (1,),
    ),
    RenderCase(
        CheckIssue(
            "page.invalid_relation",
            IssueSeverity.ERROR,
            Path("pages/x.md"),
            5,
            ("link_cardinality_or_image", "依赖"),
        ),
        "page.invalid_relation.link_cardinality_or_image",
        (1,),
    ),
    RenderCase(
        CheckIssue(
            "page.invalid_relation",
            IssueSeverity.ERROR,
            Path("pages/x.md"),
            5,
            ("extra_content", "父主题"),
        ),
        "page.invalid_relation.extra_content",
        (1,),
    ),
    RenderCase(
        CheckIssue(
            "page.parse_error", IssueSeverity.ERROR, Path("pages/a.md"), 9, ("lazy_continuation",)
        ),
        "page.parse_error.lazy_continuation",
    ),
    RenderCase(
        CheckIssue(
            "page.parse_error",
            IssueSeverity.ERROR,
            Path("pages/a.md"),
            9,
            ("source_alignment_failed", "missing.md"),
        ),
        "page.parse_error.source_alignment_failed",
        (1,),
    ),
    RenderCase(
        CheckIssue(
            "page.parse_error",
            IssueSeverity.ERROR,
            Path("pages/a.md"),
            None,
            ("machine_block_map_missing",),
        ),
        "page.parse_error.machine_block_map_missing",
    ),
    RenderCase(
        CheckIssue(
            "page.parse_error",
            IssueSeverity.ERROR,
            Path("pages/a.md"),
            9,
            ("link_destination_missing",),
        ),
        "page.parse_error.link_destination_missing",
    ),
    RenderCase(
        CheckIssue(
            "page.parse_error",
            IssueSeverity.ERROR,
            Path("pages/a.md"),
            9,
            ("image_destination_missing",),
        ),
        "page.parse_error.image_destination_missing",
    ),
    RenderCase(
        CheckIssue(
            "page.capacity", IssueSeverity.REVIEW, Path("pages/a.md"), None, ("620", "70000")
        ),
        "page.capacity",
        (0, 1),
    ),
    RenderCase(
        CheckIssue(
            "link.missing",
            IssueSeverity.ERROR,
            Path("conclusions.md"),
            8,
            ("pages/a.md", "pages/a.md"),
        ),
        "link.missing.same",
        (1,),
    ),
    RenderCase(
        CheckIssue(
            "link.missing", IssueSeverity.ERROR, Path("conclusions.md"), 8, ("./a.md", "pages/a.md")
        ),
        "link.missing.distinct",
        (0, 1),
    ),
    RenderCase(
        CheckIssue(
            "link.escape",
            IssueSeverity.ERROR,
            Path("pages/a.md"),
            9,
            ("destination", "../../outside.md"),
        ),
        "link.escape.destination",
        (1,),
    ),
    RenderCase(
        CheckIssue(
            "link.escape",
            IssueSeverity.ERROR,
            Path("pages/linked.md"),
            None,
            ("managed_path", "pages/linked.md"),
        ),
        "link.escape.managed_path",
        (1,),
    ),
    RenderCase(
        CheckIssue("path.symlink_escape", IssueSeverity.ERROR, Path(".draft/operations/abc"), None),
        "path.symlink_escape",
    ),
    RenderCase(
        CheckIssue(
            "relation.duplicate",
            IssueSeverity.ERROR,
            Path("pages/question.md"),
            9,
            ("依赖", "pages/question.md"),
        ),
        "relation.duplicate",
        (0, 1),
    ),
    RenderCase(
        CheckIssue("parent.root_declared", IssueSeverity.ERROR, Path("index.md"), 4),
        "parent.root_declared",
    ),
    RenderCase(
        CheckIssue("parent.missing", IssueSeverity.ERROR, Path("pages/a.md"), None),
        "parent.missing",
    ),
    RenderCase(
        CheckIssue("parent.multiple", IssueSeverity.ERROR, Path("pages/a.md"), 5),
        "parent.multiple",
    ),
    RenderCase(
        CheckIssue(
            "layout.parent_mismatch",
            IssueSeverity.ERROR,
            Path("pages/sub/x.md"),
            4,
            ("index.md", "pages/sub/index.md"),
        ),
        "layout.parent_mismatch",
        (0, 1),
    ),
    RenderCase(
        CheckIssue(
            "parent.cycle",
            IssueSeverity.ERROR,
            Path("pages/a.md"),
            None,
            ("pages/a.md", "pages/b.md", "pages/a.md"),
        ),
        "parent.cycle",
        (0, 1, 2),
    ),
    RenderCase(
        CheckIssue(
            "parent.cycle",
            IssueSeverity.ERROR,
            Path("pages/a.md"),
            None,
            ("pages/a.md", "pages/a.md"),
        ),
        "parent.cycle.self_loop",
        (0, 1),
    ),
    RenderCase(
        CheckIssue("parent.unreachable", IssueSeverity.ERROR, Path("pages/a.md"), None),
        "parent.unreachable",
    ),
    RenderCase(
        CheckIssue(
            "parent.navigation_missing",
            IssueSeverity.ERROR,
            Path("index.md"),
            None,
            ("pages/a.md",),
        ),
        "parent.navigation_missing",
        (0,),
    ),
    RenderCase(
        CheckIssue("hub.empty", IssueSeverity.ERROR, Path("pages/empty/index.md"), None),
        "hub.empty",
    ),
    RenderCase(
        CheckIssue("dependency.self", IssueSeverity.ERROR, Path("pages/a.md"), 7),
        "dependency.self",
    ),
    RenderCase(
        CheckIssue(
            "dependency.cycle",
            IssueSeverity.ERROR,
            Path("pages/a.md"),
            None,
            ("pages/a.md", "pages/b.md", "pages/a.md"),
        ),
        "dependency.cycle",
        (0, 1, 2),
    ),
    RenderCase(
        CheckIssue(
            "dependency.cycle",
            IssueSeverity.ERROR,
            Path("pages/a.md"),
            None,
            ("pages/a.md", "pages/a.md"),
        ),
        "dependency.cycle.self_loop",
        (0, 1),
    ),
    RenderCase(
        CheckIssue(
            "replacement.pair_missing",
            IssueSeverity.ERROR,
            Path("pages/new.md"),
            7,
            ("pages/question.md", "被取代"),
        ),
        "replacement.pair_missing",
        (0, 1),
    ),
    RenderCase(
        CheckIssue(
            "replacement.status_mismatch",
            IssueSeverity.ERROR,
            Path("pages/new.md"),
            7,
            ("relation_requires_status",),
        ),
        "replacement.status_mismatch.relation_requires_status",
    ),
    RenderCase(
        CheckIssue(
            "replacement.status_mismatch",
            IssueSeverity.ERROR,
            Path("pages/old.md"),
            None,
            ("status_requires_relation",),
        ),
        "replacement.status_mismatch.status_requires_relation",
    ),
    RenderCase(
        CheckIssue(
            "replacement.cycle",
            IssueSeverity.ERROR,
            Path("pages/a.md"),
            None,
            ("pages/a.md", "pages/b.md", "pages/a.md"),
        ),
        "replacement.cycle",
        (0, 1, 2),
    ),
    RenderCase(
        CheckIssue(
            "replacement.cycle",
            IssueSeverity.ERROR,
            Path("pages/a.md"),
            None,
            ("pages/a.md", "pages/a.md"),
        ),
        "replacement.cycle.self_loop",
        (0, 1),
    ),
    RenderCase(
        CheckIssue(
            "challenge.pair_missing",
            IssueSeverity.ERROR,
            Path("pages/challenger.md"),
            7,
            ("pages/question.md", "被质疑"),
        ),
        "challenge.pair_missing",
        (0, 1),
    ),
    RenderCase(
        CheckIssue("output.forbidden", IssueSeverity.ERROR, Path("pages/a.md"), 7),
        "output.forbidden",
    ),
    RenderCase(
        CheckIssue("output.missing", IssueSeverity.ERROR, Path("pages/a.md"), None),
        "output.missing",
    ),
    RenderCase(
        CheckIssue(
            "output.wrong_target", IssueSeverity.ERROR, Path("pages/a.md"), 7, ("duplicate",)
        ),
        "output.wrong_target.duplicate",
    ),
    RenderCase(
        CheckIssue(
            "output.wrong_target",
            IssueSeverity.ERROR,
            Path("pages/a.md"),
            7,
            ("not_conclusions", "../index.md"),
        ),
        "output.wrong_target.not_conclusions",
        (1,),
    ),
    RenderCase(
        CheckIssue(
            "output.backlink_missing",
            IssueSeverity.ERROR,
            Path("conclusions.md"),
            None,
            ("pages/a.md",),
        ),
        "output.backlink_missing",
        (0,),
    ),
    RenderCase(
        CheckIssue(
            "operation.journal_corrupt", IssueSeverity.ERROR, Path(".draft/operation.lock"), None
        ),
        "operation.journal_corrupt",
    ),
    RenderCase(
        CheckIssue(
            "operation.recovery_required",
            IssueSeverity.ERROR,
            Path(".draft/operation.lock"),
            None,
            (_RECOVERY_ID,),
        ),
        "operation.recovery_required",
        (0,),
    ),
    RenderCase(
        CheckIssue("operation.orphan_allocating", IssueSeverity.REVIEW, Path(_JOURNAL), None),
        "operation.orphan_allocating",
    ),
    RenderCase(
        CheckIssue(
            "operation.inconsistent_state",
            IssueSeverity.ERROR,
            Path(_JOURNAL),
            None,
            ("prepared",),
        ),
        "operation.inconsistent_state",
        (0,),
    ),
    RenderCase(
        CheckIssue("trash.deadlines_invalid", IssueSeverity.ERROR, Path(_JOURNAL), None, (_OP_ID,)),
        "trash.deadlines_invalid",
        (0,),
    ),
    RenderCase(
        CheckIssue("trash.package_missing", IssueSeverity.ERROR, Path(_JOURNAL), None, (_OP_ID,)),
        "trash.package_missing",
        (0,),
    ),
    RenderCase(
        CheckIssue("trash.package_corrupt", IssueSeverity.ERROR, Path(_JOURNAL), None, (_OP_ID,)),
        "trash.package_corrupt",
        (0,),
    ),
    RenderCase(
        CheckIssue("trash.retained", IssueSeverity.INFO, Path(_JOURNAL), None, (_OP_ID,)),
        "trash.retained",
        (0,),
    ),
    RenderCase(
        CheckIssue("trash.handoff_pending", IssueSeverity.WARNING, Path(_JOURNAL), None, (_OP_ID,)),
        "trash.handoff_pending",
        (0,),
    ),
    RenderCase(
        CheckIssue(
            "trash.pending_unexplained", IssueSeverity.ERROR, Path(_JOURNAL), None, (_OP_ID,)
        ),
        "trash.pending_unexplained",
        (0,),
    ),
]


def test_valid_render_cases_cover_every_published_code() -> None:
    assert {case.issue.code for case in VALID_RENDER_CASES} == EXPECTED_CODES


@pytest.mark.parametrize(
    "case",
    VALID_RENDER_CASES,
    ids=[case.case_id for case in VALID_RENDER_CASES],
)
def test_each_catalog_case_renders_valid_single_line(case: RenderCase) -> None:
    _assert_valid_render(case)


VARIANT_CODES = (
    "root.missing_file",
    "page.invalid_status",
    "page.invalid_relation",
    "page.parse_error",
    "link.missing",
    "link.escape",
    "replacement.status_mismatch",
    "output.wrong_target",
)
VARIANT_GROUPS = [
    tuple(case.issue for case in VALID_RENDER_CASES if case.issue.code == code)
    for code in VARIANT_CODES
]


@pytest.mark.parametrize("issues", VARIANT_GROUPS)
def test_registered_variants_choose_distinct_messages(
    issues: tuple[CheckIssue, ...],
) -> None:
    rendered = [render_issue(issue) for issue in issues]
    for text, issue in zip(rendered, issues, strict=True):
        _assert_single_line(text)
        assert issue.code not in text
    assert len(set(rendered)) == len(rendered)


def test_unknown_issue_fallback_is_one_line_and_lossless() -> None:
    issue = CheckIssue("future.code", IssueSeverity.WARNING, None, None, ("a\nb", "value"))
    rendered = render_issue(issue)
    _assert_single_line(rendered)
    assert issue.code in rendered
    assert json.dumps(list(issue.details), ensure_ascii=False) in rendered


def test_known_code_with_invalid_location_exposes_code_and_raw_details() -> None:
    issue = CheckIssue("link.missing", IssueSeverity.ERROR, Path("a.md"), None, ("x", "x"))
    rendered = render_issue(issue)
    _assert_single_line(rendered)
    assert issue.code in rendered
    assert json.dumps(list(issue.details), ensure_ascii=False) in rendered


def test_known_code_with_non_positive_line_does_not_render_fake_line() -> None:
    issue = CheckIssue("link.missing", IssueSeverity.ERROR, Path("a.md"), 0, ("x", "x"))
    rendered = render_issue(issue)
    _assert_single_line(rendered)
    assert issue.path is not None
    assert f"{issue.path.as_posix()}:{issue.line}" not in rendered
    assert issue.code in rendered


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
def test_unclosed_cycle_fallback_is_one_line_and_lossless(issue: CheckIssue) -> None:
    rendered = render_issue(issue)
    _assert_single_line(rendered)
    assert issue.code in rendered
    assert json.dumps(list(issue.details), ensure_ascii=False) in rendered


def test_complex_issue_explicitly_points_to_json() -> None:
    issue = CheckIssue(
        "operation.inconsistent_state",
        IssueSeverity.ERROR,
        Path(".draft/operations/a/journal.json"),
        None,
        ("prepared",),
    )
    rendered = render_issue(issue)
    _assert_single_line(rendered)
    assert "--json" in rendered


def test_path_with_control_character_stays_one_line() -> None:
    path = "bad\nname.md"
    issue = CheckIssue("link.missing", IssueSeverity.ERROR, Path(path), 5, ("x", "x"))
    rendered = render_issue(issue)
    _assert_single_line(rendered)
    assert _escaped_fact(path) in rendered


def test_code_with_control_character_stays_one_line() -> None:
    code = "bad\ncode"
    issue = CheckIssue(code, IssueSeverity.ERROR, Path("a.md"), None, ("x",))
    rendered = render_issue(issue)
    _assert_single_line(rendered)
    assert _escaped_fact(code) in rendered


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
