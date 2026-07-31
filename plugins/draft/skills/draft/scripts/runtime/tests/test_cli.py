import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from conftest import build_valid_candidate, snapshot_tree

from draft import __version__
from draft.cli import main


def test_version_prints_package_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == __version__


def run_json(args: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, dict[str, object]]:
    code = main(args)
    output = capsys.readouterr().out
    return code, cast("dict[str, object]", json.loads(output))


def as_dicts(value: object) -> list[dict[str, object]]:
    assert isinstance(value, list)
    return [cast("dict[str, object]", item) for item in cast("list[object]", value)]


def test_init_success_and_not_empty_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "topic"
    code, payload = run_json(["init", str(root), "--json"], capsys)
    assert code == 0
    assert payload["ok"] is True
    assert payload["result_type"] == "init"

    code, payload = run_json(["init", str(root), "--json"], capsys)
    assert code == 2
    assert payload["ok"] is False
    assert payload["result_type"] == "error"
    error = payload["error"]
    assert isinstance(error, dict)
    assert error["code"] == "init.not_empty"


def test_adopt_success_and_invalid_candidate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "candidate"
    root.mkdir()
    (root / "pages").mkdir()
    (root / "index.md").write_text(
        "# 主题\n\n> 状态：`已关闭：纯导航页，不表达独立结论`\n\n- [结论汇总](conclusions.md)\n",
        encoding="utf-8",
    )
    (root / "conclusions.md").write_text(
        "# 结论汇总\n\n> 状态：`已确认`\n>\n> 父主题：[根索引](index.md)\n",
        encoding="utf-8",
    )
    code, payload = run_json(["adopt", str(root), "--json"], capsys)
    assert code == 0
    assert payload["ok"] is True

    broken = tmp_path / "broken"
    broken.mkdir()
    code, payload = run_json(["adopt", str(broken), "--json"], capsys)
    assert code == 2
    error = payload["error"]
    assert isinstance(error, dict)
    assert error["code"] == "adopt.invalid_candidate"


