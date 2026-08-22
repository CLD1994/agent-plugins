"""marker 状态穿透契约：持有状态的调用方传入后，边界不再读取 marker 文件。

计数方式：用计数 wrapper 包住真实 read_marker_snapshot，同时替换
draft.graph 与 draft.operation_facts 两个命名空间（后者在模块级绑定了该函数，
单独 patch draft.graph 拦截不到 facts 层调用；read_marker 及各处惰性导入
最终都落到 draft.graph.read_marker_snapshot）。真实文件系统协作者在集成层验证。
"""

from __future__ import annotations

from pathlib import Path

import pytest

import draft.graph as graph_module
import draft.operation_facts as facts_module
from draft.checker import check_draft
from draft.conclusions import CONCLUSIONS_PAGE, load_generation_graph
from draft.diagnostic_codes import CheckCode
from draft.errors import DraftError
from draft.graph import FORMAT_VERSION, MarkerSnapshot, MarkerState, load_graph, load_graph_tolerant
from draft.lifecycle import adopt
from draft.models import CheckReport, RootState
from draft.operation_facts import load_operation_facts
from draft.paths import resolve_draft_root
from draft.query import load_tolerant_view
from draft.structure import move_page
from draft.vocabulary import ConclusionsState
from tests.integration.support import DraftFactory, build_valid_candidate, managed_marker_text

PAGE_INDEX = """# Topic

> 状态：`已关闭：纯导航页，不表达独立结论`

## 子主题

- [甲](pages/a.md)
"""

PAGE_A = """# 甲

> 状态：`待讨论`
>
> 父主题：[根索引](../index.md)

甲。
"""

# 受管分类测试需要 conclusions.md 可解析为普通页面（parse_page 要求状态块）。
CONCLUSIONS_TEXT = "# 结论\n\n> 状态：`待讨论`\n>\n> 父主题：[根索引](index.md)\n\n正文。\n"


