from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from draft.diagnostic_codes import CheckCode
from draft.models import OperationPhase
from draft.operations import TrashDeadlines
from draft.trash import trash_page
from draft.trash_lifecycle import (
    advance_trash_lifecycle,
    recover_draft,
)
from tests.integration.support import DraftFactory

TRASH_INDEX = """# Topic

> 状态：`已关闭：纯导航页，不表达独立结论`

## 子主题

- [结论汇总](conclusions.md)
- [旧页](pages/obsolete.md)
"""

TRASH_CONCLUSIONS = """# 结论汇总

> 状态：`已确认`
>
> 父主题：[根索引](index.md)
"""

OBSOLETE = """# 旧页

> 状态：`待讨论`
>
> 父主题：[根索引](../index.md)

旧页的正文。
"""


def build_trashable_draft(draft_factory: DraftFactory) -> Path:
    return draft_factory(
        {
            "index.md": TRASH_INDEX,
            "conclusions.md": TRASH_CONCLUSIONS,
            "pages/obsolete.md": OBSOLETE,
        }
    )


CREATED = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)
EXPIRY = CREATED + timedelta(days=7)
PURGE = CREATED + timedelta(days=37)


def _journal_path(root: Path, operation_id: str) -> Path:
    return root / ".draft/operations" / operation_id / "journal.json"


def _phase(root: Path, operation_id: str) -> str:
    import json

    return json.loads(_journal_path(root, operation_id).read_text(encoding="utf-8"))["phase"]


def _deadlines(root: Path, operation_id: str) -> TrashDeadlines:
    import json

    metadata = json.loads(_journal_path(root, operation_id).read_text(encoding="utf-8"))["metadata"]
    return TrashDeadlines.parse(metadata)


def _package_exists(root: Path, operation_id: str) -> bool:
    return (root / ".draft/trash/outgoing" / operation_id).is_dir()


def _accept_into(bin_dir: Path, accepted: list[str]) -> Callable[[str], None]:
    def accept(path: str) -> None:
        import shutil

        target = bin_dir / f"accepted-{len(accepted)}"
        shutil.move(path, target)
        accepted.append(path)

    return accept


def test_retained_before_expiry_does_not_handoff(
    draft_factory: DraftFactory,
) -> None:
    root = build_trashable_draft(draft_factory)
    pending = trash_page(root, Path("pages/obsolete.md"), now=CREATED)
    calls: list[str] = []

    report = advance_trash_lifecycle(
        root, now=EXPIRY - timedelta(microseconds=1), handoff=calls.append
    )

    assert calls == []
    assert report.results == ()
    assert _phase(root, pending.operation_id) == "trash_retained"
    assert _package_exists(root, pending.operation_id)


def test_exact_restore_expiry_transitions_to_pending_and_attempts(
    draft_factory: DraftFactory, tmp_path: Path
) -> None:
    root = build_trashable_draft(draft_factory)
    pending = trash_page(root, Path("pages/obsolete.md"), now=CREATED)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    accepted: list[str] = []

    report = advance_trash_lifecycle(root, now=EXPIRY, handoff=_accept_into(bin_dir, accepted))

    assert len(accepted) == 1
    assert len(report.results) == 1
    # 只把一次性副本交给适配器；权威包在 succeeded 耐久后才清理。
    assert _phase(root, pending.operation_id) in (
        "trash_handoff_pending",
        "trash_handoff_succeeded",
        "completed",
    )
    assert _phase(root, pending.operation_id) == "completed"
    assert not _package_exists(root, pending.operation_id)


def test_failed_handoff_sets_next_attempt_to_plus_24_hours(
    draft_factory: DraftFactory,
) -> None:
    root = build_trashable_draft(draft_factory)
    pending = trash_page(root, Path("pages/obsolete.md"), now=CREATED)

    def fail(path: str) -> None:
        raise OSError("recycle bin unavailable")

    report = advance_trash_lifecycle(root, now=EXPIRY, handoff=fail)

    assert _phase(root, pending.operation_id) == "trash_handoff_pending"
    assert _package_exists(root, pending.operation_id)
    deadlines = _deadlines(root, pending.operation_id)
    assert deadlines.next_handoff_attempt_at == EXPIRY + timedelta(hours=24)
    assert report.results and report.results[0].warnings


