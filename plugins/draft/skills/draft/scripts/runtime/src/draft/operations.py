from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from collections.abc import Callable, Mapping, Set
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Protocol, cast

from draft.diagnostic_codes import CheckCode
from draft.errors import DraftError
from draft.graph import INTERNAL_DIR, MARKER_PATH
from draft.models import (
    MutationKind,
    OperationKind,
    OperationPhase,
    OperationPlan,
    OperationResult,
    RootState,
)
from draft.observations import EntryKind, Observation, ObservationManifest, ObservationProbe
from draft.paths import resolve_draft_root
from draft.plan_effects import public_changed_paths


class _MutationLike(Protocol):
    @property
    def path(self) -> Path: ...


JOURNAL_VERSION = 3
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
    # 恢复副本路径：read_journal 单一解析边界验证后的受约束相对路径，绝不接受
    # 裸字符串。消费者只接触这些 Path，禁止字符串拼接决定 I/O 边界。
    original: Path | None
    staged: Path | None
    applied: bool


@dataclass(frozen=True)
class Journal:
    operation_id: str
    kind: OperationKind
    phase: OperationPhase
    root_state: RootState
    observations: ObservationManifest
    mutations: tuple[JournalMutation, ...]
    remove_empty_directories: tuple[Path, ...]
    metadata: dict[str, str]
    directory: Path


def _journal_to_json(journal: Journal) -> bytes:
    payload = {
        "journal_version": JOURNAL_VERSION,
        "operation_id": journal.operation_id,
        "kind": journal.kind.value,
        "phase": journal.phase.value,
        "root_state": journal.root_state.value,
        "observations": [
            {
                "path": observation.path.as_posix(),
                "kind": observation.kind.value,
                "sha256": observation.sha256,
                "members": list(observation.members),
            }
            for observation in journal.observations.entries
        ],
        "mutations": [
            {
                "path": mutation.path.as_posix(),
                "kind": mutation.kind.value,
                "before_sha256": mutation.before_sha256,
                "after_sha256": mutation.after_sha256,
                "original": (
                    mutation.original.as_posix() if mutation.original is not None else None
                ),
                "staged": mutation.staged.as_posix() if mutation.staged is not None else None,
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


def _valid_sha256(value: str) -> bool:
    return re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _validate_journal_path(value: str, journal_path: Path) -> Path:
    relative = PurePosixPath(value)
    if not relative.parts or relative.is_absolute() or ".." in relative.parts:
        raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, journal_path)
    # 拒绝显式 `.` 或空段（非规范拼写）：PurePosixPath 会归一化它们，纯语义校验。
    if any(part in ("", ".", "..") for part in value.split("/")):
        raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, journal_path)
    return Path(*relative.parts)


def _validate_observation_path(value: str, journal_path: Path) -> Path:
    """观察路径校验：根目录观察的合法协议值是 ``.``；其余路径按严格路径校验。"""
    if value == ".":
        return Path(".")
    return _validate_journal_path(value, journal_path)


def _recovery_path_for(
    value: str | None,
    *,
    expected: Path,
    journal_path: Path,
) -> Path | None:
    """original/staged 必须在单一解析边界精确等于协议形状（``originals/{i:04d}.bin`` 等）。

    绝对路径、``..``、错误子目录、错误 index 和非规范拼写都在任何 I/O 之前被拒绝；
    消费者只拿到受约束的相对 Path，禁止字符串拼接决定 I/O 边界。
    """
    if value is None:
        return None
    if value != expected.as_posix():
        raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, journal_path)
    return expected


def _parse_observations(data: object, journal_path: Path) -> ObservationManifest:
    from draft.observations import EntryKind, Observation, observation_shape_error

    if not isinstance(data, list):
        raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, journal_path)
    entries: list[Observation] = []
    seen: set[Path] = set()
    for raw in cast("list[object]", data):
        if not isinstance(raw, dict):
            raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, journal_path)
        item = cast("dict[str, object]", raw)
        kind = EntryKind(_expect_str(item.get("kind"), journal_path))
        sha256 = _expect_optional_str(item.get("sha256"), journal_path)
        if sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, journal_path)
        members_raw = item.get("members")
        if not isinstance(members_raw, list) or not all(
            isinstance(member, str) for member in cast("list[object]", members_raw)
        ):
            raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, journal_path)
        path = _validate_observation_path(_expect_str(item.get("path"), journal_path), journal_path)
        if path in seen:
            raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, journal_path)
        seen.add(path)
        entry = Observation(
            path=path,
            kind=kind,
            sha256=sha256,
            members=tuple(cast("list[str]", members_raw)),
        )
        if observation_shape_error(entry) is not None:
            raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, journal_path)
        entries.append(entry)
    return ObservationManifest(entries=tuple(entries))


