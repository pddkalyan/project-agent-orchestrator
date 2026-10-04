#!/usr/bin/env python3
"""
ChatGPT-plan reviewer bridge scaffold for TASK-0003.

Original worker: worker_gemini_cli / gemini-3.5-flash-lite
Recovery hardening: bootstrap_chatgpt / gpt-5.6-sol

This module is deliberately offline-only. It does not perform OAuth, network
requests, local AI inference, paid API calls, or merge operations. It validates
the data boundary that a future self-hosted Sign in with ChatGPT adapter must
satisfy before a live reviewer request may occur.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

EXACT_REVIEWER_MODEL = "gpt-6-astra"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
TASK_RE = re.compile(r"^TASK-[0-9]+$")

BLOCKED_AUTH_REQUIRED = "BLOCKED_AUTH_REQUIRED"
BLOCKED_NO_ASTRA = "BLOCKED_NO_ASTRA"
BLOCKED_PLAN_ALLOWANCE = "BLOCKED_PLAN_ALLOWANCE"
BLOCKED_EVIDENCE_MISMATCH = "BLOCKED_EVIDENCE_MISMATCH"
BLOCKED_INVALID_VERDICT = "BLOCKED_INVALID_VERDICT"
BLOCKED_RETRY_LIMIT = "BLOCKED_RETRY_LIMIT"

DIRECT_SECRET_PATTERNS = (
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b", re.IGNORECASE),
    re.compile(r"\bAIzaSy[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"bearer\s+\S+", re.IGNORECASE),
)
CREDENTIAL_LABEL_RE = re.compile(
    r"\b(api[_ -]?key|access[_ -]?key|token|password|passwd|secret|authorization|"
    r"private[_ -]?key|client[_ -]?secret|credential|cookie|session|auth[_ -]?code|"
    r"pkce|verifier|refresh[_ -]?token|access[_ -]?token)\b",
    re.IGNORECASE,
)
SECRET_KEY_RE = re.compile(
    r"(token|secret|password|passwd|cookie|authorization|auth_code|authorization_code|"
    r"pkce|verifier|credential|api_key|access_key|refresh_token|access_token)",
    re.IGNORECASE,
)
PRIVATE_KEY_BEGIN = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
PRIVATE_KEY_END = re.compile(r"-----END [A-Z0-9 ]*PRIVATE KEY-----")


def sanitize_text(text: Any, max_chars: int = 5000) -> str:
    """Conservatively redact credential-like text before persistence/logging."""
    value = str(text)
    out: list[str] = []
    redact_next = False
    in_private_key = False

    for line in value.splitlines():
        if in_private_key:
            out.append("[REDACTED-POTENTIAL-SECRET]")
            if PRIVATE_KEY_END.search(line):
                in_private_key = False
            continue
        if PRIVATE_KEY_BEGIN.search(line):
            out.append("[REDACTED-POTENTIAL-SECRET]")
            in_private_key = not bool(PRIVATE_KEY_END.search(line))
            redact_next = False
            continue
        if redact_next:
            if line.strip():
                out.append("[REDACTED-POTENTIAL-SECRET]")
                redact_next = False
            else:
                out.append("")
            continue
        if any(pattern.search(line) for pattern in DIRECT_SECRET_PATTERNS):
            out.append("[REDACTED-POTENTIAL-SECRET]")
            continue
        if CREDENTIAL_LABEL_RE.search(line):
            out.append("[REDACTED-POTENTIAL-SECRET]")
            if re.search(r"[:=]\s*(?:\||>)?\s*$", line):
                redact_next = True
            continue
        out.append(line)

    result = "\n".join(out)
    marker = "\n[TRUNCATED-SANITIZED]"
    if len(result) > max_chars:
        result = result[: max(0, max_chars - len(marker))] + marker
    return result


def sanitize_object(value: Any) -> Any:
    """Recursively sanitize structured data destined for history/evidence."""
    if isinstance(value, Mapping):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if SECRET_KEY_RE.search(key_text):
                clean[key_text] = "[REDACTED-POTENTIAL-SECRET]"
            else:
                clean[key_text] = sanitize_object(item)
        return clean
    if isinstance(value, list):
        return [sanitize_object(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_object(item) for item in value]
    if isinstance(value, str):
        return sanitize_text(value)
    return value


def validate_model_catalog(catalog: Mapping[str, Any] | None) -> tuple[bool, str]:
    """Allow only the exact account-authorized GPT-6 Astra identifier."""
    if not isinstance(catalog, Mapping):
        return False, BLOCKED_NO_ASTRA
    models = catalog.get("authorized_models")
    if not isinstance(models, list) or catalog.get("user_authorized") is not True:
        return False, BLOCKED_NO_ASTRA
    return (EXACT_REVIEWER_MODEL in models, "MODEL_ALLOWED" if EXACT_REVIEWER_MODEL in models else BLOCKED_NO_ASTRA)


def validate_auth_profile(profile: Mapping[str, Any] | None) -> tuple[bool, str]:
    """Validate non-secret host-local authorization metadata only."""
    if not isinstance(profile, Mapping):
        return False, BLOCKED_AUTH_REQUIRED
    if profile.get("active") is not True or profile.get("expired") is not False:
        return False, BLOCKED_AUTH_REQUIRED
    if not isinstance(profile.get("profile_id"), str) or not profile.get("profile_id"):
        return False, BLOCKED_AUTH_REQUIRED
    if any(SECRET_KEY_RE.search(str(key)) for key in profile.keys()):
        return False, BLOCKED_AUTH_REQUIRED
    return True, "AUTH_VALID"


def validate_plan_allowance(allowance: Mapping[str, Any] | None) -> tuple[bool, str]:
    """Permit included-plan usage only; separately billed/credit paths fail closed."""
    if not isinstance(allowance, Mapping):
        return False, BLOCKED_PLAN_ALLOWANCE
    if allowance.get("billing_mode") != "ZERO_SPEND_PLAN":
        return False, BLOCKED_PLAN_ALLOWANCE
    if allowance.get("separately_billed") is not False:
        return False, BLOCKED_PLAN_ALLOWANCE
    if allowance.get("credits_enabled") is not False:
        return False, BLOCKED_PLAN_ALLOWANCE
    remaining = allowance.get("remaining_requests")
    if not isinstance(remaining, int) or isinstance(remaining, bool) or remaining <= 0:
        return False, BLOCKED_PLAN_ALLOWANCE
    return True, "ALLOWANCE_VALID"


def validate_ci_evidence(candidate_sha: str, required_ci: Sequence[Mapping[str, Any]]) -> tuple[bool, str]:
    if not required_ci:
        return False, "MISSING_CI_EVIDENCE"
    for item in required_ci:
        if str(item.get("head_sha", "")) != candidate_sha:
            return False, "CI_SHA_MISMATCH"
        if item.get("result") != "PASS":
            return False, "CI_NOT_PASS"
        if not str(item.get("run_id", "")).isdigit():
            return False, "INVALID_CI_RUN_ID"
        if not DIGEST_RE.fullmatch(str(item.get("digest", ""))):
            return False, "INVALID_CI_DIGEST"
    return True, "CI_BOUND"


def validate_review_request(expected_sha: str, request: Mapping[str, Any]) -> tuple[bool, str]:
    """Bind repository, PR, candidate/base SHA, changed files, context, and CI."""
    if not SHA_RE.fullmatch(expected_sha):
        return False, "INVALID_EXPECTED_SHA"
    if not isinstance(request, Mapping):
        return False, "INVALID_REVIEW_REQUEST"
    if request.get("candidate_sha") != expected_sha:
        return False, "SHA_MISMATCH"
    if not SHA_RE.fullmatch(str(request.get("base_sha", ""))):
        return False, "INVALID_BASE_SHA"
    repository = request.get("repository")
    if not isinstance(repository, str) or repository.count("/") != 1:
        return False, "INVALID_REPOSITORY"
    pr_number = request.get("pr_number")
    if not isinstance(pr_number, int) or isinstance(pr_number, bool) or pr_number < 1:
        return False, "INVALID_PR_NUMBER"
    context_version = request.get("reviewer_context_version")
    if not isinstance(context_version, str) or not context_version:
        return False, "MISSING_CONTEXT_VERSION"
    changed = request.get("changed_files")
    if not isinstance(changed, list) or not changed or any(not isinstance(path, str) or not path for path in changed):
        return False, "INVALID_CHANGED_FILES"
    if len(changed) != len(set(changed)):
        return False, "DUPLICATE_CHANGED_FILES"
    return validate_ci_evidence(expected_sha, request.get("required_ci") or [])


def _normalize_finding(finding: Mapping[str, Any]) -> dict[str, Any] | None:
    required = (
        "severity",
        "exact_location",
        "exact_problem",
        "why_it_matters",
        "required_correction",
        "acceptance_criteria",
    )
    if any(key not in finding for key in required):
        return None
    if finding.get("severity") not in ("P1", "P2", "P3"):
        return None
    criteria = finding.get("acceptance_criteria")
    if not isinstance(criteria, list) or not criteria or any(not isinstance(x, str) or not x for x in criteria):
        return None
    result = {
        "severity": finding["severity"],
        "exact_location": sanitize_text(finding["exact_location"], 500),
        "exact_problem": sanitize_text(finding["exact_problem"], 2000),
        "why_it_matters": sanitize_text(finding["why_it_matters"], 2000),
        "required_correction": sanitize_text(finding["required_correction"], 3000),
        "acceptance_criteria": [sanitize_text(x, 1000) for x in criteria],
    }
    guidance = finding.get("implementation_guidance")
    if guidance:
        result["implementation_guidance"] = sanitize_text(guidance, 2000)
    return result


def validate_verdict(verdict: Mapping[str, Any] | None) -> tuple[bool, str, dict[str, Any] | None]:
    if not isinstance(verdict, Mapping):
        return False, BLOCKED_INVALID_VERDICT, None
    status = verdict.get("status")
    if status == "APPROVED":
        if verdict.get("findings") not in (None, []):
            return False, BLOCKED_INVALID_VERDICT, None
        return True, "VERDICT_VALID", {"status": "APPROVED", "findings": []}
    if status != "REJECTED":
        return False, BLOCKED_INVALID_VERDICT, None
    findings = verdict.get("findings")
    if not isinstance(findings, list) or not findings:
        return False, BLOCKED_INVALID_VERDICT, None
    normalized: list[dict[str, Any]] = []
    for finding in findings:
        if not isinstance(finding, Mapping):
            return False, BLOCKED_INVALID_VERDICT, None
        item = _normalize_finding(finding)
        if item is None:
            return False, BLOCKED_INVALID_VERDICT, None
        normalized.append(item)
    return True, "VERDICT_VALID", {"status": "REJECTED", "findings": normalized}


def compute_idempotency_key(task_id: str, sha: str, status: str, attempt: int) -> str:
    payload = f"{task_id}\n{sha}\n{status}\n{attempt}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_correction_package(
    task_id: str,
    reviewed_sha: str,
    findings: Sequence[Mapping[str, Any]],
    current_attempt: int,
    max_retries: int,
) -> dict[str, Any]:
    if not TASK_RE.fullmatch(task_id):
        raise ValueError("invalid task_id")
    if not SHA_RE.fullmatch(reviewed_sha):
        raise ValueError("invalid reviewed_sha")
    if not isinstance(current_attempt, int) or isinstance(current_attempt, bool) or current_attempt < 0:
        raise ValueError("invalid current_attempt")
    if not isinstance(max_retries, int) or isinstance(max_retries, bool) or max_retries < 0:
        raise ValueError("invalid max_retries")
    if current_attempt >= max_retries:
        return {
            "action": BLOCKED_RETRY_LIMIT,
            "task_id": task_id,
            "reviewed_sha": reviewed_sha,
            "current_attempt": current_attempt,
            "max_retries": max_retries,
            "corrections": [],
        }
    return {
        "action": "QUEUE_CORRECTION",
        "task_id": task_id,
        "reviewed_sha": reviewed_sha,
        "next_attempt": current_attempt + 1,
        "max_retries": max_retries,
        "corrections": sanitize_object(list(findings)),
    }


def evaluate_review(
    *,
    task_id: str,
    expected_sha: str,
    review_request: Mapping[str, Any],
    model_catalog: Mapping[str, Any] | None,
    auth_profile: Mapping[str, Any] | None,
    plan_allowance: Mapping[str, Any] | None,
    verdict_payload: Mapping[str, Any] | None,
    current_attempt: int = 0,
    max_retries: int = 3,
) -> dict[str, Any]:
    """Evaluate a future live review response without performing any live call."""
    if not TASK_RE.fullmatch(task_id):
        raise ValueError("invalid task_id")

    auth_ok, auth_status = validate_auth_profile(auth_profile)
    if not auth_ok:
        return {"schema_version": 1, "task_id": task_id, "reviewed_sha": expected_sha, "reviewer_model": EXACT_REVIEWER_MODEL, "status": auth_status}

    model_ok, model_status = validate_model_catalog(model_catalog)
    if not model_ok:
        return {"schema_version": 1, "task_id": task_id, "reviewed_sha": expected_sha, "reviewer_model": EXACT_REVIEWER_MODEL, "status": model_status}

    allowance_ok, allowance_status = validate_plan_allowance(plan_allowance)
    if not allowance_ok:
        return {"schema_version": 1, "task_id": task_id, "reviewed_sha": expected_sha, "reviewer_model": EXACT_REVIEWER_MODEL, "status": allowance_status}

    request_ok, request_status = validate_review_request(expected_sha, review_request)
    if not request_ok:
        return {"schema_version": 1, "task_id": task_id, "reviewed_sha": expected_sha, "reviewer_model": EXACT_REVIEWER_MODEL, "status": BLOCKED_EVIDENCE_MISMATCH, "reason": request_status}

    verdict_ok, verdict_status, verdict = validate_verdict(verdict_payload)
    if not verdict_ok or verdict is None:
        return {"schema_version": 1, "task_id": task_id, "reviewed_sha": expected_sha, "reviewer_model": EXACT_REVIEWER_MODEL, "status": verdict_status}

    result: dict[str, Any] = {
        "schema_version": 1,
        "task_id": task_id,
        "reviewed_sha": expected_sha,
        "base_sha": review_request["base_sha"],
        "repository": review_request["repository"],
        "pr_number": review_request["pr_number"],
        "reviewer_context_version": review_request["reviewer_context_version"],
        "reviewer_model": EXACT_REVIEWER_MODEL,
        "status": verdict["status"],
        "findings": verdict["findings"],
    }
    result["idempotency_key"] = compute_idempotency_key(task_id, expected_sha, verdict["status"], current_attempt)

    if verdict["status"] == "APPROVED":
        result["approval_confirmation"] = {
            "exact_sha_bound": True,
            "merge_allowed": False,
            "auto_merge_allowed": False,
        }
    else:
        result["correction_package"] = build_correction_package(
            task_id,
            expected_sha,
            verdict["findings"],
            current_attempt,
            max_retries,
        )

    return sanitize_object(result)


def deduplicate_result(result: Mapping[str, Any], existing_idempotency_keys: Iterable[str]) -> dict[str, Any]:
    key = result.get("idempotency_key")
    if not isinstance(key, str) or not key:
        raise ValueError("result missing idempotency_key")
    if key in set(existing_idempotency_keys):
        return {
            "action": "DUPLICATE_NOOP",
            "idempotency_key": key,
            "reviewed_sha": result.get("reviewed_sha"),
            "status": result.get("status"),
        }
    return {
        "action": "PERSIST_ONCE",
        "idempotency_key": key,
        "record": sanitize_object(dict(result)),
    }


def self_check() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "component": "ChatGPT Plan Reviewer Bridge",
        "mode": "OFFLINE_SCAFFOLD_DISABLED",
        "live_authorization_enabled": False,
        "paid_api_allowed": False,
        "local_inference": False,
        "required_model": EXACT_REVIEWER_MODEL,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        print(json.dumps(self_check(), indent=2))
        return 0
    print(json.dumps(self_check(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
