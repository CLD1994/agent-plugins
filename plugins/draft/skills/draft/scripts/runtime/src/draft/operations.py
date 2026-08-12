from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import cast

from draft.diagnostic_codes import CheckCode
from draft.errors import DraftError
from draft.graph import INTERNAL_DIR, MARKER_PATH
from draft.models import (
    MutationKind,
    OperationPhase,
    OperationPlan,
    OperationResult,
    ValidationMode,
)
from draft.paths import resolve_draft_root
from draft.plan_effects import public_changed_paths

JOURNAL_VERSION = 2
LOCK_RELATIVE = Path(INTERNAL_DIR) / "operation.lock"
OPERATIONS_RELATIVE = Path(INTERNAL_DIR) / "operations"

# 第一版固定的生命周期时间常量：恢复窗口 7 天、清理期限再延 30 天、
# 普通投递失败后节流 24 小时。这些是绝对事实，不随默认值变化追溯改变已有删除包。
RESTORE_WINDOW = timedelta(days=7)
PURGE_WINDOW_AFTER_RESTORE = timedelta(days=30)
HANDOFF_RETRY_INTERVAL = timedelta(hours=24)

# 投递失败记录的稳定元数据字段：错误类型、错误消息、尝试时间。
HANDOFF_ERROR_TYPE_KEY = "handoff_error_type"
HANDOFF_ERROR_MESSAGE_KEY = "handoff_error_message"
HANDOFF_ATTEMPTED_AT_KEY = "handoff_attempted_at"

_MID_WRITE_PHASES = frozenset(
    {
        OperationPhase.PREPARED,
        OperationPhase.APPLYING,
        OperationPhase.VALIDATING,
    }
)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def normalize_utc(value: datetime | None) -> datetime:
    """唯一的产时/注入时钟归一化边界：缺省取当前 UTC，拒绝 naive 与非 UTC 输入。"""
    current = datetime.now(UTC) if value is None else value
    if current.tzinfo is None or current.utcoffset() != timedelta(0):
        raise DraftError("operation.time_invalid")
    return current.astimezone(UTC)


def format_utc(value: datetime) -> str:
    """Z 归一化的 RFC 3339 字符串；与 ``TrashDeadlines.parse`` 互相可读。"""
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_utc_field(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value)
    return parsed.astimezone(UTC)


@dataclass(frozen=True)
class TrashDeadlines:
    """回收操作的不可变绝对期限事实：创建即冻结，随日志耐久保存。"""

    created_at: datetime
    restore_expires_at: datetime
    purge_eligible_at: datetime
    next_handoff_attempt_at: datetime

    @classmethod
    def from_created_at(cls, created_at: datetime) -> TrashDeadlines:
        created = normalize_utc(created_at)
        restore = created + RESTORE_WINDOW
        purge = restore + PURGE_WINDOW_AFTER_RESTORE
        return cls(created, restore, purge, restore)

    @staticmethod
    def parse(metadata: Mapping[str, str]) -> TrashDeadlines:
        """从日志元数据解析期限：缺失、naive、非 UTC、错配或内部不一致统一视为日志损坏。"""

        def field(key: str) -> datetime:
            value = metadata.get(key)
            if value is None:
                raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value)
            return _parse_utc_field(value)

        created = field("created_at")
        restore = field("restore_expires_at")
        purge = field("purge_eligible_at")
        next_attempt = field("next_handoff_attempt_at")
        if restore != created + RESTORE_WINDOW:
            raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value)
        if purge != restore + PURGE_WINDOW_AFTER_RESTORE:
            raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value)
        if next_attempt < restore:
            raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value)
        return TrashDeadlines(created, restore, purge, next_attempt)

    def to_metadata(self) -> dict[str, str]:
        return {
            "created_at": format_utc(self.created_at),
            "restore_expires_at": format_utc(self.restore_expires_at),
            "purge_eligible_at": format_utc(self.purge_eligible_at),
            "next_handoff_attempt_at": format_utc(self.next_handoff_attempt_at),
        }


def has_handoff_failure(metadata: Mapping[str, str]) -> bool:
    """返回元数据是否携带结构完整的投递失败记录（错误类型/消息/尝试时间）。"""
    error_type = metadata.get(HANDOFF_ERROR_TYPE_KEY)
    message = metadata.get(HANDOFF_ERROR_MESSAGE_KEY)
    attempted = metadata.get(HANDOFF_ATTEMPTED_AT_KEY)
    if error_type is None or message is None or attempted is None:
        return False
    try:
        parsed = datetime.fromisoformat(attempted)
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == timedelta(0)


