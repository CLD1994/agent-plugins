import json
from pathlib import Path

import pytest

from draft.diagnostic_codes import CheckCode
from draft.errors import DraftError
from draft.graph import load_graph
from draft.models import (
    FileMutation,
    MutationKind,
    OperationKind,
    OperationPhase,
    OperationPlan,
    RootState,
)
from draft.observations import EMPTY_OBSERVATIONS, graph_observations
from draft.operations import (
    JOURNAL_VERSION,
    execute_plan,
    operation_journal_path,
    preallocate_journal,
    read_journal,
    read_lock,
    recover,
    sha256_bytes,
)
from draft.vocabulary import ConclusionsState
from tests.integration.support import managed_marker_text, write_raw_journal


@pytest.fixture
def operation_draft(tmp_path: Path) -> Path:
    root = tmp_path / "topic"
    files = {
        "index.md": """# Topic

> 状态：`已关闭：纯导航页，不表达独立结论`

## 子主题

- [A](pages/a.md)
- [B](pages/b.md)
- [结论汇总](conclusions.md)
""",
        "conclusions.md": """# 结论汇总

> 状态：`已确认`
>
> 父主题：[根索引](index.md)

## 已确认结论

- [A](pages/a.md)
- [B](pages/b.md)
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
        ".draft/draft.json": managed_marker_text(conclusions_state=ConclusionsState.GENERATED),
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
        kind=OperationKind.CREATE,
        root=root,
        root_state=RootState.REGISTERED,
        observations=EMPTY_OBSERVATIONS,
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
        "kind": OperationKind.CREATE.value,
        "phase": phase,
        "root_state": "registered",
        "observations": [],
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
        "root_state": "registered",
        "observations": [],
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


@pytest.mark.parametrize("version", [4, "2"])
def test_unknown_or_malformed_journal_version_is_rejected(
    operation_draft: Path, version: object
) -> None:
    # version=None 由 write_raw_journal 默认取当前权威版本，不再表示损坏。
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


def test_journal_unsafe_original_never_touches_external_file(
    operation_draft: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """日志 original 指向根外绝对路径：read_journal 在解析边界拒绝，绝不读取外部文件。"""
    sentinel = tmp_path / "external-original.bin"
    sentinel.write_bytes(b"must never be read")
    journal_dir = write_journal(operation_draft, "b" * 32, "prepared")
    data = json.loads((journal_dir / "journal.json").read_text(encoding="utf-8"))
    data["mutations"] = [
        {
            "path": "pages/a.md",
            "kind": "write",
            "before_sha256": "c" * 64,
            "after_sha256": "d" * 64,
            "original": str(sentinel),
            "staged": "staged/0000.bin",
            "applied": False,
        }
    ]
    (journal_dir / "journal.json").write_text(json.dumps(data), encoding="utf-8")

    original_read = Path.read_bytes

    def read_bytes(self: Path) -> bytes:
        if self == sentinel:
            raise AssertionError("external original must not be read")
        return original_read(self)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        read_journal(journal_dir / "journal.json")
    assert original_read(sentinel) == b"must never be read"


def _write_journal_with_observations(
    root: Path, operation_id: str, observations: list[dict[str, object]]
) -> Path:
    directory = root / ".draft" / "operations" / operation_id
    directory.mkdir(parents=True, exist_ok=True)
    journal: dict[str, object] = {
        "journal_version": JOURNAL_VERSION,
        "operation_id": operation_id,
        "kind": OperationKind.CREATE.value,
        "phase": "completed",
        "root_state": "registered",
        "observations": observations,
        "mutations": [],
        "remove_empty_directories": [],
        "metadata": {},
    }
    journal_path = directory / "journal.json"
    journal_path.write_text(json.dumps(journal), encoding="utf-8")
    return journal_path


def test_read_journal_rejects_duplicate_observation_paths(operation_draft: Path) -> None:
    digest = "a" * 64
    duplicate: list[dict[str, object]] = [
        {"path": "pages/a.md", "kind": "file", "sha256": digest, "members": []},
        {"path": "pages/a.md", "kind": "file", "sha256": digest, "members": []},
    ]
    path = _write_journal_with_observations(operation_draft, "9" * 32, duplicate)
    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        read_journal(path)


@pytest.mark.parametrize(
    "bad_observation",
    [
        # 目录带摘要：sha256 与 members 互斥。
        {"path": "pages", "kind": "directory", "sha256": "a" * 64, "members": ["a.md"]},
        # 文件带成员：FILE 只允许摘要。
        {"path": "pages/a.md", "kind": "file", "sha256": "a" * 64, "members": ["b.md"]},
        # 文件缺摘要：FILE 必须携带摘要。
        {"path": "pages/a.md", "kind": "file", "sha256": None, "members": []},
        # 缺失条目带摘要：MISSING 不得携带摘要或成员。
        {"path": "pages/a.md", "kind": "missing", "sha256": "a" * 64, "members": []},
        # 摘要不是 64 位十六进制：FILE 摘要形状非法。
        {"path": "pages/a.md", "kind": "file", "sha256": "x", "members": []},
    ],
)
def test_read_journal_rejects_illegal_observation_shapes(
    operation_draft: Path, bad_observation: dict[str, object]
) -> None:
    path = _write_journal_with_observations(operation_draft, "9" * 32, [bad_observation])
    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        read_journal(path)


def test_preallocated_restore_requires_reverse_reference(
    operation_draft: Path,
) -> None:
    root = operation_draft
    restore_id = "3" * 32
    with pytest.raises(DraftError, match="operation.plan_invalid"):
        preallocate_journal(
            root,
            kind=OperationKind.RESTORE,
            operation_id=restore_id,
            root_state=RootState.REGISTERED,
            observations=EMPTY_OBSERVATIONS,
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
            kind=OperationKind.RESTORE,
            operation_id=restore_id,
            root_state=RootState.REGISTERED,
            observations=EMPTY_OBSERVATIONS,
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
        kind=OperationKind.RESTORE,
        operation_id=restore_id,
        root_state=RootState.REGISTERED,
        observations=EMPTY_OBSERVATIONS,
        metadata={"restores_trash_operation": trash_id},
    )
    plan = OperationPlan(
        kind=OperationKind.RESTORE,
        root=root,
        mutations=(),
        root_state=RootState.REGISTERED,
        observations=EMPTY_OBSERVATIONS,
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
            kind=OperationKind.CREATE,
            operation_id=operation_id,
            root_state=RootState.REGISTERED,
            observations=EMPTY_OBSERVATIONS,
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


def _operation_dir(root: Path) -> Path:
    return next(entry for entry in (root / ".draft/operations").iterdir() if entry.is_dir())


def test_recover_consumes_owned_temp_leftover(
    operation_draft: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """target=before + 精确 temp 摘要=after：允许消费（复核后清理）并前向完成（回归 H3）。"""
    from draft.operations import _mutation_temporary_path  # pyright: ignore[reportPrivateUsage]

    root = operation_draft
    plan, _old_a, _old_b, _new_a, new_b = two_write_plan(root)
    monkeypatch.setattr("draft.operations.replace_file", crash_on_nth_replace(2))
    with pytest.raises(RuntimeError, match="injected crash"):
        execute_plan(plan)
    monkeypatch.undo()

    operation_id = _operation_dir(root).name
    b_temp = root / _mutation_temporary_path(Path("pages/b.md"), operation_id, 1)
    assert b_temp.read_bytes() == new_b  # 崩溃遗留精确 temp。

    result = recover(root)

    assert result.phase == "completed"
    assert not b_temp.exists()  # 遗留 temp 被清理。
    assert (root / "pages/b.md").read_bytes() == new_b


def test_recover_rejects_coexisting_temp_when_target_after(
    operation_draft: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """target=after 却存在共存精确 temp：原子 replace 不会遗留，判外部变化（回归 H3）。"""
    from draft.operations import _mutation_temporary_path  # pyright: ignore[reportPrivateUsage]

    root = operation_draft
    plan, _old_a, _old_b, new_a, _new_b = two_write_plan(root)
    monkeypatch.setattr("draft.operations.replace_file", crash_on_nth_replace(2))
    with pytest.raises(RuntimeError, match="injected crash"):
        execute_plan(plan)
    monkeypatch.undo()

    operation_id = _operation_dir(root).name
    a_temp = root / _mutation_temporary_path(Path("pages/a.md"), operation_id, 0)
    a_temp.write_bytes(new_a)

    with pytest.raises(DraftError, match="operation.external_change"):
        recover(root)

    assert (root / "pages/a.md").read_bytes() == new_a
    assert a_temp.is_file()  # 现场保留。
    assert (root / ".draft/operation.lock").exists()


def test_recover_rejects_wrong_temp_content(
    operation_draft: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """target=before + temp 摘要不符：外部篡改，判 external_change 并保留现场（回归 H3）。"""
    from draft.operations import _mutation_temporary_path  # pyright: ignore[reportPrivateUsage]

    root = operation_draft
    plan, old_a, _old_b, _new_a, _new_b = two_write_plan(root)
    monkeypatch.setattr("draft.operations.replace_file", crash_on_nth_replace(1))
    with pytest.raises(RuntimeError, match="injected crash"):
        execute_plan(plan)
    monkeypatch.undo()

    operation_id = _operation_dir(root).name
    a_temp = root / _mutation_temporary_path(Path("pages/a.md"), operation_id, 0)
    a_temp.write_bytes(b"tampered")

    with pytest.raises(DraftError, match="operation.external_change"):
        recover(root)

    assert (root / "pages/a.md").read_bytes() == old_a  # 目标仍 before。
    assert a_temp.read_bytes() == b"tampered"
    assert (root / ".draft/operation.lock").exists()


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
        kind=OperationKind.CREATE,
        root=root,
        root_state=RootState.REGISTERED,
        observations=EMPTY_OBSERVATIONS,
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
        kind=OperationKind.CREATE,
        root=root,
        root_state=RootState.REGISTERED,
        observations=EMPTY_OBSERVATIONS,
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
        "root_state": "registered",
        "observations": [],
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
    dirs = [entry for entry in operations.iterdir() if entry.is_dir()]
    assert len(dirs) == 1
    journal_dir = dirs[0]
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
        kind=OperationKind.CREATE,
        root=root,
        root_state=RootState.REGISTERED,
        observations=EMPTY_OBSERVATIONS,
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


def _plan_with_observed_inputs(root: Path) -> OperationPlan:
    """构造带真实读集观察的双写计划：观察清单覆盖 index、两页与 marker。"""
    graph = load_graph(root)
    old_a = (root / "pages/a.md").read_bytes()
    old_b = (root / "pages/b.md").read_bytes()
    new_a = old_a + b"\nforward-a\n"
    new_b = old_b + b"\nforward-b\n"
    return OperationPlan(
        kind=OperationKind.CREATE,
        root=root,
        root_state=RootState.REGISTERED,
        observations=graph_observations(graph),
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


def test_graph_observations_covers_empty_managed_directory(
    operation_draft: Path,
) -> None:
    """受管理命名空间的空目录也进入观察清单：向其中新增页面可被复核发现（计划 Task 10）。"""
    from dataclasses import dataclass
    from typing import cast

    from draft.models import PageDocument
    from draft.observations import EntryKind, graph_observations

    root = operation_draft
    (root / "pages/empty").mkdir()

    @dataclass
    class FakeGraph:
        root: Path
        pages: dict[Path, PageDocument]

    class _Page:
        text = "# A\n"

    manifest = graph_observations(
        FakeGraph(root=root, pages={Path("pages/a.md"): cast(PageDocument, _Page())})
    )
    observed = manifest.for_path(Path("pages/empty"))
    assert observed is not None
    assert observed.kind is EntryKind.DIRECTORY
    assert observed.members == ()


def test_execute_plan_lock_competition_finalizes_preallocated_journal(
    operation_draft: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """锁竞争失败（另一进程抢先取得锁）时，本次新建日志必须终态化，不留 orphan。"""
    import draft.operations as ops_module

    root = operation_draft
    our_id = "c" * 32
    competitor_id = "d" * 32
    plan = OperationPlan(
        kind=OperationKind.CREATE,
        root=root,
        root_state=RootState.REGISTERED,
        observations=EMPTY_OBSERVATIONS,
        mutations=(),
        metadata={"operation_id": our_id},
    )
    original_read_lock = ops_module.read_lock

    def race(root_path: Path) -> str | None:
        result = original_read_lock(root_path)
        if result is None:
            # 竞争者抢先取得不同 operation_id 的锁（真实锁文件）。
            ops_module.acquire_lock(root_path, competitor_id)
        return result

    monkeypatch.setattr(ops_module, "read_lock", race)
    with pytest.raises(DraftError, match=CheckCode.OPERATION_RECOVERY_REQUIRED.value):
        execute_plan(plan)

    # 本次新建日志已终态化，锁仍属竞争者。
    journal = read_journal(operation_journal_path(root, our_id))
    assert journal.phase is OperationPhase.ROLLED_BACK
    assert read_lock(root) == competitor_id


def test_execute_plan_lock_race_does_not_corrupt_adopted_journal(
    operation_draft: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """锁竞争失败方采用既有预分配日志时，不得改写获胜者日志或清理其恢复材料。"""
    import draft.operations as ops_module

    root = operation_draft
    winner_id = "a" * 32
    preallocate_journal(
        root,
        kind=OperationKind.CREATE,
        operation_id=winner_id,
        root_state=RootState.REGISTERED,
        observations=EMPTY_OBSERVATIONS,
        metadata={},
    )
    plan = OperationPlan(
        kind=OperationKind.CREATE,
        root=root,
        root_state=RootState.REGISTERED,
        observations=EMPTY_OBSERVATIONS,
        mutations=(),
        metadata={"operation_id": winner_id},
    )
    original_read_lock = ops_module.read_lock

    def race(root_path: Path) -> str | None:
        result = original_read_lock(root_path)
        if result is None:
            # 竞争者抢先取得同一 operation_id 的锁。
            ops_module.acquire_lock(root_path, winner_id)
        return result

    monkeypatch.setattr(ops_module, "read_lock", race)
    journal_path = root / ".draft/operations" / winner_id / "journal.json"
    journal_before = journal_path.read_bytes()

    with pytest.raises(DraftError, match=CheckCode.OPERATION_RECOVERY_REQUIRED.value):
        execute_plan(plan)

    # 失败方不得改写日志、不得清理副本；锁仍属获胜者。
    assert journal_path.read_bytes() == journal_before
    assert read_lock(root) == winner_id
    assert not (root / ".draft/operations" / winner_id / "originals").exists()
    assert not (root / ".draft/operations" / winner_id / "staged").exists()


def test_recover_uses_live_file_state_not_stale_applied(
    operation_draft: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """崩溃窗口：新文件已落盘但 applied=true 尚未持久化；恢复不得误判为外部变化。

    文件落盘发生在 applied=true 日志持久化之前。目录成员变化必须按活文件 before/after
    分类推导，不能信任可能滞后的 applied 标志。
    """
    import draft.operations as ops_module

    root = operation_draft
    new_bytes = b"resource-data\n"
    plan = OperationPlan(
        kind=OperationKind.CREATE,
        root=root,
        root_state=RootState.REGISTERED,
        observations=graph_observations(load_graph(root)),
        mutations=(
            # 非受管页面文件：只改变目录成员，不触发页面解析检查。
            FileMutation(
                Path("pages/new.txt"),
                MutationKind.WRITE,
                None,
                sha256_bytes(new_bytes),
                new_bytes,
            ),
        ),
    )
    original_apply_write = ops_module._apply_write  # pyright: ignore[reportPrivateUsage]

    def write_then_crash(
        root_path: Path,
        target: Path,
        content: bytes,
        *,
        temporary: Path,
    ) -> None:
        original_apply_write(root_path, target, content, temporary=temporary)
        raise RuntimeError("injected crash after file lands")

    monkeypatch.setattr(ops_module, "_apply_write", write_then_crash)
    with pytest.raises(RuntimeError, match="injected crash after file lands"):
        execute_plan(plan)
    monkeypatch.undo()

    # 新文件已落盘（after），但日志仍记录 applied=false：正常崩溃窗口。
    assert (root / "pages/new.txt").exists()
    result = recover(root)
    assert result.phase == "completed"
    assert (root / "pages/new.txt").exists()
    assert not (root / ".draft/operation.lock").exists()


def test_execute_plan_detects_new_page_in_previously_empty_directory(
    operation_draft: Path,
) -> None:
    """plan→execute 之间向此前为空的受管理目录新增页面：观察复核拒绝且不遗留锁。"""
    root = operation_draft
    (root / "pages/empty").mkdir()
    plan = _plan_with_observed_inputs(root)
    (root / "pages/empty/new.md").write_text("# new\n", encoding="utf-8")

    with pytest.raises(DraftError, match="operation.external_change"):
        execute_plan(plan)

    assert read_lock(root) is None


def test_execute_plan_observation_conflict_finalizes_journal_and_releases_lock(
    operation_draft: Path,
) -> None:
    """首次业务变更前复核失败：终态化本次准备日志并释放锁，不遗留锁或中段日志。"""
    root = operation_draft
    plan = _plan_with_observed_inputs(root)
    # 修改一个被观察但未参与 mutation 的页面（index.md 属于读集）。
    (root / "index.md").write_text("external edit", encoding="utf-8")

    with pytest.raises(DraftError, match="operation.external_change"):
        execute_plan(plan)

    assert read_lock(root) is None
    operations = root / ".draft/operations"
    remaining = [entry for entry in operations.iterdir() if entry.is_dir()]
    assert len(remaining) == 1
    journal = read_journal(remaining[0] / "journal.json")
    assert journal.phase is OperationPhase.ROLLED_BACK


def test_recover_mid_write_verifies_observed_inputs_and_keeps_scene(
    operation_draft: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """中段恢复必须复核观察清单：被观察但未参与 mutation 的页面变化不能静默通过。"""
    root = operation_draft
    plan = _plan_with_observed_inputs(root)
    original_write_journal = __import__(
        "draft.operations", fromlist=["write_journal"]
    ).write_journal

    def crash_on_applying(journal: object) -> None:
        if getattr(journal, "phase", None) is OperationPhase.APPLYING:
            raise RuntimeError("injected crash at applying")
        original_write_journal(journal)

    monkeypatch.setattr("draft.operations.write_journal", crash_on_applying)
    with pytest.raises(RuntimeError, match="injected crash at applying"):
        execute_plan(plan)
    monkeypatch.undo()
    assert (root / ".draft/operation.lock").exists()

    # 外部编辑一个被观察但非 mutation 的页面：保持结构合法，只改变读集摘要
    # （否则 registered 后置完整检查会先拦截，无法独立证明观察复核缺口）。
    index = (root / "index.md").read_text(encoding="utf-8")
    (root / "index.md").write_text(index.replace("# Topic", "# Topic edited"), encoding="utf-8")

    with pytest.raises(DraftError, match="operation.external_change"):
        recover(root)

    # 无法解释的读集变化：保留锁、日志和恢复材料，等待人工处理。
    assert (root / ".draft/operation.lock").exists()


def test_abandoned_allocating_cleans_recovery_copies(
    operation_draft: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """allocating 崩溃（prepared 日志写入前）：副本目录尚未创建，无锁放弃并清理（回归 H4）。"""
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
    # prepare 先登记后建文件：崩溃时 originals/staged 尚未创建。
    assert not (journal_dir / "originals").exists()
    assert not (journal_dir / "staged").exists()

    result = recover(root)

    assert result.phase == "rolled_back"
    assert not (journal_dir / "originals").exists()
    assert not (journal_dir / "staged").exists()
    assert not (root / ".draft/operation.lock").exists()
    assert (root / "pages/a.md").read_bytes() == old_a
    assert (root / "pages/b.md").read_bytes() == old_b


def test_crash_during_recovery_copy_creation_is_judgable(
    operation_draft: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """恢复副本创建中途崩溃：journal 已 PREPARED、副本部分缺失，恢复可判定并回滚（回归 H4）。"""
    root = operation_draft
    plan, old_a, old_b, _new_a, _new_b = two_write_plan(root)

    import draft.operations as operations_module

    original_durable = operations_module.durable_write
    calls = {"n": 0}

    def crash_on_second_durable(path: object, value: object) -> None:
        # journal 写入不再经过 durable_write（回归 H2 发布拆分），副本写入从第 1 次
        # durable_write 起计：第 2 次即第一个 staged 写入处崩溃。
        calls["n"] += 1
        if calls["n"] == 2:  # prepared 日志后第一个 staged 写入处崩溃。
            raise RuntimeError("injected crash during copy creation")
        original_durable(path, value)  # type: ignore[arg-type]

    monkeypatch.setattr("draft.operations.durable_write", crash_on_second_durable)
    with pytest.raises(RuntimeError, match="injected crash"):
        execute_plan(plan)
    monkeypatch.undo()

    journal_dir = next(entry for entry in (root / ".draft/operations").iterdir() if entry.is_dir())
    assert (journal_dir / "originals" / "0000.bin").exists()  # 第一个 original 已创建。
    assert not (journal_dir / "staged" / "0000.bin").exists()  # staged 文件写入处崩溃。

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
            kind=OperationKind.CREATE,
            operation_id="a" * 32,
            root_state=RootState.REGISTERED,
            observations=EMPTY_OBSERVATIONS,
            metadata={},
        )

    # 外部目录必须保持为空：不得被写入任何内部状态。
    assert list(external.iterdir()) == []


def _write_journal_with_copies(
    operation_draft: Path, op_id: str, *, staged_names: list[str], originals_names: list[str]
) -> Path:
    """写入带 originals/staged 副本与登记 mutation 的日志；返回日志路径。"""
    import json as json_module

    root = operation_draft
    journal_dir = root / ".draft/operations" / op_id
    mutations: list[dict[str, object]] = []
    for index, name in enumerate(originals_names):
        (journal_dir / "originals").mkdir(parents=True, exist_ok=True)
        path = journal_dir / "originals" / name
        path.write_bytes(b"original")
        mutations.append(
            {
                "path": f"pages/old-{index}.md",
                "kind": "write",
                "before_sha256": sha256_bytes(b"original"),
                "after_sha256": "a" * 64,
                "original": f"originals/{name}",
                "staged": None,
                "applied": True,
            }
        )
    for index, name in enumerate(staged_names):
        (journal_dir / "staged").mkdir(parents=True, exist_ok=True)
        path = journal_dir / "staged" / name
        path.write_bytes(b"staged")
        mutations.append(
            {
                "path": f"pages/new-{index}.md",
                "kind": "write",
                "before_sha256": None,
                "after_sha256": sha256_bytes(b"staged"),
                "original": None,
                "staged": f"staged/{name}",
                "applied": True,
            }
        )
    journal: dict[str, object] = {
        "journal_version": JOURNAL_VERSION,
        "operation_id": op_id,
        "kind": OperationKind.CREATE.value,
        "phase": OperationPhase.COMPLETED.value,
        "root_state": "registered",
        "observations": [],
        "mutations": mutations,
        "remove_empty_directories": [],
        "metadata": {},
    }
    journal_path = journal_dir / "journal.json"
    journal_path.write_text(json_module.dumps(journal), encoding="utf-8")
    return journal_path


def test_clean_recovery_copies_rejects_unknown_staged_member(
    operation_draft: Path,
) -> None:
    """staged/ 中的未知成员不得被静默删除（回归 H5）。"""
    from draft.operations import clean_recovery_copies, read_journal

    root = operation_draft
    op_id = "a" * 32
    journal_path = _write_journal_with_copies(
        root, op_id, staged_names=["0000.bin"], originals_names=[]
    )
    staged = root / ".draft/operations" / op_id / "staged"
    unknown = staged / "unknown.txt"
    unknown.write_text("unknown\n", encoding="utf-8")

    with pytest.raises(DraftError, match=CheckCode.OPERATION_INCONSISTENT_STATE.value):
        clean_recovery_copies(read_journal(journal_path))

    assert unknown.is_file()
    assert (staged / "0000.bin").is_file()


def test_clean_recovery_copies_rejects_symlinked_staged_dir(
    operation_draft: Path, tmp_path: Path
) -> None:
    """originals/staged 被替换为目录符号链接：清理必须拒绝而非跟随删除（回归 H5）。"""
    from draft.operations import clean_recovery_copies, read_journal

    root = operation_draft
    op_id = "b" * 32
    journal_path = _write_journal_with_copies(
        root, op_id, staged_names=["0000.bin"], originals_names=[]
    )
    staged = root / ".draft/operations" / op_id / "staged"
    external = tmp_path / "external"
    external.mkdir()
    (external / "0000.bin").write_bytes(b"external")
    import shutil

    shutil.rmtree(staged)
    staged.symlink_to(external)

    with pytest.raises(DraftError, match=CheckCode.OPERATION_INCONSISTENT_STATE.value):
        clean_recovery_copies(read_journal(journal_path))


def test_clean_recovery_copies_rejects_dangling_symlinked_dir(
    operation_draft: Path, tmp_path: Path
) -> None:
    """悬空 originals 符号链接：按 link 处理为损坏，不折叠成幂等缺失后继续解锁（回归 M2）。"""
    from draft.operations import clean_recovery_copies, read_journal

    root = operation_draft
    op_id = "c" * 32
    journal_path = _write_journal_with_copies(
        root, op_id, staged_names=["0000.bin"], originals_names=[]
    )
    staged = root / ".draft/operations" / op_id / "staged"
    import shutil

    shutil.rmtree(staged)
    staged.symlink_to(tmp_path / "nowhere", target_is_directory=True)

    with pytest.raises(DraftError, match=CheckCode.OPERATION_INCONSISTENT_STATE.value):
        clean_recovery_copies(read_journal(journal_path))

    assert staged.is_symlink()


def test_classify_node_distinguishes_dangling_symlink(tmp_path: Path) -> None:
    """lstat 分类：悬空符号链接是 SYMLINK 不是 MISSING（回归 M2）。"""
    from draft.observations import EntryKind
    from draft.operations import classify_node

    dangling = tmp_path / "dangling"
    dangling.symlink_to(tmp_path / "nowhere")
    assert classify_node(dangling) is EntryKind.SYMLINK
    assert classify_node(tmp_path / "missing") is EntryKind.MISSING
    assert classify_node(tmp_path) is EntryKind.DIRECTORY


def test_anchored_leaf_verifies_and_deletes_via_same_handle(tmp_path: Path) -> None:
    """锚定访问：验证与删除使用同一 no-follow 目录句柄，缺失中间组件视为 MISSING。"""
    from draft.observations import EntryKind
    from draft.operations import anchored_leaf

    root = tmp_path / "root"
    leaf_rel = Path("a/b/file.txt")
    (root / "a" / "b").mkdir(parents=True)
    (root / leaf_rel).write_bytes(b"content")

    with anchored_leaf(
        root, leaf_rel, code=CheckCode.OPERATION_INCONSISTENT_STATE.value, code_path=leaf_rel
    ) as leaf:
        assert leaf.classify() is EntryKind.FILE
        assert leaf.read() == b"content"
        leaf.unlink()
    assert not (root / leaf_rel).exists()

    # 中间组件缺失：叶视为 MISSING。
    with anchored_leaf(
        root,
        Path("a/c/missing.txt"),
        code=CheckCode.OPERATION_INCONSISTENT_STATE.value,
        code_path=leaf_rel,
    ) as leaf:
        assert leaf.classify() is EntryKind.MISSING


def testanchored_leaf_rejects_intermediate_symlink(tmp_path: Path) -> None:
    """中间组件替换为符号链接：锚定访问必须以稳定错误停止，不得跟随链接。"""
    import shutil

    from draft.errors import DraftError
    from draft.operations import anchored_leaf  # pyright: ignore[reportPrivateUsage]

    root = tmp_path / "root"
    external = tmp_path / "external"
    external.mkdir()
    (external / "payload.txt").write_bytes(b"external")
    (root / "a" / "b").mkdir(parents=True)
    (root / "a" / "b" / "file.txt").write_bytes(b"inside")
    shutil.rmtree(root / "a" / "b")
    (root / "a" / "b").symlink_to(external)

    with (
        pytest.raises(DraftError, match=CheckCode.OPERATION_INCONSISTENT_STATE.value),
        anchored_leaf(
            root,
            Path("a/b/file.txt"),
            code=CheckCode.OPERATION_INCONSISTENT_STATE.value,
            code_path=Path("a/b/file.txt"),
        ) as leaf,
    ):
        leaf.unlink()
    assert (external / "payload.txt").read_bytes() == b"external"


def _write_journal_with_originals_and_staged(
    operation_draft: Path, op_id: str, *, extra_sub: str | None
) -> Path:
    """写入 H5 合法的 originals+staged 登记日志；可在 originals 或 staged 注入未知成员。"""
    import json as json_module

    root = operation_draft
    journal_dir = root / ".draft/operations" / op_id
    (journal_dir / "originals").mkdir(parents=True)
    (journal_dir / "staged").mkdir()
    (journal_dir / "originals" / "0000.bin").write_bytes(b"original")
    (journal_dir / "staged" / "0000.bin").write_bytes(b"staged0")
    if extra_sub is not None:
        (journal_dir / extra_sub / "extra.txt").write_bytes(b"extra")
    mutations: list[dict[str, object]] = [
        {
            "path": "pages/old-0.md",
            "kind": "write",
            "before_sha256": sha256_bytes(b"original"),
            "after_sha256": sha256_bytes(b"staged0"),
            "original": "originals/0000.bin",
            "staged": "staged/0000.bin",
            "applied": False,
        }
    ]
    journal: dict[str, object] = {
        "journal_version": JOURNAL_VERSION,
        "operation_id": op_id,
        "kind": OperationKind.CREATE.value,
        "phase": OperationPhase.PREPARED.value,
        "root_state": "registered",
        "observations": [],
        "mutations": mutations,
        "remove_empty_directories": [],
        "metadata": {},
    }
    journal_path = journal_dir / "journal.json"
    journal_path.write_text(json_module.dumps(journal), encoding="utf-8")
    return journal_path


@pytest.mark.parametrize("extra_sub", ["originals", "staged"])
def test_clean_recovery_copies_global_precheck_preserves_legal_copies(
    operation_draft: Path, extra_sub: str
) -> None:
    """任一目录含未知成员：全局预检在第一次 unlink 前失败，两边合法副本都保留（回归 M1）。"""
    from draft.operations import clean_recovery_copies, read_journal

    root = operation_draft
    op_id = f"{'e' * 32}"
    journal_dir = root / ".draft/operations" / op_id
    journal_path = _write_journal_with_originals_and_staged(root, op_id, extra_sub=extra_sub)

    with pytest.raises(DraftError, match=CheckCode.OPERATION_INCONSISTENT_STATE.value):
        clean_recovery_copies(read_journal(journal_path))

    assert (journal_dir / "originals" / "0000.bin").read_bytes() == b"original"
    assert (journal_dir / "staged" / "0000.bin").read_bytes() == b"staged0"
    assert (journal_dir / extra_sub / "extra.txt").read_bytes() == b"extra"


def test_no_lock_allocating_with_unknown_content_is_inconsistent(
    operation_draft: Path,
) -> None:
    """无锁 allocating 操作目录含未知内容：不得递归删除，报 inconsistent_state（回归 H5）。"""
    from draft.operations import recover

    root = operation_draft
    op_id = "c" * 32
    journal_dir = root / ".draft/operations" / op_id
    write_journal(root, op_id, "allocating")
    unknown = journal_dir / "unknown.txt"
    unknown.write_text("unknown\n", encoding="utf-8")

    with pytest.raises(DraftError, match=CheckCode.OPERATION_INCONSISTENT_STATE.value):
        recover(root)

    assert unknown.is_file()


# ---------------------------------------------------------------------------
# 第八轮 H1/H2：严格耐久发布状态机（确定名 pending + 不吞同步失败）
# ---------------------------------------------------------------------------


def _valid_journal_bytes(op_id: str, phase: str = "allocating") -> bytes:
    """构造 _parse_journal 接受的最小合法日志 bytes（operation_id 与目录名匹配）。"""
    import json as json_module

    payload: dict[str, object] = {
        "journal_version": JOURNAL_VERSION,
        "operation_id": op_id,
        "kind": OperationKind.CREATE.value,
        "phase": phase,
        "root_state": "registered",
        "observations": [],
        "mutations": [],
        "remove_empty_directories": [],
        "metadata": {},
    }
    return (json_module.dumps(payload) + "\n").encode("utf-8")


def _fail_fsync_at(monkeypatch: pytest.MonkeyPatch, fail_at: frozenset[int]) -> None:
    """让第 N 次 os.fsync 抛 OSError（file fsync 1 / pending dir 2 / final dir 3）。"""
    import os as os_module

    real_fsync = os_module.fsync
    calls = {"n": 0}

    def flaky_fsync(fd: int) -> None:
        calls["n"] += 1
        if calls["n"] in fail_at:
            raise OSError(f"injected fsync failure #{calls['n']}")
        real_fsync(fd)

    monkeypatch.setattr(os_module, "fsync", flaky_fsync)


def test_pending_decision_matrix() -> None:
    """恢复期 pending 收敛决策表：publish/delete/ignore/external（回归 H2）。"""
    from draft.observations import EntryKind
    from draft.operations import _pending_decision  # pyright: ignore[reportPrivateUsage]

    F, M, D, S = (
        EntryKind.FILE,
        EntryKind.MISSING,
        EntryKind.DIRECTORY,
        EntryKind.SYMLINK,
    )
    expected = "a" * 64
    other = "0" * 64

    # 目标缺失 + pending 授权精确 → 发布 pending。
    assert (
        _pending_decision(
            target_kind=M,
            pending_kind=F,
            target_digest=None,
            pending_digest=expected,
            expected_digest=expected,
        )
        == "publish"
    )
    # 目标与 pending 都是授权精确 → pending 已消费，只删 pending。
    assert (
        _pending_decision(
            target_kind=F,
            pending_kind=F,
            target_digest=expected,
            pending_digest=expected,
            expected_digest=expected,
        )
        == "delete"
    )
    # 目标已是授权精确且无 pending → 已发布。
    assert (
        _pending_decision(
            target_kind=F,
            pending_kind=M,
            target_digest=expected,
            pending_digest=None,
            expected_digest=expected,
        )
        == "ignore"
    )
    # 目标与 pending 都缺失 → 无可收敛。
    assert (
        _pending_decision(
            target_kind=M,
            pending_kind=M,
            target_digest=None,
            pending_digest=None,
            expected_digest=expected,
        )
        == "ignore"
    )
    # pending 摘要不符 / 类型错误（目录、符号链接）→ external，保留现场。
    assert (
        _pending_decision(
            target_kind=M,
            pending_kind=F,
            target_digest=None,
            pending_digest=other,
            expected_digest=expected,
        )
        == "external"
    )
    assert (
        _pending_decision(
            target_kind=M,
            pending_kind=D,
            target_digest=None,
            pending_digest=None,
            expected_digest=expected,
        )
        == "external"
    )
    assert (
        _pending_decision(
            target_kind=M,
            pending_kind=S,
            target_digest=None,
            pending_digest=None,
            expected_digest=expected,
        )
        == "external"
    )
    # 目标被外部替换（摘要不符）→ external，不得删除/覆盖。
    assert (
        _pending_decision(
            target_kind=F,
            pending_kind=F,
            target_digest=other,
            pending_digest=expected,
            expected_digest=expected,
        )
        == "external"
    )
    assert (
        _pending_decision(
            target_kind=F,
            pending_kind=M,
            target_digest=other,
            pending_digest=None,
            expected_digest=expected,
        )
        == "external"
    )
    # 目标类型错误（目录）+ 无 pending → external。
    assert (
        _pending_decision(
            target_kind=D,
            pending_kind=M,
            target_digest=None,
            pending_digest=None,
            expected_digest=expected,
        )
        == "external"
    )


def test_durable_write_file_fsync_failure_leaves_pending_scene(
    operation_draft: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """文件 fsync 失败：报 durability_failed，目标未发布，pending 保留（回归 H1）。"""
    from draft.operations import durable_write

    target = operation_draft / ".draft/operations/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/journal.json"
    target.parent.mkdir(parents=True)
    _fail_fsync_at(monkeypatch, frozenset({1}))
    with pytest.raises(DraftError, match=CheckCode.OPERATION_DURABILITY_FAILED.value):
        durable_write(target, _valid_journal_bytes("a" * 32))
    monkeypatch.undo()
    assert not target.exists()
    assert (target.parent / "journal.json.pending").is_file()


def test_durable_write_pending_dir_fsync_failure_then_resolve_converges(
    operation_draft: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """pending 目录项 fsync 失败（已同步、发布前）：报错保留 pending，随后收敛（回归 H1/H2）。"""
    from draft.operations import durable_write, read_journal, resolve_journal_pending

    target = operation_draft / ".draft/operations/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb/journal.json"
    target.parent.mkdir(parents=True)
    _fail_fsync_at(monkeypatch, frozenset({2}))
    with pytest.raises(DraftError, match=CheckCode.OPERATION_DURABILITY_FAILED.value):
        durable_write(target, _valid_journal_bytes("b" * 32))
    monkeypatch.undo()
    assert not target.exists()
    pending = target.parent / "journal.json.pending"
    assert pending.is_file()
    assert pending.read_bytes() == _valid_journal_bytes("b" * 32)
    # 恢复：发布有效 pending 后日志可读。
    resolve_journal_pending(target)
    assert target.is_file()
    assert read_journal(target).operation_id == "b" * 32
    assert not pending.exists()


def test_durable_write_final_dir_fsync_failure_reports_not_durable(
    operation_draft: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """最终目录 fsync 失败：发布已发生但报 durability_failed，调用方不得视为耐久（回归 H1）。

    create-only 发布的 fsync 序为 file(1)/pending-dir(2)/after-link(3)/after-unlink(4)，
    最终目录同步是第 4 次。
    """
    from draft.operations import durable_write

    target = operation_draft / ".draft/operations/cccccccccccccccccccccccccccccccc/journal.json"
    target.parent.mkdir(parents=True)
    _fail_fsync_at(monkeypatch, frozenset({4}))
    with pytest.raises(DraftError, match=CheckCode.OPERATION_DURABILITY_FAILED.value):
        durable_write(target, _valid_journal_bytes("c" * 32))
    monkeypatch.undo()
    assert target.is_file()
    assert target.read_bytes() == _valid_journal_bytes("c" * 32)
    assert not (target.parent / "journal.json.pending").exists()


def test_resolve_journal_pending_invalid_preserves_scene(operation_draft: Path) -> None:
    """非法 pending：报 journal_corrupt，保留现场，绝不按文件名删除（回归 H2）。"""
    from draft.operations import resolve_journal_pending

    target = operation_draft / ".draft/operations/eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee/journal.json"
    target.parent.mkdir(parents=True)
    pending = target.parent / "journal.json.pending"
    pending.write_bytes(b'{"journal_version": 3, "partial')
    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        resolve_journal_pending(target)
    assert pending.is_file()
    assert not target.exists()


def test_recover_publishes_bootstrap_pending_and_cleans_allocating(
    operation_draft: Path,
) -> None:
    """仅剩崩溃遗留的 bootstrap pending：recover 发布后按 allocating 放弃并清理目录（回归 H2）。"""
    from draft.operations import recover

    root = operation_draft
    op_id = "0" * 32
    journal_dir = root / ".draft/operations" / op_id
    journal_dir.mkdir(parents=True)
    (journal_dir / "journal.json.pending").write_bytes(_valid_journal_bytes(op_id, "allocating"))

    recover(root)

    assert not journal_dir.exists()


def test_clean_recovery_copies_converges_with_copy_pending(
    operation_draft: Path,
) -> None:
    """登记叶缺失但 .pending 为授权内容：发布后清理收敛，不把 pending 当未知成员（回归 H2）。"""
    from draft.operations import clean_recovery_copies, read_journal

    root = operation_draft
    op_id = "d" * 32
    journal_path = _write_journal_with_originals_and_staged(root, op_id, extra_sub=None)
    journal_dir = root / ".draft/operations" / op_id
    # 模拟崩溃：original 叶未发布，仅剩已同步 pending。
    (journal_dir / "originals" / "0000.bin").unlink()
    (journal_dir / "originals" / "0000.bin.pending").write_bytes(b"original")

    clean_recovery_copies(read_journal(journal_path))

    assert not (journal_dir / "originals").exists()
    assert not (journal_dir / "staged").exists()


def test_subprocess_kill_leaves_synced_pending_then_resolve_converges(
    operation_draft: Path,
) -> None:
    """真实子进程在发布前被 SIGKILL：已同步 pending 保留，新进程 resolve 后收敛（回归 H2）。"""
    import signal
    import subprocess
    import sys

    root = operation_draft
    op_id = "f" * 32
    journal_dir = root / ".draft/operations" / op_id
    journal_dir.mkdir(parents=True)
    target = journal_dir / "journal.json"
    payload = _valid_journal_bytes(op_id)

    script = (
        "import os, signal, sys\n"
        "from pathlib import Path\n"
        # create-only 发布在 link（no-clobber 发布点）处崩溃：pending 已 fsync。
        "os.link = lambda *a, **k: os.kill(os.getpid(), signal.SIGKILL)\n"
        "from draft.operations import durable_write\n"
        f"durable_write(Path({str(target)!r}), {payload!r})\n"
    )
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True, timeout=60)
    assert proc.returncode == -signal.SIGKILL

    # 崩溃现场：目标未发布、已同步 pending 保留。
    assert not target.exists()
    pending = journal_dir / "journal.json.pending"
    assert pending.is_file()
    assert pending.read_bytes() == payload

    # 新进程收敛：发布 pending 后日志可读，pending 已消费。
    from draft.operations import resolve_journal_pending

    resolve_journal_pending(target)
    assert read_journal(target).operation_id == op_id
    assert not pending.exists()


def test_resolve_journal_pending_with_old_journal_publishes_newer(
    operation_draft: Path,
) -> None:
    """旧 journal 与有效 pending 并存：发布较新的 pending（回归 H2 决策表"完整发布新"）。"""
    from draft.operations import resolve_journal_pending

    op_id = "9" * 32
    journal_dir = operation_draft / ".draft/operations" / op_id
    journal_dir.mkdir(parents=True)
    target = journal_dir / "journal.json"
    target.write_bytes(_valid_journal_bytes(op_id, "prepared"))
    (journal_dir / "journal.json.pending").write_bytes(_valid_journal_bytes(op_id, "applying"))

    resolve_journal_pending(target)

    assert read_journal(target).phase.value == "applying"
    assert not (journal_dir / "journal.json.pending").exists()


def test_resolve_journal_pending_with_old_journal_keeps_old_on_invalid(
    operation_draft: Path,
) -> None:
    """旧 journal 存在 + pending 半写无效：保留旧 journal，不报错（回归 H2 决策表"选择旧"）。"""
    from draft.operations import resolve_journal_pending

    op_id = "8" * 32
    journal_dir = operation_draft / ".draft/operations" / op_id
    journal_dir.mkdir(parents=True)
    target = journal_dir / "journal.json"
    target.write_bytes(_valid_journal_bytes(op_id, "prepared"))
    pending = journal_dir / "journal.json.pending"
    pending.write_bytes(b'{"journal_version": 3, "partial')

    resolve_journal_pending(target)

    assert read_journal(target).phase.value == "prepared"
    # 非法 pending 不删除，留作 checker 可见残留。
    assert pending.is_file()


# ---------------------------------------------------------------------------
# 第八轮 H3/M2：节点隔离删除 + fd 生命周期
# ---------------------------------------------------------------------------


def test_quarantine_decision_matrix() -> None:
    """恢复期 quarantine 收敛决策表：complete/restore/delete_stale/external/ignore（回归 H3）。"""
    from draft.observations import EntryKind
    from draft.operations import _quarantine_decision  # pyright: ignore[reportPrivateUsage]

    F, M, D, S = (
        EntryKind.FILE,
        EntryKind.MISSING,
        EntryKind.DIRECTORY,
        EntryKind.SYMLINK,
    )
    expected = "a" * 64
    other = "0" * 64

    # 目标缺失 + quarantine 授权精确 → 完成删除（隔离已通过验证）。
    assert (
        _quarantine_decision(
            target_kind=M,
            quarantine_kind=F,
            target_digest=None,
            quarantine_digest=expected,
            expected_digest=expected,
        )
        == "complete"
    )
    # 目标与 quarantine 都是授权精确（同一 inode 中途移动）→ 删除 stale quarantine。
    assert (
        _quarantine_decision(
            target_kind=F,
            quarantine_kind=F,
            target_digest=expected,
            quarantine_digest=expected,
            expected_digest=expected,
        )
        == "delete_stale"
    )
    # 目标缺失 + quarantine 摘要不符 → 恢复原名。
    assert (
        _quarantine_decision(
            target_kind=M,
            quarantine_kind=F,
            target_digest=None,
            quarantine_digest=other,
            expected_digest=expected,
        )
        == "restore"
    )
    # quarantine 类型错误 / 目标被替换 → external。
    assert (
        _quarantine_decision(
            target_kind=M,
            quarantine_kind=D,
            target_digest=None,
            quarantine_digest=None,
            expected_digest=expected,
        )
        == "external"
    )
    assert (
        _quarantine_decision(
            target_kind=M,
            quarantine_kind=S,
            target_digest=None,
            quarantine_digest=None,
            expected_digest=expected,
        )
        == "external"
    )
    assert (
        _quarantine_decision(
            target_kind=F,
            quarantine_kind=F,
            target_digest=other,
            quarantine_digest=expected,
            expected_digest=expected,
        )
        == "external"
    )
    # 无 quarantine → ignore。
    assert (
        _quarantine_decision(
            target_kind=F,
            quarantine_kind=M,
            target_digest=expected,
            quarantine_digest=None,
            expected_digest=expected,
        )
        == "ignore"
    )
    assert (
        _quarantine_decision(
            target_kind=M,
            quarantine_kind=M,
            target_digest=None,
            quarantine_digest=None,
            expected_digest=expected,
        )
        == "ignore"
    )


def test_anchored_leaf_does_not_leak_directory_fds(
    operation_draft: Path,
) -> None:
    """anchored_leaf 每层 fd 及时关闭：成功路径多次调用前后 fd 数量不变（回归 M2）。"""
    import os as os_module

    from draft.operations import anchored_leaf

    root = operation_draft
    target = Path("pages/a.md")
    (root / target).write_text("content", encoding="utf-8")

    def open_fd_count() -> int:
        return len(os_module.listdir("/proc/self/fd"))

    before = open_fd_count()
    for _ in range(50):
        with anchored_leaf(root, target, code="test.code", code_path=root) as leaf:
            assert leaf.classify().value == "file"
    assert open_fd_count() == before


def test_anchored_leaf_closes_fds_on_missing_intermediate(
    operation_draft: Path,
) -> None:
    """anchored_leaf 中间父组件缺失：fd 不泄漏（回归 M2）。"""
    import os as os_module

    from draft.operations import anchored_leaf

    root = operation_draft
    target = Path("missing/deep/leaf.md")

    def open_fd_count() -> int:
        return len(os_module.listdir("/proc/self/fd"))

    before = open_fd_count()
    for _ in range(50):
        with anchored_leaf(root, target, code="test.code", code_path=root) as leaf:
            assert leaf.classify().value == "missing"
    assert open_fd_count() == before


def test_clean_recovery_copies_converges_with_quarantine(
    operation_draft: Path,
) -> None:
    """登记叶隔离后崩溃：recover_quarantine 完成删除，清理收敛（回归 H3）。"""
    from draft.operations import clean_recovery_copies, read_journal

    root = operation_draft
    op_id = "a" * 32
    journal_path = _write_journal_with_originals_and_staged(root, op_id, extra_sub=None)
    journal_dir = root / ".draft/operations" / op_id
    # 模拟：originals/0000.bin 已被隔离（删除中断）。
    (journal_dir / "originals" / "0000.bin").unlink()
    (journal_dir / "originals" / "0000.bin.quarantine").write_bytes(b"original")

    clean_recovery_copies(read_journal(journal_path))

    assert not (journal_dir / "originals").exists()
    assert not (journal_dir / "staged").exists()


def test_recover_managed_temp_quarantine_converges(operation_draft: Path) -> None:
    """managed temp 隔离后崩溃：恢复前收敛 quarantine，不误报（回归 H3）。"""
    from draft.operations import (
        _mutation_temporary_path,  # pyright: ignore[reportPrivateUsage]
        _recover_managed_temp_quarantines,  # pyright: ignore[reportPrivateUsage]
        read_journal,
    )

    root = operation_draft
    op_id = "b" * 32
    journal_path = _write_journal_with_originals_and_staged(root, op_id, extra_sub=None)
    journal = read_journal(journal_path)
    temp = _mutation_temporary_path(Path("pages/old-0.md"), op_id, 0)
    quarantine = root / temp.with_name(temp.name + ".quarantine")
    quarantine.write_bytes(b"staged0")

    _recover_managed_temp_quarantines(root, journal)

    assert not quarantine.exists()


def test_clean_owned_temp_artifacts_quarantines_exact_temp(operation_draft: Path) -> None:
    """managed temp 删除走隔离→验证→删除：精确 temp 被隔离删除（回归 H3）。"""
    from draft.operations import (
        _clean_owned_temp_artifacts,  # pyright: ignore[reportPrivateUsage]
        _mutation_temporary_path,  # pyright: ignore[reportPrivateUsage]
        read_journal,
    )

    root = operation_draft
    op_id = "c" * 32
    journal_path = _write_journal_with_originals_and_staged(root, op_id, extra_sub=None)
    journal = read_journal(journal_path)
    temp = _mutation_temporary_path(Path("pages/old-0.md"), op_id, 0)
    (root / temp).write_bytes(b"staged0")

    _clean_owned_temp_artifacts(root, journal, frozenset({temp}))

    assert not (root / temp).exists()


def test_read_journal_non_utf8_is_stable_corrupt(operation_draft: Path) -> None:
    """非 UTF-8 journal：报 operation.journal_corrupt，不泄漏 UnicodeDecodeError（回归 M3）。"""
    from draft.operations import read_journal

    root = operation_draft
    op_id = "1" * 32
    journal_dir = root / ".draft/operations" / op_id
    journal_dir.mkdir(parents=True)
    (journal_dir / "journal.json").write_bytes(b"\xff\xfe\x00 garbage")

    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        read_journal(journal_dir / "journal.json")


def test_read_lock_non_utf8_is_stable_corrupt(operation_draft: Path) -> None:
    """非 UTF-8 lock：read_lock 与 recover 都报 operation.journal_corrupt，不泄漏（回归 M3）。"""
    from draft.operations import read_lock, recover

    root = operation_draft
    (root / ".draft").mkdir(parents=True, exist_ok=True)
    (root / ".draft/operation.lock").write_bytes(b"\xff\xfe\x00 garbage")

    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        read_lock(root)
    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        recover(root)


def test_resolve_journal_pending_rejects_mutation_replaced_successor(
    operation_draft: Path,
) -> None:
    """旧 journal 存在 + pending 可解析但替换 mutation：保留 old+pending，报损坏（回归 H1）。"""
    import json as json_module

    from draft.operations import resolve_journal_pending

    root = operation_draft
    op_id = "7" * 32
    journal_dir = root / ".draft/operations" / op_id
    journal_dir.mkdir(parents=True)
    target = journal_dir / "journal.json"

    def _journal_bytes(phase: str, after: str) -> bytes:
        payload: dict[str, object] = {
            "journal_version": JOURNAL_VERSION,
            "operation_id": op_id,
            "kind": OperationKind.CREATE.value,
            "phase": phase,
            "root_state": "registered",
            "observations": [],
            "mutations": [
                {
                    "path": "pages/a.md",
                    "kind": "write",
                    "before_sha256": None,
                    "after_sha256": after,
                    "original": None,
                    "staged": "staged/0000.bin",
                    "applied": False,
                }
            ],
            "remove_empty_directories": [],
            "metadata": {},
        }
        return (json_module.dumps(payload) + "\n").encode("utf-8")

    old_bytes = _journal_bytes("prepared", "a" * 64)
    pending_bytes = _journal_bytes("applying", "b" * 64)  # 换 mutation 摘要。
    target.write_bytes(old_bytes)
    (journal_dir / "journal.json.pending").write_bytes(pending_bytes)

    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        resolve_journal_pending(target)

    assert target.read_bytes() == old_bytes  # old 字节不变。
    assert (journal_dir / "journal.json.pending").read_bytes() == pending_bytes  # pending 保留。


def test_resolve_journal_pending_rejects_phase_regression(operation_draft: Path) -> None:
    """旧 journal 存在 + pending 阶段倒退：保留 old+pending，报损坏（回归 H1）。"""
    import json as json_module

    from draft.operations import resolve_journal_pending

    root = operation_draft
    op_id = "6" * 32
    journal_dir = root / ".draft/operations" / op_id
    journal_dir.mkdir(parents=True)
    target = journal_dir / "journal.json"

    def _journal_bytes(phase: str) -> bytes:
        payload: dict[str, object] = {
            "journal_version": JOURNAL_VERSION,
            "operation_id": op_id,
            "kind": OperationKind.CREATE.value,
            "phase": phase,
            "root_state": "registered",
            "observations": [],
            "mutations": [],
            "remove_empty_directories": [],
            "metadata": {},
        }
        return (json_module.dumps(payload) + "\n").encode("utf-8")

    old_bytes = _journal_bytes("applying")
    pending_bytes = _journal_bytes("allocating")  # 倒退回分配期。
    target.write_bytes(old_bytes)
    (journal_dir / "journal.json.pending").write_bytes(pending_bytes)

    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        resolve_journal_pending(target)

    assert target.read_bytes() == old_bytes
    assert (journal_dir / "journal.json.pending").read_bytes() == pending_bytes


def test_resolve_journal_pending_rejects_identity_rewrite(operation_draft: Path) -> None:
    """旧 journal 存在 + pending 改写 kind：保留 old+pending，报损坏（回归 H1）。"""
    import json as json_module

    from draft.operations import resolve_journal_pending

    root = operation_draft
    op_id = "5" * 32
    journal_dir = root / ".draft/operations" / op_id
    journal_dir.mkdir(parents=True)
    target = journal_dir / "journal.json"

    def _journal_bytes(kind: str) -> bytes:
        payload: dict[str, object] = {
            "journal_version": JOURNAL_VERSION,
            "operation_id": op_id,
            "kind": kind,
            "phase": "prepared",
            "root_state": "registered",
            "observations": [],
            "mutations": [],
            "remove_empty_directories": [],
            "metadata": {},
        }
        return (json_module.dumps(payload) + "\n").encode("utf-8")

    old_bytes = _journal_bytes("create")
    pending_bytes = _journal_bytes("move")  # kind 改写。
    target.write_bytes(old_bytes)
    (journal_dir / "journal.json.pending").write_bytes(pending_bytes)

    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        resolve_journal_pending(target)

    assert target.read_bytes() == old_bytes
    assert (journal_dir / "journal.json.pending").read_bytes() == pending_bytes
