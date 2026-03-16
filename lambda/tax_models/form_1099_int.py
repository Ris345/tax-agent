"""
Pydantic v2 model for IRS Form 1099-INT (Interest Income).

Reference: https://www.irs.gov/forms-pubs/about-form-1099-int

Issued by banks, credit unions, brokerages, and the US Treasury when they pay
$10+ (or $600+ for certain institutions) in interest during the year.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import Field, field_validator, model_validator

from .common import (
    EIN,
    TIN,
    NonNegativeDollar,
    StateCode,
    TaxFormBase,
    TaxYear,
)

# CUSIP numbers are exactly 9 alphanumeric characters.
_CUSIP_RE_PATTERN = r"^[A-Z0-9]{9}$"


class Form1099INT(TaxFormBase):
    """
    IRS Form 1099-INT — Interest Income.

    Covers all 18 boxes.  Cross-field invariants:
    - foreign_country required when foreign_tax_paid is set.
    - specified_private_activity_bond_interest ≤ tax_exempt_interest (Box 9 ⊆ Box 8).
    - federal_income_tax_withheld ≤ interest_income.
    - State code required when any state-level field is provided.
    - CUSIP number format validated when present (Box 14).
    """

    document_type: Literal["1099-INT"] = "1099-INT"
    tax_year: TaxYear

    # ── Payer (the financial institution) ────────────────────────────────────

    payer_tin: EIN = Field(description="Payer's taxpayer identification number (EIN)")
    payer_name: str = Field(min_length=1, max_length=75)
    payer_address: Optional[str] = Field(None, max_length=200)

    # ── Recipient ─────────────────────────────────────────────────────────────

    recipient_tin: TIN = Field(
        description="Recipient's TIN — SSN for individuals, EIN for entities"
    )
    recipient_name: str = Field(min_length=1, max_length=75)
    recipient_address: Optional[str] = Field(None, max_length=200)
    account_number: Optional[str] = Field(None, max_length=20)

    # ── Box 1: required interest income ──────────────────────────────────────

    interest_income: NonNegativeDollar = Field(
        description="Box 1 — Interest income (total taxable interest paid)"
    )

    # ── Box 2: early withdrawal penalty ──────────────────────────────────────

    early_withdrawal_penalty: Optional[NonNegativeDollar] = Field(
        None,
        description="Box 2 — Early withdrawal penalty (forfeited interest on CD)",
    )

    # ── Box 3: US Savings Bond / Treasury interest ────────────────────────────

    us_savings_bond_interest: Optional[NonNegativeDollar] = Field(
        None,
        description=(
            "Box 3 — Interest on US Savings Bonds and Treasury obligations "
            "(exempt from state/local tax)"
        ),
    )

    # ── Box 4: federal backup withholding ────────────────────────────────────

    federal_income_tax_withheld: Optional[NonNegativeDollar] = Field(
        None, description="Box 4 — Federal income tax withheld (backup withholding)"
    )

    # ── Box 5: investment expenses ────────────────────────────────────────────

    investment_expenses: Optional[NonNegativeDollar] = Field(
        None,
        description=(
            "Box 5 — Investment expenses (single-class REMICs only; "
            "not deductible post-TCJA for individuals)"
        ),
    )

    # ── Boxes 6–7: foreign tax ────────────────────────────────────────────────

    foreign_tax_paid: Optional[NonNegativeDollar] = Field(
        None, description="Box 6 — Foreign tax paid"
    )
    foreign_country: Optional[str] = Field(
        None, max_length=100,
        description="Box 7 — Foreign country or US possession",
    )

    # ── Boxes 8–10: tax-exempt and market discount interest ───────────────────

    tax_exempt_interest: Optional[NonNegativeDollar] = Field(
        None,
        description=(
            "Box 8 — Tax-exempt interest (municipal bonds, etc.); "
            "reported on Form 1040 even though tax-exempt"
        ),
    )
    specified_private_activity_bond_interest: Optional[NonNegativeDollar] = Field(
        None,
        description=(
            "Box 9 — Specified private activity bond interest subject to AMT; "
            "must be a subset of Box 8"
        ),
    )
    market_discount: Optional[NonNegativeDollar] = Field(
        None,
        description="Box 10 — Market discount (accrued on bonds bought below face)",
    )

    # ── Boxes 11–13: bond premium ─────────────────────────────────────────────

    bond_premium: Optional[NonNegativeDollar] = Field(
        None, description="Box 11 — Bond premium (reduces taxable interest)"
    )
    bond_premium_on_treasury_obligations: Optional[NonNegativeDollar] = Field(
        None, description="Box 12 — Bond premium on US Treasury obligations"
    )
    bond_premium_on_tax_exempt_bonds: Optional[NonNegativeDollar] = Field(
        None, description="Box 13 — Bond premium on tax-exempt bonds"
    )

    # ── Box 14: CUSIP number ──────────────────────────────────────────────────

    tax_exempt_bond_cusip: Optional[str] = Field(
        None,
        min_length=9,
        max_length=9,
        description="Box 14 — CUSIP number of the tax-exempt bond",
        pattern=_CUSIP_RE_PATTERN,
    )

    # ── Boxes 15–18: state withholding ───────────────────────────────────────

    state_tax_withheld: Optional[NonNegativeDollar] = Field(
        None, description="Box 15 — State tax withheld"
    )
    state: Optional[StateCode] = Field(
        None, description="Box 16 — State"
    )
    state_id_no: Optional[str] = Field(
        None, max_length=20,
        description="Box 17 — Payer's state identification number",
    )
    state_income: Optional[NonNegativeDollar] = Field(
        None, description="Box 18 — State income"
    )

    # ── Cross-field validation ────────────────────────────────────────────────

    @model_validator(mode="after")
    def _cross_field_invariants(self) -> "Form1099INT":
        # Box 6 requires Box 7 (foreign tax paid requires a country)
        if self.foreign_tax_paid is not None and not self.foreign_country:
            raise ValueError(
                "foreign_country (Box 7) is required when foreign_tax_paid (Box 6) is set"
            )

        # Box 9 is a subset of Box 8 (AMT interest ⊆ total tax-exempt interest)
        if (
            self.specified_private_activity_bond_interest is not None
            and self.tax_exempt_interest is not None
            and self.specified_private_activity_bond_interest > self.tax_exempt_interest
        ):
            raise ValueError(
                f"specified_private_activity_bond_interest (Box 9: "
                f"{self.specified_private_activity_bond_interest}) cannot exceed "
                f"tax_exempt_interest (Box 8: {self.tax_exempt_interest})"
            )

        # Backup withholding ≤ total interest paid
        if (
            self.federal_income_tax_withheld is not None
            and self.federal_income_tax_withheld > self.interest_income
        ):
            raise ValueError(
                f"federal_income_tax_withheld ({self.federal_income_tax_withheld}) "
                f"cannot exceed interest_income ({self.interest_income})"
            )

        # State code required when any state field is populated
        if any(
            v is not None
            for v in (self.state_tax_withheld, self.state_id_no, self.state_income)
        ) and self.state is None:
            raise ValueError(
                "state (Box 16) is required when state_tax_withheld, "
                "state_id_no, or state_income is provided"
            )

        return self
