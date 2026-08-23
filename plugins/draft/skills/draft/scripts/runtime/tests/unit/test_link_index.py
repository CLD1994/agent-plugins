from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from draft.link_index import (
    Backlink,
    Completeness,
    DirectoryMember,
    FileFingerprint,
    IndexedFile,
    LegacyIndexedFile,
    LegacyLinkIndex,
    LinkIndex,
    LinkIndexContent,
    LinkSpan,
    OutboundEdge,
    build_index_content,
    materialize_index,
)
from draft.operations import sha256_bytes
from draft.resources import ResourceIdentity, ResourceKind

# ---------------------------------------------------------------------------
# v1 legacy schema（structure.py 当前依赖，行为契约不变）
# ---------------------------------------------------------------------------


def _legacy_index() -> LegacyLinkIndex:
    identity = ResourceIdentity(ResourceKind.PAGE, "pages/b.md")
    backlink = Backlink(Path("pages/a.md"), LinkSpan(6, 10))
    file = LegacyIndexedFile(
        Path("pages/a.md"),
        FileFingerprint(1, 12, 3),
        "a" * 64,
        "parsed",
        (OutboundEdge(identity, backlink.span),),
    )
    return LegacyLinkIndex(
        "/draft", 3, Completeness.COMPLETE, {file.path: file}, {identity: (backlink,)}
    )


def test_index_round_trip_and_reverse_edges() -> None:
    index = _legacy_index()
    raw = index.to_json()
    loaded = LegacyLinkIndex.from_json(raw)
    assert loaded == index
    identity = ResourceIdentity(ResourceKind.PAGE, "pages/b.md")
    assert loaded.backlinks[identity] == (Backlink(Path("pages/a.md"), LinkSpan(6, 10)),)


def test_index_rejects_wrong_schema_and_inconsistent_reverse_table() -> None:
    payload = _legacy_index().to_dict()
    payload["schema_version"] = 999
    with pytest.raises(ValueError):
        LegacyLinkIndex.from_dict(payload)
    payload = _legacy_index().to_dict()
    payload["backlinks"] = {"local:missing.md": []}
    with pytest.raises(ValueError):
        LegacyLinkIndex.from_dict(payload)


# ---------------------------------------------------------------------------
# v2 schema：类型、序列化与严格解析
# ---------------------------------------------------------------------------


def _b_identity() -> ResourceIdentity:
    return ResourceIdentity(ResourceKind.PAGE, "pages/b.md")


def _v2_index() -> LinkIndex:
    identity = _b_identity()
    edge = OutboundEdge(identity, LinkSpan(6, 10))
    file = IndexedFile(path=Path("pages/a.md"), sha256="a" * 64, outbound=(edge,))
    return LinkIndex(
        revision="3",
        files={file.path: file},
        backlinks={identity: (Backlink(file.path, edge.span),)},
        directories={Path("."): (DirectoryMember("pages", "directory"),)},
    )


def test_v2_serialization_has_exact_top_level_keys() -> None:
    payload = _v2_index().to_dict()
    assert set(payload) == {"schema_version", "revision", "files", "backlinks", "directories"}


def test_v2_serialization_is_deterministic_across_insertion_order() -> None:
    identity = _b_identity()
    edge = OutboundEdge(identity, LinkSpan(6, 10))
    file_a = IndexedFile(path=Path("pages/a.md"), sha256="a" * 64, outbound=(edge,))
    file_c = IndexedFile(path=Path("pages/c.md"), sha256="c" * 64, outbound=())
    directories = {Path("."): (DirectoryMember("pages", "directory"),)}
    forward = LinkIndex(
        revision="3",
        files={file_a.path: file_a, file_c.path: file_c},
        backlinks={identity: (Backlink(file_a.path, edge.span),)},
        directories=directories,
    )
    reverse = LinkIndex(
        revision="3",
        files={file_c.path: file_c, file_a.path: file_a},
        backlinks={identity: (Backlink(file_a.path, edge.span),)},
        directories=directories,
    )
    assert forward.to_json() == reverse.to_json()


def test_v2_round_trip() -> None:
    index = _v2_index()
    assert LinkIndex.from_json(index.to_json()) == index


def test_v2_rejects_wrong_schema() -> None:
    payload = _v2_index().to_dict()
    payload["schema_version"] = 999
    with pytest.raises(ValueError):
        LinkIndex.from_dict(payload)


