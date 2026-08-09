from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import cast
from urllib.parse import unquote

from draft.diagnostic_codes import CheckCode, LinkEscapeKind
from draft.errors import DraftError
from draft.markdown import parse_page
from draft.models import (
    CheckIssue,
    IssueSeverity,
    MarkdownLink,
    PageDocument,
    ValidationMode,
)
from draft.paths import resolve_draft_root
from draft.resources import (
    ResourceIdentity,
    ResourceKind,
    ResourceReference,
    TargetState,
    resolve_page_resources,
)
from draft.vocabulary import ConclusionsState, RelationCode

LoadMode = ValidationMode

ROOT_PAGE = Path("index.md")
CONCLUSIONS_PAGE = Path("conclusions.md")
PAGES_DIR = Path("pages")
INTERNAL_DIR = ".draft"
MARKER_PATH = Path(INTERNAL_DIR) / "draft.json"
PROVISIONAL_MARKER_PATH = Path(INTERNAL_DIR) / "draft.provisional.json"
FORMAT_VERSION = 1

_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


@dataclass(frozen=True)
class DraftGraph:
    root: Path
    pages: Mapping[Path, PageDocument]
    parents: Mapping[Path, Path]
    children: Mapping[Path, tuple[Path, ...]]
    inbound_links: Mapping[Path, tuple[tuple[Path, MarkdownLink], ...]]
    relations: Mapping[RelationCode, tuple[tuple[Path, Path, int], ...]]
    body_resources: Mapping[Path, tuple[ResourceReference, ...]] = field(
        default_factory=dict[Path, tuple[ResourceReference, ...]]
    )
    inbound_resources: Mapping[ResourceIdentity, tuple[Path, ...]] = field(
        default_factory=dict[ResourceIdentity, tuple[Path, ...]]
    )


@dataclass(frozen=True)
class LoadResult:
    graph: DraftGraph
    issues: list[CheckIssue]


def error_issue(
    code: CheckCode,
    path: Path | None,
    line: int | None = None,
    *details: str,
) -> CheckIssue:
    return CheckIssue(
        code=code.value,
        severity=IssueSeverity.ERROR,
        path=path,
        line=line,
        details=details,
    )


@dataclass(frozen=True)
class MarkerState:
    version: int
    provisional_manifest: tuple[tuple[str, str], ...] | None
    conclusions_state: ConclusionsState = ConclusionsState.NOT_GENERATED


def _parse_manifest_entries(value: object, marker: Path) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, list):
        raise DraftError(CheckCode.FORMAT_INVALID.value, marker)
    entries: list[tuple[str, str]] = []
    for item in cast("list[object]", value):
        if not isinstance(item, list):
            raise DraftError(CheckCode.FORMAT_INVALID.value, marker)
        pair = cast("list[object]", item)
        if len(pair) != 2:
            raise DraftError(CheckCode.FORMAT_INVALID.value, marker)
        path_text, digest = pair
        if not isinstance(path_text, str) or not isinstance(digest, str):
            raise DraftError(CheckCode.FORMAT_INVALID.value, marker)
        posix = PurePosixPath(path_text)
        if posix.is_absolute() or ".." in posix.parts:
            raise DraftError(CheckCode.FORMAT_INVALID.value, marker)
        entries.append((path_text, digest))
    return tuple(entries)


def _parse_conclusions_state(value: object, marker: Path) -> ConclusionsState:
    if not isinstance(value, str):
        raise DraftError(CheckCode.FORMAT_INVALID.value, marker)
    try:
        return ConclusionsState(value)
    except ValueError as exc:
        raise DraftError(CheckCode.FORMAT_INVALID.value, marker) from exc


