from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import stat
import uuid
from collections.abc import Callable, Generator, Mapping, Set
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Literal, NoReturn, Protocol, cast

if TYPE_CHECKING:
    from draft.link_index_maintenance import PreparedLinkIndexUpdate

from draft.diagnostic_codes import (
    LINK_INDEX_REBUILD_REQUIRED,
    OPERATION_EXTERNAL_CHANGE,
    OPERATION_INDEX_UPDATE_FAILED,
    CheckCode,
)
from draft.errors import DraftError
from draft.graph import INTERNAL_DIR, MARKER_PATH
from draft.models import (
    LinkIndexResult,
    MutationKind,
    OperationKind,
    OperationPhase,
    OperationPlan,
    OperationResult,
    ResultWarning,
    RootState,
)
from draft.observations import (
    EntryKind,
    Observation,
    ObservationManifest,
    ObservationProbe,
    classify_node,
)
from draft.paths import resolve_draft_root
from draft.plan_effects import public_changed_paths


class _MutationLike(Protocol):
    @property
    def path(self) -> Path: ...


JOURNAL_VERSION = 4
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
# 终态处置键（与 HANDOFF 键同属 journal metadata 协议）：唯一权威来源，trash_lifecycle 复用。
DISPOSITION_KEY = "disposition"
# trash_retained 阶段的恢复认领键：T 用它回指已完成关联的 R。
RESTORE_OPERATION_ID_KEY = "restore_operation_id"
# 投递重试下一次时间键（与 HANDOFF 键同属失败记录协议）。
NEXT_HANDOFF_ATTEMPT_KEY = "next_handoff_attempt_at"
# prepared 冻结点追加的公开变更路径审计键。
PUBLIC_CHANGED_PATHS_KEY = "public_changed_paths"

_MID_WRITE_PHASES = frozenset(
    {
        OperationPhase.PREPARED,
        OperationPhase.APPLYING,
        OperationPhase.VALIDATING,
        OperationPhase.ROLLING_BACK,
    }
)

# 链接索引字段的 phase 分组（规格 §4 字段矩阵）：唯一权威来源，序列化、解析、
# 逐条校验与后继转换共享。
_PRE_LINK_INDEX_PHASES = frozenset(
    {
        OperationPhase.ALLOCATING,
        OperationPhase.PREPARED,
        OperationPhase.APPLYING,
        OperationPhase.VALIDATING,
    }
)
_LINK_INDEX_PHASES = frozenset(
    {
        OperationPhase.LINK_INDEX_UPDATING,
        OperationPhase.LINK_INDEX_PUBLISHED,
        OperationPhase.LINK_INDEX_REBUILD_REQUIRED,
    }
)
_ROLLBACK_PHASES = frozenset(
    {
        OperationPhase.ROLLING_BACK,
        OperationPhase.ROLLED_BACK,
    }
)
# 经索引阶段到达的原有成功终态：字段原样保留，sha256 仅 published 保留。
_PRESERVED_LINK_INDEX_PHASES = frozenset(
    {
        OperationPhase.COMPLETED,
        OperationPhase.TRASH_RETAINED,
        OperationPhase.TRASH_HANDOFF_PENDING,
        OperationPhase.TRASH_HANDOFF_SUCCEEDED,
        OperationPhase.TRASH_PURGE_COMMITTED,
    }
)

_REVISION_PATTERN = re.compile(r"[1-9][0-9]*")


class DurabilityStep(StrEnum):
    """受支持的耐久 I/O 协作者步骤。"""

    STAGING_WRITE = "staging_write"
    STAGING_FSYNC = "staging_fsync"
    STAGING_DIRECTORY_FSYNC = "staging_directory_fsync"
    INDEX_REPLACE = "index_replace"
    INDEX_DIRECTORY_FSYNC = "index_directory_fsync"
    JOURNAL_WRITE = "journal_write"
    JOURNAL_DIRECTORY_FSYNC = "journal_directory_fsync"
    FLAG_CREATE = "flag_create"
    FLAG_DELETE = "flag_delete"
    FLAG_DIRECTORY_FSYNC = "flag_directory_fsync"


class DurabilityObserver(Protocol):
    """可选的耐久边界观察者；默认没有观察者。"""

    def checkpoint(self, step: DurabilityStep) -> None: ...


_DURABILITY_OBSERVER: ContextVar[DurabilityObserver | None] = ContextVar(
    "draft_durability_observer", default=None
)


@contextmanager
def durability_observer_scope(observer: DurabilityObserver) -> Generator[None, None, None]:
    """在一次调用树内安装耐久边界观察者，并在退出时恢复外层观察者。"""
    token = _DURABILITY_OBSERVER.set(observer)
    try:
        yield
    finally:
        _DURABILITY_OBSERVER.reset(token)


def durability_checkpoint(step: DurabilityStep) -> None:
    """在真实耐久 I/O 前通知当前观察者；无观察者时保持真实 I/O。"""
    observer = _DURABILITY_OBSERVER.get()
    if observer is not None:
        observer.checkpoint(step)


def validate_link_index_revision(value: object, *, path: Path) -> str:
    """链接索引 revision 的格式校验边界（规格 §3）。

    只接受匹配 ``[1-9][0-9]*`` 的规范十进制字符串并原样返回：不接受前导零、
    符号、小数、指数、空白或 JSON 数字（含 ``int``）。本函数只做格式与规范串
    校验，不做数值比较；取数值最大 revision 由后续 journal authority 扫描按
    任意精度整数执行（不按字符串字典序）。
    """
    if not isinstance(value, str) or _REVISION_PATTERN.fullmatch(value) is None:
        raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, path)
    return value


class MutationDirection(StrEnum):
    """mutation artifact 恢复方向：正向应用或回滚。"""

    FORWARD = "forward"
    ROLLBACK = "rollback"


class MutationRole(StrEnum):
    """mutation 角色：新建 WRITE / 改写 WRITE / 删除。"""

    NEW_WRITE = "new_write"
    REWRITE_WRITE = "rewrite_write"
    DELETE = "delete"


class ArtifactState(StrEnum):
    """目标/pending/quarantine 节点的授权分类：缺失/等于 before/等于 after/摘要不符/非文件。"""

    MISSING = "missing"
    BEFORE = "before"
    AFTER = "after"
    WRONG = "wrong"
    NON_FILE = "non_file"


class MutationRecoveryAction(StrEnum):
    """方向感知 artifact 恢复的按序动作：发布/消费/继续/完成/外部变化。"""

    PUBLISH_PENDING = "publish_pending"
    PUBLISH_STAGED = "publish_staged"
    PUBLISH_ORIGINAL = "publish_original"
    CONSUME_PENDING = "consume_pending"
    CONSUME_QUARANTINE = "consume_quarantine"
    RETRY_APPLY = "retry_apply"
    DONE = "done"
    EXTERNAL = "external"


def _mutation_role(mutation: JournalMutation) -> MutationRole:
    if mutation.kind is MutationKind.DELETE:
        return MutationRole.DELETE
    if mutation.before_sha256 is None:
        return MutationRole.NEW_WRITE
    return MutationRole.REWRITE_WRITE


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


def fsync_directory(
    path: Path,
    *,
    durability_step: DurabilityStep | None = None,
) -> None:
    """严格同步目录：打开或 fsync 失败都映射为 operation.durability_failed。

    作为提交点前置的同步必须可失败。以 no-follow 打开，确保同步的是此前验证过的
    同一目录，而不是被链接替换后的其他目录。
    """
    try:
        if durability_step is not None:
            durability_checkpoint(durability_step)
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        raise DraftError(CheckCode.OPERATION_DURABILITY_FAILED.value, path) from exc
    try:
        os.fsync(descriptor)
    except OSError as exc:
        raise DraftError(CheckCode.OPERATION_DURABILITY_FAILED.value, path) from exc
    finally:
        os.close(descriptor)


def _fsync_dir_fd(
    parent_fd: int,
    code_path: Path,
    *,
    durability_step: DurabilityStep | None = None,
) -> None:
    """严格同步已打开的目录句柄：失败映射为 operation.durability_failed。"""
    try:
        if durability_step is not None:
            durability_checkpoint(durability_step)
        os.fsync(parent_fd)
    except OSError as exc:
        raise DraftError(CheckCode.OPERATION_DURABILITY_FAILED.value, code_path) from exc


_PENDING_SUFFIX = ".pending"
_QUARANTINE_SUFFIX = ".quarantine"


def _pending_name(name: str) -> str:
    """确定名 pending：目标名 + '.pending'，可由授权源决定的目标路径派生。

    禁止随机 uuid 临时路径：崩溃遗留的 pending 必须能被恢复按精确名字、类型与摘要
    分类收敛，而不是按文件名形状批量猜测归属。
    """
    return f"{name}{_PENDING_SUFFIX}"


def _quarantine_name(name: str) -> str:
    """确定名 quarantine：目标名 + '.quarantine'，由授权源决定的目标路径派生。

    删除采用"先原子隔离、后验证、再删除"：授权名字被移动到 quarantine 名后才删除，
    崩溃遗留的 quarantine 必须能被恢复按精确名字、类型与摘要分类收敛，不产生随机
    孤儿名。
    """
    return f"{name}{_QUARANTINE_SUFFIX}"


