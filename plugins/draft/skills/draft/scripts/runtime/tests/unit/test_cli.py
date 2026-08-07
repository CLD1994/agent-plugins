from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

import draft.cli as cli
from draft import __version__
from draft.errors import DraftError
from draft.models import (
    CheckIssue,
    CheckReport,
    IssueSeverity,
    LifecycleWarning,
    OperationPhase,
    OperationResult,
    TrashResult,
)
from draft.paths import ResolvedPath
from draft.trash_lifecycle import LifecycleAdvanceReport, LifecycleFailure

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


def test_build_parser_parses_check_with_candidate_flag() -> None:
    args = cli.build_parser().parse_args(["check", "/draft", "--candidate"])
    assert args.command == "check"
    assert args.candidate is True


def test_build_parser_parses_promote_with_variable_children() -> None:
    args = cli.build_parser().parse_args(
        ["promote", "/draft", "pages/a.md", "pages/b.md", "pages/c.md"]
    )
    assert args.command == "promote"
    assert args.page == "pages/a.md"
    assert args.children == ["pages/b.md", "pages/c.md"]


def test_build_parser_json_flag_before_subcommand() -> None:
    args = cli.build_parser().parse_args(["--json", "check", "/draft"])
    assert args.json is True


def test_build_parser_json_flag_after_subcommand() -> None:
    args = cli.build_parser().parse_args(["check", "/draft", "--json"])
    assert args.json is True


# ---------------------------------------------------------------------------
# _dispatch: init / adopt
# ---------------------------------------------------------------------------


def test_dispatch_init_calls_initialize_and_returns_success() -> None:
    args = cli.build_parser().parse_args(["init", "/draft"])
    with patch("draft.lifecycle.initialize", autospec=True) as initialize:
        payload, human, exit_code = cli._dispatch(args)  # pyright: ignore[reportPrivateUsage]  # 验证 init 分派到 initialize 并返回成功载荷
    initialize.assert_called_once_with(Path("/draft"))
    assert payload == {"result_type": "init", "root": "/draft"}
    assert human == "已初始化草稿：/draft"
    assert exit_code == 0


def test_dispatch_adopt_calls_adopt_and_returns_success() -> None:
    args = cli.build_parser().parse_args(["adopt", "/draft"])
    with patch("draft.lifecycle.adopt", autospec=True) as adopt:
        payload, human, exit_code = cli._dispatch(args)  # pyright: ignore[reportPrivateUsage]  # 验证 adopt 分派到 adopt 并返回成功载荷
    adopt.assert_called_once_with(Path("/draft"))
    assert payload == {"result_type": "adopt", "root": "/draft"}
    assert human == "已接管草稿：/draft"
    assert exit_code == 0


# ---------------------------------------------------------------------------
# _dispatch: move / promote / collapse / trash（经 _page_operand 隔离）
# ---------------------------------------------------------------------------


def test_dispatch_move_calls_move_page_with_paged_operands() -> None:
    args = cli.build_parser().parse_args(["move", "/draft", "pages/a.md", "pages/b.md"])
    with (
        patch("draft.paths.resolve_page_operand", autospec=True) as resolve_page_operand,
        patch("draft.structure.move_page", autospec=True) as move_page,
    ):
        resolve_page_operand.side_effect = [_resolved("pages/a.md"), _resolved("pages/b.md")]
        move_page.return_value = _operation_result(kind="move")
        payload, human, exit_code = cli._dispatch(args)  # pyright: ignore[reportPrivateUsage]  # 验证 move 分派到 move_page 并组装操作载荷
    move_page.assert_called_once_with(Path("/draft"), Path("pages/a.md"), Path("pages/b.md"))
    assert payload == {
        "result_type": "move",
        "operation_id": "a" * 32,
        "phase": "completed",
        "changed_paths": ["pages/a.md", "pages/b.md"],
        "warnings": [],
    }
    assert human == "已移动 pages/a.md → pages/b.md"
    assert exit_code == 0


