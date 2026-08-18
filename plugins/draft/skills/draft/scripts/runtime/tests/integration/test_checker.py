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
from draft.models import CheckIssue, CheckReport, IssueSeverity, OperationPhase, RootState
from draft.operations import JOURNAL_VERSION, Journal
from draft.trash import trash_page
from draft.vocabulary import ConclusionsState
from tests.integration.support import (
    DraftFactory,
    managed_marker_text,
    snapshot_tree,
    write_raw_journal,
)

ROOT_PAGE = """# Topic

> 状态：`已关闭：纯导航页，不表达独立结论`

## 子主题

- [问题](pages/question.md)
- [结论汇总](conclusions.md)
"""

CONCLUSIONS = """# 结论汇总

> 状态：`已确认`
>
> 父主题：[根索引](index.md)

## 已确认结论

- [已确认的问题](pages/question.md)
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
    root = draft_factory(healthy(), root_state=None)

    report = check_draft(root)

    assert codes(report) == [CheckCode.FORMAT_MISSING.value]


def test_registered_mode_accepts_marker_without_runtime_directories(
    draft_factory: DraftFactory,
) -> None:
    """最小受管根（marker 存在、无 operations/trash 按需目录）通过检查。"""
    root = draft_factory(healthy())

    report = check_draft(root)

    assert report.errors == ()
    assert (root / ".draft/draft.json").is_file()
    assert not (root / ".draft/operations").exists()
    assert not (root / ".draft/trash").exists()


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

    assert CheckCode.PATH_SYMLINK_ESCAPE.value in codes(report)


@pytest.mark.parametrize("root_state", tuple(RootState))
def test_managed_roots_share_operation_state_diagnostics(
    draft_factory: DraftFactory,
    root_state: RootState,
) -> None:
    """candidate 与 registered 共享操作日志、锁与内部命名空间协议（规格 §5.4）。"""
    root = draft_factory(healthy(), root_state=root_state)
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


@pytest.mark.parametrize("root_state", tuple(RootState))
def test_managed_roots_share_internal_namespace_rules(
    draft_factory: DraftFactory,
    root_state: RootState,
) -> None:
    root = draft_factory(healthy(), root_state=root_state)
    (root / ".draft" / "junk.json").write_text("x", encoding="utf-8")

    report = check_draft(root)

    assert CheckCode.INTERNAL_UNEXPECTED_ENTRY.value in codes(report)


@pytest.mark.parametrize("root_state", tuple(RootState))
def test_non_directory_entry_in_operations_is_reported(
    draft_factory: DraftFactory,
    root_state: RootState,
) -> None:
    """operations/ 下的非目录条目是内部协议非法形状，不能静默跳过（规格 §5.4）。"""
    root = draft_factory(healthy(), root_state=root_state, precreate_internal=True)
    (root / ".draft" / "operations" / "garbage").write_text("x", encoding="utf-8")

    report = check_draft(root)

    assert CheckCode.INTERNAL_UNEXPECTED_ENTRY.value in codes(report)


@pytest.mark.parametrize("root_state", tuple(RootState))
def test_operations_path_as_regular_file_is_reported(
    draft_factory: DraftFactory,
    root_state: RootState,
) -> None:
    """``.draft/operations`` 本身是普通文件时报告内部形状损坏，不得当作空目录。"""
    root = draft_factory(healthy(), root_state=root_state, precreate_internal=True)
    operations = root / ".draft" / "operations"
    operations.rmdir()
    operations.write_text("not a directory", encoding="utf-8")

    report = check_draft(root)

    assert CheckCode.INTERNAL_UNEXPECTED_ENTRY.value in codes(report)


@pytest.mark.parametrize("root_state", tuple(RootState))
def test_trash_path_as_regular_file_is_reported(
    draft_factory: DraftFactory,
    root_state: RootState,
) -> None:
    """``.draft/trash`` 本身是普通文件时报告内部形状损坏。"""
    import shutil

    root = draft_factory(healthy(), root_state=root_state)
    trash = root / ".draft" / "trash"
    if trash.exists():
        shutil.rmtree(trash)
    trash.write_text("not a directory", encoding="utf-8")

    report = check_draft(root)

    assert CheckCode.INTERNAL_UNEXPECTED_ENTRY.value in codes(report)


@pytest.mark.parametrize("root_state", tuple(RootState))
def test_orphan_trash_package_without_holding_journal_is_reported(
    draft_factory: DraftFactory,
    root_state: RootState,
) -> None:
    """未被任何持有中 trash 日志引用的权威包是孤儿：反向闭合扫描必须报告。"""
    root = draft_factory(healthy(), root_state=root_state)
    package = root / ".draft/trash/outgoing" / ("a" * 32)
    package.mkdir(parents=True)
    (package / "package.json").write_text("{}", encoding="utf-8")

    report = check_draft(root)

    assert CheckCode.INTERNAL_UNEXPECTED_ENTRY.value in codes(report)


@pytest.mark.parametrize("root_state", tuple(RootState))
def test_trash_outgoing_as_regular_file_is_reported(
    draft_factory: DraftFactory,
    root_state: RootState,
) -> None:
    """``.draft/trash/outgoing`` 本身是普通文件时报告内部形状损坏。"""
    import shutil

    root = draft_factory(healthy(), root_state=root_state)
    trash = root / ".draft/trash"
    if trash.exists():
        shutil.rmtree(trash)
    trash.mkdir()
    (trash / "outgoing").write_text("not a directory", encoding="utf-8")

    report = check_draft(root)

    assert CheckCode.INTERNAL_UNEXPECTED_ENTRY.value in codes(report)


@pytest.mark.parametrize("root_state", tuple(RootState))
def test_trash_unknown_entry_is_reported(
    draft_factory: DraftFactory,
    root_state: RootState,
) -> None:
    """``.draft/trash`` 下除 outgoing 外的条目非法。"""
    root = draft_factory(healthy(), root_state=root_state, precreate_internal=True)
    (root / ".draft/trash/junk").write_text("x", encoding="utf-8")

    report = check_draft(root)

    assert CheckCode.INTERNAL_UNEXPECTED_ENTRY.value in codes(report)


@pytest.mark.parametrize("root_state", tuple(RootState))
def test_trash_outgoing_regular_file_entry_is_reported(
    draft_factory: DraftFactory,
    root_state: RootState,
) -> None:
    """``outgoing`` 下非目录条目非法。"""
    root = draft_factory(healthy(), root_state=root_state, precreate_internal=True)
    (root / ".draft/trash/outgoing/garbage").write_text("x", encoding="utf-8")

    report = check_draft(root)

    assert CheckCode.INTERNAL_UNEXPECTED_ENTRY.value in codes(report)


@pytest.mark.parametrize("root_state", tuple(RootState))
def test_trash_outgoing_invalid_name_directory_is_reported(
    draft_factory: DraftFactory,
    root_state: RootState,
) -> None:
    """``outgoing`` 下包目录名不是合法 operation_id 时非法。"""
    root = draft_factory(healthy(), root_state=root_state, precreate_internal=True)
    (root / ".draft/trash/outgoing/not-hex").mkdir()

    report = check_draft(root)

    assert CheckCode.INTERNAL_UNEXPECTED_ENTRY.value in codes(report)


@pytest.mark.parametrize("root_state", tuple(RootState))
def test_held_trash_package_is_not_reported(
    draft_factory: DraftFactory,
    root_state: RootState,
) -> None:
    """被持有中 trash 日志引用的权威包不误报为孤儿。"""
    from datetime import UTC, datetime

    from draft.trash import trash_page

    root = draft_factory(healthy(), root_state=root_state)
    trash_page(root, Path("pages/question.md"), now=datetime(2026, 7, 21, 12, tzinfo=UTC))

    report = check_draft(root)

    assert CheckCode.INTERNAL_UNEXPECTED_ENTRY.value not in codes(report)


def test_provisional_shape_in_final_marker_is_invalid(
    draft_factory: DraftFactory,
) -> None:
    """正式 marker 不接受 provisional 键：旧内嵌 provisional 形状现在是 format.invalid。"""
    root = draft_factory(healthy())
    (root / ".draft/draft.json").write_text(
        '{"format_version":1,"root_state":"candidate","conclusions_state":"not_generated","adopt_state":"provisional","candidate_manifest":[]}\n',
        encoding="utf-8",
    )

    report = check_draft(root)

    assert CheckCode.FORMAT_INVALID.value in codes(report)


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
        managed_marker_text(conclusions_state=ConclusionsState.GENERATED), encoding="utf-8"
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


def _generated_conclusions_root(draft_factory: DraftFactory) -> Path:
    """已生成所有权的草稿：固定形状的结论页 + 结论链接位于最后的规范根导航。"""
    files = healthy()
    files["index.md"] = (
        ROOT_PAGE.replace("- [结论汇总](conclusions.md)\n", "") + "- [结论汇总](conclusions.md)\n"
    )
    files["conclusions.md"] = (
        "# 结论汇总\n\n> 状态：`当前提案`\n>\n> 父主题：[根索引](index.md)\n\n"
        "## 已确认结论\n\n- [问题](pages/question.md)\n"
    )
    root = draft_factory(files)
    (root / ".draft/draft.json").write_text(
        managed_marker_text(conclusions_state=ConclusionsState.GENERATED), encoding="utf-8"
    )
    return root


def test_not_generated_unexpected_conclusions_file_is_error(
    draft_factory: DraftFactory,
) -> None:
    """规格 §12.6：not_generated 时意外出现 conclusions.md 必须报告，不当作普通页。"""
    files = healthy()
    del files["conclusions.md"]
    files["index.md"] = ROOT_PAGE.replace("- [结论汇总](conclusions.md)\n", "")
    files["conclusions.md"] = CONCLUSIONS
    root = draft_factory(files)
    (root / ".draft/draft.json").write_text(managed_marker_text(), encoding="utf-8")

    report = check_draft(root)

    assert CheckCode.CONCLUSIONS_UNEXPECTED_FILE.value in codes(report)
    # 不把意外机器文件当作普通页面：不应出现缺父主题/不可达等误导诊断。
    assert CheckCode.PARENT_MISSING.value not in codes(report)
    assert CheckCode.PARENT_UNREACHABLE.value not in codes(report)


def test_generated_conclusions_shape_change_is_error(
    draft_factory: DraftFactory,
) -> None:
    root = _generated_conclusions_root(draft_factory)
    assert check_draft(root).errors == ()

    (root / "conclusions.md").write_text(
        (root / "conclusions.md").read_text(encoding="utf-8").replace("# 结论汇总", "# 任意标题"),
        encoding="utf-8",
    )

    report = check_draft(root)

    assert CheckCode.CONCLUSIONS_SHAPE_INVALID.value in codes(report)


def test_generated_conclusions_nav_link_deletion_is_error(
    draft_factory: DraftFactory,
) -> None:
    root = _generated_conclusions_root(draft_factory)
    (root / "index.md").write_text(
        (root / "index.md")
        .read_text(encoding="utf-8")
        .replace("- [结论汇总](conclusions.md)\n", ""),
        encoding="utf-8",
    )

    report = check_draft(root)

    assert CheckCode.CONCLUSIONS_NAVIGATION_INVALID.value in codes(report)


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
    files["pages/question.md"] = QUESTION + "[甲](pages/a.md) [乙](pages/b.md)\n"
    report = check_draft(draft_factory(files))
    issues = [i for i in report.errors if i.code == CheckCode.LINK_MISSING.value]
    assert [(i.line, i.details) for i in issues] == [
        (8, ("pages/a.md", "pages/pages/a.md")),
        (8, ("pages/b.md", "pages/pages/b.md")),
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
    # 填充必须保持固定列表形状，否则 shape_invalid 会掩盖容量豁免断言。
    padding = "\n".join(f"- [填充 {index}](pages/fill.md)" for index in range(610))
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

- [问题](pages/question.md)
- [旧页](pages/obsolete.md)
- [结论汇总](conclusions.md)
"""

