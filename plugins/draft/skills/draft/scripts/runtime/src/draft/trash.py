from __future__ import annotations

import dataclasses
import json
import uuid
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from draft.checker import check_draft
from draft.diagnostic_codes import CheckCode
from draft.errors import DraftError
from draft.graph import (
    CONCLUSIONS_PAGE,
    INTERNAL_DIR,
    ROOT_PAGE,
    DraftGraph,
    load_graph,
    load_graph_tolerant,
    resolve_local_destination,
)
from draft.markdown import (
    append_parent_navigation,
    canonical_parent_navigation,
    insert_navigation_before_conclusions,
    navigation_removal_edits,
    parse_page,
    rewrite_link_destinations,
)
from draft.models import (
    FileMutation,
    MarkdownLink,
    MutationKind,
    OperationKind,
    OperationPhase,
    OperationPlan,
    OperationResult,
    RootState,
    SourceSpan,
    TrashResult,
)
from draft.observations import ObservationManifest
from draft.operations import (
    Journal,
    TrashDeadlines,
    acquire_lock,
    clean_recovery_copies,
    execute_plan,
    execute_preallocated_plan,
    normalize_utc,
    operation_journal_path,
    preallocate_journal,
    read_journal,
    read_lock,
    release_lock,
    resolve_journal_pending,
    sha256_bytes,
    sha256_file,
    validate_operation_id,
    validate_restore_link,
    with_metadata,
    with_observations,
    with_phase,
    without_metadata,
    write_journal,
)
from draft.paths import relative_markdown_destination, resolve_draft_root
from draft.plan_effects import PlanAction, PlanEffect
from draft.trash_package import (
    OUTGOING_RELATIVE,
    PACKAGE_VERSION,
    VerifiedTrashPackage,
    clean_authoritative_package,
    cleanup_authorization,
    load_verified_package,
)

OPERATIONS_RELATIVE = Path(INTERNAL_DIR) / "operations"


def _read_managed_text(root: Path, relative: Path) -> str:
    """受管理 Markdown 的字节保真读取：保留 CRLF，不触发通用换行规范化。

    通用文本读取会把 CRLF 规范化为 LF；任何参与编辑或摘要证明的页面读取都必须
    基于实际字节，否则未修改内容会被整页改写（只允许改写协议指定跨度）。
    """
    return (root / relative).read_bytes().decode("utf-8")


def _rebase_links(text: str, old_path: Path, new_path: Path, root: Path) -> str:
    """Recompute every local destination of a page moved from old_path to new_path."""
    page = parse_page(new_path, text)
    edits: dict[SourceSpan, str] = {}
    for link in page.links:
        try:
            target, anchor = resolve_local_destination(old_path, link.destination, root)
        except DraftError:
            continue
        destination = relative_markdown_destination(new_path, target, anchor)
        if destination != link.destination:
            edits.setdefault(link.destination_span, destination)
    return rewrite_link_destinations(text, edits)


def _retarget_links(
    text: str, from_page: Path, source_path: Path, target_path: Path, root: Path
) -> str:
    """把 from_page 中解析到 source_path 的链接目标重定向到 target_path（其余不变）。"""
    page = parse_page(from_page, text)
    edits: dict[SourceSpan, str] = {}
    for link in page.links:
        try:
            resolved, anchor = resolve_local_destination(from_page, link.destination, root)
        except DraftError:
            continue
        if resolved != source_path:
            continue
        destination = relative_markdown_destination(from_page, target_path, anchor)
        if destination != link.destination:
            edits.setdefault(link.destination_span, destination)
    return rewrite_link_destinations(text, edits)


def _is_hub(path: Path) -> bool:
    return path.name == "index.md" and path != ROOT_PAGE


def _blockers(graph: DraftGraph, page: Path) -> tuple[str, ...]:
    canonical = canonical_parent_navigation(graph, page)
    blocked: list[tuple[str, int]] = []
    for source, link in graph.inbound_links.get(page, ()):
        if source == CONCLUSIONS_PAGE:
            # 机器结论列表允许过时：其中的入站链接不阻碍回收。
            continue
        if (
            canonical is not None
            and source == graph.parents[page]
            and link.whole_span == canonical.whole_span
        ):
            continue
        blocked.append((str(source), link.whole_span.start))
    for label, edges in graph.relations.items():
        for source, target, _line in edges:
            if target == page:
                blocked.append((f"{source}({label.value})", 0))
    return tuple(f"{source}@{offset}" for source, offset in sorted(set(blocked)))