def test_retry_before_next_attempt_is_skipped_and_at_next_attempt_runs(
    draft_factory: DraftFactory, tmp_path: Path
) -> None:
    root = build_trashable_draft(draft_factory)
    pending = trash_page(root, Path("pages/obsolete.md"), now=CREATED)

    def fail(path: str) -> None:
        raise OSError("recycle bin unavailable")

    advance_trash_lifecycle(root, now=EXPIRY, handoff=fail)
    next_attempt = _deadlines(root, pending.operation_id).next_handoff_attempt_at
    assert next_attempt == EXPIRY + timedelta(hours=24)

    calls: list[str] = []
    advance_trash_lifecycle(
        root, now=next_attempt - timedelta(microseconds=1), handoff=calls.append
    )
    assert calls == []  # 严格 < 节流：未到点不重试。

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    accepted: list[str] = []
    advance_trash_lifecycle(root, now=next_attempt, handoff=_accept_into(bin_dir, accepted))
    assert len(accepted) == 1
    assert _phase(root, pending.operation_id) == "completed"


def test_purge_deadline_forces_final_attempt_despite_throttle(
    draft_factory: DraftFactory,
) -> None:
    root = build_trashable_draft(draft_factory)
    pending = trash_page(root, Path("pages/obsolete.md"), now=CREATED)

    def fail(path: str) -> None:
        raise OSError("recycle bin unavailable")

    advance_trash_lifecycle(root, now=EXPIRY, handoff=fail)
    next_attempt = _deadlines(root, pending.operation_id).next_handoff_attempt_at

    # 推进到清理期限之后、但人为把 next_attempt 调到更晚：清理期限仍强制最后一次投递。
    later = next_attempt + timedelta(days=30)
    assert later >= PURGE
    advance_trash_lifecycle(root, now=later, handoff=fail)

    assert _phase(root, pending.operation_id) == "completed"
    assert not _package_exists(root, pending.operation_id)


def test_crash_during_final_attempt_never_commits_purge(
    draft_factory: DraftFactory,
) -> None:
    root = build_trashable_draft(draft_factory)
    pending = trash_page(root, Path("pages/obsolete.md"), now=CREATED)

    def fail(path: str) -> None:
        raise OSError("first failure")

    advance_trash_lifecycle(root, now=EXPIRY, handoff=fail)

    def crash(path: str) -> None:
        raise RuntimeError("injected process interruption")

    with pytest.raises(RuntimeError, match="injected process interruption"):
        advance_trash_lifecycle(root, now=PURGE, handoff=crash)

    # 非 OSError 中断不转为明确失败：阶段与权威包保持可重试。
    assert _phase(root, pending.operation_id) == "trash_handoff_pending"
    assert _package_exists(root, pending.operation_id)


def test_handoff_success_marker_prevents_resubmission_after_crash(
    draft_factory: DraftFactory,
) -> None:
    root = build_trashable_draft(draft_factory)
    pending = trash_page(root, Path("pages/obsolete.md"), now=CREATED)

    from draft.operations import acquire_lock, read_journal, with_phase, write_journal

    # 手工构造“成功标记已耐久、清理前崩溃”现场：T=trash_handoff_succeeded、持锁、包仍在。
    journal = read_journal(_journal_path(root, pending.operation_id))
    succeeded = with_phase(journal, OperationPhase.TRASH_HANDOFF_SUCCEEDED)
    write_journal(succeeded)
    acquire_lock(root, pending.operation_id)

    calls: list[str] = []
    recover_draft(root, now=PURGE, handoff=calls.append)

    assert calls == []  # succeeded 已耐久：恢复只清理，不再调用适配器。
    assert _phase(root, pending.operation_id) == "completed"
    assert not _package_exists(root, pending.operation_id)


def test_purge_committed_recovery_only_finishes_cleanup(
    draft_factory: DraftFactory,
) -> None:
    root = build_trashable_draft(draft_factory)
    pending = trash_page(root, Path("pages/obsolete.md"), now=CREATED)

    from draft.operations import acquire_lock, read_journal, with_phase, write_journal

    journal = read_journal(_journal_path(root, pending.operation_id))
    # 手工写入 purge_committed 模拟“最终清理提交点已耐久、清理前崩溃”。
    committed = with_phase(journal, OperationPhase.TRASH_PURGE_COMMITTED)
    write_journal(committed)
    acquire_lock(root, pending.operation_id)
    assert _package_exists(root, pending.operation_id)

    calls: list[str] = []
    recover_draft(root, now=PURGE, handoff=calls.append)

    assert calls == []
    assert _phase(root, pending.operation_id) == "completed"
    assert not _package_exists(root, pending.operation_id)


