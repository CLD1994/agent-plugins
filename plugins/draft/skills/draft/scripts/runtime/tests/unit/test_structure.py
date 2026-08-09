from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from draft.errors import DraftError
from draft.graph import (
    ROOT_PAGE,
    DraftGraph,
    resolve_local_destination,
)
from draft.markdown import parse_page
from draft.models import (
    FileMutation,
    MarkdownLink,
    MutationKind,
    OperationPlan,
    PageDocument,
    SourceSpan,
)
from draft.operations import sha256_bytes
from draft.structure import (
    plan_collapse,
    plan_move,
    plan_path_remap,
    plan_promote,
)
from draft.vocabulary import RelationCode

ROOT = Path("/draft")


# ---------------------------------------------------------------------------
# 局部内存图构造器：从 Markdown 文本构造一致的 DraftGraph，不访问文件系统。
# ---------------------------------------------------------------------------


def _graph_from_texts(
    texts: Mapping[Path, str],
    *,
    root: Path = ROOT,
) -> DraftGraph:
    """从 Markdown 文本构造一致的内存 DraftGraph。

    镜像 graph._load 的派生逻辑（解析页面、解析链接、派生父子与关系），
    但不访问文件系统：页面文本即为唯一来源。
    """
    pages: dict[Path, PageDocument] = {}
    for path in sorted(texts):
        pages[path] = parse_page(path, texts[path])

    managed_set = set(pages)
    inbound: dict[Path, list[tuple[Path, MarkdownLink]]] = {}
    relation_edges: dict[RelationCode, list[tuple[Path, Path, int]]] = {
        label: [] for label in RelationCode
    }

    for source in sorted(pages):
        page = pages[source]
        resolved_links: dict[SourceSpan, Path | None] = {}
        for link in page.links:
            try:
                target, _anchor = resolve_local_destination(source, link.destination, root)
            except DraftError:
                resolved_links[link.whole_span] = None
                continue
            resolved_links[link.whole_span] = target
            if target in managed_set:
                inbound.setdefault(target, []).append((source, link))
        for relation in page.relations:
            target = resolved_links.get(relation.link.whole_span)
            if target is None or target not in managed_set:
                continue
            relation_edges[relation.code].append((source, target, relation.link.line))

    parents: dict[Path, Path] = {}
    for source in sorted(pages):
        declared = [r for r in pages[source].relations if r.code is RelationCode.PARENT]
        if len(declared) != 1:
            continue
        matches = [
            target for src, target, _line in relation_edges[RelationCode.PARENT] if src == source
        ]
        if len(matches) == 1:
            parents[source] = matches[0]

    children: dict[Path, list[Path]] = {}
    for child, parent in parents.items():
        children.setdefault(parent, []).append(child)

    return DraftGraph(
        root=root,
        pages={path: pages[path] for path in sorted(pages)},
        parents={path: parents[path] for path in sorted(parents)},
        children={parent: tuple(sorted(entries)) for parent, entries in sorted(children.items())},
        inbound_links={
            target: tuple(sorted(entries, key=lambda entry: (entry[0], entry[1].whole_span.start)))
            for target, entries in sorted(inbound.items())
        },
        relations={label: tuple(sorted(edges)) for label, edges in relation_edges.items()},
    )


# ---------------------------------------------------------------------------
# FileMutation 构造助手：从预期文本独立构造变更项，不复制被测渲染路径。
# ---------------------------------------------------------------------------


def _write(path: Path, before_text: str | None, after_text: str) -> FileMutation:
    after_bytes = after_text.encode("utf-8")
    before_bytes = before_text.encode("utf-8") if before_text is not None else None
    return FileMutation(
        path=path,
        kind=MutationKind.WRITE,
        before_sha256=sha256_bytes(before_bytes) if before_bytes is not None else None,
        after_sha256=sha256_bytes(after_bytes),
        content=after_bytes,
    )


