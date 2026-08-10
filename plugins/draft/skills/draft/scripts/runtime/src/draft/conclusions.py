"""按需生成结论页：固定机器产物形状的唯一生产权威来源。

所有权状态只从 not_generated 前进到 generated。生成器从导航树深度优先
收集 confirmed 页面，输出固定形状的结论页，并原子更新根导航与登记标记。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from draft.errors import DraftError
from draft.graph import (
    CONCLUSIONS_PAGE,
    MARKER_PATH,
    ROOT_PAGE,
    DraftGraph,
)
from draft.markdown import append_parent_navigation
from draft.models import (
    FileMutation,
    MutationKind,
    OperationPlan,
    PageDocument,
    RelationCode,
    StatusCode,
)
from draft.operations import sha256_bytes
from draft.vocabulary import ConclusionsState, status_label


class ConclusionsView(Protocol):
    """生成器需要的只读图结构；DraftGraph 与内存替身都满足。"""

    @property
    def root(self) -> Path: ...

    @property
    def pages(self) -> Mapping[Path, PageDocument]: ...

    @property
    def parents(self) -> Mapping[Path, Path]: ...

    @property
    def children(self) -> Mapping[Path, tuple[Path, ...]]: ...


CONCLUSIONS_TITLE = "结论汇总"
CONCLUSIONS_HEADING = "## 已确认结论"

# 所有权状态由 vocabulary 权威定义；此处再导出方便生成器消费者统一导入。
__all__ = [
    "ConclusionsState",
    "GenerationPlan",
    "GenerationReason",
    "is_conclusions_document",
    "is_conclusions_nav_canonical",
    "plan_generation",
]


def is_conclusions_document(document: PageDocument, root: Path) -> bool:
    """结论页固定机器形状：一级标题、状态、父主题与章节标题。

    生成、检查、页面创建和结构操作必须依据同一套形状定义识别结论页，
    不分别复制标题或链接字面量后各自判断（规格 §12.4）。
    """
    from draft.graph import resolve_local_destination

    if document.title != CONCLUSIONS_TITLE:
        return False
    if document.status.code is not StatusCode.PROPOSED:
        return False
    parents = [r for r in document.relations if r.code is RelationCode.PARENT]
    if len(parents) != 1:
        return False
    try:
        target, _anchor = resolve_local_destination(
            document.path, parents[0].link.destination, root
        )
    except DraftError:
        return False
    if target != ROOT_PAGE:
        return False
    return CONCLUSIONS_HEADING in document.text


def is_conclusions_nav_canonical(index_text: str, root: Path) -> bool:
    """根导航结论链接是否唯一、规范且位于最后（规格 §12.4）。"""
    from draft.graph import resolve_local_destination
    from draft.markdown import MarkdownLink, parse_page

    document = parse_page(ROOT_PAGE, index_text)
    conclusions_links: list[MarkdownLink] = []
    for link in document.links:
        try:
            target, _anchor = resolve_local_destination(ROOT_PAGE, link.destination, root)
        except DraftError:
            continue
        if target == CONCLUSIONS_PAGE:
            conclusions_links.append(link)
    if len(conclusions_links) != 1:
        return False
    (link,) = conclusions_links
    lines = index_text.split("\n")
    start_line = index_text.count("\n", 0, link.whole_span.start)
    for line in lines[start_line + 1 :]:
        stripped = line.strip()
        if stripped.startswith("#"):
            break
        if stripped.startswith("- ["):
            return False
    return True


class GenerationReason(StrEnum):
    NO_CONFIRMED_PAGES = "no_confirmed_pages"


def _confirmed_pages(graph: ConclusionsView) -> tuple[Path, ...]:
    """按父导航深度优先顺序收集 confirmed 页面，排除根与结论页。

    导航声明顺序必须可确定：子页未在父导航中声明时生成失败，提示先运行 check，
    不能退回按路径排序的近似结果（规格 §12.4）。
    """
    navigated: set[Path] = set()

    def walk(path: Path, order: list[Path]) -> None:
        if (
            path != ROOT_PAGE
            and path != CONCLUSIONS_PAGE
            and graph.pages.get(path, None) is not None
        ):
            page = graph.pages[path]
            if page.status.code is StatusCode.CONFIRMED:
                order.append(path)
        for child in _children_in_declaration_order(graph, path):
            if child in navigated:
                continue
            navigated.add(child)
            walk(child, order)

    order: list[Path] = []
    walk(ROOT_PAGE, order)
    # 未被根导航遍历到达的受管理页面（孤儿或父关系断裂）：导航结构不合法，
    # 禁止退回路径排序的近似结果（规格 §12.4）。
    unreached = sorted(
        page
        for page in graph.pages
        if page not in navigated and page not in (ROOT_PAGE, CONCLUSIONS_PAGE)
    )
    if unreached:
        raise DraftError(
            "conclusions.invalid_navigation",
            graph.root,
            details=(unreached[0].as_posix(),),
        )
    return tuple(order)


def _children_in_declaration_order(graph: ConclusionsView, parent: Path) -> tuple[Path, ...]:
    """按父页面导航声明顺序返回子页。"""
    return graph.children.get(parent, ())


def _render_conclusions(graph: ConclusionsView, confirmed: tuple[Path, ...]) -> str:
    lines = [
        f"# {CONCLUSIONS_TITLE}",
        "",
        f"> 状态：`{status_label(StatusCode.PROPOSED)}`",
        ">",
        "> 父主题：[根索引](index.md)",
        "",
        CONCLUSIONS_HEADING,
        "",
    ]
    for page in confirmed:
        document = graph.pages[page]
        lines.append(f"- [{document.title}]({page.as_posix()})")
    return "\n".join(lines) + "\n"


def _ensure_conclusions_nav(index_text: str, root: Path) -> str:
    """保证根导航结论链接唯一、规范且位于最后；缺失或错位时修复。"""
    from draft.graph import resolve_local_destination
    from draft.markdown import MarkdownLink, parse_page, remove_parent_navigation

    document = parse_page(ROOT_PAGE, index_text)
    conclusions_link: MarkdownLink | None = None
    for link in document.links:
        try:
            target, _anchor = resolve_local_destination(ROOT_PAGE, link.destination, root)
        except DraftError:
            continue
        if target == CONCLUSIONS_PAGE:
            conclusions_link = link
            break
    if conclusions_link is not None:
        lines = index_text.split("\n")
        start_line = index_text.count("\n", 0, conclusions_link.whole_span.start)
        is_last = True
        for line in lines[start_line + 1 :]:
            stripped = line.strip()
            if stripped.startswith("#"):
                break
            if stripped.startswith("- ["):
                is_last = False
                break
        if is_last:
            return index_text
        index_text = remove_parent_navigation(index_text, conclusions_link)
    return append_parent_navigation(index_text, CONCLUSIONS_TITLE, CONCLUSIONS_PAGE.as_posix())


def _marker_bytes(state: ConclusionsState) -> bytes:
    payload = {
        "format_version": 1,
        "conclusions_state": state.value,
    }
    return (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")


@dataclass(frozen=True)
class GenerationPlan:
    generated: bool
    reason: GenerationReason | None
    operation: OperationPlan | None


def plan_generation(graph: ConclusionsView, *, state: ConclusionsState) -> GenerationPlan:
    """构造结论生成计划；不执行写入。"""
    confirmed = _confirmed_pages(graph)
    if state is ConclusionsState.NOT_GENERATED:
        conclusions_path = graph.root / CONCLUSIONS_PAGE
        if CONCLUSIONS_PAGE in graph.pages or (
            conclusions_path.is_file() and not conclusions_path.is_symlink()
        ):
            from draft.diagnostic_codes import CheckCode

            raise DraftError(
                CheckCode.CONCLUSIONS_UNEXPECTED_FILE.value, graph.root / CONCLUSIONS_PAGE
            )
        if not confirmed:
            return GenerationPlan(
                generated=False, reason=GenerationReason.NO_CONFIRMED_PAGES, operation=None
            )
    mutations: list[FileMutation] = []
    index = graph.pages.get(ROOT_PAGE)
    index_text = index.text if index is not None else ""
    if index is not None:
        # 已生成时根导航结论链接缺失或错位必须修复；未生成时首次生成创建。
        index_text = _ensure_conclusions_nav(index_text, graph.root)

    rendered = _render_conclusions(graph, confirmed)
    previous_document = graph.pages.get(CONCLUSIONS_PAGE)
    conclusions_path = graph.root / CONCLUSIONS_PAGE
    # 优先使用已解析的页面文本；解析失败的损坏页回退到磁盘原文，保证可重写。
    if previous_document is not None:
        previous_bytes = previous_document.text.encode("utf-8")
    elif conclusions_path.is_file() and not conclusions_path.is_symlink():
        previous_bytes = conclusions_path.read_bytes()
    else:
        previous_bytes = None
    previous = previous_bytes is not None
    rendered_bytes = rendered.encode("utf-8")

    conclusions_changed = not previous or previous_bytes != rendered_bytes
    index_changed = index is not None and index_text != index.text
    # 结论页与根导航都无需变化：幂等，不产生公开效果。
    if not conclusions_changed and not index_changed:
        return GenerationPlan(generated=True, reason=None, operation=None)

    if conclusions_changed:
        mutations.append(
            FileMutation(
                CONCLUSIONS_PAGE,
                MutationKind.WRITE,
                sha256_bytes(previous_bytes) if previous else None,
                sha256_bytes(rendered_bytes),
                rendered_bytes,
            )
        )
    if index_changed:
        assert index is not None
        mutations.append(
            FileMutation(
                ROOT_PAGE,
                MutationKind.WRITE,
                sha256_bytes(index.text.encode("utf-8")),
                sha256_bytes(index_text.encode("utf-8")),
                index_text.encode("utf-8"),
            )
        )
    marker_bytes = _marker_bytes(ConclusionsState.GENERATED)
    marker_path = graph.root / MARKER_PATH
    marker_before = marker_path.read_bytes() if marker_path.is_file() else None
    mutations.append(
        FileMutation(
            MARKER_PATH,
            MutationKind.WRITE,
            sha256_bytes(marker_before) if marker_before is not None else None,
            sha256_bytes(marker_bytes),
            marker_bytes,
        )
    )
    from draft.plan_effects import PlanAction, PlanEffect

    effects = [
        PlanEffect(action=PlanAction.MODIFY, path=path)
        for path in (
            *((CONCLUSIONS_PAGE,) if conclusions_changed else ()),
            *((ROOT_PAGE,) if index_changed else ()),
        )
    ]
    plan = OperationPlan(
        kind="generate-conclusions",
        root=graph.root,
        mutations=tuple(mutations),
        validation_mode="registered",
        effects=tuple(effects),
    )
    return GenerationPlan(generated=True, reason=None, operation=plan)


def generate_conclusions(root: Path) -> GenerationPlan:
    """按需生成结论页并原子更新根导航与登记标记；返回生成结果。"""
    from draft.graph import read_marker
    from draft.operations import execute_plan
    from draft.paths import resolve_draft_root

    resolved = resolve_draft_root(root)
    state = read_marker(resolved).conclusions_state
    graph = _load_graph_with_conclusions(resolved, state)
    plan = plan_generation(graph, state=state)
    if plan.operation is not None:
        execute_plan(plan.operation)
    return plan


def load_generation_graph(root: Path) -> DraftGraph:
    """读取所有权状态并加载生成器可重写结论页的容错快照。"""
    from draft.graph import read_marker
    from draft.paths import resolve_draft_root

    resolved = resolve_draft_root(root)
    state = read_marker(resolved).conclusions_state
    return _load_graph_with_conclusions(resolved, state)


def _load_graph_with_conclusions(root: Path, state: ConclusionsState) -> DraftGraph:
    """专用容错加载：generated 时容忍缺失/损坏的结论页（可替换目标）。"""
    from draft.graph import load_graph_tolerant

    if state is ConclusionsState.GENERATED:
        conclusions = root / CONCLUSIONS_PAGE
        if conclusions.is_file() and not conclusions.is_symlink():
            # 保留文件以便生成器重写；不在这里做普通加载期检查。
            pass
    return load_graph_tolerant(root).graph