def trash_plan(
    graph: DraftGraph,
    page: Path,
    *,
    root_state: RootState,
    observations: ObservationManifest,
) -> tuple[OperationPlan, Path]:
    if page in (ROOT_PAGE, CONCLUSIONS_PAGE):
        raise DraftError("trash.protected", page)
    if page not in graph.pages:
        raise DraftError("structure.unknown_page", page)
    if graph.children.get(page):
        raise DraftError("trash.has_children", page)
    blocked = _blockers(graph, page)
    if blocked:
        raise DraftError("trash.inbound_reference", page, details=blocked)

    parent = graph.parents[page]
    parent_page = graph.pages[parent]
    canonical = canonical_parent_navigation(graph, page)
    parent_edits: dict[SourceSpan, str] = {}
    if canonical is not None:
        parent_edits = navigation_removal_edits(parent_page.text, canonical)
    parent_after = rewrite_link_destinations(parent_page.text, parent_edits)

    operation_id = uuid.uuid4().hex
    package_relative = OUTGOING_RELATIVE / operation_id
    payload_relative = package_relative / "payload" / page
    mutations: list[FileMutation] = []
    remove_empty: list[Path] = []

    collapsed_hub: Path | None = None
    parent_final = parent
    parent_final_text = parent_after
    extra_writes: dict[Path, str] = {}
    if (
        _is_hub(parent)
        and len(graph.children.get(parent, ())) == 1
        and graph.children[parent] == (page,)
    ):
        collapsed_hub = parent
        leaf = parent.parent.parent / f"{parent.parent.name}.md"
        parent_final = leaf
        parent_final_text = _rebase_links(parent_after, parent, leaf, graph.root)
        remove_empty.append(parent.parent)

    # 权威包保存页面实际字节：CRLF 页面恢复后必须逐字节一致。
    page_bytes = graph.page_bytes.get(page, graph.pages[page].text.encode("utf-8"))
    package = {
        "version": PACKAGE_VERSION,
        "operation_id": operation_id,
        "page_path": page.as_posix(),
        "page_sha256": sha256_bytes(page_bytes),
        "parent_path": parent_final.as_posix(),
        "parent_sha256": sha256_bytes(parent_final_text.encode("utf-8")),
        "collapsed": collapsed_hub is not None,
        "hub_path": collapsed_hub.as_posix() if collapsed_hub is not None else None,
        "payload": payload_relative.relative_to(package_relative).as_posix(),
    }
    package_bytes = (json.dumps(package, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

    mutations.append(
        FileMutation(
            package_relative / "package.json",
            MutationKind.WRITE,
            None,
            sha256_bytes(package_bytes),
            package_bytes,
        )
    )
    mutations.append(
        FileMutation(
            payload_relative,
            MutationKind.WRITE,
            None,
            sha256_bytes(page_bytes),
            page_bytes,
        )
    )

    if collapsed_hub is not None:
        leaf_bytes = parent_final_text.encode("utf-8")
        mutations.append(
            FileMutation(
                parent_final, MutationKind.WRITE, None, sha256_bytes(leaf_bytes), leaf_bytes
            )
        )
        mutations.append(
            FileMutation(
                collapsed_hub, MutationKind.DELETE, graph.page_digest(collapsed_hub), None, None
            )
        )
        for other_path, other_page in graph.pages.items():
            if other_path in (page, collapsed_hub):
                continue
            edits: dict[SourceSpan, str] = {}
            for link in other_page.links:
                try:
                    target, anchor = resolve_local_destination(
                        other_path, link.destination, graph.root
                    )
                except DraftError:
                    continue
                if target != collapsed_hub:
                    continue
                destination = relative_markdown_destination(other_path, parent_final, anchor)
                if destination != link.destination:
                    edits.setdefault(link.destination_span, destination)
            if edits:
                extra_writes[other_path] = rewrite_link_destinations(other_page.text, edits)
    else:
        extra_writes[parent] = parent_after

    for path, text in extra_writes.items():
        before = graph.pages[path].text.encode("utf-8")
        after = text.encode("utf-8")
        if before == after:
            continue
        mutations.append(
            FileMutation(
                path,
                MutationKind.WRITE,
                graph.page_digest(path),
                sha256_bytes(after),
                after,
            )
        )
    mutations.append(FileMutation(page, MutationKind.DELETE, sha256_bytes(page_bytes), None, None))

    writes = sorted(
        (mutation for mutation in mutations if mutation.kind is MutationKind.WRITE),
        key=lambda mutation: str(mutation.path),
    )
    deletes = sorted(
        (mutation for mutation in mutations if mutation.kind is MutationKind.DELETE),
        key=lambda mutation: str(mutation.path),
    )
    effects: list[PlanEffect] = []
    effects.append(PlanEffect(action=PlanAction.REMOVE, path=page))
    if collapsed_hub is not None:
        effects.append(PlanEffect(action=PlanAction.REMOVE, path=collapsed_hub))
    for path, _text in sorted(extra_writes.items()):
        effects.append(PlanEffect(action=PlanAction.MODIFY, path=path))
    plan = OperationPlan(
        kind=OperationKind.TRASH,
        root=graph.root,
        mutations=tuple([*writes, *deletes]),
        root_state=root_state,
        observations=observations,
        remove_empty_directories=tuple(remove_empty),
        metadata={"operation_id": operation_id},
        effects=tuple(effects),
    )
    return plan, package_relative


def require_retained_restore_window(journal: Journal, now: datetime) -> TrashDeadlines:
    """验证 T 仍为 trash_retained、期限完整且当前仍在恢复窗口内。"""
    if (
        journal.kind is not OperationKind.TRASH
        or journal.phase is not OperationPhase.TRASH_RETAINED
    ):
        raise DraftError("trash.restore_expired", journal.directory)
    deadlines = TrashDeadlines.parse(journal.metadata)
    if now >= deadlines.restore_expires_at:
        raise DraftError("trash.restore_expired", journal.directory)
    return deadlines


def abandon_unlinked_restore(
    root: Path, restore_id: str, *, release_owned_lock: bool = False
) -> None:
    """把调用方尚未关联的 R=allocating 耐久转为 rolled_back，清理分配材料。"""
    journal_path = operation_journal_path(root, restore_id)
    resolve_journal_pending(journal_path)
    if journal_path.is_file():
        journal = read_journal(journal_path)
        if (
            journal.kind is OperationKind.RESTORE
            and journal.phase is OperationPhase.ALLOCATING
            and not journal.mutations
        ):
            write_journal(with_phase(journal, OperationPhase.ROLLED_BACK))
            clean_recovery_copies(journal)
    if release_owned_lock and read_lock(root) == restore_id:
        release_lock(root, restore_id)


def require_no_existing_restore_claim(root: Path, journal: Journal, restore_id: str) -> None:
    """锁内重读 T：已有他方恢复认领不是良性并发，保留 T 与 R1 原样。"""
    existing = journal.metadata.get("restore_operation_id")
    if existing is None:
        return
    if existing == restore_id:
        raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, journal.directory)
    r1_id = validate_operation_id(existing)
    r1_path = operation_journal_path(root, r1_id)
    if not r1_path.is_file():
        raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, journal.directory)
    r1 = read_journal(r1_path)
    if r1.kind is not OperationKind.RESTORE or (
        r1.metadata.get("restores_trash_operation") != journal.operation_id
    ):
        raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, journal.directory)
    # T <-> R1 合法可恢复关联，但 R1 不是本次 R：交给显式恢复。
    raise DraftError(CheckCode.OPERATION_RECOVERY_REQUIRED.value, journal.directory)


