"""
PDF generation Lambda for the Step Functions pipeline.

Receives:  {document, doc_id, user_id, bucket}
Returns:   {pdf_key, presigned_url, expires_in}

Uses fpdf2 (pure-Python, MIT) to render a formatted tax document summary.
The PDF is uploaded to S3 under pdfs/{user_id}/{doc_id}.pdf and a
presigned GET URL is returned (expiry: PRESIGNED_URL_EXPIRY_SECONDS env var).
"""

from __future__ import annotations

import io
import logging
import os
from datetime import date
from typing import Any

import boto3
from fpdf import FPDF

log = logging.getLogger(__name__)
log.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

_PRESIGNED_EXPIRY = int(os.environ.get("PRESIGNED_URL_EXPIRY_SECONDS", "3600"))
_s3 = boto3.client("s3")


# ── PDF rendering ──────────────────────────────────────────────────────────────

_SECTION_COLOUR = (30, 58, 138)    # indigo-900
_LABEL_COLOUR   = (75, 85, 99)     # gray-600
_VALUE_COLOUR   = (17, 24, 39)     # gray-900
_WARN_COLOUR    = (220, 38, 38)    # red-600

# Human-readable labels for common fields
_FIELD_LABELS: dict[str, str] = {
    # W-2
    "employer_ein":                  "Employer EIN",
    "employer_name":                 "Employer Name",
    "employer_address":              "Employer Address",
    "employee_ssn":                  "Employee SSN",
    "employee_first_name":           "First Name",
    "employee_last_name":            "Last Name",
    "employee_address":              "Employee Address",
    "wages_tips_other_compensation": "Box 1 — Wages, Tips",
    "federal_income_tax_withheld":   "Box 2 — Federal Tax Withheld",
    "social_security_wages":         "Box 3 — SS Wages",
    "social_security_tax_withheld":  "Box 4 — SS Tax Withheld",
    "medicare_wages_tips":           "Box 5 — Medicare Wages",
    "medicare_tax_withheld":         "Box 6 — Medicare Tax Withheld",
    # 1099-NEC
    "payer_tin":                     "Payer TIN",
    "payer_name":                    "Payer Name",
    "recipient_tin":                 "Recipient TIN",
    "recipient_name":                "Recipient Name",
    "nonemployee_compensation":      "Box 1 — Nonemployee Compensation",
    # 1099-INT
    "interest_income":               "Box 1 — Interest Income",
    "payer_tin":                     "Payer TIN",
    # 1099-B
    "payer_name":                    "Broker Name",
    "recipient_tin":                 "Recipient TIN",
}

_SECTION_ORDER: dict[str, list[str]] = {
    "W2": [
        ["employer_ein", "employer_name", "employer_address"],
        ["employee_ssn", "employee_first_name", "employee_last_name", "employee_address"],
        [
            "wages_tips_other_compensation", "federal_income_tax_withheld",
            "social_security_wages", "social_security_tax_withheld",
            "medicare_wages_tips", "medicare_tax_withheld",
        ],
    ],
    "1099-NEC": [
        ["payer_tin", "payer_name", "payer_address"],
        ["recipient_tin", "recipient_name", "recipient_address"],
        ["nonemployee_compensation", "federal_income_tax_withheld"],
    ],
    "1099-INT": [
        ["payer_tin", "payer_name", "payer_address"],
        ["recipient_tin", "recipient_name", "recipient_address"],
        ["interest_income", "federal_income_tax_withheld", "tax_exempt_interest"],
    ],
    "1099-B": [
        ["payer_name", "payer_address"],
        ["recipient_tin", "recipient_name", "recipient_address"],
    ],
}

_SECTION_TITLES: dict[str, list[str]] = {
    "W2":      ["Employer Information", "Employee Information", "Income & Withholding"],
    "1099-NEC": ["Payer Information",   "Recipient Information", "Compensation"],
    "1099-INT": ["Payer Information",   "Recipient Information", "Interest Income"],
    "1099-B":   ["Broker Information",  "Recipient Information"],
}


