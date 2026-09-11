from typing import get_args

import pytest
from pydantic import ValidationError

from evidencegraph.docket.questions import (
    COLUMN_QUESTIONS,
    ENTITY_QUESTIONS,
    QUESTIONS,
    RELATION_QUESTIONS,
)
from evidencegraph.schema import TABLE_MODELS, EntityKind, Outcome, Relation, RelationKind


def test_question_traceability():
    assert set(QUESTIONS) == {f"DQ{i}" for i in range(1, 9)} | {f"LQ{i}" for i in range(1, 9)}
    assert set(ENTITY_QUESTIONS) == set(get_args(EntityKind))
    assert set(RELATION_QUESTIONS) == set(get_args(RelationKind))
    assert set(COLUMN_QUESTIONS) == set(TABLE_MODELS)
    for table, model in TABLE_MODELS.items():
        question, fields = COLUMN_QUESTIONS[table]
        assert question in QUESTIONS
        assert set(fields.split()) == set(model.model_fields)


def relation(**overrides):
    return Relation.model_validate(
        {
            "relation_id": "r-test",
            "kind": "produced",
            "subject_id": "a",
            "object_id": "b",
            "outcome": "supported",
            "method": "test",
            "rationale": "test",
            "witness_ids": ["w-1"],
            "citation_ids": ["ref-1"],
            "candidates": ["b"],
            **overrides,
        }
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"candidates": []},
        {"candidates": ["a", "b"]},
        {"citation_ids": []},
        {"witness_ids": []},
        {"kind": "same_actor_as", "authenticated": False},
    ],
)
def test_invalid_claims_fail_closed(overrides):
    with pytest.raises(ValidationError):
        relation(**overrides)


def test_models_are_frozen_and_forbid_extra():
    with pytest.raises(ValidationError):
        relation(novel_column=True)
    value = relation()
    with pytest.raises(ValidationError):
        value.outcome = Outcome.UNMATCHED
