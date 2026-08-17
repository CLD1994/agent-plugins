from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from draft.diagnostic_codes import CheckCode
from draft.errors import DraftError
from draft.models import MutationKind, OperationKind, OperationPhase, RootState
from draft.observations import (
    EMPTY_OBSERVATIONS,
    EntryKind,
    Observation,
    ObservationManifest,
    directory_observation,
    file_observation,
)
from draft.operations import (
    HANDOFF_ATTEMPTED_AT_KEY,
    HANDOFF_ERROR_MESSAGE_KEY,
    HANDOFF_ERROR_TYPE_KEY,
    JOURNAL_VERSION,
    Journal,
    JournalMutation,
    TrashDeadlines,
    _parse_journal,  # pyright: ignore[reportPrivateUsage]
    _verify_observations_for_recovery,  # pyright: ignore[reportPrivateUsage]
    _write_recovery_decision,  # pyright: ignore[reportPrivateUsage]
    adjusted_observation_manifest,
    explained_directory_deltas,
    format_utc,
    handoff_failure_metadata,
    has_handoff_failure,
    normalize_utc,
    sha256_bytes,
    validate_operation_id,
    with_metadata,
    with_phase,
    without_metadata,
)

# ---------------------------------------------------------------------------
# adjusted_observation_manifest：纯函数，不访问文件系统
# ---------------------------------------------------------------------------


def test_adjusted_observation_manifest_explains_directory_members() -> None:
    manifest = ObservationManifest(
        entries=(
            directory_observation(Path("pages"), ("a.md",)),
            file_observation(Path("pages/b.md"), b"b"),
        )
    )
    adjusted = adjusted_observation_manifest(
        manifest,
        excluded=frozenset({Path(".draft/operations/x")}),
        explained=frozenset({Path("pages/b.md")}),
        added={Path("pages"): frozenset({"c.md"})},
        removed={Path("pages"): frozenset({"a.md"})},
    )
    # explained mutation 路径被排除。
    assert adjusted.for_path(Path("pages/b.md")) is None
    pages = adjusted.for_path(Path("pages"))
    assert pages is not None
    # 目录成员 = planned - removed + added。
    assert pages.members == ("c.md",)


def test_adjusted_observation_manifest_synthesizes_created_directory() -> None:
    """计划期缺失（父目录观察未列出）且本操作实际创建的目录，必须合成精确成员期望。

    回归 P1：promote 首次创建 pages/topic 时，恢复若跳过该目录内部验证，
    外部注入的页面会被静默吸收。
    """
    manifest = ObservationManifest(
        entries=(
            directory_observation(Path("pages"), ("a.md", "topic.md")),
            file_observation(Path("pages/a.md"), b"a"),
        )
    )
    adjusted = adjusted_observation_manifest(
        manifest,
        excluded=frozenset({Path(".draft/operations/x")}),
        explained=frozenset({Path("pages/topic.md")}),
        added={Path("pages/topic"): frozenset({"index.md"}), Path("pages"): frozenset({"topic"})},
        removed={Path("pages"): frozenset({"topic.md"})},
    )
    pages = adjusted.for_path(Path("pages"))
    assert pages is not None
    assert pages.members == ("a.md", "topic")
    topic = adjusted.for_path(Path("pages/topic"))
    assert topic is not None
    assert topic.kind is EntryKind.DIRECTORY
    assert topic.members == ("index.md",)


def test_adjusted_observation_manifest_does_not_synthesize_existing_directory() -> None:
    """父目录观察已列出该目录（计划期已存在）时不得合成"只含本操作成员"期望。"""
    manifest = ObservationManifest(
        entries=(
            directory_observation(Path("pages"), ("a.md", "topic")),
            file_observation(Path("pages/a.md"), b"a"),
        )
    )
    adjusted = adjusted_observation_manifest(
        manifest,
        excluded=frozenset({Path(".draft/operations/x")}),
        explained=frozenset(),
        added={Path("pages/topic"): frozenset({"index.md"}), Path("pages"): frozenset({"topic"})},
        removed={},
    )
    # pages/topic 已存在：合成必须跳过，否则预存成员会被误判为外部变化。
    assert adjusted.for_path(Path("pages/topic")) is None


def test_adjusted_observation_manifest_converts_missing_observation_to_directory() -> None:
    """计划期显式 MISSING 的目录被本操作创建时，转换为精确成员期望。"""
    from draft.observations import missing_observation

    manifest = ObservationManifest(
        entries=(
            missing_observation(Path("pages/topic")),
            directory_observation(Path("pages"), ("a.md",)),
        )
    )
    adjusted = adjusted_observation_manifest(
        manifest,
        excluded=frozenset({Path(".draft/operations/x")}),
        explained=frozenset(),
        added={Path("pages/topic"): frozenset({"index.md"}), Path("pages"): frozenset({"topic"})},
        removed={},
    )
    topic = adjusted.for_path(Path("pages/topic"))
    assert topic is not None
    assert topic.kind is EntryKind.DIRECTORY
    assert topic.members == ("index.md",)


