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

import errno
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from draft.diagnostic_codes import CheckCode
from draft.errors import DraftError
from draft.link_index import (
    INDEX_RELATIVE_PATH,
    DirectoryMember,
    IndexedFile,
    LinkIndex,
    LinkIndexScan,
    build_index_content,
)
from draft.models import LinkIndexResult, ResultWarning
from draft.observations import EntryKind
from draft.operations import (
    OPERATIONS_RELATIVE,
    Journal,
    classify_node_or_error,
    durable_write,
    ensure_directory_chain,
    fsync_directory,
    operation_journal_path,
    read_journal,
    reject_symlink_components,
    sha256_bytes,
    validate_link_index_revision,
    validate_operation_id,
)

_INDEX_UPDATE_FAILED = "operation.index_update_failed"
_SCAN_READ_FAILED = _INDEX_UPDATE_FAILED
_EXTERNAL_CHANGE = "operation.external_change"
_SCAN_UNSTABLE = _EXTERNAL_CHANGE
_INCONSISTENT = CheckCode.OPERATION_INCONSISTENT_STATE.value

# 候选制品精确文件名（operation 目录内，spec §2）：operation ID 唯一推导。
LINK_INDEX_STAGED_FILE_NAME = "link-index.staged.json"
# rebuild-required 纯存在性 flag 精确相对路径（spec §2/§10）。
REBUILD_FLAG_RELATIVE_PATH = Path(".draft") / "link-index-rebuild-required.flag"
# flag 普通 I/O 失败的稳定 warning code（spec §11）。
_REBUILD_FLAG_UPDATE_FAILED = "link_index.rebuild_flag_update_failed"


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
    known_documents: tuple[IndexedFile, ...] = (),
) -> LinkIndexScan:
    """严格扫描根内 Markdown 与目录事实。

    - 排除整个 ``.draft/``；
    - 记录目录直接成员名称与节点类型（no-follow lstat 分类）；
    - 只读取普通 ``.md`` 文件，不跟随符号链接；
    - 每个覆盖文件读取正文并计算 SHA-256（inode/size/mtime 只作进程内变化探针）；
    - 不可读子目录使遍历失败（``os.walk onerror`` → ``operation.index_update_failed``），
      不静默跳过、不产生完整候选（spec §8）；
    - 扫描结束后二次观察目录与文件身份，发现变化返回 ``operation.external_change``，
      不产生完整候选；
    - 向 ``build_index_content``（纯增量）传递 bytes；``known_documents`` 只在
      path+sha256 匹配时复用已提取 links，不跳过正文读取或摘要比较。
    """
    documents: dict[Path, bytes] = {}
    directories: dict[Path, tuple[DirectoryMember, ...]] = {}
    identities: dict[Path, tuple[int, int, int, int]] = {}

    for current_root, dirnames, filenames in os.walk(root, onerror=_scan_walk_error):
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
            except FileNotFoundError:
                # 文件在 walk 与 lstat 之间被删除：根在扫描期间变化，不稳定扫描。
                raise DraftError(_SCAN_UNSTABLE, root) from None
            except OSError as exc:
                raise DraftError(_SCAN_READ_FAILED, relative) from exc
            if not stat.S_ISREG(st.st_mode):
                continue  # 符号链接或非普通 .md 节点不读取（spec §8.3 no-follow）
            try:
                raw = read_file_no_follow(path, relative)
            except FileNotFoundError:
                # 文件在 lstat 与读取之间被删除：不稳定扫描。
                raise DraftError(_SCAN_UNSTABLE, root) from None
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


def _scan_walk_error(error: OSError) -> None:
    """``os.walk`` 遍历到不可读目录时把扫描标记为失败，不静默跳过（spec §8）。"""
    filename = error.filename or "."
    raise DraftError(_SCAN_READ_FAILED, Path(filename)) from error


def read_file_no_follow(path: Path, relative: Path) -> bytes:
    """no-follow 读取普通文件正文。

    文件在 walk 与读取之间被删除时传播 ``FileNotFoundError``，由扫描调用方映射为
    不稳定扫描（``operation.external_change``）；其余 OSError（如 EACCES）映射为
    扫描失败（``operation.index_update_failed``）。
    """
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        raise  # 文件消失：不稳定扫描语义由调用方决定。
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

    def _verify_walk_error(error: OSError) -> None:
        # 首轮可读而二轮不可读：根在扫描期间变化，不稳定扫描。
        raise DraftError(_SCAN_UNSTABLE, root) from error

    seen_directories: dict[Path, tuple[DirectoryMember, ...]] = {}
    seen_files: set[Path] = set()
    for current_root, dirnames, filenames in os.walk(root, onerror=_verify_walk_error):
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