def _proven_unapplied_restore(root: Path, journal: Journal) -> bool:
    """证明 R 未应用任何变更且所有受管理文件仍为 before 态。"""
    if any(mutation.applied for mutation in journal.mutations):
        return False
    for mutation in journal.mutations:
        if sha256_file(root / mutation.path) != mutation.before_sha256:
            return False
    return True


class RestoreRecoveryAction(StrEnum):
    """R/T 双日志恢复决策结果（第十轮 H2，第十一轮 H1/M1 闭合）。"""

    VERIFY_AND_COMMIT_CLEANUP = "verify_and_commit_cleanup"
    RESUME_COMMITTED_CLEANUP = "resume_committed_cleanup"
    RESUME_FORWARD = "resume_forward"
    RESUME_ROLLBACK = "resume_rollback"
    ABANDON_UNAPPLIED = "abandon_unapplied"
    INCONSISTENT = "inconsistent"


def restore_recovery_decision(
    *,
    restore_phase: OperationPhase,
    trash_phase: OperationPhase,
    links_match: bool,
    proven_unapplied: bool,
) -> RestoreRecoveryAction:
    """restore 双日志恢复决策（纯函数，唯一权威 R/T 决策来源）。

    完整分区见计划"行为契约一"：关联不匹配一律 INCONSISTENT；只有显式列出的合法
    (R, T) 分区才返回对应动作。中段相位（applying/validating/rolling_back）不再是
    INCONSISTENT，而是交给公开 recovery 入口复用通用 mutation 恢复；RESUME_* 动作
    只能由显式恢复路径执行，正常业务调用不得因 phase 可恢复而吞掉原错误。未列
    组合（如 R=rolled_back,T=rolled_back）一律 INCONSISTENT，不在后续 journal 写入
    时才暴露（第十一轮 M1）。
    """
    if not links_match:
        return RestoreRecoveryAction.INCONSISTENT
    pair = (restore_phase, trash_phase)
    if pair in (
        (OperationPhase.APPLYING, OperationPhase.TRASH_RETAINED),
        (OperationPhase.VALIDATING, OperationPhase.TRASH_RETAINED),
    ):
        return RestoreRecoveryAction.RESUME_FORWARD
    if pair == (OperationPhase.ROLLING_BACK, OperationPhase.TRASH_RETAINED):
        return RestoreRecoveryAction.RESUME_ROLLBACK
    if pair == (OperationPhase.COMPLETED, OperationPhase.TRASH_RETAINED):
        return RestoreRecoveryAction.VERIFY_AND_COMMIT_CLEANUP
    if pair == (OperationPhase.COMPLETED, OperationPhase.ROLLED_BACK):
        return RestoreRecoveryAction.RESUME_COMMITTED_CLEANUP
    if pair == (OperationPhase.ROLLED_BACK, OperationPhase.TRASH_RETAINED):
        return RestoreRecoveryAction.ABANDON_UNAPPLIED
    if (
        restore_phase in (OperationPhase.ALLOCATING, OperationPhase.PREPARED)
        and trash_phase is OperationPhase.TRASH_RETAINED
        and proven_unapplied
    ):
        return RestoreRecoveryAction.ABANDON_UNAPPLIED
    return RestoreRecoveryAction.INCONSISTENT


