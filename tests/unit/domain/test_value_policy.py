"""Exact expression and conversion policy; no provider or prose inference."""

from uuid import uuid4

import pytest
from news_ai_domain.values import (
    ClaimValueCandidate,
    ClaimValueKind,
    Quantity,
    anchors_for_claim,
    value_violations,
)
from pydantic import ValidationError


def numeric(text, amount, kind="NUMBER", relation="EXACT", upper=None):
    return ClaimValueCandidate(
        source_text=text,
        value={"kind": kind, "quantity": {"relation": relation, "amount": amount, "upper": upper}},
    )


def measurement(text, amount, unit="km", dimension="DISTANCE", relation="EXACT", upper=None):
    return ClaimValueCandidate(
        source_text=text,
        value={
            "kind": "MEASUREMENT",
            "dimension": dimension,
            "unit": unit,
            "quantity": {"relation": relation, "amount": amount, "upper": upper},
        },
    )


def currency(text, amount, code="USD"):
    return ClaimValueCandidate(
        source_text=text,
        value={
            "kind": "CURRENCY",
            "currency": code,
            "quantity": {"relation": "EXACT", "amount": amount, "upper": None},
        },
    )


def temporal(text, value, precision="DAY", kind="DATE"):
    return ClaimValueCandidate(
        source_text=text, value={"kind": kind, "value": value, "precision": precision}
    )


def compare(source, target, transformation="FORMAT_EQUIVALENT"):
    anchor = anchors_for_claim(uuid4(), (source,))[0]
    return value_violations(anchor, target.value, target.source_text, transformation)


@pytest.mark.parametrize(
    "text,amount",
    [
        ("1.2 billion", "1200000000"),
        ("1,200 million", "1200000000"),
        ("1,200,000", "1200000"),
        ("1.2 million", "1200000"),
        ("1 thousand", "1000"),
        ("1 trillion", "1000000000000"),
        ("1 lakh", "100000"),
        ("1 crore", "10000000"),
    ],
)
def test_exact_scale_normalization(text, amount):
    assert str(numeric(text, amount).value.quantity.amount) == str(
        Quantity(relation="EXACT", amount=amount).amount
    )


@pytest.mark.parametrize(
    "a,av,b,bv",
    [
        ("1.2 billion", "1200000000", "1.2 million", "1200000"),
        ("120 million", "120000000", "120 billion", "120000000000"),
        ("100", "100", "101", "101"),
        ("5.5", "5.5", "5.6", "5.6"),
    ],
)
def test_magnitude_changes_fail(a, av, b, bv):
    assert "MAGNITUDE_MISMATCH" in compare(numeric(a, av), numeric(b, bv))


def test_format_equivalence_is_exact_and_identity_is_application_owned():
    assert not compare(numeric("1.2 billion", "1200000000"), numeric("1,200 million", "1200000000"))
    assert not compare(numeric("5", "5"), numeric("5.0", "5.0"))
    claim = uuid4()
    assert anchors_for_claim(claim, (numeric("5", "5"),)) == anchors_for_claim(
        claim, (numeric("5", "5.0"),)
    )


@pytest.mark.parametrize(
    "value", [float("nan"), float("inf"), 5.1, "NaN", "Infinity", "invalid", "1e101", True]
)
def test_nonexact_or_nonfinite_quantities_fail(value):
    with pytest.raises(ValidationError):
        Quantity(relation="EXACT", amount=value)


def test_percent_points_and_dimensionless_numbers_are_distinct():
    percent = numeric("10%", "10", "PERCENT")
    points = numeric("10 percentage points", "10", "PERCENTAGE_POINT")
    assert "KIND_MISMATCH" in compare(percent, points)
    assert "KIND_MISMATCH" in compare(points, percent)
    assert "KIND_MISMATCH" in compare(numeric("0.52", "0.52"), numeric("52%", "52", "PERCENT"))
    assert not compare(numeric("52%", "52", "PERCENT"), numeric("52 percent", "52", "PERCENT"))


@pytest.mark.parametrize(
    "relation,text",
    [
        ("APPROXIMATE", "about 5"),
        ("AT_LEAST", "at least 5"),
        ("GREATER_THAN", ">5"),
        ("AT_MOST", "at most 5"),
        ("LESS_THAN", "<5"),
    ],
)
def test_relation_cannot_be_lost(relation, text):
    assert "RELATION_MISMATCH" in compare(numeric(text, "5", relation=relation), numeric("5", "5"))