def _publish_pending(
    parent_fd: int,
    name: str,
    content: bytes,
    *,
    pending_mode: Literal["create", "replace"],
    publish: Literal["link", "replace"],
    code_path: Path,
    write_step: DurabilityStep | None = None,
    file_fsync_step: DurabilityStep | None = None,
    directory_fsync_step: DurabilityStep | None = None,
) -> None:
    """在父目录句柄内把 content 耐久发布为 name（确定名 pending + 严格同步）。

    顺序：创建 pending → 写入 → fsync 文件 → 严格 fsync 父目录（pending 目录项耐久，
    崩溃时恢复可按授权源分类）→ 发布 → 严格 fsync 父目录（新名字目录项耐久，至少
    一个授权名字可证明存在）→ 删除 pending → 严格 fsync 父目录（pending 移除耐久）。

    - ``pending_mode="create"``：O_EXCL 原子创建，已有 pending 拒绝（绝不截断未知
      pending）；``pending_mode="replace"``：journal 更新由已验证的转换授权持有该命名
      临时，半写残留可替换。
    - ``publish="link"``：no-clobber 发布，发布瞬间目标已出现即外部变化（create-only
      目标与恢复写不覆盖）；``publish="replace"``：经转换验证后的权威 journal 原子替换。

    任一步失败保留 pending/旧目标现场并抛稳定领域错误；调用方不得把该写入当作已耐久，
    也不得继续删除权威叶或释放锁。
    """
    pending = _pending_name(name)
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_NOFOLLOW
        | (os.O_EXCL if pending_mode == "create" else os.O_TRUNC)
    )
    try:
        if write_step is not None:
            durability_checkpoint(write_step)
        descriptor = os.open(pending, flags, 0o644, dir_fd=parent_fd)
    except FileExistsError as exc:
        raise DraftError("operation.external_change", code_path) from exc
    except OSError as exc:
        raise DraftError(CheckCode.OPERATION_DURABILITY_FAILED.value, code_path) from exc
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            if file_fsync_step is not None:
                durability_checkpoint(file_fsync_step)
            os.fsync(handle.fileno())
    except OSError as exc:
        raise DraftError(CheckCode.OPERATION_DURABILITY_FAILED.value, code_path) from exc
    _fsync_dir_fd(parent_fd, code_path, durability_step=directory_fsync_step)
    if publish == "link":
        try:
            os.link(pending, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        except FileExistsError as exc:
            raise DraftError("operation.external_change", code_path) from exc
        except OSError as exc:
            raise DraftError(CheckCode.OPERATION_DURABILITY_FAILED.value, code_path) from exc
        # 新名字耐久后（至少一个授权名字可证明存在），复核发布内容仍是我们写入的
        # 授权字节，且 pending 仍指向同一节点：任一被替换都拒绝消费，绝不删除未知节点。
        _fsync_dir_fd(parent_fd, code_path, durability_step=directory_fsync_step)
        if _read_name_bytes(parent_fd, name, code_path=code_path) != content or not _same_name_node(
            parent_fd,
            pending,
            _name_node(parent_fd, name, code="operation.external_change", code_path=code_path),
            code="operation.external_change",
            code_path=code_path,
        ):
            raise DraftError("operation.external_change", code_path)
        try:
            os.unlink(pending, dir_fd=parent_fd)
        except OSError as exc:
            raise DraftError(CheckCode.OPERATION_DURABILITY_FAILED.value, code_path) from exc
    else:
        try:
            os.replace(pending, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        except OSError as exc:
            raise DraftError(CheckCode.OPERATION_DURABILITY_FAILED.value, code_path) from exc
    _fsync_dir_fd(parent_fd, code_path, durability_step=directory_fsync_step)


def _create_only_publish(
    parent_fd: int,
    name: str,
    content: bytes,
    *,
    code_path: Path,
    write_step: DurabilityStep | None = None,
    file_fsync_step: DurabilityStep | None = None,
    directory_fsync_step: DurabilityStep | None = None,
) -> None:
    """create-only 发布：目标与 pending 都必须是 missing 的首次创建。

    绝不截断未知 pending、绝不覆盖既有目标；目标/pending 已占用（file/directory/link/
    other）都保留现场报 operation.external_change，除非调用方已先跑专用恢复决策并证明
    exact。用于 journal bootstrap、original/staged、attempt、cleanup evidence。
    """
    _publish_pending(
        parent_fd,
        name,
        content,
        pending_mode="create",
        publish="link",
        code_path=code_path,
        write_step=write_step,
        file_fsync_step=file_fsync_step,
        directory_fsync_step=directory_fsync_step,
    )


def _replace_publish(
    parent_fd: int,
    name: str,
    content: bytes,
    *,
    code_path: Path,
    write_step: DurabilityStep | None = None,
    file_fsync_step: DurabilityStep | None = None,
    directory_fsync_step: DurabilityStep | None = None,
) -> None:
    """权威 journal 更新发布：调用方必须先验证授权 old→new 转换。

    journal 自身的命名临时可替换半写残留（旧 journal 权威且已同步）；最终 os.replace
    原子替换目标，绝不出现目标缺失窗口。
    """
    _publish_pending(
        parent_fd,
        name,
        content,
        pending_mode="replace",
        publish="replace",
        code_path=code_path,
        write_step=write_step,
        file_fsync_step=file_fsync_step,
        directory_fsync_step=directory_fsync_step,
    )


def _open_parent_dir(path: Path, *, code: str, code_path: Path) -> int:
    """no-follow 打开目录句柄：符号链接/类型错误映射为调用方稳定领域错误。"""
    try:
        return os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        raise DraftError(code, code_path) from exc


def ensure_directory_chain(root: Path, relative: Path, *, code: str, code_path: Path) -> int:
    """从 root no-follow 逐层打开/创建目录链，每创建一层立即 fsync 父目录。

    返回最终父目录 fd（调用方负责 close）。缺失目录以 mkdirat 创建后立即 fsync 当前
    父目录（新目录项耐久），再打开下一层；中间组件为符号链接/非目录/不可打开时以
    稳定 ``code`` 停止并保留现场。路径组件在同一 no-follow 锚定链上创建与同步，绝不
    按绝对路径重新解析祖先。``relative`` 为空（"."）时直接打开并返回 root 句柄。
    """
    if not relative.parts:
        return _open_parent_dir(root, code=code, code_path=code_path)
    try:
        parent_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        raise DraftError(code, code_path) from exc
    try:
        for part in relative.parts:
            try:
                next_fd = os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=parent_fd,
                )
            except FileNotFoundError:
                try:
                    os.mkdir(part, 0o755, dir_fd=parent_fd)
                except OSError as exc:
                    raise DraftError(code, code_path) from exc
                # 新目录项耐久后才继续：父目录同步失败即停止，不推进 phase。
                _fsync_dir_fd(parent_fd, code_path)
                try:
                    next_fd = os.open(
                        part,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=parent_fd,
                    )
                except OSError as exc:
                    raise DraftError(code, code_path) from exc
            except OSError as exc:
                raise DraftError(code, code_path) from exc
            os.close(parent_fd)
            parent_fd = next_fd
        return parent_fd
    except BaseException:
        os.close(parent_fd)
        raise


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
    绝不按文件名形状删除。
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
    """把崩溃遗留的 ``<目标>.pending`` 收敛为已发布/已消费状态。

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
            # 新名字耐久后，复核发布内容仍是授权内容且 pending 未被替换。
            _fsync_dir_fd(parent_fd, code_path)
            try:
                published = _read_name_bytes(parent_fd, target.name, code_path=code_path)
            except DraftError:
                raise
            if sha256_bytes(published) != expected_digest or not _same_name_node(
                parent_fd,
                pending_name,
                _name_node(
                    parent_fd, target.name, code="operation.external_change", code_path=code_path
                ),
                code="operation.external_change",
                code_path=code_path,
            ):
                raise DraftError("operation.external_change", code_path)
            try:
                os.unlink(pending_name, dir_fd=parent_fd)
            except OSError as exc:
                raise DraftError(CheckCode.OPERATION_DURABILITY_FAILED.value, code_path) from exc
            _fsync_dir_fd(parent_fd, code_path)
        elif decision == "delete":
            # 消费前复核 pending 仍是授权内容（决策与删除之间被替换则拒绝）。
            try:
                pending_live = _read_name_bytes(parent_fd, pending_name, code_path=code_path)
            except DraftError:
                raise
            if sha256_bytes(pending_live) != expected_digest:
                raise DraftError("operation.external_change", code_path)
            try:
                os.unlink(pending_name, dir_fd=parent_fd)
            except OSError as exc:
                raise DraftError(CheckCode.OPERATION_DURABILITY_FAILED.value, code_path) from exc
            _fsync_dir_fd(parent_fd, code_path)


def _quarantine_decision(
    *,
    target_kind: EntryKind,
    quarantine_kind: EntryKind,
    target_digest: str | None,
    quarantine_digest: str | None,
    expected_digest: str,
) -> str:
    """恢复期 quarantine 收敛决策（纯函数）：complete / restore / delete_stale / external / ignore。

    授权源是 expected_digest。quarantine 是删除隔离名（``{name}.quarantine``），由
    确定名派生。恢复语义：目标缺失 + quarantine 授权精确 → 隔离已通过
    验证，删除它完成删除；目标与 quarantine 都是授权精确（同一 inode 中途移动）→
    quarantine 已消费，删除它；目标缺失 + quarantine 摘要不符 → 被隔离的不是授权叶，
    恢复原名并报告外部变化（原名占用时同时保留两者）；其余类型/摘要不符一律保留现场。
    """
    if quarantine_kind is EntryKind.MISSING:
        return "ignore"
    if quarantine_kind is not EntryKind.FILE or quarantine_digest != expected_digest:
        return "restore" if quarantine_kind is EntryKind.FILE else "external"
    if target_kind is EntryKind.MISSING:
        return "complete"
    if target_kind is EntryKind.FILE and target_digest == expected_digest:
        return "delete_stale"
    return "external"


def recover_quarantine(
    root: Path,
    relative: Path,
    expected_digest: str,
    *,
    code: str,
    code_path: Path,
) -> None:
    """把崩溃遗留的 ``<目标>.quarantine`` 收敛为已删除/已恢复状态。

    授权源是 expected_digest。目标与 quarantine 在同一锚定父目录内分类复核，只处理
    精确授权 quarantine；类型/摘要不符即外部改写，恢复原名或保留现场报错，不按文件名
    形状批量删除。
    """
    with anchored_leaf(root, relative, code=code, code_path=code_path) as target:
        if isinstance(target, _MissingLeaf):
            return
        parent_fd = target.parent_fd
        target_kind = target.classify()
        quarantine = _AnchoredLeaf(
            parent_fd, _quarantine_name(target.name), relative, code, code_path
        )
        quarantine_kind = quarantine.classify()
        decision = _quarantine_decision(
            target_kind=target_kind,
            quarantine_kind=quarantine_kind,
            target_digest=sha256_bytes(target.read()) if target_kind is EntryKind.FILE else None,
            quarantine_digest=(
                sha256_bytes(quarantine.read()) if quarantine_kind is EntryKind.FILE else None
            ),
            expected_digest=expected_digest,
        )
        if decision == "restore":
            try:
                os.link(
                    quarantine.name,
                    target.name,
                    src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd,
                )
            except FileExistsError:
                # 原名已被占用：同时保留两者，报告外部变化。
                raise DraftError("operation.external_change", code_path) from None
            except OSError as exc:
                raise DraftError(CheckCode.OPERATION_DURABILITY_FAILED.value, code_path) from exc
            # 新名字耐久后，复核恢复内容仍是授权内容且 quarantine 未被替换。
            _fsync_dir_fd(parent_fd, code_path)
            try:
                restored = _read_name_bytes(parent_fd, target.name, code_path=code_path)
            except DraftError:
                raise
            if sha256_bytes(restored) != expected_digest or not _same_name_node(
                parent_fd,
                quarantine.name,
                _name_node(
                    parent_fd, target.name, code="operation.external_change", code_path=code_path
                ),
                code="operation.external_change",
                code_path=code_path,
            ):
                raise DraftError("operation.external_change", code_path)
            try:
                os.unlink(quarantine.name, dir_fd=parent_fd)
            except OSError as exc:
                raise DraftError(CheckCode.OPERATION_DURABILITY_FAILED.value, code_path) from exc
            _fsync_dir_fd(parent_fd, code_path)
            raise DraftError(code, code_path)
        if decision in ("complete", "delete_stale"):
            # 消费前复核 quarantine 仍是授权内容（决策与删除之间被替换则拒绝）。
            try:
                quarantined_live = _read_name_bytes(parent_fd, quarantine.name, code_path=code_path)
            except DraftError:
                raise
            if sha256_bytes(quarantined_live) != expected_digest:
                raise DraftError("operation.external_change", code_path)
            try:
                os.unlink(quarantine.name, dir_fd=parent_fd)
            except OSError as exc:
                raise DraftError(CheckCode.OPERATION_DURABILITY_FAILED.value, code_path) from exc
            _fsync_dir_fd(parent_fd, code_path)
        elif decision == "external":
            raise DraftError(code, code_path)


def reject_symlink_components(root: Path, relative: Path) -> None:
    """逐组件拒绝内部状态路径上的符号链接，防止 .draft 等中间组件逃逸到根外。"""
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise DraftError(CheckCode.PATH_SYMLINK_ESCAPE.value, cursor)


def classify_node_or_error(path: Path, *, code: str, code_path: Path) -> EntryKind:
    """调用方拥有错误语义的分类边界。

    ``classify_node`` 是低层事实函数，只吞 ENOENT；其原始 OSError 语义只允许在本包装
    后的持久协议入口出现。同一入口的 classify/read/decode 共享领域错误码并保留
    ``__cause__``，绝不把 PermissionError/I/O error 泄漏给 CLI。
    """
    try:
        return classify_node(path)
    except OSError as exc:
        error = DraftError(code, code_path)
        error.__cause__ = exc
        raise error from exc


def _name_node(
    parent_fd: int,
    name: str,
    *,
    code: str,
    code_path: Path,
) -> tuple[int, int] | None:
    """no-follow stat 返回 (st_ino, st_dev)；仅 ENOENT（名字确实不存在）返回 None。

    其他 OSError（EACCES/EIO 等）按调用者拥有的 code/path 转 DraftError。
    ENOENT 与"无法读取节点身份"是会导致不同行为的现实状态，不能共用 None——
    幂等缺失只允许由 FileNotFoundError 证明，其他 I/O 失败必须阻止成功、清理与解锁。
    """
    try:
        st = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise DraftError(code, code_path) from exc
    return (st.st_ino, st.st_dev)


def _same_name_node(
    parent_fd: int,
    name: str,
    node: tuple[int, int] | None,
    *,
    code: str,
    code_path: Path,
) -> bool:
    """name 当前是否仍指向 node（删除前 inode 复核）。

    命名动作之间名字被外部替换时，绝不能按名字删除未知节点：只有名字仍指向
    我们验证过的同一 inode 才允许消费。身份读取失败（EACCES/EIO）让 DraftError
    传播，不得把读取失败返回 false 后在调用者处当作幂等完成；
    node 为 None（名字本就不存在）时返回 False。
    """
    if node is None:
        return False
    return _name_node(parent_fd, name, code=code, code_path=code_path) == node


def _read_name_bytes(
    parent_fd: int,
    name: str,
    *,
    code_path: Path,
    code: str = CheckCode.OPERATION_DURABILITY_FAILED.value,
) -> bytes:
    """在父目录句柄内 no-follow 读取名字内容；失败映射稳定领域错误。"""
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
    except OSError as exc:
        raise DraftError(code, code_path) from exc
    try:
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1 << 16)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    except OSError as exc:
        raise DraftError(code, code_path) from exc
    finally:
        os.close(descriptor)


@dataclass(frozen=True)
class _AnchoredLeaf:
    """锚定到 no-follow 打开的父目录句柄的叶节点访问对象。

    验证（stat/read 摘要）与删除（unlink/rmdir）使用同一句柄：中间组件在"检查通过"
    之后变成符号链接时，句柄仍指向原目录 inode，绝不跟随新链接解析新路径。
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
        """create-only 耐久发布叶文件（restore 恢复缺失叶）。

        目标与 pending 都必须 missing（O_EXCL + link 无覆盖发布）：发布瞬间目标或
        pending 已出现即外部变化，保留现场报错，绝不截断未知 pending、绝不覆盖既有
        目标。严格同步父目录，失败映射 operation.durability_failed。供清理证据恢复
        使用：调用方已先收敛崩溃遗留 pending，绝不按绝对路径重新解析。
        """
        _create_only_publish(self.parent_fd, self.name, content, code_path=self.code_path)

    def quarantine_delete(self, expected_digest: str) -> None:
        """先隔离、后验证、再删除。

        节点事务（每个命名动作后严格 fsync，新名字耐久后才允许删旧名字）：
        1. no-clobber 快照：link 原子捕获当前节点到确定名 quarantine（EEXIST→外部变化）；
        2. fsync 父目录：quarantine 名字耐久后，才允许删除原名；
        3. inode 复核：原名仍指向刚隔离的同一节点，否则不触碰未知节点；
        4. unlink 原名 + fsync；
        5. 验证隔离节点类型与摘要：先捕获 inode，再读内容验摘要；不符则 no-replace
           恢复原名（原名占用时同时保留两者）并报告外部变化；
        6. 消费前 inode 复核：quarantine 名仍指向刚验证的同一节点，否则不删除未知节点；
        7. unlink quarantine + fsync。

        POSIX link+unlink 无法消除系统调用间隙的改名（renameat2 RENAME_NOREPLACE
        本分支 stdlib 未暴露，不支持即定义为稳定失败）；本实现通过每步 fsync 与
        inode 复核保证：任何可检测的替换点都不删除未知节点，冲突时两份内容均可诊断，
        且任一命名点崩溃后至少一个授权名字可证明存在。
        """
        quarantine = _quarantine_name(self.name)
        # 1. no-clobber 快照：link 原子捕获当前节点。
        try:
            os.link(
                self.name,
                quarantine,
                src_dir_fd=self.parent_fd,
                dst_dir_fd=self.parent_fd,
                follow_symlinks=False,
            )
        except FileExistsError as exc:
            raise DraftError("operation.external_change", self.code_path) from exc
        except OSError as exc:
            raise self._io_error(exc) from exc
        # 2. quarantine 名字耐久后，才允许删除原名。
        _fsync_dir_fd(self.parent_fd, self.code_path)
        # 3. inode 复核：原名仍指向刚隔离的同一节点。
        snapshot = _name_node(
            self.parent_fd, quarantine, code="operation.external_change", code_path=self.code_path
        )
        if not _same_name_node(
            self.parent_fd,
            self.name,
            snapshot,
            code="operation.external_change",
            code_path=self.code_path,
        ):
            raise DraftError("operation.external_change", self.code_path)
        # 4. 删除原名。
        try:
            os.unlink(self.name, dir_fd=self.parent_fd)
        except OSError as exc:
            raise self._io_error(exc) from exc
        _fsync_dir_fd(self.parent_fd, self.code_path)
        # 5. 验证隔离节点：先捕获 inode，再分类与读内容验摘要。
        verified_node = _name_node(
            self.parent_fd, quarantine, code="operation.external_change", code_path=self.code_path
        )
        quarantined = _AnchoredLeaf(
            self.parent_fd, quarantine, self.display, self.code, self.code_path
        )
        node = quarantined.classify()
        if node is not EntryKind.FILE:
            self._restore_quarantined(quarantine)
            raise DraftError("operation.external_change", self.code_path)
        if sha256_bytes(quarantined.read()) != expected_digest:
            self._restore_quarantined(quarantine)
            raise DraftError("operation.external_change", self.code_path)
        # 6. 消费前 inode 复核：quarantine 名仍指向刚验证的同一节点。
        if not _same_name_node(
            self.parent_fd,
            quarantine,
            verified_node,
            code="operation.external_change",
            code_path=self.code_path,
        ):
            raise DraftError("operation.external_change", self.code_path)
        # 7. 消费（删除）quarantine。
        try:
            os.unlink(quarantine, dir_fd=self.parent_fd)
        except OSError as exc:
            raise self._io_error(exc) from exc
        _fsync_dir_fd(self.parent_fd, self.code_path)

    def _restore_quarantined(self, quarantine: str) -> None:
        """no-clobber 恢复 quarantine→原名：原名占用时同时保留两者并报告外部变化。"""
        try:
            os.link(
                quarantine,
                self.name,
                src_dir_fd=self.parent_fd,
                dst_dir_fd=self.parent_fd,
                follow_symlinks=False,
            )
        except FileExistsError:
            raise DraftError("operation.external_change", self.code_path) from None
        except OSError as exc:
            raise self._io_error(exc) from exc
        try:
            os.unlink(quarantine, dir_fd=self.parent_fd)
        except OSError as exc:
            raise self._io_error(exc) from exc
        _fsync_dir_fd(self.parent_fd, self.code_path)


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

    def quarantine_delete(self, _expected_digest: str) -> None:
        raise AssertionError("missing leaf cannot be quarantined")

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
    # 根以 no-follow 目录方式打开：跟根本身是符号链接时稳定领域错误，
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
            # 每打开下一层立即关闭上一层：成功、中间缺失与异常分支都保证
            # fd 数量回到调用前，避免大 mutation/深路径耗尽描述符。
            os.close(parent_fd)
            parent_fd = next_fd
        yield _AnchoredLeaf(parent_fd, relative.name, relative, code, code_path)
    finally:
        if not closed:
            os.close(parent_fd)


def durable_write(
    root: Path,
    relative: Path,
    value: bytes,
    *,
    write_step: DurabilityStep | None = None,
    file_fsync_step: DurabilityStep | None = None,
    directory_fsync_step: DurabilityStep | None = None,
) -> None:
    """create-only 严格耐久写：journal bootstrap、original/staged、
    attempt、cleanup evidence 等首次创建目标。

    目录链从 ``root`` no-follow 逐层打开/创建：缺失目录 mkdirat 创建后立即
    fsync 父目录，逐层同步到最终父目录，绝不按绝对路径重新解析祖先。目标与 pending
    都必须是 missing 的瞬时状态：O_EXCL 创建确定名 pending（绝不截断未知 pending）+
    link 无覆盖发布（发布瞬间目标已出现即外部变化），绝不覆盖既有目标。严格同步失败
    映射 operation.durability_failed。
    """
    parent_fd = ensure_directory_chain(
        root,
        relative.parent,
        code=CheckCode.OPERATION_DURABILITY_FAILED.value,
        code_path=relative.parent,
    )
    try:
        _create_only_publish(
            parent_fd,
            relative.name,
            value,
            code_path=relative,
            write_step=write_step,
            file_fsync_step=file_fsync_step,
            directory_fsync_step=directory_fsync_step,
        )
    finally:
        os.close(parent_fd)


def acquire_lock(root: Path, operation_id: str) -> None:
    """排他创建写锁。锁创建的各步失败都在最窄边界映射稳定领域错误。

    write/file-fsync/parent-fsync 任一失败都映射 operation.durability_failed：锁文件
    可能已存在但未耐久，下一次命令按实际锁存在分类（存在→recovery_required，丢失→
    继续）；绝不泄漏原始 OSError 到 CLI。锁文件路径不得为符号链接。
    """
    reject_symlink_components(root, LOCK_RELATIVE)
    lock = root / LOCK_RELATIVE
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise DraftError(CheckCode.OPERATION_DURABILITY_FAILED.value, lock.parent) from exc
    payload = json.dumps({"operation_id": operation_id}) + "\n"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise DraftError(CheckCode.OPERATION_RECOVERY_REQUIRED.value, lock) from exc
    except OSError as exc:
        raise DraftError(CheckCode.OPERATION_DURABILITY_FAILED.value, lock) from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise DraftError(CheckCode.OPERATION_DURABILITY_FAILED.value, lock) from exc
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
    # lstat 分类：缺失→无锁；非普通文件（目录/链接等）→损坏。分类与读取共享稳定
    # 领域错误：lstat/read/decode 任一失败都映射 journal_corrupt，
    # 绝不泄漏原始 OSError。
    node = classify_node_or_error(
        lock, code=CheckCode.OPERATION_JOURNAL_CORRUPT.value, code_path=lock
    )
    if node is EntryKind.MISSING:
        return None
    if node is not EntryKind.FILE:
        raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, lock)
    data = _read_persistent_bytes(lock, code=CheckCode.OPERATION_JOURNAL_CORRUPT.value)
    decoded = _decode_persistent_json(
        data, code=CheckCode.OPERATION_JOURNAL_CORRUPT.value, path=lock
    )
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
    lock = root / LOCK_RELATIVE
    try:
        lock.unlink()
    except OSError as exc:
        # 锁移除失败（已消失/权限/类型变化）：锁状态无法证明，稳定领域错误不泄漏。
        raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, lock) from exc
    fsync_directory(lock.parent)


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
    # 链接索引结果（规格 §4）：必填字段，合法性由 phase 决定，pre-index/rollback 阶段必须为 None。
    link_index_revision: str | None
    link_index_result: LinkIndexResult | None
    link_index_sha256: str | None


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
                "member_types": [list(member) for member in observation.member_types],
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
    # 序列化只输出当前 phase 允许且有值的索引键；禁止字段必须缺失，不写 JSON null。
    allowed = _link_index_allowed_fields(journal.phase)
    if journal.link_index_revision is not None and "link_index_revision" in allowed:
        payload["link_index_revision"] = journal.link_index_revision
    if journal.link_index_result is not None and "link_index_result" in allowed:
        payload["link_index_result"] = journal.link_index_result.value
    if journal.link_index_sha256 is not None and "link_index_sha256" in allowed:
        payload["link_index_sha256"] = journal.link_index_sha256
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
        member_types_raw = item.get("member_types", [])
        if not isinstance(member_types_raw, list) or not all(
            isinstance(member, list)
            and len(cast("list[object]", member)) == 2
            and all(isinstance(value, str) for value in cast("list[object]", member))
            for member in cast("list[object]", member_types_raw)
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
            member_types=tuple(
                (cast("str", member[0]), cast("str", member[1]))
                for member in cast("list[list[object]]", member_types_raw)
            ),
        )
        if observation_shape_error(entry) is not None:
            raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, journal_path)
        entries.append(entry)
    return ObservationManifest(entries=tuple(entries))


