"""
Claude vision fallback Lambda for the Step Functions pipeline.

Receives:  {bucket, key, textract_payload}
Returns:   Updated textract_payload with Claude-extracted values merged in.

Wraps the existing extract_flagged_fields() logic as a standalone Lambda.
On failure the Step Functions Catch routes to ValidateDocument with the
original Textract payload, so this Lambda raising is safe.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from typing import Any

import boto3

log = logging.getLogger(__name__)
log.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

# ── Config ─────────────────────────────────────────────────────────────────────

_CLAUDE_MODEL           = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-20250514")
_CLAUDE_CONF_THRESHOLD  = float(os.environ.get("CLAUDE_CONFIDENCE_THRESHOLD", "80.0"))
_ANTHROPIC_SECRET_ARN   = os.environ.get("ANTHROPIC_SECRET_ARN", "")
_SOURCE_TAG             = "claude_sonnet4_fallback"

_EXT_TO_MIME: dict[str, str] = {
    ".pdf":  "application/pdf",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png":  "image/png",
    ".tiff": "image/tiff",
    ".tif":  "image/tiff",
}

# ── AWS + Anthropic clients (warm-start cached) ────────────────────────────────

_s3_client = boto3.client("s3")
_sm_client = boto3.client("secretsmanager")
_anthropic_client = None  # lazy-initialised


def _get_anthropic_client():
    global _anthropic_client
    if _anthropic_client is not None:
        return _anthropic_client

    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key and _ANTHROPIC_SECRET_ARN:
        secret  = _sm_client.get_secret_value(SecretId=_ANTHROPIC_SECRET_ARN)
        raw     = secret["SecretString"]
        try:
            api_key = json.loads(raw).get("ANTHROPIC_API_KEY", raw)
        except json.JSONDecodeError:
            api_key = raw

    _anthropic_client = anthropic.Anthropic(api_key=api_key)
    return _anthropic_client


# ── Field descriptions (used to build focused Claude prompt) ──────────────────

_W2_FIELD_DESCRIPTIONS: dict[str, str] = {
    "employer_ein":                  "Employer EIN (Box b) — format XX-XXXXXXX",
    "employer_name":                 "Employer legal name",
    "employer_address":              "Employer street address, city, state, ZIP",
    "employee_ssn":                  "Employee SSN (Box a) — format XXX-XX-XXXX",
    "employee_first_name":           "Employee first name",
    "employee_last_name":            "Employee last name / surname",
    "employee_address":              "Employee home address",
    "tax_year":                      "Tax year (4-digit year, e.g. 2024)",
    "wages_tips_other_compensation": "Box 1 — Wages, tips, other compensation ($)",
    "federal_income_tax_withheld":   "Box 2 — Federal income tax withheld ($)",
    "social_security_wages":         "Box 3 — Social security wages ($)",
    "social_security_tax_withheld":  "Box 4 — Social security tax withheld ($)",
    "medicare_wages_tips":           "Box 5 — Medicare wages and tips ($)",
    "medicare_tax_withheld":         "Box 6 — Medicare tax withheld ($)",
    "social_security_tips":          "Box 7 — Social security tips ($)",
    "allocated_tips":                "Box 8 — Allocated tips ($)",
    "dependent_care_benefits":       "Box 10 — Dependent care benefits ($)",
    "nonqualified_plans":            "Box 11 — Nonqualified plans ($)",
    "box_12a":                       "Box 12a — Code letter + dollar amount",
    "box_12b":                       "Box 12b — Code letter + dollar amount",
    "box_12c":                       "Box 12c — Code letter + dollar amount",
    "box_12d":                       "Box 12d — Code letter + dollar amount",
    "statutory_employee":            "Box 13 — Statutory employee checkbox (true/false)",
    "retirement_plan":               "Box 13 — Retirement plan checkbox (true/false)",
    "third_party_sick_pay":          "Box 13 — Third-party sick pay checkbox (true/false)",
    "state":                         "Box 15 — Two-letter state abbreviation (e.g. CA)",
    "employer_state_id":             "Box 15 — Employer state ID number",
    "state_wages_tips":              "Box 16 — State wages, tips ($)",
    "state_income_tax":              "Box 17 — State income tax ($)",
}


def _build_prompt(flagged: list[str]) -> str:
    field_lines = "\n".join(
        f"  - {alias}: {_W2_FIELD_DESCRIPTIONS.get(alias, alias)}"
        for alias in flagged
    )
    return (
        f"You are a precise IRS tax form reader. Extract ONLY the following fields "
        f"from this W-2 form image. For each field provide: value (exact text as it "
        f"appears, null if not present), confidence (0-100), and brief reasoning.\n\n"
        f"Fields to extract:\n{field_lines}\n\n"
        f"Return raw JSON only — no markdown fences, no extra text:\n"
        f'{{"fields": [{{"alias": "...", "value": "...", "confidence": 90, "reasoning": "..."}}]}}'
    )


def _fetch_document_b64(bucket: str, key: str) -> tuple[str, str]:
    """Return (base64_data, media_type)."""
    resp  = _s3_client.get_object(Bucket=bucket, Key=key)
    data  = resp["Body"].read()
    ext   = os.path.splitext(key.lower())[1]
    mime  = _EXT_TO_MIME.get(ext, "application/pdf")
    return base64.b64encode(data).decode("utf-8"), mime


def _content_block(b64: str, mime: str) -> dict:
    if mime == "application/pdf":
        return {
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": b64},
        }
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": mime, "data": b64},
    }


def _call_claude(b64: str, mime: str, flagged: list[str]) -> list[dict]:
    """Return list of {alias, value, confidence, reasoning} from Claude."""
    import anthropic

    client  = _get_anthropic_client()
    prompt  = _build_prompt(flagged)
    content = [_content_block(b64, mime), {"type": "text", "text": prompt}]

    with client.messages.stream(
        model=_CLAUDE_MODEL,
        max_tokens=2048,
        system=(
            "You extract text from IRS tax forms with exact precision. "
            "Return raw JSON only — never markdown, never prose."
        ),
        messages=[{"role": "user", "content": content}],
    ) as stream:
        message = stream.get_final_message()

    raw = message.content[0].text.strip()
    # Strip markdown fences if present
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    parsed = json.loads(raw)
    return parsed.get("fields", [])


def _merge(payload: dict, claude_fields: list[dict]) -> dict:
    import copy
    result = copy.deepcopy(payload)
    merged, still_flagged = [], []

    for entry in claude_fields:
        alias      = entry.get("alias", "")
        value      = entry.get("value")
        confidence = min(100, max(0, int(entry.get("confidence", 0))))
        if not value:
            confidence = 0

        if alias in result.get("fields", {}):
            result["fields"][alias] = {
                "value":              value,
                "confidence":         confidence,
                "flagged_for_review": confidence < _CLAUDE_CONF_THRESHOLD,
                "source":             _SOURCE_TAG,
            }
            if value and confidence >= _CLAUDE_CONF_THRESHOLD:
                merged.append(alias)
            else:
                still_flagged.append(alias)

    result.setdefault("summary", {}).update(
        source=_SOURCE_TAG,
        needs_claude_fallback=False,
        claude_fallback_fields_merged=merged,
        still_flagged_after_fallback=still_flagged,
        claude_model=_CLAUDE_MODEL,
    )
    return result


# ── Lambda entry point ─────────────────────────────────────────────────────────

def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    bucket          = event["bucket"]
    key             = event["key"]
    textract_payload = event["textract_payload"]

    flagged = textract_payload.get("summary", {}).get("flagged_fields", [])
    if not flagged:
        # Nothing to do — return payload unchanged
        return textract_payload

    log.info("Claude fallback starting", extra={"flagged_count": len(flagged)})

    b64, mime     = _fetch_document_b64(bucket, key)
    claude_fields = _call_claude(b64, mime, flagged)
    result        = _merge(textract_payload, claude_fields)

    log.info(
        "Claude fallback complete",
        extra={
            "merged": len(result["summary"].get("claude_fallback_fields_merged", [])),
            "still_flagged": len(result["summary"].get("still_flagged_after_fallback", [])),
        },
    )
    return result
