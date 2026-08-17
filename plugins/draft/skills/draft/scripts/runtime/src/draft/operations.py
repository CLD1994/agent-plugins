from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import uuid
from collections.abc import Callable, Mapping, Set
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Literal, Protocol, cast

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
    """严格同步目录：打开或 fsync 失败都映射为 operation.durability_failed。

    曾经静默吞掉 OSError（回归 H1）：调用方会把"日志/证据已耐久"的结论建立在未同步
    的目录项上，崩溃后可能同时失去内容与重建证据。作为提交点前置的同步必须可失败。
    """
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    except OSError as exc:
        raise DraftError(CheckCode.OPERATION_DURABILITY_FAILED.value, path) from exc
    try:
        os.fsync(descriptor)
    except OSError as exc:
        raise DraftError(CheckCode.OPERATION_DURABILITY_FAILED.value, path) from exc
    finally:
        os.close(descriptor)


def _fsync_dir_fd(parent_fd: int, code_path: Path) -> None:
    """严格同步已打开的目录句柄：失败映射为 operation.durability_failed（回归 H1）。"""
    try:
        os.fsync(parent_fd)
    except OSError as exc:
        raise DraftError(CheckCode.OPERATION_DURABILITY_FAILED.value, code_path) from exc


_PENDING_SUFFIX = ".pending"


def _pending_name(name: str) -> str:
    """确定名 pending：目标名 + '.pending'，可由授权源决定的目标路径派生（回归 H2）。

    禁止随机 uuid 临时路径：崩溃遗留的 pending 必须能被恢复按精确名字、类型与摘要
    分类收敛，而不是按文件名形状批量猜测归属。
    """
    return f"{name}{_PENDING_SUFFIX}"


