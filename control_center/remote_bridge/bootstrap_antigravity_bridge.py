"""Fail-closed Windows Antigravity ping-only bridge bootstrap.

A preflight is strictly read-only. --activate-ping requires an existing,
owner-controlled github_bridge sidecar and an exact reviewed source artifact.
No shell commands or native agent messages are dispatched by this installer.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

REPO_NAME = "pddkalyan/project-agent-orchestrator"
AUTHORIZED_USER_ID = 159762630
EXPECTED_BRANCH = "agent/control-center-standalone"
EXPECTED_HEAD_SHA = "15b148cfbc3acc0eec10c51ab8e8bf413613f23f"
SAFE_SOURCE_COMMIT = "1123e6ef988d18155c42257c54dd318e3f8aac2b"
SAFE_SOURCE_BLOB = "adc3ac6c9c7156a1f135caaedb87fd89c9284b5e"
SAFE_SOURCE_PATH = "control_center/remote_bridge/bridge.py"

TOKEN_PATTERNS = (
    re.compile(r"github_pat_[A-Za-z0-9_]{16,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{16,}", re.I),
    re.compile(r"(?i)bearer\s+\S+"),
)


class BootstrapBlocked(Exception):
    """Identified, fail-closed operator issue without secret-bearing details."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def sanitize_text(text: str) -> str:
    value = str(text or "")
    for pattern in TOKEN_PATTERNS:
        value = pattern.sub("[REDACTED_SECRET]", value)
    return value


def find_agentapi_executable(custom_path: str = "") -> str:
    if custom_path and Path(custom_path).is_file():
        return str(Path(custom_path).resolve())
    candidate = shutil.which("agentapi")
    if candidate:
        return candidate
    for part in ("antigravity-cli", "antigravity"):
        filename = Path.home() / ".gemini" / part / "bin" / "agentapi.bat"
        if filename.is_file():
            return str(filename)
    return ""


def _run(cmd: list[str], cwd: Optional[str] = None, runner: Optional[Callable] = None):
    if runner:
        value = runner(cmd, cwd=cwd)
        return value[0], value[1], value[2]
    try:
        result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, shell=False, timeout=30)
        return result.returncode, result.stdout, result.stderr
    except (FileNotFoundError, OSError):
        return 127, "", "COMMAND_UNAVAILABLE"
    except subprocess.TimeoutExpired:
        return 124, "", "COMMAND_TIMEOUT"


def run_subprocess_with_retry(cmd, cwd=None, max_retries=3, runner=None, sleep_fn=time.sleep):
    count = min(max(1, int(max_retries)), 3)
    last = (1, "", "", "CHILD_FAILED")
    for n in range(count):
        rc, stdout, stderr = _run(cmd, cwd=cwd, runner=runner)
        if rc == 0:
            return 0, stdout, "", "SUCCESS"
        category = "BINARY_NOT_FOUND" if rc == 127 else "TIMEOUT" if rc == 124 else "CHILD_FAILED"
        last = (rc, "", "", category)
        if category == "BINARY_NOT_FOUND":
            break
        if n < count - 1:
            sleep_fn(0.25 * (2 ** n))
    return last


def verify_gh_auth(max_retries=3, runner=None):
    rc, stdout, _, category = run_subprocess_with_retry(
        ["gh", "api", "user", "--jq", "{id: .id, login: .login}"],
        max_retries=max_retries,
        runner=runner,
    )
    if rc != 0:
        return False, "GH_CLI_MISSING" if rc == 127 else "GH_API_AUTH_FAILED", {"category": category}
    try:
        user = json.loads(stdout.strip())
        if (not isinstance(user, dict) or type(user.get("id")) is not int
                or user["id"] != AUTHORIZED_USER_ID or user.get("login") != "pddkalyan"):
            return False, "GH_OWNER_MISMATCH", {"category": "OWNER_MISMATCH"}
        return True, "AUTH_SUCCESS", {"user_id": AUTHORIZED_USER_ID}
    except (ValueError, TypeError):
        return False, "GH_RESPONSE_INVALID", {"category": "MALFORMED"}


def _repo_name_from_remote(url: str) -> Optional[str]:
    if not isinstance(url, str):
        return None
    remote = url.strip().removesuffix(".git").rstrip("/")
    match = re.fullmatch(r"https://github\.com/([A-Za-z0-9_-]+)/([A-Za-z0-9_-]+)", remote)
    if not match:
        match = re.fullmatch(r"git@github\.com:([A-Za-z0-9_-]+)/([A-Za-z0-9_-]+)", remote)
    return "/".join(match.groups()).lower() if match else None


