"""
Lambda handler: W-2 document analysis via AWS Textract QUERIES adapter.

Trigger  : S3 PutObject event (PDF or image)
Returns  : Confidence-scored JSON; fields < CONFIDENCE_THRESHOLD are flagged
           and automatically re-extracted via the Claude Sonnet 4 vision fallback.

Pipeline per document
---------------------
1. Textract AnalyzeDocument (QUERIES adapter) → 29 W-2 field queries
2. If any field < CONFIDENCE_THRESHOLD → fetch the raw page from S3, base64-encode
   it, call claude_fallback.extract_flagged_fields()
3. Return merged payload (Textract + Claude results, with source tags)

Textract QUERIES limit: 30 queries per AnalyzeDocument call.
Multi-page PDFs: AnalyzeDocument processes page 1 only (synchronous API).
  For multi-page payloads, extend to StartDocumentAnalysis (async).
"""

from __future__ import annotations

import base64
import json
import logging
import os
import urllib.parse
from dataclasses import asdict, dataclass
from typing import Optional

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from claude_fallback import extract_flagged_fields

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

CONFIDENCE_THRESHOLD: float = float(os.environ.get("CONFIDENCE_THRESHOLD", "85.0"))

_RETRY_CONFIG = Config(retries={"max_attempts": 3, "mode": "adaptive"})
_textract = boto3.client("textract", config=_RETRY_CONFIG)
_s3 = boto3.client("s3", config=_RETRY_CONFIG)

# Infer media type from S3 key extension for the Claude fallback
_EXT_TO_MIME: dict[str, str] = {
    ".pdf":  "application/pdf",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png":  "image/png",
    ".tiff": "image/tiff",
    ".tif":  "image/tiff",
    ".webp": "image/webp",
}


def _media_type_from_key(key: str) -> str:
    ext = "." + key.rsplit(".", 1)[-1].lower() if "." in key else ""
    return _EXT_TO_MIME.get(ext, "application/pdf")

# ─── W-2 query definitions ────────────────────────────────────────────────────
# Textract QUERIES: natural-language questions anchored to the W-2 layout.
# Alias becomes the key in the returned payload.
# Total: 29 queries (limit is 30 per API call).

W2_QUERIES: list[dict[str, str]] = [
    # ── Employee / Employer identifiers ──────────────────────────────────────
    {"Text": "What is the employee social security number?",           "Alias": "employee_ssn"},
    {"Text": "What is the employer identification number EIN?",        "Alias": "employer_ein"},
    {"Text": "What is the employer name?",                             "Alias": "employer_name"},
    {"Text": "What is the employer address and zip code?",             "Alias": "employer_address"},
    {"Text": "What is the employee first name and middle initial?",    "Alias": "employee_first_name"},
    {"Text": "What is the employee last name?",                        "Alias": "employee_last_name"},
    {"Text": "What is the employee address and zip code?",             "Alias": "employee_address"},

    # ── Box 1–8: Federal wages and taxes ─────────────────────────────────────
    {"Text": "What is the amount in box 1 wages tips other compensation?",    "Alias": "box_1_wages_tips_other"},
    {"Text": "What is the amount in box 2 federal income tax withheld?",      "Alias": "box_2_federal_income_tax_withheld"},
    {"Text": "What is the amount in box 3 social security wages?",            "Alias": "box_3_social_security_wages"},
    {"Text": "What is the amount in box 4 social security tax withheld?",     "Alias": "box_4_social_security_tax_withheld"},
    {"Text": "What is the amount in box 5 Medicare wages and tips?",          "Alias": "box_5_medicare_wages_tips"},
    {"Text": "What is the amount in box 6 Medicare tax withheld?",            "Alias": "box_6_medicare_tax_withheld"},
    {"Text": "What is the amount in box 7 social security tips?",             "Alias": "box_7_social_security_tips"},
    {"Text": "What is the amount in box 8 allocated tips?",                   "Alias": "box_8_allocated_tips"},

    # ── Box 9–11: Other benefits ──────────────────────────────────────────────
    {"Text": "What is the verification code in box 9?",                       "Alias": "box_9_verification_code"},
    {"Text": "What is the amount in box 10 dependent care benefits?",         "Alias": "box_10_dependent_care_benefits"},
    {"Text": "What is the amount in box 11 nonqualified plans?",              "Alias": "box_11_nonqualified_plans"},

    # ── Box 12a–12d: Deferred compensation codes ──────────────────────────────
    {"Text": "What is the code and amount in box 12a?",  "Alias": "box_12a"},
    {"Text": "What is the code and amount in box 12b?",  "Alias": "box_12b"},
    {"Text": "What is the code and amount in box 12c?",  "Alias": "box_12c"},
    {"Text": "What is the code and amount in box 12d?",  "Alias": "box_12d"},

    # ── Box 13: Checkboxes ────────────────────────────────────────────────────
    {"Text": "Is the statutory employee checkbox in box 13 checked?",         "Alias": "box_13_statutory_employee"},
    {"Text": "Is the retirement plan checkbox in box 13 checked?",            "Alias": "box_13_retirement_plan"},
    {"Text": "Is the third party sick pay checkbox in box 13 checked?",       "Alias": "box_13_third_party_sick_pay"},

    # ── Box 14–17: Other / State ──────────────────────────────────────────────
    {"Text": "What is listed in box 14 other?",                               "Alias": "box_14_other"},
    {"Text": "What is the state abbreviation in box 15?",                     "Alias": "box_15_state"},
    {"Text": "What is the employer state ID number in box 15?",               "Alias": "box_15_employer_state_id"},
    {"Text": "What is the amount in box 16 state wages tips?",                "Alias": "box_16_state_wages_tips"},
    {"Text": "What is the amount in box 17 state income tax?",                "Alias": "box_17_state_income_tax"},
]

