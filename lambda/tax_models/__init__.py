"""
tax_models — Pydantic v2 models for IRS tax forms W-2, 1099-NEC, 1099-B, 1099-INT.

Quick start
-----------
Parse a known document type directly::

    from tax_models import W2
    w2 = W2(
        document_type="W2",
        tax_year=2024,
        employer_ein="12-3456789",
        employer_name="Acme Corp",
        employee_ssn="123-45-6789",
        employee_first_name="Jane",
        employee_last_name="Doe",
        wages_tips_other_compensation="52350.00",
        federal_income_tax_withheld="7500.00",
        social_security_wages="52350.00",
        social_security_tax_withheld="3245.70",
        medicare_wages_tips="52350.00",
        medicare_tax_withheld="759.08",
    )

Parse an unknown type via the discriminated union::

    from tax_models import parse_tax_document
    doc = parse_tax_document({"document_type": "1099-NEC", ...})

Build from a Textract pipeline payload::

    from tax_models import from_textract_payload
    doc = from_textract_payload(textract_payload_dict)

Use the TypeAdapter directly for bulk validation::

    from pydantic import TypeAdapter
    from tax_models import TaxDocumentUnion
    adapter = TypeAdapter(TaxDocumentUnion)
    docs = [adapter.validate_python(d) for d in raw_list]

Annotated types for use in other models::

    from tax_models.common import EIN, SSN, TIN, NonNegativeDollar, StateCode
"""

from .common import (
    EIN,
    SSN,
    TIN,
    US_STATE_CODES,
    NonNegativeDollar,
    SignedDollar,
    StateCode,
    TaxFormBase,
    TaxYear,
)
from .form_1099_b import BrokerageTransaction, CoverageStatus, Form1099B, GainLossType
from .form_1099_int import Form1099INT
from .form_1099_nec import Form1099NEC
from .union import (
    DOCUMENT_TYPE_MAP,
    TaxDocumentUnion,
    document_type_for,
    from_textract_payload,
    parse_tax_document,
)
from .w2 import W2, W2Box12Code, W2Box12Entry, W2StateEntry

__all__ = [
    # ── Shared / common ─────────────────────────────────────────
    "TaxFormBase",
    "EIN",
    "SSN",
    "TIN",
    "NonNegativeDollar",
    "SignedDollar",
    "StateCode",
    "TaxYear",
    "US_STATE_CODES",
    # ── W-2 ─────────────────────────────────────────────────────
    "W2",
    "W2Box12Code",
    "W2Box12Entry",
    "W2StateEntry",
    # ── 1099-NEC ────────────────────────────────────────────────
    "Form1099NEC",
    # ── 1099-B ──────────────────────────────────────────────────
    "Form1099B",
    "BrokerageTransaction",
    "GainLossType",
    "CoverageStatus",
    # ── 1099-INT ────────────────────────────────────────────────
    "Form1099INT",
    # ── Union + factories ────────────────────────────────────────
    "TaxDocumentUnion",
    "DOCUMENT_TYPE_MAP",
    "parse_tax_document",
    "from_textract_payload",
    "document_type_for",
]
