from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any, cast

import pytest

from draft.diagnostic_codes import CheckCode
from draft.errors import DraftError
from draft.models import TrashResult
from draft.operations import operation_journal_path, read_journal
from draft.trash import (
    OPERATIONS_RELATIVE,
    OUTGOING_RELATIVE,
    trash_page,
)
from draft.trash_package import load_verified_package
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


def _retained_trash(root: Path) -> TrashResult:
    return trash_page(root, Path("pages/obsolete.md"), now=datetime(2026, 7, 21, tzinfo=UTC))


def _clean_with_authorization(root: Path, operation_id: str, *, phase: str = "rolled_back") -> None:
    """把 T 日志重写为已提交阶段、幂等持锁后经清理授权清理（M1 门：测试必须用已提交授权）。"""
    import json as json_module

    from draft.operations import acquire_lock, read_lock
    from draft.trash_package import clean_authoritative_package, cleanup_authorization

    journal_path = root / OPERATIONS_RELATIVE / operation_id / "journal.json"
    raw = json_module.loads(journal_path.read_text(encoding="utf-8"))
    raw["phase"] = phase
    journal_path.write_text(json_module.dumps(raw, ensure_ascii=False), encoding="utf-8")
    if read_lock(root) != operation_id:
        acquire_lock(root, operation_id)
    clean_authoritative_package(
        root, cleanup_authorization(root, operation_id, lock_operation_id=operation_id)
    )


def _verify_package(root: Path, operation_id: str) -> None:
    """直接以原回收日志 T 为信任根校验权威删除包；不经过恢复流程。"""
    journal = read_journal(root / OPERATIONS_RELATIVE / operation_id / "journal.json")
    load_verified_package(root, journal)


def test_package_rejects_extra_empty_directory(draft_factory: DraftFactory) -> None:
    """权威包内额外的空目录是损坏：load_verified_package 必须拒绝（唯一权威包不变量）。"""
    from draft.operations import operation_journal_path

    root = build_trashable_draft(draft_factory)
    pending = _retained_trash(root)
    (root / ".draft/trash/outgoing" / pending.operation_id / "empty").mkdir()

    journal = read_journal(operation_journal_path(root, pending.operation_id))
    with pytest.raises(DraftError, match=CheckCode.TRASH_PACKAGE_CORRUPT.value):
        load_verified_package(root, journal)