def read_journal(journal_path: Path) -> Journal:
    if not journal_path.is_file():
        raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, journal_path)
    try:
        decoded = json.loads(journal_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, journal_path) from exc
    return _parse_journal(decoded, journal_path)


def _parse_journal(data: object, journal_path: Path) -> Journal:
    """纯解析：无文件系统访问，可在单测中直接构造畸形输入。

    在单一解析边界完成全部形状与协议验证：路径、hash 形状、kind/hash 组合、
    original/staged 协议形状与唯一性。任何 I/O 消费者只接触已验证对象，
    恢复材料路径不得由字符串拼接决定 I/O 边界（回归 H5）。
    """
    if not isinstance(data, dict):
        raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, journal_path)
    parsed = cast("dict[str, object]", data)
    version = parsed.get("journal_version")
    if version != JOURNAL_VERSION:
        raise DraftError(
            "operation.journal_version_unsupported",
            journal_path,
            details=(str(version), str(JOURNAL_VERSION)),
        )
    try:
        kind = OperationKind(_expect_str(parsed.get("kind"), journal_path))
        phase = OperationPhase(_expect_str(parsed.get("phase"), journal_path))
        root_state = RootState(_expect_str(parsed.get("root_state"), journal_path))
        observations = _parse_observations(parsed.get("observations"), journal_path)
        mutations_raw = parsed.get("mutations")
        if not isinstance(mutations_raw, list):
            raise TypeError("mutations must be a list")
        mutations: list[JournalMutation] = []
        seen_paths: set[Path] = set()
        seen_originals: set[Path] = set()
        seen_staged: set[Path] = set()
        for index, entry in enumerate(cast("list[object]", mutations_raw)):
            if not isinstance(entry, dict):
                raise TypeError("mutation must be an object")
            item = cast("dict[str, object]", entry)
            applied = item.get("applied")
            if not isinstance(applied, bool):
                raise TypeError("applied must be a boolean")
            mutation_kind = MutationKind(_expect_str(item.get("kind"), journal_path))
            before = _expect_optional_str(item.get("before_sha256"), journal_path)
            after = _expect_optional_str(item.get("after_sha256"), journal_path)
            if before is not None and not _valid_sha256(before):
                raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, journal_path)
            if after is not None and not _valid_sha256(after):
                raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, journal_path)
            if mutation_kind is MutationKind.DELETE:
                # 删除针对既存文件：before 必须非空，after 恒为 None。
                if before is None or after is not None:
                    raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, journal_path)
            else:
                # WRITE 必须有 after（新建 before=None 或改写 before 非空）。
                if after is None:
                    raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, journal_path)
            path = _validate_journal_path(_expect_str(item.get("path"), journal_path), journal_path)
            if path in seen_paths:
                raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, journal_path)
            seen_paths.add(path)
            original = _recovery_path_for(
                _expect_optional_str(item.get("original"), journal_path),
                expected=Path("originals") / f"{index:04d}.bin",
                journal_path=journal_path,
            )
            staged = _recovery_path_for(
                _expect_optional_str(item.get("staged"), journal_path),
                expected=Path("staged") / f"{index:04d}.bin",
                journal_path=journal_path,
            )
            # 协议形状：original 当且仅当 before!=None；staged 当且仅当 WRITE。
            if (original is None) != (before is None):
                raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, journal_path)
            if mutation_kind is MutationKind.DELETE:
                if staged is not None:
                    raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, journal_path)
            elif staged is None:
                raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, journal_path)
            if original is not None:
                if original in seen_originals:
                    raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, journal_path)
                seen_originals.add(original)
            if staged is not None:
                if staged in seen_staged:
                    raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, journal_path)
                seen_staged.add(staged)
            mutations.append(
                JournalMutation(
                    path=path,
                    kind=mutation_kind,
                    before_sha256=before,
                    after_sha256=after,
                    original=original,
                    staged=staged,
                    applied=applied,
                )
            )
        directories_raw = parsed.get("remove_empty_directories")
        if not isinstance(directories_raw, list):
            raise TypeError("remove_empty_directories must be a list")
        metadata_raw = parsed.get("metadata")
        if not isinstance(metadata_raw, dict):
            raise TypeError("metadata must be an object")
    except (TypeError, ValueError) as exc:
        raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, journal_path) from exc
    metadata = {
        _expect_str(key, journal_path): _expect_str(value, journal_path)
        for key, value in cast("dict[object, object]", metadata_raw).items()
    }
    directory_id = validate_operation_id(journal_path.parent.name)
    operation_id = validate_operation_id(_expect_str(parsed.get("operation_id"), journal_path))
    if operation_id != directory_id:
        raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, journal_path)
    return Journal(
        operation_id=operation_id,
        kind=kind,
        phase=phase,
        root_state=root_state,
        observations=observations,
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
        root_state=journal.root_state,
        observations=journal.observations,
        mutations=journal.mutations,
        remove_empty_directories=journal.remove_empty_directories,
        metadata=journal.metadata,
        directory=journal.directory,
    )


