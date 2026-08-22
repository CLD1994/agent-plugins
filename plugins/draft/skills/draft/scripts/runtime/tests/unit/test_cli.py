from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

import draft.cli as cli
from draft import __version__
from draft.cli_results import CheckSlice, project_check
from draft.diagnostic_codes import CheckCode
from draft.errors import DraftError
from draft.graph import MarkerState
from draft.models import (
    CheckIssue,
    CheckReport,
    IssueSeverity,
    LifecycleWarning,
    OperationPhase,
    OperationResult,
    RootState,
    TrashResult,
)
from draft.paths import ManagedRoot, ResolvedPath, RootSource


def _managed(path: str = "/draft") -> ManagedRoot:
    return ManagedRoot(
        path=Path(path),
        marker=MarkerState(version=1, root_state=RootState.REGISTERED),
        source=RootSource.CLI,
    )


# ---------------------------------------------------------------------------
# 辅助构造器
# ---------------------------------------------------------------------------


def _resolved(relative: str) -> ResolvedPath:
    """构造受真实接口约束的 ResolvedPath：_page_operand 真实执行后返回 .relative。"""
    root = Path("/draft")
    return ResolvedPath(root=root, relative=Path(relative), absolute=root / relative)


def _operation_result(
    *,
    operation_id: str = "a" * 32,
    kind: str = "move",
    phase: OperationPhase = OperationPhase.COMPLETED,
    changed_paths: tuple[Path, ...] = (Path("pages/a.md"), Path("pages/b.md")),
    warnings: tuple[LifecycleWarning, ...] = (),
) -> OperationResult:
    return OperationResult(
        operation_id=operation_id,
        kind=kind,
        phase=phase,
        changed_paths=changed_paths,
        warnings=warnings,
    )


def _trash_result(
    *,
    operation_id: str = "b" * 32,
    phase: OperationPhase = OperationPhase.TRASH_RETAINED,
    package: Path = Path(".draft/trash/outgoing/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"),
    restore_expires_at: datetime | None = None,
    purge_eligible_at: datetime | None = None,
    warnings: tuple[LifecycleWarning, ...] = (),
) -> TrashResult:
    return TrashResult(
        operation_id=operation_id,
        phase=phase,
        package=package,
        restore_expires_at=restore_expires_at,
        purge_eligible_at=purge_eligible_at,
        warnings=warnings,
    )


def _assert_human_result(human: str, *facts: object) -> None:
    assert human
    assert human.splitlines() == [human]
    for fact in facts:
        assert str(fact) in human


# ---------------------------------------------------------------------------
# build_parser
# ---------------------------------------------------------------------------


def test_build_parser_parses_version_flag() -> None:
    args = cli.build_parser().parse_args(["--version"])
    assert args.version is True


def test_build_parser_parses_init_command() -> None:
    args = cli.build_parser().parse_args(["init", "/draft"])
    assert args.command == "init"
    assert args.root == "/draft"
    assert args.candidate is False


def test_build_parser_parses_init_candidate_command() -> None:
    args = cli.build_parser().parse_args(["init", "--candidate", "/draft"])
    assert args.command == "init"
    assert args.root == "/draft"
    assert args.candidate is True


def test_build_parser_parses_check_with_root() -> None:
    args = cli.build_parser().parse_args(["check", "--root", "/draft"])
    assert args.command == "check"
    assert args.root == "/draft"


def test_build_parser_parses_promote_with_variable_children() -> None:
    args = cli.build_parser().parse_args(
        ["promote", "--root", "/draft", "pages/a.md", "pages/b.md", "pages/c.md"]
    )
    assert args.command == "promote"
    assert args.page == "pages/a.md"
    assert args.children == ["pages/b.md", "pages/c.md"]


def test_build_parser_json_flag_before_subcommand() -> None:
    args = cli.build_parser().parse_args(["--json", "check", "--root", "/draft"])
    assert args.json is True


def test_build_parser_json_flag_after_subcommand() -> None:
    args = cli.build_parser().parse_args(["check", "--root", "/draft", "--json"])
    assert args.json is True


# ---------------------------------------------------------------------------
# _dispatch: init / adopt
# ---------------------------------------------------------------------------


