import errno
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from draft import __version__
from draft.cli import main
from draft.diagnostic_codes import CheckCode
from draft.models import RootState
from draft.vocabulary import ConclusionsState
from tests.integration.support import (
    DraftFactory,
    build_valid_candidate,
    managed_marker_text,
    snapshot_tree,
)

PAGE_INDEX = """# 根

> 状态：`已确认：根索引`

正文。
"""


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


def assert_human_output_facts(
    output: str,
    facts_by_line: tuple[tuple[object, ...], ...],
) -> list[str]:
    lines = output.splitlines()
    assert len(lines) == len(facts_by_line)
    for line, facts in zip(lines, facts_by_line, strict=True):
        assert line
        for fact in facts:
            assert str(fact) in line
    return lines


def test_init_success_and_not_empty_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "topic"
    code, payload = run_json(["init", str(root), "--json"], capsys)
    assert code == 0
    assert "ok" not in payload
    assert payload["result_type"] == "init"

    code, payload = run_json(["init", str(root), "--json"], capsys)
    assert code == 2
    assert payload["ok"] is False
    assert payload["result_type"] == "error"
    error = payload["error"]
    assert isinstance(error, dict)
    assert error["code"] == "init.not_empty"


def test_init_candidate_writes_exact_marker(tmp_path: Path) -> None:
    root = tmp_path / "candidate"
    assert main(["init", "--candidate", str(root), "--json"]) == 0
    assert json.loads((root / ".draft/draft.json").read_text()) == {
        "format_version": 1,
        "root_state": "candidate",
        "conclusions_state": "not_generated",
    }


def test_init_registered_writes_exact_marker(tmp_path: Path) -> None:
    root = tmp_path / "registered"
    assert main(["init", str(root), "--json"]) == 0
    assert json.loads((root / ".draft/draft.json").read_text()) == {
        "format_version": 1,
        "root_state": "registered",
        "conclusions_state": "not_generated",
    }


def test_adopt_success_and_invalid_candidate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "candidate"
    build_valid_candidate(root)
    code, payload = run_json(["adopt", "--root", str(root), "--json"], capsys)
    assert code == 0
    assert "ok" not in payload

    # 未受管目录（无 marker）在根定位阶段就以 format.missing 拒绝。
    broken = tmp_path / "broken"
    broken.mkdir()
    code, payload = run_json(["adopt", "--root", str(broken), "--json"], capsys)
    assert code == 2
    error = payload["error"]
    assert isinstance(error, dict)
    assert error["code"] == CheckCode.FORMAT_MISSING.value


