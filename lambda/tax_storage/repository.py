"""
DynamoDB repository for extracted tax document schemas.

Table design
------------
PK  user_id   (String) – e.g. "usr-abc123"
SK  doc_id    (String) – e.g. "W2#2024#<uuid4>"

Audit log entries share the same table with SK prefix "AUDIT#":
    SK: AUDIT#{iso_timestamp}#{uuid4}
    (query with begins_with("AUDIT#") to fetch all audit events for a user)

TTL
---
The ``expires_at`` attribute (Unix epoch, integer seconds) is configured
as the DynamoDB TTL attribute.  Items are automatically deleted ~1 year
after creation.

PII handling
------------
Fields listed in encryption.PII_FIELDS are JSON-serialised and encrypted
with the AWS Encryption SDK before storage (``encrypted_pii`` Binary).
All other fields are stored as plaintext in ``schema_data`` (Map).
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Iterator
from uuid import uuid4

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from .encryption import decrypt_pii, encrypt_pii, split_fields

log = logging.getLogger(__name__)

_TABLE_NAME: str = os.environ["TAX_DOCS_TABLE_NAME"]
_TTL_DAYS: int   = int(os.environ.get("TAX_DOCS_TTL_DAYS", "365"))

# Metadata keys that are not document field values — excluded when re-splitting
# a materialised document for update operations.
_METADATA_KEYS: frozenset[str] = frozenset({
    "user_id", "doc_id", "document_type", "tax_year",
    "created_at", "updated_at", "expires_at", "source",
    "confidence_summary", "field_metadata", "pii_decryption_error",
})

# ── DynamoDB type helpers ──────────────────────────────────────────────────────


def _to_decimal(value: Any) -> Any:
    """
    Recursively convert float → Decimal (DynamoDB rejects Python floats).
    Strings, ints, bools, None, bytes, and non-numeric types are returned
    unchanged.  Dicts and lists are traversed recursively.
    """
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _to_decimal(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_decimal(v) for v in value]
    return value


def _from_decimal(value: Any) -> Any:
    """
    Recursively convert Decimal → float for JSON-serialisable output.
    """
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: _from_decimal(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_from_decimal(v) for v in value]
    return value


# ── Module-level DynamoDB resource (re-used across Lambda invocations) ─────────

_dynamodb = boto3.resource("dynamodb")
_table    = _dynamodb.Table(_TABLE_NAME)


# ── Repository ─────────────────────────────────────────────────────────────────


class TaxDocumentRepository:
    """
    CRUD operations for tax document records in DynamoDB.

    All public methods accept and return plain Python dicts.  DynamoDB-specific
    concerns (Decimal conversion, Binary attributes, pagination tokens) are
    handled internally.
    """

    # ── Write ──────────────────────────────────────────────────────────────────

    def put_document(
        self,
        *,
        user_id: str,
        document: dict[str, Any],
        source: str = "textract",
        field_metadata: dict[str, Any] | None = None,
    ) -> str:
        """
        Persist a tax document extracted from Textract / Claude.

        ``document`` must be a flat dict that can be validated against one of
        the tax_models schemas.  The caller is responsible for validation
        before calling this method.

        ``field_metadata`` is the raw Textract ``fields`` dict
        (alias → {value, confidence, flagged_for_review, source}).  Only the
        non-value keys (confidence, flagged_for_review, source) are persisted;
        the values are already captured in document.

        Returns the generated ``doc_id`` (e.g. ``"W2#2024#<uuid4>"``).

        Raises:
            KeyError: if ``document_type`` or ``tax_year`` is missing.
            botocore.exceptions.ClientError: on DynamoDB write failure.
        """
        document_type: str = document["document_type"]
        tax_year: int       = int(document["tax_year"])

        doc_id     = f"{document_type}#{tax_year}#{uuid4()}"
        created_at = datetime.now(UTC).isoformat()
        expires_at = int(
            (datetime.now(UTC) + timedelta(days=_TTL_DAYS)).timestamp()
        )

        # Split fields: PII → encrypted binary, rest → plaintext map.
        pii_fields, schema_fields = split_fields(document, document_type)

        encrypted_pii: bytes | None = None
        if pii_fields:
            encrypted_pii = encrypt_pii(
                pii_fields,
                user_id=user_id,
                doc_id=doc_id,
                document_type=document_type,
                tax_year=tax_year,
            )

        # Pull confidence_summary out of schema_fields if present
        # (comes from Textract processor payload).
        confidence_summary = schema_fields.pop("summary", None)

        item: dict[str, Any] = {
            "user_id":       user_id,
            "doc_id":        doc_id,
            "document_type": document_type,
            "tax_year":      tax_year,
            "created_at":    created_at,
            "expires_at":    expires_at,
            "source":        source,
        }

        if confidence_summary is not None:
            item["confidence_summary"] = _to_decimal(confidence_summary)

        if field_metadata is not None:
            # Strip 'value' to avoid double-storing data that is already in
            # schema_data / encrypted_pii.  Keep confidence + flagged_for_review.
            sanitised_meta = {
                alias: {k: v for k, v in meta.items() if k != "value"}
                for alias, meta in field_metadata.items()
                if isinstance(meta, dict)
            }
            item["field_metadata"] = _to_decimal(sanitised_meta)

        if schema_fields:
            item["schema_data"] = _to_decimal(schema_fields)

        if encrypted_pii is not None:
            item["encrypted_pii"] = encrypted_pii  # Binary attribute

        _table.put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(user_id)",  # no silent overwrites
        )
        log.info(
            "Stored tax document",
            extra={"user_id": user_id, "doc_id": doc_id, "source": source},
        )
        return doc_id

    def update_document(
        self,
        *,
        user_id: str,
        doc_id: str,
        corrections: dict[str, Any],
        field_metadata: dict[str, Any] | None = None,
    ) -> None:
        """
        Apply user corrections to an existing tax document.

        ``corrections`` is a flat dict of field → new value.  PII fields are
        re-encrypted with the same encryption context; non-PII fields are
        stored in ``schema_data``.

        Uses a read-modify-write to correctly merge PII corrections with the
        existing encrypted blob.

        Raises:
            KeyError: if the document does not exist.
            botocore.exceptions.ClientError(ConditionalCheckFailedException):
                on concurrent modification (document deleted between read and write).
        """
        # 1. Read current state with PII decrypted.
        current = self.get_document(user_id=user_id, doc_id=doc_id, decrypt=True)
        if current is None:
            raise KeyError(
                f"Document not found: user_id={user_id!r} doc_id={doc_id!r}"
            )

        document_type: str = str(current["document_type"])
        tax_year: int      = int(current["tax_year"])
        created_at: str    = str(current["created_at"])
        expires_at: int    = int(current["expires_at"])

        # 2. Build document-field slice (strip metadata).
        doc_fields = {k: v for k, v in current.items() if k not in _METADATA_KEYS}

        # 3. Apply user corrections (override existing values).
        doc_fields.update(
            {k: v for k, v in corrections.items() if k not in _METADATA_KEYS}
        )

        # 4. Re-split and re-encrypt.
        pii_fields, schema_fields = split_fields(doc_fields, document_type)

        encrypted_pii: bytes | None = None
        if pii_fields:
            encrypted_pii = encrypt_pii(
                pii_fields,
                user_id=user_id,
                doc_id=doc_id,
                document_type=document_type,
                tax_year=tax_year,
            )

        # 5. Build the replacement item.
        item: dict[str, Any] = {
            "user_id":       user_id,
            "doc_id":        doc_id,
            "document_type": document_type,
            "tax_year":      tax_year,
            "created_at":    created_at,
            "updated_at":    datetime.now(UTC).isoformat(),
            "expires_at":    expires_at,
            "source":        "user_corrected",
        }

        existing_summary = current.get("confidence_summary")
        if existing_summary is not None:
            item["confidence_summary"] = _to_decimal(existing_summary)

        # Prefer caller-supplied field_metadata; fall back to existing.
        effective_meta = (
            field_metadata
            if field_metadata is not None
            else current.get("field_metadata")
        )
        if effective_meta is not None:
            sanitised = {
                alias: {k: v for k, v in m.items() if k != "value"}
                for alias, m in effective_meta.items()
                if isinstance(m, dict)
            }
            item["field_metadata"] = _to_decimal(sanitised)

        if schema_fields:
            item["schema_data"] = _to_decimal(schema_fields)

        if encrypted_pii is not None:
            item["encrypted_pii"] = encrypted_pii

        _table.put_item(
            Item=item,
            ConditionExpression="attribute_exists(user_id)",
        )
        log.info(
            "Updated tax document",
            extra={"user_id": user_id, "doc_id": doc_id},
        )

    # ── Audit logging ──────────────────────────────────────────────────────────

    def write_audit_log(
        self,
        *,
        user_id: str,
        doc_id: str,
        audit_action: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """
        Write an immutable audit log entry to the tax documents table.

        Audit entries use SK prefix ``AUDIT#`` so they can be listed separately:
            Key("doc_id").begins_with("AUDIT#")

        Entries share the table's TTL attribute and expire after ``_TTL_DAYS``.
        """
        now = datetime.now(UTC)
        audit_sk  = f"AUDIT#{now.isoformat()}#{uuid4()}"
        expires_at = int((now + timedelta(days=_TTL_DAYS)).timestamp())

        item: dict[str, Any] = {
            "user_id":    user_id,
            "doc_id":     audit_sk,
            "action":     audit_action,
            "target_doc": doc_id,
            "timestamp":  now.isoformat(),
            "expires_at": expires_at,
        }
        if ip_address:
            item["ip_address"] = ip_address
        if user_agent:
            item["user_agent"] = user_agent[:512]  # guard against extremely long UA strings

        _table.put_item(Item=item)
        log.info(
            "Wrote audit log",
            extra={
                "user_id":       user_id,
                "audit_action":  audit_action,
                "target_doc":    doc_id,
            },
        )

    # ── Read ───────────────────────────────────────────────────────────────────

    def get_document(
        self,
        *,
        user_id: str,
        doc_id: str,
        decrypt: bool = True,
    ) -> dict[str, Any] | None:
        """
        Fetch a single document by (user_id, doc_id).

        Returns ``None`` if the item does not exist or has expired.
        When ``decrypt=True`` (default) the PII fields are decrypted and
        merged back into the returned dict.
        """
        response = _table.get_item(Key={"user_id": user_id, "doc_id": doc_id})
        item = response.get("Item")
        if item is None:
            return None
        return self._materialise(item, decrypt=decrypt)

    def get_all_docs_by_user(
        self,
        user_id: str,
        *,
        document_type: str | None = None,
        tax_year: int | None = None,
        decrypt: bool = False,
        page_size: int = 25,
    ) -> Iterator[list[dict[str, Any]]]:
        """
        Yield pages of documents for *user_id*.

        Optional filters (applied server-side via KeyConditionExpression):
        - ``document_type``: restrict to a single form type
          (e.g. ``"W2"``, ``"1099-NEC"``)
        - ``tax_year``: restrict to a specific tax year;
          requires ``document_type`` to be set

        Filtering uses ``begins_with`` on the SK prefix so no scan or
        FilterExpression is needed.  Audit log entries (SK prefix ``AUDIT#``)
        are automatically excluded when ``document_type`` is not set because
        the key condition requires ``doc_id`` to NOT begin with "AUDIT#"
        implicitly via the partition scan.

        ``decrypt=False`` by default for bulk queries to avoid the cost of
        decrypting PII that callers may not need.

        Yields successive pages (lists of dicts) until exhausted.
        """
        key_condition = Key("user_id").eq(user_id)

        if document_type:
            if tax_year is not None:
                prefix = f"{document_type}#{tax_year}#"
            else:
                prefix = f"{document_type}#"
            key_condition &= Key("doc_id").begins_with(prefix)
        else:
            # Exclude audit log entries from normal document listings.
            # Audit entries start with "AUDIT#"; real docs start with a type letter.
            # We achieve this by listing only items whose SK does NOT begin with
            # "AUDIT#" via a FilterExpression (scan-side only, keys already filtered).
            pass

        paginator_kwargs: dict[str, Any] = {
            "KeyConditionExpression": key_condition,
            "Limit": page_size,
        }

        # Exclude audit entries when doing an unfiltered user listing.
        if not document_type:
            from boto3.dynamodb.conditions import Attr
            paginator_kwargs["FilterExpression"] = Attr("action").not_exists()

        last_evaluated_key: dict | None = None

        while True:
            if last_evaluated_key:
                paginator_kwargs["ExclusiveStartKey"] = last_evaluated_key

            response = _table.query(**paginator_kwargs)
            items    = response.get("Items", [])
            if items:
                yield [self._materialise(item, decrypt=decrypt) for item in items]

            last_evaluated_key = response.get("LastEvaluatedKey")
            if not last_evaluated_key:
                break

    # ── Delete ─────────────────────────────────────────────────────────────────

    def delete_document(self, *, user_id: str, doc_id: str) -> bool:
        """
        Hard-delete a document record.

        Returns ``True`` if the item existed and was deleted, ``False`` if
        it was not found.  DynamoDB TTL handles soft-expiry; this method is
        for explicit user-requested deletion (GDPR right-to-erasure etc.).
        """
        try:
            _table.delete_item(
                Key={"user_id": user_id, "doc_id": doc_id},
                ConditionExpression="attribute_exists(user_id)",
            )
            log.info(
                "Deleted tax document",
                extra={"user_id": user_id, "doc_id": doc_id},
            )
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _materialise(
        self,
        item: dict[str, Any],
        *,
        decrypt: bool,
    ) -> dict[str, Any]:
        """
        Convert a raw DynamoDB item into a clean Python dict.

        Steps:
        1. Convert Decimal → float for numeric fields.
        2. Optionally decrypt ``encrypted_pii`` and merge into the result.
        3. Remove internal storage keys (``encrypted_pii``).
        4. Flatten ``schema_data`` into the top-level result dict.
        """
        result: dict[str, Any] = {}

        # Copy scalar / map fields, converting Decimals.
        for key, value in item.items():
            if key == "encrypted_pii":
                continue  # handled separately
            result[key] = _from_decimal(value)

        # Flatten schema_data into the top-level result dict.
        schema_data = result.pop("schema_data", None)
        if schema_data:
            result.update(schema_data)

        if decrypt and "encrypted_pii" in item:
            document_type: str = item["document_type"]
            tax_year: int      = int(item["tax_year"])
            try:
                pii = decrypt_pii(
                    bytes(item["encrypted_pii"]),  # Binary → bytes
                    user_id=item["user_id"],
                    doc_id=item["doc_id"],
                    document_type=document_type,
                    tax_year=tax_year,
                )
                result.update(pii)
            except Exception:
                log.exception(
                    "PII decryption failed",
                    extra={"doc_id": item.get("doc_id")},
                )
                result["pii_decryption_error"] = True

        return result
