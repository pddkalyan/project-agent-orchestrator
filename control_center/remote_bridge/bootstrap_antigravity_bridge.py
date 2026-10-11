"""
Windows Bridge Bootstrap Installer & Repair Tool for Antigravity Remote Bridge.

Pure standard library implementation for safe Windows-native one-time
bootstrap and offline/pinned source repair without external dependencies.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import py_compile
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

REPO_NAME = "pddkalyan/project-agent-orchestrator"
AUTHORIZED_USER_ID = 159762630
EXPECTED_BRANCH = "agent/control-center-standalone"
SIDECAR_ID = "antigravity_bridge"

DEFAULT_CONFIG_DEFAULTS = {
    "dispatch_enabled": False,
    "ping_enabled": False,
    "status_enabled": True,
    "allow_dirty_continue": False,
    "poll_interval_sec": 900,
    "min_status_interval_sec": 900,
    "expected_branch": EXPECTED_BRANCH,
    "repo": REPO_NAME,
    "authorized_user_id": AUTHORIZED_USER_ID,
    "inbox_issue": 54,
    "status_issue": 55,
}

SECRET_PATTERNS = [
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}", re.IGNORECASE),
    re.compile(r"bearer\s+\S+", re.IGNORECASE),
]


def sanitize_text(text: str) -> str:
    if not text:
        return ""
    result = str(text)
    for pattern in SECRET_PATTERNS:
        result = pattern.sub("[REDACTED_SECRET]", result)
    return result


def find_agentapi_executable(custom_path: str = "") -> str:
    if custom_path and os.path.exists(custom_path):
        return os.path.abspath(custom_path)

    which_path = shutil.which("agentapi")
    if which_path:
        return which_path

    home = Path.home()
    primary = home / ".gemini" / "antigravity-cli" / "bin" / "agentapi.bat"
    if primary.is_file():
        return str(primary)

    secondary = home / ".gemini" / "antigravity" / "bin" / "agentapi.bat"
    if secondary.is_file():
        return str(secondary)

    return ""


def run_subprocess_with_retry(
    cmd: List[str],
    cwd: Optional[str] = None,
    max_retries: int = 3,
    initial_delay: float = 0.5,
    runner: Optional[Callable] = None,
) -> Tuple[int, str, str, str]:
    """Run subprocess with bounded retry and sanitized output categorization."""
    if runner is not None:
        return runner(cmd, cwd=cwd)

    last_returncode = -1
    last_stdout = ""
    last_stderr = ""
    category = "UNKNOWN"

    for attempt in range(max_retries):
        try:
            proc = subprocess.run(
                cmd,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                shell=False,
                timeout=30,
            )
            last_returncode = proc.returncode
            last_stdout = proc.stdout
            last_stderr = sanitize_text(proc.stderr)

            if proc.returncode == 0:
                return 0, last_stdout, last_stderr, "SUCCESS"

            # Categorize error
            stderr_lower = last_stderr.lower()
            if "rate limit" in stderr_lower or "429" in stderr_lower:
                category = "RATE_LIMITED"
            elif "unauthorized" in stderr_lower or "401" in stderr_lower or "bad credentials" in stderr_lower:
                category = "UNAUTHORIZED"
            elif "could not resolve host" in stderr_lower or "connection refused" in stderr_lower or "timeout" in stderr_lower:
                category = "NETWORK_ERROR"
            else:
                category = "SUBPROCESS_ERROR"

        except FileNotFoundError:
            return 127, "", f"Executable not found: {cmd[0]}", "BINARY_NOT_FOUND"
        except subprocess.TimeoutExpired:
            last_returncode = 124
            last_stderr = "Subprocess timed out after 30 seconds"
            category = "NETWORK_TIMEOUT"
        except Exception as exc:
            last_returncode = 1
            last_stderr = sanitize_text(str(exc))
            category = "SYSTEM_ERROR"

        if attempt < max_retries - 1:
            time.sleep(initial_delay * (2 ** attempt))

    return last_returncode, last_stdout, last_stderr, category


def verify_gh_auth(
    max_retries: int = 3,
    runner: Optional[Callable] = None,
) -> Tuple[bool, str, Dict[str, Any]]:
    """Verify GitHub CLI auth status and user ID match with retry and sanitized errors."""
    cmd = ["gh", "api", "user", "--jq", "{id: .id, login: .login}"]
    ret, stdout, stderr, category = run_subprocess_with_retry(
        cmd, max_retries=max_retries, runner=runner
    )

    if ret == 127 or category == "BINARY_NOT_FOUND":
        return False, "BLOCKED: GH_CLI_MISSING", {"category": category, "stderr": stderr}

    if ret != 0:
        return False, "BLOCKED: GH_API_AUTH_FAILED", {"category": category, "stderr": stderr, "exit_code": ret}

    try:
        data = json.loads(stdout.strip())
        user_id = data.get("id")
        login = data.get("login")

        if user_id != AUTHORIZED_USER_ID:
            return False, "BLOCKED: GH_API_AUTH_FAILED", {
                "category": "WRONG_USER",
                "expected_id": AUTHORIZED_USER_ID,
                "found_login": sanitize_text(str(login)),
            }

        return True, "AUTH_SUCCESS", {"user_id": user_id, "login": login}
    except (json.JSONDecodeError, AttributeError):
        return False, "BLOCKED: GH_API_AUTH_FAILED", {"category": "MALFORMED_RESPONSE", "stdout": stdout}


def retrieve_bridge_source(
    workspace_dir: str,
    max_retries: int = 3,
    allow_raw: bool = False,
    runner: Optional[Callable] = None,
    http_fetcher: Optional[Callable] = None,
    check_script_dir: bool = True,
) -> Tuple[Optional[str], str, Dict[str, Any]]:
    """
    Retrieve bridge.py source code using local checkout, GitHub API, or raw HTTP fallback.
    Returns (source_code, status_code, diagnostics).
    """
    diagnostics: Dict[str, Any] = {}

    # 1. Try reading from local repository checkout if available
    local_bridge_path = Path(workspace_dir) / "control_center" / "remote_bridge" / "bridge.py"
    if local_bridge_path.is_file():
        try:
            content = local_bridge_path.read_text(encoding="utf-8")
            if "class AntigravityRemoteBridge" in content:
                diagnostics["source"] = "LOCAL_CHECKOUT"
                diagnostics["path"] = str(local_bridge_path)
                return content, "SOURCE_RETRIEVED", diagnostics
        except OSError as exc:
            diagnostics["local_error"] = str(exc)

    # Check if script directory contains bridge.py
    if check_script_dir:
        script_dir_bridge = Path(__file__).resolve().parent / "bridge.py"
        if script_dir_bridge.is_file():
            try:
                content = script_dir_bridge.read_text(encoding="utf-8")
                if "class AntigravityRemoteBridge" in content:
                    diagnostics["source"] = "SCRIPT_DIRECTORY"
                    diagnostics["path"] = str(script_dir_bridge)
                    return content, "SOURCE_RETRIEVED", diagnostics
            except OSError as exc:
                diagnostics["script_dir_error"] = str(exc)

    # 2. Try fetching via GitHub CLI API
    cmd = ["gh", "api", f"repos/{REPO_NAME}/contents/control_center/remote_bridge/bridge.py"]
    ret, stdout, stderr, category = run_subprocess_with_retry(
        cmd, max_retries=max_retries, runner=runner
    )
    if ret == 0:
        try:
            payload = json.loads(stdout)
            if isinstance(payload, dict) and "content" in payload:
                raw_b64 = payload["content"].replace("\n", "").replace("\r", "")
                content = base64.b64decode(raw_b64).decode("utf-8")
                if "class AntigravityRemoteBridge" in content:
                    diagnostics["source"] = "GITHUB_API"
                    return content, "SOURCE_RETRIEVED", diagnostics
        except Exception as exc:
            diagnostics["github_api_parse_error"] = str(exc)

    # 3. Fallback to raw.githubusercontent.com if allowed/needed
    if allow_raw or True:
        raw_url = f"https://raw.githubusercontent.com/{REPO_NAME}/{EXPECTED_BRANCH}/control_center/remote_bridge/bridge.py"
        for attempt in range(max_retries):
            try:
                if http_fetcher:
                    content = http_fetcher(raw_url)
                else:
                    req = urllib.request.Request(raw_url, headers={"User-Agent": "Antigravity-Bootstrap/1.0"})
                    with urllib.request.urlopen(req, timeout=15) as resp:
                        content = resp.read().decode("utf-8")
                if "class AntigravityRemoteBridge" in content:
                    diagnostics["source"] = "RAW_GITHUB"
                    return content, "SOURCE_RETRIEVED", diagnostics
            except Exception as exc:
                diagnostics[f"raw_attempt_{attempt}"] = sanitize_text(str(exc))
                if attempt < max_retries - 1:
                    time.sleep(0.5 * (2 ** attempt))

    return None, "BLOCKED: PINNED_SOURCE_DOWNLOAD_FAILED", diagnostics


class BackupManager:
    """Manages staging backups and restoration of bridge configuration and scripts."""

    def __init__(self, state_dir: Path):
        self.state_dir = state_dir
        self.backup_dir = state_dir / "backups" / f"backup_{int(time.time())}"
        self.backups: List[Tuple[Path, Path]] = []

    def backup_file(self, target_path: Path) -> Optional[Path]:
        if not target_path.exists():
            return None
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = self.backup_dir / target_path.name
        shutil.copy2(target_path, backup_path)
        self.backups.append((target_path, backup_path))
        return backup_path

    def restore(self) -> None:
        for target_path, backup_path in reversed(self.backups):
            if backup_path.exists():
                shutil.copy2(backup_path, target_path)
        if self.backup_dir.exists():
            shutil.rmtree(self.backup_dir, ignore_errors=True)


def validate_workspace(workspace_dir: str, runner: Optional[Callable] = None) -> Tuple[bool, str, Dict[str, Any]]:
    """Validate workspace path, Git remote, branch, and dirty status."""
    if not workspace_dir or not isinstance(workspace_dir, str):
        return False, "WORKSPACE_INVALID: Path required", {}

    abs_ws = os.path.abspath(workspace_dir)
    if not os.path.exists(abs_ws) or not os.path.isdir(abs_ws):
        return False, f"WORKSPACE_INVALID: Directory does not exist at {abs_ws}", {}

    # Check Git repo
    cmd_remote = ["git", "remote", "get-url", "origin"]
    ret_r, out_r, err_r, _ = run_subprocess_with_retry(cmd_remote, cwd=abs_ws, runner=runner)
    if ret_r != 0 or REPO_NAME.lower() not in out_r.lower():
        return False, "WORKSPACE_INVALID: Git remote origin mismatch", {"stderr": err_r}

    cmd_branch = ["git", "rev-parse", "--abbrev-ref", "HEAD"]
    ret_b, out_b, _, _ = run_subprocess_with_retry(cmd_branch, cwd=abs_ws, runner=runner)
    branch = out_b.strip() if ret_b == 0 else "UNKNOWN"

    cmd_status = ["git", "status", "--porcelain"]
    ret_s, out_s, _, _ = run_subprocess_with_retry(cmd_status, cwd=abs_ws, runner=runner)
    is_dirty = bool(out_s.strip()) if ret_s == 0 else True

    return True, "WORKSPACE_VALID", {
        "abs_path": abs_ws,
        "branch": branch,
        "is_dirty": is_dirty,
    }


def execute_bootstrap(
    workspace_dir: str,
    defer_metadata_to_sidecar: bool = False,
    activate_ping: bool = False,
    custom_agentapi: str = "",
    max_retries: int = 3,
    runner: Optional[Callable] = None,
    http_fetcher: Optional[Callable] = None,
    check_script_dir: bool = True,
) -> Tuple[int, Dict[str, Any]]:
    """
    Execute one-time bootstrap repair workflow.
    Returns (exit_code, result_dict).
    """
    output: Dict[str, Any] = {
        "native_metadata": "DEFERRED_TO_SIDECAR_RUNTIME" if defer_metadata_to_sidecar else "UNVERIFIED",
        "worktree_dirty_preserved": True,
        "native_message_sent": "NO",
        "status": "UNKNOWN",
        "diagnostics": {},
    }

    # 1. Validate Workspace
    ws_ok, ws_msg, ws_info = validate_workspace(workspace_dir, runner=runner)
    if not ws_ok:
        output["status"] = ws_msg
        return 1, output

    output["worktree_dirty_preserved"] = ws_info.get("is_dirty", True)
    abs_workspace = ws_info["abs_path"]

    # 2. Verify GitHub CLI Authentication
    auth_ok, auth_status, auth_diag = verify_gh_auth(max_retries=max_retries, runner=runner)
    if not auth_ok:
        output["status"] = auth_status
        output["diagnostics"]["gh_auth"] = auth_diag
        return 1, output

    # 3. Retrieve Bridge Source
    source_code, source_status, source_diag = retrieve_bridge_source(
        abs_workspace, max_retries=max_retries, runner=runner, http_fetcher=http_fetcher, check_script_dir=check_script_dir
    )
    if not source_code:
        output["status"] = source_status
        output["diagnostics"]["source"] = source_diag
        return 1, output

    # 4. Compile Source Verification
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as tmp:
        tmp.write(source_code)
        tmp_path = tmp.name

    try:
        py_compile.compile(tmp_path, doraise=True)
    except py_compile.PyCompileError as exc:
        output["status"] = "BLOCKED: CORRUPTED_SOURCE_CODE"
        output["diagnostics"]["compile_error"] = str(exc)
        return 1, output
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    # 5. Backup & Installation Setup
    state_dir = Path.home() / ".antigravity_bridge_state"
    state_dir.mkdir(parents=True, exist_ok=True)
    backup_mgr = BackupManager(state_dir)

    installed_bridge_dir = state_dir / "installed"
    installed_bridge_dir.mkdir(parents=True, exist_ok=True)
    installed_bridge_path = installed_bridge_dir / "bridge.py"

    config_path = state_dir / "config.json"

    # Backup existing
    backup_mgr.backup_file(installed_bridge_path)
    backup_mgr.backup_file(config_path)

    try:
        # Write installed bridge.py
        installed_bridge_path.write_text(source_code, encoding="utf-8")

        # Load or create config
        existing_config: Dict[str, Any] = {}
        if config_path.is_file():
            try:
                existing_config = json.loads(config_path.read_text(encoding="utf-8"))
            except Exception:
                existing_config = {}

        new_config = dict(DEFAULT_CONFIG_DEFAULTS)
        new_config.update(existing_config)
        new_config["workspace_dir"] = abs_workspace

        if activate_ping:
            new_config["ping_enabled"] = True
            new_config["status_enabled"] = True

        # Enforce safe defaults for dispatch
        new_config["dispatch_enabled"] = False
        new_config["allow_dirty_continue"] = False

        config_path.write_text(json.dumps(new_config, indent=2), encoding="utf-8")

        # Setup Sidecar Configuration
        sidecar_dir = Path.home() / ".gemini" / "config" / "sidecars" / SIDECAR_ID
        sidecar_dir.mkdir(parents=True, exist_ok=True)
        sidecar_json_path = sidecar_dir / "sidecar.json"

        sidecar_config = {
            "display_name": "Antigravity Bridge",
            "description": "ChatGPT ↔ GitHub ↔ Windows Antigravity Bridge",
            "command": sys.executable,
            "args": [str(installed_bridge_path)],
            "restart_policy": "on-failure",
            "env": {
                "PYTHONUNBUFFERED": "1"
            }
        }
        sidecar_json_path.write_text(json.dumps(sidecar_config, indent=2), encoding="utf-8")

        output["status"] = "READY_TO_INSTALL" if not activate_ping else "BOOTSTRAP_ACTIVATED"
        return 0, output

    except Exception as exc:
        backup_mgr.restore()
        output["status"] = f"BLOCKED: INSTALLATION_FAILED:{str(exc)}"
        return 1, output


def main() -> int:
    parser = argparse.ArgumentParser(description="Windows Bridge Bootstrap Repair Installer")
    parser.add_argument("--workspace", required=True, help="Path to workspace directory")
    parser.add_argument("--defer-metadata-to-sidecar", action="store_true", help="Defer metadata check to sidecar")
    parser.add_argument("--activate-ping", action="store_true", help="Opt-in ping_enabled")
    parser.add_argument("--agentapi", default="", help="Custom agentapi executable path")

    args = parser.parse_args()

    exit_code, result = execute_bootstrap(
        workspace_dir=args.workspace,
        defer_metadata_to_sidecar=args.defer_metadata_to_sidecar,
        activate_ping=args.activate_ping,
        custom_agentapi=args.agentapi,
    )

    print(f"NATIVE_METADATA: {result.get('native_metadata')}")
    print(f"WORKTREE_DIRTY_PRESERVED: {result.get('worktree_dirty_preserved')}")
    print(f"{result.get('status')}")
    print(f"NATIVE_MESSAGE_SENT: {result.get('native_message_sent')}")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
