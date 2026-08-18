"""到期推进、节流移交与最终清理：trash_retained -> trash_handoff_pending -> 成功/清理。

批量推进按 operation_id 排序；前项成功而当前项损坏时，公开信封保留前项结果与停止位置，
后续项不读取、不触碰。结构写前置推进出现同类失败时，请求的结构写不得开始。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from draft.diagnostic_codes import CheckCode
from draft.errors import DraftError
from draft.models import (
    LifecycleWarning,
    OperationKind,
    OperationPhase,
    OperationResult,
    TrashResult,
)
from draft.operations import (
    DISPOSITION_KEY,
    HANDOFF_ATTEMPTED_AT_KEY,
    HANDOFF_ERROR_MESSAGE_KEY,
    HANDOFF_ERROR_TYPE_KEY,
    HANDOFF_RETRY_INTERVAL,
    Journal,
    TrashDeadlines,
    acquire_lock,
    clean_recovery_copies,
    durable_write,
    format_utc,
    fsync_directory,
    normalize_utc,
    operation_journal_path,
    read_journal,
    read_lock,
    recover,
    recover_digest_pending,
    recover_locked_operation,
    recover_quarantine,
    reject_symlink_components,
    release_lock,
    resolve_journal_pending,
    sha256_bytes,
    validate_operation_id,
    with_metadata,
    with_phase,
    write_journal,
)
from draft.paths import resolve_draft_root
from draft.trash import (
    OPERATIONS_RELATIVE,
    resolve_restore_execution,
)
from draft.trash_package import (
    OUTGOING_RELATIVE,
    VerifiedTrashPackage,
    clean_authoritative_package,
    cleanup_authorization,
    load_verified_package,
)

Handoff = Callable[[str], None]

SYSTEM_TRASH_DISPOSITION = "system_trash"
EXPIRED_PURGE_DISPOSITION = "expired_purge"

type LifecycleResult = OperationResult | TrashResult


def _default_handoff(path: str) -> None:
    from send2trash import send2trash

    send2trash(path)


@dataclass(frozen=True)
class RecoverableItem:
    """一个仍处于恢复窗口内的回收项，供 `query recoverable` 返回。"""

    operation_id: str
    page: Path
    restore_expires_at: datetime


@dataclass(frozen=True)
class LifecycleFailure:
    operation_id: str
    code: str
    paths: tuple[Path, ...] = ()
    details: tuple[str, ...] = ()


@dataclass(frozen=True)
class LifecycleAdvanceReport:
    results: tuple[LifecycleResult, ...] = ()
    error: LifecycleFailure | None = None


class LifecycleAdvanceError(DraftError):
    """结构写前置推进失败时携带已完成结果的适配异常；保留 report.error.code 供退出码分类。"""

    report: LifecycleAdvanceReport

    def __init__(self, report: LifecycleAdvanceReport) -> None:
        failure = report.error
        if failure is None:
            raise ValueError("LifecycleAdvanceError requires report.error")
        super().__init__(failure.code, *failure.paths, details=failure.details)
        self.report = report


def _root_from_package(package: VerifiedTrashPackage) -> Path:
    # package.directory = root / OUTGOING_RELATIVE / operation_id
    return package.directory.parents[len(OUTGOING_RELATIVE.parts)]


def _attempt_authorized(package: VerifiedTrashPackage, attempt: Path) -> list[tuple[Path, str]]:
    """attempt 精确树形协议的授权叶节点（绝对路径 + 锚定摘要）。"""
    return [
        (attempt / "package.json", sha256_bytes(package.manifest_bytes)),
        (attempt / package.payload_relative, package.page_sha256),
    ]


def _attempt_authorized_from_journal(journal: Journal, attempt: Path) -> list[tuple[Path, str]]:
    """从同一受约束包授权对象派生 attempt 的精确叶节点（package.json + payload/页面）。

    终态恢复（succeeded/purge_committed）不重新加载权威包（可能已被消费），直接以
    ``parse_package_authorization`` 的授权集合锚定 attempt 内容与摘要；空 mutation、
    第三个包内 WRITE 等非法日志在同一解析边界被拒绝，绝不在 attempt 清理中接受
    任意扫描结果（回归 H6）。
    """
    from draft.trash_package import parse_package_authorization

    auth = parse_package_authorization(journal, attempt)
    return [
        (attempt / "package.json", auth.package_json_digest),
        (attempt / auth.payload_relative, auth.payload_digest),
    ]


def _clean_attempt_tree(attempt: Path, authorized: list[tuple[Path, str]], code_path: Path) -> None:
    """attempt 精确树形协议清理：只删授权叶节点与空父目录，未知内容/符号链接拒绝。

    attempt 是可重建副本，但"可重建"只授权覆盖本操作记录的精确快照文件，不授权
    递归删除后来出现的任意条目。预检整个 attempt 树，任何未知成员、符号链接、
    错误类型或摘要不符都以 operation.inconsistent_state 停止并保留（回归 M1）。
    授权叶节点部分缺失（提交点后已移走）幂等容忍。
    """
    import os

    from draft.observations import EntryKind
    from draft.operations import anchored_leaf, classify_node_or_error

    # lstat 分类：悬空符号链接是 link 不是 missing，不得折叠成幂等缺失（回归 M2）。
    attempt_node = classify_node_or_error(
        attempt,
        code=CheckCode.OPERATION_INCONSISTENT_STATE.value,
        code_path=code_path,
    )
    if attempt_node is EntryKind.MISSING:
        return
    if attempt_node is not EntryKind.DIRECTORY:
        raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, code_path)
    allowed_dirs = {attempt}
    for leaf, _ in authorized:
        cursor = leaf.parent
        while cursor not in allowed_dirs:
            allowed_dirs.add(cursor)
            cursor = cursor.parent
    allowed_leaves = {leaf for leaf, _ in authorized}
    digest_by_path = dict(authorized)
    # 先收敛崩溃遗留的 attempt 叶 pending/quarantine（回归 H2/H3）：发布/消费后才做
    # 未知成员检查，否则 .pending/.quarantine 会被当作未知内容拒绝且无法重建投递副本。
    for leaf, digest in authorized:
        relative = leaf.relative_to(attempt)
        recover_digest_pending(
            attempt,
            relative,
            digest,
            code=CheckCode.OPERATION_INCONSISTENT_STATE.value,
            code_path=code_path,
        )
        recover_quarantine(
            attempt,
            relative,
            digest,
            code=CheckCode.OPERATION_INCONSISTENT_STATE.value,
            code_path=code_path,
        )
    for dirpath, dirnames, filenames in os.walk(attempt, followlinks=False):
        directory = Path(dirpath)
        if directory.is_symlink() or directory not in allowed_dirs:
            raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, directory)
        for name in dirnames:
            child = directory / name
            if child.is_symlink() or child not in allowed_dirs:
                raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, child)
        for name in filenames:
            leaf = directory / name
            if leaf.is_symlink() or leaf not in allowed_leaves or not leaf.is_file():
                raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, leaf)
            if sha256_bytes(leaf.read_bytes()) != digest_by_path[leaf]:
                raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, leaf)
    # 删除阶段锚定到 attempt 根句柄：复核与删除同一句柄，不按绝对路径重新解析（回归 H1）。
    for leaf, digest in sorted(authorized):
        relative = leaf.relative_to(attempt)
        with anchored_leaf(
            attempt,
            relative,
            code=CheckCode.OPERATION_INCONSISTENT_STATE.value,
            code_path=code_path,
        ) as anchor:
            node = anchor.classify()
            if node is EntryKind.MISSING:
                continue  # 提交点后已移走：幂等容忍。
            if node is not EntryKind.FILE:
                raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, code_path)
            if sha256_bytes(anchor.read()) != digest:
                raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, code_path)
            # 隔离→验证→删除：验证与删除同一锚定句柄（回归 H3）。
            anchor.quarantine_delete(digest)
    for directory in sorted(
        allowed_dirs - {attempt}, key=lambda path: len(path.parts), reverse=True
    ):
        relative = directory.relative_to(attempt)
        with anchored_leaf(
            attempt,
            relative,
            code=CheckCode.OPERATION_INCONSISTENT_STATE.value,
            code_path=code_path,
        ) as anchor:
            node = anchor.classify()
            if node is EntryKind.MISSING:
                continue
            if node is not EntryKind.DIRECTORY:
                raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, code_path)
            anchor.rmdir()
    # 最后清理 attempt 根：已分类为 DIRECTORY，预检通过后应已空。
    try:
        attempt.rmdir()
    except OSError as exc:
        raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, code_path) from exc


def make_attempt_copy(package: VerifiedTrashPackage) -> Path:
    """从已验证快照制作操作内一次性副本。

    只使用 load_verified_package 验证时刻的不可变字节（manifest_bytes、page_bytes）
    与已验证的 payload_relative：绝不再读取 live 权威包文件，也不重新用 page_path
    猜测 payload 路径；逐文件耐久写入并同步目录，再交给外部适配器。重建前先按
    精确树形协议预检并清理旧 attempt：未知内容使重试停止保留，不递归删除（回归 M1）。
    """
    from draft.observations import EntryKind
    from draft.operations import classify_node_or_error

    root = _root_from_package(package)
    reject_symlink_components(root, OUTGOING_RELATIVE)
    attempt_relative = OPERATIONS_RELATIVE / package.operation_id / "attempt"
    reject_symlink_components(root, attempt_relative.parent)
    attempt = root / attempt_relative
    # lstat 分类：悬空符号链接是 link 不是 missing，不能绕过清理后由 mkdir 泄漏
    # 原始 FileExistsError（回归 M2）。
    attempt_node = classify_node_or_error(
        attempt,
        code=CheckCode.OPERATION_INCONSISTENT_STATE.value,
        code_path=attempt,
    )
    if attempt_node is EntryKind.DIRECTORY:
        _clean_attempt_tree(attempt, _attempt_authorized(package, attempt), attempt)
    elif attempt_node is not EntryKind.MISSING:
        raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, attempt)
    attempt.mkdir(parents=True, exist_ok=False)
    # 目录链从 attempt 逐层 no-follow 创建/同步（回归 M2）：嵌套 payload 的父目录
    # 逐层 mkdirat + fsync，绝不按绝对路径重新解析祖先。
    durable_write(attempt, Path("package.json"), package.manifest_bytes)
    durable_write(attempt, package.payload_relative, package.page_bytes)
    fsync_directory(attempt)
    return attempt


def remove_attempt_copy(package: VerifiedTrashPackage, path: Path) -> None:
    """幂等清理一次性副本：按精确树形协议清理，不递归删除未知内容。"""
    _clean_attempt_tree(path, _attempt_authorized(package, path), path)


def with_disposition(journal: Journal, disposition: str, phase: OperationPhase) -> Journal:
    """保留全部证据元数据并追加终态处置。"""
    return with_metadata(with_phase(journal, phase), {DISPOSITION_KEY: disposition})


def with_handoff_failure(
    journal: Journal,
    error: OSError,
    *,
    attempted_at: datetime,
    next_attempt_at: datetime,
    phase: OperationPhase,
) -> Journal:
    """记录结构化投递失败元数据与下一次尝试时间，转入指定阶段。"""
    return with_metadata(
        with_phase(journal, phase),
        {
            HANDOFF_ERROR_TYPE_KEY: type(error).__name__,
            HANDOFF_ERROR_MESSAGE_KEY: str(error),
            HANDOFF_ATTEMPTED_AT_KEY: format_utc(attempted_at),
            "next_handoff_attempt_at": format_utc(next_attempt_at),
        },
    )


def require_no_restore_claim_for_expiry(root: Path, journal: Journal) -> None:
    """到期推进转 pending 前：T 不得已有恢复认领。

    合法旧认领为 recovery_required；缺失或错配为 inconsistent。
    """
    existing = journal.metadata.get("restore_operation_id")
    if existing is None:
        return
    r1_id = validate_operation_id(existing)
    r1_path = operation_journal_path(root, r1_id)
    if not r1_path.is_file():
        raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, journal.directory)
    r1 = read_journal(r1_path)
    if r1.kind is not OperationKind.RESTORE or (
        r1.metadata.get("restores_trash_operation") != journal.operation_id
    ):
        raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, journal.directory)
    raise DraftError(CheckCode.OPERATION_RECOVERY_REQUIRED.value, journal.directory)


def reread_matching_trash_journal(root: Path, operation_id: str) -> Journal:
    # 先发布崩溃遗留的 journal pending（回归 H2）：持锁推进从最新日志状态继续。
    resolve_journal_pending(operation_journal_path(root, operation_id))
    journal = read_journal(operation_journal_path(root, operation_id))
    if journal.operation_id != operation_id or journal.kind is not OperationKind.TRASH:
        raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, journal.directory)
    return journal


def _attempt_handoff_locked(
    root: Path,
    journal: Journal,
    verified_package: VerifiedTrashPackage,
    *,
    now: datetime,
    handoff: Handoff,
) -> TrashResult:
    """持锁投递：成功沿 succeeded->completed；明确 OSError 在清理期限前后分别节流或最终清理。"""
    operation_id = journal.operation_id
    deadlines = TrashDeadlines.parse(journal.metadata)
    attempt = make_attempt_copy(verified_package)
    # 调用外部 handoff 前用已验证快照复核权威包：验证后发生的摘要/成员变化必须阻止
    # 适配器读取未验证内容，保留权威包与可诊断状态（回归 H4）。
    from draft.observations import FilesystemObservationProbe, verify_observations

    changed = verify_observations(FilesystemObservationProbe(root), verified_package.observations)
    if changed:
        raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, verified_package.directory)
    try:
        handoff(str(attempt))
    except OSError as error:
        if now >= deadlines.purge_eligible_at:
            failed = with_handoff_failure(
                journal,
                error,
                attempted_at=now,
                next_attempt_at=now + HANDOFF_RETRY_INTERVAL,
                phase=OperationPhase.TRASH_PURGE_COMMITTED,
            )
            write_journal(failed)
            # M1：清理授权从锁内重读的已提交 journal 派生（purge_committed 已耐久写入）。
            clean_authoritative_package(
                root,
                cleanup_authorization(
                    root, journal.operation_id, lock_operation_id=journal.operation_id
                ),
            )
            remove_attempt_copy(verified_package, attempt)
            write_journal(
                with_disposition(failed, EXPIRED_PURGE_DISPOSITION, OperationPhase.COMPLETED)
            )
            release_lock(root, operation_id)
            return TrashResult(
                operation_id=operation_id,
                phase=OperationPhase.COMPLETED,
                package=verified_package.directory,
            )
        next_attempt_at = now + HANDOFF_RETRY_INTERVAL
        failed = with_handoff_failure(
            journal,
            error,
            attempted_at=now,
            next_attempt_at=next_attempt_at,
            phase=OperationPhase.TRASH_HANDOFF_PENDING,
        )
        write_journal(failed)
        remove_attempt_copy(verified_package, attempt)
        release_lock(root, operation_id)
        return TrashResult(
            operation_id=operation_id,
            phase=OperationPhase.TRASH_HANDOFF_PENDING,
            package=verified_package.directory,
            warnings=(
                LifecycleWarning(
                    code="trash.handoff_failed",
                    operation_id=operation_id,
                    details=(type(error).__name__, str(error)),
                ),
            ),
        )
    # 成功：先耐久 succeeded，再清权威包与副本，最后 completed+disposition。
    succeeded = with_phase(journal, OperationPhase.TRASH_HANDOFF_SUCCEEDED)
    write_journal(succeeded)
    # M1：清理授权从锁内重读的已提交 journal 派生（handoff_succeeded 已耐久写入）。
    clean_authoritative_package(
        root,
        cleanup_authorization(root, journal.operation_id, lock_operation_id=journal.operation_id),
    )
    remove_attempt_copy(verified_package, attempt)
    write_journal(with_disposition(succeeded, SYSTEM_TRASH_DISPOSITION, OperationPhase.COMPLETED))
    release_lock(root, operation_id)
    return TrashResult(
        operation_id=operation_id,
        phase=OperationPhase.COMPLETED,
        package=verified_package.directory,
    )


def _advance_one(
    root: Path,
    journal: Journal,
    *,
    now: datetime,
    handoff: Handoff,
) -> TrashResult | None:
    """无锁扫描入口：未到期直接返回；到期则取得锁后委托持锁推进。"""
    scan_deadlines = TrashDeadlines.parse(journal.metadata)
    if journal.phase is OperationPhase.TRASH_RETAINED and now < scan_deadlines.restore_expires_at:
        return None

    acquire_lock(root, journal.operation_id)
    return _advance_one_locked(root, journal.operation_id, now=now, handoff=handoff)


def _advance_one_locked(
    root: Path,
    operation_id: str,
    *,
    now: datetime,
    handoff: Handoff,
) -> TrashResult | None:
    """持锁推进单项：调用方必须已经持有 operation_id 的写锁。"""
    current = reread_matching_trash_journal(root, operation_id)
    deadlines = TrashDeadlines.parse(current.metadata)
    if current.phase is OperationPhase.TRASH_RETAINED:
        if now < deadlines.restore_expires_at:
            release_lock(root, current.operation_id)
            return None
        require_no_restore_claim_for_expiry(root, current)
        current = with_phase(current, OperationPhase.TRASH_HANDOFF_PENDING)
        write_journal(current)
    elif current.phase is not OperationPhase.TRASH_HANDOFF_PENDING:
        release_lock(root, current.operation_id)
        return None

    if now < deadlines.next_handoff_attempt_at and now < deadlines.purge_eligible_at:
        release_lock(root, current.operation_id)
        return None
    verified_package = load_verified_package(root, current)
    return _attempt_handoff_locked(
        root,
        current,
        verified_package,
        now=now,
        handoff=handoff,
    )


def _to_failure(operation_id: str, error: DraftError) -> LifecycleFailure:
    return LifecycleFailure(
        operation_id=operation_id,
        code=error.code,
        paths=tuple(error.paths),
        details=error.details,
    )


def _iter_trash_journals(root: Path) -> list[Path]:
    """按 operation_id 排序返回候选 trash 日志路径；调用方惰性读取以支持首错即停。"""
    reject_symlink_components(root, OPERATIONS_RELATIVE)
    operations = root / OPERATIONS_RELATIVE
    if not operations.is_dir():
        return []
    paths: list[Path] = []
    for entry in sorted(operations.iterdir()):
        if entry.is_symlink():
            raise DraftError(CheckCode.PATH_SYMLINK_ESCAPE.value, entry)
        if not entry.is_dir() or not (entry / "journal.json").is_file():
            continue
        paths.append(operation_journal_path(root, entry.name))
    return paths


def advance_trash_lifecycle(
    root: Path,
    *,
    now: datetime | None = None,
    handoff: Handoff | None = None,
) -> LifecycleAdvanceReport:
    """推进全部到期删除包：按 operation_id 排序，首个损坏项停止并保留前项结果。"""
    resolved = resolve_draft_root(root)
    normalized_now = normalize_utc(now)
    active_handoff = handoff if handoff is not None else _default_handoff
    results: list[LifecycleResult] = []
    for journal_path in _iter_trash_journals(resolved):
        operation_id = journal_path.parent.name
        try:
            journal = read_journal(journal_path)
            if journal.kind is not OperationKind.TRASH:
                continue
            if journal.phase not in (
                OperationPhase.TRASH_RETAINED,
                OperationPhase.TRASH_HANDOFF_PENDING,
            ):
                continue
            result = _advance_one(resolved, journal, now=normalized_now, handoff=active_handoff)
        except DraftError as error:
            return LifecycleAdvanceReport(
                results=tuple(results), error=_to_failure(operation_id, error)
            )
        if result is not None:
            results.append(result)
    return LifecycleAdvanceReport(results=tuple(results), error=None)


def _recover_locked_trash(
    root: Path, journal: Journal, *, now: datetime, handoff: Handoff
) -> TrashResult | None:
    """持锁 trash 日志恢复：succeeded/purge_committed 只清现场不调适配器；其余交 _advance_one。"""
    operation_id = journal.operation_id
    if journal.phase in (
        OperationPhase.TRASH_HANDOFF_SUCCEEDED,
        OperationPhase.TRASH_PURGE_COMMITTED,
    ):
        # 提交点已耐久：只完成清理，绝不再调用适配器。清理走统一权威包原语，
        # 从 T 日志派生授权条目逐个复核删除，未知内容使清理拒绝并保留现场。
        clean_authoritative_package(
            root,
            cleanup_authorization(
                root, journal.operation_id, lock_operation_id=journal.operation_id
            ),
        )
        attempt = root / OPERATIONS_RELATIVE / operation_id / "attempt"
        _clean_attempt_tree(attempt, _attempt_authorized_from_journal(journal, attempt), attempt)
        terminal = with_disposition(
            journal,
            EXPIRED_PURGE_DISPOSITION
            if journal.phase is OperationPhase.TRASH_PURGE_COMMITTED
            else SYSTEM_TRASH_DISPOSITION,
            OperationPhase.COMPLETED,
        )
        write_journal(terminal)
        release_lock(root, operation_id)
        package_dir = root / OUTGOING_RELATIVE / operation_id
        return TrashResult(
            operation_id=operation_id, phase=OperationPhase.COMPLETED, package=package_dir
        )
    if journal.phase is OperationPhase.ROLLING_BACK:
        # 回滚意图已耐久：trash 的 mutation 回滚由统一恢复入口继续（幂等跳过已恢复
        # 目标），不得按生命周期推进或释放锁。终态化后返回 trash 结果形状。
        result = recover(root)
        return TrashResult(
            operation_id=result.operation_id,
            phase=result.phase,
            package=root / OUTGOING_RELATIVE / operation_id,
            changed_paths=result.changed_paths,
        )
    # trash_retained/trash_handoff_pending 持锁：用持锁变体推进，不再重新获取锁。
    return _advance_one_locked(root, operation_id, now=now, handoff=handoff)


def _handle_no_lock_dir(
    root: Path, entry: Path, *, now: datetime, handoff: Handoff
) -> LifecycleResult | None:
    """无锁操作目录分类：放弃孤儿分配碎片、保留损坏现场、忽略终态审计。"""
    reject_symlink_components(root, OPERATIONS_RELATIVE)
    if entry.is_symlink():
        raise DraftError(CheckCode.PATH_SYMLINK_ESCAPE.value, entry)
    journal_path = operation_journal_path(root, entry.name)
    if not journal_path.is_file():
        # 无 journal 且无 pending：空目录可回收，否则按非空现场保留。
        if not any(entry.iterdir()):
            entry.rmdir()
            return None
        # 只有崩溃遗留 pending：发布有效日志后继续，非法 pending 报损坏（回归 H2）。
        resolve_journal_pending(journal_path)
    else:
        resolve_journal_pending(journal_path)
    journal = read_journal(journal_path)
    if journal.kind is OperationKind.RESTORE:
        if journal.phase in (
            OperationPhase.COMPLETED,
            OperationPhase.ROLLED_BACK,
        ):
            # 无锁终态 R：终态先于 T 提交/解关联且先于释放锁写入，因此无锁终态 R
            # 必然意味着 T 已一致（rolled_back 或已解关联），是完整完成的审计记录
            # （第十轮 H2）。剩余 T 认领只是审计痕迹，不是可恢复现场。
            return None
        # 孤儿未关联 R（allocating、无 mutation、无 T 反向关联）：安全放弃为 rolled_back。
        if (
            journal.phase is OperationPhase.ALLOCATING
            and not journal.mutations
            and not _restore_linked_back(root, journal)
        ):
            write_journal(with_phase(journal, OperationPhase.ROLLED_BACK))
            clean_recovery_copies(journal)
            return OperationResult(
                operation_id=journal.operation_id,
                kind="restore",
                phase=OperationPhase.ROLLED_BACK,
                changed_paths=(),
            )
        # 其余无锁 restore（被 T 关联或处于中间阶段）一律保留现场。
        raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, journal.directory)
    if journal.kind is OperationKind.TRASH:
        if journal.phase in (
            OperationPhase.TRASH_HANDOFF_SUCCEEDED,
            OperationPhase.TRASH_PURGE_COMMITTED,
        ):
            raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, journal.directory)
        # trash_retained/trash_handoff_pending：交给批量推进。
        return None
    # 普通操作终态：保留审计；中间阶段由 operations.recover 处理（无锁视为 inconsistent）。
    if journal.phase in (OperationPhase.COMPLETED, OperationPhase.ROLLED_BACK):
        return None
    raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, journal.directory)


def _restore_linked_back(root: Path, restore_journal: Journal) -> bool:
    """R 是否被某个 T 反向关联（如是则不当孤儿放弃）。"""
    reject_symlink_components(root, OPERATIONS_RELATIVE)
    operations = root / OPERATIONS_RELATIVE
    if not operations.is_dir():
        return False
    for entry in operations.iterdir():
        if entry.is_symlink():
            # 链接条目绝不能静默跳过：否则可能漏掉真实反向关联，把 R 误判为孤儿。
            raise DraftError(CheckCode.PATH_SYMLINK_ESCAPE.value, entry)
        if not entry.is_dir() or entry.name == restore_journal.operation_id:
            continue
        back_path = operation_journal_path(root, entry.name)
        if not back_path.is_file():
            if not any(entry.iterdir()):
                # 空预分配碎片：无证据可言，与无锁分类对空目录的处理一致。
                continue
            # 非空目录缺日志：证据可能已丢失，停止并保留现场，不得误判孤儿。
            raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, entry)
        # 日志不可读（损坏/版本不符）时绝不跳过：该日志可能正是本 R 的反向关联
        # 证据，吞掉错误会把证据缺失误判为无关联，进而把 R 错误写成 rolled_back。
        candidate = read_journal(back_path)
        if (
            candidate.kind is OperationKind.TRASH
            and candidate.metadata.get("restore_operation_id") == restore_journal.operation_id
        ):
            return True
    return False


def _recover_locked_restore(root: Path, restore_journal: Journal) -> LifecycleResult:
    """按 R/T 提交状态恢复锁内 restore（第十轮 H2，第十一轮 H1/M1 闭合）。

    先调用唯一权威纯决策 ``restore_recovery_decision``。RESUME_FORWARD/ROLLBACK 时保留
    R 锁、复用通用 mutation 恢复（``recover_locked_operation`` retain_terminal_lock=True），
    终态 R 耐久后重读 R/T 并重新决策；只有提交前分支（T 仍为 trash_retained，需要复核
    restore 后置状态）加载并验证完整包，提交后分支凭 cleanup evidence 继续清理，绝不
    重新要求已经获准删除的完整包。中段恢复后仍不可收敛或决策不一致时保留 R/T、锁、
    包与恢复材料报 inconsistent，不猜测、不静默成功。
    """
    from draft.trash import (
        RestoreRecoveryAction,
        _proven_unapplied_restore,  # pyright: ignore[reportPrivateUsage]
        load_restore_recovery_facts,
        restore_recovery_decision,
    )

    # 恢复专用关联读取：接受 R→T 正确而 T claim 缺失的 ABANDON 耐久中间状态，不把
    # 程序自己写出的 TRASH_UNCLAIMED 拒绝为 plan_invalid（第十二轮 H1）。
    facts = load_restore_recovery_facts(root, restore_journal.operation_id)
    action = restore_recovery_decision(
        restore_phase=facts.restore.phase,
        trash_phase=facts.trash.phase,
        link_state=facts.link_state,
        proven_unapplied=_proven_unapplied_restore(root, facts.restore),
    )
    if action in (
        RestoreRecoveryAction.RESUME_FORWARD,
        RestoreRecoveryAction.RESUME_ROLLBACK,
    ):
        recover_locked_operation(
            root,
            facts.restore.operation_id,
            retain_terminal_lock=True,
        )
        facts = load_restore_recovery_facts(root, facts.restore.operation_id)
        action = restore_recovery_decision(
            restore_phase=facts.restore.phase,
            trash_phase=facts.trash.phase,
            link_state=facts.link_state,
            proven_unapplied=_proven_unapplied_restore(root, facts.restore),
        )
    if action in (
        RestoreRecoveryAction.RESUME_FORWARD,
        RestoreRecoveryAction.RESUME_ROLLBACK,
        RestoreRecoveryAction.INCONSISTENT,
    ):
        # 中段恢复后只允许收敛到 verify/committed/abandon；仍不可收敛或决策不一致
        # 时保留现场报 inconsistent，不把失败解释为放弃或成功。
        raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, facts.restore.directory)
    if action is RestoreRecoveryAction.VERIFY_AND_COMMIT_CLEANUP:
        package: VerifiedTrashPackage | None = load_verified_package(root, facts.trash)
    else:
        package = None
    return resolve_restore_execution(
        root,
        trash_id=facts.trash.operation_id,
        restore_id=facts.restore.operation_id,
        action=action,
        package=package,
        execution_error=None,
    )


def recover_draft(
    root: Path,
    *,
    now: datetime | None = None,
    handoff: Handoff | None = None,
) -> LifecycleAdvanceReport:
    """恢复全部崩溃现场并推进到期删除包；首个损坏项停止并保留前项结果。"""
    resolved = resolve_draft_root(root)
    normalized_now = normalize_utc(now)
    active_handoff = handoff if handoff is not None else _default_handoff
    results: list[LifecycleResult] = []

    locked = read_lock(resolved)
    if locked is not None:
        journal = read_journal(operation_journal_path(resolved, locked))
        if journal.operation_id != locked:
            raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, journal.directory)
        if journal.kind is OperationKind.RESTORE:
            results.append(_recover_locked_restore(resolved, journal))
        elif journal.kind is OperationKind.TRASH:
            result = _recover_locked_trash(
                resolved, journal, now=normalized_now, handoff=active_handoff
            )
            if result is not None:
                results.append(result)
        else:
            results.append(recover(resolved))

    reject_symlink_components(resolved, OPERATIONS_RELATIVE)
    operations = resolved / OPERATIONS_RELATIVE
    if operations.is_dir():
        for entry in sorted(operations.iterdir()):
            if not entry.is_dir() or entry.name == locked:
                continue
            try:
                result = _handle_no_lock_dir(
                    resolved, entry, now=normalized_now, handoff=active_handoff
                )
            except DraftError as error:
                return LifecycleAdvanceReport(
                    results=tuple(results), error=_to_failure(entry.name, error)
                )
            if result is not None:
                results.append(result)

    # 无锁 trash_retained/trash_handoff_pending 批量推进。
    for journal_path in _iter_trash_journals(resolved):
        if read_lock(resolved) is not None:
            break
        operation_id = journal_path.parent.name
        try:
            journal = read_journal(journal_path)
            if journal.kind is not OperationKind.TRASH:
                continue
            if journal.phase not in (
                OperationPhase.TRASH_RETAINED,
                OperationPhase.TRASH_HANDOFF_PENDING,
            ):
                continue
            result = _advance_one(resolved, journal, now=normalized_now, handoff=active_handoff)
        except DraftError as error:
            return LifecycleAdvanceReport(
                results=tuple(results), error=_to_failure(operation_id, error)
            )
        if result is not None:
            results.append(result)

    return LifecycleAdvanceReport(results=tuple(results), error=None)


def prepare_managed_command(
    root: Path,
    *,
    now: datetime | None = None,
    handoff: Handoff | None = None,
) -> None:
    """受管根命令的业务逻辑前置推进：成功时丢弃推进细节，损坏时以稳定错误阻止命令。

    candidate 与 registered 共享回收期限和权威删除包协议（规格 §8.1）。调用方必须
    先确认受管根 marker 有效；本边界不重新判定根状态。
    """
    report = advance_trash_lifecycle(root, now=now, handoff=handoff)
    if report.error is not None:
        raise LifecycleAdvanceError(report)


def list_recoverable(
    root: Path,
    *,
    as_of: datetime | None = None,
) -> tuple[RecoverableItem, ...]:
    """只读返回仍可恢复的回收项，严格按 ``as_of < restore_expires_at`` 过滤。

    任一候选日志或权威删除包损坏时整体失败，不返回部分项目。
    """
    resolved = resolve_draft_root(root)
    normalized = normalize_utc(as_of)
    items: list[RecoverableItem] = []
    for journal_path in _iter_trash_journals(resolved):
        journal = read_journal(journal_path)
        if journal.kind is not OperationKind.TRASH:
            continue
        if journal.phase is not OperationPhase.TRASH_RETAINED:
            continue
        deadlines = TrashDeadlines.parse(journal.metadata)
        if normalized >= deadlines.restore_expires_at:
            continue
        package = load_verified_package(resolved, journal)
        items.append(
            RecoverableItem(
                operation_id=journal.operation_id,
                page=package.page_path,
                restore_expires_at=deadlines.restore_expires_at,
            )
        )
    return tuple(sorted(items, key=lambda item: (item.restore_expires_at, item.operation_id)))


def recover_interrupted_operations(
    root: Path,
    *,
    now: datetime | None = None,
    handoff: Handoff | None = None,
) -> tuple[LifecycleResult, ...]:
    """只修复崩溃或中断留下的未完成写操作；不枚举仍保留或等待移交的回收项。

    当前 ``recover_draft`` 中锁内恢复与无锁操作目录分类属于这里；批量到期推进
    由 ``prepare_managed_command`` 在命令前置统一完成。
    """
    resolved = resolve_draft_root(root)
    normalized_now = normalize_utc(now)
    active_handoff = handoff if handoff is not None else _default_handoff
    results: list[LifecycleResult] = []

    locked = read_lock(resolved)
    if locked is not None:
        journal = read_journal(operation_journal_path(resolved, locked))
        if journal.operation_id != locked:
            raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, journal.directory)
        if journal.kind is OperationKind.RESTORE:
            results.append(_recover_locked_restore(resolved, journal))
        elif journal.kind is OperationKind.TRASH:
            result = _recover_locked_trash(
                resolved, journal, now=normalized_now, handoff=active_handoff
            )
            if result is not None:
                results.append(result)
        else:
            results.append(recover(resolved))

    reject_symlink_components(resolved, OPERATIONS_RELATIVE)
    operations = resolved / OPERATIONS_RELATIVE
    if operations.is_dir():
        for entry in sorted(operations.iterdir()):
            if not entry.is_dir() or entry.name == locked:
                continue
            result = _handle_no_lock_dir(
                resolved, entry, now=normalized_now, handoff=active_handoff
            )
            if result is not None:
                results.append(result)

    return tuple(results)