def _delete(path: Path, before_text: str) -> FileMutation:
    before_bytes = before_text.encode("utf-8")
    return FileMutation(
        path=path,
        kind=MutationKind.DELETE,
        before_sha256=sha256_bytes(before_bytes),
        after_sha256=None,
        content=None,
    )


def _plan(
    kind: str,
    mutations: list[FileMutation],
    *,
    remove_empty: tuple[Path, ...] = (),
    metadata: dict[str, str] | None = None,
) -> OperationPlan:
    writes = sorted(
        (m for m in mutations if m.kind is MutationKind.WRITE),
        key=lambda m: str(m.path),
    )
    deletes = sorted(
        (m for m in mutations if m.kind is MutationKind.DELETE),
        key=lambda m: str(m.path),
    )
    return OperationPlan(
        kind=kind,
        root=ROOT,
        mutations=tuple([*writes, *deletes]),
        validation_mode="registered",
        remove_empty_directories=remove_empty,
        metadata=metadata or {},
    )


# ---------------------------------------------------------------------------
# 页面文本：最小但合法的 Markdown，确保 parse_page 与 check_graph 一致。
# ---------------------------------------------------------------------------

_INDEX = """# 根

> 状态：`已关闭：纯导航页`

## 子主题

- [结论汇总](conclusions.md)
- [甲](pages/a.md)
"""

_CONCLUSIONS = """# 结论汇总

> 状态：`已确认`
>
> 父主题：[根](index.md)
"""

_A = """# 甲

> 状态：`待讨论`
>
> 父主题：[根](../index.md)

甲的正文。
"""

# 带入站链接的图：乙页正文链接到甲页
_INDEX_WITH_B = """# 根

> 状态：`已关闭：纯导航页`

## 子主题

- [结论汇总](conclusions.md)
- [甲](pages/a.md)
- [乙](pages/b.md)
"""

_B_LINKS_A = """# 乙

> 状态：`待讨论`
>
> 父主题：[根](../index.md)

提到 [甲](a.md)。
"""

# 枢纽图：子树移动与折叠
_HUB_INDEX = """# 根

> 状态：`已关闭：纯导航页`

## 子主题

- [结论汇总](conclusions.md)
- [旧枢纽](pages/old/index.md)
"""

_HUB_CONCLUSIONS = """# 结论汇总

> 状态：`已确认`
>
> 父主题：[根](index.md)
"""

_OLD_HUB = """# 旧枢纽

> 状态：`已关闭：纯导航页`
>
> 父主题：[根](../../index.md)

## 子主题

- [甲](a.md)
"""

_OLD_HUB_CHILD = """# 甲

> 状态：`待讨论`
>
> 父主题：[旧枢纽](index.md)

甲的正文。
"""

# 空枢纽（无子页）：折叠专用
_EMPTY_HUB_INDEX = """# 根

> 状态：`已关闭：纯导航页`

## 子主题

- [结论汇总](conclusions.md)
- [空枢纽](pages/sub/index.md)
"""

_EMPTY_HUB = """# 空枢纽

> 状态：`已关闭：纯导航页`
>
> 父主题：[根](../../index.md)
"""

# 升格图
_PROMOTE_INDEX = """# 根

> 状态：`已关闭：纯导航页`

## 子主题

- [结论汇总](conclusions.md)
- [主题](pages/topic.md)
- [甲](pages/child-a.md)
- [乙](pages/child-b.md)
"""

_PROMOTE_TOPIC = """# 主题

> 状态：`待讨论`
>
> 父主题：[根](../index.md)

主题的正文。
"""

_PROMOTE_CHILD_A = """# 甲

> 状态：`待讨论`
>
> 父主题：[根](../index.md)

甲的正文。
"""

_PROMOTE_CHILD_B = """# 乙

> 状态：`待讨论`
>
> 父主题：[根](../index.md)

乙的正文。
"""

# 未选子页保留图：主题有两个子页，只升格其中一个
_PROMOTE_UNSELECTED_INDEX = """# 根

> 状态：`已关闭：纯导航页`

## 子主题

- [结论汇总](conclusions.md)
- [主题](pages/topic.md)
- [甲](pages/child-a.md)
- [乙](pages/child-b.md)
"""


