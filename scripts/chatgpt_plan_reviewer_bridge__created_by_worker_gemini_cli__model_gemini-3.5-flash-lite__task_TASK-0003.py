#!/usr/bin/env python3
"""
ChatGPT-plan reviewer bridge scaffold for TASK-0003.

Original worker: worker_gemini_cli / gemini-3.5-flash-lite
Recovery hardening: bootstrap_chatgpt / gpt-5.6-sol

Offline-only scaffold. It performs no OAuth, network requests, local AI
inference, paid API calls, cleanup mutations, or merge operations.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from typing import Any, Iterable, Mapping, Sequence

EXACT_REVIEWER_MODEL = "gpt-6-astra"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
TASK_RE = re.compile(r"^TASK-[0-9]+$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")

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

EXPECTED_SNAPSHOT_KEYS = {
    "task_id",
    "repository",
    "pr_number",
    "base_sha",
    "candidate_sha",
    "reviewer_context_version",
    "changed_files",
    "required_ci",
}
EXPECTED_CI_KEYS = {"workflow", "run_id", "head_sha", "result", "digest"}
AUTH_METADATA_KEYS = {"active", "expired", "profile_id"}

FINDING_REQUIRED_TEXT = (
    "exact_location",
    "exact_problem",
    "why_it_matters",
    "required_correction",
)
FINDING_ALLOWED_KEYS = {
    "severity",
    *FINDING_REQUIRED_TEXT,
    "acceptance_criteria",
    "implementation_guidance",
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _nonblank_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _has_direct_secret(text: str) -> bool:
    return any(pattern.search(text) for pattern in DIRECT_SECRET_PATTERNS)


def _leading_spaces(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def sanitize_text(text: Any, max_chars: int = 5000) -> str:
    """Conservatively redact credential-bearing lines and complete blocks."""
    value = str(text)
    out: list[str] = []
    in_private_key = False
    block_indent: int | None = None
    redact_until_blank = False

    for line in value.splitlines():
        stripped = line.strip()
        indent = _leading_spaces(line)

        if in_private_key:
            out.append("[REDACTED-POTENTIAL-SECRET]")
            if PRIVATE_KEY_END.search(line):
                in_private_key = False
            continue

        if PRIVATE_KEY_BEGIN.search(line):
            out.append("[REDACTED-POTENTIAL-SECRET]")
            in_private_key = not bool(PRIVATE_KEY_END.search(line))
            block_indent = None
            redact_until_blank = False
            continue

        if block_indent is not None:
            if not stripped:
                out.append("")
                continue
            if indent > block_indent:
                out.append("[REDACTED-POTENTIAL-SECRET]")
                continue
            block_indent = None

        if redact_until_blank:
            if not stripped:
                out.append("")
                redact_until_blank = False
            else:
                out.append("[REDACTED-POTENTIAL-SECRET]")
            continue

        if _has_direct_secret(line):
            out.append("[REDACTED-POTENTIAL-SECRET]")
            continue

        if CREDENTIAL_LABEL_RE.search(line):
            out.append("[REDACTED-POTENTIAL-SECRET]")
            # YAML block scalar: redact every indented continuation line.
            if re.search(r"[:=]\s*[|>][+-]?\s*(?:#.*)?$", line):
                block_indent = indent
            # Label-only / value-on-following-lines form: conservatively redact
            # every continuation line until a blank separator.
            elif re.search(r"[:=]\s*(?:#.*)?$", line):
                redact_until_blank = True
            continue

        out.append(line)

    result = "\n".join(out)
    marker = "\n[TRUNCATED-SANITIZED]"
    if len(result) > max_chars:
        result = result[: max(0, max_chars - len(marker))] + marker
    return result


def sanitize_object(value: Any) -> Any:
    """Recursively redact secret-bearing keys as well as values."""
    if isinstance(value, Mapping):
        clean: dict[str, Any] = {}
        redacted_index = 0
        for key, item in value.items():
            key_text = str(key)
            key_is_secret = bool(SECRET_KEY_RE.search(key_text) or _has_direct_secret(key_text))
            if key_is_secret:
                redacted_index += 1
                clean[f"__redacted_secret_key_{redacted_index}__"] = "[REDACTED-POTENTIAL-SECRET]"
            else:
                clean[key_text] = sanitize_object(item)
        return clean
    if isinstance(value, (list, tuple)):
        return [sanitize_object(item) for item in value]
    if isinstance(value, str):
        return sanitize_text(value)
    return value


def validate_model_catalog(catalog: Mapping[str, Any] | None) -> tuple[bool, str]:
    if not isinstance(catalog, Mapping):
        return False, BLOCKED_NO_ASTRA
    models = catalog.get("authorized_models")
    if not isinstance(models, list) or catalog.get("user_authorized") is not True:
        return False, BLOCKED_NO_ASTRA
    if EXACT_REVIEWER_MODEL not in models:
        return False, BLOCKED_NO_ASTRA
    return True, "MODEL_ALLOWED"


def validate_auth_profile(profile: Mapping[str, Any] | None) -> tuple[bool, str]:
    """Accept only non-secret host-local metadata with an exact allowlist."""
    if not isinstance(profile, Mapping):
        return False, BLOCKED_AUTH_REQUIRED
    if set(profile.keys()) != AUTH_METADATA_KEYS:
        return False, BLOCKED_AUTH_REQUIRED
    if profile.get("active") is not True or profile.get("expired") is not False:
        return False, BLOCKED_AUTH_REQUIRED
    profile_id = profile.get("profile_id")
    if not _nonblank_string(profile_id):
        return False, BLOCKED_AUTH_REQUIRED
    if CREDENTIAL_LABEL_RE.search(profile_id) or _has_direct_secret(profile_id):
        return False, BLOCKED_AUTH_REQUIRED
    return True, "AUTH_VALID"


def validate_plan_allowance(allowance: Mapping[str, Any] | None) -> tuple[bool, str]:
    if not isinstance(allowance, Mapping):
        return False, BLOCKED_PLAN_ALLOWANCE
    expected_keys = {"billing_mode", "separately_billed", "credits_enabled", "remaining_requests"}
    if set(allowance.keys()) != expected_keys:
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


def normalize_review_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and canonicalize an independently supplied immutable snapshot."""
    if not isinstance(snapshot, Mapping):
        raise ValueError("expected snapshot must be an object")
    if set(snapshot.keys()) != EXPECTED_SNAPSHOT_KEYS:
        raise ValueError("expected snapshot fields mismatch")

    task_id = snapshot["task_id"]
    repository = snapshot["repository"]
    pr_number = snapshot["pr_number"]
    base_sha = snapshot["base_sha"]
    candidate_sha = snapshot["candidate_sha"]
    context_version = snapshot["reviewer_context_version"]
    changed_files = snapshot["changed_files"]
    required_ci = snapshot["required_ci"]

    if not isinstance(task_id, str) or not TASK_RE.fullmatch(task_id):
        raise ValueError("invalid task_id")
    if not _nonblank_string(repository) or repository.count("/") != 1:
        raise ValueError("invalid repository")
    if not isinstance(pr_number, int) or isinstance(pr_number, bool) or pr_number < 1:
        raise ValueError("invalid pr_number")
    if not isinstance(base_sha, str) or not SHA_RE.fullmatch(base_sha):
        raise ValueError("invalid base_sha")
    if not isinstance(candidate_sha, str) or not SHA_RE.fullmatch(candidate_sha):
        raise ValueError("invalid candidate_sha")
    if not _nonblank_string(context_version):
        raise ValueError("invalid reviewer_context_version")
    if not isinstance(changed_files, list) or not changed_files:
        raise ValueError("invalid changed_files")
    if any(not _nonblank_string(path) for path in changed_files):
        raise ValueError("invalid changed_file")
    if len(changed_files) != len(set(changed_files)):
        raise ValueError("duplicate changed_files")

    if not isinstance(required_ci, list) or not required_ci:
        raise ValueError("missing required_ci")

    normalized_ci: list[dict[str, Any]] = []
    ci_identity: set[tuple[Any, ...]] = set()
    for item in required_ci:
        if not isinstance(item, Mapping) or set(item.keys()) != EXPECTED_CI_KEYS:
            raise ValueError("invalid CI evidence fields")
        workflow = item["workflow"]
        run_id = item["run_id"]
        head_sha = item["head_sha"]
        result = item["result"]
        digest = item["digest"]
        if not _nonblank_string(workflow):
            raise ValueError("invalid CI workflow")
        if not isinstance(run_id, int) or isinstance(run_id, bool) or run_id < 1:
            raise ValueError("invalid CI run_id")
        if head_sha != candidate_sha:
            raise ValueError("CI head_sha mismatch")
        if result != "PASS":
            raise ValueError("CI result must be PASS")
        if not isinstance(digest, str) or not DIGEST_RE.fullmatch(digest):
            raise ValueError("invalid CI digest")
        identity = (workflow, run_id, head_sha, result, digest)
        if identity in ci_identity:
            raise ValueError("duplicate CI evidence")
        ci_identity.add(identity)
        normalized_ci.append(
            {
                "workflow": workflow,
                "run_id": run_id,
                "head_sha": head_sha,
                "result": result,
                "digest": digest,
            }
        )

    normalized_ci.sort(key=lambda item: (item["workflow"], item["run_id"], item["digest"]))

    return {
        "task_id": task_id,
        "repository": repository,
        "pr_number": pr_number,
        "base_sha": base_sha,
        "candidate_sha": candidate_sha,
        "reviewer_context_version": context_version,
        "changed_files": sorted(changed_files),
        "required_ci": normalized_ci,
    }


