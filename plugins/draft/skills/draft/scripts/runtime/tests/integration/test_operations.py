import json
from pathlib import Path

import pytest

from draft.diagnostic_codes import CheckCode
from draft.errors import DraftError
from draft.models import FileMutation, MutationKind, OperationPlan
from draft.operations import (
    JOURNAL_VERSION,
    execute_plan,
    preallocate_journal,
    read_journal,
    recover,
    sha256_bytes,
)
from tests.integration.support import write_raw_journal


@pytest.fixture
def operation_draft(tmp_path: Path) -> Path:
    root = tmp_path / "topic"
    files = {
        "index.md": """# Topic

> 状态：`已关闭：纯导航页，不表达独立结论`

- [结论汇总](conclusions.md)
- [A](pages/a.md)
- [B](pages/b.md)
""",
        "conclusions.md": """# 结论汇总

> 状态：`已确认`
>
> 父主题：[根索引](index.md)

- [A](pages/a.md)：A。
- [B](pages/b.md)：B。
""",
        "pages/a.md": """# A

> 状态：`已确认`
>
> 父主题：[根索引](../index.md)

A。
""",
        "pages/b.md": """# B

> 状态：`已确认`
>
> 父主题：[根索引](../index.md)

B。
""",
        ".draft/draft.json": '{"format_version":1,"conclusions_state":"not_generated"}\n',
    }
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    (root / ".draft/operations").mkdir(parents=True)
    (root / ".draft/trash/outgoing").mkdir(parents=True)
    return root


def two_write_plan(root: Path) -> tuple[OperationPlan, bytes, bytes, bytes, bytes]:
    old_a = (root / "pages/a.md").read_bytes()
    old_b = (root / "pages/b.md").read_bytes()
    new_a = old_a + b"\nforward-a\n"
    new_b = old_b + b"\nforward-b\n"
    plan = OperationPlan(
        kind="test",
        root=root,
        validation_mode="registered",
        mutations=(
            FileMutation(
                Path("pages/a.md"),
                MutationKind.WRITE,
                sha256_bytes(old_a),
                sha256_bytes(new_a),
                new_a,
            ),
            FileMutation(
                Path("pages/b.md"),
                MutationKind.WRITE,
                sha256_bytes(old_b),
                sha256_bytes(new_b),
                new_b,
            ),
        ),
    )
    return plan, old_a, old_b, new_a, new_b