def test_dispatch_init_calls_initialize_and_returns_success(mocker: MockerFixture) -> None:
    args = cli.build_parser().parse_args(["init", "/draft"])
    initialize = mocker.patch("draft.lifecycle.initialize", autospec=True)
    payload, human, exit_code = cli._dispatch(args, root=Path("/draft"))  # pyright: ignore[reportPrivateUsage]  # 验证 init 分派到 initialize 并返回成功载荷
    initialize.assert_called_once_with(Path("/draft"), root_state=RootState.REGISTERED)
    assert payload == {"result_type": "init", "root": "/draft"}
    _assert_human_result(human, Path("/draft"))
    assert exit_code == 0


def test_dispatch_init_candidate_passes_candidate_root_state(mocker: MockerFixture) -> None:
    args = cli.build_parser().parse_args(["init", "--candidate", "/draft"])
    initialize = mocker.patch("draft.lifecycle.initialize", autospec=True)
    payload, _human, exit_code = cli._dispatch(args, root=Path("/draft"))  # pyright: ignore[reportPrivateUsage]
    initialize.assert_called_once_with(Path("/draft"), root_state=RootState.CANDIDATE)
    assert payload == {"result_type": "init", "root": "/draft"}
    assert exit_code == 0


def test_dispatch_adopt_calls_adopt_and_returns_success(mocker: MockerFixture) -> None:
    args = cli.build_parser().parse_args(["adopt", "--root", "/draft"])
    adopt = mocker.patch("draft.lifecycle.adopt", autospec=True)
    payload, human, exit_code = cli._dispatch(args, root=Path("/draft"))  # pyright: ignore[reportPrivateUsage]  # 验证 adopt 分派到 adopt 并返回成功载荷
    adopt.assert_called_once_with(Path("/draft"))
    assert payload == {"result_type": "adopt", "root": "/draft"}
    _assert_human_result(human, Path("/draft"))
    assert exit_code == 0


# ---------------------------------------------------------------------------
# _dispatch: move / promote / collapse / trash（经 _page_operand 隔离）
# ---------------------------------------------------------------------------


def test_dispatch_move_calls_move_entries_with_operands(mocker: MockerFixture) -> None:
    args = cli.build_parser().parse_args(["move", "--root", "/draft", "pages/a.md", "pages/b.md"])
    move_entries = mocker.patch("draft.structure.move_entries", autospec=True)
    move_entries.return_value = _operation_result(kind="move")
    payload, human, exit_code = cli._dispatch(args, root=Path("/draft"))  # pyright: ignore[reportPrivateUsage]  # 验证 move 分派到 move_entries 并组装操作载荷
    move_entries.assert_called_once_with(Path("/draft"), ["pages/a.md"], "pages/b.md")
    assert payload == {
        "result_type": "move",
        "changed_paths": ["pages/a.md", "pages/b.md"],
    }
    _assert_human_result(human, "pages/a.md", "pages/b.md")
    assert exit_code == 0


def test_dispatch_promote_calls_promote_page_with_paged_operands(mocker: MockerFixture) -> None:
    args = cli.build_parser().parse_args(
        ["promote", "--root", "/draft", "pages/a.md", "pages/b.md", "pages/c.md"]
    )
    resolve_page_operand = mocker.patch("draft.paths.resolve_page_operand", autospec=True)
    promote_page = mocker.patch("draft.structure.promote_page", autospec=True)
    resolve_page_operand.side_effect = [
        _resolved("pages/a.md"),
        _resolved("pages/b.md"),
        _resolved("pages/c.md"),
    ]
    promote_page.return_value = _operation_result(kind="promote")
    payload, human, exit_code = cli._dispatch(args, root=Path("/draft"))  # pyright: ignore[reportPrivateUsage]  # 验证 promote 分派到 promote_page 并组装操作载荷
    promote_page.assert_called_once_with(
        Path("/draft"), Path("pages/a.md"), [Path("pages/b.md"), Path("pages/c.md")]
    )
    assert payload["result_type"] == "promote"
    _assert_human_result(human, Path("pages/a.md"), len(args.children))
    assert exit_code == 0