def _publish_via_pending(
    parent_fd: int,
    name: str,
    content: bytes,
    *,
    no_clobber: bool,
    code_path: Path,
) -> None:
    """在父目录句柄内把 content 耐久发布为 name（确定名 pending + 严格同步，回归 H1/H2）。

    顺序：截断创建 pending → 写入 → fsync 文件 → 严格 fsync 父目录（pending 目录项
    耐久，崩溃时恢复可按授权源分类）→ 发布 → 严格 fsync 父目录（目标目录项耐久）。
    任一步失败保留 pending/旧目标现场并抛 operation.durability_failed；调用方不得
    把该写入当作已耐久，也不得继续删除权威叶或释放锁。

    no_clobber=True：link 无覆盖发布，发布瞬间目标已出现即外部变化（恢复写不覆盖）。
    """
    pending = _pending_name(name)
    # 打开 pending：O_NOFOLLOW 拒绝符号链接；持锁写无并发，已有崩溃遗留 pending 由
    # 当前拥有者重写。
    try:
        descriptor = os.open(
            pending,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
            0o644,
            dir_fd=parent_fd,
        )
    except OSError as exc:
        raise DraftError(CheckCode.OPERATION_DURABILITY_FAILED.value, code_path) from exc
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise DraftError(CheckCode.OPERATION_DURABILITY_FAILED.value, code_path) from exc
    _fsync_dir_fd(parent_fd, code_path)
    if no_clobber:
        try:
            os.link(pending, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        except FileExistsError as exc:
            raise DraftError("operation.external_change", code_path) from exc
        except OSError as exc:
            raise DraftError(CheckCode.OPERATION_DURABILITY_FAILED.value, code_path) from exc
        try:
            os.unlink(pending, dir_fd=parent_fd)
        except OSError as exc:
            raise DraftError(CheckCode.OPERATION_DURABILITY_FAILED.value, code_path) from exc
    else:
        try:
            os.replace(pending, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        except OSError as exc:
            raise DraftError(CheckCode.OPERATION_DURABILITY_FAILED.value, code_path) from exc
    _fsync_dir_fd(parent_fd, code_path)


def _open_parent_dir(path: Path, *, code: str, code_path: Path) -> int:
    """no-follow 打开目录句柄：符号链接/类型错误映射为调用方稳定领域错误。"""
    try:
        return os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        raise DraftError(code, code_path) from exc


def _pending_decision(
    *,
    target_kind: EntryKind,
    pending_kind: EntryKind,
    target_digest: str | None,
    pending_digest: str | None,
    expected_digest: str,
) -> str:
    """恢复期 pending 收敛决策（纯函数）：publish / delete / ignore / external。

    授权源是调用方给出的 expected_digest（日志登记摘要或 T 授权摘要）。目标与 pending
    都必须为普通文件且摘要等于授权才可动作；类型/摘要不符一律保留现场报 external，
    绝不按文件名形状删除（回归 H2）。
    """
    if pending_kind is EntryKind.MISSING:
        if target_kind is EntryKind.FILE and target_digest == expected_digest:
            return "ignore"  # 已发布且无 pending。
        if target_kind is EntryKind.MISSING:
            return "ignore"  # 从未开始或已幂等缺失：无 pending 可收敛。
        return "external"
    if pending_kind is not EntryKind.FILE or pending_digest != expected_digest:
        return "external"
    if target_kind is EntryKind.MISSING:
        return "publish"  # pending 已耐久、目标未发布：发布它。
    if target_kind is EntryKind.FILE and target_digest == expected_digest:
        return "delete"  # 目标已发布且 pending 是同一授权内容：pending 已消费，删除。
    return "external"


def recover_digest_pending(
    root: Path,
    relative: Path,
    expected_digest: str,
    *,
    code: str,
    code_path: Path,
) -> None:
    """把崩溃遗留的 ``<目标>.pending`` 收敛为已发布/已消费状态（回归 H2）。

    授权源是 expected_digest。只处理精确授权 pending：目标与 pending 在同一锚定父目录
    内分类复核，类型/摘要不符即外部改写，保留现场报错，不按文件名形状批量删除。
    发布用 no-clobber link（目标在发布瞬间出现即外部变化，保留两者）。
    """
    with anchored_leaf(root, relative, code=code, code_path=code_path) as target:
        if isinstance(target, _MissingLeaf):
            return  # 中间父组件缺失：目标与 pending 都必然缺失，无可收敛。
        parent_fd = target.parent_fd
        pending_name = _pending_name(target.name)
        pending = _AnchoredLeaf(parent_fd, pending_name, relative, code, code_path)
        target_kind = target.classify()
        pending_kind = pending.classify()
        decision = _pending_decision(
            target_kind=target_kind,
            pending_kind=pending_kind,
            target_digest=sha256_bytes(target.read()) if target_kind is EntryKind.FILE else None,
            pending_digest=sha256_bytes(pending.read()) if pending_kind is EntryKind.FILE else None,
            expected_digest=expected_digest,
        )
        if decision == "external":
            raise DraftError(code, code_path)
        if decision == "publish":
            # 目标此刻仍缺失，link 无覆盖发布；EEXIST 说明发布瞬间被外部占用。
            try:
                os.link(
                    pending_name,
                    target.name,
                    src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd,
                )
            except FileExistsError as exc:
                raise DraftError("operation.external_change", code_path) from exc
            except OSError as exc:
                raise DraftError(CheckCode.OPERATION_DURABILITY_FAILED.value, code_path) from exc
            try:
                os.unlink(pending_name, dir_fd=parent_fd)
            except OSError as exc:
                raise DraftError(CheckCode.OPERATION_DURABILITY_FAILED.value, code_path) from exc
            _fsync_dir_fd(parent_fd, code_path)
        elif decision == "delete":
            try:
                os.unlink(pending_name, dir_fd=parent_fd)
            except OSError as exc:
                raise DraftError(CheckCode.OPERATION_DURABILITY_FAILED.value, code_path) from exc
            _fsync_dir_fd(parent_fd, code_path)


def reject_symlink_components(root: Path, relative: Path) -> None:
    """逐组件拒绝内部状态路径上的符号链接，防止 .draft 等中间组件逃逸到根外。"""
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise DraftError(CheckCode.PATH_SYMLINK_ESCAPE.value, cursor)


def classify_node(path: Path) -> EntryKind:
    """lstat 节点分类：missing/普通文件/目录/符号链接/其他。

    悬空符号链接是 SYMLINK 而不是 MISSING：``Path.exists()`` 对悬空链接返回 False，
    会把本应按链接/路径错误处理的现实状态折叠成幂等缺失（回归 M2）。所有清理与
    恢复分支必须先分类再决策，不得用 ``exists()`` 作为类型判断前置。
    """
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        return EntryKind.MISSING
    if stat.S_ISLNK(mode):
        return EntryKind.SYMLINK
    if stat.S_ISREG(mode):
        return EntryKind.FILE
    if stat.S_ISDIR(mode):
        return EntryKind.DIRECTORY
    return EntryKind.OTHER


@dataclass(frozen=True)
class _AnchoredLeaf:
    """锚定到 no-follow 打开的父目录句柄的叶节点访问对象。

    验证（stat/read 摘要）与删除（unlink/rmdir）使用同一句柄：中间组件在"检查通过"
    之后变成符号链接时，句柄仍指向原目录 inode，绝不跟随新链接解析新路径（回归 H1）。
    """

    parent_fd: int
    name: str
    display: Path
    code: str
    code_path: Path

    def _io_error(self, exc: OSError) -> DraftError:
        error = DraftError(self.code, self.code_path)
        error.__cause__ = exc
        return error

    def classify(self) -> EntryKind:
        try:
            mode = os.stat(self.name, dir_fd=self.parent_fd, follow_symlinks=False).st_mode
        except FileNotFoundError:
            return EntryKind.MISSING
        if stat.S_ISLNK(mode):
            return EntryKind.SYMLINK
        if stat.S_ISREG(mode):
            return EntryKind.FILE
        if stat.S_ISDIR(mode):
            return EntryKind.DIRECTORY
        return EntryKind.OTHER

    def read(self) -> bytes:
        try:
            descriptor = os.open(self.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self.parent_fd)
        except OSError as exc:
            raise self._io_error(exc) from exc
        try:
            chunks: list[bytes] = []
            while True:
                chunk = os.read(descriptor, 1 << 16)
                if not chunk:
                    return b"".join(chunks)
                chunks.append(chunk)
        finally:
            os.close(descriptor)

    def unlink(self) -> None:
        try:
            os.unlink(self.name, dir_fd=self.parent_fd)
        except OSError as exc:
            raise self._io_error(exc) from exc

    def rmdir(self) -> None:
        try:
            os.rmdir(self.name, dir_fd=self.parent_fd)
        except OSError as exc:
            raise self._io_error(exc) from exc

    def write(self, content: bytes) -> None:
        """no-clobber 耐久发布叶文件（restore 恢复缺失叶，回归 H1/H2/H3）。

        确定名 pending（``<名>.pending``）+ link 无覆盖发布：发布瞬间目标已出现即
        外部变化，保留 pending 与新目标并报错，绝不覆盖。严格同步父目录，失败映射
        operation.durability_failed。供清理证据恢复使用：绝不按绝对路径重新解析。
        """
        _publish_via_pending(
            self.parent_fd, self.name, content, no_clobber=True, code_path=self.code_path
        )


@dataclass(frozen=True)
class _MissingLeaf:
    """中间父组件缺失时的占位：叶节点必然缺失，任何访问按缺失处理。"""

    display: Path
    code: str
    code_path: Path

    def classify(self) -> EntryKind:
        return EntryKind.MISSING

    def read(self) -> bytes:
        raise AssertionError("missing leaf has no content")

    def unlink(self) -> None:
        raise AssertionError("missing leaf cannot be unlinked")

    def rmdir(self) -> None:
        raise AssertionError("missing leaf cannot be removed")

    def write(self, _content: bytes) -> None:
        # 中间父组件缺失：叶必然缺失，无需恢复（清理可继续，无需重建父链）。
        return


@contextmanager
def anchored_leaf(
    root: Path,
    relative: Path,
    *,
    code: str,
    code_path: Path,
):
    """no-follow 打开叶节点祖先目录链，产出锚定到最终父目录句柄的访问对象。

    任何中间组件缺失时叶视为 MISSING（幂等容忍）；中间组件为符号链接、非目录或
    其他不可打开状态时以稳定 ``code`` 停止并保留现场。``root`` 必须由调用方先验证
    不是符号链接。验证与删除使用同一句柄，禁止第二次按绝对路径解析。
    """
    if not relative.parts:
        raise ValueError("relative must be non-empty")
    # 根以 no-follow 目录方式打开（回归 H3）：跟根本身是符号链接时稳定领域错误，
    # 不跟随链接解析到其他目录。
    try:
        parent_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        raise DraftError(code, code_path) from exc
    closed = False
    try:
        for part in relative.parts[:-1]:
            try:
                next_fd = os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=parent_fd,
                )
            except FileNotFoundError:
                os.close(parent_fd)
                closed = True
                yield _MissingLeaf(relative, code, code_path)
                return
            except OSError as exc:
                os.close(parent_fd)
                closed = True
                raise DraftError(code, code_path) from exc
            # 每打开下一层立即关闭上一层（回归 M2）：成功、中间缺失与异常分支都保证
            # fd 数量回到调用前，避免大 mutation/深路径耗尽描述符。
            os.close(parent_fd)
            parent_fd = next_fd
        yield _AnchoredLeaf(parent_fd, relative.name, relative, code, code_path)
    finally:
        if not closed:
            os.close(parent_fd)


def durable_write(path: Path, value: bytes) -> None:
    """内部状态（日志、锁、恢复副本、清理证据）的严格耐久写：确定名 pending + 原子发布。

    崩溃遗留为 ``<目标名>.pending`` 而非随机 uuid 临时文件（回归 H2）：恢复按授权源
    精确分类收敛。父目录以 no-follow 打开（发布边界不重新解析路径）；严格同步失败
    映射 operation.durability_failed（回归 H1）。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    parent_fd = _open_parent_dir(
        path.parent, code=CheckCode.OPERATION_DURABILITY_FAILED.value, code_path=path.parent
    )
    try:
        _publish_via_pending(parent_fd, path.name, value, no_clobber=False, code_path=path)
    finally:
        os.close(parent_fd)


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


def _parse_journal_bytes(data: bytes, journal_path: Path) -> Journal:
    """持久 JSON bytes 解码 + 形状验证的单一解析边界（回归 M3）。

    严格 UTF-8 解码、JSON 解析与 journal 形状验证在同一处；``UnicodeDecodeError``、
    读取错误与 JSON 错误统一映射 operation.journal_corrupt，绝不泄漏原始异常。
    """
    try:
        decoded = json.loads(data.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, journal_path) from exc
    return _parse_journal(decoded, journal_path)


def read_journal(journal_path: Path) -> Journal:
    if not journal_path.is_file():
        raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, journal_path)
    return _parse_journal_bytes(journal_path.read_bytes(), journal_path)


def resolve_journal_pending(journal_path: Path) -> None:
    """把崩溃遗留的 ``journal.json.pending`` 收敛为已发布日志（回归 H2）。

    ``durable_write`` 的 pending 总是比现存 journal 更新（同一写调用里 replace 前
    崩溃才留下 pending；replace 会消费它），因此有效 pending 发布为新 journal 比回退
    旧 journal 更忠实。旧 journal 存在且 pending 非法（崩溃半写/类型错误）时保留旧
    journal 推进，pending 留作 checker 可见的可人工处理残留；旧 journal 缺失且 pending
    非法时无法继续，报 journal_corrupt。绝不按文件名删除 pending。
    """
    pending_path = journal_path.with_name(_pending_name(journal_path.name))
    pending_kind = classify_node(pending_path)
    if pending_kind is EntryKind.MISSING:
        return
    old_exists = classify_node(journal_path) is EntryKind.FILE
    if pending_kind is not EntryKind.FILE:
        if old_exists:
            return  # 保留旧 journal 推进；类型错误 pending 由 checker 报告。
        raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, pending_path)
    try:
        journal = _parse_journal_bytes(pending_path.read_bytes(), pending_path)
    except DraftError:
        if old_exists:
            return  # 半写 pending：旧 journal 仍可推进。
        raise
    if journal.operation_id != journal_path.parent.name:
        if old_exists:
            return
        raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, pending_path)
    parent_fd = _open_parent_dir(
        journal_path.parent,
        code=CheckCode.OPERATION_DURABILITY_FAILED.value,
        code_path=journal_path.parent,
    )
    try:
        try:
            os.replace(
                pending_path.name,
                journal_path.name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
        except OSError as exc:
            raise DraftError(CheckCode.OPERATION_DURABILITY_FAILED.value, pending_path) from exc
        _fsync_dir_fd(parent_fd, journal_path.parent)
    finally:
        os.close(parent_fd)


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


def _apply_write(root: Path, target: Path, content: bytes, *, temporary: Path) -> None:
    destination = root / target
    cursor = root
    for part in target.parts[:-1]:
        cursor /= part
        if cursor.is_symlink():
            raise DraftError(CheckCode.PATH_SYMLINK_ESCAPE.value, cursor)
    destination.parent.mkdir(parents=True, exist_ok=True)
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

    全局无副作用预检两个目录的全部成员：任何未登记成员、符号链接、错误类型或
    摘要不符都在第一次 unlink 前报 operation.inconsistent_state 并保留（回归 M1）。
    无登记 mutation（allocating 崩溃）时目录必须为空：prepare 已先登记后建文件
    （回归 H4），任何内容都不可解释为本操作副作用，一律拒绝。预检通过后按锚定
    句柄复核并删除登记叶，已知空目录 rmdir；登记叶部分缺失（提交点后幂等清理）
    容忍。journal.json 不属于本函数清理范围，由调用方按"最后处理 journal"的状态
    机决定。
    """
    registered: dict[str, dict[str, str]] = {"originals": {}, "staged": {}}
    for mutation in journal.mutations:
        if (
            mutation.original is not None
            and mutation.before_sha256 is not None
            and mutation.original.parent == Path("originals")
        ):
            registered["originals"][mutation.original.name] = mutation.before_sha256
        if (
            mutation.staged is not None
            and mutation.after_sha256 is not None
            and mutation.staged.parent == Path("staged")
        ):
            registered["staged"][mutation.staged.name] = mutation.after_sha256

    present: dict[str, list[Path]] = {"originals": [], "staged": []}
    for sub in ("originals", "staged"):
        directory = journal.directory / sub
        # lstat 分类：悬空符号链接是 link 不是 missing，不得折叠成幂等缺失后继续解锁。
        directory_node = classify_node(directory)
        if directory_node is EntryKind.MISSING:
            continue
        if directory_node is not EntryKind.DIRECTORY:
            raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, directory)
        allowed = registered[sub]
        # 先收敛崩溃遗留的登记文件 pending（回归 H2）：发布/消费后才做未知成员检查，
        # 否则 .pending 会作为未登记成员被拒绝且锁永久保留。
        for name, digest in sorted(allowed.items()):
            recover_digest_pending(
                journal.directory,
                Path(sub) / name,
                digest,
                code=CheckCode.OPERATION_INCONSISTENT_STATE.value,
                code_path=journal.directory / sub / name,
            )
        for entry in directory.iterdir():
            relative = Path(sub) / entry.name
            if entry.name not in allowed:
                raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, entry)
            with anchored_leaf(
                journal.directory,
                relative,
                code=CheckCode.OPERATION_INCONSISTENT_STATE.value,
                code_path=entry,
            ) as leaf:
                if leaf.classify() is not EntryKind.FILE:
                    raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, entry)
                if sha256_bytes(leaf.read()) != allowed[entry.name]:
                    raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, entry)
            present[sub].append(relative)

    for sub in ("originals", "staged"):
        for relative in sorted(present[sub]):
            with anchored_leaf(
                journal.directory,
                relative,
                code=CheckCode.OPERATION_INCONSISTENT_STATE.value,
                code_path=journal.directory / relative,
            ) as leaf:
                node = leaf.classify()
                if node is EntryKind.MISSING:
                    continue  # 提交点后部分清理幂等容忍。
                if node is not EntryKind.FILE:
                    raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, relative)
                if sha256_bytes(leaf.read()) != registered[sub][relative.name]:
                    raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, relative)
                leaf.unlink()
        directory = journal.directory / sub
        if classify_node(directory) is EntryKind.DIRECTORY:
            with anchored_leaf(
                journal.directory,
                Path(sub),
                code=CheckCode.OPERATION_INCONSISTENT_STATE.value,
                code_path=directory,
            ) as leaf:
                if leaf.classify() is EntryKind.DIRECTORY:
                    leaf.rmdir()


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
            # 确定性临时路径（回归 H2）：回滚重写也走登记精确临时路径，不用随机 uuid。
            _apply_write(
                root,
                mutation.path,
                original.read_bytes(),
                temporary=_mutation_temporary_path(
                    mutation.path, journal.operation_id, journal.mutations.index(mutation)
                ),
            )
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
        # 确定性临时路径（回归 H2）：前向恢复重写也走登记精确临时路径，不用随机 uuid。
        _apply_write(
            root,
            mutation.path,
            content,
            temporary=_mutation_temporary_path(
                mutation.path, journal.operation_id, journal.mutations.index(mutation)
            ),
        )
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
    back_path = operation_journal_path(root, back_id)
    resolve_journal_pending(back_path)
    back_journal = read_journal(back_path)
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
    changed, _ = _verify_observations_for_recovery(root, journal, explained_mutations=())
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
    mutations: list[JournalMutation] = []
    original_contents: dict[int, bytes] = {}
    staged_contents: dict[int, bytes] = {}
    for index, mutation in enumerate(plan.mutations):
        current_hash = sha256_file(root / mutation.path)
        if current_hash != mutation.before_sha256:
            if not retain_terminal_lock:
                release_lock(root, operation_id)
            raise DraftError("operation.external_change", root / mutation.path)
        original_name: Path | None = None
        staged_name: Path | None = None
        if mutation.before_sha256 is not None:
            # 在验证过的 before 状态读取并复核 original 字节；journal 只存根内相对路径。
            original_name = Path("originals") / f"{index:04d}.bin"
            original_bytes = (root / mutation.path).read_bytes()
            if sha256_bytes(original_bytes) != mutation.before_sha256:
                if not retain_terminal_lock:
                    release_lock(root, operation_id)
                raise DraftError("operation.external_change", root / mutation.path)
            original_contents[index] = original_bytes
        if mutation.kind is MutationKind.WRITE:
            if mutation.content is None or sha256_bytes(mutation.content) != (
                mutation.after_sha256
            ):
                if not retain_terminal_lock:
                    release_lock(root, operation_id)
                raise DraftError("operation.plan_invalid", root / mutation.path)
            staged_name = Path("staged") / f"{index:04d}.bin"
            staged_contents[index] = mutation.content
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
    # 先在文件创建前耐久登记全部 original/staged 路径与摘要（回归 H4）：allocating
    # 阶段永远不会有未登记文件；崩溃 mid-prepare 留下 PREPARED 日志 + 部分缺失文件，
    # 恢复按登记清单容忍缺失并拒绝任何未登记内容。
    write_journal(prepared)
    originals.mkdir(exist_ok=True)
    staged.mkdir(exist_ok=True)
    for index, mutation in enumerate(prepared.mutations):
        if index in original_contents:
            durable_write(originals / f"{index:04d}.bin", original_contents[index])
        if mutation.staged is not None:
            durable_write(staged / f"{index:04d}.bin", staged_contents[index])

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
    """复核现存恢复材料摘要；登记但缺失（prepare 先登记后建文件，回归 H4）容忍。

    缺失的 original 只在目标已进入 after 时才需要回滚，而 prepare 完成前不会应用
    任何 mutation，因此登记即缺失不会阻断回滚；staged 缺失只阻断前向完成。
    """
    for mutation in journal.mutations:
        if mutation.original is not None:
            original = journal.directory / mutation.original
            if original.exists() and sha256_file(original) != mutation.before_sha256:
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