def with_observations(journal: Journal, observations: ObservationManifest) -> Journal:
    """公开的不可变日志观察清单替换；restore 在规划后把读集观察耐久写入日志。"""
    return Journal(
        operation_id=journal.operation_id,
        kind=journal.kind,
        phase=journal.phase,
        root_state=journal.root_state,
        observations=observations,
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
        root_state=journal.root_state,
        observations=journal.observations,
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
        root_state=journal.root_state,
        observations=journal.observations,
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
        root_state=journal.root_state,
        observations=journal.observations,
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
            marker_mutation = relative == MARKER_PATH and plan.kind in (
                OperationKind.GENERATE_CONCLUSIONS,
                OperationKind.ADOPT,
            )
            trash_package = plan.kind is OperationKind.TRASH and relative.is_relative_to(
                trash_outgoing
            )
            if not (trash_package or marker_mutation):
                raise DraftError("path.escape", relative)
        cursor = plan.root
        for part in relative.parts:
            cursor /= part
            if cursor.is_symlink():
                raise DraftError(CheckCode.PATH_SYMLINK_ESCAPE.value, cursor)


def _check(root: Path) -> None:
    from draft.checker import check_draft

    report = check_draft(root, include_operation_state=False)
    if report.errors:
        codes = ", ".join(issue.code for issue in report.errors)
        raise DraftError("operation.validation_failed", root, details=(codes,))


def _mutation_temporary_path(target: Path, operation_id: str, index: int) -> Path:
    """WRITE 目标的精确临时相对路径：与 operation_id + mutation index 绑定。

    所有权证据是精确路径，不是文件名形状：同名不同目录或未登记的 temp-like 文件
    一律按普通目录成员处理（恢复时按成员不符拒绝）。崩溃遗留的同名精确临时文件由
    恢复复核类型与摘要后清理或替换。
    """
    digest = hashlib.sha256(f"{operation_id}:{index}".encode()).hexdigest()[:16]
    return target.with_name(f"{target.name}.tmp-{digest}")


def _apply_write(
    root: Path, target: Path, content: bytes, *, temporary: Path | None = None
) -> None:
    destination = root / target
    cursor = root
    for part in target.parts[:-1]:
        cursor /= part
        if cursor.is_symlink():
            raise DraftError(CheckCode.PATH_SYMLINK_ESCAPE.value, cursor)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if temporary is None:
        temporary = destination.with_name(f"{destination.name}.tmp-{uuid.uuid4().hex}")
    else:
        temporary = root / temporary
        reject_symlink_components(root, temporary.relative_to(root))
    if temporary.exists() or temporary.is_symlink():
        # 崩溃遗留的同名精确临时文件：复核普通文件类型与内容后替换，不重复创建。
        if temporary.is_symlink() or not temporary.is_file():
            raise DraftError(CheckCode.PATH_SYMLINK_ESCAPE.value, temporary)
        if temporary.read_bytes() != content:
            raise DraftError("operation.external_change", temporary)
        replace_file(temporary, destination)
        fsync_directory(destination.parent)
        return
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
    index_by_path = {m.path: index for index, m in enumerate(current.mutations)}
    for mutation in [*writes, *deletes]:
        if mutation.applied:
            continue
        if mutation.kind is MutationKind.WRITE:
            staged = current.directory / cast("Path", mutation.staged)
            staged_hash = sha256_file(staged)
            if staged_hash is None:
                raise DraftError("operation.staged_missing", staged)
            temporary = _mutation_temporary_path(
                mutation.path, current.operation_id, index_by_path[mutation.path]
            )
            _apply_write(root, mutation.path, staged.read_bytes(), temporary=temporary)
        else:
            _apply_delete(root, mutation.path)
        current = _with_applied(current, mutation.path)
        write_journal(current)
    return current


def _clean_recovery_copies(journal: Journal) -> None:
    """按日志登记路径精确清理恢复副本（originals/staged）。

    只删除 mutation.original/mutation.staged 登记的文件（复核普通文件类型与锚定
    摘要）；已知空目录 rmdir。未知成员、符号链接、错误类型或摘要不符以
    operation.inconsistent_state 停止并保留现场，绝不递归删除恢复目录中的未知
    内容。已登记文件部分缺失（提交点后幂等清理）容忍。
    """
    for sub in ("originals", "staged"):
        directory = journal.directory / sub
        if not directory.exists():
            continue
        if directory.is_symlink() or not directory.is_dir():
            raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, directory)
        registered: dict[str, str] = {}  # 文件名 -> 锚定摘要
        for mutation in journal.mutations:
            if sub == "originals":
                if (
                    mutation.original is not None
                    and mutation.before_sha256 is not None
                    and mutation.original.parent == Path(sub)
                ):
                    registered[mutation.original.name] = mutation.before_sha256
            elif (
                mutation.staged is not None
                and mutation.after_sha256 is not None
                and mutation.staged.parent == Path(sub)
            ):
                registered[mutation.staged.name] = mutation.after_sha256
        if registered:
            # 有登记 mutation：严格只删登记文件，未知成员/类型/摘要不符拒绝。
            unknown = {entry.name for entry in directory.iterdir()} - set(registered)
            if unknown:
                raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, directory)
            for name in sorted(registered):
                target = directory / name
                if not target.exists():
                    continue  # 提交点后部分清理幂等容忍。
                if target.is_symlink() or not target.is_file():
                    raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, target)
                if sha256_bytes(target.read_bytes()) != registered[name]:
                    raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, target)
                target.unlink()
        else:
            # 无登记 mutation（allocating/prepare 崩溃）：目录是协议持有的 prepare
            # 暂存，删除其全部普通文件；符号链接或目录仍是未知内容，拒绝。
            for entry in directory.iterdir():
                if entry.is_symlink() or not entry.is_file():
                    raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, entry)
                entry.unlink()
        directory.rmdir()


