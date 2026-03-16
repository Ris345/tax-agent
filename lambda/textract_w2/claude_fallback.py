"""
Claude Sonnet 4 vision fallback for low-confidence Textract W-2 fields.

Flow
----
1. Receive base64 PDF page + list of flagged aliases + the Textract payload dict.
2. Build a scoped extraction prompt — only the flagged fields, with exact
   box labels and expected value formats.
3. Stream the document to Claude claude-sonnet-4-20250514 (Claude Sonnet 4)
   and collect the final message via get_final_message().
4. Validate the structured JSON response with Pydantic.
5. Merge Claude's results back into the Textract payload, updating confidence
   scores and flagging fields that are still below threshold.

The Anthropic API key is read at cold-start from AWS Secrets Manager so it
is never stored in Lambda environment variables in plain text.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Optional

import anthropic
import boto3
from botocore.exceptions import ClientError
from pydantic import BaseModel, Field, model_validator

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

# ── Configuration ─────────────────────────────────────────────────────────────
# claude-sonnet-4-20250514 is the full model ID for Claude Sonnet 4 (alias: claude-sonnet-4-0).
MODEL: str = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-20250514")

# Fields below this threshold in Claude's response are still flagged for review.
CLAUDE_CONFIDENCE_THRESHOLD: float = float(
    os.environ.get("CLAUDE_CONFIDENCE_THRESHOLD", "80.0")
)

# Secrets Manager secret name / ARN that holds the Anthropic API key.
# Populated by the CDK stack via the ANTHROPIC_SECRET_ARN environment variable.
_ANTHROPIC_SECRET_ARN: str = os.environ.get("ANTHROPIC_SECRET_ARN", "")

MAX_TOKENS = 2048
_SOURCE_TAG = "claude_sonnet4_fallback"

# ── W-2 field descriptions ────────────────────────────────────────────────────
# Each entry maps a field alias to a human-readable label used in the prompt.
# The descriptions deliberately mirror IRS W-2 box names so Claude can locate
# them even on scanned/low-quality forms.

W2_FIELD_DESCRIPTIONS: dict[str, str] = {
    # Employee / Employer identifiers
    "employee_ssn":                       "Employee social security number (format: XXX-XX-XXXX)",
    "employer_ein":                       "Employer identification number EIN (format: XX-XXXXXXX)",
    "employer_name":                      "Employer name (text)",
    "employer_address":                   "Employer address including zip code (text)",
    "employee_first_name":                "Employee first name and middle initial (text)",
    "employee_last_name":                 "Employee last name (text)",
    "employee_address":                   "Employee address including zip code (text)",
    # Box 1–8: Federal wages and taxes
    "box_1_wages_tips_other":             "Box 1 — Wages, tips, other compensation (dollar amount, e.g. 52350.00)",
    "box_2_federal_income_tax_withheld":  "Box 2 — Federal income tax withheld (dollar amount)",
    "box_3_social_security_wages":        "Box 3 — Social security wages (dollar amount)",
    "box_4_social_security_tax_withheld": "Box 4 — Social security tax withheld (dollar amount)",
    "box_5_medicare_wages_tips":          "Box 5 — Medicare wages and tips (dollar amount)",
    "box_6_medicare_tax_withheld":        "Box 6 — Medicare tax withheld (dollar amount)",
    "box_7_social_security_tips":         "Box 7 — Social security tips (dollar amount, or null if blank)",
    "box_8_allocated_tips":               "Box 8 — Allocated tips (dollar amount, or null if blank)",
    # Box 9–11
    "box_9_verification_code":            "Box 9 — Verification code (alphanumeric, or null)",
    "box_10_dependent_care_benefits":     "Box 10 — Dependent care benefits (dollar amount, or null)",
    "box_11_nonqualified_plans":          "Box 11 — Nonqualified plans (dollar amount, or null)",
    # Box 12a–d: Deferred compensation
    "box_12a":                            "Box 12a — Letter code + dollar amount (e.g. 'D 1200.00'), or null",
    "box_12b":                            "Box 12b — Letter code + dollar amount, or null",
    "box_12c":                            "Box 12c — Letter code + dollar amount, or null",
    "box_12d":                            "Box 12d — Letter code + dollar amount, or null",
    # Box 13: Checkboxes
    "box_13_statutory_employee":          "Box 13 — Statutory employee checkbox (return 'checked' or 'unchecked')",
    "box_13_retirement_plan":             "Box 13 — Retirement plan checkbox (return 'checked' or 'unchecked')",
    "box_13_third_party_sick_pay":        "Box 13 — Third-party sick pay checkbox (return 'checked' or 'unchecked')",
    # Box 14–17: Other / State
    "box_14_other":                       "Box 14 — Other: label and amount pairs (text), or null",
    "box_15_state":                       "Box 15 — State abbreviation (2-letter code, e.g. CA)",
    "box_15_employer_state_id":           "Box 15 — Employer state ID number (alphanumeric)",
    "box_16_state_wages_tips":            "Box 16 — State wages, tips, etc. (dollar amount)",
    "box_17_state_income_tax":            "Box 17 — State income tax (dollar amount)",
}

# ── System prompt ─────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are a precise W-2 tax form data extractor with deep knowledge of IRS form layout.

Your sole task: read the W-2 form document and extract exactly the fields listed by \
the user. Do not extract any other fields.

Output requirements (non-negotiable):
1. Respond with raw JSON only — no markdown fences, no prose outside the JSON.
2. The root object must have exactly one key: "fields" (an array).
3. Every element in "fields" must have:
   - "alias"      : the exact field identifier from the user's list (string)
   - "value"      : the extracted string, or null if the field is blank/not visible
   - "confidence" : integer 0–100 representing your certainty
   - "reasoning"  : one sentence explaining your confidence level

Confidence scale:
  90–100 : field is clearly printed, fully legible, and unambiguous
  75–89  : field is legible but slightly degraded (scan artefacts, partial stamps, low DPI)
  50–74  : field is partially obscured or ambiguous; best-effort read, set value to your best guess
  0–49   : field cannot be reliably determined; set value to null

Formatting rules:
  - Dollar amounts: strip currency symbols and commas → "52350.00"
  - Checkboxes: return "checked" or "unchecked" only
  - SSN / EIN: preserve hyphens as printed
  - Do not invent or infer values not visible in the document
  - Include one entry per alias in the user's list — even if value is null
"""