def _package_snapshot(package_dir: Path) -> dict[str, bytes]:
    return {
        path.relative_to(package_dir).as_posix(): path.read_bytes()
        for path in sorted(package_dir.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def _rewrite_manifest(package_dir: Path, **changes: object) -> None:
    manifest = package_dir / "package.json"
    data = cast("dict[str, object]", json.loads(manifest.read_text(encoding="utf-8")))
    data.update(changes)
    manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _anchor_manifest_to_journal(root: Path, operation_id: str) -> None:
    """把日志中 package.json 的 after_sha256 重锚到当前包内容，模拟自洽但恶意的日志+包对。"""
    journal_path = root / OPERATIONS_RELATIVE / operation_id / "journal.json"
    journal = cast("dict[str, object]", json.loads(journal_path.read_text(encoding="utf-8")))
    manifest_relative = OUTGOING_RELATIVE / operation_id / "package.json"
    digest = hashlib.sha256((root / manifest_relative).read_bytes()).hexdigest()
    mutations = cast("list[dict[str, object]]", journal["mutations"])
    for mutation in mutations:
        if mutation["path"] == manifest_relative.as_posix():
            mutation["after_sha256"] = digest
    journal_path.write_text(json.dumps(journal, ensure_ascii=False, indent=2), encoding="utf-8")


class _ExternalReadSentinel:
    """记录对某一精确路径的任何 stat/open/read_bytes 触达；正常固定包读取不受影响。"""

    def __init__(self, target: Path) -> None:
        self._target = os.path.normpath(str(target))
        self.hits: list[str] = []

    def _touches(self, path: Path) -> bool:
        return os.path.normpath(str(path)) == self._target

    def install(self, monkeypatch: pytest.MonkeyPatch) -> _ExternalReadSentinel:
        original_stat = Path.stat
        original_open = Path.open
        original_read_bytes = Path.read_bytes
        sentinel = self

        def stat(self: Path, *, follow_symlinks: bool = True) -> os.stat_result:
            # lstat (follow_symlinks=False) 是检测符号链接的安全机制，不触达目标；
            # 只有跟随式 stat 才会读取越界目标，需要记录。
            if follow_symlinks and sentinel._touches(self):
                sentinel.hits.append("stat")
            return original_stat(self, follow_symlinks=follow_symlinks)

        def open(
            self: Path,
            mode: str = "r",
            buffering: int = -1,
            encoding: str | None = None,
            errors: str | None = None,
            newline: str | None = None,
        ) -> IO[Any]:
            if sentinel._touches(self):
                sentinel.hits.append("open")
            return original_open(self, mode, buffering, encoding, errors, newline)

        def read_bytes(self: Path) -> bytes:
            if sentinel._touches(self):
                sentinel.hits.append("read_bytes")
            return original_read_bytes(self)

        monkeypatch.setattr(Path, "stat", stat)
        monkeypatch.setattr(Path, "open", open)
        monkeypatch.setattr(Path, "read_bytes", read_bytes)
        return self


def test_package_rejects_absolute_payload_before_read(
    draft_factory: DraftFactory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = build_trashable_draft(draft_factory)
    pending = _retained_trash(root)
    package_dir = root / OUTGOING_RELATIVE / pending.operation_id
    external = tmp_path / "external-payload.md"
    external.write_bytes(b"external bytes that must never be read")
    sentinel = _ExternalReadSentinel(external).install(monkeypatch)
    _rewrite_manifest(package_dir, payload=str(external))
    _anchor_manifest_to_journal(root, pending.operation_id)
    before = _package_snapshot(package_dir)

    with pytest.raises(DraftError, match=CheckCode.TRASH_PACKAGE_CORRUPT.value):
        _verify_package(root, pending.operation_id)

    assert sentinel.hits == []
    assert _package_snapshot(package_dir) == before
    assert (package_dir / "payload/pages/obsolete.md").read_bytes() == OBSOLETE.encode("utf-8")
    assert external.read_bytes() == b"external bytes that must never be read"


def test_package_rejects_parent_escape_before_read(
    draft_factory: DraftFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = build_trashable_draft(draft_factory)
    pending = _retained_trash(root)
    package_dir = root / OUTGOING_RELATIVE / pending.operation_id
    outside = package_dir.parent / "outside.md"
    outside.write_bytes(b"escape target that must never be read")
    sentinel = _ExternalReadSentinel(outside).install(monkeypatch)
    _rewrite_manifest(package_dir, payload="../outside.md")
    _anchor_manifest_to_journal(root, pending.operation_id)
    before = _package_snapshot(package_dir)

    with pytest.raises(DraftError, match=CheckCode.TRASH_PACKAGE_CORRUPT.value):
        _verify_package(root, pending.operation_id)

    assert sentinel.hits == []
    assert _package_snapshot(package_dir) == before
    assert outside.read_bytes() == b"escape target that must never be read"


def test_package_rejects_self_reported_hash_not_anchored_to_trash_journal(
    draft_factory: DraftFactory,
) -> None:
    root = build_trashable_draft(draft_factory)
    pending = _retained_trash(root)
    package_dir = root / OUTGOING_RELATIVE / pending.operation_id
    replacement = OBSOLETE.replace("旧页的正文。", "被替换的正文。")
    (package_dir / "payload/pages/obsolete.md").write_text(replacement, encoding="utf-8")
    # 清单内部自洽（payload 与 page_sha256 同时改写），但与原始回收日志 T 不一致。
    _rewrite_manifest(
        package_dir, page_sha256=hashlib.sha256(replacement.encode("utf-8")).hexdigest()
    )
    before = _package_snapshot(package_dir)

    with pytest.raises(DraftError, match=CheckCode.TRASH_PACKAGE_CORRUPT.value):
        _verify_package(root, pending.operation_id)

    assert _package_snapshot(package_dir) == before
    assert not (root / "pages/obsolete.md").exists()


def test_package_rejects_operation_id_or_symlink_mismatch(
    draft_factory: DraftFactory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = build_trashable_draft(draft_factory)
    pending = _retained_trash(root)
    package_dir = root / OUTGOING_RELATIVE / pending.operation_id
    original = _package_snapshot(package_dir)

    # 情形一：包目录与 T.operation_id 不匹配（权威目录缺失），必须先失败。
    moved = package_dir.with_name("b" * 32)
    package_dir.rename(moved)
    with pytest.raises(DraftError, match=CheckCode.TRASH_PACKAGE_CORRUPT.value):
        _verify_package(root, pending.operation_id)
    assert _package_snapshot(moved) == original
    moved.rename(package_dir)

    # 情形二：清单控制路径上的符号链接，必须在触达目标 I/O 之前失败。
    payload = package_dir / "payload/pages/obsolete.md"
    external = tmp_path / "external.md"
    external.write_bytes(payload.read_bytes())
    sentinel = _ExternalReadSentinel(payload).install(monkeypatch)
    payload.unlink()
    payload.symlink_to(external)

    with pytest.raises(DraftError, match=CheckCode.PATH_SYMLINK_ESCAPE.value):
        _verify_package(root, pending.operation_id)

    assert sentinel.hits == []
    assert payload.is_symlink()
    assert (package_dir / "package.json").read_bytes() == original["package.json"]


def test_package_json_as_directory_is_corrupt(draft_factory: DraftFactory) -> None:
    """package.json 被替换成目录：报稳定损坏，而非原始 IsADirectoryError。

    叶节点类型必须在摘要触达前验证：目录、设备、FIFO 等都不是权威包文件。
    """
    from draft.operations import operation_journal_path

    root = build_trashable_draft(draft_factory)
    pending = _retained_trash(root)
    package_json = root / ".draft/trash/outgoing" / pending.operation_id / "package.json"
    package_json.unlink()
    package_json.mkdir()

    journal = read_journal(operation_journal_path(root, pending.operation_id))
    with pytest.raises(DraftError, match=CheckCode.TRASH_PACKAGE_CORRUPT.value):
        load_verified_package(root, journal)


def test_payload_missing_is_corrupt(draft_factory: DraftFactory) -> None:
    """payload 缺失：报稳定损坏，而非原始 FileNotFoundError。"""
    from draft.operations import operation_journal_path

    root = build_trashable_draft(draft_factory)
    pending = _retained_trash(root)
    payload = root / ".draft/trash/outgoing" / pending.operation_id / "payload/pages/obsolete.md"
    payload.unlink()

    journal = read_journal(operation_journal_path(root, pending.operation_id))
    with pytest.raises(DraftError, match=CheckCode.TRASH_PACKAGE_CORRUPT.value):
        load_verified_package(root, journal)


def test_non_utf8_manifest_with_anchored_journal_is_corrupt(
    draft_factory: DraftFactory,
) -> None:
    """清单字节与日志锚定一致但无法按 UTF-8 解码：报稳定损坏，而非 UnicodeDecodeError。"""
    from draft.operations import operation_journal_path

    root = build_trashable_draft(draft_factory)
    pending = _retained_trash(root)
    manifest = root / ".draft/trash/outgoing" / pending.operation_id / "package.json"
    manifest.write_bytes(b"\xff\xfe\x00garbage")
    _anchor_manifest_to_journal(root, pending.operation_id)

    journal = read_journal(operation_journal_path(root, pending.operation_id))
    with pytest.raises(DraftError, match=CheckCode.TRASH_PACKAGE_CORRUPT.value):
        load_verified_package(root, journal)


def test_check_reports_package_json_directory_as_stable_diagnostic(
    draft_factory: DraftFactory,
) -> None:
    """check_draft 对损坏包返回稳定诊断，不把原始文件系统异常抛给调用方。"""
    from draft.checker import check_draft

    root = build_trashable_draft(draft_factory)
    pending = _retained_trash(root)
    package_json = root / ".draft/trash/outgoing" / pending.operation_id / "package.json"
    package_json.unlink()
    package_json.mkdir()

    report = check_draft(root)
    assert CheckCode.TRASH_PACKAGE_CORRUPT.value in tuple(issue.code for issue in report.errors)


def _leaf_snapshot(package_dir: Path) -> dict[str, bytes]:
    return {
        path.relative_to(package_dir).as_posix(): path.read_bytes()
        for path in package_dir.rglob("*")
        if path.is_file() and not path.is_symlink()
    }


@pytest.mark.parametrize("level", ["payload", "payload/pages"])
def test_cleanup_rejects_payload_intermediate_symlink(
    draft_factory: DraftFactory, tmp_path: Path, level: str
) -> None:
    """payload 中间父目录被替换为符号链接：清理不得跟随链接访问根外节点（回归 H1）。"""
    import shutil

    root = build_trashable_draft(draft_factory)
    pending = _retained_trash(root)
    package = root / ".draft/trash/outgoing" / pending.operation_id
    external = tmp_path / "external"
    external.mkdir()
    # 把整棵 payload 树移到外部，再把中间父目录替换为指向外部的符号链接。
    shutil.copytree(package / "payload", external / "payload", dirs_exist_ok=True)
    shutil.rmtree(package / "payload")
    (package / level).parent.mkdir(parents=True, exist_ok=True)
    (package / level).symlink_to(external / "payload")

    with pytest.raises(DraftError, match=CheckCode.TRASH_PACKAGE_CORRUPT.value):
        _clean_with_authorization(root, pending.operation_id)

    # 根外节点完全不变：符号链接未被跟随，外部文件仍存在、权威包现场保留。
    external_leaf = external / "payload" / "pages" / "obsolete.md"
    assert external_leaf.exists()
    assert (package / "package.json").is_file()


def test_cleanup_rejects_third_package_write(
    draft_factory: DraftFactory,
) -> None:
    """T 日志出现第三个包内 WRITE：清理必须判损坏，不得删除任何授权叶节点（回归 H1）。"""
    from draft.operations import operation_journal_path

    root = build_trashable_draft(draft_factory)
    pending = _retained_trash(root)
    package = root / ".draft/trash/outgoing" / pending.operation_id
    journal_path = operation_journal_path(root, pending.operation_id)
    read_journal(journal_path)
    # 追加第三个包内 WRITE（恶意日志）。
    # 形状合法（H5）但构成第三个包内 WRITE：read_journal 接受，包授权解析器必须拒绝。
    import json as json_module

    raw = json_module.loads(journal_path.read_text(encoding="utf-8"))
    forged = {
        "path": (package.relative_to(root) / "payload/extra.md").as_posix(),
        "kind": "write",
        "before_sha256": None,
        "after_sha256": "a" * 64,
        "original": None,
        "staged": f"staged/{len(raw['mutations']):04d}.bin",
        "applied": False,
    }
    raw["mutations"].append(forged)
    journal_path.write_text(json_module.dumps(raw, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(DraftError, match=CheckCode.TRASH_PACKAGE_CORRUPT.value):
        _clean_with_authorization(root, pending.operation_id)

    # 合法叶节点与未知内容都保留。
    before = _leaf_snapshot(package)
    assert before["package.json"] == (package / "package.json").read_bytes()


def test_cleanup_leaf_removal_is_anchored_to_dir_fd(
    draft_factory: DraftFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """叶节点验证与删除通过同一锚定目录句柄（dir_fd），绝不按绝对路径二次解析（回归 H1）。"""
    import os as os_module

    root = build_trashable_draft(draft_factory)
    pending = _retained_trash(root)

    unlink_calls: list[tuple[str, int | None]] = []
    original_unlink = os_module.unlink

    def spy_unlink(path: str, *, dir_fd: int | None = None) -> None:
        unlink_calls.append((path, dir_fd))
        if dir_fd is not None:
            original_unlink(path, dir_fd=dir_fd)
        else:
            original_unlink(path)

    monkeypatch.setattr(os_module, "unlink", spy_unlink)
    _clean_with_authorization(root, pending.operation_id)

    # 授权叶必须经 dir_fd 锚定删除（基础名 + 句柄），绝不以绝对路径 unlink。
    assert any(path == "package.json" and dir_fd is not None for path, dir_fd in unlink_calls)
    package = root / ".draft/trash/outgoing" / pending.operation_id
    assert not any(path == str(package / "package.json") for path, _ in unlink_calls)


def test_cleanup_mid_delete_extra_preserves_evidence_and_converges(
    draft_factory: DraftFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """删除期间出现晚到 extra：失败保留证据与未知内容，移除后从证据收敛（回归 H2）。"""
    from draft.operations import operation_journal_path

    root = build_trashable_draft(draft_factory)
    pending = _retained_trash(root)
    package = root / ".draft/trash/outgoing" / pending.operation_id
    journal = read_journal(operation_journal_path(root, pending.operation_id))

    import draft.trash_package as trash_package_module

    original_precheck = trash_package_module._precheck_package_tree  # pyright: ignore[reportPrivateUsage]

    def precheck_then_inject(
        root_path: Path, package_path: Path, authorized: list[tuple[Path, str]], code_path: Path
    ) -> None:
        original_precheck(root_path, package_path, authorized, code_path)
        (package / "late.txt").write_text("late\n", encoding="utf-8")

    monkeypatch.setattr(trash_package_module, "_precheck_package_tree", precheck_then_inject)
    with pytest.raises(DraftError, match=CheckCode.TRASH_PACKAGE_CORRUPT.value):
        _clean_with_authorization(root, pending.operation_id)

    # 未知内容保留，且耐久证据在锁所属操作目录中（可重建合法包，不只剩未知内容）。
    assert (package / "late.txt").read_text(encoding="utf-8") == "late\n"
    evidence = journal.directory / "cleanup-evidence.json"
    assert evidence.is_file()

    # 停止注入后移除未知条目，再次清理：从证据重建缺失叶并完成收敛。
    monkeypatch.undo()
    (package / "late.txt").unlink()
    _clean_with_authorization(root, pending.operation_id)
    assert not package.exists()
    assert not evidence.exists()


def test_cleanup_dangling_package_dir_symlink_is_corrupt(
    draft_factory: DraftFactory, tmp_path: Path
) -> None:
    """权威包目录是悬空符号链接：按 link 处理为损坏，不折叠成幂等缺失（回归 M2）。"""
    import shutil

    root = build_trashable_draft(draft_factory)
    pending = _retained_trash(root)
    package = root / ".draft/trash/outgoing" / pending.operation_id
    shutil.rmtree(package)
    package.symlink_to(tmp_path / "nowhere", target_is_directory=True)

    with pytest.raises(DraftError, match=CheckCode.TRASH_PACKAGE_CORRUPT.value):
        _clean_with_authorization(root, pending.operation_id)

    assert package.is_symlink()


def test_cleanup_preserves_full_evidence_when_unknown_present(
    draft_factory: DraftFactory,
) -> None:
    """清理前存在未知成员：预检必须在第一次删除前失败，完整证据保留（回归 H2）。"""

    root = build_trashable_draft(draft_factory)
    pending = _retained_trash(root)
    package = root / ".draft/trash/outgoing" / pending.operation_id
    before = _leaf_snapshot(package)
    (package / "intruder.txt").write_text("入侵\n", encoding="utf-8")

    with pytest.raises(DraftError, match=CheckCode.TRASH_PACKAGE_CORRUPT.value):
        _clean_with_authorization(root, pending.operation_id)

    # 完整证据保留：package.json、payload 与全部摘要不变。
    assert _leaf_snapshot(package) == {**before, "intruder.txt": b"\xe5\x85\xa5\xe4\xbe\xb5\n"}


def test_cleanup_evidence_dir_fsync_failure_does_not_delete_leaves(
    draft_factory: DraftFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """证据写目录同步失败：报 durability_failed，不得继续删除权威叶（回归 H1）。"""
    import os as os_module

    root = build_trashable_draft(draft_factory)
    pending = _retained_trash(root)
    package = root / ".draft/trash/outgoing" / pending.operation_id
    before = _leaf_snapshot(package)

    real_fsync = os_module.fsync
    calls = {"n": 0}

    def fail_evidence_dir_fsync(fd: int) -> None:
        # durable_write(证据) 的 fsync 序列：file(1) → pending dir(2) → final dir(3)。
        calls["n"] += 1
        if calls["n"] == 3:
            raise OSError("injected evidence final dir fsync failure")
        real_fsync(fd)

    monkeypatch.setattr(os_module, "fsync", fail_evidence_dir_fsync)
    with pytest.raises(DraftError, match=CheckCode.OPERATION_DURABILITY_FAILED.value):
        _clean_with_authorization(root, pending.operation_id)
    monkeypatch.undo()

    # 权威叶一个未删：耐久失败后调用方不得继续删除。
    assert _leaf_snapshot(package) == before
    assert (package / "package.json").is_file()


# ---------------------------------------------------------------------------
# 第八轮 M1/H4：清理授权门 + 受约束清理证据模式
# ---------------------------------------------------------------------------


def _commit_phase(root: Path, operation_id: str, phase: str) -> None:
    """把 T 日志 phase 直接重写为给定阶段（测试用；不涉及锁）。"""
    import json as json_module

    journal_path = root / OPERATIONS_RELATIVE / operation_id / "journal.json"
    raw = json_module.loads(journal_path.read_text(encoding="utf-8"))
    raw["phase"] = phase
    journal_path.write_text(json_module.dumps(raw, ensure_ascii=False), encoding="utf-8")


def _valid_evidence_payload(root: Path, operation_id: str) -> dict[str, object]:
    """构造与已提交 T 授权一致的合法证据 payload（两叶 present）。"""
    import base64

    from draft.trash_package import parse_package_authorization

    journal = read_journal(operation_journal_path(root, operation_id))
    auth = parse_package_authorization(journal, root / OUTGOING_RELATIVE / operation_id)
    leaves: list[dict[str, object]] = []
    for relative, digest in auth.leaves():
        content = (root / relative).read_bytes()
        leaves.append(
            {
                "relative": relative.as_posix(),
                "sha256": digest,
                "state": "present",
                "content": base64.b64encode(content).decode("ascii"),
            }
        )
    return {
        "version": 2,
        "operation_id": operation_id,
        "cleanup_phase": "rolled_back",
        "leaves": leaves,
    }


def _write_evidence(root: Path, operation_id: str, payload: dict[str, object]) -> Path:
    journal = read_journal(operation_journal_path(root, operation_id))
    evidence = journal.directory / "cleanup-evidence.json"
    evidence.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return evidence


def test_cleanup_authorization_rejects_non_committed_phases(
    draft_factory: DraftFactory,
) -> None:
    """retained/handoff_pending 一律拒绝清理授权，即使已持锁（回归 M1）。"""
    from draft.operations import acquire_lock
    from draft.trash_package import cleanup_authorization

    root = build_trashable_draft(draft_factory)
    pending = _retained_trash(root)
    acquire_lock(root, pending.operation_id)
    for phase in ("trash_retained", "trash_handoff_pending"):
        _commit_phase(root, pending.operation_id, phase)
        with pytest.raises(DraftError, match=CheckCode.OPERATION_INCONSISTENT_STATE.value):
            cleanup_authorization(
                root, pending.operation_id, lock_operation_id=pending.operation_id
            )


def test_cleanup_authorization_requires_matching_lock(
    draft_factory: DraftFactory,
) -> None:
    """清理授权必须匹配当前锁所有者：不持锁/锁不匹配一律拒绝（回归 M1）。"""
    from draft.trash_package import cleanup_authorization

    root = build_trashable_draft(draft_factory)
    pending = _retained_trash(root)
    _commit_phase(root, pending.operation_id, "rolled_back")
    other = "a" * 32
    with pytest.raises(DraftError, match=CheckCode.OPERATION_INCONSISTENT_STATE.value):
        cleanup_authorization(root, pending.operation_id, lock_operation_id=other)


def test_cleanup_authorization_rereads_committed_journal(
    draft_factory: DraftFactory,
) -> None:
    """授权从锁内重读的磁盘 journal 派生，不接受调用方手中的旧对象（回归 M1）。"""
    from draft.operations import acquire_lock
    from draft.trash_package import cleanup_authorization

    root = build_trashable_draft(draft_factory)
    pending = _retained_trash(root)
    acquire_lock(root, pending.operation_id)
    # 磁盘上已推进到提交阶段；任何"旧对象"都无法绕过该重读。
    _commit_phase(root, pending.operation_id, "trash_purge_committed")
    authorization = cleanup_authorization(
        root, pending.operation_id, lock_operation_id=pending.operation_id
    )
    assert authorization.journal.phase.value == "trash_purge_committed"
    assert authorization.journal.operation_id == pending.operation_id


def test_clean_retained_never_deletes_package(
    draft_factory: DraftFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """retained journal + 持锁：清理授权拒绝，删除调用次数必须为零，锁/包不变（回归 M1）。"""
    import os as os_module

    from draft.operations import acquire_lock, read_lock
    from draft.trash_package import cleanup_authorization

    root = build_trashable_draft(draft_factory)
    pending = _retained_trash(root)
    package = root / ".draft/trash/outgoing" / pending.operation_id
    before = _leaf_snapshot(package)
    acquire_lock(root, pending.operation_id)

    unlink_calls: list[tuple[str, int | None]] = []
    original_unlink = os_module.unlink

    def spy_unlink(path: str, *, dir_fd: int | None = None) -> None:
        unlink_calls.append((path, dir_fd))
        if dir_fd is not None:
            original_unlink(path, dir_fd=dir_fd)
        else:
            original_unlink(path)

    monkeypatch.setattr(os_module, "unlink", spy_unlink)
    with pytest.raises(DraftError, match=CheckCode.OPERATION_INCONSISTENT_STATE.value):
        cleanup_authorization(root, pending.operation_id, lock_operation_id=pending.operation_id)
    monkeypatch.undo()

    assert not unlink_calls
    assert _leaf_snapshot(package) == before
    assert read_lock(root) == pending.operation_id


def test_clean_handoff_pending_never_deletes_package(
    draft_factory: DraftFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """handoff_pending journal + 持锁：清理授权拒绝，删除次数为零（回归 M1）。"""
    import os as os_module

    from draft.operations import acquire_lock
    from draft.trash_package import cleanup_authorization

    root = build_trashable_draft(draft_factory)
    pending = _retained_trash(root)
    package = root / ".draft/trash/outgoing" / pending.operation_id
    acquire_lock(root, pending.operation_id)
    _commit_phase(root, pending.operation_id, "trash_handoff_pending")

    unlink_calls: list[tuple[str, int | None]] = []
    original_unlink = os_module.unlink

    def spy_unlink(path: str, *, dir_fd: int | None = None) -> None:
        unlink_calls.append((path, dir_fd))
        if dir_fd is not None:
            original_unlink(path, dir_fd=dir_fd)
        else:
            original_unlink(path)

    monkeypatch.setattr(os_module, "unlink", spy_unlink)
    with pytest.raises(DraftError, match=CheckCode.OPERATION_INCONSISTENT_STATE.value):
        cleanup_authorization(root, pending.operation_id, lock_operation_id=pending.operation_id)
    monkeypatch.undo()

    assert not unlink_calls
    assert (package / "package.json").is_file()


def test_parse_cleanup_evidence_rejects_unknown_top_field(
    draft_factory: DraftFactory,
) -> None:
    """未知顶层字段：证据损坏，拒绝（回归 H4 精确字段集合）。"""
    from draft.trash_package import parse_cleanup_evidence

    root = build_trashable_draft(draft_factory)
    pending = _retained_trash(root)
    _commit_phase(root, pending.operation_id, "rolled_back")
    payload = _valid_evidence_payload(root, pending.operation_id)
    payload["extra_field"] = "x"
    journal = read_journal(operation_journal_path(root, pending.operation_id))

    with pytest.raises(DraftError, match=CheckCode.TRASH_PACKAGE_CORRUPT.value):
        parse_cleanup_evidence(json.dumps(payload).encode("utf-8"), journal, root / "outgoing")


def test_parse_cleanup_evidence_rejects_duplicate_relative(
    draft_factory: DraftFactory,
) -> None:
    """重复 relative 或缺叶：证据损坏，拒绝（回归 H4）。"""
    from draft.trash_package import parse_cleanup_evidence

    root = build_trashable_draft(draft_factory)
    pending = _retained_trash(root)
    _commit_phase(root, pending.operation_id, "rolled_back")
    payload = _valid_evidence_payload(root, pending.operation_id)
    payload["leaves"] = [payload["leaves"][0], payload["leaves"][0]]  # type: ignore[index]
    journal = read_journal(operation_journal_path(root, pending.operation_id))

    with pytest.raises(DraftError, match=CheckCode.TRASH_PACKAGE_CORRUPT.value):
        parse_cleanup_evidence(json.dumps(payload).encode("utf-8"), journal, root / "outgoing")


def test_parse_cleanup_evidence_rejects_missing_leaf(
    draft_factory: DraftFactory,
) -> None:
    """只记录一个叶：证据损坏（回归 H4 恰好两个授权叶）。"""
    from draft.trash_package import parse_cleanup_evidence

    root = build_trashable_draft(draft_factory)
    pending = _retained_trash(root)
    _commit_phase(root, pending.operation_id, "rolled_back")
    payload = _valid_evidence_payload(root, pending.operation_id)
    payload["leaves"] = payload["leaves"][:1]  # type: ignore[index]
    journal = read_journal(operation_journal_path(root, pending.operation_id))

    with pytest.raises(DraftError, match=CheckCode.TRASH_PACKAGE_CORRUPT.value):
        parse_cleanup_evidence(json.dumps(payload).encode("utf-8"), journal, root / "outgoing")


def test_parse_cleanup_evidence_rejects_self_digest_not_anchored(
    draft_factory: DraftFactory,
) -> None:
    """证据自报摘要与 T 授权不符：损坏，拒绝（回归 H4 交叉校验）。"""
    from draft.trash_package import parse_cleanup_evidence

    root = build_trashable_draft(draft_factory)
    pending = _retained_trash(root)
    _commit_phase(root, pending.operation_id, "rolled_back")
    payload = _valid_evidence_payload(root, pending.operation_id)
    leaves = cast("list[dict[str, object]]", payload["leaves"])
    leaves[0]["sha256"] = "0" * 64
    journal = read_journal(operation_journal_path(root, pending.operation_id))

    with pytest.raises(DraftError, match=CheckCode.TRASH_PACKAGE_CORRUPT.value):
        parse_cleanup_evidence(json.dumps(payload).encode("utf-8"), journal, root / "outgoing")


def test_parse_cleanup_evidence_rejects_invalid_base64(
    draft_factory: DraftFactory,
) -> None:
    """非法 base64（非严格字符）：损坏，拒绝（回归 H4 严格解码）。"""
    from draft.trash_package import parse_cleanup_evidence

    root = build_trashable_draft(draft_factory)
    pending = _retained_trash(root)
    _commit_phase(root, pending.operation_id, "rolled_back")
    payload = _valid_evidence_payload(root, pending.operation_id)
    leaves = cast("list[dict[str, object]]", payload["leaves"])
    leaves[0]["content"] = "!!! not-base64 !!!"
    journal = read_journal(operation_journal_path(root, pending.operation_id))

    with pytest.raises(DraftError, match=CheckCode.TRASH_PACKAGE_CORRUPT.value):
        parse_cleanup_evidence(json.dumps(payload).encode("utf-8"), journal, root / "outgoing")


def test_parse_cleanup_evidence_rejects_non_utf8(
    draft_factory: DraftFactory,
) -> None:
    """非 UTF-8 字节：损坏，拒绝（回归 H4/M3 持久 JSON 解码边界）。"""
    from draft.trash_package import parse_cleanup_evidence

    root = build_trashable_draft(draft_factory)
    pending = _retained_trash(root)
    _commit_phase(root, pending.operation_id, "rolled_back")
    journal = read_journal(operation_journal_path(root, pending.operation_id))

    with pytest.raises(DraftError, match=CheckCode.TRASH_PACKAGE_CORRUPT.value):
        parse_cleanup_evidence(b"\xff\xfe\x00 garbage", journal, root / "outgoing")


def test_parse_cleanup_evidence_rejects_wrong_phase_and_operation(
    draft_factory: DraftFactory,
) -> None:
    """cleanup_phase/operation_id 与日志不符：损坏（回归 H4）。"""
    from draft.trash_package import parse_cleanup_evidence

    root = build_trashable_draft(draft_factory)
    pending = _retained_trash(root)
    _commit_phase(root, pending.operation_id, "rolled_back")
    journal = read_journal(operation_journal_path(root, pending.operation_id))

    payload = _valid_evidence_payload(root, pending.operation_id)
    payload["cleanup_phase"] = "trash_purge_committed"
    with pytest.raises(DraftError, match=CheckCode.TRASH_PACKAGE_CORRUPT.value):
        parse_cleanup_evidence(json.dumps(payload).encode("utf-8"), journal, root / "outgoing")

    payload = _valid_evidence_payload(root, pending.operation_id)
    payload["operation_id"] = "1" * 32
    with pytest.raises(DraftError, match=CheckCode.TRASH_PACKAGE_CORRUPT.value):
        parse_cleanup_evidence(json.dumps(payload).encode("utf-8"), journal, root / "outgoing")


def test_restore_fails_before_first_leaf_write_on_later_entry_corrupt(
    draft_factory: DraftFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """后一个证据条目损坏：首次受管写入前整体失败，不得先恢复前一个叶（回归 H4）。"""
    from draft.operations import acquire_lock
    from draft.trash_package import clean_authoritative_package, cleanup_authorization

    root = build_trashable_draft(draft_factory)
    pending = _retained_trash(root)
    package = root / ".draft/trash/outgoing" / pending.operation_id
    acquire_lock(root, pending.operation_id)
    _commit_phase(root, pending.operation_id, "rolled_back")
    payload = _valid_evidence_payload(root, pending.operation_id)
    leaves = cast("list[dict[str, object]]", payload["leaves"])
    # 把第一个叶的内容也替换成错误摘要：无论排序，损坏都必须先于任何写入被整体拒绝。
    leaves[1]["sha256"] = "0" * 64
    evidence = _write_evidence(root, pending.operation_id, payload)
    # 删除两个叶：让恢复需要重建。
    (root / OUTGOING_RELATIVE / pending.operation_id / "package.json").unlink()
    (root / OUTGOING_RELATIVE / pending.operation_id / "payload" / "pages" / "obsolete.md").unlink()

    write_spy = {"writes": 0}
    from draft.operations import durable_write

    original_durable = durable_write

    def counting_durable(path: object, value: object) -> None:
        write_spy["writes"] += 1
        original_durable(path, value)  # type: ignore[arg-type]

    monkeypatch.setattr("draft.operations.durable_write", counting_durable)
    with pytest.raises(DraftError, match=CheckCode.TRASH_PACKAGE_CORRUPT.value):
        clean_authoritative_package(
            root,
            cleanup_authorization(
                root, pending.operation_id, lock_operation_id=pending.operation_id
            ),
        )
    monkeypatch.undo()

    # 证据文件在操作目录中，两个叶都未恢复写入。
    assert evidence.is_file()
    assert not (package / "package.json").exists()


def test_cleanup_evidence_node_directory_or_link_is_corrupt(
    draft_factory: DraftFactory,
) -> None:
    """证据节点是目录/符号链接：不是"没有证据"，按损坏保留现场（回归 H4）。"""

    root = build_trashable_draft(draft_factory)
    pending = _retained_trash(root)
    package = root / ".draft/trash/outgoing" / pending.operation_id
    journal = read_journal(operation_journal_path(root, pending.operation_id))
    evidence = journal.directory / "cleanup-evidence.json"
    evidence.mkdir()  # 证据位置被目录占用。
    before = _leaf_snapshot(package)

    with pytest.raises(DraftError, match=CheckCode.TRASH_PACKAGE_CORRUPT.value):
        _clean_with_authorization(root, pending.operation_id)

    assert _leaf_snapshot(package) == before
    assert evidence.is_dir()


def test_cleanup_evidence_already_missing_entry_converges(
    draft_factory: DraftFactory,
) -> None:
    """证据含 already_missing 叶：跳过该叶恢复，其余叶照常，清理收敛（回归 H4）。"""
    import base64

    from draft.operations import acquire_lock
    from draft.trash_package import (
        clean_authoritative_package,
        cleanup_authorization,
        parse_package_authorization,
    )

    root = build_trashable_draft(draft_factory)
    pending = _retained_trash(root)
    package = root / ".draft/trash/outgoing" / pending.operation_id
    acquire_lock(root, pending.operation_id)
    _commit_phase(root, pending.operation_id, "rolled_back")
    journal = read_journal(operation_journal_path(root, pending.operation_id))
    auth = parse_package_authorization(journal, package)
    present_relative, present_digest = auth.package_json_path, auth.package_json_digest
    missing_relative, missing_digest = auth.payload_path, auth.payload_digest
    content = (root / present_relative).read_bytes()
    payload: dict[str, object] = {
        "version": 2,
        "operation_id": pending.operation_id,
        "cleanup_phase": "rolled_back",
        "leaves": [
            {
                "relative": present_relative.as_posix(),
                "sha256": present_digest,
                "state": "present",
                "content": base64.b64encode(content).decode("ascii"),
            },
            {
                "relative": missing_relative.as_posix(),
                "sha256": missing_digest,
                "state": "already_missing",
            },
        ],
    }
    _write_evidence(root, pending.operation_id, payload)
    # payload 树已整体缺失：已提交阶段下为幂等状态，证据记录其为 already_missing。
    import shutil

    shutil.rmtree(package / "payload")

    clean_authoritative_package(
        root,
        cleanup_authorization(root, pending.operation_id, lock_operation_id=pending.operation_id),
    )

    assert not package.exists()
    evidence = journal.directory / "cleanup-evidence.json"
    assert not evidence.exists()