def finalize_operation(
    root: Path,
    journal: Journal,
    phase: OperationPhase,
    plan_kind: OperationKind,
    *,
    retain_terminal_lock: bool = False,
    changed_paths: tuple[Path, ...] | None = None,
) -> OperationResult:
    """Mark a terminal phase; by default clean recovery copies then release the lock.

    ``retain_terminal_lock=True`` 仅耐久写入终态并移除空目录，不释放写锁、不清理恢复副本，
    供恢复包装层在证明终态后统一解关联、清理与释放。``changed_paths`` 由调用方从
    公开效果派生；缺省回退到日志 mutation 路径（恢复等无公开效果边界）。

    顺序为终态日志耐久 → 安全清理已授权恢复材料 → 释放锁：清理遇到未知成员/类型
    错误时保留锁与现场，CLI 报错后 recover 仍能凭锁定位，不会出现"报错时根内已无锁"
    （回归 H5）。
    """
    terminal = _with_phase(journal, phase)
    write_journal(terminal)
    if not retain_terminal_lock:
        _clean_recovery_copies(journal)
        release_lock(root, journal.operation_id)
    for relative in journal.remove_empty_directories:
        with suppress(OSError):
            (root / relative).rmdir()
    if changed_paths is None:
        changed_paths = tuple(mutation.path for mutation in journal.mutations)
    return OperationResult(
        operation_id=journal.operation_id,
        kind=plan_kind.value,
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
            original = journal.directory / cast("Path", mutation.original)
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
        staged = journal.directory / cast("Path", mutation.staged)
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
    if journal.root_state is RootState.REGISTERED:
        # registered 根的应用后完整检查是事务提交前的二次确认。
        try:
            _check(root)
        except DraftError as error:
            if error.code == "operation.validation_failed":
                _rollback(
                    root,
                    current,
                    retain_terminal_lock=retain_terminal_lock,
                    changed_paths=changed_paths,
                )
                raise DraftError(
                    "operation.validation_failed", root, details=error.details
                ) from error
            raise
    else:
        # candidate 根已由操作级门禁与观察复核保证：只核对 mutation 应用后摘要。
        for mutation in current.mutations:
            if sha256_file(root / mutation.path) != mutation.after_sha256:
                raise DraftError("operation.external_change", root / mutation.path)
    if current.kind is OperationKind.TRASH:
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
    kind: OperationKind,
    operation_id: str,
    root_state: RootState,
    observations: ObservationManifest,
    metadata: dict[str, str],
) -> Journal:
    """统一预分配：独占创建操作目录、同步父目录项、耐久写入 allocating 日志。

    父目录 fsync 保证“引用方已耐久时，被引用的操作目录必然存在”。
    已存在的操作目录绝不复用：直接报 operation.id_conflict，不打开也不改写其内容。
    """
    validate_operation_id(operation_id, code="operation.plan_invalid")
    if kind is OperationKind.RESTORE:
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
        root_state=root_state,
        observations=observations,
        mutations=(),
        remove_empty_directories=(),
        metadata=dict(metadata),
        directory=directory,
    )
    write_journal(journal)
    return journal


