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
from draft.graph import provisional_marker_path
from draft.models import (
    LifecycleWarning,
    OperationPhase,
    OperationResult,
    TrashResult,
)
from draft.operations import (
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
    reject_symlink_components,
    release_lock,
    validate_operation_id,
    validate_restore_link,
    with_metadata,
    with_phase,
    write_journal,
)
from draft.paths import resolve_draft_root
from draft.trash import (
    OPERATIONS_RELATIVE,
    remove_authoritative_package,
    remove_tree,
    resolve_restore_execution,
)
from draft.trash_package import (
    OUTGOING_RELATIVE,
    VerifiedTrashPackage,
    load_verified_package,
)

Handoff = Callable[[str], None]

DISPOSITION_KEY = "disposition"
SYSTEM_TRASH_DISPOSITION = "system_trash"
EXPIRED_PURGE_DISPOSITION = "expired_purge"

type LifecycleResult = OperationResult | TrashResult


def _default_handoff(path: str) -> None:
    from send2trash import send2trash

    send2trash(path)


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


def make_attempt_copy(package: VerifiedTrashPackage) -> Path:
    """从已重新验证的权威包制作操作内一次性副本。

    只复制已验证的清单与负载（不含包内任何额外项），逐文件耐久写入并同步目录，
    再交给外部适配器；符号链接或错误类型的旧 attempt 视为损坏。
    """
    root = _root_from_package(package)
    reject_symlink_components(root, OUTGOING_RELATIVE)
    attempt_relative = OPERATIONS_RELATIVE / package.operation_id / "attempt"
    reject_symlink_components(root, attempt_relative.parent)
    attempt = root / attempt_relative
    if attempt.is_symlink() or (attempt.exists() and not attempt.is_dir()):
        raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, attempt)
    if attempt.is_dir():
        remove_tree(attempt)
    attempt.mkdir(parents=True, exist_ok=False)
    manifest_bytes = (package.directory / "package.json").read_bytes()
    payload_relative = Path("payload") / package.page_path
    durable_write(attempt / "package.json", manifest_bytes)
    durable_write(attempt / payload_relative, package.page_bytes)
    fsync_directory(attempt)
    return attempt


def remove_attempt_copy(path: Path) -> None:
    """幂等清理一次性副本。"""
    if path.exists() or path.is_symlink():
        remove_tree(path)


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
    if r1.kind != "restore" or r1.metadata.get("restores_trash_operation") != journal.operation_id:
        raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, journal.directory)
    raise DraftError(CheckCode.OPERATION_RECOVERY_REQUIRED.value, journal.directory)


def reread_matching_trash_journal(root: Path, operation_id: str) -> Journal:
    journal = read_journal(operation_journal_path(root, operation_id))
    if journal.operation_id != operation_id or journal.kind != "trash":
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
            remove_authoritative_package(verified_package)
            remove_attempt_copy(attempt)
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
        remove_attempt_copy(attempt)
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
    remove_authoritative_package(verified_package)
    remove_attempt_copy(attempt)
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


def collect_lifecycle_warnings(
    report: LifecycleAdvanceReport,
) -> tuple[LifecycleWarning, ...]:
    """汇聚批量推进中各结果携带的结构化警告，供结构写命令合并进自身结果。"""
    return tuple(warning for result in report.results for warning in result.warnings)


def prepare_structural_write(
    root: Path,
    *,
    now: datetime | None = None,
    handoff: Handoff | None = None,
) -> LifecycleAdvanceReport:
    """普通结构写命令的前置推进：到期包先推进，损坏即抛 LifecycleAdvanceError 中止结构写。"""
    report = advance_trash_lifecycle(root, now=now, handoff=handoff)
    if report.error is not None:
        raise LifecycleAdvanceError(report)
    return report


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
            if journal.kind != "trash":
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
        # 提交点已耐久：只完成清理，绝不再调用适配器。清理前先逐组件校验内部路径。
        reject_symlink_components(root, OUTGOING_RELATIVE)
        reject_symlink_components(root, OPERATIONS_RELATIVE)
        package_dir = root / OUTGOING_RELATIVE / operation_id
        if package_dir.is_dir():
            remove_tree(package_dir)
        attempt = root / OPERATIONS_RELATIVE / operation_id / "attempt"
        remove_attempt_copy(attempt)
        terminal = with_disposition(
            journal,
            EXPIRED_PURGE_DISPOSITION
            if journal.phase is OperationPhase.TRASH_PURGE_COMMITTED
            else SYSTEM_TRASH_DISPOSITION,
            OperationPhase.COMPLETED,
        )
        write_journal(terminal)
        release_lock(root, operation_id)
        return TrashResult(
            operation_id=operation_id, phase=OperationPhase.COMPLETED, package=package_dir
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
        if not any(entry.iterdir()):
            entry.rmdir()
            return None
        raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, entry)
    journal = read_journal(journal_path)
    if journal.kind == "restore":
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
    if journal.kind == "trash":
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
            candidate.kind == "trash"
            and candidate.metadata.get("restore_operation_id") == restore_journal.operation_id
        ):
            return True
    return False


def recover_draft(
    root: Path,
    *,
    now: datetime | None = None,
    handoff: Handoff | None = None,
) -> LifecycleAdvanceReport:
    """恢复全部崩溃现场并推进到期删除包；首个损坏项停止并保留前项结果。"""
    resolved = resolve_draft_root(root)
    provisional = provisional_marker_path(resolved)
    if provisional is not None:
        raise DraftError("adopt.recovery_required", provisional)
    normalized_now = normalize_utc(now)
    active_handoff = handoff if handoff is not None else _default_handoff
    results: list[LifecycleResult] = []

    locked = read_lock(resolved)
    if locked is not None:
        journal = read_journal(operation_journal_path(resolved, locked))
        if journal.operation_id != locked:
            raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, journal.directory)
        if journal.kind == "restore":
            trash_journal = validate_restore_link(resolved, journal)
            package = load_verified_package(resolved, trash_journal)
            results.append(
                resolve_restore_execution(
                    resolved,
                    trash_id=trash_journal.operation_id,
                    restore_id=locked,
                    package=package,
                    execution_result=None,
                    execution_error=None,
                )
            )
        elif journal.kind == "trash":
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
            if journal.kind != "trash":
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
