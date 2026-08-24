from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn, cast

import pytest

from draft.conclusions import GenerationResult, generate_conclusions
from draft.diagnostic_codes import OPERATION_INDEX_UPDATE_FAILED, CheckCode
from draft.errors import DraftError
from draft.link_index import (
    Freshness,
    LinkIndex,
    LinkSpan,
    build_index,
    check_index_freshness,
    invalidate_index,
    read_index,
    write_index_atomic,
)
from draft.link_index_maintenance import (
    LINK_INDEX_STAGED_FILE_NAME,
    load_link_index_baseline,
    read_link_index_authority,
    scan_link_index,
)
from draft.models import LinkIndexResult, OperationKind, OperationPhase, OperationResult, RootState
from draft.operations import JOURNAL_VERSION, Journal, read_lock, sha256_bytes
from draft.page_create import create_page
from draft.page_metadata import add_relation, remove_relation, set_status
from draft.resources import ResourceIdentity, ResourceKind
from draft.structure import collapse_hub, move_entries, promote_page
from draft.trash import restore_retained_trash, trash_page
from draft.vocabulary import EditableRelationCode, StatusCode
from tests.integration.support import DraftFactory

_ENTRY_INDEX = """# Topic

> 状态：`已关闭：纯导航页，不表达独立结论`

## 子主题

- [甲](pages/a.md)
- [乙](pages/b.md)
"""

_ENTRY_A = """# 甲

> 状态：`待讨论`
>
> 父主题：[根索引](../index.md)

甲。
"""

_ENTRY_CONFIRMED_A = _ENTRY_A.replace("待讨论", "已确认")

_ENTRY_B = """# 乙

> 状态：`待讨论`
>
> 父主题：[根索引](../index.md)

乙。
"""


def _entry_root(draft_factory: DraftFactory) -> Path:
    return draft_factory(
        {
            "index.md": _ENTRY_INDEX,
            "pages/a.md": _ENTRY_A,
            "pages/b.md": _ENTRY_B,
        }
    )


def _latest_journal(root: Path, kind: str) -> dict[str, object]:
    candidates: list[dict[str, object]] = []
    operations = root / ".draft" / "operations"
    for path in operations.glob("*/journal.json"):
        payload = cast("dict[str, object]", json.loads(path.read_text(encoding="utf-8")))
        if payload.get("kind") == kind and payload.get("link_index_revision") is not None:
            candidates.append(payload)
    assert candidates, f"no indexed journal for {kind}"
    return max(candidates, key=lambda item: int(cast("str", item["link_index_revision"])))


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


_ENTRY_NAMES = (
    "create",
    "move",
    "promote",
    "collapse",
    "status set",
    "relation add",
    "relation remove",
    "generate-conclusions",
    "trash",
    "restore",
)


@dataclass(frozen=True)
class _EntryCase:
    """一个用户入口的独立夹具、业务观察和索引事实预期。"""

    root: Path
    invoke: Callable[[], object]
    assert_business: Callable[[], None]
    kind: str
    terminal_phase: OperationPhase
    revision: str
    files: frozenset[Path]
    backlink_sources: Mapping[ResourceIdentity, frozenset[Path]]
    forbidden_files: frozenset[Path] = frozenset()
    forbidden_edges: tuple[tuple[ResourceIdentity, Path], ...] = ()


def _assert_all(*conditions: bool) -> None:
    assert all(conditions)


def _page_identity(path: str) -> ResourceIdentity:
    return ResourceIdentity(ResourceKind.PAGE, path)


