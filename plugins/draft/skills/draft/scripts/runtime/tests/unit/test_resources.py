from __future__ import annotations

from pathlib import Path

import pytest

from draft.markdown import parse_page
from draft.resources import (
    HostOS,
    ProbeState,
    ResolvedResource,
    ResourceKind,
    ResourceScope,
    TargetState,
    resolve_body_resource,
    resolve_page_resources,
)

ROOT = Path("/draft")
SOURCE = Path("pages/a.md")


def _fake_probe(states: dict[Path, ProbeState]) -> object:
    """受真实接口约束的目标探针伪实现；缺失路径默认 MISSING。"""

    def probe(target: Path) -> ProbeState:
        return states.get(target, ProbeState.MISSING)

    return probe


def _state(result: ResolvedResource) -> tuple[str, str, str]:
    return (result.kind.value, result.scope.value, result.target_state.value)


# 分类矩阵：词法命名空间决定 kind/scope，目标探针决定 target_state。
@pytest.mark.parametrize(
    ("destination", "host", "states", "expected"),
    [
        ("b.md#one", "linux", {"/draft/pages/b.md": ProbeState.FILE}, ("page", "draft", "present")),
        ("missing.md", "linux", {}, ("page", "draft", "missing")),
        ("../conclusions.md", "linux", {}, ("page", "draft", "missing")),
        (
            "../notes.md",
            "linux",
            {"/draft/notes.md": ProbeState.FILE},
            ("local", "draft", "present"),
        ),
        (
            "../../notes.md",
            "linux",
            {"/notes.md": ProbeState.FILE},
            ("local", "outside", "present"),
        ),
        (
            "b.md",
            "linux",
            {"/draft/pages/b.md": ProbeState.DIRECTORY},
            ("page", "draft", "invalid"),
        ),
        (
            "../asset.png",
            "linux",
            {"/draft/asset.png": ProbeState.FILE},
            ("local", "draft", "present"),
        ),
        ("../../shared.png", "linux", {}, ("local", "outside", "missing")),
        (
            r"..\asset.png",
            "linux",
            {"/draft/asset.png": ProbeState.FILE},
            ("local", "draft", "present"),
        ),
        (r"C:\docs\a.pdf", "linux", {}, ("local", "unknown", "unavailable")),
        (r"C:docs\a.pdf", "linux", {}, ("local", "unknown", "unavailable")),
        (r"\\server\share\a.pdf", "linux", {}, ("local", "unknown", "unavailable")),
        (
            "/opt/shared/a.pdf",
            "linux",
            {"/opt/shared/a.pdf": ProbeState.FILE},
            ("local", "outside", "present"),
        ),
        ("https://example.test/a#part", "linux", {}, ("external", "unknown", "unchecked")),
        ("%zz/invalid", "linux", {}, ("local", "unknown", "invalid")),
    ],
)
def test_body_resource_classification_matrix(
    destination: str, host: str, states: dict[str, ProbeState], expected: tuple[str, str, str]
) -> None:
    probe_states = {Path(path): state for path, state in states.items()}
    result = resolve_body_resource(
        ROOT,
        SOURCE,
        destination,
        host_os=HostOS(host),
        target_probe=_fake_probe(probe_states),  # type: ignore[arg-type]
    )

    assert _state(result) == expected


def test_page_kind_is_lexical_and_unaffected_by_existence() -> None:
    """目标从缺失变为存在时，kind 和 scope 不变，只更新 target_state。"""
    path = ROOT / "pages/notes.md"

    missing = resolve_body_resource(
        ROOT,
        SOURCE,
        "notes.md",
        host_os=HostOS.LINUX,
        target_probe=_fake_probe({}),  # type: ignore[arg-type]
    )
    assert missing.kind is ResourceKind.PAGE
    assert missing.scope is ResourceScope.DRAFT
    assert missing.target_state is TargetState.MISSING

    present = resolve_body_resource(
        ROOT,
        SOURCE,
        "notes.md",
        host_os=HostOS.LINUX,
        target_probe=_fake_probe({path: ProbeState.FILE}),  # type: ignore[arg-type]
    )
    assert present.kind is ResourceKind.PAGE
    assert present.scope is ResourceScope.DRAFT
    assert present.target_state is TargetState.PRESENT


def test_images_and_links_to_same_resource_deduplicate_by_identity() -> None:
    text = (
        "# 标题\n\n> 状态：`待讨论`\n>\n> 父主题：[根](../index.md)\n\n"
        "[图](../asset.png) 与 ![图](../asset.png)\n"
    )
    page = parse_page(SOURCE, text)
    states = {ROOT / "asset.png": ProbeState.FILE}

    references = resolve_page_resources(
        ROOT,
        page,
        host_os=HostOS.LINUX,
        target_probe=_fake_probe(states),  # type: ignore[arg-type]
    )

    identities = [ref.resource.identity for ref in references]
    assert len(identities) == len(set(identities))
    assert all(ref.resource.kind is ResourceKind.LOCAL for ref in references)


def test_anchor_does_not_participate_in_identity() -> None:
    text = (
        "# 标题\n\n> 状态：`待讨论`\n>\n> 父主题：[根](../index.md)\n\n"
        "[甲](b.md#one) 与 [乙](b.md#two)\n"
    )
    page = parse_page(SOURCE, text)

    references = resolve_page_resources(
        ROOT,
        page,
        host_os=HostOS.LINUX,
        target_probe=_fake_probe({}),  # type: ignore[arg-type]
    )

    identities = [ref.resource.identity for ref in references]
    assert len(identities) == 1
    assert identities[0].value == "pages/b.md"


def test_page_resources_excludes_relation_link_positions() -> None:
    text = "# 标题\n\n> 状态：`待讨论`\n>\n> 父主题：[根](../index.md)\n\n正文 [链接](other.md)\n"
    page = parse_page(SOURCE, text)

    references = resolve_page_resources(
        ROOT,
        page,
        host_os=HostOS.LINUX,
        target_probe=_fake_probe({}),  # type: ignore[arg-type]
    )

    # 只有正文链接 other.md 是资源；父主题声明不进入正文资源。
    assert [ref.resource.identity.value for ref in references] == ["pages/other.md"]


def test_relative_destination_keeps_anchor_and_posix_target() -> None:
    result = resolve_body_resource(
        ROOT,
        SOURCE,
        "b.md#边界",
        host_os=HostOS.LINUX,
        target_probe=_fake_probe({ROOT / "pages/b.md": ProbeState.FILE}),  # type: ignore[arg-type]
    )

    assert result.target == "pages/b.md"
    assert result.anchor == "边界"
    assert result.was_relative is True
    assert result.local_absolute == str(ROOT / "pages/b.md")
