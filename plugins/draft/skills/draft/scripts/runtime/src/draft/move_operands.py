"""move 的双类型操作数：分类、映射与结果校验（规格 §2）。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath

from draft.diagnostic_codes import CheckCode
from draft.errors import DraftError
from draft.graph import DraftGraph
from draft.paths import physical_parent

_INTERNAL_DIR = ".draft"
_PAGES_DIR = "pages"


class SourceKind(StrEnum):
    MANAGED_PAGE = "managed_page"
    UNMANAGED_FILE = "unmanaged_file"
    DIRECTORY = "directory"


class TargetKind(StrEnum):
    FILE = "file"
    PREFIX = "prefix"


@dataclass(frozen=True)
class MoveSource:
    path: Path
    kind: SourceKind
    members: tuple[Path, ...]


@dataclass(frozen=True)
class MoveTarget:
    path: Path
    kind: TargetKind


@dataclass(frozen=True)
class MoveRequest:
    sources: tuple[MoveSource, ...]
    target: MoveTarget
    results: Mapping[Path, Path]


def is_internal_territory(relative: Path) -> bool:
    """内部协议领地是词法事实：第一个路径段为 .draft（规格 §2.2 顺序 2）。"""
    return bool(relative.parts) and relative.parts[0] == _INTERNAL_DIR


def target_kind_of(value: str) -> TargetKind:
    return TargetKind.FILE if PurePosixPath(value).suffix == ".md" else TargetKind.PREFIX


def map_sources(sources: Sequence[MoveSource], target: MoveTarget) -> dict[Path, Path]:
    """规格 §2.4 映射规则：文件取基名，目录取目录名 + 成员相对路径。"""
    results: dict[Path, Path] = {}
    for source in sources:
        if source.kind is SourceKind.DIRECTORY:
            for member in source.members:
                results[member] = target.path / source.path.name / member.relative_to(
                    source.path
                )
        else:
            results[source.path] = target.path / source.path.name
    return results


def validate_results(
    graph: DraftGraph, results: Mapping[Path, Path], directory_sources: Sequence[Path]
) -> None:
    """规格 §2.4 结果校验：命名空间、既有页、来源目录内部、碰撞、父页占据。"""
    result_paths = set(results.values())
    source_paths = set(results)
    seen: set[Path] = set()
    for source in sorted(results, key=lambda path: results[path].as_posix()):
        result = results[source]
        if not result.parts or result.parts[0] != _PAGES_DIR:
            raise DraftError("structure.invalid_target", result)
        if result in graph.pages:
            raise DraftError("structure.target_exists", result)
        for directory in directory_sources:
            if result == directory or result.is_relative_to(directory):
                raise DraftError("structure.descendant", result)
        if result in seen:
            raise DraftError("structure.conflict", source)
        seen.add(result)
        parent = physical_parent(result)
        if parent is None:
            raise DraftError("structure.missing_parent", result)
        if parent in result_paths:
            continue
        if parent in graph.pages and parent not in source_paths:
            continue
        raise DraftError("structure.missing_parent", result)


def enumerate_directory(root: Path, directory: Path) -> tuple[Path, ...]:
    """规格 §2.3：只收集 .md 普通文件；符号链接条目拒绝整批；空枚举 → unknown。"""
    from draft.observations import EntryKind
    from draft.operations import classify_node

    members: list[Path] = []
    base = root / directory
    for descendant in sorted(base.rglob("*"), key=lambda item: item.as_posix()):
        relative = descendant.relative_to(root)
        if _INTERNAL_DIR in relative.parts:
            continue
        if classify_node(descendant) is EntryKind.SYMLINK:
            raise DraftError(CheckCode.PATH_SYMLINK_ESCAPE.value, relative)
        if not descendant.is_file() or descendant.suffix != ".md":
            continue
        members.append(relative)
    if not members:
        raise DraftError("structure.unknown_page", directory)
    return tuple(members)


def classify_move_request(
    root: Path,
    graph: DraftGraph,
    source_values: Sequence[str],
    target_value: str,
) -> MoveRequest:
    """规格 §2.2 顺序 1—8 与 §2.1 形态校验；在事实加载之后调用（规格 §5）。"""
    from draft.graph import CONCLUSIONS_PAGE, ROOT_PAGE
    from draft.observations import EntryKind
    from draft.operations import classify_node
    from draft.paths import resolve_move_operand

    target_kind = target_kind_of(target_value)
    target_relative = _lexical_operand(target_value)
    sources: list[MoveSource] = []
    for value in source_values:
        relative = _lexical_operand(value)
        if is_internal_territory(relative):
            raise DraftError(CheckCode.PATH_INTERNAL.value, relative)
        resolved = resolve_move_operand(root, value)
        entry_kind = classify_node(resolved.absolute)
        if entry_kind is EntryKind.DIRECTORY:
            if relative == Path(_PAGES_DIR):
                raise DraftError("structure.protected", relative)
            members = enumerate_directory(root, relative)
            sources.append(MoveSource(relative, SourceKind.DIRECTORY, members))
            continue
        if entry_kind is EntryKind.FILE:
            if relative in graph.pages:
                if relative in (ROOT_PAGE, CONCLUSIONS_PAGE):
                    raise DraftError("structure.protected", relative)
                sources.append(MoveSource(relative, SourceKind.MANAGED_PAGE, (relative,)))
                continue
            if resolved.relative.suffix == ".md":
                sources.append(MoveSource(relative, SourceKind.UNMANAGED_FILE, (relative,)))
                continue
            raise DraftError("path.not_markdown", relative)
        raise DraftError("structure.unknown_page", relative)

    if target_kind is TargetKind.FILE:
        if len(sources) != 1 or sources[0].kind is SourceKind.DIRECTORY:
            raise DraftError(
                "cli.move_operands_invalid", details=("文件目标只允许一个文件 SOURCE",)
            )
        results: Mapping[Path, Path] = {sources[0].path: target_relative}
    else:
        results = map_sources(sources, MoveTarget(target_relative, target_kind))
    return MoveRequest(tuple(sources), MoveTarget(target_relative, target_kind), results)


def _lexical_operand(value: str) -> Path:
    """规格 §2.2 顺序 1：绝对路径与 .. 在任何文件系统探测之前拒绝。"""
    posix = PurePosixPath(value)
    if posix.is_absolute() or ".." in posix.parts:
        raise DraftError("path.escape", Path(value))
    return Path(*posix.parts)
