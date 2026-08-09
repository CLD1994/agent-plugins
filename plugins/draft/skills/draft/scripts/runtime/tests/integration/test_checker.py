from datetime import UTC, datetime
from pathlib import Path

import pytest

from draft.checker import check_draft
from draft.diagnostic_codes import (
    CheckCode,
    LinkEscapeKind,
    ReplacementStatusReason,
)
from draft.errors import DraftError
from draft.models import CheckReport, IssueSeverity
from draft.operations import JOURNAL_VERSION
from draft.trash import trash_page
from tests.integration.support import DraftFactory, snapshot_tree, write_raw_journal

ROOT_PAGE = """# Topic

> 状态：`已关闭：纯导航页，不表达独立结论`

## 子主题

- [结论汇总](conclusions.md)
- [问题](pages/question.md)
"""

CONCLUSIONS = """# 结论汇总

> 状态：`已确认`
>
> 父主题：[根索引](index.md)

- [已确认的问题](pages/question.md)：使用显式状态前缀。
"""

QUESTION = """# 问题

> 状态：`已确认`
>
> 父主题：[根索引](../index.md)

使用显式状态前缀。
"""


def healthy() -> dict[str, str]:
    return {
        "index.md": ROOT_PAGE,
        "conclusions.md": CONCLUSIONS,
        "pages/question.md": QUESTION,
    }


def codes(report: CheckReport) -> list[str]:
    return sorted(issue.code for issue in report.errors)


def test_minimal_registered_draft_is_clean(draft_factory: DraftFactory) -> None:
    root = draft_factory(healthy())

    report = check_draft(root)

    assert report.errors == ()
    assert report.reviews == ()


def test_journal_operation_id_mismatch_is_corrupt_without_mutation(
    draft_factory: DraftFactory,
) -> None:
    root = draft_factory(healthy())
    journal_path = write_raw_journal(
        root,
        version=JOURNAL_VERSION,
        operation_id="7" * 32,
        directory_id="8" * 32,
    )
    before = journal_path.read_bytes()

    report = check_draft(root)

    assert CheckCode.OPERATION_JOURNAL_CORRUPT.value in codes(report)
    assert journal_path.read_bytes() == before


def test_confirmed_page_without_output_is_valid(draft_factory: DraftFactory) -> None:
    """已确认页面不再要求产出声明；确认状态本身决定结论归属。"""
    root = draft_factory(healthy())

    report = check_draft(root)

    assert report.errors == ()


def test_missing_marker(draft_factory: DraftFactory) -> None:
    root = draft_factory(healthy(), registered=False)

    report = check_draft(root)

    assert codes(report) == [CheckCode.FORMAT_MISSING.value]


def test_registered_mode_accepts_marker_without_runtime_directories(
    draft_factory: DraftFactory,
) -> None:
    root = draft_factory(healthy())
    internal = root / ".draft"
    (internal / "trash" / "outgoing").rmdir()
    (internal / "trash").rmdir()
    (internal / "operations").rmdir()

    report = check_draft(root)

    assert report.errors == ()
    assert (internal / "draft.json").is_file()


def test_invalid_marker_json(draft_factory: DraftFactory) -> None:
    root = draft_factory(healthy())
    (root / ".draft/draft.json").write_text("not json\n", encoding="utf-8")

    report = check_draft(root)

    assert codes(report) == [CheckCode.FORMAT_INVALID.value]


def test_unsupported_format_version(draft_factory: DraftFactory) -> None:
    root = draft_factory(healthy())
    (root / ".draft/draft.json").write_text('{"format_version":2}\n', encoding="utf-8")

    report = check_draft(root)

    assert codes(report) == [CheckCode.FORMAT_UNSUPPORTED.value]


def test_symlinked_marker_reports_symlink_escape(draft_factory: DraftFactory) -> None:
    root = draft_factory(healthy())
    marker = root / ".draft/draft.json"
    marker.unlink()
    marker.symlink_to("outside.json")

    report = check_draft(root)

    assert codes(report) == [CheckCode.PATH_SYMLINK_ESCAPE.value]


def test_candidate_mode_reports_final_marker_as_already_registered(
    draft_factory: DraftFactory,
) -> None:
    root = draft_factory(healthy())

    report = check_draft(root, mode="candidate")

    assert codes(report) == [CheckCode.CANDIDATE_ALREADY_REGISTERED.value]


def test_candidate_mode_accepts_unregistered_layout(draft_factory: DraftFactory) -> None:
    root = draft_factory(healthy(), registered=False)

    report = check_draft(root, mode="candidate")

    assert report.errors == ()


def test_candidate_mode_rejects_internal_entries(draft_factory: DraftFactory) -> None:
    root = draft_factory(healthy(), registered=False)
    (root / ".draft" / "operations" / ("a" * 32)).mkdir(parents=True)

    report = check_draft(root, mode="candidate")

    assert codes(report) == [CheckCode.CANDIDATE_INTERNAL_STATE.value]


def test_candidate_mode_provisional_marker_boundary(draft_factory: DraftFactory) -> None:
    root = draft_factory(healthy(), registered=False)
    internal = root / ".draft"
    internal.mkdir()
    (internal / "draft.json").write_text(
        '{"format_version":1,"conclusions_state":"not_generated","adopt_state":"provisional","candidate_manifest":[]}\n',
        encoding="utf-8",
    )

    with pytest.raises(DraftError, match="adopt.recovery_required"):
        check_draft(root, mode="candidate")

    allowed = check_draft(root, mode="candidate", allow_provisional_adopt=True)

    assert allowed.errors == ()


def test_marker_with_unknown_fields_is_invalid(draft_factory: DraftFactory) -> None:
    root = draft_factory(healthy())
    (root / ".draft/draft.json").write_text('{"format_version":1,"extra":true}\n', encoding="utf-8")

    report = check_draft(root)

    assert codes(report) == [CheckCode.FORMAT_INVALID.value]


