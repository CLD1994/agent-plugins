"""可复现的链接索引性能基准。

基准在真实临时文件系统上调用 runtime 已有的 maintenance、query、check 和 notification
入口。scanner 与 journal 写入的包裹只存在于本文件，用于观察公开 I/O 边界，不改变生产
协议，也不把完整规模接入 pytest。
"""

from __future__ import annotations

import argparse
import json
import os
import random
import stat
import tempfile
import time
import tracemalloc
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from draft.checker import check_draft
from draft.cli import notify_files_changed
from draft.diagnostic_codes import OPERATION_INDEX_UPDATE_FAILED
from draft.errors import DraftError
from draft.link_index import IndexedFile, LinkIndexScan, LinkIndexScanStats
from draft.link_index_maintenance import (
    LinkIndexBaseline,
    maintain_link_index,
    read_link_index_authority,
)
from draft.models import RootState
from draft.operations import Journal
from draft.query import (
    backlinks_from_index,
    load_tolerant_view,
    maintain_link_index_for_query,
)
from draft.resources import ResourceIdentity, ResourceKind

DEFAULT_SEED = 20260825
SCENARIOS: tuple[str, ...] = (
    "first_build",
    "fresh_query_backlinks",
    "fresh_check",
    "notification_noop",
    "modify_one",
    "modify_one_percent",
    "add_markdown",
    "delete_markdown",
    "rename_markdown",
    "directory_nodes",
    "rebuild_required_recovery",
    "check_maintenance",
    "journal_growth",
)


@dataclass(frozen=True)
class BenchmarkResult:
    """一个场景的一次测量结果。

    ``markdown_files`` 是规范字段；JSON 同时提供 ``files`` 这一面向报告的短名称，
    两者由同一字段派生，避免 benchmark 消费方维护两套计数。
    """

    scenario: str
    markdown_files: int
    width: int
    read_files: int
    read_bytes: int
    parsed_files: int
    wall_seconds: float
    peak_index_bytes: int
    index_file_bytes: int
    journal_write_count: int
    journal_write_bytes: int

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "scenario": self.scenario,
            "files": self.markdown_files,
            "markdown_files": self.markdown_files,
            "width": self.width,
            "read_files": self.read_files,
            "read_bytes": self.read_bytes,
            "parsed_files": self.parsed_files,
            "wall_seconds": self.wall_seconds,
            "peak_index_bytes": self.peak_index_bytes,
            "index_file_bytes": self.index_file_bytes,
            "journal_write_count": self.journal_write_count,
            "journal_write_bytes": self.journal_write_bytes,
        }
        return payload


@dataclass
class _Observation:
    scan_stats: list[LinkIndexScanStats] = field(default_factory=list)
    journal_write_count: int = 0
    journal_write_bytes: int = 0


def _marker_bytes() -> bytes:
    return (
        b'{"format_version":1,"root_state":"registered",'
        b'"conclusions_state":"not_generated"}\n'
    )


def _write_dataset(root: Path, markdown_files: int, seed: int) -> None:
    if markdown_files < 2:
        raise ValueError("markdown_files must be at least 2")
    if root.exists():
        raise ValueError(f"benchmark root already exists: {root}")

    root.mkdir(parents=True)
    (root / "pages").mkdir()
    internal = root / ".draft"
    (internal / "operations").mkdir(parents=True)
    (internal / "draft.json").write_bytes(_marker_bytes())
    (root / "index.md").write_text(
        "# Root\n\n> 状态：`已关闭：纯导航页，不表达独立结论`\n\nBenchmark root.\n",
        encoding="utf-8",
    )

    generator = random.Random(seed)
    target = "doc-00001.md"
    for number in range(1, markdown_files):
        token = generator.getrandbits(64)
        (root / f"doc-{number:05d}.md").write_text(
            f"# Note {number}\n\nseed={token}\n\nSee [target]({target}).\n",
            encoding="utf-8",
        )


def _document_path(root: Path, number: int) -> Path:
    return root / f"doc-{number:05d}.md"


def _append_change(path: Path, marker: str) -> None:
    path.write_text(path.read_text(encoding="utf-8") + f"\nchange={marker}\n", encoding="utf-8")


def _count_markdown_files(root: Path) -> int:
    count = 0
    for current, dirnames, filenames in os.walk(root):
        current_path = Path(current)
        relative = current_path.relative_to(root)
        if relative == Path(".draft") or relative.parts[:1] == (".draft",):
            dirnames[:] = []
            continue
        for name in filenames:
            path = current_path / name
            try:
                node = os.lstat(path)
            except OSError:
                continue
            if name.endswith(".md") and stat.S_ISREG(node.st_mode):
                count += 1
    return count