def validate_restore_link(root: Path, restore_journal: Journal) -> Journal:
    """唯一双向关联验证：恢复日志必须指向回收日志，且回收日志必须回指本恢复操作。"""
    if restore_journal.kind is not OperationKind.RESTORE:
        raise DraftError("operation.plan_invalid", restore_journal.directory)
    back_reference = restore_journal.metadata.get("restores_trash_operation")
    if back_reference is None:
        raise DraftError("operation.plan_invalid", restore_journal.directory)
    back_id = validate_operation_id(back_reference, code="operation.plan_invalid")
    back_journal = read_journal(operation_journal_path(root, back_id))
    if (
        back_journal.kind is not OperationKind.TRASH
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
    if journal.kind is OperationKind.RESTORE:
        validate_restore_link(plan.root, journal)


def _verify_plan_observations(root: Path, journal: Journal) -> None:
    """锁内、首次业务变更前复核观察清单；差异即外部变化，不应用 mutation。"""
    changed = _verify_observations_for_recovery(root, journal, explained_mutations=())
    if changed:
        raise DraftError("operation.external_change", root / changed[0])


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
        created_journal = False
    else:
        journal = preallocate_journal(
            root,
            kind=plan.kind,
            operation_id=operation_id,
            root_state=plan.root_state,
            observations=plan.observations,
            metadata=dict(plan.metadata),
        )
        created_journal = True
    try:
        acquire_lock(root, operation_id)
    except DraftError:
        # 锁竞争失败（另一进程抢先取得锁）。只有"本次创建且锁属其他操作"时才可
        # 终态化本次未授权日志；采用既有日志或锁属同一操作时不得改写共享日志或
        # 清理副本（可能破坏获胜者的恢复证据），一律报告恢复未决。
        holder = read_lock(root)
        if created_journal and holder is not None and holder != operation_id:
            write_journal(with_phase(journal, OperationPhase.ROLLED_BACK))
            _clean_recovery_copies(journal)
        raise
    try:
        _verify_plan_observations(root, journal)
    except DraftError:
        # 首次业务变更前的复核失败：本次准备日志尚未授权任何根变更，安全终态化
        # 并释放锁，不遗留锁或中段日志（计划 Task 7 Step 4）。
        write_journal(with_phase(journal, OperationPhase.ROLLED_BACK))
        _clean_recovery_copies(journal)
        release_lock(root, operation_id)
        raise
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
    # 预分配执行同样复核读集观察（如 restore 的权威包）：计划与日志的读集在
    # 首次业务变更前必须与当前事实一致。
    _verify_plan_observations(root, journal)
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
        original_name: Path | None = None
        staged_name: Path | None = None
        if mutation.before_sha256 is not None:
            # journal 字段只存根内相对路径（解析边界验证形状）；文件写到绝对目录。
            original_name = Path("originals") / f"{index:04d}.bin"
            durable_write(originals / f"{index:04d}.bin", (root / mutation.path).read_bytes())
        if mutation.kind is MutationKind.WRITE:
            if mutation.content is None or sha256_bytes(mutation.content) != (
                mutation.after_sha256
            ):
                if not retain_terminal_lock:
                    release_lock(root, operation_id)
                raise DraftError("operation.plan_invalid", root / mutation.path)
            staged_name = Path("staged") / f"{index:04d}.bin"
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
        root_state=plan.root_state,
        observations=journal.observations,
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
        kind=journal.kind.value,
        phase=journal.phase,
        changed_paths=_recover_changed_paths(journal),
    )


def _plan_time_missing_index(
    observations: ObservationManifest,
) -> Callable[[Path], bool]:
    """构建 O(1) 的"计划期缺失"查询（按深度传递：父缺失则子缺失）。

    只有正向证据才成立：目录自身是 MISSING 观察，或其父目录观察明确未列出该名字，
    或其父目录自身也可证明计划期缺失（传递）。没有任何存在性证据时保守判定为已
    存在，避免把已存在但未观察的共享目录（如 EMPTY_OBSERVATIONS 夹具下的 .draft）
    误判为缺失。一次性索引，查询不重新扫描观察清单。
    """
    path_to_entry = {entry.path: entry for entry in observations.entries}
    memo: dict[Path, bool] = {}

    def is_missing(directory: Path) -> bool:
        cached = memo.get(directory)
        if cached is not None:
            return cached
        entry = path_to_entry.get(directory)
        if entry is not None:
            result = entry.kind is EntryKind.MISSING
            memo[directory] = result
            return result
        parent = directory.parent
        if parent == directory:
            memo[directory] = False
            return False
        parent_entry = path_to_entry.get(parent)
        if parent_entry is not None:
            # 父观察为目录：成员里没有它则缺失；父为文件/符号链接：子目录不可能存在。
            result = (
                parent_entry.kind is not EntryKind.DIRECTORY
                or directory.name not in parent_entry.members
            )
            memo[directory] = result
            return result
        result = is_missing(parent)
        memo[directory] = result
        return result

    return is_missing


