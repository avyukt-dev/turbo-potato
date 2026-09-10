from news_ai_domain import ClaimVerificationStatus, FactCheckLabel


def test_claim_status_and_fact_check_label_are_separate_vocabularies() -> None:
    claim_status_values = {status.value for status in ClaimVerificationStatus}
    fact_check_values = {label.value for label in FactCheckLabel}

    assert "PARTIALLY_SUPPORTED" in claim_status_values
    assert "PARTIALLY_TRUE" not in claim_status_values
    assert "PARTIALLY_TRUE" in fact_check_values
    assert "PARTIALLY_SUPPORTED" not in fact_check_values


def test_unverified_is_not_false_or_refuted() -> None:
    assert ClaimVerificationStatus.UNVERIFIED != ClaimVerificationStatus.REFUTED
    assert FactCheckLabel.UNVERIFIED != FactCheckLabel.FALSE