def _markdown_width(root: Path) -> int:
    width = 0
    for current, dirnames, filenames in os.walk(root):
        current_path = Path(current)
        relative = current_path.relative_to(root)
        if relative == Path(".draft") or relative.parts[:1] == (".draft",):
            dirnames[:] = []
            continue
        count = 0
        for name in filenames:
            if not name.endswith(".md"):
                continue
            try:
                node = os.lstat(current_path / name)
            except OSError:
                continue
            if stat.S_ISREG(node.st_mode):
                count += 1
        width = max(width, count)
    return width


def _index_file_bytes(root: Path) -> int:
    path = root / ".draft" / "link-index.json"
    try:
        node = os.lstat(path)
    except FileNotFoundError:
        return 0
    if not stat.S_ISREG(node.st_mode):
        return 0
    return node.st_size


def _aggregate_stats(observation: _Observation) -> LinkIndexScanStats:
    if not observation.scan_stats:
        raise RuntimeError("benchmark action did not expose a link-index scan")
    return LinkIndexScanStats(
        read_files=sum(stats.read_files for stats in observation.scan_stats),
        read_bytes=sum(stats.read_bytes for stats in observation.scan_stats),
        parsed_files=sum(stats.parsed_files for stats in observation.scan_stats),
    )


def _measure(root: Path, action: Callable[[], object]) -> BenchmarkResult:
    """在 scanner/journal 两个稳定边界观察一次公开入口。"""
    import draft.link_index_maintenance as maintenance_module
    import draft.operations as operations_module

    observation = _Observation()
    original_scan = maintenance_module.scan_link_index
    original_maintenance_write = maintenance_module.write_journal
    original_operations_write = operations_module.write_journal

    def observed_scan(
        scan_root: Path,
        baseline: LinkIndexBaseline | None,
        known_documents: tuple[IndexedFile, ...] = (),
    ) -> LinkIndexScan:
        scan = original_scan(scan_root, baseline, known_documents=known_documents)
        observation.scan_stats.append(scan.stats)
        return scan

    def observed_write(journal: Journal) -> None:
        original_operations_write(journal)
        journal_path = journal.directory / "journal.json"
        observation.journal_write_count += 1
        observation.journal_write_bytes += journal_path.stat().st_size

    maintenance_module.scan_link_index = observed_scan
    maintenance_module.write_journal = observed_write
    operations_module.write_journal = observed_write
    tracemalloc.start()
    started = time.perf_counter()
    try:
        action()
        wall_seconds = time.perf_counter() - started
        _current, peak_index_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
        maintenance_module.scan_link_index = original_scan
        maintenance_module.write_journal = original_maintenance_write
        operations_module.write_journal = original_operations_write

    stats = _aggregate_stats(observation)
    markdown_files = _count_markdown_files(root)
    if stats.read_files != markdown_files:
        raise RuntimeError(
            f"strict scan coverage mismatch: read {stats.read_files}, files {markdown_files}"
        )
    return BenchmarkResult(
        scenario="",
        markdown_files=markdown_files,
        width=_markdown_width(root),
        read_files=stats.read_files,
        read_bytes=stats.read_bytes,
        parsed_files=stats.parsed_files,
        wall_seconds=wall_seconds,
        peak_index_bytes=peak_index_bytes,
        index_file_bytes=_index_file_bytes(root),
        journal_write_count=observation.journal_write_count,
        journal_write_bytes=observation.journal_write_bytes,
    )


def _build_initial(root: Path) -> None:
    result = maintain_link_index(root)
    if not result.published or result.index is None:
        raise RuntimeError("initial benchmark index was not published")


def _query_backlinks(root: Path) -> None:
    view = load_tolerant_view(root)
    result = maintain_link_index_for_query(root, view.graph, RootState.REGISTERED)
    if result.index is None:
        raise RuntimeError("fresh query did not expose an index")
    target = ResourceIdentity(ResourceKind.LOCAL, "doc-00001.md")
    if not backlinks_from_index(result.index, target):
        raise RuntimeError("benchmark target has no backlinks")


def _rebuild_then_recover(root: Path) -> None:
    import draft.link_index_maintenance as maintenance_module

    original_scan = maintenance_module.scan_link_index

    def fail_scan(
        scan_root: Path,
        baseline: LinkIndexBaseline | None,
        known_documents: tuple[IndexedFile, ...] = (),
    ) -> LinkIndexScan:
        del baseline, known_documents
        raise DraftError(OPERATION_INDEX_UPDATE_FAILED, scan_root)

    maintenance_module.scan_link_index = fail_scan
    try:
        maintain_link_index(root)
    finally:
        maintenance_module.scan_link_index = original_scan


