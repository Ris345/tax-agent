"""
Discriminated union of all supported IRS tax document types, plus factory
helpers for constructing the correct model from raw data or Textract payloads.
"""

from __future__ import annotations

from typing import Annotated, Any, Union

from pydantic import Field, TypeAdapter, ValidationError

from .form_1099_b import Form1099B
from .form_1099_int import Form1099INT
from .form_1099_nec import Form1099NEC
from .w2 import W2

# ─── Discriminated union ──────────────────────────────────────────────────────
#
# Pydantic v2 uses the value of the `document_type` Literal field on each
# model to select the correct class at parse time — no isinstance checks needed.
#
# Valid discriminator values:
#   "W2"        → W2
#   "1099-NEC"  → Form1099NEC
#   "1099-B"    → Form1099B
#   "1099-INT"  → Form1099INT

TaxDocumentUnion = Annotated[
    Union[W2, Form1099NEC, Form1099B, Form1099INT],
    Field(discriminator="document_type"),
]

# TypeAdapter lets us validate TaxDocumentUnion without wrapping it in a model.
# Reuse across calls — TypeAdapter construction is not free.
_adapter: TypeAdapter[TaxDocumentUnion] = TypeAdapter(TaxDocumentUnion)  # type: ignore[type-arg]

# ─── Lookup table ─────────────────────────────────────────────────────────────

DOCUMENT_TYPE_MAP: dict[str, type[W2 | Form1099NEC | Form1099B | Form1099INT]] = {
    "W2":       W2,
    "1099-NEC": Form1099NEC,
    "1099-B":   Form1099B,
    "1099-INT": Form1099INT,
}

# ─── Public factory functions ─────────────────────────────────────────────────


def parse_tax_document(
    data: dict[str, Any],
) -> W2 | Form1099NEC | Form1099B | Form1099INT:
    """
    Validate a raw dict against the discriminated union.

    The dict must contain a ``document_type`` key whose value is one of:
    ``"W2"``, ``"1099-NEC"``, ``"1099-B"``, ``"1099-INT"``.

    Raises:
        ValueError: if ``document_type`` is missing or not recognised.
        pydantic.ValidationError: if the data is structurally invalid for its type.

    Example::

        doc = parse_tax_document({
            "document_type": "W2",
            "tax_year": 2024,
            "employer_ein": "12-3456789",
            ...
        })
        assert isinstance(doc, W2)
    """
    doc_type = data.get("document_type")
    if doc_type not in DOCUMENT_TYPE_MAP:
        raise ValueError(
            f"Unknown or missing document_type {doc_type!r}. "
            f"Supported values: {sorted(DOCUMENT_TYPE_MAP)}"
        )
    return _adapter.validate_python(data)


def from_textract_payload(
    payload: dict[str, Any],
) -> W2 | Form1099NEC | Form1099B | Form1099INT:
    """
    Build a tax document model from a ``W2TextractProcessor`` payload dict,
    optionally post-processed by the Claude fallback.

    The payload schema is::

        {
          "document_type": "W2",
          "tax_year": 2024,
          "document_bucket": "...",
          "document_key": "...",
          "fields": {
            "employer_ein":     {"value": "12-3456789", "confidence": 97.5, ...},
            "wages_tips_other_compensation": {"value": "52350.00", ...},
            ...
          },
          "summary": { ... }
        }

    Only the ``"value"`` from each field entry is forwarded to the Pydantic model;
    ``"confidence"``, ``"flagged_for_review"``, and ``"source"`` are metadata and
    are intentionally discarded.

    ``"tax_year"`` may be at the top level or inside ``"fields"``.

    Raises:
        ValueError: if ``document_type`` is unknown.
        pydantic.ValidationError: if the extracted values fail schema validation.
    """
    raw_fields: dict[str, Any] = payload.get("fields", {})

    # Flatten each {"value": ..., "confidence": ...} entry to just its value.
    flat: dict[str, Any] = {
        alias: (entry["value"] if isinstance(entry, dict) else entry)
        for alias, entry in raw_fields.items()
    }

    # Promote top-level metadata that the models need.
    flat["document_type"] = payload.get("document_type")
    if "tax_year" in payload and "tax_year" not in flat:
        flat["tax_year"] = payload["tax_year"]

    # Drop None values so Pydantic uses model defaults for optional fields.
    flat = {k: v for k, v in flat.items() if v is not None}

    return parse_tax_document(flat)


def document_type_for(
    document: W2 | Form1099NEC | Form1099B | Form1099INT,
) -> str:
    """Return the ``document_type`` string of any tax document instance."""
    return document.document_type  # type: ignore[return-value]