class TaxPDF(FPDF):
    def header(self) -> None:
        self.set_font("Helvetica", "B", 14)
        self.set_text_color(*_SECTION_COLOUR)
        self.cell(0, 10, "Tax Document Summary", ln=True, align="C")
        self.set_draw_color(*_SECTION_COLOUR)
        self.set_line_width(0.5)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(4)

    def footer(self) -> None:
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 10, f"Page {self.page_no()} — Generated {date.today()}", align="C")


def _render_section(pdf: TaxPDF, title: str, fields: list[str], document: dict) -> None:
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(*_SECTION_COLOUR)
    pdf.cell(0, 7, title, ln=True)
    pdf.set_line_width(0.3)
    pdf.set_draw_color(*_SECTION_COLOUR)
    pdf.line(pdf.get_x(), pdf.get_y(), 200, pdf.get_y())
    pdf.ln(2)

    for field in fields:
        raw = document.get(field)
        if raw is None:
            continue
        label = _FIELD_LABELS.get(field, field.replace("_", " ").title())
        value = str(raw)

        # Mask sensitive values: show only last 4 chars of SSN / EIN
        if field in ("employee_ssn", "recipient_tin", "payer_tin", "employer_ein"):
            value = "***-**-" + value[-4:] if len(value) >= 4 else "****"

        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(*_LABEL_COLOUR)
        pdf.cell(65, 6, label + ":", ln=False)
        pdf.set_text_color(*_VALUE_COLOUR)
        pdf.multi_cell(0, 6, value)

    pdf.ln(3)


def _build_pdf(document: dict, doc_id: str) -> bytes:
    doc_type = document.get("document_type", "TAX")
    tax_year = document.get("tax_year", "")

    pdf = TaxPDF()
    pdf.add_page()

    # Sub-title
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(*_LABEL_COLOUR)
    pdf.cell(0, 6, f"Form {doc_type}  ·  Tax Year {tax_year}  ·  ID: {doc_id}", ln=True)
    pdf.ln(4)

    sections = _SECTION_ORDER.get(doc_type, [])
    titles   = _SECTION_TITLES.get(doc_type, [f"Section {i+1}" for i in range(len(sections))])

    for title, fields in zip(titles, sections):
        _render_section(pdf, title, fields, document)

    # For 1099-B: list first 10 transactions
    if doc_type == "1099-B":
        transactions: list[dict] = document.get("transactions", [])
        if transactions:
            pdf.set_font("Helvetica", "B", 10)
            pdf.set_text_color(*_SECTION_COLOUR)
            pdf.cell(0, 7, "Transactions (first 10)", ln=True)
            pdf.set_font("Helvetica", "", 8)
            pdf.set_text_color(*_VALUE_COLOUR)
            for txn in transactions[:10]:
                line = (
                    f"{txn.get('description', '')}  |  "
                    f"Sold: {txn.get('date_sold', '')}  |  "
                    f"Proceeds: {txn.get('proceeds', '')}  |  "
                    f"{txn.get('gain_loss_type', '')}"
                )
                pdf.multi_cell(0, 5, line)
            pdf.ln(2)

    return pdf.output()


# ── Lambda entry point ─────────────────────────────────────────────────────────

def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    document = event["document"]
    doc_id   = event["doc_id"]
    user_id  = event["user_id"]
    bucket   = event["bucket"]

    pdf_key = f"pdfs/{user_id}/{doc_id}.pdf"

    log.info("Generating PDF", extra={"doc_id": doc_id, "key": pdf_key})

    pdf_bytes = _build_pdf(document, doc_id)

    _s3.put_object(
        Bucket=bucket,
        Key=pdf_key,
        Body=pdf_bytes,
        ContentType="application/pdf",
        ServerSideEncryption="aws:kms",
    )

    presigned_url = _s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": pdf_key},
        ExpiresIn=_PRESIGNED_EXPIRY,
    )

    log.info("PDF uploaded", extra={"pdf_key": pdf_key})
    return {
        "pdf_key":       pdf_key,
        "presigned_url": presigned_url,
        "expires_in":    _PRESIGNED_EXPIRY,
    }
