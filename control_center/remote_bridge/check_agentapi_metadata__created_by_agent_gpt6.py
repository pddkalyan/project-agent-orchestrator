"""Read-only, redacted Windows Antigravity conversation-metadata verifier.

Run from a trusted Windows PowerShell session or the Antigravity tool sandbox.
This never sends messages, changes configs, or prints raw metadata/IDs/paths.

Usage:
    python control_center/remote_bridge/check_agentapi_metadata__created_by_agent_gpt6.py
    python .../check_agentapi_metadata__created_by_agent_gpt6.py --agentapi C:\\path\\to\\agentapi.bat

No extra packages or paid services needed.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def summarize_metadata(output, expected_id):
    """Only reveal fixed booleans and output field *names*, never private values."""
    try:
        document = json.loads(output)
    except (TypeError, ValueError):
        return {"json_object": False, "exact_conversation_id_match": False,
                "field_names": [], "verdict": "FAIL_CLOSED"}
    if not isinstance(document, dict):
        return {"json_object": False, "exact_conversation_id_match": False,
                "field_names": [], "verdict": "FAIL_CLOSED"}
    # Do not leak arbitrary field names: some APIs may use user-supplied
    # strings or identifiers as keys. Reveal only a fixed allowlist of
    # schema labels to help diagnose camelCase vs snake_case safely.
    public_schema_keys = {
        "conversation_id", "conversationId", "id", "workspace", "workspace_dir",
        "workspaceUri", "project", "projectPath", "metadata", "status", "title",
    }
    names = sorted(key for key in document
                   if isinstance(key, str) and key in public_schema_keys)
    match = bool(expected_id) and document.get("conversation_id") == expected_id
    return {"json_object": True, "exact_conversation_id_match": bool(match),
            "field_names": names, "verdict": "SCHEMA_VERIFIED" if match else "FAIL_CLOSED"}


def main():
    parser = argparse.ArgumentParser(description="Safe read-only agentapi metadata check")
    parser.add_argument("--agentapi", default="", help="Trusted executable path (optional)")
    args = parser.parse_args()
    state_file = Path.home() / ".antigravity_bridge_state" / "config.json"
    try:
        config = json.loads(state_file.read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            raise ValueError("Config must be a JSON object")
        conversation_id = config.get("conversation_id")
        if not isinstance(conversation_id, str) or not conversation_id.strip():
            raise ValueError("Conversation ID not configured")
        conversation_id = conversation_id.strip()
    except (OSError, ValueError):
        print(json.dumps({"verdict": "CONFIG_UNAVAILABLE", "sends_message": False}))
        return 2

    fallback = Path.home() / ".gemini" / "antigravity" / "bin" / "agentapi.bat"
    executable = args.agentapi or shutil.which("agentapi") or (
        str(fallback) if fallback.is_file() else ""
    )
    if not executable:
        print(json.dumps({"verdict": "AGENTAPI_UNAVAILABLE", "sends_message": False}))
        return 2

    try:
        process = subprocess.run(
            [executable, "get-conversation-metadata", conversation_id],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,  # Do not expose paths, IDs, or tokens.
            text=True,
            shell=False,
            timeout=20,
        )
        if process.returncode != 0:
            print(json.dumps({"verdict": "METADATA_COMMAND_FAILED",
                              "sends_message": False}))
            return 1
        result = summarize_metadata(process.stdout, conversation_id)
        result["sends_message"] = False
        print(json.dumps(result, sort_keys=True))
        return 0 if result["verdict"] == "SCHEMA_VERIFIED" else 1
    except (OSError, subprocess.TimeoutExpired):
        print(json.dumps({"verdict": "METADATA_COMMAND_FAILED",
                          "sends_message": False}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
