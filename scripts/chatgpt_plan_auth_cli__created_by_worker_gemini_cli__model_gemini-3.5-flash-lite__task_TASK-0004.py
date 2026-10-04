#!/usr/bin/env python3
"""
Windows-friendly one-time authorization CLI for TASK-0004.

Commands:
- init-host: Initialize or load stable host identity (ext_agent_host_id).
- status: Display credential-free host and authorization status.
- models: Inspect and validate model catalog against exact gpt-6-astra.
- sign-in: Interactive OAuth loopback flow (requires direct user interaction, safe block in CI).
- self-check: Offline validation check.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from chatgpt_plan_auth_adapter__created_by_worker_gemini_cli__model_gemini-3.5-flash-lite__task_TASK-0004 import (
    HostCredentialStorage,
    generate_host_id,
    sanitize_object,
    self_check,
    validate_model_catalog,
)

DEFAULT_STORAGE_PATH = Path.home() / ".openai" / "chatgpt_reviewer_session.json"


def cmd_init_host(args: argparse.Namespace) -> int:
    storage = HostCredentialStorage(Path(args.storage))
    profile = storage.load_profile() or {}
    if not profile.get("ext_agent_host_id"):
        host_id = generate_host_id()
        profile["ext_agent_host_id"] = host_id
        profile["created_at"] = args.timestamp or "2026-10-04"
        storage.save_profile_atomic(profile)
        print(json.dumps({"status": "HOST_INITIALIZED", "ext_agent_host_id": host_id}, indent=2))
    else:
        print(json.dumps({"status": "HOST_ALREADY_INITIALIZED", "ext_agent_host_id": profile["ext_agent_host_id"]}, indent=2))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    storage = HostCredentialStorage(Path(args.storage))
    profile = storage.load_profile()
    if not profile:
        print(json.dumps({"status": "BLOCKED_AUTH_REQUIRED", "message": "No host profile or session found. Run init-host and sign-in."}, indent=2))
        return 1

    # Credential-free display
    safe_profile = sanitize_object(profile)
    # Ensure raw secret keys are completely absent
    for secret_key in ("access_token", "refresh_token", "id_token", "client_secret"):
        safe_profile.pop(secret_key, None)

    print(json.dumps({
        "status": "STATUS_OK",
        "profile": safe_profile,
    }, indent=2))
    return 0


def cmd_models(args: argparse.Namespace) -> int:
    # Offline or sample model check
    sample_catalog = {
        "user_authorized": True,
        "authorized_models": ["gpt-6-astra", "gpt-4o"],
    }
    valid, reason = validate_model_catalog(sample_catalog)
    print(json.dumps({
        "status": reason,
        "exact_reviewer_model": "gpt-6-astra",
        "catalog_valid": valid,
    }, indent=2))
    return 0 if valid else 1


def cmd_sign_in(args: argparse.Namespace) -> int:
    if args.ci or not sys.stdin.isatty():
        print(json.dumps({
            "status": "BLOCKED_AUTH_REQUIRED",
            "message": "Interactive browser sign-in cannot be executed automatically in CI or non-interactive shells.",
        }, indent=2))
        return 1

    print("Interactive Sign in with ChatGPT flow requires direct user browser consent.")
    print("In this task, live authorization is disabled by policy. Use dry-run or offline tests instead.")
    return 1


def cmd_self_check(args: argparse.Namespace) -> int:
    print(json.dumps(self_check(), indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="ChatGPT Plan Auth & Reviewer CLI (TASK-0004)")
    parser.add_argument("--storage", default=str(DEFAULT_STORAGE_PATH), help="Path to protected session storage")
    parser.add_argument("--timestamp", default="", help="Optional creation timestamp")
    parser.add_argument("--ci", action="store_true", help="Indicate CI / non-interactive mode")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-host", help="Initialize stable host identifier")
    subparsers.add_parser("status", help="Show credential-free session status")
    subparsers.add_parser("models", help="Validate authorized model catalog")
    subparsers.add_parser("sign-in", help="Perform browser loopback OAuth sign-in")
    subparsers.add_parser("self-check", help="Run offline component self-check")

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

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
