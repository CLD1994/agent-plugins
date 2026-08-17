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
from draft.operations import (
    Journal,
    JournalMutation,
    reject_symlink_components,
    sha256_bytes,
    sha256_file,
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


def _parse_manifest(manifest_path: Path) -> dict[str, object]:
    try:
        decoded = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, manifest_path) from exc
    if not isinstance(decoded, dict):
        raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, manifest_path)
    data = cast("dict[str, object]", decoded)
    if set(data) != set(_MANIFEST_FIELDS):
        raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, manifest_path)
    version = data["version"]
    if isinstance(version, bool) or version != PACKAGE_VERSION:
        raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, manifest_path)
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

    reject_symlink_components(root, OUTGOING_RELATIVE)
    _reject_symlink(package_dir)
    manifest_path = package_dir / "package.json"
    _reject_symlink(manifest_path)
    _require_regular_file(manifest_path, manifest_path)
    manifest_mutation = require_journal_write(trash_journal, package_relative / "package.json")
    if sha256_file(manifest_path) != manifest_mutation.after_sha256:
        raise DraftError(CheckCode.TRASH_PACKAGE_CORRUPT.value, manifest_path)

    data = _parse_manifest(manifest_path)
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
    )
