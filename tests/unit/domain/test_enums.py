from news_ai_domain import ClaimVerificationStatus, FactCheckLabel


def test_claim_status_and_fact_check_label_are_separate_vocabularies() -> None:
    assert "PARTIALLY_SUPPORTED" in ClaimVerificationStatus
    assert "PARTIALLY_TRUE" not in ClaimVerificationStatus
    assert "PARTIALLY_TRUE" in FactCheckLabel
    assert "PARTIALLY_SUPPORTED" not in FactCheckLabel


def test_unverified_is_not_false_or_refuted() -> None:
    assert ClaimVerificationStatus.UNVERIFIED != ClaimVerificationStatus.REFUTED
    assert FactCheckLabel.UNVERIFIED != FactCheckLabel.FALSE
