# pyright: reportPrivateUsage=false

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from draft.errors import DraftError
from draft.models import MarkdownLink, SourceSpan
from draft.move_closure import MoveClosure, UnmanagedFile
from draft.resources import ProbeState, ResourceReference, resolve_body_resource

ROOT = Path("/draft")


def _present(_path: Path) -> ProbeState:
    return ProbeState.FILE


def _reference(source: Path, text: str, destination: str) -> tuple[str, ResourceReference]:
    """构造一条指向 destination 的链接引用；返回 (整段文本, 引用)。"""
    link_text = f"[目标]({destination})"
    full = text if text else f"前文 {link_text} 后文\n"
    start = full.index(destination)
    link = MarkdownLink(
        label="目标",
        destination=destination,
        destination_span=SourceSpan(start, start + len(destination)),
        whole_span=SourceSpan(full.index(link_text), full.index(link_text) + len(link_text)),
        line=1,
    )
    resource = resolve_body_resource(ROOT, source, destination, target_probe=_present)
    return full, ResourceReference(link=link, resource=resource)


def test_plan_unmanaged_rebases_outbound_relative_links() -> None:
    from draft.structure import _plan_unmanaged

    source = Path("00-background.md")
    text, ref_local = _reference(source, "", "other.md")
    _ext = "[外](https://example.com/x)"
    full = text + _ext + "\n"
    # 手工链接位置仅 ref_local 参与断言，外链通过文本不变性验证
    closure = MoveClosure(unmanaged={source: UnmanagedFile(full, (ref_local,))}, blocking=())

    mutations, effects = _plan_unmanaged(
        ROOT, closure, {source: Path("pages/00-background.md")}, {}
    )

    writes = [m for m in mutations if m.kind.value == "write"]
    deletes = [m for m in mutations if m.kind.value == "delete"]
    assert len(writes) == 1 and len(deletes) == 1
    assert writes[0].path == Path("pages/00-background.md")
    assert deletes[0].path == source
    assert writes[0].content is not None
    rendered = writes[0].content.decode("utf-8")
    assert "[目标](../other.md)" in rendered
    assert _ext in rendered
    assert effects[0].from_path == source and effects[0].to_path == Path("pages/00-background.md")


def test_plan_unmanaged_rewrites_inbound_links_from_stationary_files() -> None:
    from draft.resources import ResourceIdentity, ResourceKind
    from draft.structure import _plan_unmanaged

    mover = Path("00-background.md")
    linker = Path("notes/reading.md")
    text, ref = _reference(linker, "", "../00-background.md")
    closure = MoveClosure(
        unmanaged={
            mover: UnmanagedFile("无链接正文\n", ()),
            linker: UnmanagedFile(text, (ref,)),
        },
        blocking=(),
    )
    moved_targets = {
        ResourceIdentity(ResourceKind.LOCAL, "00-background.md"): ResourceIdentity(
            ResourceKind.PAGE, "pages/00-background.md"
        )
    }

    mutations, _effects = _plan_unmanaged(
        ROOT, closure, {mover: Path("pages/00-background.md")}, moved_targets
    )

    by_path = {m.path: m for m in mutations}
    linker_write = by_path[linker]
    assert linker_write.before_sha256 is not None
    assert linker_write.content is not None
    assert "[目标](../pages/00-background.md)" in linker_write.content.decode("utf-8")


def test_plan_unmanaged_rejects_rendered_text_that_lost_extractability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from draft import structure
    from draft.structure import _plan_unmanaged

    source = Path("00-background.md")
    text, ref = _reference(source, "", "other.md")
    closure = MoveClosure(unmanaged={source: UnmanagedFile(text, (ref,))}, blocking=())

    def _broken_rewrite(text: str, replacements: Mapping[SourceSpan, str]) -> str:
        # 目标含转义序列，提取管线对齐必然失败（source_alignment_failed）
        return "看 [x](a\\.md)。\n"

    monkeypatch.setattr(structure, "rewrite_link_destinations", _broken_rewrite)

    with pytest.raises(DraftError) as caught:
        _plan_unmanaged(ROOT, closure, {source: Path("pages/00-background.md")}, {})

    assert caught.value.code == "operation.plan_invalid"
