"""Exact, bounded factual value annotations; never evidence or inferred truth."""

import calendar
import json
import re
import unicodedata
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation, localcontext
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Literal
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

VALUE_INTEGRITY_POLICY_VERSION = "value-integrity-policy-v1"


class ClaimValueKind(StrEnum):
    NUMBER = "NUMBER"
    PERCENT = "PERCENT"
    PERCENTAGE_POINT = "PERCENTAGE_POINT"
    CURRENCY = "CURRENCY"
    DATE = "DATE"
    TIME = "TIME"
    DATETIME = "DATETIME"
    DURATION = "DURATION"
    MEASUREMENT = "MEASUREMENT"


class ValueRelation(StrEnum):
    EXACT = "EXACT"
    APPROXIMATE = "APPROXIMATE"
    RANGE = "RANGE"
    AT_LEAST = "AT_LEAST"
    GREATER_THAN = "GREATER_THAN"
    AT_MOST = "AT_MOST"
    LESS_THAN = "LESS_THAN"


class TemporalPrecision(StrEnum):
    YEAR = "YEAR"
    MONTH = "MONTH"
    DAY = "DAY"
    MINUTE = "MINUTE"
    SECOND = "SECOND"


class MeasurementDimension(StrEnum):
    DISTANCE = "DISTANCE"
    MASS = "MASS"


class FrozenValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Quantity(FrozenValue):
    relation: ValueRelation
    amount: Decimal
    upper: Decimal | None = None

    @field_validator("amount", "upper", mode="before")
    @classmethod
    def exact_decimal(cls, value):
        if value is None:
            return value
        if isinstance(value, (float, bool)):
            raise ValueError("factual quantities must not use binary floats")
        try:
            number = Decimal(value)
        except (InvalidOperation, TypeError):
            raise ValueError("invalid exact quantity") from None
        if (
            not number.is_finite()
            or len(number.as_tuple().digits) > 128
            or abs(number.as_tuple().exponent) > 100
        ):
            raise ValueError("factual quantity is non-finite or unbounded")
        with localcontext() as context:
            context.prec = 512
            return number.normalize()

    @model_validator(mode="after")
    def valid_range(self):
        if self.relation == ValueRelation.RANGE:
            if self.upper is None or self.amount >= self.upper:
                raise ValueError("range requires increasing endpoints")
        elif self.upper is not None:
            raise ValueError("only ranges have two endpoints")
        return self


class NumericValue(FrozenValue):
    kind: Literal["NUMBER", "PERCENT", "PERCENTAGE_POINT"]
    quantity: Quantity


class CurrencyValue(FrozenValue):
    kind: Literal["CURRENCY"]
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    quantity: Quantity


class DurationValue(FrozenValue):
    kind: Literal["DURATION"]
    unit: Literal["second", "minute", "hour", "day", "week"]
    quantity: Quantity


class MeasurementValue(FrozenValue):
    kind: Literal["MEASUREMENT"]
    dimension: MeasurementDimension
    unit: Literal["mm", "cm", "m", "km", "mg", "g", "kg", "tonne"]
    quantity: Quantity

    @model_validator(mode="after")
    def valid_dimension(self):
        if UNIT_REGISTRY[self.unit][0] != self.dimension:
            raise ValueError("unit does not belong to dimension")
        return self


class DateValue(FrozenValue):
    kind: Literal["DATE"]
    precision: Literal["YEAR", "MONTH", "DAY"]
    value: str = Field(max_length=10)

    @model_validator(mode="after")
    def valid_date(self):
        patterns = {"YEAR": r"\d{4}", "MONTH": r"\d{4}-\d{2}", "DAY": r"\d{4}-\d{2}-\d{2}"}
        if re.fullmatch(patterns[self.precision], self.value) is None:
            raise ValueError("date does not preserve precision")
        date.fromisoformat(
            self.value + {"YEAR": "-01-01", "MONTH": "-01", "DAY": ""}[self.precision]
        )
        return self


class TimeValue(FrozenValue):
    kind: Literal["TIME"]
    precision: Literal["MINUTE", "SECOND"]
    value: str = Field(max_length=30)

    @model_validator(mode="after")
    def valid_time(self):
        pattern = (
            r"\d{2}:\d{2}"
            + (r":\d{2}" if self.precision == "SECOND" else "")
            + r"(?:Z|[+-]\d{2}:\d{2})?"
        )
        if re.fullmatch(pattern, self.value) is None:
            raise ValueError("time does not preserve precision")
        time.fromisoformat(self.value)
        return self