# ── Pydantic response models ──────────────────────────────────────────────────

class ClaudeFieldResult(BaseModel):
    alias: str
    value: Optional[str]
    confidence: int = Field(ge=0, le=100)
    reasoning: str

    @model_validator(mode="after")
    def clamp_and_normalize(self) -> "ClaudeFieldResult":
        # Hard-clamp in case the model drifts outside 0–100
        self.confidence = max(0, min(100, self.confidence))
        # Normalize empty string → None
        if self.value is not None and self.value.strip() == "":
            self.value = None
        return self


class ClaudeW2Response(BaseModel):
    fields: list[ClaudeFieldResult]


# ── Anthropic client (cached for Lambda container reuse) ──────────────────────

_client: Optional[anthropic.Anthropic] = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is not None:
        return _client

    api_key = os.environ.get("ANTHROPIC_API_KEY")

    # Prefer Secrets Manager over a plain env var so the key is never stored
    # in Lambda configuration in clear text.
    if not api_key and _ANTHROPIC_SECRET_ARN:
        api_key = _fetch_secret(_ANTHROPIC_SECRET_ARN)
        os.environ["ANTHROPIC_API_KEY"] = api_key  # cache in process for warm starts

    _client = anthropic.Anthropic(api_key=api_key)
    return _client


def _fetch_secret(secret_arn: str) -> str:
    sm = boto3.client("secretsmanager")
    try:
        resp = sm.get_secret_value(SecretId=secret_arn)
    except ClientError as exc:
        raise RuntimeError(
            f"Failed to retrieve Anthropic API key from Secrets Manager "
            f"({secret_arn}): {exc}"
        ) from exc

    secret = resp.get("SecretString") or ""
    # Secret can be a raw string or a JSON object {"ANTHROPIC_API_KEY": "sk-ant-..."}
    if secret.startswith("{"):
        try:
            secret = json.loads(secret).get("ANTHROPIC_API_KEY", secret)
        except json.JSONDecodeError:
            pass
    return secret.strip()


# ── Public API ────────────────────────────────────────────────────────────────

def extract_flagged_fields(
    base64_page: str,
    flagged_fields: list[str],
    textract_payload: dict,
    *,
    media_type: str = "application/pdf",
) -> dict:
    """
    Run Claude vision extraction for each alias in *flagged_fields* and merge
    the results back into *textract_payload*.

    Args:
        base64_page:      Base64-encoded PDF page or image (no data-URI prefix).
        flagged_fields:   Field aliases flagged by Textract (low confidence / missing).
        textract_payload: The full W2Payload dict returned by W2TextractProcessor.
        media_type:       MIME type of base64_page. Defaults to "application/pdf".
                          Pass "image/jpeg", "image/png", etc. for rasterised pages.

    Returns:
        A deep copy of textract_payload with Claude's extractions merged in.
        The original dict is never mutated.
    """
    if not flagged_fields:
        logger.info("No flagged fields — skipping Claude fallback")
        return textract_payload

    known = [f for f in flagged_fields if f in W2_FIELD_DESCRIPTIONS]
    unknown = sorted(set(flagged_fields) - set(known))
    if unknown:
        logger.warning("Skipping unknown field aliases: %s", unknown)
    if not known:
        return textract_payload

    logger.info(
        "Claude fallback: requesting %d field(s) via %s — %s",
        len(known), MODEL, known,
    )
    t0 = time.monotonic()

    user_prompt = _build_prompt(known)
    raw_json = _call_claude(base64_page, user_prompt, media_type)
    parsed = _parse_response(raw_json)
    result = _merge(textract_payload, parsed, known)

    logger.info("Claude fallback finished in %.2fs", time.monotonic() - t0)
    return result


# ── Internal helpers ──────────────────────────────────────────────────────────