def crash_on_nth_replace(nth: int) -> object:
    calls = 0

    def crash(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == nth:
            raise RuntimeError("injected crash")
        source.replace(target)

    return crash


def write_journal(root: Path, operation_id: str, phase: str) -> Path:
    journal_dir = root / ".draft/operations" / operation_id
    journal_dir.mkdir(parents=True, exist_ok=True)
    journal: dict[str, object] = {
        "journal_version": JOURNAL_VERSION,
        "operation_id": operation_id,
        "kind": "test",
        "phase": phase,
        "validation_mode": "registered",
        "mutations": [],
        "remove_empty_directories": [],
        "metadata": {},
    }
    (journal_dir / "journal.json").write_text(json.dumps(journal), encoding="utf-8")
    return journal_dir


def write_trash_journal(
    root: Path, operation_id: str, *, phase: str, metadata: dict[str, str]
) -> Path:
    """写入当前版本的回收（trash）操作日志，用于恢复关联测试。"""
    journal_dir = root / ".draft/operations" / operation_id
    journal_dir.mkdir(parents=True, exist_ok=True)
    journal: dict[str, object] = {
        "journal_version": JOURNAL_VERSION,
        "operation_id": operation_id,
        "kind": "trash",
        "phase": phase,
        "validation_mode": "registered",
        "mutations": [],
        "remove_empty_directories": [],
        "metadata": metadata,
    }
    (journal_dir / "journal.json").write_text(json.dumps(journal), encoding="utf-8")
    return journal_dir


def test_version_one_journal_is_explicitly_unsupported(operation_draft: Path) -> None:
    path = write_raw_journal(operation_draft, version=1)
    with pytest.raises(DraftError, match="operation.journal_version_unsupported"):
        read_journal(path)


@pytest.mark.parametrize("version", [3, "2", None])
def test_unknown_or_malformed_journal_version_is_rejected(
    operation_draft: Path, version: object
) -> None:
    path = write_raw_journal(operation_draft, version=version)
    with pytest.raises(DraftError, match="operation.journal_version_unsupported"):
        read_journal(path)


def test_journal_operation_id_must_match_directory(operation_draft: Path) -> None:
    root = operation_draft
    directory_id = "1" * 32
    declared_id = "2" * 32
    journal = write_journal(root, directory_id, "completed")
    data = json.loads((journal / "journal.json").read_text(encoding="utf-8"))
    data["operation_id"] = declared_id
    (journal / "journal.json").write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        read_journal(journal / "journal.json")


def test_preallocated_restore_requires_reverse_reference(
    operation_draft: Path,
) -> None:
    root = operation_draft
    restore_id = "3" * 32
    with pytest.raises(DraftError, match="operation.plan_invalid"):
        preallocate_journal(
            root,
            kind="restore",
            operation_id=restore_id,
            validation_mode="registered",
            metadata={},
        )

    assert not (root / ".draft" / "operations" / restore_id).exists()


@pytest.mark.parametrize("bad_reference", ["not-hex", "G" * 32, "a" * 31, "../x"])
def test_preallocated_restore_rejects_invalid_back_reference(
    operation_draft: Path, bad_reference: str
) -> None:
    root = operation_draft
    restore_id = "7" * 32
    with pytest.raises(DraftError, match="operation.plan_invalid"):
        preallocate_journal(
            root,
            kind="restore",
            operation_id=restore_id,
            validation_mode="registered",
            metadata={"restores_trash_operation": bad_reference},
        )

    assert not (root / ".draft" / "operations" / restore_id).exists()


def test_preallocated_restore_requires_forward_reference_before_execution(
    operation_draft: Path,
) -> None:
    root = operation_draft
    trash_id = "4" * 32
    restore_id = "5" * 32
    write_trash_journal(root, trash_id, phase="trash_handoff_pending", metadata={})
    preallocate_journal(
        root,
        kind="restore",
        operation_id=restore_id,
        validation_mode="registered",
        metadata={"restores_trash_operation": trash_id},
    )
    plan = OperationPlan(
        kind="restore",
        root=root,
        mutations=(),
        metadata={"operation_id": restore_id},
    )

    with pytest.raises(DraftError, match="operation.plan_invalid"):
        execute_plan(plan)


def test_preallocate_never_reuses_existing_operation_directory(
    operation_draft: Path,
) -> None:
    root = operation_draft
    operation_id = "6" * 32
    directory = root / ".draft" / "operations" / operation_id
    directory.mkdir()
    sentinel = directory / "sentinel.txt"
    sentinel.write_text("do not touch", encoding="utf-8")
    before = {
        path.relative_to(directory): path.read_bytes()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }

    with pytest.raises(DraftError, match="operation.id_conflict"):
        preallocate_journal(
            root,
            kind="test",
            operation_id=operation_id,
            validation_mode="registered",
            metadata={},
        )

    after = {
        path.relative_to(directory): path.read_bytes()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }
    assert before == after
    assert sentinel.read_text(encoding="utf-8") == "do not touch"


def test_recover_finishes_a_partially_applied_plan(
    operation_draft: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = operation_draft
    plan, _old_a, _old_b, new_a, new_b = two_write_plan(root)

    monkeypatch.setattr("draft.operations.replace_file", crash_on_nth_replace(2))
    with pytest.raises(RuntimeError, match="injected crash"):
        execute_plan(plan)

    monkeypatch.undo()
    result = recover(root)

    assert result.phase == "completed"
    assert (root / "pages/a.md").read_bytes() == new_a
    assert (root / "pages/b.md").read_bytes() == new_b
    assert not (root / ".draft/operation.lock").exists()


def test_recover_stops_on_external_edit(
    operation_draft: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = operation_draft
    plan, _old_a, _old_b, _new_a, _new_b = two_write_plan(root)

    monkeypatch.setattr("draft.operations.replace_file", crash_on_nth_replace(2))
    with pytest.raises(RuntimeError, match="injected crash"):
        execute_plan(plan)
    monkeypatch.undo()
    (root / "pages/a.md").write_text("human edit", encoding="utf-8")

    with pytest.raises(DraftError, match="operation.external_change"):
        recover(root)

    assert (root / "pages/a.md").read_text(encoding="utf-8") == "human edit"
    assert (root / ".draft/operation.lock").exists()


def test_second_writer_rejected_before_markdown_scan(operation_draft: Path) -> None:
    root = operation_draft
    lock = root / ".draft/operation.lock"
    lock.write_text('{"operation_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}', encoding="utf-8")
    # 破坏一页语法：若实现先扫描 Markdown 就会以解析错误而不是锁错误失败。
    (root / "pages/a.md").write_text("not a managed page\n", encoding="utf-8")
    plan, _old_a, _old_b, _new_a, _new_b = two_write_plan(root)

    with pytest.raises(DraftError, match=CheckCode.OPERATION_RECOVERY_REQUIRED.value):
        execute_plan(plan)


def test_no_lock_allocating_orphan_is_cleaned(operation_draft: Path) -> None:
    root = operation_draft
    journal_dir = write_journal(root, "b" * 32, "allocating")

    result = recover(root)

    assert result.phase == "completed"
    assert not journal_dir.exists()


def test_lock_with_missing_journal_is_preserved(operation_draft: Path) -> None:
    root = operation_draft
    lock = root / ".draft/operation.lock"
    lock.write_text('{"operation_id":"cccccccccccccccccccccccccccccccc"}', encoding="utf-8")

    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        recover(root)

    assert lock.exists()


def test_no_lock_prepared_journal_is_inconsistent(operation_draft: Path) -> None:
    root = operation_draft
    write_journal(root, "d" * 32, "prepared")

    with pytest.raises(DraftError, match=CheckCode.OPERATION_INCONSISTENT_STATE.value):
        recover(root)


def test_no_lock_succeeded_journal_is_inconsistent(operation_draft: Path) -> None:
    root = operation_draft
    write_journal(root, "e" * 32, "trash_handoff_succeeded")

    with pytest.raises(DraftError, match=CheckCode.OPERATION_INCONSISTENT_STATE.value):
        recover(root)


def test_no_lock_terminal_and_pending_journals_are_accepted(operation_draft: Path) -> None:
    root = operation_draft
    completed = write_journal(root, "f" * 32, "completed")
    rolled_back = write_journal(root, "0" * 32, "rolled_back")
    pending = write_journal(root, "1" * 32, "trash_handoff_pending")

    result = recover(root)

    assert result.phase == "completed"
    assert completed.exists()
    assert rolled_back.exists()
    assert pending.exists()


def test_missing_staged_bytes_roll_back_from_originals(
    operation_draft: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = operation_draft
    plan, old_a, old_b, _new_a, _new_b = two_write_plan(root)

    monkeypatch.setattr("draft.operations.replace_file", crash_on_nth_replace(2))
    with pytest.raises(RuntimeError, match="injected crash"):
        execute_plan(plan)
    monkeypatch.undo()
    operations = root / ".draft/operations"
    journal_dirs = [entry for entry in operations.iterdir() if entry.is_dir()]
    assert len(journal_dirs) == 1
    staged = journal_dirs[0] / "staged"
    for entry in staged.iterdir():
        entry.unlink()

    result = recover(root)

    assert result.phase == "rolled_back"
    assert (root / "pages/a.md").read_bytes() == old_a
    assert (root / "pages/b.md").read_bytes() == old_b
    assert not (root / ".draft/operation.lock").exists()


def test_completed_operation_releases_lock_and_cleans_copies(operation_draft: Path) -> None:
    root = operation_draft
    plan, _old_a, _old_b, new_a, new_b = two_write_plan(root)

    result = execute_plan(plan)

    assert result.phase == "completed"
    assert (root / "pages/a.md").read_bytes() == new_a
    assert (root / "pages/b.md").read_bytes() == new_b
    assert not (root / ".draft/operation.lock").exists()
    operation_dir = root / ".draft/operations" / result.operation_id
    assert (operation_dir / "journal.json").is_file()
    assert not (operation_dir / "originals").exists()
    assert not (operation_dir / "staged").exists()


def test_mutation_path_escape_rejected_before_allocation(operation_draft: Path) -> None:
    root = operation_draft
    plan = OperationPlan(
        kind="test",
        root=root,
        validation_mode="registered",
        mutations=(
            FileMutation(
                Path("../evil.md"),
                MutationKind.WRITE,
                None,
                sha256_bytes(b"evil"),
                b"evil",
            ),
        ),
    )

    with pytest.raises(DraftError, match="path.escape"):
        execute_plan(plan)

    operations = root / ".draft/operations"
    assert list(operations.iterdir()) == []


def test_mutation_through_symlink_rejected_before_allocation(
    operation_draft: Path, tmp_path: Path
) -> None:
    root = operation_draft
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "pages/sub").symlink_to(outside, target_is_directory=True)
    plan = OperationPlan(
        kind="test",
        root=root,
        validation_mode="registered",
        mutations=(
            FileMutation(
                Path("pages/sub/x.md"),
                MutationKind.WRITE,
                None,
                sha256_bytes(b"x"),
                b"x",
            ),
        ),
    )

    with pytest.raises(DraftError, match=CheckCode.PATH_SYMLINK_ESCAPE.value):
        execute_plan(plan)

    operations = root / ".draft/operations"
    assert list(operations.iterdir()) == []


def test_second_platform_recovers_from_root_local_facts_only(
    operation_draft: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = operation_draft
    plan, _old_a, _old_b, new_a, new_b = two_write_plan(root)

    monkeypatch.setattr("draft.operations.replace_file", crash_on_nth_replace(1))
    with pytest.raises(RuntimeError, match="injected crash"):
        execute_plan(plan)
    monkeypatch.undo()

    # 模拟另一个平台进程：只凭显式根路径，没有任何内存状态。
    result = recover(root)

    assert result.phase == "completed"
    assert (root / "pages/a.md").read_bytes() == new_a
    assert (root / "pages/b.md").read_bytes() == new_b


def test_checker_reports_operation_states(operation_draft: Path) -> None:
    import shutil

    from draft.checker import check_draft

    root = operation_draft

    write_journal(root, "2" * 32, "allocating")
    report = check_draft(root)
    assert [issue.code for issue in report.errors] == []
    assert [issue.code for issue in report.reviews] == [CheckCode.OPERATION_ORPHAN_ALLOCATING.value]
    shutil.rmtree(root / ".draft/operations" / ("2" * 32))

    write_journal(root, "3" * 32, "prepared")
    report = check_draft(root)
    inconsistent = next(
        issue
        for issue in report.errors
        if issue.code == CheckCode.OPERATION_INCONSISTENT_STATE.value
    )
    assert inconsistent.details == ("prepared",)
    shutil.rmtree(root / ".draft/operations" / ("3" * 32))

    lock = root / ".draft/operation.lock"
    lock.write_text('{"operation_id":"44444444444444444444444444444444"}', encoding="utf-8")
    report = check_draft(root)
    assert CheckCode.OPERATION_JOURNAL_CORRUPT.value in [issue.code for issue in report.errors]

    write_journal(root, "4" * 32, "applying")
    report = check_draft(root)
    recovery = next(
        issue
        for issue in report.errors
        if issue.code == CheckCode.OPERATION_RECOVERY_REQUIRED.value
    )
    assert recovery.details == ("4" * 32,)


@pytest.mark.parametrize(
    "bad_id",
    [
        "",
        "a" * 31,
        "a" * 33,
        "A" * 32,
        "../../outside",
        "../outside",
        "/etc/passwd",
        "ab/cd" + "e" * 27,
        "g" * 32,
    ],
)
def test_lock_with_invalid_operation_id_is_corrupt_not_traversal(
    operation_draft: Path, bad_id: str, tmp_path: Path
) -> None:
    import json as json_module

    root = operation_draft
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "journal.json"
    sentinel.write_text("{}", encoding="utf-8")
    lock = root / ".draft/operation.lock"
    lock.write_text(json_module.dumps({"operation_id": bad_id}), encoding="utf-8")

    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        recover(root)

    assert lock.exists()
    assert sentinel.read_text(encoding="utf-8") == "{}"


def test_lock_traversal_never_executes_outside_journal(
    operation_draft: Path, tmp_path: Path
) -> None:
    root = operation_draft
    # operations/../../outside 解析为草稿根内的 topic/outside：遍历目标在根内也是逃逸。
    outside = root / "outside"
    outside.mkdir()
    journal: dict[str, object] = {
        "journal_version": JOURNAL_VERSION,
        "operation_id": "../../outside",
        "kind": "test",
        "phase": "applying",
        "validation_mode": "registered",
        "mutations": [],
        "remove_empty_directories": [],
        "metadata": {},
    }
    payload = json.dumps(journal)
    (outside / "journal.json").write_text(payload, encoding="utf-8")
    lock = root / ".draft/operation.lock"
    lock.write_text(json.dumps({"operation_id": "../../outside"}), encoding="utf-8")

    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        recover(root)

    assert (outside / "journal.json").read_text(encoding="utf-8") == payload
    assert lock.exists()


def _crash_release_lock(root: Path, operation_id: str) -> None:
    raise RuntimeError("injected crash after terminal journal write")


def test_completed_journal_with_matching_lock_is_cleaned_up(
    operation_draft: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = operation_draft
    plan, _old_a, _old_b, new_a, new_b = two_write_plan(root)

    monkeypatch.setattr("draft.operations.release_lock", _crash_release_lock)
    with pytest.raises(RuntimeError, match="injected crash"):
        execute_plan(plan)
    monkeypatch.undo()
    assert (root / ".draft/operation.lock").exists()

    result = recover(root)

    assert result.phase == "completed"
    assert (root / "pages/a.md").read_bytes() == new_a
    assert (root / "pages/b.md").read_bytes() == new_b
    assert not (root / ".draft/operation.lock").exists()
    operations = root / ".draft/operations"
    journal_dir = next(operations.iterdir())
    assert (journal_dir / "journal.json").is_file()
    assert not (journal_dir / "originals").exists()
    assert not (journal_dir / "staged").exists()


def test_rolled_back_journal_with_matching_lock_releases_lock(
    operation_draft: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = operation_draft
    _plan, old_a, _old_b, _new_a, _new_b = two_write_plan(root)
    # 让验证失败：计划把 a.md 改坏，使检查后存在结构错误。
    broken = (root / "pages/a.md").read_bytes().replace(b"(../index.md)", b"(../missing.md)")
    plan = OperationPlan(
        kind="test",
        root=root,
        validation_mode="registered",
        mutations=(
            FileMutation(
                Path("pages/a.md"),
                MutationKind.WRITE,
                sha256_bytes(old_a),
                sha256_bytes(broken),
                broken,
            ),
        ),
    )

    monkeypatch.setattr("draft.operations.release_lock", _crash_release_lock)
    with pytest.raises((RuntimeError, DraftError)):
        execute_plan(plan)
    monkeypatch.undo()
    lock = root / ".draft/operation.lock"
    assert lock.exists()
    import json as json_module

    journal_dir = next(entry for entry in (root / ".draft/operations").iterdir() if entry.is_dir())
    journal = json_module.loads((journal_dir / "journal.json").read_text(encoding="utf-8"))
    assert journal["phase"] == "rolled_back"
    assert (root / "pages/a.md").read_bytes() == old_a

    result = recover(root)

    assert result.phase == "rolled_back"
    assert not lock.exists()
    assert (root / "pages/a.md").read_bytes() == old_a


def test_completed_journal_with_external_edit_keeps_lock(
    operation_draft: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = operation_draft
    plan, _old_a, _old_b, _new_a, _new_b = two_write_plan(root)

    monkeypatch.setattr("draft.operations.release_lock", _crash_release_lock)
    with pytest.raises(RuntimeError, match="injected crash"):
        execute_plan(plan)
    monkeypatch.undo()
    (root / "pages/a.md").write_text("human edit", encoding="utf-8")

    with pytest.raises(DraftError, match="operation.external_change"):
        recover(root)

    assert (root / ".draft/operation.lock").exists()
    assert (root / "pages/a.md").read_text(encoding="utf-8") == "human edit"


def test_abandoned_allocating_cleans_recovery_copies(
    operation_draft: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = operation_draft
    plan, old_a, old_b, _new_a, _new_b = two_write_plan(root)

    import draft.operations as operations_module

    original_write_journal = operations_module.write_journal

    def crash_before_prepared(journal: object) -> None:
        if (
            getattr(journal, "phase", None) == "prepared"
            or getattr(journal, "phase", "")
            == __import__("draft.models", fromlist=["OperationPhase"]).OperationPhase.PREPARED
        ):
            raise RuntimeError("injected crash before prepared")
        original_write_journal(journal)  # type: ignore[arg-type]

    monkeypatch.setattr("draft.operations.write_journal", crash_before_prepared)
    with pytest.raises(RuntimeError, match="injected crash"):
        execute_plan(plan)
    monkeypatch.undo()

    journal_dir = next(entry for entry in (root / ".draft/operations").iterdir() if entry.is_dir())
    assert (journal_dir / "originals").exists()
    assert (journal_dir / "staged").exists()

    result = recover(root)

    assert result.phase == "rolled_back"
    assert not (journal_dir / "originals").exists()
    assert not (journal_dir / "staged").exists()
    assert not (root / ".draft/operation.lock").exists()
    assert (root / "pages/a.md").read_bytes() == old_a
    assert (root / "pages/b.md").read_bytes() == old_b


def test_internal_state_symlink_escape_rejected(operation_draft: Path, tmp_path: Path) -> None:
    """.draft 等中间组件为符号链接时，预分配与加锁必须拒绝，不得写到根外。"""
    import shutil

    from draft.operations import acquire_lock

    root = operation_draft
    external = tmp_path / "external"
    external.mkdir()
    shutil.rmtree(root / ".draft")
    (root / ".draft").symlink_to(external, target_is_directory=True)

    with pytest.raises(DraftError, match=CheckCode.PATH_SYMLINK_ESCAPE.value):
        acquire_lock(root, "a" * 32)
    with pytest.raises(DraftError, match=CheckCode.PATH_SYMLINK_ESCAPE.value):
        preallocate_journal(
            root,
            kind="test",
            operation_id="a" * 32,
            validation_mode="registered",
            metadata={},
        )

    # 外部目录必须保持为空：不得被写入任何内部状态。
    assert list(external.iterdir()) == []
