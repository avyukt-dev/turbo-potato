"""Closed, application-owned certainty ceilings, independent of prose/provider."""

from uuid import uuid4

import pytest
from news_ai_content.certainty import (
    ClaimPresentation,
    certainty_ceiling,
    presentation_violations,
)
from news_ai_domain import ClaimVerificationStatus as Status
from news_ai_domain import FactCheckLabel as Label


@pytest.mark.parametrize(
    "status,label,strength,frame,valid",
    [
        ("SUPPORTED", "TRUE", "HIGH", "DIRECT", True),
        ("SUPPORTED", "TRUE", "LOW", "UNCERTAIN", True),
        ("SUPPORTED", "MOSTLY_TRUE", "HIGH", "DIRECT", False),
        ("SUPPORTED", "MOSTLY_TRUE", "MEDIUM", "QUALIFIED", True),
        ("PARTIALLY_SUPPORTED", "PARTIALLY_TRUE", "HIGH", "DIRECT", False),
        ("PARTIALLY_SUPPORTED", "PARTIALLY_TRUE", "MEDIUM", "QUALIFIED", True),
        ("PARTIALLY_SUPPORTED", "MISLEADING", "MEDIUM", "QUALIFIED", True),
        ("PARTIALLY_SUPPORTED", "OUT_OF_CONTEXT", "HIGH", "DIRECT", False),
        ("DISPUTED", "UNVERIFIED", "LOW", "DIRECT", False),
        ("DISPUTED", "UNVERIFIED", "LOW", "DISPUTED", True),
        ("UNVERIFIED", "UNVERIFIED", "HIGH", "DIRECT", False),
        ("UNVERIFIED", "UNVERIFIED", "LOW", "UNCERTAIN", True),
        ("REFUTED", "FALSE", "HIGH", "DIRECT", False),
        ("REFUTED", "FALSE", "NONE", "REFUTATION", True),
        ("REFUTED", "FABRICATED", "NONE", "REFUTATION", True),
        ("UNVERIFIED", "SATIRE", "LOW", "DIRECT", False),
        ("UNVERIFIED", "SATIRE", "NONE", "UNCERTAIN", True),
    ],
)
def test_matrix(status, label, strength, frame, valid):
    presentation = ClaimPresentation(
        claim_id=uuid4(),
        source_status=status,
        source_fact_check_label=label,
        assertion_strength=strength,
        frame=frame,
    )
    assert (not presentation_violations(presentation, Status(status), Label(label))) is valid


@pytest.mark.parametrize(
    "status,label",
    [
        ("UNASSESSED", "UNVERIFIED"),
        ("SUPPORTED", "FALSE"),
        ("REFUTED", "TRUE"),
        ("UNVERIFIED", "TRUE"),
        ("DISPUTED", "TRUE"),
        ("PARTIALLY_SUPPORTED", "TRUE"),
    ],
)
def test_impossible_pairs_fail_closed(status, label):
    with pytest.raises(ValueError, match="unsupported"):
        certainty_ceiling(Status(status), Label(label))
