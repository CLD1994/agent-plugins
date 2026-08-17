"""观察捕获与探针的真实文件系统边界：graph_observations 目录枚举、FilesystemObservationProbe。

单元测试不得访问真实文件系统；目录枚举与探针观察属于真实协作者边界，放在集成层。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

from draft.models import PageDocument
from draft.observations import (
    EntryKind,
    FilesystemObservationProbe,
    graph_observations,
)


@dataclass
class FakeGraph:
    root: Path
    pages: dict[Path, PageDocument]


class _Page:
    text = "# 甲\n"


def test_graph_observations_covers_pages_dirs_and_marker(tmp_path: Path) -> None:
    (tmp_path / "pages").mkdir()
    (tmp_path / "pages/a.md").write_text("# 甲\n", encoding="utf-8")
    (tmp_path / ".draft").mkdir()
    (tmp_path / ".draft/draft.json").write_text("{}", encoding="utf-8")

    manifest = graph_observations(
        FakeGraph(root=tmp_path, pages={Path("pages/a.md"): cast(PageDocument, _Page())})
    )
    assert manifest.for_path(Path("pages/a.md")) is not None
    assert manifest.for_path(Path("pages")) is not None
    assert manifest.for_path(Path(".draft/draft.json")) is not None


def test_filesystem_probe_observes_actual_state(tmp_path: Path) -> None:
    (tmp_path / "pages").mkdir()
    (tmp_path / "pages/a.md").write_text("甲", encoding="utf-8")
    probe = FilesystemObservationProbe(tmp_path)

    assert probe.observe(Path("pages/a.md")).kind is EntryKind.FILE
    assert probe.observe(Path("pages")).kind is EntryKind.DIRECTORY
    assert probe.observe(Path("pages/missing.md")).kind is EntryKind.MISSING