CHECKER_TRASH_CONCLUSIONS = """# 结论汇总

> 状态：`已确认`
>
> 父主题：[根索引](index.md)

## 已确认结论

- [问题](pages/question.md)
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
    from draft.models import RootState
    from draft.observations import graph_observations

    plan, _package_relative = trash_plan(
        graph,
        Path("pages/obsolete.md"),
        root_state=RootState.REGISTERED,
        observations=graph_observations(graph),
    )
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


@pytest.mark.parametrize("root_state", tuple(RootState))
def test_terminal_operation_residual_pending_is_reported(
    draft_factory: DraftFactory,
    root_state: RootState,
) -> None:
    """终态操作目录残留崩溃 pending：check 必须报告，不得当作无痕（回归 H4）。"""
    root = draft_factory(healthy(), root_state=root_state)
    journal_path = write_raw_journal(root, version=JOURNAL_VERSION)
    (journal_path.parent / "journal.json.pending").write_text("stale\n", encoding="utf-8")

    report = check_draft(root)

    assert CheckCode.INTERNAL_UNEXPECTED_ENTRY.value in codes(report)


@pytest.mark.parametrize("root_state", tuple(RootState))
def test_terminal_operation_leftover_originals_is_reported(
    draft_factory: DraftFactory,
    root_state: RootState,
) -> None:
    """终态操作目录残留恢复副本：check 必须报告（回归 H4 终态残留可见）。"""
    root = draft_factory(healthy(), root_state=root_state)
    journal_path = write_raw_journal(root, version=JOURNAL_VERSION)
    (journal_path.parent / "originals").mkdir()

    report = check_draft(root)

    assert CheckCode.INTERNAL_UNEXPECTED_ENTRY.value in codes(report)


@pytest.mark.parametrize("root_state", tuple(RootState))
def test_evidence_node_as_directory_is_reported(
    draft_factory: DraftFactory,
    root_state: RootState,
) -> None:
    """cleanup-evidence.json 节点是目录：check 报告错误 evidence（回归 H4）。"""
    root = draft_factory(healthy(), root_state=root_state)
    journal_path = write_raw_journal(root, version=JOURNAL_VERSION)
    (journal_path.parent / "cleanup-evidence.json").mkdir()

    report = check_draft(root)

    assert CheckCode.INTERNAL_UNEXPECTED_ENTRY.value in codes(report)


@pytest.mark.parametrize("root_state", tuple(RootState))
def test_checker_reports_invalid_evidence_names_without_mutating(
    draft_factory: DraftFactory, root_state: RootState
) -> None:
    """checker 对 final/pending/quarantine 内容损坏可见且始终只读（回归 H1）。"""
    import json as json_module

    root = draft_factory(healthy(), root_state=root_state)
    op_id = "b" * 32
    op_dir = root / ".draft/operations" / op_id
    op_dir.mkdir(parents=True)
    journal: dict[str, object] = {
        "journal_version": JOURNAL_VERSION,
        "operation_id": op_id,
        "kind": "trash",
        "phase": "trash_handoff_succeeded",
        "root_state": "registered",
        "observations": [],
        "mutations": [],
        "remove_empty_directories": [],
        "metadata": {},
    }
    (op_dir / "journal.json").write_text(json_module.dumps(journal), encoding="utf-8")
    garbage = b'{"not": "evidence"}'
    for name in (
        "cleanup-evidence.json",
        "cleanup-evidence.json.pending",
        "cleanup-evidence.json.quarantine",
    ):
        (op_dir / name).write_bytes(garbage)

    report = check_draft(root)

    # 联合分类一次性报告损坏：三名同一联合状态只有一个 trash.package_corrupt。
    corrupt = codes(report).count(CheckCode.TRASH_PACKAGE_CORRUPT.value)
    assert corrupt >= 1
    # checker 只读：三名 bytes 完全不变。
    for name in (
        "cleanup-evidence.json",
        "cleanup-evidence.json.pending",
        "cleanup-evidence.json.quarantine",
    ):
        assert (op_dir / name).read_bytes() == garbage


# ---------------------------------------------------------------------------
# 第十一轮 H2/M2：checker 与运行时共用 cleanup evidence 联合决策；分类/读取错误稳定。
# ---------------------------------------------------------------------------


def build_cleanup_evidence_checker_case(
    draft_factory: DraftFactory,
) -> tuple[Path, Path, bytes]:
    """真实 TRASH 日志 + 权威包 + 合法 cleanup evidence 字节（联合状态测试夹具）。

    通过真实 trash_page 建立 T，把阶段改写为已提交清理点，按同一 T 授权构造证据，
    再由 read_journal 取操作目录；checker 与运行时共用同一 parse_cleanup_evidence。
    """
    import base64
    import json

    from draft.operations import operation_journal_path, read_journal
    from draft.trash_package import parse_package_authorization

    root = draft_factory(_trashable_checker_files())
    result = trash_page(
        root,
        Path("pages/obsolete.md"),
        now=datetime(2026, 7, 21, 12, 0, tzinfo=UTC),
    )
    journal_path = operation_journal_path(root, result.operation_id)
    raw = json.loads(journal_path.read_text(encoding="utf-8"))
    raw["phase"] = OperationPhase.TRASH_HANDOFF_SUCCEEDED.value
    journal_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    journal = read_journal(journal_path)
    package = root / ".draft/trash/outgoing" / result.operation_id
    authorization = parse_package_authorization(journal, package)
    leaves: list[dict[str, object]] = []
    for relative, digest in authorization.leaves():
        content = (root / relative).read_bytes()
        leaves.append(
            {
                "relative": relative.as_posix(),
                "sha256": digest,
                "state": "present",
                "content": base64.b64encode(content).decode("ascii"),
            }
        )
    evidence = {
        "version": 2,
        "operation_id": result.operation_id,
        "cleanup_phase": journal.phase.value,
        "leaves": leaves,
    }
    return root, journal.directory, json.dumps(evidence, ensure_ascii=False).encode("utf-8")


def write_cleanup_evidence_names(
    operation_dir: Path,
    evidence_bytes: bytes,
    names: tuple[str, ...],
    *,
    same_inode: bool,
) -> None:
    """按角色名写入证据；same_inode 用 os.link 建立硬链接证明同一次发布/隔离。"""
    protocol_names = {
        "final": "cleanup-evidence.json",
        "pending": "cleanup-evidence.json.pending",
        "quarantine": "cleanup-evidence.json.quarantine",
    }
    first = operation_dir / protocol_names[names[0]]
    first.write_bytes(evidence_bytes)
    for role in names[1:]:
        target = operation_dir / protocol_names[role]
        if same_inode:
            target.hardlink_to(first)
        else:
            target.write_bytes(evidence_bytes)


@pytest.mark.parametrize(
    ("names", "same_inode", "corrupt"),
    [
        (("final",), False, False),
        (("pending",), False, False),
        (("quarantine",), False, False),
        (("final", "pending"), True, False),
        (("final", "pending"), False, True),
        (("final", "quarantine"), True, False),
        (("final", "quarantine"), False, True),
        (("pending", "quarantine"), False, True),
        (("final", "pending", "quarantine"), False, True),
    ],
)
def test_checker_matches_cleanup_evidence_joint_protocol(
    draft_factory: DraftFactory,
    names: tuple[str, ...],
    same_inode: bool,
    corrupt: bool,
) -> None:
    """checker 对联合 evidence 状态的损坏结论与运行时纯决策一致（第十一轮 H2）。

    两名并存必须证明同一次发布/隔离（同 inode）才合法；不同 inode、pending+quarantine
    并存与三名并存一律报告损坏。checker 始终只读：前后根树快照完全一致。
    """
    root, operation_dir, evidence_bytes = build_cleanup_evidence_checker_case(draft_factory)
    write_cleanup_evidence_names(
        operation_dir,
        evidence_bytes,
        names,
        same_inode=same_inode,
    )
    before = snapshot_tree(root)
    report = check_draft(root)
    assert (CheckCode.TRASH_PACKAGE_CORRUPT.value in codes(report)) is corrupt
    assert snapshot_tree(root) == before


def test_checker_evidence_stat_permission_error_is_stable(
    draft_factory: DraftFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """evidence 身份读取（_name_node 的 os.stat）PermissionError：check_draft 稳定诊断（H4/M2）。

    classify 用 os.lstat、身份捕获用 os.stat：deny os.stat 只命中身份捕获，证明
    "classify 成功、身份 stat 失败"同样产生稳定 trash.package_corrupt，且 path 为根内
    相对（L1），不泄漏原始异常、不改树。
    """
    import os

    import draft.operations as ops_module

    root, operation_dir, evidence_bytes = build_cleanup_evidence_checker_case(draft_factory)
    (operation_dir / "cleanup-evidence.json").write_bytes(evidence_bytes)
    evidence_names = {
        "cleanup-evidence.json",
        "cleanup-evidence.json.pending",
        "cleanup-evidence.json.quarantine",
    }
    real_stat = ops_module.os.stat

    def deny_evidence_stat(
        name: str | bytes | os.PathLike[str],
        *,
        dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> object:
        if ops_module.os.fspath(name) in evidence_names and dir_fd is not None:
            raise PermissionError("denied")
        return real_stat(name, dir_fd=dir_fd, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(ops_module.os, "stat", deny_evidence_stat)
    before = snapshot_tree(root)
    report = check_draft(root)
    expected = Path(".draft/operations") / operation_dir.name / "cleanup-evidence.json"
    hits = [issue for issue in report.errors if issue.code == CheckCode.TRASH_PACKAGE_CORRUPT.value]
    assert hits
    for issue in hits:
        assert issue.path is not None
        assert issue.path == expected
        assert not issue.path.is_absolute()
    assert snapshot_tree(root) == before


def test_checker_evidence_read_io_error_is_stable(
    draft_factory: DraftFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """evidence 内容读取 EIO：check_draft 返回稳定诊断，不抛原始异常（M2）。"""
    import errno
    import os

    import draft.operations as ops_module

    root, operation_dir, evidence_bytes = build_cleanup_evidence_checker_case(draft_factory)
    (operation_dir / "cleanup-evidence.json").write_bytes(evidence_bytes)
    real_open = ops_module.os.open

    def fail_evidence_open(
        name: str | bytes | os.PathLike[str],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if ops_module.os.fspath(name) == "cleanup-evidence.json" and dir_fd is not None:
            raise OSError(errno.EIO, "injected I/O error")
        return real_open(name, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(ops_module.os, "open", fail_evidence_open)
    before = snapshot_tree(root)
    report = check_draft(root)
    assert any(issue.code == CheckCode.TRASH_PACKAGE_CORRUPT.value for issue in report.errors)
    assert snapshot_tree(root) == before


@pytest.mark.parametrize("root_state", tuple(RootState))
def test_clean_terminal_operation_is_not_reported(
    draft_factory: DraftFactory,
    root_state: RootState,
) -> None:
    """终态操作目录只含 journal.json：不报告内部条目残留（回归 H4 不误报）。"""
    root = draft_factory(healthy(), root_state=root_state)
    write_raw_journal(root, version=JOURNAL_VERSION)

    report = check_draft(root)

    assert CheckCode.INTERNAL_UNEXPECTED_ENTRY.value not in codes(report)


@pytest.mark.parametrize("root_state", tuple(RootState))
def test_non_utf8_marker_is_reported_as_format_invalid(
    draft_factory: DraftFactory,
    root_state: RootState,
) -> None:
    """非 UTF-8 的 draft.json：check 报告 format.invalid，而非原始异常（回归 M3）。"""
    root = draft_factory(healthy(), root_state=root_state)
    (root / ".draft/draft.json").write_bytes(b"\xff\xfe\x00 garbage")

    report = check_draft(root)

    assert CheckCode.FORMAT_INVALID.value in codes(report)


# ---------------------------------------------------------------------------
# 第十二轮 H3/L1：checker 识别真实 restore cleanup owner；根内相对路径机器协议。
# 正例由真实 restore writer 在 cleanup evidence 发布后中断创建，不手改最终 journal。
# ---------------------------------------------------------------------------


class SimulatedProcessExit(BaseException):
    """模拟进程在 cleanup evidence 已耐久发布后退出：不得被生产 except DraftError 捕获。"""


def _restore_cleanup_owner_scene(
    draft_factory: DraftFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, str, str]:
    """真实 restore 在 evidence 已发布、首个包叶删除前中断：返回 (root, restore_id, trash_id)。

    现场：R=completed、锁属 R、T=rolled_back、T 目录含 cleanup-evidence.json、权威包
    完整保留。evidence 由真实 producer（clean_authoritative_package 的
    ``_write_cleanup_evidence``）耐久写出，不手改最终 journal。
    """
    import json

    import draft.trash_package as tp_module
    from draft.operations import Journal
    from draft.trash import restore_retained_trash
    from draft.trash_package import PackageAuthorization

    root = draft_factory(_trashable_checker_files())
    created = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)
    pending = trash_page(root, Path("pages/obsolete.md"), now=created)
    real_write_evidence = tp_module._write_cleanup_evidence  # pyright: ignore[reportPrivateUsage]
    fired = False

    def write_evidence_then_crash(
        journal: Journal,
        auth: PackageAuthorization,
        leaves_bytes: dict[Path, bytes],
    ) -> None:
        nonlocal fired
        real_write_evidence(journal, auth, leaves_bytes)
        if not fired:
            fired = True
            raise SimulatedProcessExit

    monkeypatch.setattr(tp_module, "_write_cleanup_evidence", write_evidence_then_crash)
    with pytest.raises(SimulatedProcessExit):
        restore_retained_trash(root, pending.operation_id, now=created)
    monkeypatch.undo()

    trash_id = pending.operation_id
    raw = json.loads(
        (root / ".draft/operations" / trash_id / "journal.json").read_text(encoding="utf-8")
    )
    restore_id = raw["metadata"]["restore_operation_id"]
    t_journal = json.loads(
        (root / ".draft/operations" / trash_id / "journal.json").read_text(encoding="utf-8")
    )
    assert t_journal["phase"] == "rolled_back"
    assert (root / ".draft/operations" / restore_id / "journal.json").exists()
    assert (root / ".draft/operation.lock").exists()
    return root, restore_id, trash_id


@pytest.mark.parametrize(
    ("names", "same_inode", "corrupt"),
    [
        (("final",), False, False),
        (("final", "pending"), True, False),
        (("final", "pending"), False, True),
        (("final", "quarantine"), True, False),
        (("final", "quarantine"), False, True),
        (("pending", "quarantine"), False, True),
        (("final", "pending", "quarantine"), False, True),
    ],
)
def test_checker_restore_cleanup_owner_joint_protocol(
    draft_factory: DraftFactory,
    monkeypatch: pytest.MonkeyPatch,
    names: tuple[str, ...],
    same_inode: bool,
    corrupt: bool,
) -> None:
    """真实 restore cleanup owner T 上运行的联合 evidence 判定（round-12 H3）。

    场景由真实 producer 在 evidence 发布后中断创建（R 持锁、T=rolled_back、evidence
    位于 T 目录）；checker 必须识别 T 为 cleanup owner 并运行共享联合分类，而不是把
    terminal T 的 evidence 一律视为残留。合法组合只产生 recovery_required，不误报
    损坏；非法组合同时产生 trash.package_corrupt。checker 只读：树快照不变。
    """
    root, _restore_id, trash_id = _restore_cleanup_owner_scene(draft_factory, monkeypatch)
    t_dir = root / ".draft/operations" / trash_id
    evidence_bytes = (t_dir / "cleanup-evidence.json").read_bytes()
    write_cleanup_evidence_names(t_dir, evidence_bytes, names, same_inode=same_inode)
    before = snapshot_tree(root)

    report = check_draft(root)

    assert (CheckCode.TRASH_PACKAGE_CORRUPT.value in codes(report)) is corrupt
    assert CheckCode.OPERATION_RECOVERY_REQUIRED.value in codes(report)
    assert CheckCode.INTERNAL_UNEXPECTED_ENTRY.value not in codes(report)
    assert snapshot_tree(root) == before


def test_checker_lock_still_scans_other_subtrees(
    draft_factory: DraftFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """锁存在时仍扫描全部操作目录与 trash tree（H3）：锁只影响诊断/上下文。"""
    root, _restore_id, _trash_id = _restore_cleanup_owner_scene(draft_factory, monkeypatch)
    evil_op = root / ".draft/operations" / ("e" * 32)
    evil_op.mkdir()
    (evil_op / "evil.txt").write_text("bad", encoding="utf-8")
    illegal = root / ".draft/trash/outgoing" / "not-an-op-id"
    illegal.mkdir()
    before = snapshot_tree(root)

    report = check_draft(root)

    assert CheckCode.OPERATION_RECOVERY_REQUIRED.value in codes(report)
    assert CheckCode.OPERATION_JOURNAL_CORRUPT.value in codes(report)
    assert CheckCode.INTERNAL_UNEXPECTED_ENTRY.value in codes(report)
    assert snapshot_tree(root) == before


def test_checker_restore_cleanup_evidence_path_is_root_relative(
    draft_factory: DraftFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """corrupt evidence 的 CheckIssue.path 是根内相对路径（L1）。"""
    root, _restore_id, trash_id = _restore_cleanup_owner_scene(draft_factory, monkeypatch)
    t_dir = root / ".draft/operations" / trash_id
    evidence_bytes = (t_dir / "cleanup-evidence.json").read_bytes()
    write_cleanup_evidence_names(t_dir, evidence_bytes, ("final", "pending"), same_inode=False)

    report = check_draft(root)

    expected = Path(".draft/operations") / trash_id / "cleanup-evidence.json"
    hits = [issue for issue in report.errors if issue.code == CheckCode.TRASH_PACKAGE_CORRUPT.value]
    assert hits
    for issue in hits:
        assert issue.path is not None
        assert issue.path == expected
        assert not issue.path.is_absolute()


def test_checker_evidence_io_error_path_is_root_relative(
    draft_factory: DraftFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """evidence 读取 EIO 的稳定诊断 path 同样是根内相对路径（L1）。"""
    import errno
    import os

    import draft.operations as ops_module

    root, _restore_id, trash_id = _restore_cleanup_owner_scene(draft_factory, monkeypatch)
    t_dir = root / ".draft/operations" / trash_id
    evidence_bytes = (t_dir / "cleanup-evidence.json").read_bytes()
    write_cleanup_evidence_names(t_dir, evidence_bytes, ("final",), same_inode=False)
    real_open = ops_module.os.open

    def fail_evidence_open(
        path: str | bytes | os.PathLike[str],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        fspath = os.fspath(path)
        name = fspath if isinstance(fspath, str) else fspath.decode(errors="surrogateescape")
        if "cleanup-evidence.json" in name and "pending" not in name:
            raise OSError(errno.EIO, "io error", name)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(ops_module.os, "open", fail_evidence_open)
    before = snapshot_tree(root)

    report = check_draft(root)

    expected = Path(".draft/operations") / trash_id / "cleanup-evidence.json"
    hits = [issue for issue in report.errors if issue.code == CheckCode.TRASH_PACKAGE_CORRUPT.value]
    assert hits
    for issue in hits:
        assert issue.path is not None
        assert issue.path == expected
        assert not issue.path.is_absolute()
    assert snapshot_tree(root) == before


# ---------------------------------------------------------------------------
# 第十二轮 M2：checker 内部目录的稳定 I/O 边界。父目录枚举/lstat 失败必须返回稳定
# 诊断，不能泄漏原始异常，也不能把失败折叠成"不存在"；独立子树仍被扫描。
# ---------------------------------------------------------------------------

_INTERNAL_PARENT_NODES = (
    ".draft/operations",
    ".draft/trash",
    ".draft/trash/outgoing",
)


@pytest.mark.parametrize("relative", _INTERNAL_PARENT_NODES)
@pytest.mark.parametrize("error_type", ["eacces", "eio"])
def test_checker_internal_parent_enumeration_failure_is_stable(
    draft_factory: DraftFactory,
    monkeypatch: pytest.MonkeyPatch,
    relative: str,
    error_type: str,
) -> None:
    """内部父目录 iterdir EACCES/EIO：check_draft 返回稳定诊断，不抛原始异常（M2）。"""
    import os

    root = draft_factory(healthy())
    target = root / relative
    target.mkdir(parents=True, exist_ok=True)
    before = snapshot_tree(root)
    real_scandir = os.scandir

    def fail_scandir(path: str | bytes | os.PathLike[str]) -> object:
        if os.fspath(path) == os.fspath(target):
            if error_type == "eacces":
                raise PermissionError(13, "Permission denied")
            raise OSError(5, "Input/output error")
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", fail_scandir)

    report = check_draft(root)
    monkeypatch.undo()

    assert isinstance(report, CheckReport)
    assert report.errors
    assert snapshot_tree(root) == before


@pytest.mark.parametrize("relative", _INTERNAL_PARENT_NODES)
@pytest.mark.parametrize("error_type", ["eacces", "eio"])
def test_checker_internal_parent_stat_failure_is_stable(
    draft_factory: DraftFactory,
    monkeypatch: pytest.MonkeyPatch,
    relative: str,
    error_type: str,
) -> None:
    """内部父目录 lstat/stat EACCES/EIO：check_draft 返回稳定诊断，不折叠成不存在（M2）。"""
    import os

    root = draft_factory(healthy())
    target = root / relative
    target.mkdir(parents=True, exist_ok=True)
    before = snapshot_tree(root)
    real_lstat = os.lstat

    def fail_lstat(path: str | bytes | os.PathLike[str], *args: object) -> object:
        if os.fspath(path) == os.fspath(target):
            if error_type == "eacces":
                raise PermissionError(13, "Permission denied")
            raise OSError(5, "Input/output error")
        return real_lstat(path, *args)

    monkeypatch.setattr(os, "lstat", fail_lstat)

    report = check_draft(root)
    monkeypatch.undo()

    assert isinstance(report, CheckReport)
    assert report.errors
    assert snapshot_tree(root) == before


def test_checker_single_operation_dir_stat_failure_is_stable(
    draft_factory: DraftFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """单个 operation 目录 lstat EACCES：check_draft 返回稳定诊断（M2）。

    操作目录必须先用真实合法 journal 建立，否则 pre-fix 也会因 journal 损坏报错；
    修复前 lstat 失败被 is_symlink 静默吞掉、is_dir 走 os.stat 不受影响，因而无诊断
    （把不可读折叠成正常）；修复后分类失败产生稳定 operation 诊断。
    """
    import os

    root = draft_factory(healthy(), precreate_internal=True)
    op_dir = root / ".draft/operations" / ("a" * 32)
    op_dir.mkdir()
    write_raw_journal(root, operation_id="a" * 32)
    before = snapshot_tree(root)
    real_lstat = os.lstat

    def fail_op_lstat(path: str | bytes | os.PathLike[str], *args: object) -> object:
        if os.fspath(path) == os.fspath(op_dir):
            raise PermissionError(13, "Permission denied")
        return real_lstat(path, *args)

    monkeypatch.setattr(os, "lstat", fail_op_lstat)

    report = check_draft(root)
    monkeypatch.undo()

    assert isinstance(report, CheckReport)
    assert report.errors
    assert snapshot_tree(root) == before


def test_checker_single_operation_dir_enumeration_failure_is_stable(
    draft_factory: DraftFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """单个 operation 目录 iterdir EACCES：check_draft 返回稳定诊断（M2）。

    Path.iterdir 在 3.12 会静默吞掉 OSError（把不可读折叠成空目录）；修复后改用
    os.scandir 枚举，失败产生稳定 operation 诊断，不静默丢弃该目录的未知条目。
    """
    import os

    root = draft_factory(healthy(), precreate_internal=True)
    op_dir = root / ".draft/operations" / ("a" * 32)
    op_dir.mkdir()
    write_raw_journal(root, operation_id="a" * 32)
    before = snapshot_tree(root)
    real_scandir = os.scandir

    def fail_op_scandir(path: str | bytes | os.PathLike[str]) -> object:
        if os.fspath(path) == os.fspath(op_dir):
            raise PermissionError(13, "Permission denied")
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", fail_op_scandir)

    report = check_draft(root)
    monkeypatch.undo()

    assert isinstance(report, CheckReport)
    assert report.errors
    assert snapshot_tree(root) == before


def test_checker_unreadable_subtree_does_not_hide_other_corruption(
    draft_factory: DraftFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """一个子树不可读时，另一个独立损坏子树仍被报告（M2）。"""
    import os

    root = draft_factory(healthy(), precreate_internal=True)
    # 独立损坏子树：illegal trash/outgoing 条目。
    illegal = root / ".draft/trash/outgoing" / "not-an-op-id"
    illegal.mkdir(parents=True)
    # 独立损坏子树：无 journal 的 operation 目录。
    evil_op = root / ".draft/operations" / ("e" * 32)
    evil_op.mkdir()
    (evil_op / "evil.txt").write_text("bad", encoding="utf-8")
    before = snapshot_tree(root)
    operations = root / ".draft/operations"
    real_scandir = os.scandir

    def fail_operations_scandir(path: str | bytes | os.PathLike[str]) -> object:
        if os.fspath(path) == os.fspath(operations):
            raise PermissionError(13, "Permission denied")
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", fail_operations_scandir)

    report = check_draft(root)
    monkeypatch.undo()

    assert CheckCode.OPERATION_JOURNAL_CORRUPT.value in codes(report)  # operations 子树不可读
    assert CheckCode.INTERNAL_UNEXPECTED_ENTRY.value in codes(report)  # trash/outgoing 损坏仍可见
    assert snapshot_tree(root) == before


# ---------------------------------------------------------------------------
# 第十三轮 M1：owner 索引不完整时未知不得折叠为 ORPHAN。合法 retained 包在
# operations/journal/lock 不可读时保持 HELD/UNKNOWN；只有可证明无 owner 的包才报
# orphan。非法名字/节点类型/链接形状不依赖 owner 事实，必须继续报告。
# ---------------------------------------------------------------------------


def _has_package_orphan(report: CheckReport, package_id: str) -> bool:
    """outgoing 下指定 package 是否被报告为 orphan 的 internal.unexpected_entry。"""
    expected = Path(".draft/trash/outgoing") / package_id
    return any(
        issue.code == CheckCode.INTERNAL_UNEXPECTED_ENTRY.value and issue.path == expected
        for issue in report.errors
    )


def _retained_package_root(draft_factory: DraftFactory) -> tuple[Path, str]:
    """真实 retained T + 权威包，返回 (root, trash_id)。"""
    root = draft_factory(_trashable_checker_files())
    created = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)
    pending = trash_page(root, Path("pages/obsolete.md"), now=created)
    assert (root / ".draft/trash/outgoing" / pending.operation_id).is_dir()
    return root, pending.operation_id


def _eacces_or_eio(error_type: str) -> BaseException:
    if error_type == "eacces":
        return PermissionError(13, "Permission denied")
    return OSError(5, "Input/output error")


@pytest.mark.parametrize("error_type", ["eacces", "eio"])
@pytest.mark.parametrize("fail_point", ["lstat", "scandir"])
def test_checker_operations_unavailable_keeps_retained_package(
    draft_factory: DraftFactory,
    monkeypatch: pytest.MonkeyPatch,
    error_type: str,
    fail_point: str,
) -> None:
    """operations 根不可 stat/枚举：合法 retained 包不得被误判 orphan（round-13 M1）。

    不能只断言 report.errors 非空；必须精确断言合法包没有 orphan 诊断。
    """
    import os

    root, trash_id = _retained_package_root(draft_factory)
    operations = root / ".draft/operations"
    before = snapshot_tree(root)
    if fail_point == "lstat":
        real_lstat = os.lstat

        def fail_lstat(path: str | bytes | os.PathLike[str], *args: object) -> object:
            if os.fspath(path) == os.fspath(operations):
                raise _eacces_or_eio(error_type)
            return real_lstat(path, *args)

        monkeypatch.setattr(os, "lstat", fail_lstat)
    else:
        real_scandir = os.scandir

        def fail_scandir(path: str | bytes | os.PathLike[str]) -> object:
            if os.fspath(path) == os.fspath(operations):
                raise _eacces_or_eio(error_type)
            return real_scandir(path)

        monkeypatch.setattr(os, "scandir", fail_scandir)

    report = check_draft(root)
    monkeypatch.undo()

    assert isinstance(report, CheckReport)
    assert CheckCode.OPERATION_JOURNAL_CORRUPT.value in codes(report)
    assert not _has_package_orphan(report, trash_id)
    assert snapshot_tree(root) == before


def test_checker_cleanup_owner_lock_unreadable_keeps_package(
    draft_factory: DraftFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """锁不可读 + 真实 cleanup owner 现场：T=rolled_back 包不得被误判 orphan（round-13 M1）。

    lock 读取失败时无法证明 T 正是锁内 restore 的 cleanup owner，必须 UNKNOWN 而不是
    ORPHAN（未知不得折叠为否定事实）。
    """
    import os

    root, _restore_id, trash_id = _restore_cleanup_owner_scene(draft_factory, monkeypatch)
    lock = root / ".draft/operation.lock"
    before = snapshot_tree(root)
    real_lstat = os.lstat

    def fail_lock_lstat(path: str | bytes | os.PathLike[str], *args: object) -> object:
        if os.fspath(path) == os.fspath(lock):
            raise OSError(5, "Input/output error")
        return real_lstat(path, *args)

    monkeypatch.setattr(os, "lstat", fail_lock_lstat)

    report = check_draft(root)
    monkeypatch.undo()

    assert isinstance(report, CheckReport)
    assert not _has_package_orphan(report, trash_id)
    assert snapshot_tree(root) == before


def test_checker_cleanup_owner_r_journal_unreadable_keeps_package(
    draft_factory: DraftFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R journal 不可读 + 真实 cleanup owner 现场：包为 UNKNOWN，不误报 orphan（round-13 M1）。"""
    import draft.operations as ops_module

    root, restore_id, trash_id = _restore_cleanup_owner_scene(draft_factory, monkeypatch)
    r_journal = root / ".draft/operations" / restore_id / "journal.json"
    before = snapshot_tree(root)
    real_read = ops_module.read_journal

    def fail_read(journal_path: Path) -> Journal:
        if journal_path == r_journal:
            raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, journal_path)
        return real_read(journal_path)

    monkeypatch.setattr(ops_module, "read_journal", fail_read)

    report = check_draft(root)
    monkeypatch.undo()

    assert isinstance(report, CheckReport)
    assert not _has_package_orphan(report, trash_id)
    assert snapshot_tree(root) == before


