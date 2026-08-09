import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from draft.diagnostic_codes import (
    CheckCode,
    InvalidRelationReason,
    InvalidStatusReason,
    LinkEscapeKind,
    ParseErrorReason,
    ReplacementStatusReason,
    RootMissingKind,
)
from draft.diagnostics import KNOWN_CHECK_CODES, issue_payload, render_issue
from draft.models import CheckIssue, IssueSeverity

# 操作诊断用例的稳定路径与操作标识；仅用于缩短测试输入，不参与被测逻辑。
_JOURNAL = ".draft/operations/abc/journal.json"
_OP_ID = "a" * 32
_RECOVERY_ID = "4" * 32


@dataclass(frozen=True)
class RenderCase:
    issue: CheckIssue
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


def test_catalog_covers_every_typed_check_code() -> None:
    assert frozenset(code.value for code in CheckCode) == KNOWN_CHECK_CODES


# 每条用例手写合法 CheckIssue 与需要展示的动态详情索引；期望全部来自结构化输入，
# 不复制具体人类可读文案。
VALID_RENDER_CASES = [
    RenderCase(
        CheckIssue(
            CheckCode.CANDIDATE_ALREADY_REGISTERED.value,
            IssueSeverity.ERROR,
            Path(".draft/draft.json"),
            None,
        ),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.CANDIDATE_INTERNAL_STATE.value,
            IssueSeverity.ERROR,
            Path(".draft"),
            None,
        ),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.FORMAT_MISSING.value,
            IssueSeverity.ERROR,
            Path(".draft/draft.json"),
            None,
        ),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.FORMAT_INVALID.value,
            IssueSeverity.ERROR,
            Path(".draft/draft.json"),
            None,
        ),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.FORMAT_UNSUPPORTED.value,
            IssueSeverity.ERROR,
            Path(".draft/draft.json"),
            None,
        ),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.ROOT_MISSING_FILE.value,
            IssueSeverity.ERROR,
            None,
            None,
            (RootMissingKind.FILE.value, "index.md"),
        ),
        (1,),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.ROOT_MISSING_FILE.value,
            IssueSeverity.ERROR,
            None,
            None,
            (RootMissingKind.DIRECTORY.value, "pages"),
        ),
        (1,),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.PAGE_UNMANAGED_PATH.value,
            IssueSeverity.ERROR,
            Path("notes.md"),
            None,
        ),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.PAGE_MISSING_TITLE.value,
            IssueSeverity.ERROR,
            Path("pages/a.md"),
            None,
        ),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.PAGE_MISSING_MACHINE_BLOCK.value,
            IssueSeverity.ERROR,
            Path("pages/a.md"),
            None,
        ),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.PAGE_MISSING_STATUS.value,
            IssueSeverity.ERROR,
            Path("pages/a.md"),
            None,
        ),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.PAGE_DUPLICATE_STATUS.value,
            IssueSeverity.ERROR,
            Path("pages/a.md"),
            5,
        ),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.PAGE_UNKNOWN_LABEL.value,
            IssueSeverity.ERROR,
            Path("pages/a.md"),
            7,
            ("状态依据",),
        ),
        (0,),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.PAGE_INVALID_STATUS.value,
            IssueSeverity.ERROR,
            Path("pages/x.md"),
            3,
            (InvalidStatusReason.NOT_SINGLE_CODE_SPAN.value,),
        ),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.PAGE_INVALID_STATUS.value,
            IssueSeverity.ERROR,
            Path("pages/x.md"),
            3,
            (InvalidStatusReason.EMPTY_EXPLANATION.value,),
        ),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.PAGE_INVALID_STATUS.value,
            IssueSeverity.ERROR,
            Path("pages/x.md"),
            3,
            (InvalidStatusReason.UNKNOWN_PREFIX.value, "进行中"),
        ),
        (1,),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.PAGE_INVALID_RELATION.value,
            IssueSeverity.ERROR,
            Path("pages/x.md"),
            5,
            (InvalidRelationReason.LINK_CARDINALITY_OR_IMAGE.value, "依赖"),
        ),
        (1,),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.PAGE_INVALID_RELATION.value,
            IssueSeverity.ERROR,
            Path("pages/x.md"),
            5,
            (InvalidRelationReason.EXTRA_CONTENT.value, "父主题"),
        ),
        (1,),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.PAGE_PARSE_ERROR.value,
            IssueSeverity.ERROR,
            Path("pages/a.md"),
            9,
            (ParseErrorReason.LAZY_CONTINUATION.value,),
        ),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.PAGE_PARSE_ERROR.value,
            IssueSeverity.ERROR,
            Path("pages/a.md"),
            9,
            (ParseErrorReason.SOURCE_ALIGNMENT_FAILED.value, "missing.md"),
        ),
        (1,),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.PAGE_PARSE_ERROR.value,
            IssueSeverity.ERROR,
            Path("pages/a.md"),
            None,
            (ParseErrorReason.MACHINE_BLOCK_MAP_MISSING.value,),
        ),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.PAGE_PARSE_ERROR.value,
            IssueSeverity.ERROR,
            Path("pages/a.md"),
            9,
            (ParseErrorReason.LINK_DESTINATION_MISSING.value,),
        ),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.PAGE_PARSE_ERROR.value,
            IssueSeverity.ERROR,
            Path("pages/a.md"),
            9,
            (ParseErrorReason.IMAGE_DESTINATION_MISSING.value,),
        ),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.PAGE_CAPACITY.value,
            IssueSeverity.REVIEW,
            Path("pages/a.md"),
            None,
            ("620", "70000"),
        ),
        (0, 1),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.LINK_MISSING.value,
            IssueSeverity.ERROR,
            Path("conclusions.md"),
            8,
            ("pages/a.md", "pages/a.md"),
        ),
        (1,),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.LINK_MISSING.value,
            IssueSeverity.ERROR,
            Path("conclusions.md"),
            8,
            ("./a.md", "pages/a.md"),
        ),
        (0, 1),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.LINK_ESCAPE.value,
            IssueSeverity.ERROR,
            Path("pages/a.md"),
            9,
            (LinkEscapeKind.DESTINATION.value, "../../outside.md"),
        ),
        (1,),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.LINK_INVALID.value,
            IssueSeverity.ERROR,
            Path("pages/a.md"),
            9,
            ("b.md", "pages/b.md"),
        ),
        (0, 1),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.LINK_UNAVAILABLE.value,
            IssueSeverity.WARNING,
            Path("pages/a.md"),
            9,
            (r"C:\docs\a.pdf", r"C:\docs\a.pdf"),
        ),
        (0, 1),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.LINK_ESCAPE.value,
            IssueSeverity.ERROR,
            Path("pages/linked.md"),
            None,
            (LinkEscapeKind.MANAGED_PATH.value, "pages/linked.md"),
        ),
        (1,),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.PATH_SYMLINK_ESCAPE.value,
            IssueSeverity.ERROR,
            Path(".draft/operations/abc"),
            None,
        ),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.RELATION_DUPLICATE.value,
            IssueSeverity.ERROR,
            Path("pages/question.md"),
            9,
            ("depends_on", "pages/question.md"),
        ),
        (1,),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.PARENT_ROOT_DECLARED.value,
            IssueSeverity.ERROR,
            Path("index.md"),
            4,
        ),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.PARENT_MISSING.value,
            IssueSeverity.ERROR,
            Path("pages/a.md"),
            None,
        ),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.PARENT_MULTIPLE.value,
            IssueSeverity.ERROR,
            Path("pages/a.md"),
            5,
        ),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.LAYOUT_PARENT_MISMATCH.value,
            IssueSeverity.ERROR,
            Path("pages/sub/x.md"),
            4,
            ("index.md", "pages/sub/index.md"),
        ),
        (0, 1),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.PARENT_CYCLE.value,
            IssueSeverity.ERROR,
            Path("pages/a.md"),
            None,
            ("pages/a.md", "pages/b.md", "pages/a.md"),
        ),
        (0, 1, 2),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.PARENT_CYCLE.value,
            IssueSeverity.ERROR,
            Path("pages/a.md"),
            None,
            ("pages/a.md", "pages/a.md"),
        ),
        (0, 1),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.PARENT_UNREACHABLE.value,
            IssueSeverity.ERROR,
            Path("pages/a.md"),
            None,
        ),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.PARENT_NAVIGATION_MISSING.value,
            IssueSeverity.ERROR,
            Path("index.md"),
            None,
            ("pages/a.md",),
        ),
        (0,),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.HUB_EMPTY.value,
            IssueSeverity.ERROR,
            Path("pages/empty/index.md"),
            None,
        ),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.DEPENDENCY_SELF.value,
            IssueSeverity.ERROR,
            Path("pages/a.md"),
            7,
        ),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.DEPENDENCY_CYCLE.value,
            IssueSeverity.ERROR,
            Path("pages/a.md"),
            None,
            ("pages/a.md", "pages/b.md", "pages/a.md"),
        ),
        (0, 1, 2),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.DEPENDENCY_CYCLE.value,
            IssueSeverity.ERROR,
            Path("pages/a.md"),
            None,
            ("pages/a.md", "pages/a.md"),
        ),
        (0, 1),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.REPLACEMENT_PAIR_MISSING.value,
            IssueSeverity.ERROR,
            Path("pages/new.md"),
            7,
            ("pages/question.md", "superseded_by"),
        ),
        (0,),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.REPLACEMENT_STATUS_MISMATCH.value,
            IssueSeverity.ERROR,
            Path("pages/new.md"),
            7,
            (ReplacementStatusReason.RELATION_REQUIRES_STATUS.value,),
        ),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.REPLACEMENT_STATUS_MISMATCH.value,
            IssueSeverity.ERROR,
            Path("pages/old.md"),
            None,
            (ReplacementStatusReason.STATUS_REQUIRES_RELATION.value,),
        ),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.REPLACEMENT_CYCLE.value,
            IssueSeverity.ERROR,
            Path("pages/a.md"),
            None,
            ("pages/a.md", "pages/b.md", "pages/a.md"),
        ),
        (0, 1, 2),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.REPLACEMENT_CYCLE.value,
            IssueSeverity.ERROR,
            Path("pages/a.md"),
            None,
            ("pages/a.md", "pages/a.md"),
        ),
        (0, 1),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.CHALLENGE_PAIR_MISSING.value,
            IssueSeverity.ERROR,
            Path("pages/challenger.md"),
            7,
            ("pages/question.md", "challenged_by"),
        ),
        (0,),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.OPERATION_JOURNAL_CORRUPT.value,
            IssueSeverity.ERROR,
            Path(".draft/operation.lock"),
            None,
        ),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.OPERATION_RECOVERY_REQUIRED.value,
            IssueSeverity.ERROR,
            Path(".draft/operation.lock"),
            None,
            (_RECOVERY_ID,),
        ),
        (0,),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.OPERATION_ORPHAN_ALLOCATING.value,
            IssueSeverity.REVIEW,
            Path(_JOURNAL),
            None,
        ),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.OPERATION_INCONSISTENT_STATE.value,
            IssueSeverity.ERROR,
            Path(_JOURNAL),
            None,
            ("prepared",),
        ),
        (0,),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.TRASH_DEADLINES_INVALID.value,
            IssueSeverity.ERROR,
            Path(_JOURNAL),
            None,
            (_OP_ID,),
        ),
        (0,),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.TRASH_PACKAGE_MISSING.value,
            IssueSeverity.ERROR,
            Path(_JOURNAL),
            None,
            (_OP_ID,),
        ),
        (0,),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.TRASH_PACKAGE_CORRUPT.value,
            IssueSeverity.ERROR,
            Path(_JOURNAL),
            None,
            (_OP_ID,),
        ),
        (0,),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.TRASH_RETAINED.value,
            IssueSeverity.INFO,
            Path(_JOURNAL),
            None,
            (_OP_ID,),
        ),
        (0,),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.TRASH_HANDOFF_PENDING.value,
            IssueSeverity.WARNING,
            Path(_JOURNAL),
            None,
            (_OP_ID,),
        ),
        (0,),
    ),
    RenderCase(
        CheckIssue(
            CheckCode.TRASH_PENDING_UNEXPLAINED.value,
            IssueSeverity.ERROR,
            Path(_JOURNAL),
            None,
            (_OP_ID,),
        ),
        (0,),
    ),
]