def test_dispatch_promote_calls_promote_page_with_paged_operands() -> None:
    args = cli.build_parser().parse_args(
        ["promote", "/draft", "pages/a.md", "pages/b.md", "pages/c.md"]
    )
    with (
        patch("draft.paths.resolve_page_operand", autospec=True) as resolve_page_operand,
        patch("draft.structure.promote_page", autospec=True) as promote_page,
    ):
        resolve_page_operand.side_effect = [
            _resolved("pages/a.md"),
            _resolved("pages/b.md"),
            _resolved("pages/c.md"),
        ]
        promote_page.return_value = _operation_result(kind="promote")
        payload, human, exit_code = cli._dispatch(args)  # pyright: ignore[reportPrivateUsage]  # 验证 promote 分派到 promote_page 并组装操作载荷
    promote_page.assert_called_once_with(
        Path("/draft"), Path("pages/a.md"), [Path("pages/b.md"), Path("pages/c.md")]
    )
    assert payload["result_type"] == "promote"
    assert human == "已升格 pages/a.md（2 个子页）"
    assert exit_code == 0


def test_dispatch_collapse_calls_collapse_hub_with_paged_operand() -> None:
    args = cli.build_parser().parse_args(["collapse", "/draft", "pages/hub/index.md"])
    with (
        patch("draft.paths.resolve_page_operand", autospec=True) as resolve_page_operand,
        patch("draft.structure.collapse_hub", autospec=True) as collapse_hub,
    ):
        resolve_page_operand.return_value = _resolved("pages/hub/index.md")
        collapse_hub.return_value = _operation_result(kind="collapse")
        payload, human, exit_code = cli._dispatch(args)  # pyright: ignore[reportPrivateUsage]  # 验证 collapse 分派到 collapse_hub 并组装操作载荷
    collapse_hub.assert_called_once_with(Path("/draft"), Path("pages/hub/index.md"))
    assert payload["result_type"] == "collapse"
    assert human == "已降级 pages/hub/index.md"
    assert exit_code == 0


def test_dispatch_trash_calls_trash_page_with_paged_operand() -> None:
    args = cli.build_parser().parse_args(["trash", "/draft", "pages/a.md"])
    with (
        patch("draft.paths.resolve_page_operand", autospec=True) as resolve_page_operand,
        patch("draft.trash.trash_page", autospec=True) as trash_page,
    ):
        resolve_page_operand.return_value = _resolved("pages/a.md")
        trash_page.return_value = _trash_result()
        payload, human, exit_code = cli._dispatch(args)  # pyright: ignore[reportPrivateUsage]  # 验证 trash 分派到 trash_page 并组装回收载荷
    trash_page.assert_called_once_with(Path("/draft"), Path("pages/a.md"))
    assert payload["result_type"] == "trash"
    assert payload["phase"] == "trash_retained"
    assert human == "已回收 pages/a.md（trash_retained）"
    assert exit_code == 0


def test_dispatch_trash_includes_first_warning_in_human_text() -> None:
    args = cli.build_parser().parse_args(["trash", "/draft", "pages/a.md"])
    warning = LifecycleWarning(
        code="trash.handoff_failed",
        operation_id="b" * 32,
        details=("OSError", "disk full"),
    )
    with (
        patch("draft.paths.resolve_page_operand", autospec=True) as resolve_page_operand,
        patch("draft.trash.trash_page", autospec=True) as trash_page,
    ):
        resolve_page_operand.return_value = _resolved("pages/a.md")
        trash_page.return_value = _trash_result(warnings=(warning,))
        _, human, exit_code = cli._dispatch(args)  # pyright: ignore[reportPrivateUsage]  # 验证 trash 载荷包含首个警告的人类可读文本
    assert "警告" in human
    assert "trash.handoff_failed" in human
    assert exit_code == 0


