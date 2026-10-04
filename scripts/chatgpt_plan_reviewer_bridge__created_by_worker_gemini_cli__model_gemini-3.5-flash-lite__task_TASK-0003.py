#!/usr/bin/env python3
"""
ChatGPT Plan Reviewer Bridge for TASK-0003.

Original worker: worker_gemini_cli / gemini-3.5-flash-lite

Provides offline and self-hosted deterministic bridge logic for exact-model policy
(`gpt-6-astra`), exact-SHA review packet validation, auth checks, plan allowance checks,
verdict validation, blocked statuses, and bounded correction packaging.
"""

from __main__ import *
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DIRECT_SECRET_PATTERNS = [
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b", re.IGNORECASE),
    re.compile(r"\bAIzaSy[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b", re.IGNORECASE),
    re.compile(r"bearer\s+\S+", re.IGNORECASE),
]
CREDENTIAL_LABEL = re.compile(
    r"\b(api[_ -]?key|access[_ -]?key|token|password|passwd|secret|authorization|"
    r"private[_ -]?key|client[_ -]?secret|credential)\b",
    re.IGNORECASE,
)
PRIVATE_KEY_BEGIN = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
PRIVATE_KEY_END = re.compile(r"-----END [A-Z0-9 ]*PRIVATE KEY-----")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def sanitize_text(text: str, max_chars: int = 5000) -> str:
    """Sanitize text by redacting direct secrets, keys, and credentials."""
    if not isinstance(text, str):
        return str(text)
    output: list[str] = []
    redact_next = False
    in_key = False
    for line in text.splitlines():
        if in_key:
            output.append("[REDACTED-POTENTIAL-SECRET]")
            if PRIVATE_KEY_END.search(line):
                in_key = False
            continue
        if PRIVATE_KEY_BEGIN.search(line):
            output.append("[REDACTED-POTENTIAL-SECRET]")
            in_key = not bool(PRIVATE_KEY_END.search(line))
            redact_next = False
            continue
        if redact_next:
            if not line.strip():
                output.append("")
                continue
            output.append("[REDACTED-POTENTIAL-SECRET]")
            redact_next = False
            continue
        if any(p.search(line) for p in DIRECT_SECRET_PATTERNS):
            output.append("[REDACTED-POTENTIAL-SECRET]")
            continue
        if CREDENTIAL_LABEL.search(line):
            output.append("[REDACTED-POTENTIAL-SECRET]")
            if re.search(r"[:=]\s*$", line):
                redact_next = True
            continue
        output.append(line)
    result = "\n".join(output)
    if len(result) > max_chars:
        result = result[:max_chars] + "\n[TRUNCATED]"
    return result


def validate_model_catalog(catalog: Dict[str, Any]) -> Tuple[bool, str]:
    """Verify that exact gpt-6-astra is available in the authorized catalog."""
    if not isinstance(catalog, dict):
        return False, "BLOCKED_NO_ASTRA"
    models = catalog.get("authorized_models", [])
    if "gpt-6-astra" in models and catalog.get("user_authorized", False):
        return True, "APPROVED_MODEL"
    return False, "BLOCKED_NO_ASTRA"


def validate_auth_profile(auth_profile: Optional[Dict[str, Any]]) -> Tuple[bool, str]:
    """Verify host-local user authorization profile exists and is active."""
    if not isinstance(auth_profile, dict) or not auth_profile.get("active", False):
        return False, "BLOCKED_AUTH_REQUIRED"
    if auth_profile.get("expired", True):
        return False, "BLOCKED_AUTH_REQUIRED"
    return True, "AUTH_VALID"


def validate_plan_allowance(allowance: Optional[Dict[str, Any]]) -> Tuple[bool, str]:
    """Verify ChatGPT plan allowance has remaining quota and zero billing."""
    if not isinstance(allowance, dict):
        return False, "BLOCKED_PLAN_ALLOWANCE"
    if allowance.get("billing_mode") != "ZERO_SPEND_PLAN":
        return False, "BLOCKED_PLAN_ALLOWANCE"
    if allowance.get("remaining_requests", 0) <= 0:
        return False, "BLOCKED_PLAN_ALLOWANCE"
    return True, "ALLOWANCE_VALID"


def validate_sha_binding(expected_sha: str, review_packet: Dict[str, Any]) -> Tuple[bool, str]:
    """Ensure exact SHA matches review packet and target reference."""
    if not SHA_RE.match(expected_sha):
        return False, "INVALID_EXPECTED_SHA"
    packet_sha = review_packet.get("reviewed_sha") or review_packet.get("reviewed_head")
    if packet_sha != expected_sha:
        return False, "SHA_MISMATCH"
    return True, "SHA_BOUND"


def evaluate_review(
    expected_sha: str,
    review_packet: Dict[str, Any],
    model_catalog: Dict[str, Any],
    auth_profile: Optional[Dict[str, Any]],
    plan_allowance: Optional[Dict[str, Any]],
    verdict_payload: Dict[str, Any],
) -> Dict[str, Any]:
    """Execute complete deterministic review bridge evaluation."""
    task_id = review_packet.get("task_id", "TASK-UNKNOWN")

    model_ok, model_msg = validate_model_catalog(model_catalog)
    if not model_ok:
        return {
            "task_id": task_id,
            "reviewed_sha": expected_sha,
            "reviewer_model": "gpt-6-astra",
            "status": model_msg,
            "reason": "Exact gpt-6-astra model not found in authorized catalog or user authorization missing."
        }

    auth_ok, auth_msg = validate_auth_profile(auth_profile)
    if not auth_ok:
        return {
            "task_id": task_id,
            "reviewed_sha": expected_sha,
            "reviewer_model": "gpt-6-astra",
            "status": auth_msg,
            "reason": "Host-local ChatGPT user authorization profile missing or expired."
        }

    allow_ok, allow_msg = validate_plan_allowance(plan_allowance)
    if not allow_ok:
        return {
            "task_id": task_id,
            "reviewed_sha": expected_sha,
            "reviewer_model": "gpt-6-astra",
            "status": allow_msg,
            "reason": "ChatGPT plan allowance exhausted or non-zero billing detected."
        }

    sha_ok, sha_msg = validate_sha_binding(expected_sha, review_packet)
    if not sha_ok:
        return {
            "task_id": task_id,
            "reviewed_sha": expected_sha,
            "reviewer_model": "gpt-6-astra",
            "status": "REJECTED",
            "reason": f"SHA binding validation failed: {sha_msg}"
        }

    status = verdict_payload.get("status", "REJECTED")
    if status not in ("APPROVED", "REJECTED"):
        status = "REJECTED"

    result = {
        "schema_version": 1,
        "task_id": task_id,
        "reviewed_sha": expected_sha,
        "reviewer_model": "gpt-6-astra",
        "status": status,
        "findings": verdict_payload.get("findings", []),
    }

    if status == "REJECTED":
        result["correction_package"] = verdict_payload.get(
            "correction_package",
            {
                "summary": "Astra review identified boundary or correctness gaps.",
                "actions": ["Address findings in findings array", "Re-run offline tests and resubmit review packet."]
            }
        )
    else:
        result["approval_confirmation"] = {
            "binding": "Exact SHA bound successfully",
            "merge_allowed": False,
            "note": "Reviewer is review-only. Merge must be handled by repository policy gate."
        }

    return result


def compute_idempotency_key(task_id: str, sha: str, attempt: int) -> str:
    raw = f"{task_id}:{sha}:{attempt}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