def _build_entry_case(draft_factory: DraftFactory, entry: str) -> _EntryCase:
    """按独立用户入口构造 case；不从生产 operation kind 集合派生预期。"""
    if entry == "create":
        root = _entry_root(draft_factory)
        return _EntryCase(
            root=root,
            invoke=lambda: create_page(
                root, Path("pages/c.md"), title="丙", status=StatusCode.PENDING
            ),
            assert_business=lambda: _assert_all((root / "pages/c.md").is_file()),
            kind=OperationKind.CREATE.value,
            terminal_phase=OperationPhase.COMPLETED,
            revision="1",
            files=frozenset(
                {Path("index.md"), Path("pages/a.md"), Path("pages/b.md"), Path("pages/c.md")}
            ),
            backlink_sources={
                _page_identity("pages/a.md"): frozenset({Path("index.md")}),
                _page_identity("pages/b.md"): frozenset({Path("index.md")}),
                _page_identity("pages/c.md"): frozenset({Path("index.md")}),
                _page_identity("index.md"): frozenset(
                    {Path("pages/a.md"), Path("pages/b.md"), Path("pages/c.md")}
                ),
            },
        )
    if entry == "move":
        root = _entry_root(draft_factory)
        return _EntryCase(
            root=root,
            invoke=lambda: move_entries(root, ["pages/a.md"], "pages/c.md"),
            assert_business=lambda: _assert_all(
                (root / "pages/c.md").is_file(), not (root / "pages/a.md").exists()
            ),
            kind=OperationKind.MOVE.value,
            terminal_phase=OperationPhase.COMPLETED,
            revision="1",
            files=frozenset({Path("index.md"), Path("pages/b.md"), Path("pages/c.md")}),
            backlink_sources={
                _page_identity("pages/b.md"): frozenset({Path("index.md")}),
                _page_identity("pages/c.md"): frozenset({Path("index.md")}),
                _page_identity("index.md"): frozenset({Path("pages/b.md"), Path("pages/c.md")}),
            },
            forbidden_files=frozenset({Path("pages/a.md")}),
            forbidden_edges=((_page_identity("pages/a.md"), Path("index.md")),),
        )
    if entry == "promote":
        root = draft_factory(
            {
                "index.md": _ENTRY_INDEX.replace(
                    "- [乙](pages/b.md)", "- [乙](pages/b.md)\n- [丙](pages/c.md)"
                ).replace("- [甲](pages/a.md)", "- [主题](pages/topic.md)"),
                "pages/topic.md": _ENTRY_A,
                "pages/b.md": _ENTRY_B,
                "pages/c.md": _ENTRY_B.replace("乙", "丙"),
            }
        )
        return _EntryCase(
            root=root,
            invoke=lambda: promote_page(
                root, Path("pages/topic.md"), [Path("pages/b.md"), Path("pages/c.md")]
            ),
            assert_business=lambda: _assert_all(
                (root / "pages/topic/index.md").is_file(),
                (root / "pages/topic/b.md").is_file(),
                (root / "pages/topic/c.md").is_file(),
                not (root / "pages/topic.md").exists(),
            ),
            kind=OperationKind.PROMOTE.value,
            terminal_phase=OperationPhase.COMPLETED,
            revision="1",
            files=frozenset(
                {
                    Path("index.md"),
                    Path("pages/topic/index.md"),
                    Path("pages/topic/b.md"),
                    Path("pages/topic/c.md"),
                }
            ),
            backlink_sources={
                _page_identity("index.md"): frozenset({Path("pages/topic/index.md")}),
                _page_identity("pages/topic/index.md"): frozenset(
                    {Path("index.md"), Path("pages/topic/b.md"), Path("pages/topic/c.md")}
                ),
                _page_identity("pages/topic/b.md"): frozenset({Path("pages/topic/index.md")}),
                _page_identity("pages/topic/c.md"): frozenset({Path("pages/topic/index.md")}),
            },
            forbidden_files=frozenset(
                {Path("pages/topic.md"), Path("pages/b.md"), Path("pages/c.md")}
            ),
        )
    if entry == "collapse":
        root = draft_factory(
            {
                "index.md": _ENTRY_INDEX.replace(
                    "- [甲](pages/a.md)", "- [枢纽](pages/hub/index.md)"
                ),
                "pages/hub/index.md": (
                    "# 枢纽\n\n> 状态：`已关闭：纯导航页，不表达独立结论`\n>\n"
                    "> 父主题：[根索引](../../index.md)\n"
                ),
                "pages/b.md": _ENTRY_B,
            }
        )
        return _EntryCase(
            root=root,
            invoke=lambda: collapse_hub(root, Path("pages/hub/index.md")),
            assert_business=lambda: _assert_all(
                (root / "pages/hub.md").is_file(), not (root / "pages/hub/index.md").exists()
            ),
            kind=OperationKind.COLLAPSE.value,
            terminal_phase=OperationPhase.COMPLETED,
            revision="1",
            files=frozenset({Path("index.md"), Path("pages/hub.md"), Path("pages/b.md")}),
            backlink_sources={
                _page_identity("pages/hub.md"): frozenset({Path("index.md")}),
                _page_identity("pages/b.md"): frozenset({Path("index.md")}),
                _page_identity("index.md"): frozenset({Path("pages/hub.md"), Path("pages/b.md")}),
            },
            forbidden_files=frozenset({Path("pages/hub/index.md")}),
        )
    if entry == "status set":
        root = _entry_root(draft_factory)
        return _EntryCase(
            root=root,
            invoke=lambda: set_status(root, Path("pages/a.md"), StatusCode.CONFIRMED),
            assert_business=lambda: _assert_all(
                "已确认" in (root / "pages/a.md").read_text(encoding="utf-8")
            ),
            kind=OperationKind.STATUS_SET.value,
            terminal_phase=OperationPhase.COMPLETED,
            revision="1",
            files=frozenset({Path("index.md"), Path("pages/a.md"), Path("pages/b.md")}),
            backlink_sources={
                _page_identity("pages/a.md"): frozenset({Path("index.md")}),
                _page_identity("pages/b.md"): frozenset({Path("index.md")}),
                _page_identity("index.md"): frozenset({Path("pages/a.md"), Path("pages/b.md")}),
            },
        )
    if entry == "relation add":
        root = _entry_root(draft_factory)
        return _EntryCase(
            root=root,
            invoke=lambda: add_relation(
                root, Path("pages/a.md"), EditableRelationCode.DEPENDS_ON, Path("pages/b.md")
            ),
            assert_business=lambda: _assert_all(
                "依赖" in (root / "pages/a.md").read_text(encoding="utf-8")
            ),
            kind=OperationKind.RELATION_ADD.value,
            terminal_phase=OperationPhase.COMPLETED,
            revision="1",
            files=frozenset({Path("index.md"), Path("pages/a.md"), Path("pages/b.md")}),
            backlink_sources={
                _page_identity("pages/a.md"): frozenset({Path("index.md")}),
                _page_identity("pages/b.md"): frozenset({Path("index.md"), Path("pages/a.md")}),
                _page_identity("index.md"): frozenset({Path("pages/a.md"), Path("pages/b.md")}),
            },
        )
    if entry == "relation remove":
        root = _entry_root(draft_factory)
        add_relation(root, Path("pages/a.md"), EditableRelationCode.DEPENDS_ON, Path("pages/b.md"))
        return _EntryCase(
            root=root,
            invoke=lambda: remove_relation(
                root, Path("pages/a.md"), EditableRelationCode.DEPENDS_ON, Path("pages/b.md")
            ),
            assert_business=lambda: _assert_all(
                "依赖" not in (root / "pages/a.md").read_text(encoding="utf-8")
            ),
            kind=OperationKind.RELATION_REMOVE.value,
            terminal_phase=OperationPhase.COMPLETED,
            revision="2",
            files=frozenset({Path("index.md"), Path("pages/a.md"), Path("pages/b.md")}),
            backlink_sources={
                _page_identity("pages/a.md"): frozenset({Path("index.md")}),
                _page_identity("pages/b.md"): frozenset({Path("index.md")}),
                _page_identity("index.md"): frozenset({Path("pages/a.md"), Path("pages/b.md")}),
            },
            forbidden_edges=((_page_identity("pages/b.md"), Path("pages/a.md")),),
        )
    if entry == "generate-conclusions":
        root = draft_factory(
            {"index.md": _ENTRY_INDEX, "pages/a.md": _ENTRY_CONFIRMED_A, "pages/b.md": _ENTRY_B}
        )
        return _EntryCase(
            root=root,
            invoke=lambda: generate_conclusions(root),
            assert_business=lambda: _assert_all((root / "conclusions.md").is_file()),
            kind=OperationKind.GENERATE_CONCLUSIONS.value,
            terminal_phase=OperationPhase.COMPLETED,
            revision="1",
            files=frozenset(
                {Path("index.md"), Path("conclusions.md"), Path("pages/a.md"), Path("pages/b.md")}
            ),
            backlink_sources={
                _page_identity("pages/a.md"): frozenset({Path("index.md"), Path("conclusions.md")}),
                _page_identity("pages/b.md"): frozenset({Path("index.md")}),
                _page_identity("conclusions.md"): frozenset({Path("index.md")}),
                _page_identity("index.md"): frozenset(
                    {Path("pages/a.md"), Path("pages/b.md"), Path("conclusions.md")}
                ),
            },
        )
    if entry == "trash":
        root = _entry_root(draft_factory)
        return _EntryCase(
            root=root,
            invoke=lambda: trash_page(
                root, Path("pages/b.md"), now=datetime(2026, 8, 1, tzinfo=UTC)
            ),
            assert_business=lambda: _assert_all(
                not (root / "pages/b.md").exists(),
                any((root / ".draft/trash/outgoing").glob("*/package.json")),
            ),
            kind=OperationKind.TRASH.value,
            terminal_phase=OperationPhase.TRASH_RETAINED,
            revision="1",
            files=frozenset({Path("index.md"), Path("pages/a.md")}),
            backlink_sources={
                _page_identity("pages/a.md"): frozenset({Path("index.md")}),
                _page_identity("index.md"): frozenset({Path("pages/a.md")}),
            },
            forbidden_files=frozenset({Path("pages/b.md")}),
            forbidden_edges=((_page_identity("pages/b.md"), Path("index.md")),),
        )
    if entry == "restore":
        root = _entry_root(draft_factory)
        trashed = trash_page(root, Path("pages/b.md"), now=datetime(2026, 8, 1, tzinfo=UTC))
        return _EntryCase(
            root=root,
            invoke=lambda: restore_retained_trash(
                root, trashed.operation_id, now=datetime(2026, 8, 2, tzinfo=UTC)
            ),
            assert_business=lambda: _assert_all((root / "pages/b.md").is_file()),
            kind=OperationKind.RESTORE.value,
            terminal_phase=OperationPhase.COMPLETED,
            revision="2",
            files=frozenset({Path("index.md"), Path("pages/a.md"), Path("pages/b.md")}),
            backlink_sources={
                _page_identity("pages/a.md"): frozenset({Path("index.md")}),
                _page_identity("pages/b.md"): frozenset({Path("index.md")}),
                _page_identity("index.md"): frozenset({Path("pages/a.md"), Path("pages/b.md")}),
            },
        )
    raise AssertionError(f"unknown entry case: {entry}")