def test_marker_with_unknown_adopt_state_is_invalid(draft_factory: DraftFactory) -> None:
    root = draft_factory(healthy())
    (root / ".draft/draft.json").write_text(
        '{"format_version":1,"adopt_state":"final","candidate_manifest":[]}\n',
        encoding="utf-8",
    )

    report = check_draft(root)

    assert codes(report) == [CheckCode.FORMAT_INVALID.value]


def test_missing_required_files(draft_factory: DraftFactory) -> None:
    files = healthy()
    del files["conclusions.md"]
    root = draft_factory(files)
    # 所有权为 generated 时 conclusions.md 必须存在。
    (root / ".draft/draft.json").write_text(
        '{"format_version":1,"conclusions_state":"generated"}\n', encoding="utf-8"
    )

    report = check_draft(root)

    assert CheckCode.ROOT_MISSING_FILE.value in codes(report)


def test_not_generated_absent_conclusions_is_not_missing_file(
    draft_factory: DraftFactory,
) -> None:
    files = healthy()
    del files["conclusions.md"]
    files["index.md"] = ROOT_PAGE.replace("- [结论汇总](conclusions.md)\n", "")
    root = draft_factory(files)

    report = check_draft(root)

    assert CheckCode.ROOT_MISSING_FILE.value not in codes(report)


def test_unmanaged_markdown_below_root(draft_factory: DraftFactory) -> None:
    files = healthy()
    files["notes.md"] = "# 笔记\n\n> 状态：`待讨论`\n\n自由笔记。\n"
    root = draft_factory(files)

    report = check_draft(root)

    assert codes(report) == [CheckCode.PAGE_UNMANAGED_PATH.value]
    assert report.errors[0].path == Path("notes.md")


def test_parse_error_keeps_specific_code_and_line(draft_factory: DraftFactory) -> None:
    files = healthy()
    files["pages/question.md"] = QUESTION.replace(
        "> 父主题：[根索引](../index.md)",
        "> 父主题：[根索引](../index.md)\n>\n> 状态依据：",
    )
    root = draft_factory(files)

    report = check_draft(root)

    assert CheckCode.PAGE_UNKNOWN_LABEL.value in codes(report)
    issue = next(
        issue for issue in report.errors if issue.code == CheckCode.PAGE_UNKNOWN_LABEL.value
    )
    assert issue.path == Path("pages/question.md")
    assert issue.line == 7


def test_missing_link_target(draft_factory: DraftFactory) -> None:
    files = healthy()
    files["pages/question.md"] = QUESTION.replace("使用显式状态前缀。", "见 [不存在](missing.md)。")
    root = draft_factory(files)

    report = check_draft(root)

    assert codes(report) == [CheckCode.LINK_MISSING.value]


def test_missing_link_reports_source_line_literal_and_normalized_target(
    draft_factory: DraftFactory,
) -> None:
    files = healthy()
    files["pages/question.md"] = QUESTION.replace("使用显式状态前缀。", "见 [不存在](missing.md)。")
    report = check_draft(draft_factory(files))
    issue = next(issue for issue in report.errors if issue.code == CheckCode.LINK_MISSING.value)
    assert issue.path == Path("pages/question.md")
    assert issue.line == 7
    assert issue.details == ("missing.md", "pages/missing.md")


def test_missing_link_with_distinct_literal_and_normalized_target(
    draft_factory: DraftFactory,
) -> None:
    files = healthy()
    files["pages/question.md"] = QUESTION.replace(
        "使用显式状态前缀。", "见 [不存在](./missing.md)。"
    )
    report = check_draft(draft_factory(files))
    issue = next(issue for issue in report.errors if issue.code == CheckCode.LINK_MISSING.value)
    assert issue.details == ("./missing.md", "pages/missing.md")


def test_two_missing_links_on_one_line_remain_distinguishable(
    draft_factory: DraftFactory,
) -> None:
    files = healthy()
    files["conclusions.md"] = CONCLUSIONS + "[甲](pages/a.md) [乙](pages/b.md)\n"
    report = check_draft(draft_factory(files))
    issues = [i for i in report.errors if i.code == CheckCode.LINK_MISSING.value]
    assert [(i.line, i.details) for i in issues] == [
        (8, ("pages/a.md", "pages/a.md")),
        (8, ("pages/b.md", "pages/b.md")),
    ]


def test_missing_relation_target_reports_link_missing_once(
    draft_factory: DraftFactory,
) -> None:
    files = healthy()
    files["pages/question.md"] = QUESTION.replace(
        "> 父主题：[根索引](../index.md)",
        "> 父主题：[根索引](../index.md)\n>\n> 依赖：[缺失](missing.md)",
    )
    report = check_draft(draft_factory(files))
    missing = [i for i in report.errors if i.code == CheckCode.LINK_MISSING.value]
    assert len(missing) == 1
    assert missing[0].path == Path("pages/question.md")
    assert missing[0].details == ("missing.md", "pages/missing.md")


def test_link_to_draft_outside_missing_target_is_missing(draft_factory: DraftFactory) -> None:
    files = healthy()
    files["pages/question.md"] = QUESTION.replace(
        "使用显式状态前缀。", "见 [外部](../../outside.md)。"
    )
    root = draft_factory(files)

    report = check_draft(root)

    assert codes(report) == [CheckCode.LINK_MISSING.value]


def test_link_to_draft_outside_missing_preserves_literal_destination(
    draft_factory: DraftFactory,
) -> None:
    files = healthy()
    files["pages/question.md"] = QUESTION.replace(
        "使用显式状态前缀。", "见 [外部](../../outside.md)。"
    )
    report = check_draft(draft_factory(files))
    issue = next(issue for issue in report.errors if issue.code == CheckCode.LINK_MISSING.value)
    assert issue.path == Path("pages/question.md")
    assert issue.line == 7
    assert issue.details == ("../../outside.md", "../outside.md")