def test_adjusted_observation_manifest_skips_synthesis_without_evidence() -> None:
    """没有任何存在性证据时保守不合成：已存在但未观察的共享目录不被误判。"""
    adjusted = adjusted_observation_manifest(
        EMPTY_OBSERVATIONS,
        excluded=frozenset({Path(".draft/operations/x")}),
        explained=frozenset(),
        added={Path(".draft"): frozenset({"operations", "operation.lock"})},
        removed={},
    )
    assert adjusted.for_path(Path(".draft")) is None


def test_adjusted_observation_manifest_synthesizes_deep_missing_directory() -> None:
    """缺失证明按深度传递：父目录可证明缺失时，其计划内子目录也可证明缺失（回归 H2）。"""
    manifest = ObservationManifest(entries=(directory_observation(Path("pages"), ("topic.md",)),))
    adjusted = adjusted_observation_manifest(
        manifest,
        excluded=frozenset({Path(".draft/operations/x")}),
        explained=frozenset({Path("pages/topic.md")}),
        added={
            Path("pages/topic/sub"): frozenset({"index.md"}),
            Path("pages/topic"): frozenset({"sub"}),
            Path("pages"): frozenset({"topic"}),
        },
        removed={Path("pages"): frozenset({"topic.md"})},
    )
    deep = adjusted.for_path(Path("pages/topic/sub"))
    assert deep is not None
    assert deep.kind is EntryKind.DIRECTORY
    assert deep.members == ("index.md",)


def test_adjusted_observation_manifest_synthesizes_created_dir_with_empty_members() -> None:
    """created_dirs 中目标文件仍为 before 的目录必须合成成员可为空的观察（回归 H3）。

    目录出现不等于目录内任意内容也由本操作创建；即使期望成员为空也必须形成明确
    观察，否则外部注入会被吸收。
    """
    manifest = ObservationManifest(entries=(directory_observation(Path("pages"), ("topic.md",)),))
    adjusted = adjusted_observation_manifest(
        manifest,
        excluded=frozenset({Path(".draft/operations/x")}),
        explained=frozenset({Path("pages/topic.md")}),
        added={Path("pages"): frozenset({"topic"})},
        removed={Path("pages"): frozenset({"topic.md"})},
        created_dirs=frozenset({Path("pages/topic")}),
    )
    topic = adjusted.for_path(Path("pages/topic"))
    assert topic is not None
    assert topic.kind is EntryKind.DIRECTORY
    assert topic.members == ()


def test_adjusted_observation_manifest_missing_observation_created_empty_dir() -> None:
    """计划期 MISSING 观察的目录被创建且成员为空：转换为空成员期望而非误报。"""
    from draft.observations import missing_observation

    manifest = ObservationManifest(entries=(missing_observation(Path("pages/topic")),))
    adjusted = adjusted_observation_manifest(
        manifest,
        excluded=frozenset({Path(".draft/operations/x")}),
        explained=frozenset(),
        added={},
        removed={},
        created_dirs=frozenset({Path("pages/topic")}),
    )
    topic = adjusted.for_path(Path("pages/topic"))
    assert topic is not None
    assert topic.kind is EntryKind.DIRECTORY
    assert topic.members == ()


def test_adjusted_observation_manifest_does_not_synthesize_observed_deep_dir() -> None:
    """深层目录已被父观察列出的不得合成（计划期已存在，只调整父成员）。"""
    manifest = ObservationManifest(
        entries=(
            directory_observation(Path("pages"), ("topic",)),
            directory_observation(Path("pages/topic"), ("sub",)),
        )
    )
    adjusted = adjusted_observation_manifest(
        manifest,
        excluded=frozenset({Path(".draft/operations/x")}),
        explained=frozenset(),
        added={Path("pages/topic"): frozenset({"new.md"})},
        removed={},
    )
    topic = adjusted.for_path(Path("pages/topic"))
    assert topic is not None
    assert topic.members == ("new.md", "sub")


# ---------------------------------------------------------------------------
# _classify_observation：按 EntryKind 区分，不依赖 sha256=None
# ---------------------------------------------------------------------------


def _journal_mutation(
    path: Path, kind: MutationKind, before: str | None, after: str | None
) -> JournalMutation:
    return JournalMutation(
        path=path,
        kind=kind,
        before_sha256=before,
        after_sha256=after,
        original=None,
        staged=None,
        applied=False,
    )


def test_classify_observation_new_file_requires_missing_not_sha256_none() -> None:
    """新建文件（before=None）只有 MISSING 才是 before；目录/符号链接等 sha256=None
    的路径类型变化必须分类为 unknown，不能被误当成本操作创建的文件前态。"""
    from draft.operations import _classify_observation  # pyright: ignore[reportPrivateUsage]

    mutation = _journal_mutation(Path("pages/new.md"), MutationKind.WRITE, None, "after")
    assert (
        _classify_observation(
            mutation, Observation(path=Path("pages/new.md"), kind=EntryKind.MISSING)
        )
        == "before"
    )
    assert (
        _classify_observation(
            mutation,
            Observation(path=Path("pages/new.md"), kind=EntryKind.DIRECTORY, members=()),
        )
        == "unknown"
    )
    assert (
        _classify_observation(
            mutation, Observation(path=Path("pages/new.md"), kind=EntryKind.SYMLINK)
        )
        == "unknown"
    )