# ---------------------------------------------------------------------------
# 耐久存储：staging、目标原子发布与 rebuild flag（spec §10，Task 4）
# ---------------------------------------------------------------------------


def _reject_ancestor_symlinks(root: Path, relative: Path, *, code: str) -> None:
    """拒绝相对路径祖先中的符号链接（spec §10 no-follow 边界）。

    只检查叶节点之前的祖先组件（从根逐层 lstat）；祖先缺失时叶必然缺失，由后续
    分类边界处理。祖先为符号链接时按 ``code`` 报内部状态错误，程序不得跟随异常
    内部节点解析新路径。
    """
    cursor = root
    for part in relative.parts[:-1]:
        cursor = cursor / part
        try:
            mode = os.lstat(cursor).st_mode
        except FileNotFoundError:
            return
        if stat.S_ISLNK(mode):
            raise DraftError(code, cursor)


def _ensure_dot_draft(root: Path) -> None:
    """确保目标父目录 ``.draft/`` 存在并同步（发布前置，spec §10）。"""
    parent_fd = ensure_directory_chain(
        root,
        INDEX_RELATIVE_PATH.parent,
        code=_INDEX_UPDATE_FAILED,
        code_path=root / INDEX_RELATIVE_PATH.parent,
    )
    os.close(parent_fd)


def _read_artifact_bytes(path: Path) -> bytes:
    """no-follow 读取索引制品/staging 正文（spec §10）。

    分类为普通文件后读取仍可能失败：ENOENT/ELOOP（节点消失或变成符号链接）是内部
    状态不一致（``operation.inconsistent_state``）；EACCES/EIO 是普通 I/O 失败
    （``operation.index_update_failed``，可降级 rebuild_required）。
    """
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        if exc.errno in (errno.ENOENT, errno.ELOOP):
            raise DraftError(_INCONSISTENT, path) from exc
        raise DraftError(_INDEX_UPDATE_FAILED, path) from exc
    try:
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1 << 16)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    except OSError as exc:
        raise DraftError(_INDEX_UPDATE_FAILED, path) from exc
    finally:
        os.close(descriptor)


def _regular_flag_identity(parent_fd: int, name: str, path: Path) -> tuple[int, int] | None:
    """dir_fd no-follow 分类 flag 叶：普通文件返回 ``(st_ino, st_dev)``，缺失返回 ``None``。

    与 ``operations._name_node`` 同一模式：删除前 inode 复核，绝不按名字删除未知
    节点。符号链接/目录/其他节点抛 ``operation.inconsistent_state``。
    """
    try:
        st = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(st.st_mode):
        raise DraftError(_INCONSISTENT, path)
    return (st.st_ino, st.st_dev)


def stage_link_index(journal: Journal, index: LinkIndex) -> str:
    """把 ``index`` 确定序列化耐久写入 operation 专属 staging（spec §10）。

    staging 精确位于 ``journal.directory / "link-index.staged.json"``。流程 create-only
    （复用 ``operations.durable_write``）或对既有普通 staging 精确摘要验证后的幂等续写：
    相同摘要直接返回，不重复写入；摘要不符或异常节点报内部状态错误。写入/同步失败
    映射 ``operation.index_update_failed``（索引发布失败可降级为 rebuild_required，
    不是 journal 耐久失败）；``operation.external_change``（pending/target 瞬时出现）
    原样传播，不折叠为可降级码。
    """
    raw = index.to_json()
    digest = sha256_bytes(raw)
    staging = journal.directory / LINK_INDEX_STAGED_FILE_NAME
    root = journal.directory.parents[2]
    _reject_ancestor_symlinks(root, staging.relative_to(root), code=_INCONSISTENT)
    if (
        classify_node_or_error(journal.directory, code=_INCONSISTENT, code_path=journal.directory)
        is not EntryKind.DIRECTORY
    ):
        raise DraftError(_INCONSISTENT, journal.directory)
    node = classify_node_or_error(staging, code=_INCONSISTENT, code_path=staging)
    if node is EntryKind.FILE:
        if sha256_bytes(_read_artifact_bytes(staging)) != digest:
            raise DraftError(_INCONSISTENT, staging)
        return digest
    if node is not EntryKind.MISSING:
        raise DraftError(_INCONSISTENT, staging)
    try:
        durable_write(journal.directory, Path(LINK_INDEX_STAGED_FILE_NAME), raw)
    except DraftError as exc:
        if exc.code == _EXTERNAL_CHANGE:
            raise
        error = DraftError(_INDEX_UPDATE_FAILED, staging)
        error.__cause__ = exc
        raise error from exc
    return digest


