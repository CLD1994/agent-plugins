from __future__ import annotations

import json
from pathlib import Path

import pytest

from draft.diagnostic_codes import CheckCode
from draft.errors import DraftError
from draft.link_index import (
    Freshness,
    LinkSpan,
    build_index,
    check_index_freshness,
    invalidate_index,
    read_index,
    write_index_atomic,
)
from draft.link_index_maintenance import (
    load_link_index_baseline,
    read_link_index_authority,
    scan_link_index,
)
from draft.models import LinkIndexResult, OperationKind, OperationPhase, RootState
from draft.operations import JOURNAL_VERSION, sha256_bytes
from draft.resources import ResourceIdentity, ResourceKind
from draft.structure import move_entries
from tests.integration.support import DraftFactory


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


# ---------------------------------------------------------------------------
# Task 3：严格 no-follow 扫描与 journal authority 读取
# ---------------------------------------------------------------------------


def _init_root(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    root.mkdir()
    (root / "pages").mkdir()
    (root / "pages" / "a.md").write_text("[b](b.md)\n", encoding="utf-8")
    internal = root / ".draft"
    internal.mkdir()
    # .draft/ 整体排除：即使内部出现 .md 也不进入 files。
    (internal / "internal.md").write_text("# internal\n", encoding="utf-8")
    return root


def _write_link_index_journal(
    root: Path,
    *,
    revision: str,
    result: LinkIndexResult,
    sha256: str | None,
    phase: OperationPhase,
    operation_id: str = "a" * 32,
) -> None:
    directory = root / ".draft" / "operations" / operation_id
    directory.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "journal_version": JOURNAL_VERSION,
        "operation_id": operation_id,
        "kind": OperationKind.CREATE.value,
        "phase": phase.value,
        "root_state": "registered",
        "observations": [],
        "mutations": [],
        "remove_empty_directories": [],
        "metadata": {},
        "link_index_revision": revision,
        "link_index_result": result.value,
    }
    if sha256 is not None:
        payload["link_index_sha256"] = sha256
    (directory / "journal.json").write_text(json.dumps(payload), encoding="utf-8")


def test_scan_hashes_every_markdown_without_following_symlinks(tmp_path: Path) -> None:
    root = _init_root(tmp_path)
    (root / "notes.md").write_text("[x](pages/a.md)\n", encoding="utf-8")
    (root / "asset.bin").write_bytes(b"\x00")
    (root / "linked.md").symlink_to(root / "notes.md")

    scan = scan_link_index(root, baseline=None)

    assert Path("notes.md") in scan.content.files
    assert Path("pages/a.md") in scan.content.files
    assert Path("linked.md") not in scan.content.files
    assert Path(".draft/internal.md") not in scan.content.files
    assert scan.stats.read_files == len(scan.content.files)
    assert scan.stats.read_bytes == sum(
        len((root / path).read_bytes()) for path in scan.content.files
    )


def test_scan_excludes_draft_directory_from_members_and_files(tmp_path: Path) -> None:
    root = _init_root(tmp_path)
    scan = scan_link_index(root, baseline=None)

    root_members = {member.name: member.kind for member in scan.content.directories[Path(".")]}
    assert ".draft" not in root_members
    assert Path(".draft") not in scan.content.directories


def test_scan_records_directory_members_and_node_types(tmp_path: Path) -> None:
    root = _init_root(tmp_path)
    (root / "asset.bin").write_bytes(b"\x00")
    (root / "sub").mkdir()
    (root / "linked").symlink_to(root / "pages" / "a.md")

    scan = scan_link_index(root, baseline=None)

    root_members = {member.name: member.kind for member in scan.content.directories[Path(".")]}
    assert root_members["asset.bin"] == "file"
    assert root_members["sub"] == "directory"
    assert root_members["linked"] == "symlink"
    pages_members = {member.name: member.kind for member in scan.content.directories[Path("pages")]}
    assert pages_members["a.md"] == "file"
    assert Path("asset.bin") not in scan.content.files


def test_scan_rejects_non_utf8_markdown(tmp_path: Path) -> None:
    root = _init_root(tmp_path)
    (root / "pages" / "bad.md").write_bytes(b"\xff\xfe\x00\x01")

    with pytest.raises(DraftError) as caught:
        scan_link_index(root, baseline=None)
    assert caught.value.code == "operation.index_update_failed"


def test_scan_read_failure_prevents_complete_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _init_root(tmp_path)
    import draft.link_index_maintenance as maintenance

    original = maintenance.read_file_no_follow

    def fail_on_a(path: Path, relative: Path) -> bytes:
        if path.name == "a.md":
            raise OSError("permission denied")
        return original(path, relative)

    monkeypatch.setattr(maintenance, "read_file_no_follow", fail_on_a)

    with pytest.raises(DraftError) as caught:
        scan_link_index(root, baseline=None)
    assert caught.value.code == "operation.index_update_failed"