def test_strict_bounds_and_ranges_are_preserved():
    assert "RELATION_MISMATCH" in compare(
        numeric("at least 5", "5", relation="AT_LEAST"), numeric(">5", "5", relation="GREATER_THAN")
    )
    source = numeric("5–7", "5", relation="RANGE", upper="7")
    assert compare(source, numeric("6", "6"))
    assert compare(source, numeric("5–8", "5", relation="RANGE", upper="8"))
    with pytest.raises(ValidationError):
        Quantity(relation="RANGE", amount="7", upper="5")
    with pytest.raises(ValidationError):
        Quantity(relation="EXACT", amount="5", upper="6")


def test_currency_preservation_no_fx_or_ambiguous_inference():
    assert "CURRENCY_MISMATCH" in compare(
        currency("USD 100", "100"), currency("EUR 100", "100", "EUR")
    )
    assert compare(
        currency("INR 1 crore", "10000000", "INR"), currency("INR 1 lakh", "100000", "INR")
    )
    assert not compare(
        currency("INR 1 crore", "10000000", "INR"), currency("INR 10 million", "10000000", "INR")
    )
    with pytest.raises(ValidationError):
        currency("$5 million", "5000000")


def test_temporal_precision_and_dates_are_not_invented():
    assert not compare(
        temporal("September 14, 2026", "2026-09-14"), temporal("2026-09-14", "2026-09-14")
    )
    assert "TEMPORAL_PRECISION_MISMATCH" in compare(
        temporal("September 2026", "2026-09", "MONTH"), temporal("September 1, 2026", "2026-09-01")
    )
    assert compare(temporal("2026", "2026", "YEAR"), temporal("2026-01-01", "2026-01-01"))
    assert compare(temporal("2026-09-14", "2026-09-14"), temporal("2026-09-15", "2026-09-15"))
    with pytest.raises(ValidationError):
        temporal("2026-02-30", "2026-02-30")


def test_aware_instant_equivalence_and_naive_time_guard():
    source = temporal("2026-09-14T10:00Z", "2026-09-14T10:00Z", "MINUTE", "DATETIME")
    target = temporal("2026-09-14T15:30+05:30", "2026-09-14T15:30+05:30", "MINUTE", "DATETIME")
    assert not compare(source, target, "TIMEZONE_EQUIVALENT")
    assert compare(source, target)
    assert compare(
        temporal("10:00", "10:00", "MINUTE", "TIME"), temporal("10:00Z", "10:00Z", "MINUTE", "TIME")
    )
    with pytest.raises(ValidationError):
        temporal("2026-09-14T10:00", "2026-09-14T10:00", "MINUTE", "DATETIME")


def test_exact_registry_conversion_and_dimension_guards():
    assert not compare(
        measurement("5 km", "5"), measurement("5000 m", "5000", "m"), "EXACT_UNIT_CONVERSION"
    )
    assert compare(measurement("5 km", "5"), measurement("5 m", "5", "m"), "EXACT_UNIT_CONVERSION")
    assert compare(
        measurement("5 kg", "5", "kg", "MASS"), measurement("5 km", "5"), "EXACT_UNIT_CONVERSION"
    )
    a = ClaimValueCandidate(
        source_text="2 hours",
        value={
            "kind": "DURATION",
            "unit": "hour",
            "quantity": {"relation": "EXACT", "amount": "2"},
        },
    )
    b = ClaimValueCandidate(
        source_text="120 minutes",
        value={
            "kind": "DURATION",
            "unit": "minute",
            "quantity": {"relation": "EXACT", "amount": "120"},
        },
    )
    assert not compare(a, b, "EXACT_UNIT_CONVERSION")
    assert compare(a, b)
    with pytest.raises(ValidationError):
        measurement("3 miles", "3", "mile")


def test_exact_copy_only_never_acquires_specificity():
    source = ClaimValueCandidate(
        source_text="$5 million", value={"kind": "EXACT_COPY_ONLY", "value_kind": "CURRENCY"}
    )
    assert not compare(source, source, "EXACT")
    assert compare(source, source, "FORMAT_EQUIVALENT")
    assert compare(source, currency("USD 5 million", "5000000"))


def test_invalid_annotation_cannot_lie_about_source_magnitude_or_kind():
    with pytest.raises(ValidationError):
        numeric("1.2 billion", "1200000")
    with pytest.raises(ValidationError):
        numeric("10%", "10", "PERCENTAGE_POINT")
    with pytest.raises(ValidationError):
        numeric("5", "5", "UNKNOWN")
    assert len(ClaimValueKind) == 9