def test_page_file_through_symlink(draft_factory: DraftFactory) -> None:
    files = healthy()
    root = draft_factory(files)
    outside = root.parent / "outside.md"
    outside.write_text("# 外部\n", encoding="utf-8")
    (root / "pages/linked.md").symlink_to(outside)

    report = check_draft(root)

    assert CheckCode.LINK_ESCAPE.value in codes(report)


def test_managed_path_symlink_escape_preserves_relative_path(
    draft_factory: DraftFactory,
) -> None:
    files = healthy()
    root = draft_factory(files)
    outside = root.parent / "outside.md"
    outside.write_text("# 外部\n", encoding="utf-8")
    (root / "pages/linked.md").symlink_to(outside)

    report = check_draft(root)
    issue = next(issue for issue in report.errors if issue.code == CheckCode.LINK_ESCAPE.value)
    assert issue.details == (LinkEscapeKind.MANAGED_PATH.value, "pages/linked.md")


def test_existing_draft_outside_resource_is_not_an_error(
    draft_factory: DraftFactory, tmp_path: Path
) -> None:
    files = healthy()
    outside = tmp_path / "shared.png"
    outside.write_bytes(b"png")
    files["pages/question.md"] = QUESTION.replace(
        "使用显式状态前缀。", "见 [外部](../../shared.png)。"
    )
    root = draft_factory(files)

    report = check_draft(root)

    assert report.errors == ()


def test_missing_image_inside_draft_reports_line(draft_factory: DraftFactory) -> None:
    files = healthy()
    files["pages/question.md"] = QUESTION.replace("使用显式状态前缀。", "![缺失](missing.png)")
    report = check_draft(draft_factory(files))
    missing = [i for i in report.errors if i.code == CheckCode.LINK_MISSING.value]
    assert len(missing) == 1
    assert missing[0].path == Path("pages/question.md")
    assert missing[0].line == 7
    assert missing[0].details == ("missing.png", "pages/missing.png")


def test_page_namespace_directory_is_invalid(draft_factory: DraftFactory, tmp_path: Path) -> None:
    files = healthy()
    (tmp_path / "draft").mkdir(exist_ok=True)
    root = draft_factory(files)
    (root / "pages/target.md").mkdir()
    files["pages/question.md"] = QUESTION.replace("使用显式状态前缀。", "见 [目录](target.md)。")
    (root / "pages/question.md").write_text(files["pages/question.md"], encoding="utf-8")

    report = check_draft(root)

    invalid = [i for i in report.errors if i.code == CheckCode.LINK_INVALID.value]
    assert len(invalid) == 1
    assert invalid[0].path == Path("pages/question.md")
    assert invalid[0].details == ("target.md", "pages/target.md")


def test_page_namespace_symlink_is_invalid(draft_factory: DraftFactory, tmp_path: Path) -> None:
    files = healthy()
    root = draft_factory(files)
    outside = tmp_path / "outside.md"
    outside.write_text("# 外\n", encoding="utf-8")
    (root / "pages/target.md").symlink_to(outside)
    files["pages/question.md"] = QUESTION.replace("使用显式状态前缀。", "见 [符号](target.md)。")
    (root / "pages/question.md").write_text(files["pages/question.md"], encoding="utf-8")

    report = check_draft(root)

    invalid = [i for i in report.errors if i.code == CheckCode.LINK_INVALID.value]
    assert len(invalid) == 1
    assert invalid[0].details == ("target.md", "pages/target.md")


def test_external_uri_is_not_an_error(draft_factory: DraftFactory) -> None:
    files = healthy()
    files["pages/question.md"] = QUESTION.replace(
        "使用显式状态前缀。", "见 [外部](https://example.test/a#part)。"
    )
    report = check_draft(draft_factory(files))

    assert report.errors == ()


def test_windows_absolute_link_is_unavailable_warning(
    draft_factory: DraftFactory,
) -> None:
    files = healthy()
    files["pages/question.md"] = QUESTION.replace(
        "使用显式状态前缀。", "见 [盘符](C:\\docs\\a.pdf)。"
    )
    report = check_draft(draft_factory(files))

    assert report.errors == ()
    unavailable = [i for i in report.warnings if i.code == CheckCode.LINK_UNAVAILABLE.value]
    assert len(unavailable) == 1
    assert unavailable[0].details == (r"C:\docs\a.pdf", r"C:\docs\a.pdf")


def test_non_root_page_without_parent(draft_factory: DraftFactory) -> None:
    files = healthy()
    files["pages/question.md"] = QUESTION.replace("> 父主题：[根索引](../index.md)\n", "")
    root = draft_factory(files)

    report = check_draft(root)

    assert CheckCode.PARENT_MISSING.value in codes(report)


def test_multiple_parent_declarations(draft_factory: DraftFactory) -> None:
    files = healthy()
    files["pages/question.md"] = QUESTION.replace(
        "> 父主题：[根索引](../index.md)",
        "> 父主题：[根索引](../index.md)\n>\n> 父主题：[结论](../conclusions.md)",
    )
    root = draft_factory(files)

    report = check_draft(root)

    assert CheckCode.PARENT_MULTIPLE.value in codes(report)


def test_root_declaring_parent(draft_factory: DraftFactory) -> None:
    files = healthy()
    files["index.md"] = ROOT_PAGE.replace(
        "状态：`已关闭：纯导航页，不表达独立结论`",
        "状态：`已关闭：纯导航页，不表达独立结论`\n>\n> 父主题：[问题](pages/question.md)",
    )
    root = draft_factory(files)

    report = check_draft(root)

    assert CheckCode.PARENT_ROOT_DECLARED.value in codes(report)