def test_checker_unknown_owner_does_not_suppress_provable_orphan(
    draft_factory: DraftFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """两个包：一个 owner 未知、另一个可证明 orphan——只报告后者 orphan（round-13 M1）。

    证明实现没有因一个 unknown 全局关闭所有 orphan 检查。
    """
    import draft.operations as ops_module

    root, restore_id, trash_id = _restore_cleanup_owner_scene(draft_factory, monkeypatch)
    orphan_id = "c" * 32
    orphan_pkg = root / ".draft/trash/outgoing" / orphan_id
    orphan_pkg.mkdir(parents=True)
    (orphan_pkg / "leaf.bin").write_bytes(b"orphan-leaf")
    r_journal = root / ".draft/operations" / restore_id / "journal.json"
    before = snapshot_tree(root)
    real_read = ops_module.read_journal

    def fail_read(journal_path: Path) -> Journal:
        if journal_path == r_journal:
            raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, journal_path)
        return real_read(journal_path)

    monkeypatch.setattr(ops_module, "read_journal", fail_read)

    report = check_draft(root)
    monkeypatch.undo()

    assert _has_package_orphan(report, orphan_id)
    assert not _has_package_orphan(report, trash_id)
    assert snapshot_tree(root) == before


@pytest.mark.parametrize("bad_shape", ["invalid_name", "regular_file", "symlink"])
def test_checker_outgoing_shape_checks_independent_of_operations(
    draft_factory: DraftFactory,
    monkeypatch: pytest.MonkeyPatch,
    bad_shape: str,
) -> None:
    """operations 不可用时，outgoing 非法形状仍分别报告（round-13 M1/M2）。

    不要把"无法判 owner"扩大成"不检查 package 自身"。
    """
    import os

    root, trash_id = _retained_package_root(draft_factory)
    outgoing = root / ".draft/trash/outgoing"
    if bad_shape == "invalid_name":
        (outgoing / "not-an-op-id").mkdir()
    elif bad_shape == "regular_file":
        (outgoing / "regular.txt").write_text("x", encoding="utf-8")
    else:
        (outgoing / "symlink").symlink_to(root / "index.md")
    operations = root / ".draft/operations"
    before = snapshot_tree(root)
    real_scandir = os.scandir

    def fail_scandir(path: str | bytes | os.PathLike[str]) -> object:
        if os.fspath(path) == os.fspath(operations):
            raise PermissionError(13, "Permission denied")
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", fail_scandir)

    report = check_draft(root)
    monkeypatch.undo()

    assert isinstance(report, CheckReport)
    assert CheckCode.OPERATION_JOURNAL_CORRUPT.value in codes(report)
    if bad_shape == "symlink":
        assert CheckCode.PATH_SYMLINK_ESCAPE.value in codes(report)
    else:
        assert CheckCode.INTERNAL_UNEXPECTED_ENTRY.value in codes(report)
    assert not _has_package_orphan(report, trash_id)
    assert snapshot_tree(root) == before