def _backlink_sources(index: LinkIndex) -> dict[ResourceIdentity, frozenset[Path]]:
    return {
        identity: frozenset(edge.source_path for edge in edges)
        for identity, edges in index.backlinks.items()
    }


def _assert_entry_index(case: _EntryCase, journal: dict[str, object]) -> None:
    assert journal["phase"] == case.terminal_phase.value
    assert journal["link_index_result"] == LinkIndexResult.PUBLISHED.value
    revision = cast("str", journal["link_index_revision"])
    assert revision == case.revision
    raw = (case.root / ".draft/link-index.json").read_bytes()
    assert sha256_bytes(raw) == journal["link_index_sha256"]
    authority = read_link_index_authority(case.root)
    assert authority.revision == case.revision
    assert authority.result is LinkIndexResult.PUBLISHED
    baseline = load_link_index_baseline(case.root)
    assert baseline.index is not None
    assert frozenset(baseline.index.files) == case.files
    assert _backlink_sources(baseline.index) == dict(case.backlink_sources)
    for path in case.forbidden_files:
        assert path not in baseline.index.files
    for identity, source in case.forbidden_edges:
        assert source not in {
            edge.source_path for edge in baseline.index.backlinks.get(identity, ())
        }


def _result_phase(result: object) -> OperationPhase:
    if isinstance(result, GenerationResult):
        assert result.operation is not None
        assert result.warnings == ()
        return result.operation.phase
    if isinstance(result, OperationResult):
        assert result.warnings == ()
        return result.phase
    phase = getattr(result, "phase", None)
    assert isinstance(phase, OperationPhase)
    assert getattr(result, "index_warnings", ()) == ()
    return phase