def _write_recovery_decision(
    *,
    before: str | None,
    after: str,
    target_kind: EntryKind,
    target_digest: str | None,
    temp_kind: EntryKind,
    temp_digest: str | None,
) -> Literal["forward", "consume", "external"]:
    """WRITE 恢复决策表（回归 H3）：输入 target/登记精确临时路径的探针事实，输出动作。

    | target 状态 | 登记 temp 状态 | 结果 |
    | before | missing | forward：从 staged 正常前向完成 |
    | before | 普通文件且摘要=after | consume：允许崩溃遗留，复核后清理再前向完成 |
    | before | 摘要不符、目录、链接、other | external_change |
    | after | missing | forward：正常提交/恢复 |
    | after | 任意存在状态 | external_change（原子 replace 不会留下共存 temp） |
    | unknown | 任意 | external_change |

    目录成员 exact/extra 是独立维度：除 before+consume 允许的精确 temp 外，任何
    额外成员都必须拒绝。探针只负责提供事实；本函数不访问文件系统，可独立单测。
    """
    if target_kind is EntryKind.FILE and target_digest == after:
        target = "after"
    elif target_kind is EntryKind.FILE and target_digest == before:
        target = "before"
    elif target_kind is EntryKind.MISSING and before is None:
        target = "before"  # 新建文件尚未创建
    else:
        target = "unknown"
    if temp_kind is EntryKind.MISSING:
        temp = "missing"
    elif temp_kind is EntryKind.FILE and temp_digest == after:
        temp = "exact"
    else:
        temp = "wrong"
    if target == "after":
        return "external" if temp != "missing" else "forward"
    if target == "before":
        if temp == "missing":
            return "forward"
        if temp == "exact":
            return "consume"
        return "external"
    return "external"


