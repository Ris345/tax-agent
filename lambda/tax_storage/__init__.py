"""
tax_storage — DynamoDB persistence layer for extracted tax documents.

Quick start
-----------
Store a document (e.g. a dict produced by from_textract_payload)::

    from tax_storage import TaxDocumentRepository

    repo   = TaxDocumentRepository()
    doc_id = repo.put_document(user_id="usr-abc123", document=validated_doc)

Fetch it back (with PII decrypted)::

    doc = repo.get_document(user_id="usr-abc123", doc_id=doc_id)

Iterate all W-2s for a user (PII NOT decrypted, for bulk ops)::

    for page in repo.get_all_docs_by_user(
        "usr-abc123",
        document_type="W2",
        tax_year=2024,
    ):
        for record in page:
            print(record["doc_id"], record["tax_year"])

Delete a record::

    deleted = repo.delete_document(user_id="usr-abc123", doc_id=doc_id)

Encryption utilities (lower-level)::

    from tax_storage.encryption import PII_FIELDS, split_fields
"""

from .encryption import PII_FIELDS, decrypt_pii, encrypt_pii, split_fields
from .repository import TaxDocumentRepository

__all__ = [
    "TaxDocumentRepository",
    "PII_FIELDS",
    "split_fields",
    "encrypt_pii",
    "decrypt_pii",
]