def adjusted_observation_manifest(
    observations: ObservationManifest,
    *,
    excluded: frozenset[Path],
    explained: frozenset[Path],
    added: Mapping[Path, Set[str]],
    removed: Mapping[Path, Set[str]],
    created_dirs: frozenset[Path] = frozenset(),
) -> ObservationManifest:
    """纯函数：按本操作可解释的目录成员变化调整观察清单。

    只排除本操作目录/锁与 explained mutation 路径；目录成员调整为"若只发生本操作
    mutation 应有的成员"，再与当前状态比较，避免把操作自身副作用误判为外部修改。

    计划期不存在的目录没有观察基线：本操作实际创建（``created_dirs``，目标文件
    尚未达到 after 时成员集合允许为空）按"只含本操作创建成员"合成精确期望（MISSING
    观察转换或未观察目录补建），恢复才能证明新目录内部只含本操作条目、不吸收外部
    注入（如 promote 首次创建 pages/topic/）。目录出现不等于目录内任意内容也由本
    操作创建。不访问文件系统，可独立单测。
    """
    from draft.observations import (
        EntryKind,
        ObservationManifest,
        directory_observation,
    )

    observed_paths = {entry.path for entry in observations.entries}
    is_missing = _plan_time_missing_index(observations)

    adjusted: list[Observation] = []
    for observation in observations.entries:
        if observation.path in excluded or observation.path in explained:
            continue
        if observation.kind is EntryKind.DIRECTORY:
            planned = set(observation.members)
            expected = tuple(
                sorted(
                    (planned - removed.get(observation.path, set()))
                    | added.get(observation.path, set())
                )
            )
            adjusted.append(directory_observation(observation.path, expected))
        elif observation.kind is EntryKind.MISSING and (
            observation.path in added or observation.path in created_dirs
        ):
            # 计划期缺失的目录被本操作创建：期望恰好是操作创建的成员集合（可为空）。
            adjusted.append(
                directory_observation(
                    observation.path, tuple(sorted(added.get(observation.path, set())))
                )
            )
        else:
            adjusted.append(observation)
    for directory in sorted(set(added) | set(created_dirs)):
        if directory in excluded or directory in explained:
            continue
        if directory in observed_paths:
            continue  # 已有观察：DIRECTORY 已在上方调整，MISSING 已在上方转换。
        if not is_missing(directory):
            continue  # 无法证明计划期缺失：保守不合成（避免误判已存在共享目录）。
        adjusted.append(
            directory_observation(directory, tuple(sorted(added.get(directory, set()))))
        )
    return ObservationManifest(entries=tuple(adjusted))


def explained_directory_deltas(
    *,
    created_paths: frozenset[Path],
    deleted_paths: frozenset[Path],
) -> tuple[dict[Path, set[str]], dict[Path, set[str]]]:
    """纯函数：按本操作实际创建/删除的路径推导可解释目录成员增删。

    ``created_paths``（before=None→after 的新建文件、操作日志目录、锁文件）每个路径
    的完整祖先链都会新增其直接子组件（含实际创建的缺失祖先目录，如 promote 首次创建
    pages/topic/ 时 pages 新增的 topic）；``deleted_paths`` 只改变直接父目录成员。
    不访问文件系统，可独立单测。
    """
    added: dict[Path, set[str]] = {}
    removed: dict[Path, set[str]] = {}
    for path in created_paths:
        current = path
        while current.parent != current:
            added.setdefault(current.parent, set()).add(current.name)
            current = current.parent
    for path in deleted_paths:
        removed.setdefault(path.parent, set()).add(path.name)
    return added, removed


def _classify_observation(mutation: JournalMutation, observed: Observation) -> str:
    """从探针观察按 EntryKind 分类 mutation 的活状态（before/after/unknown）。

    sha256=None 对 MISSING/DIRECTORY/SYMLINK/OTHER 都成立，不能仅凭摘要判断：
    - DELETE（after=None）：MISSING 为 after；FILE 摘要匹配 before 为 before；
      目录/符号链接等路径类型变化均为 unknown。
    - WRITE（after!=None）：FILE 摘要匹配 after/before；新建文件（before=None）
      只在 MISSING（文件不存在）时为 before，目录/符号链接等均为 unknown。
    """
    if mutation.after_sha256 is None:  # DELETE：文件消失为 after。
        if observed.kind is EntryKind.MISSING:
            return "after"
        if observed.kind is EntryKind.FILE and observed.sha256 == mutation.before_sha256:
            return "before"
        return "unknown"
    if observed.kind is EntryKind.FILE:
        if observed.sha256 == mutation.after_sha256:
            return "after"
        if observed.sha256 == mutation.before_sha256:
            return "before"
        return "unknown"
    if mutation.before_sha256 is None:
        # 新建文件：文件不存在为 before；目录/符号链接/其他不可解释为该文件的前态。
        if observed.kind is EntryKind.MISSING:
            return "before"
        return "unknown"
    return "unknown"


