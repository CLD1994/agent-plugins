"""权威删除包的严格解析：以原始回收日志 T 为信任根。

清单（package.json）自报字段不是信任根：包目录固定从 ``T.operation_id`` 派生，
``package.json`` 与 payload 的哈希必须先锚定到 ``T.mutations``，之后清单字段才允许参与解析。
在任何清单控制路径触达目标 I/O 之前，先完成规范化边界与符号链接检查。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import cast

from draft.diagnostic_codes import CheckCode
from draft.errors import DraftError
from draft.graph import INTERNAL_DIR
from draft.models import MutationKind, OperationKind
from draft.observations import EntryKind, ObservationManifest
from draft.operations import (
    Journal,
    JournalMutation,
    anchored_leaf,
    classify_node,
    durable_write,
    fsync_directory,
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
        leaf.unlink()


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


_CLEANUP_EVIDENCE_VERSION = 1
_CLEANUP_EVIDENCE_NAME = "cleanup-evidence.json"


def _cleanup_evidence_path(journal: Journal) -> Path:
    """清理证据路径：锁所属操作目录内，包目录全部成功移除前不得清理。"""
    return journal.directory / _CLEANUP_EVIDENCE_NAME


def _write_cleanup_evidence(
    journal: Journal,
    auth: PackageAuthorization,
    leaves_bytes: dict[Path, bytes],
) -> None:
    """删除第一个权威叶节点前，把清理开始时存在的授权叶字节耐久记录在锁所属操作目录。

    只保存实际存在的叶（post-commit 部分缺失的叶不在其中）；该证据在包目录全部
    成功移除前不得清理：删除中途出现 extra/类型变化时，恢复用它重建完整合法包，
    绝不只剩未知内容（回归 H2）。
    """
    import base64

    entries: list[dict[str, str]] = []
    for relative in (auth.package_json_path, auth.payload_path):
        content = leaves_bytes.get(relative)
        if content is not None:
            entries.append(
                {
                    "relative": relative.as_posix(),
                    "sha256": sha256_bytes(content),
                    "b64": base64.b64encode(content).decode("ascii"),
                }
            )
    if not entries:
        return  # 清理开始时已无任何合法叶：无可保留证据，直接幂等清理空目录。
    payload = {"version": _CLEANUP_EVIDENCE_VERSION, "leaves": entries}
    durable_write(_cleanup_evidence_path(journal), (json.dumps(payload) + "\n").encode("utf-8"))


def _clear_cleanup_evidence(journal: Journal) -> None:
    """清理成功后移除证据；只删除本文件，不触碰其他内容。"""
    path = _cleanup_evidence_path(journal)
    if path.is_file():
        path.unlink()
    fsync_directory(path.parent)


def _restore_missing_leaves_from_evidence(root: Path, journal: Journal, code_path: Path) -> None:
    """从证据恢复缺失授权叶：上次清理中途失败后使合法包完整可重建。

    只恢复 classify 为 MISSING 的授权叶，且证据叶的 relative/sha256 必须与日志授权
    一致；现存叶保持不动（类型/摘要问题由预检拒绝）。恢复写入与验证/删除共用锚定
    句柄，不按绝对路径重新解析。
    """
    import base64

    auth = parse_package_authorization(journal, code_path)
    expected = {auth.package_json_path, auth.payload_path}
    evidence = _cleanup_evidence_path(journal)
    try:
        data = json.loads(evidence.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path) from exc
    if not isinstance(data, dict):
        raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path)
    parsed = cast("dict[str, object]", data)
    if parsed.get("version") != _CLEANUP_EVIDENCE_VERSION:
        raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path)
    entries = parsed.get("leaves")
    if not isinstance(entries, list):
        raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path)
    for entry in cast("list[object]", entries):
        if not isinstance(entry, dict):
            raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path)
        item = cast("dict[str, object]", entry)
        relative_raw = item.get("relative")
        digest = item.get("sha256")
        b64 = item.get("b64")
        if (
            not isinstance(relative_raw, str)
            or not isinstance(digest, str)
            or not isinstance(b64, str)
        ):
            raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path)
        relative = Path(relative_raw)
        if relative not in expected:
            raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path)
        content = base64.b64decode(b64)
        if sha256_bytes(content) != digest:
            raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path)
        with anchored_leaf(
            root, relative, code=CheckCode.TRASH_PACKAGE_CORRUPT.value, code_path=code_path
        ) as leaf:
            if leaf.classify() is EntryKind.MISSING:
                leaf.write(content)


def clean_authoritative_package(root: Path, trash_journal: Journal) -> None:
    """唯一权威包清理原语：日志验证 → 节点分类 → 证据事务 → 锚定删除。

    清理计划从原 T 日志派生恰好两个授权叶节点（package.json + payload）；预检枚举
    整个包树，任何未知成员、符号链接、错误类型或摘要不符都在第一次 unlink 之前
    失败。首次删除前把两个合法叶的字节耐久写入锁所属操作目录（回归 H2），删除
    中途出现 extra/类型变化时证据保留、下次恢复先重建完整合法包再收敛；包目录
    全部成功移除后才清除证据。删除阶段全部经锚定句柄（回归 H1）。

    restore、handoff、purge 与终态恢复全部经此原语清理，不再各自直接 remove_tree。
    """
    reject_symlink_components(root, OUTGOING_RELATIVE)
    package_dir = root / OUTGOING_RELATIVE / trash_journal.operation_id
    # 先验证日志协议再碰包：物理包缺失只是文件系统状态，不能跳过日志语义验证
    # （回归 H6）——空 mutation、缺 package write、第三个包内 WRITE 都必须报损坏。
    auth = parse_package_authorization(trash_journal, package_dir)
    authorized = list(auth.leaves())
    # lstat 分类：悬空符号链接是 link 不是 missing，不得折叠成幂等缺失（回归 M2）。
    package_node = classify_node(package_dir)
    if package_node is EntryKind.MISSING:
        # 证据存在说明上次清理已删除全部叶：清除证据，幂等完成；缺失且无证据为
        # 已提交消费，同样无操作。
        _clear_cleanup_evidence(trash_journal)
        return
    if package_node is not EntryKind.DIRECTORY:
        raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, package_dir)

    evidence_present = _cleanup_evidence_path(trash_journal).is_file()
    if evidence_present:
        # 上次清理中途失败：先从证据恢复缺失叶，使合法包完整可重建。
        _restore_missing_leaves_from_evidence(root, trash_journal, package_dir)
    _precheck_package_tree(root, package_dir, authorized, package_dir)
    if not evidence_present:
        # 首次删除前：用锚定句柄读取存在的授权叶已验证字节，耐久写入证据。
        # post-commit 已缺失的叶不在其中；叶类型已变化时跳过（删除阶段会拒绝）。
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
        _write_cleanup_evidence(trash_journal, auth, leaves_bytes)
    for relative, digest in sorted(authorized):
        _remove_verified_leaf(root, relative, digest, package_dir)
    for directory in sorted(
        _package_directories(root, package_dir, authorized),
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        _remove_verified_dir(root, directory.relative_to(root), package_dir)
    _clear_cleanup_evidence(trash_journal)
