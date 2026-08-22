from __future__ import annotations

import hashlib
import json
import os
import re
import stat
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
    RootState,
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


def _sha256_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


ROOT_PAGE = Path("index.md")
CONCLUSIONS_PAGE = Path("conclusions.md")
PAGES_DIR = Path("pages")
INTERNAL_DIR = ".draft"
MARKER_PATH = Path(INTERNAL_DIR) / "draft.json"
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
    def page_digest(self, path: Path) -> str:
        """页面内容摘要：加载文本保留原始字节（含 \\r\\n），编码后即磁盘字节。"""
        return _sha256_hex(self.pages[path].text.encode("utf-8"))


@dataclass(frozen=True)
class LoadResult:
    graph: DraftGraph
    issues: list[CheckIssue]
    # 正文链接目标事实（missing/invalid/unavailable）：是查询结果数据，不阻止命令。
    resource_facts: list[CheckIssue]


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
    root_state: RootState
    conclusions_state: ConclusionsState = ConclusionsState.NOT_GENERATED


@dataclass(frozen=True)
class MarkerSnapshot:
    """一次读取的不可变 marker 快照（round-15 H1）：state、raw_bytes、sha256 同源。

    同一操作计划的 root_state/conclusions_state、marker 原始 bytes（若该操作要修改
    marker）、before digest 与观察清单中的 marker 摘要必须来自同一次成功读取；不得
    由构造者分别传入互相矛盾的对象，也不得在 snapshot 后再次读取 live 文件重建事实。
    """

    state: MarkerState
    raw_bytes: bytes

    @property
    def sha256(self) -> str:
        return _sha256_hex(self.raw_bytes)


def _parse_conclusions_state(value: object, marker: Path) -> ConclusionsState:
    if not isinstance(value, str):
        raise DraftError(CheckCode.FORMAT_INVALID.value, marker)
    try:
        return ConclusionsState(value)
    except ValueError as exc:
        raise DraftError(CheckCode.FORMAT_INVALID.value, marker) from exc


def _expect_marker_string(value: object, marker: Path) -> str:
    if not isinstance(value, str):
        raise DraftError(CheckCode.FORMAT_INVALID.value, marker)
    return value


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
    if keys != {"format_version", "root_state", "conclusions_state"}:
        raise DraftError(CheckCode.FORMAT_INVALID.value, marker)
    try:
        root_state = RootState(_expect_marker_string(data.get("root_state"), marker))
    except ValueError as exc:
        raise DraftError(CheckCode.FORMAT_INVALID.value, marker) from exc
    return MarkerState(
        version=version,
        root_state=root_state,
        conclusions_state=_parse_conclusions_state(data.get("conclusions_state"), marker),
    )


def classify_marker_parent(internal: Path, marker: Path) -> bool:
    """稳定分类 .draft 中间组件，是 CLI 入口与 snapshot 读取器共用的单一权威（round-16 M1）。

    返回 ``False`` 当且仅当 ``.draft`` 缺失（ENOENT，缺失合法：调用方按无 marker
    处理，最终由 leaf 分类给出 ``format.missing``）；返回 ``True`` 当 ``.draft`` 是
    普通目录。symlink 是路径错误（在 ``.draft`` 自身位置报告 ``path.symlink_escape``，
    与 reject_symlink_components 同语义）；``.draft`` 已存在但无法 lstat（EACCES/EIO）
    或存在但非目录映射 ``format.invalid @ marker``。后三种均立即抛 DraftError，
    不得折叠为缺失或 None。
    """
    try:
        mode = os.lstat(internal).st_mode
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise DraftError(CheckCode.FORMAT_INVALID.value, marker) from exc
    if stat.S_ISLNK(mode):
        raise DraftError(CheckCode.PATH_SYMLINK_ESCAPE.value, internal) from None
    if not stat.S_ISDIR(mode):
        raise DraftError(CheckCode.FORMAT_INVALID.value, marker) from None
    return True