def _decode_persistent_json(data: bytes, *, code: str, path: Path) -> object:
    """持久 JSON 严格解码边界：非法 UTF-8 与 JSON 错误统一映射领域错误。

    journal 与 lock 共用此 helper；``UnicodeDecodeError`` 绝不泄漏原始异常。
    """
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DraftError(code, path) from exc


def _parse_journal_bytes(data: bytes, journal_path: Path) -> Journal:
    """持久 JSON bytes 解码 + 形状验证的单一解析边界。

    严格 UTF-8 解码、JSON 解析与 journal 形状验证在同一处；``UnicodeDecodeError``
    与 JSON 错误统一映射 operation.journal_corrupt，绝不泄漏原始异常。
    """
    decoded = _decode_persistent_json(
        data, code=CheckCode.OPERATION_JOURNAL_CORRUPT.value, path=journal_path
    )
    return _parse_journal(decoded, journal_path)


# ---------------------------------------------------------------------------
# 日志单步后继的权威转换验证器
# ---------------------------------------------------------------------------
# journal pending 或普通 write_journal 发布前都必须证明 new 是 old（或 bootstrap）
# 的合法后继：identity 不可变、阶段只能走状态图允许的边、mutation 已冻结后不可增删换、
# applied 只能 false→true、待删空目录冻结后不可变、metadata 只能按拥有它的转换变化。
# 纯函数，不访问文件系统；``resolve_journal_pending`` 与 ``write_journal`` 共用。

# 合法阶段边：覆盖正向执行、恢复收敛与 trash/restore/adopt 生命周期全部写点。
# 终态（completed/rolled_back）没有回到中段的后继。
# 受管理操作与 adopt 共用的提交前阶段边；不含 validating→completed，由各 kind 按
# 终态语义补入（普通操作 completed；trash 走 trash_retained）。
_COMMON_PRE_COMMIT_EDGES: frozenset[tuple[OperationPhase, OperationPhase]] = frozenset(
    {
        (OperationPhase.ALLOCATING, OperationPhase.ALLOCATING),
        (OperationPhase.ALLOCATING, OperationPhase.PREPARED),
        (OperationPhase.ALLOCATING, OperationPhase.ROLLED_BACK),
        (OperationPhase.PREPARED, OperationPhase.APPLYING),
        (OperationPhase.PREPARED, OperationPhase.ROLLING_BACK),
        (OperationPhase.APPLYING, OperationPhase.APPLYING),
        (OperationPhase.APPLYING, OperationPhase.VALIDATING),
        (OperationPhase.APPLYING, OperationPhase.ROLLING_BACK),
        (OperationPhase.VALIDATING, OperationPhase.VALIDATING),
        (OperationPhase.VALIDATING, OperationPhase.ROLLING_BACK),
        (OperationPhase.ROLLING_BACK, OperationPhase.ROLLING_BACK),
        (OperationPhase.ROLLING_BACK, OperationPhase.ROLLED_BACK),
    }
)

_FORWARD_COMPLETE_EDGE: frozenset[tuple[OperationPhase, OperationPhase]] = frozenset(
    {(OperationPhase.VALIDATING, OperationPhase.COMPLETED)}
)

_GENERIC_STRUCTURAL_KINDS = frozenset(
    {
        OperationKind.CREATE,
        OperationKind.MOVE,
        OperationKind.PROMOTE,
        OperationKind.COLLAPSE,
        OperationKind.STATUS_SET,
        OperationKind.RELATION_ADD,
        OperationKind.RELATION_REMOVE,
        OperationKind.GENERATE_CONCLUSIONS,
    }
)

# kind 拥有的 phase 与边的单一权威来源：解析单条 journal 与验证后继转换
# 都从这里读取，不允许普通操作借用 adopt/trash 生命周期边。RECOVER 不允许形成持久
# journal，phase 集为空。
_BASE_PHASES = frozenset(
    {
        OperationPhase.ALLOCATING,
        OperationPhase.PREPARED,
        OperationPhase.APPLYING,
        OperationPhase.VALIDATING,
        OperationPhase.ROLLING_BACK,
        OperationPhase.COMPLETED,
        OperationPhase.ROLLED_BACK,
    }
)
_GENERIC_STRUCTURAL_PHASES = _BASE_PHASES | _LINK_INDEX_PHASES

_PHASES_BY_KIND: dict[OperationKind, frozenset[OperationPhase]] = {
    kind: _GENERIC_STRUCTURAL_PHASES for kind in _GENERIC_STRUCTURAL_KINDS
}
# adopt 只修改 `.draft/` 内 marker，不进入索引阶段（规格 §5）。
_PHASES_BY_KIND[OperationKind.ADOPT] = _BASE_PHASES
# restore 与普通结构 kind 一样进入索引阶段后到达 completed。
_PHASES_BY_KIND[OperationKind.RESTORE] = _GENERIC_STRUCTURAL_PHASES
_PHASES_BY_KIND[OperationKind.TRASH] = _GENERIC_STRUCTURAL_PHASES | frozenset(
    {
        OperationPhase.TRASH_RETAINED,
        OperationPhase.TRASH_HANDOFF_PENDING,
        OperationPhase.TRASH_HANDOFF_SUCCEEDED,
        OperationPhase.TRASH_PURGE_COMMITTED,
    }
)
_PHASES_BY_KIND[OperationKind.RECOVER] = frozenset()
# 专用 maintenance kind：allocating → updating → published/rebuild_required → completed。
_PHASES_BY_KIND[OperationKind.LINK_INDEX_UPDATE] = frozenset(
    {
        OperationPhase.ALLOCATING,
        OperationPhase.LINK_INDEX_UPDATING,
        OperationPhase.LINK_INDEX_PUBLISHED,
        OperationPhase.LINK_INDEX_REBUILD_REQUIRED,
        OperationPhase.COMPLETED,
        OperationPhase.ROLLED_BACK,
    }
)

_BASE_STRUCTURAL_EDGES = _COMMON_PRE_COMMIT_EDGES | _FORWARD_COMPLETE_EDGE
_GENERIC_LINK_INDEX_EDGES = frozenset(
    {
        (OperationPhase.VALIDATING, OperationPhase.LINK_INDEX_UPDATING),
        (OperationPhase.LINK_INDEX_UPDATING, OperationPhase.LINK_INDEX_PUBLISHED),
        (OperationPhase.LINK_INDEX_UPDATING, OperationPhase.LINK_INDEX_REBUILD_REQUIRED),
        (OperationPhase.LINK_INDEX_PUBLISHED, OperationPhase.COMPLETED),
        (OperationPhase.LINK_INDEX_REBUILD_REQUIRED, OperationPhase.COMPLETED),
    }
)
_GENERIC_STRUCTURAL_EDGES = _BASE_STRUCTURAL_EDGES | _GENERIC_LINK_INDEX_EDGES

_EDGES_BY_KIND: dict[OperationKind, frozenset[tuple[OperationPhase, OperationPhase]]] = {
    kind: _GENERIC_STRUCTURAL_EDGES for kind in _GENERIC_STRUCTURAL_KINDS
}
_EDGES_BY_KIND[OperationKind.ADOPT] = _BASE_STRUCTURAL_EDGES | frozenset(
    {
        # adopt：marker 已生效恢复允许 prepared/applying 直接提交（规格 §12.2）。
        (OperationPhase.PREPARED, OperationPhase.COMPLETED),
        (OperationPhase.APPLYING, OperationPhase.COMPLETED),
        # adopt：marker 仍为 before 时放弃本次登记（_recover_adopt 观察变化路径）。
        (OperationPhase.PREPARED, OperationPhase.ROLLED_BACK),
        (OperationPhase.APPLYING, OperationPhase.ROLLED_BACK),
        (OperationPhase.VALIDATING, OperationPhase.ROLLED_BACK),
    }
)
_EDGES_BY_KIND[OperationKind.RESTORE] = _GENERIC_STRUCTURAL_EDGES | frozenset(
    {
        # restore：proven-unapplied 放弃（未应用任何 mutation）直接写 rolled_back。
        (OperationPhase.PREPARED, OperationPhase.ROLLED_BACK),
    }
)
_EDGES_BY_KIND[OperationKind.TRASH] = _COMMON_PRE_COMMIT_EDGES | frozenset(
    {
        (OperationPhase.VALIDATING, OperationPhase.TRASH_RETAINED),
        (OperationPhase.TRASH_RETAINED, OperationPhase.TRASH_RETAINED),
        (OperationPhase.TRASH_RETAINED, OperationPhase.TRASH_HANDOFF_PENDING),
        (OperationPhase.TRASH_RETAINED, OperationPhase.ROLLED_BACK),
        (OperationPhase.TRASH_HANDOFF_PENDING, OperationPhase.TRASH_HANDOFF_PENDING),
        (OperationPhase.TRASH_HANDOFF_PENDING, OperationPhase.TRASH_PURGE_COMMITTED),
        (OperationPhase.TRASH_HANDOFF_PENDING, OperationPhase.TRASH_HANDOFF_SUCCEEDED),
        (OperationPhase.TRASH_HANDOFF_SUCCEEDED, OperationPhase.COMPLETED),
        (OperationPhase.TRASH_PURGE_COMMITTED, OperationPhase.COMPLETED),
        (OperationPhase.VALIDATING, OperationPhase.LINK_INDEX_UPDATING),
        (OperationPhase.LINK_INDEX_UPDATING, OperationPhase.LINK_INDEX_PUBLISHED),
        (OperationPhase.LINK_INDEX_UPDATING, OperationPhase.LINK_INDEX_REBUILD_REQUIRED),
        (OperationPhase.LINK_INDEX_PUBLISHED, OperationPhase.TRASH_RETAINED),
        (OperationPhase.LINK_INDEX_REBUILD_REQUIRED, OperationPhase.TRASH_RETAINED),
    }
)
_EDGES_BY_KIND[OperationKind.RECOVER] = frozenset()
_EDGES_BY_KIND[OperationKind.LINK_INDEX_UPDATE] = frozenset(
    {
        (OperationPhase.ALLOCATING, OperationPhase.ALLOCATING),
        (OperationPhase.ALLOCATING, OperationPhase.LINK_INDEX_UPDATING),
        (OperationPhase.ALLOCATING, OperationPhase.ROLLED_BACK),
        (OperationPhase.LINK_INDEX_UPDATING, OperationPhase.LINK_INDEX_PUBLISHED),
        (OperationPhase.LINK_INDEX_UPDATING, OperationPhase.LINK_INDEX_REBUILD_REQUIRED),
        (OperationPhase.LINK_INDEX_PUBLISHED, OperationPhase.COMPLETED),
        (OperationPhase.LINK_INDEX_REBUILD_REQUIRED, OperationPhase.COMPLETED),
    }
)

_HANDOFF_KEYS = frozenset(
    {
        HANDOFF_ERROR_TYPE_KEY,
        HANDOFF_ERROR_MESSAGE_KEY,
        HANDOFF_ATTEMPTED_AT_KEY,
        NEXT_HANDOFF_ATTEMPT_KEY,
    }
)

# 阶段边允许的 metadata 增量（add, change, remove）：None=任意键；frozenset=只允许
# 这些键；不在表中的阶段边 metadata 必须逐键相等。
_JOURNAL_METADATA_DELTA: dict[
    tuple[OperationPhase, OperationPhase],
    tuple[frozenset[str] | None, frozenset[str] | None, frozenset[str] | None],
] = {
    # allocating 计划期：只允许已批准的关联/观察元数据单调补充，不得改写或删除。
    (OperationPhase.ALLOCATING, OperationPhase.ALLOCATING): (None, frozenset(), frozenset()),
    # prepared 一次性冻结完整状态：计划合并的 metadata 键（operation_id、
    # public_changed_paths 等）在此一次性确立，之后 metadata 不可变（除 trash 转换）。
    (OperationPhase.ALLOCATING, OperationPhase.PREPARED): (None, frozenset(), frozenset()),
    # trash_retained：恢复认领键可增可删，其余不变。
    (OperationPhase.TRASH_RETAINED, OperationPhase.TRASH_RETAINED): (
        frozenset({RESTORE_OPERATION_ID_KEY}),
        frozenset(),
        frozenset({RESTORE_OPERATION_ID_KEY}),
    ),
    # handoff 失败记录：重试节流替换失败字段，purge 提交时新增失败字段。
    (OperationPhase.TRASH_HANDOFF_PENDING, OperationPhase.TRASH_HANDOFF_PENDING): (
        _HANDOFF_KEYS,
        _HANDOFF_KEYS,
        frozenset(),
    ),
    (OperationPhase.TRASH_HANDOFF_PENDING, OperationPhase.TRASH_PURGE_COMMITTED): (
        _HANDOFF_KEYS,
        _HANDOFF_KEYS,
        frozenset(),
    ),
    # 提交点 → completed：只允许追加 disposition。
    (OperationPhase.TRASH_HANDOFF_SUCCEEDED, OperationPhase.COMPLETED): (
        frozenset({DISPOSITION_KEY}),
        frozenset(),
        frozenset(),
    ),
    (OperationPhase.TRASH_PURGE_COMMITTED, OperationPhase.COMPLETED): (
        frozenset({DISPOSITION_KEY}),
        frozenset(),
        frozenset(),
    ),
}