def _verify_observations_for_recovery(
    root: Path,
    journal: Journal,
    explained_mutations: tuple[_MutationLike, ...],
    *,
    probe: ObservationProbe | None = None,
) -> tuple[Path, ...]:
    """恢复时复核观察：只排除本操作目录、锁与 explained mutation 路径。

    目录成员变化以探针观察的活文件 before/after 分类推导，不能信任可能滞后的
    applied 标志：文件落盘发生在 applied=true 日志持久化之前，二者之间崩溃时
    applied=false 但文件已是 after，仍应解释该成员变化。remove_empty_directories
    只在终态推进时执行，中段恢复时对应目录仍存在，不应计入。``probe`` 缺省为文件
    系统探针，单测可注入 fake 探针；比较探针统一包装临时文件过滤。
    """
    from draft.observations import (
        EntryKind,
        FilesystemObservationProbe,
        verify_observations,
    )

    if probe is None:
        probe = FilesystemObservationProbe(root)
    excluded = {journal.directory.relative_to(root), LOCK_RELATIVE}
    explained = {mutation.path for mutation in explained_mutations}
    created_paths = {journal.directory.relative_to(root), LOCK_RELATIVE}
    deleted_paths: set[Path] = set()
    # 从全部计划创建目标（WRITE-before-None mutation、操作日志目录、锁）的祖先链
    # 推导"本操作可能创建的目录闭包"；只把当前确实存在（本操作已 mkdir）的目录
    # 计入可解释创建。不等到目标文件达到 after 才承认目录出现：mkdir 后、目标文件
    # 替换前崩溃时，本操作创建的空目录不得被误判为外部变化。created_dirs 单独保留，
    # 供合成"成员可为空"的明确观察（回归 H3）。
    is_missing = _plan_time_missing_index(journal.observations)
    plan_created_targets = [
        mutation.path
        for mutation in journal.mutations
        if mutation.kind is MutationKind.WRITE and mutation.before_sha256 is None
    ]
    created_dirs: set[Path] = set()
    for target in [*plan_created_targets, journal.directory.relative_to(root), LOCK_RELATIVE]:
        current = target.parent
        while is_missing(current):
            if probe.observe(current).kind is EntryKind.DIRECTORY:
                created_paths.add(current)
                created_dirs.add(current)
            current = current.parent
            if current == current.parent:
                break
    for mutation in journal.mutations:
        state = _classify_observation(mutation, probe.observe(mutation.path))
        if state != "after":
            continue
        if mutation.kind is MutationKind.WRITE and mutation.before_sha256 is None:
            created_paths.add(mutation.path)
        elif mutation.kind is MutationKind.DELETE:
            deleted_paths.add(mutation.path)
    added, removed = explained_directory_deltas(
        created_paths=frozenset(created_paths),
        deleted_paths=frozenset(deleted_paths),
    )
    adjusted = adjusted_observation_manifest(
        journal.observations,
        excluded=frozenset(excluded),
        explained=frozenset(explained),
        added=added,
        removed=removed,
        created_dirs=frozenset(created_dirs),
    )

    owned_temp_paths = frozenset(
        _mutation_temporary_path(mutation.path, journal.operation_id, index)
        for index, mutation in enumerate(journal.mutations)
        if mutation.kind is MutationKind.WRITE
    )

    class _RecoveryProbe:
        """比较探针：只忽略本操作日志登记的精确临时路径（operation_id + index 绑定）。

        所有权证据是精确路径，不是文件名形状：预存 temp-like 合法文件不得被过滤
        （否则计划观察与 live 成员不一致，误报 external_change）；未登记的 temp-like
        入侵者（即使与目标同基础名）必须保留在成员里（否则被静默吸收）。
        """

        def __init__(self, inner: ObservationProbe) -> None:
            self._inner = inner

        def observe(self, path: Path) -> Observation:
            observed = self._inner.observe(path)
            if observed.kind is EntryKind.DIRECTORY:
                members = tuple(
                    member for member in observed.members if (path / member) not in owned_temp_paths
                )
                return Observation(path=path, kind=EntryKind.DIRECTORY, members=members)
            return observed

    return verify_observations(_RecoveryProbe(probe), adjusted, explained_mutations=())