def test_dispatch_collapse_calls_collapse_hub_with_paged_operand(mocker: MockerFixture) -> None:
    args = cli.build_parser().parse_args(["collapse", "--root", "/draft", "pages/hub/index.md"])
    resolve_page_operand = mocker.patch("draft.paths.resolve_page_operand", autospec=True)
    collapse_hub = mocker.patch("draft.structure.collapse_hub", autospec=True)
    resolve_page_operand.return_value = _resolved("pages/hub/index.md")
    collapse_hub.return_value = _operation_result(kind="collapse")
    payload, human, exit_code = cli._dispatch(args, root=Path("/draft"))  # pyright: ignore[reportPrivateUsage]  # 验证 collapse 分派到 collapse_hub 并组装操作载荷
    collapse_hub.assert_called_once_with(Path("/draft"), Path("pages/hub/index.md"))
    assert payload["result_type"] == "collapse"
    _assert_human_result(human, Path("pages/hub/index.md"))
    assert exit_code == 0


def test_dispatch_trash_calls_trash_page_with_paged_operand(mocker: MockerFixture) -> None:
    args = cli.build_parser().parse_args(["trash", "--root", "/draft", "pages/a.md"])
    resolve_page_operand = mocker.patch("draft.paths.resolve_page_operand", autospec=True)
    trash_page = mocker.patch("draft.trash.trash_page", autospec=True)
    resolve_page_operand.return_value = _resolved("pages/a.md")
    trash_page.return_value = _trash_result()
    payload, human, exit_code = cli._dispatch(args, root=Path("/draft"))  # pyright: ignore[reportPrivateUsage]  # 验证 trash 分派到 trash_page 并组装回收载荷
    trash_page.assert_called_once_with(Path("/draft"), Path("pages/a.md"))
    assert payload["result_type"] == "trash"
    assert payload["operation_id"] == trash_page.return_value.operation_id
    assert "phase" not in payload
    _assert_human_result(human, Path("pages/a.md"), trash_page.return_value.phase.value)
    assert exit_code == 0


def test_dispatch_trash_includes_first_warning_in_human_text(mocker: MockerFixture) -> None:
    args = cli.build_parser().parse_args(["trash", "--root", "/draft", "pages/a.md"])
    warning = LifecycleWarning(
        code="trash.handoff_failed",
        operation_id="b" * 32,
        details=("OSError", "disk full"),
    )
    resolve_page_operand = mocker.patch("draft.paths.resolve_page_operand", autospec=True)
    trash_page = mocker.patch("draft.trash.trash_page", autospec=True)
    resolve_page_operand.return_value = _resolved("pages/a.md")
    trash_page.return_value = _trash_result(warnings=(warning,))
    _, human, exit_code = cli._dispatch(args, root=Path("/draft"))  # pyright: ignore[reportPrivateUsage]  # 验证 trash 载荷包含首个警告的人类可读文本
    _assert_human_result(human, warning.code, warning.operation_id, *warning.details)
    assert exit_code == 0


# ---------------------------------------------------------------------------
# _dispatch: restore / recover
# ---------------------------------------------------------------------------


def test_dispatch_restore_completed_returns_success_payload(mocker: MockerFixture) -> None:
    args = cli.build_parser().parse_args(["restore", "--root", "/draft", "b" * 32])
    restore = mocker.patch("draft.trash.restore_retained_trash", autospec=True)
    restore.return_value = _operation_result(
        operation_id="b" * 32,
        kind="restore",
        phase=OperationPhase.COMPLETED,
        changed_paths=(Path("pages/a.md"),),
    )
    payload, human, exit_code = cli._dispatch(args, root=Path("/draft"))  # pyright: ignore[reportPrivateUsage]  # 验证 restore 成功路径返回完成载荷与退出码 0
    assert payload["result_type"] == "restore"
    assert payload["operation_id"] == "b" * 32
    assert "phase" not in payload
    assert exit_code == 0
    _assert_human_result(human, args.operation_id)


def test_dispatch_restore_rolled_back_returns_exit_three(mocker: MockerFixture) -> None:
    args = cli.build_parser().parse_args(["restore", "--root", "/draft", "b" * 32])
    restore = mocker.patch("draft.trash.restore_retained_trash", autospec=True)
    restore.return_value = _operation_result(
        operation_id="b" * 32,
        kind="restore",
        phase=OperationPhase.ROLLED_BACK,
        changed_paths=(),
    )
    payload, human, exit_code = cli._dispatch(args, root=Path("/draft"))  # pyright: ignore[reportPrivateUsage]  # 验证 restore 回滚路径返回 ok=False 与退出码 3
    assert payload["result_type"] == "restore"
    assert payload["ok"] is False
    assert payload["operation_id"] == "b" * 32
    assert exit_code == 3
    _assert_human_result(human)


