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
from draft.models import FileMutation, MutationKind, OperationPlan, PageDocument, StatusCode
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


CONCLUSIONS_TITLE = "结论汇总"
CONCLUSIONS_HEADING = "## 已确认结论"

# 所有权状态由 vocabulary 权威定义；此处再导出方便生成器消费者统一导入。
__all__ = [
    "ConclusionsState",
    "GenerationPlan",
    "GenerationReason",
    "plan_generation",
]


class GenerationReason(StrEnum):
    NO_CONFIRMED_PAGES = "no_confirmed_pages"


def _confirmed_pages(graph: ConclusionsView) -> tuple[Path, ...]:
    """按父导航深度优先顺序收集 confirmed 页面，排除根与结论页。"""

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
            walk(child, order)

    order: list[Path] = []
    walk(ROOT_PAGE, order)
    return tuple(order)


def _children_in_declaration_order(graph: ConclusionsView, parent: Path) -> tuple[Path, ...]:
    """按父页面导航声明顺序返回子页；无法确定声明顺序时按路径排序。"""
    children = [child for child, declared in graph.parents.items() if declared == parent]
    return tuple(sorted(children))


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


def _root_navigation_after(graph: ConclusionsView, index_text: str) -> str:
    """在根索引 ``## 子主题`` 末尾加入唯一且最后的结论链接。"""
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
            raise DraftError("conclusions.unexpected_file", graph.root / CONCLUSIONS_PAGE)
        if not confirmed:
            return GenerationPlan(
                generated=False, reason=GenerationReason.NO_CONFIRMED_PAGES, operation=None
            )
    mutations: list[FileMutation] = []
    index = graph.pages.get(ROOT_PAGE)
    index_text = index.text if index is not None else ""
    if index is not None and CONCLUSIONS_PAGE not in graph.pages:
        index_text = _root_navigation_after(graph, index_text)

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

    index_unchanged = True
    if index is not None:
        index_bytes = index_text.encode("utf-8")
        index_unchanged = index_bytes == index.text.encode("utf-8")

    # 已生成且结论页与根导航都无需变化：幂等，不产生公开效果。
    if (
        state is ConclusionsState.GENERATED
        and previous
        and previous_bytes == rendered_bytes
        and index_unchanged
    ):
        return GenerationPlan(generated=True, reason=None, operation=None)

    if previous:
        assert previous_bytes is not None
        mutations.append(
            FileMutation(
                CONCLUSIONS_PAGE,
                MutationKind.WRITE,
                sha256_bytes(previous_bytes),
                sha256_bytes(rendered_bytes),
                rendered_bytes,
            )
        )
    else:
        mutations.append(
            FileMutation(
                CONCLUSIONS_PAGE,
                MutationKind.WRITE,
                None,
                sha256_bytes(rendered_bytes),
                rendered_bytes,
            )
        )
    if index is not None and not index_unchanged:
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
        PlanEffect(action=PlanAction.MODIFY, path=CONCLUSIONS_PAGE),
        PlanEffect(action=PlanAction.MODIFY, path=ROOT_PAGE),
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


def _load_graph_with_conclusions(root: Path, state: ConclusionsState) -> DraftGraph:
    """专用容错加载：generated 时容忍缺失/损坏的结论页（可替换目标）。"""
    from draft.graph import load_graph_tolerant

    if state is ConclusionsState.GENERATED:
        conclusions = root / CONCLUSIONS_PAGE
        if conclusions.is_file() and not conclusions.is_symlink():
            # 保留文件以便生成器重写；不在这里做普通加载期检查。
            pass
    return load_graph_tolerant(root).graph