def parse_marker_value(decoded: object, marker: Path) -> MarkerState:
    if not isinstance(decoded, dict):
        raise DraftError(CheckCode.FORMAT_INVALID.value, marker)
    data = cast("dict[str, object]", decoded)
    version = data.get("format_version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise DraftError(CheckCode.FORMAT_INVALID.value, marker)
    if version != FORMAT_VERSION:
        raise DraftError(CheckCode.FORMAT_UNSUPPORTED.value, marker)
    keys = set(data)
    # 旧 {"format_version":1} 标记有意以 format.invalid 拒绝；不提供迁移或双读。
    if keys == {"format_version"}:
        raise DraftError(CheckCode.FORMAT_INVALID.value, marker)
    if keys == {"format_version", "conclusions_state"}:
        return MarkerState(
            version=version,
            provisional_manifest=None,
            conclusions_state=_parse_conclusions_state(data.get("conclusions_state"), marker),
        )
    if keys == {"format_version", "conclusions_state", "adopt_state", "candidate_manifest"}:
        if data.get("adopt_state") != "provisional":
            raise DraftError(CheckCode.FORMAT_INVALID.value, marker)
        state = _parse_conclusions_state(data.get("conclusions_state"), marker)
        if state is not ConclusionsState.NOT_GENERATED:
            raise DraftError(CheckCode.FORMAT_INVALID.value, marker)
        return MarkerState(
            version=version,
            provisional_manifest=_parse_manifest_entries(data.get("candidate_manifest"), marker),
            conclusions_state=state,
        )
    raise DraftError(CheckCode.FORMAT_INVALID.value, marker)


def _read_marker_file(path: Path) -> MarkerState:
    if path.is_symlink():
        raise DraftError(CheckCode.PATH_SYMLINK_ESCAPE.value, path)
    if not path.exists():
        raise DraftError(CheckCode.FORMAT_MISSING.value, path)
    if not path.is_file():
        # 已存在但不是普通文件（目录等）：异常内部状态，不是"缺失"。
        raise DraftError(CheckCode.FORMAT_INVALID.value, path)
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DraftError(CheckCode.FORMAT_INVALID.value, path) from exc
    return parse_marker_value(decoded, path)


def read_marker(root: Path, *, allow_provisional_adopt: bool = False) -> MarkerState:
    """Read and validate .draft/draft.json, raising stable codes.

    A provisional adopt marker is crash evidence: only callers that explicitly
    opt in via allow_provisional_adopt may treat it as readable state.
    """
    marker = root / MARKER_PATH
    state = _read_marker_file(marker)
    if state.provisional_manifest is not None and not allow_provisional_adopt:
        raise DraftError("adopt.recovery_required", marker)
    return state


def read_provisional_marker(root: Path) -> MarkerState:
    """Read and validate .draft/draft.provisional.json."""
    return _read_marker_file(root / PROVISIONAL_MARKER_PATH)


def provisional_marker_path(root: Path) -> Path | None:
    """Return the provisional marker path iff it holds a valid provisional adopt state.

    只有路径确实不存在才返回 None；存在但无效（符号链接、损坏、非临时格式）一律
    传播稳定错误——不得把异常内部状态折叠为不存在而让 registered 检查放行。
    """
    marker = root / PROVISIONAL_MARKER_PATH
    if marker.is_symlink():
        raise DraftError(CheckCode.PATH_SYMLINK_ESCAPE.value, marker)
    if not marker.exists():
        return None
    if not marker.is_file():
        # 已存在但不是普通文件（目录/FIFO 等）：异常内部状态，必须报错而非折叠为不存在。
        raise DraftError(CheckCode.FORMAT_INVALID.value, marker)
    state = read_provisional_marker(root)
    if state.provisional_manifest is None:
        raise DraftError(CheckCode.FORMAT_INVALID.value, marker)
    return marker


def _normalize_relative(decoded: str) -> PurePosixPath | None:
    parts: list[str] = []
    for part in PurePosixPath(decoded).parts:
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                return None
            parts.pop()
        else:
            parts.append(part)
    return PurePosixPath(*parts) if parts else PurePosixPath(".")


def is_external_destination(destination: str) -> bool:
    return _SCHEME.match(destination) is not None


def resolve_local_destination(
    source: Path, destination: str, root: Path
) -> tuple[Path, str | None]:
    """Resolve a Markdown destination to a root-relative path plus optional anchor.

    Raises DraftError("link.external") for external schemes and
    DraftError("link.escape") for absolute or root-escaping targets.
    """
    del root  # destinations become root-relative once normalized; containment is lexical
    if _SCHEME.match(destination):
        raise DraftError("link.external", source)
    path_part, separator, anchor = destination.partition("#")
    decoded = unquote(path_part)
    if PurePosixPath(decoded).is_absolute():
        raise DraftError(CheckCode.LINK_ESCAPE.value, source)
    if decoded == "":
        target = source
    else:
        joined = source.parent / PurePosixPath(decoded)
        normalized = _normalize_relative(str(joined))
        if normalized is None or normalized == PurePosixPath("."):
            raise DraftError(CheckCode.LINK_ESCAPE.value, source)
        target = Path(str(normalized))
    return target, (anchor if separator else None)


def _discover(root: Path) -> tuple[list[Path], list[CheckIssue]]:
    managed: list[Path] = []
    issues: list[CheckIssue] = []
    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        if entry.name == INTERNAL_DIR:
            continue
        if entry.is_symlink():
            issues.append(
                error_issue(
                    CheckCode.LINK_ESCAPE,
                    Path(entry.name),
                    None,
                    LinkEscapeKind.MANAGED_PATH.value,
                    entry.name,
                )
            )
            continue
        if entry.is_dir():
            for descendant in sorted(entry.rglob("*")):
                relative = descendant.relative_to(root)
                if descendant.is_symlink():
                    issues.append(
                        error_issue(
                            CheckCode.LINK_ESCAPE,
                            relative,
                            None,
                            LinkEscapeKind.MANAGED_PATH.value,
                            relative.as_posix(),
                        )
                    )
                    continue
                if not descendant.is_file() or descendant.suffix != ".md":
                    continue
                if relative.parts[0] == PAGES_DIR.name:
                    managed.append(relative)
                else:
                    issues.append(error_issue(CheckCode.PAGE_UNMANAGED_PATH, relative))
            continue
        if entry.suffix != ".md":
            continue
        if entry.name in (ROOT_PAGE.name, CONCLUSIONS_PAGE.name):
            managed.append(Path(entry.name))
        else:
            issues.append(error_issue(CheckCode.PAGE_UNMANAGED_PATH, Path(entry.name)))
    return managed, issues


def _load(root: Path) -> LoadResult:
    issues: list[CheckIssue] = []
    managed_paths, discover_issues = _discover(root)
    issues.extend(discover_issues)
    managed_set = set(managed_paths)

    pages: dict[Path, PageDocument] = {}
    for relative in managed_paths:
        try:
            text = (root / relative).read_text(encoding="utf-8")
            pages[relative] = parse_page(relative, text)
        except DraftError as error:
            issues.append(
                CheckIssue(
                    code=error.code,
                    severity=IssueSeverity.ERROR,
                    path=relative,
                    line=error.line,
                    details=error.details,
                )
            )

    inbound: dict[Path, list[tuple[Path, MarkdownLink]]] = {}
    relation_edges: dict[RelationCode, list[tuple[Path, Path, int]]] = {
        label: [] for label in RelationCode
    }
    link_issues: list[CheckIssue] = []
    body_resources: dict[Path, tuple[ResourceReference, ...]] = {}
    inbound_resources: dict[ResourceIdentity, list[Path]] = {}

    for source in sorted(pages):
        page = pages[source]
        references = resolve_page_resources(root, page)
        body_resources[source] = references
        for reference in references:
            resource = reference.resource
            identity = resource.identity
            inbound_resources.setdefault(identity, []).append(source)
            target_state = resource.target_state
            if target_state is TargetState.MISSING:
                link_issues.append(
                    error_issue(
                        CheckCode.LINK_MISSING,
                        source,
                        reference.link.line,
                        reference.link.destination,
                        resource.target,
                    )
                )
            elif target_state is TargetState.INVALID:
                link_issues.append(
                    error_issue(
                        CheckCode.LINK_INVALID,
                        source,
                        reference.link.line,
                        reference.link.destination,
                        resource.target,
                    )
                )
            elif target_state is TargetState.UNAVAILABLE:
                link_issues.append(
                    CheckIssue(
                        code=CheckCode.LINK_UNAVAILABLE.value,
                        severity=IssueSeverity.WARNING,
                        path=source,
                        line=reference.link.line,
                        details=(reference.link.destination, resource.target),
                    )
                )
            if identity.kind is ResourceKind.PAGE and target_state is TargetState.PRESENT:
                target = Path(identity.value)
                if target in managed_set:
                    inbound.setdefault(target, []).append((source, reference.link))
        for relation in page.relations:
            try:
                target, _anchor = resolve_local_destination(source, relation.link.destination, root)
            except DraftError as error:
                if error.code == "link.external":
                    continue
                link_issues.append(
                    error_issue(
                        CheckCode.LINK_ESCAPE,
                        source,
                        relation.link.line,
                        LinkEscapeKind.DESTINATION.value,
                        relation.link.destination,
                    )
                )
                continue
            if target not in managed_set:
                link_issues.append(
                    error_issue(
                        CheckCode.LINK_MISSING,
                        source,
                        relation.link.line,
                        relation.link.destination,
                        target.as_posix(),
                    )
                )
                continue
            relation_edges[relation.code].append((source, target, relation.link.line))
            inbound.setdefault(target, []).append((source, relation.link))
    issues.extend(link_issues)

    parents: dict[Path, Path] = {}
    for source, page in pages.items():
        declared = [relation for relation in page.relations if relation.code is RelationCode.PARENT]
        if len(declared) != 1:
            continue
        matches = [
            target
            for edge_source, target, _line in relation_edges[RelationCode.PARENT]
            if edge_source == source
        ]
        if len(matches) == 1:
            parents[source] = matches[0]

    children: dict[Path, list[Path]] = {}
    for child, parent in parents.items():
        children.setdefault(parent, []).append(child)

    graph = DraftGraph(
        root=root,
        pages={path: pages[path] for path in sorted(pages)},
        parents={path: parents[path] for path in sorted(parents)},
        children={parent: tuple(sorted(entries)) for parent, entries in sorted(children.items())},
        inbound_links={
            target: tuple(sorted(entries, key=lambda entry: (entry[0], entry[1].whole_span.start)))
            for target, entries in sorted(inbound.items())
        },
        relations={label: tuple(sorted(edges)) for label, edges in relation_edges.items()},
        body_resources={path: body_resources[path] for path in sorted(body_resources)},
        inbound_resources={
            identity: tuple(sorted(paths)) for identity, paths in sorted(inbound_resources.items())
        },
    )
    return LoadResult(graph=graph, issues=issues)


def load_graph(
    root: Path,
    mode: LoadMode = "registered",
    *,
    allow_provisional_adopt: bool = False,
) -> DraftGraph:
    resolved = resolve_draft_root(root)
    result = load_graph_tolerant(resolved, mode, allow_provisional_adopt=allow_provisional_adopt)
    if result.issues:
        first = result.issues[0]
        raise DraftError(
            first.code,
            *(() if first.path is None else (first.path,)),
            details=first.details,
            line=first.line,
        )
    return result.graph


def _candidate_internal_issues(
    resolved: Path, *, allow_provisional_adopt: bool
) -> list[CheckIssue]:
    """Candidate mode rejects every pre-existing internal entry.

    The two marker files are the only allowed internal entries: a final marker
    (draft.json) is already-registered evidence, a provisional marker
    (draft.provisional.json) is adopt crash evidence readable only by the
    private adopt recheck that explicitly opts in.
    """
    issues: list[CheckIssue] = []
    internal = resolved / INTERNAL_DIR
    if internal.is_symlink() or (internal.exists() and not internal.is_dir()):
        return [error_issue(CheckCode.CANDIDATE_INTERNAL_STATE, Path(INTERNAL_DIR))]
    if not internal.is_dir():
        return issues
    entries = sorted(internal.iterdir())
    if not entries:
        issues.append(error_issue(CheckCode.CANDIDATE_INTERNAL_STATE, Path(INTERNAL_DIR)))
    final = resolved / MARKER_PATH
    if final.is_file() and not final.is_symlink():
        try:
            final_state = read_marker(resolved, allow_provisional_adopt=True)
        except DraftError as error:
            issues.append(
                CheckIssue(
                    code=error.code,
                    severity=IssueSeverity.ERROR,
                    path=MARKER_PATH,
                    line=None,
                )
            )
        else:
            if final_state.provisional_manifest is None:
                # 已登记是终态事实：其余内部条目都是合法登记状态，不再逐条报告。
                return [error_issue(CheckCode.CANDIDATE_ALREADY_REGISTERED, MARKER_PATH)]
            if not allow_provisional_adopt:
                raise DraftError("adopt.recovery_required", final)
    provisional = resolved / PROVISIONAL_MARKER_PATH
    if provisional.is_file() and not provisional.is_symlink():
        try:
            provisional_state = read_provisional_marker(resolved)
        except DraftError as error:
            issues.append(
                CheckIssue(
                    code=error.code,
                    severity=IssueSeverity.ERROR,
                    path=PROVISIONAL_MARKER_PATH,
                    line=None,
                )
            )
        else:
            if provisional_state.provisional_manifest is None:
                issues.append(error_issue(CheckCode.FORMAT_INVALID, PROVISIONAL_MARKER_PATH))
            elif not allow_provisional_adopt:
                raise DraftError("adopt.recovery_required", provisional)
    allowed = {MARKER_PATH, PROVISIONAL_MARKER_PATH}
    for entry in entries:
        relative = entry.relative_to(resolved)
        if relative not in allowed or entry.is_symlink() or not entry.is_file():
            issues.append(error_issue(CheckCode.CANDIDATE_INTERNAL_STATE, relative))
    return issues


def load_graph_tolerant(
    root: Path,
    mode: LoadMode = "registered",
    *,
    allow_provisional_adopt: bool = False,
) -> LoadResult:
    resolved = root if root.is_absolute() else resolve_draft_root(root)
    marker_issues: list[CheckIssue] = []
    if mode == "registered":
        # 独立临时标记文件存在即半完成接管现场：registered 一律视为恢复需求。
        if provisional_marker_path(resolved) is not None:
            raise DraftError("adopt.recovery_required", resolved / PROVISIONAL_MARKER_PATH)
        try:
            read_marker(resolved)
        except DraftError as error:
            if error.code == "adopt.recovery_required":
                raise
            marker_issues.append(
                CheckIssue(
                    code=error.code,
                    severity=IssueSeverity.ERROR,
                    path=MARKER_PATH,
                    line=None,
                )
            )
    else:
        marker_issues.extend(
            _candidate_internal_issues(resolved, allow_provisional_adopt=allow_provisional_adopt)
        )
    result = _load(resolved)
    return LoadResult(graph=result.graph, issues=[*marker_issues, *result.issues])