def _classify_marker_leaf(marker: Path) -> None:
    """稳定分类 marker 最终节点（round-14 M2 分类语义，round-15 H1 独立成叶分类）。

    一次受保护 ``os.lstat`` 按 mode 分类，不再串联 ``Path.is_symlink/exists/is_file``
    （CPython 3.12 的 ``is_symlink`` 不吞 OSError，EACCES/EIO 会原样泄漏）：ENOENT ->
    format.missing；symlink -> path.symlink_escape；目录/其他非普通文件 ->
    format.invalid；EACCES/EIO 等其他身份读取失败 -> format.invalid。随后由
    snapshot 读取器从同一普通文件读取 bytes。
    """
    try:
        mode = os.lstat(marker).st_mode
    except FileNotFoundError as exc:
        raise DraftError(CheckCode.FORMAT_MISSING.value, marker) from exc
    except OSError as exc:
        # 已存在但不可 stat（EACCES/EIO）：普通 I/O 失败，不是"缺失"。
        raise DraftError(CheckCode.FORMAT_INVALID.value, marker) from exc
    if stat.S_ISLNK(mode):
        raise DraftError(CheckCode.PATH_SYMLINK_ESCAPE.value, marker)
    if not stat.S_ISREG(mode):
        # 已存在但不是普通文件（目录等）：异常内部状态，不是"缺失"。
        raise DraftError(CheckCode.FORMAT_INVALID.value, marker)


def read_marker_snapshot(root: Path) -> MarkerSnapshot:
    """一次读取返回不可变 marker 快照（round-15 H1）：state、bytes、digest 同源。

    实现顺序必须是稳定分类 .draft 中间组件 -> 稳定分类 marker 最终节点 -> 读取普通
    文件 bytes 一次 -> 从同一 bytes 完成 UTF-8、JSON、schema 解析。不做第二次
    read_text/read_bytes；non-UTF-8、JSON 损坏、未知字段与版本不支持语义保持既有。
    """
    marker = root / MARKER_PATH
    # .draft 缺失合法：返回 False 后仍继续由 leaf 分类给出 format.missing。
    classify_marker_parent(root / INTERNAL_DIR, marker)
    _classify_marker_leaf(marker)
    try:
        raw = marker.read_bytes()
        decoded = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        # 非法 UTF-8 的登记文件与 JSON 损坏一致映射 format.invalid（回归 M3）。
        raise DraftError(CheckCode.FORMAT_INVALID.value, marker) from exc
    return MarkerSnapshot(state=parse_marker_value(decoded, marker), raw_bytes=raw)


def read_marker(root: Path) -> MarkerState:
    """Read and validate .draft/draft.json, returning the parsed state.

    ``read_marker`` 是只返回解析状态的兼容包装；需要 marker bytes/digest，或把 marker
    与计划、日志、mutation、observation 组合的入口必须使用 ``read_marker_snapshot``
    （round-15 H1），不能为满足这些消费再次读取 live 文件。
    """
    return read_marker_snapshot(root).state


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


def _root_conclusions_managed(state: ConclusionsState | None) -> bool:
    """根级 conclusions.md 的受管分类以 marker 所有权状态为条件（round-18 M1/A1）。

    合法 marker 的 generated 保持受管（机器产物）；not_generated 时根级
    ``conclusions.md`` 不再按受管页解析，与其他未受管内容同等对待（不阻断结构命令、
    链接不承诺重写），同一文件在加载器与检查器中的分类由此闭合。marker 缺失/非法/
    未知版本（None，维持现状）继续按受管对待；此类根的结构命令本就在更早的 marker
    错误处失败，不引入行为变化。
    """
    return state is not ConclusionsState.NOT_GENERATED


def _discover(
    root: Path, conclusions_state: ConclusionsState | None
) -> tuple[list[Path], list[CheckIssue]]:
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
        if entry.name == ROOT_PAGE.name:
            managed.append(Path(entry.name))
        elif entry.name == CONCLUSIONS_PAGE.name:
            if _root_conclusions_managed(conclusions_state):
                managed.append(Path(entry.name))
            else:
                issues.append(error_issue(CheckCode.PAGE_UNMANAGED_PATH, Path(entry.name)))
        else:
            issues.append(error_issue(CheckCode.PAGE_UNMANAGED_PATH, Path(entry.name)))
    return managed, issues


