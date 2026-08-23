from __future__ import annotations

from pathlib import Path

from draft.link_index import (
    Freshness,
    build_index,
    check_index_freshness,
    invalidate_index,
    read_index,
    write_index_atomic,
)


def test_index_missing_and_corrupt_are_explicit(tmp_path: Path) -> None:
    status, index, details = read_index(tmp_path)
    assert (status, index, details) == (Freshness.UNKNOWN, None, ("missing",))
    path = tmp_path / ".draft" / "link-index.json"
    path.parent.mkdir()
    path.write_bytes(b"not-json")
    status, _, details = read_index(tmp_path)
    assert status is Freshness.CORRUPT
    assert details[0] == "corrupt"


def test_atomic_write_and_invalidation(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("正文。\n", encoding="utf-8")
    write_index_atomic(tmp_path, build_index(tmp_path))
    assert read_index(tmp_path)[0] is Freshness.STALE
    invalidate_index(tmp_path, reason="watcher_gap")
    assert (tmp_path / ".draft" / "link-index.invalid").read_text() == "watcher_gap\n"
    assert read_index(tmp_path)[0] is Freshness.UNKNOWN


def test_freshness_detects_directory_and_fingerprint_changes(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("正文。\n", encoding="utf-8")
    index = build_index(tmp_path)
    assert check_index_freshness(tmp_path, index) is Freshness.FRESH
    (tmp_path / "new.md").write_text("新增。\n", encoding="utf-8")
    assert check_index_freshness(tmp_path, index) is Freshness.STALE
