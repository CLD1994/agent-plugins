"""可失效的 Markdown 反向链接索引。

索引是性能缓存，不改变 move 的全根闭包语义。文件格式有明确版本、根身份和
完整性字段；读取失败统一归类为 ``corrupt``，不存在归类为 ``unknown``。
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, cast

from draft.errors import DraftError
from draft.markdown import extract_body_links
from draft.operations import sha256_bytes
from draft.resources import ResourceIdentity, ResourceKind, resolve_body_resource

if TYPE_CHECKING:
    from draft.models import FileMutation

INDEX_SCHEMA_VERSION = 1
INDEX_RELATIVE_PATH = Path(".draft") / "link-index.json"
INVALID_RELATIVE_PATH = Path(".draft") / "link-index.invalid"


class Completeness(StrEnum):
    COMPLETE = "complete"
    INVALID = "invalid"


class Freshness(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    UNKNOWN = "unknown"
    CORRUPT = "corrupt"


@dataclass(frozen=True, order=True)
class FileFingerprint:
    file_id: int
    size: int
    mtime_ns: int


@dataclass(frozen=True, order=True)
class LinkSpan:
    start: int
    end: int


@dataclass(frozen=True, order=True)
class OutboundEdge:
    resource_identity: ResourceIdentity
    span: LinkSpan


@dataclass(frozen=True, order=True)
class Backlink:
    source_path: Path
    span: LinkSpan


@dataclass(frozen=True, order=True)
class DirectoryMember:
    name: str
    kind: str


@dataclass(frozen=True)
class IndexedFile:
    path: Path
    fingerprint: FileFingerprint
    sha256: str
    parse_status: str
    outbound: tuple[OutboundEdge, ...] = ()


@dataclass(frozen=True)
class LinkIndex:
    root_id: str
    generation: int
    completeness: Completeness
    files: dict[Path, IndexedFile] = field(
        default_factory=lambda: dict[Path, IndexedFile]()
    )
    backlinks: dict[ResourceIdentity, tuple[Backlink, ...]] = field(
        default_factory=lambda: dict[ResourceIdentity, tuple[Backlink, ...]]()
    )
    directories: dict[Path, tuple[DirectoryMember, ...]] = field(
        default_factory=lambda: dict[Path, tuple[DirectoryMember, ...]]()
    )
    schema_version: int = INDEX_SCHEMA_VERSION
    event_seq: int | None = None

    def __post_init__(self) -> None:
        if self.schema_version != INDEX_SCHEMA_VERSION:
            raise ValueError("unsupported index schema")
        if self.generation < 0:
            raise ValueError("generation must be non-negative")
        _validate_reverse_edges(self.files, self.backlinks)

    @property
    def status(self) -> Completeness:
        return self.completeness

    def to_dict(self) -> dict[str, object]:
        return {
            "root_id": self.root_id,
            "schema_version": self.schema_version,
            "generation": self.generation,
            "completeness": self.completeness.value,
            "event_seq": self.event_seq,
            "files": {
                path.as_posix(): {
                    "file_id": item.fingerprint.file_id,
                    "size": item.fingerprint.size,
                    "mtime_ns": item.fingerprint.mtime_ns,
                    "sha256": item.sha256,
                    "parse_status": item.parse_status,
                    "outbound": [
                        {
                            "kind": edge.resource_identity.kind.value,
                            "value": edge.resource_identity.value,
                            "start": edge.span.start,
                            "end": edge.span.end,
                        }
                        for edge in item.outbound
                    ],
                }
                for path, item in sorted(self.files.items(), key=lambda pair: pair[0].as_posix())
            },
            "backlinks": {
                f"{identity.kind.value}:{identity.value}": [
                    {
                        "source_path": edge.source_path.as_posix(),
                        "start": edge.span.start,
                        "end": edge.span.end,
                    }
                    for edge in sorted(edges)
                ]
                for identity, edges in sorted(
                    self.backlinks.items(), key=lambda pair: (pair[0].kind.value, pair[0].value)
                )
            },
            "directories": {
                path.as_posix(): [{"name": member.name, "kind": member.kind} for member in members]
                for path, members in sorted(
                    self.directories.items(), key=lambda pair: pair[0].as_posix()
                )
            },
        }

    def to_json(self) -> bytes:
        return (
            json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        ).encode()

    @classmethod
    def from_dict(cls, payload: object) -> LinkIndex:
        if not isinstance(payload, dict):
            raise ValueError("index root must be an object")
        data = cast("dict[str, object]", payload)
        if data.get("schema_version") != INDEX_SCHEMA_VERSION:
            raise ValueError("unsupported index schema")
        root_id = _required_str(data, "root_id")
        generation = _required_int(data, "generation")
        completeness = Completeness(_required_str(data, "completeness"))
        event_seq = data.get("event_seq")
        if event_seq is not None and (
            not isinstance(event_seq, int) or isinstance(event_seq, bool)
        ):
            raise ValueError("invalid event_seq")
        files = _decode_files(data.get("files"))
        backlinks = _decode_backlinks(data.get("backlinks"))
        directories = _decode_directories(data.get("directories"))
        return cls(
            root_id, generation, completeness, files, backlinks, directories, event_seq=event_seq
        )

    @classmethod
    def from_json(cls, raw: bytes) -> LinkIndex:
        return cls.from_dict(json.loads(raw.decode("utf-8")))

    def add_backlink(self, identity: ResourceIdentity, backlink: Backlink) -> LinkIndex:
        current = set(self.backlinks.get(identity, ()))
        current.add(backlink)
        updated = dict(self.backlinks)
        updated[identity] = tuple(sorted(current))
        return LinkIndex(
            self.root_id,
            self.generation,
            self.completeness,
            dict(self.files),
            updated,
            dict(self.directories),
            self.schema_version,
            self.event_seq,
        )

    def remove_backlink(self, identity: ResourceIdentity, backlink: Backlink) -> LinkIndex:
        current = tuple(edge for edge in self.backlinks.get(identity, ()) if edge != backlink)
        updated = dict(self.backlinks)
        if current:
            updated[identity] = current
        else:
            updated.pop(identity, None)
        return LinkIndex(
            self.root_id,
            self.generation,
            self.completeness,
            dict(self.files),
            updated,
            dict(self.directories),
            self.schema_version,
            self.event_seq,
        )


def _required_str(data: dict[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise ValueError(f"invalid {key}")
    return value


def _required_int(data: dict[str, object], key: str) -> int:
    value = data.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"invalid {key}")
    return value


def _decode_files(value: object) -> dict[Path, IndexedFile]:
    if not isinstance(value, dict):
        raise ValueError("invalid files")
    value = cast("dict[str, object]", value)
    result: dict[Path, IndexedFile] = {}
    for raw_path, raw_item in value.items():
        if not isinstance(raw_item, dict):
            raise ValueError("invalid file record")
        item = cast("dict[str, object]", raw_item)
        outbound_raw = item.get("outbound")
        if not isinstance(outbound_raw, list):
            raise ValueError("invalid outbound")
        outbound: list[OutboundEdge] = []
        for raw_edge in cast("list[object]", outbound_raw):
            if not isinstance(raw_edge, dict):
                raise ValueError("invalid outbound edge")
            edge = cast("dict[str, object]", raw_edge)
            outbound.append(
                OutboundEdge(
                    ResourceIdentity(
                        ResourceKind(_required_str(edge, "kind")), _required_str(edge, "value")
                    ),
                    LinkSpan(_required_int(edge, "start"), _required_int(edge, "end")),
                )
            )
        result[Path(raw_path)] = IndexedFile(
            Path(raw_path),
            FileFingerprint(
                _required_int(item, "file_id"),
                _required_int(item, "size"),
                _required_int(item, "mtime_ns"),
            ),
            _required_str(item, "sha256"),
            _required_str(item, "parse_status"),
            tuple(outbound),
        )
    return result


def _decode_backlinks(value: object) -> dict[ResourceIdentity, tuple[Backlink, ...]]:
    if not isinstance(value, dict):
        raise ValueError("invalid backlinks")
    value = cast("dict[str, object]", value)
    result: dict[ResourceIdentity, tuple[Backlink, ...]] = {}
    for key, raw_edges in value.items():
        if ":" not in key or not isinstance(raw_edges, list):
            raise ValueError("invalid backlink record")
        kind, value_ = key.split(":", 1)
        identity = ResourceIdentity(ResourceKind(kind), value_)
        edges: list[Backlink] = []
        for raw_edge in cast("list[object]", raw_edges):
            if not isinstance(raw_edge, dict):
                raise ValueError("invalid backlink edge")
            edge = cast("dict[str, object]", raw_edge)
            edges.append(
                Backlink(
                    Path(_required_str(edge, "source_path")),
                    LinkSpan(_required_int(edge, "start"), _required_int(edge, "end")),
                )
            )
        result[identity] = tuple(sorted(set(edges)))
    return result


def _decode_directories(value: object) -> dict[Path, tuple[DirectoryMember, ...]]:
    if not isinstance(value, dict):
        raise ValueError("invalid directories")
    value = cast("dict[str, object]", value)
    result: dict[Path, tuple[DirectoryMember, ...]] = {}
    for raw_path, raw_members in value.items():
        if not isinstance(raw_members, list):
            raise ValueError("invalid directory record")
        members: list[DirectoryMember] = []
        for raw_member in cast("list[object]", raw_members):
            if not isinstance(raw_member, dict):
                raise ValueError("invalid directory member")
            member = cast("dict[str, object]", raw_member)
            members.append(
                DirectoryMember(_required_str(member, "name"), _required_str(member, "kind"))
            )
        result[Path(raw_path)] = tuple(sorted(set(members)))
    return result


def _validate_reverse_edges(
    files: dict[Path, IndexedFile], backlinks: dict[ResourceIdentity, tuple[Backlink, ...]]
) -> None:
    expected: dict[ResourceIdentity, set[Backlink]] = {}
    for item in files.values():
        for edge in item.outbound:
            expected.setdefault(edge.resource_identity, set()).add(Backlink(item.path, edge.span))
    actual = {identity: set(edges) for identity, edges in backlinks.items()}
    if expected != actual:
        raise ValueError("backlink table inconsistent")


def index_path(root: Path) -> Path:
    return root / INDEX_RELATIVE_PATH


def read_index(root: Path) -> tuple[Freshness, LinkIndex | None, tuple[str, ...]]:
    status, index, details, _ = read_index_snapshot(root)
    return status, index, details


def read_index_snapshot(
    root: Path,
) -> tuple[Freshness, LinkIndex | None, tuple[str, ...], bytes | None]:
    path = index_path(root)
    invalid = root / INVALID_RELATIVE_PATH
    try:
        invalid_state = invalid.exists()
    except OSError as exc:
        return Freshness.UNKNOWN, None, ("invalid_unavailable", exc.__class__.__name__), None
    if invalid_state:
        return Freshness.UNKNOWN, None, ("invalidated",), None
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return Freshness.UNKNOWN, None, ("missing",), None
    except OSError as exc:
        return Freshness.UNKNOWN, None, ("unavailable", exc.__class__.__name__), None
    try:
        index = LinkIndex.from_json(raw)
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        return Freshness.CORRUPT, None, ("corrupt", str(exc)), raw
    expected_root = str(root.resolve())
    if index.root_id != expected_root:
        return Freshness.CORRUPT, index, ("root_mismatch",), raw
    if index.completeness is not Completeness.COMPLETE:
        return Freshness.UNKNOWN, index, ("incomplete",), raw
    return Freshness.STALE, index, (), raw


def write_index_atomic(root: Path, index: LinkIndex) -> None:
    target = index_path(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(index.to_json())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, target)
        fd_dir = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(fd_dir)
        finally:
            os.close(fd_dir)
        invalid = root / INVALID_RELATIVE_PATH
        with contextlib.suppress(FileNotFoundError):
            invalid.unlink()
    except OSError as exc:
        with contextlib.suppress(OSError):
            os.unlink(temp_name)
        raise DraftError("operation.index_update_failed", target) from exc


def invalidate_index(root: Path, *, reason: str = "invalid") -> None:
    marker = root / INVALID_RELATIVE_PATH
    marker.parent.mkdir(parents=True, exist_ok=True)
    try:
        marker.write_text(reason + "\n", encoding="utf-8")
    except OSError as exc:
        raise DraftError("operation.index_update_failed", marker) from exc


def build_index(root: Path, *, generation: int = 0) -> LinkIndex:
    files: dict[Path, IndexedFile] = {}
    directories: dict[Path, tuple[DirectoryMember, ...]] = {}
    for directory, dirnames, filenames in os.walk(root):
        directory_path = Path(directory)
        relative_dir = directory_path.relative_to(root)
        if relative_dir == Path(".draft") or relative_dir.parts[:1] == (".draft",):
            dirnames[:] = []
            continue
        members: list[DirectoryMember] = []
        for name in sorted((*dirnames, *filenames)):
            member_path = directory_path / name
            if member_path.is_dir() and not member_path.is_symlink():
                kind = "directory"
            elif member_path.is_file() and not member_path.is_symlink():
                kind = "file"
            elif member_path.is_symlink():
                kind = "symlink"
            else:
                kind = "other"
            members.append(DirectoryMember(name, kind))
        directories[relative_dir] = tuple(members)
        for name in filenames:
            if not name.endswith(".md"):
                continue
            path = directory_path / name
            relative = path.relative_to(root)
            try:
                raw = path.read_bytes()
                stat_result = path.stat()
            except OSError as exc:
                raise DraftError("operation.index_update_failed", relative) from exc
            outbound: list[OutboundEdge] = []
            parse_status = "parsed"
            try:
                links = extract_body_links(raw.decode("utf-8"))
            except (UnicodeDecodeError, DraftError):
                parse_status = "unparseable"
                links = ()
            for link in links:
                try:
                    resource = resolve_body_resource(root, relative, link.destination)
                except DraftError:
                    parse_status = "unparseable"
                    continue
                outbound.append(
                    OutboundEdge(
                        resource.identity,
                        LinkSpan(link.destination_span.start, link.destination_span.end),
                    )
                )
            files[relative] = IndexedFile(
                relative,
                FileFingerprint(
                    getattr(stat_result, "st_ino", 0), stat_result.st_size, stat_result.st_mtime_ns
                ),
                sha256_bytes(raw),
                parse_status,
                tuple(outbound),
            )
    backlinks: dict[ResourceIdentity, list[Backlink]] = {}
    for item in files.values():
        for edge in item.outbound:
            backlinks.setdefault(edge.resource_identity, []).append(Backlink(item.path, edge.span))
    return LinkIndex(
        str(root.resolve()),
        generation,
        Completeness.COMPLETE,
        files,
        {key: tuple(sorted(value)) for key, value in backlinks.items()},
        directories,
    )


def update_index_from_mutations(
    root: Path, index: LinkIndex, mutations: tuple[FileMutation, ...]
) -> LinkIndex:
    """仅解析本次 mutation 改变的 Markdown；未变记录和边直接复用。"""
    files = dict(index.files)
    for mutation in mutations:
        if mutation.path.suffix != ".md":
            continue
        if mutation.content is None:
            files.pop(mutation.path, None)
            continue
        files[mutation.path] = _index_file(root, mutation.path, mutation.content)
    backlinks: dict[ResourceIdentity, list[Backlink]] = {}
    for item in files.values():
        for edge in item.outbound:
            backlinks.setdefault(edge.resource_identity, []).append(Backlink(item.path, edge.span))
    return LinkIndex(
        str(root.resolve()),
        index.generation + 1,
        Completeness.COMPLETE,
        files,
        {identity: tuple(sorted(edges)) for identity, edges in backlinks.items()},
        _directory_snapshot(root),
    )


def _index_file(root: Path, relative: Path, raw: bytes) -> IndexedFile:
    try:
        stat_result = (root / relative).stat()
    except OSError as exc:
        raise DraftError("operation.index_update_failed", relative) from exc
    outbound: list[OutboundEdge] = []
    parse_status = "parsed"
    try:
        links = extract_body_links(raw.decode("utf-8"))
    except (UnicodeDecodeError, DraftError):
        parse_status = "unparseable"
        links = ()
    for link in links:
        try:
            resource = resolve_body_resource(root, relative, link.destination)
        except DraftError:
            parse_status = "unparseable"
            continue
        outbound.append(
            OutboundEdge(
                resource.identity,
                LinkSpan(link.destination_span.start, link.destination_span.end),
            )
        )
    return IndexedFile(
        relative,
        FileFingerprint(
            getattr(stat_result, "st_ino", 0), stat_result.st_size, stat_result.st_mtime_ns
        ),
        sha256_bytes(raw),
        parse_status,
        tuple(outbound),
    )


def _directory_snapshot(root: Path) -> dict[Path, tuple[DirectoryMember, ...]]:
    directories: dict[Path, tuple[DirectoryMember, ...]] = {}
    try:
        for directory, dirnames, filenames in os.walk(root):
            directory_path = Path(directory)
            relative = directory_path.relative_to(root)
            if relative == Path(".draft") or relative.parts[:1] == (".draft",):
                dirnames[:] = []
                continue
            members: list[DirectoryMember] = []
            for name in sorted((*dirnames, *filenames)):
                entry = directory_path / name
                kind = (
                    "directory" if entry.is_dir() and not entry.is_symlink() else
                    "file" if entry.is_file() and not entry.is_symlink() else
                    "symlink" if entry.is_symlink() else "other"
                )
                members.append(DirectoryMember(name, kind))
            directories[relative] = tuple(members)
    except OSError as exc:
        raise DraftError("operation.index_update_failed", root) from exc
    return directories


def check_index_freshness(root: Path, index: LinkIndex) -> Freshness:
    if index.root_id != str(root.resolve()) or index.completeness is not Completeness.COMPLETE:
        return Freshness.UNKNOWN
    try:
        seen_dirs: dict[Path, tuple[DirectoryMember, ...]] = {}
        seen_files: set[Path] = set()
        for directory, dirnames, filenames in os.walk(root):
            directory_path = Path(directory)
            relative_dir = directory_path.relative_to(root)
            if relative_dir == Path(".draft") or relative_dir.parts[:1] == (".draft",):
                dirnames[:] = []
                continue
            members: list[DirectoryMember] = []
            for name in sorted((*dirnames, *filenames)):
                member_path = directory_path / name
                kind = (
                    "directory"
                    if member_path.is_dir() and not member_path.is_symlink()
                    else "file"
                    if member_path.is_file() and not member_path.is_symlink()
                    else "symlink"
                    if member_path.is_symlink()
                    else "other"
                )
                members.append(DirectoryMember(name, kind))
                if name.endswith(".md") and kind == "file":
                    relative = member_path.relative_to(root)
                    seen_files.add(relative)
                    stat_result = member_path.stat()
                    item = index.files.get(relative)
                    if item is None:
                        return Freshness.STALE
                    fingerprint = FileFingerprint(
                        getattr(stat_result, "st_ino", 0),
                        stat_result.st_size,
                        stat_result.st_mtime_ns,
                    )
                    if fingerprint != item.fingerprint:
                        return Freshness.STALE
            seen_dirs[relative_dir] = tuple(members)
        if seen_dirs != index.directories or seen_files != set(index.files):
            return Freshness.STALE
    except OSError:
        return Freshness.UNKNOWN
    return Freshness.FRESH