def _raise_journal_invalid(path: Path) -> NoReturn:
    raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, path)


def _link_index_allowed_fields(phase: OperationPhase) -> frozenset[str]:
    """序列化允许的索引键集合：禁止字段必须缺失，不写 JSON null（规格 §4）。"""
    if phase in _PRE_LINK_INDEX_PHASES or phase in _ROLLBACK_PHASES:
        return frozenset()
    if phase is OperationPhase.LINK_INDEX_UPDATING:
        return frozenset({"link_index_revision"})
    if phase is OperationPhase.LINK_INDEX_PUBLISHED:
        return frozenset({"link_index_revision", "link_index_result", "link_index_sha256"})
    if phase is OperationPhase.LINK_INDEX_REBUILD_REQUIRED:
        return frozenset({"link_index_revision", "link_index_result"})
    return frozenset({"link_index_revision", "link_index_result", "link_index_sha256"})


def _validate_link_index_fields(journal: Journal, *, path: Path) -> None:
    """逐条 journal 的链接索引字段矩阵（规格 §4）：pre-index/rollback 禁止、
    索引阶段按行约束、终态保留阶段原样保留。"""
    phase = journal.phase
    revision = journal.link_index_revision
    result = journal.link_index_result
    sha256 = journal.link_index_sha256
    if phase in _PRE_LINK_INDEX_PHASES or phase in _ROLLBACK_PHASES:
        if revision is not None or result is not None or sha256 is not None:
            _raise_journal_invalid(path)
        return
    if phase is OperationPhase.LINK_INDEX_UPDATING:
        if revision is None or result is not None or sha256 is not None:
            _raise_journal_invalid(path)
        validate_link_index_revision(revision, path=path)
        return
    if phase is OperationPhase.LINK_INDEX_PUBLISHED:
        if revision is None or result is not LinkIndexResult.PUBLISHED or sha256 is None:
            _raise_journal_invalid(path)
        validate_link_index_revision(revision, path=path)
        if not _valid_sha256(sha256):
            _raise_journal_invalid(path)
        return
    if phase is OperationPhase.LINK_INDEX_REBUILD_REQUIRED:
        if revision is None or result is not LinkIndexResult.REBUILD_REQUIRED or sha256 is not None:
            _raise_journal_invalid(path)
        validate_link_index_revision(revision, path=path)
        return
    # 终态保留阶段：全缺省合法，或 revision+result 原样保留；sha256 仅 published 保留。
    if revision is None:
        if result is not None or sha256 is not None:
            _raise_journal_invalid(path)
        return
    if result is None:
        _raise_journal_invalid(path)
    validate_link_index_revision(revision, path=path)
    if result is LinkIndexResult.PUBLISHED:
        if sha256 is None or not _valid_sha256(sha256):
            _raise_journal_invalid(path)
    elif sha256 is not None:  # REBUILD_REQUIRED：禁止 sha256。
        _raise_journal_invalid(path)


_LINK_INDEX_FIELD_SET_EDGES = frozenset(
    {
        (OperationPhase.VALIDATING, OperationPhase.LINK_INDEX_UPDATING),
        (OperationPhase.ALLOCATING, OperationPhase.LINK_INDEX_UPDATING),
    }
)


def _validate_link_index_transition(old: Journal, new: Journal, *, path: Path) -> None:
    """链接索引字段的后继增量（规格 §5）：只在索引阶段边建立/收敛字段，
    其余边必须原样保留；``link_index_updating`` 之后不得进入 rollback。"""
    old_state = (old.link_index_revision, old.link_index_result, old.link_index_sha256)
    new_state = (new.link_index_revision, new.link_index_result, new.link_index_sha256)
    if (old.phase, new.phase) in _LINK_INDEX_FIELD_SET_EDGES:
        # 进入 updating：从全空变为只设 revision，result/sha256 仍禁止。
        if old_state != (None, None, None):
            _raise_journal_invalid(path)
        if new.link_index_revision is None or new.link_index_result is not None:
            _raise_journal_invalid(path)
        if new.link_index_sha256 is not None:
            _raise_journal_invalid(path)
        return
    if (old.phase, new.phase) == (
        OperationPhase.LINK_INDEX_UPDATING,
        OperationPhase.LINK_INDEX_PUBLISHED,
    ):
        if old.link_index_revision is None or old.link_index_result is not None:
            _raise_journal_invalid(path)
        if old.link_index_sha256 is not None:
            _raise_journal_invalid(path)
        if (new.link_index_revision, new.link_index_result) != (
            old.link_index_revision,
            LinkIndexResult.PUBLISHED,
        ):
            _raise_journal_invalid(path)
        if new.link_index_sha256 is None:
            _raise_journal_invalid(path)
        return
    if (old.phase, new.phase) == (
        OperationPhase.LINK_INDEX_UPDATING,
        OperationPhase.LINK_INDEX_REBUILD_REQUIRED,
    ):
        if old.link_index_revision is None or old.link_index_result is not None:
            _raise_journal_invalid(path)
        if old.link_index_sha256 is not None:
            _raise_journal_invalid(path)
        if new_state != (old.link_index_revision, LinkIndexResult.REBUILD_REQUIRED, None):
            _raise_journal_invalid(path)
        return
    if new.phase in _ROLLBACK_PHASES:
        # 进入 rollback：链接索引字段必须清空（spec §4/§5 rollback 禁止）。既有索引
        # 结果随业务回滚作废（如 restore 把 trash 从 trash_retained 提交为 rolled_back），
        # 不再作为 authority 结果。``link_index_updating`` 之后不允许进入 rollback，
        # 因此此处只会清空终态保留阶段携带的既有结果。
        if new_state != (None, None, None):
            _raise_journal_invalid(path)
        return
    # 其余边（pre-index 保持空、终态保留索引结果）字段必须原样保留。
    if old_state != new_state:
        _raise_journal_invalid(path)


def _validate_mutation_transition(old: Journal, new: Journal, *, path: Path) -> None:
    """mutation 列表转换规则：冻结前只允许 allocating→prepared 一次性冻结；
    冻结后 identity 逐条不可变，applied 只能 false→true 单向推进。"""
    if not old.mutations:
        if old.phase is OperationPhase.ALLOCATING and new.phase is OperationPhase.PREPARED:
            return  # 一次性冻结点：mutation 形状已由解析边界验证。
        if not new.mutations:
            return  # allocating→allocating/→rolled_back 必须保持空。
        _raise_journal_invalid(path)
    if len(new.mutations) != len(old.mutations):
        _raise_journal_invalid(path)
    for before, after in zip(old.mutations, new.mutations, strict=True):
        if (
            before.path,
            before.kind,
            before.before_sha256,
            before.after_sha256,
            before.original,
            before.staged,
        ) != (
            after.path,
            after.kind,
            after.before_sha256,
            after.after_sha256,
            after.original,
            after.staged,
        ):
            # 已冻结 mutation 不可增删、换路径/种类/摘要/恢复材料。
            _raise_journal_invalid(path)
        if after.applied and not before.applied:
            continue  # false→true：合法的已应用推进。
        if after.applied == before.applied:
            continue  # 状态保持（如 adopt 恢复直接终态化）。
        _raise_journal_invalid(path)  # applied=true→false 反转：拒绝。


def validate_journal_kind_phase(journal: Journal, *, path: Path) -> None:
    """kind 是否拥有该 phase 的单一权威检查。

    解析单条 journal 与验证后继转换共用；RECOVER 不允许形成持久 journal，任何 phase
    都被拒绝。跨 kind 借用 adopt/trash 生命周期状态在此被拒，不进入权威状态。
    """
    if journal.phase not in _PHASES_BY_KIND.get(journal.kind, frozenset()):
        _raise_journal_invalid(path)
    _validate_link_index_fields(journal, path=path)


def _validate_journal_transition(old: Journal | None, new: Journal, *, path: Path) -> None:
    """验证 new 是 old（或 bootstrap）的合法单步后继（纯函数）。

    old=None 为 bootstrap：只允许 allocating、空 mutations、空待删空目录。
    违反任何身份/阶段/内容规则抛 operation.journal_corrupt。阶段合法性由
    ``(kind, phase)`` 与 ``(kind, old_phase, new_phase)`` 共同决定。
    """
    validate_journal_kind_phase(new, path=path)
    if old is None:
        if (
            new.phase is not OperationPhase.ALLOCATING
            or new.mutations
            or new.remove_empty_directories
        ):
            _raise_journal_invalid(path)
        return
    if (
        new.operation_id != old.operation_id
        or new.kind != old.kind
        or new.root_state != old.root_state
        or new.directory != old.directory
    ):
        # identity（operation_id/kind/root_state/操作目录）不可改写。
        _raise_journal_invalid(path)
    if (old.phase, new.phase) not in _EDGES_BY_KIND.get(new.kind, frozenset()):
        _raise_journal_invalid(path)
    _validate_mutation_transition(old, new, path=path)
    _validate_link_index_transition(old, new, path=path)
    if (
        not (old.phase is OperationPhase.ALLOCATING and new.phase is OperationPhase.ALLOCATING)
        and old.observations != new.observations
    ):
        # 观察清单只允许在 allocating→allocating（恢复读集补充）变化，其余必须一致。
        _raise_journal_invalid(path)
    if old.phase is OperationPhase.ALLOCATING:
        if new.phase is OperationPhase.PREPARED:
            pass  # 待删空目录在 prepared 一次性冻结。
        elif new.remove_empty_directories:
            _raise_journal_invalid(path)  # allocating→allocating/→rolled_back 必须保持空。
    elif old.remove_empty_directories != new.remove_empty_directories:
        _raise_journal_invalid(path)
    delta = _JOURNAL_METADATA_DELTA.get((old.phase, new.phase))
    if delta is None:
        if old.metadata != new.metadata:
            _raise_journal_invalid(path)
        return
    add_allowed, change_allowed, remove_allowed = delta
    for key in set(old.metadata) | set(new.metadata):
        if old.metadata.get(key) == new.metadata.get(key):
            continue
        if key not in old.metadata:
            allowed = add_allowed
        elif key not in new.metadata:
            allowed = remove_allowed
        else:
            allowed = change_allowed
        if allowed is None or key in allowed:  # None=该方向允许任意键。
            continue
        _raise_journal_invalid(path)


def validate_journal_transition(old: Journal | None, new: Journal, *, path: Path) -> None:
    """公开的转换验证入口：resolve_journal_pending 与 write_journal 共用。"""
    _validate_journal_transition(old, new, path=path)


def _read_persistent_bytes(path: Path, *, code: str) -> bytes:
    """no-follow 读取持久协议文件 bytes 一次。

    分类后（is_file 等）与实际读取之间节点被删除/目录化/替换为链接时，原始 OSError
    在最窄边界映射为稳定领域错误，绝不泄漏给 CLI。调用方负责解码与解析。
    """
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise DraftError(code, path) from exc
    try:
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1 << 16)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    except OSError as exc:
        raise DraftError(code, path) from exc
    finally:
        os.close(descriptor)


def _read_optional_journal(path: Path) -> Journal | None:
    """读取当前持久 journal；缺失（分类为 missing）返回 None，其余类型/读取失败报损坏。"""
    node = classify_node_or_error(
        path, code=CheckCode.OPERATION_JOURNAL_CORRUPT.value, code_path=path
    )
    if node is EntryKind.MISSING:
        return None
    if node is not EntryKind.FILE:
        _raise_journal_invalid(path)
    return _parse_journal_bytes(
        _read_persistent_bytes(path, code=CheckCode.OPERATION_JOURNAL_CORRUPT.value), path
    )


def read_journal(journal_path: Path) -> Journal:
    return _parse_journal_bytes(
        _read_persistent_bytes(journal_path, code=CheckCode.OPERATION_JOURNAL_CORRUPT.value),
        journal_path,
    )


def resolve_journal_pending(journal_path: Path) -> None:
    """把崩溃遗留的 ``journal.json.pending`` 收敛为已发布日志。

    只有能由当前耐久 journal 通过一次合法状态转换产生的完整新对象才可发布：
    ``validate_journal_transition`` 消费 old/new 两个纯对象判定合法单步后继，不凭文件名
    获得权威。可解析但非法后继（改身份/阶段倒退/换 mutation/applied 反转/metadata 越权）
    保留 old+pending 并报 journal_corrupt；半写/类型错误 pending 在旧 journal 存在时保留
    旧 journal 推进（崩溃容错，checker 报告残留），旧 journal 缺失时无法继续报损坏。
    绝不按文件名删除 pending。
    """
    pending_path = journal_path.with_name(_pending_name(journal_path.name))
    pending_kind = classify_node_or_error(
        pending_path, code=CheckCode.OPERATION_JOURNAL_CORRUPT.value, code_path=pending_path
    )
    if pending_kind is EntryKind.MISSING:
        return
    old = _read_optional_journal(journal_path)
    if pending_kind is not EntryKind.FILE:
        if old is not None:
            return  # 类型错误 pending：保留旧 journal 推进，checker 报告。
        raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, pending_path)
    try:
        journal = _parse_journal_bytes(
            _read_persistent_bytes(pending_path, code=CheckCode.OPERATION_JOURNAL_CORRUPT.value),
            pending_path,
        )
    except DraftError:
        if old is not None:
            return  # 半写 pending：旧 journal 仍可推进（崩溃容错），checker 报告。
        raise
    # 权威后继验证：identity 不可变、阶段边合法、mutation/观察/待删目录/metadata 受约束。
    validate_journal_transition(old, journal, path=journal_path)
    parent_fd = _open_parent_dir(
        journal_path.parent,
        code=CheckCode.OPERATION_DURABILITY_FAILED.value,
        code_path=journal_path.parent,
    )
    try:
        try:
            durability_checkpoint(DurabilityStep.JOURNAL_WRITE)
            os.replace(
                pending_path.name,
                journal_path.name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
        except OSError as exc:
            raise DraftError(CheckCode.OPERATION_DURABILITY_FAILED.value, pending_path) from exc
        _fsync_dir_fd(
            parent_fd,
            journal_path.parent,
            durability_step=DurabilityStep.JOURNAL_DIRECTORY_FSYNC,
        )
    finally:
        os.close(parent_fd)


def _parse_journal(data: object, journal_path: Path) -> Journal:
    """纯解析：无文件系统访问，可在单测中直接构造畸形输入。

    在单一解析边界完成全部形状与协议验证：路径、hash 形状、kind/hash 组合、
    original/staged 协议形状与唯一性。任何 I/O 消费者只接触已验证对象，
    恢复材料路径不得由字符串拼接决定 I/O 边界。
    """
    if not isinstance(data, dict):
        raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, journal_path)
    parsed = cast("dict[str, object]", data)
    version = parsed.get("journal_version")
    if version != JOURNAL_VERSION:
        raise DraftError(
            CheckCode.OPERATION_JOURNAL_VERSION_UNSUPPORTED.value,
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
        # 链接索引字段：区分键缺失（None）与显式 null（拒绝）；键缺失与值语义由
        # validate_journal_kind_phase 按 phase 字段矩阵校验。
        if "link_index_revision" in parsed:
            raw_revision = parsed["link_index_revision"]
            if raw_revision is None:
                raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, journal_path)
            link_index_revision = _expect_str(raw_revision, journal_path)
        else:
            link_index_revision = None
        if "link_index_result" in parsed:
            raw_result = parsed["link_index_result"]
            if raw_result is None:
                raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, journal_path)
            link_index_result = LinkIndexResult(_expect_str(raw_result, journal_path))
        else:
            link_index_result = None
        if "link_index_sha256" in parsed:
            raw_sha256 = parsed["link_index_sha256"]
            if raw_sha256 is None:
                raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, journal_path)
            link_index_sha256 = _expect_str(raw_sha256, journal_path)
            if not _valid_sha256(link_index_sha256):
                raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, journal_path)
        else:
            link_index_sha256 = None
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
    journal = Journal(
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
        link_index_revision=link_index_revision,
        link_index_result=link_index_result,
        link_index_sha256=link_index_sha256,
    )
    # 磁盘读取验证 kind 拥有该 phase：跨 kind 借用 adopt/trash 状态的
    # journal 即使结构合法也不是权威状态。链接索引字段矩阵在同一边界校验（规格 §4）。
    validate_journal_kind_phase(journal, path=journal_path)
    return journal