def review_identity_digest(snapshot: Mapping[str, Any]) -> str:
    return sha256_json(normalize_review_snapshot(snapshot))


def validate_review_request(
    expected_snapshot: Mapping[str, Any],
    review_request: Mapping[str, Any],
) -> tuple[bool, str]:
    """Require exact equality to the independent trusted snapshot."""
    expected = normalize_review_snapshot(expected_snapshot)
    try:
        actual = normalize_review_snapshot(review_request)
    except ValueError as exc:
        return False, f"INVALID_REVIEW_REQUEST:{exc}"
    if actual != expected:
        for field in (
            "task_id",
            "repository",
            "pr_number",
            "base_sha",
            "candidate_sha",
            "reviewer_context_version",
            "changed_files",
            "required_ci",
        ):
            if actual.get(field) != expected.get(field):
                return False, f"SNAPSHOT_MISMATCH:{field}"
        return False, "SNAPSHOT_MISMATCH"
    return True, "SNAPSHOT_BOUND"


def _normalize_finding(finding: Mapping[str, Any]) -> dict[str, Any] | None:
    if not isinstance(finding, Mapping):
        return None
    if set(finding.keys()) - FINDING_ALLOWED_KEYS:
        return None
    if finding.get("severity") not in ("P1", "P2", "P3"):
        return None
    for key in FINDING_REQUIRED_TEXT:
        if not _nonblank_string(finding.get(key)):
            return None
    criteria = finding.get("acceptance_criteria")
    if (
        not isinstance(criteria, list)
        or not criteria
        or any(not _nonblank_string(item) for item in criteria)
    ):
        return None
    guidance = finding.get("implementation_guidance")
    if guidance is not None and not _nonblank_string(guidance):
        return None

    result: dict[str, Any] = {
        "severity": finding["severity"],
        "exact_location": sanitize_text(finding["exact_location"], 500),
        "exact_problem": sanitize_text(finding["exact_problem"], 2000),
        "why_it_matters": sanitize_text(finding["why_it_matters"], 2000),
        "required_correction": sanitize_text(finding["required_correction"], 3000),
        "acceptance_criteria": [sanitize_text(item, 1000) for item in criteria],
    }
    if guidance is not None:
        result["implementation_guidance"] = sanitize_text(guidance, 2000)
    return result


