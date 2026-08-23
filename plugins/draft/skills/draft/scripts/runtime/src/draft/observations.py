"""操作输入观察：捕获并复核计划实际读取的根内事实。

候选/正式写命令的计划器在锁外读取文件与目录成员集合，操作日志把这些观察作为
"读集证据"持久化；执行器在锁内、首次业务变更前复核观察，发现未改输入或目录
成员发生并发变化时不应用 mutation（operation.external_change）。

观察捕获复杂度必须为 O(观察文件总字节数 + 目录条目数)，不得在逐页循环中重复
全根扫描或再次读取同一文件；已经由图/页面加载器读取的 bytes 直接交给 builder
计算摘要，不为捕获观察而二次读取文件。
"""

from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from draft.errors import DraftError

if TYPE_CHECKING:
    from draft.graph import DraftGraph, MarkerSnapshot
    from draft.models import PageDocument


class _MutationLike(Protocol):
    @property
    def path(self) -> Path: ...


class _GraphLike(Protocol):
    @property
    def root(self) -> Path: ...

    @property
    def pages(self) -> Mapping[Path, PageDocument]: ...


class EntryKind(StrEnum):
    MISSING = "missing"
    FILE = "file"
    DIRECTORY = "directory"
    SYMLINK = "symlink"
    OTHER = "other"


