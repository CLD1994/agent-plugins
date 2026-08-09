from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from draft.diagnostic_codes import CheckCode
from draft.errors import DraftError
from draft.models import MutationKind, OperationPhase
from draft.operations import (
    HANDOFF_ATTEMPTED_AT_KEY,
    HANDOFF_ERROR_MESSAGE_KEY,
    HANDOFF_ERROR_TYPE_KEY,
    Journal,
    JournalMutation,
    TrashDeadlines,
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
        kind="move",
        phase=OperationPhase.PREPARED,
        validation_mode="registered",
        mutations=(
            JournalMutation(
                path=Path("pages/a.md"),
                kind=MutationKind.WRITE,
                before_sha256="before-hash",
                after_sha256="after-hash",
                original="originals/0000.bin",
                staged="staged/0000.bin",
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