def validate_workspace(workspace_dir: str, runner=None):
    workspace = Path(workspace_dir).resolve()
    if not workspace.is_dir():
        return False, "WORKSPACE_MISSING", {}
    checks = (
        (["git", "remote", "get-url", "origin"], "remote"),
        (["git", "rev-parse", "--abbrev-ref", "HEAD"], "branch"),
        (["git", "rev-parse", "HEAD"], "head"),
        (["git", "status", "--porcelain"], "dirty"),
    )
    values = {}
    for cmd, key in checks:
        rc, out, _ = _run(cmd, cwd=str(workspace), runner=runner)
        if rc != 0:
            return False, f"GIT_{key.upper()}_FAILED", {}
        values[key] = out.strip()
    if _repo_name_from_remote(values["remote"]) != REPO_NAME:
        return False, "REPO_MISMATCH", {}
    if values["branch"] != EXPECTED_BRANCH:
        return False, "BRANCH_MISMATCH", {}
    if values["head"].lower() != EXPECTED_HEAD_SHA:
        return False, "HEAD_SHA_MISMATCH", {}
    return True, "WORKSPACE_VALID", {"branch": EXPECTED_BRANCH, "head_sha": EXPECTED_HEAD_SHA, "is_dirty": bool(values["dirty"]), "workspace": str(workspace)}


def _git_blob_id(blob: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(blob)).encode("ascii") + b"\x00" + blob).hexdigest()


def _verified_source(content: bytes) -> bool:
    return _git_blob_id(content) == SAFE_SOURCE_BLOB


def _read_trusted_candidate(file_path: Path) -> Optional[bytes]:
    if not file_path.is_file() or file_path.is_symlink():
        return None
    try:
        data = file_path.read_bytes()
        return data if _verified_source(data) else None
    except OSError:
        return None


def retrieve_bridge_source(workspace_dir: str, runner=None, max_retries=3, check_script_dir=True):
    """Never accept the default GitHub branch or unverified local source."""
    checked = []
    if check_script_dir:
        checked.append(Path(__file__).resolve().parent / "bridge.py")
    checked.append(Path(workspace_dir) / SAFE_SOURCE_PATH)
    for path in checked:
        source = _read_trusted_candidate(path)
        if source is not None:
            return source, "SOURCE_RETRIEVED", {"source": "PINNED_LOCAL"}

    endpoint = f"repos/{REPO_NAME}/contents/{SAFE_SOURCE_PATH}?ref={SAFE_SOURCE_COMMIT}"
    rc, out, _, cat = run_subprocess_with_retry(["gh", "api", endpoint], runner=runner, max_retries=max_retries)
    if rc != 0:
        return None, "PINNED_SOURCE_UNAVAILABLE", {"category": cat}
    try:
        payload = json.loads(out)
        if not isinstance(payload, dict) or payload.get("sha") != SAFE_SOURCE_BLOB:
            return None, "PINNED_SOURCE_MISMATCH", {}
        if payload.get("encoding") != "base64":
            return None, "PINNED_SOURCE_MISMATCH", {}
        content = base64.b64decode(payload["content"], validate=False)
        if not _verified_source(content):
            return None, "PINNED_SOURCE_MISMATCH", {}
        return content, "SOURCE_RETRIEVED", {"source": "PINNED_GITHUB_API"}
    except (ValueError, TypeError, KeyError):
        return None, "PINNED_SOURCE_INVALID", {}


def _safe_existing_paths():
    home = Path.home()
    state = home / ".antigravity_bridge_state"
    sidecars = home / ".gemini" / "config" / "sidecars"
    installed = sidecars / "github_bridge"
    return state, installed / "bridge.py", installed / "sidecar.json", sidecars / "antigravity_bridge" / "sidecar.json"


def _read_existing_config(state: Path, workspace: str) -> dict:
    config_path = state / "config.json"
    if not config_path.is_file() or config_path.is_symlink():
        raise BootstrapBlocked("PRIVATE_CONFIG_MISSING")
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        raise BootstrapBlocked("PRIVATE_CONFIG_INVALID")
    if not isinstance(config, dict) or not isinstance(config.get("conversation_id"), str) or not config["conversation_id"].strip():
        raise BootstrapBlocked("PRIVATE_CONVERSATION_UNBOUND")
    if not isinstance(config.get("workspace_dir"), str):
        raise BootstrapBlocked("PRIVATE_WORKSPACE_MISSING")
    if os.path.normcase(os.path.abspath(config["workspace_dir"])) != os.path.normcase(os.path.abspath(workspace)):
        raise BootstrapBlocked("PRIVATE_WORKSPACE_MISMATCH")
    if config.get("dispatch_enabled") is not False or config.get("allow_dirty_continue") is not False:
        raise BootstrapBlocked("BROAD_DISPATCH_MUST_BE_DISABLED")
    if config.get("status_enabled") is not True:
        raise BootstrapBlocked("STATUS_RECEIPTS_REQUIRED")
    return config