def _recover_adopt(root: Path, journal: Journal, operation_id: str) -> OperationResult:
    """按 marker before/after 单向收敛 adopt 登记事务（规格 §12.2）。

    adopt 日志只有 marker 一项 mutation：before=candidate、after=registered。
    marker 已是 after 时只完成提交与清理，绝不回滚根身份。
    """
    marker_mutations = [mutation for mutation in journal.mutations if mutation.path == MARKER_PATH]
    if len(marker_mutations) != 1:
        raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, journal.directory)
    marker = marker_mutations[0]
    current = _classify(root, marker)

    if journal.phase in (OperationPhase.COMPLETED, OperationPhase.ROLLED_BACK):
        expected = "after" if journal.phase is OperationPhase.COMPLETED else "before"
        if current != expected:
            raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, journal.directory)
        _clean_recovery_copies(journal)
        release_lock(root, operation_id)
        return OperationResult(
            operation_id=operation_id,
            kind=journal.kind.value,
            phase=journal.phase,
            changed_paths=(MARKER_PATH,),
        )
    if current == "unknown":
        raise DraftError("operation.external_change", root / MARKER_PATH)
    if current == "after":
        # 登记已生效（marker 已是 registered）：只完成日志提交、清理并解锁。
        committed = _with_phase(journal, OperationPhase.COMPLETED)
        write_journal(committed)
        _clean_recovery_copies(journal)
        release_lock(root, operation_id)
        return OperationResult(
            operation_id=operation_id,
            kind=journal.kind.value,
            phase=OperationPhase.COMPLETED,
            changed_paths=(MARKER_PATH,),
        )
    # marker 仍是 before（candidate）：复核观察清单；未变则前向完成，变化则放弃本次 adopt。
    changed = _verify_observations_for_recovery(root, journal, explained_mutations=(marker,))
    if changed:
        rolled = _with_phase(journal, OperationPhase.ROLLED_BACK)
        write_journal(rolled)
        _clean_recovery_copies(journal)
        release_lock(root, operation_id)
        # 外部修改必须可观察："已安全放弃 adopt" 不等于恢复成功（规格 §12.2）。
        # 已证明 marker 未提交且回滚安全，释放锁后仍报告外部变化。
        raise DraftError("operation.external_change", root / changed[0])
    return _finish_forward(root, journal, changed_paths=(MARKER_PATH,))


def _clean_owned_temp_artifacts(root: Path, journal: Journal) -> None:
    """清理本操作崩溃遗留、目标尚未替换的同名临时文件（精确路径 + 摘要复核）。

    在观察复核通过后调用：前向完成会重新创建目标并替换，旧临时文件是孤儿。临时
    文件必须与对应 mutation 的 after 摘要一致；类型不符或摘要不符即外部篡改，
    停止并保留现场，不能当作本操作副作用静默清除。只处理日志登记的精确临时路径，
    不扫描父目录猜测候选。
    """
    for index, mutation in enumerate(journal.mutations):
        if mutation.kind is not MutationKind.WRITE or mutation.after_sha256 is None:
            continue
        target = root / mutation.path
        if target.exists() or target.is_symlink():
            continue  # 目标已就位：临时文件已被 replace 消耗。
        temporary = root / _mutation_temporary_path(mutation.path, journal.operation_id, index)
        if not temporary.exists():
            continue
        if temporary.is_symlink() or not temporary.is_file():
            raise DraftError("operation.external_change", temporary)
        if sha256_file(temporary) != mutation.after_sha256:
            raise DraftError("operation.external_change", temporary)
        temporary.unlink()
        fsync_directory(temporary.parent)


def _recover_locked(root: Path, operation_id: str) -> OperationResult:
    journal = read_journal(operation_journal_path(root, operation_id))
    if journal.operation_id != operation_id:
        raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, journal.directory)
    if journal.kind is OperationKind.ADOPT:
        return _recover_adopt(root, journal, operation_id)
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
            kind=journal.kind.value,
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
    # 恢复前向完成必须复核观察清单：mutation 的 before/after 分类是可解释变化，
    # 其余被观察文件或目录成员的差异是无法解释的外部修改，保留现场（计划 Task 7
    # Step 4），不能把依赖旧读集语义的结果静默提交。
    changed = _verify_observations_for_recovery(
        root, journal, explained_mutations=journal.mutations
    )
    if changed:
        raise DraftError("operation.external_change", root / changed[0])
    _clean_owned_temp_artifacts(root, journal)
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
            # 无锁 allocating 碎片：协议明确拥有 journal.json 与 originals/staged
            # 目录（prepare 阶段创建）；其余未知条目报 inconsistent_state，不得用
            # rglob 递归删除任意内容（回归 H5）。
            known_names = {"journal.json", "originals", "staged"}
            extra = [path for path in entry.iterdir() if path.name not in known_names]
            if extra:
                raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, entry)
            (entry / "journal.json").unlink()
            for sub in ("originals", "staged"):
                subdir = entry / sub
                if subdir.is_symlink() or (subdir.exists() and not subdir.is_dir()):
                    raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, subdir)
                if subdir.is_dir():
                    for child in subdir.iterdir():
                        child.unlink()
                    subdir.rmdir()
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