def validate_verdict(
    verdict: Mapping[str, Any] | None,
    candidate_sha: str,
    expected_snapshot_digest: str,
) -> tuple[bool, str, dict[str, Any] | None]:
    """Bind the reviewer result to both candidate SHA and full snapshot digest."""
    if not isinstance(verdict, Mapping):
        return False, BLOCKED_INVALID_VERDICT, None
    allowed = {"status", "reviewed_sha", "review_snapshot_digest", "findings"}
    if set(verdict.keys()) - allowed:
        return False, BLOCKED_INVALID_VERDICT, None
    if verdict.get("reviewed_sha") != candidate_sha:
        return False, BLOCKED_INVALID_VERDICT, None
    if verdict.get("review_snapshot_digest") != expected_snapshot_digest:
        return False, BLOCKED_INVALID_VERDICT, None

    status = verdict.get("status")
    findings = verdict.get("findings")
    if status == "APPROVED":
        if findings not in (None, []):
            return False, BLOCKED_INVALID_VERDICT, None
        return True, "VERDICT_VALID", {
            "status": "APPROVED",
            "reviewed_sha": candidate_sha,
            "review_snapshot_digest": expected_snapshot_digest,
            "findings": [],
        }

    if status != "REJECTED" or not isinstance(findings, list) or not findings:
        return False, BLOCKED_INVALID_VERDICT, None

    normalized: list[dict[str, Any]] = []
    for finding in findings:
        item = _normalize_finding(finding)
        if item is None:
            return False, BLOCKED_INVALID_VERDICT, None
        normalized.append(item)

    return True, "VERDICT_VALID", {
        "status": "REJECTED",
        "reviewed_sha": candidate_sha,
        "review_snapshot_digest": expected_snapshot_digest,
        "findings": normalized,
    }


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