def _verify_observations_for_recovery(
    root: Path,
    journal: Journal,
    explained_mutations: tuple[_MutationLike, ...],
    *,
    probe: ObservationProbe | None = None,
) -> tuple[tuple[Path, ...], frozenset[Path]]:
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

    # 每个 WRITE 按 target/登记 temp 状态表一次性分类（回归 H3）：只有
    # before+owned-temp-exact（可消费的崩溃遗留）被排除出目录成员，target=after 时
    # 任何共存 temp 都是外部变化，未登记的 temp-like 成员一律保留。
    consume_temps: set[Path] = set()
    for index, mutation in enumerate(journal.mutations):
        if mutation.kind is not MutationKind.WRITE or mutation.after_sha256 is None:
            continue
        temporary = _mutation_temporary_path(mutation.path, journal.operation_id, index)
        target_observed = probe.observe(mutation.path)
        temp_observed = probe.observe(temporary)
        decision = _write_recovery_decision(
            before=mutation.before_sha256,
            after=mutation.after_sha256,
            target_kind=target_observed.kind,
            target_digest=target_observed.sha256,
            temp_kind=temp_observed.kind,
            temp_digest=temp_observed.sha256,
        )
        if decision == "external":
            raise DraftError("operation.external_change", mutation.path)
        if decision == "consume":
            consume_temps.add(temporary)

    class _RecoveryProbe:
        """比较探针：只忽略允许消费的精确临时路径（target=before 且摘要=after）。

        所有权证据是精确路径 + 状态组合，不是文件名形状：预存 temp-like 合法文件
        不得被过滤；target=after 时精确 temp 的存在已在上方判为外部变化；未登记的
        temp-like 入侵者（即使与目标同基础名）必须保留在成员里（否则被静默吸收）。
        """

        def __init__(self, inner: ObservationProbe) -> None:
            self._inner = inner

        def observe(self, path: Path) -> Observation:
            observed = self._inner.observe(path)
            if observed.kind is EntryKind.DIRECTORY:
                members = tuple(
                    member for member in observed.members if (path / member) not in consume_temps
                )
                return Observation(path=path, kind=EntryKind.DIRECTORY, members=members)
            return observed

    changed = verify_observations(_RecoveryProbe(probe), adjusted, explained_mutations=())
    return changed, frozenset(consume_temps)


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
    changed, _ = _verify_observations_for_recovery(root, journal, explained_mutations=(marker,))
    if changed:
        rolled = _with_phase(journal, OperationPhase.ROLLED_BACK)
        write_journal(rolled)
        _clean_recovery_copies(journal)
        release_lock(root, operation_id)
        # 外部修改必须可观察："已安全放弃 adopt" 不等于恢复成功（规格 §12.2）。
        # 已证明 marker 未提交且回滚安全，释放锁后仍报告外部变化。
        raise DraftError("operation.external_change", root / changed[0])
    return _finish_forward(root, journal, changed_paths=(MARKER_PATH,))