def build_restore_plan(root: Path, restore_id: str, package: VerifiedTrashPackage) -> OperationPlan:
    """构造恢复计划：页面回写、父/枢纽导航恢复；冲突在此以 operation.* 抛出。

    规划阶段边界把受管页面读取失败（删除、目录化、非 UTF-8）统一转换为
    ``operation.external_change``：规划前的外部变化可安全回滚，进入统一 resolver
    证明 R 未应用后映射为 ``trash.restore_conflict``、解关联并释放锁。不把原始
    FileNotFoundError/IsADirectoryError/UnicodeDecodeError 泄漏给调用方。
    """
    try:
        return _build_restore_plan_impl(root, restore_id, package)
    except DraftError:
        raise
    except (OSError, UnicodeDecodeError) as exc:
        raise DraftError("operation.external_change", package.page_path) from exc


def _build_restore_plan_impl(
    root: Path, restore_id: str, package: VerifiedTrashPackage
) -> OperationPlan:
    """构造恢复计划：页面回写、父/枢纽导航恢复；冲突在此以 operation.* 抛出。

    registered 根在规划前通过完整健康门禁；candidate 根按操作级契约恢复，不要求
    全根健康，但折叠恢复需要重写全部指向叶页的入站链接，不可解析页面仍须阻止。
    """
    from draft.graph import read_marker
    from draft.models import RootState
    from draft.query import load_tolerant_view

    resolved = resolve_draft_root(root)
    root_state = read_marker(resolved).root_state
    page_path = package.page_path
    parent_path = package.parent_path
    hub_path = package.hub_path
    collapsed = package.collapsed
    page_hash = package.page_sha256
    parent_hash = package.parent_sha256
    page_bytes = package.page_bytes

    if (resolved / page_path).exists():
        raise DraftError("operation.validation_failed", page_path, details=("restore_conflict",))
    if sha256_file(resolved / parent_path) != parent_hash:
        raise DraftError("operation.validation_failed", parent_path, details=("restore_conflict",))
    if root_state is RootState.REGISTERED:
        report = check_draft(resolved, include_operation_state=False)
        if report.errors:
            codes = tuple(issue.code for issue in report.errors)
            raise DraftError("operation.validation_failed", resolved, details=codes)
        graph = load_graph(resolved)
    else:
        view = load_tolerant_view(resolved)
        graph = view.graph
        if view.blocking_paths and collapsed:
            # 折叠恢复需重写所有指向叶页的入站链接：不可解析页面可能隐藏链接。
            details = tuple(path.as_posix() for path in view.blocking_paths)
            raise DraftError("operation.validation_failed", resolved, details=details)
    page_title = parse_page(page_path, page_bytes.decode("utf-8")).title

    mutations: list[FileMutation] = []
    mutations.append(FileMutation(page_path, MutationKind.WRITE, None, page_hash, page_bytes))
    if collapsed:
        if hub_path is None:
            raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, package.directory)
        leaf_text = _read_managed_text(root, parent_path)
        hub_text = _rebase_links(leaf_text, parent_path, hub_path, root)
        navigation = relative_markdown_destination(hub_path, page_path, None)
        hub_text = append_parent_navigation(hub_text, page_title, navigation)
        hub_bytes = hub_text.encode("utf-8")
        mutations.append(
            FileMutation(hub_path, MutationKind.WRITE, None, sha256_bytes(hub_bytes), hub_bytes)
        )
        mutations.append(FileMutation(parent_path, MutationKind.DELETE, parent_hash, None, None))
        for other_path, other_page in graph.pages.items():
            edits: dict[SourceSpan, str] = {}
            for link in other_page.links:
                try:
                    target, anchor = resolve_local_destination(
                        other_path, link.destination, graph.root
                    )
                except DraftError:
                    continue
                if target != parent_path:
                    continue
                destination = relative_markdown_destination(other_path, hub_path, anchor)
                if destination != link.destination:
                    edits.setdefault(link.destination_span, destination)
            if edits:
                after = rewrite_link_destinations(other_page.text, edits).encode("utf-8")
                mutations.append(
                    FileMutation(
                        other_path,
                        MutationKind.WRITE,
                        graph.page_digest(other_path),
                        sha256_bytes(after),
                        after,
                    )
                )
    else:
        parent_text = _read_managed_text(root, parent_path)
        navigation = relative_markdown_destination(parent_path, page_path, None)
        if parent_path == ROOT_PAGE:
            # 根导航结论链接由生成器管理，必须保持最后（规格 §12.4）。
            inserted = insert_navigation_before_conclusions(
                parent_text, page_title, navigation, conclusions_target=CONCLUSIONS_PAGE
            )
            if inserted != parent_text:
                restored_text = inserted
            else:
                restored_text = append_parent_navigation(parent_text, page_title, navigation)
        else:
            restored_text = append_parent_navigation(parent_text, page_title, navigation)
        restored_bytes = restored_text.encode("utf-8")
        mutations.append(
            FileMutation(
                parent_path,
                MutationKind.WRITE,
                parent_hash,
                sha256_bytes(restored_bytes),
                restored_bytes,
            )
        )

    writes = sorted(
        (mutation for mutation in mutations if mutation.kind is MutationKind.WRITE),
        key=lambda mutation: str(mutation.path),
    )
    deletes = sorted(
        (mutation for mutation in mutations if mutation.kind is MutationKind.DELETE),
        key=lambda mutation: str(mutation.path),
    )
    observations = _restore_observations(package, graph)
    return OperationPlan(
        kind=OperationKind.RESTORE,
        root=resolved,
        mutations=tuple([*writes, *deletes]),
        root_state=root_state,
        observations=observations,
        metadata={"operation_id": restore_id},
    )


