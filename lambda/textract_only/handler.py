"""
Textract-only Lambda for the Step Functions pipeline.

Receives:  {bucket, key, user_id, document_type}
Returns:   Textract payload dict with summary.needs_claude_fallback bool.

This handler deliberately does NOT call Claude — that is a separate
Step Functions state (ClaudeFallback).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import boto3
from botocore.exceptions import ClientError

log = logging.getLogger(__name__)
log.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

_CONFIDENCE_THRESHOLD = float(os.environ.get("CONFIDENCE_THRESHOLD", "85.0"))

# ── Textract query catalogue (per document type) ──────────────────────────────

_W2_QUERIES = [
    {"Text": "What is the employer identification number?",          "Alias": "employer_ein"},
    {"Text": "What is the employer name?",                          "Alias": "employer_name"},
    {"Text": "What is the employer address?",                       "Alias": "employer_address"},
    {"Text": "What is the employee social security number?",        "Alias": "employee_ssn"},
    {"Text": "What is the employee first name?",                    "Alias": "employee_first_name"},
    {"Text": "What is the employee last name?",                     "Alias": "employee_last_name"},
    {"Text": "What is the employee address?",                       "Alias": "employee_address"},
    {"Text": "What is the tax year?",                               "Alias": "tax_year"},
    {"Text": "What is the amount in Box 1 wages tips and other compensation?", "Alias": "wages_tips_other_compensation"},
    {"Text": "What is the amount in Box 2 federal income tax withheld?",       "Alias": "federal_income_tax_withheld"},
    {"Text": "What is the amount in Box 3 social security wages?",             "Alias": "social_security_wages"},
    {"Text": "What is the amount in Box 4 social security tax withheld?",      "Alias": "social_security_tax_withheld"},
    {"Text": "What is the amount in Box 5 Medicare wages and tips?",           "Alias": "medicare_wages_tips"},
    {"Text": "What is the amount in Box 6 Medicare tax withheld?",             "Alias": "medicare_tax_withheld"},
    {"Text": "What is the amount in Box 7 social security tips?",              "Alias": "social_security_tips"},
    {"Text": "What is the amount in Box 8 allocated tips?",                    "Alias": "allocated_tips"},
    {"Text": "What is the amount in Box 10 dependent care benefits?",          "Alias": "dependent_care_benefits"},
    {"Text": "What is the amount in Box 11 nonqualified plans?",               "Alias": "nonqualified_plans"},
    {"Text": "What is the Box 12 code and amount entry 1?",                    "Alias": "box_12a"},
    {"Text": "What is the Box 12 code and amount entry 2?",                    "Alias": "box_12b"},
    {"Text": "What is the Box 12 code and amount entry 3?",                    "Alias": "box_12c"},
    {"Text": "What is the Box 12 code and amount entry 4?",                    "Alias": "box_12d"},
    {"Text": "Is Box 13 statutory employee checked?",                          "Alias": "statutory_employee"},
    {"Text": "Is Box 13 retirement plan checked?",                             "Alias": "retirement_plan"},
    {"Text": "Is Box 13 third party sick pay checked?",                        "Alias": "third_party_sick_pay"},
    {"Text": "What is the state in Box 15?",                                   "Alias": "state"},
    {"Text": "What is the employer state ID number in Box 15?",                "Alias": "employer_state_id"},
    {"Text": "What is the state wages tips in Box 16?",                        "Alias": "state_wages_tips"},
    {"Text": "What is the state income tax in Box 17?",                        "Alias": "state_income_tax"},
]

assert len(_W2_QUERIES) <= 30, "Textract QUERIES adapter hard limit is 30"

_QUERIES_BY_DOC_TYPE: dict[str, list[dict]] = {
    "W2": _W2_QUERIES,
    # 1099 types: add their query lists here when needed
}

# ── AWS clients (module-level — shared across warm invocations) ────────────────

_textract = boto3.client("textract")


# ── Core processing ────────────────────────────────────────────────────────────

def _run_textract(bucket: str, key: str, queries: list[dict]) -> list[dict]:
    response = _textract.analyze_document(
        Document={"S3Object": {"Bucket": bucket, "Name": key}},
        FeatureTypes=["QUERIES"],
        QueriesConfig={"Queries": queries},
    )
    return response["Blocks"]


def _parse_blocks(
    blocks: list[dict],
    confidence_threshold: float,
) -> tuple[dict[str, Any], list[str]]:
    """
    Extract QUERY_RESULT answers from Textract blocks.

    Returns (fields_dict, flagged_aliases) where fields_dict maps
    alias → {value, confidence, flagged_for_review, source}.
    """
    block_map   = {b["Id"]: b for b in blocks}
    fields: dict[str, Any] = {}
    flagged:  list[str]    = []

    for block in blocks:
        if block.get("BlockType") != "QUERY":
            continue
        alias = block.get("Query", {}).get("Alias", "")
        if not alias:
            continue

        # Follow ANSWER relationship → QUERY_RESULT block
        answer_ids = [
            rel["Ids"]
            for rel in block.get("Relationships", [])
            if rel.get("Type") == "ANSWER"
        ]
        value:      str | None = None
        confidence: float      = 0.0

        if answer_ids:
            result_id    = answer_ids[0][0]
            result_block = block_map.get(result_id, {})
            value        = result_block.get("Text")
            confidence   = result_block.get("Confidence", 0.0)

        below_threshold = confidence < confidence_threshold or value is None
        if below_threshold:
            flagged.append(alias)

        fields[alias] = {
            "value":               value,
            "confidence":          round(confidence, 2),
            "flagged_for_review":  below_threshold,
            "source":              "textract",
        }

    return fields, flagged


def _extract_user_id(key: str) -> str:
    """
    Extract user_id from a user-scoped S3 key.

    Key format (set by Next.js /api/upload): uploads/{userId}/{date}/{uuid}.{ext}
    Segment 0 = "uploads", segment 1 = userId.

    Raises ValueError if the key does not conform to this format.
    """
    parts = key.split("/")
    if len(parts) < 3 or parts[0] != "uploads":
        raise ValueError(
            f"S3 key {key!r} does not match expected format "
            "'uploads/{{userId}}/{{date}}/{{filename}}'"
        )
    return parts[1]


def _build_payload(
    bucket: str,
    key: str,
    user_id: str,
    document_type: str,
    fields: dict[str, Any],
    flagged: list[str],
) -> dict[str, Any]:
    tax_year_entry = fields.get("tax_year", {})
    tax_year_raw   = tax_year_entry.get("value") if isinstance(tax_year_entry, dict) else None

    return {
        "document_type":    document_type,
        "tax_year":         tax_year_raw,
        "document_bucket":  bucket,
        "document_key":     key,
        # user_id extracted from key prefix — flows through every Step Functions state
        # so that the DynamoDB store Lambda can use it as the PK without a separate
        # lookup and without trusting client-supplied input.
        "user_id":          user_id,
        "fields":           fields,
        "summary": {
            "total_fields":          len(fields),
            "flagged_fields":        flagged,
            "total_flagged":         len(flagged),
            "needs_claude_fallback": len(flagged) > 0,
            "confidence_threshold":  _CONFIDENCE_THRESHOLD,
            "source":                "textract",
        },
    }


# ── Lambda entry point ─────────────────────────────────────────────────────────

def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    bucket        = event["bucket"]
    key           = event["key"]
    document_type = event.get("document_type", "W2")

    # Extract user_id from the S3 key prefix (uploads/{userId}/...) set by
    # the authenticated Next.js upload route. The Step Functions pipeline
    # passes this through to the DynamoDB store Lambda as the partition key.
    user_id = event.get("user_id") or _extract_user_id(key)

    queries = _QUERIES_BY_DOC_TYPE.get(document_type)
    if queries is None:
        raise ValueError(f"Unsupported document_type: {document_type!r}")

    log.info("Starting Textract analysis", extra={"bucket": bucket, "key": key})

    blocks          = _run_textract(bucket, key, queries)
    fields, flagged = _parse_blocks(blocks, _CONFIDENCE_THRESHOLD)
    payload         = _build_payload(bucket, key, user_id, document_type, fields, flagged)

    log.info(
        "Textract complete",
        extra={"total_fields": len(fields), "flagged": len(flagged)},
    )
    return payload
