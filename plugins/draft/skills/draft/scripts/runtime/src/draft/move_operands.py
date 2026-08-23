"""move 的双类型操作数：分类、映射与结果校验（规格 §2）。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Protocol

from draft.diagnostic_codes import CheckCode
from draft.errors import DraftError
from draft.graph import CONCLUSIONS_PAGE, ROOT_PAGE, DraftGraph
from draft.observations import EntryKind, classify_node
from draft.paths import ResolvedPath, physical_parent, resolve_move_operand

_INTERNAL_DIR = ".draft"
_PAGES_DIR = "pages"
# CLI 参数形态错误码（round-1 L1）：cli.py 预检与分类共享同一权威常量。
MOVE_OPERANDS_INVALID = "cli.move_operands_invalid"


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

    def __post_init__(self) -> None:
        # round-1 L10：results 键必须恰好等于各来源 members 并集；plan_move_request
        # 以 request.results[member] 取值，该不变量由构造方保证、此处集中校验。
        expected = {member for source in self.sources for member in source.members}
        if set(self.results) != expected:
            raise ValueError(
                "results keys must equal the union of source members "
                f"(got {sorted(set(self.results))}, expected {sorted(expected)})"
            )


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


def precheck_move_shape(
    root: Path,
    source_values: Sequence[str],
    target_value: str,
    *,
    probe: MoveOperandProbe | None = None,
) -> None:
    """在加载根事实前拒绝只由参数形状决定的组合。"""
    if probe is None:
        probe = FilesystemMoveProbe()
    target_kind = target_kind_of(target_value)
    for value in source_values:
        relative = _lexical_operand(value)
        if is_internal_territory(relative):
            raise DraftError(CheckCode.PATH_INTERNAL.value, relative)
    if target_kind is not TargetKind.FILE:
        return
    if len(source_values) != 1:
        raise DraftError(MOVE_OPERANDS_INVALID, details=("文件目标只允许一个文件 SOURCE",))
    resolved = probe.resolve_operand(root, source_values[0])
    if probe.node_kind(resolved.absolute) is EntryKind.DIRECTORY:
        raise DraftError(MOVE_OPERANDS_INVALID, details=("文件目标只允许一个文件 SOURCE",))


def validate_results(graph: DraftGraph, request: MoveRequest) -> None:
    """规格 §2.4 结果校验：命名空间、既有页、来源目录内部、碰撞、父页占据。

    round-1 L10：目录来源由 MoveRequest.sources 自推导，不再由调用方派生传入。
    """
    results = request.results
    directory_sources = [
        source.path for source in request.sources if source.kind is SourceKind.DIRECTORY
    ]
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
    members: list[Path] = []
    base = root / directory
    for descendant in sorted(base.rglob("*"), key=lambda item: item.as_posix()):
        relative = descendant.relative_to(root)
        if is_internal_territory(relative):
            continue
        if classify_node(descendant) is EntryKind.SYMLINK:
            raise DraftError(CheckCode.PATH_SYMLINK_ESCAPE.value, relative)
        if not descendant.is_file() or descendant.suffix != ".md":
            continue
        members.append(relative)
    if not members:
        raise DraftError("structure.unknown_page", directory)
    return tuple(members)


class MoveOperandProbe(Protocol):
    """操作数分类的文件系统探测边界（round-1 M4）：生产走真实 FS，单测注入 fake。

    词法判定（顺序 1—2）留在 ``classify_move_request`` 内；顺序 3—8 的探测经此注入，
    使决策表在「单元测试不访问真实 FS」约束下可单元化。
    """

    def resolve_operand(self, root: Path, value: str) -> ResolvedPath:
        """词法拒绝后的规范化探测；符号链接组件以 path.symlink_escape 拒绝。"""
        ...

    def node_kind(self, absolute: Path) -> EntryKind: ...

    def enumerate_directory(self, root: Path, relative: Path) -> tuple[Path, ...]:
        """目录来源成员枚举；符号链接条目拒绝整批、空枚举 → structure.unknown_page。"""
        ...


class FilesystemMoveProbe:
    """生产探测：复用既有 resolve_move_operand/classify_node/enumerate_directory。"""

    def resolve_operand(self, root: Path, value: str) -> ResolvedPath:
        return resolve_move_operand(root, value)

    def node_kind(self, absolute: Path) -> EntryKind:
        return classify_node(absolute)

    def enumerate_directory(self, root: Path, relative: Path) -> tuple[Path, ...]:
        return enumerate_directory(root, relative)


def classify_move_request(
    root: Path,
    graph: DraftGraph,
    source_values: Sequence[str],
    target_value: str,
    *,
    probe: MoveOperandProbe | None = None,
) -> MoveRequest:
    """规格 §2.2 顺序 1—8 与 §2.1 形态校验；在事实加载之后调用（规格 §5）。

    词法判定（顺序 1—2）与文件系统探测（顺序 3—8）经注入的 ``probe`` 解耦：生产
    走 ``FilesystemMoveProbe``，单测注入 fake 使决策表可单元化（round-1 M4）。
    """
    if probe is None:
        probe = FilesystemMoveProbe()
    target_kind = target_kind_of(target_value)
    target_relative = _lexical_operand(target_value)
    sources: list[MoveSource] = []
    for value in source_values:
        relative = _lexical_operand(value)
        if not relative.parts:
            # round-1 L9：根本身作为来源会全根枚举后以误导性 descendant 拒绝；此处
            # 显式以 structure.protected 拒绝（根目录不可作为移动来源）。
            raise DraftError("structure.protected", Path(value))
        if is_internal_territory(relative):
            raise DraftError(CheckCode.PATH_INTERNAL.value, relative)
        resolved = probe.resolve_operand(root, value)
        entry_kind = probe.node_kind(resolved.absolute)
        if entry_kind is EntryKind.DIRECTORY:
            if relative == Path(_PAGES_DIR):
                raise DraftError("structure.protected", relative)
            members = probe.enumerate_directory(root, relative)
            sources.append(MoveSource(relative, SourceKind.DIRECTORY, members))
            continue
        if entry_kind is EntryKind.FILE:
            # 规格 §2.5：根 index.md 与 conclusions.md 无论受管资格一律保护。
            # 候选根 + not_generated 时 conclusions.md 不在 graph.pages 内，该检查
            # 必须先于受管分支执行，否则会被当作未受管理文件提升走。
            if relative in (ROOT_PAGE, CONCLUSIONS_PAGE):
                raise DraftError("structure.protected", relative)
            if relative in graph.pages:
                sources.append(MoveSource(relative, SourceKind.MANAGED_PAGE, (relative,)))
                continue
            if resolved.relative.suffix == ".md":
                sources.append(MoveSource(relative, SourceKind.UNMANAGED_FILE, (relative,)))
                continue
            raise DraftError("path.not_markdown", relative)
        raise DraftError("structure.unknown_page", relative)

    owners: dict[Path, Path] = {}
    for source in sources:
        for member in source.members:
            previous = owners.get(member)
            if previous is not None:
                raise DraftError(
                    "structure.conflict",
                    member,
                    details=tuple(sorted((previous.as_posix(), source.path.as_posix()))),
                )
            owners[member] = source.path

    if target_kind is TargetKind.FILE:
        if len(sources) != 1 or sources[0].kind is SourceKind.DIRECTORY:
            raise DraftError(MOVE_OPERANDS_INVALID, details=("文件目标只允许一个文件 SOURCE",))
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
