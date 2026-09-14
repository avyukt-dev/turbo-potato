"""Closed, application-owned certainty ceilings, independent of prose/provider."""

from itertools import product
from uuid import uuid4

import pytest
from news_ai_content.certainty import (
    _STRENGTH_RANK,
    ClaimAssertionStrength,
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
        ("PARTIALLY_SUPPORTED", "PARTIALLY_TRUE", "HIGH", "DIRECT", False),
        ("PARTIALLY_SUPPORTED", "PARTIALLY_TRUE", "MEDIUM", "QUALIFIED", True),
        ("DISPUTED", "UNVERIFIED", "LOW", "DIRECT", False),
        ("DISPUTED", "UNVERIFIED", "LOW", "DISPUTED", True),
        ("UNVERIFIED", "UNVERIFIED", "HIGH", "DIRECT", False),
        ("UNVERIFIED", "UNVERIFIED", "LOW", "UNCERTAIN", True),
        ("REFUTED", "FALSE", "HIGH", "DIRECT", False),
        ("REFUTED", "FALSE", "NONE", "REFUTATION", True),
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
    tuple(product(Status, Label)),
)
def test_only_current_producer_pairs_are_authorized(status, label):
    accepted = {
        (Status.SUPPORTED, Label.TRUE): ("HIGH", ("DIRECT", "QUALIFIED", "UNCERTAIN")),
        (Status.PARTIALLY_SUPPORTED, Label.PARTIALLY_TRUE): ("MEDIUM", ("QUALIFIED", "UNCERTAIN")),
        (Status.DISPUTED, Label.UNVERIFIED): ("LOW", ("DISPUTED",)),
        (Status.UNVERIFIED, Label.UNVERIFIED): ("LOW", ("UNCERTAIN",)),
        (Status.REFUTED, Label.FALSE): ("NONE", ("REFUTATION",)),
    }
    if (status, label) in accepted:
        ceiling = certainty_ceiling(status, label)
        assert (ceiling.maximum_strength, ceiling.allowed_frames) == accepted[(status, label)]
    else:
        with pytest.raises(
            ValueError, match="unsupported immutable claim status/label combination"
        ):
            certainty_ceiling(status, label)
        presentation = ClaimPresentation(
            claim_id=uuid4(),
            source_status=status,
            source_fact_check_label=label,
            assertion_strength="NONE",
            frame="UNCERTAIN",
        )
        assert "SOURCE_COMBINATION_INVALID" in presentation_violations(presentation, status, label)


def test_strength_order_is_explicit_and_immutable():
    assert dict(_STRENGTH_RANK) == {
        ClaimAssertionStrength.NONE: 0,
        ClaimAssertionStrength.LOW: 1,
        ClaimAssertionStrength.MEDIUM: 2,
        ClaimAssertionStrength.HIGH: 3,
    }
    with pytest.raises(TypeError):
        _STRENGTH_RANK[ClaimAssertionStrength.HIGH] = 0
