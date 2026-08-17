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
    validate_journal_transition,
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
    kind: str = OperationKind.CREATE.value,
    phase: str = OperationPhase.PREPARED.value,
) -> dict[str, object]:
    """构造解析输入；mutation 缺省字段由各用例显式给出，不再复制默认值。"""
    return {
        "journal_version": JOURNAL_VERSION,
        "operation_id": operation_id,
        "kind": kind,
        "phase": phase,
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


# ---------------------------------------------------------------------------
# validate_journal_transition：日志单步后继的权威转换验证器（回归 H1）
# ---------------------------------------------------------------------------


def _j(
    phase: OperationPhase = OperationPhase.ALLOCATING,
    *,
    kind: OperationKind = OperationKind.CREATE,
    root_state: RootState = RootState.REGISTERED,
    op_id: str = "a" * 32,
    mutations: tuple[JournalMutation, ...] = (),
    observations: ObservationManifest = EMPTY_OBSERVATIONS,
    remove_empty: tuple[Path, ...] = (),
    metadata: dict[str, str] | None = None,
) -> Journal:
    return Journal(
        operation_id=op_id,
        kind=kind,
        phase=phase,
        root_state=root_state,
        observations=observations,
        mutations=mutations,
        remove_empty_directories=remove_empty,
        metadata=dict(metadata or {}),
        directory=Path(".draft/operations") / op_id,
    )


def _m(
    path: str = "pages/a.md",
    *,
    kind: MutationKind = MutationKind.WRITE,
    before: str | None = None,
    after: str | None = "a" * 64,
    original: Path | None = None,
    staged: Path | None = Path("staged/0000.bin"),
    applied: bool = False,
) -> JournalMutation:
    return JournalMutation(Path(path), kind, before, after, original, staged, applied)


def test_journal_transition_bootstrap_accepts_allocating_empty() -> None:
    """bootstrap：allocating、空 mutation、空待删目录是合法初始日志。"""
    validate_journal_transition(None, _j(), path=Path("journal.json"))


def test_journal_transition_bootstrap_rejects_phase() -> None:
    """bootstrap 只允许 allocating：其他 phase 的 pending 不得发布。"""
    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        validate_journal_transition(None, _j(OperationPhase.PREPARED), path=Path("journal.json"))


def test_journal_transition_bootstrap_rejects_mutations() -> None:
    """bootstrap 只允许空 mutation：真实 bootstrap 只能产生初始 allocating。"""
    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        validate_journal_transition(None, _j(mutations=(_m(),)), path=Path("journal.json"))


def test_journal_transition_bootstrap_rejects_remove_empty() -> None:
    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        validate_journal_transition(
            None, _j(remove_empty=(Path("pages/topic"),)), path=Path("journal.json")
        )


def test_journal_transition_rejects_identity_rewrite() -> None:
    """identity（kind/root_state/operation_id/目录）不可改写。"""
    old = _j(OperationPhase.ALLOCATING)
    for rewrite in (
        _j(OperationPhase.ALLOCATING, kind=OperationKind.MOVE),
        _j(OperationPhase.ALLOCATING, root_state=RootState.CANDIDATE),
        _j(OperationPhase.ALLOCATING, op_id="b" * 32),
    ):
        with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
            validate_journal_transition(old, rewrite, path=Path("journal.json"))


def test_journal_transition_rejects_phase_regression() -> None:
    """阶段倒退：applying→allocating、terminal→mid 都拒绝。"""
    frozen = (_m(applied=False),)
    applying = _j(OperationPhase.APPLYING, mutations=frozen)
    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        validate_journal_transition(
            applying, _j(OperationPhase.ALLOCATING, mutations=frozen), path=Path("journal.json")
        )
    completed = _j(OperationPhase.COMPLETED, mutations=frozen)
    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        validate_journal_transition(
            completed, _j(OperationPhase.APPLYING, mutations=frozen), path=Path("journal.json")
        )


def test_journal_transition_rejects_skipped_commit_point() -> None:
    """跳过提交点：prepared→validating 不在状态图边上。"""
    frozen = (_m(applied=False),)
    prepared = _j(OperationPhase.PREPARED, mutations=frozen)
    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        validate_journal_transition(
            prepared, _j(OperationPhase.VALIDATING, mutations=frozen), path=Path("journal.json")
        )


def test_journal_transition_allows_adjacent_forward_edges() -> None:
    """正向相邻边合法：prepared→applying→validating→completed。"""
    frozen = (_m(applied=False),)
    prepared = _j(OperationPhase.PREPARED, mutations=frozen)
    applying = _j(OperationPhase.APPLYING, mutations=frozen)
    validating = _j(OperationPhase.VALIDATING, mutations=frozen)
    completed = _j(OperationPhase.COMPLETED, mutations=frozen)
    validate_journal_transition(prepared, applying, path=Path("journal.json"))
    validate_journal_transition(applying, validating, path=Path("journal.json"))
    validate_journal_transition(validating, completed, path=Path("journal.json"))


def test_journal_transition_rejects_mutation_replace() -> None:
    """已冻结 mutation 不可替换 path/摘要/恢复材料。"""
    old = _j(OperationPhase.APPLYING, mutations=(_m(path="pages/a.md"),))
    for replacement in (
        _j(OperationPhase.APPLYING, mutations=(_m(path="pages/b.md"),)),
        _j(OperationPhase.APPLYING, mutations=(_m(after="b" * 64),)),
        _j(OperationPhase.APPLYING, mutations=(_m(staged=Path("staged/0001.bin")),)),
    ):
        with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
            validate_journal_transition(old, replacement, path=Path("journal.json"))


def test_journal_transition_rejects_mutation_add_and_remove() -> None:
    old = _j(OperationPhase.APPLYING, mutations=(_m(),))
    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        validate_journal_transition(
            old,
            _j(OperationPhase.APPLYING, mutations=(_m(), _m(path="pages/b.md"))),
            path=Path("journal.json"),
        )
    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        validate_journal_transition(
            old, _j(OperationPhase.APPLYING, mutations=()), path=Path("journal.json")
        )


def test_journal_transition_applied_flag_forward_only() -> None:
    """applied 只能 false→true 单向推进，不得反转。"""
    old = _j(OperationPhase.APPLYING, mutations=(_m(applied=True),))
    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        validate_journal_transition(
            old,
            _j(OperationPhase.APPLYING, mutations=(_m(applied=False),)),
            path=Path("journal.json"),
        )
    # 保持已应用状态（如 adopt 恢复直接终态化）合法。
    validate_journal_transition(
        old,
        _j(OperationPhase.APPLYING, mutations=(_m(applied=True),)),
        path=Path("journal.json"),
    )


def test_journal_transition_observations_only_allocating() -> None:
    """观察清单只允许 allocating→allocating 变化，其余必须一致。"""
    entry = file_observation(Path("pages/a.md"), b"a" * 64)
    manifest = ObservationManifest(entries=(entry,))
    old = _j(OperationPhase.ALLOCATING)
    validate_journal_transition(
        old, _j(OperationPhase.ALLOCATING, observations=manifest), path=Path("journal.json")
    )
    frozen = (_m(applied=False),)
    prepared = _j(OperationPhase.PREPARED, mutations=frozen)
    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        validate_journal_transition(
            prepared,
            _j(OperationPhase.APPLYING, mutations=frozen, observations=manifest),
            path=Path("journal.json"),
        )


def test_journal_transition_metadata_restore_claim_only() -> None:
    """trash_retained→trash_retained 只允许增删 restore_operation_id。"""
    op_id = "a" * 32
    old = _j(OperationPhase.TRASH_RETAINED, kind=OperationKind.TRASH, op_id=op_id)
    claimed = _j(
        OperationPhase.TRASH_RETAINED,
        kind=OperationKind.TRASH,
        op_id=op_id,
        metadata={"restore_operation_id": "b" * 32},
    )
    validate_journal_transition(old, claimed, path=Path("journal.json"))
    unclaimed = _j(OperationPhase.TRASH_RETAINED, kind=OperationKind.TRASH, op_id=op_id)
    validate_journal_transition(claimed, unclaimed, path=Path("journal.json"))
    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        validate_journal_transition(
            old,
            _j(
                OperationPhase.TRASH_RETAINED,
                kind=OperationKind.TRASH,
                op_id=op_id,
                metadata={"restore_operation_id": "b" * 32, "foreign": "x"},
            ),
            path=Path("journal.json"),
        )


def test_journal_transition_metadata_handoff_failure_and_disposition() -> None:
    """handoff 失败记录/终态处置只允许拥有它们的转换改变。"""
    op_id = "a" * 32
    pending = _j(
        OperationPhase.TRASH_HANDOFF_PENDING,
        kind=OperationKind.TRASH,
        op_id=op_id,
        metadata={"created_at": "2026-01-01T00:00:00Z"},
    )
    retry = _j(
        OperationPhase.TRASH_HANDOFF_PENDING,
        kind=OperationKind.TRASH,
        op_id=op_id,
        metadata={
            "created_at": "2026-01-01T00:00:00Z",
            HANDOFF_ERROR_TYPE_KEY: "OSError",
            HANDOFF_ERROR_MESSAGE_KEY: "boom",
            HANDOFF_ATTEMPTED_AT_KEY: "2026-01-01T00:00:00Z",
        },
    )
    validate_journal_transition(pending, retry, path=Path("journal.json"))
    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        validate_journal_transition(
            pending,
            _j(
                OperationPhase.TRASH_HANDOFF_PENDING,
                kind=OperationKind.TRASH,
                op_id=op_id,
                metadata={"created_at": "2026-01-01T00:00:00Z", "foreign": "x"},
            ),
            path=Path("journal.json"),
        )
    succeeded = _j(
        OperationPhase.TRASH_HANDOFF_SUCCEEDED,
        kind=OperationKind.TRASH,
        op_id=op_id,
        metadata={"created_at": "2026-01-01T00:00:00Z"},
    )
    validate_journal_transition(
        succeeded,
        _j(
            OperationPhase.COMPLETED,
            kind=OperationKind.TRASH,
            op_id=op_id,
            metadata={"created_at": "2026-01-01T00:00:00Z", "disposition": "system_trash"},
        ),
        path=Path("journal.json"),
    )
    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        validate_journal_transition(
            succeeded,
            _j(
                OperationPhase.COMPLETED,
                kind=OperationKind.TRASH,
                op_id=op_id,
                metadata={"created_at": "2026-01-01T00:00:00Z", "foreign": "x"},
            ),
            path=Path("journal.json"),
        )


def test_journal_transition_prepared_freezes_metadata_and_mutations() -> None:
    """allocating→prepared：一次性冻结 mutations 与计划合并 metadata；之后 metadata 不可变。"""
    prepared = _j(
        OperationPhase.PREPARED,
        mutations=(_m(),),
        metadata={"operation_id": "a" * 32, "public_changed_paths": "pages/a.md"},
    )
    validate_journal_transition(_j(), prepared, path=Path("journal.json"))
    applying = _j(
        OperationPhase.APPLYING,
        mutations=(_m(),),
        metadata={"operation_id": "a" * 32, "public_changed_paths": "pages/a.md"},
    )
    validate_journal_transition(prepared, applying, path=Path("journal.json"))
    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        validate_journal_transition(
            prepared,
            _j(
                OperationPhase.APPLYING,
                mutations=(_m(),),
                metadata={"operation_id": "a" * 32, "foreign": "x"},
            ),
            path=Path("journal.json"),
        )


def test_journal_transition_allocating_to_allocating_metadata_add_only() -> None:
    """allocating→allocating：metadata 只允许单调补充，不得改写或删除。"""
    op_id = "a" * 32
    old = _j(
        OperationPhase.ALLOCATING,
        kind=OperationKind.RESTORE,
        op_id=op_id,
        metadata={"restores_trash_operation": "b" * 32},
    )
    validate_journal_transition(
        old,
        _j(
            OperationPhase.ALLOCATING,
            kind=OperationKind.RESTORE,
            op_id=op_id,
            metadata={"restores_trash_operation": "b" * 32, "extra": "x"},
        ),
        path=Path("journal.json"),
    )
    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        validate_journal_transition(
            old,
            _j(
                OperationPhase.ALLOCATING,
                kind=OperationKind.RESTORE,
                op_id=op_id,
                metadata={"restores_trash_operation": "c" * 32},
            ),
            path=Path("journal.json"),
        )
    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        validate_journal_transition(
            old,
            _j(OperationPhase.ALLOCATING, kind=OperationKind.RESTORE, op_id=op_id),
            path=Path("journal.json"),
        )


def test_journal_transition_rejects_cross_kind_edge() -> None:
    """跨 kind 的非法阶段边必须被拒绝：普通操作不得接受 adopt/trash 跳转（回归 M1）。"""
    frozen = (_m(),)
    cases: list[tuple[OperationKind, OperationPhase, OperationPhase]] = [
        (OperationKind.CREATE, OperationPhase.PREPARED, OperationPhase.COMPLETED),
        (OperationKind.CREATE, OperationPhase.APPLYING, OperationPhase.COMPLETED),
        (OperationKind.CREATE, OperationPhase.VALIDATING, OperationPhase.TRASH_RETAINED),
        (OperationKind.CREATE, OperationPhase.TRASH_RETAINED, OperationPhase.TRASH_HANDOFF_PENDING),
        (OperationKind.MOVE, OperationPhase.VALIDATING, OperationPhase.TRASH_RETAINED),
        (OperationKind.ADOPT, OperationPhase.VALIDATING, OperationPhase.TRASH_RETAINED),
        (OperationKind.ADOPT, OperationPhase.TRASH_RETAINED, OperationPhase.TRASH_RETAINED),
        (OperationKind.RESTORE, OperationPhase.TRASH_RETAINED, OperationPhase.ROLLED_BACK),
        (OperationKind.TRASH, OperationPhase.VALIDATING, OperationPhase.COMPLETED),
    ]
    for kind, old_phase, new_phase in cases:
        old = _j(old_phase, kind=kind, mutations=frozen)
        new = _j(new_phase, kind=kind, mutations=frozen)
        with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
            validate_journal_transition(old, new, path=Path("journal.json"))


def test_journal_transition_rejects_kind_without_phase() -> None:
    """kind 不拥有该 phase 时，即使边在全局表中也不得发布（解析侧，回归 M1）。"""
    from draft.operations import validate_journal_kind_phase  # pyright: ignore[reportPrivateUsage]

    for kind, phase in [
        (OperationKind.CREATE, OperationPhase.TRASH_RETAINED),
        (OperationKind.CREATE, OperationPhase.TRASH_HANDOFF_PENDING),
        (OperationKind.CREATE, OperationPhase.TRASH_PURGE_COMMITTED),
        (OperationKind.ADOPT, OperationPhase.TRASH_RETAINED),
        (OperationKind.RESTORE, OperationPhase.TRASH_RETAINED),
    ]:
        with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
            validate_journal_kind_phase(_j(phase, kind=kind), path=Path("journal.json"))


def test_journal_transition_rejects_recover_journal() -> None:
    """RECOVER 不允许形成持久 journal：任何 phase 都被拒绝。"""
    from draft.operations import validate_journal_kind_phase  # pyright: ignore[reportPrivateUsage]

    for phase in (OperationPhase.ALLOCATING, OperationPhase.PREPARED, OperationPhase.COMPLETED):
        with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
            validate_journal_kind_phase(
                _j(phase, kind=OperationKind.RECOVER), path=Path("journal.json")
            )
    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        validate_journal_transition(
            _j(OperationPhase.ALLOCATING, kind=OperationKind.RECOVER),
            _j(OperationPhase.ALLOCATING, kind=OperationKind.RECOVER),
            path=Path("journal.json"),
        )


def test_journal_transition_allows_kind_owned_edges() -> None:
    """各 kind 拥有的合法边必须被接受，防止纯表与真实 writer 漂移（回归 M1）。"""
    frozen = (_m(),)
    allowed: list[tuple[OperationKind, OperationPhase, OperationPhase]] = [
        # 普通结构操作正向链。
        (OperationKind.CREATE, OperationPhase.ALLOCATING, OperationPhase.PREPARED),
        (OperationKind.CREATE, OperationPhase.PREPARED, OperationPhase.APPLYING),
        (OperationKind.CREATE, OperationPhase.APPLYING, OperationPhase.VALIDATING),
        (OperationKind.CREATE, OperationPhase.VALIDATING, OperationPhase.COMPLETED),
        (OperationKind.CREATE, OperationPhase.ALLOCATING, OperationPhase.ROLLED_BACK),
        # ADOPT：通用正向 + marker 已生效的 prepared/applying→completed。
        (OperationKind.ADOPT, OperationPhase.PREPARED, OperationPhase.COMPLETED),
        (OperationKind.ADOPT, OperationPhase.APPLYING, OperationPhase.COMPLETED),
        (OperationKind.ADOPT, OperationPhase.ALLOCATING, OperationPhase.ROLLED_BACK),
        # TRASH：validating→trash_retained 与生命周期推进。
        (OperationKind.TRASH, OperationPhase.VALIDATING, OperationPhase.TRASH_RETAINED),
        (OperationKind.TRASH, OperationPhase.TRASH_RETAINED, OperationPhase.TRASH_HANDOFF_PENDING),
        (
            OperationKind.TRASH,
            OperationPhase.TRASH_HANDOFF_PENDING,
            OperationPhase.TRASH_PURGE_COMMITTED,
        ),
        (
            OperationKind.TRASH,
            OperationPhase.TRASH_HANDOFF_PENDING,
            OperationPhase.TRASH_HANDOFF_SUCCEEDED,
        ),
        (OperationKind.TRASH, OperationPhase.TRASH_HANDOFF_SUCCEEDED, OperationPhase.COMPLETED),
        (OperationKind.TRASH, OperationPhase.TRASH_RETAINED, OperationPhase.ROLLED_BACK),
        # RESTORE：proven-unapplied 的 prepared→rolled_back。
        (OperationKind.RESTORE, OperationPhase.PREPARED, OperationPhase.ROLLED_BACK),
        (OperationKind.RESTORE, OperationPhase.ALLOCATING, OperationPhase.ROLLED_BACK),
    ]
    for kind, old_phase, new_phase in allowed:
        validate_journal_transition(
            _j(old_phase, kind=kind, mutations=frozen),
            _j(new_phase, kind=kind, mutations=frozen),
            path=Path("journal.json"),
        )


def test_journal_transition_generic_rollback_requires_intent() -> None:
    """普通操作只能经 ROLLING_BACK 到 ROLLED_BACK；prepared/applying/validating 直达拒绝。"""
    frozen = (_m(),)
    for kind in (OperationKind.CREATE, OperationKind.MOVE, OperationKind.STATUS_SET):
        for phase in (OperationPhase.PREPARED, OperationPhase.APPLYING, OperationPhase.VALIDATING):
            old = _j(phase, kind=kind, mutations=frozen)
            with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
                validate_journal_transition(
                    old, _j(OperationPhase.ROLLED_BACK, kind=kind, mutations=frozen),
                    path=Path("journal.json"),
                )
            rolling = _j(OperationPhase.ROLLING_BACK, kind=kind, mutations=frozen)
            validate_journal_transition(old, rolling, path=Path("journal.json"))
            validate_journal_transition(rolling, rolling, path=Path("journal.json"))
            validate_journal_transition(
                rolling, _j(OperationPhase.ROLLED_BACK, kind=kind, mutations=frozen),
                path=Path("journal.json"),
            )
            with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
                validate_journal_transition(
                    rolling, _j(OperationPhase.COMPLETED, kind=kind, mutations=frozen),
                    path=Path("journal.json"),
                )


def test_journal_transition_rolling_back_rejects_unknown_edges() -> None:
    """ROLLING_BACK 不允许进入 trash 生命周期或从中段返回正向写阶段。"""
    frozen = (_m(),)
    rolling = _j(OperationPhase.ROLLING_BACK, mutations=frozen)
    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        validate_journal_transition(
            rolling, _j(OperationPhase.VALIDATING, mutations=frozen), path=Path("journal.json")
        )
    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        validate_journal_transition(
            _j(OperationPhase.ALLOCATING, mutations=frozen),
            _j(OperationPhase.ROLLING_BACK, mutations=frozen),
            path=Path("journal.json"),
        )


def test_parse_journal_rejects_phase_not_owned_by_kind() -> None:
    """磁盘读取必须拒绝该 kind 不拥有的 phase（回归 M1：parser 也验证 kind-phase）。"""
    payload = _journal_payload([_write_mutation(0)], phase="trash_retained")
    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        _parse_journal(payload, _parse_path())


def test_parse_journal_rejects_recover_kind() -> None:
    """RECOVER 不得形成持久 journal：解析即拒绝。"""
    payload = _journal_payload([_write_mutation(0)], kind="recover")
    with pytest.raises(DraftError, match=CheckCode.OPERATION_JOURNAL_CORRUPT.value):
        _parse_journal(payload, _parse_path())


def test_artifact_plan_time_absent() -> None:
    """计划时缺失证明：只有正向证据且祖先链由本操作创建才拥有（第十轮 H4）。"""
    from draft.operations import (
        _planned_created_directory_closure,  # pyright: ignore[reportPrivateUsage]
        artifact_plan_time_absent,
    )

    dir_obs = ObservationManifest(
        entries=(
            Observation(path=Path("pages"), kind=EntryKind.DIRECTORY, members=("a.md",)),
            file_observation(Path("pages/a.md"), b"a"),
        )
    )
    planned = _planned_created_directory_closure(dir_obs, ())
    assert planned == frozenset()
    # 父目录已观察 DIRECTORY 且不含该名字：精确缺失即拥有。
    assert artifact_plan_time_absent(
        dir_obs, Path("pages/a.md.pending"), planned_created_directories=planned
    )
    # 父目录明确含名字：不拥有。
    assert not artifact_plan_time_absent(
        dir_obs, Path("pages/a.md"), planned_created_directories=planned
    )
    # 没有观察：无证据，不拥有。
    assert not artifact_plan_time_absent(
        EMPTY_OBSERVATIONS, Path("pages/a.md.pending"), planned_created_directories=frozenset()
    )
    # 父为 FILE/SYMLINK/OTHER：不拥有。
    file_parent = ObservationManifest(
        entries=(file_observation(Path("pages/a.md"), b"a"),)
    )
    assert not artifact_plan_time_absent(
        file_parent, Path("pages/a.md/x.pending"), planned_created_directories=frozenset()
    )
    # 缺失祖先不在创建闭包内：不拥有。
    no_obs = ObservationManifest(
        entries=(Observation(path=Path("pages/foo"), kind=EntryKind.MISSING),)
    )
    assert not artifact_plan_time_absent(
        no_obs, Path("pages/foo/x.pending"), planned_created_directories=frozenset()
    )
    # 缺失祖先在本操作创建闭包内：拥有。
    assert artifact_plan_time_absent(
        no_obs,
        Path("pages/foo/x.pending"),
        planned_created_directories=frozenset({Path("pages/foo")}),
    )
    # 创建闭包只包含 mutation 目标缺失祖先。
    mutated = ObservationManifest(
        entries=(
            Observation(path=Path("pages"), kind=EntryKind.DIRECTORY, members=()),
            Observation(path=Path("pages/foo"), kind=EntryKind.MISSING),
        )
    )
    from draft.operations import JournalMutation as _JM  # pyright: ignore[reportPrivateUsage]

    planned2 = _planned_created_directory_closure(
        mutated,
        (_JM(Path("pages/foo/leaf.md"), MutationKind.WRITE, None, "a" * 64, None, None, False),),
    )
    assert planned2 == frozenset({Path("pages/foo")})


def test_mutation_artifact_actions_forward() -> None:
    """正向应用的方向感知决策表（第十轮 H3）。"""
    from draft.operations import (  # pyright: ignore[reportPrivateUsage]
        ArtifactState,
        MutationDirection,
        MutationRole,
        mutation_artifact_actions,
    )

    D = MutationDirection.FORWARD
    S = ArtifactState
    P = "publish_pending"
    Pk = "publish_staged"
    Cp = "consume_pending"
    Cq = "consume_quarantine"
    R = "retry_apply"
    Done = "done"
    Ex = "external"

    def act(
        role: MutationRole,
        t: ArtifactState,
        p: ArtifactState,
        q: ArtifactState,
        *,
        po: bool = False,
        qo: bool = False,
    ) -> tuple[object, ...]:
        return mutation_artifact_actions(
            direction=D,
            role=role,
            target=t,
            pending=p,
            quarantine=q,
            pending_owned=po,
            quarantine_owned=qo,
        )

    # forward NEW_WRITE。
    assert act(MutationRole.NEW_WRITE, S.MISSING, S.MISSING, S.MISSING) == (R,)
    assert act(MutationRole.NEW_WRITE, S.MISSING, S.AFTER, S.MISSING, po=True) == (P, Done)
    assert act(MutationRole.NEW_WRITE, S.AFTER, S.MISSING, S.MISSING) == (Done,)
    assert act(MutationRole.NEW_WRITE, S.AFTER, S.AFTER, S.MISSING, po=True) == (Cp, Done)
    assert act(MutationRole.NEW_WRITE, S.MISSING, S.AFTER, S.MISSING, po=False) == (Ex,)
    assert act(MutationRole.NEW_WRITE, S.AFTER, S.MISSING, S.AFTER) == (Ex,)
    assert act(MutationRole.NEW_WRITE, S.WRONG, S.MISSING, S.MISSING) == (Ex,)
    # forward REWRITE_WRITE。
    assert act(MutationRole.REWRITE_WRITE, S.BEFORE, S.MISSING, S.MISSING) == (R,)
    assert act(MutationRole.REWRITE_WRITE, S.BEFORE, S.MISSING, S.BEFORE, qo=True) == (Cq, R)
    assert act(MutationRole.REWRITE_WRITE, S.MISSING, S.MISSING, S.BEFORE, qo=True) == (
        Pk, Cq, Done,
    )
    assert act(
        MutationRole.REWRITE_WRITE, S.MISSING, S.AFTER, S.BEFORE, po=True, qo=True
    ) == (P, Cq, Done)
    assert act(MutationRole.REWRITE_WRITE, S.AFTER, S.MISSING, S.BEFORE, qo=True) == (Cq, Done)
    assert act(
        MutationRole.REWRITE_WRITE, S.AFTER, S.AFTER, S.BEFORE, po=True, qo=True
    ) == (Cp, Cq, Done)
    assert act(MutationRole.REWRITE_WRITE, S.AFTER, S.MISSING, S.MISSING) == (Done,)
    assert act(MutationRole.REWRITE_WRITE, S.AFTER, S.AFTER, S.MISSING, po=True) == (Cp, Done)
    assert act(MutationRole.REWRITE_WRITE, S.AFTER, S.MISSING, S.BEFORE, qo=False) == (Ex,)
    assert act(MutationRole.REWRITE_WRITE, S.MISSING, S.AFTER, S.BEFORE, po=False, qo=True) == (Ex,)
    assert act(MutationRole.REWRITE_WRITE, S.BEFORE, S.AFTER, S.MISSING, po=True) == (Ex,)
    # forward DELETE。
    assert act(MutationRole.DELETE, S.BEFORE, S.MISSING, S.MISSING) == (R,)
    assert act(MutationRole.DELETE, S.BEFORE, S.MISSING, S.BEFORE, qo=True) == (Cq, R)
    assert act(MutationRole.DELETE, S.MISSING, S.MISSING, S.BEFORE, qo=True) == (Cq, Done)
    assert act(MutationRole.DELETE, S.MISSING, S.MISSING, S.MISSING) == (Done,)
    assert act(MutationRole.DELETE, S.BEFORE, S.AFTER, S.MISSING, po=True) == (Ex,)
    assert act(MutationRole.DELETE, S.MISSING, S.MISSING, S.BEFORE, qo=False) == (Ex,)


def test_mutation_artifact_actions_rollback() -> None:
    """回滚方向的方向感知决策表（第十轮 H3）：quarantine 保存 AFTER、pending 保存 BEFORE。"""
    from draft.operations import (  # pyright: ignore[reportPrivateUsage]
        ArtifactState,
        MutationDirection,
        MutationRole,
        mutation_artifact_actions,
    )

    D = MutationDirection.ROLLBACK
    S = ArtifactState
    P = "publish_pending"
    Po = "publish_original"
    Cp = "consume_pending"
    Cq = "consume_quarantine"
    R = "retry_apply"
    Done = "done"
    Ex = "external"

    def act(
        role: MutationRole,
        t: ArtifactState,
        p: ArtifactState,
        q: ArtifactState,
        *,
        po: bool = False,
        qo: bool = False,
    ) -> tuple[object, ...]:
        return mutation_artifact_actions(
            direction=D,
            role=role,
            target=t,
            pending=p,
            quarantine=q,
            pending_owned=po,
            quarantine_owned=qo,
        )

    # rollback NEW_WRITE（删除 after）。
    assert act(MutationRole.NEW_WRITE, S.AFTER, S.MISSING, S.MISSING) == (R,)
    assert act(MutationRole.NEW_WRITE, S.AFTER, S.MISSING, S.AFTER, qo=True) == (Cq, R)
    assert act(MutationRole.NEW_WRITE, S.MISSING, S.MISSING, S.AFTER, qo=True) == (Cq, Done)
    assert act(MutationRole.NEW_WRITE, S.MISSING, S.MISSING, S.MISSING) == (Done,)
    assert act(MutationRole.NEW_WRITE, S.AFTER, S.MISSING, S.AFTER, qo=False) == (Ex,)
    assert act(MutationRole.NEW_WRITE, S.MISSING, S.BEFORE, S.MISSING, po=True) == (Ex,)
    # rollback REWRITE_WRITE。
    assert act(MutationRole.REWRITE_WRITE, S.AFTER, S.MISSING, S.MISSING) == (R,)
    assert act(MutationRole.REWRITE_WRITE, S.AFTER, S.MISSING, S.AFTER, qo=True) == (Cq, R)
    assert act(MutationRole.REWRITE_WRITE, S.MISSING, S.MISSING, S.AFTER, qo=True) == (
        Po, Cq, Done,
    )
    assert act(
        MutationRole.REWRITE_WRITE, S.MISSING, S.BEFORE, S.AFTER, po=True, qo=True
    ) == (P, Cq, Done)
    assert act(MutationRole.REWRITE_WRITE, S.BEFORE, S.MISSING, S.AFTER, qo=True) == (Cq, Done)
    assert act(
        MutationRole.REWRITE_WRITE, S.BEFORE, S.BEFORE, S.AFTER, po=True, qo=True
    ) == (Cp, Cq, Done)
    assert act(MutationRole.REWRITE_WRITE, S.BEFORE, S.MISSING, S.MISSING) == (Done,)
    assert act(MutationRole.REWRITE_WRITE, S.AFTER, S.BEFORE, S.MISSING, po=True) == (Ex,)
    assert act(MutationRole.REWRITE_WRITE, S.MISSING, S.BEFORE, S.AFTER, po=False, qo=True) == (Ex,)
    # rollback DELETE。
    assert act(MutationRole.DELETE, S.MISSING, S.MISSING, S.MISSING) == (R,)
    assert act(MutationRole.DELETE, S.MISSING, S.BEFORE, S.MISSING, po=True) == (P, Done)
    assert act(MutationRole.DELETE, S.BEFORE, S.MISSING, S.MISSING) == (Done,)
    assert act(MutationRole.DELETE, S.BEFORE, S.BEFORE, S.MISSING, po=True) == (Cp, Done)
    assert act(MutationRole.DELETE, S.MISSING, S.BEFORE, S.MISSING, po=False) == (Ex,)
    assert act(MutationRole.DELETE, S.MISSING, S.MISSING, S.AFTER, qo=True) == (Ex,)


def test_mutation_artifact_actions_ownership_required() -> None:
    """任何存在的 artifact 未证明计划时缺失都报外部变化（第十轮 H4）。"""
    from draft.operations import (  # pyright: ignore[reportPrivateUsage]
        ArtifactState,
        MutationDirection,
        MutationRecoveryAction,
        MutationRole,
        mutation_artifact_actions,
    )

    S = ArtifactState
    cases = [
        (MutationRole.NEW_WRITE, S.MISSING, S.AFTER, S.MISSING, False, False),
        (MutationRole.REWRITE_WRITE, S.MISSING, S.MISSING, S.BEFORE, False, False),
        (MutationRole.REWRITE_WRITE, S.AFTER, S.AFTER, S.MISSING, False, False),
        (MutationRole.DELETE, S.MISSING, S.MISSING, S.BEFORE, False, False),
    ]
    for role, t, p, q, po, qo in cases:
        result = mutation_artifact_actions(
            direction=MutationDirection.FORWARD,
            role=role,
            target=t,
            pending=p,
            quarantine=q,
            pending_owned=po,
            quarantine_owned=qo,
        )
        assert result == (MutationRecoveryAction.EXTERNAL,), (role, t, p, q)


def test_pending_decision_matrix() -> None:
    """恢复期 pending 收敛决策表：publish/delete/ignore/external（回归 H2）。"""
    from draft.observations import EntryKind
    from draft.operations import _pending_decision  # pyright: ignore[reportPrivateUsage]

    F, M, D, S = (
        EntryKind.FILE,
        EntryKind.MISSING,
        EntryKind.DIRECTORY,
        EntryKind.SYMLINK,
    )
    expected = "a" * 64
    other = "0" * 64

    # 目标缺失 + pending 授权精确 → 发布 pending。
    assert (
        _pending_decision(
            target_kind=M,
            pending_kind=F,
            target_digest=None,
            pending_digest=expected,
            expected_digest=expected,
        )
        == "publish"
    )
    # 目标与 pending 都是授权精确 → pending 已消费，只删 pending。
    assert (
        _pending_decision(
            target_kind=F,
            pending_kind=F,
            target_digest=expected,
            pending_digest=expected,
            expected_digest=expected,
        )
        == "delete"
    )
    # 目标已是授权精确且无 pending → 已发布。
    assert (
        _pending_decision(
            target_kind=F,
            pending_kind=M,
            target_digest=expected,
            pending_digest=None,
            expected_digest=expected,
        )
        == "ignore"
    )
    # 目标与 pending 都缺失 → 无可收敛。
    assert (
        _pending_decision(
            target_kind=M,
            pending_kind=M,
            target_digest=None,
            pending_digest=None,
            expected_digest=expected,
        )
        == "ignore"
    )
    # pending 摘要不符 / 类型错误（目录、符号链接）→ external，保留现场。
    assert (
        _pending_decision(
            target_kind=M,
            pending_kind=F,
            target_digest=None,
            pending_digest=other,
            expected_digest=expected,
        )
        == "external"
    )
    assert (
        _pending_decision(
            target_kind=M,
            pending_kind=D,
            target_digest=None,
            pending_digest=None,
            expected_digest=expected,
        )
        == "external"
    )
    assert (
        _pending_decision(
            target_kind=M,
            pending_kind=S,
            target_digest=None,
            pending_digest=None,
            expected_digest=expected,
        )
        == "external"
    )
    # 目标被外部替换（摘要不符）→ external，不得删除/覆盖。
    assert (
        _pending_decision(
            target_kind=F,
            pending_kind=F,
            target_digest=other,
            pending_digest=expected,
            expected_digest=expected,
        )
        == "external"
    )
    assert (
        _pending_decision(
            target_kind=F,
            pending_kind=M,
            target_digest=other,
            pending_digest=None,
            expected_digest=expected,
        )
        == "external"
    )
    # 目标类型错误（目录）+ 无 pending → external。
    assert (
        _pending_decision(
            target_kind=D,
            pending_kind=M,
            target_digest=None,
            pending_digest=None,
            expected_digest=expected,
        )
        == "external"
    )


def test_quarantine_decision_matrix() -> None:
    """恢复期 quarantine 收敛决策表：complete/restore/delete_stale/external/ignore（回归 H3）。"""
    from draft.observations import EntryKind
    from draft.operations import _quarantine_decision  # pyright: ignore[reportPrivateUsage]

    F, M, D, S = (
        EntryKind.FILE,
        EntryKind.MISSING,
        EntryKind.DIRECTORY,
        EntryKind.SYMLINK,
    )
    expected = "a" * 64
    other = "0" * 64

    # 目标缺失 + quarantine 授权精确 → 完成删除（隔离已通过验证）。
    assert (
        _quarantine_decision(
            target_kind=M,
            quarantine_kind=F,
            target_digest=None,
            quarantine_digest=expected,
            expected_digest=expected,
        )
        == "complete"
    )
    # 目标与 quarantine 都是授权精确（同一 inode 中途移动）→ 删除 stale quarantine。
    assert (
        _quarantine_decision(
            target_kind=F,
            quarantine_kind=F,
            target_digest=expected,
            quarantine_digest=expected,
            expected_digest=expected,
        )
        == "delete_stale"
    )
    # 目标缺失 + quarantine 摘要不符 → 恢复原名。
    assert (
        _quarantine_decision(
            target_kind=M,
            quarantine_kind=F,
            target_digest=None,
            quarantine_digest=other,
            expected_digest=expected,
        )
        == "restore"
    )
    # quarantine 类型错误 / 目标被替换 → external。
    assert (
        _quarantine_decision(
            target_kind=M,
            quarantine_kind=D,
            target_digest=None,
            quarantine_digest=None,
            expected_digest=expected,
        )
        == "external"
    )
    assert (
        _quarantine_decision(
            target_kind=M,
            quarantine_kind=S,
            target_digest=None,
            quarantine_digest=None,
            expected_digest=expected,
        )
        == "external"
    )
    assert (
        _quarantine_decision(
            target_kind=F,
            quarantine_kind=F,
            target_digest=other,
            quarantine_digest=expected,
            expected_digest=expected,
        )
        == "external"
    )
    # 无 quarantine → ignore。
    assert (
        _quarantine_decision(
            target_kind=F,
            quarantine_kind=M,
            target_digest=expected,
            quarantine_digest=None,
            expected_digest=expected,
        )
        == "ignore"
    )
    assert (
        _quarantine_decision(
            target_kind=M,
            quarantine_kind=M,
            target_digest=None,
            quarantine_digest=None,
            expected_digest=expected,
        )
        == "ignore"
    )
