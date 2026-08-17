from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from draft.checker import check_draft
from draft.diagnostic_codes import CheckCode
from draft.errors import DraftError
from draft.models import OperationPhase, RootState
from draft.operations import JOURNAL_VERSION, read_journal
from draft.trash import restore_retained_trash, trash_page
from draft.trash_lifecycle import recover_draft
from tests.integration.support import DraftFactory

TRASH_INDEX = """# Topic

> 状态：`已关闭：纯导航页，不表达独立结论`

## 子主题

- [旧页](pages/obsolete.md)
- [其他](pages/other.md)
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

OTHER = """# 其他

> 状态：`待讨论`
>
> 父主题：[根索引](../index.md)

其他的正文。
"""


def build_trashable_draft(draft_factory: DraftFactory) -> Path:
    return draft_factory(
        {
            "index.md": TRASH_INDEX,
            "conclusions.md": TRASH_CONCLUSIONS,
            "pages/obsolete.md": OBSOLETE,
            "pages/other.md": OTHER,
        }
    )


def build_solo_trash_draft(draft_factory: DraftFactory) -> Path:
    return draft_factory(
        {
            "index.md": TRASH_INDEX.replace(
                "- [旧页](pages/obsolete.md)\n- [其他](pages/other.md)",
                "- [单人枢纽](pages/solo/index.md)",
            ),
            "conclusions.md": TRASH_CONCLUSIONS,
            "pages/solo/index.md": (
                "# 单人枢纽\n\n> 状态：`已关闭：纯导航页`\n>\n"
                "> 父主题：[根索引](../../index.md)\n\n- [唯一](only.md)\n"
            ),
            "pages/solo/only.md": (
                "# 唯一\n\n> 状态：`待讨论`\n>\n> 父主题：[单人枢纽](index.md)\n\n唯一的正文。\n"
            ),
        }
    )


def build_collapsed_rebase_draft(draft_factory: DraftFactory) -> Path:
    """单人枢纽（唯一子页）+ 另一个正文链接到该枢纽的页：折叠时其他页被重定向。"""
    return draft_factory(
        {
            "index.md": (
                "# Topic\n\n> 状态：`已关闭：纯导航页，不表达独立结论`\n\n## 子主题\n\n"
                "- [单人枢纽](pages/solo/index.md)\n"
                "- [其他](pages/other.md)\n"
                "- [结论汇总](conclusions.md)\n"
            ),
            "conclusions.md": TRASH_CONCLUSIONS,
            "pages/solo/index.md": (
                "# 单人枢纽\n\n> 状态：`已关闭：纯导航页`\n>\n"
                "> 父主题：[根索引](../../index.md)\n\n- [唯一](only.md)\n"
            ),
            "pages/solo/only.md": (
                "# 唯一\n\n> 状态：`待讨论`\n>\n> 父主题：[单人枢纽](index.md)\n\n唯一的正文。\n"
            ),
            "pages/other.md": (
                "# 其他\n\n> 状态：`待讨论`\n>\n> 父主题：[根索引](../index.md)\n\n"
                "提到 [单人枢纽](solo/index.md)。\n"
            ),
        }
    )


def test_trash_retains_authoritative_package_without_handoff(
    draft_factory: DraftFactory,
) -> None:
    root = build_trashable_draft(draft_factory)
    now = datetime(2026, 7, 21, tzinfo=UTC)

    result = trash_page(root, Path("pages/obsolete.md"), now=now)

    assert result.phase is OperationPhase.TRASH_RETAINED
    assert result.package.is_dir()
    assert result.restore_expires_at == now + timedelta(days=7)
    assert result.purge_eligible_at == now + timedelta(days=37)
    assert (result.package / "package.json").is_file()
    assert (result.package / "payload/pages/obsolete.md").is_file()
    assert not (root / "pages/obsolete.md").exists()
    assert not (root / ".draft/operation.lock").exists()
    assert check_draft(root).errors == ()


def test_protected_pages_cannot_be_trashed(draft_factory: DraftFactory) -> None:
    root = build_trashable_draft(draft_factory)

    with pytest.raises(DraftError, match="trash.protected"):
        trash_page(root, Path("index.md"), now=datetime(2026, 7, 21, tzinfo=UTC))
    with pytest.raises(DraftError, match="trash.protected"):
        trash_page(root, Path("conclusions.md"), now=datetime(2026, 7, 21, tzinfo=UTC))

    assert list((root / ".draft/trash/outgoing").iterdir()) == []


def test_page_with_children_cannot_be_trashed(draft_factory: DraftFactory) -> None:
    root = build_solo_trash_draft(draft_factory)

    with pytest.raises(DraftError, match="trash.has_children"):
        trash_page(root, Path("pages/solo/index.md"), now=datetime(2026, 7, 21, tzinfo=UTC))


def test_inbound_references_block_trash(draft_factory: DraftFactory) -> None:
    root = build_trashable_draft(draft_factory)
    (root / "pages/other.md").write_text(OTHER + "\n提到 [旧页](obsolete.md)。\n", encoding="utf-8")
    (root / "pages/blocker.md").write_text(
        "# 阻碍\n\n> 状态：`待讨论`\n>\n> 父主题：[根索引](../index.md)\n>\n"
        "> 依赖：[旧页](obsolete.md)\n\n阻碍的正文。\n",
        encoding="utf-8",
    )
    (root / "index.md").write_text(
        TRASH_INDEX.replace(
            "- [结论汇总](conclusions.md)\n",
            "- [阻碍](pages/blocker.md)\n- [结论汇总](conclusions.md)\n",
        ),
        encoding="utf-8",
    )

    with pytest.raises(DraftError, match="trash.inbound_reference") as captured:
        trash_page(root, Path("pages/obsolete.md"), now=datetime(2026, 7, 21, tzinfo=UTC))

    details = " ".join(captured.value.details)
    assert "pages/blocker.md" in details
    assert "pages/other.md" in details
    assert (root / "pages/obsolete.md").exists()


def test_trash_last_child_collapses_hub_in_same_operation(
    draft_factory: DraftFactory,
) -> None:
    root = build_solo_trash_draft(draft_factory)

    result = trash_page(root, Path("pages/solo/only.md"), now=datetime(2026, 7, 21, tzinfo=UTC))

    assert result.phase is OperationPhase.TRASH_RETAINED
    assert (root / "pages/solo.md").is_file()
    assert not (root / "pages/solo").exists()
    assert "- [单人枢纽](pages/solo.md)" in (root / "index.md").read_text(encoding="utf-8")
    assert check_draft(root).errors == ()


# ---------------------------------------------------------------------------
# 恢复认领协议（设计 §13.3）：一次耐久认领、一把锁、同一终态解决器。
# ---------------------------------------------------------------------------

NOW = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)


def _journal_path(root: Path, operation_id: str) -> Path:
    return root / ".draft/operations" / operation_id / "journal.json"


def _read_journal(root: Path, operation_id: str):
    import json

    return json.loads(_journal_path(root, operation_id).read_text(encoding="utf-8"))


def _write_journal(root: Path, operation_id: str, data: dict[str, object]) -> None:
    import json

    path = _journal_path(root, operation_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _rewrite_phase(root: Path, operation_id: str, phase: str) -> None:
    data = _read_journal(root, operation_id)
    data["phase"] = phase
    _write_journal(root, operation_id, data)


def _package_snapshot(root: Path, operation_id: str) -> dict[str, bytes]:
    package = root / ".draft/trash/outgoing" / operation_id
    if not package.is_dir():
        return {}
    return {
        path.relative_to(package).as_posix(): path.read_bytes()
        for path in sorted(package.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def _other_operation_dirs(root: Path, *exclude: str) -> list[Path]:
    operations = root / ".draft/operations"
    return [
        entry
        for entry in sorted(operations.iterdir())
        if entry.is_dir() and entry.name not in exclude
    ]


def test_candidate_trash_then_restore_roundtrips(draft_factory: DraftFactory) -> None:
    """候选根复用 trash/restore 生命周期（计划 Task 11）：恢复按当前根状态执行。"""
    root = draft_factory(
        {
            "index.md": TRASH_INDEX,
            "conclusions.md": TRASH_CONCLUSIONS,
            "pages/obsolete.md": OBSOLETE,
            "pages/other.md": OTHER,
        },
        root_state=RootState.CANDIDATE,
    )
    pending = trash_page(root, Path("pages/obsolete.md"), now=NOW)

    result = restore_retained_trash(root, pending.operation_id, now=NOW)

    assert result.phase is OperationPhase.COMPLETED
    assert (root / "pages/obsolete.md").read_text(encoding="utf-8") == OBSOLETE
    assert not (root / ".draft/operation.lock").exists()
    # 恢复日志保留创建时的根状态作为审计事实。
    restore_journal = read_journal(next((root / ".draft/operations").iterdir()) / "journal.json")
    assert restore_journal.root_state is RootState.CANDIDATE


def test_restore_rejects_concurrent_package_edit(
    draft_factory: DraftFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """restore 读集观察必须覆盖权威包：并发的 package.json 改动使恢复失败而非静默删除。"""
    import draft.operations as ops_module
    from draft.models import OperationPlan
    from draft.operations import Journal

    root = build_trashable_draft(draft_factory)
    pending = trash_page(root, Path("pages/obsolete.md"), now=NOW)
    package = root / ".draft/trash/outgoing" / pending.operation_id

    original_verify = ops_module._verify_plan_observations  # pyright: ignore[reportPrivateUsage]

    def verify_then_edit(root_path: Path, plan: OperationPlan, journal: Journal) -> None:
        package_file = package / "package.json"
        package_file.write_text(
            package_file.read_text(encoding="utf-8") + "\n// 外部修改\n", encoding="utf-8"
        )
        original_verify(root_path, plan, journal)

    monkeypatch.setattr(ops_module, "_verify_plan_observations", verify_then_edit)

    # 读集变化使恢复按领域错误中止，而不是静默完成。
    with pytest.raises(DraftError, match="trash.restore_conflict"):
        restore_retained_trash(root, pending.operation_id, now=NOW)

    # 唯一权威包未被静默删除。
    assert package.exists()
    assert (package / "package.json").is_file()


def test_restore_completes_within_window(draft_factory: DraftFactory) -> None:
    root = build_trashable_draft(draft_factory)
    pending = trash_page(root, Path("pages/obsolete.md"), now=NOW)

    result = restore_retained_trash(root, pending.operation_id, now=NOW)

    assert result.phase is OperationPhase.COMPLETED
    assert (root / "pages/obsolete.md").read_text(encoding="utf-8") == OBSOLETE
    assert "- [旧页](pages/obsolete.md)" in (root / "index.md").read_text(encoding="utf-8")
    assert not (root / ".draft/operation.lock").exists()
    assert not (root / ".draft/trash/outgoing" / pending.operation_id).exists()
    assert check_draft(root).errors == ()


def test_restore_loses_to_expiry_transition_before_claim(
    draft_factory: DraftFactory,
) -> None:
    root = build_trashable_draft(draft_factory)
    pending = trash_page(root, Path("pages/obsolete.md"), now=NOW)
    # 模拟到期推进已把 T 耐久转为 trash_handoff_pending（锁已释放）。
    _rewrite_phase(root, pending.operation_id, "trash_handoff_pending")
    before_package = _package_snapshot(root, pending.operation_id)

    with pytest.raises(DraftError, match="trash.restore_expired"):
        restore_retained_trash(root, pending.operation_id, now=NOW)

    # T 未被改写，权威包原样保留，锁已释放。
    assert _read_journal(root, pending.operation_id)["phase"] == "trash_handoff_pending"
    assert _package_snapshot(root, pending.operation_id) == before_package
    assert not (root / ".draft/operation.lock").exists()
    # 本次未关联的 R 已放弃为 rolled_back。
    r_dirs = _other_operation_dirs(root, pending.operation_id)
    assert len(r_dirs) == 1
    assert _read_journal(root, r_dirs[0].name)["phase"] == "rolled_back"


def test_restore_rolled_back_clears_claim_and_preserves_package(
    draft_factory: DraftFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = build_trashable_draft(draft_factory)
    pending = trash_page(root, Path("pages/obsolete.md"), now=NOW)

    import draft.operations as operations_module

    original_write = operations_module.write_journal

    def inject_concurrent_page(journal: object) -> None:
        original_write(journal)  # type: ignore[arg-type]
        if getattr(journal, "phase", None) is OperationPhase.PREPARED:
            # 并发方把恢复目标页写成了计划后状态（可识别、可回滚）。
            (root / "pages/obsolete.md").write_text(OBSOLETE, encoding="utf-8")

    monkeypatch.setattr("draft.operations.write_journal", inject_concurrent_page)
    result = restore_retained_trash(root, pending.operation_id, now=NOW)
    monkeypatch.undo()

    assert result.phase is OperationPhase.ROLLED_BACK
    t_journal = _read_journal(root, pending.operation_id)
    assert t_journal["phase"] == "trash_retained"
    assert "restore_operation_id" not in t_journal["metadata"]
    assert _package_snapshot(root, pending.operation_id)  # 权威包仍保留
    assert not (root / "pages/obsolete.md").exists()
    assert not (root / ".draft/operation.lock").exists()


def test_restore_conflict_when_page_already_exists(
    draft_factory: DraftFactory,
) -> None:
    root = build_trashable_draft(draft_factory)
    pending = trash_page(root, Path("pages/obsolete.md"), now=NOW)
    # 原路径已被外部重建：恢复计划在认领后构建时发现冲突。
    (root / "pages/obsolete.md").write_text("# 重建\n", encoding="utf-8")

    with pytest.raises(DraftError, match="trash.restore_conflict"):
        restore_retained_trash(root, pending.operation_id, now=NOW)

    t_journal = _read_journal(root, pending.operation_id)
    assert t_journal["phase"] == "trash_retained"
    assert "restore_operation_id" not in t_journal["metadata"]
    assert _package_snapshot(root, pending.operation_id)
    assert not (root / ".draft/operation.lock").exists()


def test_second_restore_lock_loser_abandons_its_unlinked_journal(
    draft_factory: DraftFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = build_trashable_draft(draft_factory)
    pending = trash_page(root, Path("pages/obsolete.md"), now=NOW)

    # R1 认领 T 后、执行前崩溃：T -> R1 已耐久，R1 持锁。
    def crash_execute(plan: object, **kwargs: object) -> object:
        raise RuntimeError("injected crash before executing restore plan")

    monkeypatch.setattr("draft.trash.execute_preallocated_plan", crash_execute)
    with pytest.raises(RuntimeError, match="injected crash"):
        restore_retained_trash(root, pending.operation_id, now=NOW)
    monkeypatch.undo()

    r1_dirs = _other_operation_dirs(root, pending.operation_id)
    assert len(r1_dirs) == 1
    r1_id = r1_dirs[0].name
    assert (
        _read_journal(root, pending.operation_id)["metadata"].get("restore_operation_id") == r1_id
    )
    before_t = _read_journal(root, pending.operation_id)
    before_package = _package_snapshot(root, pending.operation_id)

    # R2 启动：写锁仍被 R1 持有，R2 必须放弃自己并报告 recovery_required。
    with pytest.raises(DraftError, match=CheckCode.OPERATION_RECOVERY_REQUIRED.value):
        restore_retained_trash(root, pending.operation_id, now=NOW)

    r2_dirs = [d for d in _other_operation_dirs(root, pending.operation_id, r1_id)]
    assert len(r2_dirs) == 1
    r2 = _read_journal(root, r2_dirs[0].name)
    assert r2["phase"] == "rolled_back"
    # T -> R1 与权威包字节不变。
    assert _read_journal(root, pending.operation_id) == before_t
    assert _package_snapshot(root, pending.operation_id) == before_package


def test_foreign_restore_claim_seen_after_lock_is_recovery_required(
    draft_factory: DraftFactory,
) -> None:
    root = build_trashable_draft(draft_factory)
    pending = trash_page(root, Path("pages/obsolete.md"), now=NOW)

    # 构造合法的 T <-> R1 关联（R1 已回滚，锁已释放），T 仍为 trash_retained。
    r1_id = "2" * 32
    _write_journal(
        root,
        r1_id,
        {
            "journal_version": JOURNAL_VERSION,
            "operation_id": r1_id,
            "kind": "restore",
            "phase": "rolled_back",
            "root_state": "registered",
            "observations": [],
            "mutations": [],
            "remove_empty_directories": [],
            "metadata": {"restores_trash_operation": pending.operation_id},
        },
    )
    t_data = _read_journal(root, pending.operation_id)
    t_data["metadata"]["restore_operation_id"] = r1_id
    _write_journal(root, pending.operation_id, t_data)
    before_package = _package_snapshot(root, pending.operation_id)

    # R2 取得锁后发现 T 指向 R1：合法可恢复关联 -> recovery_required，不覆盖 T。
    with pytest.raises(DraftError, match=CheckCode.OPERATION_RECOVERY_REQUIRED.value):
        restore_retained_trash(root, pending.operation_id, now=NOW)

    assert (
        _read_journal(root, pending.operation_id)["metadata"].get("restore_operation_id") == r1_id
    )
    assert _package_snapshot(root, pending.operation_id) == before_package
    r2_dirs = [d for d in _other_operation_dirs(root, pending.operation_id, r1_id)]
    assert _read_journal(root, r2_dirs[0].name)["phase"] == "rolled_back"


def test_foreign_restore_claim_with_broken_link_is_inconsistent(
    draft_factory: DraftFactory,
) -> None:
    root = build_trashable_draft(draft_factory)
    pending = trash_page(root, Path("pages/obsolete.md"), now=NOW)

    # R1 反向关联错配：T -> R1，但 R1 指向另一个回收操作。
    r1_id = "2" * 32
    _write_journal(
        root,
        r1_id,
        {
            "journal_version": JOURNAL_VERSION,
            "operation_id": r1_id,
            "kind": "restore",
            "phase": "rolled_back",
            "root_state": "registered",
            "observations": [],
            "mutations": [],
            "remove_empty_directories": [],
            "metadata": {"restores_trash_operation": "3" * 32},
        },
    )
    t_data = _read_journal(root, pending.operation_id)
    t_data["metadata"]["restore_operation_id"] = r1_id
    _write_journal(root, pending.operation_id, t_data)

    with pytest.raises(DraftError, match=CheckCode.OPERATION_INCONSISTENT_STATE.value):
        restore_retained_trash(root, pending.operation_id, now=NOW)


def test_package_evidence_mismatch_is_not_restore_conflict(
    draft_factory: DraftFactory,
) -> None:
    root = build_trashable_draft(draft_factory)
    pending = trash_page(root, Path("pages/obsolete.md"), now=NOW)
    # 破坏权威包证据：在认领前就让包无法由 T 锚定验证。
    package_json = root / ".draft/trash/outgoing" / pending.operation_id / "package.json"
    package_json.write_text("corrupt\n", encoding="utf-8")

    with pytest.raises(DraftError, match=CheckCode.TRASH_PACKAGE_CORRUPT.value):
        restore_retained_trash(root, pending.operation_id, now=NOW)

    # T 未被改写，包证据原样保留（损坏现场），锁已释放。
    assert _read_journal(root, pending.operation_id)["phase"] == "trash_retained"
    assert package_json.read_bytes() == b"corrupt\n"
    assert not (root / ".draft/operation.lock").exists()


def test_crash_between_r_completed_and_t_rolled_back_recovers(
    draft_factory: DraftFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = build_trashable_draft(draft_factory)
    pending = trash_page(root, Path("pages/obsolete.md"), now=NOW)

    # 在 R=completed 证明后、T 解关联前崩溃：保留 R 锁与权威包。
    def crash_verify(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected crash after R completed before T resolved")

    monkeypatch.setattr("draft.trash.verify_restore_after_state", crash_verify)
    with pytest.raises(RuntimeError, match="injected crash"):
        restore_retained_trash(root, pending.operation_id, now=NOW)
    monkeypatch.undo()

    # 崩溃现场：R=completed 持锁，T 仍 trash_retained 带认领，权威包仍在。
    r_dirs = _other_operation_dirs(root, pending.operation_id)
    r_id = r_dirs[0].name
    assert _read_journal(root, r_id)["phase"] == "completed"
    assert (root / ".draft/operation.lock").exists()
    assert _read_journal(root, pending.operation_id)["phase"] == "trash_retained"
    assert _package_snapshot(root, pending.operation_id)

    # 恢复器用同一终态解决器解释现场：证明证据、转 T、清理包、释放锁。
    report = recover_draft(root)
    assert any(result.phase is OperationPhase.COMPLETED for result in report.results)
    assert _read_journal(root, pending.operation_id)["phase"] == "rolled_back"
    assert not (root / ".draft/trash/outgoing" / pending.operation_id).exists()
    assert not (root / ".draft/operation.lock").exists()
    assert (root / "pages/obsolete.md").read_text(encoding="utf-8") == OBSOLETE


def test_completed_restore_with_extra_mutation_is_rejected(
    draft_factory: DraftFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R=completed 夹带额外变更（超出 T 结构变更集合）时，终态证明必须拒绝并保留权威包。"""
    root = build_trashable_draft(draft_factory)
    pending = trash_page(root, Path("pages/obsolete.md"), now=NOW)

    # 先让正常恢复完成 R=completed，但在 T 解关联前崩溃以保留现场。
    def crash_verify(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected crash after R completed")

    monkeypatch.setattr("draft.trash.verify_restore_after_state", crash_verify)
    with pytest.raises(RuntimeError, match="injected crash"):
        restore_retained_trash(root, pending.operation_id, now=NOW)
    monkeypatch.undo()

    r_id = _other_operation_dirs(root, pending.operation_id)[0].name
    # 伪造：在合法 R completed 的变更集合里夹带一个额外删除变更。
    r_data = _read_journal(root, r_id)
    r_data["mutations"].append(
        {
            "path": "pages/other.md",
            "kind": "delete",
            "before_sha256": "f" * 64,
            "after_sha256": None,
            "original": None,
            "staged": None,
            "applied": True,
        }
    )
    _write_journal(root, r_id, r_data)
    before_package = _package_snapshot(root, pending.operation_id)

    with pytest.raises(DraftError, match=CheckCode.OPERATION_INCONSISTENT_STATE.value):
        recover_draft(root)

    # 权威包、锁、T 全部保留：绝不因伪造 completed 删除权威包。
    assert _package_snapshot(root, pending.operation_id) == before_package
    assert (root / ".draft/operation.lock").exists()
    assert _read_journal(root, pending.operation_id)["phase"] == "trash_retained"


def test_completed_restore_with_wrong_parent_content_is_rejected(
    draft_factory: DraftFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R 伪造父页 after（自报哈希与活文件一致但内容多了额外字节）时，独立证明必须拒绝。"""
    import hashlib

    root = build_trashable_draft(draft_factory)
    pending = trash_page(root, Path("pages/obsolete.md"), now=NOW)

    def crash_verify(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected crash after R completed")

    monkeypatch.setattr("draft.trash.verify_restore_after_state", crash_verify)
    with pytest.raises(RuntimeError, match="injected crash"):
        restore_retained_trash(root, pending.operation_id, now=NOW)
    monkeypatch.undo()

    r_id = _other_operation_dirs(root, pending.operation_id)[0].name
    # 伪造：活父页在正确还原基础上追加额外内容，并把 R 的父页 after 改成其哈希，
    # 使“活文件 == R 自报 after”这一自证成立，但父页已不再是回收后内容 + 页面导航。
    parent = root / "index.md"
    forged_parent = parent.read_bytes() + "\n伪造的额外内容。\n".encode()
    parent.write_bytes(forged_parent)
    forged_after = hashlib.sha256(forged_parent).hexdigest()
    r_data = _read_journal(root, r_id)
    for mutation in r_data["mutations"]:
        if mutation["path"] == "index.md" and mutation["kind"] == "write":
            mutation["after_sha256"] = forged_after
    _write_journal(root, r_id, r_data)
    before_package = _package_snapshot(root, pending.operation_id)

    with pytest.raises(DraftError, match=CheckCode.OPERATION_INCONSISTENT_STATE.value):
        recover_draft(root)

    assert _package_snapshot(root, pending.operation_id) == before_package
    assert (root / ".draft/operation.lock").exists()
    assert _read_journal(root, pending.operation_id)["phase"] == "trash_retained"


def test_completed_restore_with_forged_extra_empty_section_is_rejected(
    draft_factory: DraftFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """父页被追加一个与本次导航无关的空 ``## 子主题`` 小节时，独立证明必须拒绝。

    回归 P1：旧实现会清洗全页所有空 ``## 子主题`` 小节，把这个伪造小节一并抹掉，
    使证明哈希仍然吻合；新实现只移除规范导航所在的那一个小节，伪造小节必须留痕。
    """
    import hashlib

    root = build_trashable_draft(draft_factory)
    pending = trash_page(root, Path("pages/obsolete.md"), now=NOW)

    def crash_verify(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected crash after R completed")

    monkeypatch.setattr("draft.trash.verify_restore_after_state", crash_verify)
    with pytest.raises(RuntimeError, match="injected crash"):
        restore_retained_trash(root, pending.operation_id, now=NOW)
    monkeypatch.undo()

    r_id = _other_operation_dirs(root, pending.operation_id)[0].name
    # 伪造：在正确还原的父页末尾追加一个额外的空「## 子主题」小节（不含规范导航），
    # 并把 R 的父页 after 改成其哈希，使逐变更校验成立，只有独立证明能识破。
    parent = root / "index.md"
    forged_parent = parent.read_bytes() + "\n## 子主题\n".encode()
    parent.write_bytes(forged_parent)
    forged_after = hashlib.sha256(forged_parent).hexdigest()
    r_data = _read_journal(root, r_id)
    for mutation in r_data["mutations"]:
        if mutation["path"] == "index.md" and mutation["kind"] == "write":
            mutation["after_sha256"] = forged_after
    _write_journal(root, r_id, r_data)
    before_package = _package_snapshot(root, pending.operation_id)

    with pytest.raises(DraftError, match=CheckCode.OPERATION_INCONSISTENT_STATE.value):
        recover_draft(root)

    assert _package_snapshot(root, pending.operation_id) == before_package
    assert (root / ".draft/operation.lock").exists()
    assert _read_journal(root, pending.operation_id)["phase"] == "trash_retained"


def build_sole_item_section_draft(draft_factory: DraftFactory) -> Path:
    """父页有已存在的 ``## 子主题`` 小节、且规范导航是小节内唯一一项。"""
    return draft_factory(
        {
            "index.md": (
                "# Topic\n\n> 状态：`已关闭：纯导航页，不表达独立结论`\n\n"
                "## 子主题\n\n- [唯一](pages/only.md)\n"
            ),
            "pages/only.md": (
                "# 唯一\n\n> 状态：`待讨论`\n>\n> 父主题：[根索引](../index.md)\n\n唯一的正文。\n"
            ),
        }
    )


def build_bare_list_parent_draft(draft_factory: DraftFactory) -> Path:
    """父页没有 ``## 子主题`` 小节，规范导航是直接列表项（恢复时 append 新建小节）。"""
    return draft_factory(
        {
            "index.md": (
                "# Topic\n\n> 状态：`已关闭：纯导航页，不表达独立结论`\n\n- [唯一](pages/only.md)\n"
            ),
            "pages/only.md": (
                "# 唯一\n\n> 状态：`待讨论`\n>\n> 父主题：[根索引](../index.md)\n\n唯一的正文。\n"
            ),
        }
    )


def test_restore_with_preexisting_sole_item_section_completes(
    draft_factory: DraftFactory,
) -> None:
    """小节原已存在（回收后留空小节头）：只去导航即可回到 parent_sha256，恢复必须完成。"""
    root = build_sole_item_section_draft(draft_factory)
    pending = trash_page(root, Path("pages/only.md"), now=NOW)
    # 回收后父页保留空的「## 子主题」小节头。
    assert "## 子主题" in (root / "index.md").read_text(encoding="utf-8")

    result = restore_retained_trash(root, pending.operation_id, now=NOW)

    assert result.phase is OperationPhase.COMPLETED
    assert (root / "pages/only.md").is_file()
    assert check_draft(root).errors == ()


def test_restore_with_append_created_section_completes(
    draft_factory: DraftFactory,
) -> None:
    """父页原本没有小节（append 新建）：连新建空小节一并去掉才回到 parent_sha256。"""
    root = build_bare_list_parent_draft(draft_factory)
    pending = trash_page(root, Path("pages/only.md"), now=NOW)
    assert "## 子主题" not in (root / "index.md").read_text(encoding="utf-8")

    result = restore_retained_trash(root, pending.operation_id, now=NOW)

    assert result.phase is OperationPhase.COMPLETED
    restored = (root / "index.md").read_text(encoding="utf-8")
    assert "## 子主题" in restored
    assert "- [唯一](pages/only.md)" in restored
    assert check_draft(root).errors == ()


def test_completed_restore_with_trailing_content_on_nav_line_is_rejected(
    draft_factory: DraftFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """规范导航行尾部被追加内容时，独立证明必须拒绝。

    回归 P1：旧实现把导航行整体排除在「小节是否有其他内容」判定之外，同行追加的
    字节会被连同小节一起抹掉；新实现先对导航行应用移除编辑再检查剩余字节。
    """
    import hashlib

    root = build_sole_item_section_draft(draft_factory)
    pending = trash_page(root, Path("pages/only.md"), now=NOW)

    def crash_verify(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected crash after R completed")

    monkeypatch.setattr("draft.trash.verify_restore_after_state", crash_verify)
    with pytest.raises(RuntimeError, match="injected crash"):
        restore_retained_trash(root, pending.operation_id, now=NOW)
    monkeypatch.undo()

    r_id = _other_operation_dirs(root, pending.operation_id)[0].name
    # 伪造：在规范导航行尾部追加内容（行不再是纯导航），并把 R 的父页 after 改成其哈希。
    parent = root / "index.md"
    forged_parent = parent.read_bytes().replace(
        b"- [\xe5\x94\xaf\xe4\xb8\x80](pages/only.md)",
        b"- [\xe5\x94\xaf\xe4\xb8\x80](pages/only.md) \xe9\x99\x84\xe6\xb3\xa8",
    )
    assert forged_parent != parent.read_bytes()
    parent.write_bytes(forged_parent)
    forged_after = hashlib.sha256(forged_parent).hexdigest()
    r_data = _read_journal(root, r_id)
    for mutation in r_data["mutations"]:
        if mutation["path"] == "index.md" and mutation["kind"] == "write":
            mutation["after_sha256"] = forged_after
    _write_journal(root, r_id, r_data)
    before_package = _package_snapshot(root, pending.operation_id)

    with pytest.raises(DraftError, match=CheckCode.OPERATION_INCONSISTENT_STATE.value):
        recover_draft(root)

    assert _package_snapshot(root, pending.operation_id) == before_package
    assert (root / ".draft/operation.lock").exists()
    assert _read_journal(root, pending.operation_id)["phase"] == "trash_retained"


def test_collapsed_restore_restores_retargeted_links(
    draft_factory: DraftFactory,
) -> None:
    """折叠枢纽恢复：被重定向到其他页的链接也随枢纽一起还原，且独立证明通过。"""
    root = build_collapsed_rebase_draft(draft_factory)

    pending = trash_page(root, Path("pages/solo/only.md"), now=NOW)
    # 折叠后：枢纽降级为叶页，其他页链接被重定向到叶页。
    assert (root / "pages/solo.md").is_file()
    assert not (root / "pages/solo").exists()
    assert "(solo.md)" in (root / "pages/other.md").read_text(encoding="utf-8")

    result = restore_retained_trash(root, pending.operation_id, now=NOW)

    assert result.phase is OperationPhase.COMPLETED
    # 枢纽与子页恢复，其他页链接被重定向回枢纽。
    assert (root / "pages/solo/index.md").is_file()
    assert (root / "pages/solo/only.md").is_file()
    other_text = (root / "pages/other.md").read_text(encoding="utf-8")
    assert "[单人枢纽](solo/index.md)" in other_text
    assert not (root / ".draft/trash/outgoing" / pending.operation_id).exists()
    assert check_draft(root).errors == ()


def test_collapsed_restore_forged_rebase_page_is_rejected(
    draft_factory: DraftFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """折叠恢复中被重定向的其他页内容被篡改（自报 after 一致）时，逆变换证明必须拒绝。"""
    import hashlib

    root = build_collapsed_rebase_draft(draft_factory)
    pending = trash_page(root, Path("pages/solo/only.md"), now=NOW)

    def crash_verify(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected crash after R completed")

    monkeypatch.setattr("draft.trash.verify_restore_after_state", crash_verify)
    with pytest.raises(RuntimeError, match="injected crash"):
        restore_retained_trash(root, pending.operation_id, now=NOW)
    monkeypatch.undo()

    r_id = _other_operation_dirs(root, pending.operation_id)[0].name
    # 伪造：other.md 追加额外内容（仍保留枢纽链接），并把 R 对 other.md 的 after 改成其哈希。
    other = root / "pages/other.md"
    forged_other = other.read_bytes() + "\n伪造的额外内容。\n".encode()
    other.write_bytes(forged_other)
    forged_after = hashlib.sha256(forged_other).hexdigest()
    r_data = _read_journal(root, r_id)
    for mutation in r_data["mutations"]:
        if mutation["path"] == "pages/other.md" and mutation["kind"] == "write":
            mutation["after_sha256"] = forged_after
    _write_journal(root, r_id, r_data)
    before_package = _package_snapshot(root, pending.operation_id)

    with pytest.raises(DraftError, match=CheckCode.OPERATION_INCONSISTENT_STATE.value):
        recover_draft(root)

    assert _package_snapshot(root, pending.operation_id) == before_package
    assert (root / ".draft/operation.lock").exists()
    assert _read_journal(root, pending.operation_id)["phase"] == "trash_retained"


def _acquire_restore_lock(root: Path, restore_id: str) -> None:
    from draft.operations import acquire_lock

    acquire_lock(root, restore_id)


def test_completed_restore_without_matching_mutations_preserves_package(
    draft_factory: DraftFactory,
) -> None:
    root = build_trashable_draft(draft_factory)
    pending = trash_page(root, Path("pages/obsolete.md"), now=NOW)

    # 构造 R=completed 但 mutation 证据无法证明页面/父 after 态。
    r_id = "5" * 32
    _write_journal(
        root,
        r_id,
        {
            "journal_version": JOURNAL_VERSION,
            "operation_id": r_id,
            "kind": "restore",
            "phase": "completed",
            "root_state": "registered",
            "observations": [],
            "mutations": [],
            "remove_empty_directories": [],
            "metadata": {"restores_trash_operation": pending.operation_id},
        },
    )
    t_data = _read_journal(root, pending.operation_id)
    t_data["metadata"]["restore_operation_id"] = r_id
    _write_journal(root, pending.operation_id, t_data)
    _acquire_restore_lock(root, r_id)
    before_package = _package_snapshot(root, pending.operation_id)

    with pytest.raises(DraftError, match=CheckCode.OPERATION_INCONSISTENT_STATE.value):
        recover_draft(root)

    # 认领、锁、权威包全部保留。
    assert (root / ".draft/operation.lock").exists()
    assert _package_snapshot(root, pending.operation_id) == before_package
    assert _read_journal(root, pending.operation_id)["phase"] == "trash_retained"


def test_completed_restore_with_mismatched_journal_identity_preserves_package(
    draft_factory: DraftFactory,
) -> None:
    root = build_trashable_draft(draft_factory)
    pending = trash_page(root, Path("pages/obsolete.md"), now=NOW)

    r_id = "6" * 32
    _write_journal(
        root,
        r_id,
        {
            "journal_version": JOURNAL_VERSION,
            "operation_id": "7" * 32,  # 与目录名不一致
            "kind": "restore",
            "phase": "completed",
            "root_state": "registered",
            "observations": [],
            "mutations": [],
            "remove_empty_directories": [],
            "metadata": {"restores_trash_operation": pending.operation_id},
        },
    )
    t_data = _read_journal(root, pending.operation_id)
    t_data["metadata"]["restore_operation_id"] = r_id
    _write_journal(root, pending.operation_id, t_data)
    _acquire_restore_lock(root, r_id)
    before_package = _package_snapshot(root, pending.operation_id)

    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        recover_draft(root)

    assert (root / ".draft/operation.lock").exists()
    assert _package_snapshot(root, pending.operation_id) == before_package


def test_proven_unapplied_external_change_maps_restore_conflict(
    draft_factory: DraftFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = build_trashable_draft(draft_factory)
    pending = trash_page(root, Path("pages/obsolete.md"), now=NOW)

    import draft.trash as trash_module

    original_execute = trash_module.execute_preallocated_plan

    def change_parent_then_execute(plan: object, **kwargs: object) -> object:
        # 在 build_restore_plan 校验父摘要之后、prepare 之前改写父文件：external_change。
        (root / "index.md").write_text(
            (root / "index.md").read_text(encoding="utf-8") + "\n并发编辑。\n",
            encoding="utf-8",
        )
        return original_execute(plan, **kwargs)  # type: ignore[arg-type,call-arg]

    monkeypatch.setattr("draft.trash.execute_preallocated_plan", change_parent_then_execute)
    with pytest.raises(DraftError, match="trash.restore_conflict"):
        restore_retained_trash(root, pending.operation_id, now=NOW)
    monkeypatch.undo()

    t_journal = _read_journal(root, pending.operation_id)
    assert t_journal["phase"] == "trash_retained"
    assert "restore_operation_id" not in t_journal["metadata"]
    assert _package_snapshot(root, pending.operation_id)
    assert not (root / ".draft/operation.lock").exists()


def test_unexplained_external_change_preserves_claim_lock_and_package(
    draft_factory: DraftFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = build_trashable_draft(draft_factory)
    pending = trash_page(root, Path("pages/obsolete.md"), now=NOW)

    import draft.operations as operations_module

    original_write = operations_module.write_journal

    def inject_unrecognizable_page(journal: object) -> None:
        original_write(journal)  # type: ignore[arg-type]
        if getattr(journal, "phase", None) is OperationPhase.VALIDATING:
            # 已应用页面被改成 before/after 都无法识别的状态：回滚无法分类。
            (root / "pages/obsolete.md").write_text("不可识别的并发垃圾内容\n", encoding="utf-8")

    monkeypatch.setattr("draft.operations.write_journal", inject_unrecognizable_page)
    with pytest.raises(DraftError, match="operation.external_change"):
        restore_retained_trash(root, pending.operation_id, now=NOW)
    monkeypatch.undo()

    # 无法证明安全：保留认领、锁、权威包，交给 recover。
    assert (root / ".draft/operation.lock").exists()
    assert _package_snapshot(root, pending.operation_id)
    t_journal = _read_journal(root, pending.operation_id)
    assert t_journal["phase"] == "trash_retained"
    assert "restore_operation_id" in t_journal["metadata"]