def test_migrate_subcommand_is_removed(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main(["migrate", str(tmp_path), "--from", "legacy-v0", "--to", str(tmp_path)])


def test_check_candidate_root_scans_unified_protocol(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "candidate"
    build_valid_candidate(root)

    code, payload = run_json(["check", "--root", str(root), "--json"], capsys)

    assert code == 0
    assert payload["ok"] is True


# ---------------------------------------------------------------------------
# round-16 M1：CLI 真实入口（main/check）对表一逐行成立，失败路径树不变。
# 每个非 init 命令的第一个 marker 读取器 select_managed_root 与 snapshot 读取器
# 对同一 .draft 事实给出同一 code/path。
# ---------------------------------------------------------------------------


def _tree_state(root: Path) -> dict[str, object]:
    """递归捕获根树文件 bytes 与符号链接目标，用于失败路径的树不变断言。"""
    state: dict[str, object] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            state[relative] = ("symlink", os.readlink(path))
        elif path.is_file():
            state[relative] = ("file", path.read_bytes().hex())
    return state


def _assert_check_error(
    payload: dict[str, object],
    code: str,
    paths: list[str],
) -> None:
    assert payload["ok"] is False
    assert payload["result_type"] == "error"
    error = payload["error"]
    assert isinstance(error, dict)
    assert error["code"] == code
    assert error["paths"] == paths


def test_check_draft_symlink_fails_without_mutation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """.draft 是 symlink：main 经 select_managed_root 报 path.symlink_escape @ .draft，树不变。

    基线旧分类器报 format.invalid @ marker（红）；修复后与 snapshot 读取器一致。
    """
    root = tmp_path / "candidate"
    build_valid_candidate(root)
    outside = tmp_path / "outside"
    outside.mkdir()
    shutil.rmtree(root / ".draft")
    (root / ".draft").symlink_to(outside, target_is_directory=True)

    before = _tree_state(root)
    code, payload = run_json(["check", "--root", str(root), "--json"], capsys)
    after = _tree_state(root)

    assert code != 0
    _assert_check_error(
        payload,
        CheckCode.PATH_SYMLINK_ESCAPE.value,
        [
            (root.resolve() / ".draft").as_posix(),
        ],
    )
    assert before == after


def test_check_draft_lstat_eio_fails_without_mutation(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """.draft 无法 stat（EIO）：main 报 format.invalid @ marker 而非泄漏 OSError，树不变。

    注入同时命中 os.lstat 与 os.stat（真实 .draft stat 失败同时影响两个 syscall）。
    基线 managed_at 的 Path.is_symlink 泄漏原始 OSError，main 未捕获（红）。
    """
    root = tmp_path / "candidate"
    build_valid_candidate(root)
    marker = root.resolve() / ".draft/draft.json"
    real_lstat = os.lstat
    real_stat = os.stat

    def fail_draft_lstat(
        path: str | bytes | os.PathLike[str], *args: object, **kwargs: object
    ) -> os.stat_result:
        if os.fsdecode(path).endswith("/.draft"):
            raise OSError(errno.EIO, "Input/output error")
        return real_lstat(path, *args, **kwargs)  # type: ignore[return-value]

    def fail_draft_stat(
        path: str | bytes | os.PathLike[str], *args: object, **kwargs: object
    ) -> os.stat_result:
        if os.fsdecode(path).endswith("/.draft"):
            raise OSError(errno.EIO, "Input/output error")
        return real_stat(path, *args, **kwargs)  # type: ignore[return-value]

    before = _tree_state(root)
    with monkeypatch.context() as patched:
        patched.setattr(os, "lstat", fail_draft_lstat)
        patched.setattr(os, "stat", fail_draft_stat)
        code, payload = run_json(["check", "--root", str(root), "--json"], capsys)
    after = _tree_state(root)

    assert code != 0
    _assert_check_error(payload, CheckCode.FORMAT_INVALID.value, [marker.as_posix()])
    assert before == after


def test_check_draft_regular_file_fails_without_mutation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """.draft 是普通文件（非目录）：format.invalid @ marker（保持用例，防回归）。"""
    root = tmp_path / "candidate"
    build_valid_candidate(root)
    shutil.rmtree(root / ".draft")
    (root / ".draft").write_text("not a directory\n", encoding="utf-8")

    before = _tree_state(root)
    code, payload = run_json(["check", "--root", str(root), "--json"], capsys)
    after = _tree_state(root)

    assert code != 0
    _assert_check_error(
        payload,
        CheckCode.FORMAT_INVALID.value,
        [
            (root.resolve() / ".draft/draft.json").as_posix(),
        ],
    )
    assert before == after


def test_check_without_draft_fails_without_mutation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """无 `.draft`：format.missing @ marker（保持用例，防回归）。"""
    root = tmp_path / "candidate"
    build_valid_candidate(root)
    shutil.rmtree(root / ".draft")

    before = _tree_state(root)
    code, payload = run_json(["check", "--root", str(root), "--json"], capsys)
    after = _tree_state(root)

    assert code != 0
    _assert_check_error(
        payload,
        CheckCode.FORMAT_MISSING.value,
        [
            (root.resolve() / ".draft/draft.json").as_posix(),
        ],
    )
    assert before == after


@pytest.mark.parametrize("precreate_internal", [False, True])
def test_candidate_check_and_adopt_accept_both_internal_layouts(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    precreate_internal: bool,
) -> None:
    """候选根按需目录缺失与已存在两类布局，check 与 adopt 都必须正常工作。

    check 检查内部命名空间、adopt 按需创建 operations/trash；目录存在性不得
    改变任一入口的控制流结果（规格 §12.2 与按需内部目录协议）。
    """
    root = tmp_path / "candidate"
    build_valid_candidate(root, precreate_internal=precreate_internal)

    code, payload = run_json(["check", "--root", str(root), "--json"], capsys)
    assert code == 0
    assert payload["ok"] is True

    code, payload = run_json(["adopt", "--root", str(root), "--json"], capsys)
    assert code == 0
    assert "ok" not in payload
    # adopt 按需创建操作目录（最小布局下由 adopt 补建，预建布局下沿用）。
    assert (root / ".draft/operations").is_dir()


def test_check_is_read_only_on_candidate_root(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "candidate"
    build_valid_candidate(root)
    before = snapshot_tree(root)

    code, payload = run_json(["check", "--root", str(root), "--json"], capsys)

    assert code == 0
    assert payload["ok"] is True
    assert snapshot_tree(root) == before


def test_check_reports_retired_provisional_entry_as_unexpected(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """provisional 协议已退役：残留 draft.provisional.json 是未登记内部条目。"""
    root = tmp_path / "candidate"
    build_valid_candidate(root)
    (root / ".draft" / "draft.provisional.json").write_text("{}", encoding="utf-8")
    before = snapshot_tree(root)

    code, payload = run_json(["check", "--root", str(root), "--json"], capsys)

    assert code == 1
    assert payload["ok"] is False
    assert [issue["code"] for issue in as_dicts(payload["errors"])] == [
        CheckCode.INTERNAL_UNEXPECTED_ENTRY.value
    ]
    assert snapshot_tree(root) == before


def test_check_reports_unexpected_internal_entry(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "candidate"
    build_valid_candidate(root)
    (root / ".draft" / "junk.json").write_text("x", encoding="utf-8")

    code, payload = run_json(["check", "--root", str(root), "--json"], capsys)

    assert code == 1
    assert payload["ok"] is False
    assert [issue["code"] for issue in as_dicts(payload["errors"])] == [
        CheckCode.INTERNAL_UNEXPECTED_ENTRY.value
    ]


def test_check_clean_and_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "topic"
    assert main(["init", str(root)]) == 0
    capsys.readouterr()

    code, payload = run_json(["check", "--root", str(root), "--json"], capsys)
    assert code == 0
    assert payload["ok"] is True
    assert payload["errors"] == []

    question = root / "pages/question.md"
    question.write_text(
        "# 问题\n\n> 状态：`已确认`\n\n无父主题声明。\n",
        encoding="utf-8",
    )
    code, payload = run_json(["check", "--root", str(root), "--json"], capsys)
    assert code == 1
    assert payload["ok"] is False
    error_dicts = as_dicts(payload["errors"])
    codes = {str(issue["code"]) for issue in error_dicts}
    assert CheckCode.PARENT_MISSING.value in codes
    first = error_dicts[0]
    assert "path" in first
    assert "line" in first
    assert "details" in first
    for issue in error_dicts:
        assert issue["severity"] == "error"
        assert set(issue) == {"code", "severity", "path", "line", "details"}


def test_check_human_clean_prints_one_nonempty_line(
    registered_draft: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["check", "--root", str(registered_draft)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    assert lines[0]


def test_check_human_is_read_only(
    registered_draft: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = snapshot_tree(registered_draft)
    main(["check", "--root", str(registered_draft)])
    assert snapshot_tree(registered_draft) == before


def test_check_human_reports_actionable_multiple_locations(
    registered_draft: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing_a = "pages/missing-a.md"
    missing_b = "pages/missing-b.md"
    (registered_draft / "pages/breaks.md").write_text(
        "# 断裂\n\n> 状态：`待讨论`\n>\n> 父主题：[根索引](../index.md)\n\n"
        f"[甲]({missing_a})\n[乙]({missing_b})\n",
        encoding="utf-8",
    )
    index = registered_draft / "index.md"
    index.write_text(
        index.read_text(encoding="utf-8") + "- [断裂](pages/breaks.md)\n",
        encoding="utf-8",
    )

    assert main(["check", "--root", str(registered_draft)]) == 1
    assert_human_output_facts(
        capsys.readouterr().out,
        (
            ("pages/breaks.md", 7, missing_a),
            ("pages/breaks.md", 8, missing_b),
            (2, 0),
        ),
    )


def test_check_plain_and_json_agree_on_exit_code_and_raw_details(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "topic"
    assert main(["init", str(root)]) == 0
    literal_target = "missing.md"
    normalized_target = "pages/missing.md"
    (root / "pages/question.md").write_text(
        "# 问题\n\n> 状态：`待讨论`\n>\n> 父主题：[根索引](../index.md)\n\n"
        f"见 [不存在]({literal_target})。\n",
        encoding="utf-8",
    )
    index = root / "index.md"
    index.write_text(
        index.read_text(encoding="utf-8") + "- [问题](pages/question.md)\n", encoding="utf-8"
    )
    capsys.readouterr()

    assert main(["check", "--root", str(root)]) == 1
    assert_human_output_facts(
        capsys.readouterr().out,
        (
            (literal_target, normalized_target),
            (),
        ),
    )

    code, payload = run_json(["check", "--root", str(root), "--json"], capsys)
    assert code == 1
    link_issues = [
        issue
        for issue in as_dicts(payload["errors"])
        if issue["code"] == CheckCode.LINK_MISSING.value
    ]
    assert link_issues
    assert link_issues[0]["details"] == [literal_target, normalized_target]
    assert link_issues[0]["severity"] == "error"


def test_json_flag_both_positions_identical(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "topic"
    assert main(["init", str(root)]) == 0
    capsys.readouterr()

    code_a, payload_a = run_json(["--json", "check", "--root", str(root)], capsys)
    code_b, payload_b = run_json(["check", "--root", str(root), "--json"], capsys)
    assert code_a == code_b == 0
    assert payload_a == payload_b


def test_check_rejects_unknown_code_before_scanning(tmp_path: Path) -> None:
    """未知检查码由生产 CheckCode choices 在扫描前以退出码 2 拒绝。"""
    root = tmp_path / "topic"
    assert main(["init", str(root)]) == 0

    with pytest.raises(SystemExit) as caught:
        main(["check", "--root", str(root), "--code", "no.such.code", "--json"])
    assert caught.value.code == 2


def test_check_code_filter_and_count_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "topic"
    assert main(["init", str(root)]) == 0
    (root / "pages/a.md").write_text("# A\n\n> 状态：`已确认`\n\nA。\n", encoding="utf-8")
    capsys.readouterr()

    code, payload = run_json(
        ["check", "--root", str(root), "--code", CheckCode.PARENT_MISSING.value, "--json"],
        capsys,
    )
    assert code == 1
    assert payload["ok"] is False
    assert payload["filtered_total"] == 1
    assert payload["returned_count"] == 1
    assert [issue["code"] for issue in as_dicts(payload["errors"])] == [
        CheckCode.PARENT_MISSING.value
    ]

    code, payload = run_json(["check", "--root", str(root), "--count-only", "--json"], capsys)
    assert code == 1
    assert payload["errors"] == []
    # count-only 保留全部计数与分页事实：未过滤完整扫描共 2 个错误。
    assert payload["filtered_total"] == 2
    assert payload["severity_counts"] == {"error": 2, "warning": 0, "info": 0, "review": 0}


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
        ["move", "--root", str(root), "pages/a.md", "pages/renamed.md", "--json"], capsys
    )
    assert code == 0
    assert "ok" not in payload
    assert (root / "pages/renamed.md").is_file()

    code, payload = run_json(
        ["move", "--root", str(root), "pages/renamed.md", "../outside.md", "--json"], capsys
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

    code, payload = run_json(["promote", "--root", str(root), "pages/a.md", "--json"], capsys)
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
        ["promote", "--root", str(root), "pages/a.md", "pages/child.md", "--json"], capsys
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

    code, payload = run_json(
        ["collapse", "--root", str(root), "pages/empty/index.md", "--json"], capsys
    )
    assert code == 0
    assert (root / "pages/empty.md").is_file()

    code, payload = run_json(["collapse", "--root", str(root), "pages/a.md", "--json"], capsys)
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

    code, payload = run_json(["trash", "--root", str(root), "index.md", "--json"], capsys)
    assert code == 2
    error = payload["error"]
    assert isinstance(error, dict)
    assert error["code"] == "trash.protected"

    code, payload = run_json(["trash", "--root", str(root), "pages/a.md", "--json"], capsys)
    assert code == 0
    assert "ok" not in payload
    assert payload["operation_id"]
    # 公开 JSON 只携带 operation_id、期限与公开变化路径，不暴露内部包。
    assert payload["restore_expires_at"] is not None
    assert "phase" not in payload
    assert "package" not in payload
    assert "pages/a.md" in cast("list[str]", payload["changed_paths"])
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
    _, trash_payload = run_json(["trash", "--root", str(root), "pages/a.md", "--json"], capsys)
    operation_id = trash_payload["operation_id"]
    assert isinstance(operation_id, str)

    code, payload = run_json(["restore", "--root", str(root), operation_id, "--json"], capsys)

    assert code == 0
    assert "ok" not in payload
    assert payload["result_type"] == "restore"
    # 恢复本身是一次新操作，携带自己的 operation_id，而非原 trash 的 id。
    assert isinstance(payload["operation_id"], str) and payload["operation_id"]
    assert "phase" not in payload
    assert (root / "pages/a.md").is_file()


def test_restore_cli_rolled_back_is_exit_three(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "topic"
    build_draft(root)
    capsys.readouterr()
    _, trash_payload = run_json(["trash", "--root", str(root), "pages/a.md", "--json"], capsys)
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
    code, payload = run_json(["restore", "--root", str(root), operation_id, "--json"], capsys)
    monkeypatch.undo()

    assert code == 3
    assert payload["ok"] is False
    assert payload["result_type"] == "restore"
    # 回滚路径同样携带本次恢复操作的 operation_id，不暴露内部 phase。
    assert isinstance(payload["operation_id"], str) and payload["operation_id"]
    assert "phase" not in payload
    assert (root / ".draft/trash/outgoing" / operation_id).is_dir()


def test_restore_cli_expired_is_exit_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "topic"
    build_draft(root)
    capsys.readouterr()
    _, trash_payload = run_json(["trash", "--root", str(root), "pages/a.md", "--json"], capsys)
    operation_id = trash_payload["operation_id"]
    assert isinstance(operation_id, str)
    # 模拟到期推进已把 T 转为 trash_handoff_pending。
    _rewrite_journal_phase(root, operation_id, "trash_handoff_pending")

    code, payload = run_json(["restore", "--root", str(root), operation_id, "--json"], capsys)

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
    _, trash_payload = run_json(["trash", "--root", str(root), "pages/a.md", "--json"], capsys)
    operation_id = trash_payload["operation_id"]
    assert isinstance(operation_id, str)
    # 原路径已被外部重建：恢复在认领后发现冲突。
    (root / "pages/a.md").write_text("# 重建\n", encoding="utf-8")

    code, payload = run_json(["restore", "--root", str(root), operation_id, "--json"], capsys)

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

    code, payload = run_json(["restore", "--root", str(root), "f" * 32, "--json"], capsys)

    assert code == 3
    assert payload["ok"] is False
    assert payload["result_type"] == "error"
    error = payload["error"]
    assert isinstance(error, dict)
    assert error["code"] == CheckCode.OPERATION_JOURNAL_CORRUPT.value


def test_recover_nothing_to_do(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "topic"
    assert main(["init", str(root)]) == 0
    capsys.readouterr()

    code, payload = run_json(["recover", "--root", str(root), "--json"], capsys)
    assert code == 0
    assert "ok" not in payload


def test_missing_root_is_exit_two(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from draft.diagnostic_codes import RootCode

    code, payload = run_json(["check", "--root", str(tmp_path / "nope"), "--json"], capsys)
    assert code == 2
    error = payload["error"]
    assert isinstance(error, dict)
    assert error["code"] == RootCode.NOT_FOUND.value
    assert error["details"] == ["cli"]


def test_unresolvable_home_maps_to_root_missing_json(
    unresolvable_home: None, capsys: pytest.CaptureFixture[str]
) -> None:
    for args in (["check", "--root", "~/topic", "--json"], ["init", "~/topic", "--json"]):
        code, payload = run_json(args, capsys)
        assert code == 2
        assert payload["ok"] is False
        assert payload["result_type"] == "error"
        error = payload["error"]
        assert isinstance(error, dict)
        assert error["code"] == "root.missing"
        assert error["paths"] == ["~/topic"]


def test_unresolvable_home_maps_to_root_missing_text(
    unresolvable_home: None, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["check", "--root", "~/topic"]) == 2
    output = capsys.readouterr().out
    assert "root.missing" in output


def test_unresolvable_home_page_operand_maps_to_root_missing(
    unresolvable_home: None, capsys: pytest.CaptureFixture[str]
) -> None:
    # move/promote/collapse/trash resolve page operands against the raw root
    # before resolve_draft_root runs, so the page-operand expansion must also
    # normalize the failure instead of leaking a RuntimeError traceback.
    code, payload = run_json(["move", "--root", "~/topic", "a.md", "b.md", "--json"], capsys)
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


def test_core_help_excludes_plugin_commands(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        main(["--help"])
    assert caught.value.code == 0
    help_text = capsys.readouterr().out
    for token in ("bind", "unbind", "hook", "session-start", "plugin-context"):
        assert token not in help_text


@pytest.mark.parametrize("command", ["bind", "unbind", "hook", "_session-start"])
def test_removed_plugin_commands_are_usage_errors(command: str) -> None:
    with pytest.raises(SystemExit) as caught:
        main([command])
    assert caught.value.code == 2


def test_move_with_relative_root_from_cwd(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "topic"
    build_draft(root)
    monkeypatch.chdir(tmp_path)
    capsys.readouterr()

    code, payload = run_json(
        ["move", "--root", "topic", "pages/a.md", "pages/renamed.md", "--json"], capsys
    )

    assert code == 0
    assert "ok" not in payload
    assert (root / "pages/renamed.md").is_file()


# ---------------------------------------------------------------------------
# Task 9：move 命令面 SOURCE... TARGET 双类型形态与分派。
# ---------------------------------------------------------------------------


def test_move_shape_requires_source_and_target(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = draft_factory({"index.md": PAGE_INDEX}, root_state=RootState.CANDIDATE)

    code, payload = run_json(
        ["move", "pages/a.md", "--root", str(root), "--json"], capsys
    )

    assert code == 2
    error = payload["error"]
    assert isinstance(error, dict)
    assert error["code"] == "cli.move_operands_invalid"


def test_move_file_target_rejects_multiple_sources(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = draft_factory({"index.md": PAGE_INDEX}, root_state=RootState.CANDIDATE)
    (root / "a.md").write_text("甲。\n", encoding="utf-8")
    (root / "b.md").write_text("乙。\n", encoding="utf-8")

    code, payload = run_json(
        ["move", "a.md", "b.md", "pages/c.md", "--root", str(root), "--json"], capsys
    )

    assert code == 2
    error = payload["error"]
    assert isinstance(error, dict)
    assert error["code"] == "cli.move_operands_invalid"


def test_move_file_target_rejects_directory_source(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = draft_factory({"index.md": PAGE_INDEX}, root_state=RootState.CANDIDATE)
    legacy = root / "legacy"
    legacy.mkdir()
    (legacy / "index.md").write_text("旧稿。\n", encoding="utf-8")

    code, payload = run_json(
        ["move", "legacy", "pages/x.md", "--root", str(root), "--json"], capsys
    )

    assert code == 2
    error = payload["error"]
    assert isinstance(error, dict)
    assert error["code"] == "cli.move_operands_invalid"
    assert (legacy / "index.md").exists()      # 拒绝零写入
    assert not (root / "pages/x.md").exists()


def test_move_prefix_target_promotes_unmanaged_file(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = draft_factory({"index.md": PAGE_INDEX}, root_state=RootState.CANDIDATE)
    (root / "stray.md").write_text("正文。\n", encoding="utf-8")

    code, payload = run_json(
        ["move", "stray.md", "pages", "--root", str(root), "--json"], capsys
    )

    assert code == 0
    assert payload["result_type"] == "move"
    assert (root / "pages/stray.md").is_file()
    assert not (root / "stray.md").exists()


def test_move_dry_run_reports_effects_without_writing(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = draft_factory({"index.md": PAGE_INDEX}, root_state=RootState.CANDIDATE)
    (root / "stray.md").write_text("正文。\n", encoding="utf-8")

    code, _payload = run_json(
        ["move", "stray.md", "pages", "--root", str(root), "--dry-run", "--json"],
        capsys,
    )

    assert code == 0
    assert (root / "stray.md").is_file()            # 预演零写入
    assert not (root / "pages/stray.md").exists()

    code, _payload = run_json(
        ["move", "stray.md", "pages", "--root", str(root), "--json"], capsys
    )
    assert code == 0
    assert (root / "pages/stray.md").is_file()      # 正式执行后就位


# ---------------------------------------------------------------------------
# Task 8：检查器四档严重级别、recover 批量信封、结构写前置推进信封。
# ---------------------------------------------------------------------------


def _trashable_cli_root(tmp_path: Path) -> Path:
    root = tmp_path / "topic"
    assert main(["init", str(root)]) == 0
    (root / "pages/a.md").write_text(
        "# A\n\n> 状态：`已确认`\n>\n> 父主题：[根索引](../index.md)\n\nA。\n",
        encoding="utf-8",
    )
    (root / "pages/b.md").write_text(
        "# B\n\n> 状态：`待讨论`\n>\n> 父主题：[根索引](../index.md)\n\nB。\n",
        encoding="utf-8",
    )
    index = root / "index.md"
    index.write_text(
        index.read_text(encoding="utf-8")
        + "- [A](pages/a.md)\n- [B](pages/b.md)\n- [结论汇总](conclusions.md)\n",
        encoding="utf-8",
    )
    conclusions = root / "conclusions.md"
    conclusions.write_text(
        "# 结论汇总\n\n> 状态：`已确认`\n>\n> 父主题：[根索引](index.md)\n\n"
        "## 已确认结论\n\n- [A](pages/a.md)\n",
        encoding="utf-8",
    )
    # conclusions.md 是机器所有权下的固定产物：所有权状态必须为 generated（规格 §12.1）。
    (root / ".draft/draft.json").write_text(
        managed_marker_text(conclusions_state=ConclusionsState.GENERATED), encoding="utf-8"
    )
    return root


def test_check_json_separates_errors_warnings_infos_and_reviews(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _trashable_cli_root(tmp_path)
    capsys.readouterr()
    # 制造一个 info（trash_retained）与一个 error（a.md 缺少父主题声明）。
    run_json(["trash", "--root", str(root), "pages/b.md", "--json"], capsys)
    (root / "pages/a.md").write_text(
        "# A\n\n> 状态：`已确认`\n\nA。\n",
        encoding="utf-8",
    )
    capsys.readouterr()

    code, payload = run_json(["check", "--root", str(root), "--json"], capsys)

    assert code == 1
    assert payload["ok"] is False
    errors = payload["errors"]
    assert isinstance(errors, list) and errors
    assert isinstance(payload["warnings"], list)
    infos = payload["infos"]
    assert isinstance(infos, list) and infos
    assert isinstance(payload["reviews"], list)
    for severity, key in (
        ("error", "errors"),
        ("warning", "warnings"),
        ("info", "infos"),
        ("review", "reviews"),
    ):
        for issue in as_dicts(payload[key]):
            assert issue["severity"] == severity
            assert set(issue) == {"code", "severity", "path", "line", "details"}


def test_recover_preflight_corruption_is_standard_error_envelope(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _trashable_cli_root(tmp_path)
    capsys.readouterr()
    # 造两个到期包：第二个破坏证据，使前置推进整体失败。
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
    index.write_text(
        index.read_text(encoding="utf-8").replace(
            "- [结论汇总](conclusions.md)\n", "- [B](pages/b.md)\n- [结论汇总](conclusions.md)\n"
        ),
        encoding="utf-8",
    )
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

    code, payload = run_json(["recover", "--root", str(root), "--json"], capsys)

    # 前置推进损坏：recover 不进入自身逻辑，只返回标准错误信封，退出码 3。
    assert code == 3
    assert payload["ok"] is False
    assert payload["result_type"] == "error"
    error = payload["error"]
    assert isinstance(error, dict)
    assert error["code"] == CheckCode.OPERATION_INCONSISTENT_STATE.value
    assert "results" not in payload
    assert "stopped_at_operation_id" not in payload


def test_structure_preflight_corruption_never_starts_write(
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
    index.write_text(
        index.read_text(encoding="utf-8").replace(
            "- [结论汇总](conclusions.md)\n", "- [B](pages/b.md)\n- [结论汇总](conclusions.md)\n"
        ),
        encoding="utf-8",
    )
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

    code, payload = run_json(
        ["move", "--root", str(root), "pages/a.md", "pages/a2.md", "--json"], capsys
    )

    # 前置推进损坏：只返回标准错误信封，不暴露推进明细，退出码 3。
    assert code == 3
    assert payload["result_type"] == "error"
    assert payload["ok"] is False
    error = payload["error"]
    assert isinstance(error, dict)
    assert error["code"] == CheckCode.OPERATION_INCONSISTENT_STATE.value
    assert "lifecycle_results" not in payload
    assert "stopped_at_operation_id" not in payload
    # 结构写未开始：源页字节不变，目标不存在。
    assert (root / "pages/a.md").read_bytes() == before_a
    assert not (root / "pages/a2.md").exists()


# ---------------------------------------------------------------------------
# Task 4：统一静默前置推进——每个已登记命令恰好推进一次
# ---------------------------------------------------------------------------


def _due_package_root(tmp_path: Path) -> Path:
    root = _trashable_cli_root(tmp_path)
    import json as json_module

    from draft.operations import TrashDeadlines
    from draft.trash import trash_page

    pending = trash_page(root, Path("pages/b.md"), now=datetime(2026, 7, 21, tzinfo=UTC))
    path = root / ".draft/operations" / pending.operation_id / "journal.json"
    data = json_module.loads(path.read_text(encoding="utf-8"))
    data["metadata"].update(
        TrashDeadlines.from_created_at(datetime(2026, 7, 10, tzinfo=UTC)).to_metadata()
    )
    path.write_text(json_module.dumps(data), encoding="utf-8")
    return root


def test_check_is_read_only_and_does_not_advance_due_package(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """check 严格只读：已到期包只作为持久化事实报告，不触发回收生命周期推进。"""
    root = _due_package_root(tmp_path)
    calls: list[str] = []
    monkeypatch.setattr("draft.trash_lifecycle._default_handoff", calls.append)
    capsys.readouterr()

    code, payload = run_json(["check", "--root", str(root), "--json"], capsys)

    assert calls == []
    assert code == 0
    assert payload["ok"] is True
    assert "lifecycle_results" not in payload


def test_move_advances_due_package_once(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _due_package_root(tmp_path)
    capsys.readouterr()
    calls: list[str] = []
    monkeypatch.setattr("draft.trash_lifecycle._default_handoff", calls.append)

    code, _payload = run_json(
        ["move", "--root", str(root), "pages/a.md", "pages/a2.md", "--json"], capsys
    )

    assert len(calls) == 1
    assert code == 0


def test_restore_advances_due_package_once(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # 一个已到期包（会触发推进）与一个保留窗口内的包（用于 restore）。
    root = _trashable_cli_root(tmp_path)
    import json as json_module

    from draft.operations import TrashDeadlines
    from draft.trash import trash_page

    due = trash_page(root, Path("pages/b.md"), now=datetime(2026, 7, 21, tzinfo=UTC))
    due_path = root / ".draft/operations" / due.operation_id / "journal.json"
    due_data = json_module.loads(due_path.read_text(encoding="utf-8"))
    due_data["metadata"].update(
        TrashDeadlines.from_created_at(datetime(2026, 7, 10, tzinfo=UTC)).to_metadata()
    )
    due_path.write_text(json_module.dumps(due_data), encoding="utf-8")
    # 重建 b.md 再回收一次，得到保留窗口内的第二个包。
    (root / "pages/b.md").write_text(
        "# B\n\n> 状态：`待讨论`\n>\n> 父主题：[根索引](../index.md)\n\nB。\n",
        encoding="utf-8",
    )
    index = root / "index.md"
    index.write_text(
        index.read_text(encoding="utf-8").replace(
            "- [结论汇总](conclusions.md)\n",
            "- [B](pages/b.md)\n- [结论汇总](conclusions.md)\n",
        ),
        encoding="utf-8",
    )
    retained = trash_page(root, Path("pages/b.md"), now=datetime(2026, 8, 20, tzinfo=UTC))
    capsys.readouterr()
    calls: list[str] = []
    monkeypatch.setattr("draft.trash_lifecycle._default_handoff", calls.append)

    code, _payload = run_json(
        ["restore", "--root", str(root), retained.operation_id, "--json"], capsys
    )

    assert len(calls) == 1
    assert code == 0


def test_init_and_check_do_not_preflight(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr("draft.trash_lifecycle._default_handoff", calls.append)
    root = tmp_path / "candidate"
    build_valid_candidate(root)
    capsys.readouterr()

    code, _payload = run_json(["check", "--root", str(root), "--json"], capsys)

    assert code == 0
    assert calls == []


def test_check_does_not_preflight_corrupted_journal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """check 不执行前置推进：损坏的恢复关联只作为回收事实原样报告，不产生推进错误信封。"""
    root = _trashable_cli_root(tmp_path)
    capsys.readouterr()
    import json as json_module

    from draft.operations import TrashDeadlines
    from draft.trash import trash_page

    pending = trash_page(root, Path("pages/b.md"), now=datetime(2026, 7, 21, tzinfo=UTC))
    path = root / ".draft/operations" / pending.operation_id / "journal.json"
    data = json_module.loads(path.read_text(encoding="utf-8"))
    data["metadata"].update(
        TrashDeadlines.from_created_at(datetime(2026, 7, 10, tzinfo=UTC)).to_metadata()
    )
    path.write_text(json_module.dumps(data), encoding="utf-8")
    broken = json_module.loads(path.read_text(encoding="utf-8"))
    broken["metadata"]["restore_operation_id"] = "0" * 32
    path.write_text(json_module.dumps(broken), encoding="utf-8")
    capsys.readouterr()

    code, payload = run_json(["check", "--root", str(root), "--json"], capsys)

    assert code == 0
    assert payload["ok"] is True
    assert "lifecycle_results" not in payload


# ---------------------------------------------------------------------------
# Task 6：dry-run 与公开 PlanEffect
# ---------------------------------------------------------------------------


def test_dry_run_move_does_not_write_and_matches_formal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "topic"
    build_draft(root)
    capsys.readouterr()
    before = snapshot_tree(root)

    code, payload = run_json(
        ["move", "--root", str(root), "pages/a.md", "pages/renamed.md", "--dry-run", "--json"],
        capsys,
    )

    assert code == 0
    assert payload["result_type"] == "plan"
    assert payload["operation"] == "move"
    effects = cast("list[dict[str, object]]", payload["effects"])
    actions = [(e.get("action"), e.get("path")) for e in effects]
    assert ("modify", "index.md") in actions
    assert "changed_paths" not in payload
    # 未执行写入：源页仍在，目标不存在。
    assert snapshot_tree(root) == before
    assert (root / "pages/a.md").is_file()
    assert not (root / "pages/renamed.md").exists()

    # 正式执行后 changed_paths 与预演效果一致。
    code, formal = run_json(
        ["move", "--root", str(root), "pages/a.md", "pages/renamed.md", "--json"], capsys
    )
    assert code == 0
    assert formal["result_type"] == "move"
    formal_paths = set(cast("list[str]", formal["changed_paths"]))
    assert "pages/a.md" in formal_paths
    assert "pages/renamed.md" in formal_paths
    assert "index.md" in formal_paths


def test_dry_run_count_only_hides_effects_keeps_metadata(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "topic"
    build_draft(root)
    capsys.readouterr()

    code, normal = run_json(
        ["move", "--root", str(root), "pages/a.md", "pages/b.md", "--dry-run", "--json"], capsys
    )
    assert code == 0
    _code, counted = run_json(
        [
            "move",
            "--root",
            str(root),
            "pages/a.md",
            "pages/b.md",
            "--dry-run",
            "--count-only",
            "--json",
        ],
        capsys,
    )

    assert counted["effects"] == []
    assert counted["total"] == normal["total"]
    assert counted["offset"] == normal["offset"]
    assert counted["limit"] == normal["limit"]
    assert counted["has_more"] == normal["has_more"]
    assert counted["next_offset"] == normal["next_offset"]


def test_formal_execution_rejects_pagination_options(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "topic"
    build_draft(root)
    capsys.readouterr()

    code, payload = run_json(
        ["move", "--root", str(root), "pages/a.md", "pages/b.md", "--limit", "10", "--json"], capsys
    )

    assert code == 2
    error = payload["error"]
    assert isinstance(error, dict)
    assert error["code"] == "cli.pagination_requires_dry_run"
    assert (root / "pages/a.md").is_file()
    assert not (root / "pages/b.md").exists()


def test_dry_run_trash_paths_never_include_dot_draft(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "topic"
    build_draft(root)
    capsys.readouterr()

    code, payload = run_json(
        ["trash", "--root", str(root), "pages/a.md", "--dry-run", "--json"], capsys
    )

    assert code == 0
    assert payload["operation"] == "trash"
    effects = cast("list[dict[str, object]]", payload["effects"])
    changed = {
        cast("str", p)
        for e in effects
        for p in (e.get("path"), e.get("from"), e.get("to"))
        if p is not None
    }
    assert all(not path.startswith(".draft/") for path in changed)
    assert (root / "pages/a.md").is_file()
    # 正式回收也不暴露 .draft 路径。
    code, formal = run_json(["trash", "--root", str(root), "pages/a.md", "--json"], capsys)
    assert code == 0
    formal_changed = cast("list[str]", formal["changed_paths"])
    assert all(not path.startswith(".draft/") for path in formal_changed)


def test_dry_run_with_existing_lock_fails_recovery_required(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "topic"
    build_draft(root)
    from draft.operations import acquire_lock

    acquire_lock(root, "a" * 32)
    capsys.readouterr()

    code, payload = run_json(
        ["move", "--root", str(root), "pages/a.md", "pages/b.md", "--dry-run", "--json"], capsys
    )

    assert code == 3
    assert payload["result_type"] == "error"
    error = payload["error"]
    assert isinstance(error, dict)
    assert error["code"] == CheckCode.OPERATION_RECOVERY_REQUIRED.value
    # 未产生业务写入。
    assert (root / "pages/a.md").is_file()
    assert not (root / "pages/b.md").exists()