def test_batch_failure_preserves_prior_results_and_never_touches_later_operation(
    draft_factory: DraftFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = draft_factory(
        {
            "index.md": TRASH_INDEX.replace(
                "- [旧页](pages/obsolete.md)",
                "- [甲](pages/a.md)\n- [乙](pages/b.md)\n- [丙](pages/c.md)",
            ),
            "conclusions.md": TRASH_CONCLUSIONS,
            "pages/a.md": "# 甲\n\n> 状态：`待讨论`\n>\n> 父主题：[根索引](../index.md)\n\n甲。\n",
            "pages/b.md": "# 乙\n\n> 状态：`待讨论`\n>\n> 父主题：[根索引](../index.md)\n\n乙。\n",
            "pages/c.md": "# 丙\n\n> 状态：`待讨论`\n>\n> 父主题：[根索引](../index.md)\n\n丙。\n",
        }
    )
    a = trash_page(root, Path("pages/a.md"), now=CREATED)
    b = trash_page(root, Path("pages/b.md"), now=CREATED)
    c = trash_page(root, Path("pages/c.md"), now=CREATED)
    operation_ids = sorted([a.operation_id, b.operation_id, c.operation_id])

    # 破坏排序中第二项（B）的权威包证据。
    middle = operation_ids[1]
    (root / ".draft/trash/outgoing" / middle / "package.json").write_text(
        "corrupt\n", encoding="utf-8"
    )
    # 在排序中第三项（C）的 journal.json 上放置读取哨兵。
    last = operation_ids[2]
    c_journal = _journal_path(root, last)
    original_read_text = Path.read_text

    def sentinel_read_text(self: Path, *args: object, **kwargs: object) -> str:
        if self == c_journal:
            raise AssertionError("advance must not read later operation C journal")
        return original_read_text(self, *args, **kwargs)  # type: ignore[call-arg]

    monkeypatch.setattr(Path, "read_text", sentinel_read_text)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    accepted: list[str] = []
    report = advance_trash_lifecycle(root, now=PURGE, handoff=_accept_into(bin_dir, accepted))
    monkeypatch.undo()

    # 第一项成功推进；第二项损坏停止；第三项不被触碰。
    assert report.error is not None
    assert report.error.operation_id == middle
    assert any(result.operation_id == operation_ids[0] for result in report.results)
    assert not any(result.operation_id == last for result in report.results)


# ---------------------------------------------------------------------------
# 评审回归：持锁保留态恢复、attempt 副本只含验证文件、批量损坏保留前项结果。
# ---------------------------------------------------------------------------


def test_locked_retained_recovery_advances_and_releases_lock(
    draft_factory: DraftFactory, tmp_path: Path
) -> None:
    """持锁 trash_retained（到期、推进中崩溃）恢复必须沿持锁推进并释放锁，不再重新加锁。"""
    from draft.operations import acquire_lock

    root = build_trashable_draft(draft_factory)
    pending = trash_page(root, Path("pages/obsolete.md"), now=CREATED)
    acquire_lock(root, pending.operation_id)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    accepted: list[str] = []

    recover_draft(root, now=EXPIRY, handoff=_accept_into(bin_dir, accepted))

    assert len(accepted) == 1
    assert not (root / ".draft/operation.lock").exists()
    assert _phase(root, pending.operation_id) == "completed"


def test_locked_retained_before_expiry_recovery_releases_lock(
    draft_factory: DraftFactory,
) -> None:
    """持锁 trash_retained（恢复窗口内）恢复必须释放锁，不得把锁留给后续结构写。"""
    from draft.operations import acquire_lock

    root = build_trashable_draft(draft_factory)
    pending = trash_page(root, Path("pages/obsolete.md"), now=CREATED)
    acquire_lock(root, pending.operation_id)
    calls: list[str] = []

    recover_draft(root, now=EXPIRY - timedelta(days=1), handoff=calls.append)

    assert calls == []
    assert not (root / ".draft/operation.lock").exists()
    assert _phase(root, pending.operation_id) == "trash_retained"
    assert _package_exists(root, pending.operation_id)


def test_attempt_copy_only_contains_verified_files(
    draft_factory: DraftFactory,
) -> None:
    from draft.operations import read_journal
    from draft.trash_lifecycle import make_attempt_copy
    from draft.trash_package import load_verified_package

    root = build_trashable_draft(draft_factory)
    pending = trash_page(root, Path("pages/obsolete.md"), now=CREATED)
    journal = read_journal(_journal_path(root, pending.operation_id))
    package = load_verified_package(root, journal)

    attempt = make_attempt_copy(package)

    files = {path.relative_to(attempt).as_posix() for path in attempt.rglob("*") if path.is_file()}
    assert files == {"package.json", "payload/pages/obsolete.md"}
    from draft.trash_lifecycle import remove_attempt_copy

    remove_attempt_copy(attempt)


def test_package_with_extra_symlink_entry_is_rejected(
    draft_factory: DraftFactory, tmp_path: Path
) -> None:
    from draft.errors import DraftError
    from draft.operations import read_journal
    from draft.trash_package import load_verified_package

    root = build_trashable_draft(draft_factory)
    pending = trash_page(root, Path("pages/obsolete.md"), now=CREATED)
    package_dir = root / ".draft/trash/outgoing" / pending.operation_id
    external = tmp_path / "external"
    external.mkdir()
    (external / "secret.txt").write_text("secret", encoding="utf-8")
    (package_dir / "extra").symlink_to(external, target_is_directory=True)
    journal = read_journal(_journal_path(root, pending.operation_id))

    with pytest.raises(DraftError, match=CheckCode.TRASH_PACKAGE_CORRUPT.value):
        load_verified_package(root, journal)


def test_recovery_scan_rejects_symlinked_operations_dir(
    draft_factory: DraftFactory, tmp_path: Path
) -> None:
    """恢复扫描在任何遍历前逐组件校验：.draft/operations 为符号链接即拒绝。"""
    import shutil

    from draft.errors import DraftError

    root = build_trashable_draft(draft_factory)
    trash_page(root, Path("pages/obsolete.md"), now=CREATED)
    external = tmp_path / "external"
    external.mkdir()
    (external / "journal.json").write_text("{}\n", encoding="utf-8")
    shutil.rmtree(root / ".draft/operations")
    (root / ".draft/operations").symlink_to(external, target_is_directory=True)
    before = (external / "journal.json").read_bytes()

    with pytest.raises(DraftError, match=CheckCode.PATH_SYMLINK_ESCAPE.value):
        recover_draft(root)

    # 外部目录内容不得被读取或改动。
    assert (external / "journal.json").read_bytes() == before


def test_sub_operation_dir_symlink_is_reported_not_skipped(
    draft_factory: DraftFactory, tmp_path: Path
) -> None:
    """operations/<id> 为符号链接时，推进、恢复、反向关联与检查器都必须报错，不得静默跳过。"""
    import shutil

    from draft.checker import check_draft
    from draft.errors import DraftError

    root = build_trashable_draft(draft_factory)
    pending = trash_page(root, Path("pages/obsolete.md"), now=CREATED)
    operations = root / ".draft/operations"
    target = tmp_path / "external-op"
    target.mkdir()
    shutil.move(str(operations / pending.operation_id), str(target / pending.operation_id))
    (operations / pending.operation_id).symlink_to(
        target / pending.operation_id, target_is_directory=True
    )
    external_journal = (target / pending.operation_id / "journal.json").read_bytes()

    # 推进扫描（_iter_trash_journals）必须报错，不静默跳过。
    with pytest.raises(DraftError, match=CheckCode.PATH_SYMLINK_ESCAPE.value):
        advance_trash_lifecycle(root, now=EXPIRY, handoff=lambda p: None)
    # 恢复扫描（_handle_no_lock_dir）必须把链接条目报为批量错误，而非静默跳过。
    report = recover_draft(root)
    assert report.error is not None
    assert report.error.code == CheckCode.PATH_SYMLINK_ESCAPE.value
    assert report.error.operation_id == pending.operation_id
    # 检查器必须报告 path.symlink_escape 问题，而非静默跳过。
    report = check_draft(root)
    assert any(issue.code == CheckCode.PATH_SYMLINK_ESCAPE.value for issue in report.errors)
    # 外部内容绝不被读取或改动。
    assert (target / pending.operation_id / "journal.json").read_bytes() == external_journal


def test_terminal_cleanup_rejects_symlinked_outgoing(
    draft_factory: DraftFactory, tmp_path: Path
) -> None:
    """终态清理（succeeded/purge_committed）在删除权威包前先校验 trash/outgoing 固定组件。"""
    import shutil

    from draft.errors import DraftError
    from draft.operations import acquire_lock, with_phase, write_journal

    root = build_trashable_draft(draft_factory)
    pending = trash_page(root, Path("pages/obsolete.md"), now=CREATED)
    # 构造 succeeded 持锁现场，再把 trash/outgoing 换成符号链接。
    from draft.operations import read_journal

    journal = read_journal(_journal_path(root, pending.operation_id))
    write_journal(with_phase(journal, OperationPhase.TRASH_HANDOFF_SUCCEEDED))
    acquire_lock(root, pending.operation_id)
    outgoing = root / ".draft/trash/outgoing"
    external = tmp_path / "external-bin"
    external.mkdir()
    (external / pending.operation_id).mkdir()
    (external / pending.operation_id / "package.json").write_text("external\n", encoding="utf-8")
    shutil.rmtree(outgoing)
    outgoing.symlink_to(external, target_is_directory=True)
    external_payload = (external / pending.operation_id / "package.json").read_bytes()

    with pytest.raises(DraftError, match=CheckCode.PATH_SYMLINK_ESCAPE.value):
        recover_draft(root)

    # 外部目录绝不被清理：外部包内容保持完整。
    assert (external / pending.operation_id / "package.json").read_bytes() == external_payload


def test_batch_corrupt_journal_preserves_prior_results(
    draft_factory: DraftFactory, tmp_path: Path
) -> None:
    """批量推进中后项日志 JSON 损坏时，前项结果必须保留在公开信封，不得向外抛。"""
    root = draft_factory(
        {
            "index.md": TRASH_INDEX.replace(
                "- [旧页](pages/obsolete.md)",
                "- [甲](pages/a.md)\n- [乙](pages/b.md)",
            ),
            "conclusions.md": TRASH_CONCLUSIONS,
            "pages/a.md": "# 甲\n\n> 状态：`待讨论`\n>\n> 父主题：[根索引](../index.md)\n\n甲。\n",
            "pages/b.md": "# 乙\n\n> 状态：`待讨论`\n>\n> 父主题：[根索引](../index.md)\n\n乙。\n",
        }
    )
    a = trash_page(root, Path("pages/a.md"), now=CREATED)
    b = trash_page(root, Path("pages/b.md"), now=CREATED)
    ordered = sorted([a.operation_id, b.operation_id])
    first, second = ordered[0], ordered[1]
    # 破坏排序后者的日志 JSON（不可解析）。
    _journal_path(root, second).write_text("not json\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    accepted: list[str] = []

    report = advance_trash_lifecycle(root, now=EXPIRY, handoff=_accept_into(bin_dir, accepted))

    assert report.error is not None
    assert report.error.operation_id == second
    assert [result.operation_id for result in report.results] == [first]


def test_orphan_restore_scan_stops_on_unreadable_sibling_journal(
    draft_factory: DraftFactory,
) -> None:
    """反向关联扫描遇到不可读日志必须停止并保留 R，不得吞错后把 R 误判孤儿写 rolled_back。"""
    import json

    root = build_trashable_draft(draft_factory)
    pending = trash_page(root, Path("pages/obsolete.md"), now=CREATED)
    operations = root / ".draft/operations"

    # 孤儿 R 候选：无锁、kind=restore、allocating、无 mutation、无 T 反向关联。
    # 选用字典序最小的 id，保证无锁扫描先处理 R（在 R 的反向关联扫描中撞到损坏日志）。
    r_id = "0" * 32
    r_dir = operations / r_id
    r_dir.mkdir()
    r_journal: dict[str, object] = {
        "journal_version": 2,
        "operation_id": r_id,
        "kind": "restore",
        "phase": "allocating",
        "validation_mode": "registered",
        "mutations": [],
        "remove_empty_directories": [],
        "metadata": {},
    }
    (r_dir / "journal.json").write_text(
        json.dumps(r_journal, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    # 损坏的兄弟日志（可能正是反向关联证据，但已不可读）。
    c_id = "f" * 32
    c_dir = operations / c_id
    c_dir.mkdir()
    (c_dir / "journal.json").write_text("not json\n", encoding="utf-8")
    r_bytes_before = (r_dir / "journal.json").read_bytes()

    report = recover_draft(root)

    # 停止并报错；R 保持 allocating，绝不写 rolled_back；T 保持 trash_retained。
    assert report.error is not None
    assert report.error.operation_id == r_id
    assert report.error.code == CheckCode.OPERATION_JOURNAL_CORRUPT.value
    assert (r_dir / "journal.json").read_bytes() == r_bytes_before
    assert _phase(root, pending.operation_id) == "trash_retained"