def publish_staged_link_index(root: Path, journal: Journal, expected_sha256: str) -> None:
    """把 staging 原子发布为 ``.draft/link-index.json``（spec §10 决策表）。

    发布目标分类以最新权威状态（``read_link_index_authority``）为准：absent 只允许
    缺失目标首次发布；rebuild_required 允许缺失或普通旧文件替换；published 必须与
    journal 完全一致（SHA-256/revision/schema/反向表）才允许更高 revision 替换。
    目标/staging/祖先为符号链接、目录或其他异常节点时保留现场报内部状态错误。
    staging 摘要必须等于 ``expected_sha256``，且 staged revision 必须严格高于权威
    revision（单调）；否则为内部不一致。

    替换使用同文件系统原子 ``os.replace``（消费 staging），成功后分别同步目标父目录
    ``.draft/`` 与 operation 目录；替换或同步失败映射 ``operation.index_update_failed``。
    """
    _reject_ancestor_symlinks(root, INDEX_RELATIVE_PATH, code=_INCONSISTENT)
    target = root / INDEX_RELATIVE_PATH
    target_node = classify_node_or_error(target, code=_INCONSISTENT, code_path=target)

    staging = journal.directory / LINK_INDEX_STAGED_FILE_NAME
    _reject_ancestor_symlinks(root, staging.relative_to(root), code=_INCONSISTENT)
    staging_node = classify_node_or_error(staging, code=_INCONSISTENT, code_path=staging)
    if staging_node is not EntryKind.FILE:
        raise DraftError(_INCONSISTENT, staging)
    staged = _read_artifact_bytes(staging)
    if sha256_bytes(staged) != expected_sha256:
        raise DraftError(_INCONSISTENT, staging)
    try:
        staged_index = LinkIndex.from_json(staged)
    except (ValueError, UnicodeDecodeError) as exc:
        raise DraftError(_INCONSISTENT, staging) from exc

    authority = read_link_index_authority(root)
    # 单调校验：staged revision 必须严格高于权威 revision（spec §3/§10）。
    if authority.revision is not None and int(staged_index.revision) <= int(authority.revision):
        raise DraftError(_INCONSISTENT, staging)
    if authority.result is None:
        if target_node is not EntryKind.MISSING:
            raise DraftError(_INCONSISTENT, target)
    elif authority.result is LinkIndexResult.REBUILD_REQUIRED:
        if target_node not in (EntryKind.MISSING, EntryKind.FILE):
            raise DraftError(_INCONSISTENT, target)
    elif authority.result is LinkIndexResult.PUBLISHED:
        if target_node is not EntryKind.FILE:
            raise DraftError(_INCONSISTENT, target)
        existing = _read_artifact_bytes(target)
        if sha256_bytes(existing) != authority.sha256:
            raise DraftError(_INCONSISTENT, target)
        try:
            existing_index = LinkIndex.from_json(existing)
        except (ValueError, UnicodeDecodeError) as exc:
            raise DraftError(_INCONSISTENT, target) from exc
        if existing_index.revision != authority.revision:
            raise DraftError(_INCONSISTENT, target)
    else:
        raise DraftError(_INCONSISTENT, target)

    _ensure_dot_draft(root)
    try:
        os.replace(staging, target)
    except OSError as exc:
        raise DraftError(_INDEX_UPDATE_FAILED, target) from exc

    # 发布后复核：目标必须是我们的精确 staging 内容，不覆盖未知节点。
    if (
        classify_node_or_error(target, code=_INCONSISTENT, code_path=target) is not EntryKind.FILE
        or sha256_bytes(_read_artifact_bytes(target)) != expected_sha256
    ):
        raise DraftError(_INCONSISTENT, target)

    try:
        fsync_directory(target.parent)
        fsync_directory(journal.directory)
    except DraftError as exc:
        error = DraftError(_INDEX_UPDATE_FAILED, target)
        error.__cause__ = exc
        raise error from exc