def _require_existing_sidecar(source_path: Path, manifest_path: Path, competing_manifest: Path):
    if competing_manifest.exists():
        raise BootstrapBlocked("COMPETING_SIDECAR_PRESENT")
    if not source_path.is_file() or source_path.is_symlink() or not manifest_path.is_file() or manifest_path.is_symlink():
        raise BootstrapBlocked("EXISTING_GITHUB_BRIDGE_MISSING")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        raise BootstrapBlocked("SIDECAR_MANIFEST_INVALID")
    if not isinstance(manifest, dict) or not isinstance(manifest.get("args"), list):
        raise BootstrapBlocked("SIDECAR_MANIFEST_INVALID")
    args = manifest["args"]
    # The known installed service starts bridge.py relative to its own dir.
    if len(args) != 1 or args[0] not in ("bridge.py", str(source_path)):
        raise BootstrapBlocked("SIDECAR_TARGET_MISMATCH")


def _atomic_replace(target: Path, data: bytes):
    # Same-directory temp protects atomicity; named file is always cleaned up.
    temp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        with open(temp, "xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def _install_ping_verified(state: Path, source_path: Path, source_data: bytes, config: dict):
    config_path = state / "config.json"
    previous_source = source_path.read_bytes()
    previous_config = config_path.read_bytes()
    backups = state / "backups" / f"ping_{uuid.uuid4().hex}"
    backups.mkdir(parents=True, exist_ok=False)
    (backups / "bridge.py").write_bytes(previous_source)
    (backups / "config.json").write_bytes(previous_config)
    try:
        new_config = dict(config)
        new_config["dispatch_enabled"] = False
        new_config["allow_dirty_continue"] = False
        new_config["status_enabled"] = True
        new_config["ping_enabled"] = True
        _atomic_replace(source_path, source_data)
        _atomic_replace(config_path, (json.dumps(new_config, indent=2) + "\n").encode("utf-8"))
    except Exception:
        try:
            _atomic_replace(source_path, previous_source)
            _atomic_replace(config_path, previous_config)
        except Exception:
            raise BootstrapBlocked("ROLLBACK_FAILED_CHECK_LOCAL_BACKUP")
        raise BootstrapBlocked("INSTALLATION_ROLLED_BACK")
    return "PING_ONLY_INSTALLED_RELOAD_REQUIRED"


def execute_bootstrap(workspace_dir: str, *, activate_ping=False, defer_metadata_to_sidecar=False, runner=None, **_unused):
    report = {"native_metadata": "DEFERRED_TO_SIDECAR_RUNTIME" if defer_metadata_to_sidecar else "UNVERIFIED",
              "native_message_sent": "NO", "status": "BLOCKED", "worktree_dirty_preserved": True}
    try:
        ok, code, ws = validate_workspace(workspace_dir, runner=runner)
        if not ok:
            raise BootstrapBlocked(code)
        report["worktree_dirty_preserved"] = ws["is_dirty"]
        auth_ok, auth_code, _ = verify_gh_auth(runner=runner)
        if not auth_ok:
            raise BootstrapBlocked(auth_code)
        source_data, source_code, _ = retrieve_bridge_source(ws["workspace"], runner=runner)
        if source_data is None:
            raise BootstrapBlocked(source_code)
        try:
            compile(source_data, SAFE_SOURCE_PATH, "exec")
        except (SyntaxError, ValueError):
            raise BootstrapBlocked("PINNED_SOURCE_UNCOMPILEABLE")
        state, sidecar_script, manifest, competing = _safe_existing_paths()
        _require_existing_sidecar(sidecar_script, manifest, competing)
        config = _read_existing_config(state, ws["workspace"])
        if activate_ping:
            report["status"] = _install_ping_verified(state, sidecar_script, source_data, config)
        else:
            report["status"] = "READY_TO_INSTALL_READ_ONLY"
        return 0, report
    except BootstrapBlocked as exc:
        report["status"] = "BLOCKED: " + exc.code
        return 1, report
    except OSError:
        report["status"] = "BLOCKED: HOST_IO_FAILURE"
        return 1, report


def main():
    parser = argparse.ArgumentParser(description="Fail-closed pinned Antigravity ping-only installer")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--defer-metadata-to-sidecar", action="store_true")
    parser.add_argument("--activate-ping", action="store_true")
    opts = parser.parse_args()
    exit_code, report = execute_bootstrap(opts.workspace, activate_ping=opts.activate_ping,
                                          defer_metadata_to_sidecar=opts.defer_metadata_to_sidecar)
    print("NATIVE_METADATA:", report["native_metadata"])
    print("WORKTREE_DIRTY_PRESERVED:", report["worktree_dirty_preserved"])
    print(report["status"])
    print("NATIVE_MESSAGE_SENT:", report["native_message_sent"])
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
