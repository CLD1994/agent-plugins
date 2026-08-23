from __future__ import annotations

import posixpath
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Protocol
from urllib.parse import quote

from draft.diagnostic_codes import CheckCode, RootCode
from draft.errors import DraftError

if TYPE_CHECKING:
    from draft.graph import MarkerState


class RootSource(StrEnum):
    CLI = "cli"
    ENV = "env"
    PWD = "pwd"


@dataclass(frozen=True)
class ManagedRoot:
    path: Path
    marker: MarkerState
    source: RootSource


@dataclass(frozen=True)
class ResolvedPath:
    root: Path
    relative: Path
    absolute: Path


class RootProbe(Protocol):
    """受真实文件系统边界约束的根探测协议；单元 fake 与生产实现共享同一协议。"""

    def is_directory(self, path: Path) -> bool: ...

    def exists(self, path: Path) -> bool: ...

    def managed_at(self, directory: Path) -> MarkerState | None:
        """返回该目录作为受管根的 marker 状态。

        - 无 ``.draft/``：None（继续祖先发现或按无 marker 处理）；
        - ``.draft/`` 存在但 marker 缺失/损坏/不支持：以具体稳定错误码抛 DraftError，
          不得折叠为 None 或越过后继续向上；
        - 合法 marker：返回 MarkerState。
        """
        ...


class FilesystemProbe:
    """生产实现：直接访问文件系统，复用共享 marker 解析器。"""

    def is_directory(self, path: Path) -> bool:
        return path.is_dir() and not path.is_symlink()

    def exists(self, path: Path) -> bool:
        return path.exists()

    def managed_at(self, directory: Path) -> MarkerState | None:
        from draft.graph import (
            INTERNAL_DIR,
            MARKER_PATH,
            classify_marker_parent,
            read_marker,
        )

        if not classify_marker_parent(directory / INTERNAL_DIR, directory / MARKER_PATH):
            return None
        return read_marker(directory)


def _expand_root(value: Path) -> Path:
    try:
        return value.expanduser()
    except RuntimeError as exc:
        raise DraftError("root.missing", value) from exc


def _source_for(explicit: str | None, environment_value: str | None) -> RootSource:
    if explicit is not None:
        return RootSource.CLI
    if environment_value is not None:
        return RootSource.ENV
    return RootSource.PWD


def discover_root_directory(
    *,
    explicit: str | None,
    environment_value: str | None,
    physical_cwd: Path,
    probe: RootProbe,
) -> Path:
    """统一根定位：CLI ``--root`` > 绝对 ``DRAFT_ROOT`` > pwd 祖先发现。

    只返回规范绝对根目录，不校验该目录的 marker；调用方按自身语义决定 marker
    验证（check 由 check_draft 按 mode 处理，其余命令用 select_managed_root）。
    高优先级来源存在但无效时直接失败，不回退。
    """
    if explicit is not None:
        return _discover_explicit(Path(explicit), physical_cwd, probe, RootSource.CLI)
    if environment_value is not None:
        return _discover_explicit(Path(environment_value), physical_cwd, probe, RootSource.ENV)
    return _discover_from_pwd(physical_cwd, probe)


def select_managed_root(
    *,
    explicit: str | None,
    environment_value: str | None,
    physical_cwd: Path,
    probe: RootProbe,
) -> ManagedRoot:
    """统一受管根选择：定位并验证 marker，返回根身份与来源。"""
    directory = discover_root_directory(
        explicit=explicit,
        environment_value=environment_value,
        physical_cwd=physical_cwd,
        probe=probe,
    )
    state = probe.managed_at(directory)
    if state is None:
        from draft.graph import MARKER_PATH

        raise DraftError(CheckCode.FORMAT_MISSING.value, directory / MARKER_PATH)
    return ManagedRoot(
        path=directory, marker=state, source=_source_for(explicit, environment_value)
    )


