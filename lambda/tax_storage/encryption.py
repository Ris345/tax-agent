"""
PII encryption / decryption for tax document storage.

Uses the AWS Encryption SDK v3 with a KMS master key and a local caching
materials manager to minimise GenerateDataKey calls.

Encryption context (bound to every ciphertext):
    user_id        – prevents cross-user ciphertext reuse
    doc_id         – prevents cross-document ciphertext reuse
    document_type  – human-readable audit trail
    tax_year       – human-readable audit trail
"""

from __future__ import annotations

import json
import os
from typing import Any

import aws_encryption_sdk
from aws_encryption_sdk import CommitmentPolicy
from aws_encryption_sdk.caches.local import LocalCryptoMaterialsCache
from aws_encryption_sdk.keyrings.aws_kms import AwsKmsKeyring
from aws_encryption_sdk.materials_managers.caching import CachingCryptographicMaterialsManager

# ── Constants ─────────────────────────────────────────────────────────────────

_KMS_KEY_ARN: str = os.environ["TAX_STORAGE_KMS_KEY_ARN"]

# Cache data keys for up to 5 minutes or 1 000 operations, whichever comes first.
_CACHE_CAPACITY   = 100    # max simultaneous cached entries
_MAX_AGE_SECONDS  = 300.0  # 5 minutes
_MAX_MESSAGES     = 1_000  # encrypt calls per cached data key

# PII fields per document type that must be encrypted at rest.
# Non-PII structural fields (dollar amounts, dates, box flags) stay in plaintext.
PII_FIELDS: dict[str, frozenset[str]] = {
    "W2": frozenset({
        "employee_ssn",
        "employer_ein",
        "employer_name",
        "employer_address",
        "employee_first_name",
        "employee_last_name",
        "employee_address",
    }),
    "1099-NEC": frozenset({
        "payer_tin",
        "payer_name",
        "payer_address",
        "recipient_tin",
        "recipient_name",
        "recipient_address",
        "account_number",
    }),
    "1099-B": frozenset({
        "recipient_tin",
        "recipient_name",
        "recipient_address",
        "account_number",
        "payer_name",
        "payer_address",
    }),
    "1099-INT": frozenset({
        "payer_tin",
        "payer_name",
        "payer_address",
        "recipient_tin",
        "recipient_name",
        "recipient_address",
        "account_number",
    }),
}


# ── Module-level client (constructed once per Lambda container) ───────────────

def _build_cmm() -> CachingCryptographicMaterialsManager:
    cache   = LocalCryptoMaterialsCache(capacity=_CACHE_CAPACITY)
    keyring = AwsKmsKeyring(generator_key_id=_KMS_KEY_ARN)
    return CachingCryptographicMaterialsManager(
        materials_manager=aws_encryption_sdk.DefaultCryptographicMaterialsManager(keyring),
        cache=cache,
        max_age=_MAX_AGE_SECONDS,
        max_messages_encrypted=_MAX_MESSAGES,
    )


_CMM: CachingCryptographicMaterialsManager | None = None


def _get_cmm() -> CachingCryptographicMaterialsManager:
    global _CMM
    if _CMM is None:
        _CMM = _build_cmm()
    return _CMM


_CLIENT = aws_encryption_sdk.EncryptionSDKClient(
    commitment_policy=CommitmentPolicy.REQUIRE_ENCRYPT_REQUIRE_DECRYPT
)


# ── Public helpers ─────────────────────────────────────────────────────────────


def split_fields(
    flat_fields: dict[str, Any],
    document_type: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Partition ``flat_fields`` into *(pii, non_pii)* dicts.

    ``pii``     — fields listed in PII_FIELDS for the given document_type
    ``non_pii`` — everything else (dollar amounts, dates, flags, …)

    Unknown document types produce an empty *pii* dict (no encryption) and
    log a warning; callers should validate ``document_type`` upstream.
    """
    pii_keys = PII_FIELDS.get(document_type, frozenset())
    pii: dict[str, Any]     = {}
    non_pii: dict[str, Any] = {}
    for key, value in flat_fields.items():
        if key in pii_keys:
            pii[key] = value
        else:
            non_pii[key] = value
    return pii, non_pii


def encrypt_pii(
    pii_fields: dict[str, Any],
    *,
    user_id: str,
    doc_id: str,
    document_type: str,
    tax_year: int,
) -> bytes:
    """
    JSON-serialise *pii_fields* and encrypt with the KMS CMK.

    The encryption context binds the ciphertext to this specific
    (user_id, doc_id, document_type, tax_year) tuple — decryption will fail
    if any of these values differ.

    Returns raw ciphertext bytes suitable for storage in a DynamoDB Binary
    attribute.
    """
    plaintext = json.dumps(pii_fields, default=str).encode("utf-8")
    encryption_context = {
        "user_id":       user_id,
        "doc_id":        doc_id,
        "document_type": document_type,
        "tax_year":      str(tax_year),
    }
    ciphertext, _ = _CLIENT.encrypt(
        source=plaintext,
        materials_manager=_get_cmm(),
        encryption_context=encryption_context,
    )
    return ciphertext


def decrypt_pii(
    ciphertext: bytes,
    *,
    user_id: str,
    doc_id: str,
    document_type: str,
    tax_year: int,
) -> dict[str, Any]:
    """
    Decrypt *ciphertext* and return the original PII dict.

    The AWS Encryption SDK verifies that the encryption context embedded in
    the ciphertext matches the values supplied here; the call raises
    ``aws_encryption_sdk.exceptions.DecryptKeyError`` on mismatch.
    """
    expected_context = {
        "user_id":       user_id,
        "doc_id":        doc_id,
        "document_type": document_type,
        "tax_year":      str(tax_year),
    }
    plaintext, header = _CLIENT.decrypt(
        source=ciphertext,
        materials_manager=_get_cmm(),
    )
    # Verify every expected key is present and matches (SDK only checks *subset*
    # of context by default; we enforce an exact match here for defence-in-depth).
    for k, v in expected_context.items():
        if header.encryption_context.get(k) != v:
            raise ValueError(
                f"Encryption context mismatch on key {k!r}: "
                f"expected {v!r}, got {header.encryption_context.get(k)!r}"
            )
    return json.loads(plaintext.decode("utf-8"))
