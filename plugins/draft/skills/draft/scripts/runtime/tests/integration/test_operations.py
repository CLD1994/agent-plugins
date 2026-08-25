import dataclasses
import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from draft.diagnostic_codes import CheckCode
from draft.errors import DraftError
from draft.graph import load_graph, read_marker_snapshot
from draft.models import (
    FileMutation,
    LinkIndexResult,
    MutationKind,
    OperationKind,
    OperationPhase,
    OperationPlan,
    ResultWarning,
    RootState,
)
from draft.observations import EMPTY_OBSERVATIONS, graph_observations
from draft.operations import (
    JOURNAL_VERSION,
    DurabilityStep,
    Journal,
    acquire_lock,
    durability_observer_scope,
    execute_plan,
    operation_journal_path,
    preallocate_journal,
    read_journal,
    read_lock,
    recover,
    sha256_bytes,
)
from draft.operations import (
    write_journal as write_journal_durable,
)
from draft.vocabulary import ConclusionsState
from tests.integration.support import FailingDurability, managed_marker_text, write_raw_journal


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


def _observed_two_write_plan(root: Path) -> tuple[OperationPlan, bytes, bytes, bytes, bytes]:
    """双写计划带真实读集观察：artifact 所有权证明需要计划时缺失证据（回归 H4）。"""
    plan, old_a, old_b, new_a, new_b = two_write_plan(root)
    observed = dataclasses.replace(
        plan, observations=graph_observations(load_graph(root), marker=read_marker_snapshot(root))
    )
    return observed, old_a, old_b, new_a, new_b


def crash_on_nth_replace(nth: int) -> object:
    calls = 0

    def crash(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == nth:
            raise RuntimeError("injected crash")
        source.replace(target)

    return crash


def crash_after_nth_apply(ops_module: Any, nth: int) -> object:
    """第 Nth 次 _apply_write 完成后崩溃（回归 H4 节点事务发布语义）。

    新的受管写不再走 replace_file（临时文件替换），而是节点事务（隔离 before +
    create-only 发布）；"文件已落盘、日志未推进"的崩溃点改为 _apply_write 完成点。
    """
    original = ops_module._apply_write  # pyright: ignore[reportPrivateUsage]
    calls = {"n": 0}

    def crash(
        root: Path,
        target: Path,
        content: bytes,
        *,
        expected_current: str | None,
        code_path: Path,
    ) -> None:
        calls["n"] += 1
        original(root, target, content, expected_current=expected_current, code_path=code_path)
        if calls["n"] == nth:
            raise RuntimeError("injected crash")

    return crash


def write_journal(
    root: Path,
    operation_id: str,
    phase: str,
    *,
    remove_empty_directories: Sequence[str] = (),
) -> Path:
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
        "remove_empty_directories": list(remove_empty_directories),
        "metadata": {},
    }
    (journal_dir / "journal.json").write_text(json.dumps(journal), encoding="utf-8")
    return journal_dir


def write_trash_phase_journal(root: Path, operation_id: str, phase: str) -> Path:
    """写入合法 TRASH kind 的阶段日志（trash 生命周期阶段由 TRASH 独占，回归 M1）。"""
    journal_dir = root / ".draft/operations" / operation_id
    journal_dir.mkdir(parents=True, exist_ok=True)
    journal: dict[str, object] = {
        "journal_version": JOURNAL_VERSION,
        "operation_id": operation_id,
        "kind": OperationKind.TRASH.value,
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
    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_VERSION_UNSUPPORTED.value):
        read_journal(path)


@pytest.mark.parametrize("version", [3, "2"])
def test_non_current_journal_version_is_rejected(operation_draft: Path, version: object) -> None:
    # version=None 由 write_raw_journal 默认取当前权威版本，不再表示损坏。
    path = write_raw_journal(operation_draft, version=version)
    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_VERSION_UNSUPPORTED.value):
        read_journal(path)


@pytest.mark.parametrize(
    ("step", "pending_expected"),
    [
        (DurabilityStep.JOURNAL_WRITE, False),
        (DurabilityStep.JOURNAL_DIRECTORY_FSYNC, True),
    ],
    ids=["journal-write", "journal-directory-fsync"],
)
def test_journal_durability_steps_keep_last_durable_successor(
    operation_draft: Path,
    step: DurabilityStep,
    pending_expected: bool,
) -> None:
    root = operation_draft
    operation_id = "a" * 32
    journal = preallocate_journal(
        root,
        kind=OperationKind.CREATE,
        operation_id=operation_id,
        root_state=RootState.REGISTERED,
        observations=EMPTY_OBSERVATIONS,
        metadata={},
    )
    successor = dataclasses.replace(journal, phase=OperationPhase.PREPARED)

    with (
        durability_observer_scope(FailingDurability({step: 1})),
        pytest.raises(DraftError) as caught,
    ):
        write_journal_durable(successor)

    assert caught.value.code == CheckCode.OPERATION_DURABILITY_FAILED.value
    current = read_journal(operation_journal_path(root, operation_id))
    assert current.phase is OperationPhase.ALLOCATING
    assert (journal.directory / "journal.json.pending").exists() is pending_expected


@pytest.mark.parametrize(
    ("step", "occurrence", "phase"),
    [
        (DurabilityStep.JOURNAL_WRITE, 8, OperationPhase.LINK_INDEX_UPDATING),
        (DurabilityStep.JOURNAL_DIRECTORY_FSYNC, 19, OperationPhase.COMPLETED),
    ],
    ids=["journal-write-after-business", "journal-directory-after-business"],
)
def test_business_after_state_survives_journal_durability_failure(
    operation_draft: Path,
    step: DurabilityStep,
    occurrence: int,
    phase: OperationPhase,
) -> None:
    """日志耐久失败不回滚已验证内容，recover 后同 revision 幂等收敛。"""
    root = operation_draft
    plan, _old_a, _old_b, new_a, new_b = two_write_plan(root)

    with (
        durability_observer_scope(FailingDurability({step: occurrence})),
        pytest.raises(DraftError) as caught,
    ):
        execute_plan(plan)

    assert caught.value.code == CheckCode.OPERATION_DURABILITY_FAILED.value
    assert (root / "pages/a.md").read_bytes() == new_a
    assert (root / "pages/b.md").read_bytes() == new_b
    operation_id = read_lock(root)
    assert operation_id is not None
    journal_before = read_journal(operation_journal_path(root, operation_id))
    assert journal_before.phase is phase
    revision_before = journal_before.link_index_revision

    recover(root)
    journal_after = read_journal(operation_journal_path(root, operation_id))
    assert journal_after.phase is OperationPhase.COMPLETED
    assert journal_after.link_index_revision == revision_before
    assert read_lock(root) is None

    recover(root)
    journal_repeat = read_journal(operation_journal_path(root, operation_id))
    assert journal_repeat.phase is OperationPhase.COMPLETED
    assert journal_repeat.link_index_revision == revision_before
    assert (root / "pages/a.md").read_bytes() == new_a
    assert (root / "pages/b.md").read_bytes() == new_b


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

    import draft.operations as operations_module

    monkeypatch.setattr(
        "draft.operations._apply_write", crash_after_nth_apply(operations_module, 2)
    )
    with pytest.raises(RuntimeError, match="injected crash"):
        execute_plan(plan)

    monkeypatch.undo()
    result = recover(root)

    assert result.phase == "completed"
    assert (root / "pages/a.md").read_bytes() == new_a
    assert (root / "pages/b.md").read_bytes() == new_b
    assert not (root / ".draft/operation.lock").exists()