def test_v2_rejects_inconsistent_reverse_table() -> None:
    identity = _b_identity()
    edge = OutboundEdge(identity, LinkSpan(6, 10))
    file = IndexedFile(path=Path("pages/a.md"), sha256="a" * 64, outbound=(edge,))
    # outbound 声明了边但 backlinks 缺失
    with pytest.raises(ValueError):
        LinkIndex(revision="1", files={file.path: file}, backlinks={}, directories={})
    # backlinks 声明了 outbound 推不出的边
    ghost = ResourceIdentity(ResourceKind.PAGE, "pages/ghost.md")
    with pytest.raises(ValueError):
        LinkIndex(
            revision="1",
            files={file.path: file},
            backlinks={ghost: (Backlink(file.path, LinkSpan(0, 1)),)},
            directories={},
        )


@pytest.mark.parametrize("revision", ["1", "9", "10", "123456789012345678901234567890"])
def test_v2_accepts_valid_revisions(revision: str) -> None:
    LinkIndex(revision=revision, files={}, backlinks={}, directories={})


@pytest.mark.parametrize("revision", ["0", "01", "+1", "-1", "1.0", "1e3", " 1", "1 ", ""])
def test_v2_rejects_invalid_revisions(revision: str) -> None:
    with pytest.raises(ValueError):
        LinkIndex(revision=revision, files={}, backlinks={}, directories={})


def test_v2_parse_rejects_invalid_sha256() -> None:
    payload = _v2_index().to_dict()
    files = cast("dict[str, dict[str, object]]", payload["files"])
    first = next(iter(files.values()))
    first["sha256"] = "xyz"
    with pytest.raises(ValueError):
        LinkIndex.from_dict(payload)


def test_v2_parse_rejects_inverted_span() -> None:
    payload = _v2_index().to_dict()
    files = cast("dict[str, dict[str, object]]", payload["files"])
    first = next(iter(files.values()))
    outbound = cast("list[dict[str, object]]", first["outbound"])
    outbound[0]["start"] = 10
    outbound[0]["end"] = 5
    with pytest.raises(ValueError):
        LinkIndex.from_dict(payload)


def test_v2_parse_rejects_unknown_resource_kind() -> None:
    payload = _v2_index().to_dict()
    files = cast("dict[str, dict[str, object]]", payload["files"])
    first = next(iter(files.values()))
    outbound = cast("list[dict[str, object]]", first["outbound"])
    outbound[0]["kind"] = "bogus"
    with pytest.raises(ValueError):
        LinkIndex.from_dict(payload)


def test_v2_parse_rejects_unknown_directory_member_kind() -> None:
    payload = _v2_index().to_dict()
    directories = cast("dict[str, list[dict[str, object]]]", payload["directories"])
    first = next(iter(directories.values()))
    first[0]["kind"] = "bogus"
    with pytest.raises(ValueError):
        LinkIndex.from_dict(payload)


def test_materialize_index_binds_revision_and_content() -> None:
    index = _v2_index()
    content = LinkIndexContent(
        files=index.files, backlinks=index.backlinks, directories=index.directories
    )
    materialized = materialize_index("7", content)
    assert materialized.revision == "7"
    assert materialized.schema_version == 2
    assert materialized.files == index.files
    assert materialized.backlinks == index.backlinks


def test_materialize_index_rejects_inconsistent_content() -> None:
    identity = _b_identity()
    edge = OutboundEdge(identity, LinkSpan(6, 10))
    file = IndexedFile(path=Path("pages/a.md"), sha256="a" * 64, outbound=(edge,))
    content = LinkIndexContent(files={file.path: file}, backlinks={}, directories={})
    with pytest.raises(ValueError):
        materialize_index("1", content)


# ---------------------------------------------------------------------------
# v2 纯增量计算：build_index_content
# ---------------------------------------------------------------------------


def _baseline_with_a() -> LinkIndex:
    identity = _b_identity()
    edge = OutboundEdge(identity, LinkSpan(4, 8))
    file = IndexedFile(
        path=Path("pages/a.md"),
        sha256=sha256_bytes(b"[b](b.md)\n"),
        outbound=(edge,),
    )
    return LinkIndex(
        revision="3",
        files={file.path: file},
        backlinks={identity: (Backlink(file.path, edge.span),)},
        directories={Path("."): (DirectoryMember("pages", "directory"),)},
    )


def test_build_index_content_unchanged_reuses_outbound() -> None:
    scan = build_index_content(
        documents={Path("pages/a.md"): b"[b](b.md)\n"},
        directories={Path("."): (DirectoryMember("pages", "directory"),)},
        baseline=_baseline_with_a(),
    )
    assert scan.stats.read_files == 1
    assert scan.stats.read_bytes == len(b"[b](b.md)\n")
    assert scan.stats.parsed_files == 0
    assert scan.changed is False
    assert scan.content.files[Path("pages/a.md")].outbound == (
        OutboundEdge(_b_identity(), LinkSpan(4, 8)),
    )


