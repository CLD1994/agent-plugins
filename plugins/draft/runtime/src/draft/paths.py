from __future__ import annotations

import posixpath
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from draft.errors import DraftError


@dataclass(frozen=True)
class ResolvedPath:
    root: Path
    relative: Path
    absolute: Path


def resolve_draft_root(value: Path, *, allow_missing: bool = False) -> Path:
    expanded = value.expanduser()
    if not allow_missing and not expanded.exists():
        raise DraftError("root.missing", expanded)
    if expanded.exists() and (expanded.is_symlink() or not expanded.is_dir()):
        raise DraftError("root.invalid", expanded)
    return expanded.resolve(strict=False)


def resolve_page_operand(root: Path, value: str) -> ResolvedPath:
    canonical_root = root.expanduser().resolve(strict=False)
    posix = PurePosixPath(value)
    if posix.is_absolute():
        raise DraftError("path.escape", Path(value))
    normalized = Path(*posix.parts)
    candidate = canonical_root / normalized
    cursor = canonical_root
    for part in normalized.parts:
        cursor /= part
        if cursor.is_symlink():
            raise DraftError("path.symlink_escape", cursor)
    canonical = candidate.resolve(strict=False)
    try:
        relative = canonical.relative_to(canonical_root)
    except ValueError as exc:
        raise DraftError("path.escape", candidate) from exc
    if candidate.suffix != ".md":
        raise DraftError("path.not_markdown", candidate)
    return ResolvedPath(root=canonical_root, relative=relative, absolute=canonical_root / relative)


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