@pytest.fixture
def marker_reads(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """计数并放行全部 marker snapshot 读取；返回按序记录的根路径。"""
    real = graph_module.read_marker_snapshot
    calls: list[Path] = []

    def counting(root: Path) -> MarkerSnapshot:
        calls.append(root)
        return real(root)

    monkeypatch.setattr(graph_module, "read_marker_snapshot", counting)
    monkeypatch.setattr(facts_module, "read_marker_snapshot", counting)
    return calls


def _registered(conclusions: ConclusionsState) -> MarkerState:
    return MarkerState(
        version=FORMAT_VERSION,
        root_state=RootState.REGISTERED,
        conclusions_state=conclusions,
    )


def test_load_graph_tolerant_consumes_passed_state_without_reading(
    draft_factory: DraftFactory, marker_reads: list[Path]
) -> None:
    """传入状态：不读取 marker 文件，marker_state 原样回传，图正常返回。"""
    root = draft_factory({"index.md": PAGE_INDEX, "pages/a.md": PAGE_A})
    state = _registered(ConclusionsState.NOT_GENERATED)

    result = load_graph_tolerant(root, marker_state=state)

    assert marker_reads == []
    assert result.marker_state == state
    assert set(result.graph.pages) == {Path("index.md"), Path("pages/a.md")}


def test_load_graph_tolerant_classification_follows_passed_state(
    draft_factory: DraftFactory, marker_reads: list[Path]
) -> None:
    """权威性：磁盘 marker 为 not_generated 时，受管分类按传入状态求值。"""
    root = draft_factory({"index.md": PAGE_INDEX, "pages/a.md": PAGE_A})
    (root / "conclusions.md").write_text(CONCLUSIONS_TEXT, encoding="utf-8")

    managed = load_graph_tolerant(root, marker_state=_registered(ConclusionsState.GENERATED))
    assert Path("conclusions.md") in managed.graph.pages
    assert marker_reads == []

    unmanaged = load_graph_tolerant(root, marker_state=_registered(ConclusionsState.NOT_GENERATED))
    assert Path("conclusions.md") not in unmanaged.graph.pages
    assert any(
        issue.code == CheckCode.PAGE_UNMANAGED_PATH.value and issue.path == Path("conclusions.md")
        for issue in unmanaged.issues
    )


def test_load_graph_tolerant_passed_state_equivalent_to_self_read(
    draft_factory: DraftFactory, marker_reads: list[Path]
) -> None:
    """等价：传入与磁盘一致的 state 时，issues 与 resource_facts 逐项相等。"""
    root = draft_factory({"index.md": PAGE_INDEX, "pages/a.md": PAGE_A})
    state = _registered(ConclusionsState.NOT_GENERATED)

    threaded = load_graph_tolerant(root, marker_state=state)
    marker_reads.clear()
    direct = load_graph_tolerant(root)

    assert threaded.issues == direct.issues
    assert threaded.resource_facts == direct.resource_facts
    assert set(threaded.graph.pages) == set(direct.graph.pages)
    assert threaded.marker_state == direct.marker_state == state
    assert marker_reads == [root]


def test_load_graph_tolerant_marker_state_none_when_disk_invalid(
    draft_factory: DraftFactory, marker_reads: list[Path]
) -> None:
    """缺省自读失败：marker_state 为 None，marker issue 照常记录（现状语义）。"""
    root = draft_factory({"index.md": PAGE_INDEX, "pages/a.md": PAGE_A})
    (root / ".draft" / "draft.json").write_text("{broken json", encoding="utf-8")

    result = load_graph_tolerant(root)

    assert result.marker_state is None
    assert any(issue.code == CheckCode.FORMAT_INVALID.value for issue in result.issues)
    assert marker_reads == [root]


def test_load_graph_consumes_passed_state_without_reading(
    draft_factory: DraftFactory, marker_reads: list[Path]
) -> None:
    """传入 registered 状态：返回图且全程不读 marker。"""
    root = draft_factory({"index.md": PAGE_INDEX, "pages/a.md": PAGE_A})

    graph = load_graph(root, marker_state=_registered(ConclusionsState.NOT_GENERATED))

    assert marker_reads == []
    assert set(graph.pages) == {Path("index.md"), Path("pages/a.md")}


def test_load_graph_rejects_candidate_from_passed_state(
    draft_factory: DraftFactory, marker_reads: list[Path]
) -> None:
    """权威性：磁盘 registered + 传入 candidate -> root.candidate，不读磁盘。"""
    root = draft_factory({"index.md": PAGE_INDEX, "pages/a.md": PAGE_A})
    candidate = MarkerState(
        version=FORMAT_VERSION,
        root_state=RootState.CANDIDATE,
        conclusions_state=ConclusionsState.NOT_GENERATED,
    )

    with pytest.raises(DraftError) as caught:
        load_graph(root, marker_state=candidate)

    assert caught.value.code == "root.candidate"
    assert marker_reads == []


def _issue_tuples(report: CheckReport) -> list[tuple[str, Path | None, tuple[str, ...]]]:
    return [(issue.code, issue.path, issue.details) for issue in report.issues]


def test_check_draft_consumes_passed_state_without_reading(
    draft_factory: DraftFactory, marker_reads: list[Path]
) -> None:
    """传入状态：不读取 marker；报告与磁盘 marker 同状态的报告逐项一致。"""
    root = draft_factory({"index.md": PAGE_INDEX, "pages/a.md": PAGE_A})
    generated = _registered(ConclusionsState.GENERATED)

    report = check_draft(root, marker_state=generated)
    assert marker_reads == []

    # 磁盘 marker 改写为同一状态后自读：两条路径的报告必须一致（含结论必需项）。
    (root / ".draft" / "draft.json").write_text(
        managed_marker_text(conclusions_state=ConclusionsState.GENERATED), encoding="utf-8"
    )
    expected = check_draft(root)
    assert marker_reads == [root]
    assert _issue_tuples(report) == _issue_tuples(expected)


def test_check_draft_default_with_corrupt_marker_reads_twice_and_reports_invalid(
    draft_factory: DraftFactory, marker_reads: list[Path]
) -> None:
    """缺省路径损坏 marker 恰好读取两次：自身失败 1 次 + tolerant 自读报 issue 1 次。

    计划未决事项 4 保留的现状语义：marker 损坏时 check_draft 开头的默认自读失败
    不计 issue，由 load_graph_tolerant 接收 None 后再自读一次并把 format.invalid
    记入报告。该语义与传入状态路径无关，本用例钉住读取次数与报告内容。
    """
    root = draft_factory({"index.md": PAGE_INDEX, "pages/a.md": PAGE_A})
    (root / ".draft" / "draft.json").write_text("{broken json", encoding="utf-8")

    report = check_draft(root)

    assert marker_reads == [root, root]
    assert any(issue.code == CheckCode.FORMAT_INVALID.value for issue in report.issues)


def test_load_tolerant_view_consumes_passed_state_without_reading(
    draft_factory: DraftFactory, marker_reads: list[Path]
) -> None:
    """传入状态：不读取 marker；视图与自读路径的阻碍集合一致。"""
    root = draft_factory(
        {"index.md": PAGE_INDEX, "pages/a.md": PAGE_A},
        root_state=RootState.CANDIDATE,
    )
    candidate = MarkerState(
        version=FORMAT_VERSION,
        root_state=RootState.CANDIDATE,
        conclusions_state=ConclusionsState.NOT_GENERATED,
    )

    threaded = load_tolerant_view(root, marker_state=candidate)
    assert marker_reads == []

    direct = load_tolerant_view(root)
    assert marker_reads == [root]
    assert threaded.blocking_paths == direct.blocking_paths
    assert set(threaded.graph.pages) == set(direct.graph.pages)


def test_generate_conclusions_dry_run_branch_consumes_snapshot(
    draft_factory: DraftFactory, marker_reads: list[Path]
) -> None:
    """generate-conclusions 预演分支按同一快照单次读取。

    组合 cli.py dry-run 分支自身（快照后调 load_tolerant_view 与
    load_generation_graph）的读取序列，调用顺序与真实分支一致。真实 CLI 入口在
    分支之前还会经 main 的 select_managed_root 门禁读取一次 marker（round-1
    问题 4 已登记为根选择职责，不属于 marker 状态穿透不变量），故本用例只钉住
    分支内部序列：穿透后快照读 1 次 + 两个入口各零读取 = 恰好 1 次。
    """
    from draft.graph import read_marker_snapshot

    root = draft_factory({"index.md": PAGE_INDEX, "pages/a.md": PAGE_A})

    resolved = resolve_draft_root(root)
    marker = read_marker_snapshot(resolved)
    state = marker.state.conclusions_state
    view = load_tolerant_view(resolved, marker_state=marker.state)
    blocking = [
        path
        for path in view.blocking_paths
        if not (state is ConclusionsState.GENERATED and path == CONCLUSIONS_PAGE)
    ]
    assert blocking == []
    graph = load_generation_graph(resolved, marker_state=marker.state)
    assert set(graph.pages) == {Path("index.md"), Path("pages/a.md")}

    assert marker_reads == [root]


def test_registered_write_command_reads_marker_twice(
    draft_factory: DraftFactory, marker_reads: list[Path]
) -> None:
    """registered move 全链 marker 事实恰好读取两次。

    第 1 次是 load_operation_facts 的 snapshot；第 2 次是 execute_plan 提交前
    校验（operations._check）对应用后状态的新鲜读取。加载链内的
    check_draft/load_graph/load_graph_tolerant 消费传入状态，不再各自读取
    （round-15 H1：同一操作计划的 marker 事实不得来自后续 live 读取）。
    提交前校验不得改传 snapshot：GENERATE_CONCLUSIONS 会把 marker 推进为
    generated，校验必须按应用后的所有权分类。
    """
    root = draft_factory({"index.md": PAGE_INDEX, "pages/a.md": PAGE_A})

    move_page(root, Path("pages/a.md"), Path("pages/c.md"))

    assert (root / "pages/c.md").is_file()
    assert marker_reads == [root, root]


def test_candidate_facts_read_marker_once(
    tmp_path: Path, marker_reads: list[Path]
) -> None:
    """candidate facts 链：snapshot 一次，tolerant 视图消费传入状态。"""
    root = tmp_path / "candidate"
    build_valid_candidate(root)

    facts = load_operation_facts(root)

    assert facts.root_state is RootState.CANDIDATE
    assert marker_reads == [root]


def test_adopt_reads_marker_once(tmp_path: Path, marker_reads: list[Path]) -> None:
    """adopt 全链 marker 事实恰好读取一次（preallocate 前的 snapshot）。"""
    root = tmp_path / "candidate"
    build_valid_candidate(root)

    adopt(root)

    assert marker_reads == [root]