def test_build_index_content_single_change_parses_only_one() -> None:
    identity_c = ResourceIdentity(ResourceKind.PAGE, "pages/c.md")
    c_file = IndexedFile(
        path=Path("pages/c.md"),
        sha256=sha256_bytes(b"[c](c.md)\n"),
        outbound=(OutboundEdge(identity_c, LinkSpan(4, 8)),),
    )
    baseline = LinkIndex(
        revision="3",
        files={
            Path("pages/a.md"): _baseline_with_a().files[Path("pages/a.md")],
            c_file.path: c_file,
        },
        backlinks={
            _b_identity(): (Backlink(Path("pages/a.md"), LinkSpan(4, 8)),),
            identity_c: (Backlink(c_file.path, LinkSpan(4, 8)),),
        },
        directories={Path("."): (DirectoryMember("pages", "directory"),)},
    )
    changed_a = b"[c](c.md)\n"
    scan = build_index_content(
        documents={Path("pages/a.md"): changed_a, Path("pages/c.md"): b"[c](c.md)\n"},
        directories=baseline.directories,
        baseline=baseline,
    )
    assert scan.stats.read_files == 2
    assert scan.stats.parsed_files == 1
    new_a = scan.content.files[Path("pages/a.md")]
    assert new_a.sha256 == sha256_bytes(changed_a)
    assert new_a.outbound == (OutboundEdge(identity_c, LinkSpan(4, 8)),)
    assert scan.content.files[Path("pages/c.md")].outbound == c_file.outbound
    assert scan.changed is True


def test_build_index_content_deletion_removes_reverse_edges() -> None:
    scan = build_index_content(
        documents={},
        directories={Path("."): (DirectoryMember("pages", "directory"),)},
        baseline=_baseline_with_a(),
    )
    assert Path("pages/a.md") not in scan.content.files
    assert scan.content.backlinks == {}
    assert scan.changed is True


def test_build_index_content_addition_adds_record() -> None:
    scan = build_index_content(
        documents={Path("pages/new.md"): b"[b](b.md)\n"},
        directories={Path("."): (DirectoryMember("pages", "directory"),)},
        baseline=None,
    )
    assert set(scan.content.files) == {Path("pages/new.md")}
    assert scan.stats.parsed_files == 1
    assert scan.stats.read_files == 1
    assert scan.changed is True
    assert scan.content.files[Path("pages/new.md")].outbound == (
        OutboundEdge(_b_identity(), LinkSpan(4, 8)),
    )


def test_build_index_content_same_sha256_new_path_reparses() -> None:
    raw = b"[b](b.md)\n"
    scan = build_index_content(
        documents={Path("pages/moved.md"): raw},
        directories={Path("."): (DirectoryMember("pages", "directory"),)},
        baseline=_baseline_with_a(),
    )
    assert Path("pages/a.md") not in scan.content.files
    moved = scan.content.files[Path("pages/moved.md")]
    assert moved.sha256 == sha256_bytes(raw)
    assert scan.stats.parsed_files == 1
    # 同摘要新路径必须按新来源重新解析资源身份
    assert moved.outbound == (OutboundEdge(_b_identity(), LinkSpan(4, 8)),)
    assert scan.changed is True


def test_build_index_content_ignores_non_markdown() -> None:
    scan = build_index_content(
        documents={
            Path("pages/a.md"): b"[b](b.md)\n",
            Path("assets/logo.png"): b"\x89PNG\r\n\x1a\n",
        },
        directories={
            Path("."): (
                DirectoryMember("pages", "directory"),
                DirectoryMember("assets", "directory"),
            )
        },
        baseline=None,
    )
    assert set(scan.content.files) == {Path("pages/a.md")}
    assert scan.stats.read_files == 1
    assert scan.stats.read_bytes == len(b"[b](b.md)\n")
    assert scan.stats.parsed_files == 1


def test_build_index_content_directory_change_sets_changed() -> None:
    scan = build_index_content(
        documents={Path("pages/a.md"): b"[b](b.md)\n"},
        directories={
            Path("."): (
                DirectoryMember("pages", "directory"),
                DirectoryMember("new.txt", "file"),
            )
        },
        baseline=_baseline_with_a(),
    )
    assert scan.stats.parsed_files == 0
    assert scan.stats.read_files == 1
    assert scan.changed is True
