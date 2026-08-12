"""候选/正式写命令的操作事实加载边界。

``load_operation_facts`` 把 registered 全局健康加载、candidate 局部页面可判定加载
和 candidate 全受管页可判定加载收束在一个边界；业务模块仍负责选择自己的事实闭包
并验证自有不变量。registered 根存在任一完整检查错误时，在调用业务计划器或执行前
拒绝。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from draft.errors import DraftError
from draft.graph import DraftGraph, load_graph, read_marker
from draft.models import RootState
from draft.observations import ObservationManifest, graph_observations
from draft.paths import resolve_draft_root
from draft.query import load_tolerant_view


@dataclass(frozen=True)
class OperationFacts:
    graph: DraftGraph
    root_state: RootState
    blocking_paths: tuple[Path, ...]
    observations: ObservationManifest


def load_operation_facts(root: Path, *, allow_hub_empty: Path | None = None) -> OperationFacts:
    """读取 marker 并按根状态选择健康门禁；返回图、根状态、阻碍与读集观察。

    registered：完整健康门禁（check_draft，含操作/trash/内部命名空间；任一错误即拒绝）。
    ``allow_hub_empty`` 供 collapse 容忍目标枢纽的 HUB_EMPTY（折叠正是修复它）。
    candidate：全受管页可判定加载，具体页阻碍由业务模块按闭包拒绝。
    """
    from draft.checker import check_draft
    from draft.diagnostic_codes import CheckCode

    resolved = resolve_draft_root(root)
    state = read_marker(resolved)
    if state.root_state is RootState.REGISTERED:
        report = check_draft(resolved, exclude_resource_facts=True)
        errors = [
            issue
            for issue in report.errors
            if not (
                allow_hub_empty is not None
                and issue.code == CheckCode.HUB_EMPTY.value
                and issue.path == allow_hub_empty
            )
        ]
        if errors:
            first = errors[0]
            raise DraftError(
                first.code,
                first.path if first.path is not None else resolved,
                details=first.details,
                line=first.line,
            )
        graph = load_graph(resolved)
        return OperationFacts(
            graph=graph,
            root_state=state.root_state,
            blocking_paths=(),
            observations=graph_observations(graph),
        )
    view = load_tolerant_view(resolved)
    return OperationFacts(
        graph=view.graph,
        root_state=state.root_state,
        blocking_paths=view.blocking_paths,
        observations=graph_observations(view.graph),
    )
