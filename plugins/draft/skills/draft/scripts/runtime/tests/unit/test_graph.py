from __future__ import annotations

from pathlib import Path

import pytest

from draft.diagnostic_codes import CheckCode, QueryCode, RootCode
from draft.errors import DraftError
from draft.graph import (
    FORMAT_VERSION,
    MarkerState,
    is_external_destination,
    parse_marker_value,
    parse_provisional_adopt_value,
    resolve_local_destination,
)
from draft.models import RootState
from draft.vocabulary import ConclusionsState

MARKER = Path(".draft/draft.json")


# ---------------------------------------------------------------------------
# 受管根协议权威类型
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("root_state", tuple(RootState))
def test_root_state_round_trips_through_authority(root_state: RootState) -> None:
    assert RootState(root_state.value) is root_state


@pytest.mark.parametrize("code", tuple(RootCode))
def test_root_code_round_trips_through_authority(code: RootCode) -> None:
    assert RootCode(code.value) is code


@pytest.mark.parametrize("code", tuple(QueryCode))
def test_query_code_round_trips_through_authority(code: QueryCode) -> None:
    assert QueryCode(code.value) is code


# ---------------------------------------------------------------------------
# parse_marker_value（正式三键协议）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("root_state", tuple(RootState))
@pytest.mark.parametrize("conclusions_state", tuple(ConclusionsState))
def test_parse_marker_value_accepts_exact_managed_root_state(
    root_state: RootState, conclusions_state: ConclusionsState
) -> None:
    state = parse_marker_value(
        {
            "format_version": FORMAT_VERSION,
            "root_state": root_state.value,
            "conclusions_state": conclusions_state.value,
        },
        MARKER,
    )
    assert state.root_state is root_state
    assert state.conclusions_state is conclusions_state


def test_parse_marker_value_accepts_final_registered_marker() -> None:
    state = parse_marker_value(
        {
            "format_version": FORMAT_VERSION,
            "root_state": RootState.REGISTERED.value,
            "conclusions_state": ConclusionsState.NOT_GENERATED.value,
        },
        MARKER,
    )
    assert state == MarkerState(
        version=FORMAT_VERSION,
        root_state=RootState.REGISTERED,
        conclusions_state=ConclusionsState.NOT_GENERATED,
    )


def test_parse_marker_value_rejects_old_two_key_marker() -> None:
    """旧 {"format_version":1,"conclusions_state":...} 标记有意以 format.invalid 拒绝，无迁移。"""
    with pytest.raises(DraftError) as caught:
        parse_marker_value(
            {"format_version": FORMAT_VERSION, "conclusions_state": "not_generated"}, MARKER
        )
    assert caught.value.code == CheckCode.FORMAT_INVALID.value


def test_parse_marker_value_rejects_old_version_only_marker() -> None:
    with pytest.raises(DraftError) as caught:
        parse_marker_value({"format_version": FORMAT_VERSION}, MARKER)
    assert caught.value.code == CheckCode.FORMAT_INVALID.value


def test_parse_marker_value_rejects_unknown_root_state() -> None:
    with pytest.raises(DraftError) as caught:
        parse_marker_value(
            {
                "format_version": FORMAT_VERSION,
                "root_state": "adopting",
                "conclusions_state": "not_generated",
            },
            MARKER,
        )
    assert caught.value.code == CheckCode.FORMAT_INVALID.value


def test_parse_marker_value_rejects_non_string_root_state() -> None:
    with pytest.raises(DraftError) as caught:
        parse_marker_value(
            {
                "format_version": FORMAT_VERSION,
                "root_state": 1,
                "conclusions_state": "not_generated",
            },
            MARKER,
        )
    assert caught.value.code == CheckCode.FORMAT_INVALID.value


def test_parse_marker_value_rejects_extra_field() -> None:
    with pytest.raises(DraftError) as caught:
        parse_marker_value(
            {
                "format_version": FORMAT_VERSION,
                "root_state": "registered",
                "conclusions_state": "not_generated",
                "extra": 1,
            },
            MARKER,
        )
    assert caught.value.code == CheckCode.FORMAT_INVALID.value


def test_parse_marker_value_rejects_provisional_keys() -> None:
    """正式 marker 解析器不接受 provisional 键；adopt 桥接走私有解析。"""
    with pytest.raises(DraftError) as caught:
        parse_marker_value(
            {
                "format_version": FORMAT_VERSION,
                "conclusions_state": "not_generated",
                "adopt_state": "provisional",
                "candidate_manifest": [],
            },
            MARKER,
        )
    assert caught.value.code == CheckCode.FORMAT_INVALID.value


