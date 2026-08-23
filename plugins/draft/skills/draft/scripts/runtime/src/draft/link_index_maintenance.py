"""链接索引维护：journal authority 读取与严格 no-follow 扫描（spec §3/§8/§9）。

本模块只负责**读取权威状态**和**建立严格扫描候选**，不做耐久发布（Task 4）：
- ``select_link_index_authority``：从已解析 Journal 序列选出数值最大 revision 的权威结果；
- ``read_link_index_authority``：枚举 ``.draft/operations/*/journal.json`` 并解析（复用
  ``operations.read_journal``，不复制 schema）；
- ``load_link_index_baseline``：按 spec §9 决策表验证最新 ``published`` 制品；
- ``scan_link_index``：no-follow 严格扫描根内 Markdown 与目录事实，向 Task 2 纯增量
  计算传递 bytes。
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from draft.diagnostic_codes import CheckCode
from draft.errors import DraftError
from draft.link_index import (
    INDEX_RELATIVE_PATH,
    DirectoryMember,
    LinkIndex,
    LinkIndexScan,
    build_index_content,
)
from draft.models import LinkIndexResult
from draft.observations import EntryKind
from draft.operations import (
    OPERATIONS_RELATIVE,
    Journal,
    classify_node_or_error,
    operation_journal_path,
    read_journal,
    sha256_bytes,
    validate_link_index_revision,
    validate_operation_id,
)

_SCAN_READ_FAILED = "operation.index_update_failed"
_SCAN_UNSTABLE = "operation.external_change"


@dataclass(frozen=True)
class LinkIndexAuthority:
    """最新权威链接索引结果（spec §2）：journal 是唯一权威来源。"""

    revision: str | None
    result: LinkIndexResult | None
    sha256: str | None


@dataclass(frozen=True)
class LinkIndexBaseline:
    """可信增量基线（spec §9 决策表）：published 时携带已验证制品。"""

    authority: LinkIndexAuthority
    index: LinkIndex | None


def select_link_index_authority(journals: tuple[Journal, ...]) -> LinkIndexAuthority:
    """从已解析 Journal 序列选出数值最大 revision 的权威结果（spec §3）。

    只收集携带 ``link_index_result`` 的合法结果记录；非结果记录（如
    ``link_index_updating``、无索引字段的终态）忽略。revision 按任意精度整数比较，
    不按字符串字典序。重复 revision 的相同结果接受，冲突结果报内部状态错误。
    """
    best: tuple[int, LinkIndexAuthority] | None = None
    seen: dict[str, tuple[LinkIndexResult, str | None]] = {}
    for journal in journals:
        if journal.link_index_revision is not None:
            validate_link_index_revision(journal.link_index_revision, path=journal.directory)
        if journal.link_index_result is None:
            continue  # 非结果记录不参与 authority
        if journal.link_index_revision is None:
            raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, journal.directory)
        revision = journal.link_index_revision
        this = (journal.link_index_result, journal.link_index_sha256)
        previous = seen.get(revision)
        if previous is not None:
            if previous != this:
                raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, journal.directory)
            continue  # 同一 revision 相同结果：接受，已记录。
        seen[revision] = this
        numeric = int(revision)
        if best is None or numeric > best[0]:
            best = (
                numeric,
                LinkIndexAuthority(revision, journal.link_index_result, journal.link_index_sha256),
            )
    if best is None:
        return LinkIndexAuthority(None, None, None)
    return best[1]


def read_link_index_authority(root: Path) -> LinkIndexAuthority:
    """枚举 ``.draft/operations/*/journal.json`` 读取最新权威结果。

    只接受规范 operation ID 目录；解析由 ``operations.read_journal`` 完成（只支持
    journal v4，不迁移、不双读）。非 v4 或损坏 journal 按既有领域错误传播。
    """
    operations_dir = root / OPERATIONS_RELATIVE
    node = classify_node_or_error(
        operations_dir,
        code=CheckCode.OPERATION_JOURNAL_CORRUPT.value,
        code_path=operations_dir,
    )
    if node is EntryKind.MISSING:
        return LinkIndexAuthority(None, None, None)
    if node is not EntryKind.DIRECTORY:
        raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, operations_dir)
    try:
        with os.scandir(operations_dir) as iterator:
            entries = sorted(iterator, key=lambda entry: entry.name)
    except OSError as exc:
        raise DraftError(CheckCode.OPERATION_JOURNAL_CORRUPT.value, operations_dir) from exc
    journals: list[Journal] = []
    for entry in entries:
        child = Path(entry.path)
        if classify_node_or_error(
            child,
            code=CheckCode.OPERATION_INCONSISTENT_STATE.value,
            code_path=child,
        ) is not EntryKind.DIRECTORY:
            continue  # 只接受目录；符号链接/普通文件不参与 authority。
        try:
            validate_operation_id(entry.name)
        except DraftError:
            continue  # 非规范 operation ID 目录不参与 authority。
        journals.append(read_journal(operation_journal_path(root, entry.name)))
    return select_link_index_authority(tuple(journals))


def load_link_index_baseline(root: Path) -> LinkIndexBaseline:
    """按 spec §9 决策表读取可信增量基线。

    最新结果为 ``rebuild_required`` 或 absent 时不读取制品。最新 ``published`` 必须
    验证制品为普通文件、制品 SHA-256、revision、schema v2 与反向表一致；任何不匹配
    都报 ``operation.inconsistent_state``，不进入增量路径。
    """
    authority = read_link_index_authority(root)
    if authority.result is not LinkIndexResult.PUBLISHED:
        return LinkIndexBaseline(authority=authority, index=None)
    artifact = root / INDEX_RELATIVE_PATH
    node = classify_node_or_error(
        artifact,
        code=CheckCode.OPERATION_INCONSISTENT_STATE.value,
        code_path=artifact,
    )
    if node is not EntryKind.FILE:
        raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, artifact)
    raw = _read_no_follow(artifact, CheckCode.OPERATION_INCONSISTENT_STATE.value)
    if sha256_bytes(raw) != authority.sha256:
        raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, artifact)
    try:
        index = LinkIndex.from_json(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, artifact) from exc
    if index.revision != authority.revision:
        raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, artifact)
    return LinkIndexBaseline(authority=authority, index=index)


def next_link_index_revision(authority: LinkIndexAuthority) -> str:
    """absent 或无法递增时返回 ``"1"``；否则对当前 revision 数值 +1 返回规范十进制串。"""
    if authority.revision is None:
        return "1"
    return str(int(authority.revision) + 1)


# ---------------------------------------------------------------------------
# 严格 no-follow 扫描（spec §8）
# ---------------------------------------------------------------------------


def scan_link_index(
    root: Path,
    baseline: LinkIndexBaseline | None,
    known_documents: tuple[object, ...] = (),
) -> LinkIndexScan:
    """严格扫描根内 Markdown 与目录事实。

    - 排除整个 ``.draft/``；
    - 记录目录直接成员名称与节点类型（no-follow lstat 分类）；
    - 只读取普通 ``.md`` 文件，不跟随符号链接；
    - 每个覆盖文件读取正文并计算 SHA-256（inode/size/mtime 只作进程内变化探针）；
    - 扫描结束后二次观察目录与文件身份，发现变化返回 ``operation.external_change``，
      不产生完整候选；
    - 向 ``build_index_content``（纯增量）传递 bytes；``known_documents`` 只在
      path+sha256 匹配时复用已提取 links，不跳过正文读取或摘要比较。
    """
    documents: dict[Path, bytes] = {}
    directories: dict[Path, tuple[DirectoryMember, ...]] = {}
    identities: dict[Path, tuple[int, int, int, int]] = {}

    for current_root, dirnames, filenames in os.walk(root):
        directory_path = Path(current_root)
        relative_dir = directory_path.relative_to(root)
        if relative_dir == Path(".draft") or relative_dir.parts[:1] == (".draft",):
            dirnames[:] = []
            continue
        members = _list_directory_members(directory_path)
        if relative_dir == Path("."):
            members = tuple(member for member in members if member.name != ".draft")
        directories[relative_dir] = members
        for name in filenames:
            if not name.endswith(".md"):
                continue
            path = directory_path / name
            relative = path.relative_to(root)
            try:
                st = os.lstat(path)
            except OSError as exc:
                raise DraftError(_SCAN_READ_FAILED, relative) from exc
            if not stat.S_ISREG(st.st_mode):
                continue  # 符号链接或非普通 .md 节点不读取（spec §8.3 no-follow）
            try:
                raw = read_file_no_follow(path, relative)
            except OSError as exc:
                raise DraftError(_SCAN_READ_FAILED, relative) from exc
            documents[relative] = raw
            identities[relative] = (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)

    _verify_root_stable(root, directories, identities)

    baseline_index = baseline.index if baseline is not None else None
    try:
        return build_index_content(
            documents,
            directories,
            baseline_index,
            root=root,
            known_documents=known_documents,
        )
    except (UnicodeDecodeError, DraftError) as exc:
        # 任一覆盖 Markdown 无法按 UTF-8 解码或提取 CommonMark 链接：不发布完整索引。
        raise DraftError(_SCAN_READ_FAILED, root) from exc


def _list_directory_members(path: Path) -> tuple[DirectoryMember, ...]:
    members: list[DirectoryMember] = []
    try:
        with os.scandir(path) as iterator:
            entries = list(iterator)
    except OSError as exc:
        raise DraftError(_SCAN_READ_FAILED, path) from exc
    for entry in entries:
        node = classify_node_or_error(
            Path(entry.path),
            code=_SCAN_READ_FAILED,
            code_path=path,
        )
        members.append(DirectoryMember(entry.name, node.value))
    return tuple(sorted(members, key=lambda member: (member.name, member.kind)))


def read_file_no_follow(path: Path, relative: Path) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise DraftError(_SCAN_READ_FAILED, relative) from exc
    try:
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1 << 16)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    except OSError as exc:
        raise DraftError(_SCAN_READ_FAILED, relative) from exc
    finally:
        os.close(descriptor)


def _read_no_follow(path: Path, code: str) -> bytes:
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


def _verify_root_stable(
    root: Path,
    expected_directories: dict[Path, tuple[DirectoryMember, ...]],
    expected_identities: dict[Path, tuple[int, int, int, int]],
) -> None:
    """二次观察：目录成员与文件身份在扫描期间变化则拒绝产生完整候选（spec §1）。"""
    seen_directories: dict[Path, tuple[DirectoryMember, ...]] = {}
    seen_files: set[Path] = set()
    for current_root, dirnames, filenames in os.walk(root):
        directory_path = Path(current_root)
        relative_dir = directory_path.relative_to(root)
        if relative_dir == Path(".draft") or relative_dir.parts[:1] == (".draft",):
            dirnames[:] = []
            continue
        members = _list_directory_members(directory_path)
        if relative_dir == Path("."):
            members = tuple(member for member in members if member.name != ".draft")
        seen_directories[relative_dir] = members
        for name in filenames:
            if not name.endswith(".md"):
                continue
            path = directory_path / name
            relative = path.relative_to(root)
            try:
                st = os.lstat(path)
            except OSError as exc:
                raise DraftError(_SCAN_UNSTABLE, root) from exc
            if not stat.S_ISREG(st.st_mode):
                continue
            seen_files.add(relative)
            expected = expected_identities.get(relative)
            identity = (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)
            if expected is None or identity != expected:
                raise DraftError(_SCAN_UNSTABLE, root)
    if seen_directories != expected_directories or seen_files != set(expected_identities):
        raise DraftError(_SCAN_UNSTABLE, root)