def test_parent_cycle_reports_one_canonical_cycle(draft_factory: DraftFactory) -> None:
    files = healthy()
    files["pages/a.md"] = (
        "# A\n\n> 状态：`待讨论`\n>\n> 父主题：[B](b.md)\n\n- [B](b.md)\n- 返回 [B](b.md)。\n"
    )
    files["pages/b.md"] = "# B\n\n> 状态：`待讨论`\n>\n> 父主题：[A](a.md)\n\n- [A](a.md)。\n"
    root = draft_factory(files)

    report = check_draft(root)

    cycle_issues = [issue for issue in report.errors if issue.code == CheckCode.PARENT_CYCLE.value]
    assert len(cycle_issues) == 1
    assert cycle_issues[0].details == ("pages/a.md", "pages/b.md", "pages/a.md")
    assert CheckCode.PARENT_UNREACHABLE.value not in codes(report)


def test_unreachable_page(draft_factory: DraftFactory) -> None:
    files = healthy()
    files["pages/a.md"] = "# A\n\n> 状态：`待讨论`\n>\n> 父主题：[B](b.md)\n\n- [B](b.md)。\n"
    files["pages/b.md"] = "# B\n\n> 状态：`待讨论`\n\n- [A](a.md)。\n"
    root = draft_factory(files)

    report = check_draft(root)

    assert CheckCode.PARENT_MISSING.value in codes(report)
    assert CheckCode.PARENT_UNREACHABLE.value in codes(report)


def test_declared_parent_differs_from_physical_parent(draft_factory: DraftFactory) -> None:
    files = healthy()
    files["pages/sub/x.md"] = (
        "# X\n\n> 状态：`待讨论`\n>\n> 父主题：[根索引](../../index.md)\n\n正文。\n"
    )
    root = draft_factory(files)

    report = check_draft(root)

    mismatch = [
        issue for issue in report.errors if issue.code == CheckCode.LAYOUT_PARENT_MISMATCH.value
    ]
    assert len(mismatch) == 1
    assert mismatch[0].details == ("index.md", "pages/sub/index.md")


def test_parent_without_visible_navigation_back(draft_factory: DraftFactory) -> None:
    files = healthy()
    files["index.md"] = ROOT_PAGE.replace("- [问题](pages/question.md)\n", "")
    root = draft_factory(files)

    report = check_draft(root)

    assert codes(report) == [CheckCode.PARENT_NAVIGATION_MISSING.value]


def test_non_root_empty_hub(draft_factory: DraftFactory) -> None:
    files = healthy()
    files["pages/empty/index.md"] = (
        "# 空枢纽\n\n> 状态：`已关闭：纯导航页`\n>\n> 父主题：[根索引](../../index.md)\n"
    )
    root = draft_factory(files)

    report = check_draft(root)

    assert CheckCode.HUB_EMPTY.value in codes(report)


def test_dependency_self_loop(draft_factory: DraftFactory) -> None:
    files = healthy()
    files["pages/question.md"] = QUESTION.replace(
        "> 父主题：[根索引](../index.md)",
        "> 父主题：[根索引](../index.md)\n>\n> 依赖：[自己](question.md)",
    )
    root = draft_factory(files)

    report = check_draft(root)

    self_issues = [
        issue for issue in report.errors if issue.code == CheckCode.DEPENDENCY_SELF.value
    ]
    assert len(self_issues) == 1
    assert self_issues[0].path == Path("pages/question.md")
    assert self_issues[0].line == 7
    assert self_issues[0].details == ()


def test_dependency_cycle_reports_one_cycle(draft_factory: DraftFactory) -> None:
    files = healthy()
    files["pages/a.md"] = (
        "# A\n\n> 状态：`待讨论`\n>\n> 父主题：[根](../index.md)\n>\n"
        "> 依赖：[B](b.md)\n\n- [B](b.md)。\n"
    )
    files["pages/b.md"] = (
        "# B\n\n> 状态：`待讨论`\n>\n> 父主题：[根](../index.md)\n>\n"
        "> 依赖：[A](a.md)\n\n- [A](a.md)。\n"
    )
    root = draft_factory(files)

    report = check_draft(root)

    cycle_issues = [
        issue for issue in report.errors if issue.code == CheckCode.DEPENDENCY_CYCLE.value
    ]
    assert len(cycle_issues) == 1
    assert cycle_issues[0].details == ("pages/a.md", "pages/b.md", "pages/a.md")


def test_replacement_pair_missing(draft_factory: DraftFactory) -> None:
    files = healthy()
    files["pages/new.md"] = (
        "# 新\n\n> 状态：`待讨论`\n>\n> 父主题：[根](../index.md)\n>\n"
        "> 取代：[旧](question.md)\n\n正文。\n"
    )
    root = draft_factory(files)

    report = check_draft(root)

    pair = [
        issue for issue in report.errors if issue.code == CheckCode.REPLACEMENT_PAIR_MISSING.value
    ]
    assert len(pair) == 1
    assert pair[0].path == Path("pages/new.md")
    assert pair[0].line == 7
    assert pair[0].details == ("pages/question.md", "superseded_by")


def test_replacement_pair_missing_reverse_branch_uses_forward_label(
    draft_factory: DraftFactory,
) -> None:
    files = healthy()
    files["pages/old.md"] = (
        "# 旧\n\n> 状态：`待讨论`\n>\n> 父主题：[根](../index.md)\n>\n"
        "> 被取代：[新](new.md)\n\n正文。\n"
    )
    files["pages/new.md"] = "# 新\n\n> 状态：`待讨论`\n>\n> 父主题：[根](../index.md)\n\n正文。\n"
    files["index.md"] = ROOT_PAGE.replace(
        "- [问题](pages/question.md)",
        "- [问题](pages/question.md)\n- [新](pages/new.md)\n- [旧](pages/old.md)",
    )
    root = draft_factory(files)

    report = check_draft(root)

    pair = [
        issue for issue in report.errors if issue.code == CheckCode.REPLACEMENT_PAIR_MISSING.value
    ]
    assert len(pair) == 1
    assert pair[0].path == Path("pages/old.md")
    assert pair[0].line == 7
    assert pair[0].details == ("pages/new.md", "supersedes")


