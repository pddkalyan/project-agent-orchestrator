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
import math
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
    r"(access[_-]?token|refresh[_-]?token|id[_-]?token|authorization|auth[_-]?code|cookie|"
    r"code[_-]?verifier|pkce|client[_-]?secret|api[_-]?key|password|secret|private[_ -]?key)",
    re.I,
)
PRIVATE_KEY_BEGIN = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
PRIVATE_KEY_END = re.compile(r"-----END [A-Z0-9 ]*PRIVATE KEY-----")

DIRECT_SECRET_PATTERNS = (
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"bearer\s+\S+", re.I),
    re.compile(r"id_token_hint=[^&\s]+", re.I),
)


def _leading_spaces(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def sanitize_text(value: Any, max_chars: int = 4000) -> str:
    """Conservatively redact credential-bearing lines and their continuations."""
    text = str(value)
    out: list[str] = []
    in_private_key = False
    block_indent: int | None = None
    redact_until_blank = False
    for line in text.splitlines():
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
        if SECRET_KEY_RE.search(line) or any(p.search(line) for p in DIRECT_SECRET_PATTERNS):
            out.append("[REDACTED-POTENTIAL-SECRET]")
            if re.search(r"[:=]\s*[|>]", line):
                block_indent = indent
            elif re.search(r"[:=]\s*(?:#.*)?$", line):
                redact_until_blank = True
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
    required = set(REQUIRED_SCOPES)
    missing = sorted(required - granted)
    if missing:
        return False, "missing required scopes"
    return True, "SCOPES_VALID"


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


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
    if not _finite_number(exp) or current >= float(exp):
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
            leeway=5,
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
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
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
            kernel32.LocalFree(ctypes.cast(out_blob.pbData, ctypes.c_void_p))

    def protect(self, plaintext: bytes) -> bytes:
        return self._crypt(plaintext, False)

    def unprotect(self, ciphertext: bytes) -> bytes:
        return self._crypt(ciphertext, True)


class FileLock:
    _process_lock = threading.Lock()

    def __init__(self, path: Path, timeout_seconds: float = 10.0):
        self.path = path
        self.timeout_seconds = timeout_seconds
        self.handle = None
        self._process_acquired = False
        self._os_acquired = False

    def __enter__(self):
        deadline = time.monotonic() + self.timeout_seconds
        if not self._process_lock.acquire(timeout=self.timeout_seconds):
            raise TimeoutError("credential lock unavailable")
        self._process_acquired = True
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.handle = open(self.path, "a+b")
            if os.name == "nt":
                import msvcrt
                self.handle.seek(0, os.SEEK_END)
                if self.handle.tell() == 0:
                    self.handle.write(b"0")
                    self.handle.flush()
                while True:
                    try:
                        self.handle.seek(0)
                        msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
                        self._os_acquired = True
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise TimeoutError("credential lock unavailable")
                        time.sleep(0.05)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX)
                self._os_acquired = True
            return self
        except Exception:
            if self.handle is not None:
                try:
                    self.handle.close()
                finally:
                    self.handle = None
            if self._process_acquired:
                self._process_acquired = False
                self._process_lock.release()
            raise

    def __exit__(self, exc_type, exc, tb):
        try:
            if self.handle is not None:
                if self._os_acquired:
                    if os.name == "nt":
                        import msvcrt
                        self.handle.seek(0)
                        msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
                self.handle.close()
                self.handle = None
        finally:
            self._os_acquired = False
            if self._process_acquired:
                self._process_acquired = False
                self._process_lock.release()