def test_dispatch_recover_success_returns_fixed_payload(mocker: MockerFixture) -> None:
    args = cli.build_parser().parse_args(["recover", "--root", "/draft"])
    result = _operation_result(
        operation_id="a" * 32,
        kind="restore",
        phase=OperationPhase.COMPLETED,
        changed_paths=(Path("pages/a.md"),),
    )
    recover = mocker.patch("draft.trash_lifecycle.recover_interrupted_operations", autospec=True)
    recover.return_value = (result,)
    payload, human, exit_code = cli._dispatch(args, root=Path("/draft"))  # pyright: ignore[reportPrivateUsage]  # 验证 recover 只报告已修复操作的数量与公开路径
    assert payload == {
        "result_type": "recover",
        "fixed_count": 1,
        "paths": ["pages/a.md"],
    }
    _assert_human_result(human, 1)
    assert exit_code == 0


def test_dispatch_recover_error_raises_standard_envelope(mocker: MockerFixture) -> None:
    args = cli.build_parser().parse_args(["recover", "--root", "/draft"])
    recover = mocker.patch("draft.trash_lifecycle.recover_interrupted_operations", autospec=True)
    recover.side_effect = DraftError(
        CheckCode.OPERATION_INCONSISTENT_STATE.value,
        Path(".draft/operations/cccccccccccccccccccccccccccccccc/journal.json"),
        details=("inconsistent",),
    )
    with pytest.raises(DraftError) as caught:
        cli._dispatch(args, root=Path("/draft"))  # pyright: ignore[reportPrivateUsage]  # 恢复损坏直接抛稳定错误，由 main 映射为退出码 3
    assert caught.value.code == CheckCode.OPERATION_INCONSISTENT_STATE.value


# ---------------------------------------------------------------------------
# _dispatch: unknown command
# ---------------------------------------------------------------------------


def test_dispatch_unknown_command_raises_draft_error() -> None:
    args = argparse.Namespace(command="nonexistent")
    with pytest.raises(DraftError) as caught:
        cli._dispatch(args, root=Path("/draft"))  # pyright: ignore[reportPrivateUsage]  # 验证未知命令路径抛出 cli.unknown_command
    assert caught.value.code == "cli.unknown_command"


# ---------------------------------------------------------------------------
# _render_check：人类模式分组编排 / 空报告 / JSON 模式
# ---------------------------------------------------------------------------


def _mixed_severity_issues() -> tuple[CheckIssue, ...]:
    by_severity = {
        severity: tuple(
            CheckIssue(
                code=f"test.{severity.value}.{index}",
                severity=severity,
                path=Path(f"{severity.value}-{index}.md"),
                line=index + 1,
                details=(f"detail-{index}",),
            )
            for index in range(count)
        )
        for severity, count in (
            (IssueSeverity.ERROR, 1),
            (IssueSeverity.WARNING, 2),
            (IssueSeverity.INFO, 3),
            (IssueSeverity.REVIEW, 4),
        )
    }
    return (
        by_severity[IssueSeverity.REVIEW][0],
        by_severity[IssueSeverity.WARNING][0],
        by_severity[IssueSeverity.INFO][0],
        by_severity[IssueSeverity.REVIEW][1],
        by_severity[IssueSeverity.ERROR][0],
        by_severity[IssueSeverity.INFO][1],
        by_severity[IssueSeverity.WARNING][1],
        by_severity[IssueSeverity.REVIEW][2],
        by_severity[IssueSeverity.INFO][2],
        by_severity[IssueSeverity.REVIEW][3],
    )


def _check_args(**overrides: object) -> argparse.Namespace:
    args = argparse.Namespace()
    args.code = None
    args.path_prefix = None
    args.limit = 20
    args.offset = 0
    args.count_only = False
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


