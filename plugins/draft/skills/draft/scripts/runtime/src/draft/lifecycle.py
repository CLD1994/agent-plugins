from __future__ import annotations

import json
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from draft.checker import check_draft
from draft.diagnostic_codes import CheckCode, RootCode
from draft.errors import DraftError
from draft.graph import (
    FORMAT_VERSION,
    INTERNAL_DIR,
    MARKER_PATH,
    PROVISIONAL_MARKER_PATH,
    parse_provisional_adopt_value,
    read_marker,
)
from draft.models import RootState
from draft.operations import (
    durable_write,
    fsync_directory,
    reject_symlink_components,
    sha256_file,
)
from draft.paths import resolve_draft_root
from draft.vocabulary import ConclusionsState


def marker_bytes(
    root_state: RootState,
    conclusions_state: ConclusionsState = ConclusionsState.NOT_GENERATED,
) -> bytes:
    """构造精确三键正式 marker；root_state 与 conclusions_state 正交。"""
    payload = {
        "format_version": FORMAT_VERSION,
        "root_state": root_state.value,
        "conclusions_state": conclusions_state.value,
    }
    return (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")


@dataclass(frozen=True)
class CandidateManifest:
    entries: tuple[tuple[Path, str], ...]


def draft_manifest(
    root: Path,
    *,
    allow_provisional_adopt: bool = False,
) -> CandidateManifest:
    from draft.graph import load_graph_tolerant

    graph = load_graph_tolerant(root, allow_provisional_adopt=allow_provisional_adopt).graph
    entries: list[tuple[Path, str]] = []
    for path in graph.pages:
        digest = sha256_file(root / path)
        if digest is None:
            raise DraftError("adopt.invalid_candidate", root / path)
        entries.append((path, digest))
    return CandidateManifest(entries=tuple(sorted(entries)))


def provisional_marker_bytes(manifest: CandidateManifest) -> bytes:
    payload = {
        "format_version": FORMAT_VERSION,
        "conclusions_state": ConclusionsState.NOT_GENERATED.value,
        "adopt_state": "provisional",
        "candidate_manifest": [[path.as_posix(), digest] for path, digest in manifest.entries],
    }
    return (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")


def read_provisional_adopt_manifest(marker: Path) -> CandidateManifest:
    """Resume evidence for adopt: provisional manifest or format error."""
    if marker.is_symlink():
        raise DraftError(CheckCode.PATH_SYMLINK_ESCAPE.value, marker)
    try:
        decoded = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DraftError(CheckCode.FORMAT_INVALID.value, marker) from exc
    state = parse_provisional_adopt_value(decoded, marker)
    entries = tuple(sorted((Path(path_text), digest) for path_text, digest in state.manifest))
    return CandidateManifest(entries=entries)


def create_marker_parent_durably(root: Path) -> None:
    (root / INTERNAL_DIR).mkdir(exist_ok=True)
    fsync_directory(root)


def rollback_provisional_marker_durably(root: Path, expected_bytes: bytes) -> None:
    internal = root / INTERNAL_DIR
    provisional = root / PROVISIONAL_MARKER_PATH
    # 回滚只清理本次 adopt 的临时状态（draft.provisional.json），绝不触碰正式
    # 标记 draft.json。重读并完整比对临时标记身份：只有仍等于本次 adopt 发布的
    # 临时标记才删除；他方覆盖的另一临时标记一律保留。
    if provisional.is_file():
        if provisional.read_bytes() != expected_bytes:
            return
        provisional.unlink(missing_ok=True)
    fsync_directory(internal)
    # 内部目录仅在为“空”时移除：并发方创建的内容必须保留。
    with suppress(OSError):
        internal.rmdir()
    fsync_directory(root)


def _index_template(title: str) -> str:
    return f"# {title}\n\n> 状态：`已关闭：纯导航页，不表达独立结论`\n\n## 子主题\n"


def initialize(root: Path, *, root_state: RootState = RootState.REGISTERED) -> None:
    """创建全新受管根：``init ROOT`` 为 registered，``init --candidate ROOT`` 为 candidate。

    使用耐久发布 + 可证明回滚协议：任何失败都不留下看似成功的半初始化根。
    不再经由 candidate check + provisional marker 绕一圈创建正式根。
    """
    target = resolve_draft_root(root, allow_missing=True)
    pre_existing = target.exists()
    if pre_existing:
        if target.is_symlink() or not target.is_dir():
            raise DraftError(RootCode.INVALID.value, target)
        if any(target.iterdir()):
            raise DraftError("init.not_empty", target)
    created: list[Path] = []
    try:
        if not pre_existing:
            target.mkdir(parents=True)
            created.append(target)
        index = target / "index.md"
        index.write_text(_index_template(target.name), encoding="utf-8")
        created.append(index)
        pages = target / "pages"
        pages.mkdir()
        created.append(pages)
        create_marker_parent_durably(target)
        created.append(target / INTERNAL_DIR)
        durable_write(target / MARKER_PATH, marker_bytes(root_state))
        report = check_draft(target)
        if report.errors:
            codes = tuple(issue.code for issue in report.errors)
            raise DraftError("init.invalid_template", target, details=codes)
    except Exception:
        for path in sorted(created, key=lambda entry: len(entry.parts), reverse=True):
            if path.is_symlink() or path.is_file():
                path.unlink(missing_ok=True)
            elif path.is_dir():
                with suppress(OSError):
                    path.rmdir()
        raise


def adopt(root: Path) -> None:
    """把 root_state=candidate 的受管根原子推进为 registered。

    只接受 candidate marker：未受管目录（无 marker）与已登记根都拒绝。provisional
    文件在 Task 8 前仍是开发期崩溃桥接；私有复核用 allow_provisional_adopt 精确排除
    自己的 provisional 文件。
    """
    target = resolve_draft_root(root)
    final_marker = target / MARKER_PATH
    provisional_marker = target / PROVISIONAL_MARKER_PATH
    reject_symlink_components(target, MARKER_PATH)
    reject_symlink_components(target, PROVISIONAL_MARKER_PATH)
    if not final_marker.is_file():
        raise DraftError("adopt.invalid_candidate", target)
    state = read_marker(target)
    if state.root_state is not RootState.CANDIDATE:
        # 有效正式标记 + 残留独立临时标记 = 已提交待清理（finalize 两步间中断）。
        # 先耐久清理临时标记，再报告已登记；绝不能留着死锁现场。
        if provisional_marker.is_file():
            provisional_marker.unlink(missing_ok=True)
            fsync_directory(target / INTERNAL_DIR)
        raise DraftError("adopt.already_registered", target)
    before = (
        read_provisional_adopt_manifest(provisional_marker)
        if provisional_marker.is_file()
        else None
    )
    if (target / INTERNAL_DIR / "operation.lock").exists():
        raise DraftError("adopt.locked", target)
    if before is None:
        # 完整候选检查（无过滤无分页）：candidate 与 registered 共享检查协议。
        report = check_draft(target)
        if report.errors:
            raise DraftError(
                "adopt.invalid_candidate",
                target,
                details=tuple(issue.code for issue in report.errors),
            )
        before = draft_manifest(target)
        create_marker_parent_durably(target)
        durable_write(provisional_marker, provisional_marker_bytes(before))

    after = draft_manifest(target, allow_provisional_adopt=True)
    if after != before:
        rollback_provisional_marker_durably(target, provisional_marker_bytes(before))
        raise DraftError(
            "adopt.invalid_candidate",
            target,
        )
    # 最终标记写入 draft.json 是登记线性化点；临时状态在独立文件中，先写正式再清理。
    durable_write(final_marker, marker_bytes(RootState.REGISTERED))
    provisional_marker.unlink(missing_ok=True)
    fsync_directory(target / INTERNAL_DIR)
