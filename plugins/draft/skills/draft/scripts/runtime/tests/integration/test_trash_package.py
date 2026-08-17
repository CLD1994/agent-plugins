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
from draft.operations import read_journal
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

    from draft.operations import operation_journal_path
    from draft.trash_package import clean_authoritative_package

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

    journal = read_journal(operation_journal_path(root, pending.operation_id))
    with pytest.raises(DraftError, match=CheckCode.TRASH_PACKAGE_CORRUPT.value):
        clean_authoritative_package(root, journal)

    # 根外节点完全不变：符号链接未被跟随，外部文件仍存在、权威包现场保留。
    external_leaf = external / "payload" / "pages" / "obsolete.md"
    assert external_leaf.exists()
    assert (package / "package.json").is_file()


def test_cleanup_rejects_third_package_write(
    draft_factory: DraftFactory,
) -> None:
    """T 日志出现第三个包内 WRITE：清理必须判损坏，不得删除任何授权叶节点（回归 H1）。"""
    from draft.operations import operation_journal_path
    from draft.trash_package import clean_authoritative_package

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
        clean_authoritative_package(root, read_journal(journal_path))

    # 合法叶节点与未知内容都保留。
    before = _leaf_snapshot(package)
    assert before["package.json"] == (package / "package.json").read_bytes()


def test_cleanup_leaf_removal_is_anchored_to_dir_fd(
    draft_factory: DraftFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """叶节点验证与删除通过同一锚定目录句柄（dir_fd），绝不按绝对路径二次解析（回归 H1）。"""
    import os as os_module

    from draft.operations import operation_journal_path
    from draft.trash_package import clean_authoritative_package

    root = build_trashable_draft(draft_factory)
    pending = _retained_trash(root)
    journal = read_journal(operation_journal_path(root, pending.operation_id))

    unlink_calls: list[tuple[str, int | None]] = []
    original_unlink = os_module.unlink

    def spy_unlink(path: str, *, dir_fd: int | None = None) -> None:
        unlink_calls.append((path, dir_fd))
        if dir_fd is not None:
            original_unlink(path, dir_fd=dir_fd)
        else:
            original_unlink(path)

    monkeypatch.setattr(os_module, "unlink", spy_unlink)
    clean_authoritative_package(root, journal)

    # 授权叶必须经 dir_fd 锚定删除（基础名 + 句柄），绝不以绝对路径 unlink。
    assert any(path == "package.json" and dir_fd is not None for path, dir_fd in unlink_calls)
    package = root / ".draft/trash/outgoing" / pending.operation_id
    assert not any(path == str(package / "package.json") for path, _ in unlink_calls)


def test_cleanup_mid_delete_extra_preserves_evidence_and_converges(
    draft_factory: DraftFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """删除期间出现晚到 extra：失败保留证据与未知内容，移除后从证据收敛（回归 H2）。"""
    from draft.operations import operation_journal_path
    from draft.trash_package import clean_authoritative_package

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
        clean_authoritative_package(root, journal)

    # 未知内容保留，且耐久证据在锁所属操作目录中（可重建合法包，不只剩未知内容）。
    assert (package / "late.txt").read_text(encoding="utf-8") == "late\n"
    evidence = journal.directory / "cleanup-evidence.json"
    assert evidence.is_file()

    # 停止注入后移除未知条目，再次清理：从证据重建缺失叶并完成收敛。
    monkeypatch.undo()
    (package / "late.txt").unlink()
    clean_authoritative_package(root, journal)
    assert not package.exists()
    assert not evidence.exists()


def test_cleanup_dangling_package_dir_symlink_is_corrupt(
    draft_factory: DraftFactory, tmp_path: Path
) -> None:
    """权威包目录是悬空符号链接：按 link 处理为损坏，不折叠成幂等缺失（回归 M2）。"""
    import shutil

    from draft.operations import operation_journal_path
    from draft.trash_package import clean_authoritative_package

    root = build_trashable_draft(draft_factory)
    pending = _retained_trash(root)
    package = root / ".draft/trash/outgoing" / pending.operation_id
    shutil.rmtree(package)
    package.symlink_to(tmp_path / "nowhere", target_is_directory=True)

    journal = read_journal(operation_journal_path(root, pending.operation_id))
    with pytest.raises(DraftError, match=CheckCode.TRASH_PACKAGE_CORRUPT.value):
        clean_authoritative_package(root, journal)

    assert package.is_symlink()


def test_cleanup_preserves_full_evidence_when_unknown_present(
    draft_factory: DraftFactory,
) -> None:
    """清理前存在未知成员：预检必须在第一次删除前失败，完整证据保留（回归 H2）。"""
    from draft.operations import operation_journal_path
    from draft.trash_package import clean_authoritative_package

    root = build_trashable_draft(draft_factory)
    pending = _retained_trash(root)
    package = root / ".draft/trash/outgoing" / pending.operation_id
    before = _leaf_snapshot(package)
    (package / "intruder.txt").write_text("入侵\n", encoding="utf-8")

    journal = read_journal(operation_journal_path(root, pending.operation_id))
    with pytest.raises(DraftError, match=CheckCode.TRASH_PACKAGE_CORRUPT.value):
        clean_authoritative_package(root, journal)

    # 完整证据保留：package.json、payload 与全部摘要不变。
    assert _leaf_snapshot(package) == {**before, "intruder.txt": b"\xe5\x85\xa5\xe4\xbe\xb5\n"}


def test_cleanup_evidence_dir_fsync_failure_does_not_delete_leaves(
    draft_factory: DraftFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """证据写目录同步失败：报 durability_failed，不得继续删除权威叶（回归 H1）。"""
    import os as os_module

    from draft.operations import operation_journal_path
    from draft.trash_package import clean_authoritative_package

    root = build_trashable_draft(draft_factory)
    pending = _retained_trash(root)
    journal = read_journal(operation_journal_path(root, pending.operation_id))
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
        clean_authoritative_package(root, journal)
    monkeypatch.undo()

    # 权威叶一个未删：耐久失败后调用方不得继续删除。
    assert _leaf_snapshot(package) == before
    assert (package / "package.json").is_file()