def _discover_explicit(
    value: Path, physical_cwd: Path, probe: RootProbe, source: RootSource
) -> Path:
    if source is RootSource.ENV and not value.is_absolute():
        # DRAFT_ROOT 必须非空绝对路径；非法来源值以原始词法路径报告 root.invalid。
        raise DraftError(RootCode.INVALID.value, value)
    expanded = _expand_root(value)
    target = expanded if expanded.is_absolute() else physical_cwd / expanded
    canonical = target.resolve(strict=False)
    if not probe.exists(canonical):
        raise DraftError(RootCode.NOT_FOUND.value, canonical, details=(source.value,))
    if not probe.is_directory(canonical):
        raise DraftError(RootCode.INVALID.value, canonical)
    return canonical


def _discover_from_pwd(physical_cwd: Path, probe: RootProbe) -> Path:
    roots: list[Path] = []
    cursor = physical_cwd
    while True:
        if probe.managed_at(cursor) is not None:
            roots.append(cursor)
        parent = cursor.parent
        if parent == cursor:
            break
        cursor = parent
    if not roots:
        raise DraftError(
            RootCode.NOT_FOUND.value,
            physical_cwd,
            details=(RootSource.PWD.value,),
        )
    if len(roots) > 1:
        raise DraftError(
            RootCode.AMBIGUOUS.value,
            physical_cwd,
            details=tuple(path.resolve(strict=False).as_posix() for path in roots),
        )
    return roots[0]


def resolve_draft_root(value: Path, *, allow_missing: bool = False) -> Path:
    expanded = _expand_root(value)
    if not allow_missing and not expanded.exists():
        raise DraftError("root.missing", expanded)
    if expanded.exists() and (expanded.is_symlink() or not expanded.is_dir()):
        raise DraftError(RootCode.INVALID.value, expanded)
    return expanded.resolve(strict=False)


def _resolve_operand(root: Path, value: str, *, require_markdown: bool) -> ResolvedPath:
    """操作数规范化的共享核心：绝对/``..`` 词法拒绝、逐组件符号链接拒绝、规范化。

    页面操作数与 move 操作数共用（round-1 L3），唯一差异是是否强制 ``.md`` 后缀。
    """
    canonical_root = _expand_root(root).resolve(strict=False)
    posix = PurePosixPath(value)
    if posix.is_absolute():
        raise DraftError("path.escape", Path(value))
    normalized = Path(*posix.parts)
    candidate = canonical_root / normalized
    cursor = canonical_root
    for part in normalized.parts:
        cursor /= part
        if cursor.is_symlink():
            raise DraftError(CheckCode.PATH_SYMLINK_ESCAPE.value, cursor)
    canonical = candidate.resolve(strict=False)
    try:
        relative = canonical.relative_to(canonical_root)
    except ValueError as exc:
        raise DraftError("path.escape", candidate) from exc
    if require_markdown and candidate.suffix != ".md":
        raise DraftError("path.not_markdown", candidate)
    return ResolvedPath(root=canonical_root, relative=relative, absolute=canonical_root / relative)


def resolve_page_operand(root: Path, value: str) -> ResolvedPath:
    return _resolve_operand(root, value, require_markdown=True)


def resolve_move_operand(root: Path, value: str) -> ResolvedPath:
    """规范化 move 操作数；与页面操作数同规则，但不要求 .md 后缀。

    目录与非 .md 普通文件也是合法操作数形状（规格 §2.2），类型分类
    由调用方完成。
    """
    return _resolve_operand(root, value, require_markdown=False)


def physical_parent(path: Path) -> Path | None:
    if path == Path("index.md"):
        return None
    if path == Path("conclusions.md"):
        return Path("index.md")
    if path.parent == Path("pages"):
        return Path("index.md")
    if path.name == "index.md":
        parent_dir = path.parent.parent
        return Path("index.md") if parent_dir == Path("pages") else parent_dir / "index.md"
    parent_dir = path.parent
    return Path("index.md") if parent_dir == Path("pages") else parent_dir / "index.md"


def relative_markdown_destination(from_page: Path, to_page: Path, anchor: str | None) -> str:
    """Canonical relative link destination between two root-relative page paths."""
    base = from_page.parent.as_posix() or "."
    relative = posixpath.relpath(to_page.as_posix(), base)
    encoded = quote(relative, safe="/-._~")
    return f"{encoded}#{anchor}" if anchor is not None else encoded