def test_render_check_groups_issues_and_emits_each_renderer_result_once(
    mocker: MockerFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    issues = _mixed_severity_issues()
    report = CheckReport(issues=issues)
    grouped = [*report.errors, *report.warnings, *report.infos, *report.reviews]
    tokens = {issue: f"render-token-{index}" for index, issue in enumerate(grouped)}
    render_issue = mocker.patch.object(cli, "render_issue", autospec=True)
    render_issue.side_effect = tokens.__getitem__

    exit_code = cli._render_check(  # pyright: ignore[reportPrivateUsage]
        report, _check_args(), json_mode=False
    )
    lines = capsys.readouterr().out.splitlines()

    assert exit_code == 1
    assert lines[:-1] == [tokens[issue] for issue in grouped]
    assert [call.args[0] for call in render_issue.call_args_list] == grouped
    assert lines[-1]
    for count in (1, 2, 3, 4):
        assert str(count) in lines[-1]


def test_render_check_filtered_empty_with_errors_does_not_claim_pass(
    mocker: MockerFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """过滤后无可见项但未过滤报告有错误时，不得声称检查通过（规格 §10）。"""
    render_issue = mocker.patch.object(cli, "render_issue", autospec=True)
    issue = CheckIssue(
        CheckCode.PARENT_MISSING.value, IssueSeverity.ERROR, Path("pages/a.md"), None
    )
    args = _check_args(code=[CheckCode.LINK_MISSING.value])
    exit_code = cli._render_check(  # pyright: ignore[reportPrivateUsage]
        CheckReport(issues=(issue,)), args, json_mode=False
    )
    out = capsys.readouterr().out
    assert exit_code == 1
    assert "检查通过" not in out
    assert "1 个错误" in out
    render_issue.assert_not_called()


def test_render_check_clean_human_output_is_one_nonempty_line(
    mocker: MockerFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    render_issue = mocker.patch.object(cli, "render_issue", autospec=True)
    exit_code = cli._render_check(  # pyright: ignore[reportPrivateUsage]
        CheckReport(issues=()), _check_args(), json_mode=False
    )
    lines = capsys.readouterr().out.splitlines()
    assert exit_code == 0
    assert len(lines) == 1
    assert lines[0]
    render_issue.assert_not_called()


def test_render_check_json_uses_structured_payload_without_human_renderer(
    mocker: MockerFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    issue = CheckIssue(
        CheckCode.PARENT_MISSING.value, IssueSeverity.ERROR, Path("pages/a.md"), None
    )
    render_issue = mocker.patch.object(cli, "render_issue", autospec=True)
    exit_code = cli._render_check(  # pyright: ignore[reportPrivateUsage]
        CheckReport(issues=(issue,)), _check_args(), json_mode=True
    )
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload == {
        "ok": False,
        "result_type": "check",
        "severity_counts": {"error": 1, "warning": 0, "info": 0, "review": 0},
        "code_counts": [[CheckCode.PARENT_MISSING.value, 1]],
        "filtered_total": 1,
        "offset": 0,
        "limit": 20,
        "returned_count": 1,
        "has_more": False,
        "next_offset": None,
        "errors": [
            {
                "code": CheckCode.PARENT_MISSING.value,
                "severity": "error",
                "path": "pages/a.md",
                "line": None,
                "details": [],
            }
        ],
        "warnings": [],
        "infos": [],
        "reviews": [],
    }
    render_issue.assert_not_called()


# ---------------------------------------------------------------------------
# project_check：检查投影（过滤 → 分页 → 严重级别分组）
# ---------------------------------------------------------------------------


def _check_issue(
    code: CheckCode,
    path: str | None,
    severity: IssueSeverity = IssueSeverity.ERROR,
) -> CheckIssue:
    return CheckIssue(
        code.value,
        severity,
        None if path is None else Path(path),
        None,
    )


def _grouped(slice_: CheckSlice) -> list[CheckIssue]:
    return [*slice_.errors, *slice_.warnings, *slice_.infos, *slice_.reviews]


def test_check_filters_union_within_dimension_and_intersection_between_dimensions() -> None:
    report = CheckReport(
        issues=(
            _check_issue(CheckCode.PARENT_MISSING, "pages/a/index.md"),
            _check_issue(CheckCode.LINK_MISSING, "pages/ab.md"),
            _check_issue(CheckCode.PARENT_MISSING, "pages/b/index.md"),
            _check_issue(CheckCode.PAGE_CAPACITY, "pages/a.md", IssueSeverity.REVIEW),
            _check_issue(CheckCode.ROOT_MISSING_FILE, None),
        )
    )
    result = project_check(
        report,
        codes=(CheckCode.PARENT_MISSING, CheckCode.LINK_MISSING),
        path_prefixes=(Path("pages/a"), Path("pages/b")),
        offset=0,
        limit=20,
        count_only=False,
    )
    assert tuple(issue.path for issue in _grouped(result)) == (
        Path("pages/a/index.md"),
        Path("pages/b/index.md"),
    )


def test_check_path_prefix_filters_root_relative_operation_diagnostic() -> None:
    """根内相对 evidence 诊断可由 --path-prefix .draft/operations/<id> 过滤（round-12 L1）。

    checker 的 trash.package_corrupt 诊断 path 必须是根内相对路径（.draft/...）；
    JSON 负载不得依赖临时根，--path-prefix 按完整段匹配该相对路径。
    """
    operation_id = "a" * 32
    evidence_path = Path(".draft/operations") / operation_id / "cleanup-evidence.json"
    report = CheckReport(
        issues=(
            CheckIssue(
                code=CheckCode.TRASH_PACKAGE_CORRUPT.value,
                severity=IssueSeverity.ERROR,
                path=evidence_path,
                line=None,
            ),
        )
    )
    result = project_check(
        report,
        path_prefixes=(Path(".draft/operations") / operation_id,),
        offset=0,
        limit=20,
        count_only=False,
    )
    assert tuple(issue.path for issue in result.errors) == (evidence_path,)
    payload = cli._check_slice_payload(result)  # pyright: ignore[reportPrivateUsage]
    serialized = json.dumps(payload, ensure_ascii=False)
    assert ".draft/operations" in serialized
    assert "/tmp" not in serialized
    assert "/draft/.draft" not in serialized


def test_check_path_prefix_matches_path_segments_not_substrings() -> None:
    report = CheckReport(
        issues=(
            _check_issue(CheckCode.PARENT_MISSING, "pages/a/index.md"),
            _check_issue(CheckCode.PARENT_MISSING, "pages/ab.md"),
        )
    )
    result = project_check(
        report, path_prefixes=(Path("pages/a"),), offset=0, limit=20, count_only=False
    )
    assert tuple(issue.path for issue in _grouped(result)) == (Path("pages/a/index.md"),)


def test_check_repeated_filters_are_idempotent() -> None:
    report = CheckReport(issues=(_check_issue(CheckCode.PARENT_MISSING, "pages/a.md"),))
    single = project_check(
        report, codes=(CheckCode.PARENT_MISSING,), offset=0, limit=20, count_only=False
    )
    repeated = project_check(
        report,
        codes=(CheckCode.PARENT_MISSING, CheckCode.PARENT_MISSING),
        path_prefixes=(Path("pages"), Path("pages")),
        offset=0,
        limit=20,
        count_only=False,
    )
    assert single.filtered_total == repeated.filtered_total == 1


def test_check_root_diagnostic_does_not_match_any_path_prefix() -> None:
    report = CheckReport(issues=(_check_issue(CheckCode.ROOT_MISSING_FILE, None),))
    result = project_check(
        report, path_prefixes=(Path("pages"),), offset=0, limit=20, count_only=False
    )
    assert result.filtered_total == 0


def test_check_default_limit_is_twenty_and_paginates() -> None:
    report = CheckReport(
        issues=tuple(_check_issue(CheckCode.PARENT_MISSING, f"pages/p{i}.md") for i in range(25))
    )
    result = project_check(report, offset=0, limit=20, count_only=False)
    assert result.returned_count == 20
    assert result.filtered_total == 25
    assert result.has_more is True
    assert result.next_offset == 20


def test_check_count_only_keeps_counts_but_empties_arrays() -> None:
    report = CheckReport(
        issues=tuple(_check_issue(CheckCode.PARENT_MISSING, f"pages/p{i}.md") for i in range(3))
    )
    result = project_check(report, offset=0, limit=20, count_only=True)
    assert result.errors == ()
    assert result.warnings == ()
    assert result.infos == ()
    assert result.reviews == ()
    assert result.filtered_total == 3
    assert result.returned_count == 3
    assert dict(result.severity_counts)["error"] == 3


def test_check_severity_and_code_counts_come_from_unfiltered_scan() -> None:
    report = CheckReport(
        issues=(
            _check_issue(CheckCode.PARENT_MISSING, "pages/a.md"),
            _check_issue(CheckCode.PARENT_MISSING, "pages/b.md"),
            _check_issue(CheckCode.PAGE_CAPACITY, "pages/c.md", IssueSeverity.REVIEW),
        )
    )
    result = project_check(
        report, codes=(CheckCode.PAGE_CAPACITY,), offset=0, limit=20, count_only=False
    )
    assert result.filtered_total == 1
    assert dict(result.severity_counts) == {
        "error": 2,
        "warning": 0,
        "info": 0,
        "review": 1,
    }
    assert dict(result.code_counts) == {
        CheckCode.PARENT_MISSING.value: 2,
        CheckCode.PAGE_CAPACITY.value: 1,
    }
    assert result.ok is False


@pytest.mark.parametrize("value", ["", "/abs", "../up", "a/../../b"])
def test_check_path_prefix_rejects_invalid_forms(value: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        cli._check_path_prefix(value)  # pyright: ignore[reportPrivateUsage]


def test_check_path_prefix_accepts_canonical_relative_path() -> None:
    assert cli._check_path_prefix("pages/a") == Path("pages/a")  # pyright: ignore[reportPrivateUsage]


# ---------------------------------------------------------------------------
# collection_payload：集合查询双分页信封
# ---------------------------------------------------------------------------


def test_partial_query_pages_items_and_blockers_with_same_window() -> None:
    from draft.cli_results import collection_payload

    items = [{"page": f"pages/p{i}.md"} for i in range(25)]
    blockers = [Path(f"pages/broken{i}.md") for i in range(45)]
    payload = collection_payload(
        items,
        blocking_paths=blockers,
        complete=False,
        offset=20,
        limit=20,
        count_only=False,
    )
    assert payload["offset"] == 20
    assert payload["limit"] == 20
    assert payload["total"] == 25
    assert payload["has_more"] is False
    assert payload["next_offset"] is None
    assert payload["blocking_total"] == len(blockers)
    assert payload["blocking_has_more"] is True
    assert payload["blocking_next_offset"] == 40
    assert payload["complete"] is False


def test_collection_payload_complete_shows_zero_blockers() -> None:
    from draft.cli_results import collection_payload

    payload = collection_payload(
        [{"page": "pages/a.md"}],
        blocking_paths=(),
        complete=True,
        offset=0,
        limit=20,
        count_only=False,
    )
    assert payload["complete"] is True
    assert payload["blocking_total"] == 0
    assert payload["blocking_has_more"] is False
    assert payload["blocking_next_offset"] is None
    assert payload["blocking_paths"] == []


def test_collection_payload_count_only_empties_both_collections() -> None:
    from draft.cli_results import collection_payload

    payload = collection_payload(
        [{"page": "pages/a.md"}],
        blocking_paths=[Path("pages/broken.md")],
        complete=False,
        offset=0,
        limit=20,
        count_only=True,
    )
    assert payload["items"] == []
    assert payload["blocking_paths"] == []
    assert payload["total"] == 1
    assert payload["blocking_total"] == 1


# ---------------------------------------------------------------------------
# _operation_payload / _trash_payload
# ---------------------------------------------------------------------------


def test_operation_payload_maps_restore_result_to_dict() -> None:
    result = OperationResult(
        operation_id="a" * 32,
        kind="restore",
        phase=OperationPhase.COMPLETED,
        changed_paths=(Path("pages/a.md"), Path("pages/b.md")),
    )
    payload = cli._operation_payload(result)  # pyright: ignore[reportPrivateUsage]  # 验证恢复结果到 JSON 载荷：保留 operation_id
    assert payload == {
        "operation_id": "a" * 32,
        "changed_paths": ["pages/a.md", "pages/b.md"],
    }


def test_trash_payload_maps_result_with_deadlines_to_dict() -> None:
    result = TrashResult(
        operation_id="b" * 32,
        phase=OperationPhase.TRASH_RETAINED,
        package=Path(".draft/trash/outgoing/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"),
        restore_expires_at=datetime(2026, 8, 13, 12, tzinfo=UTC),
        purge_eligible_at=datetime(2026, 9, 12, 12, tzinfo=UTC),
        warnings=(),
    )
    payload = cli._trash_payload(result)  # pyright: ignore[reportPrivateUsage]  # 验证回收结果到 JSON 载荷：保留 operation_id 与期限
    assert payload == {
        "operation_id": "b" * 32,
        "restore_expires_at": "2026-08-13T12:00:00Z",
        "changed_paths": [],
    }


def test_trash_payload_maps_none_deadlines_to_null() -> None:
    result = TrashResult(
        operation_id="b" * 32,
        phase=OperationPhase.TRASH_RETAINED,
        package=Path(".draft/trash/outgoing/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"),
    )
    payload = cli._trash_payload(result)  # pyright: ignore[reportPrivateUsage]  # 验证回收结果无期限时映射为 null，且不暴露内部包路径
    assert payload["restore_expires_at"] is None
    assert "package" not in payload
    assert "phase" not in payload


# ---------------------------------------------------------------------------
# _exit_for_error
# ---------------------------------------------------------------------------


def test_exit_for_error_returns_operation_exit_for_operation_codes() -> None:
    assert cli._exit_for_error(DraftError(CheckCode.OPERATION_RECOVERY_REQUIRED.value)) == 3  # pyright: ignore[reportPrivateUsage]  # 验证 operation.* 错误映射到操作退出码


def test_exit_for_error_returns_domain_exit_for_other_codes() -> None:
    assert cli._exit_for_error(DraftError("init.not_empty")) == 2  # pyright: ignore[reportPrivateUsage]  # 验证普通错误映射到域退出码
    assert cli._exit_for_error(DraftError("path.escape")) == 2  # pyright: ignore[reportPrivateUsage]  # 验证路径错误映射到域退出码
    assert cli._exit_for_error(DraftError("trash.protected")) == 2  # pyright: ignore[reportPrivateUsage]  # 验证回收错误映射到域退出码


# ---------------------------------------------------------------------------
# main: check / version / error handling
# ---------------------------------------------------------------------------


def test_main_version_returns_zero(capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["--version"])
    assert code == 0
    assert capsys.readouterr().out.strip() == __version__


def _stub_root_discovery(mocker: MockerFixture) -> None:
    mocker.patch(
        "draft.cli.select_managed_root",
        autospec=True,
        return_value=_managed(),
    )


def test_main_check_clean_returns_zero(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    _stub_root_discovery(mocker)
    check_draft = mocker.patch("draft.checker.check_draft", autospec=True)
    check_draft.return_value = CheckReport(issues=())
    code = cli.main(["check", "--root", "/draft", "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["result_type"] == "check"


def test_main_check_with_errors_returns_one(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    report = CheckReport(
        issues=(
            CheckIssue(
                CheckCode.PARENT_MISSING.value, IssueSeverity.ERROR, Path("pages/a.md"), None
            ),
        )
    )
    _stub_root_discovery(mocker)
    check_draft = mocker.patch("draft.checker.check_draft", autospec=True)
    check_draft.return_value = report
    code = cli.main(["check", "--root", "/draft", "--json"])
    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False


def test_main_check_draft_error_returns_domain_exit_code(
    mocker: MockerFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _stub_root_discovery(mocker)
    check_draft = mocker.patch("draft.checker.check_draft", autospec=True)
    check_draft.side_effect = DraftError("root.missing", Path("/draft"))
    code = cli.main(["check", "--root", "/draft", "--json"])
    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "root.missing"


def test_main_dispatch_draft_error_returns_domain_exit_code(
    mocker: MockerFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    initialize = mocker.patch("draft.lifecycle.initialize", autospec=True)
    initialize.side_effect = DraftError("init.not_empty", Path("/draft"))
    code = cli.main(["init", "/draft", "--json"])
    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "init.not_empty"


def test_main_dispatch_os_error_returns_runtime_exit_code(
    mocker: MockerFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    initialize = mocker.patch("draft.lifecycle.initialize", autospec=True)
    initialize.side_effect = OSError("disk full")
    code = cli.main(["init", "/draft", "--json"])
    assert code == 4
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "runtime.os_error"