def _restore_observations(package: VerifiedTrashPackage, graph: DraftGraph) -> ObservationManifest:
    """恢复读集观察：页面/目录/状态与权威删除包文件。

    恢复计划读取父/枢纽导航、受影响页面与权威包；并发修改其中任一都会改变读集，
    执行前复核必须拒绝（唯一权威包不变量）。权威包观察直接复用 load_verified_package
    的已验证快照，不重新扫描磁盘：验证后新增的未知条目不能进入基线、不能随整包
    递归删除（回归：验证—观察窗口的并发新增被复核发现并保留现场）。
    """
    from draft.observations import (
        ObservationManifest,
        graph_observations,
    )

    entries = list(graph_observations(graph).entries)
    entries.extend(package.observations.entries)
    return ObservationManifest(entries=tuple(entries))


def verify_restore_after_state(
    root: Path,
    trash_journal: Journal,
    restore_journal: Journal,
    package: VerifiedTrashPackage,
) -> None:
    """逐项精确证明 R=completed 的变更与 T 结构变更对应、活文件匹配 after。

    提交后证明边界把受管页面读取失败（删除、目录化、非 UTF-8）统一转换为
    ``operation.inconsistent_state``：证明证据不足必须保留 completed R、关联与锁，
    不得把提交后证据不足误映射为普通恢复冲突。
    """
    try:
        _verify_restore_after_state_impl(root, trash_journal, restore_journal, package)
    except DraftError:
        raise
    except (OSError, UnicodeDecodeError) as exc:
        raise DraftError(
            CheckCode.OPERATION_INCONSISTENT_STATE.value, restore_journal.directory
        ) from exc


def _verify_restore_after_state_impl(
    root: Path,
    trash_journal: Journal,
    restore_journal: Journal,
    package: VerifiedTrashPackage,
) -> None:
    """逐项精确证明 R=completed 的变更与 T 结构变更对应、活文件匹配 after。

    R 必须与 T 的结构变更（受管理页面，排除内部 package/payload）逐项反向对应：
    - 无重复、无缺失、无额外路径；每项 kind 正确；
    - T 删除（页面/枢纽）-> R 以 WRITE 重建，before=None、after 锚定 T.before；
    - T 新建（collapsed 叶页，before=None）-> R 以 DELETE 移除，before 锚定 T.after、after=None；
    - T 改写（父页/回链重定向，before!=None）-> R 以 WRITE 还原，before 锚定 T.after，
      after 由活文件与结构校验约束（编辑类内容字节不可逆，故不锚定其 after 摘要）。
    最后逐项核对活文件与 after 摘要一致、删除文件不存在。
    """
    if restore_journal.metadata.get("restores_trash_operation") != package.operation_id:
        raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, restore_journal.directory)
    if trash_journal.metadata.get("restore_operation_id") != restore_journal.operation_id:
        raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, restore_journal.directory)

    internal = Path(INTERNAL_DIR)
    t_structural = [
        mutation
        for mutation in trash_journal.mutations
        if not mutation.path.is_relative_to(internal)
    ]
    actual = restore_journal.mutations
    actual_paths = [mutation.path for mutation in actual]
    if len(actual_paths) != len(set(actual_paths)) or len(actual) != len(t_structural):
        raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, restore_journal.directory)
    actual_by_path = {mutation.path: mutation for mutation in actual}

    for t_mutation in t_structural:
        r_mutation = actual_by_path.get(t_mutation.path)
        if r_mutation is None:
            raise DraftError(
                CheckCode.OPERATION_INCONSISTENT_STATE.value, restore_journal.directory
            )
        if t_mutation.kind is MutationKind.DELETE:
            # T 删除 -> R 重建：WRITE，before=None。
            if r_mutation.kind is not MutationKind.WRITE or r_mutation.before_sha256 is not None:
                raise DraftError(
                    CheckCode.OPERATION_INCONSISTENT_STATE.value, restore_journal.directory
                )
            if (
                t_mutation.path == package.page_path
                and r_mutation.after_sha256 != t_mutation.before_sha256
            ):
                # 页面：after 锚定 T.before（=page_sha256，由包内字节给出）。
                raise DraftError(
                    CheckCode.OPERATION_INCONSISTENT_STATE.value, restore_journal.directory
                )
            # 枢纽（collapsed）：after 由独立证明覆盖（子导航追加位置不同，不锚定 T.before）。
        elif t_mutation.before_sha256 is None:
            # T 新建 -> R 移除：DELETE，before 锚定 T.after，after=None。
            if (
                r_mutation.kind is not MutationKind.DELETE
                or r_mutation.before_sha256 != t_mutation.after_sha256
                or r_mutation.after_sha256 is not None
            ):
                raise DraftError(
                    CheckCode.OPERATION_INCONSISTENT_STATE.value, restore_journal.directory
                )
        else:
            # T 改写 -> R 还原：WRITE，before 锚定 T.after。
            if (
                r_mutation.kind is not MutationKind.WRITE
                or r_mutation.before_sha256 != t_mutation.after_sha256
            ):
                raise DraftError(
                    CheckCode.OPERATION_INCONSISTENT_STATE.value, restore_journal.directory
                )

    for mutation in actual:
        if mutation.kind is MutationKind.WRITE and (
            mutation.after_sha256 is None
            or sha256_file(root / mutation.path) != mutation.after_sha256
        ):
            raise DraftError(
                CheckCode.OPERATION_INCONSISTENT_STATE.value, restore_journal.directory
            )
        if mutation.kind is MutationKind.DELETE and (root / mutation.path).exists():
            raise DraftError(
                CheckCode.OPERATION_INCONSISTENT_STATE.value, restore_journal.directory
            )

    # 独立证明父/枢纽还原结果：不依赖 R 自报 after 摘要，用包内 parent_sha256 复核。
    _prove_parent_restore(root, package, trash_journal)