class DateTimeValue(FrozenValue):
    kind: Literal["DATETIME"]
    precision: Literal["MINUTE", "SECOND"]
    value: str = Field(max_length=40)

    @model_validator(mode="after")
    def valid_instant(self):
        parsed = datetime.fromisoformat(self.value)
        expected = (
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}"
            + (r":\d{2}" if self.precision == "SECOND" else "")
            + r"(?:Z|[+-]\d{2}:\d{2})"
        )
        if re.fullmatch(expected, self.value) is None or parsed.utcoffset() is None:
            raise ValueError("datetime requires known timezone and exact precision")
        return self


class ExactCopyValue(FrozenValue):
    kind: Literal["EXACT_COPY_ONLY"]
    value_kind: ClaimValueKind


CanonicalValue = Annotated[
    NumericValue
    | CurrencyValue
    | DurationValue
    | MeasurementValue
    | DateValue
    | TimeValue
    | DateTimeValue
    | ExactCopyValue,
    Field(discriminator="kind"),
]

# Base units: seconds, metres, grams. No months/years, FX or inferred factors.
UNIT_REGISTRY = MappingProxyType(
    {
        "second": ("DURATION", Decimal("1")),
        "minute": ("DURATION", Decimal("60")),
        "hour": ("DURATION", Decimal("3600")),
        "day": ("DURATION", Decimal("86400")),
        "week": ("DURATION", Decimal("604800")),
        "mm": ("DISTANCE", Decimal("0.001")),
        "cm": ("DISTANCE", Decimal("0.01")),
        "m": ("DISTANCE", Decimal("1")),
        "km": ("DISTANCE", Decimal("1000")),
        "mg": ("MASS", Decimal("0.001")),
        "g": ("MASS", Decimal("1")),
        "kg": ("MASS", Decimal("1000")),
        "tonne": ("MASS", Decimal("1000000")),
    }
)
SCALE_REGISTRY = MappingProxyType(
    {
        "": Decimal(1),
        "thousand": Decimal(1000),
        "million": Decimal(1000000),
        "billion": Decimal(1000000000),
        "trillion": Decimal(1000000000000),
        "lakh": Decimal(100000),
        "crore": Decimal(10000000),
    }
)
_NUMBER = r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_SCALE = r"(?:thousand|million|billion|trillion|lakh|crore)"
_RELATIONS = MappingProxyType(
    {
        "": "EXACT",
        "about ": "APPROXIMATE",
        "approximately ": "APPROXIMATE",
        "at least ": "AT_LEAST",
        ">=": "AT_LEAST",
        ">": "GREATER_THAN",
        "more than ": "GREATER_THAN",
        "at most ": "AT_MOST",
        "<=": "AT_MOST",
        "<": "LESS_THAN",
        "less than ": "LESS_THAN",
    }
)
_ALIASES = MappingProxyType(
    {
        "second": ("second", "seconds", "s"),
        "minute": ("minute", "minutes", "min"),
        "hour": ("hour", "hours", "h"),
        "day": ("day", "days"),
        "week": ("week", "weeks"),
        "mm": ("mm", "millimeter", "millimeters", "millimetre", "millimetres"),
        "cm": ("cm", "centimeter", "centimeters", "centimetre", "centimetres"),
        "m": ("m", "meter", "meters", "metre", "metres"),
        "km": ("km", "kilometer", "kilometers", "kilometre", "kilometres"),
        "mg": ("mg", "milligram", "milligrams"),
        "g": ("g", "gram", "grams"),
        "kg": ("kg", "kilogram", "kilograms"),
        "tonne": ("tonne", "tonnes", "metric tonne", "metric tonnes"),
    }
)


def mechanical_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).split())


def _quantity_text(text: str) -> Quantity:
    lowered = text.lower().strip()
    relation = "EXACT"
    for prefix in sorted(_RELATIONS, key=len, reverse=True):
        if prefix and lowered.startswith(prefix):
            relation = _RELATIONS[prefix]
            lowered = lowered[len(prefix) :].strip()
            break
    pattern = rf"({_NUMBER})(?:\s+({_SCALE}))?"
    match = re.fullmatch(rf"(?:between\s+)?{pattern}\s*(?:–|—|-|to|and)\s*{pattern}", lowered)
    with localcontext() as context:
        context.prec = 512
        if match:
            if relation != "EXACT":
                raise ValueError("unsupported combined range qualifier")
            a, scale_a, b, scale_b = match.groups()
            return Quantity(
                relation="RANGE",
                amount=Decimal(a.replace(",", "")) * SCALE_REGISTRY[scale_a or scale_b or ""],
                upper=Decimal(b.replace(",", "")) * SCALE_REGISTRY[scale_b or scale_a or ""],
            )
        match = re.fullmatch(pattern, lowered)
        if match is None:
            raise ValueError("expression cannot be mechanically normalized")
        amount, scale = match.groups()
        return Quantity(
            relation=relation, amount=Decimal(amount.replace(",", "")) * SCALE_REGISTRY[scale or ""]
        )


