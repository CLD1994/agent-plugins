from __future__ import annotations

import json
from contextlib import suppress
from pathlib import Path

from draft.checker import check_draft
from draft.diagnostic_codes import RootCode
from draft.errors import DraftError
from draft.graph import (
    FORMAT_VERSION,
    INTERNAL_DIR,
    MARKER_PATH,
    read_marker,
)
from draft.models import (
    FileMutation,
    MutationKind,
    OperationKind,
    OperationPlan,
    OperationResult,
    RootState,
)
from draft.observations import graph_observations
from draft.operations import (
    durable_write,
    fsync_directory,
    reject_symlink_components,
)
from draft.paths import resolve_draft_root
from draft.vocabulary import ConclusionsState


def marker_bytes(
    root_state: RootState,
    conclusions_state: ConclusionsState = ConclusionsState.NOT_GENERATED,
) -> bytes:
    """构造精确三键正式 marker；root_state 与 conclusions_state 正交。"""
    payload = {
        "format_version": FORMAT_VERSION,
        "root_state": root_state.value,
        "conclusions_state": conclusions_state.value,
    }
    return (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")


def create_marker_parent_durably(root: Path) -> None:
    (root / INTERNAL_DIR).mkdir(exist_ok=True)
    fsync_directory(root)


def _index_template(title: str) -> str:
    return f"# {title}\n\n> 状态：`已关闭：纯导航页，不表达独立结论`\n\n## 子主题\n"


def initialize(root: Path, *, root_state: RootState = RootState.REGISTERED) -> None:
    """创建全新受管根：``init ROOT`` 为 registered，``init --candidate ROOT`` 为 candidate。

    使用耐久发布 + 可证明回滚协议：任何失败都不留下看似成功的半初始化根。
    不再经由 candidate check + provisional marker 绕一圈创建正式根。
    """
    target = resolve_draft_root(root, allow_missing=True)
    pre_existing = target.exists()
    if pre_existing:
        if target.is_symlink() or not target.is_dir():
            raise DraftError(RootCode.INVALID.value, target)
        if any(target.iterdir()):
            raise DraftError("init.not_empty", target)
    created: list[Path] = []
    try:
        if not pre_existing:
            target.mkdir(parents=True)
            created.append(target)
        index = target / "index.md"
        index.write_text(_index_template(target.name), encoding="utf-8")
        created.append(index)
        pages = target / "pages"
        pages.mkdir()
        created.append(pages)
        create_marker_parent_durably(target)
        created.append(target / INTERNAL_DIR)
        durable_write(target / MARKER_PATH, marker_bytes(root_state))
        report = check_draft(target)
        if report.errors:
            codes = tuple(issue.code for issue in report.errors)
            raise DraftError("init.invalid_template", target, details=codes)
    except Exception:
        for path in sorted(created, key=lambda entry: len(entry.parts), reverse=True):
            if path.is_symlink() or path.is_file():
                path.unlink(missing_ok=True)
            elif path.is_dir():
                with suppress(OSError):
                    path.rmdir()
        raise


def adopt(root: Path) -> OperationResult:
    """把 root_state=candidate 的受管根原子推进为 registered。

    只接受 candidate marker：未受管目录（无 marker）与已登记根都拒绝。完整候选检查
    通过后以 kind="adopt" 的普通计划执行，marker 的 candidate→registered 替换作为
    唯一受管根 mutation 原子提交；恢复按 OperationKind.ADOPT 单向收敛。
    """
    from draft.graph import load_graph_tolerant
    from draft.operations import execute_plan, sha256_bytes

    target = resolve_draft_root(root)
    final_marker = target / MARKER_PATH
    reject_symlink_components(target, MARKER_PATH)
    if not final_marker.is_file():
        raise DraftError("adopt.invalid_candidate", target)
    state = read_marker(target)
    if state.root_state is not RootState.CANDIDATE:
        raise DraftError("adopt.already_registered", target)
    if (target / INTERNAL_DIR / "operation.lock").exists():
        raise DraftError("adopt.locked", target)
    # 完整候选检查（无过滤无分页）：candidate 与 registered 共享检查协议。
    report = check_draft(target)
    if report.errors:
        raise DraftError(
            "adopt.invalid_candidate",
            target,
            details=tuple(issue.code for issue in report.errors),
        )
    before_bytes = final_marker.read_bytes()
    after_bytes = marker_bytes(RootState.REGISTERED, state.conclusions_state)
    graph = load_graph_tolerant(target).graph
    plan = OperationPlan(
        kind=OperationKind.ADOPT,
        root=target,
        mutations=(
            FileMutation(
                MARKER_PATH,
                MutationKind.WRITE,
                sha256_bytes(before_bytes),
                sha256_bytes(after_bytes),
                after_bytes,
            ),
        ),
        root_state=RootState.CANDIDATE,
        observations=graph_observations(graph),
    )
    return execute_plan(plan)