def test_superseded_status_mismatch(draft_factory: DraftFactory) -> None:
    files = healthy()
    files["pages/new.md"] = (
        "# 新\n\n> 状态：`待讨论`\n>\n> 父主题：[根](../index.md)\n>\n"
        "> 取代：[旧](question.md)\n\n正文。\n"
    )
    files["pages/question.md"] = QUESTION.replace("> 状态：`已确认`", "> 状态：`待讨论`").replace(
        "> 父主题：[根索引](../index.md)",
        "> 父主题：[根索引](../index.md)\n>\n> 被取代：[新](new.md)",
    )
    root = draft_factory(files)

    report = check_draft(root)

    mismatch = [
        issue
        for issue in report.errors
        if issue.code == CheckCode.REPLACEMENT_STATUS_MISMATCH.value
    ]
    assert len(mismatch) == 1
    assert mismatch[0].path == Path("pages/question.md")
    assert mismatch[0].line == 7
    assert mismatch[0].details == (ReplacementStatusReason.RELATION_REQUIRES_STATUS.value,)


def test_confirmed_status_without_superseded_by(draft_factory: DraftFactory) -> None:
    files = healthy()
    files["pages/old.md"] = "# 旧\n\n> 状态：`已取代`\n>\n> 父主题：[根](../index.md)\n\n正文。\n"
    root = draft_factory(files)

    report = check_draft(root)

    mismatch = [
        issue
        for issue in report.errors
        if issue.code == CheckCode.REPLACEMENT_STATUS_MISMATCH.value
    ]
    assert len(mismatch) == 1
    assert mismatch[0].path == Path("pages/old.md")
    assert mismatch[0].line is None
    assert mismatch[0].details == (ReplacementStatusReason.STATUS_REQUIRES_RELATION.value,)


def test_replacement_cycle(draft_factory: DraftFactory) -> None:
    files = healthy()
    files["pages/a.md"] = (
        "# A\n\n> 状态：`已取代`\n>\n> 父主题：[根](../index.md)\n>\n"
        "> 取代：[B](b.md)\n>\n> 被取代：[B](b.md)\n\n正文。\n"
    )
    files["pages/b.md"] = (
        "# B\n\n> 状态：`已取代`\n>\n> 父主题：[根](../index.md)\n>\n"
        "> 取代：[A](a.md)\n>\n> 被取代：[A](a.md)\n\n正文。\n"
    )
    root = draft_factory(files)

    report = check_draft(root)

    cycle_issues = [
        issue for issue in report.errors if issue.code == CheckCode.REPLACEMENT_CYCLE.value
    ]
    assert len(cycle_issues) == 1
    assert cycle_issues[0].details == ("pages/a.md", "pages/b.md", "pages/a.md")
    assert CheckCode.REPLACEMENT_PAIR_MISSING.value not in codes(report)
    assert CheckCode.REPLACEMENT_STATUS_MISMATCH.value not in codes(report)


def test_replacement_self_loop_is_a_cycle(draft_factory: DraftFactory) -> None:
    files = healthy()
    files["pages/a.md"] = (
        "# A\n\n> 状态：`已取代`\n>\n> 父主题：[根索引](../index.md)\n>\n"
        "> 取代：[A](a.md)\n>\n> 被取代：[A](a.md)\n\n正文。\n"
    )
    files["index.md"] = ROOT_PAGE.replace(
        "- [问题](pages/question.md)",
        "- [问题](pages/question.md)\n- [A](pages/a.md)",
    )
    root = draft_factory(files)

    report = check_draft(root)

    cycle = [issue for issue in report.errors if issue.code == CheckCode.REPLACEMENT_CYCLE.value]
    assert len(cycle) == 1
    assert cycle[0].details == ("pages/a.md", "pages/a.md")


def test_parent_self_loop_is_a_cycle(draft_factory: DraftFactory) -> None:
    files = healthy()
    files["pages/a.md"] = "# A\n\n> 状态：`待讨论`\n>\n> 父主题：[A](a.md)\n\n正文。\n"
    files["index.md"] = ROOT_PAGE.replace(
        "- [问题](pages/question.md)",
        "- [问题](pages/question.md)\n- [A](pages/a.md)",
    )
    root = draft_factory(files)

    report = check_draft(root)

    cycle = [issue for issue in report.errors if issue.code == CheckCode.PARENT_CYCLE.value]
    assert len(cycle) == 1
    assert cycle[0].details == ("pages/a.md", "pages/a.md")


def test_challenge_pair_missing(draft_factory: DraftFactory) -> None:
    files = healthy()
    files["pages/challenger.md"] = (
        "# 质疑者\n\n> 状态：`待讨论`\n>\n> 父主题：[根](../index.md)\n>\n"
        "> 质疑：[问题](question.md)\n\n正文。\n"
    )
    root = draft_factory(files)

    report = check_draft(root)

    pair = [
        issue for issue in report.errors if issue.code == CheckCode.CHALLENGE_PAIR_MISSING.value
    ]
    assert len(pair) == 1
    assert pair[0].path == Path("pages/challenger.md")
    assert pair[0].line == 7
    assert pair[0].details == ("pages/question.md", "challenged_by")


def test_challenge_pair_missing_reverse_branch_uses_forward_label(
    draft_factory: DraftFactory,
) -> None:
    files = healthy()
    files["pages/old.md"] = (
        "# 旧\n\n> 状态：`待讨论`\n>\n> 父主题：[根](../index.md)\n>\n"
        "> 被质疑：[新](new.md)\n\n正文。\n"
    )
    files["pages/new.md"] = "# 新\n\n> 状态：`待讨论`\n>\n> 父主题：[根](../index.md)\n\n正文。\n"
    files["index.md"] = ROOT_PAGE.replace(
        "- [问题](pages/question.md)",
        "- [问题](pages/question.md)\n- [新](pages/new.md)\n- [旧](pages/old.md)",
    )
    root = draft_factory(files)

    report = check_draft(root)

    pair = [
        issue for issue in report.errors if issue.code == CheckCode.CHALLENGE_PAIR_MISSING.value
    ]
    assert len(pair) == 1
    assert pair[0].path == Path("pages/old.md")
    assert pair[0].line == 7
    assert pair[0].details == ("pages/new.md", "challenges")