def test_migrate_subcommand_is_removed(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main(["migrate", str(tmp_path), "--from", "legacy-v0", "--to", str(tmp_path)])


def test_check_parser_accepts_candidate_flag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "candidate"
    build_valid_candidate(root)

    code, payload = run_json(["check", str(root), "--candidate", "--json"], capsys)

    assert code == 0
    assert payload["ok"] is True


def test_candidate_check_is_read_only(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "candidate"
    build_valid_candidate(root)
    before = snapshot_tree(root)

    code, payload = run_json(["check", str(root), "--candidate", "--json"], capsys)

    assert code == 0
    assert payload["ok"] is True
    assert snapshot_tree(root) == before
    assert not (root / ".draft").exists()


def test_candidate_check_rejects_registered_root_without_mutation(
    registered_draft: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    before = snapshot_tree(registered_draft)
    code, payload = run_json(
        ["check", str(registered_draft), "--candidate", "--json"],
        capsys,
    )
    assert code == 1
    assert payload["ok"] is False
    assert payload["result_type"] == "check"
    assert [issue["code"] for issue in as_dicts(payload["errors"])] == [
        "candidate.already_registered"
    ]
    assert snapshot_tree(registered_draft) == before


def test_candidate_check_registered_root_human_hint(
    registered_draft: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(["check", str(registered_draft), "--candidate"])
    output = capsys.readouterr().out
    assert code == 1
    assert "candidate.already_registered" in output
    assert "--candidate" in output


def test_candidate_check_provisional_marker_is_recovery_required(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "candidate"
    build_valid_candidate(root)
    internal = root / ".draft"
    internal.mkdir()
    (internal / "draft.json").write_text(
        '{"format_version":1,"adopt_state":"provisional","candidate_manifest":[]}\n',
        encoding="utf-8",
    )
    before = snapshot_tree(root)

    code, payload = run_json(["check", str(root), "--candidate", "--json"], capsys)

    assert code == 3
    assert payload["ok"] is False
    assert payload["result_type"] == "error"
    error = payload["error"]
    assert isinstance(error, dict)
    assert error["code"] == "adopt.recovery_required"
    assert snapshot_tree(root) == before


def test_candidate_check_rejects_internal_state_without_mutation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    variants: list[str] = ["empty", "operation", "trash"]
    for variant in variants:
        root = tmp_path / variant
        build_valid_candidate(root)
        internal = root / ".draft"
        if variant == "empty":
            internal.mkdir()
        elif variant == "operation":
            journal_dir = internal / "operations" / ("a" * 32)
            journal_dir.mkdir(parents=True)
            (journal_dir / "journal.json").write_text("{}\n", encoding="utf-8")
        else:
            package = internal / "trash" / "outgoing" / ("b" * 32)
            package.mkdir(parents=True)
            (package / "page.md").write_text("# 回收\n", encoding="utf-8")
        before = snapshot_tree(root)

        code, payload = run_json(["check", str(root), "--candidate", "--json"], capsys)

        assert code == 1, variant
        assert payload["ok"] is False
        codes = [issue["code"] for issue in as_dicts(payload["errors"])]
        assert codes == ["candidate.internal_state"], variant
        assert snapshot_tree(root) == before, variant


def test_check_clean_and_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "topic"
    assert main(["init", str(root)]) == 0
    capsys.readouterr()

    code, payload = run_json(["check", str(root), "--json"], capsys)
    assert code == 0
    assert payload["ok"] is True
    assert payload["errors"] == []

    question = root / "pages/question.md"
    question.write_text(
        "# 问题\n\n> 状态：`已确认`\n>\n> 父主题：[根索引](../index.md)\n\n无产出。\n",
        encoding="utf-8",
    )
    code, payload = run_json(["check", str(root), "--json"], capsys)
    assert code == 1
    assert payload["ok"] is False
    error_dicts = as_dicts(payload["errors"])
    codes = {str(issue["code"]) for issue in error_dicts}
    assert "output.missing" in codes
    first = error_dicts[0]
    assert "path" in first
    assert "line" in first
    assert "details" in first


def test_json_flag_both_positions_identical(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "topic"
    assert main(["init", str(root)]) == 0
    capsys.readouterr()

    code_a, payload_a = run_json(["--json", "check", str(root)], capsys)
    code_b, payload_b = run_json(["check", str(root), "--json"], capsys)
    assert code_a == code_b == 0
    assert payload_a == payload_b


def build_draft(root: Path) -> None:
    assert main(["init", str(root)]) == 0
    (root / "pages/a.md").write_text(
        "# A\n\n> 状态：`待讨论`\n>\n> 父主题：[根索引](../index.md)\n\nA。\n",
        encoding="utf-8",
    )
    index = root / "index.md"
    index.write_text(
        index.read_text(encoding="utf-8") + "- [A](pages/a.md)\n",
        encoding="utf-8",
    )


def test_move_success_and_escape_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "topic"
    build_draft(root)
    capsys.readouterr()

    code, payload = run_json(
        ["move", str(root), "pages/a.md", "pages/renamed.md", "--json"], capsys
    )
    assert code == 0
    assert payload["ok"] is True
    assert (root / "pages/renamed.md").is_file()

    code, payload = run_json(
        ["move", str(root), "pages/renamed.md", "../outside.md", "--json"], capsys
    )
    assert code == 2
    error = payload["error"]
    assert isinstance(error, dict)
    assert error["code"] == "path.escape"


def test_promote_success_and_missing_child(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "topic"
    build_draft(root)
    capsys.readouterr()

    code, payload = run_json(["promote", str(root), "pages/a.md", "--json"], capsys)
    assert code == 2
    error = payload["error"]
    assert isinstance(error, dict)
    assert error["code"] == "structure.missing_child"

    (root / "pages/child.md").write_text(
        "# 子\n\n> 状态：`待讨论`\n>\n> 父主题：[根索引](../index.md)\n\n子。\n",
        encoding="utf-8",
    )
    index = root / "index.md"
    index.write_text(
        index.read_text(encoding="utf-8") + "- [子](pages/child.md)\n",
        encoding="utf-8",
    )
    code, payload = run_json(
        ["promote", str(root), "pages/a.md", "pages/child.md", "--json"], capsys
    )
    assert code == 0
    assert (root / "pages/a/index.md").is_file()
    assert (root / "pages/a/child.md").is_file()


def test_collapse_success_and_not_hub(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "topic"
    build_draft(root)
    (root / "pages/empty").mkdir()
    (root / "pages/empty/index.md").write_text(
        "# 空枢纽\n\n> 状态：`已关闭：纯导航页`\n>\n> 父主题：[根索引](../../index.md)\n",
        encoding="utf-8",
    )
    index = root / "index.md"
    index.write_text(
        index.read_text(encoding="utf-8") + "- [空枢纽](pages/empty/index.md)\n",
        encoding="utf-8",
    )
    capsys.readouterr()

    code, payload = run_json(["collapse", str(root), "pages/empty/index.md", "--json"], capsys)
    assert code == 0
    assert (root / "pages/empty.md").is_file()

    code, payload = run_json(["collapse", str(root), "pages/a.md", "--json"], capsys)
    assert code == 2
    error = payload["error"]
    assert isinstance(error, dict)
    assert error["code"] == "structure.not_hub"


def test_trash_protected_failure_and_retained_success(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "topic"
    build_draft(root)
    capsys.readouterr()

    code, payload = run_json(["trash", str(root), "index.md", "--json"], capsys)
    assert code == 2
    error = payload["error"]
    assert isinstance(error, dict)
    assert error["code"] == "trash.protected"

    code, payload = run_json(["trash", str(root), "pages/a.md", "--json"], capsys)
    assert code == 0
    assert payload["ok"] is True
    assert payload["phase"] == "trash_retained"
    # 公开 JSON 信封必须携带承诺的两个期限。
    assert payload["restore_expires_at"] is not None
    assert payload["purge_eligible_at"] is not None
    outgoing = root / ".draft/trash/outgoing"
    assert len(list(outgoing.iterdir())) == 1
    assert not (root / ".draft/operation.lock").exists()


def _trash_journal_path(root: Path, operation_id: str) -> Path:
    return root / ".draft/operations" / operation_id / "journal.json"


def _rewrite_journal_phase(root: Path, operation_id: str, phase: str) -> None:
    import json as json_module

    path = _trash_journal_path(root, operation_id)
    data = json_module.loads(path.read_text(encoding="utf-8"))
    data["phase"] = phase
    path.write_text(json_module.dumps(data), encoding="utf-8")


def test_restore_cli_completed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "topic"
    build_draft(root)
    capsys.readouterr()
    _, trash_payload = run_json(["trash", str(root), "pages/a.md", "--json"], capsys)
    operation_id = trash_payload["operation_id"]
    assert isinstance(operation_id, str)

    code, payload = run_json(["restore", str(root), operation_id, "--json"], capsys)

    assert code == 0
    assert payload["ok"] is True
    assert payload["result_type"] == "restore"
    assert payload["phase"] == "completed"
    assert (root / "pages/a.md").is_file()


def test_restore_cli_rolled_back_is_exit_three(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "topic"
    build_draft(root)
    capsys.readouterr()
    _, trash_payload = run_json(["trash", str(root), "pages/a.md", "--json"], capsys)
    operation_id = trash_payload["operation_id"]
    assert isinstance(operation_id, str)

    import draft.operations as operations_module
    from draft.models import OperationPhase

    original_write = operations_module.write_journal

    def inject_concurrent_page(journal: object) -> None:
        original_write(journal)  # type: ignore[arg-type]
        if getattr(journal, "phase", None) is OperationPhase.PREPARED:
            (root / "pages/a.md").write_text(
                "# A\n\n> 状态：`待讨论`\n>\n> 父主题：[根索引](../index.md)\n\nA。\n",
                encoding="utf-8",
            )

    monkeypatch.setattr("draft.operations.write_journal", inject_concurrent_page)
    code, payload = run_json(["restore", str(root), operation_id, "--json"], capsys)
    monkeypatch.undo()
    human = capsys.readouterr().out

    assert code == 3
    assert payload["ok"] is False
    assert payload["result_type"] == "restore"
    assert payload["phase"] == "rolled_back"
    assert "已恢复" not in human
    assert (root / ".draft/trash/outgoing" / operation_id).is_dir()


def test_restore_cli_expired_is_exit_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "topic"
    build_draft(root)
    capsys.readouterr()
    _, trash_payload = run_json(["trash", str(root), "pages/a.md", "--json"], capsys)
    operation_id = trash_payload["operation_id"]
    assert isinstance(operation_id, str)
    # 模拟到期推进已把 T 转为 trash_handoff_pending。
    _rewrite_journal_phase(root, operation_id, "trash_handoff_pending")

    code, payload = run_json(["restore", str(root), operation_id, "--json"], capsys)

    assert code == 2
    assert payload["ok"] is False
    assert payload["result_type"] == "error"
    error = payload["error"]
    assert isinstance(error, dict)
    assert error["code"] == "trash.restore_expired"
    assert (root / ".draft/trash/outgoing" / operation_id).is_dir()


def test_restore_cli_conflict_is_exit_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "topic"
    build_draft(root)
    capsys.readouterr()
    _, trash_payload = run_json(["trash", str(root), "pages/a.md", "--json"], capsys)
    operation_id = trash_payload["operation_id"]
    assert isinstance(operation_id, str)
    # 原路径已被外部重建：恢复在认领后发现冲突。
    (root / "pages/a.md").write_text("# 重建\n", encoding="utf-8")

    code, payload = run_json(["restore", str(root), operation_id, "--json"], capsys)

    assert code == 2
    assert payload["ok"] is False
    assert payload["result_type"] == "error"
    error = payload["error"]
    assert isinstance(error, dict)
    assert error["code"] == "trash.restore_conflict"
    assert (root / ".draft/trash/outgoing" / operation_id).is_dir()


def test_restore_cli_unknown_operation_is_exit_three(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "topic"
    build_draft(root)
    capsys.readouterr()

    code, payload = run_json(["restore", str(root), "f" * 32, "--json"], capsys)

    assert code == 3
    assert payload["ok"] is False
    assert payload["result_type"] == "error"
    error = payload["error"]
    assert isinstance(error, dict)
    assert error["code"] == "operation.journal_corrupt"


def test_recover_nothing_to_do(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "topic"
    assert main(["init", str(root)]) == 0
    capsys.readouterr()

    code, payload = run_json(["recover", str(root), "--json"], capsys)
    assert code == 0
    assert payload["ok"] is True


def test_missing_root_is_exit_two(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, payload = run_json(["check", str(tmp_path / "nope"), "--json"], capsys)
    assert code == 2
    error = payload["error"]
    assert isinstance(error, dict)
    assert error["code"] == "root.missing"


def test_help_matrix_exits_zero() -> None:
    for args in (
        ["--help"],
        ["init", "--help"],
        ["adopt", "--help"],
        ["check", "--help"],
        ["move", "--help"],
        ["promote", "--help"],
        ["collapse", "--help"],
        ["trash", "--help"],
        ["restore", "--help"],
        ["recover", "--help"],
    ):
        with pytest.raises(SystemExit) as captured:
            main(args)
        assert captured.value.code == 0


def test_bind_and_unbind_round_trip(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "topic"
    build_draft(root)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    skill = tmp_path / "SKILL.md"
    skill.write_text("stub skill", encoding="utf-8")
    monkeypatch.setenv("DRAFT_DATA", str(data_dir))
    monkeypatch.setenv("DRAFT_SKILL_PATH", str(skill))
    capsys.readouterr()

    code, payload = run_json(["bind", str(root), "--session", "abc123", "--json"], capsys)
    assert code == 0
    assert payload["ok"] is True
    assert payload["result_type"] == "bind"
    assert payload["root"] == str(root.resolve())
    assert "platform" not in payload
    binding_dir = data_dir / "bindings"
    assert len(list(binding_dir.iterdir())) == 1

    code, payload = run_json(["unbind", "--session", "abc123", "--json"], capsys)
    assert code == 0
    assert payload["removed"] is True
    assert list(binding_dir.iterdir()) == []


def test_bind_accepts_opaque_session_strings(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "topic"
    build_draft(root)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    skill = tmp_path / "SKILL.md"
    skill.write_text("stub skill", encoding="utf-8")
    monkeypatch.setenv("DRAFT_DATA", str(data_dir))
    monkeypatch.setenv("DRAFT_SKILL_PATH", str(skill))
    capsys.readouterr()

    for session in ("", "..", "会话/①"):
        code, payload = run_json(["bind", str(root), "--session", session, "--json"], capsys)
        assert code == 0
        assert payload["ok"] is True
    binding_dir = data_dir / "bindings"
    files = list(binding_dir.iterdir())
    assert len(files) == 3
    assert all(path.parent == binding_dir for path in files)


def test_bind_uses_skill_from_unrelated_location(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "topic"
    build_draft(root)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    deep = tmp_path / "unrelated" / "deep" / "depth"
    deep.mkdir(parents=True)
    skill = deep / "SKILL.md"
    skill.write_text("relocated skill", encoding="utf-8")
    monkeypatch.setenv("DRAFT_DATA", str(data_dir))
    monkeypatch.setenv("DRAFT_SKILL_PATH", str(skill))
    capsys.readouterr()

    code, payload = run_json(["bind", str(root), "--session", "layout", "--json"], capsys)
    assert code == 0
    assert payload["ok"] is True


def test_bind_requires_plugin_context(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "topic"
    build_draft(root)
    monkeypatch.delenv("DRAFT_DATA", raising=False)
    capsys.readouterr()

    code, payload = run_json(["bind", str(root), "--session", "abc123", "--json"], capsys)
    assert code == 4
    error = payload["error"]
    assert isinstance(error, dict)
    assert error["code"] == "binding.plugin_context_unavailable"


def _bind_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    root = tmp_path / "topic"
    build_draft(root)
    skill = tmp_path / "SKILL.md"
    skill.write_text("stub skill", encoding="utf-8")
    workspace = (tmp_path / "workspace").resolve()
    (workspace / "relative-data").mkdir(parents=True)
    monkeypatch.chdir(workspace)
    monkeypatch.setenv("DRAFT_SKILL_PATH", str(skill))
    return root, workspace


@pytest.mark.parametrize("value", ["", "   ", ".", "relative-data"])
def test_bind_rejects_invalid_data_dir_without_touching_cwd(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    root, workspace = _bind_workspace(tmp_path, monkeypatch)
    monkeypatch.setenv("DRAFT_DATA", value)
    capsys.readouterr()

    code, payload = run_json(["bind", str(root), "--session", "abc123", "--json"], capsys)

    assert code == 4
    error = payload["error"]
    assert isinstance(error, dict)
    assert error["code"] == "binding.data_dir_unavailable"
    assert not (workspace / "bindings").exists()
    assert list((workspace / "relative-data").iterdir()) == []


def test_bind_rejects_symlink_data_dir(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, workspace = _bind_workspace(tmp_path, monkeypatch)
    real = (tmp_path / "real-data").resolve()
    real.mkdir()
    link = workspace / "linked-data"
    link.symlink_to(real)
    monkeypatch.setenv("DRAFT_DATA", str(link))
    capsys.readouterr()

    code, payload = run_json(["bind", str(root), "--session", "abc123", "--json"], capsys)

    assert code == 4
    error = payload["error"]
    assert isinstance(error, dict)
    assert error["code"] == "binding.data_dir_unavailable"
    assert list(real.iterdir()) == []


def test_unbind_rejects_invalid_data_dir_without_removing_cwd_bindings(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, workspace = _bind_workspace(tmp_path, monkeypatch)
    foreign = workspace / "relative-data" / "bindings" / "keep.json"
    foreign.parent.mkdir(parents=True)
    foreign.write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("DRAFT_DATA", "relative-data")
    capsys.readouterr()

    code, payload = run_json(["unbind", "--session", "abc123", "--json"], capsys)

    assert code == 4
    error = payload["error"]
    assert isinstance(error, dict)
    assert error["code"] == "binding.data_dir_unavailable"
    assert foreign.is_file()


def test_bind_accepts_canonical_absolute_data_dir(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, workspace = _bind_workspace(tmp_path, monkeypatch)
    data_dir = (tmp_path / "data").resolve()
    data_dir.mkdir()
    monkeypatch.setenv("DRAFT_DATA", str(data_dir))
    capsys.readouterr()

    code, payload = run_json(["bind", str(root), "--session", "abc123", "--json"], capsys)

    assert code == 0
    assert payload["ok"] is True
    assert len(list((data_dir / "bindings").iterdir())) == 1
    assert not (workspace / "bindings").exists()


def test_bind_hook_help_exits_zero() -> None:
    for args in (["bind", "--help"], ["unbind", "--help"]):
        with pytest.raises(SystemExit) as captured:
            main(args)
        assert captured.value.code == 0


def test_move_with_relative_root_from_cwd(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "topic"
    build_draft(root)
    monkeypatch.chdir(tmp_path)
    capsys.readouterr()

    code, payload = run_json(["move", "topic", "pages/a.md", "pages/renamed.md", "--json"], capsys)

    assert code == 0
    assert payload["ok"] is True
    assert (root / "pages/renamed.md").is_file()


# ---------------------------------------------------------------------------
# Task 8：检查器四档严重级别、recover 批量信封、结构写前置推进信封。
# ---------------------------------------------------------------------------


def _trashable_cli_root(tmp_path: Path) -> Path:
    root = tmp_path / "topic"
    assert main(["init", str(root)]) == 0
    (root / "pages/a.md").write_text(
        "# A\n\n> 状态：`已确认`\n>\n> 父主题：[根索引](../index.md)\n>\n"
        "> 产出：[结论汇总](../conclusions.md)\n\nA。\n",
        encoding="utf-8",
    )
    (root / "pages/b.md").write_text(
        "# B\n\n> 状态：`待讨论`\n>\n> 父主题：[根索引](../index.md)\n\nB。\n",
        encoding="utf-8",
    )
    index = root / "index.md"
    index.write_text(
        index.read_text(encoding="utf-8") + "- [A](pages/a.md)\n- [B](pages/b.md)\n",
        encoding="utf-8",
    )
    conclusions = root / "conclusions.md"
    conclusions.write_text(
        conclusions.read_text(encoding="utf-8") + "- [A](pages/a.md)：A。\n",
        encoding="utf-8",
    )
    return root


def test_check_json_separates_errors_warnings_infos_and_reviews(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _trashable_cli_root(tmp_path)
    capsys.readouterr()
    # 制造一个 info（trash_retained）与一个 error（已确认页缺产出）。
    run_json(["trash", str(root), "pages/b.md", "--json"], capsys)
    (root / "pages/a.md").write_text(
        "# A\n\n> 状态：`已确认`\n>\n> 父主题：[根索引](../index.md)\n\nA。\n",
        encoding="utf-8",
    )
    capsys.readouterr()

    code, payload = run_json(["check", str(root), "--json"], capsys)

    assert code == 1
    assert payload["ok"] is False
    errors = payload["errors"]
    assert isinstance(errors, list) and errors
    assert isinstance(payload["warnings"], list)
    infos = payload["infos"]
    assert isinstance(infos, list) and infos
    assert isinstance(payload["reviews"], list)


def test_recover_json_preserves_partial_results_on_later_corruption(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _trashable_cli_root(tmp_path)
    capsys.readouterr()
    # 造两个到期包：第一个可推进，第二个破坏证据。
    import json as json_module

    from draft.operations import TrashDeadlines
    from draft.trash import trash_page

    first = trash_page(root, Path("pages/b.md"), now=datetime(2026, 7, 21, tzinfo=UTC))
    # 重建 B 以便第二次回收。
    (root / "pages/b.md").write_text(
        "# B\n\n> 状态：`待讨论`\n>\n> 父主题：[根索引](../index.md)\n\nB。\n",
        encoding="utf-8",
    )
    index = root / "index.md"
    index.write_text(index.read_text(encoding="utf-8") + "- [B](pages/b.md)\n", encoding="utf-8")
    second = trash_page(root, Path("pages/b.md"), now=datetime(2026, 7, 21, tzinfo=UTC))
    # 把两个包都改到已过期、清理期限之前，并破坏排序在后者的证据。
    for op_id in (first.operation_id, second.operation_id):
        path = root / ".draft/operations" / op_id / "journal.json"
        data = json_module.loads(path.read_text(encoding="utf-8"))
        data["metadata"].update(
            TrashDeadlines.from_created_at(datetime(2026, 7, 10, tzinfo=UTC)).to_metadata()
        )
        path.write_text(json_module.dumps(data), encoding="utf-8")
    ordered = sorted([first.operation_id, second.operation_id])
    # 给排序后者加一个指向不存在 R 的恢复认领：到期推进时 require_no_restore_claim_for_expiry 失败。
    broken_path = root / ".draft/operations" / ordered[1] / "journal.json"
    broken_data = json_module.loads(broken_path.read_text(encoding="utf-8"))
    broken_data["metadata"]["restore_operation_id"] = "0" * 32
    broken_path.write_text(json_module.dumps(broken_data), encoding="utf-8")
    capsys.readouterr()

    code, payload = run_json(["recover", str(root), "--json"], capsys)

    assert code == 3
    assert payload["ok"] is False
    assert payload["result_type"] == "recover"
    assert [r["operation_id"] for r in as_dicts(payload["results"])] == [ordered[0]]
    error = payload["error"]
    assert isinstance(error, dict)
    assert error["operation_id"] == ordered[1]
    assert payload["stopped_at_operation_id"] == ordered[1]


def test_structure_preflight_partial_failure_reports_progress_and_never_starts_write(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _trashable_cli_root(tmp_path)
    capsys.readouterr()
    from draft.operations import TrashDeadlines
    from draft.trash import trash_page

    first = trash_page(root, Path("pages/b.md"), now=datetime(2026, 7, 21, tzinfo=UTC))
    (root / "pages/b.md").write_text(
        "# B\n\n> 状态：`待讨论`\n>\n> 父主题：[根索引](../index.md)\n\nB。\n",
        encoding="utf-8",
    )
    index = root / "index.md"
    index.write_text(index.read_text(encoding="utf-8") + "- [B](pages/b.md)\n", encoding="utf-8")
    second = trash_page(root, Path("pages/b.md"), now=datetime(2026, 7, 21, tzinfo=UTC))
    import json as json_module

    for op_id in (first.operation_id, second.operation_id):
        path = root / ".draft/operations" / op_id / "journal.json"
        data = json_module.loads(path.read_text(encoding="utf-8"))
        data["metadata"].update(
            TrashDeadlines.from_created_at(datetime(2026, 7, 10, tzinfo=UTC)).to_metadata()
        )
        path.write_text(json_module.dumps(data), encoding="utf-8")
    ordered = sorted([first.operation_id, second.operation_id])
    broken_path = root / ".draft/operations" / ordered[1] / "journal.json"
    broken_data = json_module.loads(broken_path.read_text(encoding="utf-8"))
    broken_data["metadata"]["restore_operation_id"] = "0" * 32
    broken_path.write_text(json_module.dumps(broken_data), encoding="utf-8")

    accepted: list[str] = []

    def accept(path: str) -> None:
        accepted.append(path)

    monkeypatch.setattr("draft.trash_lifecycle._default_handoff", accept)
    before_a = (root / "pages/a.md").read_bytes()
    capsys.readouterr()

    code, payload = run_json(["move", str(root), "pages/a.md", "pages/a2.md", "--json"], capsys)

    assert code == 3
    assert payload["result_type"] == "error"
    assert [r["operation_id"] for r in as_dicts(payload["lifecycle_results"])] == [ordered[0]]
    error = payload["error"]
    assert isinstance(error, dict)
    assert error["operation_id"] == ordered[1]
    assert payload["stopped_at_operation_id"] == ordered[1]
    # 结构写未开始：源页字节不变，目标不存在。
    assert (root / "pages/a.md").read_bytes() == before_a
    assert not (root / "pages/a2.md").exists()
