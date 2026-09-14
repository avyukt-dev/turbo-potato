"""Evidence graph edges are typed provenance, never inferred independence or truth."""

from uuid import uuid4

import pytest
from news_ai_evidence import (
    EvidenceGraphRelationSpec,
    EvidenceGraphRelationType,
    EvidenceOriginRole,
    EvidenceProvenanceState,
)
from pydantic import ValidationError


def test_graph_relation_contract_is_closed_and_requires_exactly_one_target():
    external = EvidenceGraphRelationSpec(
        relation_type=EvidenceGraphRelationType.DERIVED_FROM,
        external_reference="https://wire.example/original",
        basis="explicit-originating_url",
    )
    durable = EvidenceGraphRelationSpec(
        relation_type=EvidenceGraphRelationType.UPDATES,
        target_evidence_id=uuid4(),
        basis="explicit-update-reference",
    )
    assert external.relation_type is EvidenceGraphRelationType.DERIVED_FROM
    assert durable.target_evidence_id is not None
    for payload in (
        {"relation_type": "UNKNOWN", "external_reference": "x", "basis": "explicit"},
        {"relation_type": "REFERENCES", "basis": "missing-target"},
        {"relation_type": "REFERENCES", "external_reference": " ", "basis": "blank"},
        {
            "relation_type": "REFERENCES",
            "external_reference": "https://user:secret@example.org/a",
            "basis": "unsafe",
        },
        {
            "relation_type": "REFERENCES",
            "target_evidence_id": str(uuid4()),
            "external_reference": "x",
            "basis": "two-targets",
        },
    ):
        with pytest.raises(ValidationError):
            EvidenceGraphRelationSpec.model_validate(payload)


def test_graph_vocabulary_does_not_encode_authority_or_independence():
    assert {item.value for item in EvidenceGraphRelationType} == {
        "DERIVED_FROM",
        "REFERENCES",
        "CONTEXT_FOR",
        "VERIFIES",
        "UPDATES",
    }
    assert "INDEPENDENT" not in EvidenceGraphRelationType.__members__
    assert "AUTHORITY" not in EvidenceGraphRelationType.__members__
    assert EvidenceOriginRole.UNKNOWN.value == "UNKNOWN"
    assert EvidenceProvenanceState.UNKNOWN.value == "UNKNOWN"