def test_classify_observation_delete_requires_file_before() -> None:
    """DELETE 的 before 态必须是 FILE 摘要匹配；目录占位为 unknown。"""
    from draft.operations import _classify_observation  # pyright: ignore[reportPrivateUsage]

    mutation = _journal_mutation(Path("pages/a.md"), MutationKind.DELETE, "before", None)
    assert (
        _classify_observation(
            mutation,
            Observation(path=Path("pages/a.md"), kind=EntryKind.FILE, sha256="before"),
        )
        == "before"
    )
    assert (
        _classify_observation(
            mutation, Observation(path=Path("pages/a.md"), kind=EntryKind.MISSING)
        )
        == "after"
    )
    assert (
        _classify_observation(
            mutation, Observation(path=Path("pages/a.md"), kind=EntryKind.DIRECTORY, members=())
        )
        == "unknown"
    )


def test_classify_observation_write_distinguishes_by_digest_on_file() -> None:
    from draft.operations import _classify_observation  # pyright: ignore[reportPrivateUsage]

    mutation = _journal_mutation(Path("pages/a.md"), MutationKind.WRITE, "before", "after")
    assert (
        _classify_observation(
            mutation,
            Observation(path=Path("pages/a.md"), kind=EntryKind.FILE, sha256="after"),
        )
        == "after"
    )
    assert (
        _classify_observation(
            mutation,
            Observation(path=Path("pages/a.md"), kind=EntryKind.FILE, sha256="before"),
        )
        == "before"
    )
    assert (
        _classify_observation(
            mutation,
            Observation(path=Path("pages/a.md"), kind=EntryKind.DIRECTORY, members=()),
        )
        == "unknown"
    )


# ---------------------------------------------------------------------------
# explained_directory_deltas：纯函数，不访问文件系统
# ---------------------------------------------------------------------------


def test_explained_directory_deltas_creates_ancestor_chain() -> None:
    """新建文件解释直接父目录与缺失祖先目录；删除只改直接父目录。"""
    added, removed = explained_directory_deltas(
        created_paths=frozenset({Path("pages/topic/index.md"), Path(".draft/operations/x")}),
        deleted_paths=frozenset({Path("pages/topic.md")}),
    )
    # 新文件：pages/topic 增 index.md，pages 增 topic（缺失祖先）。
    assert added[Path("pages/topic")] == {"index.md"}
    assert added[Path("pages")] == {"topic"}
    # 操作日志目录链：.draft/operations 增 x，.draft 增 operations。
    assert added[Path(".draft/operations")] == {"x"}
    assert added[Path(".draft")] == {"operations"}
    # 删除只改直接父目录。
    assert removed[Path("pages")] == {"topic.md"}
    assert Path("pages/topic") not in removed


def test_verify_recovery_uses_probe_not_direct_fs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """注入 fake 探针时，mutation 分类与目录比较都走探针，不访问真实文件系统。"""
    import draft.operations as ops_module

    class FakeProbe:
        def __init__(self, states: dict[Path, Observation]) -> None:
            self.states = states

        def observe(self, path: Path) -> Observation:
            return self.states.get(path, Observation(path=path, kind=EntryKind.MISSING))

    new_sha = "a" * 64
    probe = FakeProbe(
        {
            Path("pages/new.md"): Observation(Path("pages/new.md"), EntryKind.FILE, sha256=new_sha),
            Path("pages"): directory_observation(Path("pages"), ("new.md",)),
        }
    )

    def boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("sha256_file must not be called with injected probe")

    monkeypatch.setattr(ops_module, "sha256_file", boom)

    journal = Journal(
        operation_id="a" * 32,
        kind=OperationKind.CREATE,
        phase=OperationPhase.APPLYING,
        root_state=RootState.REGISTERED,
        observations=ObservationManifest(entries=(directory_observation(Path("pages"), ()),)),
        mutations=(
            JournalMutation(
                Path("pages/new.md"),
                MutationKind.WRITE,
                None,
                new_sha,
                None,
                None,
                True,
            ),
        ),
        remove_empty_directories=(),
        metadata={},
        directory=Path(".draft/operations") / ("a" * 32),
    )
    changed, _ = _verify_observations_for_recovery(
        Path("."),
        journal,
        (),
        probe=probe,  # type: ignore[arg-type]
    )
    # 新文件 at after：pages 目录成员变化可解释，无外部变化。
    assert changed == ()


# ---------------------------------------------------------------------------
# TrashDeadlines.from_created_at
# ---------------------------------------------------------------------------