def write_journal(journal: Journal) -> None:
    """耐久写入 journal，发布前验证 new 是当前持久 journal 的合法单步后继。

    先收敛崩溃遗留 pending（有效后继发布、半写残留保留旧 journal），再从磁盘重读当前
    持久 journal 作为 old，以 ``validate_journal_transition`` 证明 new 是合法后继后才发布。
    bootstrap（无旧 journal）走 create-only；更新走经过转换验证的原子 replace。
    """
    path = journal.directory / "journal.json"
    resolve_journal_pending(path)
    old = _read_optional_journal(path)
    validate_journal_transition(old, journal, path=path)
    parent_fd = ensure_directory_chain(
        path.parent,
        Path("."),
        code=CheckCode.OPERATION_DURABILITY_FAILED.value,
        code_path=path.parent,
    )
    try:
        if old is None:
            _create_only_publish(
                parent_fd,
                path.name,
                _journal_to_json(journal),
                code_path=path,
                write_step=DurabilityStep.JOURNAL_WRITE,
                directory_fsync_step=DurabilityStep.JOURNAL_DIRECTORY_FSYNC,
            )
        else:
            _replace_publish(
                parent_fd,
                path.name,
                _journal_to_json(journal),
                code_path=path,
                write_step=DurabilityStep.JOURNAL_WRITE,
                directory_fsync_step=DurabilityStep.JOURNAL_DIRECTORY_FSYNC,
            )
    finally:
        os.close(parent_fd)


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
        link_index_revision=journal.link_index_revision,
        link_index_result=journal.link_index_result,
        link_index_sha256=journal.link_index_sha256,
    )