def _clean_owned_temp_artifacts(
    root: Path, journal: Journal, consume_temps: frozenset[Path]
) -> None:
    """清理决策表允许消费的崩溃遗留临时文件（精确相对路径 + 摘要复核）。

    在观察复核通过后调用：前向完成会重新创建目标并替换，旧临时文件是孤儿。只处理
    ``_write_recovery_decision`` 判为 consume 的精确临时路径；临时文件必须与对应
    mutation 的 after 摘要一致，类型不符或摘要不符（决策与清理之间被外部改写）即
    外部篡改，停止并保留现场。不扫描父目录猜测候选。
    """
    for index, mutation in enumerate(journal.mutations):
        if mutation.kind is not MutationKind.WRITE or mutation.after_sha256 is None:
            continue
        relative = _mutation_temporary_path(mutation.path, journal.operation_id, index)
        if relative not in consume_temps:
            continue
        temporary = root / relative
        if classify_node(temporary) is not EntryKind.FILE:
            raise DraftError("operation.external_change", temporary)
        if sha256_file(temporary) != mutation.after_sha256:
            raise DraftError("operation.external_change", temporary)
        temporary.unlink()
        fsync_directory(temporary.parent)


def _recover_locked(root: Path, operation_id: str) -> OperationResult:
    # 先把崩溃遗留的 journal pending 发布为最新日志（回归 H2），再从最新日志恢复。
    resolve_journal_pending(operation_journal_path(root, operation_id))
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
    changed, consume_temps = _verify_observations_for_recovery(
        root, journal, explained_mutations=journal.mutations
    )
    if changed:
        raise DraftError("operation.external_change", root / changed[0])
    _clean_owned_temp_artifacts(root, journal, consume_temps)
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
        if not entry.is_dir():
            continue
        journal_path = entry / "journal.json"
        if not journal_path.is_file():
            # 操作目录无 journal：只剩崩溃遗留 pending 时发布有效日志后继续处理；
            # 非法 pending 报损坏（不按文件名删除）；既无 journal 也无 pending 跳过。
            if classify_node(entry / _pending_name("journal.json")) is EntryKind.MISSING:
                continue
            resolve_journal_pending(journal_path)
        else:
            # journal 与 pending 并存：pending 是更新的完整日志，先发布（回归 H2）。
            resolve_journal_pending(journal_path)
        journal = read_journal(journal_path)
        if journal.phase is OperationPhase.ALLOCATING:
            if "restores_trash_operation" in journal.metadata:
                # 关联恢复日志必须继续存在：无锁放弃为 rolled_back，保留审计事实。
                _clean_recovery_copies(journal)
                write_journal(_with_phase(journal, OperationPhase.ROLLED_BACK))
                continue
            # 无锁 allocating 碎片：协议明确拥有 journal.json 与 originals/staged
            # 目录；其余未知条目报 inconsistent_state，不得用 rglob 递归删除任意
            # 内容（回归 H5）。统一状态机：完整预检全部恢复目录 → 删除精确授权叶
            # → 删除空目录 → journal 最后删除（回归 H4）。
            known_names = {"journal.json", "originals", "staged"}
            extra = [path for path in entry.iterdir() if path.name not in known_names]
            if extra:
                raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, entry)
            _clean_recovery_copies(journal)
            (entry / "journal.json").unlink()
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
