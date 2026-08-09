from __future__ import annotations

import pytest

from draft.vocabulary import (
    EditableRelationCode,
    RelationCode,
    StatusCode,
    relation_code,
    relation_label,
    status_code,
    status_label,
)


def test_every_status_code_round_trips_through_the_markdown_label() -> None:
    for code in StatusCode:
        assert status_code(status_label(code)) is code


def test_every_relation_code_round_trips_through_the_markdown_label() -> None:
    for code in RelationCode:
        assert relation_code(relation_label(code)) is code


def test_editable_relations_are_only_active_directions() -> None:
    assert tuple(EditableRelationCode) == (
        EditableRelationCode.DEPENDS_ON,
        EditableRelationCode.SUPERSEDES,
        EditableRelationCode.CHALLENGES,
    )


def test_removed_relation_is_not_published() -> None:
    assert "OUTPUT" not in RelationCode.__members__


def test_unknown_labels_raise_value_error() -> None:
    with pytest.raises(ValueError):
        status_code("进行中")
    with pytest.raises(ValueError):
        relation_code("产出")


def test_editable_relation_values_match_relation_codes() -> None:
    for editable in EditableRelationCode:
        assert editable.value in {code.value for code in RelationCode}
