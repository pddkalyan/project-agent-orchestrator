#!/usr/bin/env python3
"""TASK-0004 Sign in with ChatGPT live adapter.

Worker origin: Gemini 3.5 Flash Lite.
Recovery hardening: GPT-5.6 Sol.

This module is safe to import and test offline. Network operations are isolated
behind explicit transport objects. Real browser consent is invoked only by the
Windows CLI after the user runs the sign-in command.
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import importlib.util
import json
import os
import re
import secrets
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence

TASK0003_PATH = Path(__file__).resolve().parent / "chatgpt_plan_reviewer_bridge__created_by_worker_gemini_cli__model_gemini-3.5-flash-lite__task_TASK-0003.py"
if TASK0003_PATH.exists():
    _spec = importlib.util.spec_from_file_location("task0003_bridge", TASK0003_PATH)
    bridge = importlib.util.module_from_spec(_spec)
    assert _spec.loader is not None
    _spec.loader.exec_module(bridge)
else:
    bridge = None

ISSUER = "https://auth.openai.com"
AUTHORIZE_URL = "https://auth.openai.com/api/accounts/authorize"
TOKEN_URL = "https://auth.openai.com/api/accounts/oauth/token"
JWKS_URL = "https://auth.openai.com/.well-known/jwks.json"
MODELS_URL = "https://api.openai.com/v1/models"
RESPONSES_URL = "https://api.openai.com/v1/responses"
RESOURCE = "https://api.openai.com/v1"
DYNAMIC_CLIENT_ID = "dynamic_agent_client"
EXACT_REVIEWER_MODEL = "gpt-6-astra"
AGENT_NAME = "Project Agent Orchestrator"

REQUIRED_SCOPES = (
    "openid",
    "profile",
    "email",
    "offline_access",
    "resource.invoke",
    "chatgpt.tokens.use.direct",
)

BLOCKED_AUTH_REQUIRED = "BLOCKED_AUTH_REQUIRED"
BLOCKED_NO_ASTRA = "BLOCKED_NO_ASTRA"
BLOCKED_PLAN_ALLOWANCE = "BLOCKED_PLAN_ALLOWANCE"
BLOCKED_NETWORK_ERROR = "BLOCKED_NETWORK_ERROR"
BLOCKED_INFRASTRUCTURE_ERROR = "BLOCKED_INFRASTRUCTURE_ERROR"
BLOCKED_INVALID_RESPONSE = "BLOCKED_INVALID_RESPONSE"

SECRET_KEY_RE = re.compile(
    r"(access[_-]?token|refresh[_-]?token|id[_-]?token|authorization|cookie|"
    r"code[_-]?verifier|pkce|client[_-]?secret|api[_-]?key|password|secret)",
    re.I,
)
DIRECT_SECRET_PATTERNS = (
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"bearer\s+\S+", re.I),
    re.compile(r"id_token_hint=[^&\s]+", re.I),
)


def sanitize_text(value: Any, max_chars: int = 4000) -> str:
    text = str(value)
    out: list[str] = []
    for line in text.splitlines():
        if SECRET_KEY_RE.search(line) or any(p.search(line) for p in DIRECT_SECRET_PATTERNS):
            out.append("[REDACTED-POTENTIAL-SECRET]")
        else:
            out.append(line)
    result = "\n".join(out)
    if len(result) > max_chars:
        result = result[: max_chars - 22] + "\n[TRUNCATED-SANITIZED]"
    return result


def sanitize_object(value: Any) -> Any:
    if isinstance(value, Mapping):
        clean: dict[str, Any] = {}
        redacted = 0
        for key, item in value.items():
            key_text = str(key)
            if SECRET_KEY_RE.search(key_text) or any(p.search(key_text) for p in DIRECT_SECRET_PATTERNS):
                redacted += 1
                clean[f"__redacted_secret_key_{redacted}__"] = "[REDACTED-POTENTIAL-SECRET]"
            else:
                clean[key_text] = sanitize_object(item)
        return clean
    if isinstance(value, list):
        return [sanitize_object(x) for x in value]
    if isinstance(value, tuple):
        return [sanitize_object(x) for x in value]
    if isinstance(value, str):
        return sanitize_text(value)
    return value


def generate_host_id() -> str:
    return f"urn:uuid:{uuid.uuid4()}"


def validate_host_id(host_id: str) -> bool:
    if not isinstance(host_id, str) or not host_id.startswith("urn:uuid:"):
        return False
    try:
        uuid.UUID(host_id.removeprefix("urn:uuid:"))
        return True
    except ValueError:
        return False


def generate_pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    return verifier, challenge


def new_authorization_attempt() -> dict[str, str]:
    verifier, challenge = generate_pkce()
    return {
        "state": secrets.token_urlsafe(32),
        "nonce": secrets.token_urlsafe(32),
        "code_verifier": verifier,
        "code_challenge": challenge,
    }


def validate_loopback_redirect_uri(redirect_uri: str) -> bool:
    parsed = urllib.parse.urlparse(redirect_uri)
    return (
        parsed.scheme == "http"
        and parsed.hostname == "127.0.0.1"
        and parsed.path == "/auth/callback"
        and parsed.port is not None
        and not parsed.username
        and not parsed.password
        and not parsed.query
        and not parsed.fragment
    )


def build_authorization_url(
    *,
    redirect_uri: str,
    ext_agent_host_id: str,
    attempt: Mapping[str, str],
    issued_client_id: str | None = None,
    retained_id_token: str | None = None,
    login_hint: str | None = None,
    agent_name: str = AGENT_NAME,
) -> str:
    if not validate_loopback_redirect_uri(redirect_uri):
        raise ValueError("redirect_uri must be http://127.0.0.1:<port>/auth/callback")
    if not validate_host_id(ext_agent_host_id):
        raise ValueError("invalid ext_agent_host_id")
    for key in ("state", "nonce", "code_challenge"):
        if not isinstance(attempt.get(key), str) or not attempt[key]:
            raise ValueError(f"missing authorization attempt field: {key}")

    first_registration = not issued_client_id
    client_id = DYNAMIC_CLIENT_ID if first_registration else issued_client_id
    params = {
        "client_id": client_id,
        "ext_agent_host_id": ext_agent_host_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": " ".join(REQUIRED_SCOPES),
        "resource": RESOURCE,
        "state": attempt["state"],
        "nonce": attempt["nonce"],
        "code_challenge_method": "S256",
        "code_challenge": attempt["code_challenge"],
    }
    if first_registration:
        params["agent_name_hint"] = agent_name
    else:
        if retained_id_token:
            params["id_token_hint"] = retained_id_token
        if login_hint:
            params["login_hint"] = login_hint
    return AUTHORIZE_URL + "?" + urllib.parse.urlencode(params)


def parse_loopback_callback(
    callback_url_or_query: str,
    *,
    expected_state: str,
    expected_client_id: str | None,
    is_new_registration: bool,
) -> dict[str, Any]:
    parsed = urllib.parse.urlparse(callback_url_or_query)
    query = parsed.query if parsed.query else callback_url_or_query.lstrip("?")
    qs = urllib.parse.parse_qs(query, keep_blank_values=True)
    state = (qs.get("state") or [""])[0]
    if not secrets.compare_digest(state, expected_state):
        raise ValueError("OAuth state mismatch")
    error = (qs.get("error") or [""])[0]
    if error:
        raise ValueError("OAuth authorization declined or failed")
    code = (qs.get("code") or [""])[0]
    if not code:
        raise ValueError("callback missing authorization code")
    callback_client_id = (qs.get("client_id") or [""])[0] or None
    if is_new_registration:
        if not callback_client_id or callback_client_id == DYNAMIC_CLIENT_ID:
            raise ValueError("new registration callback missing issued client_id")
        client_id = callback_client_id
    else:
        if not expected_client_id:
            raise ValueError("returning sign-in missing expected client_id")
        if callback_client_id and callback_client_id != expected_client_id:
            raise ValueError("callback client_id mismatch")
        client_id = expected_client_id
    return {
        "code": code,
        "state": state,
        "client_id": client_id,
        "scope": (qs.get("scope") or [""])[0],
    }


def build_token_exchange_request(*, client_id: str, code: str, code_verifier: str, redirect_uri: str) -> dict[str, Any]:
    if client_id == DYNAMIC_CLIENT_ID:
        raise ValueError("token exchange requires issued client_id")
    if not validate_loopback_redirect_uri(redirect_uri):
        raise ValueError("invalid redirect_uri")
    return {
        "url": TOKEN_URL,
        "method": "POST",
        "data": {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": redirect_uri,
            "resource": RESOURCE,
        },
    }


def build_token_refresh_request(*, client_id: str, refresh_token: str) -> dict[str, Any]:
    if not client_id or client_id == DYNAMIC_CLIENT_ID:
        raise ValueError("refresh requires issued client_id")
    if not refresh_token:
        raise ValueError("refresh_token required")
    return {
        "url": TOKEN_URL,
        "method": "POST",
        "data": {
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": refresh_token,
            "resource": RESOURCE,
        },
    }


def verify_granted_scopes(scopes: str | Sequence[str]) -> tuple[bool, str]:
    granted = set(scopes.split() if isinstance(scopes, str) else scopes)
    required = {"offline_access", "chatgpt.tokens.use.direct"}
    missing = sorted(required - granted)
    if missing:
        return False, "missing required scopes: " + ",".join(missing)
    return True, "SCOPES_VALID"


def validate_verified_id_claims(
    claims: Mapping[str, Any],
    *,
    expected_client_id: str,
    expected_nonce: str,
    now: float | None = None,
) -> tuple[bool, str]:
    current = time.time() if now is None else now
    if claims.get("iss") != ISSUER:
        return False, "issuer mismatch"
    aud = claims.get("aud")
    if isinstance(aud, list):
        if expected_client_id not in aud:
            return False, "audience mismatch"
    elif aud != expected_client_id:
        return False, "audience mismatch"
    if claims.get("nonce") != expected_nonce:
        return False, "nonce mismatch"
    if not isinstance(claims.get("sub"), str) or not claims["sub"]:
        return False, "subject missing"
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)) or current >= exp:
        return False, "id token expired"
    return True, "ID_TOKEN_VALID"


def verify_id_token(
    id_token: str,
    *,
    expected_client_id: str,
    expected_nonce: str,
    signature_verifier: Callable[[str], Mapping[str, Any]],
) -> tuple[bool, str, Mapping[str, Any] | None]:
    if signature_verifier is None:
        return False, "signature verifier required", None
    try:
        claims = signature_verifier(id_token)
    except Exception:
        return False, "signature verification failed", None
    if not isinstance(claims, Mapping):
        return False, "signature verifier returned invalid claims", None
    valid, reason = validate_verified_id_claims(
        claims,
        expected_client_id=expected_client_id,
        expected_nonce=expected_nonce,
    )
    return valid, reason, claims if valid else None


class PyJWTSignatureVerifier:
    """Live JWKS verifier. PyJWT[crypto] is loaded only for explicit live sign-in."""

    def __init__(self, jwks_url: str = JWKS_URL):
        self.jwks_url = jwks_url

    def __call__(self, token: str) -> Mapping[str, Any]:
        try:
            import jwt  # type: ignore
        except ImportError as exc:
            raise RuntimeError("Live sign-in requires PyJWT[crypto]. Install: py -m pip install 'PyJWT[crypto]'") from exc
        jwks_client = jwt.PyJWKClient(self.jwks_url)
        key = jwks_client.get_signing_key_from_jwt(token)
        # Audience/issuer are validated again by validate_verified_id_claims; disable
        # them here only so one function owns the expected runtime values.
        return jwt.decode(
            token,
            key.key,
            algorithms=["RS256"],
            options={"verify_aud": False, "verify_iss": False},
        )


class SecretProtector:
    def protect(self, plaintext: bytes) -> bytes:
        raise NotImplementedError

    def unprotect(self, ciphertext: bytes) -> bytes:
        raise NotImplementedError


class WindowsDPAPIProtector(SecretProtector):
    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    def __init__(self):
        if os.name != "nt":
            raise RuntimeError("Windows DPAPI protector requires Windows")

    @staticmethod
    def _blob(data: bytes):
        buf = ctypes.create_string_buffer(data)
        blob = WindowsDPAPIProtector.DATA_BLOB(
            len(data),
            ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)),
        )
        return blob, buf

    def _crypt(self, data: bytes, decrypt: bool) -> bytes:
        in_blob, _buf = self._blob(data)
        out_blob = self.DATA_BLOB()
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        if decrypt:
            ok = crypt32.CryptUnprotectData(
                ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)
            )
        else:
            ok = crypt32.CryptProtectData(
                ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)
            )
        if not ok:
            raise OSError("Windows DPAPI operation failed")
        try:
            return ctypes.string_at(out_blob.pbData, out_blob.cbData)
        finally:
            kernel32.LocalFree(out_blob.pbData)

    def protect(self, plaintext: bytes) -> bytes:
        return self._crypt(plaintext, False)

    def unprotect(self, ciphertext: bytes) -> bytes:
        return self._crypt(ciphertext, True)


class FileLock:
    _process_lock = threading.Lock()

    def __init__(self, path: Path):
        self.path = path
        self.handle = None

    def __enter__(self):
        self._process_lock.acquire()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = open(self.path, "a+b")
        if os.name == "nt":
            import msvcrt
            self.handle.seek(0)
            if self.handle.tell() == 0:
                self.handle.write(b"0")
                self.handle.flush()
            self.handle.seek(0)
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if self.handle is not None:
                if os.name == "nt":
                    import msvcrt
                    self.handle.seek(0)
                    msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
                self.handle.close()
        finally:
            self._process_lock.release()


class HostCredentialStorage:
    """Host-local encrypted profile store with atomic replacement and serialized refresh."""

    SECRET_FIELDS = {"access_token", "refresh_token", "id_token"}

    def __init__(self, storage_path: Path, protector: SecretProtector):
        self.storage_path = storage_path
        self.lock_path = storage_path.with_suffix(storage_path.suffix + ".lock")
        self.protector = protector

    @contextmanager
    def locked(self) -> Iterator[None]:
        with FileLock(self.lock_path):
            yield

    def _encode(self, profile: Mapping[str, Any]) -> dict[str, Any]:
        public = {k: v for k, v in profile.items() if k not in self.SECRET_FIELDS}
        secret = {k: profile[k] for k in self.SECRET_FIELDS if k in profile}
        ciphertext = self.protector.protect(json.dumps(secret, sort_keys=True).encode("utf-8"))
        return {
            "schema_version": 1,
            "public": public,
            "protected_secret_blob": base64.b64encode(ciphertext).decode("ascii"),
        }

    def _decode(self, envelope: Mapping[str, Any]) -> dict[str, Any]:
        public = envelope.get("public")
        blob = envelope.get("protected_secret_blob")
        if not isinstance(public, Mapping) or not isinstance(blob, str):
            raise ValueError("invalid credential envelope")
        secret = json.loads(self.protector.unprotect(base64.b64decode(blob)).decode("utf-8"))
        if not isinstance(secret, Mapping):
            raise ValueError("invalid protected credential payload")
        profile = dict(public)
        profile.update(secret)
        return profile

    def load_profile(self) -> dict[str, Any] | None:
        if not self.storage_path.exists():
            return None
        envelope = json.loads(self.storage_path.read_text(encoding="utf-8"))
        return self._decode(envelope)

    def save_profile_atomic(self, profile: Mapping[str, Any]) -> None:
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        envelope = self._encode(profile)
        fd, temp_name = tempfile.mkstemp(prefix=self.storage_path.name + ".", suffix=".tmp", dir=str(self.storage_path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as tmp:
                json.dump(envelope, tmp, sort_keys=True, indent=2)
                tmp.flush()
                os.fsync(tmp.fileno())
            os.replace(temp_name, self.storage_path)
            if os.name != "nt":
                os.chmod(self.storage_path, 0o600)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def safe_status(self) -> dict[str, Any]:
        profile = self.load_profile()
        if not profile:
            return {"status": BLOCKED_AUTH_REQUIRED, "connected": False}
        return {
            "status": "CONNECTED" if "access_token" in profile else BLOCKED_AUTH_REQUIRED,
            "connected": "access_token" in profile,
            "profile_label": profile.get("profile_label"),
            "email": profile.get("email"),
            "client_id": profile.get("client_id"),
            "ext_agent_host_id": profile.get("ext_agent_host_id"),
            "scopes": profile.get("scopes", []),
            "expires_at": profile.get("expires_at"),
        }


class HttpTransport:
    """Explicit live transport; never used by CI tests unless replaced with a fake."""

    def post_form(self, url: str, data: Mapping[str, Any], headers: Mapping[str, str] | None = None) -> dict[str, Any]:
        body = urllib.parse.urlencode(data).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded", **dict(headers or {})},
        )
        return self._json(request)

    def get_json(self, url: str, headers: Mapping[str, str] | None = None) -> dict[str, Any]:
        return self._json(urllib.request.Request(url, headers=dict(headers or {})))

    def stream_sse(self, url: str, payload: Mapping[str, Any], headers: Mapping[str, str]) -> Iterator[dict[str, Any]]:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", **dict(headers)},
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                for raw in response:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    yield json.loads(data)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            raise RuntimeError(json.dumps({"http_status": exc.code, "body": sanitize_text(body)})) from None

    @staticmethod
    def _json(request: urllib.request.Request) -> dict[str, Any]:
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            raise RuntimeError(json.dumps({"http_status": exc.code, "body": sanitize_text(body)})) from None
        if not isinstance(payload, dict):
            raise RuntimeError("OpenAI endpoint returned non-object JSON")
        return payload


def normalize_token_response(payload: Mapping[str, Any], *, client_id: str, ext_agent_host_id: str, claims: Mapping[str, Any]) -> dict[str, Any]:
    required = ("access_token", "refresh_token", "id_token", "token_type", "expires_in", "scope")
    if any(not payload.get(k) for k in required):
        raise ValueError("token response missing required fields")
    ok, reason = verify_granted_scopes(str(payload["scope"]))
    if not ok:
        raise ValueError(reason)
    expires_in = int(payload["expires_in"])
    return {
        "profile_label": client_id,
        "email": claims.get("email"),
        "issuer": claims["iss"],
        "subject": claims["sub"],
        "client_id": client_id,
        "ext_agent_host_id": ext_agent_host_id,
        "id_token": payload["id_token"],
        "access_token": payload["access_token"],
        "refresh_token": payload["refresh_token"],
        "token_type": payload["token_type"],
        "expires_in": expires_in,
        "expires_at": int(time.time()) + expires_in,
        "earliest_refresh_at": payload.get("earliest_refresh_at"),
        "scopes": sorted(set(str(payload["scope"]).split())),
        "saved_at": int(time.time()),
    }


def refresh_profile(storage: HostCredentialStorage, transport: Any) -> dict[str, Any]:
    """Serialize refreshes and atomically replace the complete rotating token set."""
    with storage.locked():
        current = storage.load_profile()
        if not current:
            return {"status": BLOCKED_AUTH_REQUIRED}
        req = build_token_refresh_request(
            client_id=str(current.get("client_id", "")),
            refresh_token=str(current.get("refresh_token", "")),
        )
        try:
            payload = transport.post_form(req["url"], req["data"])
        except Exception as exc:
            text = sanitize_text(exc)
            return {"status": map_transport_error(text), "message": text}
        for key in ("access_token", "refresh_token", "id_token", "expires_in", "scope"):
            if key not in payload:
                return {"status": BLOCKED_AUTH_REQUIRED, "message": "refresh response incomplete"}
        updated = dict(current)
        updated.update(
            {
                "access_token": payload["access_token"],
                "refresh_token": payload["refresh_token"],
                "id_token": payload["id_token"],
                "expires_in": int(payload["expires_in"]),
                "expires_at": int(time.time()) + int(payload["expires_in"]),
                "earliest_refresh_at": payload.get("earliest_refresh_at"),
                "scopes": sorted(set(str(payload["scope"]).split())),
                "saved_at": int(time.time()),
            }
        )
        ok, _ = verify_granted_scopes(updated["scopes"])
        if not ok:
            return {"status": BLOCKED_AUTH_REQUIRED, "message": "required plan scopes missing after refresh"}
        storage.save_profile_atomic(updated)
        return {"status": "REFRESHED", "profile": storage.safe_status()}


def parse_model_catalog(payload: Mapping[str, Any]) -> dict[str, Any]:
    models = payload.get("models")
    if not isinstance(models, list):
        raise ValueError("model catalog missing models array")
    visible: list[str] = []
    for item in models:
        if not isinstance(item, Mapping):
            continue
        if item.get("visibility") == "list" and isinstance(item.get("slug"), str):
            visible.append(item["slug"])
    return {"user_authorized": True, "authorized_models": visible}


def validate_model_catalog(catalog: Mapping[str, Any] | None) -> tuple[bool, str]:
    if not isinstance(catalog, Mapping):
        return False, BLOCKED_NO_ASTRA
    models = catalog.get("authorized_models")
    if catalog.get("user_authorized") is not True or not isinstance(models, list):
        return False, BLOCKED_NO_ASTRA
    return (EXACT_REVIEWER_MODEL in models, "MODEL_ALLOWED" if EXACT_REVIEWER_MODEL in models else BLOCKED_NO_ASTRA)


def list_models(profile: Mapping[str, Any], transport: Any) -> dict[str, Any]:
    access_token = profile.get("access_token")
    if not access_token:
        return {"status": BLOCKED_AUTH_REQUIRED}
    try:
        payload = transport.get_json(MODELS_URL, {"Authorization": f"Bearer {access_token}"})
        catalog = parse_model_catalog(payload)
    except Exception as exc:
        return {"status": map_transport_error(sanitize_text(exc))}
    ok, status = validate_model_catalog(catalog)
    return {"status": status, "catalog": catalog, "astra_available": ok}


UNSUPPORTED_RESPONSE_FIELDS = {
    "previous_response_id",
    "conversation",
    "background",
    "metadata",
    "user",
    "temperature",
    "top_p",
    "max_output_tokens",
    "service_tier",
    "tool_search",
}


def build_responses_plan_request(*, review_prompt: str, review_context: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(review_prompt, str) or not review_prompt.strip():
        raise ValueError("review_prompt required")
    if not isinstance(review_context, Mapping) or not review_context:
        raise ValueError("review_context required")
    for key in review_context:
        if key in UNSUPPORTED_RESPONSE_FIELDS:
            raise ValueError(f"unsupported SIWC review context field: {key}")
    context_text = json.dumps(sanitize_object(dict(review_context)), sort_keys=True, separators=(",", ":"))
    return {
        "model": EXACT_REVIEWER_MODEL,
        "input": [
            {
                "role": "user",
                "content": review_prompt + "\n\nIMMUTABLE_REVIEW_CONTEXT_JSON:\n" + context_text,
            }
        ],
        "instructions": "You are GPT-6 Astra acting only as the exact-SHA project reviewer. Return the structured verdict requested by the review contract.",
        "store": False,
        "stream": True,
    }


def assemble_stream(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    text_parts: list[str] = []
    completed = False
    for event in events:
        event_type = event.get("type")
        if event_type == "response.output_text.delta":
            delta = event.get("delta")
            if isinstance(delta, str):
                text_parts.append(delta)
        elif event_type == "response.completed":
            completed = True
        elif event_type == "response.failed":
            error = event.get("response", {}).get("error") if isinstance(event.get("response"), Mapping) else None
            code = error.get("code") if isinstance(error, Mapping) else "unknown_error"
            return {"status": map_response_error_code(str(code)), "completed": False}
        elif event_type == "response.incomplete":
            return {"status": BLOCKED_INFRASTRUCTURE_ERROR, "completed": False}
    if not completed:
        return {"status": BLOCKED_INVALID_RESPONSE, "completed": False}
    return {"status": "COMPLETED", "completed": True, "text": "".join(text_parts)}


def map_response_error_code(code: str) -> str:
    if code in {
        "subscription_sharing_usage_limit_exceeded",
        "subscription_sharing_usage_unavailable",
    }:
        return BLOCKED_PLAN_ALLOWANCE
    if code in {
        "subscription_sharing_invalid_user",
        "chatpass_v2_scope_not_authorized",
        "chatpass_v2_invalid_authorization_context",
    }:
        return BLOCKED_AUTH_REQUIRED
    if code == "subscription_sharing_user_unavailable":
        return BLOCKED_INFRASTRUCTURE_ERROR
    if code in {
        "subscription_sharing_unsupported_capability",
        "subscription_sharing_route_not_supported",
    }:
        return BLOCKED_INVALID_RESPONSE
    return BLOCKED_INFRASTRUCTURE_ERROR


def map_http_status(status: int) -> str:
    if status in (401, 403):
        return BLOCKED_AUTH_REQUIRED
    if status == 429:
        return BLOCKED_PLAN_ALLOWANCE
    if status >= 500:
        return BLOCKED_INFRASTRUCTURE_ERROR
    return BLOCKED_INVALID_RESPONSE


def map_transport_error(message: str) -> str:
    lower = message.lower()
    if '"http_status": 401' in lower or '"http_status": 403' in lower:
        return BLOCKED_AUTH_REQUIRED
    if '"http_status": 429' in lower:
        return BLOCKED_PLAN_ALLOWANCE
    if "timeout" in lower or "network" in lower or "connection" in lower:
        return BLOCKED_NETWORK_ERROR
    if '"http_status": 5' in lower:
        return BLOCKED_INFRASTRUCTURE_ERROR
    return BLOCKED_INFRASTRUCTURE_ERROR


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
    if bridge is None:
        raise RuntimeError("TASK-0003 reviewer bridge not found")
    # Never forward credential-bearing live profile fields into TASK-0003.
    safe_auth = None
    if isinstance(auth_profile, Mapping):
        safe_auth = {
            "active": auth_profile.get("active"),
            "expired": auth_profile.get("expired"),
            "profile_id": auth_profile.get("profile_id"),
        }
    return bridge.evaluate_review(
        expected_snapshot=expected_snapshot,
        review_request=review_request,
        model_catalog=model_catalog,
        auth_profile=safe_auth,
        plan_allowance=plan_allowance,
        verdict_payload=verdict_payload,
        current_attempt=current_attempt,
        max_retries=max_retries,
    )


def self_check() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "component": "ChatGPT Plan Auth Adapter",
        "mode": "LIVE_CAPABLE_USER_INITIATED_ONLY",
        "required_model": EXACT_REVIEWER_MODEL,
        "zero_extra_spend": True,
        "api_key_path": False,
        "paid_fallback": False,
        "ci_live_authorization": False,
        "conversation_access": False,
        "local_inference": False,
        "merge_allowed": False,
    }