assert len(W2_QUERIES) <= 30, "Textract QUERIES adapter supports at most 30 queries per call"


# ─── Data model ───────────────────────────────────────────────────────────────

@dataclass
class FieldResult:
    alias: str
    value: Optional[str]
    confidence: float
    flagged_for_review: bool

    def to_dict(self) -> dict:
        return {
            "value": self.value,
            "confidence": round(self.confidence, 2),
            "flagged_for_review": self.flagged_for_review,
        }


@dataclass
class W2Payload:
    document_bucket: str
    document_key: str
    fields: dict[str, dict]
    summary: dict

    def to_dict(self) -> dict:
        return asdict(self)


# ─── Core processor ───────────────────────────────────────────────────────────

class W2TextractProcessor:
    def __init__(self, threshold: float = CONFIDENCE_THRESHOLD) -> None:
        self.threshold = threshold

    def process(self, bucket: str, key: str) -> W2Payload:
        raw_blocks = self._call_textract(bucket, key)
        fields = self._parse_blocks(raw_blocks)
        return self._build_payload(bucket, key, fields)

    # ── Textract call ──────────────────────────────────────────────────────

    def _call_textract(self, bucket: str, key: str) -> list[dict]:
        logger.info("Calling Textract AnalyzeDocument", extra={"bucket": bucket, "key": key})
        try:
            response = _textract.analyze_document(
                Document={"S3Object": {"Bucket": bucket, "Name": key}},
                FeatureTypes=["QUERIES"],
                QueriesConfig={"Queries": W2_QUERIES},
            )
        except ClientError as exc:
            error_code = exc.response["Error"]["Code"]
            logger.error("Textract error %s: %s", error_code, exc)
            raise

        blocks = response.get("Blocks", [])
        logger.info("Textract returned %d blocks", len(blocks))
        return blocks

    # ── Block parsing ──────────────────────────────────────────────────────

    def _parse_blocks(self, blocks: list[dict]) -> list[FieldResult]:
        """
        Map QUERY blocks → their QUERY_RESULT answers via ANSWER relationships.

        Block graph:
          QUERY ──[ANSWER]──► QUERY_RESULT
        """
        block_map: dict[str, dict] = {b["Id"]: b for b in blocks}
        results: list[FieldResult] = []

        for block in blocks:
            if block.get("BlockType") != "QUERY":
                continue

            query_meta = block.get("Query", {})
            alias: str = query_meta.get("Alias", block["Id"])
            result_block = self._find_answer_block(block, block_map)

            if result_block is not None:
                raw_confidence: float = result_block.get("Confidence", 0.0)
                value: Optional[str] = result_block.get("Text") or None
            else:
                # Textract found no answer — treat as zero confidence
                raw_confidence = 0.0
                value = None

            results.append(
                FieldResult(
                    alias=alias,
                    value=value,
                    confidence=raw_confidence,
                    flagged_for_review=(raw_confidence < self.threshold),
                )
            )

        return results

    @staticmethod
    def _find_answer_block(query_block: dict, block_map: dict[str, dict]) -> Optional[dict]:
        for rel in query_block.get("Relationships", []):
            if rel.get("Type") == "ANSWER":
                for answer_id in rel.get("Ids", []):
                    candidate = block_map.get(answer_id)
                    if candidate and candidate.get("BlockType") == "QUERY_RESULT":
                        return candidate
        return None

    # ── Payload assembly ───────────────────────────────────────────────────

    def _build_payload(
        self, bucket: str, key: str, fields: list[FieldResult]
    ) -> W2Payload:
        flagged = [f.alias for f in fields if f.flagged_for_review]
        not_extracted = [f.alias for f in fields if f.value is None]

        field_dict = {f.alias: f.to_dict() for f in fields}

        summary = {
            "total_fields": len(fields),
            "extracted_count": sum(1 for f in fields if f.value is not None),
            "flagged_count": len(flagged),
            "flagged_fields": flagged,
            "not_extracted_fields": not_extracted,
            "requires_fallback": len(flagged) > 0,
            "confidence_threshold": self.threshold,
            "average_confidence": (
                round(sum(f.confidence for f in fields) / len(fields), 2)
                if fields else 0.0
            ),
        }

        logger.info(
            "W-2 extraction complete",
            extra={
                "flagged_count": summary["flagged_count"],
                "extracted_count": summary["extracted_count"],
                "requires_fallback": summary["requires_fallback"],
            },
        )

        return W2Payload(
            document_bucket=bucket,
            document_key=key,
            fields=field_dict,
            summary=summary,
        )


