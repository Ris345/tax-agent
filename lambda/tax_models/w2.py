"""
Pydantic v2 model for IRS Form W-2 (Wage and Tax Statement).

Reference: https://www.irs.gov/forms-pubs/about-form-w-2
"""

from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import Field, model_validator

from .common import (
    EIN,
    SSN,
    NonNegativeDollar,
    StateCode,
    TaxFormBase,
    TaxYear,
)

# ─── Box 12 codes (IRS-defined deferred compensation identifiers) ─────────────


class W2Box12Code(str, Enum):
    """
    All valid IRS Box 12 codes as of tax year 2024.
    Note: codes I, O, U, X are not defined by the IRS and are deliberately absent.
    """
    A  = "A"   # Uncollected Social Security or RRTA tax on tips
    B  = "B"   # Uncollected Medicare tax on tips
    C  = "C"   # Taxable cost of group-term life insurance over $50,000
    D  = "D"   # Elective deferrals to a section 401(k) plan
    E  = "E"   # Elective deferrals to a section 403(b) plan
    F  = "F"   # Elective deferrals to a section 408(k)(6) SEP
    G  = "G"   # Elective deferrals and employer contributions to a section 457(b) plan
    H  = "H"   # Elective deferrals to a section 501(c)(18)(D) tax-exempt plan
    J  = "J"   # Nontaxable sick pay
    K  = "K"   # 20% excise tax on excess golden parachute payments
    L  = "L"   # Substantiated employee business expense reimbursements
    M  = "M"   # Uncollected SS or RRTA tax on cost of group-term life > $50K (former)
    N  = "N"   # Uncollected Medicare tax on cost of group-term life > $50K (former)
    P  = "P"   # Excludable moving expense reimbursements paid directly to employee
    Q  = "Q"   # Nontaxable combat pay
    R  = "R"   # Employer contributions to an Archer MSA
    S  = "S"   # Employee salary reduction contributions under a section 408(p) SIMPLE
    T  = "T"   # Adoption benefits
    V  = "V"   # Income from the exercise of nonstatutory stock options
    W  = "W"   # Employer contributions (including employee contributions through a
               #   cafeteria plan) to an employee's HSA
    Y  = "Y"   # Deferrals under a section 409A nonqualified deferred compensation plan
    Z  = "Z"   # Income under a nonqualified deferred compensation plan that fails
               #   section 409A
    AA = "AA"  # Designated Roth contributions to a section 401(k) plan
    BB = "BB"  # Designated Roth contributions to a section 403(b) plan
    DD = "DD"  # Cost of employer-sponsored health coverage
    EE = "EE"  # Designated Roth contributions under a section 457(b) plan
    FF = "FF"  # Permitted benefits under a qualified small employer HRA (QSEHRA)
    GG = "GG"  # Income from qualified equity grants under section 83(i)
    HH = "HH"  # Aggregate deferrals under section 83(i) elections as of close of year


# ─── Nested sub-models ────────────────────────────────────────────────────────


class W2Box12Entry(TaxFormBase):
    """A single Box 12 entry: one IRS letter code plus its dollar amount."""

    code: W2Box12Code
    amount: NonNegativeDollar


class W2StateEntry(TaxFormBase):
    """
    Boxes 15–17: one state withholding row.
    A W-2 may report wages for up to two states on the same form.
    """

    state: StateCode = Field(description="Box 15 — State abbreviation")
    employer_state_id: str = Field(
        min_length=1,
        max_length=20,
        description="Box 15 — Employer's state ID number",
    )
    state_wages_tips: NonNegativeDollar = Field(
        description="Box 16 — State wages, tips, etc."
    )
    state_income_tax: NonNegativeDollar = Field(
        description="Box 17 — State income tax withheld"
    )

    @model_validator(mode="after")
    def _state_tax_le_wages(self) -> "W2StateEntry":
        if self.state_income_tax > self.state_wages_tips:
            raise ValueError(
                f"state_income_tax ({self.state_income_tax}) cannot exceed "
                f"state_wages_tips ({self.state_wages_tips})"
            )
        return self


# ─── W-2 model ────────────────────────────────────────────────────────────────