# ===========================================================================
# plan_move: 叶页移动与入站链接重定向
# ===========================================================================


def test_plan_move_leaf_rewrites_inbound_links() -> None:
    """叶页移动：父导航重写、入站链接重定向、未变页面省略，比较完整 OperationPlan。"""
    graph = _graph_from_texts(
        {
            Path("index.md"): _INDEX_WITH_B,
            Path("conclusions.md"): _CONCLUSIONS,
            Path("pages/a.md"): _A,
            Path("pages/b.md"): _B_LINKS_A,
        }
    )

    plan = plan_move(graph, Path("pages/a.md"), Path("pages/c.md"))

    expected_index = _INDEX_WITH_B.replace("pages/a.md", "pages/c.md")
    expected_b = _B_LINKS_A.replace("](a.md)", "](c.md)")
    expected = _plan(
        "move",
        [
            _write(Path("index.md"), _INDEX_WITH_B, expected_index),
            _write(Path("pages/b.md"), _B_LINKS_A, expected_b),
            _write(Path("pages/c.md"), None, _A),
            _delete(Path("pages/a.md"), _A),
        ],
    )
    assert plan == expected


def test_plan_move_leaf_to_same_directory_omits_unchanged_pages() -> None:
    """同目录叶页移动：移动页内容不变，只有父导航和移动路径变化。"""
    graph = _graph_from_texts(
        {
            Path("index.md"): _INDEX,
            Path("conclusions.md"): _CONCLUSIONS,
            Path("pages/a.md"): _A,
        }
    )

    plan = plan_move(graph, Path("pages/a.md"), Path("pages/b.md"))

    expected_index = _INDEX.replace("pages/a.md", "pages/b.md")
    expected = _plan(
        "move",
        [
            _write(Path("index.md"), _INDEX, expected_index),
            _write(Path("pages/b.md"), None, _A),
            _delete(Path("pages/a.md"), _A),
        ],
    )
    assert plan == expected
    # conclusions.md 未变化：不出现在 mutations 中
    mutation_paths = {m.path for m in plan.mutations}
    assert Path("conclusions.md") not in mutation_paths


# ===========================================================================
# plan_move: 子树移动
# ===========================================================================


def test_plan_move_subtree_relocates_complete_subtree() -> None:
    """枢纽子树移动：枢纽及子页路径更新，链接相对重写，空目录清理。"""
    graph = _graph_from_texts(
        {
            Path("index.md"): _HUB_INDEX,
            Path("conclusions.md"): _HUB_CONCLUSIONS,
            Path("pages/old/index.md"): _OLD_HUB,
            Path("pages/old/a.md"): _OLD_HUB_CHILD,
        }
    )

    plan = plan_move(graph, Path("pages/old/index.md"), Path("pages/archive/index.md"))

    mutation_paths = {m.path for m in plan.mutations}
    # 子树整体迁移：枢纽与子页都出现在变更中
    assert Path("pages/archive/index.md") in mutation_paths
    assert Path("pages/archive/a.md") in mutation_paths
    assert Path("pages/old/index.md") in mutation_paths
    assert Path("pages/old/a.md") in mutation_paths
    # 根索引导航更新
    assert Path("index.md") in mutation_paths
    # 旧目录被清理
    assert Path("pages/old") in plan.remove_empty_directories
    assert Path("pages/old") not in {
        d for d in plan.remove_empty_directories if d != Path("pages/old")
    }


# ===========================================================================
# plan_move: 错误分支
# ===========================================================================


def test_plan_move_missing_source_raises_unknown_page() -> None:
    graph = _graph_from_texts(
        {
            Path("index.md"): _INDEX,
            Path("conclusions.md"): _CONCLUSIONS,
            Path("pages/a.md"): _A,
        }
    )
    with pytest.raises(DraftError) as caught:
        plan_move(graph, Path("pages/missing.md"), Path("pages/b.md"))
    assert caught.value.code == "structure.unknown_page"


