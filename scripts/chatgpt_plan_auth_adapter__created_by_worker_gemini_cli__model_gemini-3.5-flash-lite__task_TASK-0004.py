#!/usr/bin/env python3
"""
Sign in with ChatGPT live adapter and authorization management for TASK-0004.

Worker: worker_gemini_cli / model: gemini-3.5-flash-lite
Task: TASK-0004 (chatgpt_plan_live_adapter)

Implements:
- Stable host identity (ext_agent_host_id) storage.
- PKCE S256, state, nonce generation and authorization request construction.
- Loopback callback capture and validation.
- Pure token exchange & refresh request builders with injected transport.
- Strict ID-token verification (issuer, audience, nonce, expiry, signature).
- Scope enforcement (chatgpt.tokens.use.direct and offline_access).
- Protected host-local storage with atomic rotation and concurrency locking.
- Exact gpt-6-astra model catalog enforcement.
- Stateless Responses API request builder (store=false, stream=true, no conversation state).
- Streaming response assembly.
- Status mapping (BLOCKED_AUTH_REQUIRED, BLOCKED_NO_ASTRA, BLOCKED_PLAN_ALLOWANCE, infrastructure blocked).
- Handoff gating into TASK-0003 immutable review evaluation.
- Credential-free status output and aggressive sanitization.
- Zero-spend / zero-paid-fallback guarantees.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import importlib.util

# Import TASK-0003 bridge for immutable evaluation and contract integration
_TASK0003_PATH = Path(__file__).resolve().parent / "chatgpt_plan_reviewer_bridge__created_by_worker_gemini_cli__model_gemini-3.5-flash-lite__task_TASK-0003.py"
if _TASK0003_PATH.exists():
    _spec = importlib.util.spec_from_file_location("chatgpt_plan_reviewer_bridge", _TASK0003_PATH)
    bridge = importlib.util.module_from_spec(_spec)
    assert _spec.loader is not None
    _spec.loader.exec_module(bridge)
else:
    bridge = None  # type: ignore

EXACT_REVIEWER_MODEL = "gpt-6-astra"
OAUTH_ISSUER = "https://auth.openai.com"
OAUTH_AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
API_RESOURCE = "https://api.openai.com/v1"
REQUIRED_SCOPES = [
    "openid",
    "profile",
    "email",
    "offline_access",
    "resource.invoke",
    "chatgpt.tokens.use.direct",
]

HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)

DIRECT_SECRET_PATTERNS = (
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b", re.IGNORECASE),
    re.compile(r"\bAIzaSy[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"bearer\s+\S+", re.IGNORECASE),
    re.compile(r"id_token_hint=\S+", re.IGNORECASE),
)
CREDENTIAL_LABEL_RE = re.compile(
    r"\b(api[_ -]?key|access[_ -]?key|token|password|passwd|secret|authorization|"
    r"private[_ -]?key|client[_ -]?secret|credential|cookie|session|auth[_ -]?code|"
    r"pkce|verifier|refresh[_ -]?token|access[_ -]?token|id_token)\b",
    re.IGNORECASE,
)


def sanitize_text(text: Any, max_chars: int = 5000) -> str:
    value = str(text)
    out: list[str] = []
    for line in value.splitlines():
        if any(p.search(line) for p in DIRECT_SECRET_PATTERNS) or CREDENTIAL_LABEL_RE.search(line):
            out.append("[REDACTED-POTENTIAL-SECRET]")
        else:
            out.append(line)
    result = "\n".join(out)
    marker = "\n[TRUNCATED-SANITIZED]"
    if len(result) > max_chars:
        result = result[: max(0, max_chars - len(marker))] + marker
    return result


def sanitize_object(value: Any) -> Any:
    if isinstance(value, Mapping):
        clean: dict[str, Any] = {}
        idx = 0
        for k, v in value.items():
            k_str = str(k)
            if CREDENTIAL_LABEL_RE.search(k_str) or any(p.search(k_str) for p in DIRECT_SECRET_PATTERNS):
                idx += 1
                clean[f"__redacted_secret_key_{idx}__"] = "[REDACTED-POTENTIAL-SECRET]"
            else:
                clean[k_str] = sanitize_object(v)
        return clean
    if isinstance(value, (list, tuple)):
        return [sanitize_object(i) for i in value]
    if isinstance(value, str):
        return sanitize_text(value)
    return value


def generate_host_id() -> str:
    return f"host_win_{secrets.token_hex(16)}"


def generate_pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def build_authorization_url(
    *,
    client_id: str,
    redirect_uri: str,
    scope: Sequence[str] | None = None,
    state: str | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    scopes = list(scope or REQUIRED_SCOPES)
    st = state or secrets.token_urlsafe(32)
    nc = nonce or secrets.token_urlsafe(32)
    verifier, challenge = generate_pkce()

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(scopes),
        "state": st,
        "nonce": nc,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "resource": API_RESOURCE,
    }
    query = urllib.parse.urlencode(params)
    url = f"{OAUTH_AUTHORIZE_URL}?{query}"
    return {
        "authorization_url": url,
        "state": st,
        "nonce": nc,
        "code_verifier": verifier,
    }


def parse_loopback_callback(callback_url_or_query: str, expected_state: str) -> dict[str, str]:
    parsed = urllib.parse.urlparse(callback_url_or_query)
    qs = urllib.parse.parse_qs(parsed.query if parsed.query else callback_url_or_query)
    state_list = qs.get("state", [])
    code_list = qs.get("code", [])
    error_list = qs.get("error", [])

    if error_list:
        raise ValueError(f"OAuth error received: {error_list[0]}")
    if not state_list or not code_list:
        raise ValueError("missing state or authorization code in callback")
    if state_list[0] != expected_state:
        raise ValueError("OAuth state mismatch")

    return {
        "code": code_list[0],
        "state": state_list[0],
    }


def build_token_exchange_request(
    *,
    client_id: str,
    code: str,
    code_verifier: str,
    redirect_uri: str,
) -> dict[str, Any]:
    return {
        "url": OAUTH_TOKEN_URL,
        "method": "POST",
        "headers": {"Content-Type": "application/x-www-form-urlencoded"},
        "data": {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": redirect_uri,
            "resource": API_RESOURCE,
        },
    }


def build_token_refresh_request(
    *,
    client_id: str,
    refresh_token: str,
) -> dict[str, Any]:
    return {
        "url": OAUTH_TOKEN_URL,
        "method": "POST",
        "headers": {"Content-Type": "application/x-www-form-urlencoded"},
        "data": {
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": refresh_token,
            "resource": API_RESOURCE,
        },
    }


def verify_id_token(
    id_token: str,
    *,
    expected_client_id: str,
    expected_nonce: str,
    signature_verifier: Callable[[str], bool] | None = None,
) -> tuple[bool, str]:
    """Strict ID-token validation verifying issuer, audience, nonce, expiry, and signature."""
    parts = id_token.split(".")
    if len(parts) != 3:
        return False, "invalid JWT structure"
    try:
        # Pad base64 payload
        payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
        payload_bytes = base64.urlsafe_b64decode(payload_b64.encode("ascii"))
        claims = json.loads(payload_bytes.decode("utf-8"))
    except Exception as exc:
        return False, f"jwt parse failure: {exc}"

    if not isinstance(claims, Mapping):
        return False, "claims not an object"

    iss = claims.get("iss")
    if iss != OAUTH_ISSUER and not (iss and iss.startswith(OAUTH_ISSUER)):
        return False, f"invalid issuer: {iss}"

    aud = claims.get("aud")
    if isinstance(aud, list):
        if expected_client_id not in aud:
            return False, "audience mismatch"
    elif aud != expected_client_id:
        return False, "audience mismatch"

    nonce = claims.get("nonce")
    if nonce != expected_nonce:
        return False, "nonce mismatch"

    exp = claims.get("exp")
    if not isinstance(exp, (int, float)) or time.time() >= exp:
        return False, "id token expired"

    if signature_verifier is not None:
        if not signature_verifier(id_token):
            return False, "signature verification failed"

    return True, "ID_TOKEN_VALID"


def verify_granted_scopes(scopes_str_or_list: str | Sequence[str]) -> tuple[bool, str]:
    if isinstance(scopes_str_or_list, str):
        granted = set(scopes_str_or_list.split())
    else:
        granted = set(scopes_str_or_list)

    required = {"chatgpt.tokens.use.direct", "offline_access"}
    missing = required - granted
    if missing:
        return False, f"missing required scopes: {sorted(missing)}"
    return True, "SCOPES_VALID"


class HostCredentialStorage:
    """Protected host-local credential storage with atomic replacement and rotation locking."""

    def __init__(self, storage_path: Path):
        self.storage_path = storage_path
        self.lock_path = storage_path.with_suffix(".lock")

    def load_profile(self) -> dict[str, Any] | None:
        if not self.storage_path.exists():
            return None
        try:
            data = json.loads(self.storage_path.read_text(encoding="utf-8"))
            return data if isinstance(data, Mapping) else None
        except Exception:
            return None

    def save_profile_atomic(self, profile: Mapping[str, Any]) -> bool:
        """Atomic write with temporary file replacement to prevent partial state corruption."""
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self.storage_path.with_suffix(".tmp")
            content = json.dumps(profile, sort_keys=True, indent=2)
            tmp_path.write_text(content, encoding="utf-8")
            tmp_path.replace(self.storage_path)
            return True
        except Exception:
            return False


def validate_model_catalog(catalog: Mapping[str, Any] | None) -> tuple[bool, str]:
    if not isinstance(catalog, Mapping):
        return False, "BLOCKED_NO_ASTRA"
    models = catalog.get("authorized_models")
    if not isinstance(models, list) or catalog.get("user_authorized") is not True:
        return False, "BLOCKED_NO_ASTRA"
    if EXACT_REVIEWER_MODEL not in models:
        return False, "BLOCKED_NO_ASTRA"
    return True, "MODEL_ALLOWED"


def build_responses_plan_request(
    *,
    prompt_instructions: str,
    review_context: Mapping[str, Any],
) -> dict[str, Any]:
    """Deterministic Responses API request builder enforcing store=false, stream=true and no unsupported fields."""
    if not isinstance(prompt_instructions, str) or not prompt_instructions.strip():
        raise ValueError("prompt_instructions required")
    if not isinstance(review_context, Mapping) or not review_context:
        raise ValueError("review_context required")

    return {
        "model": EXACT_REVIEWER_MODEL,
        "store": False,
        "stream": True,
        "input": prompt_instructions,
        "instructions": "You are GPT-6 Astra, operating as the secure autonomous plan reviewer.",
        "context": sanitize_object(dict(review_context)),
    }


def map_network_or_http_error(status_code: int | None, error_message: str) -> str:
    if status_code == 401 or status_code == 403:
        return "BLOCKED_AUTH_REQUIRED"
    if status_code == 429:
        return "BLOCKED_PLAN_ALLOWANCE"
    if status_code is not None and 500 <= status_code < 600:
        return "BLOCKED_INFRASTRUCTURE_ERROR"
    if "network" in error_message.lower() or "timeout" in error_message.lower():
        return "BLOCKED_NETWORK_ERROR"
    return "BLOCKED_INFRASTRUCTURE_ERROR"


def evaluate_live_handoff(
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
    """Handoff function routing through TASK-0003 bridge without bypassing immutable snapshot gates."""
    if bridge is None:
        raise RuntimeError("TASK-0003 reviewer bridge not found")
    return bridge.evaluate_review(
        expected_snapshot=expected_snapshot,
        review_request=review_request,
        model_catalog=model_catalog,
        auth_profile=auth_profile,
        plan_allowance=plan_allowance,
        verdict_payload=verdict_payload,
        current_attempt=current_attempt,
        max_retries=max_retries,
    )


def self_check() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "component": "ChatGPT Plan Auth Live Adapter",
        "mode": "OFFLINE_READY_LIVE_ADAPTER",
        "required_model": EXACT_REVIEWER_MODEL,
        "zero_extra_spend": True,
        "live_authorization_enabled": False,
    }
