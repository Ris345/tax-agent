"""
Pipeline error-recording Lambda for the Step Functions pipeline.

Receives:  {pipeline_stage, bucket, key, user_id, error}
Returns:   nothing (ResultPath: null in ASL)

Logs structured error details and forwards to SNS (if configured) so that
on-call engineers receive an alert. The Lambda itself should almost never fail;
any exception here is caught by the Step Functions Catch on each *Failed state.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import boto3

log = logging.getLogger(__name__)
log.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

_SNS_TOPIC_ARN = os.environ.get("PIPELINE_ERROR_SNS_TOPIC_ARN", "")
_sns = boto3.client("sns") if _SNS_TOPIC_ARN else None


def handler(event: dict[str, Any], context: Any) -> None:
    stage   = event.get("pipeline_stage", "Unknown")
    bucket  = event.get("bucket", "")
    key     = event.get("key", "")
    user_id = event.get("user_id", "")
    error   = event.get("error", {})

    log.error(
        "Pipeline stage failed",
        extra={
            "pipeline_stage": stage,
            "bucket":         bucket,
            "key":            key,
            "user_id":        user_id,
            "error_cause":    error.get("Cause", ""),
            "error_type":     error.get("Error", ""),
        },
    )

    if _sns is not None:
        message = (
            f"Tax pipeline failure\n"
            f"Stage  : {stage}\n"
            f"File   : s3://{bucket}/{key}\n"
            f"User   : {user_id}\n"
            f"Error  : {error.get('Error', 'Unknown')}\n"
            f"Cause  : {error.get('Cause', '')[:500]}\n"
        )
        _sns.publish(
            TopicArn=_SNS_TOPIC_ARN,
            Subject=f"[TAX-PIPELINE] {stage} failed",
            Message=message,
        )
