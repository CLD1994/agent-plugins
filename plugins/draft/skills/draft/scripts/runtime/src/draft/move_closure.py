"""move 专用事实闭包：根内全部 Markdown 的链接可判定性（规格 §4）。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from draft.errors import DraftError
from draft.graph import INTERNAL_DIR, PAGES_DIR, DraftGraph
from draft.link_index import LinkIndex
from draft.markdown import extract_body_links
from draft.models import MarkdownLink
from draft.operations import sha256_bytes
from draft.resources import ResourceIdentity, ResourceReference, resolve_body_resource


@dataclass(frozen=True)
class UnmanagedFile:
    text: str
    references: tuple[ResourceReference, ...]
    sha256: str | None = None


@dataclass(frozen=True)
class MoveClosure:
    unmanaged: Mapping[Path, UnmanagedFile]
    blocking: tuple[Path, ...]
    directories: Mapping[Path, tuple[tuple[str, str], ...]] = field(
        default_factory=lambda: dict[Path, tuple[tuple[str, str], ...]]()
    )


def _io_failure_reason(error: OSError) -> str:
    """把闭包读取失败压缩为稳定诊断，不把平台异常文本暴露给调用方。"""
    if isinstance(error, FileNotFoundError):
        return "not_found"
    if isinstance(error, PermissionError):
        return "permission_denied"
    return "io_error"


def decode_and_extract(raw: bytes) -> tuple[str, tuple[MarkdownLink, ...]] | None:
    """不可解码或链接不可提取 → None（调用方计入阻碍，规格 §4.2）。"""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    try:
        return text, extract_body_links(text)
    except DraftError:
        return None


def merged_blocking(
    facts_blocking: Sequence[Path], closure: MoveClosure
) -> tuple[Path, ...]:
    return tuple(sorted(set(facts_blocking) | set(closure.blocking)))


def scan_move_closure(root: Path, graph: DraftGraph) -> MoveClosure:
    unmanaged: dict[Path, UnmanagedFile] = {}
    blocking: set[Path] = set()
    directories: dict[Path, tuple[tuple[str, str], ...]] = {}
    try:
        _record_directory(root, Path("."), directories)
        entries = sorted(root.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise DraftError(
            "operation.external_change", root, details=(_io_failure_reason(exc),)
        ) from exc
    for entry in entries:
        if entry.name in (INTERNAL_DIR, PAGES_DIR.name):
            # .draft 不是内容；pages/ 下 .md 全部是受管理页面，其可解析性
            # 已由事实加载的 blocking_paths 覆盖。
            continue
        _scan_entry(root, entry, graph, unmanaged, blocking, directories)
    return MoveClosure(
        unmanaged=unmanaged,
        blocking=tuple(sorted(blocking)),
        directories=directories,
    )


def scan_indexed_move_closure(
    root: Path,
    graph: DraftGraph,
    index: LinkIndex,
    moved_identities: set[ResourceIdentity],
    moved_paths: set[Path],
) -> MoveClosure:
    """从已验证 fresh 索引读取移动来源及反向命中正文。"""
    candidates = set(moved_paths)
    for identity in moved_identities:
        for backlink in index.backlinks.get(identity, ()):
            candidates.add(backlink.source_path)
    unmanaged: dict[Path, UnmanagedFile] = {}
    blocking: set[Path] = set()
    for relative in sorted(candidates, key=lambda item: item.as_posix()):
        if relative in graph.pages or relative.suffix != ".md":
            continue
        _scan_unmanaged_file(root, relative, graph, unmanaged, blocking)
    directories = {
        path: tuple((member.name, member.kind) for member in members)
        for path, members in index.directories.items()
    }
    return MoveClosure(
        unmanaged=unmanaged,
        blocking=tuple(sorted(blocking)),
        directories=directories,
    )


def _scan_entry(
    root: Path,
    absolute: Path,
    graph: DraftGraph,
    unmanaged: dict[Path, UnmanagedFile],
    blocking: set[Path],
    directories: dict[Path, tuple[tuple[str, str], ...]],
) -> None:
    if absolute.is_symlink():
        # 闭包只承诺根内 .md 普通文件（规格 §4.1），符号链接不在扫描范围——此处跳过。
        # 根级符号链接是否阻碍 move 不取决于本闭包：load_graph_tolerant 会对根级符号
        # 链接产生 link.escape issue 进入既有 blocking_paths（round-1 L5 实证），move
        # 经 merged_blocking 一并判断而被拒。
        return
    if absolute.is_dir():
        relative_dir = absolute.relative_to(root)
        _record_directory(root, relative_dir, directories)
        try:
            descendants = sorted(absolute.rglob("*"), key=lambda item: item.as_posix())
        except OSError as exc:
            raise DraftError(
                "operation.external_change",
                relative_dir,
                details=(_io_failure_reason(exc),),
            ) from exc
        for descendant in descendants:
            try:
                if descendant.is_dir() and not descendant.is_symlink():
                    _record_directory(root, descendant.relative_to(root), directories)
            except OSError as exc:
                raise DraftError(
                    "operation.external_change",
                    descendant.relative_to(root),
                    details=(_io_failure_reason(exc),),
                ) from exc
            if descendant.is_symlink() or not descendant.is_file():
                continue
            if descendant.suffix != ".md":
                continue
            _scan_unmanaged_file(
                root, descendant.relative_to(root), graph, unmanaged, blocking
            )
        return
    if not absolute.is_file() or absolute.suffix != ".md":
        return
    _scan_unmanaged_file(root, absolute.relative_to(root), graph, unmanaged, blocking)


def _record_directory(
    root: Path, relative: Path, directories: dict[Path, tuple[tuple[str, str], ...]]
) -> None:
    target = root if relative == Path(".") else root / relative
    try:
        if target.is_symlink() or not target.is_dir():
            return
        directories[relative] = tuple(
            sorted(
                (
                    entry.name,
                    "directory"
                    if entry.is_dir()
                    else "file"
                    if entry.is_file()
                    else "symlink"
                    if entry.is_symlink()
                    else "other",
                )
                for entry in target.iterdir()
            )
        )
    except OSError as exc:
        raise DraftError(
            "operation.external_change", relative, details=(_io_failure_reason(exc),)
        ) from exc


def _scan_unmanaged_file(
    root: Path,
    relative: Path,
    graph: DraftGraph,
    unmanaged: dict[Path, UnmanagedFile],
    blocking: set[Path],
) -> None:
    if relative in graph.pages:
        return
    try:
        raw = (root / relative).read_bytes()
    except OSError as exc:
        raise DraftError(
            "operation.external_change", relative, details=(_io_failure_reason(exc),)
        ) from exc
    parsed = decode_and_extract(raw)
    if parsed is None:
        blocking.add(relative)
        return
    text, links = parsed
    references = tuple(
        ResourceReference(
            link=link, resource=resolve_body_resource(root, relative, link.destination)
        )
        for link in links
    )
    unmanaged[relative] = UnmanagedFile(
        text=text, references=references, sha256=sha256_bytes(raw)
    )
