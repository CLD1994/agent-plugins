from __future__ import annotations

import json
from contextlib import suppress
from pathlib import Path

from draft.checker import check_draft
from draft.diagnostic_codes import CheckCode, RootCode
from draft.errors import DraftError
from draft.graph import (
    FORMAT_VERSION,
    INTERNAL_DIR,
    MARKER_PATH,
)
from draft.models import (
    FileMutation,
    MutationKind,
    OperationKind,
    OperationPlan,
    OperationResult,
    RootState,
)
from draft.operations import (
    durable_write,
    fsync_directory,
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
        durable_write(target, MARKER_PATH, marker_bytes(root_state))
        # marker 也纳入通用清理集合：最终检查返回错误或抛异常等任何确定性失败都必须
        # 回滚 marker，不能把"有效 marker + 缺失骨架"伪装成已完成初始化（规格 §6）。
        # 进程级中断（BaseException）不进入 except，保留已完成的根。
        created.append(target / MARKER_PATH)
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
    from draft.graph import load_graph_tolerant, read_marker_snapshot
    from draft.operations import execute_plan, sha256_bytes

    target = resolve_draft_root(root)
    # marker 身份、before bytes、state 与观察摘要全部来自同一次 snapshot（round-15 H1）；
    # .draft/marker symlink 与非普通节点由 snapshot 读取器分类，不再有 is_file/read_bytes
    # 旁路。ENOENT -> format.missing，不得改写为 adopt.invalid_candidate。
    snapshot = read_marker_snapshot(target)
    state = snapshot.state
    if state.root_state is not RootState.CANDIDATE:
        raise DraftError("adopt.already_registered", target)
    if (target / INTERNAL_DIR / "operation.lock").exists():
        raise DraftError("adopt.locked", target)
    # 完整候选检查（无过滤无分页）：candidate 与 registered 共享检查协议；预检查先
    # 结束独立 maintenance operation，再开始 adopt（spec §14/§16）。
    report = check_draft(target, marker_state=state, maintain_index=True)
    if report.errors:
        raise DraftError(
            "adopt.invalid_candidate",
            target,
            details=tuple(issue.code for issue in report.errors),
        )
    before_bytes = snapshot.raw_bytes
    after_bytes = marker_bytes(RootState.REGISTERED, state.conclusions_state)
    if sha256_bytes(before_bytes) == sha256_bytes(after_bytes):
        # 结构性排除 before==after：不得写入前后摘要相同的 adopt journal。
        raise DraftError(CheckCode.FORMAT_INVALID.value, target / MARKER_PATH)
    graph = load_graph_tolerant(target, marker_state=state).graph
    from draft.observations import (
        Observation,
        ObservationManifest,
        graph_observations,
        internal_protocol_observations,
    )

    # marker 由 snapshot 生成，internal_protocol_observations 已跳过 MARKER_PATH；
    # 合并后 marker 恰好一项，不能靠重复读取后 setdefault 隐藏。
    merged: dict[Path, Observation] = {}
    for observation in (
        *graph_observations(graph, marker=snapshot).entries,
        *internal_protocol_observations(graph).entries,
    ):
        merged.setdefault(observation.path, observation)
    observations = ObservationManifest(entries=tuple(merged.values()))
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
        observations=observations,
    )
    return execute_plan(plan)