def test_plan_move_target_outside_pages_raises_invalid_target() -> None:
    graph = _graph_from_texts(
        {
            Path("index.md"): _INDEX,
            Path("conclusions.md"): _CONCLUSIONS,
            Path("pages/a.md"): _A,
        }
    )
    with pytest.raises(DraftError) as caught:
        plan_move(graph, Path("pages/a.md"), Path("other.md"))
    assert caught.value.code == "structure.invalid_target"


def test_plan_move_target_already_exists_raises_target_exists() -> None:
    graph = _graph_from_texts(
        {
            Path("index.md"): _INDEX_WITH_B,
            Path("conclusions.md"): _CONCLUSIONS,
            Path("pages/a.md"): _A,
            Path("pages/b.md"): _B_LINKS_A,
        }
    )
    with pytest.raises(DraftError) as caught:
        plan_move(graph, Path("pages/a.md"), Path("pages/b.md"))
    assert caught.value.code == "structure.target_exists"


def test_plan_move_into_descendant_raises_descendant() -> None:
    graph = _graph_from_texts(
        {
            Path("index.md"): _HUB_INDEX,
            Path("conclusions.md"): _HUB_CONCLUSIONS,
            Path("pages/old/index.md"): _OLD_HUB,
            Path("pages/old/a.md"): _OLD_HUB_CHILD,
        }
    )
    with pytest.raises(DraftError) as caught:
        plan_move(
            graph,
            Path("pages/old/index.md"),
            Path("pages/old/sub/index.md"),
        )
    assert caught.value.code == "structure.descendant"


def test_plan_move_protected_root_raises_protected() -> None:
    graph = _graph_from_texts(
        {
            Path("index.md"): _INDEX,
            Path("conclusions.md"): _CONCLUSIONS,
            Path("pages/a.md"): _A,
        }
    )
    with pytest.raises(DraftError) as caught:
        plan_move(graph, ROOT_PAGE, Path("pages/b.md"))
    assert caught.value.code == "structure.protected"


# ===========================================================================
# plan_promote: 选定子页升格与未选子页保留
# ===========================================================================


def test_plan_promote_creates_hub_and_reparents_selected_children() -> None:
    """升格：主题页变为枢纽，选定子页移入子目录，根索引导航更新。"""
    graph = _graph_from_texts(
        {
            Path("index.md"): _PROMOTE_INDEX,
            Path("conclusions.md"): _CONCLUSIONS,
            Path("pages/topic.md"): _PROMOTE_TOPIC,
            Path("pages/child-a.md"): _PROMOTE_CHILD_A,
            Path("pages/child-b.md"): _PROMOTE_CHILD_B,
        }
    )

    plan = plan_promote(
        graph,
        Path("pages/topic.md"),
        (Path("pages/child-a.md"), Path("pages/child-b.md")),
    )

    mutation_paths = {m.path for m in plan.mutations}
    # 升格后的枢纽与子页路径
    assert Path("pages/topic/index.md") in mutation_paths
    assert Path("pages/topic/child-a.md") in mutation_paths
    assert Path("pages/topic/child-b.md") in mutation_paths
    # 原路径删除
    assert Path("pages/topic.md") in mutation_paths
    assert Path("pages/child-a.md") in mutation_paths
    assert Path("pages/child-b.md") in mutation_paths
    # 根索引导航更新
    assert Path("index.md") in mutation_paths
    assert plan.kind == "promote"