class HostCredentialStorage:
    """Host-local encrypted profile store with atomic replacement and serialized refresh."""

    SECRET_FIELDS = {"access_token", "refresh_token", "id_token"}

    def __init__(self, storage_path: Path, protector: SecretProtector):
        self.storage_path = storage_path
        self.lock_path = storage_path.with_suffix(storage_path.suffix + ".lock")
        self.registration_path = storage_path.with_suffix(storage_path.suffix + ".registration.json")
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

    def save_registration_atomic(self, registration: Mapping[str, Any]) -> None:
        allowed = {"profile_label", "client_id", "ext_agent_host_id", "registration_pending", "subject"}
        public = {k: registration.get(k) for k in allowed if k in registration}
        self.registration_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=self.registration_path.name + ".", suffix=".tmp", dir=str(self.registration_path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as tmp:
                json.dump(public, tmp, sort_keys=True, indent=2)
                tmp.flush()
                os.fsync(tmp.fileno())
            os.replace(temp_name, self.registration_path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def load_registration(self) -> dict[str, Any] | None:
        if not self.registration_path.exists():
            return None
        value = json.loads(self.registration_path.read_text(encoding="utf-8"))
        if not isinstance(value, Mapping):
            raise ValueError("invalid registration metadata")
        return dict(value)

    def profile_version(self, profile: Mapping[str, Any] | None) -> int:
        value = (profile or {}).get("profile_version", 0)
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0

    def replace_profile_if_version(
        self,
        *,
        expected_version: int,
        profile: Mapping[str, Any],
        expected_subject: str | None = None,
        expected_client_id: str | None = None,
    ) -> bool:
        with self.locked():
            current = self.load_profile()
            if self.profile_version(current) != expected_version:
                return False
            if current and expected_subject and current.get("subject") not in (None, expected_subject):
                return False
            if current and expected_client_id and current.get("client_id") not in (None, expected_client_id):
                return False
            updated = dict(profile)
            updated["profile_version"] = expected_version + 1
            self.save_profile_atomic(updated)
            return True

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


class SafeTransportError(RuntimeError):
    """Public-safe transport failure carrying only allowlisted metadata."""

    def __init__(self, *, http_status: int | None = None, code: str | None = None, category: str = "transport"):
        super().__init__(category)
        self.http_status = http_status
        self.code = code
        self.category = category


def _safe_error_code_from_body(body: str) -> str | None:
    try:
        parsed = json.loads(body)
    except Exception:
        return None
    if not isinstance(parsed, Mapping):
        return None
    error = parsed.get("error")
    if isinstance(error, Mapping) and isinstance(error.get("code"), str):
        return error["code"][:120]
    if isinstance(error, str):
        return error[:120]
    return None


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
            raise SafeTransportError(http_status=exc.code, code=_safe_error_code_from_body(body), category="http") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise SafeTransportError(category="network") from None

    @staticmethod
    def _json(request: urllib.request.Request) -> dict[str, Any]:
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            raise SafeTransportError(http_status=exc.code, code=_safe_error_code_from_body(body), category="http") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise SafeTransportError(category="network") from None
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise SafeTransportError(category="invalid_response") from None
        if not isinstance(payload, dict):
            raise SafeTransportError(category="invalid_response")
        return payload


def _positive_lifetime(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("invalid token lifetime")
    if isinstance(value, int):
        result = value
    elif isinstance(value, str) and value.isdigit():
        result = int(value)
    else:
        raise ValueError("invalid token lifetime")
    if result <= 0 or result > 86400 * 30:
        raise ValueError("invalid token lifetime")
    return result


def normalize_token_response(payload: Mapping[str, Any], *, client_id: str, ext_agent_host_id: str, claims: Mapping[str, Any]) -> dict[str, Any]:
    required = ("access_token", "refresh_token", "id_token", "token_type", "expires_in", "scope")
    if any(k not in payload for k in required):
        raise ValueError("token response missing required fields")
    for key in ("access_token", "refresh_token", "id_token", "token_type", "scope"):
        if not isinstance(payload.get(key), str) or not payload[key]:
            raise ValueError("token response contains invalid fields")
    if payload["token_type"].lower() != "bearer":
        raise ValueError("token_type must be Bearer")
    ok, reason = verify_granted_scopes(payload["scope"])
    if not ok:
        raise ValueError(reason)
    expires_in = _positive_lifetime(payload["expires_in"])
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


def _public_transport_status(exc: Exception) -> str:
    if isinstance(exc, SafeTransportError):
        if exc.code:
            return map_transport_error(exc.code)
        if exc.http_status is not None:
            return map_http_status(exc.http_status)
        if exc.category == "network":
            return BLOCKED_NETWORK_ERROR
        if exc.category == "invalid_response":
            return BLOCKED_INVALID_RESPONSE
    return BLOCKED_INFRASTRUCTURE_ERROR


def refresh_profile(storage: HostCredentialStorage, transport: Any) -> dict[str, Any]:
    """Serialize refreshes with a durable pre-dispatch state.

    Once REFRESH_IN_PROGRESS is persisted, the old rotating refresh credential
    is never submitted automatically again, even after timeout, crash, malformed
    response, or replacement-write failure.
    """
    try:
        with storage.locked():
            current = storage.load_profile()
            if not current:
                return {"status": BLOCKED_AUTH_REQUIRED}
            if current.get("session_state") not in (None, "ACTIVE"):
                return {"status": BLOCKED_AUTH_REQUIRED}
            try:
                req = build_token_refresh_request(
                    client_id=current.get("client_id") if isinstance(current.get("client_id"), str) else "",
                    refresh_token=current.get("refresh_token") if isinstance(current.get("refresh_token"), str) else "",
                )
            except Exception:
                return {"status": BLOCKED_AUTH_REQUIRED}

            # Persist the uncertainty boundary BEFORE dispatch. If this write
            # fails, no request is sent. If anything after dispatch fails, this
            # durable blocked state survives process restart.
            in_progress = dict(current)
            in_progress["profile_version"] = storage.profile_version(current) + 1
            in_progress["session_state"] = "REFRESH_IN_PROGRESS"
            in_progress["refresh_started_at"] = int(time.time())
            in_progress.pop("access_token", None)
            try:
                storage.save_profile_atomic(in_progress)
            except Exception:
                return {"status": BLOCKED_INFRASTRUCTURE_ERROR}

            try:
                payload = transport.post_form(req["url"], req["data"])
            except Exception as exc:
                return {"status": _public_transport_status(exc)}

            if not isinstance(payload, Mapping):
                return {"status": BLOCKED_INVALID_RESPONSE}

            replacement = dict(in_progress)
            replacement["saved_at"] = int(time.time())
            replacement["session_state"] = "BLOCKED_REFRESH_INVALID"
            replacement.pop("access_token", None)

            new_refresh = payload.get("refresh_token")
            if isinstance(new_refresh, str) and new_refresh:
                replacement["refresh_token"] = new_refresh

            try:
                access = payload.get("access_token")
                token_type = payload.get("token_type", current.get("token_type", "Bearer"))
                if not isinstance(access, str) or not access:
                    raise ValueError("invalid access token")
                if not isinstance(new_refresh, str) or not new_refresh:
                    raise ValueError("invalid refresh token")
                if not isinstance(token_type, str) or token_type.lower() != "bearer":
                    raise ValueError("invalid token type")
                expires_in = _positive_lifetime(payload.get("expires_in"))

                if "scope" not in payload:
                    scopes = list(current.get("scopes", []))
                else:
                    supplied_scope = payload.get("scope")
                    if not isinstance(supplied_scope, str) or not supplied_scope.strip():
                        raise ValueError("invalid refresh scopes")
                    scopes = sorted(set(supplied_scope.split()))
                ok, _ = verify_granted_scopes(scopes)
                if not ok:
                    raise ValueError("required scopes missing")

                replacement.update({
                    "access_token": access,
                    "refresh_token": new_refresh,
                    "id_token": current.get("id_token"),
                    "token_type": "Bearer",
                    "expires_in": expires_in,
                    "expires_at": int(time.time()) + expires_in,
                    "earliest_refresh_at": payload.get("earliest_refresh_at"),
                    "scopes": scopes,
                    "session_state": "ACTIVE",
                })
                replacement.pop("refresh_started_at", None)
                try:
                    storage.save_profile_atomic(replacement)
                except Exception:
                    # The durable REFRESH_IN_PROGRESS record remains on disk.
                    return {"status": BLOCKED_INFRASTRUCTURE_ERROR}
                return {"status": "REFRESHED", "profile": storage.safe_status()}
            except Exception:
                # Whether validation or the ACTIVE replacement write failed,
                # never let a partially committed/uncertain refresh become ready.
                # Preserve a returned replacement refresh token when possible,
                # but remove active access and force a durable blocked state.
                replacement.pop("access_token", None)
                replacement["session_state"] = "BLOCKED_REFRESH_INVALID"
                try:
                    storage.save_profile_atomic(replacement)
                except Exception:
                    # The pre-dispatch REFRESH_IN_PROGRESS record remains durable.
                    return {"status": BLOCKED_INFRASTRUCTURE_ERROR}
                return {"status": BLOCKED_AUTH_REQUIRED}
    except Exception:
        return {"status": BLOCKED_INFRASTRUCTURE_ERROR}


def needs_refresh(profile: Mapping[str, Any], *, now: float | None = None, skew_seconds: int = 120) -> bool:
    current = time.time() if now is None else now
    expires_at = profile.get("expires_at")
    if not _finite_number(expires_at):
        return True
    expires = float(expires_at)
    if current >= expires:
        return True
    earliest = profile.get("earliest_refresh_at")
    if earliest is not None:
        if not _finite_number(earliest):
            return True
        if current < float(earliest):
            return False
    return current + skew_seconds >= expires


def ensure_fresh_profile(storage: HostCredentialStorage, transport: Any) -> tuple[dict[str, Any] | None, str]:
    try:
        profile = storage.load_profile()
    except Exception:
        return None, BLOCKED_INFRASTRUCTURE_ERROR
    if not profile or profile.get("session_state") not in (None, "ACTIVE"):
        return None, BLOCKED_AUTH_REQUIRED
    if not _finite_number(profile.get("expires_at")):
        return None, BLOCKED_AUTH_REQUIRED
    if needs_refresh(profile):
        refreshed = refresh_profile(storage, transport)
        if refreshed.get("status") != "REFRESHED":
            return None, str(refreshed.get("status", BLOCKED_AUTH_REQUIRED))
        try:
            profile = storage.load_profile()
        except Exception:
            return None, BLOCKED_INFRASTRUCTURE_ERROR
    if not profile:
        return None, BLOCKED_AUTH_REQUIRED
    access = profile.get("access_token")
    refresh = profile.get("refresh_token")
    token_type = profile.get("token_type", "Bearer")
    expires_at = profile.get("expires_at")
    scopes = profile.get("scopes")
    if not isinstance(access, str) or not access or not isinstance(refresh, str) or not refresh:
        return None, BLOCKED_AUTH_REQUIRED
    if not isinstance(token_type, str) or token_type.lower() != "bearer":
        return None, BLOCKED_AUTH_REQUIRED
    if not _finite_number(expires_at) or time.time() >= float(expires_at):
        return None, BLOCKED_AUTH_REQUIRED
    ok, _ = verify_granted_scopes(scopes if isinstance(scopes, list) else [])
    if not ok:
        return None, BLOCKED_AUTH_REQUIRED
    return profile, "PROFILE_READY"


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
        return {"status": _public_transport_status(exc)}
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
    """Validate a stateless Responses stream and extract exactly one verdict message.

    Reasoning items may precede the assistant message. Tool/refusal/unknown output
    remains fail-closed because TASK-0004 requests no tools and requires a JSON
    verdict in the assistant text.
    """
    text_parts: list[str] = []
    final_text: str | None = None
    response_id: str | None = None
    terminal = False
    response_started = False
    message_item: tuple[int, str] | None = None
    message_item_done = False
    active_content: tuple[int, int, str] | None = None
    text_done_seen = False
    reasoning_items: dict[int, str] = {}
    last_sequence: int | None = None

    def invalid() -> dict[str, Any]:
        return {"status": BLOCKED_INVALID_RESPONSE, "completed": False}

    def valid_index(value: Any) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0

    def valid_id(value: Any) -> bool:
        return isinstance(value, str) and bool(value)

    def validate_sequence(event: Mapping[str, Any]) -> bool:
        nonlocal last_sequence
        if "sequence_number" not in event:
            return True
        seq = event.get("sequence_number")
        if not valid_index(seq):
            return False
        if last_sequence is not None and seq <= last_sequence:
            return False
        last_sequence = seq
        return True

    def bind_response(response: Any, allowed_statuses: set[str]) -> bool:
        nonlocal response_id
        if not isinstance(response, Mapping):
            return False
        rid = response.get("id")
        status = response.get("status")
        if not valid_id(rid) or status not in allowed_statuses:
            return False
        if response_id is None:
            response_id = rid
        elif rid != response_id:
            return False
        model = response.get("model")
        if model is not None and model != EXACT_REVIEWER_MODEL:
            return False
        return True

    def validate_reasoning_event(event: Mapping[str, Any], event_type: str) -> bool:
        output_index = event.get("output_index")
        item_id = event.get("item_id")
        if not valid_index(output_index) or reasoning_items.get(output_index) != item_id:
            return False
        if event_type.startswith("response.reasoning_summary_"):
            summary_index = event.get("summary_index")
            if not valid_index(summary_index):
                return False
            if event_type.endswith("_part.added") or event_type.endswith("_part.done"):
                part = event.get("part")
                return (
                    isinstance(part, Mapping)
                    and part.get("type") == "summary_text"
                    and isinstance(part.get("text"), str)
                )
            if event_type.endswith("_text.delta"):
                return isinstance(event.get("delta"), str)
            if event_type.endswith("_text.done"):
                return isinstance(event.get("text"), str)
            return False
        if event_type == "response.reasoning_text.delta":
            return valid_index(event.get("content_index")) and isinstance(event.get("delta"), str)
        if event_type == "response.reasoning_text.done":
            return valid_index(event.get("content_index")) and isinstance(event.get("text"), str)
        return False

    def terminal_message_text(response: Mapping[str, Any]) -> str | None:
        output = response.get("output")
        if not isinstance(output, list):
            return None
        messages = []
        for item in output:
            if not isinstance(item, Mapping):
                return None
            item_type = item.get("type")
            if item_type == "reasoning":
                if not valid_id(item.get("id")):
                    return None
                continue
            if item_type != "message":
                return None
            if item.get("role") != "assistant" or item.get("status") != "completed":
                return None
            content = item.get("content")
            if not isinstance(content, list) or len(content) != 1:
                return None
            part = content[0]
            if not isinstance(part, Mapping) or part.get("type") != "output_text" or not isinstance(part.get("text"), str):
                return None
            messages.append((item.get("id"), part.get("text")))
        if len(messages) != 1:
            return None
        if message_item is not None and messages[0][0] != message_item[1]:
            return None
        return messages[0][1]

    for event in events:
        if terminal:
            return invalid()
        if not isinstance(event, Mapping) or not validate_sequence(event):
            return invalid()
        event_type = event.get("type")
        if not isinstance(event_type, str):
            return invalid()

        if event_type == "response.queued":
            if response_started or not bind_response(event.get("response"), {"queued", "in_progress"}):
                return invalid()
            continue

        if event_type in {"response.created", "response.in_progress"}:
            if not bind_response(event.get("response"), {"in_progress"}):
                return invalid()
            response_started = True
            continue

        if event_type == "response.output_item.added":
            if not response_started:
                return invalid()
            output_index = event.get("output_index")
            item = event.get("item")
            if not valid_index(output_index) or not isinstance(item, Mapping):
                return invalid()
            item_id = item.get("id")
            item_type = item.get("type")
            if not valid_id(item_id):
                return invalid()
            if item_type == "reasoning":
                if output_index in reasoning_items or (message_item and output_index == message_item[0]):
                    return invalid()
                reasoning_items[output_index] = item_id
                continue
            if item_type == "message":
                if message_item is not None or output_index in reasoning_items:
                    return invalid()
                if item.get("role") != "assistant" or item.get("status") not in ("in_progress", "completed"):
                    return invalid()
                message_item = (output_index, item_id)
                continue
            return invalid()

        if event_type in {
            "response.reasoning_summary_part.added",
            "response.reasoning_summary_part.done",
            "response.reasoning_summary_text.delta",
            "response.reasoning_summary_text.done",
            "response.reasoning_text.delta",
            "response.reasoning_text.done",
        }:
            if not validate_reasoning_event(event, event_type):
                return invalid()
            continue

        if event_type == "response.content_part.added":
            if message_item is None or active_content is not None or text_done_seen:
                return invalid()
            output_index = event.get("output_index")
            content_index = event.get("content_index")
            item_id = event.get("item_id")
            part = event.get("part")
            if (
                output_index != message_item[0]
                or item_id != message_item[1]
                or not valid_index(content_index)
                or not isinstance(part, Mapping)
                or part.get("type") != "output_text"
                or not isinstance(part.get("text"), str)
            ):
                return invalid()
            active_content = (output_index, content_index, item_id)
            continue

        if event_type == "response.output_text.delta":
            if active_content is None or text_done_seen:
                return invalid()
            if (
                event.get("output_index") != active_content[0]
                or event.get("content_index") != active_content[1]
                or event.get("item_id") != active_content[2]
            ):
                return invalid()
            delta = event.get("delta")
            if not isinstance(delta, str):
                return invalid()
            text_parts.append(delta)
            continue

        if event_type == "response.output_text.done":
            if active_content is None or text_done_seen:
                return invalid()
            if (
                event.get("output_index") != active_content[0]
                or event.get("content_index") != active_content[1]
                or event.get("item_id") != active_content[2]
            ):
                return invalid()
            text = event.get("text")
            if not isinstance(text, str) or text != "".join(text_parts):
                return invalid()
            final_text = text
            text_done_seen = True
            continue

        if event_type == "response.content_part.done":
            if active_content is None or not text_done_seen:
                return invalid()
            if (
                event.get("output_index") != active_content[0]
                or event.get("content_index") != active_content[1]
                or event.get("item_id") != active_content[2]
            ):
                return invalid()
            part = event.get("part")
            if (
                not isinstance(part, Mapping)
                or part.get("type") != "output_text"
                or part.get("text") != final_text
            ):
                return invalid()
            active_content = None
            continue

        if event_type == "response.output_item.done":
            output_index = event.get("output_index")
            item = event.get("item")
            if not valid_index(output_index) or not isinstance(item, Mapping):
                return invalid()
            item_id = item.get("id")
            item_type = item.get("type")
            if item_type == "reasoning":
                if reasoning_items.get(output_index) != item_id:
                    return invalid()
                reasoning_items.pop(output_index, None)
                continue
            if item_type == "message":
                if (
                    message_item is None
                    or output_index != message_item[0]
                    or item_id != message_item[1]
                    or active_content is not None
                    or not text_done_seen
                    or message_item_done
                    or item.get("role") != "assistant"
                    or item.get("status") != "completed"
                ):
                    return invalid()
                content = item.get("content")
                if not isinstance(content, list) or len(content) != 1:
                    return invalid()
                part = content[0]
                if not isinstance(part, Mapping) or part.get("type") != "output_text" or part.get("text") != final_text:
                    return invalid()
                message_item_done = True
                continue
            return invalid()

        if event_type == "response.completed":
            if (
                not response_started
                or message_item is None
                or not message_item_done
                or active_content is not None
                or not text_done_seen
                or reasoning_items
                or not bind_response(event.get("response"), {"completed"})
            ):
                return invalid()
            response = event.get("response")
            terminal_text = terminal_message_text(response)
            if terminal_text is None or terminal_text != final_text:
                return invalid()
            terminal = True
            continue

        if event_type == "response.failed":
            response = event.get("response")
            if not bind_response(response, {"failed"}):
                return invalid()
            error = response.get("error")
            code = error.get("code") if isinstance(error, Mapping) else "unknown_error"
            if not isinstance(code, str):
                return invalid()
            return {"status": map_response_error_code(code), "completed": False}

        if event_type == "response.incomplete":
            if not bind_response(event.get("response"), {"incomplete"}):
                return invalid()
            return {"status": BLOCKED_INFRASTRUCTURE_ERROR, "completed": False}

        if event_type == "error":
            code = event.get("code")
            if code is not None and not isinstance(code, str):
                return invalid()
            return {
                "status": map_response_error_code(code) if isinstance(code, str) else BLOCKED_INFRASTRUCTURE_ERROR,
                "completed": False,
            }

        # Refusals, tool calls, annotations, audio and unknown event families are
        # not part of this no-tools JSON-verdict contract.
        return invalid()

    if not terminal or final_text is None or not final_text:
        return invalid()
    return {"status": "COMPLETED", "completed": True, "text": final_text}



def _validate_allowance_evidence(evidence: Mapping[str, Any] | None, profile: Mapping[str, Any], *, now: float | None = None) -> tuple[bool, str, dict[str, Any] | None]:
    if not isinstance(evidence, Mapping):
        return False, BLOCKED_PLAN_ALLOWANCE, None
    required = {
        "billing_mode", "separately_billed", "credits_enabled", "remaining_requests",
        "profile_id", "subject", "client_id", "observed_at", "expires_at",
    }
    if set(evidence.keys()) != required:
        return False, BLOCKED_PLAN_ALLOWANCE, None
    current = time.time() if now is None else now
    if evidence.get("profile_id") != profile.get("profile_label"):
        return False, BLOCKED_PLAN_ALLOWANCE, None
    if evidence.get("subject") != profile.get("subject"):
        return False, BLOCKED_PLAN_ALLOWANCE, None
    if evidence.get("client_id") != profile.get("client_id"):
        return False, BLOCKED_PLAN_ALLOWANCE, None
    observed = evidence.get("observed_at")
    expires = evidence.get("expires_at")
    if not _finite_number(observed) or not _finite_number(expires):
        return False, BLOCKED_PLAN_ALLOWANCE, None
    observed_value = float(observed)
    expires_value = float(expires)
    if observed_value > current or expires_value <= observed_value or current >= expires_value:
        return False, BLOCKED_PLAN_ALLOWANCE, None
    plan = {
        "billing_mode": evidence.get("billing_mode"),
        "separately_billed": evidence.get("separately_billed"),
        "credits_enabled": evidence.get("credits_enabled"),
        "remaining_requests": evidence.get("remaining_requests"),
    }
    if bridge is None:
        return False, BLOCKED_INFRASTRUCTURE_ERROR, None
    ok, status = bridge.validate_plan_allowance(plan)
    return ok, status, plan if ok else None


def _transport_review(*, profile: Mapping[str, Any], transport: Any, review_prompt: str, review_context: Mapping[str, Any]) -> dict[str, Any]:
    try:
        request = build_responses_plan_request(review_prompt=review_prompt, review_context=review_context)
        events = list(transport.stream_sse(
            RESPONSES_URL,
            request,
            {"Authorization": f"Bearer {profile['access_token']}"},
        ))
        return assemble_stream(events)
    except SafeTransportError as exc:
        return {"status": _public_transport_status(exc), "completed": False}
    except Exception:
        return {"status": BLOCKED_INVALID_RESPONSE, "completed": False}


def run_streamed_review(
    *,
    storage: HostCredentialStorage | None = None,
    plan_allowance_evidence: Mapping[str, Any] | None = None,
    activation_policy: Mapping[str, Any],
    transport: Any,
    review_prompt: str,
    expected_snapshot: Mapping[str, Any] | None = None,
    review_request: Mapping[str, Any] | None = None,
    existing_records: Sequence[Mapping[str, Any]] = (),
    current_attempt: int = 0,
    max_retries: int = 3,
    # Legacy arguments are accepted only to fail closed; they may not authorize transport.
    profile: Mapping[str, Any] | None = None,
    model_catalog: Mapping[str, Any] | None = None,
    review_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Single production live-review path; every trust boundary is enforced here."""
    if activation_policy.get("reviewer_enabled") is not True:
        return {"status": BLOCKED_PLAN_ALLOWANCE, "completed": False}
    if activation_policy.get("zero_extra_spend_confirmed") is not True:
        return {"status": BLOCKED_PLAN_ALLOWANCE, "completed": False}
    if storage is None or expected_snapshot is None or review_request is None:
        return {"status": BLOCKED_INVALID_RESPONSE, "completed": False}
    if profile is not None or model_catalog is not None or review_context is not None:
        return {"status": BLOCKED_INVALID_RESPONSE, "completed": False}
    if bridge is None:
        return {"status": BLOCKED_INFRASTRUCTURE_ERROR, "completed": False}

    try:
        expected = bridge.normalize_review_snapshot(expected_snapshot)
        request_ok, _ = bridge.validate_review_request(expected, review_request)
    except Exception:
        return {"status": BLOCKED_INVALID_RESPONSE, "completed": False}
    if not request_ok:
        return {"status": BLOCKED_INVALID_RESPONSE, "completed": False}

    fresh_profile, fresh_status = ensure_fresh_profile(storage, transport)
    if fresh_profile is None:
        return {"status": fresh_status, "completed": False}

    models = list_models(fresh_profile, transport)
    if models.get("status") != "MODEL_ALLOWED":
        return {"status": models.get("status", BLOCKED_NO_ASTRA), "completed": False}
    catalog = models.get("catalog")
    if not isinstance(catalog, Mapping):
        return {"status": BLOCKED_NO_ASTRA, "completed": False}

    allowance_ok, allowance_status, plan_allowance = _validate_allowance_evidence(plan_allowance_evidence, fresh_profile)
    if not allowance_ok or plan_allowance is None:
        return {"status": allowance_status, "completed": False}

    safe_auth = {
        "active": True,
        "expired": False,
        "profile_id": fresh_profile.get("profile_label"),
    }
    preflight = evaluate_live_handoff(
        expected_snapshot=expected,
        review_request=review_request,
        model_catalog=catalog,
        auth_profile=safe_auth,
        plan_allowance=plan_allowance,
        verdict_payload=None,
        current_attempt=current_attempt,
        max_retries=max_retries,
    )
    # A missing verdict must be the only pre-transport failure now; any earlier
    # evidence/auth/model/allowance mismatch must prevent inference.
    if preflight.get("status") not in (bridge.BLOCKED_INVALID_VERDICT,):
        return {"status": preflight.get("status", BLOCKED_INVALID_RESPONSE), "completed": False}

    streamed = _transport_review(
        profile=fresh_profile,
        transport=transport,
        review_prompt=review_prompt,
        review_context=expected,
    )
    if streamed.get("status") != "COMPLETED":
        return streamed

    try:
        verdict = json.loads(streamed.get("text", ""))
    except Exception:
        return {"status": BLOCKED_INVALID_RESPONSE, "completed": False}
    if not isinstance(verdict, Mapping):
        return {"status": BLOCKED_INVALID_RESPONSE, "completed": False}

    result = evaluate_live_handoff(
        expected_snapshot=expected,
        review_request=review_request,
        model_catalog=catalog,
        auth_profile=safe_auth,
        plan_allowance=plan_allowance,
        verdict_payload=verdict,
        current_attempt=current_attempt,
        max_retries=max_retries,
    )
    valid, _ = bridge.validate_emitted_result(result)
    if not valid:
        return {"status": BLOCKED_INVALID_RESPONSE, "completed": False}
    try:
        persistence = bridge.deduplicate_result(result, existing_records)
    except Exception:
        return {"status": BLOCKED_INVALID_RESPONSE, "completed": False}
    if persistence.get("action") == "CONFLICT_BLOCKED":
        return {"status": BLOCKED_INVALID_RESPONSE, "completed": False, "persistence": persistence}
    return {
        "status": result.get("status"),
        "completed": True,
        "result": result,
        "persistence": persistence,
    }


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

    if any(code in lower for code in (
        "subscription_sharing_usage_limit_exceeded",
        "subscription_sharing_usage_unavailable",
    )):
        return BLOCKED_PLAN_ALLOWANCE

    if any(code in lower for code in (
        "invalid_grant",
        "invalid_refresh_token",
        "token_expired",
        "refresh_token_expired",
        "refresh_token_invalidated",
        "refresh_token_reused",
        "invalid_client",
        "subscription_sharing_invalid_user",
        "chatpass_v2_scope_not_authorized",
        "chatpass_v2_invalid_authorization_context",
    )):
        return BLOCKED_AUTH_REQUIRED

    if any(code in lower for code in (
        "subscription_sharing_unsupported_capability",
        "subscription_sharing_route_not_supported",
    )):
        return BLOCKED_INVALID_RESPONSE

    if "subscription_sharing_user_unavailable" in lower:
        return BLOCKED_INFRASTRUCTURE_ERROR

    if '"http_status": 401' in lower or '"http_status": 403' in lower:
        return BLOCKED_AUTH_REQUIRED
    if '"http_status": 429' in lower:
        return BLOCKED_PLAN_ALLOWANCE
    if "timeout" in lower or "network" in lower or "connection" in lower:
        return BLOCKED_NETWORK_ERROR
    if re.search(r'"http_status"\s*:\s*5\d\d', lower):
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
