from __future__ import annotations

import json
from pathlib import Path

from draft.graph import FORMAT_VERSION, parse_marker_value
from draft.lifecycle import CandidateManifest, provisional_marker_bytes

# ---------------------------------------------------------------------------
# provisional_marker_bytes
# ---------------------------------------------------------------------------


def test_provisional_marker_bytes_serializes_valid_manifest() -> None:
    manifest = CandidateManifest(
        entries=(
            (Path("conclusions.md"), "sha256:bbb"),
            (Path("pages/a.md"), "sha256:aaa"),
        )
    )
    result = provisional_marker_bytes(manifest)
    decoded = json.loads(result)
    assert decoded == {
        "format_version": FORMAT_VERSION,
        "adopt_state": "provisional",
        "candidate_manifest": [
            ["conclusions.md", "sha256:bbb"],
            ["pages/a.md", "sha256:aaa"],
        ],
    }


def test_provisional_marker_bytes_serializes_empty_manifest() -> None:
    manifest = CandidateManifest(entries=())
    result = provisional_marker_bytes(manifest)
    decoded = json.loads(result)
    assert decoded == {
        "format_version": FORMAT_VERSION,
        "adopt_state": "provisional",
        "candidate_manifest": [],
    }


def test_provisional_marker_bytes_ends_with_newline_and_is_utf8() -> None:
    manifest = CandidateManifest(entries=())
    result = provisional_marker_bytes(manifest)
    assert result.endswith(b"\n")
    # 中文字符在候选清单路径中也能正确解码。
    result.decode("utf-8")


def test_provisional_marker_bytes_uses_posix_paths() -> None:
    manifest = CandidateManifest(entries=((Path("pages/sub/deep.md"), "sha256:xxx"),))
    result = provisional_marker_bytes(manifest)
    decoded = json.loads(result)
    assert decoded["candidate_manifest"] == [["pages/sub/deep.md", "sha256:xxx"]]


def test_provisional_marker_bytes_round_trips_through_parse_marker_value() -> None:
    manifest = CandidateManifest(
        entries=(
            (Path("conclusions.md"), "sha256:bbb"),
            (Path("pages/a.md"), "sha256:aaa"),
        )
    )
    result = provisional_marker_bytes(manifest)
    decoded = json.loads(result)
    marker = Path(".draft/draft.provisional.json")
    state = parse_marker_value(decoded, marker)
    assert state.version == FORMAT_VERSION
    assert state.provisional_manifest == (
        ("conclusions.md", "sha256:bbb"),
        ("pages/a.md", "sha256:aaa"),
    )