# ---------------------------------------------------------------------------
# _dispatch: restore / recover
# ---------------------------------------------------------------------------


def test_dispatch_restore_completed_returns_success_payload() -> None:
    args = cli.build_parser().parse_args(["restore", "/draft", "b" * 32])
    with patch("draft.trash.restore_retained_trash", autospec=True) as restore:
        restore.return_value = _operation_result(
            operation_id="b" * 32,
            kind="restore",
            phase=OperationPhase.COMPLETED,
            changed_paths=(Path("pages/a.md"),),
        )
        payload, human, exit_code = cli._dispatch(args)  # pyright: ignore[reportPrivateUsage]  # 验证 restore 成功路径返回完成载荷与退出码 0
    assert payload["result_type"] == "restore"
    assert payload["phase"] == "completed"
    assert exit_code == 0
    assert "已恢复" in human


def test_dispatch_restore_rolled_back_returns_exit_three() -> None:
    args = cli.build_parser().parse_args(["restore", "/draft", "b" * 32])
    with patch("draft.trash.restore_retained_trash", autospec=True) as restore:
        restore.return_value = _operation_result(
            operation_id="b" * 32,
            kind="restore",
            phase=OperationPhase.ROLLED_BACK,
            changed_paths=(),
        )
        payload, human, exit_code = cli._dispatch(args)  # pyright: ignore[reportPrivateUsage]  # 验证 restore 回滚路径返回 ok=False 与退出码 3
    assert payload["result_type"] == "restore"
    assert payload["ok"] is False
    assert payload["phase"] == "rolled_back"
    assert exit_code == 3
    assert "回滚" in human


def test_dispatch_recover_success_returns_results_payload() -> None:
    args = cli.build_parser().parse_args(["recover", "/draft"])
    report = LifecycleAdvanceReport(results=(), error=None)
    with patch("draft.trash_lifecycle.recover_draft", autospec=True) as recover_draft:
        recover_draft.return_value = report
        payload, human, exit_code = cli._dispatch(args)  # pyright: ignore[reportPrivateUsage]  # 验证 recover 成功路径返回结果载荷与退出码 0
    assert payload == {"result_type": "recover", "results": []}
    assert human == "恢复完成：0 项结果"
    assert exit_code == 0


def test_dispatch_recover_error_returns_failure_payload_and_exit_code() -> None:
    args = cli.build_parser().parse_args(["recover", "/draft"])
    failure = LifecycleFailure(
        operation_id="c" * 32,
        code="operation.inconsistent_state",
        paths=(Path(".draft/operations/cccccccccccccccccccccccccccccccc/journal.json"),),
        details=("inconsistent",),
    )
    report = LifecycleAdvanceReport(results=(), error=failure)
    with patch("draft.trash_lifecycle.recover_draft", autospec=True) as recover_draft:
        recover_draft.return_value = report
        payload, human, exit_code = cli._dispatch(args)  # pyright: ignore[reportPrivateUsage]  # 验证 recover 失败路径保留停止位置并返回操作类退出码
    assert payload["result_type"] == "recover"
    assert payload["ok"] is False
    error = payload["error"]
    assert isinstance(error, dict)
    assert error["operation_id"] == "c" * 32
    assert error["code"] == "operation.inconsistent_state"
    assert payload["stopped_at_operation_id"] == "c" * 32
    assert exit_code == 3
    assert "恢复停止" in human


# ---------------------------------------------------------------------------
# _dispatch: unknown command
# ---------------------------------------------------------------------------


def test_dispatch_unknown_command_raises_draft_error() -> None:
    args = argparse.Namespace(command="nonexistent")
    with pytest.raises(DraftError) as caught:
        cli._dispatch(args)  # pyright: ignore[reportPrivateUsage]  # 验证未知命令路径抛出 cli.unknown_command
    assert caught.value.code == "cli.unknown_command"


# ---------------------------------------------------------------------------
# _operation_payload / _trash_payload
# ---------------------------------------------------------------------------