def _load(root: Path, conclusions_state: ConclusionsState | None) -> LoadResult:
    issues: list[CheckIssue] = []
    managed_paths, discover_issues = _discover(root, conclusions_state)
    issues.extend(discover_issues)
    managed_set = set(managed_paths)

    pages: dict[Path, PageDocument] = {}
    for relative in managed_paths:
        try:
            # 解析与重写基于原始解码文本保留 \r\n：编码回 UTF-8 即磁盘原始字节，
            # 机械命令只修改协议要求的跨度，未修改区域的字节表示保持不变。
            text = (root / relative).read_bytes().decode("utf-8")
            pages[relative] = parse_page(relative, text)
        except (UnicodeDecodeError, OSError) as exc:
            issues.append(
                CheckIssue(
                    code=CheckCode.PAGE_PARSE_ERROR.value,
                    severity=IssueSeverity.ERROR,
                    path=relative,
                    line=None,
                    details=(str(exc),),
                )
            )
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
    resource_facts: list[CheckIssue] = []
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
                issue = error_issue(
                    CheckCode.LINK_MISSING,
                    source,
                    reference.link.line,
                    reference.link.destination,
                    resource.target,
                )
                link_issues.append(issue)
                resource_facts.append(issue)
            elif target_state is TargetState.INVALID:
                issue = error_issue(
                    CheckCode.LINK_INVALID,
                    source,
                    reference.link.line,
                    reference.link.destination,
                    resource.target,
                )
                link_issues.append(issue)
                resource_facts.append(issue)
            elif target_state is TargetState.UNAVAILABLE:
                issue = CheckIssue(
                    code=CheckCode.LINK_UNAVAILABLE.value,
                    severity=IssueSeverity.WARNING,
                    path=source,
                    line=reference.link.line,
                    details=(reference.link.destination, resource.target),
                )
                link_issues.append(issue)
                resource_facts.append(issue)
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

    # 子页按父页面导航声明顺序排列；父导航未声明该子页时按路径排序作为确定性兜底。
    for parent, entries in children.items():
        positions: dict[Path, int] = {}
        document = pages.get(parent)
        if document is not None:
            for link in document.links:
                try:
                    target, _anchor = resolve_local_destination(parent, link.destination, root)
                except DraftError:
                    continue
                if target in entries and target not in positions:
                    positions[target] = link.whole_span.start

        def _declared_key(
            child: Path, positions: Mapping[Path, int] = positions
        ) -> tuple[object, Path]:
            return (positions.get(child, 1 << 30), child)

        entries.sort(key=_declared_key)

    graph = DraftGraph(
        root=root,
        pages={path: pages[path] for path in sorted(pages)},
        parents={path: parents[path] for path in sorted(parents)},
        # children 已按父导航声明顺序排序；外层保持父键排序，条目顺序不变。
        children={parent: tuple(entries) for parent, entries in sorted(children.items())},
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
    return LoadResult(graph=graph, issues=issues, resource_facts=resource_facts)


def structural_issues(result: LoadResult) -> list[CheckIssue]:
    """结构性问题：排除正文资源事实后的加载问题，用于命令的可写性判定。"""
    facts = set(result.resource_facts)
    return [issue for issue in result.issues if issue not in facts]


def load_graph(root: Path) -> DraftGraph:
    resolved = resolve_draft_root(root)
    result = load_graph_tolerant(resolved)
    try:
        state = read_marker(resolved)
    except DraftError:
        # 无效 marker 已由 tolerant 记录为阻断 issue；由下方 blocking 分支报出。
        pass
    else:
        if state.root_state is not RootState.REGISTERED:
            # 候选根普通写命令在 Task 9–11 按操作级门禁开放前，注册模式入口
            # 显式拒绝，不能因为 marker 可读就意外放行。此代码随门禁落地删除。
            raise DraftError("root.candidate", resolved)
    blocking = structural_issues(result)
    if blocking:
        first = blocking[0]
        raise DraftError(
            first.code,
            *(() if first.path is None else (first.path,)),
            details=first.details,
            line=first.line,
        )
    return result.graph


def load_graph_tolerant(root: Path) -> LoadResult:
    """容错加载：candidate 与 registered 共享同一页面/关系/链接/路径协议。

    marker 是根身份的唯一权威：``load_graph_tolerant`` 读取并校验 marker，不按外部
    mode 区分分支。发现的受管分类消费同一次读取的所有权状态（round-18 M1/A1），
    不新增第二次 marker 文件读取。
    """
    resolved = root if root.is_absolute() else resolve_draft_root(root)
    marker_issues: list[CheckIssue] = []
    conclusions_state: ConclusionsState | None = None
    try:
        marker_state = read_marker(resolved)
    except DraftError as error:
        marker_issues.append(
            CheckIssue(
                code=error.code,
                severity=IssueSeverity.ERROR,
                path=MARKER_PATH,
                line=None,
            )
        )
    else:
        conclusions_state = marker_state.conclusions_state
    result = _load(resolved, conclusions_state)
    return LoadResult(
        graph=result.graph,
        issues=[*marker_issues, *result.issues],
        resource_facts=result.resource_facts,
    )
