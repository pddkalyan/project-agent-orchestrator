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

from chatgpt_plan_auth_adapter__created_by_worker_gemini_cli__model_gemini-3.5-flash-lite__task_TASK-0004 import (
    AGENT_NAME,
    BLOCKED_AUTH_REQUIRED,
    DYNAMIC_CLIENT_ID,
    HttpTransport,
    HostCredentialStorage,
    PyJWTSignatureVerifier,
    WindowsDPAPIProtector,
    build_authorization_url,
    build_token_exchange_request,
    generate_host_id,
    list_models,
    new_authorization_attempt,
    normalize_token_response,
    parse_loopback_callback,
    self_check,
    validate_host_id,
    verify_granted_scopes,
    verify_id_token,
)

DEFAULT_DIR = Path.home() / ".project-agent-orchestrator" / "chatgpt"
DEFAULT_STORAGE = DEFAULT_DIR / "profile.json"
DEFAULT_HOST_FILE = DEFAULT_DIR / "host.json"


def _json(data: Any) -> None:
    print(json.dumps(data, indent=2, sort_keys=True))


def _require_windows_storage(path: Path) -> HostCredentialStorage:
    return HostCredentialStorage(path, WindowsDPAPIProtector())


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
        storage = _require_windows_storage(Path(args.storage))
        status = storage.safe_status()
    except Exception as exc:
        _json({"status": BLOCKED_AUTH_REQUIRED, "host_id": host_id, "message": str(exc)})
        return 1
    status["ext_agent_host_id"] = host_id
    _json(status)
    return 0 if status.get("connected") else 1


def cmd_models(args: argparse.Namespace) -> int:
    storage = _require_windows_storage(Path(args.storage))
    profile = storage.load_profile()
    if not profile:
        _json({"status": BLOCKED_AUTH_REQUIRED})
        return 1
    result = list_models(profile, HttpTransport())
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
    storage = _require_windows_storage(Path(args.storage))
    existing = storage.load_profile()
    issued_client_id = existing.get("client_id") if existing else None
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
    storage.save_profile_atomic(profile)

    _json({
        "status": "SIGNED_IN",
        "profile": storage.safe_status(),
        "next": "Run the models command and confirm astra_available=true before enabling the reviewer workflow.",
    })
    return 0


def cmd_self_check(args: argparse.Namespace) -> int:
    _json(self_check())
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Project Agent ChatGPT-plan authorization CLI")
    parser.add_argument("--storage", default=str(DEFAULT_STORAGE))
    parser.add_argument("--host-file", default=str(DEFAULT_HOST_FILE))
    parser.add_argument("--ci", action="store_true")
    parser.add_argument("--timeout", type=int, default=300)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-host")
    sub.add_parser("status")
    sub.add_parser("models")
    sub.add_parser("sign-in")
    sub.add_parser("self-check")
    args = parser.parse_args()

    if args.command == "init-host":
        return cmd_init_host(args)
    if args.command == "status":
        return cmd_status(args)
    if args.command == "models":
        return cmd_models(args)
    if args.command == "sign-in":
        return cmd_sign_in(args)
    if args.command == "self-check":
        return cmd_self_check(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