def _scenario_action(root: Path, scenario: str, markdown_files: int) -> Callable[[], object]:
    if scenario == "first_build":
        return lambda: maintain_link_index(root)
    if scenario == "fresh_query_backlinks":
        return lambda: _query_backlinks(root)
    if scenario == "fresh_check":
        return lambda: check_draft(root, maintain_index=True)
    if scenario == "notification_noop":
        return lambda: notify_files_changed(root)
    if scenario == "modify_one":
        _append_change(_document_path(root, 1), "one")
        return lambda: maintain_link_index(root)
    if scenario == "modify_one_percent":
        changed = min(markdown_files - 1, max(1, markdown_files // 100))
        for number in range(1, changed + 1):
            _append_change(_document_path(root, number), f"percent-{number}")
        return lambda: maintain_link_index(root)
    if scenario == "add_markdown":
        (root / "doc-added.md").write_text(
            "# Added\n\nSee [target](doc-00001.md).\n", encoding="utf-8"
        )
        return lambda: maintain_link_index(root)
    if scenario == "delete_markdown":
        _document_path(root, 1).unlink()
        return lambda: maintain_link_index(root)
    if scenario == "rename_markdown":
        _document_path(root, 1).rename(root / "doc-renamed.md")
        return lambda: maintain_link_index(root)
    if scenario == "directory_nodes":
        (root / "empty-directory").mkdir()
        return lambda: maintain_link_index(root)
    if scenario == "rebuild_required_recovery":
        _rebuild_then_recover(root)

        def recover_index() -> object:
            result = maintain_link_index(root)
            if not result.published or result.index is None:
                raise RuntimeError("rebuild-required recovery did not publish an index")
            return result

        return recover_index
    if scenario == "check_maintenance":
        return lambda: check_draft(root, maintain_index=True)
    if scenario == "journal_growth":
        growth = min(8, markdown_files - 1)
        for number in range(1, growth + 1):
            _append_change(_document_path(root, number), f"journal-{number}")
            _build_initial(root)

        def maintain_after_journal_growth() -> object:
            result = maintain_link_index(root)
            authority = read_link_index_authority(root)
            if authority.revision != str(growth + 1):
                raise RuntimeError("journal growth action did not observe latest revision")
            return result

        return maintain_after_journal_growth
    raise ValueError(f"unknown benchmark scenario: {scenario}")


def run_benchmark_scenario(
    scenario: str,
    *,
    markdown_files: int,
    root: Path,
    seed: int = DEFAULT_SEED,
) -> BenchmarkResult:
    """生成固定数据集并运行一个可观测场景。``root`` 必须是新的临时目录路径。"""
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown benchmark scenario: {scenario}")
    _write_dataset(root, markdown_files, seed)
    if scenario in {
        "fresh_query_backlinks",
        "fresh_check",
        "notification_noop",
        "modify_one",
        "modify_one_percent",
        "add_markdown",
        "delete_markdown",
        "rename_markdown",
        "directory_nodes",
        "rebuild_required_recovery",
        "journal_growth",
    }:
        _build_initial(root)
    action = _scenario_action(root, scenario, markdown_files)
    measured = _measure(root, action)
    return BenchmarkResult(
        scenario=scenario,
        markdown_files=measured.markdown_files,
        width=measured.width,
        read_files=measured.read_files,
        read_bytes=measured.read_bytes,
        parsed_files=measured.parsed_files,
        wall_seconds=measured.wall_seconds,
        peak_index_bytes=measured.peak_index_bytes,
        index_file_bytes=measured.index_file_bytes,
        journal_write_count=measured.journal_write_count,
        journal_write_bytes=measured.journal_write_bytes,
    )


def run_benchmark_suite(
    file_counts: Sequence[int], *, output: Path, seed: int = DEFAULT_SEED
) -> tuple[BenchmarkResult, ...]:
    """运行显式请求的规模与全部场景，并写出确定结构的 JSON 结果。"""
    if not file_counts:
        raise ValueError("at least one file count is required")
    results: list[BenchmarkResult] = []
    with tempfile.TemporaryDirectory(prefix="draft-link-index-benchmark-") as directory:
        workspace = Path(directory)
        for count in file_counts:
            if count < 2:
                raise ValueError("file counts must be at least 2")
            for scenario in SCENARIOS:
                root = workspace / f"files-{count}" / scenario
                results.append(
                    run_benchmark_scenario(
                        scenario, markdown_files=count, root=root, seed=seed
                    )
                )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps([result.to_dict() for result in results], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return tuple(results)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--files",
        nargs="+",
        type=int,
        default=[100],
        metavar="N",
        help="要测量的 Markdown 文件数（默认 100；完整规模需显式传入）",
    )
    parser.add_argument("--output", type=Path, required=True, help="JSON 输出路径")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="固定数据集种子")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    file_counts = cast("list[int]", args.files)
    results = run_benchmark_suite(file_counts, output=args.output, seed=args.seed)
    print(f"wrote {len(results)} benchmark records to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