def test_scan_reuses_known_document_links_without_skipping_read(tmp_path: Path) -> None:
    root = _init_root(tmp_path)
    from draft.link_index import IndexedFile, OutboundEdge

    path = Path("pages/a.md")
    raw = (root / path).read_bytes()
    digest = sha256_bytes(raw)
    identity = ResourceIdentity(ResourceKind.PAGE, "pages/b.md")
    edge = OutboundEdge(identity, LinkSpan(4, 8))
    known = IndexedFile(path=path, sha256=digest, outbound=(edge,))

    scan = scan_link_index(root, baseline=None, known_documents=(known,))

    assert scan.stats.read_files == 1  # 正文仍全部读取并求摘要
    assert scan.stats.parsed_files == 0  # 摘要匹配时复用已提取 links，不重复解析
    assert scan.content.files[path].outbound == (edge,)


def test_scan_known_document_with_stale_sha256_still_reparses(tmp_path: Path) -> None:
    root = _init_root(tmp_path)
    from draft.link_index import IndexedFile, OutboundEdge

    path = Path("pages/a.md")
    old_edge = OutboundEdge(
        ResourceIdentity(ResourceKind.PAGE, "pages/old.md"), LinkSpan(0, 1)
    )
    stale = IndexedFile(path=path, sha256="b" * 64, outbound=(old_edge,))

    scan = scan_link_index(root, baseline=None, known_documents=(stale,))

    assert scan.stats.parsed_files == 1  # sha256 不匹配，不得复用
    assert scan.content.files[path].outbound == (
        OutboundEdge(ResourceIdentity(ResourceKind.PAGE, "pages/b.md"), LinkSpan(4, 8)),
    )


def test_scan_rejects_instability_when_root_changes_during_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _init_root(tmp_path)
    import draft.link_index_maintenance as maintenance

    original = maintenance.read_file_no_follow

    def inject_late_file(path: Path, relative: Path) -> bytes:
        if path.name == "a.md":
            (root / "pages" / "late.md").write_text("# late\n", encoding="utf-8")
        return original(path, relative)

    monkeypatch.setattr(maintenance, "read_file_no_follow", inject_late_file)

    with pytest.raises(DraftError) as caught:
        scan_link_index(root, baseline=None)
    assert caught.value.code == "operation.external_change"


def test_scan_fails_on_unreadable_subdirectory(tmp_path: Path) -> None:
    root = _init_root(tmp_path)
    locked = root / "locked"
    locked.mkdir()
    (locked / "hidden.md").write_text("# hidden\n", encoding="utf-8")
    locked.chmod(0o000)
    try:
        with pytest.raises(DraftError) as caught:
            scan_link_index(root, baseline=None)
        assert caught.value.code == "operation.index_update_failed"
    finally:
        locked.chmod(0o755)


def test_scan_marks_unstable_when_file_disappears_during_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _init_root(tmp_path)
    import draft.link_index_maintenance as maintenance

    original = maintenance.read_file_no_follow

    def disappear(path: Path, relative: Path) -> bytes:
        if path.name == "a.md":
            raise FileNotFoundError("gone")
        return original(path, relative)

    monkeypatch.setattr(maintenance, "read_file_no_follow", disappear)

    with pytest.raises(DraftError) as caught:
        scan_link_index(root, baseline=None)
    assert caught.value.code == "operation.external_change"


def test_scan_rejects_duplicate_known_documents(tmp_path: Path) -> None:
    root = _init_root(tmp_path)
    from draft.link_index import IndexedFile, OutboundEdge

    path = Path("pages/a.md")
    raw = (root / path).read_bytes()
    digest = sha256_bytes(raw)
    edge = OutboundEdge(ResourceIdentity(ResourceKind.PAGE, "pages/b.md"), LinkSpan(4, 8))
    known = IndexedFile(path=path, sha256=digest, outbound=(edge,))

    with pytest.raises(ValueError):
        scan_link_index(root, baseline=None, known_documents=(known, known))


def test_authority_is_absent_when_no_operations(tmp_path: Path) -> None:
    root = _init_root(tmp_path)
    authority = read_link_index_authority(root)
    assert (authority.revision, authority.result, authority.sha256) == (None, None, None)


def test_authority_selects_numeric_max_revision_over_multiple_journals(
    tmp_path: Path,
) -> None:
    root = _init_root(tmp_path)
    _write_link_index_journal(
        root,
        revision="9",
        result=LinkIndexResult.PUBLISHED,
        sha256="a" * 64,
        phase=OperationPhase.LINK_INDEX_PUBLISHED,
        operation_id="a" * 32,
    )
    _write_link_index_journal(
        root,
        revision="10",
        result=LinkIndexResult.REBUILD_REQUIRED,
        sha256=None,
        phase=OperationPhase.LINK_INDEX_REBUILD_REQUIRED,
        operation_id="b" * 32,
    )
    authority = read_link_index_authority(root)
    assert authority.revision == "10"
    assert authority.result is LinkIndexResult.REBUILD_REQUIRED


def test_baseline_does_not_read_artifact_for_rebuild_required(tmp_path: Path) -> None:
    root = _init_root(tmp_path)
    _write_link_index_journal(
        root,
        revision="4",
        result=LinkIndexResult.REBUILD_REQUIRED,
        sha256=None,
        phase=OperationPhase.LINK_INDEX_REBUILD_REQUIRED,
    )
    baseline = load_link_index_baseline(root)
    assert baseline.authority.revision == "4"
    assert baseline.index is None


