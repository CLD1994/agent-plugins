"""原子叶子页面创建：校验新叶子路径、生成页面骨架并原子更新父导航。"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from pathlib import Path

from draft.errors import DraftError
from draft.graph import (
    PAGES_DIR,
    DraftGraph,
)
from draft.markdown import (
    append_parent_navigation,
    insert_navigation_before_conclusions,
    parse_page,
)
from draft.models import (
    FileMutation,
    MutationKind,
    OperationKind,
    OperationPlan,
    OperationResult,
    RootState,
    StatusCode,
)
from draft.observations import ObservationManifest, missing_observation
from draft.operations import sha256_bytes
from draft.paths import physical_parent, relative_markdown_destination
from draft.plan_effects import PlanAction, PlanEffect
from draft.vocabulary import RelationCode, relation_label, status_label


@dataclass(frozen=True)
class ResolvedNewLeaf:
    root: Path
    relative: Path


def resolve_new_leaf_operand(root: Path, value: str) -> ResolvedNewLeaf:
    """校验新叶子页面路径；语法非法时抛稳定路径错误。"""
    if value.startswith("/") or "\\" in value:
        raise DraftError("path.escape", Path(value))
    raw_parts = value.split("/")
    if not raw_parts or raw_parts[0] != PAGES_DIR.name:
        raise DraftError("structure.invalid_target", Path(value))
    if any(part in ("", ".", "..") for part in raw_parts):
        raise DraftError("path.escape", Path(value))
    if not value.endswith(".md"):
        raise DraftError("path.not_markdown", Path(value))
    if raw_parts[-1] == "index.md":
        raise DraftError("structure.invalid_target", Path(value))
    relative = Path(*raw_parts)
    return ResolvedNewLeaf(root=root, relative=relative)


def _validate_title(title: str) -> None:
    if not title or any(ch in title for ch in ("\r", "\n")):
        raise DraftError("create.invalid_title")
    for ch in title:
        if unicodedata.category(ch).startswith("C"):
            raise DraftError("create.invalid_title")


def plan_create_page(
    graph: DraftGraph,
    path: Path,
    *,
    title: str,
    status: StatusCode,
    root_state: RootState,
    observations: ObservationManifest,
) -> OperationPlan:
    """构造创建计划：CREATE 新页 + MODIFY 父导航，原子同计划。"""
    _validate_title(title)
    if status not in (StatusCode.PENDING, StatusCode.PROPOSED):
        raise DraftError("create.invalid_status", path)
    if path in graph.pages:
        raise DraftError("structure.target_exists", path)

    parent = physical_parent(path)
    if parent is None or parent not in graph.pages:
        raise DraftError("structure.missing_parent", path)
    parent_page = graph.pages[parent]

    parent_link = relative_markdown_destination(path, parent, None)
    page_text = (
        f"# {title}\n\n"
        f"> 状态：`{status_label(status)}`\n>\n"
        f"> 父主题：[{relation_label(RelationCode.PARENT)}]({parent_link})\n\n正文。\n"
    )
    page_bytes = page_text.encode("utf-8")
    # 证明标题转义正确：解析后标题等于输入文本。
    document = parse_page(path, page_text)
    if document.title != title:
        raise DraftError("create.title_roundtrip", path)

    from draft.graph import CONCLUSIONS_PAGE, ROOT_PAGE

    if parent == ROOT_PAGE:
        parent_after = insert_navigation_before_conclusions(
            parent_page.text,
            title,
            path.as_posix(),
            conclusions_target=CONCLUSIONS_PAGE,
        )
        if parent_after == parent_page.text:
            parent_after = append_parent_navigation(parent_page.text, title, path.as_posix())
    else:
        parent_after = append_parent_navigation(parent_page.text, title, path.as_posix())
    parent_bytes = parent_after.encode("utf-8")

    mutations = [
        FileMutation(path, MutationKind.WRITE, None, sha256_bytes(page_bytes), page_bytes),
        FileMutation(
            parent,
            MutationKind.WRITE,
            sha256_bytes(parent_page.text.encode("utf-8")),
            sha256_bytes(parent_bytes),
            parent_bytes,
        ),
    ]
    effects = (
        PlanEffect(action=PlanAction.CREATE, path=path),
        PlanEffect(action=PlanAction.MODIFY, path=parent),
    )
    observations = ObservationManifest(entries=(*observations.entries, missing_observation(path)))
    return OperationPlan(
        kind=OperationKind.CREATE,
        root=graph.root,
        mutations=tuple(mutations),
        root_state=root_state,
        observations=observations,
        effects=effects,
    )


def create_page(
    root: Path,
    page: Path,
    *,
    title: str,
    status: StatusCode,
) -> OperationResult:
    """原子创建叶子页面；前置推进由 CLI 编排统一完成。

    candidate 根按操作级门禁执行：只拒绝目标/父页不可判定，不要求全根健康。
    """
    from draft.operation_facts import load_operation_facts
    from draft.operations import execute_plan
    from draft.paths import resolve_draft_root

    resolved = resolve_draft_root(root)
    facts = load_operation_facts(resolved)
    blocking = set(facts.blocking_paths)
    if page in blocking:
        raise DraftError("structure.target_exists", page)
    parent = physical_parent(page)
    if parent in blocking:
        raise DraftError("structure.missing_parent", page)
    plan = plan_create_page(
        facts.graph,
        page,
        title=title,
        status=status,
        root_state=facts.root_state,
        observations=facts.observations,
    )
    return execute_plan(plan)
