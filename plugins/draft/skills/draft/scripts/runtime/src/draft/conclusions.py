"""按需生成结论页：固定机器产物形状的唯一生产权威来源。

所有权状态只从 not_generated 前进到 generated。生成器从导航树深度优先
收集 confirmed 页面，输出固定形状的结论页，并原子更新根导航与登记标记。
"""

from __future__ import annotations

import json
import re
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
from draft.models import (
    FileMutation,
    MarkdownLink,
    MutationKind,
    OperationKind,
    OperationPlan,
    PageDocument,
    RelationCode,
    RootState,
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

_SUBTOPIC_HEADING = re.compile(r"^## 子主题\s*$")


def is_canonical_conclusions_entry(line: str) -> bool:
    """单行是否为规范结论条目：``- [标题](相对路径)``，整行恰好一个非图片链接。

    规格 §12.4 的列表形状是“只包含标题和链接”的扁平条目。不靠文本正则近似，
    而是解析整行并校验：列表项标记恰为 ``-``，标记之后的内容恰好由一个非图片
    Markdown 链接组成，标签转义后与解析结果闭合，目标为允许的相对路径语法
    （无协议、绝对路径或 tooltip）。
    """
    from draft.errors import DraftError
    from draft.markdown import escape_link_label, list_item_content, parse_page

    content = list_item_content(line)
    if content is None:
        return False
    # 通用列表识别用于判断“是否列表项”；规范结论条目的标记必须恰为 ``-``。
    if not line.startswith("-"):
        return False
    content = content.strip()
    try:
        parsed = parse_page(Path("__entry__.md"), f"# x\n\n> 状态：`待讨论`\n\n{content}\n")
    except DraftError:
        return False
    links = [link for link in parsed.links if not link.is_image]
    if len(links) != 1:
        return False
    (link,) = links
    if not link.label.strip():
        return False
    if not _entry_destination_ok(link.destination):
        return False
    # 标签用与生成器相同的转义边界重新编码后必须与原文闭合（含 [ ] 转义标题）。
    return content == f"[{escape_link_label(link.label)}]({link.destination})"


def _entry_destination_ok(destination: str) -> bool:
    """允许的目标语法：非空、相对路径、无协议/绝对前缀。"""
    if not destination:
        return False
    if destination.startswith("/"):
        return False
    return ":" not in destination.split("/", 1)[0]


def is_conclusions_document(document: PageDocument, root: Path) -> bool:
    """结论页固定机器形状：一级标题、状态、父主题、章节标题与列表形状。

    生成、检查、页面创建和结构操作必须依据同一套形状定义识别结论页，
    不分别复制标题或链接字面量后各自判断（规格 §12.4）。

    状态允许生成器写出的 ``proposed`` 与规格 §12.5 允许机器所有权下由 Agent
    推进的 ``confirmed``；其余区域保持固定：唯一父主题指向根、唯一
    ``## 已确认结论`` 章节、无额外标题或段落，章节后只允许
    ``- [标题](路径)`` 扁平条目。
    """
    from draft.graph import resolve_local_destination

    if document.title != CONCLUSIONS_TITLE:
        return False
    if document.status.code not in (StatusCode.PROPOSED, StatusCode.CONFIRMED):
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

    lines = document.text.splitlines()
    headings_1 = [line for line in lines if line.startswith("# ") and not line.startswith("##")]
    if len(headings_1) != 1 or headings_1[0].strip() != f"# {CONCLUSIONS_TITLE}":
        return False
    headings_2 = [
        index
        for index, line in enumerate(lines)
        if line.startswith("## ") and not line.startswith("###")
    ]
    if len(headings_2) != 1:
        return False
    heading_index = headings_2[0]
    if lines[heading_index].strip() != CONCLUSIONS_HEADING:
        return False
    if any(line.startswith("###") for line in lines):
        return False

    # 章节标题前的机器块：只允许状态行、空引用行与父主题行。
    machine: list[str] = []
    for line in lines[:heading_index]:
        stripped = line.strip()
        if not stripped or stripped == f"# {CONCLUSIONS_TITLE}":
            continue
        machine.append(stripped)
    if len(machine) != 3:
        return False
    if not machine[0].startswith("> 状态："):
        return False
    if machine[1] != ">":
        return False
    if not machine[2].startswith("> 父主题："):
        return False

    for line in lines[heading_index + 1 :]:
        stripped = line.strip()
        if not stripped:
            continue
        if not is_canonical_conclusions_entry(stripped):
            return False
    return True


def _subtopic_section(index_text: str) -> tuple[int, int] | None:
    """根索引 ``## 子主题`` 小节的行区间 ``[start, end)``；无该小节时返回 None。

    ``end`` 是下一个 ``#``/``##`` 级别标题的行号；更深小节（``###``）仍属于
    本小节，不提前截断。
    """
    lines = index_text.split("\n")
    start = next((index for index, line in enumerate(lines) if _SUBTOPIC_HEADING.match(line)), None)
    if start is None:
        return None
    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if line.startswith("# ") or line.startswith("## "):
            end = index
            break
    return start, end


def _conclusions_nav_links(index_text: str, root: Path) -> list[MarkdownLink]:
    """``## 子主题`` 小节内指向结论页的链接；小节外链接不计入导航。"""
    from draft.graph import resolve_local_destination
    from draft.markdown import parse_page

    document = parse_page(ROOT_PAGE, index_text)
    section = _subtopic_section(index_text)
    if section is None:
        return []
    start, end = section
    links: list[MarkdownLink] = []
    for link in document.links:
        line_index = index_text.count("\n", 0, link.whole_span.start)
        if not (start < line_index < end):
            continue
        try:
            target, _anchor = resolve_local_destination(ROOT_PAGE, link.destination, root)
        except DraftError:
            continue
        if target == CONCLUSIONS_PAGE:
            links.append(link)
    return links


def _is_last_list_item(index_text: str, link: MarkdownLink, section_end: int) -> bool:
    """链接是小节内最后一个列表项；其后到小节结束不允许再有任何列表项。

    与 markdown 模块的列表识别规则一致（``-``/``+``/``*``/有序），不只看 ``- [``。
    """
    from draft.markdown import list_item_content

    lines = index_text.split("\n")
    line_index = index_text.count("\n", 0, link.whole_span.start)
    return all(
        list_item_content(line.strip()) is None for line in lines[line_index + 1 : section_end]
    )


def is_conclusions_nav_canonical(index_text: str, root: Path) -> bool:
    """根导航结论链接唯一、规范（标签与字面目标）且位于 ``## 子主题`` 最后（规格 §12.4）。"""
    links = _conclusions_nav_links(index_text, root)
    if len(links) != 1:
        return False
    (link,) = links
    if link.label != CONCLUSIONS_TITLE:
        return False
    if link.destination != CONCLUSIONS_PAGE.as_posix():
        return False
    section = _subtopic_section(index_text)
    assert section is not None
    _, end = section
    return _is_last_list_item(index_text, link, end)


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
        lines.append(_render_conclusions_entry(page, document.title))
    return "\n".join(lines) + "\n"


def _render_conclusions_entry(page: Path, title: str) -> str:
    """生成单条结论条目：标题经与导航同一转义边界编码，解析标签必须还原为原始标题。"""
    from draft.errors import DraftError
    from draft.markdown import escape_link_label, parse_page

    entry = f"- [{escape_link_label(title)}]({page.as_posix()})"
    parsed = parse_page(Path("__entry__.md"), f"# x\n\n> 状态：`待讨论`\n\n{entry}\n")
    if not parsed.links or parsed.links[0].label != title:
        raise DraftError("conclusions.title_roundtrip", page)
    return entry


def _ensure_conclusions_nav(index_text: str, root: Path) -> str:
    """保证根导航结论链接唯一、规范（标签/字面目标）且位于 ``## 子主题`` 最后。

    缺失、错位、重复或非规范形式都移除后重建规范链接。
    """
    from draft.markdown import append_parent_navigation, remove_parent_navigation_many

    if is_conclusions_nav_canonical(index_text, root):
        return index_text
    links = _conclusions_nav_links(index_text, root)
    if links:
        index_text = remove_parent_navigation_many(index_text, links)
    return append_parent_navigation(index_text, CONCLUSIONS_TITLE, CONCLUSIONS_PAGE.as_posix())


def _marker_bytes(root_state: RootState, state: ConclusionsState) -> bytes:
    payload = {
        "format_version": 1,
        "root_state": root_state.value,
        "conclusions_state": state.value,
    }
    return (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")


@dataclass(frozen=True)
class GenerationPlan:
    generated: bool
    reason: GenerationReason | None
    operation: OperationPlan | None


def plan_generation(
    graph: ConclusionsView,
    *,
    state: ConclusionsState,
    root_state: RootState,
) -> GenerationPlan:
    """构造结论生成计划；不执行写入。root_state 必须由调用方从当前 marker 权威读出。"""
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
    marker_bytes = _marker_bytes(root_state, ConclusionsState.GENERATED)
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
    from draft.models import RootState
    from draft.observations import graph_observations

    plan = OperationPlan(
        kind=OperationKind.GENERATE_CONCLUSIONS,
        root=graph.root,
        mutations=tuple(mutations),
        root_state=RootState.REGISTERED,
        observations=graph_observations(graph),
        effects=tuple(effects),
    )
    return GenerationPlan(generated=True, reason=None, operation=plan)


def generate_conclusions(root: Path) -> GenerationPlan:
    """按需生成结论页并原子更新根导航与登记标记；返回生成结果。

    门禁是生成器的可判定性而非一般健康：全部受管页面路径/标题/状态/导航可判定才
    允许（不可解析页面阻止，不完整导航由生成器报 conclusions.invalid_navigation）。
    结论页在 generated 所有权下是可替换的机器产物，损坏不阻止生成。
    """
    from draft.graph import read_marker
    from draft.operations import execute_plan
    from draft.paths import resolve_draft_root
    from draft.query import load_tolerant_view

    resolved = resolve_draft_root(root)
    marker = read_marker(resolved)
    state = marker.conclusions_state
    view = load_tolerant_view(resolved)
    blocking = [
        path
        for path in view.blocking_paths
        if not (state is ConclusionsState.GENERATED and path == CONCLUSIONS_PAGE)
    ]
    if blocking:
        details = tuple(path.as_posix() for path in blocking)
        raise DraftError("conclusions.incomplete", resolved, details=details)
    graph = _load_graph_with_conclusions(resolved, state)
    plan = plan_generation(graph, state=state, root_state=marker.root_state)
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