def test_trash_deadlines_freeze_all_absolute_times() -> None:
    created = datetime(2026, 8, 6, 12, tzinfo=UTC)
    deadlines = TrashDeadlines.from_created_at(created)
    assert deadlines.to_metadata() == {
        "created_at": "2026-08-06T12:00:00Z",
        "restore_expires_at": "2026-08-13T12:00:00Z",
        "purge_eligible_at": "2026-09-12T12:00:00Z",
        "next_handoff_attempt_at": "2026-08-13T12:00:00Z",
    }


def test_trash_deadlines_from_created_at_rejects_naive_datetime() -> None:
    with pytest.raises(DraftError, match="operation.time_invalid"):
        TrashDeadlines.from_created_at(datetime(2026, 8, 6, 12))


def test_trash_deadlines_from_created_at_rejects_non_utc_datetime() -> None:
    non_utc = timezone(timedelta(hours=2))
    with pytest.raises(DraftError, match="operation.time_invalid"):
        TrashDeadlines.from_created_at(datetime(2026, 8, 6, 12, tzinfo=non_utc))


# ---------------------------------------------------------------------------
# TrashDeadlines.parse
# ---------------------------------------------------------------------------


def test_trash_deadlines_parse_round_trips_to_metadata() -> None:
    created = datetime(2026, 8, 6, 12, tzinfo=UTC)
    original = TrashDeadlines.from_created_at(created)
    parsed = TrashDeadlines.parse(original.to_metadata())
    assert parsed == original


def test_trash_deadlines_parse_rejects_missing_field() -> None:
    metadata = {
        "created_at": "2026-08-06T12:00:00Z",
        "restore_expires_at": "2026-08-13T12:00:00Z",
        "purge_eligible_at": "2026-09-12T12:00:00Z",
        # next_handoff_attempt_at 缺失
    }
    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        TrashDeadlines.parse(metadata)


def test_trash_deadlines_parse_rejects_naive_datetime() -> None:
    metadata = {
        "created_at": "2026-08-06T12:00:00",
        "restore_expires_at": "2026-08-13T12:00:00Z",
        "purge_eligible_at": "2026-09-12T12:00:00Z",
        "next_handoff_attempt_at": "2026-08-13T12:00:00Z",
    }
    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        TrashDeadlines.parse(metadata)


def test_trash_deadlines_parse_rejects_non_utc_datetime() -> None:
    metadata = {
        "created_at": "2026-08-06T12:00:00+02:00",
        "restore_expires_at": "2026-08-13T12:00:00Z",
        "purge_eligible_at": "2026-09-12T12:00:00Z",
        "next_handoff_attempt_at": "2026-08-13T12:00:00Z",
    }
    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        TrashDeadlines.parse(metadata)


def test_trash_deadlines_parse_rejects_inconsistent_restore_expires_at() -> None:
    metadata = {
        "created_at": "2026-08-06T12:00:00Z",
        "restore_expires_at": "2026-08-14T12:00:00Z",
        "purge_eligible_at": "2026-09-12T12:00:00Z",
        "next_handoff_attempt_at": "2026-08-13T12:00:00Z",
    }
    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        TrashDeadlines.parse(metadata)


def test_trash_deadlines_parse_rejects_inconsistent_purge_eligible_at() -> None:
    metadata = {
        "created_at": "2026-08-06T12:00:00Z",
        "restore_expires_at": "2026-08-13T12:00:00Z",
        "purge_eligible_at": "2026-10-12T12:00:00Z",
        "next_handoff_attempt_at": "2026-08-13T12:00:00Z",
    }
    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        TrashDeadlines.parse(metadata)


def test_trash_deadlines_parse_rejects_next_attempt_before_restore() -> None:
    metadata = {
        "created_at": "2026-08-06T12:00:00Z",
        "restore_expires_at": "2026-08-13T12:00:00Z",
        "purge_eligible_at": "2026-09-12T12:00:00Z",
        "next_handoff_attempt_at": "2026-08-12T12:00:00Z",
    }
    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        TrashDeadlines.parse(metadata)


def test_trash_deadlines_parse_accepts_next_attempt_at_or_after_restore() -> None:
    metadata = {
        "created_at": "2026-08-06T12:00:00Z",
        "restore_expires_at": "2026-08-13T12:00:00Z",
        "purge_eligible_at": "2026-09-12T12:00:00Z",
        "next_handoff_attempt_at": "2026-08-13T12:00:00Z",
    }
    parsed = TrashDeadlines.parse(metadata)
    assert parsed.next_handoff_attempt_at == parsed.restore_expires_at


def test_trash_deadlines_parse_accepts_next_attempt_after_restore() -> None:
    metadata = {
        "created_at": "2026-08-06T12:00:00Z",
        "restore_expires_at": "2026-08-13T12:00:00Z",
        "purge_eligible_at": "2026-09-12T12:00:00Z",
        "next_handoff_attempt_at": "2026-08-14T12:00:00Z",
    }
    parsed = TrashDeadlines.parse(metadata)
    assert parsed.next_handoff_attempt_at > parsed.restore_expires_at