def _without_navigation(text: str, canonical: MarkdownLink) -> str:
    """只移除规范导航项（整行独占则去整行，否则只去链接标记），其余字节一律保留。"""
    return rewrite_link_destinations(text, navigation_removal_edits(text, canonical))


def _drop_blank_own_subtopic_section(text: str, canonical: MarkdownLink) -> str | None:
    """导航所在 ``## 子主题`` 小节在移除导航后全为空白时，返回连小节一并移除的文本。

    不做全页清洗：最多处理包含本次规范导航的那一个小节。判定前先对导航行应用移除
    编辑再检查剩余字节——导航与其他内容同行时剩余部分仍算小节内容，小节不得判空。
    小节不存在或移除导航后仍有内容时返回 None（不适用）。
    """
    import re as re_module

    subtopic_heading = re_module.compile(r"^## 子主题\s*$")
    lines = text.split("\n")
    nav_index = canonical.line - 1
    # 找规范导航所在的 ## 子主题 小节标题（导航行之前最近的子主题标题）。
    section_start: int | None = None
    for index in range(nav_index - 1, -1, -1):
        if index < 0 or index >= len(lines):
            break
        line = lines[index]
        if line.startswith("#"):
            if subtopic_heading.match(line):
                section_start = index
            break
    if section_start is None:
        return None
    section_end = len(lines)
    for index in range(section_start + 1, len(lines)):
        if lines[index].startswith("#"):
            section_end = index
            break
    # 该小节除规范导航外是否还有其他内容。导航行本身也要先应用移除编辑、再检查
    # 剩余字节：导航与其他内容同行时，剩余部分仍算小节内容，不得把小节判空。
    line_start = text.rfind("\n", 0, canonical.whole_span.start) + 1
    line_end = text.find("\n", canonical.whole_span.end)
    if line_end == -1:
        line_end = len(text)
    nav_line = text[line_start:line_end]
    edits = navigation_removal_edits(text, canonical)
    ((nav_span, nav_replacement),) = edits.items()
    rel_start = nav_span.start - line_start
    rel_end = nav_span.end - line_start
    remaining_nav_line = nav_line[:rel_start] + nav_replacement + nav_line[rel_end:]
    other_content = any(
        (remaining_nav_line if index == nav_index else lines[index]).strip()
        for index in range(section_start + 1, section_end)
    )
    if other_content:
        return None
    # 小节只有规范导航：连同小节标题一并移除（最多一个）。
    without_section = lines[:section_start] + lines[section_end:]
    return "\n".join(without_section)


def _parent_restore_candidates(text: str, canonical: MarkdownLink) -> tuple[str, ...]:
    """逆变换的确定性候选：append 复用已有小节（只去导航）或新建小节（连小节去掉）。

    append_parent_navigation 有 ``## 子主题`` 小节就复用、没有才新建，两种原貌都合法，
    因此逆变换必须按这两个（且仅这两个）确定性构造分别复核 T 耐久的 parent_sha256。
    """
    without_nav = _without_navigation(text, canonical)
    stripped = _drop_blank_own_subtopic_section(text, canonical)
    if stripped is None:
        return (without_nav,)
    return (without_nav, stripped)


