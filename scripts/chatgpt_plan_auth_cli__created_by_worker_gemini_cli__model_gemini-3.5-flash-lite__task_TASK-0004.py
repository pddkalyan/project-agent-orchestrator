#!/usr/bin/env python3
"""Windows CLI for TASK-0004 Sign in with ChatGPT.

The sign-in command is always user-initiated. CI/non-interactive environments
are blocked. No API key or paid fallback path exists.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import importlib.util

_ADAPTER_PATH = Path(__file__).resolve().parent / "chatgpt_plan_auth_adapter__created_by_worker_gemini_cli__model_gemini-3.5-flash-lite__task_TASK-0004.py"
_spec = importlib.util.spec_from_file_location("task0004_auth_adapter", _ADAPTER_PATH)
adapter = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(adapter)

AGENT_NAME = adapter.AGENT_NAME
BLOCKED_AUTH_REQUIRED = adapter.BLOCKED_AUTH_REQUIRED
HttpTransport = adapter.HttpTransport
HostCredentialStorage = adapter.HostCredentialStorage
PyJWTSignatureVerifier = adapter.PyJWTSignatureVerifier
WindowsDPAPIProtector = adapter.WindowsDPAPIProtector
build_authorization_url = adapter.build_authorization_url
build_token_exchange_request = adapter.build_token_exchange_request
generate_host_id = adapter.generate_host_id
ensure_fresh_profile = adapter.ensure_fresh_profile
list_models = adapter.list_models
new_authorization_attempt = adapter.new_authorization_attempt
normalize_token_response = adapter.normalize_token_response
parse_loopback_callback = adapter.parse_loopback_callback
self_check = adapter.self_check
validate_host_id = adapter.validate_host_id
verify_granted_scopes = adapter.verify_granted_scopes
verify_id_token = adapter.verify_id_token


DEFAULT_DIR = Path.home() / ".project-agent-orchestrator" / "chatgpt"
DEFAULT_PROFILES_DIR = DEFAULT_DIR / "profiles"
DEFAULT_HOST_FILE = DEFAULT_DIR / "host.json"


def _json(data: Any) -> None:
    print(json.dumps(data, indent=2, sort_keys=True))


def _require_windows_storage(path: Path) -> HostCredentialStorage:
    return HostCredentialStorage(path, WindowsDPAPIProtector())


def _safe_profile_label(label: str) -> str:
    if not isinstance(label, str) or not label or not all(ch.isalnum() or ch in "._-" for ch in label):
        raise ValueError("profile label must use only letters, numbers, dot, underscore or dash")
    return label


def _storage_path(args: argparse.Namespace) -> Path:
    if args.storage:
        return Path(args.storage)
    return DEFAULT_PROFILES_DIR / (_safe_profile_label(args.profile) + ".json")


def _load_or_create_host_id(host_file: Path) -> str:
    if host_file.exists():
        payload = json.loads(host_file.read_text(encoding="utf-8"))
        host_id = payload.get("ext_agent_host_id")
        if not validate_host_id(host_id):
            raise RuntimeError("saved ext_agent_host_id is invalid")
        return host_id
    host_file.parent.mkdir(parents=True, exist_ok=True)
    host_id = generate_host_id()
    temp = host_file.with_suffix(".tmp")
    temp.write_text(json.dumps({"ext_agent_host_id": host_id}, indent=2), encoding="utf-8")
    os.replace(temp, host_file)
    return host_id


class _CallbackHandler(BaseHTTPRequestHandler):
    callback_query = ""
    event = threading.Event()

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/auth/callback":
            self.send_response(404)
            self.end_headers()
            return
        type(self).callback_query = parsed.query
        type(self).event.set()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Authorization received. You can close this tab and return to the terminal.")


def _listen_once(timeout_seconds: int = 300) -> tuple[HTTPServer, str]:
    _CallbackHandler.callback_query = ""
    _CallbackHandler.event = threading.Event()
    server = HTTPServer(("127.0.0.1", 0), _CallbackHandler)
    port = server.server_address[1]
    redirect_uri = f"http://127.0.0.1:{port}/auth/callback"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, redirect_uri


def cmd_init_host(args: argparse.Namespace) -> int:
    host_id = _load_or_create_host_id(Path(args.host_file))
    _json({"status": "HOST_READY", "ext_agent_host_id": host_id})
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    host_id = _load_or_create_host_id(Path(args.host_file))
    try:
        storage = _require_windows_storage(_storage_path(args))
        status = storage.safe_status()
    except Exception:
        _json({"status": BLOCKED_AUTH_REQUIRED, "host_id": host_id, "message": "Protected profile is unavailable or invalid."})
        return 1
    status["ext_agent_host_id"] = host_id
    _json(status)
    return 0 if status.get("connected") else 1


def cmd_models(args: argparse.Namespace) -> int:
    storage = _require_windows_storage(_storage_path(args))
    transport = HttpTransport()
    profile, status = ensure_fresh_profile(storage, transport)
    if not profile:
        _json({"status": status})
        return 1
    result = list_models(profile, transport)
    _json(result)
    return 0 if result.get("astra_available") else 1


def cmd_sign_in(args: argparse.Namespace) -> int:
    if args.ci or os.environ.get("CI", "").lower() == "true" or not sys.stdin.isatty():
        _json({"status": BLOCKED_AUTH_REQUIRED, "message": "Interactive sign-in is blocked in CI/non-interactive shells."})
        return 1
    if os.name != "nt":
        _json({"status": BLOCKED_AUTH_REQUIRED, "message": "TASK-0004 live sign-in target is Windows."})
        return 1

    host_id = _load_or_create_host_id(Path(args.host_file))
    storage = _require_windows_storage(_storage_path(args))
    try:
        with storage.locked():
            existing = storage.load_profile()
            registration = storage.load_registration()
            existing_version = storage.profile_version(existing)
    except Exception:
        _json({"status": BLOCKED_AUTH_REQUIRED, "message": "Protected profile is unavailable or locked."})
        return 1

    if registration:
        if not existing and registration.get("registration_pending") is not True:
            _json({"status": BLOCKED_AUTH_REQUIRED, "message": "Saved registration is not a pending retry for this profile."})
            return 1
        if registration.get("profile_label") != args.profile or registration.get("ext_agent_host_id") != host_id:
            _json({"status": BLOCKED_AUTH_REQUIRED, "message": "Saved registration does not match this profile or host."})
            return 1
        reg_client = registration.get("client_id")
        if not isinstance(reg_client, str) or not reg_client or reg_client == adapter.DYNAMIC_CLIENT_ID:
            _json({"status": BLOCKED_AUTH_REQUIRED, "message": "Saved registration metadata is invalid."})
            return 1
        if existing and existing.get("client_id") and existing.get("client_id") != reg_client:
            _json({"status": BLOCKED_AUTH_REQUIRED, "message": "Saved registration conflicts with the active profile."})
            return 1
        if existing and existing.get("subject") and registration.get("subject") not in (None, existing.get("subject")):
            _json({"status": BLOCKED_AUTH_REQUIRED, "message": "Saved registration identity conflicts with the active profile."})
            return 1

    issued_client_id = existing.get("client_id") if existing else (registration.get("client_id") if registration else None)
    retained_id_token = existing.get("id_token") if existing else None
    login_hint = existing.get("email") if existing else None

    server, redirect_uri = _listen_once(args.timeout)
    attempt = new_authorization_attempt()
    url = build_authorization_url(
        redirect_uri=redirect_uri,
        ext_agent_host_id=host_id,
        attempt=attempt,
        issued_client_id=issued_client_id,
        retained_id_token=retained_id_token,
        login_hint=login_hint,
        agent_name=AGENT_NAME,
    )

    print("Opening Continue with ChatGPT in your browser. Complete consent there.")
    if not webbrowser.open(url):
        print("Browser did not open automatically. Copy the authorization URL from a secure local terminal only.")
        print("Do not paste that URL into chat or logs because returning sign-ins may contain id_token_hint.")
        server.shutdown()
        return 1

    if not _CallbackHandler.event.wait(args.timeout):
        server.shutdown()
        _json({"status": BLOCKED_AUTH_REQUIRED, "message": "Authorization callback timed out."})
        return 1
    query = _CallbackHandler.callback_query
    server.shutdown()

    callback = parse_loopback_callback(
        query,
        expected_state=attempt["state"],
        expected_client_id=issued_client_id,
        is_new_registration=issued_client_id is None,
    )
    client_id = callback["client_id"]

    # Persist registration metadata separately from the active rotating-token
    # session. A failed authorization must never overwrite a newer active session.
    registration = {
        "profile_label": args.profile,
        "client_id": client_id,
        "ext_agent_host_id": host_id,
        "registration_pending": True,
    }
    if existing and isinstance(existing.get("subject"), str):
        registration["subject"] = existing["subject"]
    try:
        with storage.locked():
            storage.save_registration_atomic(registration)
    except Exception:
        _json({"status": BLOCKED_AUTH_REQUIRED, "message": "Could not persist registration metadata safely."})
        return 1

    request = build_token_exchange_request(
        client_id=client_id,
        code=callback["code"],
        code_verifier=attempt["code_verifier"],
        redirect_uri=redirect_uri,
    )
    transport = HttpTransport()
    try:
        token_payload = transport.post_form(request["url"], request["data"])
    except Exception:
        _json({"status": BLOCKED_AUTH_REQUIRED, "message": "Token exchange failed. Start sign-in again."})
        return 1

    id_token = token_payload.get("id_token")
    if not isinstance(id_token, str):
        _json({"status": BLOCKED_AUTH_REQUIRED, "message": "Token response did not contain an ID token."})
        return 1

    valid, reason, claims = verify_id_token(
        id_token,
        expected_client_id=client_id,
        expected_nonce=attempt["nonce"],
        signature_verifier=PyJWTSignatureVerifier(),
    )
    if not valid or claims is None:
        _json({"status": BLOCKED_AUTH_REQUIRED, "message": reason})
        return 1

    if existing and existing.get("subject") and existing.get("subject") != claims.get("sub"):
        _json({"status": BLOCKED_AUTH_REQUIRED, "message": "Returning sign-in identity did not match the selected registration."})
        return 1

    scope_value = token_payload.get("scope", callback.get("scope", ""))
    ok, scope_reason = verify_granted_scopes(str(scope_value))
    if not ok:
        _json({"status": BLOCKED_AUTH_REQUIRED, "message": scope_reason})
        return 1
    token_payload = dict(token_payload)
    token_payload["scope"] = scope_value

    profile = normalize_token_response(
        token_payload,
        client_id=client_id,
        ext_agent_host_id=host_id,
        claims=claims,
    )
    profile["profile_label"] = args.profile
    profile["registration_pending"] = False
    profile["session_state"] = "ACTIVE"
    committed = storage.replace_profile_if_version(
        expected_version=existing_version,
        profile=profile,
        expected_subject=existing.get("subject") if existing else None,
        expected_client_id=issued_client_id,
    )
    if not committed:
        _json({"status": BLOCKED_AUTH_REQUIRED, "message": "Profile changed during sign-in; the newer session was preserved. Start sign-in again."})
        return 1
    try:
        with storage.locked():
            registration["registration_pending"] = False
            registration["subject"] = claims.get("sub")
            storage.save_registration_atomic(registration)
    except Exception:
        _json({"status": BLOCKED_AUTH_REQUIRED, "message": "Sign-in completed but registration metadata could not be finalized safely."})
        return 1

    _json({
        "status": "SIGNED_IN",
        "profile": storage.safe_status(),
        "next": "Run the models command and confirm astra_available=true before enabling the reviewer workflow.",
    })
    return 0


def cmd_profiles(args: argparse.Namespace) -> int:
    DEFAULT_PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    profiles = []
    for path in sorted(DEFAULT_PROFILES_DIR.glob("*.json")):
        try:
            storage = _require_windows_storage(path)
            status = storage.safe_status()
            profiles.append({
                "profile": path.stem,
                "connected": status.get("connected", False),
                "email": status.get("email"),
                "client_id": status.get("client_id"),
            })
        except Exception:
            profiles.append({"profile": path.stem, "connected": False, "status": BLOCKED_AUTH_REQUIRED})
    _json({"status": "PROFILES", "profiles": profiles})
    return 0


def cmd_self_check(args: argparse.Namespace) -> int:
    _json(self_check())
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Project Agent ChatGPT-plan authorization CLI")
    parser.add_argument("--storage", default="", help="Optional explicit protected profile path")
    parser.add_argument("--profile", default="default", help="Saved ChatGPT registration label")
    parser.add_argument("--host-file", default=str(DEFAULT_HOST_FILE))
    parser.add_argument("--ci", action="store_true")
    parser.add_argument("--timeout", type=int, default=300)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-host")
    sub.add_parser("status")
    sub.add_parser("models")
    sub.add_parser("profiles")
    sub.add_parser("sign-in")
    sub.add_parser("self-check")
    args = parser.parse_args()

    if args.command == "init-host":
        return cmd_init_host(args)
    if args.command == "status":
        return cmd_status(args)
    if args.command == "models":
        return cmd_models(args)
    if args.command == "profiles":
        return cmd_profiles(args)
    if args.command == "sign-in":
        return cmd_sign_in(args)
    if args.command == "self-check":
        return cmd_self_check(args)
    return 2


def _safe_main() -> int:
    try:
        return main()
    except KeyboardInterrupt:
        _json({"status": BLOCKED_AUTH_REQUIRED, "message": "Operation cancelled."})
        return 130
    except Exception:
        _json({"status": BLOCKED_AUTH_REQUIRED, "message": "Operation failed safely. No credential details were emitted."})
        return 1


if __name__ == "__main__":
    raise SystemExit(_safe_main())