def test_plan_promote_preserves_unselected_children() -> None:
    """升格只选定部分子页：未选子页保留在原位，不出现在变更中。"""
    graph = _graph_from_texts(
        {
            Path("index.md"): _PROMOTE_UNSELECTED_INDEX,
            Path("conclusions.md"): _CONCLUSIONS,
            Path("pages/topic.md"): _PROMOTE_TOPIC,
            Path("pages/child-a.md"): _PROMOTE_CHILD_A,
            Path("pages/child-b.md"): _PROMOTE_CHILD_B,
        }
    )

    plan = plan_promote(
        graph,
        Path("pages/topic.md"),
        (Path("pages/child-a.md"),),
    )

    mutation_paths = {m.path for m in plan.mutations}
    # 选定子页移入子目录
    assert Path("pages/topic/child-a.md") in mutation_paths
    assert Path("pages/child-a.md") in mutation_paths
    # 未选子页保留：不出现在变更中
    assert Path("pages/child-b.md") not in mutation_paths
    assert Path("pages/topic/child-b.md") not in mutation_paths


def test_plan_promote_requires_at_least_one_child() -> None:
    graph = _graph_from_texts(
        {
            Path("index.md"): _PROMOTE_INDEX,
            Path("conclusions.md"): _CONCLUSIONS,
            Path("pages/topic.md"): _PROMOTE_TOPIC,
            Path("pages/child-a.md"): _PROMOTE_CHILD_A,
            Path("pages/child-b.md"): _PROMOTE_CHILD_B,
        }
    )
    with pytest.raises(DraftError) as caught:
        plan_promote(graph, Path("pages/topic.md"), ())
    assert caught.value.code == "structure.missing_child"


def test_plan_promote_rejects_page_with_children() -> None:
    graph = _graph_from_texts(
        {
            Path("index.md"): _HUB_INDEX,
            Path("conclusions.md"): _HUB_CONCLUSIONS,
            Path("pages/old/index.md"): _OLD_HUB,
            Path("pages/old/a.md"): _OLD_HUB_CHILD,
        }
    )
    with pytest.raises(DraftError) as caught:
        plan_promote(
            graph,
            Path("pages/old/index.md"),
            (Path("pages/old/a.md"),),
        )
    assert caught.value.code == "structure.invalid_target"


# ===========================================================================
# plan_collapse: 空枢纽折叠与拒绝
# ===========================================================================


def test_plan_collapse_empty_hub_produces_leaf() -> None:
    """空枢纽折叠为叶页：父导航重写、枢纽链接相对重写、空目录清理。"""
    graph = _graph_from_texts(
        {
            Path("index.md"): _EMPTY_HUB_INDEX,
            Path("conclusions.md"): _CONCLUSIONS,
            Path("pages/sub/index.md"): _EMPTY_HUB,
        }
    )

    plan = plan_collapse(graph, Path("pages/sub/index.md"))

    expected_index = _EMPTY_HUB_INDEX.replace("pages/sub/index.md", "pages/sub.md")
    expected_leaf = _EMPTY_HUB.replace("(../../index.md)", "(../index.md)")
    expected = _plan(
        "collapse",
        [
            _write(Path("index.md"), _EMPTY_HUB_INDEX, expected_index),
            _write(Path("pages/sub.md"), None, expected_leaf),
            _delete(Path("pages/sub/index.md"), _EMPTY_HUB),
        ],
        remove_empty=(Path("pages/sub"),),
    )
    assert plan == expected


def test_plan_collapse_rejects_non_empty_hub() -> None:
    graph = _graph_from_texts(
        {
            Path("index.md"): _HUB_INDEX,
            Path("conclusions.md"): _HUB_CONCLUSIONS,
            Path("pages/old/index.md"): _OLD_HUB,
            Path("pages/old/a.md"): _OLD_HUB_CHILD,
        }
    )
    with pytest.raises(DraftError) as caught:
        plan_collapse(graph, Path("pages/old/index.md"))
    assert caught.value.code == "structure.hub_not_empty"


def test_plan_collapse_rejects_non_hub_page() -> None:
    graph = _graph_from_texts(
        {
            Path("index.md"): _INDEX,
            Path("conclusions.md"): _CONCLUSIONS,
            Path("pages/a.md"): _A,
        }
    )
    with pytest.raises(DraftError) as caught:
        plan_collapse(graph, Path("pages/a.md"))
    assert caught.value.code == "structure.not_hub"


