"""move 专用事实闭包：根内全部 Markdown 的链接可判定性（规格 §4）。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from draft.errors import DraftError
from draft.graph import INTERNAL_DIR, PAGES_DIR, DraftGraph
from draft.markdown import extract_body_links
from draft.models import MarkdownLink
from draft.resources import ResourceReference, resolve_body_resource


@dataclass(frozen=True)
class UnmanagedFile:
    text: str
    references: tuple[ResourceReference, ...]


@dataclass(frozen=True)
class MoveClosure:
    unmanaged: Mapping[Path, UnmanagedFile]
    blocking: tuple[Path, ...]


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
    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        if entry.name in (INTERNAL_DIR, PAGES_DIR.name):
            # .draft 不是内容；pages/ 下 .md 全部是受管理页面，其可解析性
            # 已由事实加载的 blocking_paths 覆盖。
            continue
        _scan_entry(root, entry, graph, unmanaged, blocking)
    return MoveClosure(unmanaged=unmanaged, blocking=tuple(sorted(blocking)))


def _scan_entry(
    root: Path,
    absolute: Path,
    graph: DraftGraph,
    unmanaged: dict[Path, UnmanagedFile],
    blocking: set[Path],
) -> None:
    if absolute.is_symlink():
        # 符号链接目标由 check 的 link.escape 问题进入既有 blocking。
        return
    if absolute.is_dir():
        for descendant in sorted(absolute.rglob("*"), key=lambda item: item.as_posix()):
            if descendant.is_symlink() or not descendant.is_file():
                continue
            if descendant.suffix != ".md":
                continue
            _scan_unmanaged_file(root, descendant.relative_to(root), graph, unmanaged, blocking)
        return
    if not absolute.is_file() or absolute.suffix != ".md":
        return
    _scan_unmanaged_file(root, absolute.relative_to(root), graph, unmanaged, blocking)


def _scan_unmanaged_file(
    root: Path,
    relative: Path,
    graph: DraftGraph,
    unmanaged: dict[Path, UnmanagedFile],
    blocking: set[Path],
) -> None:
    if relative in graph.pages:
        return
    parsed = decode_and_extract((root / relative).read_bytes())
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
    unmanaged[relative] = UnmanagedFile(text=text, references=references)