@pytest.mark.parametrize("decoded", [[], "x", 1, None])
def test_parse_marker_value_rejects_non_dict(decoded: object) -> None:
    with pytest.raises(DraftError) as caught:
        parse_marker_value(decoded, MARKER)
    assert caught.value.code == CheckCode.FORMAT_INVALID.value


def test_parse_marker_value_rejects_bool_version() -> None:
    # bool 是 int 子类，但不得被接受为版本号。
    with pytest.raises(DraftError) as caught:
        parse_marker_value({"format_version": True}, MARKER)
    assert caught.value.code == CheckCode.FORMAT_INVALID.value


def test_parse_marker_value_rejects_non_integer_version() -> None:
    with pytest.raises(DraftError) as caught:
        parse_marker_value({"format_version": "1"}, MARKER)
    assert caught.value.code == CheckCode.FORMAT_INVALID.value


def test_parse_marker_value_rejects_unsupported_version() -> None:
    with pytest.raises(DraftError) as caught:
        parse_marker_value({"format_version": FORMAT_VERSION + 1}, MARKER)
    assert caught.value.code == CheckCode.FORMAT_UNSUPPORTED.value


# ---------------------------------------------------------------------------
# parse_provisional_adopt_value（开发期 adopt 桥接，Task 8 删除）
# ---------------------------------------------------------------------------


def test_parse_provisional_adopt_value_round_trips_manifest() -> None:
    state = parse_provisional_adopt_value(
        {
            "format_version": FORMAT_VERSION,
            "conclusions_state": "not_generated",
            "adopt_state": "provisional",
            "candidate_manifest": [["pages/b.md", "d2"], ["pages/a.md", "d1"]],
        },
        MARKER,
    )
    assert state.manifest == (("pages/b.md", "d2"), ("pages/a.md", "d1"))


def test_parse_provisional_adopt_value_accepts_empty_manifest() -> None:
    state = parse_provisional_adopt_value(
        {
            "format_version": FORMAT_VERSION,
            "conclusions_state": "not_generated",
            "adopt_state": "provisional",
            "candidate_manifest": [],
        },
        MARKER,
    )
    assert state.manifest == ()


def test_parse_provisional_adopt_value_rejects_wrong_adopt_state() -> None:
    with pytest.raises(DraftError) as caught:
        parse_provisional_adopt_value(
            {
                "format_version": FORMAT_VERSION,
                "conclusions_state": "not_generated",
                "adopt_state": "final",
                "candidate_manifest": [],
            },
            MARKER,
        )
    assert caught.value.code == CheckCode.FORMAT_INVALID.value


def test_parse_provisional_adopt_value_rejects_missing_manifest_field() -> None:
    with pytest.raises(DraftError) as caught:
        parse_provisional_adopt_value(
            {
                "format_version": FORMAT_VERSION,
                "conclusions_state": "not_generated",
                "adopt_state": "provisional",
            },
            MARKER,
        )
    assert caught.value.code == CheckCode.FORMAT_INVALID.value


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
def test_parse_provisional_adopt_value_rejects_malformed_manifest(manifest: object) -> None:
    with pytest.raises(DraftError) as caught:
        parse_provisional_adopt_value(
            {
                "format_version": FORMAT_VERSION,
                "conclusions_state": "not_generated",
                "adopt_state": "provisional",
                "candidate_manifest": manifest,
            },
            MARKER,
        )
    assert caught.value.code == CheckCode.FORMAT_INVALID.value


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
    assert caught.value.code == CheckCode.LINK_ESCAPE.value


def test_resolve_local_destination_rejects_root_escape() -> None:
    with pytest.raises(DraftError) as caught:
        resolve_local_destination(Path("index.md"), "../outside.md", Path("/unused"))
    assert caught.value.code == CheckCode.LINK_ESCAPE.value


def test_resolve_local_destination_rejects_self_escape_via_dotdot() -> None:
    # pages/a.md -> ../../top.md 逃逸到根之上。
    with pytest.raises(DraftError) as caught:
        resolve_local_destination(Path("pages/a.md"), "../../top.md", Path("/unused"))
    assert caught.value.code == CheckCode.LINK_ESCAPE.value


def test_resolve_local_destination_normalizes_internal_dotdot() -> None:
    target, _ = resolve_local_destination(Path("pages/sub/a.md"), "../b.md", Path("/unused"))
    assert target == Path("pages/b.md")