def handoff_failure_metadata(error: OSError, *, attempted_at: datetime) -> dict[str, str]:
    """构造投递失败记录元数据；调用方负责把 ``attempted_at`` 经 ``normalize_utc`` 归一。"""
    return {
        HANDOFF_ERROR_TYPE_KEY: type(error).__name__,
        HANDOFF_ERROR_MESSAGE_KEY: str(error),
        HANDOFF_ATTEMPTED_AT_KEY: format_utc(attempted_at),
    }


def sha256_file(path: Path) -> str | None:
    if path.is_symlink():
        raise DraftError(CheckCode.PATH_SYMLINK_ESCAPE.value, path)
    if not path.exists():
        return None
    return sha256_bytes(path.read_bytes())


def replace_file(source: Path, target: Path) -> None:
    source.replace(target)


def fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def reject_symlink_components(root: Path, relative: Path) -> None:
    """逐组件拒绝内部状态路径上的符号链接，防止 .draft 等中间组件逃逸到根外。"""
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise DraftError(CheckCode.PATH_SYMLINK_ESCAPE.value, cursor)


def durable_write(path: Path, value: bytes) -> None:
    """Durable write for internal state (journal, lock, copies); not the managed-page seam."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp-{uuid.uuid4().hex}")
    with temporary.open("xb") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    fsync_directory(path.parent)


def acquire_lock(root: Path, operation_id: str) -> None:
    reject_symlink_components(root, LOCK_RELATIVE)
    lock = root / LOCK_RELATIVE
    lock.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"operation_id": operation_id}) + "\n"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise DraftError(CheckCode.OPERATION_RECOVERY_REQUIRED.value, lock) from exc
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    fsync_directory(lock.parent)


_OPERATION_ID = re.compile(r"[0-9a-f]{32}")


def validate_operation_id(
    value: str, *, code: str = CheckCode.OPERATION_JOURNAL_CORRUPT.value
) -> str:
    """唯一的 operation_id 边界：所有外部标识在任何路径拼接前必须过这里。"""
    if not _OPERATION_ID.fullmatch(value):
        raise DraftError(code)
    return value


def read_lock(root: Path) -> str | None:
    reject_symlink_components(root, LOCK_RELATIVE)
    lock = root / LOCK_RELATIVE
    if not lock.exists():
        return None
    try:
        decoded = json.loads(lock.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, lock) from exc
    if not isinstance(decoded, dict):
        raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, lock)
    operation_id = cast("dict[str, object]", decoded).get("operation_id")
    if not isinstance(operation_id, str):
        raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, lock)
    return validate_operation_id(operation_id)


def release_lock(root: Path, operation_id: str) -> None:
    current = read_lock(root)
    if current != operation_id:
        raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, root / LOCK_RELATIVE)
    (root / LOCK_RELATIVE).unlink()
    fsync_directory(root / LOCK_RELATIVE.parent)


@dataclass(frozen=True)
class JournalMutation:
    path: Path
    kind: MutationKind
    before_sha256: str | None
    after_sha256: str | None
    original: str | None
    staged: str | None
    applied: bool


@dataclass(frozen=True)
class Journal:
    operation_id: str
    kind: str
    phase: OperationPhase
    validation_mode: str
    mutations: tuple[JournalMutation, ...]
    remove_empty_directories: tuple[Path, ...]
    metadata: dict[str, str]
    directory: Path


def _journal_to_json(journal: Journal) -> bytes:
    payload = {
        "journal_version": JOURNAL_VERSION,
        "operation_id": journal.operation_id,
        "kind": journal.kind,
        "phase": journal.phase.value,
        "validation_mode": journal.validation_mode,
        "mutations": [
            {
                "path": mutation.path.as_posix(),
                "kind": mutation.kind.value,
                "before_sha256": mutation.before_sha256,
                "after_sha256": mutation.after_sha256,
                "original": mutation.original,
                "staged": mutation.staged,
                "applied": mutation.applied,
            }
            for mutation in journal.mutations
        ],
        "remove_empty_directories": [path.as_posix() for path in journal.remove_empty_directories],
        "metadata": journal.metadata,
    }
    return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _expect_str(value: object, journal_path: Path) -> str:
    if not isinstance(value, str):
        raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, journal_path)
    return value


def _expect_optional_str(value: object, journal_path: Path) -> str | None:
    if value is not None and not isinstance(value, str):
        raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, journal_path)
    return value


def _validate_journal_path(value: str, journal_path: Path) -> Path:
    relative = PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, journal_path)
    return Path(*relative.parts)


def read_journal(journal_path: Path) -> Journal:
    if not journal_path.is_file():
        raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, journal_path)
    try:
        decoded = json.loads(journal_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, journal_path) from exc
    if not isinstance(decoded, dict):
        raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, journal_path)
    data = cast("dict[str, object]", decoded)
    version = data.get("journal_version")
    if version != JOURNAL_VERSION:
        raise DraftError(
            "operation.journal_version_unsupported",
            journal_path,
            details=(str(version), str(JOURNAL_VERSION)),
        )
    try:
        phase = OperationPhase(_expect_str(data.get("phase"), journal_path))
        mutations_raw = data.get("mutations")
        if not isinstance(mutations_raw, list):
            raise TypeError("mutations must be a list")
        mutations: list[JournalMutation] = []
        for entry in cast("list[object]", mutations_raw):
            if not isinstance(entry, dict):
                raise TypeError("mutation must be an object")
            item = cast("dict[str, object]", entry)
            applied = item.get("applied")
            if not isinstance(applied, bool):
                raise TypeError("applied must be a boolean")
            mutations.append(
                JournalMutation(
                    path=_validate_journal_path(
                        _expect_str(item.get("path"), journal_path), journal_path
                    ),
                    kind=MutationKind(_expect_str(item.get("kind"), journal_path)),
                    before_sha256=_expect_optional_str(item.get("before_sha256"), journal_path),
                    after_sha256=_expect_optional_str(item.get("after_sha256"), journal_path),
                    original=_expect_optional_str(item.get("original"), journal_path),
                    staged=_expect_optional_str(item.get("staged"), journal_path),
                    applied=applied,
                )
            )
        directories_raw = data.get("remove_empty_directories")
        if not isinstance(directories_raw, list):
            raise TypeError("remove_empty_directories must be a list")
        metadata_raw = data.get("metadata")
        if not isinstance(metadata_raw, dict):
            raise TypeError("metadata must be an object")
    except (TypeError, ValueError) as exc:
        raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, journal_path) from exc
    metadata = {
        _expect_str(key, journal_path): _expect_str(value, journal_path)
        for key, value in cast("dict[object, object]", metadata_raw).items()
    }
    directory_id = validate_operation_id(journal_path.parent.name)
    operation_id = validate_operation_id(_expect_str(data.get("operation_id"), journal_path))
    if operation_id != directory_id:
        raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, journal_path)
    return Journal(
        operation_id=operation_id,
        kind=_expect_str(data.get("kind"), journal_path),
        phase=phase,
        validation_mode=_expect_str(data.get("validation_mode"), journal_path),
        mutations=tuple(mutations),
        remove_empty_directories=tuple(
            _validate_journal_path(_expect_str(entry, journal_path), journal_path)
            for entry in cast("list[object]", directories_raw)
        ),
        metadata=metadata,
        directory=journal_path.parent,
    )


def write_journal(journal: Journal) -> None:
    durable_write(journal.directory / "journal.json", _journal_to_json(journal))


def with_phase(journal: Journal, phase: OperationPhase) -> Journal:
    """公开的不可变日志阶段替换；生命周期模块共享。"""
    return Journal(
        operation_id=journal.operation_id,
        kind=journal.kind,
        phase=phase,
        validation_mode=journal.validation_mode,
        mutations=journal.mutations,
        remove_empty_directories=journal.remove_empty_directories,
        metadata=journal.metadata,
        directory=journal.directory,
    )


def with_metadata(journal: Journal, extra: Mapping[str, str]) -> Journal:
    """公开的不可变日志元数据合并；生命周期模块共享。"""
    return Journal(
        operation_id=journal.operation_id,
        kind=journal.kind,
        phase=journal.phase,
        validation_mode=journal.validation_mode,
        mutations=journal.mutations,
        remove_empty_directories=journal.remove_empty_directories,
        metadata={**journal.metadata, **extra},
        directory=journal.directory,
    )


def without_metadata(journal: Journal, key: str) -> Journal:
    """公开的不可变日志元数据删除；用于清除已解关联的恢复认领。"""
    remaining = {k: v for k, v in journal.metadata.items() if k != key}
    return Journal(
        operation_id=journal.operation_id,
        kind=journal.kind,
        phase=journal.phase,
        validation_mode=journal.validation_mode,
        mutations=journal.mutations,
        remove_empty_directories=journal.remove_empty_directories,
        metadata=remaining,
        directory=journal.directory,
    )


_with_phase = with_phase


def _with_applied(journal: Journal, path: Path) -> Journal:
    return Journal(
        operation_id=journal.operation_id,
        kind=journal.kind,
        phase=journal.phase,
        validation_mode=journal.validation_mode,
        mutations=tuple(
            JournalMutation(
                path=mutation.path,
                kind=mutation.kind,
                before_sha256=mutation.before_sha256,
                after_sha256=mutation.after_sha256,
                original=mutation.original,
                staged=mutation.staged,
                applied=True if mutation.path == path else mutation.applied,
            )
            for mutation in journal.mutations
        ),
        remove_empty_directories=journal.remove_empty_directories,
        metadata=journal.metadata,
        directory=journal.directory,
    )


def _validate_plan_paths(plan: OperationPlan) -> None:
    trash_outgoing = Path(INTERNAL_DIR) / "trash" / "outgoing"
    for mutation in plan.mutations:
        relative = mutation.path
        if relative.is_absolute() or ".." in relative.parts:
            raise DraftError("path.escape", relative)
        if relative.parts and relative.parts[0] == INTERNAL_DIR:
            generate_marker = plan.kind == "generate-conclusions" and relative == MARKER_PATH
            trash_package = plan.kind == "trash" and relative.is_relative_to(trash_outgoing)
            if not (trash_package or generate_marker):
                raise DraftError("path.escape", relative)
        cursor = plan.root
        for part in relative.parts:
            cursor /= part
            if cursor.is_symlink():
                raise DraftError(CheckCode.PATH_SYMLINK_ESCAPE.value, cursor)


def _check(root: Path, validation_mode: str) -> None:
    from draft.checker import check_draft

    mode = "candidate" if validation_mode == "candidate" else "registered"
    report = check_draft(
        root, mode=mode, include_operation_state=False, exclude_resource_facts=True
    )
    if report.errors:
        codes = ", ".join(issue.code for issue in report.errors)
        raise DraftError("operation.validation_failed", root, details=(codes,))


def _apply_write(root: Path, target: Path, content: bytes) -> None:
    destination = root / target
    cursor = root
    for part in target.parts[:-1]:
        cursor /= part
        if cursor.is_symlink():
            raise DraftError(CheckCode.PATH_SYMLINK_ESCAPE.value, cursor)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f"{destination.name}.tmp-{uuid.uuid4().hex}")
    with temporary.open("xb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    replace_file(temporary, destination)
    fsync_directory(destination.parent)


def _apply_delete(root: Path, target: Path) -> None:
    destination = root / target
    if destination.is_symlink():
        raise DraftError(CheckCode.PATH_SYMLINK_ESCAPE.value, destination)
    if destination.exists():
        destination.unlink()
        fsync_directory(destination.parent)


def _execute_apply_phase(root: Path, journal: Journal) -> Journal:
    """Apply pending mutations, durable-recording each step. Returns latest journal."""
    current = journal
    writes = [m for m in current.mutations if m.kind is MutationKind.WRITE]
    deletes = [m for m in current.mutations if m.kind is MutationKind.DELETE]
    for mutation in [*writes, *deletes]:
        if mutation.applied:
            continue
        if mutation.kind is MutationKind.WRITE:
            staged = current.directory / cast(str, mutation.staged)
            staged_hash = sha256_file(staged)
            if staged_hash is None:
                raise DraftError("operation.staged_missing", staged)
            _apply_write(root, mutation.path, staged.read_bytes())
        else:
            _apply_delete(root, mutation.path)
        current = _with_applied(current, mutation.path)
        write_journal(current)
    return current


def _clean_recovery_copies(journal: Journal) -> None:
    for sub in ("originals", "staged"):
        directory = journal.directory / sub
        if directory.is_dir():
            for entry in directory.iterdir():
                entry.unlink()
            directory.rmdir()


def finalize_operation(
    root: Path,
    journal: Journal,
    phase: OperationPhase,
    plan_kind: str,
    *,
    retain_terminal_lock: bool = False,
    changed_paths: tuple[Path, ...] | None = None,
) -> OperationResult:
    """Mark a terminal phase; by default release the lock and clean recovery copies.

    ``retain_terminal_lock=True`` 仅耐久写入终态并移除空目录，不释放写锁、不清理恢复副本，
    供恢复包装层在证明终态后统一解关联、清理与释放。``changed_paths`` 由调用方从
    公开效果派生；缺省回退到日志 mutation 路径（恢复等无公开效果边界）。
    """
    terminal = _with_phase(journal, phase)
    write_journal(terminal)
    if not retain_terminal_lock:
        release_lock(root, journal.operation_id)
        _clean_recovery_copies(journal)
    for relative in journal.remove_empty_directories:
        with suppress(OSError):
            (root / relative).rmdir()
    if changed_paths is None:
        changed_paths = tuple(mutation.path for mutation in journal.mutations)
    return OperationResult(
        operation_id=journal.operation_id,
        kind=plan_kind,
        phase=phase,
        changed_paths=changed_paths,
    )


def clean_recovery_copies(journal: Journal) -> None:
    """公开恢复副本清理入口，供恢复包装层在终态证明后调用。"""
    _clean_recovery_copies(journal)


def _rollback(
    root: Path,
    journal: Journal,
    *,
    retain_terminal_lock: bool = False,
    changed_paths: tuple[Path, ...] | None = None,
) -> OperationResult:
    """Restore every target to its before state; requires all targets classifiable."""
    for mutation in journal.mutations:
        current = sha256_file(root / mutation.path)
        known = {mutation.before_sha256, mutation.after_sha256}
        if current not in known:
            raise DraftError("operation.external_change", root / mutation.path)
    for mutation in reversed(journal.mutations):
        current = sha256_file(root / mutation.path)
        if current == mutation.before_sha256:
            continue
        if mutation.before_sha256 is None:
            _apply_delete(root, mutation.path)
        else:
            original = journal.directory / cast(str, mutation.original)
            original_hash = sha256_file(original)
            if original_hash != mutation.before_sha256:
                raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, original)
            _apply_write(root, mutation.path, original.read_bytes())
    return finalize_operation(
        root,
        journal,
        OperationPhase.ROLLED_BACK,
        journal.kind,
        retain_terminal_lock=retain_terminal_lock,
        changed_paths=changed_paths,
    )


def _finish_forward(
    root: Path,
    journal: Journal,
    *,
    retain_terminal_lock: bool = False,
    changed_paths: tuple[Path, ...] | None = None,
) -> OperationResult:
    """Apply remaining after states, validate, and complete or roll back."""
    current = journal
    for mutation in current.mutations:
        current_hash = sha256_file(root / mutation.path)
        if current_hash == mutation.after_sha256:
            continue
        if current_hash != mutation.before_sha256:
            raise DraftError("operation.external_change", root / mutation.path)
        if (
            mutation.kind is MutationKind.WRITE
            and not mutation.applied
            and (
                mutation.staged is None or sha256_file(current.directory / mutation.staged) is None
            )
        ):
            return _rollback(
                root,
                current,
                retain_terminal_lock=retain_terminal_lock,
                changed_paths=changed_paths,
            )
        current = _apply_one(root, current, mutation)
    return _validate_and_complete(
        root, current, retain_terminal_lock=retain_terminal_lock, changed_paths=changed_paths
    )


def _apply_one(root: Path, journal: Journal, mutation: JournalMutation) -> Journal:
    if mutation.kind is MutationKind.WRITE:
        staged = journal.directory / cast(str, mutation.staged)
        content = staged.read_bytes()
        _apply_write(root, mutation.path, content)
    else:
        _apply_delete(root, mutation.path)
    updated = _with_applied(journal, mutation.path)
    write_journal(updated)
    return updated


def _validate_and_complete(
    root: Path,
    journal: Journal,
    *,
    retain_terminal_lock: bool = False,
    changed_paths: tuple[Path, ...] | None = None,
) -> OperationResult:
    current = _with_phase(journal, OperationPhase.VALIDATING)
    write_journal(current)
    try:
        _check(root, current.validation_mode)
    except DraftError as error:
        if error.code == "operation.validation_failed":
            _rollback(
                root,
                current,
                retain_terminal_lock=retain_terminal_lock,
                changed_paths=changed_paths,
            )
            raise DraftError("operation.validation_failed", root, details=error.details) from error
        raise
    if current.kind == "trash":
        # 回收以待恢复为耐久终态：写 trash_retained、释放写锁、清理恢复副本；
        # 权威删除包原样保留，到期推进与系统回收站移交由 trash_lifecycle 子系统驱动。
        return finalize_operation(
            root,
            current,
            OperationPhase.TRASH_RETAINED,
            current.kind,
            changed_paths=changed_paths,
        )
    return finalize_operation(
        root,
        current,
        OperationPhase.COMPLETED,
        current.kind,
        retain_terminal_lock=retain_terminal_lock,
        changed_paths=changed_paths,
    )


def preallocate_journal(
    root: Path,
    *,
    kind: str,
    operation_id: str,
    validation_mode: ValidationMode,
    metadata: dict[str, str],
) -> Journal:
    """统一预分配：独占创建操作目录、同步父目录项、耐久写入 allocating 日志。

    父目录 fsync 保证“引用方已耐久时，被引用的操作目录必然存在”。
    已存在的操作目录绝不复用：直接报 operation.id_conflict，不打开也不改写其内容。
    """
    validate_operation_id(operation_id, code="operation.plan_invalid")
    if kind == "restore":
        back_reference = metadata.get("restores_trash_operation")
        if back_reference is None:
            raise DraftError("operation.plan_invalid", root / OPERATIONS_RELATIVE / operation_id)
        validate_operation_id(back_reference, code="operation.plan_invalid")
    operations = root / OPERATIONS_RELATIVE
    reject_symlink_components(root, OPERATIONS_RELATIVE)
    if operations.exists() and not operations.is_dir():
        raise DraftError("operation.plan_invalid", operations)
    operations.mkdir(parents=True, exist_ok=True)
    fsync_directory(operations.parent)
    directory = operations / operation_id
    try:
        directory.mkdir(exist_ok=False)
    except FileExistsError as exc:
        raise DraftError("operation.id_conflict", directory) from exc
    fsync_directory(operations)
    journal = Journal(
        operation_id=operation_id,
        kind=kind,
        phase=OperationPhase.ALLOCATING,
        validation_mode=validation_mode,
        mutations=(),
        remove_empty_directories=(),
        metadata=dict(metadata),
        directory=directory,
    )
    write_journal(journal)
    return journal


def validate_restore_link(root: Path, restore_journal: Journal) -> Journal:
    """唯一双向关联验证：恢复日志必须指向回收日志，且回收日志必须回指本恢复操作。"""
    if restore_journal.kind != "restore":
        raise DraftError("operation.plan_invalid", restore_journal.directory)
    back_reference = restore_journal.metadata.get("restores_trash_operation")
    if back_reference is None:
        raise DraftError("operation.plan_invalid", restore_journal.directory)
    back_id = validate_operation_id(back_reference, code="operation.plan_invalid")
    back_journal = read_journal(operation_journal_path(root, back_id))
    if (
        back_journal.kind != "trash"
        or back_journal.metadata.get("restore_operation_id") != restore_journal.operation_id
    ):
        raise DraftError("operation.plan_invalid", restore_journal.directory)
    return back_journal


def operation_journal_path(root: Path, operation_id: str) -> Path:
    """唯一的日志路径构造：先验证 operation_id 形状，再逐组件拒绝符号链接。"""
    validated = validate_operation_id(operation_id, code="operation.plan_invalid")
    relative = OPERATIONS_RELATIVE / validated / "journal.json"
    reject_symlink_components(root, relative)
    return root / relative


def require_plan_operation_id(plan: OperationPlan) -> str:
    value = plan.metadata.get("operation_id")
    if value is None:
        raise DraftError("operation.plan_invalid", plan.root)
    return validate_operation_id(value, code="operation.plan_invalid")


def validate_preallocated_journal(plan: OperationPlan, journal: Journal) -> None:
    if (
        journal.operation_id != require_plan_operation_id(plan)
        or journal.kind != plan.kind
        or journal.phase is not OperationPhase.ALLOCATING
        or journal.mutations
    ):
        raise DraftError("operation.plan_invalid", journal.directory)
    if journal.kind == "restore":
        validate_restore_link(plan.root, journal)


def execute_plan(plan: OperationPlan) -> OperationResult:
    root = resolve_draft_root(plan.root)
    _validate_plan_paths(plan)
    lock_id = read_lock(root)
    if lock_id is not None:
        raise DraftError(CheckCode.OPERATION_RECOVERY_REQUIRED.value, root / LOCK_RELATIVE)

    operation_id = validate_operation_id(
        plan.metadata.get("operation_id") or uuid.uuid4().hex, code="operation.plan_invalid"
    )
    directory = root / OPERATIONS_RELATIVE / operation_id
    existing_journal_path = directory / "journal.json"
    if existing_journal_path.is_file():
        # 采用预先耐久创建的 allocating 日志（如 trash 恢复关联），保留其元数据。
        existing = read_journal(existing_journal_path)
        validate_preallocated_journal(plan, existing)
        journal = existing
    else:
        journal = preallocate_journal(
            root,
            kind=plan.kind,
            operation_id=operation_id,
            validation_mode=plan.validation_mode,
            metadata=dict(plan.metadata),
        )
    acquire_lock(root, operation_id)
    return _execute_allocated_plan(
        plan, journal, lock_already_held=True, retain_terminal_lock=False
    )


def execute_preallocated_plan(
    plan: OperationPlan,
    *,
    retain_terminal_lock: bool,
) -> OperationResult:
    """执行已经预分配并已持有写锁的计划；恢复包装层用以保留终态锁。"""
    root = resolve_draft_root(plan.root)
    operation_id = require_plan_operation_id(plan)
    if read_lock(root) != operation_id:
        raise DraftError("operation.plan_invalid", root)
    journal = read_journal(operation_journal_path(root, operation_id))
    validate_preallocated_journal(plan, journal)
    return _execute_allocated_plan(
        plan,
        journal,
        lock_already_held=True,
        retain_terminal_lock=retain_terminal_lock,
    )


def _execute_allocated_plan(
    plan: OperationPlan,
    journal: Journal,
    *,
    lock_already_held: bool,
    retain_terminal_lock: bool,
) -> OperationResult:
    """执行已分配日志的 prepare/apply/validate/rollback 主体。

    调用方负责分配日志与（当 ``lock_already_held=True`` 时）持有写锁。
    ``retain_terminal_lock=True`` 时终态只耐久写入，不释放锁、不清理恢复副本，
    交由恢复包装层在证明终态后统一处理。
    """
    root = resolve_draft_root(plan.root)
    _validate_plan_paths(plan)
    operation_id = journal.operation_id
    directory = journal.directory
    if lock_already_held and read_lock(root) != operation_id:
        raise DraftError("operation.plan_invalid", root)

    originals = directory / "originals"
    staged = directory / "staged"
    originals.mkdir(exist_ok=True)
    staged.mkdir(exist_ok=True)
    mutations: list[JournalMutation] = []
    for index, mutation in enumerate(plan.mutations):
        current_hash = sha256_file(root / mutation.path)
        if current_hash != mutation.before_sha256:
            if not retain_terminal_lock:
                release_lock(root, operation_id)
            raise DraftError("operation.external_change", root / mutation.path)
        original_name: str | None = None
        staged_name: str | None = None
        if mutation.before_sha256 is not None:
            original_name = f"originals/{index:04d}.bin"
            durable_write(originals / f"{index:04d}.bin", (root / mutation.path).read_bytes())
        if mutation.kind is MutationKind.WRITE:
            if mutation.content is None or sha256_bytes(mutation.content) != (
                mutation.after_sha256
            ):
                if not retain_terminal_lock:
                    release_lock(root, operation_id)
                raise DraftError("operation.plan_invalid", root / mutation.path)
            staged_name = f"staged/{index:04d}.bin"
            durable_write(staged / f"{index:04d}.bin", mutation.content)
        mutations.append(
            JournalMutation(
                path=mutation.path,
                kind=mutation.kind,
                before_sha256=mutation.before_sha256,
                after_sha256=mutation.after_sha256,
                original=original_name,
                staged=staged_name,
                applied=False,
            )
        )
    changed_paths = public_changed_paths(plan.effects) if plan.effects else None
    metadata = dict(journal.metadata)
    if changed_paths is not None:
        metadata[PUBLIC_CHANGED_PATHS_KEY] = _encode_public_paths(changed_paths)
    prepared = Journal(
        operation_id=operation_id,
        kind=plan.kind,
        phase=OperationPhase.PREPARED,
        validation_mode=plan.validation_mode,
        mutations=tuple(mutations),
        remove_empty_directories=plan.remove_empty_directories,
        metadata={**plan.metadata, **metadata},
        directory=directory,
    )
    write_journal(prepared)

    for mutation in prepared.mutations:
        current_hash = sha256_file(root / mutation.path)
        if current_hash != mutation.before_sha256:
            return _rollback(
                root,
                prepared,
                retain_terminal_lock=retain_terminal_lock,
                changed_paths=changed_paths,
            )

    applying = _with_phase(prepared, OperationPhase.APPLYING)
    write_journal(applying)
    applied = _execute_apply_phase(root, applying)
    return _validate_and_complete(
        root,
        applied,
        retain_terminal_lock=retain_terminal_lock,
        changed_paths=changed_paths,
    )


PUBLIC_CHANGED_PATHS_KEY = "public_changed_paths"


def _encode_public_paths(paths: tuple[Path, ...]) -> str:
    return json.dumps([path.as_posix() for path in paths])


def _public_path_safe(path: Path) -> bool:
    """公开变化路径必须为相对、不含 ``..`` 且不位于 ``.draft/`` 内部。"""
    if path.is_absolute():
        return False
    if ".." in path.parts:
        return False
    return not (path.parts and path.parts[0] == INTERNAL_DIR)


def _journal_corrupt(journal: Journal) -> DraftError:
    return DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, journal.directory / "journal.json")


def _persisted_public_paths(journal: Journal) -> tuple[Path, ...] | None:
    """恢复时读取日志中持久化的公开变化路径。

    元数据存在但损坏（非法 JSON、类型错误或包含绝对/``..``/``.draft/`` 内部
    路径）时视为内部状态损坏，明确停止而不是回退猜测公开语义（规格 §13）；
    缺失（旧格式日志）返回 None 由调用方安全回退。
    """
    value = journal.metadata.get(PUBLIC_CHANGED_PATHS_KEY)
    if value is None:
        return None
    try:
        raw: object = json.loads(value)
    except json.JSONDecodeError as exc:
        raise _journal_corrupt(journal) from exc
    if not isinstance(raw, list):
        raise _journal_corrupt(journal)
    checked: list[Path] = []
    for entry in cast("list[object]", raw):
        if not isinstance(entry, str):
            raise _journal_corrupt(journal)
        path = Path(entry)
        if not _public_path_safe(path):
            raise _journal_corrupt(journal)
        checked.append(path)
    return tuple(checked)


def _recover_changed_paths(journal: Journal) -> tuple[Path, ...]:
    """恢复结果的公开路径：优先日志持久化的公开语义，旧日志回退到公开 mutation 子集。

    缺失元数据只回退到公开安全的 mutation 路径，绝不公开 ``.draft/`` 内部路径。
    """
    persisted = _persisted_public_paths(journal)
    if persisted is not None:
        return persisted
    public: list[Path] = []
    seen: set[Path] = set()
    for mutation in journal.mutations:
        if mutation.path in seen:
            continue
        if _public_path_safe(mutation.path):
            public.append(mutation.path)
            seen.add(mutation.path)
    return tuple(public)


def _classify(root: Path, mutation: JournalMutation) -> str:
    current = sha256_file(root / mutation.path)
    if current == mutation.before_sha256:
        return "before"
    if current == mutation.after_sha256:
        return "after"
    return "unknown"


def _verify_recovery_material(journal: Journal) -> None:
    for mutation in journal.mutations:
        if mutation.original is not None:
            original = journal.directory / mutation.original
            if sha256_file(original) != mutation.before_sha256:
                raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, original)
        if mutation.staged is not None:
            staged = journal.directory / mutation.staged
            if not staged.exists():
                continue  # 暂存缺失只阻断前向完成，回滚仍可从恢复副本进行
            if sha256_file(staged) != mutation.after_sha256:
                raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, staged)


def _recover_terminal(root: Path, journal: Journal, operation_id: str) -> OperationResult:
    """合法崩溃窗口：终态日志已耐久但锁未释放。验证文件状态后清理并释放锁。"""
    expected = "after" if journal.phase is OperationPhase.COMPLETED else "before"
    for mutation in journal.mutations:
        if _classify(root, mutation) != expected:
            raise DraftError("operation.external_change", root / mutation.path)
    _clean_recovery_copies(journal)
    for relative in journal.remove_empty_directories:
        with suppress(OSError):
            (root / relative).rmdir()
    release_lock(root, operation_id)
    return OperationResult(
        operation_id=operation_id,
        kind=journal.kind,
        phase=journal.phase,
        changed_paths=_recover_changed_paths(journal),
    )


def _recover_locked(root: Path, operation_id: str) -> OperationResult:
    journal = read_journal(operation_journal_path(root, operation_id))
    if journal.operation_id != operation_id:
        raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, journal.directory)
    if journal.phase in (OperationPhase.COMPLETED, OperationPhase.ROLLED_BACK):
        return _recover_terminal(root, journal, operation_id)
    if journal.phase is OperationPhase.ALLOCATING:
        # 完整计划尚未耐久形成：不得前向完成，只能放弃、清理分配材料并释放锁。
        abandoned = _with_phase(journal, OperationPhase.ROLLED_BACK)
        write_journal(abandoned)
        _clean_recovery_copies(journal)
        release_lock(root, operation_id)
        return OperationResult(
            operation_id=operation_id,
            kind=journal.kind,
            phase=OperationPhase.ROLLED_BACK,
            changed_paths=(),
        )
    if journal.phase not in _MID_WRITE_PHASES:
        raise DraftError(
            "operation.unsupported_phase",
            journal.directory,
            details=(f"阶段 {journal.phase.value} 由 trash 子系统恢复",),
        )
    _verify_recovery_material(journal)
    for mutation in journal.mutations:
        if _classify(root, mutation) == "unknown":
            raise DraftError("operation.external_change", root / mutation.path)
    return _finish_forward(root, journal, changed_paths=_recover_changed_paths(journal))


def recover(root_path: Path) -> OperationResult:
    root = resolve_draft_root(root_path)
    operation_id = read_lock(root)
    if operation_id is not None:
        return _recover_locked(root, operation_id)

    reject_symlink_components(root, OPERATIONS_RELATIVE)
    operations = root / OPERATIONS_RELATIVE
    if not operations.is_dir():
        return OperationResult(
            operation_id="", kind="recover", phase=OperationPhase.COMPLETED, changed_paths=()
        )
    for entry in sorted(operations.iterdir()):
        journal_path = entry / "journal.json"
        if not entry.is_dir() or not journal_path.is_file():
            continue
        journal = read_journal(journal_path)
        if journal.phase is OperationPhase.ALLOCATING:
            if "restores_trash_operation" in journal.metadata:
                # 关联恢复日志必须继续存在：无锁放弃为 rolled_back，保留审计事实。
                _clean_recovery_copies(journal)
                write_journal(_with_phase(journal, OperationPhase.ROLLED_BACK))
                continue
            for child in sorted(entry.rglob("*"), reverse=True):
                if child.is_file():
                    child.unlink()
                elif child.is_dir():
                    child.rmdir()
            entry.rmdir()
        elif (
            journal.phase in _MID_WRITE_PHASES
            or journal.phase is OperationPhase.TRASH_HANDOFF_SUCCEEDED
            or journal.phase is OperationPhase.TRASH_PURGE_COMMITTED
        ):
            raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, journal_path)
        # completed / rolled_back / trash_retained / trash_handoff_pending：终态或非写入状态，
        # trash 子系统负责推进 trash_retained 与 trash_handoff_pending。
    return OperationResult(
        operation_id="", kind="recover", phase=OperationPhase.COMPLETED, changed_paths=()
    )