class W2(TaxFormBase):
    """
    IRS Form W-2 — Wage and Tax Statement.

    Covers all boxes 1–17 plus employee/employer identifiers.
    The `document_type` Literal is the discriminator key used by
    TaxDocumentUnion.

    Cross-field invariants validated at model level:
    - federal_income_tax_withheld ≤ wages_tips_other_compensation
    - social_security_tax_withheld ≤ social_security_wages
    - medicare_tax_withheld ≤ medicare_wages_tips
    - Box 12 codes must be unique within a single W-2
    """

    document_type: Literal["W2"] = "W2"
    tax_year: TaxYear

    # ── Employer / Employee identifiers ───────────────────────────────────────

    employer_ein: EIN = Field(
        description="Employer identification number — Box b"
    )
    employer_name: str = Field(
        min_length=1, max_length=75,
        description="Employer's name — Box c",
    )
    employer_address: Optional[str] = Field(
        None, max_length=200,
        description="Employer's address, city, state, ZIP — Box c",
    )

    employee_ssn: SSN = Field(
        description="Employee's social security number — Box a"
    )
    employee_first_name: str = Field(min_length=1, max_length=50)
    employee_last_name: str = Field(min_length=1, max_length=50)
    employee_address: Optional[str] = Field(None, max_length=200)

    # ── Boxes 1–6: required federal wages and tax ────────────────────────────

    wages_tips_other_compensation: NonNegativeDollar = Field(
        description="Box 1 — Wages, tips, other compensation"
    )
    federal_income_tax_withheld: NonNegativeDollar = Field(
        description="Box 2 — Federal income tax withheld"
    )
    social_security_wages: NonNegativeDollar = Field(
        description="Box 3 — Social security wages"
    )
    social_security_tax_withheld: NonNegativeDollar = Field(
        description="Box 4 — Social security tax withheld"
    )
    medicare_wages_tips: NonNegativeDollar = Field(
        description="Box 5 — Medicare wages and tips"
    )
    medicare_tax_withheld: NonNegativeDollar = Field(
        description="Box 6 — Medicare tax withheld"
    )

    # ── Boxes 7–11: optional federal amounts ─────────────────────────────────

    social_security_tips: Optional[NonNegativeDollar] = Field(
        None, description="Box 7 — Social security tips"
    )
    allocated_tips: Optional[NonNegativeDollar] = Field(
        None, description="Box 8 — Allocated tips"
    )
    verification_code: Optional[str] = Field(
        None, max_length=16,
        description="Box 9 — Verification code",
    )
    dependent_care_benefits: Optional[NonNegativeDollar] = Field(
        None, description="Box 10 — Dependent care benefits"
    )
    nonqualified_plans: Optional[NonNegativeDollar] = Field(
        None, description="Box 11 — Nonqualified plans"
    )

    # ── Box 12: deferred compensation (up to 4 entries, codes must be unique) ─

    box_12: list[W2Box12Entry] = Field(
        default_factory=list,
        max_length=4,
        description="Box 12a–d: deferred compensation code + amount pairs",
    )

    # ── Box 13: checkboxes ────────────────────────────────────────────────────

    statutory_employee: bool = Field(
        False, description="Box 13 — Statutory employee"
    )
    retirement_plan: bool = Field(
        False, description="Box 13 — Retirement plan participant"
    )
    third_party_sick_pay: bool = Field(
        False, description="Box 13 — Third-party sick pay"
    )

    # ── Box 14: other ─────────────────────────────────────────────────────────

    other: Optional[str] = Field(
        None, max_length=500,
        description="Box 14 — Other (employer-defined codes and amounts)",
    )

    # ── Boxes 15–17: state withholding (up to 2 rows) ────────────────────────

    state_entries: list[W2StateEntry] = Field(
        default_factory=list,
        max_length=2,
        description="Boxes 15–17: state, employer state ID, state wages, state tax",
    )

    # ── Cross-field validation ────────────────────────────────────────────────

    @model_validator(mode="after")
    def _cross_field_invariants(self) -> "W2":
        # Federal withholding cannot exceed total wages
        if self.federal_income_tax_withheld > self.wages_tips_other_compensation:
            raise ValueError(
                f"federal_income_tax_withheld ({self.federal_income_tax_withheld}) "
                f"cannot exceed wages_tips_other_compensation "
                f"({self.wages_tips_other_compensation})"
            )

        # SS tax must not exceed SS wages (6.2% rate; we only check direction)
        if self.social_security_tax_withheld > self.social_security_wages:
            raise ValueError(
                f"social_security_tax_withheld ({self.social_security_tax_withheld}) "
                f"cannot exceed social_security_wages ({self.social_security_wages})"
            )

        # Medicare tax must not exceed Medicare wages (1.45% + 0.9% additional)
        if self.medicare_tax_withheld > self.medicare_wages_tips:
            raise ValueError(
                f"medicare_tax_withheld ({self.medicare_tax_withheld}) "
                f"cannot exceed medicare_wages_tips ({self.medicare_wages_tips})"
            )

        # Box 12 codes must be unique on a single W-2
        codes = [entry.code for entry in self.box_12]
        if len(codes) != len(set(codes)):
            duplicates = [c for c in set(codes) if codes.count(c) > 1]
            raise ValueError(
                f"Duplicate Box 12 codes are not allowed on a single W-2: "
                f"{duplicates}"
            )

        return self