def test_valid_render_cases_cover_every_published_code() -> None:
    assert {case.issue.code for case in VALID_RENDER_CASES} == KNOWN_CHECK_CODES


@pytest.mark.parametrize(
    "case",
    VALID_RENDER_CASES,
    ids=lambda case: case.issue.code,
)
def test_each_catalog_case_renders_valid_single_line(case: RenderCase) -> None:
    _assert_valid_render(case)


VARIANT_CODES: tuple[CheckCode, ...] = (
    CheckCode.ROOT_MISSING_FILE,
    CheckCode.PAGE_INVALID_STATUS,
    CheckCode.PAGE_INVALID_RELATION,
    CheckCode.PAGE_PARSE_ERROR,
    CheckCode.LINK_MISSING,
    CheckCode.LINK_ESCAPE,
    CheckCode.REPLACEMENT_STATUS_MISMATCH,
)

VARIANT_GROUPS = [
    pytest.param(
        tuple(case.issue for case in VALID_RENDER_CASES if case.issue.code == code.value),
        id=code.value,
    )
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
    issue = CheckIssue(
        CheckCode.LINK_MISSING.value, IssueSeverity.ERROR, Path("a.md"), None, ("x", "x")
    )
    rendered = render_issue(issue)
    _assert_single_line(rendered)
    assert issue.code in rendered
    assert json.dumps(list(issue.details), ensure_ascii=False) in rendered


def test_relation_detail_renders_current_chinese_label() -> None:
    """details 携带稳定关系码，人类输出渲染为当前中文标签。"""
    issue = CheckIssue(
        CheckCode.REPLACEMENT_PAIR_MISSING.value,
        IssueSeverity.ERROR,
        Path("pages/new.md"),
        7,
        ("pages/old.md", "superseded_by"),
    )
    rendered = render_issue(issue)
    _assert_single_line(rendered)
    assert "被取代" in rendered
    assert "superseded_by" not in rendered


def test_known_code_with_non_positive_line_does_not_render_fake_line() -> None:
    issue = CheckIssue(
        CheckCode.LINK_MISSING.value, IssueSeverity.ERROR, Path("a.md"), 0, ("x", "x")
    )
    rendered = render_issue(issue)
    _assert_single_line(rendered)
    assert issue.path is not None
    assert f"{issue.path.as_posix()}:{issue.line}" not in rendered
    assert issue.code in rendered


CYCLE_CODES: tuple[CheckCode, ...] = (
    CheckCode.PARENT_CYCLE,
    CheckCode.DEPENDENCY_CYCLE,
    CheckCode.REPLACEMENT_CYCLE,
)


@pytest.mark.parametrize("code", CYCLE_CODES, ids=lambda code: code.value)
def test_unclosed_cycle_fallback_is_one_line_and_lossless(code: CheckCode) -> None:
    issue = CheckIssue(
        code.value,
        IssueSeverity.ERROR,
        Path("pages/a.md"),
        None,
        ("pages/a.md", "pages/b.md"),
    )
    rendered = render_issue(issue)
    _assert_single_line(rendered)
    assert issue.code in rendered
    assert json.dumps(list(issue.details), ensure_ascii=False) in rendered


def test_complex_issue_explicitly_points_to_json() -> None:
    issue = CheckIssue(
        CheckCode.OPERATION_INCONSISTENT_STATE.value,
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
    issue = CheckIssue(CheckCode.LINK_MISSING.value, IssueSeverity.ERROR, Path(path), 5, ("x", "x"))
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
        CheckCode.PAGE_CAPACITY.value,
        IssueSeverity.REVIEW,
        Path("pages/a.md"),
        None,
        ("601", "70000"),
    )
    assert issue_payload(issue) == {
        "code": CheckCode.PAGE_CAPACITY.value,
        "severity": IssueSeverity.REVIEW.value,
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
