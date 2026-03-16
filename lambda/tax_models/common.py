"""
Shared Pydantic v2 annotated types, validators, and base model.

All IRS tax form models in this package build on the types defined here so
that every validation rule is defined exactly once and can be tested in
isolation.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict

# ─── Regex patterns ───────────────────────────────────────────────────────────

_EIN_RE = re.compile(r"^\d{2}-\d{7}$")           # XX-XXXXXXX
_SSN_RE = re.compile(r"^\d{3}-\d{2}-\d{4}$")     # XXX-XX-XXXX
_TIN_RE = re.compile(r"^(\d{2}-\d{7}|\d{3}-\d{2}-\d{4})$")  # either

# ─── US state and territory codes ────────────────────────────────────────────

US_STATE_CODES: frozenset[str] = frozenset({
    # 50 states
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    # Federal district + US territories accepted on IRS forms
    "DC", "AS", "GU", "MP", "PR", "VI",
})

# ─── Dollar amount annotated types ───────────────────────────────────────────

_TWO_PLACES = Decimal("0.01")


def _coerce_to_decimal(v: Any) -> Decimal:
    """
    Accept str, int, float, or Decimal.  Strips leading '$' and commas so
    that raw OCR output (e.g. "$52,350.00") is accepted without pre-processing.
    """
    if isinstance(v, str):
        v = v.replace("$", "").replace(",", "").strip()
        if v in ("", "N/A", "n/a", "-"):
            raise ValueError("Empty or placeholder string cannot be a dollar amount")
    try:
        d = Decimal(str(v))
    except InvalidOperation:
        raise ValueError(f"Cannot convert {v!r} to a decimal dollar amount")

    # Reject more than 2 decimal places — quantize would silently round,
    # so we check the exponent before normalising.
    _, _, exp = d.as_tuple()
    if exp < -2:
        raise ValueError(
            f"Dollar amount {d} has more than 2 decimal places "
            f"(got {-exp}); tax forms allow a maximum of 2"
        )
    return d.quantize(_TWO_PLACES, rounding=ROUND_HALF_EVEN)


def _assert_non_negative(v: Decimal) -> Decimal:
    if v < 0:
        raise ValueError(f"Dollar amount cannot be negative: {v}")
    return v


# NonNegativeDollar — the default for wages, taxes, interest, etc.
NonNegativeDollar = Annotated[
    Decimal,
    BeforeValidator(_coerce_to_decimal),
    AfterValidator(_assert_non_negative),
]

# SignedDollar — used for gains/losses, adjustments, and net positions
# (same coercion pipeline, negative values allowed)
SignedDollar = Annotated[
    Decimal,
    BeforeValidator(_coerce_to_decimal),
]

# ─── Identifier annotated types ───────────────────────────────────────────────


def _validate_ein(v: str) -> str:
    """Employer Identification Number: XX-XXXXXXX (two digits, hyphen, seven digits)."""
    v = v.strip()
    if not _EIN_RE.match(v):
        raise ValueError(
            f"EIN must be in XX-XXXXXXX format (e.g. '12-3456789'), got: {v!r}"
        )
    return v


def _validate_ssn(v: str) -> str:
    """Social Security Number: XXX-XX-XXXX."""
    v = v.strip()
    if not _SSN_RE.match(v):
        raise ValueError(
            f"SSN must be in XXX-XX-XXXX format (e.g. '123-45-6789'), got: {v!r}"
        )
    return v


def _validate_tin(v: str) -> str:
    """
    Taxpayer Identification Number — accepts either EIN (XX-XXXXXXX)
    or SSN (XXX-XX-XXXX).  Used for 1099 recipient fields where the
    filer can be a business (EIN) or an individual (SSN).
    """
    v = v.strip()
    if not _TIN_RE.match(v):
        raise ValueError(
            f"TIN must be an EIN (XX-XXXXXXX) or SSN (XXX-XX-XXXX), got: {v!r}"
        )
    return v


EIN = Annotated[str, AfterValidator(_validate_ein)]
SSN = Annotated[str, AfterValidator(_validate_ssn)]
TIN = Annotated[str, AfterValidator(_validate_tin)]   # EIN or SSN

# ─── State code annotated type ────────────────────────────────────────────────


def _validate_state_code(v: str) -> str:
    upper = v.strip().upper()
    if upper not in US_STATE_CODES:
        raise ValueError(
            f"Invalid US state/territory code: {v!r}. "
            f"Must be one of: {', '.join(sorted(US_STATE_CODES))}"
        )
    return upper


StateCode = Annotated[str, AfterValidator(_validate_state_code)]

# ─── Tax year annotated type ──────────────────────────────────────────────────

_MIN_TAX_YEAR = 1990


def _validate_tax_year(v: int) -> int:
    current = date.today().year
    if not (_MIN_TAX_YEAR <= v <= current):
        raise ValueError(
            f"Tax year {v} is outside the valid range "
            f"({_MIN_TAX_YEAR}–{current})"
        )
    return v


TaxYear = Annotated[int, AfterValidator(_validate_tax_year)]

# ─── Base model ───────────────────────────────────────────────────────────────


class TaxFormBase(BaseModel):
    """
    Shared Pydantic v2 configuration for all IRS tax form models.

    frozen=True         — tax forms are immutable records; mutation raises an error.
    str_strip_whitespace — silently trims leading/trailing whitespace from OCR output.
    use_enum_values     — stores the raw string value of Enum fields (round-trips as JSON).
    populate_by_name    — accept field names regardless of any alias defined.
    extra="forbid"      — unknown fields raise ValidationError (protects data integrity).
    """

    model_config = ConfigDict(
        frozen=True,
        str_strip_whitespace=True,
        use_enum_values=True,
        populate_by_name=True,
        extra="forbid",
    )