# ---------------------------------------------------------------------------
# normalize_utc / format_utc
# ---------------------------------------------------------------------------


def test_normalize_utc_rejects_naive_datetime() -> None:
    with pytest.raises(DraftError, match="operation.time_invalid"):
        normalize_utc(datetime(2026, 8, 6, 12))


def test_normalize_utc_rejects_non_utc_datetime() -> None:
    non_utc = timezone(timedelta(hours=2))
    with pytest.raises(DraftError, match="operation.time_invalid"):
        normalize_utc(datetime(2026, 8, 6, 12, tzinfo=non_utc))


def test_normalize_utc_returns_utc_input_unchanged() -> None:
    moment = datetime(2026, 8, 6, 12, tzinfo=UTC)
    assert normalize_utc(moment) == moment


def test_normalize_utc_defaults_to_now_utc() -> None:
    result = normalize_utc(None)
    assert result.utcoffset() == timedelta(0)


def test_format_utc_produces_z_suffix() -> None:
    moment = datetime(2026, 8, 6, 12, 0, 0, tzinfo=UTC)
    assert format_utc(moment) == "2026-08-06T12:00:00Z"


# ---------------------------------------------------------------------------
# sha256_bytes / validate_operation_id
# ---------------------------------------------------------------------------


def test_sha256_bytes_is_deterministic() -> None:
    assert sha256_bytes(b"hello") == (
        "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    )


def test_validate_operation_id_accepts_32_hex_lowercase() -> None:
    value = "a" * 32
    assert validate_operation_id(value) == value


@pytest.mark.parametrize("bad", ["", "a" * 31, "a" * 33, "A" * 32, "g" * 32, "../x", "/etc/passwd"])
def test_validate_operation_id_rejects_invalid(bad: str) -> None:
    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        validate_operation_id(bad)


def test_validate_operation_id_uses_custom_code() -> None:
    with pytest.raises(DraftError, match="operation.plan_invalid"):
        validate_operation_id("bad", code="operation.plan_invalid")


# ---------------------------------------------------------------------------
# Journal 不可变转换
# ---------------------------------------------------------------------------


def _journal() -> Journal:
    return Journal(
        operation_id="a" * 32,
        kind=OperationKind.MOVE,
        phase=OperationPhase.PREPARED,
        root_state=RootState.REGISTERED,
        observations=EMPTY_OBSERVATIONS,
        mutations=(
            JournalMutation(
                path=Path("pages/a.md"),
                kind=MutationKind.WRITE,
                before_sha256="before-hash",
                after_sha256="after-hash",
                original=Path("originals/0000.bin"),
                staged=Path("staged/0000.bin"),
                applied=False,
            ),
        ),
        remove_empty_directories=(Path("pages/old"),),
        metadata={"key1": "value1", "key2": "value2"},
        directory=Path("/draft/.draft/operations/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
    )


def test_with_phase_changes_only_phase_and_preserves_all_other_fields() -> None:
    original = _journal()
    updated = with_phase(original, OperationPhase.APPLYING)
    assert updated == dataclasses.replace(original, phase=OperationPhase.APPLYING)


def test_with_metadata_merges_extra_and_preserves_all_other_fields() -> None:
    original = _journal()
    extra = {"key3": "value3"}
    updated = with_metadata(original, extra)
    assert updated == dataclasses.replace(original, metadata={**original.metadata, **extra})


def test_with_metadata_overwrites_existing_key() -> None:
    original = _journal()
    updated = with_metadata(original, {"key1": "new"})
    assert updated.metadata == {"key1": "new", "key2": "value2"}


def test_without_metadata_removes_key_and_preserves_all_other_fields() -> None:
    original = _journal()
    updated = without_metadata(original, "key1")
    assert updated == dataclasses.replace(original, metadata={"key2": "value2"})


def test_without_metadata_nonexistent_key_preserves_all_fields() -> None:
    original = _journal()
    updated = without_metadata(original, "nonexistent")
    assert updated == dataclasses.replace(original, metadata=original.metadata)


# ---------------------------------------------------------------------------
# has_handoff_failure / handoff_failure_metadata
# ---------------------------------------------------------------------------


def test_has_handoff_failure_true_for_complete_record() -> None:
    metadata = {
        HANDOFF_ERROR_TYPE_KEY: "OSError",
        HANDOFF_ERROR_MESSAGE_KEY: "disk full",
        HANDOFF_ATTEMPTED_AT_KEY: "2026-08-06T12:00:00Z",
    }
    assert has_handoff_failure(metadata) is True


def test_has_handoff_failure_false_when_error_type_missing() -> None:
    metadata = {
        HANDOFF_ERROR_MESSAGE_KEY: "disk full",
        HANDOFF_ATTEMPTED_AT_KEY: "2026-08-06T12:00:00Z",
    }
    assert has_handoff_failure(metadata) is False


def test_has_handoff_failure_false_when_message_missing() -> None:
    metadata = {
        HANDOFF_ERROR_TYPE_KEY: "OSError",
        HANDOFF_ATTEMPTED_AT_KEY: "2026-08-06T12:00:00Z",
    }
    assert has_handoff_failure(metadata) is False


def test_has_handoff_failure_false_when_attempted_missing() -> None:
    metadata = {
        HANDOFF_ERROR_TYPE_KEY: "OSError",
        HANDOFF_ERROR_MESSAGE_KEY: "disk full",
    }
    assert has_handoff_failure(metadata) is False


def test_has_handoff_failure_false_when_attempted_is_naive() -> None:
    metadata = {
        HANDOFF_ERROR_TYPE_KEY: "OSError",
        HANDOFF_ERROR_MESSAGE_KEY: "disk full",
        HANDOFF_ATTEMPTED_AT_KEY: "2026-08-06T12:00:00",
    }
    assert has_handoff_failure(metadata) is False


def test_has_handoff_failure_false_when_attempted_is_non_utc() -> None:
    metadata = {
        HANDOFF_ERROR_TYPE_KEY: "OSError",
        HANDOFF_ERROR_MESSAGE_KEY: "disk full",
        HANDOFF_ATTEMPTED_AT_KEY: "2026-08-06T12:00:00+02:00",
    }
    assert has_handoff_failure(metadata) is False


def test_handoff_failure_metadata_records_structured_fields() -> None:
    error = OSError("disk full")
    attempted_at = datetime(2026, 8, 6, 12, tzinfo=UTC)
    metadata = handoff_failure_metadata(error, attempted_at=attempted_at)
    assert metadata == {
        HANDOFF_ERROR_TYPE_KEY: "OSError",
        HANDOFF_ERROR_MESSAGE_KEY: "disk full",
        HANDOFF_ATTEMPTED_AT_KEY: "2026-08-06T12:00:00Z",
    }


# ---------------------------------------------------------------------------
# 恢复公开路径：public_changed_paths 元数据解析与安全回退
# ---------------------------------------------------------------------------


def _journal_with_metadata(metadata: dict[str, str]) -> Journal:
    return Journal(
        operation_id="a" * 32,
        kind=OperationKind.GENERATE_CONCLUSIONS,
        phase=OperationPhase.COMPLETED,
        root_state=RootState.REGISTERED,
        observations=EMPTY_OBSERVATIONS,
        mutations=(
            JournalMutation(
                path=Path("conclusions.md"),
                kind=MutationKind.WRITE,
                before_sha256="b",
                after_sha256="a",
                original=None,
                staged=None,
                applied=True,
            ),
            JournalMutation(
                path=Path("index.md"),
                kind=MutationKind.WRITE,
                before_sha256="b",
                after_sha256="a",
                original=None,
                staged=None,
                applied=True,
            ),
            JournalMutation(
                path=Path(".draft/draft.json"),
                kind=MutationKind.WRITE,
                before_sha256="b",
                after_sha256="a",
                original=None,
                staged=None,
                applied=True,
            ),
        ),
        remove_empty_directories=(),
        metadata=metadata,
        directory=Path("/draft/.draft/operations/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
    )


def test_persisted_public_paths_returns_parsed_paths() -> None:
    from draft.operations import _persisted_public_paths  # pyright: ignore[reportPrivateUsage]

    journal = _journal_with_metadata(
        {"public_changed_paths": json.dumps(["conclusions.md", "index.md"])}
    )
    assert _persisted_public_paths(journal) == (Path("conclusions.md"), Path("index.md"))


def test_persisted_public_paths_missing_returns_none() -> None:
    from draft.operations import _persisted_public_paths  # pyright: ignore[reportPrivateUsage]

    assert _persisted_public_paths(_journal_with_metadata({})) is None


@pytest.mark.parametrize(
    "raw",
    ["not-json", '{"a": 1}', '"str"', "42", "[1]", '["conclusions.md", 2]'],
)
def test_persisted_public_paths_malformed_raises(raw: str) -> None:
    """元数据存在但损坏时明确停止，不静默回退（规格 §13 不公开内部路径）。"""
    from draft.operations import _persisted_public_paths  # pyright: ignore[reportPrivateUsage]

    journal = _journal_with_metadata({"public_changed_paths": raw})
    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        _persisted_public_paths(journal)


@pytest.mark.parametrize(
    "entry",
    ["/etc/passwd", "../secret.md", ".draft/draft.json", "pages/../a.md", "a/../../b.md"],
)
def test_persisted_public_paths_unsafe_entry_raises(entry: str) -> None:
    """合法 JSON 但包含绝对路径、.. 或 .draft/ 内部路径的条目视为损坏。"""
    from draft.operations import _persisted_public_paths  # pyright: ignore[reportPrivateUsage]

    journal = _journal_with_metadata(
        {"public_changed_paths": json.dumps(["conclusions.md", entry])}
    )
    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        _persisted_public_paths(journal)


def test_recover_changed_paths_prefers_persisted() -> None:
    from draft.operations import _recover_changed_paths  # pyright: ignore[reportPrivateUsage]

    journal = _journal_with_metadata({"public_changed_paths": json.dumps(["conclusions.md"])})
    assert _recover_changed_paths(journal) == (Path("conclusions.md"),)


def test_recover_changed_paths_missing_returns_public_mutations_only() -> None:
    """旧日志缺元数据时只回退到公开安全的 mutation 路径，绝不公开 .draft/。"""
    from draft.operations import _recover_changed_paths  # pyright: ignore[reportPrivateUsage]

    result = _recover_changed_paths(_journal_with_metadata({}))
    assert result == (Path("conclusions.md"), Path("index.md"))
    assert all(".draft" not in path.parts for path in result)


# ---------------------------------------------------------------------------
# _parse_journal：单一解析边界的形状与协议验证（回归 H5）
# ---------------------------------------------------------------------------


def _journal_payload(
    mutations: list[dict[str, object]],
    *,
    operation_id: str = "a" * 32,
) -> dict[str, object]:
    """构造解析输入；mutation 缺省字段由各用例显式给出，不再复制默认值。"""
    return {
        "journal_version": JOURNAL_VERSION,
        "operation_id": operation_id,
        "kind": OperationKind.CREATE.value,
        "phase": OperationPhase.PREPARED.value,
        "root_state": RootState.REGISTERED.value,
        "observations": [],
        "mutations": mutations,
        "remove_empty_directories": [],
        "metadata": {},
    }


def _parse_path(operation_id: str = "a" * 32) -> Path:
    return Path(f"{operation_id}/journal.json")


def _write_mutation(
    index: int,
    *,
    path: str = "pages/a.md",
    before: str | None = None,
    staged: str | None | object = ...,
) -> dict[str, object]:
    return {
        "path": path,
        "kind": "write",
        "before_sha256": before,
        "after_sha256": "b" * 64,
        "original": f"originals/{index:04d}.bin" if before is not None else None,
        "staged": f"staged/{index:04d}.bin" if staged is ... else staged,
        "applied": False,
    }


def _delete_mutation(
    index: int,
    *,
    path: str = "pages/d.md",
    original: str | None | object = ...,
) -> dict[str, object]:
    return {
        "path": path,
        "kind": "delete",
        "before_sha256": "c" * 64,
        "after_sha256": None,
        "original": f"originals/{index:04d}.bin" if original is ... else original,
        "staged": None,
        "applied": False,
    }


def test_parse_journal_valid_write_and_delete_yields_constrained_paths() -> None:
    journal = _parse_journal(
        _journal_payload(
            [
                _write_mutation(0, before="a" * 64),
                _delete_mutation(1),
            ]
        ),
        _parse_path(),
    )
    write, delete = journal.mutations
    assert write.original == Path("originals/0000.bin")
    assert write.staged == Path("staged/0000.bin")
    assert delete.original == Path("originals/0001.bin")
    assert delete.staged is None


def test_parse_journal_accepts_new_write_without_original() -> None:
    journal = _parse_journal(_journal_payload([_write_mutation(0)]), _parse_path())
    assert journal.mutations[0].original is None
    assert journal.mutations[0].staged == Path("staged/0000.bin")


@pytest.mark.parametrize(
    "field",
    ["/etc/passwd", "../escape.bin", "originals/../x.bin", "staged/0000.bin", "other/0000.bin"],
)
def test_parse_journal_rejects_unsafe_original_before_any_io(field: str) -> None:
    bad = _write_mutation(0, before="a" * 64)
    bad["original"] = field
    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        _parse_journal(_journal_payload([bad]), _parse_path())


def test_parse_journal_rejects_wrong_original_index() -> None:
    bad = _write_mutation(0, before="a" * 64)
    bad["original"] = "originals/0001.bin"
    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        _parse_journal(_journal_payload([bad]), _parse_path())


def test_parse_journal_rejects_staged_on_delete() -> None:
    bad = _delete_mutation(0)
    bad["staged"] = "staged/0000.bin"
    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        _parse_journal(_journal_payload([bad]), _parse_path())


def test_parse_journal_rejects_staged_missing_on_write() -> None:
    bad = _write_mutation(0)
    bad["staged"] = None
    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        _parse_journal(_journal_payload([bad]), _parse_path())


def test_parse_journal_rejects_original_missing_when_before_present() -> None:
    bad = _write_mutation(0, before="a" * 64)
    bad["original"] = None
    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        _parse_journal(_journal_payload([bad]), _parse_path())


def test_parse_journal_rejects_original_present_on_new_write() -> None:
    bad = _write_mutation(0)
    bad["original"] = "originals/0000.bin"
    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        _parse_journal(_journal_payload([bad]), _parse_path())


@pytest.mark.parametrize("hash_field", ["before_sha256", "after_sha256"])
def test_parse_journal_rejects_non_64hex_hash(hash_field: str) -> None:
    bad = _write_mutation(0)
    bad[hash_field] = "deadbeef"
    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        _parse_journal(_journal_payload([bad]), _parse_path())


def test_parse_journal_rejects_delete_without_before() -> None:
    bad = _delete_mutation(0)
    bad["before_sha256"] = None
    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        _parse_journal(_journal_payload([bad]), _parse_path())


def test_parse_journal_rejects_delete_with_after() -> None:
    bad = _delete_mutation(0)
    bad["after_sha256"] = "b" * 64
    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        _parse_journal(_journal_payload([bad]), _parse_path())


def test_parse_journal_rejects_write_without_after() -> None:
    bad = _write_mutation(0)
    bad["after_sha256"] = None
    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        _parse_journal(_journal_payload([bad]), _parse_path())


def test_parse_journal_rejects_duplicate_target_path() -> None:
    mutations = [
        _write_mutation(0, path="pages/a.md"),
        _write_mutation(1, path="pages/a.md", before="a" * 64),
    ]
    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        _parse_journal(_journal_payload(mutations), _parse_path())


def test_parse_journal_rejects_duplicate_original_path() -> None:
    first = _write_mutation(0, before="a" * 64)
    second = _write_mutation(1, path="pages/b.md", before="b" * 64)
    second["original"] = "originals/0000.bin"
    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        _parse_journal(_journal_payload([first, second]), _parse_path())


def test_parse_journal_rejects_duplicate_staged_path() -> None:
    first = _write_mutation(0)
    second = _write_mutation(1, path="pages/b.md")
    second["staged"] = "staged/0000.bin"
    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        _parse_journal(_journal_payload([first, second]), _parse_path())


@pytest.mark.parametrize("path", ["", ".", "./", "pages/."])
def test_parse_journal_rejects_empty_or_dot_mutation_path(path: str) -> None:
    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        _parse_journal(_journal_payload([_write_mutation(0, path=path)]), _parse_path())


# ---------------------------------------------------------------------------
# _write_recovery_decision：WRITE 恢复决策表（回归 H3）
# ---------------------------------------------------------------------------

_BEFORE = "b" * 64
_AFTER = "a" * 64


@pytest.mark.parametrize(
    "target_kind,target_digest,temp_kind,temp_digest,before,expected",
    [
        # target=before（文件摘要匹配 before；新建文件 before=None 且尚未创建）：
        (EntryKind.FILE, _BEFORE, EntryKind.MISSING, None, _BEFORE, "forward"),
        (EntryKind.FILE, _BEFORE, EntryKind.FILE, _AFTER, _BEFORE, "consume"),
        (EntryKind.FILE, _BEFORE, EntryKind.FILE, "x" * 64, _BEFORE, "external"),
        (EntryKind.FILE, _BEFORE, EntryKind.DIRECTORY, None, _BEFORE, "external"),
        (EntryKind.FILE, _BEFORE, EntryKind.SYMLINK, None, _BEFORE, "external"),
        (EntryKind.FILE, _BEFORE, EntryKind.OTHER, None, _BEFORE, "external"),
        (EntryKind.MISSING, None, EntryKind.MISSING, None, None, "forward"),
        (EntryKind.MISSING, None, EntryKind.FILE, _AFTER, None, "consume"),
        # target=after（文件摘要匹配 after）：任何共存 temp 都是外部变化。
        (EntryKind.FILE, _AFTER, EntryKind.MISSING, None, _BEFORE, "forward"),
        (EntryKind.FILE, _AFTER, EntryKind.FILE, _AFTER, _BEFORE, "external"),
        (EntryKind.FILE, _AFTER, EntryKind.FILE, "x" * 64, _BEFORE, "external"),
        (EntryKind.FILE, _AFTER, EntryKind.DIRECTORY, None, _BEFORE, "external"),
        (EntryKind.FILE, _AFTER, EntryKind.SYMLINK, None, _BEFORE, "external"),
        # target=unknown：一律 external。
        (EntryKind.DIRECTORY, None, EntryKind.MISSING, None, _BEFORE, "external"),
        (EntryKind.FILE, "y" * 64, EntryKind.MISSING, None, _BEFORE, "external"),
        (EntryKind.MISSING, None, EntryKind.MISSING, None, _BEFORE, "external"),
        (EntryKind.SYMLINK, None, EntryKind.FILE, _AFTER, None, "external"),
    ],
)
def test_write_recovery_decision_table(
    target_kind: EntryKind,
    target_digest: str | None,
    temp_kind: EntryKind,
    temp_digest: str | None,
    before: str | None,
    expected: str,
) -> None:
    """决策表预期由测试数据独立给出，不复制生产算法。"""
    assert (
        _write_recovery_decision(
            before=before,
            after=_AFTER,
            target_kind=target_kind,
            target_digest=target_digest,
            temp_kind=temp_kind,
            temp_digest=temp_digest,
        )
        == expected
    )
