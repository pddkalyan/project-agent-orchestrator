"""
Antigravity Sidecar GitHub Command/Status Bridge (Disabled by default)

Owner-approved bridge development (Issue #44 subtask).
Pure standard library Python implementation.
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

REPO_NAME = "pddkalyan/project-agent-orchestrator"
AUTHORIZED_USER_ID = 159762630
INBOX_ISSUE_NUMBER = 54
EXACT_INBOX_ISSUE_URL = f"https://api.github.com/repos/{REPO_NAME}/issues/{INBOX_ISSUE_NUMBER}"
STATUS_ISSUE_NUMBER = 55
ALLOWED_ISSUES = {44, 47}
ALLOWED_ACTIONS = {"status_request", "continue_issue"}
EXPECTED_BRANCH = "agent/control-center-standalone"
ENVELOPE_MARKER = "ANTIGRAVITY_CONTROL_V1"

# Enum Status Codes for Public Status Receipts
STATUS_CODES = {
    "DISPATCH_SUCCESS": "DISPATCH_SUCCESS",
    "DISPATCH_FAILED": "DISPATCH_FAILED",
    "STATUS_REQUEST_PROCESSED": "STATUS_REQUEST_PROCESSED",
    "CONFIG_INVALID": "CONFIG_INVALID",
    "NO_OP": "NO_OP",
    "CLAIM_FAILED": "CLAIM_FAILED",
    "INVALID_PAYLOAD": "INVALID_PAYLOAD",
    "RATE_LIMITED": "RATE_LIMITED",
    "INITIALIZED": "INITIALIZED",
}

SAFE_PROMPT_TEMPLATES = {
    44: "Supervise Issue #44 subtask execution under strict zero-spend, no merges, no force push, no remote privilege constraints.",
    47: "Supervise Issue #47 subtask execution under strict zero-spend, no merges, no force push, no remote privilege constraints.",
}

COMMAND_ID_REGEX = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
    r"|^[a-zA-Z0-9_\-]{8,64}$"
)


def parse_iso_timestamp(ts_str):
    if not ts_str or not isinstance(ts_str, str):
        return None
    try:
        ts_str = ts_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def default_subprocess_runner(args, cwd=None, input_data=None):
    proc = subprocess.run(
        args,
        cwd=cwd,
        input=input_data,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=False,
    )
    return proc.returncode, proc.stdout, proc.stderr


class AntigravityRemoteBridge:
    def __init__(
        self,
        config=None,
        state_dir=None,
        runner=None,
        now_fn=None,
    ):
        self.state_dir = state_dir or os.path.expanduser("~/.antigravity_bridge_state")
        os.makedirs(self.state_dir, exist_ok=True)

        self.runner = runner or default_subprocess_runner
        self.now_fn = now_fn or (lambda: datetime.now(timezone.utc))

        self.watermark_file = os.path.join(self.state_dir, "watermark.json")
        self.claims_dir = os.path.join(self.state_dir, "claims")
        self.status_file = os.path.join(self.state_dir, "status_state.json")
        self.config_file = os.path.join(self.state_dir, "config.json")
        os.makedirs(self.claims_dir, exist_ok=True)

        self.config = {
            "workspace_dir": "",
            "dispatch_enabled": False,
            "status_enabled": False,
            "conversation_id": "",
            "poll_interval_sec": 900,
            "min_status_interval_sec": 900,
            "expected_branch": EXPECTED_BRANCH,
            "repo": REPO_NAME,
            "authorized_user_id": AUTHORIZED_USER_ID,
            "inbox_issue": INBOX_ISSUE_NUMBER,
            "status_issue": STATUS_ISSUE_NUMBER,
        }

        self.config_error = None
        self._load_local_config()

        if config:
            self.config.update(config)

    def _load_local_config(self):
        if not os.path.exists(self.config_file):
            self.config_error = "Local configuration file missing"
            return

        try:
            with open(self.config_file, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if not isinstance(loaded, dict):
                self.config_error = "Local configuration is not a JSON object"
                return

            self.config["workspace_dir"] = loaded.get("workspace_dir", "")
            self.config["dispatch_enabled"] = bool(loaded.get("dispatch_enabled", False))
            self.config["status_enabled"] = bool(loaded.get("status_enabled", False))
            self.config["conversation_id"] = str(loaded.get("conversation_id", "")).strip()
            if "poll_interval_sec" in loaded:
                self.config["poll_interval_sec"] = int(loaded.get("poll_interval_sec", 900))
        except Exception as e:
            self.config_error = f"Malformed local configuration: {str(e)}"

    def is_config_valid(self):
        if self.config_error:
            return False, self.config_error
        workspace = self.config.get("workspace_dir", "").strip()
        if not workspace:
            return False, "workspace_dir is not configured"
        if not os.path.isabs(workspace):
            return False, "workspace_dir must be an absolute path"
        if not os.path.isdir(workspace):
            return False, "workspace_dir directory does not exist"
        return True, "Valid"

    def get_watermark(self):
        if not os.path.exists(self.watermark_file):
            return None, None
        try:
            with open(self.watermark_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict) or "last_comment_id" not in data:
                return None, "WATERMARK_CORRUPT"
            comment_id = data.get("last_comment_id")
            if not isinstance(comment_id, int):
                return None, "WATERMARK_CORRUPT"
            return comment_id, None
        except Exception:
            return None, "WATERMARK_CORRUPT"

    def set_watermark(self, comment_id):
        temp_file = self.watermark_file + ".tmp"
        payload = {"last_comment_id": comment_id, "updated_at": self.now_fn().isoformat()}
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(temp_file, self.watermark_file)

    def get_local_git_info(self):
        valid_cfg, msg = self.is_config_valid()
        if not valid_cfg:
            return {"branch": "UNKNOWN", "head_sha": "UNKNOWN", "is_dirty": True, "valid_repo": False, "error": msg}

        workspace = self.config["workspace_dir"]

        ret_r, remote_out, _ = self.runner(["git", "remote", "get-url", "origin"], cwd=workspace)
        if ret_r != 0 or REPO_NAME not in remote_out:
            return {"branch": "UNKNOWN", "head_sha": "UNKNOWN", "is_dirty": True, "valid_repo": False, "error": "Git remote origin mismatch"}

        ret_b, branch, _ = self.runner(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=workspace)
        ret_h, head_sha, _ = self.runner(["git", "rev-parse", "HEAD"], cwd=workspace)
        ret_s, status, _ = self.runner(["git", "status", "--porcelain"], cwd=workspace)

        current_branch = branch.strip() if ret_b == 0 else "UNKNOWN"
        current_sha = head_sha.strip() if ret_h == 0 else "UNKNOWN"
        is_dirty = bool(status.strip()) if ret_s == 0 else True

        valid_repo = (
            ret_b == 0
            and ret_h == 0
            and current_branch == self.config["expected_branch"]
            and bool(re.match(r"^[0-9a-fA-F]{40}$", current_sha))
        )

        return {
            "branch": current_branch,
            "head_sha": current_sha,
            "is_dirty": is_dirty,
            "valid_repo": valid_repo,
        }

    def fetch_inbox_comments(self):
        inbox_issue = INBOX_ISSUE_NUMBER
        repo = REPO_NAME
        all_comments = []
        page = 1

        while True:
            endpoint = f"repos/{repo}/issues/{inbox_issue}/comments?per_page=100&page={page}"
            ret, stdout, stderr = self.runner(["gh", "api", endpoint])
            if ret != 0:
                return None, "GH_API_FAILED"
            try:
                comments = json.loads(stdout)
                if not isinstance(comments, list):
                    return None, "GH_API_INVALID_RESPONSE"
                if not comments:
                    break
                all_comments.extend(comments)
                if len(comments) < 100:
                    break
                page += 1
            except Exception:
                return None, "GH_API_JSON_ERROR"

        return all_comments, None

    def parse_command_envelope(self, comment_body):
        if not isinstance(comment_body, str) or ENVELOPE_MARKER not in comment_body:
            return None, "MISSING_MARKER"

        json_str = comment_body
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", comment_body, re.DOTALL)
        if match:
            json_str = match.group(1)
        else:
            start = comment_body.find("{")
            end = comment_body.rfind("}")
            if start != -1 and end != -1 and start < end:
                json_str = comment_body[start : end + 1]

        try:
            data = json.loads(json_str)
        except Exception:
            return None, "MALFORMED_JSON"

        if not isinstance(data, dict):
            return None, "NOT_JSON_OBJECT"

        if data.get("marker") != ENVELOPE_MARKER:
            return None, "INVALID_MARKER"

        return data, None

    def validate_command(self, comment, payload, git_info):
        user_info = comment.get("user", {})
        if not isinstance(user_info, dict) or user_info.get("id") != AUTHORIZED_USER_ID:
            return False, "UNAUTHORIZED_USER"

        issue_url = comment.get("issue_url", "")
        if issue_url != EXACT_INBOX_ISSUE_URL:
            return False, "INVALID_ISSUE_CONTEXT"

        command_id = payload.get("command_id")
        if not command_id or not isinstance(command_id, str) or not COMMAND_ID_REGEX.match(command_id):
            return False, "INVALID_COMMAND_ID"

        action = payload.get("action")
        if action not in ALLOWED_ACTIONS:
            return False, "UNAUTHORIZED_ACTION"

        issue_num = payload.get("issue_number")
        if not isinstance(issue_num, int) or issue_num not in ALLOWED_ISSUES:
            return False, "UNAUTHORIZED_ISSUE_TARGET"

        expected_branch = payload.get("expected_branch")
        if expected_branch != EXPECTED_BRANCH:
            return False, "BRANCH_MISMATCH"

        expected_sha = payload.get("expected_head_sha")
        if not expected_sha or not re.match(r"^[0-9a-fA-F]{40}$", str(expected_sha)):
            return False, "INVALID_EXPECTED_SHA"

        if not git_info.get("valid_repo", False):
            return False, "LOCAL_GIT_UNVERIFIED"

        if git_info["branch"] != expected_branch:
            return False, "LOCAL_BRANCH_MISMATCH"

        if git_info["head_sha"].lower() != str(expected_sha).lower():
            return False, "LOCAL_HEAD_SHA_MISMATCH"

        now = self.now_fn()
        issued_at = parse_iso_timestamp(payload.get("issued_at"))
        expires_at = parse_iso_timestamp(payload.get("expires_at"))

        if not issued_at or not expires_at:
            return False, "INVALID_TIMESTAMPS"

        if issued_at > now:
            return False, "FUTURE_COMMAND"

        if now > expires_at:
            return False, "EXPIRED_COMMAND"

        if (expires_at - issued_at).total_seconds() > 86400:
            return False, "EXCESSIVE_TTL"

        return True, "VALID"

    def _claim_command(self, command_id, payload):
        if not isinstance(command_id, str) or not COMMAND_ID_REGEX.match(command_id):
            return False, "INVALID_COMMAND_ID", None

        claim_filename = f"{command_id}.json"
        claim_path = os.path.abspath(os.path.join(self.claims_dir, claim_filename))
        real_claims_dir = os.path.realpath(self.claims_dir)
        real_claim_path = os.path.realpath(claim_path)

        if not real_claim_path.startswith(real_claims_dir + os.sep) and real_claim_path != real_claims_dir:
            return False, "PATH_TRAVERSAL_DETECTED", None

        if os.path.islink(claim_path):
            return False, "SYMLINK_DETECTED", None

        claim_data = {
            "command_id": command_id,
            "status": "PENDING",
            "claimed_at": self.now_fn().isoformat(),
            "payload": payload,
            "dispatch_result": "UNKNOWN",
        }
        encoded_data = json.dumps(claim_data, indent=2).encode("utf-8")

        try:
            fd = os.open(claim_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(encoded_data)
                f.flush()
                os.fsync(f.fileno())
            return True, "CLAIMED", claim_path
        except FileExistsError:
            return False, "DUPLICATE_CLAIM", claim_path
        except Exception:
            return False, "CLAIM_WRITE_ERROR", claim_path

    def _update_claim_status(self, claim_path, status, result_code):
        if not claim_path or not os.path.exists(claim_path):
            return False
        try:
            with open(claim_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["status"] = status
            data["dispatch_result"] = result_code
            data["updated_at"] = self.now_fn().isoformat()

            temp_path = claim_path + ".tmp"
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(temp_path, claim_path)
            return True
        except Exception:
            return False

    def dispatch_command(self, payload, claim_path):
        valid_cfg, msg = self.is_config_valid()
        if not valid_cfg:
            self._update_claim_status(claim_path, "FAILED", STATUS_CODES["CONFIG_INVALID"])
            return False, STATUS_CODES["CONFIG_INVALID"]

        if not self.config.get("dispatch_enabled", False):
            self._update_claim_status(claim_path, "FAILED", STATUS_CODES["DISPATCH_FAILED"])
            return False, STATUS_CODES["DISPATCH_FAILED"]

        conv_id = self.config.get("conversation_id", "").strip()
        if not conv_id:
            self._update_claim_status(claim_path, "FAILED", STATUS_CODES["CONFIG_INVALID"])
            return False, STATUS_CODES["CONFIG_INVALID"]

        issue_num = payload["issue_number"]
        prompt_template = SAFE_PROMPT_TEMPLATES.get(issue_num)
        if not prompt_template:
            self._update_claim_status(claim_path, "FAILED", STATUS_CODES["INVALID_PAYLOAD"])
            return False, STATUS_CODES["INVALID_PAYLOAD"]

        ret, stdout, stderr = self.runner(["agentapi", "send-message", conv_id, prompt_template])
        if ret == 0:
            self._update_claim_status(claim_path, "SENT", STATUS_CODES["DISPATCH_SUCCESS"])
            return True, STATUS_CODES["DISPATCH_SUCCESS"]
        else:
            self._update_claim_status(claim_path, "FAILED", STATUS_CODES["DISPATCH_FAILED"])
            return False, STATUS_CODES["DISPATCH_FAILED"]

    def process_inbox(self):
        valid_cfg, msg = self.is_config_valid()
        if not valid_cfg:
            return {"status": "ERROR", "error": STATUS_CODES["CONFIG_INVALID"], "processed_count": 0}

        comments, err = self.fetch_inbox_comments()
        if err:
            return {"status": "ERROR", "error": err, "processed_count": 0}

        if not isinstance(comments, list):
            return {"status": "ERROR", "error": "INVALID_RESPONSE_FORMAT", "processed_count": 0}

        comments = sorted(comments, key=lambda c: c.get("id", 0))

        last_watermark, wm_err = self.get_watermark()
        if wm_err == "WATERMARK_CORRUPT":
            return {"status": "ERROR", "error": "WATERMARK_CORRUPT", "processed_count": 0}

        if last_watermark is None:
            highest_id = max([c.get("id", 0) for c in comments], default=0)
            self.set_watermark(highest_id)
            return {
                "status": "INITIALIZED",
                "message": f"First enrollment completed. Watermark set to comment ID {highest_id}.",
                "processed_count": 0,
            }

        results = []
        new_highest_id = last_watermark

        for comment in comments:
            cid = comment.get("id", 0)
            if cid <= last_watermark:
                continue

            new_highest_id = max(new_highest_id, cid)
            body = comment.get("body", "")

            if ENVELOPE_MARKER not in body:
                continue

            payload, parse_err = self.parse_command_envelope(body)
            git_info = self.get_local_git_info()

            if parse_err:
                results.append({"comment_id": cid, "valid": False, "reason": parse_err})
                continue

            is_valid, validation_msg = self.validate_command(comment, payload, git_info)
            if not is_valid:
                results.append({"comment_id": cid, "valid": False, "reason": validation_msg})
                continue

            command_id = payload["command_id"]
            claimed, claim_msg, claim_path = self._claim_command(command_id, payload)
            if not claimed:
                results.append({"comment_id": cid, "valid": False, "reason": f"CLAIM_FAILED:{claim_msg}"})
                continue

            if payload.get("action") == "status_request":
                self._update_claim_status(claim_path, "COMPLETED", STATUS_CODES["STATUS_REQUEST_PROCESSED"])
                results.append({"comment_id": cid, "valid": True, "action": "status_request", "dispatched": False})
                self.post_status_receipt(force=True, last_result_code=STATUS_CODES["STATUS_REQUEST_PROCESSED"])
            elif payload.get("action") == "continue_issue":
                disp_ok, disp_code = self.dispatch_command(payload, claim_path)
                results.append({"comment_id": cid, "valid": True, "action": "continue_issue", "dispatched": disp_ok, "code": disp_code})
                self.post_status_receipt(force=False, last_result_code=disp_code)

        self.set_watermark(new_highest_id)
        return {"status": "SUCCESS", "processed_count": len(results), "results": results}

    def post_status_receipt(self, force=False, last_result_code=STATUS_CODES["NO_OP"]):
        if not self.config.get("status_enabled", False):
            return False, "STATUS_DISABLED"

        valid_cfg, msg = self.is_config_valid()
        if not valid_cfg:
            return False, STATUS_CODES["CONFIG_INVALID"]

        now = self.now_fn()
        now_ts = now.timestamp()

        last_posted_ts = 0
        if os.path.exists(self.status_file):
            try:
                with open(self.status_file, "r", encoding="utf-8") as f:
                    sdata = json.load(f)
                    last_posted_ts = sdata.get("last_posted_timestamp", 0)
            except Exception:
                pass

        elapsed = now_ts - last_posted_ts
        min_interval = self.config.get("min_status_interval_sec", 900)
        if elapsed < min_interval:
            return False, STATUS_CODES["RATE_LIMITED"]

        git_info = self.get_local_git_info()

        receipt_seed = f"{now.isoformat()}-{git_info['head_sha']}"
        receipt_id = hashlib.sha256(receipt_seed.encode("utf-8")).hexdigest()[:16]

        receipt_data = {
            "receipt_id": receipt_id,
            "observation_utc": now.isoformat(),
            "configured_branch": EXPECTED_BRANCH,
            "git_head_sha": git_info["head_sha"],
            "git_dirty": git_info["is_dirty"],
            "bridge_process_alive": True,
            "last_command_result_code": last_result_code if last_result_code in STATUS_CODES else STATUS_CODES["NO_OP"],
            "antigravity_execution": "UNKNOWN",
            "antigravity_model": "UNKNOWN",
            "antigravity_quota": "UNKNOWN",
            "astra_review": "UNKNOWN",
            "astra_usage": "UNKNOWN",
        }

        body_text = f"### Antigravity Bridge Status Receipt\n```json\n{json.dumps(receipt_data, indent=2)}\n```"

        status_issue = STATUS_ISSUE_NUMBER
        repo = REPO_NAME
        endpoint = f"repos/{repo}/issues/{status_issue}/comments"

        ret, stdout, stderr = self.runner(["gh", "api", endpoint, "-f", f"body={body_text}"])
        if ret == 0:
            try:
                with open(self.status_file, "w", encoding="utf-8") as f:
                    json.dump({"last_posted_timestamp": now_ts, "last_posted_utc": now.isoformat()}, f)
            except Exception:
                pass
            return True, "STATUS_POSTED"
        else:
            return False, "GH_API_STATUS_POST_FAILED"


def run_polling_loop(bridge, max_iterations=None, sleep_fn=time.sleep):
    iteration = 0
    while max_iterations is None or iteration < max_iterations:
        res = bridge.process_inbox()
        poll_interval = bridge.config.get("poll_interval_sec", 900)
        iteration += 1
        if max_iterations is not None and iteration >= max_iterations:
            break
        sleep_fn(poll_interval)


def main():
    bridge = AntigravityRemoteBridge()
    valid, msg = bridge.is_config_valid()
    if not valid:
        print(f"Bridge configuration invalid: {msg}. Halting.")
        sys.exit(1)
    print("Starting Antigravity Remote Bridge continuous polling loop...")
    run_polling_loop(bridge)


if __name__ == "__main__":
    main()
