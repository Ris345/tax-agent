"""
Pydantic v2 model for IRS Form 1099-B
(Proceeds from Broker and Barter Exchange Transactions).

Reference: https://www.irs.gov/forms-pubs/about-form-1099-b

Design notes
------------
- One Form1099B may contain many transactions. Each is a BrokerageTransaction.
- date_acquired=None represents the IRS "VARIOUS" designation (positions
  acquired on multiple dates), not a missing value.
- cost_or_basis=None means the basis was not reported to the IRS (common for
  older/uncovered securities).
- net_gain_loss is a computed property, not a stored field, to prevent
  inconsistency between the raw boxes and the derived figure.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Literal, Optional

from pydantic import Field, model_validator

from .common import (
    TIN,
    NonNegativeDollar,
    SignedDollar,
    StateCode,
    TaxFormBase,
    TaxYear,
)


# ─── Enumerations ─────────────────────────────────────────────────────────────


class GainLossType(str, Enum):
    """Box 2: how the gain or loss is classified for tax purposes."""

    SHORT_TERM = "short_term"   # held ≤ 1 year → ordinary rates
    LONG_TERM  = "long_term"    # held > 1 year → preferential rates
    ORDINARY   = "ordinary"     # marked-to-market (certain regulated futures, etc.)


class CoverageStatus(str, Enum):
    """
    Box 5: whether the broker is required to report the cost basis to the IRS.
    Covered = basis reported; Uncovered = basis NOT reported (older positions).
    """

    COVERED   = "covered"
    UNCOVERED = "uncovered"


# ─── Transaction sub-model ────────────────────────────────────────────────────


class BrokerageTransaction(TaxFormBase):
    """
    A single reportable sale on Form 1099-B.

    One consolidated 1099-B statement from a brokerage contains one
    BrokerageTransaction per trade lot sold during the year.
    """

    # Box 1a — description of property (e.g. "100 SHS AAPL")
    description: str = Field(
        min_length=1, max_length=200,
        description="Box 1a — Description of property",
    )

    # Box 1b — date acquired; None = "VARIOUS" (multiple lots)
    date_acquired: Optional[date] = Field(
        None,
        description="Box 1b — Date acquired (None indicates IRS 'VARIOUS')",
    )

    # Box 1c — date of sale or exchange
    date_sold: date = Field(description="Box 1c — Date sold or disposed of")

    # Box 1d — gross or net proceeds
    proceeds: NonNegativeDollar = Field(
        description="Box 1d — Proceeds (sales price)"
    )

    # Box 1e — cost or other basis; None = not reported to IRS (uncovered)
    cost_or_basis: Optional[NonNegativeDollar] = Field(
        None,
        description="Box 1e — Cost or other basis (None = not reported to IRS)",
    )

    # Box 1f — accrued market discount (increases ordinary income)
    accrued_market_discount: Optional[NonNegativeDollar] = Field(
        None, description="Box 1f — Accrued market discount"
    )

    # Box 1g — wash sale loss disallowed (reduces deductible loss)
    wash_sale_loss_disallowed: Optional[NonNegativeDollar] = Field(
        None, description="Box 1g — Wash sale loss disallowed"
    )

    # Box 2 — short-term / long-term / ordinary classification
    gain_loss_type: GainLossType = Field(
        description="Box 2 — Short-term, long-term, or ordinary gain/loss"
    )

    # Box 3 — proceeds from collectibles or Qualified Opportunity Fund
    proceeds_from_collectibles_or_qof: bool = Field(
        False,
        description="Box 3 — Proceeds from collectibles or QOF investment",
    )

    # Box 4 — federal backup withholding
    federal_income_tax_withheld: Optional[NonNegativeDollar] = Field(
        None, description="Box 4 — Federal income tax withheld"
    )

    # Box 5 — covered / noncovered
    coverage_status: CoverageStatus = Field(
        CoverageStatus.COVERED,
        description="Box 5 — Covered (basis reported) or noncovered",
    )

    # Box 6 — gross or net proceeds reported
    gross_proceeds_reported: bool = Field(
        True,
        description="Box 6 — True = gross proceeds reported; False = net proceeds",
    )

    # Box 7 — loss not allowed based on amount in Box 1d
    loss_not_allowed: bool = Field(False, description="Box 7 — Loss not allowed")

    # ── Derived property ───────────────────────────────────────────────────

    @property
    def net_gain_loss(self) -> Optional[Decimal]:
        """
        Net realised gain (positive) or loss (negative).

        Returns None when cost_or_basis is not reported (uncovered security).
        Wash-sale adjustments are reflected: a disallowed loss reduces the
        apparent loss by the disallowed amount.
        """
        if self.cost_or_basis is None:
            return None
        raw = self.proceeds - self.cost_or_basis
        if self.wash_sale_loss_disallowed:
            raw += self.wash_sale_loss_disallowed
        return raw

    # ── Validators ────────────────────────────────────────────────────────

    @model_validator(mode="after")
    def _date_order(self) -> "BrokerageTransaction":
        if self.date_acquired and self.date_acquired > self.date_sold:
            raise ValueError(
                f"date_acquired ({self.date_acquired}) cannot be after "
                f"date_sold ({self.date_sold})"
            )
        return self

    @model_validator(mode="after")
    def _uncovered_basis_consistency(self) -> "BrokerageTransaction":
        # Uncovered securities typically have no basis reported
        if (
            self.coverage_status == CoverageStatus.UNCOVERED
            and self.cost_or_basis is not None
            # Allow it: some brokers report basis even for uncovered positions
        ):
            pass  # not an error — just unusual; no constraint enforced
        return self


# ─── Form 1099-B model ────────────────────────────────────────────────────────


class Form1099B(TaxFormBase):
    """
    IRS Form 1099-B — Proceeds from Broker and Barter Exchange Transactions.

    A single Form1099B aggregates all reportable transactions from one brokerage
    account in a given tax year.  The list must contain at least one transaction.
    """

    document_type: Literal["1099-B"] = "1099-B"
    tax_year: TaxYear

    # ── Payer (the broker) ────────────────────────────────────────────────────

    payer_name: str = Field(min_length=1, max_length=75)
    payer_address: Optional[str] = Field(None, max_length=200)

    # ── Recipient ─────────────────────────────────────────────────────────────

    recipient_tin: TIN = Field(
        description="Recipient's TIN — SSN for individuals, EIN for entities"
    )
    recipient_name: str = Field(min_length=1, max_length=75)
    recipient_address: Optional[str] = Field(None, max_length=200)
    account_number: Optional[str] = Field(None, max_length=20)

    # ── Transactions (at least one required) ─────────────────────────────────

    transactions: list[BrokerageTransaction] = Field(
        min_length=1,
        description="One BrokerageTransaction per reportable sale during the year",
    )

    # ── Aggregate totals (present on consolidated brokerage statements) ───────
    # These are summary rows derived from the transactions above.
    # Stored separately so downstream code can verify them against transaction sums.

    aggregate_proceeds: Optional[NonNegativeDollar] = Field(
        None,
        description="Sum of all transaction proceeds (for reconciliation)",
    )
    aggregate_cost_or_basis: Optional[NonNegativeDollar] = Field(
        None,
        description="Sum of all reported cost or basis (for reconciliation)",
    )
    aggregate_wash_sale_loss: Optional[NonNegativeDollar] = Field(
        None,
        description="Sum of all wash sale losses disallowed (for reconciliation)",
    )
    aggregate_net_gain_loss: Optional[SignedDollar] = Field(
        None,
        description="Net realised gain/loss across all transactions",
    )

    # ── Boxes 15–18: state withholding ───────────────────────────────────────

    state_tax_withheld: Optional[NonNegativeDollar] = Field(
        None, description="Box 16 — State tax withheld"
    )
    state: Optional[StateCode] = Field(
        None, description="Box 17 — State"
    )
    state_id_no: Optional[str] = Field(
        None, max_length=20,
        description="Box 17 — Payer's state identification number",
    )
    state_income: Optional[NonNegativeDollar] = Field(
        None, description="Box 18 — State income"
    )

    # ── Validators ────────────────────────────────────────────────────────────

    @model_validator(mode="after")
    def _state_code_required_with_state_data(self) -> "Form1099B":
        if any(
            v is not None
            for v in (self.state_tax_withheld, self.state_id_no, self.state_income)
        ) and self.state is None:
            raise ValueError(
                "state (Box 17) is required when state_tax_withheld, "
                "state_id_no, or state_income is provided"
            )
        return self

    @model_validator(mode="after")
    def _all_transactions_same_tax_year(self) -> "Form1099B":
        """All sale dates on a 1099-B must fall within the declared tax year."""
        for txn in self.transactions:
            if txn.date_sold.year != self.tax_year:
                raise ValueError(
                    f"Transaction date_sold {txn.date_sold} does not fall in "
                    f"tax_year {self.tax_year} (description: {txn.description!r})"
                )
        return self
