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
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
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


@dataclass(frozen=True)
class Observation:
    """根内相对路径的类型、摘要与目录成员集合；``kind`` 决定其他字段含义。"""

    path: Path
    kind: EntryKind
    sha256: str | None = None
    members: tuple[str, ...] = ()


@dataclass(frozen=True)
class ObservationManifest:
    entries: tuple[Observation, ...]

    def for_path(self, path: Path) -> Observation | None:
        for entry in self.entries:
            if entry.path == path:
                return entry
        return None


EMPTY_OBSERVATIONS = ObservationManifest(entries=())


def file_observation(path: Path, data: bytes) -> Observation:
    from draft.operations import sha256_bytes

    return Observation(path=path, kind=EntryKind.FILE, sha256=sha256_bytes(data))


def file_digest_observation(path: Path, sha256: str) -> Observation:
    return Observation(path=path, kind=EntryKind.FILE, sha256=sha256)


def directory_observation(path: Path, members: tuple[str, ...]) -> Observation:
    return Observation(path=path, kind=EntryKind.DIRECTORY, members=tuple(sorted(members)))


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
        if current != entry:
            changed.append(entry.path)
    return tuple(sorted(changed))


def graph_observations(graph: _GraphLike) -> ObservationManifest:
    """从已加载图构建读集观察：页面摘要复用已解析文本，目录成员与 marker 各枚举一次。

    ``graph`` 需提供 ``root`` 与 ``pages``（path → 带 ``text`` 的页面对象）。
    复杂度 O(页面字节总数 + 受管理目录条目数 + marker 一次读取)。
    """
    from draft.graph import MARKER_PATH, PAGES_DIR
    from draft.operations import sha256_bytes

    entries: list[Observation] = []
    directories: set[Path] = set()
    for relative, page in graph.pages.items():
        entries.append(file_digest_observation(relative, sha256_bytes(page.text.encode("utf-8"))))
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
        members = (
            tuple(sorted(entry.name for entry in target.iterdir()))
            if target.is_dir() and not target.is_symlink()
            else ()
        )
        entries.append(directory_observation(directory, members))
    marker = root / MARKER_PATH
    if marker.is_file() and not marker.is_symlink():
        entries.append(file_digest_observation(MARKER_PATH, sha256_bytes(marker.read_bytes())))
    return ObservationManifest(entries=tuple(entries))