def test_confirmed_page_with_open_challenge_is_accepted(draft_factory: DraftFactory) -> None:
    files = healthy()
    files["pages/challenger.md"] = (
        "# 质疑者\n\n> 状态：`待讨论`\n>\n> 父主题：[根](../index.md)\n>\n"
        "> 质疑：[问题](question.md)\n\n- [问题](question.md)。\n"
    )
    files["pages/question.md"] = QUESTION.replace(
        "> 父主题：[根索引](../index.md)",
        "> 父主题：[根索引](../index.md)\n>\n> 被质疑：[质疑者](challenger.md)",
    )
    files["index.md"] = ROOT_PAGE.replace(
        "- [问题](pages/question.md)",
        "- [问题](pages/question.md)\n- [质疑者](pages/challenger.md)",
    )
    root = draft_factory(files)

    report = check_draft(root)

    assert report.errors == ()


def test_oversized_ordinary_page_is_review_only(draft_factory: DraftFactory) -> None:
    files = healthy()
    padding = "\n".join(f"第 {index} 行补充材料。" for index in range(610))
    files["pages/question.md"] = QUESTION + padding + "\n"
    root = draft_factory(files)

    report = check_draft(root)

    assert report.errors == ()
    assert [issue.code for issue in report.reviews] == [CheckCode.PAGE_CAPACITY.value]
    assert report.reviews[0].severity is IssueSeverity.REVIEW
    assert report.reviews[0].details == (
        "618",
        str(len(files["pages/question.md"].encode("utf-8"))),
    )


def test_oversized_conclusions_page_is_exempt(draft_factory: DraftFactory) -> None:
    files = healthy()
    padding = "\n".join(f"<!-- 填充 {index} -->" for index in range(610))
    files["conclusions.md"] = CONCLUSIONS + padding + "\n"
    root = draft_factory(files)

    report = check_draft(root)

    assert report.errors == ()
    assert report.reviews == ()


def test_markdown_in_non_pages_subdirectory_is_unmanaged(
    draft_factory: DraftFactory,
) -> None:
    files = healthy()
    files["notes/hidden.md"] = "# 隐藏\n\n> 状态：`待讨论`\n\n隐藏笔记。\n"
    root = draft_factory(files)

    report = check_draft(root)

    assert codes(report) == [CheckCode.PAGE_UNMANAGED_PATH.value]
    assert report.errors[0].path == Path("notes/hidden.md")


def test_nested_non_pages_markdown_and_symlink_dir(
    draft_factory: DraftFactory, tmp_path: Path
) -> None:
    files = healthy()
    files["notes/deep/hidden.md"] = "# 深藏\n\n> 状态：`待讨论`\n\n深。\n"
    root = draft_factory(files)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "escape.md").write_text("# 外\n", encoding="utf-8")
    (root / "linked-dir").symlink_to(outside, target_is_directory=True)

    report = check_draft(root)

    assert CheckCode.PAGE_UNMANAGED_PATH.value in codes(report)
    assert CheckCode.LINK_ESCAPE.value in codes(report)
    assert not any(issue.path == Path("linked-dir/escape.md") for issue in report.errors)


def test_duplicate_normalized_relation_target_is_an_error(
    draft_factory: DraftFactory,
) -> None:
    files = healthy()
    files["pages/question.md"] = QUESTION.replace(
        "> 父主题：[根索引](../index.md)",
        "> 父主题：[根索引](../index.md)\n>\n> 依赖：[其他](other.md)\n>\n"
        "> 依赖：[其他副本](./other.md)",
    )
    files["pages/other.md"] = (
        "# 其他\n\n> 状态：`待讨论`\n>\n> 父主题：[根索引](../index.md)\n\n其他。\n"
    )
    files["index.md"] = ROOT_PAGE.replace(
        "- [问题](pages/question.md)",
        "- [问题](pages/question.md)\n- [其他](pages/other.md)",
    )
    root = draft_factory(files)

    report = check_draft(root)

    assert codes(report) == [CheckCode.RELATION_DUPLICATE.value]
    issue = report.errors[0]
    assert issue.path == Path("pages/question.md")
    assert issue.line == 9
    assert issue.details == ("depends_on", "pages/other.md")


def test_same_label_with_distinct_targets_is_allowed(
    draft_factory: DraftFactory,
) -> None:
    files = healthy()
    files["pages/question.md"] = QUESTION.replace(
        "> 父主题：[根索引](../index.md)",
        "> 父主题：[根索引](../index.md)\n>\n> 依赖：[甲](other.md)\n>\n> 依赖：[乙](second.md)",
    )
    files["pages/other.md"] = (
        "# 其他\n\n> 状态：`待讨论`\n>\n> 父主题：[根索引](../index.md)\n\n其他。\n"
    )
    files["pages/second.md"] = (
        "# 第二\n\n> 状态：`待讨论`\n>\n> 父主题：[根索引](../index.md)\n\n第二。\n"
    )
    files["index.md"] = ROOT_PAGE.replace(
        "- [问题](pages/question.md)",
        "- [问题](pages/question.md)\n- [其他](pages/other.md)\n- [第二](pages/second.md)",
    )
    root = draft_factory(files)

    report = check_draft(root)

    assert report.errors == ()


# ---------------------------------------------------------------------------
# 回收生命周期诊断矩阵（设计 §13/§14）：trash_retained、trash_handoff_pending、
# 权威包有效性与投递失败记录的严重级别边界。
# ---------------------------------------------------------------------------

