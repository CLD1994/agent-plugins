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