def _prove_parent_restore(
    root: Path, package: VerifiedTrashPackage, trash_journal: Journal
) -> None:
    """独立证明父/枢纽还原结果恰好是回收后内容 + 页面导航，不含任意额外内容。

    不依赖 R 自报的 after 摘要：从 live 父/枢纽按两个确定性候选逆变换（只去导航，
    或连 append 新建的空 ``## 子主题`` 小节一并去掉——append 有已存在小节时复用、
    没有才新建，两种原貌都合法）后重新摘要，必须等于 T 耐久记录的 parent_sha256
    （回收后的父/叶内容）。编辑字节不可逆，所以改为"移除导航后应回到回收后状态"
    这一可证等价式。折叠时被重定向的其他页面则逐页应用 T 的 hub->leaf 逆重定向复核。
    用容错加载：证明只依赖恢复相关页面，注册门禁已在规划时完成。
    """
    graph = load_graph_tolerant(root).graph
    canonical = canonical_parent_navigation(graph, package.page_path)
    if canonical is None:
        raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, package.parent_path)
    if package.collapsed:
        hub_path = package.hub_path
        if hub_path is None:
            raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, package.directory)
        hub_text = _read_managed_text(root, hub_path)
        # 枢纽去掉页面导航（两种候选）后逆 rebase 回叶页，应回到回收后的叶内容。
        if not any(
            sha256_bytes(
                _rebase_links(candidate, hub_path, package.parent_path, root).encode("utf-8")
            )
            == package.parent_sha256
            for candidate in _parent_restore_candidates(hub_text, canonical)
        ):
            raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, hub_path)
        # 折叠时被重定向的其他页面：对每页应用 T 的 hub->leaf 逆重定向到 live 页，
        # 应回到 T 的 after（leaf 目标版本），不依赖 R 自报 after。
        internal = Path(INTERNAL_DIR)
        for t_mutation in trash_journal.mutations:
            if t_mutation.path.is_relative_to(internal):
                continue
            if t_mutation.path in (package.page_path, package.parent_path, hub_path):
                continue
            if t_mutation.kind is MutationKind.WRITE and t_mutation.before_sha256 is not None:
                live_text = _read_managed_text(root, t_mutation.path)
                rebased_back = _retarget_links(
                    live_text, t_mutation.path, hub_path, package.parent_path, root
                )
                if sha256_bytes(rebased_back.encode("utf-8")) != t_mutation.after_sha256:
                    raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, t_mutation.path)
    else:
        parent_text = _read_managed_text(root, package.parent_path)
        if not any(
            sha256_bytes(candidate.encode("utf-8")) == package.parent_sha256
            for candidate in _parent_restore_candidates(parent_text, canonical)
        ):
            raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, package.parent_path)


def trash_page(root: Path, page: Path, *, now: datetime | None = None) -> TrashResult:
    """回收页面：创建权威删除包并落地为 7 天保留态，不立即移交系统回收站。"""
    from draft.operation_facts import load_operation_facts

    resolved = resolve_draft_root(root)
    normalized_now = normalize_utc(now)
    deadlines = TrashDeadlines.from_created_at(normalized_now)
    if read_lock(resolved) is not None:
        raise DraftError(CheckCode.OPERATION_RECOVERY_REQUIRED.value, resolved)
    facts = load_operation_facts(resolved)
    if facts.blocking_paths:
        details = tuple(path.as_posix() for path in facts.blocking_paths)
        raise DraftError("structure.dirty", resolved, details=details)
    plan, _package_relative = trash_plan(
        facts.graph, page, root_state=facts.root_state, observations=facts.observations
    )
    plan = dataclasses.replace(plan, metadata={**plan.metadata, **deadlines.to_metadata()})
    result = execute_plan(plan)
    journal = read_journal(operation_journal_path(resolved, plan.metadata["operation_id"]))
    verified = load_verified_package(resolved, journal)
    return TrashResult(
        operation_id=journal.operation_id,
        phase=OperationPhase.TRASH_RETAINED,
        package=verified.directory,
        restore_expires_at=deadlines.restore_expires_at,
        purge_eligible_at=deadlines.purge_eligible_at,
        changed_paths=result.changed_paths,
    )


