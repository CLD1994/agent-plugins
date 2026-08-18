"""权威删除包的严格解析：以原始回收日志 T 为信任根。

清单（package.json）自报字段不是信任根：包目录固定从 ``T.operation_id`` 派生，
``package.json`` 与 payload 的哈希必须先锚定到 ``T.mutations``，之后清单字段才允许参与解析。
在任何清单控制路径触达目标 I/O 之前，先完成规范化边界与符号链接检查。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Literal, cast

from draft.diagnostic_codes import CheckCode
from draft.errors import DraftError
from draft.graph import INTERNAL_DIR
from draft.models import MutationKind, OperationKind, OperationPhase
from draft.observations import EntryKind, ObservationManifest
from draft.operations import (
    LOCK_RELATIVE,
    RESTORE_OPERATION_ID_KEY,
    Journal,
    JournalMutation,
    _name_node,  # pyright: ignore[reportPrivateUsage]
    _pending_name,  # pyright: ignore[reportPrivateUsage]
    _quarantine_name,  # pyright: ignore[reportPrivateUsage]
    _read_name_bytes,  # pyright: ignore[reportPrivateUsage]
    _same_name_node,  # pyright: ignore[reportPrivateUsage]
    anchored_leaf,
    classify_node_or_error,
    durable_write,
    operation_journal_path,
    read_journal,
    read_lock,
    reject_symlink_components,
    sha256_bytes,
    validate_operation_id,
)

OUTGOING_RELATIVE = Path(INTERNAL_DIR) / "trash" / "outgoing"
PACKAGE_VERSION = 1

_MANIFEST_FIELDS = frozenset(
    {
        "version",
        "operation_id",
        "page_path",
        "page_sha256",
        "parent_path",
        "parent_sha256",
        "collapsed",
        "hub_path",
        "payload",
    }
)


@dataclass(frozen=True)
class PackageAuthorization:
    """从原 T 日志纯语义派生的受约束包授权（H6）。

    信任根是日志本身：恰好一个 package.json WRITE 与恰好一个 payload WRITE，
    均 before=None、after=合法摘要，无其他包内 mutation；非包 DELETE 必须 1-2 个
    （页面 + 可选折叠集线），非包 before=None WRITE（折叠集线叶）至多一个。
    任何包 I/O、外部 handoff 或"缺失即幂等"分支之前都必须先通过此解析。
    """

    package_relative: Path
    package_json_path: Path
    package_json_digest: str
    payload_path: Path
    payload_relative: Path
    payload_digest: str

    def leaves(self) -> tuple[tuple[Path, str], ...]:
        return (
            (self.package_json_path, self.package_json_digest),
            (self.payload_path, self.payload_digest),
        )


@dataclass(frozen=True)
class VerifiedTrashPackage:
    operation_id: str
    directory: Path
    page_path: Path
    parent_path: Path
    hub_path: Path | None
    collapsed: bool
    page_bytes: bytes
    page_sha256: str
    parent_sha256: str
    # payload 相对包目录的路径与已验证包树快照：恢复观察与包清理都以它为准，
    # 不再重新扫描磁盘，验证后新增的未知条目不能进入基线。manifest_bytes 是验证
    # 时刻的不可变清单字节：摘要复核与解析共用同一份，消费者不得重读 live 文件。
    payload_relative: Path
    manifest_bytes: bytes
    observations: ObservationManifest


def parse_package_authorization(journal: Journal, code_path: Path) -> PackageAuthorization:
    """纯语义解析：从 T 日志产生受约束的包授权，不访问文件系统（回归 H6）。

    物理包缺失只是文件系统状态，不能跳过日志协议验证：空 mutation、缺 package
    write、零/多个 payload、第三个包内 WRITE 或非法组合都必须在任何包 I/O、外部
    handoff 或"缺失即幂等成功"分支之前失败并保留锁。调用点：

    - ``load_verified_package``：验证前门；
    - ``clean_authoritative_package``：在 ``package_dir.exists()`` 早退之前；
    - attempt 终态恢复：``_attempt_authorized_from_journal`` 从同一受约束对象派生。
    """
    if journal.kind is not OperationKind.TRASH:
        raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path)
    package_relative = OUTGOING_RELATIVE / journal.operation_id
    package_writes: list[JournalMutation] = []
    outside_deletes = 0
    outside_new_writes = 0
    for mutation in journal.mutations:
        if mutation.path.is_relative_to(package_relative):
            # 包内只允许两个新建文件：任何 DELETE、改写（before!=None）或更多
            # WRITE 都是恶意/损坏日志。
            if mutation.kind is not MutationKind.WRITE or mutation.before_sha256 is not None:
                raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path)
            package_writes.append(mutation)
        elif mutation.kind is MutationKind.DELETE:
            outside_deletes += 1
        elif mutation.before_sha256 is None:
            # 折叠集线叶新建文件：合法组合至多一个（非折叠为零）。
            outside_new_writes += 1
    if len(package_writes) != 2:
        raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path)
    package_json: JournalMutation | None = None
    payload: JournalMutation | None = None
    for mutation in package_writes:
        if mutation.path == package_relative / "package.json":
            if package_json is not None:
                raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path)
            package_json = mutation
        elif mutation.path.is_relative_to(package_relative / "payload"):
            if payload is not None:
                raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path)
            payload = mutation
        else:
            raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path)
    if package_json is None or payload is None:
        raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path)
    if not 1 <= outside_deletes <= 2 or outside_new_writes > 1:
        raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path)
    assert package_json.after_sha256 is not None
    assert payload.after_sha256 is not None
    return PackageAuthorization(
        package_relative=package_relative,
        package_json_path=package_relative / "package.json",
        package_json_digest=package_json.after_sha256,
        payload_path=payload.path,
        payload_relative=payload.path.relative_to(package_relative),
        payload_digest=payload.after_sha256,
    )


def require_journal_write(journal: Journal, relative_path: Path) -> JournalMutation:
    matches = tuple(
        mutation
        for mutation in journal.mutations
        if mutation.path == relative_path and mutation.kind is MutationKind.WRITE
    )
    if len(matches) != 1 or matches[0].after_sha256 is None:
        raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, relative_path)
    return matches[0]


def _require_journal_delete(
    journal: Journal, relative_path: Path, code_path: Path
) -> JournalMutation:
    matches = tuple(
        mutation
        for mutation in journal.mutations
        if mutation.path == relative_path and mutation.kind is MutationKind.DELETE
    )
    if len(matches) != 1:
        raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path)
    return matches[0]


def _relative_path(value: object, *, code_path: Path) -> Path:
    if not isinstance(value, str):
        raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path)
    pure = PurePosixPath(value)
    if pure.is_absolute() or ".." in pure.parts or not pure.parts:
        raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path)
    return Path(*pure.parts)


def _managed_markdown_path(value: object, code_path: Path) -> Path:
    relative = _relative_path(value, code_path=code_path)
    if relative.suffix != ".md" or relative.parts[0] == INTERNAL_DIR:
        raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path)
    return relative


def _expect_str(data: dict[str, object], key: str, code_path: Path) -> str:
    value = data[key]
    if not isinstance(value, str):
        raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path)
    return value


def _reject_symlink(path: Path) -> None:
    if path.is_symlink():
        raise DraftError(CheckCode.PATH_SYMLINK_ESCAPE.value, path)


def _require_regular_file(path: Path, code_path: Path) -> None:
    """权威包叶节点必须是普通文件：目录、设备、FIFO、符号链接等均视为损坏。

    必须在摘要或解码触达目标 I/O 之前执行，避免 IsADirectoryError 等原始文件
    系统异常绕过稳定领域错误（破坏包停止且不遗留新恢复现场）。
    """
    if path.is_symlink() or not path.is_file():
        raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path)


def _reject_symlink_components(base: Path, relative: Path) -> None:
    cursor = base
    for part in relative.parts:
        cursor /= part
        _reject_symlink(cursor)


def _reject_package_extra_entries(
    package_dir: Path, payload_relative: Path, code_path: Path
) -> None:
    """包目录只允许 package.json、payload 文件及其必要父目录；额外文件、空目录、
    符号链接均视为损坏（唯一权威包不变量）。"""
    allowed_dirs = {Path(".")}
    current = payload_relative.parent
    while current != Path("."):
        allowed_dirs.add(current)
        current = current.parent
    allowed_files = {Path("package.json"), payload_relative}
    for entry in package_dir.rglob("*"):
        relative = entry.relative_to(package_dir)
        if entry.is_symlink():
            raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path)
        if entry.is_dir():
            if relative not in allowed_dirs:
                raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path)
        elif relative not in allowed_files:
            raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path)


def _verified_package_observations(
    root: Path,
    package_dir: Path,
    payload_relative: Path,
    manifest_digest: str,
    payload_digest: str,
) -> ObservationManifest:
    """从已验证协议形状构造不可变包观察快照：只有 package.json、payload 及其父目录。

    观察来自验证时刻的授权集合（而非重新扫描磁盘），恢复执行复核与包清理都以
    它为准：验证后新增的未知条目无法进入基线，会因目录成员/摘要不一致被拒绝，
    绝不被吸收后随整包递归删除（唯一权威包不变量）。
    """
    from draft.observations import (
        Observation,
        directory_observation,
        file_digest_observation,
    )

    package_relative = package_dir.relative_to(root)
    parts = payload_relative.parts
    # 包目录直接成员 = {package.json, payload 第一段}；中间目录成员 = 下一段。
    entries: list[Observation] = [
        file_digest_observation(package_relative / "package.json", manifest_digest),
        file_digest_observation(package_relative / payload_relative, payload_digest),
        directory_observation(package_relative, tuple(sorted({"package.json", parts[0]}))),
    ]
    cursor = package_relative
    for index, part in enumerate(parts[:-1]):
        cursor = cursor / part
        entries.append(directory_observation(cursor, (parts[index + 1],)))
    return ObservationManifest(entries=tuple(entries))


def _parse_manifest(manifest_bytes: bytes, code_path: Path) -> dict[str, object]:
    """从已验证清单字节解码：摘要复核与解析必须共用同一份字节，避免二次读取漂移。"""
    try:
        decoded = json.loads(manifest_bytes.decode("utf-8"))
    except ValueError as exc:
        raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path) from exc
    if not isinstance(decoded, dict):
        raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path)
    data = cast("dict[str, object]", decoded)
    if set(data) != set(_MANIFEST_FIELDS):
        raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path)
    version = data["version"]
    if isinstance(version, bool) or version != PACKAGE_VERSION:
        raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path)
    return data


def load_verified_package(root: Path, trash_journal: Journal) -> VerifiedTrashPackage:
    """以原回收日志为信任根验证权威删除包。

    共同责任边界：把预期的文件系统异常（目录替换叶节点、不可读、非 UTF-8 清单等）
    统一转换为 ``trash.package_corrupt``，不把 IsADirectoryError、FileNotFoundError、
    UnicodeDecodeError 等原始异常泄漏给 check/restore/recover 消费者。
    """
    try:
        return _load_verified_package(root, trash_journal)
    except DraftError:
        raise
    except (OSError, UnicodeDecodeError) as exc:
        raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, trash_journal.directory) from exc


def _load_verified_package(root: Path, trash_journal: Journal) -> VerifiedTrashPackage:
    package_id = validate_operation_id(trash_journal.operation_id)
    package_relative = OUTGOING_RELATIVE / package_id
    package_dir = root / package_relative
    if trash_journal.kind is not OperationKind.TRASH:
        raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, package_dir)
    # 先验证日志协议（恰好两个包内新建文件等），再做任何包 I/O（回归 H6）：
    # 第三个包内 WRITE、空 mutation 等在读取 manifest 前就被拒绝。
    parse_package_authorization(trash_journal, package_dir)

    reject_symlink_components(root, OUTGOING_RELATIVE)
    _reject_symlink(package_dir)
    manifest_path = package_dir / "package.json"
    _reject_symlink(manifest_path)
    _require_regular_file(manifest_path, manifest_path)
    manifest_mutation = require_journal_write(trash_journal, package_relative / "package.json")
    assert manifest_mutation.after_sha256 is not None
    # 清单只读一次：同一份字节同时用于日志摘要复核、UTF-8/JSON 解析，并作为不可变
    # manifest_bytes 存给消费者；任何 live 重读都可能在验证后采信未验证字节。
    manifest_bytes = manifest_path.read_bytes()
    if sha256_bytes(manifest_bytes) != manifest_mutation.after_sha256:
        raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, manifest_path)

    data = _parse_manifest(manifest_bytes, manifest_path)
    if _expect_str(data, "operation_id", manifest_path) != package_id:
        raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, manifest_path)
    page_hash = _expect_str(data, "page_sha256", manifest_path)
    parent_hash = _expect_str(data, "parent_sha256", manifest_path)
    collapsed = data["collapsed"]
    if not isinstance(collapsed, bool):
        raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, manifest_path)
    hub_raw = data["hub_path"]
    if hub_raw is not None and not isinstance(hub_raw, str):
        raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, manifest_path)

    payload_relative = _relative_path(data["payload"], code_path=manifest_path)
    page_path = _managed_markdown_path(data["page_path"], manifest_path)
    parent_path = _managed_markdown_path(data["parent_path"], manifest_path)
    hub_path = _managed_markdown_path(hub_raw, manifest_path) if hub_raw is not None else None
    _reject_symlink_components(package_dir, payload_relative)
    _reject_package_extra_entries(package_dir, payload_relative, manifest_path)

    payload_mutation = require_journal_write(trash_journal, package_relative / payload_relative)
    if payload_mutation.after_sha256 != page_hash:
        raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, manifest_path)
    page_delete = _require_journal_delete(trash_journal, page_path, manifest_path)
    if page_delete.before_sha256 != page_hash:
        raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, manifest_path)
    parent_write = require_journal_write(trash_journal, parent_path)
    if parent_write.after_sha256 != parent_hash:
        raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, manifest_path)
    if collapsed:
        if hub_path is None:
            raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, manifest_path)
        _require_journal_delete(trash_journal, hub_path, manifest_path)
    elif hub_path is not None:
        raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, manifest_path)
    for mutation in trash_journal.mutations:
        if mutation.path.is_relative_to(OUTGOING_RELATIVE) and not mutation.path.is_relative_to(
            package_relative
        ):
            raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, manifest_path)

    _reject_symlink_components(root, page_path)
    _reject_symlink_components(root, parent_path)
    if hub_path is not None:
        _reject_symlink_components(root, hub_path)

    payload_path = package_dir / payload_relative
    _require_regular_file(payload_path, payload_path)
    page_bytes = payload_path.read_bytes()
    if sha256_bytes(page_bytes) != payload_mutation.after_sha256:
        raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, payload_path)
    return VerifiedTrashPackage(
        operation_id=package_id,
        directory=package_dir,
        page_path=page_path,
        parent_path=parent_path,
        hub_path=hub_path,
        collapsed=collapsed,
        page_bytes=page_bytes,
        page_sha256=page_hash,
        parent_sha256=parent_hash,
        payload_relative=payload_relative,
        manifest_bytes=manifest_bytes,
        observations=_verified_package_observations(
            root,
            package_dir,
            payload_relative,
            manifest_mutation.after_sha256,  # require_journal_write 已保证非 None
            page_hash,
        ),
    )


def _package_directories(
    root: Path, package_dir: Path, authorized: list[tuple[Path, str]]
) -> set[Path]:
    """授权目录树：包目录 + 每个叶节点的父目录链（不含 root）。"""
    allowed = {package_dir}
    for relative, _ in authorized:
        cursor = (root / relative).parent
        while cursor != root and cursor not in allowed:
            allowed.add(cursor)
            cursor = cursor.parent
    return allowed


def _precheck_package_tree(
    root: Path,
    package_dir: Path,
    authorized: list[tuple[Path, str]],
    code_path: Path,
) -> None:
    """只读预检：在第一次删除前枚举整个包树，未知/符号链接/类型/摘要不符即失败。

    不跟随目录符号链接遍历；目录符号链接与未知成员都在第一次 unlink 之前拒绝，
    保证清理失败不破坏已授权证据（回归 H2）。
    """
    import os

    allowed_dirs = _package_directories(root, package_dir, authorized)
    allowed_leaves = {root / relative for relative, _ in authorized}
    digest_by_path = {root / relative: digest for relative, digest in authorized}
    for dirpath, dirnames, filenames in os.walk(package_dir, followlinks=False):
        directory = Path(dirpath)
        if directory.is_symlink() or directory not in allowed_dirs:
            raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, directory)
        for name in dirnames:
            child = directory / name
            if child.is_symlink() or child not in allowed_dirs:
                raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, child)
        for name in filenames:
            leaf = directory / name
            if leaf.is_symlink() or leaf not in allowed_leaves or not leaf.is_file():
                raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, leaf)
            if sha256_bytes(leaf.read_bytes()) != digest_by_path[leaf]:
                raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, leaf)


def _remove_verified_leaf(
    root: Path, relative: Path, expected_digest: str, code_path: Path
) -> None:
    """现存授权叶节点：锚定 no-follow 目录句柄复核类型与摘要后删除精确路径。

    验证（stat/read 摘要）与删除（unlink）使用同一锚定句柄：中间组件在"检查通过"
    之后变成符号链接时，句柄仍指向原目录 inode，绝不按字符串重新解析绝对路径
    （回归 H1）。缺失（提交点后幂等清理）容忍；摘要不符说明验证后内容被改写，
    拒绝删除。
    """
    from draft.operations import anchored_leaf

    with anchored_leaf(
        root, relative, code=CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path=code_path
    ) as leaf:
        node = leaf.classify()
        if node is EntryKind.MISSING:
            return
        if node is not EntryKind.FILE:
            raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path)
        if sha256_bytes(leaf.read()) != expected_digest:
            raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path)
        # 隔离→验证→删除：验证与删除同一锚定句柄，隔离名由授权目标派生（回归 H3）。
        leaf.quarantine_delete(expected_digest)


def _remove_verified_dir(root: Path, relative: Path, code_path: Path) -> None:
    """已知目录 rmdir：缺失容忍；非空目录（含未知内容）、符号链接或类型变化拒绝。"""
    from draft.operations import anchored_leaf

    with anchored_leaf(
        root, relative, code=CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path=code_path
    ) as leaf:
        node = leaf.classify()
        if node is EntryKind.MISSING:
            return
        if node is not EntryKind.DIRECTORY:
            raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path)
        leaf.rmdir()


_CLEANUP_EVIDENCE_VERSION = 2
_CLEANUP_EVIDENCE_NAME = "cleanup-evidence.json"
_EVIDENCE_TOP_FIELDS = frozenset({"version", "operation_id", "cleanup_phase", "leaves"})
_EVIDENCE_LEAF_FIELDS = frozenset({"relative", "sha256", "state"})
_EVIDENCE_PRESENT_FIELDS = frozenset({"relative", "sha256", "state", "content"})


@dataclass(frozen=True)
class CleanupEvidenceLeaf:
    """单个授权叶的证据状态：present（携带内容）或 already_missing（不携带内容）。"""

    relative: Path
    sha256: str
    state: Literal["present", "already_missing"]
    content: bytes | None


@dataclass(frozen=True)
class CleanupEvidence:
    """受约束的清理证据：删除后重建权威包的信任根（回归 H4）。

    顶层字段精确匹配 {version, operation_id, cleanup_phase, leaves}；恰好两个按授权
    排序的叶，每个授权叶出现一次。present 叶内容严格 base64 解码且摘要等于 T 授权；
    already_missing 只出现在已证明清理提交的阶段且不携带内容。整个对象先完整验证，
    再允许任何恢复副作用。
    """

    version: int
    operation_id: str
    cleanup_phase: str
    leaves: tuple[CleanupEvidenceLeaf, ...]


class CleanupEvidenceIntent(StrEnum):
    """证据生命周期意图：包仍存在（必须保留证据）或包已删除（证据必须消失）。"""

    KEEP = "keep"
    REMOVE = "remove"


class EvidenceNodeState(StrEnum):
    """evidence final/pending/quarantine 单名状态：缺失/合法/内容损坏/非文件。"""

    MISSING = "missing"
    VALID = "valid"
    INVALID = "invalid"
    NON_FILE = "non_file"


class _CleanupEvidenceRole(StrEnum):
    """cleanup evidence 三名（final/pending/quarantine）的内部角色标识。

    不与词汇枚举值重名，避免在源码中散落裸协议值字面量。
    """

    FINAL = "final_name"
    PENDING = "pending_name"
    QUARANTINE = "quarantine_name"


class CleanupEvidenceAction(StrEnum):
    """cleanup evidence 收敛的按序动作：发布/恢复/消费/就绪/完成/损坏。"""

    PUBLISH_PENDING = "publish_pending"
    CONSUME_PENDING = "consume_pending"
    RESTORE_QUARANTINE = "restore_quarantine"
    CONSUME_FINAL = "consume_final"
    CONSUME_QUARANTINE = "consume_quarantine"
    READY = "ready"
    DONE = "done"
    CORRUPT = "corrupt"


def cleanup_evidence_actions(
    *,
    intent: CleanupEvidenceIntent,
    final: EvidenceNodeState,
    pending: EvidenceNodeState,
    quarantine: EvidenceNodeState,
    final_pending_same_node: bool,
    final_quarantine_same_node: bool,
) -> tuple[CleanupEvidenceAction, ...]:
    """cleanup evidence final/pending/quarantine 收敛决策（纯函数，第十轮 H1）。

    KEEP（包 exact/partial，证据必须保留）与 REMOVE（包已删除，证据必须消失）分别按
    合法组合输出动作；任一 INVALID/NON_FILE 名、同节点证明缺失、或表外多名组合都报
    CORRUPT（保留现场，绝不"逐个删到干净"）。决策不访问文件系统，checker 与恢复器共用。
    """
    for name in (final, pending, quarantine):
        if name in (EvidenceNodeState.INVALID, EvidenceNodeState.NON_FILE):
            return (CleanupEvidenceAction.CORRUPT,)
    if intent is CleanupEvidenceIntent.KEEP:
        return _keep_evidence_actions(
            final, pending, quarantine, final_pending_same_node, final_quarantine_same_node
        )
    return _remove_evidence_actions(
        final, pending, quarantine, final_pending_same_node, final_quarantine_same_node
    )


def cleanup_evidence_protocol_corrupt(
    *,
    final: EvidenceNodeState,
    pending: EvidenceNodeState,
    quarantine: EvidenceNodeState,
    final_pending_same_node: bool,
    final_quarantine_same_node: bool,
) -> bool:
    """KEEP/REMOVE 任一判 CORRUPT 即返回 True；不访问文件系统（第十一轮 H2）。

    checker 只读结论与运行时收敛动作共用同一 ``cleanup_evidence_actions`` 权威决策：
    KEEP 与 REMOVE 的 CORRUPT 集合必须一致（由穷举测试锁定），因此 checker 不必选择
    更宽松的一支，也不得自行发明第三套 phase 规则。
    """

    def actions(intent: CleanupEvidenceIntent) -> tuple[CleanupEvidenceAction, ...]:
        return cleanup_evidence_actions(
            intent=intent,
            final=final,
            pending=pending,
            quarantine=quarantine,
            final_pending_same_node=final_pending_same_node,
            final_quarantine_same_node=final_quarantine_same_node,
        )

    keep_corrupt = CleanupEvidenceAction.CORRUPT in actions(CleanupEvidenceIntent.KEEP)
    remove_corrupt = CleanupEvidenceAction.CORRUPT in actions(CleanupEvidenceIntent.REMOVE)
    return keep_corrupt or remove_corrupt


def _keep_evidence_actions(
    final: EvidenceNodeState,
    pending: EvidenceNodeState,
    quarantine: EvidenceNodeState,
    final_pending_same_node: bool,
    final_quarantine_same_node: bool,
) -> tuple[CleanupEvidenceAction, ...]:
    if (
        final is EvidenceNodeState.MISSING
        and pending is EvidenceNodeState.MISSING
        and quarantine is EvidenceNodeState.MISSING
    ):
        # 首次清理、证据尚未写入：无可收敛，等待清理开始后按需写入。
        return (CleanupEvidenceAction.READY,)
    if final is EvidenceNodeState.VALID and pending is EvidenceNodeState.MISSING:
        if quarantine is EvidenceNodeState.MISSING:
            return (CleanupEvidenceAction.READY,)
        if quarantine is EvidenceNodeState.VALID and final_quarantine_same_node:
            # 同一次隔离的精确节点：消费陈旧 quarantine，保留 final。
            return (CleanupEvidenceAction.CONSUME_QUARANTINE, CleanupEvidenceAction.READY)
        return (CleanupEvidenceAction.CORRUPT,)
    if final is EvidenceNodeState.MISSING and pending is EvidenceNodeState.VALID:
        if quarantine is EvidenceNodeState.MISSING:
            return (CleanupEvidenceAction.PUBLISH_PENDING, CleanupEvidenceAction.READY)
        return (CleanupEvidenceAction.CORRUPT,)
    if (
        final is EvidenceNodeState.VALID
        and pending is EvidenceNodeState.VALID
        and final_pending_same_node
        and quarantine is EvidenceNodeState.MISSING
    ):
        # 两个节点/字节与同一 T 授权一致：消费 pending，保留 final。
        return (CleanupEvidenceAction.CONSUME_PENDING, CleanupEvidenceAction.READY)
    if (
        final is EvidenceNodeState.MISSING
        and pending is EvidenceNodeState.MISSING
        and quarantine is EvidenceNodeState.VALID
    ):
        return (CleanupEvidenceAction.RESTORE_QUARANTINE, CleanupEvidenceAction.READY)
    return (CleanupEvidenceAction.CORRUPT,)


def _remove_evidence_actions(
    final: EvidenceNodeState,
    pending: EvidenceNodeState,
    quarantine: EvidenceNodeState,
    final_pending_same_node: bool,
    final_quarantine_same_node: bool,
) -> tuple[CleanupEvidenceAction, ...]:
    if (
        final is EvidenceNodeState.MISSING
        and pending is EvidenceNodeState.MISSING
        and quarantine is EvidenceNodeState.MISSING
    ):
        return (CleanupEvidenceAction.DONE,)
    if final is EvidenceNodeState.VALID and pending is EvidenceNodeState.MISSING:
        if quarantine is EvidenceNodeState.MISSING:
            return (CleanupEvidenceAction.CONSUME_FINAL, CleanupEvidenceAction.DONE)
        if quarantine is EvidenceNodeState.VALID and final_quarantine_same_node:
            # 先完成 final 删除，再消费 quarantine。
            return (
                CleanupEvidenceAction.CONSUME_FINAL,
                CleanupEvidenceAction.CONSUME_QUARANTINE,
                CleanupEvidenceAction.DONE,
            )
        return (CleanupEvidenceAction.CORRUPT,)
    if final is EvidenceNodeState.MISSING and pending is EvidenceNodeState.VALID:
        if quarantine is EvidenceNodeState.MISSING:
            return (CleanupEvidenceAction.CONSUME_PENDING, CleanupEvidenceAction.DONE)
        return (CleanupEvidenceAction.CORRUPT,)
    if (
        final is EvidenceNodeState.VALID
        and pending is EvidenceNodeState.VALID
        and final_pending_same_node
        and quarantine is EvidenceNodeState.MISSING
    ):
        # 同一次发布：先消费 pending，再消费 final。
        return (
            CleanupEvidenceAction.CONSUME_PENDING,
            CleanupEvidenceAction.CONSUME_FINAL,
            CleanupEvidenceAction.DONE,
        )
    if final is EvidenceNodeState.MISSING and quarantine is EvidenceNodeState.VALID:
        if pending is EvidenceNodeState.MISSING:
            return (CleanupEvidenceAction.CONSUME_QUARANTINE, CleanupEvidenceAction.DONE)
        return (CleanupEvidenceAction.CORRUPT,)
    return (CleanupEvidenceAction.CORRUPT,)


@dataclass(frozen=True)
class CleanupAuthorization:
    """已提交的永久删除授权（回归 M1）：锁内重读的已提交 T journal + 已验证处置。

    由 ``cleanup_authorization`` 从锁内重读构造并证明（绝不使用调用方手中的旧 journal
    对象；调用方只传递 operation_id 与锁所有者，授权内容以磁盘最新日志为准）：
    - handoff/purge：当前锁必须属于 T 本身；
    - restore：当前锁必须属于匹配的已 completed restore R，且 T↔R 双向关联精确匹配。
    构造边界已证明的处置种类，不再保存未经约束的锁所有者字符串。
    """

    journal: Journal
    cleanup_kind: Literal["handoff", "purge", "restore"]


def cleanup_authorization(
    root: Path, operation_id: str, *, lock_operation_id: str
) -> CleanupAuthorization:
    """锁内重读当前 T journal，验证已提交阶段与 phase 对应的锁所有者后构造清理授权（回归 M1）。

    永久删除授权不能由"某个锁存在 + T phase 字符串"拼出，必须证明是谁完成了哪个提交点：

    | T phase | 当前锁 | 关联证明 | 处置 |
    |---|---|---|---|
    | trash_handoff_succeeded | T.operation_id | 不需要 R | handoff |
    | trash_purge_committed | T.operation_id | 不需要 R | purge |
    | rolled_back | R.operation_id | R.kind=restore、R.phase=completed、
    |             |                | R→T 与 T→R 精确匹配 | restore |
    | 任意其他组合 | 任意 | 任意 | 拒绝 |

    trash_retained/trash_handoff_pending 无论包状态都不得进入删除；completed 只能按
    disposition 与既有终态证据审计，不能重新授权删除。
    """
    if read_lock(root) != lock_operation_id:
        raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, root / LOCK_RELATIVE)
    journal = read_journal(operation_journal_path(root, operation_id))
    if journal.operation_id != operation_id or journal.kind is not OperationKind.TRASH:
        raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, journal.directory)
    if journal.phase is OperationPhase.TRASH_HANDOFF_SUCCEEDED:
        if lock_operation_id != journal.operation_id:
            raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, journal.directory)
        return CleanupAuthorization(journal=journal, cleanup_kind="handoff")
    if journal.phase is OperationPhase.TRASH_PURGE_COMMITTED:
        if lock_operation_id != journal.operation_id:
            raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, journal.directory)
        return CleanupAuthorization(journal=journal, cleanup_kind="purge")
    if journal.phase is OperationPhase.ROLLED_BACK:
        # restore 清理：锁必须属于匹配的已 completed restore R，T↔R 双向关联精确匹配。
        r_id = journal.metadata.get(RESTORE_OPERATION_ID_KEY)
        if r_id is None or r_id != lock_operation_id:
            raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, journal.directory)
        restore = read_journal(operation_journal_path(root, r_id))
        if (
            restore.kind is not OperationKind.RESTORE
            or restore.phase is not OperationPhase.COMPLETED
            or restore.metadata.get("restores_trash_operation") != journal.operation_id
        ):
            raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, journal.directory)
        return CleanupAuthorization(journal=journal, cleanup_kind="restore")
    raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, journal.directory)


def _cleanup_evidence_path(journal: Journal) -> Path:
    """清理证据路径：锁所属操作目录内，包目录全部成功移除前不得清理。"""
    return journal.directory / _CLEANUP_EVIDENCE_NAME


def parse_cleanup_evidence(data: bytes, journal: Journal, code_path: Path) -> CleanupEvidence:
    """从 bytes 完整解析并验证清理证据：任何字段/状态/摘要问题都在首次副作用前失败。

    顶层与叶字段集合精确匹配，拒绝未知字段、重复 relative、缺叶、非法 base64 与非法
    UTF-8；证据叶摘要必须等于 T 授权摘要（回归 H4）。存在但非法不等于不存在。
    """
    import base64
    import binascii

    try:
        decoded: object = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path) from exc
    if not isinstance(decoded, dict):
        raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path)
    parsed = cast("dict[str, object]", decoded)
    if frozenset(parsed) != _EVIDENCE_TOP_FIELDS:
        raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path)
    version = parsed.get("version")
    operation_id = parsed.get("operation_id")
    cleanup_phase = parsed.get("cleanup_phase")
    leaves_raw = parsed.get("leaves")
    if (
        version != _CLEANUP_EVIDENCE_VERSION
        or not isinstance(operation_id, str)
        or operation_id != journal.operation_id
        or not isinstance(cleanup_phase, str)
        or cleanup_phase != journal.phase.value
    ):
        raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path)
    if not isinstance(leaves_raw, list) or len(cast("list[object]", leaves_raw)) != 2:
        raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path)
    auth = parse_package_authorization(journal, code_path)
    expected_digest = {
        auth.package_json_path: auth.package_json_digest,
        auth.payload_path: auth.payload_digest,
    }
    expected_order = [auth.package_json_path, auth.payload_path]
    leaves: list[CleanupEvidenceLeaf] = []
    seen: set[Path] = set()
    for raw in cast("list[object]", leaves_raw):
        if not isinstance(raw, dict):
            raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path)
        item = cast("dict[str, object]", raw)
        state = item.get("state")
        if state == "present":
            if frozenset(item) != _EVIDENCE_PRESENT_FIELDS:
                raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path)
            relative_raw = item.get("relative")
            sha = item.get("sha256")
            b64 = item.get("content")
            if (
                not isinstance(relative_raw, str)
                or not isinstance(sha, str)
                or not isinstance(b64, str)
            ):
                raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path)
            relative = Path(relative_raw)
            if relative not in expected_digest or relative in seen:
                raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path)
            try:
                content = base64.b64decode(b64, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path) from exc
            if sha256_bytes(content) != sha or sha != expected_digest[relative]:
                raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path)
            seen.add(relative)
            leaves.append(CleanupEvidenceLeaf(relative, sha, "present", content))
        elif state == "already_missing":
            if frozenset(item) != _EVIDENCE_LEAF_FIELDS:
                raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path)
            relative_raw = item.get("relative")
            sha = item.get("sha256")
            if not isinstance(relative_raw, str) or not isinstance(sha, str):
                raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path)
            relative = Path(relative_raw)
            if relative not in expected_digest or relative in seen:
                raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path)
            if sha != expected_digest[relative]:
                raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path)
            seen.add(relative)
            leaves.append(CleanupEvidenceLeaf(relative, sha, "already_missing", None))
        else:
            raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path)
    if len(seen) != 2:
        raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path)
    leaves.sort(key=lambda leaf: expected_order.index(leaf.relative))
    return CleanupEvidence(_CLEANUP_EVIDENCE_VERSION, operation_id, cleanup_phase, tuple(leaves))


def _write_cleanup_evidence(
    journal: Journal,
    auth: PackageAuthorization,
    leaves_bytes: dict[Path, bytes],
) -> None:
    """删除第一个权威叶节点前，把两个授权叶（present/already_missing）耐久记录在锁所属操作目录。

    每个授权叶都出现一次：清理开始时存在的叶记录 present（携带内容），已缺失的叶记录
    already_missing（不携带内容，只在已证明清理提交的阶段产生）。该证据在包目录全部
    成功移除前不得清理：删除中途出现 extra/类型变化时，恢复用它重建完整合法包，
    绝不只剩未知内容（回归 H2/H4）。
    """
    import base64

    expected_digest = {
        auth.package_json_path: auth.package_json_digest,
        auth.payload_path: auth.payload_digest,
    }
    entries: list[dict[str, object]] = []
    for relative in (auth.package_json_path, auth.payload_path):
        content = leaves_bytes.get(relative)
        if content is not None:
            entries.append(
                {
                    "relative": relative.as_posix(),
                    "sha256": sha256_bytes(content),
                    "state": "present",
                    "content": base64.b64encode(content).decode("ascii"),
                }
            )
        else:
            entries.append(
                {
                    "relative": relative.as_posix(),
                    "sha256": expected_digest[relative],
                    "state": "already_missing",
                }
            )
    payload: dict[str, object] = {
        "version": _CLEANUP_EVIDENCE_VERSION,
        "operation_id": journal.operation_id,
        "cleanup_phase": journal.phase.value,
        "leaves": entries,
    }
    durable_write(
        journal.directory,
        Path(_CLEANUP_EVIDENCE_NAME),
        (json.dumps(payload) + "\n").encode("utf-8"),
    )


def classify_cleanup_evidence_artifacts(
    journal: Journal,
) -> tuple[EvidenceNodeState, EvidenceNodeState, EvidenceNodeState, bool, bool] | None:
    """只读分类 cleanup-evidence final/pending/quarantine 三名（checker 与恢复器共用）。

    三名都在操作目录同一锚定句柄内分类：MISSING/普通文件解析（VALID/INVALID）/非文件。
    ``parse_cleanup_evidence`` 完整验证 T 授权；非法字节即 INVALID。返回
    (final, pending, quarantine, final_pending_same_node, final_quarantine_same_node)；
    操作目录中间组件缺失（_MissingLeaf）时返回 None（三名单名必然全部缺失）。
    纯只读，无副作用。
    """
    from draft.operations import _AnchoredLeaf  # pyright: ignore[reportPrivateUsage]

    evidence_path = _cleanup_evidence_path(journal)
    names = (
        (_CleanupEvidenceRole.FINAL, Path(_CLEANUP_EVIDENCE_NAME)),
        (_CleanupEvidenceRole.PENDING, Path(_pending_name(_CLEANUP_EVIDENCE_NAME))),
        (_CleanupEvidenceRole.QUARANTINE, Path(_quarantine_name(_CLEANUP_EVIDENCE_NAME))),
    )
    with anchored_leaf(
        journal.directory,
        Path(_CLEANUP_EVIDENCE_NAME),
        code=CheckCode.TRASH_PACKAGE_CORRUPT.value,
        code_path=evidence_path,
    ) as leaf:
        if not isinstance(leaf, _AnchoredLeaf):
            return None
        states: dict[_CleanupEvidenceRole, EvidenceNodeState] = {}
        nodes: dict[_CleanupEvidenceRole, tuple[int, int] | None] = {}
        for role, relative in names:
            node = _AnchoredLeaf(
                leaf.parent_fd,
                relative.name,
                journal.directory,
                CheckCode.TRASH_PACKAGE_CORRUPT.value,
                evidence_path,
            )
            kind = node.classify()
            nodes[role] = _name_node(
                leaf.parent_fd,
                relative.name,
                code=CheckCode.TRASH_PACKAGE_CORRUPT.value,
                code_path=evidence_path,
            )
            if kind is EntryKind.MISSING:
                states[role] = EvidenceNodeState.MISSING
            elif kind is not EntryKind.FILE:
                states[role] = EvidenceNodeState.NON_FILE
            else:
                data = _read_name_bytes(
                    leaf.parent_fd,
                    relative.name,
                    code_path=evidence_path,
                    code=CheckCode.TRASH_PACKAGE_CORRUPT.value,
                )
                try:
                    parse_cleanup_evidence(data, journal, evidence_path)
                    states[role] = EvidenceNodeState.VALID
                except DraftError:
                    states[role] = EvidenceNodeState.INVALID
        final_node = nodes[_CleanupEvidenceRole.FINAL]
        pending_node = nodes[_CleanupEvidenceRole.PENDING]
        quarantine_node = nodes[_CleanupEvidenceRole.QUARANTINE]
        final_pending_same = final_node is not None and final_node == pending_node
        final_quarantine_same = final_node is not None and final_node == quarantine_node
        return (
            states[_CleanupEvidenceRole.FINAL],
            states[_CleanupEvidenceRole.PENDING],
            states[_CleanupEvidenceRole.QUARANTINE],
            final_pending_same,
            final_quarantine_same,
        )


def _consume_cleanup_evidence_name(journal: Journal, relative: Path) -> None:
    """inode+内容复核后消费（删除）evidence 名；缺失幂等容忍，类型/解析失败保留现场。"""
    from draft.operations import _AnchoredLeaf, _fsync_dir_fd  # pyright: ignore[reportPrivateUsage]

    code_path = _cleanup_evidence_path(journal)
    with anchored_leaf(
        journal.directory,
        relative,
        code=CheckCode.TRASH_PACKAGE_CORRUPT.value,
        code_path=code_path,
    ) as leaf:
        if not isinstance(leaf, _AnchoredLeaf):
            return  # 中间组件缺失：证据必然缺失，幂等。
        verified = _name_node(
            leaf.parent_fd,
            leaf.name,
            code=CheckCode.TRASH_PACKAGE_CORRUPT.value,
            code_path=code_path,
        )
        if verified is None:
            return  # ENOENT：名字确实不存在，幂等（H4：只允许缺失证明幂等）。
        if leaf.classify() is not EntryKind.FILE:
            raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path)
        data = _read_name_bytes(
            leaf.parent_fd,
            leaf.name,
            code_path=code_path,
            code=CheckCode.TRASH_PACKAGE_CORRUPT.value,
        )
        parse_cleanup_evidence(data, journal, code_path)  # 重新验证后才消费。
        if not _same_name_node(
            leaf.parent_fd,
            leaf.name,
            verified,
            code=CheckCode.TRASH_PACKAGE_CORRUPT.value,
            code_path=code_path,
        ):
            raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path)
        try:
            os.unlink(leaf.name, dir_fd=leaf.parent_fd)
        except OSError as exc:
            raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path) from exc
        _fsync_dir_fd(leaf.parent_fd, code_path)


def _publish_cleanup_evidence_name(journal: Journal, source: str, *, code_path: Path) -> None:
    """把授权 pending/quarantine（源名）no-clobber 发布为 final 并消费源名（回归 H1/H3）。"""
    import os as os_module

    from draft.operations import _AnchoredLeaf, _fsync_dir_fd  # pyright: ignore[reportPrivateUsage]

    with anchored_leaf(
        journal.directory,
        Path(_CLEANUP_EVIDENCE_NAME),
        code=CheckCode.TRASH_PACKAGE_CORRUPT.value,
        code_path=code_path,
    ) as final_leaf:
        if not isinstance(final_leaf, _AnchoredLeaf):
            raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path)
        if final_leaf.classify() is not EntryKind.MISSING:
            raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path)
        source_leaf = _AnchoredLeaf(
            final_leaf.parent_fd,
            source,
            journal.directory,
            CheckCode.TRASH_PACKAGE_CORRUPT.value,
            code_path,
        )
        if source_leaf.classify() is not EntryKind.FILE:
            raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path)
        data = _read_name_bytes(
            final_leaf.parent_fd,
            source,
            code_path=code_path,
            code=CheckCode.TRASH_PACKAGE_CORRUPT.value,
        )
        parse_cleanup_evidence(data, journal, code_path)  # 发布前完整验证 T 授权。
        verified = _name_node(
            final_leaf.parent_fd,
            source,
            code=CheckCode.TRASH_PACKAGE_CORRUPT.value,
            code_path=code_path,
        )
        try:
            os_module.link(
                source,
                final_leaf.name,
                src_dir_fd=final_leaf.parent_fd,
                dst_dir_fd=final_leaf.parent_fd,
            )
        except FileExistsError as exc:
            raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path) from exc
        except OSError as exc:
            raise DraftError(CheckCode.OPERATION_DURABILITY_FAILED.value, code_path) from exc
        _fsync_dir_fd(final_leaf.parent_fd, code_path)
        if not _same_name_node(
            final_leaf.parent_fd,
            source,
            verified,
            code=CheckCode.TRASH_PACKAGE_CORRUPT.value,
            code_path=code_path,
        ):
            raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path)
        try:
            os_module.unlink(source, dir_fd=final_leaf.parent_fd)
        except OSError as exc:
            raise DraftError(CheckCode.OPERATION_DURABILITY_FAILED.value, code_path) from exc
        _fsync_dir_fd(final_leaf.parent_fd, code_path)


def converge_cleanup_evidence(journal: Journal, *, intent: CleanupEvidenceIntent) -> None:
    """cleanup evidence final/pending/quarantine 的共享收敛边界（第十轮 H1）。

    KEEP：任何包叶删除前把证据收敛为就绪（发布合法 pending / 恢复 quarantine /
    消费同一次隔离的陈旧名）。REMOVE：包已删除后把证据收敛为空。决策来自纯函数
    ``cleanup_evidence_actions``，I/O 层只按动作执行；非法类型/内容/多名组合在首次
    副作用前报 trash.package_corrupt 并保留现场。checker 只调用只读分类与纯决策，
    不进入本边界。
    """
    classified = classify_cleanup_evidence_artifacts(journal)
    if classified is None:
        return  # 操作目录缺失：证据必然缺失，无动作。
    final, pending, quarantine, same_pending, same_quarantine = classified
    actions = cleanup_evidence_actions(
        intent=intent,
        final=final,
        pending=pending,
        quarantine=quarantine,
        final_pending_same_node=same_pending,
        final_quarantine_same_node=same_quarantine,
    )
    if CleanupEvidenceAction.CORRUPT in actions:
        raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, _cleanup_evidence_path(journal))
    code_path = _cleanup_evidence_path(journal)
    for action in actions:
        if action is CleanupEvidenceAction.READY or action is CleanupEvidenceAction.DONE:
            continue
        if action is CleanupEvidenceAction.PUBLISH_PENDING:
            _publish_cleanup_evidence_name(
                journal, _pending_name(_CLEANUP_EVIDENCE_NAME), code_path=code_path
            )
        elif action is CleanupEvidenceAction.RESTORE_QUARANTINE:
            _publish_cleanup_evidence_name(
                journal, _quarantine_name(_CLEANUP_EVIDENCE_NAME), code_path=code_path
            )
        elif action is CleanupEvidenceAction.CONSUME_PENDING:
            _consume_cleanup_evidence_name(journal, Path(_pending_name(_CLEANUP_EVIDENCE_NAME)))
        elif action is CleanupEvidenceAction.CONSUME_FINAL:
            _consume_cleanup_evidence_name(journal, Path(_CLEANUP_EVIDENCE_NAME))
        elif action is CleanupEvidenceAction.CONSUME_QUARANTINE:
            _consume_cleanup_evidence_name(journal, Path(_quarantine_name(_CLEANUP_EVIDENCE_NAME)))


def _restore_missing_leaves_from_evidence(root: Path, journal: Journal, code_path: Path) -> None:
    """从证据恢复缺失授权叶：上次清理中途失败后使合法包完整可重建。

    先以 ``parse_cleanup_evidence`` 完整解析并验证整个证据（任何条目损坏都在首次受管
    写入前失败），再逐个收敛 pending 并补写仍缺失的叶（回归 H4）。恢复写入与验证共用
    锚定句柄，不按绝对路径重新解析。
    """
    from draft.operations import recover_digest_pending, recover_quarantine

    evidence_path = _cleanup_evidence_path(journal)
    from draft.operations import _AnchoredLeaf  # pyright: ignore[reportPrivateUsage]

    with anchored_leaf(
        journal.directory,
        Path(_CLEANUP_EVIDENCE_NAME),
        code=CheckCode.TRASH_PACKAGE_CORRUPT.value,
        code_path=evidence_path,
    ) as evidence_leaf:
        if not isinstance(evidence_leaf, _AnchoredLeaf):
            raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, evidence_path)
        if evidence_leaf.classify() is not EntryKind.FILE:
            raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, evidence_path)
        evidence = parse_cleanup_evidence(
            _read_name_bytes(
                evidence_leaf.parent_fd,
                evidence_leaf.name,
                code_path=evidence_path,
                code=CheckCode.TRASH_PACKAGE_CORRUPT.value,
            ),
            journal,
            code_path,
        )
    for leaf_state in evidence.leaves:
        if leaf_state.state != "present":
            continue
        relative = leaf_state.relative
        content = leaf_state.content
        assert content is not None
        # 先收敛崩溃遗留 pending/quarantine（发布待发布叶/完成被中断的删除），再补写
        # 仍缺失的叶（回归 H2/H3）。
        recover_digest_pending(
            root,
            relative,
            leaf_state.sha256,
            code=CheckCode.TRASH_PACKAGE_CORRUPT.value,
            code_path=code_path,
        )
        recover_quarantine(
            root,
            relative,
            leaf_state.sha256,
            code=CheckCode.TRASH_PACKAGE_CORRUPT.value,
            code_path=code_path,
        )
        with anchored_leaf(
            root, relative, code=CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path=code_path
        ) as leaf:
            if leaf.classify() is EntryKind.MISSING:
                leaf.write(content)


def clean_authoritative_package(root: Path, authorization: CleanupAuthorization) -> None:
    """唯一权威包清理原语：已提交授权门 → 日志协议 → 节点分类 → 证据事务 → 锚定删除。

    清理授权必须是已提交阶段（rolled_back / handoff_succeeded / purge_committed）的
    锁内重读 journal（回归 M1）：trash_retained/trash_handoff_pending 无论包状态都
    不得进入删除；completed 只能审计，不能重新猜授权。清理计划从 T 日志派生恰好两个
    授权叶；预检枚举整个包树，任何未知成员在第一次 unlink 之前失败。首次删除前把两个
    授权叶耐久写入证据（回归 H2），删除中途出现 extra/类型变化时证据保留、下次恢复
    先重建完整合法包再收敛；包目录全部成功移除后才清除证据。证据节点按 lstat 分类：
    只有 missing 与完整合法证据可继续，错误类型保留现场（回归 H4）。

    restore、handoff、purge 与终态恢复全部经此原语清理，不再各自直接 remove_tree。
    """
    journal = authorization.journal
    reject_symlink_components(root, OUTGOING_RELATIVE)
    # 任何包叶删除前把 evidence 收敛为 KEEP-ready（发布合法 pending / 恢复 quarantine
    # / 消费同一次隔离的陈旧名；回归 H1/H5）：完整验证 T 授权后才允许继续。
    converge_cleanup_evidence(journal, intent=CleanupEvidenceIntent.KEEP)
    package_dir = root / OUTGOING_RELATIVE / journal.operation_id
    # 先验证日志协议再碰包：物理包缺失只是文件系统状态，不能跳过日志语义验证
    # （回归 H6）——空 mutation、缺 package write、第三个包内 WRITE 都必须报损坏。
    auth = parse_package_authorization(journal, package_dir)
    authorized = list(auth.leaves())
    # lstat 分类：悬空符号链接是 link 不是 missing，不得折叠成幂等缺失（回归 M2）。
    package_node = classify_node_or_error(
        package_dir, code=CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path=package_dir
    )
    if package_node is EntryKind.MISSING:
        # 已提交阶段：包缺失 = 清理已删除全部叶（幂等）。evidence 必须按 REMOVE
        # 意图收敛为空（只消费验证过的精确名）；类型/内容损坏保留现场（回归 H4）。
        converge_cleanup_evidence(journal, intent=CleanupEvidenceIntent.REMOVE)
        return
    if package_node is not EntryKind.DIRECTORY:
        raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, package_dir)

    evidence_path = _cleanup_evidence_path(journal)
    evidence_node = classify_node_or_error(
        evidence_path, code=CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path=evidence_path
    )
    if evidence_node not in (EntryKind.MISSING, EntryKind.FILE):
        raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, evidence_path)
    if evidence_node is EntryKind.FILE:
        # 上次清理中途失败：先从证据恢复缺失叶，使合法包完整可重建。
        _restore_missing_leaves_from_evidence(root, journal, package_dir)
    _precheck_package_tree(root, package_dir, authorized, package_dir)
    if evidence_node is EntryKind.MISSING:
        # 首次删除前：用锚定句柄读取存在的授权叶已验证字节，耐久写入证据；
        # 已缺失的授权叶记录为 already_missing。
        leaves_bytes: dict[Path, bytes] = {}
        for relative, _ in sorted(authorized):
            with anchored_leaf(
                root,
                relative,
                code=CheckCode.TRASH_PACKAGE_CORRUPT.value,
                code_path=package_dir,
            ) as leaf:
                if leaf.classify() is EntryKind.FILE:
                    leaves_bytes[relative] = leaf.read()
        _write_cleanup_evidence(journal, auth, leaves_bytes)
    for relative, digest in sorted(authorized):
        _remove_verified_leaf(root, relative, digest, package_dir)
    for directory in sorted(
        _package_directories(root, package_dir, authorized),
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        _remove_verified_dir(root, directory.relative_to(root), package_dir)
    # 包目录全部成功移除后：evidence 按 REMOVE 意图收敛为空。
    converge_cleanup_evidence(journal, intent=CleanupEvidenceIntent.REMOVE)
