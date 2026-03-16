"""
Pydantic validation Lambda for the Step Functions pipeline.

Receives:  {textract_payload, document_type}
Returns:   {is_valid, document, warnings}

Raises a named exception "PydanticValidationError" on schema violations so
that the Step Functions Catch can route to ValidationFailed by error name.
"""

from __future__ import annotations

import json
import logging
import os
from decimal import Decimal
from typing import Any

from pydantic import ValidationError

from tax_models import from_textract_payload

log = logging.getLogger(__name__)
log.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


class PydanticValidationError(Exception):
    """Raised on Pydantic schema violations — matched by Step Functions Catch."""


def _decimal_default(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return str(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serialisable")


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    textract_payload = event["textract_payload"]
    document_type    = event.get("document_type", textract_payload.get("document_type"))

    log.info("Validating document", extra={"document_type": document_type})

    try:
        doc = from_textract_payload(textract_payload)
    except ValidationError as exc:
        # Serialise Pydantic errors for downstream logging / alerting
        errors = exc.errors(include_url=False)
        log.warning("Pydantic validation failed", extra={"errors": errors})
        raise PydanticValidationError(json.dumps(errors)) from exc
    except (ValueError, KeyError) as exc:
        log.warning("Payload pre-processing failed", extra={"error": str(exc)})
        raise

    # Collect non-fatal warnings: fields still flagged after Claude fallback
    still_flagged = (
        textract_payload.get("summary", {})
        .get("still_flagged_after_fallback", [])
    )
    warnings = [
        f"Low-confidence field after all extraction attempts: {f}"
        for f in still_flagged
    ]

    # Serialise via model_dump + json round-trip to handle Decimal / date / Enum
    doc_dict = json.loads(
        json.dumps(doc.model_dump(mode="python"), default=_decimal_default)
    )

    log.info("Validation succeeded", extra={"warnings": len(warnings)})
    return {
        "is_valid": True,
        "document": doc_dict,
        "warnings": warnings,
    }
