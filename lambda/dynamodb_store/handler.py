"""
DynamoDB storage Lambda for the Step Functions pipeline.

Receives:  {user_id, document, source, bucket, key, field_metadata?}
Returns:   {doc_id, stored}

Wraps TaxDocumentRepository.put_document().
field_metadata (optional) — raw Textract fields dict with per-alias confidence
scores; forwarded to the repository so the review UI can display confidence badges.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from tax_storage import TaxDocumentRepository

log = logging.getLogger(__name__)
log.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

# Module-level repo — one DynamoDB Table object per warm container.
_repo = TaxDocumentRepository()


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    user_id        = event["user_id"]
    document       = event["document"]
    source         = event.get("source", "textract")
    field_metadata = event.get("field_metadata")  # may be None for old pipeline executions

    log.info(
        "Storing tax document",
        extra={
            "user_id":       user_id,
            "document_type": document.get("document_type"),
            "tax_year":      document.get("tax_year"),
            "source":        source,
        },
    )

    doc_id = _repo.put_document(
        user_id=user_id,
        document=document,
        source=source,
        field_metadata=field_metadata,
    )

    log.info("Document stored", extra={"doc_id": doc_id})
    return {"doc_id": doc_id, "stored": True}
