from __future__ import annotations

import pytest

from draft.link_index import (
    Backlink,
    Completeness,
    FileFingerprint,
    IndexedFile,
    LinkIndex,
    LinkSpan,
    OutboundEdge,
)
from draft.resources import ResourceIdentity, ResourceKind


def _index() -> LinkIndex:
    from pathlib import Path

    identity = ResourceIdentity(ResourceKind.PAGE, "pages/b.md")
    backlink = Backlink(Path("pages/a.md"), LinkSpan(6, 10))
    file = IndexedFile(
        Path("pages/a.md"),
        FileFingerprint(1, 12, 3),
        "a" * 64,
        "parsed",
        (OutboundEdge(identity, backlink.span),),
    )
    return LinkIndex("/draft", 3, Completeness.COMPLETE, {file.path: file}, {identity: (backlink,)})


def test_index_round_trip_and_reverse_edges() -> None:
    from pathlib import Path

    index = _index()
    raw = index.to_json()
    loaded = LinkIndex.from_json(raw)
    assert loaded == index
    identity = ResourceIdentity(ResourceKind.PAGE, "pages/b.md")
    assert loaded.backlinks[identity] == (Backlink(Path("pages/a.md"), LinkSpan(6, 10)),)


def test_index_rejects_wrong_schema_and_inconsistent_reverse_table() -> None:
    payload = _index().to_dict()
    payload["schema_version"] = 999
    with pytest.raises(ValueError):
        LinkIndex.from_dict(payload)
    payload = _index().to_dict()
    payload["backlinks"] = {"local:missing.md": []}
    with pytest.raises(ValueError):
        LinkIndex.from_dict(payload)