CHECKER_TRASH_INDEX = """# Topic

> 状态：`已关闭：纯导航页，不表达独立结论`

## 子主题

- [结论汇总](conclusions.md)
- [问题](pages/question.md)
- [旧页](pages/obsolete.md)
"""

CHECKER_TRASH_CONCLUSIONS = """# 结论汇总

> 状态：`已确认`
>
> 父主题：[根索引](index.md)

- [问题](pages/question.md)：问题。
"""

CHECKER_QUESTION = """# 问题

> 状态：`已确认`
>
> 父主题：[根索引](../index.md)

问题。
"""

CHECKER_OBSOLETE = """# 旧页

> 状态：`待讨论`
>
> 父主题：[根索引](../index.md)

旧页的正文。
"""


def _trashable_checker_files() -> dict[str, str]:
    return {
        "index.md": CHECKER_TRASH_INDEX,
        "conclusions.md": CHECKER_TRASH_CONCLUSIONS,
        "pages/question.md": CHECKER_QUESTION,
        "pages/obsolete.md": CHECKER_OBSOLETE,
    }


def _build_trash_state(
    root: Path,
    *,
    phase: str,
    created_at: datetime,
    failure: bool = False,
    package: str = "valid",
) -> str:
    """构造真实权威删除包 + 日志，再改写为指定生命周期阶段与期限。

    通过 ``_trash_plan`` + ``execute_plan`` 产出锚定到真实 T mutation 的权威包，
    然后改写日志阶段与期限/失败记录。``package`` 控制包保持有效、被删除或被破坏。
    """
    import json
    import shutil

    from draft.graph import load_graph
    from draft.operations import (
        TrashDeadlines,
        execute_plan,
        read_lock,
        release_lock,
    )
    from draft.trash import trash_plan
    from draft.trash_package import OUTGOING_RELATIVE

    graph = load_graph(root)
    plan, _package_relative = trash_plan(graph, Path("pages/obsolete.md"))
    execute_plan(plan)
    operation_id = plan.metadata["operation_id"]
    if read_lock(root) == operation_id:
        release_lock(root, operation_id)

    journal_path = root / ".draft/operations" / operation_id / "journal.json"
    data = json.loads(journal_path.read_text(encoding="utf-8"))
    metadata: dict[str, str] = dict(data["metadata"])
    metadata.update(TrashDeadlines.from_created_at(created_at).to_metadata())
    attempted = TrashDeadlines.from_created_at(created_at).to_metadata()["created_at"]
    if failure:
        metadata["handoff_error_type"] = "OSError"
        metadata["handoff_error_message"] = "recycle bin unavailable"
        metadata["handoff_attempted_at"] = attempted
    else:
        metadata.pop("handoff_error_type", None)
        metadata.pop("handoff_error_message", None)
        metadata.pop("handoff_attempted_at", None)
    data["phase"] = phase
    data["metadata"] = metadata
    journal_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    package_dir = root / OUTGOING_RELATIVE / operation_id
    if package == "missing" and package_dir.exists():
        shutil.rmtree(package_dir)
    elif package == "invalid" and (package_dir / "package.json").is_file():
        (package_dir / "package.json").write_text("corrupt\n", encoding="utf-8")
    return operation_id


def _codes_for(report: CheckReport, operation_id: str, severity: IssueSeverity) -> list[str]:
    return [
        issue.code
        for issue in report.issues
        if issue.severity is severity and issue.details == (operation_id,)
    ]


def test_checker_trash_retained_before_expiry_is_info(
    draft_factory: DraftFactory,
) -> None:
    root = draft_factory(_trashable_checker_files())
    created = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)
    operation_id = _build_trash_state(root, phase="trash_retained", created_at=created)

    before = snapshot_tree(root)
    report = check_draft(root)
    assert snapshot_tree(root) == before

    assert _codes_for(report, operation_id, IssueSeverity.INFO) == [CheckCode.TRASH_RETAINED.value]
    assert _codes_for(report, operation_id, IssueSeverity.ERROR) == []
    assert _codes_for(report, operation_id, IssueSeverity.WARNING) == []


def test_checker_trash_retained_after_expiry_is_still_info(
    draft_factory: DraftFactory,
) -> None:
    root = draft_factory(_trashable_checker_files())
    # created 8 天前：restore_expires_at（created+7d）已过，但尚未被惰性推进，仍为合法 INFO。
    created = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    operation_id = _build_trash_state(root, phase="trash_retained", created_at=created)

    before = snapshot_tree(root)
    report = check_draft(root)
    assert snapshot_tree(root) == before

    assert _codes_for(report, operation_id, IssueSeverity.INFO) == [CheckCode.TRASH_RETAINED.value]
    assert _codes_for(report, operation_id, IssueSeverity.ERROR) == []


def test_checker_pending_with_failure_record_is_warning_before_next_attempt(
    draft_factory: DraftFactory,
) -> None:
    root = draft_factory(_trashable_checker_files())
    created = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)
    # next_handoff_attempt_at = created + 7d（2026-07-28），尚未到点。
    operation_id = _build_trash_state(
        root, phase="trash_handoff_pending", created_at=created, failure=True
    )

    before = snapshot_tree(root)
    report = check_draft(root)
    assert snapshot_tree(root) == before

    assert _codes_for(report, operation_id, IssueSeverity.WARNING) == [
        CheckCode.TRASH_HANDOFF_PENDING.value
    ]
    assert _codes_for(report, operation_id, IssueSeverity.ERROR) == []