def validate_value_text(value: CanonicalValue, text: str) -> None:
    """Small exact expression grammar, not NLP; unsupported expressions require exact copy."""
    text = mechanical_text(text)
    if isinstance(value, ExactCopyValue):
        return
    if isinstance(value, (NumericValue, CurrencyValue, DurationValue, MeasurementValue)):
        expression = text
        if value.kind == "PERCENT":
            expression = re.sub(r"\s*(%|percent)$", "", expression, flags=re.I)
            if expression == text:
                raise ValueError("percentage marker required")
        elif value.kind == "PERCENTAGE_POINT":
            expression = re.sub(r"\s+percentage points?$", "", expression, flags=re.I)
            if expression == text:
                raise ValueError("percentage-point marker required")
        elif isinstance(value, CurrencyValue):
            markers = [value.currency]
            if value.currency == "INR":
                markers += ["₹"]
            # Bare dollar/pound symbols remain ambiguous, never guessed.
            found = next((marker for marker in markers if expression.startswith(marker)), None)
            if found is None:
                raise ValueError("unambiguous currency marker required")
            expression = expression[len(found) :].strip()
        elif isinstance(value, (DurationValue, MeasurementValue)):
            found = next(
                (
                    alias
                    for alias in sorted(_ALIASES[value.unit], key=len, reverse=True)
                    if expression.lower().endswith(" " + alias)
                ),
                None,
            )
            if found is None:
                raise ValueError("declared unit marker required")
            expression = expression[: -(len(found) + 1)].strip()
        if _quantity_text(expression) != value.quantity:
            raise ValueError("normalized quantity differs from supplied expression")
        return
    if isinstance(value, DateValue):
        choices = {value.value}
        parts = value.value.split("-")
        if len(parts) >= 2:
            month = calendar.month_name[int(parts[1])]
            choices.add(
                f"{month} {parts[0]}" if len(parts) == 2 else f"{month} {int(parts[2])}, {parts[0]}"
            )
            if len(parts) == 3:
                choices.add(f"{int(parts[2])} {month} {parts[0]}")
        if text not in choices:
            raise ValueError("date text does not preserve value and precision")
    elif text != value.value:
        raise ValueError("time requires exact explicit ISO representation")


class ClaimValueCandidate(FrozenValue):
    source_text: str = Field(min_length=1, max_length=300)
    value: CanonicalValue

    @field_validator("source_text")
    @classmethod
    def normalize_source_span(cls, value):
        normalized = mechanical_text(value)
        if not normalized:
            raise ValueError("value source span must not be blank")
        return normalized

    @model_validator(mode="after")
    def anchored_expression(self):
        validate_value_text(self.value, self.source_text)
        return self


class ClaimValueAnchor(ClaimValueCandidate):
    anchor_id: UUID


class ClaimValues(FrozenValue):
    policy_version: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9-]+$")
    anchors: tuple[ClaimValueAnchor, ...] = Field(max_length=100)
    ai_run_id: UUID | None = None

    @model_validator(mode="after")
    def unique_anchors(self):
        if len({item.anchor_id for item in self.anchors}) != len(self.anchors):
            raise ValueError("value anchors must be unique")
        return self


def anchors_for_claim(
    claim_id: UUID, candidates: tuple[ClaimValueCandidate, ...]
) -> tuple[ClaimValueAnchor, ...]:
    material = sorted(
        (item.model_dump(mode="json") for item in candidates),
        key=lambda item: json.dumps(item, sort_keys=True),
    )
    return tuple(
        ClaimValueAnchor(
            **item,
            anchor_id=uuid5(claim_id, json.dumps(item, sort_keys=True, separators=(",", ":"))),
        )
        for item in material
    )


class ValueTransformation(StrEnum):
    EXACT = "EXACT"
    FORMAT_EQUIVALENT = "FORMAT_EQUIVALENT"
    EXACT_UNIT_CONVERSION = "EXACT_UNIT_CONVERSION"
    TIMEZONE_EQUIVALENT = "TIMEZONE_EQUIVALENT"


