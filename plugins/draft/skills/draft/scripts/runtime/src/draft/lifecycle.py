from __future__ import annotations

import json
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from draft.checker import check_draft
from draft.errors import DraftError
from draft.graph import (
    FORMAT_VERSION,
    INTERNAL_DIR,
    MARKER_PATH,
    PROVISIONAL_MARKER_PATH,
    load_graph,
    parse_marker_value,
    read_marker,
)
from draft.models import ValidationMode
from draft.operations import (
    durable_write,
    fsync_directory,
    reject_symlink_components,
    sha256_file,
)
from draft.paths import resolve_draft_root

FINAL_MARKER_BYTES = b'{"format_version":1}\n'


@dataclass(frozen=True)
class CandidateManifest:
    entries: tuple[tuple[Path, str], ...]


def draft_manifest(
    root: Path,
    *,
    mode: ValidationMode,
    allow_provisional_adopt: bool = False,
) -> CandidateManifest:
    graph = load_graph(
        root,
        mode=mode,
        allow_provisional_adopt=allow_provisional_adopt,
    )
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
        "adopt_state": "provisional",
        "candidate_manifest": [[path.as_posix(), digest] for path, digest in manifest.entries],
    }
    return (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")


def read_provisional_adopt_manifest(marker: Path) -> CandidateManifest:
    """Resume evidence for adopt: final marker, provisional manifest or format error."""
    if marker.is_symlink():
        raise DraftError("path.symlink_escape", marker)
    try:
        decoded = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DraftError("format.invalid", marker) from exc
    state = parse_marker_value(decoded, marker)
    if state.provisional_manifest is None:
        raise DraftError("adopt.already_registered", marker.parent.parent)
    entries = tuple(
        sorted((Path(path_text), digest) for path_text, digest in state.provisional_manifest)
    )
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
    return (
        f"# {title}\n\n"
        "> 状态：`已关闭：纯导航页，不表达独立结论`\n\n"
        "## 子主题\n\n"
        "- [结论汇总](conclusions.md)\n"
    )


CONCLUSIONS_TEMPLATE = "# 结论汇总\n\n> 状态：`已确认`\n>\n> 父主题：[根索引](index.md)\n"


def initialize(root: Path) -> None:
    target = resolve_draft_root(root, allow_missing=True)
    pre_existing = target.exists()
    if pre_existing:
        if target.is_symlink() or not target.is_dir():
            raise DraftError("root.invalid", target)
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
        conclusions = target / "conclusions.md"
        conclusions.write_text(CONCLUSIONS_TEMPLATE, encoding="utf-8")
        created.append(conclusions)
        pages = target / "pages"
        pages.mkdir()
        created.append(pages)
        candidate = check_draft(target, mode="candidate")
        if candidate.errors:
            codes = tuple(issue.code for issue in candidate.errors)
            raise DraftError("init.invalid_template", target, details=codes)
        before = draft_manifest(target, mode="candidate")
        create_marker_parent_durably(target)
        created.append(target / INTERNAL_DIR)
        provisional_marker = target / PROVISIONAL_MARKER_PATH
        durable_write(provisional_marker, provisional_marker_bytes(before))
        created.append(provisional_marker)
        after_report = check_draft(target, mode="candidate", allow_provisional_adopt=True)
        after = draft_manifest(target, mode="candidate", allow_provisional_adopt=True)
        if after_report.errors or after != before:
            codes = tuple(issue.code for issue in after_report.errors)
            raise DraftError("init.invalid_template", target, details=codes)
        durable_write(target / MARKER_PATH, FINAL_MARKER_BYTES)
        provisional_marker.unlink(missing_ok=True)
        fsync_directory(target / INTERNAL_DIR)
        registered = check_draft(target, mode="registered")
        if registered.errors:
            codes = tuple(issue.code for issue in registered.errors)
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
    target = resolve_draft_root(root)
    final_marker = target / MARKER_PATH
    provisional_marker = target / PROVISIONAL_MARKER_PATH
    reject_symlink_components(target, MARKER_PATH)
    reject_symlink_components(target, PROVISIONAL_MARKER_PATH)
    if final_marker.is_file():
        final_state = read_marker(target, allow_provisional_adopt=True)
        if final_state.provisional_manifest is not None:
            raise DraftError("adopt.recovery_required", final_marker)
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
        report = check_draft(target, mode="candidate")
        if report.errors:
            raise DraftError(
                "adopt.invalid_candidate",
                target,
                details=tuple(issue.code for issue in report.errors),
            )
        before = draft_manifest(target, mode="candidate")
        create_marker_parent_durably(target)
        durable_write(provisional_marker, provisional_marker_bytes(before))

    after_report = check_draft(
        target,
        mode="candidate",
        allow_provisional_adopt=True,
    )
    after = draft_manifest(
        target,
        mode="candidate",
        allow_provisional_adopt=True,
    )
    if after_report.errors or after != before:
        rollback_provisional_marker_durably(target, provisional_marker_bytes(before))
        raise DraftError(
            "adopt.invalid_candidate",
            target,
            details=tuple(issue.code for issue in after_report.errors),
        )
    # 最终标记写入 draft.json 是登记线性化点；临时状态在独立文件中，先写正式再清理。
    durable_write(final_marker, FINAL_MARKER_BYTES)
    provisional_marker.unlink(missing_ok=True)
    fsync_directory(target / INTERNAL_DIR)