def test_recover_converges_apply_crash_leftover_quarantine(
    operation_draft: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """改写应用发布前崩溃：before 隔离名遗留，恢复补发 after 并收敛（回归 H4）。"""
    import draft.operations as operations_module

    root = operation_draft
    plan, _old_a, old_b, new_a, new_b = _observed_two_write_plan(root)
    original_write = operations_module._AnchoredLeaf.write  # pyright: ignore[reportPrivateUsage]
    calls = {"n": 0}

    def crash_on_second_write(self: Any, content: bytes) -> None:
        calls["n"] += 1
        if calls["n"] == 2:  # b.md 的 create-only 发布：a.md 已完成、b.md 隔离名遗留。
            raise RuntimeError("injected crash during publish")
        original_write(self, content)  # type: ignore[arg-type]

    monkeypatch.setattr(
        operations_module._AnchoredLeaf,  # pyright: ignore[reportPrivateUsage]
        "write",
        crash_on_second_write,
    )
    with pytest.raises(RuntimeError, match="injected crash during publish"):
        execute_plan(plan)
    monkeypatch.undo()

    # b.md 的 before 隔离名遗留、目标缺失；a.md 已完成。
    assert not (root / "pages/b.md").exists()
    assert (root / "pages/b.md.quarantine").read_bytes() == old_b
    assert (root / "pages/a.md").read_bytes() == new_a  # a.md 已是 after。

    result = recover(root)

    assert result.phase == "completed"
    assert (root / "pages/b.md").read_bytes() == new_b
    assert not (root / "pages/b.md.quarantine").exists()
    assert not (root / ".draft/operation.lock").exists()


def test_recover_converges_exact_pending_at_after_target(
    operation_draft: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """target=after 且共存精确 pending（发布 link 后、消费 pending 前崩溃）：收敛消费。"""
    import draft.operations as operations_module

    root = operation_draft
    plan, _old_a, _old_b, new_a, _new_b = _observed_two_write_plan(root)
    monkeypatch.setattr(
        "draft.operations._apply_write",
        crash_after_nth_apply(operations_module, 1),
    )
    with pytest.raises(RuntimeError, match="injected crash"):
        execute_plan(plan)
    monkeypatch.undo()
    assert (root / "pages/a.md").read_bytes() == new_a

    # 注入崩溃窗口内的精确 pending：target=after + pending=after → 删除 pending 收敛。
    (root / "pages/a.md.pending").write_bytes(new_a)

    result = recover(root)

    assert result.phase == "completed"
    assert (root / "pages/a.md").read_bytes() == new_a
    assert not (root / "pages/a.md.pending").exists()
    assert not (root / ".draft/operation.lock").exists()


def test_recover_rejects_foreign_pending_content(
    operation_draft: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """target=after 却存在摘要不符的 pending：外部篡改，判 external_change 并保留（回归 H4）。"""
    import draft.operations as operations_module

    root = operation_draft
    plan, _old_a, _old_b, new_a, _new_b = two_write_plan(root)
    monkeypatch.setattr(
        "draft.operations._apply_write",
        crash_after_nth_apply(operations_module, 1),
    )
    with pytest.raises(RuntimeError, match="injected crash"):
        execute_plan(plan)
    monkeypatch.undo()
    assert (root / "pages/a.md").read_bytes() == new_a

    foreign = root / "pages/a.md.pending"
    foreign.write_bytes(b"tampered")

    with pytest.raises(DraftError, match="operation.external_change"):
        recover(root)

    assert (root / "pages/a.md").read_bytes() == new_a  # after 目标保留。
    assert foreign.read_bytes() == b"tampered"  # 现场保留。
    assert (root / ".draft/operation.lock").exists()


def test_recover_never_consumes_preexisting_pending_artifact(
    operation_draft: Path,
) -> None:
    """计划前既有的 {目标}.pending（即使内容恰好等于 after）不得被消费或发布（回归 H4）。

    所有权证据是计划时缺失观察，不是确定性名字 + 匹配摘要。命令先安全失败，recover
    必须保留既有 pending，不得把它发布成目标或删除。
    """
    root = operation_draft
    new_bytes = b"# New\n\nfresh page\n"
    pending = root / "pages/new.md.pending"
    pending.write_bytes(new_bytes)  # 计划前既有：观察清单会把 pending 列为目录成员。
    plan = OperationPlan(
        kind=OperationKind.CREATE,
        root=root,
        root_state=RootState.REGISTERED,
        observations=graph_observations(load_graph(root), marker=read_marker_snapshot(root)),
        mutations=(
            FileMutation(
                Path("pages/new.md"),
                MutationKind.WRITE,
                None,
                sha256_bytes(new_bytes),
                new_bytes,
            ),
        ),
    )
    with pytest.raises(DraftError, match="operation.external_change"):
        execute_plan(plan)
    with pytest.raises(DraftError, match="operation.external_change"):
        recover(root)
    assert pending.read_bytes() == new_bytes  # 原路径、类型、字节完全不变。
    assert not (root / "pages/new.md").exists()
    assert (root / ".draft/operation.lock").exists()


def test_recover_preserves_preexisting_quarantine_artifact(
    operation_draft: Path,
) -> None:
    """计划前既有的 {目标}.quarantine（摘要等于授权 before）不得被消费（回归 H4）。"""
    root = operation_draft
    old_a = (root / "pages/a.md").read_bytes()
    new_a = old_a + b"\nforward-a\n"
    quarantine = root / "pages/a.md.quarantine"
    quarantine.write_bytes(old_a)  # 计划前既有：观察把 quarantine 列为目录成员。
    plan = OperationPlan(
        kind=OperationKind.CREATE,
        root=root,
        root_state=RootState.REGISTERED,
        observations=graph_observations(load_graph(root), marker=read_marker_snapshot(root)),
        mutations=(
            FileMutation(
                Path("pages/a.md"),
                MutationKind.WRITE,
                sha256_bytes(old_a),
                sha256_bytes(new_a),
                new_a,
            ),
        ),
    )
    with pytest.raises(DraftError, match="operation.external_change"):
        execute_plan(plan)
    with pytest.raises(DraftError, match="operation.external_change"):
        recover(root)
    assert quarantine.read_bytes() == old_a  # 既有 quarantine 原样保留。
    assert (root / "pages/a.md").read_bytes() == old_a
    assert (root / ".draft/operation.lock").exists()


def _write_rolling_back_rewrite_journal(
    root: Path, op_id: str, *, before: bytes, after: bytes
) -> Path:
    """写入回滚意图已耐久、rewrite WRITE 的 ROLLING_BACK 日志（含 originals/staged）。"""
    import json as json_module

    journal_dir = root / ".draft/operations" / op_id
    (journal_dir / "originals").mkdir(parents=True)
    (journal_dir / "staged").mkdir()
    (journal_dir / "originals" / "0000.bin").write_bytes(before)
    (journal_dir / "staged" / "0000.bin").write_bytes(after)
    mutations: list[dict[str, object]] = [
        {
            "path": "pages/a.md",
            "kind": "write",
            "before_sha256": sha256_bytes(before),
            "after_sha256": sha256_bytes(after),
            "original": "originals/0000.bin",
            "staged": "staged/0000.bin",
            "applied": True,
        }
    ]
    journal: dict[str, object] = {
        "journal_version": JOURNAL_VERSION,
        "operation_id": op_id,
        "kind": OperationKind.CREATE.value,
        "phase": OperationPhase.ROLLING_BACK.value,
        "root_state": "registered",
        "observations": _graph_observations_payload(root),
        "mutations": mutations,
        "remove_empty_directories": [],
        "metadata": {},
    }
    journal_path = journal_dir / "journal.json"
    journal_path.write_text(json_module.dumps(journal), encoding="utf-8")
    return journal_path


def test_recover_rolls_back_interrupted_rewrite_from_artifacts(
    operation_draft: Path,
) -> None:
    """回滚改写中断（quarantine=after、pending=before、target=missing）：方向感知收敛（回归 H3）。

    正向与回滚共用 .pending/.quarantine 后缀但代表不同授权摘要；ROLLING_BACK 日志的
    恢复必须按回滚方向把 pending 发布为 before、消费 quarantine（after），不得误报外部
    变化，也不得把回滚 artifact 前向完成。
    """
    import draft.operations as ops_module
    from draft.operations import acquire_lock, read_lock

    root = operation_draft
    old_a = (root / "pages/a.md").read_bytes()
    new_a = old_a + b"\nrollback-a\n"
    op_id = "f" * 32
    _write_rolling_back_rewrite_journal(root, op_id, before=old_a, after=new_a)
    acquire_lock(root, op_id)
    # 构造回滚中断现场：after 已隔离、before 已写 pending、目标缺失。
    (root / "pages/a.md").unlink()
    (root / "pages/a.md.quarantine").write_bytes(new_a)
    (root / "pages/a.md.pending").write_bytes(old_a)

    result = ops_module.recover(root)

    assert result.phase == OperationPhase.ROLLED_BACK
    assert (root / "pages/a.md").read_bytes() == old_a  # before 恢复。
    assert not (root / "pages/a.md.quarantine").exists()
    assert not (root / "pages/a.md.pending").exists()
    assert read_lock(root) is None


def test_recover_forwards_interrupted_rewrite_from_artifacts(
    operation_draft: Path,
) -> None:
    """正向改写中断（quarantine=before、pending=after、target=missing）：前向收敛（回归 H3）。"""
    import draft.operations as ops_module
    from draft.operations import acquire_lock, read_lock

    root = operation_draft
    old_a = (root / "pages/a.md").read_bytes()
    new_a = old_a + b"\nforward-a\n"
    op_id = "e" * 32
    _write_prepared_rewrite_journal(root, op_id, before=old_a, after=new_a)
    acquire_lock(root, op_id)
    # 构造正向中断现场：before 已隔离、after 已写 pending、目标缺失。
    (root / "pages/a.md").unlink()
    (root / "pages/a.md.quarantine").write_bytes(old_a)
    (root / "pages/a.md.pending").write_bytes(new_a)

    result = ops_module.recover(root)

    assert result.phase == OperationPhase.COMPLETED
    assert (root / "pages/a.md").read_bytes() == new_a  # after 发布。
    assert not (root / "pages/a.md.quarantine").exists()
    assert not (root / "pages/a.md.pending").exists()
    assert read_lock(root) is None


def _write_prepared_rewrite_journal(root: Path, op_id: str, *, before: bytes, after: bytes) -> Path:
    """写入 PREPARED、rewrite WRITE 的日志（含 originals/staged）；用于正向中断现场。"""
    import json as json_module

    journal_dir = root / ".draft/operations" / op_id
    (journal_dir / "originals").mkdir(parents=True)
    (journal_dir / "staged").mkdir()
    (journal_dir / "originals" / "0000.bin").write_bytes(before)
    (journal_dir / "staged" / "0000.bin").write_bytes(after)
    mutations: list[dict[str, object]] = [
        {
            "path": "pages/a.md",
            "kind": "write",
            "before_sha256": sha256_bytes(before),
            "after_sha256": sha256_bytes(after),
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
        "observations": _graph_observations_payload(root),
        "mutations": mutations,
        "remove_empty_directories": [],
        "metadata": {},
    }
    journal_path = journal_dir / "journal.json"
    journal_path.write_text(json_module.dumps(journal), encoding="utf-8")
    return journal_path


def test_recover_stops_on_external_edit(
    operation_draft: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = operation_draft
    plan, _old_a, _old_b, _new_a, _new_b = two_write_plan(root)

    import draft.operations as operations_module

    monkeypatch.setattr(
        "draft.operations._apply_write", crash_after_nth_apply(operations_module, 2)
    )
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
    write_trash_phase_journal(root, "e" * 32, "trash_handoff_succeeded")

    with pytest.raises(DraftError, match=CheckCode.OPERATION_INCONSISTENT_STATE.value):
        recover(root)


def test_no_lock_terminal_and_pending_journals_are_accepted(operation_draft: Path) -> None:
    root = operation_draft
    completed = write_journal(root, "f" * 32, "completed")
    rolled_back = write_journal(root, "0" * 32, "rolled_back")
    pending = write_trash_phase_journal(root, "1" * 32, "trash_handoff_pending")

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

    import draft.operations as operations_module

    # 第 1 次 apply 后崩溃：a.md 已应用、b.md 仍 before，staged 缺失时才可回滚。
    monkeypatch.setattr(
        "draft.operations._apply_write", crash_after_nth_apply(operations_module, 1)
    )
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


# ---------------------------------------------------------------------------
# 通用业务 operation 的链接索引阶段（spec §5/§10/§11）
# ---------------------------------------------------------------------------

_LINK_INDEX_PHASES = frozenset(
    {
        OperationPhase.LINK_INDEX_UPDATING,
        OperationPhase.LINK_INDEX_PUBLISHED,
        OperationPhase.LINK_INDEX_REBUILD_REQUIRED,
    }
)


def test_write_operation_publishes_link_index_before_completed(
    operation_draft: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = operation_draft
    plan, _old_a, _old_b, new_a, new_b = two_write_plan(root)
    phases: list[OperationPhase] = []
    original_write = write_journal_durable
    from draft.operations import Journal as OperationsJournal

    def spy(journal: OperationsJournal) -> object:
        if journal.phase in _LINK_INDEX_PHASES:
            # 索引阶段期间锁与恢复材料仍存在，终态前不释放。
            assert read_lock(root) == journal.operation_id
            op_dir = journal.directory
            assert (op_dir / "originals/0000.bin").exists()
            assert (op_dir / "staged/0000.bin").exists()
        phases.append(journal.phase)
        return original_write(journal)

    monkeypatch.setattr("draft.operations.write_journal", spy)
    result = execute_plan(plan)

    assert result.phase is OperationPhase.COMPLETED
    assert phases[-1] is OperationPhase.COMPLETED
    index_phases = [phase for phase in phases if phase in _LINK_INDEX_PHASES]
    assert index_phases == [
        OperationPhase.LINK_INDEX_UPDATING,
        OperationPhase.LINK_INDEX_PUBLISHED,
    ]
    journal = read_journal(operation_journal_path(root, result.operation_id))
    assert journal.link_index_result is LinkIndexResult.PUBLISHED
    assert journal.link_index_revision == "1"
    assert journal.link_index_sha256 is not None
    artifact = root / ".draft/link-index.json"
    assert artifact.is_file()
    assert sha256_bytes(artifact.read_bytes()) == journal.link_index_sha256
    assert result.warnings == ()
    # 业务内容保留且索引已发布。
    assert (root / "pages/a.md").read_bytes() == new_a
    assert (root / "pages/b.md").read_bytes() == new_b


def test_index_build_failure_records_rebuild_required_and_completes(
    operation_draft: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = operation_draft
    plan, _old_a, _old_b, new_a, new_b = two_write_plan(root)
    import draft.link_index_maintenance as maintenance

    def fail_scan(_root: Path, baseline: object = None, known_documents: object = ()) -> object:
        raise DraftError("operation.index_update_failed", _root)

    monkeypatch.setattr(maintenance, "scan_link_index", fail_scan)
    result = execute_plan(plan)

    assert result.phase is OperationPhase.COMPLETED
    journal = read_journal(operation_journal_path(root, result.operation_id))
    assert journal.link_index_result is LinkIndexResult.REBUILD_REQUIRED
    assert journal.link_index_revision == "1"
    assert journal.link_index_sha256 is None
    assert (root / ".draft/link-index-rebuild-required.flag").exists()
    assert result.warnings == (ResultWarning("link_index.rebuild_required"),)
    # 业务内容保留：索引失败不得触发业务 rollback（spec §11）。
    assert (root / "pages/a.md").read_bytes() == new_a
    assert (root / "pages/b.md").read_bytes() == new_b
    assert not (root / ".draft/operation.lock").exists()
    # 索引失败不会进入 rolling_back。
    rolled_back = [
        path
        for path in (root / ".draft/operations").glob("*/journal.json")
        if json.loads(path.read_text(encoding="utf-8"))["phase"] == "rolled_back"
    ]
    assert rolled_back == []


def test_index_journal_write_failure_leaves_recovery_pending(
    operation_draft: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = operation_draft
    plan, _old_a, _old_b, new_a, new_b = two_write_plan(root)
    original_write = write_journal_durable
    from draft.operations import Journal as OperationsJournal

    def fail_index_result_write(journal: OperationsJournal) -> object:
        if journal.phase is OperationPhase.LINK_INDEX_PUBLISHED:
            raise DraftError(CheckCode.OPERATION_DURABILITY_FAILED.value, journal.directory)
        return original_write(journal)

    monkeypatch.setattr("draft.operations.write_journal", fail_index_result_write)
    with pytest.raises(DraftError) as caught:
        execute_plan(plan)

    assert caught.value.code == CheckCode.OPERATION_DURABILITY_FAILED.value
    # 停在 recovery pending：锁保留，journal 停在 link_index_updating。
    operation_id = read_lock(root)
    assert operation_id is not None
    journal = read_journal(operation_journal_path(root, operation_id))
    assert journal.phase is OperationPhase.LINK_INDEX_UPDATING
    # 业务 after 内容保留。
    assert (root / "pages/a.md").read_bytes() == new_a
    assert (root / "pages/b.md").read_bytes() == new_b


def test_mutation_without_link_index_facts_skips_index_phase(
    operation_draft: Path,
) -> None:
    """没有 mutation/目录事实变化的 operation 不进入索引阶段（spec §6）。"""
    root = operation_draft
    plan, _old_a, _old_b, _new_a, _new_b = two_write_plan(root)
    adopt_plan = dataclasses.replace(
        plan,
        kind=OperationKind.ADOPT,
        mutations=(),
        remove_empty_directories=(),
    )
    result = execute_plan(adopt_plan)

    assert result.phase is OperationPhase.COMPLETED
    journal = read_journal(operation_journal_path(root, result.operation_id))
    assert journal.link_index_result is None
    assert journal.link_index_revision is None
    assert not (root / ".draft/link-index.json").exists()


def test_recover_after_published_terminal_does_not_delete_retained_directory(
    operation_draft: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """携带索引结果的终态：恢复不得再执行计划内空目录清理（spec §6）。

    复合场景（每步单独合法）：普通 I/O 失败保留目录 → 索引按实际状态发布 →
    completed 写完后崩溃于 release_lock 前 → 公开 recover 进入终态恢复。目录此时
    已是已发布索引的事实，再删除即产生索引发布后的未记录目录成员变化。
    """
    root = operation_draft
    (root / "pages/old").mkdir()
    plan, _old_a, _old_b, _new_a, _new_b = two_write_plan(root)
    plan = dataclasses.replace(plan, remove_empty_directories=(Path("pages/old"),))
    target = root / "pages/old"
    original_rmdir = Path.rmdir

    def fail_target_rmdir(self: Path) -> None:
        if self == target:
            raise PermissionError(13, "injected io failure", str(self))
        original_rmdir(self)

    monkeypatch.setattr(Path, "rmdir", fail_target_rmdir)
    result = execute_plan(plan)
    monkeypatch.undo()

    # 普通 I/O 失败保留目录；索引按实际状态发布并携带该目录事实。
    assert result.phase is OperationPhase.COMPLETED
    assert target.exists()
    journal = read_journal(operation_journal_path(root, result.operation_id))
    assert journal.link_index_result is LinkIndexResult.PUBLISHED
    artifact_before = sha256_bytes((root / ".draft/link-index.json").read_bytes())

    # 崩溃窗口：终态日志已耐久但锁未释放，公开 recover 走终态恢复。
    acquire_lock(root, result.operation_id)
    recover(root)

    # 恢复不删已进入索引事实的目录；索引制品不变且对严格扫描仍然 fresh。
    assert target.exists()
    assert sha256_bytes((root / ".draft/link-index.json").read_bytes()) == artifact_before
    from draft.link_index_maintenance import load_link_index_baseline, scan_link_index

    baseline = load_link_index_baseline(root)
    assert baseline.index is not None
    assert scan_link_index(root, baseline).changed is False
    assert read_lock(root) is None


def test_recover_without_link_index_result_still_cleans_planned_directories(
    operation_draft: Path,
) -> None:
    """无索引字段的终态保持既有恢复行为：恢复时清理残留计划内空目录。

    该状态无法经 execute_plan 构造（只要 remove_empty_directories 非空，
    operation_changes_link_index 必为 True，会进入索引阶段）；用遗留样式手写
    journal（不写 link_index 字段，解析为 None）直接驱动 `_recover_terminal`。
    """
    root = operation_draft
    (root / "pages/old").mkdir()
    op_id = "9" * 32
    write_journal(
        root,
        op_id,
        "completed",
        remove_empty_directories=("pages/old",),
    )
    target = root / "pages/old"

    acquire_lock(root, op_id)
    recover(root)

    # 无链接索引结果：恢复侧既有清理行为不变。
    assert not target.exists()
    assert read_lock(root) is None


# ---------------------------------------------------------------------------
# 链接索引 phase 的恢复（spec §12）：公开 recover 前向收敛三个索引 phase。
# ---------------------------------------------------------------------------


def crash_after_link_index_write(monkeypatch: pytest.MonkeyPatch, phase: OperationPhase) -> None:
    """真实 writer 耐久写入目标索引 phase 后抛进程退出；只命中该 phase 的首次写入。"""
    import draft.operations as operations_module

    real_write = operations_module.write_journal
    fired = False

    def write_then_crash(journal: Journal) -> None:
        nonlocal fired
        real_write(journal)
        if not fired and journal.phase is phase:
            fired = True
            raise RuntimeError("injected crash")

    monkeypatch.setattr(operations_module, "write_journal", write_then_crash)


def crash_before_link_index_write(monkeypatch: pytest.MonkeyPatch, phase: OperationPhase) -> None:
    """在目标索引 phase 的 journal 耐久写入前抛进程退出（副作用已发生、日志未推进）。"""
    import draft.operations as operations_module

    real_write = operations_module.write_journal

    def crash_before(journal: Journal) -> None:
        if journal.phase is phase:
            raise RuntimeError("injected crash before journal write")
        return real_write(journal)

    monkeypatch.setattr(operations_module, "write_journal", crash_before)


def _persist_interrupted_operation(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: OperationPhase,
) -> str:
    """建立业务 operation 耐久停在目标索引 phase 的真实现场，返回 operation_id。"""
    plan, _old_a, _old_b, _new_a, _new_b = two_write_plan(root)
    if phase is OperationPhase.LINK_INDEX_REBUILD_REQUIRED:
        import draft.link_index_maintenance as maintenance

        def fail_scan(_root: Path, baseline: object = None, known_documents: object = ()) -> object:
            raise DraftError("operation.index_update_failed", _root)

        monkeypatch.setattr(maintenance, "scan_link_index", fail_scan)
    crash_after_link_index_write(monkeypatch, phase)
    with pytest.raises(RuntimeError, match="injected crash"):
        execute_plan(plan)
    monkeypatch.undo()
    operation_id = read_lock(root)
    assert operation_id is not None
    return operation_id


@pytest.mark.parametrize(
    ("phase", "expected"),
    [
        (OperationPhase.LINK_INDEX_UPDATING, LinkIndexResult.REBUILD_REQUIRED),
        (OperationPhase.LINK_INDEX_PUBLISHED, LinkIndexResult.PUBLISHED),
        (
            OperationPhase.LINK_INDEX_REBUILD_REQUIRED,
            LinkIndexResult.REBUILD_REQUIRED,
        ),
    ],
)
def test_recover_link_index_phase(
    operation_draft: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: OperationPhase,
    expected: LinkIndexResult,
) -> None:
    """每个索引 phase 崩溃：公开 recover 按原 kind 收敛终态并保留 revision（spec §12）。"""
    root = operation_draft
    operation_id = _persist_interrupted_operation(root, monkeypatch, phase)
    journal_before = read_journal(operation_journal_path(root, operation_id))
    assert journal_before.phase is phase
    revision = journal_before.link_index_revision

    result = recover(root)

    assert result.phase is OperationPhase.COMPLETED
    journal = read_journal(operation_journal_path(root, operation_id))
    assert journal.link_index_result is expected
    assert journal.link_index_revision == revision
    assert read_lock(root) is None
    assert b"forward-a" in (root / "pages/a.md").read_bytes()
    assert b"forward-b" in (root / "pages/b.md").read_bytes()

    # 幂等：再次 recover 不改变结果、revision、flag 和制品。
    flag_before = (root / ".draft/link-index-rebuild-required.flag").exists()
    artifact_path = root / ".draft/link-index.json"
    artifact_before = artifact_path.read_bytes() if artifact_path.exists() else None
    recover(root)
    journal_after = read_journal(operation_journal_path(root, operation_id))
    assert journal_after.link_index_result is expected
    assert journal_after.link_index_revision == revision
    assert (root / ".draft/link-index-rebuild-required.flag").exists() == flag_before
    artifact_after = artifact_path.read_bytes() if artifact_path.exists() else None
    assert artifact_after == artifact_before


def test_recover_link_index_updating_cleans_pending_and_staging(
    operation_draft: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """updating 下 ``.pending`` 残留：收敛 rebuild_required 并清理 staging/.pending。"""
    root = operation_draft
    operation_id = _persist_interrupted_operation(
        root, monkeypatch, OperationPhase.LINK_INDEX_UPDATING
    )
    op_dir = root / ".draft/operations" / operation_id
    # 模拟 stage 的 durable_write 中途崩溃：pending 已耐久、staging 尚未发布。
    (op_dir / "link-index.staged.json.pending").write_text("{partial", encoding="utf-8")

    result = recover(root)

    assert result.phase is OperationPhase.COMPLETED
    journal = read_journal(operation_journal_path(root, operation_id))
    assert journal.link_index_result is LinkIndexResult.REBUILD_REQUIRED
    assert not (op_dir / "link-index.staged.json.pending").exists()
    assert not (op_dir / "link-index.staged.json").exists()


def test_recover_link_index_updating_with_complete_staging_enters_rebuild_required(
    operation_draft: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """updating 下完整 staging（stage 后、publish 前崩溃）：收敛 rebuild_required 并清理。"""
    root = operation_draft
    plan, _old_a, _old_b, _new_a, _new_b = two_write_plan(root)
    import draft.link_index_maintenance as maintenance

    def crash_publish(_root: Path, journal: object, expected_sha256: str) -> None:
        raise RuntimeError("injected crash during publish")

    monkeypatch.setattr(maintenance, "publish_staged_link_index", crash_publish)
    with pytest.raises(RuntimeError, match="injected crash"):
        execute_plan(plan)
    monkeypatch.undo()
    operation_id = read_lock(root)
    assert operation_id is not None
    op_dir = root / ".draft/operations" / operation_id
    journal = read_journal(operation_journal_path(root, operation_id))
    assert journal.phase is OperationPhase.LINK_INDEX_UPDATING
    assert (op_dir / "link-index.staged.json").exists()

    result = recover(root)

    assert result.phase is OperationPhase.COMPLETED
    journal = read_journal(operation_journal_path(root, operation_id))
    assert journal.link_index_result is LinkIndexResult.REBUILD_REQUIRED
    assert not (op_dir / "link-index.staged.json").exists()


def test_recover_link_index_updating_does_not_guess_published_from_replaced_target(
    operation_draft: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """updating 下目标已被替换（publish 后、published 耐久前崩溃）：不猜测已发布。"""
    root = operation_draft
    plan, _old_a, _old_b, _new_a, _new_b = two_write_plan(root)
    crash_before_link_index_write(monkeypatch, OperationPhase.LINK_INDEX_PUBLISHED)
    with pytest.raises(RuntimeError, match="injected crash before journal write"):
        execute_plan(plan)
    monkeypatch.undo()
    operation_id = read_lock(root)
    assert operation_id is not None
    journal = read_journal(operation_journal_path(root, operation_id))
    assert journal.phase is OperationPhase.LINK_INDEX_UPDATING
    revision = journal.link_index_revision
    target = root / ".draft/link-index.json"
    assert target.is_file()  # 目标已被原子替换，但 journal 尚未耐久 published。

    result = recover(root)

    assert result.phase is OperationPhase.COMPLETED
    journal = read_journal(operation_journal_path(root, operation_id))
    assert journal.link_index_result is LinkIndexResult.REBUILD_REQUIRED
    assert journal.link_index_revision == revision
    assert target.is_file()  # 恢复只清理 staging，不猜测也不删除已替换目标。
    target_after = target.read_bytes()
    recover(root)
    repeated = read_journal(operation_journal_path(root, operation_id))
    assert repeated.phase is OperationPhase.COMPLETED
    assert repeated.link_index_result is LinkIndexResult.REBUILD_REQUIRED
    assert repeated.link_index_revision == revision
    assert target.read_bytes() == target_after
    assert read_lock(root) is None
    assert b"forward-a" in (root / "pages/a.md").read_bytes()
    assert b"forward-b" in (root / "pages/b.md").read_bytes()


def test_recover_link_index_published_verifies_target_before_terminal(
    operation_draft: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """published 必须验证目标 SHA-256/revision：不一致则停止并保留现场（spec §12）。"""
    root = operation_draft
    operation_id = _persist_interrupted_operation(
        root, monkeypatch, OperationPhase.LINK_INDEX_PUBLISHED
    )
    target = root / ".draft/link-index.json"
    assert target.is_file()
    tampered = b"tampered artifact bytes"
    target.write_bytes(tampered)

    with pytest.raises(DraftError, match=CheckCode.OPERATION_INCONSISTENT_STATE.value):
        recover(root)

    # 现场保留：锁仍持有、journal 仍在 published、目标未被改写。
    assert read_lock(root) == operation_id
    journal = read_journal(operation_journal_path(root, operation_id))
    assert journal.phase is OperationPhase.LINK_INDEX_PUBLISHED
    assert target.read_bytes() == tampered


def test_recover_link_index_cleans_only_current_operation_staging(
    operation_draft: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """恢复只清理当前 operation 的 staging/.pending，不删除其他 operation 或未知文件。"""
    root = operation_draft
    operation_id = _persist_interrupted_operation(
        root, monkeypatch, OperationPhase.LINK_INDEX_UPDATING
    )
    op_dir = root / ".draft/operations" / operation_id
    (op_dir / "link-index.staged.json").write_text("{}", encoding="utf-8")
    other_id = "f" * 32
    other_dir = root / ".draft/operations" / other_id
    other_dir.mkdir(parents=True)
    (other_dir / "link-index.staged.json").write_text("other", encoding="utf-8")
    (other_dir / "stray.txt").write_text("unknown", encoding="utf-8")

    recover(root)

    assert not (op_dir / "link-index.staged.json").exists()
    assert not (op_dir / "link-index.staged.json.pending").exists()
    assert (other_dir / "link-index.staged.json").read_text(encoding="utf-8") == "other"
    assert (other_dir / "stray.txt").read_text(encoding="utf-8") == "unknown"


def test_recover_allocating_with_staging_is_inconsistent(operation_draft: Path) -> None:
    """allocating 不应有 staging：出现未知内部文件时报不一致并保留现场（spec §12）。"""
    root = operation_draft
    op_id = "a" * 32
    directory = root / ".draft/operations" / op_id
    directory.mkdir(parents=True)
    write_journal(root, op_id, "allocating")
    (directory / "link-index.staged.json").write_text("{}", encoding="utf-8")
    acquire_lock(root, op_id)

    with pytest.raises(DraftError, match=CheckCode.OPERATION_INCONSISTENT_STATE.value):
        recover(root)

    assert read_lock(root) == op_id
    assert (directory / "link-index.staged.json").exists()
    journal = read_journal(operation_journal_path(root, op_id))
    assert journal.phase is OperationPhase.ALLOCATING


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

    import draft.operations as operations_module

    monkeypatch.setattr(
        "draft.operations._apply_write", crash_after_nth_apply(operations_module, 1)
    )
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
        observations=graph_observations(graph, marker=read_marker_snapshot(root)),
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
        FakeGraph(root=root, pages={Path("pages/a.md"): cast(PageDocument, _Page())}),
        marker=read_marker_snapshot(root),
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
        observations=graph_observations(load_graph(root), marker=read_marker_snapshot(root)),
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
        expected_current: str | None,
        code_path: Path,
    ) -> None:
        original_apply_write(
            root_path,
            target,
            content,
            expected_current=expected_current,
            code_path=code_path,
        )
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


def _delete_linked_page_plan(root: Path) -> tuple[OperationPlan, bytes]:
    """registered 根中删除被引用页面：应用后完整检查必然失败并触发回滚。"""
    old_a = (root / "pages/a.md").read_bytes()
    plan = OperationPlan(
        kind=OperationKind.CREATE,
        root=root,
        root_state=RootState.REGISTERED,
        observations=EMPTY_OBSERVATIONS,
        mutations=(
            FileMutation(
                Path("pages/a.md"),
                MutationKind.DELETE,
                sha256_bytes(old_a),
                None,
                None,
            ),
        ),
    )
    return plan, old_a


def test_registered_validation_failure_rolls_back_and_restores(
    operation_draft: Path,
) -> None:
    """registered 根应用后检查失败：回滚恢复 original 并释放锁（回滚方向意图正常流程）。"""
    root = operation_draft
    plan, old_a = _delete_linked_page_plan(root)
    with pytest.raises(DraftError, match="operation.validation_failed"):
        execute_plan(plan)
    assert (root / "pages/a.md").read_bytes() == old_a
    assert not (root / ".draft/operation.lock").exists()
    journal_dir = next(entry for entry in (root / ".draft/operations").iterdir() if entry.is_dir())
    journal = read_journal(journal_dir / "journal.json")
    assert journal.phase is OperationPhase.ROLLED_BACK


def test_rollback_writes_intent_before_first_mutation(
    operation_draft: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """回滚首 mutation 前耐久登记 ROLLING_BACK 意图（第十轮 H3）。

    正向与回滚共用 {目标}.pending/.quarantine 后缀，恢复器必须凭 phase 区分授权摘要。
    注入崩溃在意图写入后、首个回滚 mutation 前：journal 必须已是 ROLLING_BACK，目标
    仍处于回滚开始前的可分类状态。
    """
    import draft.operations as operations_module

    root = operation_draft
    plan, _old_a = _delete_linked_page_plan(root)
    original_write_journal = operations_module.write_journal

    def crash_after_intent(journal: object) -> None:
        original_write_journal(journal)  # type: ignore[arg-type]
        if getattr(journal, "phase", None) is OperationPhase.ROLLING_BACK:
            raise RuntimeError("injected crash after rollback intent")

    monkeypatch.setattr(operations_module, "write_journal", crash_after_intent)
    with pytest.raises(RuntimeError, match="injected crash after rollback intent"):
        execute_plan(plan)
    monkeypatch.undo()

    journal_dir = next(entry for entry in (root / ".draft/operations").iterdir() if entry.is_dir())
    journal = read_journal(journal_dir / "journal.json")
    assert journal.phase is OperationPhase.ROLLING_BACK
    # 删除已应用、尚未回滚：目标处于回滚前的 after（缺失）状态，锁仍持有。
    assert not (root / "pages/a.md").exists()
    assert (root / ".draft/operation.lock").exists()


def test_read_lock_classifies_lstat_permission_error(
    operation_draft: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """lock 的 lstat PermissionError 映射稳定错误并保留现场（回归 M2）。"""
    import os as os_module

    from draft.operations import read_lock

    root = operation_draft
    lock = root / ".draft/operation.lock"
    lock.write_text(json.dumps({"operation_id": "a" * 32}) + "\n", encoding="utf-8")
    real_lstat = os_module.lstat

    def deny_lock_lstat(
        path: str | bytes | os_module.PathLike[str],
        *,
        dir_fd: int | None = None,
    ) -> object:
        if os_module.fspath(path) == os_module.fspath(lock):
            raise PermissionError("denied")
        if dir_fd is None:
            return real_lstat(path)
        return real_lstat(path, dir_fd=dir_fd)

    monkeypatch.setattr(os_module, "lstat", deny_lock_lstat)
    with pytest.raises(DraftError) as caught:
        read_lock(root)
    assert caught.value.code == CheckCode.OPERATION_JOURNAL_CORRUPT.value
    assert isinstance(caught.value.__cause__, PermissionError)
    monkeypatch.undo()
    assert lock.exists()
    assert lock.read_text(encoding="utf-8") == json.dumps({"operation_id": "a" * 32}) + "\n"


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

    def crash_on_second_durable(root: object, relative: object, value: object) -> None:
        # journal 写入不再经过 durable_write（回归 H2 发布拆分），副本写入从第 1 次
        # durable_write 起计：第 2 次即第一个 staged 写入处崩溃。
        calls["n"] += 1
        if calls["n"] == 2:  # prepared 日志后第一个 staged 写入处崩溃。
            raise RuntimeError("injected crash during copy creation")
        original_durable(root, relative, value)  # type: ignore[arg-type]

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
    operation_draft: Path,
    op_id: str,
    *,
    extra_sub: str | None,
    observations: object | None = None,
) -> Path:
    """写入 H5 合法的 originals+staged 登记日志；可在 originals 或 staged 注入未知成员。

    ``observations`` 为真实观察清单的 JSON 载荷（默认空）；temp 所有权证明需要计划时
    缺失证据（回归 H4）。
    """
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
        "observations": [] if observations is None else observations,
        "mutations": mutations,
        "remove_empty_directories": [],
        "metadata": {},
    }
    journal_path = journal_dir / "journal.json"
    journal_path.write_text(json_module.dumps(journal), encoding="utf-8")
    return journal_path


def _graph_observations_payload(root: Path) -> list[dict[str, object]]:
    """真实图观察清单的 JSON 载荷：管理目录成员枚举（artifact 所有权证据）。"""
    manifest = graph_observations(load_graph(root), marker=read_marker_snapshot(root))
    return [
        {
            "path": entry.path.as_posix(),
            "kind": entry.kind.value,
            "sha256": entry.sha256,
            "members": list(entry.members),
        }
        for entry in manifest.entries
    ]


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
    """构造协议合法的 journal bytes（operation_id 与目录名匹配）。

    prepared/applying 携带一个真实 WRITE mutation（回归 Q1）：真实生产者只产生非空
    mutation 的 prepared/applying，测试 fixture 不得维持更宽松的"空 mutation 合法
    journal"。
    """
    import json as json_module

    mutations: list[dict[str, object]] = []
    if phase in ("prepared", "applying"):
        mutations = [
            {
                "path": "pages/a.md",
                "kind": "write",
                "before_sha256": "a" * 64,
                "after_sha256": "b" * 64,
                "original": "originals/0000.bin",
                "staged": "staged/0000.bin",
                "applied": phase == "applying",
            }
        ]
    payload: dict[str, object] = {
        "journal_version": JOURNAL_VERSION,
        "operation_id": op_id,
        "kind": OperationKind.CREATE.value,
        "phase": phase,
        "root_state": "registered",
        "observations": [],
        "mutations": mutations,
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


def test_durable_write_file_fsync_failure_leaves_pending_scene(
    operation_draft: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """文件 fsync 失败：报 durability_failed，目标未发布，pending 保留（回归 H1）。"""
    from draft.operations import durable_write

    target = operation_draft / ".draft/operations/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/journal.json"
    target.parent.mkdir(parents=True)
    _fail_fsync_at(monkeypatch, frozenset({1}))
    with pytest.raises(DraftError, match=CheckCode.OPERATION_DURABILITY_FAILED.value):
        durable_write(target.parent, Path("journal.json"), _valid_journal_bytes("a" * 32))
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
        durable_write(target.parent, Path("journal.json"), _valid_journal_bytes("b" * 32))
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
        durable_write(target.parent, Path("journal.json"), _valid_journal_bytes("c" * 32))
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
        f"durable_write(Path({str(target)!r}).parent, Path('journal.json'), {payload!r})\n"
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
    journal_path = _write_journal_with_originals_and_staged(
        root, op_id, extra_sub=None, observations=_graph_observations_payload(root)
    )
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
    journal_path = _write_journal_with_originals_and_staged(
        root, op_id, extra_sub=None, observations=_graph_observations_payload(root)
    )
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
    """旧 journal 存在 + pending 阶段倒退：保留 old+pending，报损坏（回归 H1）。

    prepared/applying 中段日志携带真实非空 mutation（协议合法，L1 修正）。
    """
    import json as json_module

    from draft.operations import resolve_journal_pending

    root = operation_draft
    op_id = "6" * 32
    journal_dir = root / ".draft/operations" / op_id
    journal_dir.mkdir(parents=True)
    target = journal_dir / "journal.json"

    def _journal_bytes(phase: str, *, mutations: list[dict[str, object]]) -> bytes:
        payload: dict[str, object] = {
            "journal_version": JOURNAL_VERSION,
            "operation_id": op_id,
            "kind": OperationKind.CREATE.value,
            "phase": phase,
            "root_state": "registered",
            "observations": [],
            "mutations": mutations,
            "remove_empty_directories": [],
            "metadata": {},
        }
        return (json_module.dumps(payload) + "\n").encode("utf-8")

    mutation: dict[str, object] = {
        "path": "pages/a.md",
        "kind": "write",
        "before_sha256": None,
        "after_sha256": "b" * 64,
        "original": None,
        "staged": "staged/0000.bin",
        "applied": True,
    }
    old_bytes = _journal_bytes("applying", mutations=[mutation])
    pending_bytes = _journal_bytes("allocating", mutations=[])  # 倒退回分配期。
    target.write_bytes(old_bytes)
    (journal_dir / "journal.json.pending").write_bytes(pending_bytes)

    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        resolve_journal_pending(target)

    assert target.read_bytes() == old_bytes
    assert (journal_dir / "journal.json.pending").read_bytes() == pending_bytes


def test_resolve_journal_pending_rejects_identity_rewrite(operation_draft: Path) -> None:
    """旧 journal 存在 + pending 改写 kind：保留 old+pending，报损坏（回归 H1）。

    prepared 中段日志携带真实非空 mutation（协议合法，L1 修正）。
    """
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
            "mutations": [
                {
                    "path": "pages/a.md",
                    "kind": "write",
                    "before_sha256": "c" * 64,
                    "after_sha256": "b" * 64,
                    "original": "originals/0000.bin",
                    "staged": "staged/0000.bin",
                    "applied": False,
                }
            ],
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


def test_acquire_lock_file_fsync_failure_is_durability_failed(
    operation_draft: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """lock 文件 fsync 失败：映射 operation.durability_failed，锁文件保留现场（回归 M3）。"""
    import os as os_module

    from draft.operations import acquire_lock

    root = operation_draft
    (root / ".draft").mkdir(parents=True, exist_ok=True)

    def failing_fsync(fd: int) -> None:
        raise OSError("injected lock fsync failure")

    monkeypatch.setattr(os_module, "fsync", failing_fsync)
    with pytest.raises(DraftError, match=CheckCode.OPERATION_DURABILITY_FAILED.value):
        acquire_lock(root, "a" * 32)
    monkeypatch.undo()

    # 锁文件已创建但未证明耐久：下一次命令按实际锁存在分类（存在→recovery_required）。
    lock = root / ".draft/operation.lock"
    assert lock.is_file()
    from draft.operations import read_lock

    assert read_lock(root) == "a" * 32


def test_acquire_lock_dir_fsync_failure_is_durability_failed(
    operation_draft: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """lock 父目录 fsync 失败：映射 operation.durability_failed（回归 M3）。"""
    import os as os_module

    from draft.operations import acquire_lock

    root = operation_draft
    (root / ".draft").mkdir(parents=True, exist_ok=True)
    real_fsync = os_module.fsync
    calls = {"n": 0}

    def failing_dir_fsync(fd: int) -> None:
        calls["n"] += 1
        if calls["n"] >= 2:  # 第一次是 lock 文件 fsync，第二次是父目录 fsync。
            raise OSError("injected dir fsync failure")
        real_fsync(fd)

    monkeypatch.setattr(os_module, "fsync", failing_dir_fsync)
    with pytest.raises(DraftError, match=CheckCode.OPERATION_DURABILITY_FAILED.value):
        acquire_lock(root, "a" * 32)
    monkeypatch.undo()


def test_read_lock_symlink_is_corrupt_not_followed(operation_draft: Path) -> None:
    """lock 是符号链接：no-follow 读取报损坏，绝不跟随链接读取目标（回归 M3）。"""
    from draft.operations import read_lock

    root = operation_draft
    (root / ".draft").mkdir(parents=True, exist_ok=True)
    external = operation_draft / "external-lock-target"
    external.write_text('{"operation_id": "' + "c" * 32 + '"}\n', encoding="utf-8")
    (root / ".draft/operation.lock").symlink_to(external)

    # reject_symlink_components 先于分类拒绝路径组件符号链接（稳定领域错误）。
    from draft.diagnostic_codes import CheckCode as _CheckCode

    with pytest.raises(DraftError, match=_CheckCode.PATH_SYMLINK_ESCAPE.value):
        read_lock(root)


def test_quarantine_delete_replaced_quarantine_name_preserves_both(
    operation_draft: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """隔离 link 后、unlink 原名前替换 quarantine 名：inode 复核拒绝，两个节点都保留（回归 H3）。"""
    import os as os_module

    from draft.operations import (
        _AnchoredLeaf,  # pyright: ignore[reportPrivateUsage]
        anchored_leaf,
        sha256_bytes,
    )

    root = operation_draft
    sub = root / "managed"
    sub.mkdir()
    leaf = sub / "leaf.txt"
    leaf.write_bytes(b"authorized")
    digest = sha256_bytes(b"authorized")
    real_link = os_module.link
    calls = {"n": 0}

    def swap_quarantine(
        src: str | bytes | os_module.PathLike[str],
        dst: str | bytes | os_module.PathLike[str],
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        calls["n"] += 1
        real_link(
            src,
            dst,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )
        if calls["n"] == 1:
            # 第一个 link 即隔离 link：随后替换 quarantine 名称为新节点。
            quarantine = sub / "leaf.txt.quarantine"
            quarantine.unlink()
            quarantine.write_bytes(b"replacement")

    monkeypatch.setattr(os_module, "link", swap_quarantine)
    with (
        anchored_leaf(root, Path("managed/leaf.txt"), code="x", code_path=leaf) as target,
        pytest.raises(DraftError, match="operation.external_change"),
    ):
        assert isinstance(target, _AnchoredLeaf)
        target.quarantine_delete(digest)
    monkeypatch.undo()

    # 原名（授权节点）与 quarantine（替换节点）都未被删除。
    assert leaf.read_bytes() == b"authorized"
    assert (sub / "leaf.txt.quarantine").read_bytes() == b"replacement"


def test_create_only_publish_replaced_pending_between_link_and_consume(
    operation_draft: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """create-only 发布后、消费 pending 前替换 pending：inode 复核拒绝，两节点保留（回归 H3）。"""
    import os as os_module

    from draft.operations import durable_write

    root = operation_draft
    target = root / "out.txt"
    pending = root / "out.txt.pending"
    real_link = os_module.link
    calls = {"n": 0}

    def swap_pending(
        src: str | bytes | os_module.PathLike[str],
        dst: str | bytes | os_module.PathLike[str],
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        calls["n"] += 1
        real_link(
            src,
            dst,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )
        if calls["n"] == 1:
            pending.unlink()
            pending.write_bytes(b"replacement")

    monkeypatch.setattr(os_module, "link", swap_pending)
    with pytest.raises(DraftError, match="operation.external_change"):
        durable_write(target.parent, Path("out.txt"), b"authorized")
    monkeypatch.undo()

    assert target.read_bytes() == b"authorized"
    assert pending.read_bytes() == b"replacement"


def test_subprocess_kill_after_quarantine_link_preserves_both_and_converges(
    operation_draft: Path,
) -> None:
    """隔离 link 已同步、unlink 原名前崩溃：原名与隔离名同时存在，恢复收敛（回归 H3）。"""
    import signal
    import subprocess
    import sys

    from draft.operations import sha256_bytes

    root = operation_draft
    sub = root / "managed"
    sub.mkdir()
    leaf = sub / "leaf.txt"
    leaf.write_bytes(b"authorized")
    digest = sha256_bytes(b"authorized")

    script = (
        "import os, signal, sys\n"
        "from pathlib import Path\n"
        f"ROOT = {str(root)!r}\n"
        f"LEAF = {str(leaf)!r}\n"
        "real_fsync = os.fsync\n"
        "calls = {'n': 0}\n"
        "def flaky(fd):\n"
        "    calls['n'] += 1\n"
        "    if calls['n'] == 1:  # 隔离 link 后的目录 fsync：原名未删，崩溃。\n"
        "        os.kill(os.getpid(), signal.SIGKILL)\n"
        "    real_fsync(fd)\n"
        "os.fsync = flaky\n"
        "from draft.operations import anchored_leaf\n"
        "with anchored_leaf(Path(ROOT), Path('managed/leaf.txt'),\n"
        "                    code='x', code_path=Path(LEAF)) as target:\n"
        f"    target.quarantine_delete({digest!r})\n"
    )
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True, timeout=60)
    assert proc.returncode == -signal.SIGKILL

    # 原名与隔离名同时存在：两个授权名都可诊断，绝不出现同时缺失。
    assert leaf.read_bytes() == b"authorized"
    quarantine = sub / "leaf.txt.quarantine"
    assert quarantine.read_bytes() == b"authorized"

    # 新进程收敛：recover_quarantine 判 delete_stale，消费 quarantine。
    from draft.operations import recover_quarantine

    recover_quarantine(root, Path("managed/leaf.txt"), digest, code="x", code_path=leaf)
    assert leaf.read_bytes() == b"authorized"
    assert not quarantine.exists()


@pytest.mark.parametrize("depth", [1, 2, 3])
def test_durable_write_nested_dir_chain_level_fsync_failure(
    operation_draft: Path, monkeypatch: pytest.MonkeyPatch, depth: int
) -> None:
    """目录链每一层 mkdirat 后的父目录 fsync 失败：durability_failed，
    目录保留、目标未发布（回归 M2）。"""
    from draft.operations import durable_write

    root = operation_draft
    parts = tuple(f"level{i}" for i in range(depth))
    relative = Path(*parts) / "file.txt"
    _fail_fsync_at(monkeypatch, frozenset({depth}))  # 第 depth 次即最深层 mkdirat 后的父 fsync。
    with pytest.raises(DraftError, match=CheckCode.OPERATION_DURABILITY_FAILED.value):
        durable_write(root, relative, b"payload")
    monkeypatch.undo()

    # 已 mkdirat 的目录保留（现场可诊断），目标未发布。
    cursor = root
    for part in parts:
        cursor = cursor / part
        assert cursor.is_dir()
    assert not (root / relative).exists()
    assert not (root / relative.with_name("file.txt.pending")).exists()


def test_durable_write_rejects_symlink_ancestor(operation_draft: Path, tmp_path: Path) -> None:
    """目录链中间组件是符号链接：no-follow 拒绝，根外不产生文件（回归 M2）。"""
    import shutil

    from draft.operations import durable_write

    root = operation_draft
    external = tmp_path / "external"
    external.mkdir()
    # 把 pages（真实目录）替换为指向外部的链接。
    pages = root / "pages"
    shutil.rmtree(pages)
    pages.symlink_to(external, target_is_directory=True)

    with pytest.raises(DraftError, match=CheckCode.OPERATION_DURABILITY_FAILED.value):
        durable_write(root, Path("pages/new/deep/file.md"), b"payload")

    assert not (external / "new").exists()  # 根外无任何文件。
    assert not (pages / "new").exists()


def test_apply_write_overwrite_rejects_wrong_current_digest(
    operation_draft: Path,
) -> None:
    """改写写目标当前摘要不符：external_change，目标保留（回归 H4）。"""
    from draft.operations import _apply_write, sha256_bytes  # pyright: ignore[reportPrivateUsage]

    root = operation_draft
    target = Path("pages/a.md")
    target_path = root / target
    original = target_path.read_bytes()
    target_path.write_bytes(b"unexpected external content")

    with pytest.raises(DraftError, match="operation.external_change"):
        _apply_write(
            root,
            target,
            b"new content",
            expected_current=sha256_bytes(original),
            code_path=target_path,
        )

    assert target_path.read_bytes() == b"unexpected external content"  # 目标保留。
    assert not (root / "pages/a.md.pending").exists()
    assert not (root / "pages/a.md.quarantine").exists()


def test_apply_write_new_file_rejects_existing_target(operation_draft: Path) -> None:
    """新建写目标已存在：external_change，目标保留（回归 H4）。"""
    from draft.operations import _apply_write  # pyright: ignore[reportPrivateUsage]

    root = operation_draft
    target = Path("pages/a.md")
    target_path = root / target
    original = target_path.read_bytes()

    with pytest.raises(DraftError, match="operation.external_change"):
        _apply_write(root, target, b"new content", expected_current=None, code_path=target_path)

    assert target_path.read_bytes() == original  # 目标保留。
    assert not (root / "pages/a.md.pending").exists()


def test_apply_delete_rejects_wrong_digest(operation_draft: Path) -> None:
    """删除目标当前摘要不符：external_change，目标保留（回归 H4）。"""
    from draft.operations import _apply_delete, sha256_bytes  # pyright: ignore[reportPrivateUsage]

    root = operation_draft
    target = Path("pages/a.md")
    target_path = root / target
    target_path.write_bytes(b"different content")

    with pytest.raises(DraftError, match="operation.external_change"):
        _apply_delete(root, target, expected_digest=sha256_bytes(b"expected"))

    assert target_path.read_bytes() == b"different content"  # 目标保留。
    assert not (root / "pages/a.md.quarantine").exists()


def test_apply_write_overwrite_preserves_unknown_quarantined_node(
    operation_draft: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """改写隔离 link 后、unlink 前替换原名：inode 复核拒绝，未知节点不删（回归 H4）。"""
    import os as os_module

    from draft.operations import _apply_write, sha256_bytes  # pyright: ignore[reportPrivateUsage]

    root = operation_draft
    target = Path("pages/a.md")
    target_path = root / target
    original = target_path.read_bytes()
    expected = sha256_bytes(original)
    real_link = os_module.link
    calls = {"n": 0}

    def swap_after_first_link(
        src: str | bytes | os_module.PathLike[str],
        dst: str | bytes | os_module.PathLike[str],
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        calls["n"] += 1
        real_link(
            src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd, follow_symlinks=follow_symlinks
        )
        if calls["n"] == 1:
            # 隔离 link 之后替换原名（新 inode）：inode 复核必须拒绝，不删未知节点。
            target_path.unlink()
            target_path.write_bytes(b"attacker replaced name")

    monkeypatch.setattr(os_module, "link", swap_after_first_link)
    with pytest.raises(DraftError, match="operation.external_change"):
        _apply_write(root, target, b"new content", expected_current=expected, code_path=target_path)
    monkeypatch.undo()

    assert target_path.read_bytes() == b"attacker replaced name"  # 未知节点保留。
    assert (root / "pages/a.md.quarantine").read_bytes() == original  # 隔离的原始节点保留。


def test_recover_artifact_identity_stat_failure_preserves_scene(
    operation_draft: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_consume_named_artifact 首次身份捕获 EACCES：recover 报 external_change（H4）。

    修复前 EACCES 折叠为 None，消费点当作"artifact 已不存在、幂等"，恢复可能错误完成
    并遗留未知 artifact；修复后身份读取失败按操作边界抛 external_change，锁与 artifact
    保留；移除故障后前向收敛。
    """
    import draft.operations as ops_module
    from draft.operations import acquire_lock, read_lock

    root = operation_draft
    old_a = (root / "pages/a.md").read_bytes()
    new_a = old_a + b"\nforward-a\n"
    op_id = "d" * 32
    _write_prepared_rewrite_journal(root, op_id, before=old_a, after=new_a)
    acquire_lock(root, op_id)
    (root / "pages/a.md").unlink()
    (root / "pages/a.md.quarantine").write_bytes(old_a)
    (root / "pages/a.md.pending").write_bytes(new_a)

    real_consume = ops_module._consume_named_artifact  # pyright: ignore[reportPrivateUsage]
    state = {"in_consume": False}

    def enter_consume(
        root_path: Path,
        relative: Path,
        *,
        name: str,
        expected_digest: str,
        code_path: Path,
    ) -> None:
        state["in_consume"] = True
        real_consume(
            root_path,
            relative,
            name=name,
            expected_digest=expected_digest,
            code_path=code_path,
        )

    monkeypatch.setattr(ops_module, "_consume_named_artifact", enter_consume)
    real_stat = ops_module.os.stat

    def deny_artifact_identity_stat(
        name: str | bytes | os.PathLike[str],
        *,
        dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> os.stat_result:
        if state["in_consume"] and dir_fd is not None:
            fspath = ops_module.os.fspath(name)
            text = fspath if isinstance(fspath, str) else fspath.decode(errors="surrogateescape")
            if text.endswith((".pending", ".quarantine")):
                raise PermissionError(13, "Permission denied")
        return real_stat(name, dir_fd=dir_fd, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(ops_module.os, "stat", deny_artifact_identity_stat)

    with pytest.raises(DraftError) as caught:
        ops_module.recover(root)
    assert caught.value.code == "operation.external_change"
    assert read_lock(root) == op_id  # 锁保留，未错误完成/解锁。
    assert (root / "pages/a.md.quarantine").exists()  # 未知节点不被删除。

    # 移除故障后再次 recover 前向收敛。
    monkeypatch.undo()
    result = ops_module.recover(root)
    assert result.phase == OperationPhase.COMPLETED
    assert read_lock(root) is None
    assert not (root / "pages/a.md.quarantine").exists()