class ValueViolation(StrEnum):
    POLICY_MISMATCH = "POLICY_MISMATCH"
    KIND_MISMATCH = "KIND_MISMATCH"
    RELATION_MISMATCH = "RELATION_MISMATCH"
    MAGNITUDE_MISMATCH = "MAGNITUDE_MISMATCH"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    UNIT_MISMATCH = "UNIT_MISMATCH"
    CONVERSION_NOT_ALLOWED = "CONVERSION_NOT_ALLOWED"
    TEMPORAL_MISMATCH = "TEMPORAL_MISMATCH"
    TEMPORAL_PRECISION_MISMATCH = "TEMPORAL_PRECISION_MISMATCH"
    RENDERED_TEXT_MISMATCH = "RENDERED_TEXT_MISMATCH"


def value_violations(
    anchor: ClaimValueAnchor,
    presented: CanonicalValue,
    rendered: str,
    transformation: ValueTransformation,
) -> tuple[ValueViolation, ...]:
    source = anchor.value
    errors = []
    if source.kind != presented.kind:
        return (ValueViolation.KIND_MISMATCH,)
    if isinstance(source, ExactCopyValue):
        if (
            presented != source
            or transformation != ValueTransformation.EXACT
            or mechanical_text(rendered) != mechanical_text(anchor.source_text)
        ):
            return (ValueViolation.CONVERSION_NOT_ALLOWED,)
        return ()
    try:
        validate_value_text(presented, rendered)
    except ValueError:
        errors.append(ValueViolation.RENDERED_TEXT_MISMATCH)
    if transformation == ValueTransformation.EXACT and mechanical_text(rendered) != mechanical_text(
        anchor.source_text
    ):
        errors.append(ValueViolation.RENDERED_TEXT_MISMATCH)
    if hasattr(source, "quantity"):
        if source.quantity.relation != presented.quantity.relation:
            errors.append(ValueViolation.RELATION_MISMATCH)
        if isinstance(source, CurrencyValue) and source.currency != presented.currency:
            errors.append(ValueViolation.CURRENCY_MISMATCH)
        if isinstance(source, (DurationValue, MeasurementValue)):
            if transformation not in (
                ValueTransformation.EXACT,
                ValueTransformation.FORMAT_EQUIVALENT,
                ValueTransformation.EXACT_UNIT_CONVERSION,
            ):
                errors.append(ValueViolation.CONVERSION_NOT_ALLOWED)
            if isinstance(source, MeasurementValue) and source.dimension != presented.dimension:
                errors.append(ValueViolation.UNIT_MISMATCH)
            if (
                source.unit != presented.unit
                and transformation != ValueTransformation.EXACT_UNIT_CONVERSION
            ):
                errors.append(ValueViolation.CONVERSION_NOT_ALLOWED)
            a, b = UNIT_REGISTRY[source.unit], UNIT_REGISTRY[presented.unit]
            with localcontext() as context:
                context.prec = 512
                if (
                    a[0] != b[0]
                    or source.quantity.amount * a[1] != presented.quantity.amount * b[1]
                    or (None if source.quantity.upper is None else source.quantity.upper * a[1])
                    != (
                        None
                        if presented.quantity.upper is None
                        else presented.quantity.upper * b[1]
                    )
                ):
                    errors.append(ValueViolation.MAGNITUDE_MISMATCH)
        else:
            if transformation not in (
                ValueTransformation.EXACT,
                ValueTransformation.FORMAT_EQUIVALENT,
            ):
                errors.append(ValueViolation.CONVERSION_NOT_ALLOWED)
            if (
                source.quantity.amount != presented.quantity.amount
                or source.quantity.upper != presented.quantity.upper
            ):
                errors.append(ValueViolation.MAGNITUDE_MISMATCH)
    else:
        if source.precision != presented.precision:
            errors.append(ValueViolation.TEMPORAL_PRECISION_MISMATCH)
        if (
            isinstance(source, DateTimeValue)
            and transformation == ValueTransformation.TIMEZONE_EQUIVALENT
        ):
            same = datetime.fromisoformat(source.value) == datetime.fromisoformat(presented.value)
        else:
            same = source.value == presented.value
            if transformation not in (
                ValueTransformation.EXACT,
                ValueTransformation.FORMAT_EQUIVALENT,
            ):
                errors.append(ValueViolation.CONVERSION_NOT_ALLOWED)
        if not same:
            errors.append(ValueViolation.TEMPORAL_MISMATCH)
    return tuple(dict.fromkeys(errors))