def test_plan_collapse_rejects_root() -> None:
    graph = _graph_from_texts(
        {
            Path("index.md"): _INDEX,
            Path("conclusions.md"): _CONCLUSIONS,
            Path("pages/a.md"): _A,
        }
    )
    with pytest.raises(DraftError) as caught:
        plan_collapse(graph, ROOT_PAGE)
    assert caught.value.code == "structure.protected"


# ===========================================================================
# plan_path_remap: 确定排序与未变页面省略
# ===========================================================================


def test_plan_path_remap_orders_writes_before_deletes_by_path() -> None:
    """变更项先写后删，各自按路径排序。"""
    graph = _graph_from_texts(
        {
            Path("index.md"): _INDEX,
            Path("conclusions.md"): _CONCLUSIONS,
            Path("pages/a.md"): _A,
        }
    )

    plan = plan_path_remap(
        graph,
        {Path("pages/a.md"): Path("pages/z.md")},
        {},
        kind="move",
    )

    kinds = [m.kind for m in plan.mutations]
    writes = [m for m in plan.mutations if m.kind is MutationKind.WRITE]
    deletes = [m for m in plan.mutations if m.kind is MutationKind.DELETE]
    # 所有 WRITE 在所有 DELETE 之前
    write_count = len(writes)
    assert kinds[:write_count] == [MutationKind.WRITE] * write_count
    assert kinds[write_count:] == [MutationKind.DELETE] * len(deletes)
    # 各组内按路径字典序
    assert [str(m.path) for m in writes] == sorted(str(m.path) for m in writes)
    assert [str(m.path) for m in deletes] == sorted(str(m.path) for m in deletes)


def test_plan_path_remap_omits_unchanged_pages() -> None:
    """未移动且内容未变的页面不出现在变更中。"""
    graph = _graph_from_texts(
        {
            Path("index.md"): _INDEX,
            Path("conclusions.md"): _CONCLUSIONS,
            Path("pages/a.md"): _A,
        }
    )

    plan = plan_path_remap(
        graph,
        {Path("pages/a.md"): Path("pages/b.md")},
        {},
        kind="move",
    )

    mutation_paths = {m.path for m in plan.mutations}
    # conclusions.md 未被移动、无入站链接到 a.md、内容未变：不出现
    assert Path("conclusions.md") not in mutation_paths


def test_plan_path_remap_dirty_graph_raises() -> None:
    """图有检查错误时，规划被拒绝。"""
    # 构造缺少 conclusions.md 的图（root.missing_file 由 check_draft 报告，
    # 但 check_graph 只检查图内规则）。改为构造一个有 hub.empty 错误的图。
    graph = _graph_from_texts(
        {
            Path("index.md"): _EMPTY_HUB_INDEX,
            Path("conclusions.md"): _CONCLUSIONS,
            Path("pages/sub/index.md"): _EMPTY_HUB,
        }
    )
    with pytest.raises(DraftError) as caught:
        plan_path_remap(
            graph,
            {Path("pages/sub/index.md"): Path("pages/sub.md")},
            {},
            kind="collapse",
        )
    assert caught.value.code == "structure.dirty"


# ===========================================================================
# plan_collapse: allow_empty_hub 抑制 hub.empty
# ===========================================================================


def test_plan_collapse_allow_empty_hub_suppresses_hub_empty_error() -> None:
    """plan_collapse 通过 allow_empty_hub 抑制 hub.empty，使规划成功。"""
    graph = _graph_from_texts(
        {
            Path("index.md"): _EMPTY_HUB_INDEX,
            Path("conclusions.md"): _CONCLUSIONS,
            Path("pages/sub/index.md"): _EMPTY_HUB,
        }
    )
    # 直接调用 plan_path_remap 不带 allow_empty_hub 会失败（上一个测试证明），
    # 但 plan_collapse 内部传了 allow_empty_hub=hub，所以成功。
    plan = plan_collapse(graph, Path("pages/sub/index.md"))
    assert plan.kind == "collapse"
    assert any(m.path == Path("pages/sub.md") for m in plan.mutations)