def _journal_paths(root: Path) -> tuple[Path, ...]:
    return tuple(sorted((root / ".draft/operations").glob("*/journal.json")))


@pytest.mark.parametrize("entry", _ENTRY_NAMES, ids=lambda value: value.replace(" ", "-"))
def test_every_mutation_entry_publishes_terminal_index_and_current_backlinks(
    draft_factory: DraftFactory,
    entry: str,
) -> None:
    case = _build_entry_case(draft_factory, entry)
    result = case.invoke()
    case.assert_business()
    assert _result_phase(result) is case.terminal_phase
    _assert_entry_index(case, _latest_journal(case.root, case.kind))


@dataclass(frozen=True)
class _NoOpCase:
    root: Path
    invoke: Callable[[], object]
    expected_code: str | None


def _build_noop_case(draft_factory: DraftFactory, entry: str) -> _NoOpCase:
    if entry == "create":
        root = _entry_root(draft_factory)
        return _NoOpCase(
            root,
            lambda: create_page(root, Path("pages/a.md"), title="甲", status=StatusCode.PENDING),
            "structure.target_exists",
        )
    if entry == "move":
        root = _entry_root(draft_factory)
        return _NoOpCase(
            root,
            lambda: move_entries(root, ["pages/a.md"], "pages/b.md"),
            "structure.target_exists",
        )
    if entry == "promote":
        root = _entry_root(draft_factory)
        return _NoOpCase(
            root,
            lambda: promote_page(root, Path("pages/a.md"), []),
            "structure.missing_child",
        )
    if entry == "collapse":
        root = _entry_root(draft_factory)
        return _NoOpCase(root, lambda: collapse_hub(root, Path("pages/a.md")), "structure.not_hub")
    if entry == "status set":
        root = _entry_root(draft_factory)
        return _NoOpCase(
            root, lambda: set_status(root, Path("pages/a.md"), StatusCode.PENDING), None
        )
    if entry == "relation add":
        root = _entry_root(draft_factory)
        add_relation(root, Path("pages/a.md"), EditableRelationCode.DEPENDS_ON, Path("pages/b.md"))
        return _NoOpCase(
            root,
            lambda: add_relation(
                root, Path("pages/a.md"), EditableRelationCode.DEPENDS_ON, Path("pages/b.md")
            ),
            None,
        )
    if entry == "relation remove":
        root = _entry_root(draft_factory)
        return _NoOpCase(
            root,
            lambda: remove_relation(
                root, Path("pages/a.md"), EditableRelationCode.DEPENDS_ON, Path("pages/b.md")
            ),
            None,
        )
    if entry == "generate-conclusions":
        root = _entry_root(draft_factory)
        return _NoOpCase(root, lambda: generate_conclusions(root), None)
    if entry == "trash":
        root = _entry_root(draft_factory)
        return _NoOpCase(root, lambda: trash_page(root, Path("index.md")), "trash.protected")
    if entry == "restore":
        root = _entry_root(draft_factory)
        return _NoOpCase(
            root,
            lambda: restore_retained_trash(root, "not-an-operation-id"),
            "operation.invalid_id",
        )
    raise AssertionError(f"unknown no-op case: {entry}")