def with_link_index_state(
    journal: Journal,
    *,
    revision: str | None,
    result: LinkIndexResult | None,
    sha256: str | None,
) -> Journal:
    """公开的不可变链接索引状态替换；索引阶段收敛路径使用（规格 §4/§5）。"""
    return Journal(
        operation_id=journal.operation_id,
        kind=journal.kind,
        phase=journal.phase,
        root_state=journal.root_state,
        observations=journal.observations,
        mutations=journal.mutations,
        remove_empty_directories=journal.remove_empty_directories,
        metadata=journal.metadata,
        directory=journal.directory,
        link_index_revision=revision,
        link_index_result=result,
        link_index_sha256=sha256,
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
        link_index_revision=journal.link_index_revision,
        link_index_result=journal.link_index_result,
        link_index_sha256=journal.link_index_sha256,
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
        link_index_revision=journal.link_index_revision,
        link_index_result=journal.link_index_result,
        link_index_sha256=journal.link_index_sha256,
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
        link_index_revision=journal.link_index_revision,
        link_index_result=journal.link_index_result,
        link_index_sha256=journal.link_index_sha256,
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
        link_index_revision=journal.link_index_revision,
        link_index_result=journal.link_index_result,
        link_index_sha256=journal.link_index_sha256,
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


def _quarantine_before_then_publish(
    leaf: _AnchoredLeaf, *, expected_digest: str, content: bytes, code_path: Path
) -> None:
    """改写/回滚的节点事务：先隔离 before 到 quarantine（保留），发布新内容后再消费。

    隔离名保持到新内容耐久发布之后才删除：崩溃不会出现"目标缺失且无
    隔离名"的歧义现场——before 节点要么仍在原名、要么可恢复地存在于隔离名，恢复
    据此决定前向补发还是外部变化。每步命名动作后严格 fsync，inode 复核后消费。
    """
    quarantine = _quarantine_name(leaf.name)
    try:
        os.link(
            leaf.name,
            quarantine,
            src_dir_fd=leaf.parent_fd,
            dst_dir_fd=leaf.parent_fd,
            follow_symlinks=False,
        )
    except FileExistsError as exc:
        raise DraftError("operation.external_change", code_path) from exc
    except OSError as exc:
        raise DraftError(CheckCode.OPERATION_DURABILITY_FAILED.value, code_path) from exc
    _fsync_dir_fd(leaf.parent_fd, code_path)
    if not _same_name_node(
        leaf.parent_fd,
        leaf.name,
        _name_node(
            leaf.parent_fd, quarantine, code="operation.external_change", code_path=code_path
        ),
        code="operation.external_change",
        code_path=code_path,
    ):
        # 原名已被替换：不触碰未知节点，no-clobber 恢复隔离名并报告外部变化。
        try:
            os.link(
                quarantine,
                leaf.name,
                src_dir_fd=leaf.parent_fd,
                dst_dir_fd=leaf.parent_fd,
                follow_symlinks=False,
            )
        except FileExistsError:
            pass  # 原名被占用：同时保留两者。
        except OSError as exc:
            raise DraftError(CheckCode.OPERATION_DURABILITY_FAILED.value, code_path) from exc
        raise DraftError("operation.external_change", code_path)
    try:
        os.unlink(leaf.name, dir_fd=leaf.parent_fd)
    except OSError as exc:
        raise DraftError(CheckCode.OPERATION_DURABILITY_FAILED.value, code_path) from exc
    _fsync_dir_fd(leaf.parent_fd, code_path)
    # 发布新内容（create-only no-clobber）：目标此刻缺失。
    leaf.write(content)
    # 消费陈旧 before 隔离名：inode + 内容复核后删除。
    quarantined = _AnchoredLeaf(leaf.parent_fd, quarantine, leaf.display, leaf.code, leaf.code_path)
    verified = _name_node(
        leaf.parent_fd, quarantine, code="operation.external_change", code_path=code_path
    )
    qk = quarantined.classify()
    qd = sha256_bytes(quarantined.read()) if qk is EntryKind.FILE else None
    if qk is not EntryKind.FILE or qd != expected_digest:
        raise DraftError("operation.external_change", code_path)
    if not _same_name_node(
        leaf.parent_fd,
        quarantine,
        verified,
        code="operation.external_change",
        code_path=code_path,
    ):
        raise DraftError("operation.external_change", code_path)
    try:
        os.unlink(quarantine, dir_fd=leaf.parent_fd)
    except OSError as exc:
        raise DraftError(CheckCode.OPERATION_DURABILITY_FAILED.value, code_path) from exc
    _fsync_dir_fd(leaf.parent_fd, code_path)


def _apply_write(
    root: Path,
    target: Path,
    content: bytes,
    *,
    expected_current: str | None,
    code_path: Path,
) -> None:
    """摘要绑定受管写。

    先沿锚定链创建/同步目标父目录，再在同一锚定父目录内验证目标当前
    授权状态并发布：
    - ``expected_current=None``（新建）：目标必须 missing，create-only no-clobber
      发布，目标瞬时出现即 external_change；
    - ``expected_current=摘要``（改写/回滚）：先验证目标为精确摘要节点，经节点事务
      隔离 before 到 quarantine（保留）后 no-clobber 发布新内容，再消费隔离名。

    日志只授权把摘要精确的节点转换为目标状态，绝不覆盖最后复核后才出现的不同节点；
    崩溃遗留的 ``{目标}.pending/.quarantine`` 由恢复按授权摘要收敛。
    """
    cursor = root
    for part in target.parts[:-1]:
        cursor /= part
        if cursor.is_symlink():
            raise DraftError(CheckCode.PATH_SYMLINK_ESCAPE.value, cursor)
    # 目录链逐层创建/同步；晚到的组件链接由后续锚定 O_NOFOLLOW 拒绝。
    parent_fd = ensure_directory_chain(
        root,
        target.parent,
        code=CheckCode.OPERATION_DURABILITY_FAILED.value,
        code_path=target.parent,
    )
    os.close(parent_fd)
    with anchored_leaf(
        root, target, code=CheckCode.PATH_SYMLINK_ESCAPE.value, code_path=code_path
    ) as leaf:
        if isinstance(leaf, _MissingLeaf):
            raise DraftError("operation.external_change", code_path)
        node = leaf.classify()
        if expected_current is None:
            if node is not EntryKind.MISSING:
                raise DraftError("operation.external_change", code_path)
            leaf.write(content)
            return
        if node is not EntryKind.FILE or sha256_bytes(leaf.read()) != expected_current:
            raise DraftError("operation.external_change", code_path)
        _quarantine_before_then_publish(
            leaf, expected_digest=expected_current, content=content, code_path=code_path
        )


def _apply_delete(root: Path, target: Path, *, expected_digest: str) -> None:
    """摘要绑定受管删：目标必须当前为 expected_digest 的精确普通文件，
    经节点事务隔离验证后消费；缺失（提交点后幂等清理）容忍。"""
    with anchored_leaf(
        root, target, code=CheckCode.PATH_SYMLINK_ESCAPE.value, code_path=root / target
    ) as leaf:
        if isinstance(leaf, _MissingLeaf):
            return  # 中间组件缺失：目标必然缺失，幂等删除。
        node = leaf.classify()
        if node is EntryKind.MISSING:
            return
        if node is not EntryKind.FILE or sha256_bytes(leaf.read()) != expected_digest:
            raise DraftError("operation.external_change", root / target)
        leaf.quarantine_delete(expected_digest)


def _execute_apply_phase(root: Path, journal: Journal) -> Journal:
    """Apply pending mutations, durable-recording each step. Returns latest journal."""
    current = journal
    writes = [m for m in current.mutations if m.kind is MutationKind.WRITE]
    deletes = [m for m in current.mutations if m.kind is MutationKind.DELETE]
    for mutation in [*writes, *deletes]:
        if mutation.applied:
            continue
        if mutation.kind is MutationKind.WRITE:
            staged = current.directory / cast("Path", mutation.staged)
            staged_hash = sha256_file(staged)
            if staged_hash is None:
                raise DraftError("operation.staged_missing", staged)
            _apply_write(
                root,
                mutation.path,
                staged.read_bytes(),
                expected_current=mutation.before_sha256,
                code_path=root / mutation.path,
            )
        else:
            _apply_delete(
                root,
                mutation.path,
                expected_digest=cast("str", mutation.before_sha256),
            )
        current = _with_applied(current, mutation.path)
        write_journal(current)
    return current


def _clean_recovery_copies(journal: Journal) -> None:
    """按日志登记路径精确清理恢复副本（originals/staged）。

    全局无副作用预检两个目录的全部成员：任何未登记成员、符号链接、错误类型或
    摘要不符都在第一次 unlink 前报 operation.inconsistent_state 并保留。
    无登记 mutation（allocating 崩溃）时目录必须为空：prepare 已先登记后建文件，
    任何内容都不可解释为本操作副作用，一律拒绝。预检通过后按锚定
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
        directory_node = classify_node_or_error(
            directory,
            code=CheckCode.OPERATION_INCONSISTENT_STATE.value,
            code_path=directory,
        )
        if directory_node is EntryKind.MISSING:
            continue
        if directory_node is not EntryKind.DIRECTORY:
            raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, directory)
        allowed = registered[sub]
        # 先收敛崩溃遗留的登记文件 pending/quarantine：发布/消费后才做
        # 未知成员检查，否则 .pending/.quarantine 会作为未登记成员被拒绝且锁永久保留。
        for name, digest in sorted(allowed.items()):
            recover_digest_pending(
                journal.directory,
                Path(sub) / name,
                digest,
                code=CheckCode.OPERATION_INCONSISTENT_STATE.value,
                code_path=journal.directory / sub / name,
            )
            recover_quarantine(
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
                # 隔离→验证→删除：验证与删除同一锚定句柄。
                leaf.quarantine_delete(registered[sub][relative.name])
        directory = journal.directory / sub
        if (
            classify_node_or_error(
                directory,
                code=CheckCode.OPERATION_INCONSISTENT_STATE.value,
                code_path=directory,
            )
            is EntryKind.DIRECTORY
        ):
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
    settle_empty_directories: bool = True,
    warnings: tuple[ResultWarning, ...] = (),
) -> OperationResult:
    """Mark a terminal phase; by default clean recovery copies then release the lock.

    ``retain_terminal_lock=True`` 仅耐久写入终态并移除空目录，不释放写锁、不清理恢复副本，
    供恢复包装层在证明终态后统一解关联、清理与释放。``changed_paths`` 由调用方从
    公开效果派生；缺省回退到日志 mutation 路径（恢复等无公开效果边界）。

    顺序为终态日志耐久 → 安全清理已授权恢复材料 → 释放锁：清理遇到未知成员/类型
    错误时保留锁与现场，CLI 报错后 recover 仍能凭锁定位，不会出现"报错时根内已无锁"
    。

    通用业务完成路径（``_validate_and_complete``）在进入索引阶段前已由
    ``settle_planned_empty_directories`` 收敛计划内空目录，因此传入
    ``settle_empty_directories=False`` 避免索引发布后再执行目录清理（spec §6）；回滚
    路径保留默认清理。``warnings`` 是索引阶段产生的稳定公开 warning，随成功结果返回。
    """
    terminal = _with_phase(journal, phase)
    write_journal(terminal)
    if not retain_terminal_lock:
        _clean_recovery_copies(journal)
        release_lock(root, journal.operation_id)
    if settle_empty_directories:
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
        warnings=warnings,
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
    # 首次回滚 mutation 前耐久登记方向意图：正向与回滚共用 {目标}.pending/.quarantine
    # 后缀，恢复器必须凭 phase 区分授权摘要。普通操作不再允许
    # prepared/applying/validating 直接写 rolled_back。
    rolling = _with_phase(journal, OperationPhase.ROLLING_BACK)
    write_journal(rolling)
    for mutation in reversed(journal.mutations):
        current = sha256_file(root / mutation.path)
        if current == mutation.before_sha256:
            continue
        if mutation.before_sha256 is None:
            # 回滚新建 WRITE：目标当前为 after 精确节点，节点事务隔离验证后消费。
            _apply_delete(
                root,
                mutation.path,
                expected_digest=cast("str", mutation.after_sha256),
            )
        else:
            original = journal.directory / cast("Path", mutation.original)
            original_hash = sha256_file(original)
            if original_hash != mutation.before_sha256:
                raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, original)
            # 回滚改写/DELETE：目标当前为 after（或缺失）授权状态，恢复 original
            # 的 before 内容（摘要绑定受管写）。
            _apply_write(
                root,
                mutation.path,
                original.read_bytes(),
                expected_current=mutation.after_sha256,
                code_path=root / mutation.path,
            )
    return finalize_operation(
        root,
        rolling,
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
        _apply_write(
            root,
            mutation.path,
            content,
            expected_current=mutation.before_sha256,
            code_path=root / mutation.path,
        )
    else:
        _apply_delete(root, mutation.path, expected_digest=cast("str", mutation.before_sha256))
    updated = _with_applied(journal, mutation.path)
    write_journal(updated)
    return updated


def operation_changes_link_index(journal: Journal) -> bool:
    """按 mutation/目录事实判定本 operation 是否需要链接索引阶段（spec §6）。

    凡改变以下任一事实都进入索引阶段，不按命令白名单：
    - ``.draft/`` 之外的普通 Markdown 内容或路径；
    - 索引覆盖目录的成员名称或节点类型（新建/删除/改名，或计划内空目录清理）。

    重写既有非 Markdown 普通文件正文不改变索引（spec §8）；``.draft/`` 内部 marker
    变化（如 adopt）不进入索引阶段。未来 kind 只要产生相同事实，就自动受同一规则约束。
    """
    for mutation in journal.mutations:
        if mutation.path.parts and mutation.path.parts[0] == INTERNAL_DIR:
            continue  # `.draft/` 内部状态变化不进入索引阶段（spec §5 adopt）。
        if mutation.path.suffix == ".md":
            return True
        if mutation.kind is MutationKind.DELETE:
            return True  # 删除任何 `.draft/` 外节点改变目录成员事实。
        if mutation.before_sha256 is None:
            return True  # 新建 `.draft/` 外节点改变目录成员事实。
        # 重写既有非 Markdown 普通文件正文：不改变索引（spec §8）。
    # 计划内空目录清理改变父目录成员事实。
    return bool(journal.remove_empty_directories)


def settle_planned_empty_directories(root: Path, journal: Journal) -> Journal:
    """在验证完成后、进入链接索引阶段前按深到浅收敛计划内空目录（spec §6）。

    只处理 ``journal.remove_empty_directories`` 精确路径，非递归 ``rmdir``。缺失视为
    已经收敛；成功同步父目录。成员/符号链接/节点类型发生未解释变化返回
    ``operation.external_change`` 并保留锁。普通 I/O 原因导致目录不能删除时可以保留，
    索引按实际目录状态构建；实际状态无法读取时由后续严格扫描映射为
    ``rebuild_required``。
    """
    for relative in journal.remove_empty_directories:
        try:
            node = classify_node(root / relative)
        except OSError:
            # 实际状态不可读：保留目录并让索引读取实际状态；不可读时索引阶段
            # 会进入 rebuild_required（spec §6）。
            continue
        if node is EntryKind.MISSING:
            continue  # 缺失视为已经收敛。
        if node is not EntryKind.DIRECTORY:
            # 符号链接/普通文件/其他节点：未解释节点类型变化，保留锁。
            raise DraftError("operation.external_change", root / relative)
        try:
            (root / relative).rmdir()
        except FileNotFoundError:
            continue  # 分类与删除之间目录消失：缺失视为收敛。
        except OSError as exc:
            if exc.errno in (errno.ENOTEMPTY, errno.EEXIST):
                # 目录仍有未计划成员：未解释变化，保留锁。
                raise DraftError("operation.external_change", root / relative) from exc
            # 普通 I/O 失败：保留目录，索引按实际状态构建。
            continue
        fsync_directory((root / relative).parent)
    return journal


@dataclass(frozen=True)
class _OperationIndexSettlement:
    """通用业务 operation 索引阶段的返回：已写入索引结果的 Journal 与公开 warning。"""

    journal: Journal
    warnings: tuple[ResultWarning, ...]


def settle_link_index_for_operation(root: Path, journal: Journal) -> _OperationIndexSettlement:
    """在业务结果验证后、终态前收敛链接索引结果（spec §5/§10/§11）。

    在产生任何索引 staging、目标替换或 flag 副作用前先耐久写 ``link_index_updating``；
    ``link_index_updating`` 之后只允许前向收敛，不进入 ``rolling_back``。索引构建或发布
    失败只记录 ``rebuild_required``，不回滚已经验证的业务 mutation。严格证明根和索引
    事实没有变化时不产生 revision（spec §3）。
    """
    from draft import link_index_maintenance as maintenance

    authority = maintenance.read_link_index_authority(root)
    next_revision = maintenance.next_link_index_revision(authority)
    prepared = maintenance.prepare_link_index_update(root)
    if prepared.scan is None and not prepared.requires_rebuild:
        # 严格证明根和索引事实没有变化：不产生 revision、不写索引阶段（spec §3）。
        return _OperationIndexSettlement(journal=journal, warnings=())
    updating = with_link_index_state(
        with_phase(journal, OperationPhase.LINK_INDEX_UPDATING),
        revision=next_revision,
        result=None,
        sha256=None,
    )
    write_journal(updating)
    if prepared.requires_rebuild:
        return _finish_operation_rebuild_required(root, updating, next_revision)
    try:
        return _finish_operation_published(root, updating, prepared, next_revision)
    except DraftError as exc:
        if exc.code in (OPERATION_INDEX_UPDATE_FAILED, OPERATION_EXTERNAL_CHANGE):
            # 发布失败但可耐久记录：降级到 rebuild_required（spec §10/§11）。
            return _finish_operation_rebuild_required(root, updating, next_revision)
        raise


def _finish_operation_published(
    root: Path,
    updating: Journal,
    prepared: PreparedLinkIndexUpdate,
    next_revision: str,
) -> _OperationIndexSettlement:
    """staging 写入、原子发布并耐久写 ``link_index_published``（spec §10）。

    staging 位于当前业务 operation 目录；``link_index_updating`` 已由调用方耐久写入。
    成功后尽力删除 rebuild flag（普通 I/O 失败形成稳定 warning，不改变结果）。
    """
    from draft import link_index_maintenance as maintenance

    assert prepared.scan is not None
    index = maintenance.materialize_index(next_revision, prepared.scan.content)
    digest = maintenance.stage_link_index(updating, index)
    maintenance.publish_staged_link_index(root, updating, digest)
    published = with_link_index_state(
        with_phase(updating, OperationPhase.LINK_INDEX_PUBLISHED),
        revision=next_revision,
        result=LinkIndexResult.PUBLISHED,
        sha256=digest,
    )
    write_journal(published)
    warnings: list[ResultWarning] = []
    flag_warning = maintenance.remove_rebuild_flag(root)
    if flag_warning is not None:
        warnings.append(flag_warning)
    return _OperationIndexSettlement(journal=published, warnings=tuple(warnings))


def _finish_operation_rebuild_required(
    root: Path,
    updating: Journal,
    next_revision: str,
) -> _OperationIndexSettlement:
    """耐久记录 ``rebuild_required`` 并补建 flag（spec §10/§11）。

    ``published=False`` 语义由调用方消费：业务 operation 保留已经验证的结果，退出码
    仍为 0，只是没有发布索引制品。warning 按规格去重排序。
    """
    from draft import link_index_maintenance as maintenance

    rebuild = with_link_index_state(
        with_phase(updating, OperationPhase.LINK_INDEX_REBUILD_REQUIRED),
        revision=next_revision,
        result=LinkIndexResult.REBUILD_REQUIRED,
        sha256=None,
    )
    write_journal(rebuild)
    warnings: list[ResultWarning] = [ResultWarning(LINK_INDEX_REBUILD_REQUIRED)]
    flag_warning = maintenance.ensure_rebuild_flag(root)
    if flag_warning is not None:
        warnings.append(flag_warning)
    # 发布失败降级路径可能残留 staging：幂等清理当前 operation 的 staging。
    maintenance.cleanup_link_index_staging(updating)
    return _OperationIndexSettlement(journal=rebuild, warnings=tuple(warnings))


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
    # 空目录清理在验证完成后、进入链接索引阶段前收敛（spec §6）。
    current = settle_planned_empty_directories(root, current)
    warnings: tuple[ResultWarning, ...] = ()
    if operation_changes_link_index(current):
        settlement = settle_link_index_for_operation(root, current)
        current = settlement.journal
        warnings = settlement.warnings
    if current.kind is OperationKind.TRASH:
        # 回收以待恢复为耐久终态：写 trash_retained、释放写锁、清理恢复副本；
        # 权威删除包原样保留，到期推进与系统回收站移交由 trash_lifecycle 子系统驱动。
        return finalize_operation(
            root,
            current,
            OperationPhase.TRASH_RETAINED,
            current.kind,
            retain_terminal_lock=retain_terminal_lock,
            changed_paths=changed_paths,
            settle_empty_directories=False,
            warnings=warnings,
        )
    return finalize_operation(
        root,
        current,
        OperationPhase.COMPLETED,
        current.kind,
        retain_terminal_lock=retain_terminal_lock,
        changed_paths=changed_paths,
        settle_empty_directories=False,
        warnings=warnings,
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
        link_index_revision=None,
        link_index_result=None,
        link_index_sha256=None,
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
        # 并释放锁，不遗留锁或中段日志。
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
        link_index_revision=None,
        link_index_result=None,
        link_index_sha256=None,
    )
    # 先在文件创建前耐久登记全部 original/staged 路径与摘要：allocating
    # 阶段永远不会有未登记文件；崩溃 mid-prepare 留下 PREPARED 日志 + 部分缺失文件，
    # 恢复按登记清单容忍缺失并拒绝任何未登记内容。
    write_journal(prepared)
    for index, mutation in enumerate(prepared.mutations):
        if index in original_contents:
            durable_write(
                journal.directory, Path("originals") / f"{index:04d}.bin", original_contents[index]
            )
        if mutation.staged is not None:
            durable_write(
                journal.directory, Path("staged") / f"{index:04d}.bin", staged_contents[index]
            )

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
    """复核现存恢复材料摘要；登记但缺失（prepare 先登记后建文件）容忍。

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


def _recover_terminal(
    root: Path,
    journal: Journal,
    operation_id: str,
    *,
    retain_terminal_lock: bool = False,
) -> OperationResult:
    """合法崩溃窗口：终态日志已耐久但锁未释放。验证文件状态后清理并释放锁。"""
    expected = "after" if journal.phase is OperationPhase.COMPLETED else "before"
    for mutation in journal.mutations:
        if _classify(root, mutation) != expected:
            raise DraftError("operation.external_change", root / mutation.path)
    if not retain_terminal_lock:
        _clean_recovery_copies(journal)
        if journal.link_index_revision is None:
            # 携带索引结果的 completed 终态：计划内目录已在发布前收敛（spec §6），
            # 残留即已发布索引事实，不得再删；无索引字段的终态保持旧清理行为。
            for relative in journal.remove_empty_directories:
                with suppress(OSError):
                    (root / relative).rmdir()
        if journal.kind is OperationKind.LINK_INDEX_UPDATE:
            # maintenance completed：幂等清理锁和已知 staging（spec §12）。
            from draft import link_index_maintenance as maintenance

            maintenance.cleanup_link_index_staging(journal)
        release_lock(root, operation_id)
    return OperationResult(
        operation_id=operation_id,
        kind=journal.kind.value,
        phase=journal.phase,
        changed_paths=_recover_changed_paths(journal),
    )


def recover_link_index_phase(
    root: Path,
    journal: Journal,
    *,
    retain_terminal_lock: bool = False,
) -> OperationResult:
    """按 spec §12 前向收敛三个链接索引 phase（公开恢复入口）。

    - ``link_index_updating``：不读取目标外观猜测发布成功，耐久记录同 revision 的
      ``rebuild_required``，尽力补建 flag，清理 staging/.pending 后进入原 kind 终态；
    - ``link_index_published``：验证 journal 绑定制品（目标 SHA-256/revision 与 journal
      一致）后才补终态，不一致停止保留现场；
    - ``link_index_rebuild_required``：尽力补建 flag，清理 staging/.pending 后补终态。

    trash 的原有成功终态为 ``trash_retained``，其余（含 restore 与 maintenance
    ``link-index.update``）为 ``completed``。``link_index_updating`` 之后不再允许业务
    rollback。``retain_terminal_lock=True`` 只耐久写终态，锁与恢复材料由调用方
    （trash/restore 双 journal 协调层）统一解关联、清理与释放。恢复不产生独立
    revision：原样保留 journal 已分配的 revision。
    """
    from draft import link_index_maintenance as maintenance

    terminal_phase = (
        OperationPhase.TRASH_RETAINED
        if journal.kind is OperationKind.TRASH
        else OperationPhase.COMPLETED
    )
    changed_paths = _recover_changed_paths(journal)
    if journal.phase is OperationPhase.LINK_INDEX_UPDATING:
        # 发布结果未知：不猜测已发布、不重复增量发布（spec §12），耐久记录同 revision
        # 的 rebuild_required，再尽力补建 flag 并清理 staging/.pending。
        rebuild = with_link_index_state(
            with_phase(journal, OperationPhase.LINK_INDEX_REBUILD_REQUIRED),
            revision=journal.link_index_revision,
            result=LinkIndexResult.REBUILD_REQUIRED,
            sha256=None,
        )
        write_journal(rebuild)
        maintenance.ensure_rebuild_flag(root)
        maintenance.cleanup_link_index_staging(rebuild)
        return finalize_operation(
            root,
            rebuild,
            terminal_phase,
            journal.kind,
            retain_terminal_lock=retain_terminal_lock,
            settle_empty_directories=False,
            changed_paths=changed_paths,
        )
    if journal.phase is OperationPhase.LINK_INDEX_PUBLISHED:
        maintenance.verify_published_link_index(root, journal)
        maintenance.cleanup_link_index_staging(journal)
        return finalize_operation(
            root,
            journal,
            terminal_phase,
            journal.kind,
            retain_terminal_lock=retain_terminal_lock,
            settle_empty_directories=False,
            changed_paths=changed_paths,
        )
    # LINK_INDEX_REBUILD_REQUIRED：尽力补建 flag，然后补原有成功终态。
    maintenance.ensure_rebuild_flag(root)
    maintenance.cleanup_link_index_staging(journal)
    return finalize_operation(
        root,
        journal,
        terminal_phase,
        journal.kind,
        retain_terminal_lock=retain_terminal_lock,
        settle_empty_directories=False,
        changed_paths=changed_paths,
    )


def _allocating_has_index_artifacts(journal: Journal) -> bool:
    """allocating 阶段不允许存在任何索引副作用（spec §12 恢复）。

    索引 staging/.pending 只在 ``link_index_updating`` 耐久后才写入；allocating 下
    出现任何该协议路径节点都属内部不一致，恢复必须保留现场而非静默清理。
    """
    from draft import link_index_maintenance as maintenance

    for name in (
        maintenance.LINK_INDEX_STAGED_FILE_NAME,
        _pending_name(maintenance.LINK_INDEX_STAGED_FILE_NAME),
    ):
        node = classify_node_or_error(
            journal.directory / name,
            code=CheckCode.OPERATION_INCONSISTENT_STATE.value,
            code_path=journal.directory / name,
        )
        if node is not EntryKind.MISSING:
            return True
    return False


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
    """WRITE 恢复决策表：输入 target/登记精确临时路径的探针事实，输出动作。

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
    # 供合成"成员可为空"的明确观察。
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

    # 每个 WRITE 按 target/登记 temp 状态表一次性分类：只有
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
                member_types = tuple(
                    member
                    for member in observed.member_types
                    if (path / member[0]) not in consume_temps
                )
                return Observation(
                    path=path,
                    kind=EntryKind.DIRECTORY,
                    members=members,
                    member_types=member_types,
                )
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


def _planned_created_directory_closure(
    observations: ObservationManifest, mutations: tuple[JournalMutation, ...]
) -> frozenset[Path]:
    """推导本操作获准创建的目录闭包：目标祖先中计划时缺失的目录（纯函数）。

    只包含 plan-time 可证明缺失的祖先；缺失祖先链必须整条由 mutation 目标覆盖，
    不与"存在但未观察的共享目录"混淆。``artifact_plan_time_absent`` 依赖它判定
    缺失祖先下的协议节点所有权。
    """
    is_missing = _plan_time_missing_index(observations)
    created: set[Path] = set()
    for mutation in mutations:
        cursor = mutation.path.parent
        while cursor.parts:
            if not is_missing(cursor):
                break
            created.add(cursor)
            parent = cursor.parent
            if parent == cursor:
                break
            cursor = parent
    return frozenset(created)


def artifact_plan_time_absent(
    observations: ObservationManifest,
    artifact: Path,
    *,
    planned_created_directories: frozenset[Path],
) -> bool:
    """协议节点（pending/quarantine/temp）是否由本操作创建。

    确定性名字与匹配摘要不是所有权证据。只有 plan-time 精确缺失才可拥有：精确路径
    可证明缺失，且其祖先链要么是已观察 DIRECTORY（受管目录内创建），要么整条缺失
    祖先链都由本操作计划创建闭包覆盖。没有观察、父为 FILE/SYMLINK/OTHER、或缺失
    祖先不在创建闭包内的一律返回 False——"路径计划时不可能存在"与"本操作获准在这里
    创建协议节点"不是同一事实。
    """
    is_missing = _plan_time_missing_index(observations)
    if not is_missing(artifact):
        return False
    by_path = {entry.path: entry for entry in observations.entries}
    cursor = artifact.parent
    while cursor.parts:
        entry = by_path.get(cursor)
        if entry is not None and entry.kind is not EntryKind.MISSING:
            # 到达已观察非缺失祖先：只有明确 DIRECTORY 才允许子协议节点。
            return entry.kind is EntryKind.DIRECTORY
        if entry is None and not is_missing(cursor):
            break  # 无存在性证据的祖先：保守停止，缺失链已全部验证。
        if cursor not in planned_created_directories:
            return False  # 缺失祖先不在本操作创建闭包内。
        parent = cursor.parent
        if parent == cursor:
            break
        cursor = parent
    # 缺失链整条都在本操作创建闭包内：本操作创建了父目录，协议节点归它所有。
    return True


def mutation_artifact_actions(
    *,
    direction: MutationDirection,
    role: MutationRole,
    target: ArtifactState,
    pending: ArtifactState,
    quarantine: ArtifactState,
    pending_owned: bool,
    quarantine_owned: bool,
) -> tuple[MutationRecoveryAction, ...]:
    """方向感知的 mutation artifact 恢复决策（纯函数）。

    输入 target/pending/quarantine 的类型与摘要分类、pending/quarantine 的所有权布尔。
    输出有序动作序列；表外组合一律 ``EXTERNAL``（保留现场）。任何存在的 artifact
    若未被证明由本操作创建（``pending_owned``/``quarantine_owned`` 为 False）也报
    外部变化，绝不消费或发布计划前既有的同名文件。禁止在 I/O helper 中再按
    ``before is None`` 等启发式猜方向。
    """
    if pending is not ArtifactState.MISSING and not pending_owned:
        return (MutationRecoveryAction.EXTERNAL,)
    if quarantine is not ArtifactState.MISSING and not quarantine_owned:
        return (MutationRecoveryAction.EXTERNAL,)
    if direction is MutationDirection.FORWARD:
        return _forward_artifact_actions(role, target, pending, quarantine)
    return _rollback_artifact_actions(role, target, pending, quarantine)


def _forward_artifact_actions(
    role: MutationRole,
    target: ArtifactState,
    pending: ArtifactState,
    quarantine: ArtifactState,
) -> tuple[MutationRecoveryAction, ...]:
    if role is MutationRole.NEW_WRITE:
        if quarantine is not ArtifactState.MISSING:
            return (MutationRecoveryAction.EXTERNAL,)
        if target is ArtifactState.MISSING and pending is ArtifactState.MISSING:
            return (MutationRecoveryAction.RETRY_APPLY,)
        if target is ArtifactState.MISSING and pending is ArtifactState.AFTER:
            return (MutationRecoveryAction.PUBLISH_PENDING, MutationRecoveryAction.DONE)
        if target is ArtifactState.AFTER and pending is ArtifactState.MISSING:
            return (MutationRecoveryAction.DONE,)
        if target is ArtifactState.AFTER and pending is ArtifactState.AFTER:
            return (MutationRecoveryAction.CONSUME_PENDING, MutationRecoveryAction.DONE)
        return (MutationRecoveryAction.EXTERNAL,)
    if role is MutationRole.DELETE:
        if pending is not ArtifactState.MISSING:
            return (MutationRecoveryAction.EXTERNAL,)
        if target is ArtifactState.BEFORE and quarantine is ArtifactState.MISSING:
            return (MutationRecoveryAction.RETRY_APPLY,)
        if target is ArtifactState.BEFORE and quarantine is ArtifactState.BEFORE:
            return (MutationRecoveryAction.CONSUME_QUARANTINE, MutationRecoveryAction.RETRY_APPLY)
        if target is ArtifactState.MISSING and quarantine is ArtifactState.BEFORE:
            return (MutationRecoveryAction.CONSUME_QUARANTINE, MutationRecoveryAction.DONE)
        if target is ArtifactState.MISSING and quarantine is ArtifactState.MISSING:
            return (MutationRecoveryAction.DONE,)
        return (MutationRecoveryAction.EXTERNAL,)
    # REWRITE_WRITE 正向。
    if (
        target is ArtifactState.BEFORE
        and quarantine is ArtifactState.MISSING
        and pending is ArtifactState.MISSING
    ):
        return (MutationRecoveryAction.RETRY_APPLY,)
    if (
        target is ArtifactState.BEFORE
        and quarantine is ArtifactState.BEFORE
        and pending is ArtifactState.MISSING
    ):
        return (MutationRecoveryAction.CONSUME_QUARANTINE, MutationRecoveryAction.RETRY_APPLY)
    if (
        target is ArtifactState.MISSING
        and quarantine is ArtifactState.BEFORE
        and pending is ArtifactState.MISSING
    ):
        return (
            MutationRecoveryAction.PUBLISH_STAGED,
            MutationRecoveryAction.CONSUME_QUARANTINE,
            MutationRecoveryAction.DONE,
        )
    if (
        target is ArtifactState.MISSING
        and quarantine is ArtifactState.BEFORE
        and pending is ArtifactState.AFTER
    ):
        return (
            MutationRecoveryAction.PUBLISH_PENDING,
            MutationRecoveryAction.CONSUME_QUARANTINE,
            MutationRecoveryAction.DONE,
        )
    if (
        target is ArtifactState.AFTER
        and quarantine is ArtifactState.BEFORE
        and pending is ArtifactState.MISSING
    ):
        return (MutationRecoveryAction.CONSUME_QUARANTINE, MutationRecoveryAction.DONE)
    if (
        target is ArtifactState.AFTER
        and quarantine is ArtifactState.BEFORE
        and pending is ArtifactState.AFTER
    ):
        return (
            MutationRecoveryAction.CONSUME_PENDING,
            MutationRecoveryAction.CONSUME_QUARANTINE,
            MutationRecoveryAction.DONE,
        )
    if (
        target is ArtifactState.AFTER
        and quarantine is ArtifactState.MISSING
        and pending is ArtifactState.MISSING
    ):
        return (MutationRecoveryAction.DONE,)
    if (
        target is ArtifactState.AFTER
        and quarantine is ArtifactState.MISSING
        and pending is ArtifactState.AFTER
    ):
        return (MutationRecoveryAction.CONSUME_PENDING, MutationRecoveryAction.DONE)
    return (MutationRecoveryAction.EXTERNAL,)


def _rollback_artifact_actions(
    role: MutationRole,
    target: ArtifactState,
    pending: ArtifactState,
    quarantine: ArtifactState,
) -> tuple[MutationRecoveryAction, ...]:
    if role is MutationRole.NEW_WRITE:
        if pending is not ArtifactState.MISSING:
            return (MutationRecoveryAction.EXTERNAL,)
        if target is ArtifactState.AFTER and quarantine is ArtifactState.MISSING:
            return (MutationRecoveryAction.RETRY_APPLY,)
        if target is ArtifactState.AFTER and quarantine is ArtifactState.AFTER:
            return (MutationRecoveryAction.CONSUME_QUARANTINE, MutationRecoveryAction.RETRY_APPLY)
        if target is ArtifactState.MISSING and quarantine is ArtifactState.AFTER:
            return (MutationRecoveryAction.CONSUME_QUARANTINE, MutationRecoveryAction.DONE)
        if target is ArtifactState.MISSING and quarantine is ArtifactState.MISSING:
            return (MutationRecoveryAction.DONE,)
        return (MutationRecoveryAction.EXTERNAL,)
    if role is MutationRole.DELETE:
        if quarantine is not ArtifactState.MISSING:
            return (MutationRecoveryAction.EXTERNAL,)
        if target is ArtifactState.MISSING and pending is ArtifactState.MISSING:
            return (MutationRecoveryAction.RETRY_APPLY,)
        if target is ArtifactState.MISSING and pending is ArtifactState.BEFORE:
            return (MutationRecoveryAction.PUBLISH_PENDING, MutationRecoveryAction.DONE)
        if target is ArtifactState.BEFORE and pending is ArtifactState.MISSING:
            return (MutationRecoveryAction.DONE,)
        if target is ArtifactState.BEFORE and pending is ArtifactState.BEFORE:
            return (MutationRecoveryAction.CONSUME_PENDING, MutationRecoveryAction.DONE)
        return (MutationRecoveryAction.EXTERNAL,)
    # REWRITE_WRITE 回滚：把正向表中的 BEFORE/AFTER 互换，quarantine 保存 AFTER、
    # pending 保存 BEFORE。
    if (
        target is ArtifactState.AFTER
        and quarantine is ArtifactState.MISSING
        and pending is ArtifactState.MISSING
    ):
        return (MutationRecoveryAction.RETRY_APPLY,)
    if (
        target is ArtifactState.AFTER
        and quarantine is ArtifactState.AFTER
        and pending is ArtifactState.MISSING
    ):
        return (MutationRecoveryAction.CONSUME_QUARANTINE, MutationRecoveryAction.RETRY_APPLY)
    if (
        target is ArtifactState.MISSING
        and quarantine is ArtifactState.AFTER
        and pending is ArtifactState.MISSING
    ):
        return (
            MutationRecoveryAction.PUBLISH_ORIGINAL,
            MutationRecoveryAction.CONSUME_QUARANTINE,
            MutationRecoveryAction.DONE,
        )
    if (
        target is ArtifactState.MISSING
        and quarantine is ArtifactState.AFTER
        and pending is ArtifactState.BEFORE
    ):
        return (
            MutationRecoveryAction.PUBLISH_PENDING,
            MutationRecoveryAction.CONSUME_QUARANTINE,
            MutationRecoveryAction.DONE,
        )
    if (
        target is ArtifactState.BEFORE
        and quarantine is ArtifactState.AFTER
        and pending is ArtifactState.MISSING
    ):
        return (MutationRecoveryAction.CONSUME_QUARANTINE, MutationRecoveryAction.DONE)
    if (
        target is ArtifactState.BEFORE
        and quarantine is ArtifactState.AFTER
        and pending is ArtifactState.BEFORE
    ):
        return (
            MutationRecoveryAction.CONSUME_PENDING,
            MutationRecoveryAction.CONSUME_QUARANTINE,
            MutationRecoveryAction.DONE,
        )
    if (
        target is ArtifactState.BEFORE
        and quarantine is ArtifactState.MISSING
        and pending is ArtifactState.MISSING
    ):
        return (MutationRecoveryAction.DONE,)
    return (MutationRecoveryAction.EXTERNAL,)


def _recover_managed_temp_quarantines(root: Path, journal: Journal) -> None:
    """恢复前把崩溃遗留的 managed temp quarantine 收敛。

    在观察复核之前运行：被中断的 consume 删除留下的 ``{temp}.quarantine`` 会被观察
    复核当成无法解释的新文件；先按授权摘要精确分类（精确→删除完成，不符→恢复原名
    并报错），让观察复核在干净树上前进。
    """
    planned_dirs = _planned_created_directory_closure(journal.observations, journal.mutations)
    for index, mutation in enumerate(journal.mutations):
        if mutation.kind is not MutationKind.WRITE or mutation.after_sha256 is None:
            continue
        relative = _mutation_temporary_path(mutation.path, journal.operation_id, index)
        quarantine_path = relative.with_name(_quarantine_name(relative.name))
        if (
            classify_node_or_error(
                root / quarantine_path,
                code="operation.external_change",
                code_path=root / quarantine_path,
            )
            is EntryKind.MISSING
        ):
            continue
        # 所有权证明：managed temp 路径也必须计划时缺失才可消费。
        if not artifact_plan_time_absent(
            journal.observations,
            quarantine_path,
            planned_created_directories=planned_dirs,
        ):
            raise DraftError("operation.external_change", root / quarantine_path)
        recover_quarantine(
            root,
            relative,
            mutation.after_sha256,
            code="operation.external_change",
            code_path=root / relative,
        )


def _artifact_state_of(
    kind: EntryKind, digest: str | None, *, before: str | None, after: str | None
) -> ArtifactState:
    """节点类型+摘要分类为 ArtifactState（纯函数）。"""
    if kind is EntryKind.MISSING:
        return ArtifactState.MISSING
    if kind is not EntryKind.FILE:
        return ArtifactState.NON_FILE
    if after is not None and digest == after:
        return ArtifactState.AFTER
    if before is not None and digest == before:
        return ArtifactState.BEFORE
    return ArtifactState.WRONG


def _consume_named_artifact(
    root: Path, relative: Path, *, name: str, expected_digest: str, code_path: Path
) -> None:
    """inode+内容复核后消费（删除）崩溃遗留 artifact 名。

    只删除我们验证过的同一节点；缺失幂等容忍，类型/摘要不符保留现场报外部变化。
    """
    with anchored_leaf(
        root, relative, code="operation.external_change", code_path=code_path
    ) as target:
        if isinstance(target, _MissingLeaf):
            return
        artifact = _AnchoredLeaf(
            target.parent_fd, name, relative, "operation.external_change", code_path
        )
        verified = _name_node(
            target.parent_fd,
            artifact.name,
            code="operation.external_change",
            code_path=code_path,
        )
        if verified is None:
            return  # ENOENT：名字确实不存在，幂等（只允许缺失证明幂等）。
        kind = artifact.classify()
        if kind is not EntryKind.FILE or sha256_bytes(artifact.read()) != expected_digest:
            raise DraftError("operation.external_change", code_path)
        if not _same_name_node(
            target.parent_fd,
            artifact.name,
            verified,
            code="operation.external_change",
            code_path=code_path,
        ):
            raise DraftError("operation.external_change", code_path)
        try:
            os.unlink(artifact.name, dir_fd=target.parent_fd)
        except OSError as exc:
            raise DraftError(CheckCode.OPERATION_DURABILITY_FAILED.value, code_path) from exc
        _fsync_dir_fd(target.parent_fd, code_path)


def _publish_pending_to_target(
    root: Path, relative: Path, *, expected_digest: str, code_path: Path
) -> None:
    """把授权 pending（内容摘要=expected）create-only 发布为目标并消费 pending。

    目标必须缺失（no-clobber link）：发布瞬间目标已出现即外部变化。发布后复核内容
    仍是授权内容且 pending 未被替换，再删除 pending。pending 与目标在同一锚定句柄内。
    """
    with anchored_leaf(
        root, relative, code="operation.external_change", code_path=code_path
    ) as target:
        if isinstance(target, _MissingLeaf):
            raise DraftError("operation.external_change", code_path)
        if target.classify() is not EntryKind.MISSING:
            raise DraftError("operation.external_change", code_path)
        pending = _AnchoredLeaf(
            target.parent_fd,
            _pending_name(target.name),
            relative,
            "operation.external_change",
            code_path,
        )
        if pending.classify() is not EntryKind.FILE:
            raise DraftError("operation.external_change", code_path)
        content = pending.read()
        if sha256_bytes(content) != expected_digest:
            raise DraftError("operation.external_change", code_path)
        try:
            os.link(
                pending.name,
                target.name,
                src_dir_fd=target.parent_fd,
                dst_dir_fd=target.parent_fd,
            )
        except FileExistsError as exc:
            raise DraftError("operation.external_change", code_path) from exc
        except OSError as exc:
            raise DraftError(CheckCode.OPERATION_DURABILITY_FAILED.value, code_path) from exc
        _fsync_dir_fd(target.parent_fd, code_path)
        if sha256_bytes(
            _read_name_bytes(target.parent_fd, target.name, code_path=code_path)
        ) != expected_digest or not _same_name_node(
            target.parent_fd,
            pending.name,
            _name_node(
                target.parent_fd, target.name, code="operation.external_change", code_path=code_path
            ),
            code="operation.external_change",
            code_path=code_path,
        ):
            raise DraftError("operation.external_change", code_path)
        try:
            os.unlink(pending.name, dir_fd=target.parent_fd)
        except OSError as exc:
            raise DraftError(CheckCode.OPERATION_DURABILITY_FAILED.value, code_path) from exc
        _fsync_dir_fd(target.parent_fd, code_path)


def _publish_recovery_content_to_target(
    root: Path,
    mutation: JournalMutation,
    *,
    content: bytes,
    expected_digest: str,
    code_path: Path,
) -> None:
    """create-only 把恢复内容（staged=after / original=before）发布为目标。"""
    with anchored_leaf(
        root, mutation.path, code="operation.external_change", code_path=code_path
    ) as target:
        if isinstance(target, _MissingLeaf):
            raise DraftError("operation.external_change", code_path)
        if target.classify() is not EntryKind.MISSING:
            raise DraftError("operation.external_change", code_path)
        if sha256_bytes(content) != expected_digest:
            raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, code_path)
        _create_only_publish(target.parent_fd, target.name, content, code_path=code_path)


def _execute_mutation_artifact_actions(
    root: Path,
    journal: Journal,
    mutation: JournalMutation,
    relative: Path,
    *,
    direction: MutationDirection,
    actions: tuple[MutationRecoveryAction, ...],
    code_path: Path,
) -> None:
    """按纯决策表动作执行 artifact 收敛（I/O 层）。

    摘要来源固定：pending=after（正向）/before（回滚）、staged=after、original=before。
    不在此处按 before/after 是否为空猜方向。
    """
    for action in actions:
        if action is MutationRecoveryAction.RETRY_APPLY:
            continue  # 无 artifact 收敛，交给阶段流继续应用。
        if action is MutationRecoveryAction.DONE:
            continue  # 目标已是正确状态。
        if action is MutationRecoveryAction.EXTERNAL:
            raise DraftError("operation.external_change", code_path)
        if action in (
            MutationRecoveryAction.PUBLISH_PENDING,
            MutationRecoveryAction.CONSUME_PENDING,
        ):
            expected = (
                mutation.after_sha256
                if direction is MutationDirection.FORWARD
                else mutation.before_sha256
            )
            if expected is None:
                raise DraftError("operation.external_change", code_path)
            if action is MutationRecoveryAction.PUBLISH_PENDING:
                _publish_pending_to_target(
                    root, relative, expected_digest=expected, code_path=code_path
                )
            else:
                _consume_named_artifact(
                    root,
                    relative,
                    name=_pending_name(relative.name),
                    expected_digest=expected,
                    code_path=code_path,
                )
        elif action is MutationRecoveryAction.CONSUME_QUARANTINE:
            expected = (
                mutation.before_sha256
                if direction is MutationDirection.FORWARD
                else mutation.after_sha256
            )
            if expected is None:
                raise DraftError("operation.external_change", code_path)
            _consume_named_artifact(
                root,
                relative,
                name=_quarantine_name(relative.name),
                expected_digest=expected,
                code_path=code_path,
            )
        elif action is MutationRecoveryAction.PUBLISH_STAGED:
            staged = journal.directory / cast("Path", mutation.staged)
            try:
                content = staged.read_bytes()
            except OSError as exc:
                raise DraftError("operation.external_change", code_path) from exc
            _publish_recovery_content_to_target(
                root,
                mutation,
                content=content,
                expected_digest=cast("str", mutation.after_sha256),
                code_path=code_path,
            )
        elif action is MutationRecoveryAction.PUBLISH_ORIGINAL:
            original = journal.directory / cast("Path", mutation.original)
            try:
                content = original.read_bytes()
            except OSError as exc:
                raise DraftError("operation.external_change", code_path) from exc
            _publish_recovery_content_to_target(
                root,
                mutation,
                content=content,
                expected_digest=cast("str", mutation.before_sha256),
                code_path=code_path,
            )


def _recover_mutation_artifacts(
    root: Path, journal: Journal, *, direction: MutationDirection
) -> None:
    """方向感知收敛受管写/删应用的崩溃遗留 pending/quarantine。

    正向与回滚共用 ``{目标}.pending/.quarantine`` 后缀，但代表不同授权摘要：正向
    quarantine 保存 before、pending 保存 after；回滚 quarantine 保存 after、pending
    保存 before。方向由 journal phase（ROLLING_BACK=回滚）决定，纯决策表
    ``mutation_artifact_actions`` 输出按序动作，I/O 层只执行。任何存在的 artifact 必须
    由计划时缺失证明（``artifact_plan_time_absent``）才可消费或发布：计划前既有节点
    即使字节恰好相同也不移动、不删除、不覆盖。
    """
    planned_dirs = _planned_created_directory_closure(journal.observations, journal.mutations)
    for mutation in journal.mutations:
        relative = mutation.path
        code_path = root / relative
        pending_path = relative.with_name(_pending_name(relative.name))
        quarantine_path = relative.with_name(_quarantine_name(relative.name))
        with anchored_leaf(
            root, relative, code="operation.external_change", code_path=code_path
        ) as target:
            if isinstance(target, _MissingLeaf):
                continue  # 父目录缺失：无 artifact 可收敛，交给阶段流重建目录链。
            pending = _AnchoredLeaf(
                target.parent_fd,
                _pending_name(target.name),
                relative,
                "operation.external_change",
                code_path,
            )
            quarantine = _AnchoredLeaf(
                target.parent_fd,
                _quarantine_name(target.name),
                relative,
                "operation.external_change",
                code_path,
            )
            tk = target.classify()
            pk = pending.classify()
            qk = quarantine.classify()
            td = sha256_bytes(target.read()) if tk is EntryKind.FILE else None
            pd = sha256_bytes(pending.read()) if pk is EntryKind.FILE else None
            qd = sha256_bytes(quarantine.read()) if qk is EntryKind.FILE else None
        actions = mutation_artifact_actions(
            direction=direction,
            role=_mutation_role(mutation),
            target=_artifact_state_of(
                tk, td, before=mutation.before_sha256, after=mutation.after_sha256
            ),
            pending=_artifact_state_of(
                pk, pd, before=mutation.before_sha256, after=mutation.after_sha256
            ),
            quarantine=_artifact_state_of(
                qk, qd, before=mutation.before_sha256, after=mutation.after_sha256
            ),
            pending_owned=artifact_plan_time_absent(
                journal.observations,
                pending_path,
                planned_created_directories=planned_dirs,
            ),
            quarantine_owned=artifact_plan_time_absent(
                journal.observations,
                quarantine_path,
                planned_created_directories=planned_dirs,
            ),
        )
        _execute_mutation_artifact_actions(
            root,
            journal,
            mutation,
            relative,
            direction=direction,
            actions=actions,
            code_path=code_path,
        )


def _clean_owned_temp_artifacts(
    root: Path, journal: Journal, consume_temps: frozenset[Path]
) -> None:
    """清理决策表允许消费的崩溃遗留临时文件（精确相对路径 + 摘要复核）。

    在观察复核通过后调用：前向完成会重新创建目标并替换，旧临时文件是孤儿。只处理
    ``_write_recovery_decision`` 判为 consume 的精确临时路径；临时文件必须与对应
    mutation 的 after 摘要一致，类型不符或摘要不符（决策与清理之间被外部改写）即
    外部篡改，停止并保留现场。不扫描父目录猜测候选。
    """
    planned_dirs = _planned_created_directory_closure(journal.observations, journal.mutations)
    for index, mutation in enumerate(journal.mutations):
        if mutation.kind is not MutationKind.WRITE or mutation.after_sha256 is None:
            continue
        relative = _mutation_temporary_path(mutation.path, journal.operation_id, index)
        if relative not in consume_temps:
            continue
        temporary = root / relative
        # 所有权证明：managed temp 路径必须计划时缺失才可消费。
        if not artifact_plan_time_absent(
            journal.observations,
            relative,
            planned_created_directories=planned_dirs,
        ):
            raise DraftError("operation.external_change", temporary)
        # 锚定隔离→验证→删除：不再用普通 Path.unlink 旁路。
        with anchored_leaf(
            root,
            relative,
            code="operation.external_change",
            code_path=temporary,
        ) as leaf:
            if leaf.classify() is not EntryKind.FILE:
                raise DraftError("operation.external_change", temporary)
            if sha256_bytes(leaf.read()) != mutation.after_sha256:
                raise DraftError("operation.external_change", temporary)
            leaf.quarantine_delete(mutation.after_sha256)


def _recover_locked(
    root: Path,
    operation_id: str,
    *,
    retain_terminal_lock: bool = False,
) -> OperationResult:
    # 先把崩溃遗留的 journal pending 发布为最新日志，再从最新日志恢复。
    resolve_journal_pending(operation_journal_path(root, operation_id))
    journal = read_journal(operation_journal_path(root, operation_id))
    if journal.operation_id != operation_id:
        raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, journal.directory)
    if journal.kind is OperationKind.ADOPT:
        return _recover_adopt(root, journal, operation_id)
    if journal.phase in (OperationPhase.COMPLETED, OperationPhase.ROLLED_BACK):
        return _recover_terminal(
            root, journal, operation_id, retain_terminal_lock=retain_terminal_lock
        )
    if journal.phase is OperationPhase.ALLOCATING:
        # allocating 不应有任何索引副作用（spec §12）：staging/.pending 出现即内部
        # 不一致，保留现场，不得静默放弃。
        if _allocating_has_index_artifacts(journal):
            raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, journal.directory)
        # 完整计划尚未耐久形成：不得前向完成，只能放弃、清理分配材料并释放锁。
        # retain_terminal_lock=True 时只耐久终态，材料与锁交由恢复包装层统一处理。
        abandoned = _with_phase(journal, OperationPhase.ROLLED_BACK)
        write_journal(abandoned)
        if not retain_terminal_lock:
            _clean_recovery_copies(journal)
            release_lock(root, operation_id)
        return OperationResult(
            operation_id=operation_id,
            kind=journal.kind.value,
            phase=OperationPhase.ROLLED_BACK,
            changed_paths=(),
        )
    if journal.phase in _LINK_INDEX_PHASES:
        # 三个链接索引 phase 前向收敛（spec §12）：不进入通用 mutation 恢复。
        return recover_link_index_phase(root, journal, retain_terminal_lock=retain_terminal_lock)
    if journal.phase not in _MID_WRITE_PHASES:
        raise DraftError(
            "operation.unsupported_phase",
            journal.directory,
            details=(f"阶段 {journal.phase.value} 由 trash 子系统恢复",),
        )
    _verify_recovery_material(journal)
    if journal.phase is OperationPhase.ROLLING_BACK:
        # 回滚意图已耐久：先按方向收敛回滚 artifact（quarantine 保存 after、pending
        # 保存 before），再继续回滚。_rollback 幂等跳过已恢复目标，全部恢复后终态化
        # 为 rolled_back（retain_terminal_lock=True 时保留锁与材料）。
        _recover_mutation_artifacts(root, journal, direction=MutationDirection.ROLLBACK)
        return _rollback(
            root,
            journal,
            retain_terminal_lock=retain_terminal_lock,
            changed_paths=_recover_changed_paths(journal),
        )
    # 先把崩溃遗留的 managed temp quarantine 收敛：被中断的 consume 删除
    # 留下的 ``{temp}.quarantine`` 若不在观察复核前处理，会被当成无法解释的新文件。
    _recover_managed_temp_quarantines(root, journal)
    # 再按方向收敛受管写/删应用的崩溃遗留 pending/quarantine（正向：quarantine 保存
    # before、pending 保存 after）：在 _classify 与观察复核前收敛，否则
    # 会被误判为无法解释的外部变化。只有计划时证明缺失的 artifact 才可消费/发布。
    _recover_mutation_artifacts(root, journal, direction=MutationDirection.FORWARD)
    for mutation in journal.mutations:
        if _classify(root, mutation) == "unknown":
            raise DraftError("operation.external_change", root / mutation.path)
    # 恢复前向完成必须复核观察清单：mutation 的 before/after 分类是可解释变化，
    # 其余被观察文件或目录成员的差异是无法解释的外部修改，保留现场，不能把依赖旧
    # 读集语义的结果静默提交。
    changed, consume_temps = _verify_observations_for_recovery(
        root, journal, explained_mutations=journal.mutations
    )
    if changed:
        raise DraftError("operation.external_change", root / changed[0])
    current = journal
    if current.phase is OperationPhase.PREPARED:
        # prepared 崩溃（originals/staged 已登记但应用未开始）：先耐久进入 applying，
        # 再按 _finish_forward 逐条应用；prepared→validating 不是合法阶段边。
        current = _with_phase(current, OperationPhase.APPLYING)
        write_journal(current)
    _clean_owned_temp_artifacts(root, current, consume_temps)
    return _finish_forward(
        root,
        current,
        retain_terminal_lock=retain_terminal_lock,
        changed_paths=_recover_changed_paths(current),
    )


def recover_locked_operation(
    root: Path,
    operation_id: str,
    *,
    retain_terminal_lock: bool = False,
) -> OperationResult:
    """恢复锁内通用 operation 的窄接口。

    供 restore 生命周期层在 R/T 协调事务内调用：验证锁仍属于 operation_id 后复用
    通用锁内恢复。``retain_terminal_lock=True`` 时终态日志耐久但不清材料、不解锁，
    由调用方重读 R/T、重新决策后统一解关联、清理与释放。不要在生命周期层调用会
    释放锁的公开 ``recover`` 再重新拿锁：释放与重拿之间会丢失 R/T 协调所有权。
    """
    if read_lock(root) != operation_id:
        raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, root / LOCK_RELATIVE)
    return _recover_locked(
        root,
        operation_id,
        retain_terminal_lock=retain_terminal_lock,
    )


def verify_locked_terminal_operation(
    root: Path,
    operation_id: str,
    *,
    expected: OperationPhase,
) -> None:
    """持锁只读验证 operation 仍处于 expected 终态；失败保留锁与材料，不清理不解锁。

    复用通用锁内终态验证（``_recover_terminal`` ``retain_terminal_lock=True``）证明
    mutation 目标仍处于终态对应的 before/after 摘要，不复制摘要循环。目录等不可读取
    目标被转换为 ``operation.external_change``，不泄漏原始 ``OSError``；符号链接目标
    保持更精确的 ``path.symlink_escape``：R 终态不能替代"目标仍为 before"的事实证明。
    调用方（restore 生命周期 ABANDON/FINISH 动作）验证通过后才允许
    解关联、清理材料与释放锁。
    """
    journal = read_journal(operation_journal_path(root, operation_id))
    if journal.phase is not expected:
        raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, journal.directory)
    try:
        _recover_terminal(root, journal, operation_id, retain_terminal_lock=True)
    except OSError as exc:
        raise DraftError("operation.external_change", root) from exc


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
            pending_path = entry / _pending_name("journal.json")
            if (
                classify_node_or_error(
                    pending_path,
                    code=CheckCode.OPERATION_JOURNAL_CORRUPT.value,
                    code_path=pending_path,
                )
                is EntryKind.MISSING
            ):
                continue
            resolve_journal_pending(journal_path)
        else:
            # journal 与 pending 并存：pending 是更新的完整日志，先发布。
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
            # 内容。统一状态机：完整预检全部恢复目录 → 删除精确授权叶 → 删除空目录
            # → journal 最后删除。
            known_names = {"journal.json", "originals", "staged"}
            extra = [path for path in entry.iterdir() if path.name not in known_names]
            if extra:
                raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, entry)
            _clean_recovery_copies(journal)
            (entry / "journal.json").unlink()
            entry.rmdir()
        elif (
            journal.phase in _MID_WRITE_PHASES
            or journal.phase in _LINK_INDEX_PHASES
            or journal.phase is OperationPhase.TRASH_HANDOFF_SUCCEEDED
            or journal.phase is OperationPhase.TRASH_PURGE_COMMITTED
        ):
            # 索引 phase 的 operation 必须持锁（锁在终态才释放）：无锁出现即锁已丢失
            # 的损坏现场，保留证据报 inconsistent（spec §12）。
            raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, journal_path)
        # completed / rolled_back / trash_retained / trash_handoff_pending：终态或非写入状态，
        # trash 子系统负责推进 trash_retained 与 trash_handoff_pending。
    return OperationResult(
        operation_id="", kind="recover", phase=OperationPhase.COMPLETED, changed_paths=()
    )