def ensure_rebuild_flag(root: Path) -> ResultWarning | None:
    """尽力耐久创建零字节 rebuild-required flag（spec §10/§11）。

    flag 只表达存在性，不写 JSON 或原因文本。已存在普通文件视为幂等成功；叶节点为
    符号链接、目录或其他异常节点是内部状态错误（``operation.inconsistent_state``），
    祖先符号链接逃逸按 ``operations.reject_symlink_components`` 惯例报
    ``path.symlink_escape``。普通 I/O 失败（创建、目录同步）转换为
    ``link_index.rebuild_flag_update_failed`` warning 返回，不改变 journal 结果。
    """
    flag = root / REBUILD_FLAG_RELATIVE_PATH
    node = classify_node_or_error(flag, code=_INCONSISTENT, code_path=flag)
    if node is EntryKind.FILE:
        return None
    if node is not EntryKind.MISSING:
        raise DraftError(_INCONSISTENT, flag)
    reject_symlink_components(root, REBUILD_FLAG_RELATIVE_PATH)
    try:
        parent_fd = ensure_directory_chain(
            root,
            REBUILD_FLAG_RELATIVE_PATH.parent,
            code=CheckCode.OPERATION_DURABILITY_FAILED.value,
            code_path=flag.parent,
        )
    except DraftError:
        return ResultWarning(_REBUILD_FLAG_UPDATE_FAILED)
    try:
        try:
            descriptor = os.open(
                REBUILD_FLAG_RELATIVE_PATH.name,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                0o644,
                dir_fd=parent_fd,
            )
        except FileExistsError:
            # 分类与创建之间 flag 已出现：普通文件视为幂等存在，异常节点报不一致。
            reappeared = classify_node_or_error(flag, code=_INCONSISTENT, code_path=flag)
            if reappeared is EntryKind.FILE:
                return None
            raise DraftError(_INCONSISTENT, flag) from None
        os.close(descriptor)
        fsync_directory(flag.parent)
    except DraftError as exc:
        if exc.code == _INCONSISTENT:
            raise
        return ResultWarning(_REBUILD_FLAG_UPDATE_FAILED)
    except OSError:
        return ResultWarning(_REBUILD_FLAG_UPDATE_FAILED)
    finally:
        os.close(parent_fd)
    return None


def remove_rebuild_flag(root: Path) -> ResultWarning | None:
    """尽力耐久删除 rebuild-required flag（spec §10/§11）。

    删除前先 lstat 分类，只删除普通文件并同步 ``.draft/``；缺失视为幂等成功。叶节点
    为符号链接、目录或其他异常节点是内部状态错误（``operation.inconsistent_state``），
    祖先符号链接逃逸按 ``operations.reject_symlink_components`` 惯例报
    ``path.symlink_escape``。删除经锚定父目录 ``dir_fd`` 执行，并在 unlink 前复核叶
    仍是已分类普通文件的同一 inode（与 ``operations._same_name_node`` 同模式），
    绝不按名字删除未知节点。普通 I/O 失败（删除、目录同步）转换为
    ``link_index.rebuild_flag_update_failed`` warning 返回。
    """
    flag = root / REBUILD_FLAG_RELATIVE_PATH
    node = classify_node_or_error(flag, code=_INCONSISTENT, code_path=flag)
    if node is EntryKind.MISSING:
        return None
    if node is not EntryKind.FILE:
        raise DraftError(_INCONSISTENT, flag)
    reject_symlink_components(root, REBUILD_FLAG_RELATIVE_PATH)
    try:
        parent_fd = ensure_directory_chain(
            root,
            REBUILD_FLAG_RELATIVE_PATH.parent,
            code=CheckCode.OPERATION_DURABILITY_FAILED.value,
            code_path=flag.parent,
        )
    except DraftError:
        return ResultWarning(_REBUILD_FLAG_UPDATE_FAILED)
    try:
        # 删除前 inode 复核：叶仍是刚分类的同一普通文件（no-follow dir_fd）。
        identity = _regular_flag_identity(parent_fd, REBUILD_FLAG_RELATIVE_PATH.name, flag)
        if identity is None:
            return None
        if _regular_flag_identity(parent_fd, REBUILD_FLAG_RELATIVE_PATH.name, flag) != identity:
            raise DraftError(_INCONSISTENT, flag)
        os.unlink(REBUILD_FLAG_RELATIVE_PATH.name, dir_fd=parent_fd)
        fsync_directory(flag.parent)
    except FileNotFoundError:
        return None
    except DraftError as exc:
        if exc.code == _INCONSISTENT:
            raise
        return ResultWarning(_REBUILD_FLAG_UPDATE_FAILED)
    except OSError:
        return ResultWarning(_REBUILD_FLAG_UPDATE_FAILED)
    finally:
        os.close(parent_fd)
    return None


def cleanup_link_index_staging(journal: Journal) -> None:
    """幂等清理当前 operation 的 ``link-index.staged.json``（spec §12）。

    只处理精确 staging 路径；缺失视为已经收敛。符号链接、目录或其他异常节点是内部
    状态错误，保留现场不按文件名删除未知节点。
    """
    staging = journal.directory / LINK_INDEX_STAGED_FILE_NAME
    node = classify_node_or_error(staging, code=_INCONSISTENT, code_path=staging)
    if node is EntryKind.MISSING:
        return
    if node is not EntryKind.FILE:
        raise DraftError(_INCONSISTENT, staging)
    try:
        staging.unlink()
        fsync_directory(journal.directory)
    except OSError as exc:
        raise DraftError(CheckCode.OPERATION_DURABILITY_FAILED.value, staging) from exc
