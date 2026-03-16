"""
Document API Lambda — multi-action CRUD handler for the review UI.

Actions
-------
list
    List all tax documents for a user (no PII decryption).
    Input:  {action, user_id, document_type?, tax_year?}
    Output: {documents: [...]}

get
    Fetch a single document with PII decrypted.
    Input:  {action, user_id, doc_id}
    Output: {document: {...} | null}

update
    Apply user corrections to an existing document.
    Input:  {action, user_id, doc_id, corrections: {...}}
    Output: {doc_id, updated: true}

audit_log
    Write an immutable download/access audit log entry.
    Input:  {action, user_id, doc_id, audit_action, ip_address?, user_agent?}
    Output: {logged: true}
"""

from __future__ import annotations

import logging
import os
from typing import Any

from tax_storage import TaxDocumentRepository

log = logging.getLogger(__name__)
log.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

_repo = TaxDocumentRepository()

# Maximum documents returned in a single list call before the caller
# should paginate using query params.
_LIST_LIMIT = 100


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    action = event.get("action")

    if action == "list":
        return _list(event)
    if action == "get":
        return _get(event)
    if action == "update":
        return _update(event)
    if action == "audit_log":
        return _audit_log(event)

    raise ValueError(f"Unknown action: {action!r}")


# ── Action handlers ────────────────────────────────────────────────────────────


def _list(event: dict[str, Any]) -> dict[str, Any]:
    user_id       = _require(event, "user_id")
    document_type = event.get("document_type")
    tax_year_raw  = event.get("tax_year")
    tax_year      = int(tax_year_raw) if tax_year_raw is not None else None

    documents: list[dict[str, Any]] = []

    for page in _repo.get_all_docs_by_user(
        user_id,
        document_type=document_type,
        tax_year=tax_year,
        decrypt=False,
        page_size=25,
    ):
        documents.extend(page)
        if len(documents) >= _LIST_LIMIT:
            documents = documents[:_LIST_LIMIT]
            break

    log.info(
        "Listed documents",
        extra={"user_id": user_id, "count": len(documents)},
    )
    return {"documents": documents}


def _get(event: dict[str, Any]) -> dict[str, Any]:
    user_id = _require(event, "user_id")
    doc_id  = _require(event, "doc_id")

    document = _repo.get_document(user_id=user_id, doc_id=doc_id, decrypt=True)

    log.info(
        "Fetched document",
        extra={"user_id": user_id, "doc_id": doc_id, "found": document is not None},
    )
    return {"document": document}


def _update(event: dict[str, Any]) -> dict[str, Any]:
    user_id     = _require(event, "user_id")
    doc_id      = _require(event, "doc_id")
    corrections = event.get("corrections")

    if not isinstance(corrections, dict):
        raise ValueError("corrections must be a dict")

    _repo.update_document(
        user_id=user_id,
        doc_id=doc_id,
        corrections=corrections,
    )

    log.info("Applied corrections", extra={"user_id": user_id, "doc_id": doc_id})
    return {"doc_id": doc_id, "updated": True}


def _audit_log(event: dict[str, Any]) -> dict[str, Any]:
    user_id      = _require(event, "user_id")
    doc_id       = _require(event, "doc_id")
    audit_action = event.get("audit_action", "unknown")
    ip_address   = event.get("ip_address")
    user_agent   = event.get("user_agent")

    _repo.write_audit_log(
        user_id=user_id,
        doc_id=doc_id,
        audit_action=audit_action,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return {"logged": True}


# ── Helpers ────────────────────────────────────────────────────────────────────


def _require(event: dict[str, Any], key: str) -> Any:
    value = event.get(key)
    if value is None:
        raise KeyError(f"Required field missing: {key!r}")
    return value