def _common_identity(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    normalized = normalize_review_snapshot(snapshot)
    return {
        "schema_version": 1,
        "task_id": normalized["task_id"],
        "repository": normalized["repository"],
        "pr_number": normalized["pr_number"],
        "base_sha": normalized["base_sha"],
        "reviewed_sha": normalized["candidate_sha"],
        "reviewer_context_version": normalized["reviewer_context_version"],
        "reviewer_model": EXACT_REVIEWER_MODEL,
        "review_identity_digest": sha256_json(normalized),
    }


def _finalize_result(result: Mapping[str, Any]) -> dict[str, Any]:
    clean = sanitize_object(dict(result))
    digest_basis = dict(clean)
    digest_basis.pop("result_digest", None)
    digest_basis.pop("idempotency_key", None)
    result_digest = sha256_json(digest_basis)
    identity_digest = clean.get("review_identity_digest")
    if not isinstance(identity_digest, str) or not HEX64_RE.fullmatch(identity_digest):
        raise ValueError("invalid review_identity_digest")
    clean["result_digest"] = result_digest
    clean["idempotency_key"] = hashlib.sha256(
        f"{identity_digest}\n{result_digest}".encode("utf-8")
    ).hexdigest()
    return clean


def blocked_result(
    expected_snapshot: Mapping[str, Any],
    status: str,
    reason: str,
) -> dict[str, Any]:
    result = _common_identity(expected_snapshot)
    result.update(
        {
            "status": status,
            "reason": sanitize_text(reason, 1000),
            "findings": [],
        }
    )
    return _finalize_result(result)


def evaluate_review(
    *,
    expected_snapshot: Mapping[str, Any],
    review_request: Mapping[str, Any],
    model_catalog: Mapping[str, Any] | None,
    auth_profile: Mapping[str, Any] | None,
    plan_allowance: Mapping[str, Any] | None,
    verdict_payload: Mapping[str, Any] | None,
    current_attempt: int = 0,
    max_retries: int = 3,
) -> dict[str, Any]:
    """Evaluate a future live result without performing any live reviewer call."""
    expected = normalize_review_snapshot(expected_snapshot)

    request_ok, request_status = validate_review_request(expected, review_request)
    if not request_ok:
        return blocked_result(expected, BLOCKED_EVIDENCE_MISMATCH, request_status)

    auth_ok, auth_status = validate_auth_profile(auth_profile)
    if not auth_ok:
        return blocked_result(expected, auth_status, "host-local authorization metadata is unavailable or invalid")

    model_ok, model_status = validate_model_catalog(model_catalog)
    if not model_ok:
        return blocked_result(expected, model_status, "exact gpt-6-astra is not present in the authorized model catalog")

    allowance_ok, allowance_status = validate_plan_allowance(plan_allowance)
    if not allowance_ok:
        return blocked_result(expected, allowance_status, "zero-extra-spend plan allowance is unavailable")

    snapshot_digest = sha256_json(expected)
    verdict_ok, verdict_status, verdict = validate_verdict(
        verdict_payload,
        expected["candidate_sha"],
        snapshot_digest,
    )
    if not verdict_ok or verdict is None:
        return blocked_result(expected, verdict_status, "reviewer verdict is malformed or bound to stale evidence")

    result = _common_identity(expected)
    result.update(
        {
            "status": verdict["status"],
            "findings": verdict["findings"],
        }
    )

    if verdict["status"] == "APPROVED":
        result["approval_confirmation"] = {
            "exact_sha_bound": True,
            "exact_snapshot_bound": True,
            "merge_allowed": False,
            "auto_merge_allowed": False,
        }
    else:
        result["correction_package"] = build_correction_package(
            expected["task_id"],
            expected["candidate_sha"],
            verdict["findings"],
            current_attempt,
            max_retries,
        )

    return _finalize_result(result)


def validate_emitted_result(result: Mapping[str, Any]) -> tuple[bool, str]:
    """Runtime invariant mirror of REVIEW_CONTRACT.schema.json."""
    if not isinstance(result, Mapping):
        return False, "not object"
    common_required = {
        "schema_version",
        "task_id",
        "repository",
        "pr_number",
        "base_sha",
        "reviewed_sha",
        "reviewer_context_version",
        "reviewer_model",
        "review_identity_digest",
        "result_digest",
        "idempotency_key",
        "status",
        "findings",
    }
    if not common_required.issubset(result.keys()):
        return False, "missing common field"
    if result.get("schema_version") != 1:
        return False, "schema_version"
    if not TASK_RE.fullmatch(str(result.get("task_id", ""))):
        return False, "task_id"
    if not SHA_RE.fullmatch(str(result.get("base_sha", ""))) or not SHA_RE.fullmatch(str(result.get("reviewed_sha", ""))):
        return False, "sha"
    if result.get("reviewer_model") != EXACT_REVIEWER_MODEL:
        return False, "reviewer_model"
    if not HEX64_RE.fullmatch(str(result.get("review_identity_digest", ""))):
        return False, "identity digest"
    if not HEX64_RE.fullmatch(str(result.get("result_digest", ""))):
        return False, "result digest"
    if not HEX64_RE.fullmatch(str(result.get("idempotency_key", ""))):
        return False, "idempotency key"

    status = result.get("status")
    blocked = {
        BLOCKED_AUTH_REQUIRED,
        BLOCKED_NO_ASTRA,
        BLOCKED_PLAN_ALLOWANCE,
        BLOCKED_EVIDENCE_MISMATCH,
        BLOCKED_INVALID_VERDICT,
    }
    if status in blocked:
        if not _nonblank_string(result.get("reason")):
            return False, "blocked reason"
        if result.get("findings") != []:
            return False, "blocked findings"
        if "approval_confirmation" in result or "correction_package" in result:
            return False, "blocked side payload"
        return True, "VALID"

    if status == "APPROVED":
        if result.get("findings") != []:
            return False, "approval findings"
        approval = result.get("approval_confirmation")
        if not isinstance(approval, Mapping):
            return False, "approval confirmation"
        if approval != {
            "exact_sha_bound": True,
            "exact_snapshot_bound": True,
            "merge_allowed": False,
            "auto_merge_allowed": False,
        }:
            return False, "approval confirmation values"
        if "correction_package" in result or "reason" in result:
            return False, "approval extra status payload"
        return True, "VALID"

    if status == "REJECTED":
        if not isinstance(result.get("findings"), list) or not result["findings"]:
            return False, "rejection findings"
        if not isinstance(result.get("correction_package"), Mapping):
            return False, "correction package"
        if "approval_confirmation" in result or "reason" in result:
            return False, "rejection extra status payload"
        return True, "VALID"

    return False, "unsupported status"


def deduplicate_result(
    result: Mapping[str, Any],
    existing_records: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Deduplicate exact deliveries and explicitly block conflicting results."""
    valid, why = validate_emitted_result(result)
    if not valid:
        raise ValueError(f"invalid result: {why}")

    identity = result["review_identity_digest"]
    result_digest = result["result_digest"]
    for record in existing_records:
        if not isinstance(record, Mapping):
            raise ValueError("existing record must be an object")
        if record.get("review_identity_digest") != identity:
            continue
        if record.get("result_digest") == result_digest:
            return {
                "action": "DUPLICATE_NOOP",
                "review_identity_digest": identity,
                "result_digest": result_digest,
                "idempotency_key": result["idempotency_key"],
                "reviewed_sha": result["reviewed_sha"],
                "status": result["status"],
            }
        return {
            "action": "CONFLICT_BLOCKED",
            "review_identity_digest": identity,
            "existing_result_digest": record.get("result_digest"),
            "incoming_result_digest": result_digest,
            "reviewed_sha": result["reviewed_sha"],
            "status": result["status"],
        }

    return {
        "action": "PERSIST_ONCE",
        "review_identity_digest": identity,
        "result_digest": result_digest,
        "idempotency_key": result["idempotency_key"],
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
        "conversation_access": False,
        "merge_allowed": False,
        "required_model": EXACT_REVIEWER_MODEL,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    print(json.dumps(self_check(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
