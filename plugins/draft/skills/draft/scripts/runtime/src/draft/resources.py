"""正式关系目标与正文资源的分辨、分类与查询。

正文资源链接允许 Draft 外路径、Linux/Windows 相对路径及两种绝对路径；
正式关系和受管理页面参数仍只能指向同一 Draft 内页面。资源身份采用词法
解析后的位置，不通过 realpath 合并符号链接；`kind` 与 `scope` 按词法命名
空间判定，与目标是否当前存在无关。
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote

from draft.diagnostic_codes import CheckCode
from draft.errors import DraftError
from draft.models import MarkdownLink, PageDocument

_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")

ROOT_PAGE = Path("index.md")
CONCLUSIONS_PAGE = Path("conclusions.md")
PAGES_DIR = Path("pages")
_WINDOWS_DRIVE_ABS = re.compile(r"^[A-Za-z]:[\\/]")
_WINDOWS_DRIVE_REL = re.compile(r"^[A-Za-z]:")
_INVALID_PERCENT = re.compile(r"%(?![0-9A-Fa-f]{2})")
_SEPARATOR = re.compile(r"[\\/]+")


class ResourceKind(StrEnum):
    PAGE = "page"
    LOCAL = "local"
    EXTERNAL = "external"


class ResourceScope(StrEnum):
    DRAFT = "draft"
    OUTSIDE = "outside"
    UNKNOWN = "unknown"


class TargetState(StrEnum):
    PRESENT = "present"
    MISSING = "missing"
    UNAVAILABLE = "unavailable"
    UNCHECKED = "unchecked"
    INVALID = "invalid"


class HostOS(StrEnum):
    LINUX = "linux"
    WINDOWS = "windows"


class ProbeState(StrEnum):
    """目标探针的可观察状态：区分缺失、普通文件、目录、符号链接与其他类型。"""

    MISSING = "missing"
    FILE = "file"
    DIRECTORY = "directory"
    SYMLINK = "symlink"
    OTHER = "other"


TargetProbe = Callable[[Path], ProbeState]


@dataclass(frozen=True, order=True)
class ResourceIdentity:
    kind: ResourceKind
    value: str


@dataclass(frozen=True)
class ResolvedResource:
    identity: ResourceIdentity
    target: str
    kind: ResourceKind
    scope: ResourceScope
    target_state: TargetState
    local_absolute: str | None
    was_relative: bool
    separator: str | None
    anchor: str | None
    # 纯锚点目标（#节）指向来源页面自身：移动时保持原文，不按相对路径重写。
    self_anchor: bool = False


@dataclass(frozen=True)
class ResourceReference:
    link: MarkdownLink
    resource: ResolvedResource


def _probe_filesystem(path: Path) -> ProbeState:
    if path.is_symlink():
        return ProbeState.SYMLINK
    if path.is_file():
        return ProbeState.FILE
    if path.is_dir():
        return ProbeState.DIRECTORY
    if path.exists():
        return ProbeState.OTHER
    return ProbeState.MISSING


def _split_mixed(destination: str) -> tuple[str, ...]:
    """按 `/` 与 `\\` 共同拆分，丢弃空段与当前段。"""
    return tuple(part for part in _SEPARATOR.split(destination) if part not in ("", "."))


def _normalize_mixed(destination: str) -> PurePosixPath | None:
    """把含 `/` 或 `\\` 的相对路径规范化为纯 POSIX 路径；越出根时返回 None。"""
    parts: list[str] = []
    for part in _split_mixed(destination):
        if part == "..":
            if not parts:
                return None
            parts.pop()
        else:
            parts.append(part)
    return PurePosixPath(*parts) if parts else PurePosixPath(".")


def _primary_separator(destination: str) -> str | None:
    if "\\" in destination:
        return "\\"
    if "/" in destination:
        return "/"
    return None


def _percent_valid(path: str) -> bool:
    return _INVALID_PERCENT.search(path) is None


def _state_of(probe: TargetProbe, absolute: Path) -> TargetState:
    state = probe(absolute)
    if state is ProbeState.MISSING:
        return TargetState.MISSING
    if state is ProbeState.FILE:
        return TargetState.PRESENT
    return TargetState.INVALID


def _local_state_of(probe: TargetProbe, absolute: Path) -> TargetState:
    state = probe(absolute)
    if state is ProbeState.MISSING:
        return TargetState.MISSING
    return TargetState.PRESENT


def _page_namespace(root_relative: PurePosixPath) -> bool:
    parts = root_relative.parts
    if root_relative in (PurePosixPath(ROOT_PAGE.name), PurePosixPath(CONCLUSIONS_PAGE.name)):
        return True
    return parts[0] == PAGES_DIR.name and root_relative.suffix == ".md"


def resolve_managed_destination(source: Path, destination: str) -> tuple[Path, str | None]:
    """把正式关系/受管理页面目标解析为根相对路径。

    只接受保持在 Draft 根目录内的相对 POSIX Markdown 目标。绝对路径、URI、
    Windows 盘符、UNC 路径和越过根目录的路径都产生现有稳定正式链接错误。
    """
    if _SCHEME.match(destination):
        raise DraftError("link.external", source)
    if "\\" in destination:
        raise DraftError(CheckCode.LINK_ESCAPE.value, source)
    path_part, separator, anchor = destination.partition("#")
    decoded = unquote(path_part)
    if PurePosixPath(decoded).is_absolute():
        raise DraftError(CheckCode.LINK_ESCAPE.value, source)
    if decoded == "":
        target = source
    else:
        joined = source.parent / PurePosixPath(decoded)
        normalized = _normalize_mixed(str(joined))
        if normalized is None or normalized == PurePosixPath("."):
            raise DraftError(CheckCode.LINK_ESCAPE.value, source)
        target = Path(*normalized.parts)
    return target, (anchor if separator else None)


def _resolve_relative(
    root: Path, source: Path, path_part: str, *, host: HostOS, probe: TargetProbe
) -> ResolvedResource:
    """相对目标（POSIX 或 Windows 风格）的词法解析与分类。"""
    base = (root / source).parent if (root / source).parent.is_absolute() else root / source.parent
    if not _percent_valid(path_part):
        return ResolvedResource(
            identity=ResourceIdentity(ResourceKind.LOCAL, path_part),
            target=path_part,
            kind=ResourceKind.LOCAL,
            scope=ResourceScope.UNKNOWN,
            target_state=TargetState.INVALID,
            local_absolute=None,
            was_relative=True,
            separator=_primary_separator(path_part),
            anchor=None,
        )
    # 词法绝对路径：从页面所在目录逐段行走规范化，不 resolve/realpath。
    lexical = base
    for part in _split_mixed(path_part):
        lexical = lexical.parent if part == ".." else lexical / part
    try:
        root_relative = lexical.relative_to(root)
    except ValueError:
        # 词法位置在 Draft 根目录之外。
        outside_relative = posixpath.relpath(str(lexical), str(root))
        return ResolvedResource(
            identity=ResourceIdentity(ResourceKind.LOCAL, outside_relative),
            target=outside_relative,
            kind=ResourceKind.LOCAL,
            scope=ResourceScope.OUTSIDE,
            target_state=_local_state_of(probe, lexical),
            local_absolute=str(lexical),
            was_relative=True,
            separator=_primary_separator(path_part),
            anchor=None,
        )
    relative = PurePosixPath(*root_relative.parts)
    if _page_namespace(relative):
        return ResolvedResource(
            identity=ResourceIdentity(ResourceKind.PAGE, relative.as_posix()),
            target=relative.as_posix(),
            kind=ResourceKind.PAGE,
            scope=ResourceScope.DRAFT,
            target_state=_state_of(probe, lexical),
            local_absolute=str(lexical),
            was_relative=True,
            separator=_primary_separator(path_part),
            anchor=None,
        )
    return ResolvedResource(
        identity=ResourceIdentity(ResourceKind.LOCAL, relative.as_posix()),
        target=relative.as_posix(),
        kind=ResourceKind.LOCAL,
        scope=ResourceScope.DRAFT,
        target_state=_local_state_of(probe, lexical),
        local_absolute=str(lexical),
        was_relative=True,
        separator=_primary_separator(path_part),
        anchor=None,
    )


def resolve_body_resource(
    root: Path,
    source: Path,
    destination: str,
    *,
    host_os: HostOS | None = None,
    target_probe: TargetProbe | None = None,
) -> ResolvedResource:
    """把一条正文链接目标解析为稳定资源事实。

    ``target_probe`` 注入目标存在性判定，单元测试可以不访问真实文件系统。
    """
    host = host_os if host_os is not None else HostOS.LINUX
    probe = target_probe if target_probe is not None else _probe_filesystem
    path_part, separator, anchor = destination.partition("#")
    if not path_part and separator == "#":
        # 纯锚点 `#节`：指向来源页面自身的节，不进入相对资源路径。
        root_relative = source.as_posix() if not source.is_absolute() else None
        if root_relative is None:
            try:
                root_relative = source.relative_to(root).as_posix()
            except ValueError:
                root_relative = source.name
        return ResolvedResource(
            identity=ResourceIdentity(ResourceKind.PAGE, root_relative),
            target=root_relative,
            kind=ResourceKind.PAGE,
            scope=ResourceScope.DRAFT,
            target_state=TargetState.PRESENT,
            local_absolute=str(root / source) if not source.is_absolute() else str(source),
            was_relative=True,
            separator="#",
            anchor=anchor,
            self_anchor=True,
        )
    if _WINDOWS_DRIVE_ABS.match(path_part) or _WINDOWS_DRIVE_REL.match(path_part):
        if host is HostOS.WINDOWS and _WINDOWS_DRIVE_ABS.match(path_part):
            lexical = Path(path_part)
            local_state = _local_state_of(probe, lexical)
            scope = ResourceScope.UNKNOWN
            return ResolvedResource(
                identity=ResourceIdentity(ResourceKind.LOCAL, path_part),
                target=path_part,
                kind=ResourceKind.LOCAL,
                scope=scope,
                target_state=local_state,
                local_absolute=str(lexical),
                was_relative=False,
                separator=None,
                anchor=anchor if separator else None,
            )
        return ResolvedResource(
            identity=ResourceIdentity(ResourceKind.LOCAL, path_part),
            target=path_part,
            kind=ResourceKind.LOCAL,
            scope=ResourceScope.UNKNOWN,
            target_state=TargetState.UNAVAILABLE,
            local_absolute=None,
            was_relative=False,
            separator=None,
            anchor=anchor if separator else None,
        )
    if path_part.startswith("\\\\"):
        return ResolvedResource(
            identity=ResourceIdentity(ResourceKind.LOCAL, path_part),
            target=path_part,
            kind=ResourceKind.LOCAL,
            scope=ResourceScope.UNKNOWN,
            target_state=TargetState.UNAVAILABLE,
            local_absolute=None,
            was_relative=False,
            separator=None,
            anchor=anchor if separator else None,
        )
    if _SCHEME.match(path_part):
        return ResolvedResource(
            identity=ResourceIdentity(ResourceKind.EXTERNAL, path_part),
            target=path_part,
            kind=ResourceKind.EXTERNAL,
            scope=ResourceScope.UNKNOWN,
            target_state=TargetState.UNCHECKED,
            local_absolute=None,
            was_relative=False,
            separator=None,
            anchor=anchor if separator else None,
        )
    if PurePosixPath(path_part).is_absolute():
        lexical = Path(path_part)
        try:
            root_relative = lexical.relative_to(root)
        except ValueError:
            return ResolvedResource(
                identity=ResourceIdentity(ResourceKind.LOCAL, path_part),
                target=path_part,
                kind=ResourceKind.LOCAL,
                scope=ResourceScope.OUTSIDE,
                target_state=_local_state_of(probe, lexical),
                local_absolute=str(lexical),
                was_relative=False,
                separator=None,
                anchor=anchor if separator else None,
            )
        relative = PurePosixPath(*root_relative.parts)
        if _page_namespace(relative):
            return ResolvedResource(
                identity=ResourceIdentity(ResourceKind.PAGE, relative.as_posix()),
                target=relative.as_posix(),
                kind=ResourceKind.PAGE,
                scope=ResourceScope.DRAFT,
                target_state=_state_of(probe, lexical),
                local_absolute=str(lexical),
                was_relative=False,
                separator=None,
                anchor=anchor if separator else None,
            )
        return ResolvedResource(
            identity=ResourceIdentity(ResourceKind.LOCAL, relative.as_posix()),
            target=relative.as_posix(),
            kind=ResourceKind.LOCAL,
            scope=ResourceScope.DRAFT,
            target_state=_local_state_of(probe, lexical),
            local_absolute=str(lexical),
            was_relative=False,
            separator=None,
            anchor=anchor if separator else None,
        )
    resolved = _resolve_relative(root, source, path_part, host=host, probe=probe)
    return ResolvedResource(
        identity=resolved.identity,
        target=resolved.target,
        kind=resolved.kind,
        scope=resolved.scope,
        target_state=resolved.target_state,
        local_absolute=resolved.local_absolute,
        was_relative=resolved.was_relative,
        separator=resolved.separator,
        anchor=anchor if separator else None,
    )


def resolve_page_resources(
    root: Path,
    page: PageDocument,
    *,
    host_os: HostOS | None = None,
    target_probe: TargetProbe | None = None,
) -> tuple[ResourceReference, ...]:
    """解析一页的全部正文资源，排除正式关系链接位置。

    同一身份的多个声明位置各自保留：去重是查询层（``page_links``）的行为，
    图存储和结构修改需要每条声明的独立引用。
    """
    relation_spans = {relation.link.whole_span for relation in page.relations}
    references: list[ResourceReference] = []
    for link in page.links:
        if link.whole_span in relation_spans:
            continue
        resolved = resolve_body_resource(
            root,
            page.path,
            link.destination,
            host_os=host_os,
            target_probe=target_probe,
        )
        references.append(ResourceReference(link=link, resource=resolved))
    return tuple(references)


def resource_identity_from_operand(
    root: Path,
    value: str,
    *,
    host_os: HostOS | None = None,
) -> ResourceIdentity:
    """把命令行反向链接目标解析为宽松正文资源身份，以 Draft 根目录为基准。"""
    resolved = resolve_body_resource(
        root, ROOT_PAGE, value, host_os=host_os, target_probe=_probe_filesystem
    )
    return resolved.identity


def rebase_body_destination(
    resource: ResolvedResource,
    *,
    new_source: Path,
    moved_targets: Mapping[ResourceIdentity, ResourceIdentity],
) -> str:
    """把正文链接目标重算到新的来源页面位置。

    只有当前系统可解释的本地相对目标才重写；本地绝对路径、Windows 绝对路径、
    UNC、盘符相对路径和外部 URI 保持原文不变。指向同时移动的页面时跟随最终
    目标映射。相对路径保留原主要分隔符风格与锚点。纯锚点（#节）指向来源页面
    自身，移动后仍指向同一页面的同一节，保持原文不变。
    """
    if resource.self_anchor:
        return f"#{resource.anchor}" if resource.anchor is not None else "#"
    if not resource.was_relative or resource.local_absolute is None:
        anchor = resource.anchor
        return f"{resource.target}#{anchor}" if anchor is not None else resource.target
    identity = resource.identity
    final_identity = moved_targets.get(identity, identity)
    relative = posixpath.relpath(final_identity.value, new_source.parent.as_posix())
    encoded = quote(relative, safe="/-._~")
    if resource.separator == "\\":
        encoded = encoded.replace("/", "\\")
    anchor = resource.anchor
    return f"{encoded}#{anchor}" if anchor is not None else encoded