def _business_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and ".draft" not in path.relative_to(root).parts
    }


@pytest.mark.parametrize("entry", _ENTRY_NAMES, ids=lambda value: value.replace(" ", "-"))
def test_every_mutation_entry_noop_keeps_business_journals_and_authority_unchanged(
    draft_factory: DraftFactory,
    entry: str,
) -> None:
    case = _build_noop_case(draft_factory, entry)
    before_business = _business_snapshot(case.root)
    before_journals = _journal_paths(case.root)
    before_authority = read_link_index_authority(case.root)
    if case.expected_code is None:
        result = case.invoke()
        if isinstance(result, GenerationResult):
            assert result.operation is None
            assert result.warnings == ()
        else:
            assert isinstance(result, OperationResult)
            assert result.changed_paths == ()
            assert result.warnings == ()
    else:
        with pytest.raises(DraftError) as caught:
            case.invoke()
        assert caught.value.code == case.expected_code
    assert _business_snapshot(case.root) == before_business
    assert _journal_paths(case.root) == before_journals
    assert read_link_index_authority(case.root) == before_authority


def _assert_degraded_result(result: object, case: _EntryCase) -> None:
    if isinstance(result, GenerationResult):
        assert result.operation is not None
        phase = result.operation.phase
    elif isinstance(result, OperationResult):
        phase = result.phase
    else:
        phase = getattr(result, "phase", None)
    assert phase is case.terminal_phase


@pytest.mark.parametrize("entry", _ENTRY_NAMES, ids=lambda value: value.replace(" ", "-"))
def test_every_mutation_entry_keeps_business_result_when_index_publish_degrades(
    draft_factory: DraftFactory,
    monkeypatch: pytest.MonkeyPatch,
    entry: str,
) -> None:
    """公共 publish 边界失败时十类入口都保留业务结果并耐久降级。"""
    import draft.link_index_maintenance as maintenance

    case = _build_entry_case(draft_factory, entry)

    def fail_publish(root: Path, journal: Journal, expected_sha256: str) -> NoReturn:
        del journal, expected_sha256
        raise DraftError(OPERATION_INDEX_UPDATE_FAILED, root)

    monkeypatch.setattr(maintenance, "publish_staged_link_index", fail_publish)
    result = case.invoke()
    case.assert_business()
    _assert_degraded_result(result, case)
    journal = _latest_journal(case.root, case.kind)
    assert journal["phase"] == case.terminal_phase.value
    assert journal["link_index_revision"] == case.revision
    assert journal["link_index_result"] == LinkIndexResult.REBUILD_REQUIRED.value
    assert "link_index_sha256" not in journal
    authority = read_link_index_authority(case.root)
    assert authority.revision == case.revision
    assert authority.result is LinkIndexResult.REBUILD_REQUIRED
    assert (case.root / ".draft/link-index-rebuild-required.flag").is_file()
    assert read_lock(case.root) is None
    operation_id = cast("str", journal["operation_id"])
    staging = case.root / ".draft/operations" / operation_id / LINK_INDEX_STAGED_FILE_NAME
    assert not staging.exists()