def test_operation_payload_maps_result_to_dict() -> None:
    result = OperationResult(
        operation_id="a" * 32,
        kind="move",
        phase=OperationPhase.COMPLETED,
        changed_paths=(Path("pages/a.md"), Path("pages/b.md")),
        warnings=(LifecycleWarning(code="op.warning", operation_id="a" * 32, details=("detail",)),),
    )
    payload = cli._operation_payload(result)  # pyright: ignore[reportPrivateUsage]  # 验证操作结果到 JSON 载荷的完整映射
    assert payload == {
        "operation_id": "a" * 32,
        "phase": "completed",
        "changed_paths": ["pages/a.md", "pages/b.md"],
        "warnings": [{"code": "op.warning", "operation_id": "a" * 32, "details": ["detail"]}],
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
    payload = cli._trash_payload(result)  # pyright: ignore[reportPrivateUsage]  # 验证回收结果到 JSON 载荷含期限字段的完整映射
    assert payload == {
        "operation_id": "b" * 32,
        "phase": "trash_retained",
        "package": ".draft/trash/outgoing/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "restore_expires_at": "2026-08-13T12:00:00Z",
        "purge_eligible_at": "2026-09-12T12:00:00Z",
        "warnings": [],
    }


def test_trash_payload_maps_none_deadlines_to_null() -> None:
    result = TrashResult(
        operation_id="b" * 32,
        phase=OperationPhase.TRASH_RETAINED,
        package=Path(".draft/trash/outgoing/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"),
    )
    payload = cli._trash_payload(result)  # pyright: ignore[reportPrivateUsage]  # 验证回收结果无期限时映射为 null
    assert payload["restore_expires_at"] is None
    assert payload["purge_eligible_at"] is None


# ---------------------------------------------------------------------------
# _exit_for_error
# ---------------------------------------------------------------------------


def test_exit_for_error_returns_operation_exit_for_operation_codes() -> None:
    assert cli._exit_for_error(DraftError("operation.recovery_required")) == 3  # pyright: ignore[reportPrivateUsage]  # 验证 operation.* 错误映射到操作退出码


def test_exit_for_error_returns_operation_exit_for_adopt_recovery_required() -> None:
    assert cli._exit_for_error(DraftError("adopt.recovery_required")) == 3  # pyright: ignore[reportPrivateUsage]  # 验证 adopt.recovery_required 映射到操作退出码


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


def test_main_check_clean_returns_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with patch("draft.checker.check_draft", autospec=True) as check_draft:
        check_draft.return_value = CheckReport(issues=())
        code = cli.main(["check", "/draft", "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["result_type"] == "check"


def test_main_check_with_errors_returns_one(capsys: pytest.CaptureFixture[str]) -> None:
    report = CheckReport(
        issues=(CheckIssue("output.missing", IssueSeverity.ERROR, Path("pages/a.md"), None),)
    )
    with patch("draft.checker.check_draft", autospec=True) as check_draft:
        check_draft.return_value = report
        code = cli.main(["check", "/draft", "--json"])
    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False


def test_main_check_draft_error_returns_domain_exit_code(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch("draft.checker.check_draft", autospec=True) as check_draft:
        check_draft.side_effect = DraftError("root.missing", Path("/draft"))
        code = cli.main(["check", "/draft", "--json"])
    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "root.missing"


def test_main_dispatch_draft_error_returns_domain_exit_code(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch("draft.lifecycle.initialize", autospec=True) as initialize:
        initialize.side_effect = DraftError("init.not_empty", Path("/draft"))
        code = cli.main(["init", "/draft", "--json"])
    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "init.not_empty"


def test_main_dispatch_os_error_returns_runtime_exit_code(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch("draft.lifecycle.initialize", autospec=True) as initialize:
        initialize.side_effect = OSError("disk full")
        code = cli.main(["init", "/draft", "--json"])
    assert code == 4
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "runtime.os_error"