def _build_prompt(fields: list[str]) -> str:
    """Construct a tightly scoped extraction prompt for only the flagged fields."""
    lines = [f"Extract exactly these {len(fields)} W-2 field(s):\n"]
    for alias in fields:
        desc = W2_FIELD_DESCRIPTIONS[alias]
        lines.append(f"  • {alias}: {desc}")
    lines.append(
        "\nReturn a JSON object with key 'fields' (array). "
        "Include one entry per alias above — even if the value is null."
    )
    return "\n".join(lines)


def _content_block(base64_page: str, media_type: str) -> dict:
    """Build the Anthropic content block for a PDF or image."""
    if media_type == "application/pdf":
        return {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": base64_page,
            },
        }
    # Rasterised page (image/jpeg, image/png, image/tiff, image/webp)
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": base64_page,
        },
    }


def _call_claude(base64_page: str, user_prompt: str, media_type: str) -> str:
    """
    Stream the document and prompt to Claude.
    Returns the complete text response.

    Streaming is used because PDF pages can be several MB of base64 data;
    streaming prevents Lambda from hitting API gateway / SDK request timeouts.
    """
    client = _get_client()

    with client.messages.stream(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    _content_block(base64_page, media_type),
                    {"type": "text", "text": user_prompt},
                ],
            }
        ],
    ) as stream:
        final = stream.get_final_message()

    if final.stop_reason not in ("end_turn", "stop_sequence"):
        logger.warning(
            "Unexpected Claude stop_reason: %s (input_tokens=%d, output_tokens=%d)",
            final.stop_reason,
            final.usage.input_tokens,
            final.usage.output_tokens,
        )

    text_blocks = [b.text for b in final.content if b.type == "text"]
    if not text_blocks:
        raise ValueError(
            f"Claude returned no text content. stop_reason={final.stop_reason}"
        )

    logger.debug(
        "Claude usage — in: %d tokens, out: %d tokens",
        final.usage.input_tokens,
        final.usage.output_tokens,
    )
    return "\n".join(text_blocks).strip()


def _parse_response(raw: str) -> ClaudeW2Response:
    """
    Parse and validate Claude's JSON response.

    Strips markdown code fences defensively (the system prompt forbids them,
    but earlier model versions occasionally include them).
    """
    cleaned = raw
    if cleaned.startswith("```"):
        cleaned = "\n".join(
            line for line in cleaned.splitlines()
            if not line.strip().startswith("```")
        ).strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Claude response is not valid JSON: {exc}\nFirst 500 chars: {raw[:500]}"
        ) from exc

    return ClaudeW2Response.model_validate(data)


def _merge(
    payload: dict,
    claude_response: ClaudeW2Response,
    requested_aliases: list[str],
) -> dict:
    """
    Merge Claude's extractions into a deep copy of the Textract payload.

    For each requested alias:
      - Overwrite value, confidence, and flagged_for_review.
      - Add source tag and claude_reasoning for traceability.
      - If Claude didn't return the field, mark it as unresolved.

    Updates payload["summary"] with fallback metadata.
    """
    payload = json.loads(json.dumps(payload))  # deep copy — never mutate input

    by_alias: dict[str, ClaudeFieldResult] = {
        f.alias: f for f in claude_response.fields
    }

    merged_count = 0
    still_flagged: list[str] = []

    for alias in requested_aliases:
        field_map: dict = payload.get("fields", {})
        if alias not in field_map:
            logger.warning("Alias '%s' absent from Textract payload — cannot merge", alias)
            still_flagged.append(alias)
            continue

        entry: dict = field_map[alias]

        if alias in by_alias:
            result = by_alias[alias]
            confidence_f = float(result.confidence)
            still_low = confidence_f < CLAUDE_CONFIDENCE_THRESHOLD

            entry["value"] = result.value
            entry["confidence"] = confidence_f
            entry["flagged_for_review"] = still_low
            entry["source"] = _SOURCE_TAG
            entry["claude_reasoning"] = result.reasoning

            if still_low:
                still_flagged.append(alias)
            merged_count += 1

            logger.debug(
                "  %-42s → value=%-20s confidence=%d%s",
                alias,
                repr(result.value),
                result.confidence,
                " [STILL FLAGGED]" if still_low else "",
            )
        else:
            # Claude was asked for this field but didn't return it
            entry["flagged_for_review"] = True
            entry["source"] = _SOURCE_TAG
            entry["claude_reasoning"] = "Field not present in Claude response."
            still_flagged.append(alias)
            logger.warning("Claude omitted expected alias '%s'", alias)

    # Patch summary block
    if "summary" in payload:
        summary = payload["summary"]
        summary["claude_fallback_fields_requested"] = len(requested_aliases)
        summary["claude_fallback_fields_merged"] = merged_count
        summary["still_flagged_after_fallback"] = still_flagged
        summary["requires_fallback"] = len(still_flagged) > 0
        summary["claude_model"] = MODEL

    logger.info(
        "Merge done — %d/%d updated, %d still flagged: %s",
        merged_count, len(requested_aliases), len(still_flagged), still_flagged,
    )
    return payload
