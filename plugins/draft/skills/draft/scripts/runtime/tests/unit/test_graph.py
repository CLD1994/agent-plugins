from __future__ import annotations

from pathlib import Path

import pytest

from draft.errors import DraftError
from draft.graph import (
    FORMAT_VERSION,
    MarkerState,
    is_external_destination,
    parse_marker_value,
    resolve_local_destination,
)

MARKER = Path(".draft/draft.json")


# ---------------------------------------------------------------------------
# parse_marker_value
# ---------------------------------------------------------------------------


def test_parse_marker_value_accepts_final_marker() -> None:
    state = parse_marker_value({"format_version": FORMAT_VERSION}, MARKER)
    assert state == MarkerState(version=FORMAT_VERSION, provisional_manifest=None)


def test_parse_marker_value_accepts_provisional_adopt_marker() -> None:
    state = parse_marker_value(
        {
            "format_version": FORMAT_VERSION,
            "adopt_state": "provisional",
            "candidate_manifest": [["pages/a.md", "sha256:abc"]],
        },
        MARKER,
    )
    assert state.version == FORMAT_VERSION
    assert state.provisional_manifest == (("pages/a.md", "sha256:abc"),)


@pytest.mark.parametrize("decoded", [[], "x", 1, None])
def test_parse_marker_value_rejects_non_dict(decoded: object) -> None:
    with pytest.raises(DraftError) as caught:
        parse_marker_value(decoded, MARKER)
    assert caught.value.code == "format.invalid"


def test_parse_marker_value_rejects_bool_version() -> None:
    # bool 是 int 子类，但不得被接受为版本号。
    with pytest.raises(DraftError) as caught:
        parse_marker_value({"format_version": True}, MARKER)
    assert caught.value.code == "format.invalid"


def test_parse_marker_value_rejects_non_integer_version() -> None:
    with pytest.raises(DraftError) as caught:
        parse_marker_value({"format_version": "1"}, MARKER)
    assert caught.value.code == "format.invalid"


def test_parse_marker_value_rejects_unsupported_version() -> None:
    with pytest.raises(DraftError) as caught:
        parse_marker_value({"format_version": FORMAT_VERSION + 1}, MARKER)
    assert caught.value.code == "format.unsupported"


def test_parse_marker_value_rejects_extra_fields_on_final_marker() -> None:
    with pytest.raises(DraftError) as caught:
        parse_marker_value({"format_version": FORMAT_VERSION, "extra": 1}, MARKER)
    assert caught.value.code == "format.invalid"


def test_parse_marker_value_rejects_wrong_adopt_state() -> None:
    with pytest.raises(DraftError) as caught:
        parse_marker_value(
            {
                "format_version": FORMAT_VERSION,
                "adopt_state": "final",
                "candidate_manifest": [],
            },
            MARKER,
        )
    assert caught.value.code == "format.invalid"


def test_parse_marker_value_rejects_provisional_with_missing_manifest_field() -> None:
    with pytest.raises(DraftError) as caught:
        parse_marker_value(
            {"format_version": FORMAT_VERSION, "adopt_state": "provisional"},
            MARKER,
        )
    assert caught.value.code == "format.invalid"


@pytest.mark.parametrize(
    "manifest",
    [
        "not-a-list",
        [["pages/a.md"]],
        [["pages/a.md", "sha256:abc", "extra"]],
        [[1, "sha256:abc"]],
        [["pages/a.md", 1]],
        ["/abs/a.md", "sha256:abc"],
        [["../escape.md", "sha256:abc"]],
    ],
)
def test_parse_marker_value_rejects_malformed_manifest(manifest: object) -> None:
    with pytest.raises(DraftError) as caught:
        parse_marker_value(
            {
                "format_version": FORMAT_VERSION,
                "adopt_state": "provisional",
                "candidate_manifest": manifest,
            },
            MARKER,
        )
    assert caught.value.code == "format.invalid"


def test_parse_marker_value_manifest_preserves_entry_order() -> None:
    state = parse_marker_value(
        {
            "format_version": FORMAT_VERSION,
            "adopt_state": "provisional",
            "candidate_manifest": [["pages/b.md", "d2"], ["pages/a.md", "d1"]],
        },
        MARKER,
    )
    assert state.provisional_manifest == (("pages/b.md", "d2"), ("pages/a.md", "d1"))


# ---------------------------------------------------------------------------
# is_external_destination
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "destination",
    ["https://example.com", "http://example.com", "mailto:a@b.com", "scheme:x"],
)
def test_is_external_destination_recognizes_schemes(destination: str) -> None:
    assert is_external_destination(destination) is True


@pytest.mark.parametrize(
    "destination",
    ["pages/a.md", "/abs/path", "../up.md", "conclusions.md#anchor", "a b", "1abc:x"],
)
def test_is_external_destination_rejects_non_schemes(destination: str) -> None:
    assert is_external_destination(destination) is False


# ---------------------------------------------------------------------------
# resolve_local_destination
# ---------------------------------------------------------------------------


def test_resolve_local_destination_decodes_and_normalizes() -> None:
    assert resolve_local_destination(
        Path("pages/a.md"), "../conclusions%2Emd#结论", Path("/unused")
    ) == (Path("conclusions.md"), "结论")


def test_resolve_local_destination_same_directory() -> None:
    assert resolve_local_destination(Path("pages/a.md"), "b.md", Path("/unused")) == (
        Path("pages/b.md"),
        None,
    )


def test_resolve_local_destination_empty_target_returns_source() -> None:
    assert resolve_local_destination(Path("index.md"), "", Path("/unused")) == (
        Path("index.md"),
        None,
    )


def test_resolve_local_destination_keeps_anchor_only_when_separator_present() -> None:
    assert resolve_local_destination(Path("index.md"), "#section", Path("/unused")) == (
        Path("index.md"),
        "section",
    )


def test_resolve_local_destination_anchor_without_hash_is_not_an_anchor() -> None:
    # 没有 '#' 分隔符时，井号是路径的一部分，不当作锚点。
    target, anchor = resolve_local_destination(Path("index.md"), "page%23note.md", Path("/unused"))
    assert target == Path("page#note.md")
    assert anchor is None


def test_resolve_local_destination_rejects_external_scheme() -> None:
    with pytest.raises(DraftError) as caught:
        resolve_local_destination(Path("index.md"), "https://example.com", Path("/unused"))
    assert caught.value.code == "link.external"


def test_resolve_local_destination_rejects_absolute_path() -> None:
    with pytest.raises(DraftError) as caught:
        resolve_local_destination(Path("index.md"), "/etc/passwd", Path("/unused"))
    assert caught.value.code == "link.escape"


def test_resolve_local_destination_rejects_root_escape() -> None:
    with pytest.raises(DraftError) as caught:
        resolve_local_destination(Path("index.md"), "../outside.md", Path("/unused"))
    assert caught.value.code == "link.escape"


def test_resolve_local_destination_rejects_self_escape_via_dotdot() -> None:
    # pages/a.md -> ../../top.md 逃逸到根之上。
    with pytest.raises(DraftError) as caught:
        resolve_local_destination(Path("pages/a.md"), "../../top.md", Path("/unused"))
    assert caught.value.code == "link.escape"


def test_resolve_local_destination_normalizes_internal_dotdot() -> None:
    target, _ = resolve_local_destination(Path("pages/sub/a.md"), "../b.md", Path("/unused"))
    assert target == Path("pages/b.md")