def test_baseline_validates_published_artifact(tmp_path: Path) -> None:
    root = _init_root(tmp_path)
    from draft.link_index import materialize_index

    content = scan_link_index(root, baseline=None).content
    index = materialize_index("1", content)
    raw = index.to_json()
    (root / ".draft" / "link-index.json").write_bytes(raw)
    _write_link_index_journal(
        root,
        revision="1",
        result=LinkIndexResult.PUBLISHED,
        sha256=sha256_bytes(raw),
        phase=OperationPhase.LINK_INDEX_PUBLISHED,
    )

    baseline = load_link_index_baseline(root)

    assert baseline.authority.revision == "1"
    assert baseline.authority.result is LinkIndexResult.PUBLISHED
    assert baseline.authority.sha256 == sha256_bytes(raw)
    assert baseline.index is not None
    assert baseline.index.revision == "1"
    assert baseline.index.schema_version == 2


def test_baseline_rejects_artifact_sha256_mismatch(tmp_path: Path) -> None:
    root = _init_root(tmp_path)
    from draft.link_index import materialize_index

    content = scan_link_index(root, baseline=None).content
    raw = materialize_index("1", content).to_json()
    (root / ".draft" / "link-index.json").write_bytes(raw)
    _write_link_index_journal(
        root,
        revision="1",
        result=LinkIndexResult.PUBLISHED,
        sha256="d" * 64,
        phase=OperationPhase.LINK_INDEX_PUBLISHED,
    )

    with pytest.raises(DraftError) as caught:
        load_link_index_baseline(root)
    assert caught.value.code == CheckCode.OPERATION_INCONSISTENT_STATE.value


def test_baseline_rejects_missing_artifact_for_published(tmp_path: Path) -> None:
    root = _init_root(tmp_path)
    _write_link_index_journal(
        root,
        revision="1",
        result=LinkIndexResult.PUBLISHED,
        sha256="a" * 64,
        phase=OperationPhase.LINK_INDEX_PUBLISHED,
    )
    with pytest.raises(DraftError) as caught:
        load_link_index_baseline(root)
    assert caught.value.code == CheckCode.OPERATION_INCONSISTENT_STATE.value


# ---------------------------------------------------------------------------
# Task 6：业务 operation 索引阶段与 maintenance authority 的端到端组合
# ---------------------------------------------------------------------------

_MOVE_INDEX = """# Topic

> 状态：`已关闭：纯导航页，不表达独立结论`

## 子主题

- [甲](pages/a.md)
"""

_MOVE_PAGE = """# 甲

> 状态：`已确认`
>
> 父主题：[根索引](../index.md)

甲。
"""


def test_business_operation_publishes_authoritative_v2_index(
    draft_factory: DraftFactory,
) -> None:
    """move 经通用索引阶段发布 revision "1" 制品；authority 按其验证为可信基线。"""
    root = draft_factory(
        {"index.md": _MOVE_INDEX, "pages/a.md": _MOVE_PAGE},
        root_state=RootState.CANDIDATE,
    )

    move_entries(root, ["pages/a.md"], "pages/b.md")

    authority = read_link_index_authority(root)
    assert authority.result is LinkIndexResult.PUBLISHED
    assert authority.revision == "1"
    baseline = load_link_index_baseline(root)
    assert baseline.index is not None
    artifact = root / ".draft" / "link-index.json"
    assert sha256_bytes(artifact.read_bytes()) == authority.sha256
    # 已发布索引覆盖移动后的页面事实：files 反映新路径。
    assert Path("pages/b.md") in baseline.index.files


def test_second_mutation_converges_incrementally_to_next_revision(
    draft_factory: DraftFactory,
) -> None:
    """第一次发布 revision "1" 后，第二次 mutation 增量收敛到 revision "2"。"""
    root = draft_factory(
        {"index.md": _MOVE_INDEX, "pages/a.md": _MOVE_PAGE},
        root_state=RootState.CANDIDATE,
    )
    move_entries(root, ["pages/a.md"], "pages/b.md")
    journals_after_first = {
        path: path.read_bytes()
        for path in (root / ".draft/operations").glob("*/journal.json")
    }

    move_entries(root, ["pages/b.md"], "pages/c.md")

    revisions = sorted(
        json.loads(path.read_text(encoding="utf-8"))["link_index_revision"]
        for path in journals_after_first
        if json.loads(path.read_text(encoding="utf-8")).get("link_index_revision")
    )
    assert revisions == ["1"]
    authority = read_link_index_authority(root)
    assert authority.revision == "2"
    assert authority.result is LinkIndexResult.PUBLISHED
    baseline = load_link_index_baseline(root)
    assert baseline.index is not None
    assert Path("pages/c.md") in baseline.index.files
    # 第一次的 journal 未被改写（每个 operation 只记录自己的结果）。
    for path, raw in journals_after_first.items():
        assert path.read_bytes() == raw
