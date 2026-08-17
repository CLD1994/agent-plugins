from __future__ import annotations

from pathlib import Path

from draft.observations import (
    EMPTY_OBSERVATIONS,
    EntryKind,
    FilesystemObservationProbe,
    Observation,
    ObservationManifest,
    directory_observation,
    file_observation,
    graph_observations,
    missing_observation,
    observation_shape_error,
    verify_observations,
)
from draft.operations import sha256_bytes

PAGES = Path("pages")


class FakeProbe:
    """受真实签名约束的观察探针：当前事实由测试显式提供。"""

    def __init__(self, states: dict[Path, Observation]) -> None:
        self.states = states

    def observe(self, path: Path) -> Observation:
        return self.states.get(path, Observation(path=path, kind=EntryKind.MISSING))


def _file(path: str, data: str) -> Observation:
    return file_observation(Path(path), data.encode("utf-8"))


def test_verify_accepts_unchanged_observations() -> None:
    manifest = ObservationManifest(
        entries=(_file("pages/a.md", "甲"), directory_observation(PAGES, ("a.md",)))
    )
    probe = FakeProbe(
        {
            Path("pages/a.md"): _file("pages/a.md", "甲"),
            PAGES: directory_observation(PAGES, ("a.md",)),
        }
    )
    assert verify_observations(probe, manifest) == ()


def test_verify_rejects_digest_change() -> None:
    manifest = ObservationManifest(entries=(_file("pages/a.md", "甲"),))
    probe = FakeProbe({Path("pages/a.md"): _file("pages/a.md", "乙")})
    assert verify_observations(probe, manifest) == (Path("pages/a.md"),)


def test_verify_rejects_type_change() -> None:
    manifest = ObservationManifest(entries=(_file("pages/a.md", "甲"),))
    probe = FakeProbe({Path("pages/a.md"): missing_observation(Path("pages/a.md"))})
    assert verify_observations(probe, manifest) == (Path("pages/a.md"),)


def test_verify_rejects_new_member_in_globally_observed_pages_tree() -> None:
    manifest = ObservationManifest(entries=(directory_observation(Path("pages"), ("a.md",)),))
    probe = FakeProbe({Path("pages"): directory_observation(Path("pages"), ("a.md", "hidden.md"))})
    assert verify_observations(probe, manifest) == (Path("pages"),)


def test_verify_ignores_unobserved_file_change() -> None:
    manifest = ObservationManifest(entries=(_file("pages/a.md", "甲"),))
    probe = FakeProbe(
        {
            Path("pages/a.md"): _file("pages/a.md", "甲"),
            Path("pages/unobserved.md"): _file("pages/unobserved.md", "变了"),
        }
    )
    assert verify_observations(probe, manifest) == ()


def test_verify_excludes_explained_mutation_paths() -> None:
    manifest = ObservationManifest(entries=(_file("pages/a.md", "甲"),))
    probe = FakeProbe({Path("pages/a.md"): _file("pages/a.md", "乙")})
    from draft.models import FileMutation, MutationKind

    mutation = FileMutation(
        Path("pages/a.md"),
        MutationKind.WRITE,
        sha256_bytes("甲".encode()),
        sha256_bytes("乙".encode()),
        "乙".encode(),
    )
    assert verify_observations(probe, manifest, explained_mutations=(mutation,)) == ()


def test_verify_returns_sorted_changed_paths() -> None:
    manifest = ObservationManifest(entries=(_file("pages/z.md", "1"), _file("pages/a.md", "1")))
    probe = FakeProbe(
        {Path("pages/z.md"): _file("pages/z.md", "2"), Path("pages/a.md"): _file("pages/a.md", "2")}
    )
    assert verify_observations(probe, manifest) == (Path("pages/a.md"), Path("pages/z.md"))


def test_file_observation_computes_digest_from_provided_bytes() -> None:
    data = "正文".encode()
    observation = file_observation(Path("pages/a.md"), data)
    assert observation.sha256 == sha256_bytes(data)


def test_manifest_for_path_returns_matching_entry() -> None:
    manifest = ObservationManifest(
        entries=(_file("pages/a.md", "甲"), directory_observation(PAGES, ("a.md",)))
    )
    assert manifest.for_path(Path("pages/a.md")) is not None
    assert manifest.for_path(Path("pages/missing.md")) is None


def test_graph_observations_covers_pages_dirs_and_marker(tmp_path: Path) -> None:
    from dataclasses import dataclass
    from typing import cast

    from draft.models import PageDocument

    (tmp_path / "pages").mkdir()
    (tmp_path / "pages/a.md").write_text("# 甲\n", encoding="utf-8")
    (tmp_path / ".draft").mkdir()
    (tmp_path / ".draft/draft.json").write_text("{}", encoding="utf-8")

    @dataclass
    class FakeGraph:
        root: Path
        pages: dict[Path, PageDocument]

    class _Page:
        text = "# 甲\n"

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


def test_empty_manifest_is_defined() -> None:
    assert EMPTY_OBSERVATIONS.entries == ()


def test_observation_shape_error_accepts_canonical_shapes() -> None:
    """合法组合：FILE 带摘要无成员；DIRECTORY 无摘要（成员可为空）；其余无摘要无成员。"""
    assert observation_shape_error(_file("pages/a.md", "甲")) is None
    assert observation_shape_error(directory_observation(PAGES, ())) is None
    assert observation_shape_error(directory_observation(PAGES, ("a.md",))) is None
    assert observation_shape_error(missing_observation(Path("pages/a.md"))) is None


def test_observation_shape_error_rejects_illegal_combinations() -> None:
    """非法组合：FILE 缺摘要/带成员、DIRECTORY 带摘要、MISSING/SYMLINK 带摘要或成员。"""
    path = Path("pages/a.md")
    assert observation_shape_error(Observation(path, EntryKind.FILE, sha256=None)) is not None
    assert (
        observation_shape_error(Observation(path, EntryKind.FILE, sha256="x", members=("a.md",)))
        is not None
    )
    assert observation_shape_error(Observation(path, EntryKind.DIRECTORY, sha256="x")) is not None
    assert observation_shape_error(Observation(path, EntryKind.MISSING, sha256="x")) is not None
    assert (
        observation_shape_error(Observation(path, EntryKind.SYMLINK, members=("a.md",))) is not None
    )
