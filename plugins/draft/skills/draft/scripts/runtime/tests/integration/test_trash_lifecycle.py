from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from draft.diagnostic_codes import CheckCode
from draft.errors import DraftError
from draft.models import OperationPhase
from draft.operations import JOURNAL_VERSION, TrashDeadlines
from draft.trash import trash_page
from draft.trash_lifecycle import (
    advance_trash_lifecycle,
    recover_draft,
)
from tests.integration.support import DraftFactory

TRASH_INDEX = """# Topic

> 状态：`已关闭：纯导航页，不表达独立结论`

## 子主题

- [旧页](pages/obsolete.md)
- [结论汇总](conclusions.md)
"""

TRASH_CONCLUSIONS = """# 结论汇总

> 状态：`已确认`
>
> 父主题：[根索引](index.md)

## 已确认结论
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


def test_handoff_blocked_when_manifest_changed_after_verification(
    draft_factory: DraftFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """验证后改写 live manifest：handoff 不得被调用，attempt 不得包含未验证字节。

    回归 H4：make_attempt_copy 曾重新读取 live package.json；若验证后清单被改写，
    副本会携带未验证字节交给外部适配器，且正常生命周期要等清理时才发现摘要变化。
    """
    import draft.trash_lifecycle as lifecycle_module
    from draft.operations import Journal
    from draft.trash_package import VerifiedTrashPackage

    root = build_trashable_draft(draft_factory)
    pending = trash_page(root, Path("pages/obsolete.md"), now=CREATED)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    accepted: list[str] = []
    package_json = root / ".draft/trash/outgoing" / pending.operation_id / "package.json"
    original_bytes = package_json.read_bytes()

    original_load = lifecycle_module.load_verified_package

    def load_then_tamper(root_path: Path, journal: Journal) -> VerifiedTrashPackage:
        verified = original_load(root_path, journal)
        package_json.write_bytes(original_bytes + b"\n# tampered\n")
        return verified

    monkeypatch.setattr(lifecycle_module, "load_verified_package", load_then_tamper)

    report = advance_trash_lifecycle(root, now=EXPIRY, handoff=_accept_into(bin_dir, accepted))
    monkeypatch.undo()

    assert report.error is not None
    assert report.error.code == CheckCode.TRASH_PACKAGE_CORRUPT.value
    assert accepted == []  # 适配器不得被调用
    assert package_json.read_bytes() == original_bytes + b"\n# tampered\n"
    assert _package_exists(root, pending.operation_id)


def test_attempt_retry_rejects_unknown_existing_content(
    draft_factory: DraftFactory,
) -> None:
    """已有 attempt 含未知内容：重试不得递归删除未知文件（回归 M1）。"""
    from draft.operations import read_journal
    from draft.trash_lifecycle import make_attempt_copy
    from draft.trash_package import load_verified_package

    root = build_trashable_draft(draft_factory)
    pending = trash_page(root, Path("pages/obsolete.md"), now=CREATED)
    journal = read_journal(_journal_path(root, pending.operation_id))
    verified = load_verified_package(root, journal)
    attempt = make_attempt_copy(verified)
    unknown = attempt / "unknown.txt"
    unknown.write_text("unknown\n", encoding="utf-8")

    with pytest.raises(DraftError, match=CheckCode.OPERATION_INCONSISTENT_STATE.value):
        make_attempt_copy(verified)

    assert unknown.is_file()
    assert (attempt / "package.json").is_file()


def test_attempt_dangling_symlink_is_stable_error(
    draft_factory: DraftFactory, tmp_path: Path
) -> None:
    """attempt 是悬空符号链接：make_attempt_copy 报稳定错误，不泄漏 FileExistsError（回归 M2）。"""
    import shutil

    from draft.operations import read_journal
    from draft.trash_lifecycle import make_attempt_copy
    from draft.trash_package import load_verified_package

    root = build_trashable_draft(draft_factory)
    pending = trash_page(root, Path("pages/obsolete.md"), now=CREATED)
    journal = read_journal(_journal_path(root, pending.operation_id))
    verified = load_verified_package(root, journal)
    attempt = make_attempt_copy(verified)
    shutil.rmtree(attempt)
    attempt.symlink_to(tmp_path / "nowhere", target_is_directory=True)

    with pytest.raises(DraftError, match=CheckCode.OPERATION_INCONSISTENT_STATE.value):
        make_attempt_copy(verified)

    assert attempt.is_symlink()


def test_remove_attempt_copy_rejects_unknown_content(
    draft_factory: DraftFactory,
) -> None:
    """清理 attempt 时遇到未知内容必须保留，不得递归删除（回归 M1）。"""
    from draft.operations import read_journal
    from draft.trash_lifecycle import make_attempt_copy, remove_attempt_copy
    from draft.trash_package import load_verified_package

    root = build_trashable_draft(draft_factory)
    pending = trash_page(root, Path("pages/obsolete.md"), now=CREATED)
    journal = read_journal(_journal_path(root, pending.operation_id))
    verified = load_verified_package(root, journal)
    attempt = make_attempt_copy(verified)
    unknown = attempt / "unknown.txt"
    unknown.write_text("unknown\n", encoding="utf-8")

    with pytest.raises(DraftError, match=CheckCode.OPERATION_INCONSISTENT_STATE.value):
        remove_attempt_copy(verified, attempt)

    assert unknown.is_file()


def test_attempt_copy_uses_verified_payload_relative(
    draft_factory: DraftFactory,
) -> None:
    """attempt 副本只使用已验证的 manifest_bytes 与 payload_relative，不重新读取 live 包。"""
    from draft.operations import read_journal
    from draft.trash_lifecycle import make_attempt_copy
    from draft.trash_package import load_verified_package

    root = build_trashable_draft(draft_factory)
    pending = trash_page(root, Path("pages/obsolete.md"), now=CREATED)
    package = root / ".draft/trash/outgoing" / pending.operation_id
    journal = read_journal(_journal_path(root, pending.operation_id))
    verified = load_verified_package(root, journal)

    attempt = make_attempt_copy(verified)

    assert (attempt / "package.json").read_bytes() == (package / "package.json").read_bytes()
    assert (attempt / verified.payload_relative).read_bytes() == verified.page_bytes
    # 副本不含包内任何额外项（只统计文件，中间目录不算）。
    assert {
        path.relative_to(attempt).as_posix() for path in attempt.rglob("*") if path.is_file()
    } == {"package.json", verified.payload_relative.as_posix()}


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


def _inject_unknown_package_entry(package: Path, entry_type: str) -> Path:
    if entry_type == "file":
        path = package / "intruder.txt"
        path.write_text("入侵\n", encoding="utf-8")
    elif entry_type == "empty_dir":
        path = package / "extra"
        path.mkdir()
    elif entry_type == "symlink":
        path = package / "evil-link"
        external = package.parent / "external.txt"
        external.write_text("x", encoding="utf-8")
        path.symlink_to(external)
    else:  # pragma: no cover
        raise AssertionError(entry_type)
    return path


@pytest.mark.parametrize("entry_type", ["file", "empty_dir", "symlink"])
def test_terminal_recovery_preserves_unknown_package_entry(
    draft_factory: DraftFactory,
    entry_type: str,
) -> None:
    """trash_handoff_succeeded 终态恢复的权威包清理不得删除未知条目（回归 H1）。

    清理原语必须拒绝而不是无条件递归删除：未知文件/空目录/符号链接保留，操作不
    报告 completed，锁与阶段保留。
    """
    from draft.operations import acquire_lock, read_journal, with_phase, write_journal

    root = build_trashable_draft(draft_factory)
    pending = trash_page(root, Path("pages/obsolete.md"), now=CREATED)
    journal = read_journal(_journal_path(root, pending.operation_id))
    write_journal(with_phase(journal, OperationPhase.TRASH_HANDOFF_SUCCEEDED))
    acquire_lock(root, pending.operation_id)
    package = root / ".draft/trash/outgoing" / pending.operation_id
    unknown = _inject_unknown_package_entry(package, entry_type)

    calls: list[str] = []
    with pytest.raises(DraftError, match=CheckCode.TRASH_PACKAGE_CORRUPT.value):
        recover_draft(root, now=PURGE, handoff=calls.append)

    assert unknown.is_symlink() or unknown.exists()
    assert calls == []
    assert (root / ".draft/operation.lock").exists()
    assert _phase(root, pending.operation_id) == "trash_handoff_succeeded"


def test_terminal_recovery_idempotent_when_payload_missing(
    draft_factory: DraftFactory,
) -> None:
    """已授权条目部分缺失（提交点后幂等清理）：终态恢复必须成功而非误报损坏。"""
    from draft.operations import acquire_lock, read_journal, with_phase, write_journal

    root = build_trashable_draft(draft_factory)
    pending = trash_page(root, Path("pages/obsolete.md"), now=CREATED)
    journal = read_journal(_journal_path(root, pending.operation_id))
    write_journal(with_phase(journal, OperationPhase.TRASH_HANDOFF_SUCCEEDED))
    acquire_lock(root, pending.operation_id)
    payload = root / ".draft/trash/outgoing" / pending.operation_id / "payload/pages/obsolete.md"
    payload.unlink()

    calls: list[str] = []
    recover_draft(root, now=PURGE, handoff=calls.append)

    assert calls == []
    assert _phase(root, pending.operation_id) == "completed"
    assert not _package_exists(root, pending.operation_id)


def test_terminal_recovery_rejects_empty_mutation_log(
    draft_factory: DraftFactory,
) -> None:
    """空 mutation 的 succeeded 日志：终态恢复必须先验证日志语义，不得写成 completed（回归 H6）。"""
    import json
    import shutil

    from draft.operations import acquire_lock, read_journal, with_phase, write_journal

    root = build_trashable_draft(draft_factory)
    pending = trash_page(root, Path("pages/obsolete.md"), now=CREATED)
    journal = read_journal(_journal_path(root, pending.operation_id))
    write_journal(with_phase(journal, OperationPhase.TRASH_HANDOFF_SUCCEEDED))
    acquire_lock(root, pending.operation_id)
    # 清空 mutation 并把权威包整个移除：物理包缺失只是文件系统状态，不能跳过日志验证。
    data = json.loads(_journal_path(root, pending.operation_id).read_text(encoding="utf-8"))
    data["mutations"] = []
    _journal_path(root, pending.operation_id).write_text(json.dumps(data), encoding="utf-8")
    shutil.rmtree(root / ".draft/trash/outgoing" / pending.operation_id)

    calls: list[str] = []
    with pytest.raises(DraftError, match=CheckCode.TRASH_PACKAGE_CORRUPT.value):
        recover_draft(root, now=PURGE, handoff=calls.append)

    assert calls == []
    assert (root / ".draft/operation.lock").exists()
    assert _phase(root, pending.operation_id) == "trash_handoff_succeeded"


def test_terminal_recovery_converges_after_mid_delete_extra(
    draft_factory: DraftFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """终态恢复删除期间出现晚到 extra：保留证据，移除后 recover 从证据收敛（回归 H2）。"""
    import draft.trash_package as trash_package_module
    from draft.operations import acquire_lock, read_journal, with_phase, write_journal

    root = build_trashable_draft(draft_factory)
    pending = trash_page(root, Path("pages/obsolete.md"), now=CREATED)
    journal = read_journal(_journal_path(root, pending.operation_id))
    write_journal(with_phase(journal, OperationPhase.TRASH_HANDOFF_SUCCEEDED))
    acquire_lock(root, pending.operation_id)
    package = root / ".draft/trash/outgoing" / pending.operation_id

    original_precheck = trash_package_module._precheck_package_tree  # pyright: ignore[reportPrivateUsage]

    def precheck_then_inject(
        root_path: Path, package_path: Path, authorized: list[tuple[Path, str]], code_path: Path
    ) -> None:
        original_precheck(root_path, package_path, authorized, code_path)
        (package / "late.txt").write_text("late\n", encoding="utf-8")

    monkeypatch.setattr(trash_package_module, "_precheck_package_tree", precheck_then_inject)
    calls: list[str] = []
    with pytest.raises(DraftError, match=CheckCode.TRASH_PACKAGE_CORRUPT.value):
        recover_draft(root, now=PURGE, handoff=calls.append)

    assert calls == []
    assert _phase(root, pending.operation_id) == "trash_handoff_succeeded"
    assert (root / ".draft/operation.lock").exists()
    assert (journal.directory / "cleanup-evidence.json").is_file()
    assert (package / "late.txt").read_text(encoding="utf-8") == "late\n"

    monkeypatch.undo()
    (package / "late.txt").unlink()
    recover_draft(root, now=PURGE, handoff=calls.append)
    assert _phase(root, pending.operation_id) == "completed"
    assert not _package_exists(root, pending.operation_id)
    assert not (journal.directory / "cleanup-evidence.json").exists()


def test_handoff_not_called_for_third_package_write(
    draft_factory: DraftFactory,
) -> None:
    """第三个包内 WRITE（物理文件缺失）：handoff 前置验证拒绝，spy 调用次数为 0（回归 H6）。"""
    import json

    root = build_trashable_draft(draft_factory)
    pending = trash_page(root, Path("pages/obsolete.md"), now=CREATED)
    path = _journal_path(root, pending.operation_id)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["mutations"].append(
        {
            "path": f".draft/trash/outgoing/{pending.operation_id}/payload/extra.md",
            "kind": "write",
            "before_sha256": None,
            "after_sha256": "a" * 64,
            "original": None,
            "staged": f"staged/{len(data['mutations']):04d}.bin",
            "applied": False,
        }
    )
    path.write_text(json.dumps(data), encoding="utf-8")

    calls: list[str] = []
    report = advance_trash_lifecycle(root, now=EXPIRY, handoff=calls.append)
    assert report.error is not None
    assert report.error.code == CheckCode.TRASH_PACKAGE_CORRUPT.value
    assert calls == []


def test_recover_cli_never_exposes_internal_paths(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    """recover 的公开 paths 不得包含 .draft/ 内部路径。"""
    import json

    from draft.cli import main

    root = build_trashable_draft(draft_factory)
    pending = trash_page(root, Path("pages/obsolete.md"), now=CREATED)

    from draft.operations import acquire_lock, read_journal, with_phase, write_journal

    journal = read_journal(_journal_path(root, pending.operation_id))
    write_journal(with_phase(journal, OperationPhase.TRASH_HANDOFF_SUCCEEDED))
    acquire_lock(root, pending.operation_id)
    capsys.readouterr()

    code = main(["recover", "--root", str(root), "--json"])
    payload = cast("dict[str, object]", json.loads(capsys.readouterr().out))

    assert code == 0
    assert payload["result_type"] == "recover"
    assert payload["fixed_count"] == 1
    paths = cast("list[str]", payload["paths"])
    assert all(not p.startswith(".draft/") for p in paths)


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

    remove_attempt_copy(package, attempt)


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
        "journal_version": JOURNAL_VERSION,
        "operation_id": r_id,
        "kind": "restore",
        "phase": "allocating",
        "root_state": "registered",
        "observations": [],
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


# ---------------------------------------------------------------------------
# Task 4：统一静默前置推进与可恢复项清单
# ---------------------------------------------------------------------------


def test_prepare_managed_command_advances_but_hides_results(
    draft_factory: DraftFactory, tmp_path: Path
) -> None:
    """普通命令的前置推进推进到期项，但成功时返回值不暴露推进细节。"""
    from draft.trash_lifecycle import prepare_managed_command

    root = build_trashable_draft(draft_factory)
    trash_page(root, Path("pages/obsolete.md"), now=CREATED)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    accepted: list[str] = []

    result = prepare_managed_command(root, now=EXPIRY, handoff=_accept_into(bin_dir, accepted))

    assert result is None
    assert len(accepted) == 1


def test_prepare_managed_command_corruption_raises_lifecycle_advance_error(
    draft_factory: DraftFactory,
) -> None:
    """损坏日志以可诊断错误阻止命令，不返回部分推进结果。"""
    import json

    from draft.trash_lifecycle import LifecycleAdvanceError, prepare_managed_command

    root = build_trashable_draft(draft_factory)
    pending = trash_page(root, Path("pages/obsolete.md"), now=CREATED)
    broken_path = _journal_path(root, pending.operation_id)
    data = json.loads(broken_path.read_text(encoding="utf-8"))
    data["phase"] = "unknown_phase"
    broken_path.write_text(json.dumps(data), encoding="utf-8")

    try:
        prepare_managed_command(root, now=EXPIRY)
    except LifecycleAdvanceError as error:
        assert error.code == CheckCode.OPERATION_JOURNAL_CORRUPT.value
        assert error.report.results == ()
    else:
        raise AssertionError("expected LifecycleAdvanceError")


def test_list_recoverable_filters_by_expiry_and_sorts(
    draft_factory: DraftFactory,
) -> None:
    """list_recoverable 只返回仍未到期项目，按到期时间再按 operation_id 排序。"""
    from draft.trash_lifecycle import list_recoverable

    root = build_trashable_draft(draft_factory)
    first = trash_page(root, Path("pages/obsolete.md"), now=CREATED)
    (root / "pages/obsolete.md").write_text(OBSOLETE, encoding="utf-8")
    idx = root / "index.md"
    idx.write_text(
        idx.read_text(encoding="utf-8").replace(
            "- [结论汇总](conclusions.md)\n",
            "- [旧页](pages/obsolete.md)\n- [结论汇总](conclusions.md)\n",
        ),
        encoding="utf-8",
    )
    second = trash_page(root, Path("pages/obsolete.md"), now=CREATED)

    items = list_recoverable(root, as_of=EXPIRY - timedelta(days=1))

    assert [item.operation_id for item in items] == sorted(
        [first.operation_id, second.operation_id]
    )
    assert all(item.restore_expires_at == EXPIRY for item in items)
    assert all(item.page == Path("pages/obsolete.md") for item in items)


def test_list_recoverable_excludes_at_exact_expiry(
    draft_factory: DraftFactory,
) -> None:
    """as_of == restore_expires_at 时项目已不可恢复，不进入结果。"""
    from draft.trash_lifecycle import list_recoverable

    root = build_trashable_draft(draft_factory)
    trash_page(root, Path("pages/obsolete.md"), now=CREATED)

    items = list_recoverable(root, as_of=EXPIRY)

    assert items == ()


def test_list_recoverable_corruption_fails_whole(
    draft_factory: DraftFactory,
) -> None:
    """任一候选日志损坏时整体失败，不返回此前读到的部分项目。"""
    import json

    from draft.trash_lifecycle import list_recoverable

    root = build_trashable_draft(draft_factory)
    pending = trash_page(root, Path("pages/obsolete.md"), now=CREATED)
    broken_path = _journal_path(root, pending.operation_id)
    data = json.loads(broken_path.read_text(encoding="utf-8"))
    data["metadata"]["created_at"] = "not-a-date"
    broken_path.write_text(json.dumps(data), encoding="utf-8")

    import pytest

    with pytest.raises(DraftError):
        list_recoverable(root, as_of=EXPIRY)