# ---------------------------------------------------------------------------
# 第十三轮 M2：checker 仍有绕过稳定 helper 的原始 Path 查询。package 节点分类
# （原 package_dir.is_dir）与 .draft child 二次分类（原 entry.is_symlink/is_dir）
# 遇 EACCES/EIO 必须返回稳定根内相对诊断，不泄漏原始异常、不折叠成缺失。
# ---------------------------------------------------------------------------


def _package_node_issue(report: CheckReport, trash_id: str) -> list[CheckIssue]:
    return [
        issue
        for issue in report.errors
        if issue.code == CheckCode.TRASH_PACKAGE_CORRUPT.value
        and issue.path is not None
        and issue.path == Path(".draft/operations") / trash_id / "journal.json"
    ]


def test_checker_package_node_stat_eio_is_stable(
    draft_factory: DraftFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """package 节点 stat EIO：check_draft 返回稳定 package_corrupt，不抛原始异常（M2）。

    修复前 ``package_dir.is_dir()`` 从 check_draft 原样泄漏 OSError（红灯）。
    """
    import os

    root, trash_id = _retained_package_root(draft_factory)
    package_dir = root / ".draft/trash/outgoing" / trash_id
    before = snapshot_tree(root)
    real_stat = os.stat

    def fail_stat(
        path: str | bytes | os.PathLike[str],
        *,
        dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> object:
        if os.fspath(path) == os.fspath(package_dir):
            raise OSError(5, "Input/output error")
        return real_stat(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(os, "stat", fail_stat)

    report = check_draft(root)
    monkeypatch.undo()

    assert isinstance(report, CheckReport)
    assert _package_node_issue(report, trash_id)
    assert CheckCode.TRASH_PACKAGE_MISSING.value not in codes(report)
    assert snapshot_tree(root) == before


def test_checker_package_node_lstat_failure_is_stable(
    draft_factory: DraftFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """package 节点 lstat EACCES/EIO：稳定 package_corrupt，不声称 package_missing（M2）。"""
    import os

    root, trash_id = _retained_package_root(draft_factory)
    package_dir = root / ".draft/trash/outgoing" / trash_id
    before = snapshot_tree(root)
    real_lstat = os.lstat

    def fail_lstat(path: str | bytes | os.PathLike[str], *args: object) -> object:
        if os.fspath(path) == os.fspath(package_dir):
            raise PermissionError(13, "Permission denied")
        return real_lstat(path, *args)

    monkeypatch.setattr(os, "lstat", fail_lstat)

    report = check_draft(root)
    monkeypatch.undo()

    assert isinstance(report, CheckReport)
    assert _package_node_issue(report, trash_id)
    assert CheckCode.TRASH_PACKAGE_MISSING.value not in codes(report)
    assert snapshot_tree(root) == before


@pytest.mark.parametrize("shape", ["file", "symlink"])
def test_checker_package_node_wrong_shape_is_corrupt(
    draft_factory: DraftFactory,
    monkeypatch: pytest.MonkeyPatch,
    shape: str,
) -> None:
    """package 节点是普通文件/链接：trash.package_corrupt，不折叠成 missing（M2）。"""
    import shutil

    root, trash_id = _retained_package_root(draft_factory)
    package_dir = root / ".draft/trash/outgoing" / trash_id
    shutil.rmtree(package_dir)
    if shape == "file":
        package_dir.write_text("not a package", encoding="utf-8")
    else:
        package_dir.symlink_to(root / "index.md")
    before = snapshot_tree(root)

    report = check_draft(root)

    assert _package_node_issue(report, trash_id)
    assert CheckCode.TRASH_PACKAGE_MISSING.value not in codes(report)
    assert snapshot_tree(root) == before


_INTERNAL_CHILD_NODES = (
    ".draft/draft.json",
    ".draft/operations",
    ".draft/trash",
)

# 内部协议 child（_check_internal_namespace 实际分类为目录的节点）；marker child 的
# stat 泄漏属于 read_marker（graph 根布局边界），按计划 Task 4 Step 5 记录而非本任务修复。
_INTERNAL_PROTOCOL_CHILDREN = (".draft/operations", ".draft/trash")


@pytest.mark.parametrize("relative", _INTERNAL_PROTOCOL_CHILDREN)
def test_checker_internal_child_stat_eio_does_not_leak(
    draft_factory: DraftFactory,
    monkeypatch: pytest.MonkeyPatch,
    relative: str,
) -> None:
    """.draft child 的 stat EIO 不再从 _check_internal_namespace 泄漏（M2）。

    修复前 ``entry.is_dir()`` 对 operations/trash child 从 check_draft 原样抛出。
    """
    import os

    root = draft_factory(healthy(), precreate_internal=True)
    child = root / relative
    before = snapshot_tree(root)
    real_stat = os.stat

    def fail_stat(
        path: str | bytes | os.PathLike[str],
        *,
        dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> object:
        if os.fspath(path) == os.fspath(child):
            raise OSError(5, "Input/output error")
        return real_stat(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(os, "stat", fail_stat)

    report = check_draft(root)
    monkeypatch.undo()

    assert isinstance(report, CheckReport)
    assert snapshot_tree(root) == before


@pytest.mark.parametrize("relative", _INTERNAL_CHILD_NODES)
def test_checker_internal_child_classify_failure_is_stable(
    draft_factory: DraftFactory,
    monkeypatch: pytest.MonkeyPatch,
    relative: str,
) -> None:
    """.draft child 的分类 lstat EACCES/EIO：稳定根内相对诊断，不泄漏原始异常（M2）。"""
    import os

    root = draft_factory(healthy(), precreate_internal=True)
    child = root / relative
    before = snapshot_tree(root)
    real_lstat = os.lstat

    def fail_lstat(path: str | bytes | os.PathLike[str], *args: object) -> object:
        if os.fspath(path) == os.fspath(child):
            raise PermissionError(13, "Permission denied")
        return real_lstat(path, *args)

    monkeypatch.setattr(os, "lstat", fail_lstat)

    report = check_draft(root)
    monkeypatch.undo()

    assert isinstance(report, CheckReport)
    expected = Path(relative)
    hits = [issue for issue in report.errors if issue.path == expected]
    assert hits, f"{relative} 应产生根内相对稳定诊断，实际 {codes(report)}"
    assert snapshot_tree(root) == before
