"""
Pydantic v2 model for IRS Form 1099-NEC (Nonemployee Compensation).

Reference: https://www.irs.gov/forms-pubs/about-form-1099-nec

Issued when a business pays $600+ to a non-employee (independent contractor,
freelancer, etc.) during the tax year.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import Field, model_validator

from .common import (
    EIN,
    TIN,
    NonNegativeDollar,
    StateCode,
    TaxFormBase,
    TaxYear,
)


class Form1099NEC(TaxFormBase):
    """
    IRS Form 1099-NEC — Nonemployee Compensation.

    Key cross-field invariants:
    - federal_income_tax_withheld ≤ nonemployee_compensation
    - State code required whenever any state-level field is populated.
    """

    document_type: Literal["1099-NEC"] = "1099-NEC"
    tax_year: TaxYear

    # ── Payer (the business issuing the form) ─────────────────────────────────

    payer_tin: EIN = Field(
        description="Payer's taxpayer identification number (EIN)"
    )
    payer_name: str = Field(min_length=1, max_length=75)
    payer_address: Optional[str] = Field(None, max_length=200)

    # ── Recipient (the non-employee) ──────────────────────────────────────────

    recipient_tin: TIN = Field(
        description="Recipient's TIN — SSN (XXX-XX-XXXX) for individuals, "
                    "EIN (XX-XXXXXXX) for businesses"
    )
    recipient_name: str = Field(min_length=1, max_length=75)
    recipient_address: Optional[str] = Field(None, max_length=200)
    account_number: Optional[str] = Field(
        None, max_length=20,
        description="Account number assigned by the payer (optional)",
    )

    # ── Box 1: required amount ────────────────────────────────────────────────

    nonemployee_compensation: NonNegativeDollar = Field(
        description="Box 1 — Nonemployee compensation (≥ $600 triggers filing)"
    )

    # ── Box 2: checkbox ───────────────────────────────────────────────────────

    payer_made_direct_sales: bool = Field(
        False,
        description=(
            "Box 2 — Payer made direct sales totaling $5,000+ of consumer "
            "products for resale"
        ),
    )

    # ── Box 4: backup withholding ─────────────────────────────────────────────

    federal_income_tax_withheld: Optional[NonNegativeDollar] = Field(
        None,
        description="Box 4 — Federal income tax withheld (24% backup withholding)",
    )

    # ── Boxes 5–7: state withholding (1099-NEC has a single state row) ───────

    state_tax_withheld: Optional[NonNegativeDollar] = Field(
        None, description="Box 5 — State tax withheld"
    )
    state: Optional[StateCode] = Field(
        None, description="Box 6 — State abbreviation"
    )
    payer_state_no: Optional[str] = Field(
        None, max_length=20,
        description="Box 6 — Payer's state identification number",
    )
    state_income: Optional[NonNegativeDollar] = Field(
        None, description="Box 7 — State income"
    )

    # ── Cross-field validation ────────────────────────────────────────────────

    @model_validator(mode="after")
    def _cross_field_invariants(self) -> "Form1099NEC":
        # Backup withholding cannot exceed the total payment
        if (
            self.federal_income_tax_withheld is not None
            and self.federal_income_tax_withheld > self.nonemployee_compensation
        ):
            raise ValueError(
                f"federal_income_tax_withheld ({self.federal_income_tax_withheld}) "
                f"cannot exceed nonemployee_compensation "
                f"({self.nonemployee_compensation})"
            )

        # State code is required whenever any other state field is set
        has_state_data = any(
            v is not None
            for v in (self.state_tax_withheld, self.payer_state_no, self.state_income)
        )
        if has_state_data and self.state is None:
            raise ValueError(
                "state (Box 6) is required when state_tax_withheld, "
                "payer_state_no, or state_income is provided"
            )

        return self