def restore_retained_trash(
    root: Path, operation_id: str, *, now: datetime | None = None
) -> OperationResult:
    """恢复一个仍处于保留窗口内的权威删除包：一次耐久认领、一把锁、一个终态解决器。"""
    resolved = resolve_draft_root(root)
    normalized_now = normalize_utc(now)
    validate_operation_id(operation_id, code="operation.invalid_id")
    restore_id = uuid.uuid4().hex
    from draft.graph import read_marker
    from draft.observations import EMPTY_OBSERVATIONS

    preallocate_journal(
        resolved,
        kind=OperationKind.RESTORE,
        operation_id=restore_id,
        # 恢复日志保留创建时的当前根状态作为审计事实；规划门禁由 build_restore_plan
        # 重新读取 marker（计划 Task 11 Step 4）。
        root_state=read_marker(resolved).root_state,
        observations=EMPTY_OBSERVATIONS,
        metadata={"restores_trash_operation": operation_id},
    )
    try:
        acquire_lock(resolved, restore_id)
    except DraftError:
        abandon_unlinked_restore(resolved, restore_id)
        raise
    try:
        trash_journal = read_journal(operation_journal_path(resolved, operation_id))
        require_retained_restore_window(trash_journal, normalized_now)
        package = load_verified_package(resolved, trash_journal)
        require_no_existing_restore_claim(resolved, trash_journal, restore_id)
    except DraftError:
        abandon_unlinked_restore(resolved, restore_id, release_owned_lock=True)
        raise
    linked_trash = with_metadata(trash_journal, {"restore_operation_id": restore_id})
    write_journal(linked_trash)
    try:
        plan = build_restore_plan(resolved, restore_id, package)
        # 读集观察耐久写入预分配日志，供执行前复核与恢复使用。
        restore_journal = read_journal(operation_journal_path(resolved, restore_id))
        write_journal(with_observations(restore_journal, plan.observations))
        result = execute_preallocated_plan(plan, retain_terminal_lock=True)
    except DraftError as error:
        return resolve_restore_execution(
            resolved,
            trash_id=operation_id,
            restore_id=restore_id,
            package=package,
            execution_result=None,
            execution_error=error,
        )
    return resolve_restore_execution(
        resolved,
        trash_id=operation_id,
        restore_id=restore_id,
        package=package,
        execution_result=result,
        execution_error=None,
    )


def resolve_restore_execution(
    root: Path,
    *,
    trash_id: str,
    restore_id: str,
    package: VerifiedTrashPackage | None,
    execution_result: OperationResult | None,
    execution_error: DraftError | None,
) -> OperationResult:
    """同一终态解决器：重读 R 与 T，证明终态后再解关联、清理、释放。

    恢复所需证据由耐久状态决定（第十轮 H2）：只有提交前分支（T 仍为 trash_retained，
    需要复核 restore 后置状态）才要求完整包；T 已 rolled_back 时凭提交状态和 cleanup
    evidence 继续清理，不重新加载/验证已获准删除的完整包。``package`` 为可选；提交前
    分支缺失时按需加载（提交后分支不加载）。
    """
    resolve_journal_pending(operation_journal_path(root, restore_id))
    terminal_restore = read_journal(operation_journal_path(root, restore_id))
    current_trash = validate_restore_link(root, terminal_restore)
    changed = tuple(mutation.path for mutation in terminal_restore.mutations)

    if terminal_restore.phase is OperationPhase.COMPLETED:
        if execution_error is not None:
            raise DraftError(
                CheckCode.OPERATION_INCONSISTENT_STATE.value, terminal_restore.directory
            )
        if current_trash.phase is OperationPhase.TRASH_RETAINED:
            # 提交前：必须证明 restore 内容完整后才把 T 提交为 rolled_back。
            if package is None:
                package = load_verified_package(root, current_trash)
            verify_restore_after_state(root, current_trash, terminal_restore, package)
            write_journal(with_phase(current_trash, OperationPhase.ROLLED_BACK))
        elif current_trash.phase is not OperationPhase.ROLLED_BACK:
            raise DraftError(
                CheckCode.OPERATION_INCONSISTENT_STATE.value, terminal_restore.directory
            )
        # M1：restore 清理授权从锁内重读的已提交 T journal 派生（rolled_back 已耐久
        # 写入）；当前锁属于 R 操作（restore_id），双向关联已由 validate_restore_link 证明。
        clean_authoritative_package(
            root,
            cleanup_authorization(root, current_trash.operation_id, lock_operation_id=restore_id),
        )
        clean_recovery_copies(terminal_restore)
        release_lock(root, restore_id)
        return OperationResult(
            operation_id=restore_id,
            kind="restore",
            phase=OperationPhase.COMPLETED,
            changed_paths=changed,
        )

    if terminal_restore.phase is OperationPhase.ROLLED_BACK:
        safe = True
    elif terminal_restore.phase in (
        OperationPhase.ALLOCATING,
        OperationPhase.PREPARED,
    ):
        safe = _proven_unapplied_restore(root, terminal_restore)
    else:
        safe = False

    if not safe:
        # 不可证明安全：保留认领、锁、包；原操作错误交给 recover。
        if execution_error is not None:
            raise execution_error
        raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, terminal_restore.directory)

    # 安全回滚/未应用：耐久 R=rolled_back（若尚未），解关联 T，清理，释放。
    if terminal_restore.phase is not OperationPhase.ROLLED_BACK:
        terminal_restore = with_phase(terminal_restore, OperationPhase.ROLLED_BACK)
        write_journal(terminal_restore)
    write_journal(without_metadata(current_trash, "restore_operation_id"))
    clean_recovery_copies(terminal_restore)
    release_lock(root, restore_id)

    if execution_error is not None:
        if execution_error.code in (
            "operation.validation_failed",
            "operation.external_change",
        ):
            raise DraftError("trash.restore_conflict", current_trash.directory)
        raise execution_error
    return OperationResult(
        operation_id=restore_id,
        kind="restore",
        phase=OperationPhase.ROLLED_BACK,
        changed_paths=changed,
    )
