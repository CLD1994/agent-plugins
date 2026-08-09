from __future__ import annotations

from pathlib import Path

import pytest

from draft.plan_effects import (
    PlanAction,
    PlanEffect,
    public_changed_paths,
)


def test_move_requires_from_and_to_only() -> None:
    with pytest.raises(ValueError):
        PlanEffect(action=PlanAction.MOVE, path=Path("a.md"))
    with pytest.raises(ValueError):
        PlanEffect(action=PlanAction.MOVE, from_path=Path("a.md"), to_path=None)
    effect = PlanEffect(action=PlanAction.MOVE, from_path=Path("a.md"), to_path=Path("b.md"))
    assert effect.from_path == Path("a.md")
    assert effect.to_path == Path("b.md")


def test_non_move_requires_path_only() -> None:
    for action in (PlanAction.CREATE, PlanAction.MODIFY, PlanAction.REMOVE):
        with pytest.raises(ValueError):
            PlanEffect(action=action, from_path=Path("a.md"))
        effect = PlanEffect(action=action, path=Path("a.md"))
        assert effect.path == Path("a.md")


def test_public_changed_paths_deduplicates_in_order() -> None:
    effects = (
        PlanEffect(action=PlanAction.CREATE, path=Path("pages/a.md")),
        PlanEffect(
            action=PlanAction.MOVE, from_path=Path("pages/b.md"), to_path=Path("pages/c.md")
        ),
        PlanEffect(action=PlanAction.MODIFY, path=Path("pages/a.md")),
        PlanEffect(action=PlanAction.REMOVE, path=Path("pages/b.md")),
    )
    assert public_changed_paths(effects) == (
        Path("pages/a.md"),
        Path("pages/b.md"),
        Path("pages/c.md"),
    )


def test_public_changed_paths_never_starts_with_dot_draft() -> None:
    effects = (
        PlanEffect(action=PlanAction.MODIFY, path=Path("index.md")),
        PlanEffect(action=PlanAction.REMOVE, path=Path("pages/a.md")),
    )
    for path in public_changed_paths(effects):
        assert not path.as_posix().startswith(".draft/")


def test_move_is_a_single_effect_not_create_plus_remove() -> None:
    """结构移动在效果层表现为一个 MOVE，而不是 CREATE 加 REMOVE。"""
    effects = (
        PlanEffect(
            action=PlanAction.MOVE, from_path=Path("pages/a.md"), to_path=Path("pages/b.md")
        ),
    )
    assert [effect.action for effect in effects] == [PlanAction.MOVE]
    assert len(effects) == 1
