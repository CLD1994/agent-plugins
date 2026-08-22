from __future__ import annotations

from pathlib import Path

from draft.move_closure import MoveClosure, decode_and_extract, merged_blocking


def test_decode_and_extract_rejects_non_utf8_bytes() -> None:
    raw = "参考 [旧稿](00-bg.md)。".encode("gb18030")

    assert decode_and_extract(raw) is None


def test_decode_and_extract_rejects_alignment_failure() -> None:
    raw = "看 [x](a\\.md)。\n".encode()

    assert decode_and_extract(raw) is None


def test_decode_and_extract_returns_text_and_links() -> None:
    raw = "看 [旧稿](00-bg.md)。\n".encode()

    parsed = decode_and_extract(raw)

    assert parsed is not None
    _text, links = parsed
    assert [link.destination for link in links] == ["00-bg.md"]


def test_merged_blocking_unions_and_sorts() -> None:
    closure = MoveClosure(unmanaged={}, blocking=(Path("z.md"), Path("a.md")))

    assert merged_blocking([Path("m.md"), Path("a.md")], closure) == (
        Path("a.md"),
        Path("m.md"),
        Path("z.md"),
    )