def classify_node(path: Path) -> EntryKind:
    """lstat 节点分类：missing/普通文件/目录/符号链接/其他（round-1 L4 下沉到 EntryKind 旁）。

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
class Observation:
    """根内相对路径的类型、摘要与目录成员集合；``kind`` 决定其他字段含义。"""

    path: Path
    kind: EntryKind
    sha256: str | None = None
    members: tuple[str, ...] = ()
    member_types: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ObservationManifest:
    entries: tuple[Observation, ...]

    def for_path(self, path: Path) -> Observation | None:
        for entry in self.entries:
            if entry.path == path:
                return entry
        return None


EMPTY_OBSERVATIONS = ObservationManifest(entries=())


def merge_observations(*manifests: ObservationManifest) -> ObservationManifest:
    """合并读集；同一路径只能出现相同事实，冲突不得静默覆盖。"""
    if manifests and all(not manifest.entries for manifest in manifests):
        return manifests[0]
    by_path: dict[Path, Observation] = {}
    for manifest in manifests:
        for entry in manifest.entries:
            previous = by_path.get(entry.path)
            if previous is not None and previous != entry:
                if (
                    previous.path == entry.path
                    and previous.kind is entry.kind
                    and previous.sha256 == entry.sha256
                    and previous.members == entry.members
                    and (not previous.member_types or not entry.member_types)
                ):
                    by_path[entry.path] = entry if entry.member_types else previous
                    continue
                raise DraftError("operation.plan_invalid", entry.path)
            by_path[entry.path] = entry
    return ObservationManifest(
        entries=tuple(by_path[path] for path in sorted(by_path, key=lambda item: item.as_posix()))
    )


def file_observation(path: Path, data: bytes) -> Observation:
    from draft.operations import sha256_bytes

    return Observation(path=path, kind=EntryKind.FILE, sha256=sha256_bytes(data))


def file_digest_observation(path: Path, sha256: str) -> Observation:
    return Observation(path=path, kind=EntryKind.FILE, sha256=sha256)


def marker_observation(snapshot: MarkerSnapshot) -> Observation:
    """由 snapshot 直接生成 marker 观察：摘要来自同一次读取的 raw_bytes（round-15 H1）。

    ``graph_observations`` 与 adopt 的合并 manifest 从调用方持有的 snapshot 生成
    marker 观察，不访问 marker 文件系统路径；同一计划的 state/bytes/digest 与观察
    摘要同源。调用方必须持有 snapshot，不得回退为读取 live 文件。
    """
    from draft.graph import MARKER_PATH

    return file_digest_observation(MARKER_PATH, snapshot.sha256)


def directory_observation(
    path: Path, members: tuple[str, ...], member_types: tuple[tuple[str, str], ...] = ()
) -> Observation:
    return Observation(
        path=path,
        kind=EntryKind.DIRECTORY,
        members=tuple(sorted(members)),
        member_types=tuple(sorted(member_types)),
    )


def missing_observation(path: Path) -> Observation:
    return Observation(path=path, kind=EntryKind.MISSING)


def observation_shape_error(entry: Observation) -> str | None:
    """返回非法 kind/字段组合的说明；合法组合返回 None。

    FILE 必须携带摘要且无成员；DIRECTORY 不得携带摘要（成员可为空）；其余种类
    不得携带摘要或成员。这是日志观察清单的闭合校验（规格 §5.4、实施计划 Task 7
    Step 2），由日志读取边界拒绝非法形状。
    """
    if entry.kind is EntryKind.FILE:
        if entry.sha256 is None or entry.members:
            return "file must carry sha256 and no members"
    elif entry.kind is EntryKind.DIRECTORY:
        if entry.sha256 is not None:
            return "directory must not carry sha256"
    else:
        if entry.sha256 is not None or entry.members:
            return f"{entry.kind.value} must carry neither sha256 nor members"
    return None


class ObservationProbe(Protocol):
    """受真实文件系统边界约束的观察探针：单元 fake 与生产实现共享同一协议。"""

    def observe(self, path: Path) -> Observation: ...


class FilesystemObservationProbe:
    """生产实现：直接访问文件系统，把根内相对路径解析为当前观察状态。"""

    def __init__(self, root: Path) -> None:
        self.root = root

    def observe(self, path: Path) -> Observation:
        from draft.operations import sha256_bytes

        target = self.root / path
        if target.is_symlink():
            return Observation(path=path, kind=EntryKind.SYMLINK)
        if not target.exists():
            return Observation(path=path, kind=EntryKind.MISSING)
        if target.is_dir():
            return Observation(
                path=path,
                kind=EntryKind.DIRECTORY,
                members=tuple(sorted(entry.name for entry in target.iterdir())),
                member_types=tuple(
                    sorted(
                        (
                            entry.name,
                            "directory"
                            if entry.is_dir()
                            else "file"
                            if entry.is_file()
                            else "symlink"
                            if entry.is_symlink()
                            else "other",
                        )
                        for entry in target.iterdir()
                    )
                ),
            )
        if target.is_file():
            return Observation(
                path=path, kind=EntryKind.FILE, sha256=sha256_bytes(target.read_bytes())
            )
        return Observation(path=path, kind=EntryKind.OTHER)


def verify_observations(
    probe: ObservationProbe,
    manifest: ObservationManifest,
    *,
    explained_mutations: tuple[_MutationLike, ...] = (),
) -> tuple[Path, ...]:
    """锁内复核观察清单，返回与当前事实不一致的路径。

    ``explained_mutations`` 是本操作自己将应用的变更（其路径不算外部变化），
    恢复时用当前 mutation 的 before/after 分类作为可解释变化。
    """
    explained = {mutation.path for mutation in explained_mutations}
    changed: list[Path] = []
    for entry in manifest.entries:
        if entry.path in explained:
            continue
        current = probe.observe(entry.path)
        if entry.kind is EntryKind.DIRECTORY and not entry.member_types:
            current = Observation(
                path=current.path,
                kind=current.kind,
                sha256=current.sha256,
                members=current.members,
            )
        if current != entry:
            changed.append(entry.path)
    return tuple(sorted(changed))


def graph_observations(graph: _GraphLike, *, marker: MarkerSnapshot) -> ObservationManifest:
    """从已加载图构建读集观察：页面摘要复用已解析文本，目录成员各枚举一次。

    ``graph`` 需提供 ``root`` 与 ``pages``（path → 带 ``text`` 的页面对象）。marker
    观察由调用方持有的 snapshot 直接生成（round-15 H1）：不访问 marker 文件系统路径，
    同一计划的 state/bytes/digest 与观察摘要同源。
    复杂度 O(页面字节总数 + 受管理目录条目数)。
    """
    from draft.graph import INTERNAL_DIR, PAGES_DIR
    from draft.operations import sha256_bytes

    entries: list[Observation] = []
    directories: set[Path] = set()
    for relative, page in graph.pages.items():
        # 摘要基于加载文本（保留 \r\n，编码即磁盘字节）；内存合成图与磁盘图同式。
        digest_provider = getattr(graph, "page_digest", None)
        digest = (
            digest_provider(relative)
            if digest_provider is not None
            else sha256_bytes(page.text.encode("utf-8"))
        )
        entries.append(file_digest_observation(relative, digest))
        parent = relative.parent
        while str(parent) != "." and str(parent) != "":
            directories.add(parent)
            parent = parent.parent
    directories.add(Path("."))
    # 受管理命名空间是 pages/ 整棵子树：必须观察全部目录成员（含此前为空的目录），
    # 才能证明 plan→execute 之间没有向任意受管理目录新增可能引用目标的页面
    # （计划 Task 10 Step 2），不能只从成功解析页面的祖先反推目录。
    pages_root = graph.root / PAGES_DIR
    if pages_root.is_dir() and not pages_root.is_symlink():
        for dirpath, _dirnames, _filenames in os.walk(pages_root):
            directories.add(Path(dirpath).relative_to(graph.root))
    root = graph.root
    for directory in sorted(directories):
        target = root / directory
        child_entries = (
            tuple(target.iterdir()) if target.is_dir() and not target.is_symlink() else ()
        )
        members = tuple(sorted(entry.name for entry in child_entries))
        member_types = tuple(
            sorted(
                (
                    entry.name,
                    "directory"
                    if entry.is_dir()
                    else "file"
                    if entry.is_file()
                    else "symlink"
                    if entry.is_symlink()
                    else "other",
                )
                for entry in child_entries
            )
        )
        entries.append(directory_observation(directory, members, member_types))
    # marker 观察由调用方持有的 snapshot 直接生成，不访问 marker 文件系统路径
    # （round-15 H1）；同一计划的 state/bytes/digest 与观察摘要同源。
    entries.append(marker_observation(marker))
    # 内部命名空间目录基线：操作会在其中按需创建 operations/operation.lock，
    # 恢复时据此可证明首次创建的 .draft/operations 等在计划期缺失（与 pages
    # 子树对称，只观察成员，不逐个观察内部文件）。
    internal = root / INTERNAL_DIR
    if internal.is_dir() and not internal.is_symlink():
        members = tuple(sorted(entry.name for entry in internal.iterdir()))
        entries.append(directory_observation(Path(INTERNAL_DIR), members))
    return ObservationManifest(entries=tuple(entries))


def internal_protocol_observations(graph: DraftGraph) -> ObservationManifest:
    """adopt 的额外观察：既有内部协议文件、目录成员与根内资源目标（规格 §12.1）。

    只观察本函数调用时已存在的 .draft/ 内部文件（操作日志、恢复副本、权威删除包、
    锁等）与根内本地资源目标；adopt 随后自己创建的操作目录与锁由恢复复核按可解释
    变化处理。复杂度 O(内部文件字节数 + 目录条目数)。
    """
    from draft.graph import INTERNAL_DIR, MARKER_PATH
    from draft.operations import sha256_bytes
    from draft.resources import ResourceKind, ResourceScope, TargetState

    entries: list[Observation] = []
    directories: set[Path] = set()
    internal = graph.root / INTERNAL_DIR
    if internal.is_dir() and not internal.is_symlink():
        for dirpath, _dirnames, filenames in os.walk(internal):
            relative_dir = Path(dirpath).relative_to(graph.root)
            directories.add(relative_dir)
            for name in sorted(filenames):
                relative = relative_dir / name
                if relative == MARKER_PATH:
                    # marker 由调用方持有的 snapshot 显式合并，不在此重读（round-15 H1）。
                    continue
                target = graph.root / relative
                if target.is_file() and not target.is_symlink():
                    entries.append(
                        file_digest_observation(relative, sha256_bytes(target.read_bytes()))
                    )
    # 完整检查实际读到的根内本地资源目标（页面/本地文件，present）。
    for references in graph.body_resources.values():
        for reference in references:
            resource = reference.resource
            if resource.target_state is not TargetState.PRESENT:
                continue
            if resource.kind is ResourceKind.PAGE or (
                resource.kind is ResourceKind.LOCAL and resource.scope is ResourceScope.DRAFT
            ):
                relative = Path(resource.target)
            else:
                continue
            if relative.is_absolute() or ".." in relative.parts:
                continue
            if relative in graph.pages:
                continue
            target = graph.root / relative
            if target.is_file() and not target.is_symlink():
                entries.append(file_digest_observation(relative, sha256_bytes(target.read_bytes())))
                directories.add(relative.parent)
    for directory in sorted(directories):
        target = graph.root / directory
        members = (
            tuple(sorted(entry.name for entry in target.iterdir()))
            if target.is_dir() and not target.is_symlink()
            else ()
        )
        entries.append(directory_observation(directory, members))
    return ObservationManifest(entries=tuple(entries))