def test_checker_pending_with_failure_record_is_warning_after_next_attempt(
    draft_factory: DraftFactory,
) -> None:
    root = draft_factory(_trashable_checker_files())
    # created 远在过去：next_handoff_attempt_at 已过，到点只改变可推进性，不改变严重级别。
    created = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    operation_id = _build_trash_state(
        root, phase="trash_handoff_pending", created_at=created, failure=True
    )

    before = snapshot_tree(root)
    report = check_draft(root)
    assert snapshot_tree(root) == before

    assert _codes_for(report, operation_id, IssueSeverity.WARNING) == [
        CheckCode.TRASH_HANDOFF_PENDING.value
    ]
    assert _codes_for(report, operation_id, IssueSeverity.ERROR) == []


def test_checker_pending_without_failure_record_is_error(
    draft_factory: DraftFactory,
) -> None:
    root = draft_factory(_trashable_checker_files())
    created = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)
    operation_id = _build_trash_state(
        root, phase="trash_handoff_pending", created_at=created, failure=False
    )

    before = snapshot_tree(root)
    report = check_draft(root)
    assert snapshot_tree(root) == before

    assert _codes_for(report, operation_id, IssueSeverity.ERROR) == [
        CheckCode.TRASH_PENDING_UNEXPLAINED.value
    ]
    assert _codes_for(report, operation_id, IssueSeverity.WARNING) == []


def test_checker_retained_with_missing_package_is_error(
    draft_factory: DraftFactory,
) -> None:
    root = draft_factory(_trashable_checker_files())
    created = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)
    operation_id = _build_trash_state(
        root, phase="trash_retained", created_at=created, package="missing"
    )

    before = snapshot_tree(root)
    report = check_draft(root)
    assert snapshot_tree(root) == before

    assert _codes_for(report, operation_id, IssueSeverity.ERROR) == [
        CheckCode.TRASH_PACKAGE_MISSING.value
    ]
    assert _codes_for(report, operation_id, IssueSeverity.INFO) == []


def test_checker_retained_with_invalid_package_is_error(
    draft_factory: DraftFactory,
) -> None:
    root = draft_factory(_trashable_checker_files())
    created = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)
    operation_id = _build_trash_state(
        root, phase="trash_retained", created_at=created, package="invalid"
    )

    before = snapshot_tree(root)
    report = check_draft(root)
    assert snapshot_tree(root) == before

    assert _codes_for(report, operation_id, IssueSeverity.ERROR) == [
        CheckCode.TRASH_PACKAGE_CORRUPT.value
    ]
    assert _codes_for(report, operation_id, IssueSeverity.INFO) == []


def test_checker_pending_with_failure_but_missing_package_is_error(
    draft_factory: DraftFactory,
) -> None:
    root = draft_factory(_trashable_checker_files())
    created = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)
    operation_id = _build_trash_state(
        root,
        phase="trash_handoff_pending",
        created_at=created,
        failure=True,
        package="missing",
    )

    before = snapshot_tree(root)
    report = check_draft(root)
    assert snapshot_tree(root) == before

    assert _codes_for(report, operation_id, IssueSeverity.ERROR) == [
        CheckCode.TRASH_PACKAGE_MISSING.value
    ]
    assert _codes_for(report, operation_id, IssueSeverity.WARNING) == []


# ---------------------------------------------------------------------------
# 检查器只读性（设计 §13.5）：check 与 check --candidate 不推进、不改动任何字节。
# ---------------------------------------------------------------------------


def test_check_does_not_change_due_or_failed_package(
    draft_factory: DraftFactory,
) -> None:
    root = draft_factory(
        {
            "index.md": CHECKER_TRASH_INDEX,
            "conclusions.md": CHECKER_TRASH_CONCLUSIONS,
            "pages/question.md": CHECKER_QUESTION,
            "pages/obsolete.md": CHECKER_OBSOLETE,
        }
    )
    # 创建一个已到期、且带失败记录的 trash_handoff_pending 包。
    pending = trash_page(root, Path("pages/obsolete.md"), now=datetime(2026, 7, 1, tzinfo=UTC))
    import json

    from draft.operations import TrashDeadlines

    journal_path = root / ".draft/operations" / pending.operation_id / "journal.json"
    data = json.loads(journal_path.read_text(encoding="utf-8"))
    data["phase"] = "trash_handoff_pending"
    data["metadata"].update(
        TrashDeadlines.from_created_at(datetime(2026, 7, 1, tzinfo=UTC)).to_metadata()
    )
    data["metadata"]["handoff_error_type"] = "OSError"
    data["metadata"]["handoff_error_message"] = "recycle bin unavailable"
    data["metadata"]["handoff_attempted_at"] = data["metadata"]["restore_expires_at"]
    journal_path.write_text(json.dumps(data), encoding="utf-8")

    before = snapshot_tree(root)
    check_draft(root)
    assert snapshot_tree(root) == before
    # 候选检查对登记根只读拒绝，同样不改动字节。
    report_candidate = check_draft(root, mode="candidate")
    assert snapshot_tree(root) == before
    assert [issue.code for issue in report_candidate.errors] == [
        CheckCode.CANDIDATE_ALREADY_REGISTERED.value
    ]


def test_checker_retained_with_invalid_deadlines_is_error(
    draft_factory: DraftFactory,
) -> None:
    """期限字段缺失/损坏时，检查器不得把 trash_retained 报为合法 INFO。"""
    root = draft_factory(_trashable_checker_files())
    created = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)
    operation_id = _build_trash_state(root, phase="trash_retained", created_at=created)
    # 删除 created_at：期限不再可解析。
    import json

    journal_path = root / ".draft/operations" / operation_id / "journal.json"
    data = json.loads(journal_path.read_text(encoding="utf-8"))
    data["metadata"].pop("created_at", None)
    journal_path.write_text(json.dumps(data), encoding="utf-8")

    before = snapshot_tree(root)
    report = check_draft(root)
    assert snapshot_tree(root) == before

    assert _codes_for(report, operation_id, IssueSeverity.ERROR) == [
        CheckCode.TRASH_DEADLINES_INVALID.value
    ]
    assert _codes_for(report, operation_id, IssueSeverity.INFO) == []