# ─── Lambda entry point ───────────────────────────────────────────────────────

_processor = W2TextractProcessor()


def _fetch_page_as_base64(bucket: str, key: str) -> str:
    """Download the S3 object and return its raw bytes as a base64 string."""
    resp = _s3.get_object(Bucket=bucket, Key=key)
    return base64.b64encode(resp["Body"].read()).decode("utf-8")


def handler(event: dict, context: object) -> dict:
    """
    Handles S3 PutObject events. Each record is processed independently.

    Pipeline:
      1. Textract QUERIES → confidence-scored W-2 fields
      2. If requires_fallback → fetch raw page, call Claude Sonnet 4 vision
         fallback for flagged fields only, merge results
      3. Return final payload

    Returns a list of per-document results (one per S3 record).
    """
    results = []

    for record in event.get("Records", []):
        s3_info = record.get("s3", {})
        bucket = s3_info["bucket"]["name"]
        key = urllib.parse.unquote_plus(s3_info["object"]["key"])

        logger.info("Processing record: s3://%s/%s", bucket, key)

        try:
            # ── Stage 1: Textract ─────────────────────────────────────────
            textract_payload = _processor.process(bucket, key)
            payload_dict = textract_payload.to_dict()

            # ── Stage 2: Claude vision fallback ──────────────────────────
            flagged: list[str] = payload_dict["summary"].get("flagged_fields", [])

            if flagged:
                logger.info(
                    "%d field(s) flagged — invoking Claude fallback: %s",
                    len(flagged), flagged,
                )
                base64_page = _fetch_page_as_base64(bucket, key)
                media_type = _media_type_from_key(key)
                payload_dict = extract_flagged_fields(
                    base64_page=base64_page,
                    flagged_fields=flagged,
                    textract_payload=payload_dict,
                    media_type=media_type,
                )

            results.append({"status": "success", "result": payload_dict})

        except ClientError as exc:
            error_code = exc.response["Error"]["Code"]
            logger.error(
                "Failed to process s3://%s/%s — %s: %s",
                bucket, key, error_code, exc,
            )
            results.append({
                "status": "error",
                "document_bucket": bucket,
                "document_key": key,
                "error_code": error_code,
                "error_message": str(exc),
            })
            raise  # marks invocation failed → DLQ

    return {
        "statusCode": 200,
        "recordCount": len(results),
        "results": results,
    }
