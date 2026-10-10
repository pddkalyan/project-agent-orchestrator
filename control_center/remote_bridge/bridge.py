"""
Antigravity Sidecar GitHub Command/Status Bridge (Disabled by default)

Owner-approved bridge development (Issue #44 subtask).
Pure standard library Python implementation.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

REPO_NAME = "pddkalyan/project-agent-orchestrator"
AUTHORIZED_USER_ID = 159762630
INBOX_ISSUE_NUMBER = 54
STATUS_ISSUE_NUMBER = 55
ALLOWED_ISSUES = {44, 47}
ALLOWED_ACTIONS = {"status_request", "continue_issue"}
EXPECTED_BRANCH = "agent/control-center-standalone"
ENVELOPE_MARKER = "ANTIGRAVITY_CONTROL_V1"

SAFE_PROMPT_TEMPLATES = {
    44: "Supervise Issue #44 subtask execution under strict zero-spend, no merges, no force push, no remote privilege constraints.",
    47: "Supervise Issue #47 subtask execution under strict zero-spend, no merges, no force push, no remote privilege constraints.",
}


def parse_iso_timestamp(ts_str):
    if not ts_str:
        return None
    try:
        ts_str = ts_str.replace("Z", "+00:00")
        return datetime.fromisoformat(ts_str)
    except Exception:
        return None


def default_subprocess_runner(args, input_data=None):
    proc = subprocess.run(
        args,
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
        sidecar_checker=None,
        now_fn=None,
    ):
        self.config = {
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
        if config:
            self.config.update(config)

        self.state_dir = state_dir or os.path.expanduser("~/.antigravity_bridge_state")
        os.makedirs(self.state_dir, exist_ok=True)

        self.runner = runner or default_subprocess_runner
        self.sidecar_checker = sidecar_checker or self._default_sidecar_checker
        self.now_fn = now_fn or (lambda: datetime.now(timezone.utc))

        self.watermark_file = os.path.join(self.state_dir, "watermark.json")
        self.claims_dir = os.path.join(self.state_dir, "claims")
        self.status_file = os.path.join(self.state_dir, "status_state.json")
        os.makedirs(self.claims_dir, exist_ok=True)

    def _default_sidecar_checker(self):
        # Checks if agentapi binary exists and is executable
        ret, out, err = self.runner(["agentapi", "--version"])
        return ret == 0

    def get_watermark(self):
        if not os.path.exists(self.watermark_file):
            return None
        try:
            with open(self.watermark_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("last_comment_id")
        except Exception:
            return None

    def set_watermark(self, comment_id):
        temp_file = self.watermark_file + ".tmp"
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump({"last_comment_id": comment_id, "updated_at": self.now_fn().isoformat()}, f)
        os.replace(temp_file, self.watermark_file)

    def get_local_git_info(self):
        ret_b, branch, _ = self.runner(["git", "rev-parse", "--abbrev-ref", "HEAD"])
        ret_h, head_sha, _ = self.runner(["git", "rev-parse", "HEAD"])
        ret_s, status, _ = self.runner(["git", "status", "--porcelain"])

        current_branch = branch.strip() if ret_b == 0 else "UNKNOWN"
        current_sha = head_sha.strip() if ret_h == 0 else "UNKNOWN"
        is_dirty = bool(status.strip()) if ret_s == 0 else True

        return {
            "branch": current_branch,
            "head_sha": current_sha,
            "is_dirty": is_dirty,
        }

    def fetch_inbox_comments(self):
        inbox_issue = self.config["inbox_issue"]
        repo = self.config["repo"]
        endpoint = f"repos/{repo}/issues/{inbox_issue}/comments"
        ret, stdout, stderr = self.runner(["gh", "api", endpoint])
        if ret != 0:
            return None, f"gh api failed: {stderr}"
        try:
            comments = json.loads(stdout)
            return comments, None
        except Exception as e:
            return None, f"JSON parse error: {str(e)}"

    def parse_command_envelope(self, comment_body):
        if ENVELOPE_MARKER not in comment_body:
            return None, "Missing marker"

        # Look for JSON block inside body or parse body as JSON
        json_str = comment_body
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", comment_body, re.DOTALL)
        if match:
            json_str = match.group(1)
        else:
            # Try finding first { and last }
            start = comment_body.find("{")
            end = comment_body.rfind("}")
            if start != -1 and end != -1 and start < end:
                json_str = comment_body[start : end + 1]

        try:
            data = json.loads(json_str)
        except Exception as e:
            return None, f"Malformed JSON: {str(e)}"

        if not isinstance(data, dict):
            return None, "JSON body is not a dict"

        if data.get("marker") != ENVELOPE_MARKER:
            return None, f"Invalid marker in payload: {data.get('marker')}"

        return data, None

    def validate_command(self, comment, payload, git_info):
        # Validate GitHub user ID
        user_info = comment.get("user", {})
        if user_info.get("id") != self.config["authorized_user_id"]:
            return False, f"Unauthorized user ID: {user_info.get('id')}"

        # Validate issue context
        issue_url = comment.get("issue_url", "")
        expected_issue_path = f"/issues/{self.config['inbox_issue']}"
        if expected_issue_path not in issue_url and not issue_url.endswith(str(self.config['inbox_issue'])):
            return False, f"Comment not on exact inbox issue #{self.config['inbox_issue']}"

        # Payload validations
        command_id = payload.get("command_id")
        if not command_id or not isinstance(command_id, str):
            return False, "Missing or invalid command_id"

        action = payload.get("action")
        if action not in ALLOWED_ACTIONS:
            return False, f"Unauthorized action: {action}"

        issue_num = payload.get("issue_number")
        try:
            issue_num = int(issue_num)
        except (TypeError, ValueError):
            return False, f"Invalid issue_number: {issue_num}"

        if issue_num not in ALLOWED_ISSUES:
            return False, f"Issue #{issue_num} not in allowlist"

        expected_branch = payload.get("expected_branch")
        if expected_branch != self.config["expected_branch"]:
            return False, f"Branch mismatch: requested {expected_branch}, expected {self.config['expected_branch']}"

        expected_sha = payload.get("expected_head_sha")
        if not expected_sha or not re.match(r"^[0-9a-fA-F]{40}$", str(expected_sha)):
            return False, f"Invalid expected_head_sha format: {expected_sha}"

        if git_info["branch"] != expected_branch:
            return False, f"Local branch mismatch: local {git_info['branch']} != expected {expected_branch}"

        if git_info["head_sha"].lower() != str(expected_sha).lower():
            return False, f"Local HEAD SHA mismatch: local {git_info['head_sha']} != expected {expected_sha}"

        # Timestamp validations
        now = self.now_fn()
        issued_at = parse_iso_timestamp(payload.get("issued_at"))
        expires_at = parse_iso_timestamp(payload.get("expires_at"))

        if not issued_at or not expires_at:
            return False, "Missing or invalid ISO timestamp(s)"

        if issued_at > now:
            return False, f"Command issued in future: {issued_at} > {now}"

        if now > expires_at:
            return False, f"Command expired: {now} > {expires_at}"

        return True, "Valid"

    def _claim_command(self, command_id, payload):
        claim_path = os.path.join(self.claims_dir, f"{command_id}.json")
        if os.path.exists(claim_path):
            return False, "Duplicate claim ID", claim_path

        claim_data = {
            "command_id": command_id,
            "status": "PENDING",
            "claimed_at": self.now_fn().isoformat(),
            "payload": payload,
            "dispatch_result": "UNKNOWN",
        }

        # Atomic write
        temp_path = claim_path + ".tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(claim_data, f, indent=2)
            os.replace(temp_path, claim_path)

            # Verification check
            with open(claim_path, "r", encoding="utf-8") as f:
                verified = json.load(f)
            if verified.get("command_id") != command_id or verified.get("status") != "PENDING":
                return False, "Claim atomic verification failed", claim_path
            return True, "Claimed", claim_path
        except Exception as e:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
            return False, f"Claim write exception: {str(e)}", claim_path

    def _update_claim_status(self, claim_path, status, result_msg):
        if not os.path.exists(claim_path):
            return False
        try:
            with open(claim_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["status"] = status
            data["dispatch_result"] = result_msg
            data["updated_at"] = self.now_fn().isoformat()

            temp_path = claim_path + ".tmp"
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(temp_path, claim_path)
            return True
        except Exception:
            return False

    def dispatch_command(self, payload, claim_path):
        if not self.config.get("dispatch_enabled", False):
            self._update_claim_status(claim_path, "FAILED", "dispatch_enabled is False")
            return False, "Bridge dispatch is disabled"

        conv_id = self.config.get("conversation_id", "").strip()
        if not conv_id:
            self._update_claim_status(claim_path, "FAILED", "conversation_id missing")
            return False, "Missing conversation_id"

        if not self.sidecar_checker():
            self._update_claim_status(claim_path, "FAILED", "Sidecar runtime unavailable")
            return False, "Sidecar runtime not verified or missing"

        issue_num = int(payload["issue_number"])
        prompt_template = SAFE_PROMPT_TEMPLATES.get(issue_num)
        if not prompt_template:
            self._update_claim_status(claim_path, "FAILED", f"No safe prompt template for issue #{issue_num}")
            return False, f"No safe prompt template for issue #{issue_num}"

        # Invoke agentapi send-message <conversation_id> <prompt>
        ret, stdout, stderr = self.runner(["agentapi", "send-message", conv_id, prompt_template])
        if ret == 0:
            msg = "Dispatched to agentapi successfully"
            self._update_claim_status(claim_path, "SENT", msg)
            return True, msg
        else:
            msg = f"agentapi send-message failed: {stderr.strip()}"
            self._update_claim_status(claim_path, "FAILED", msg)
            return False, msg

    def process_inbox(self):
        comments, err = self.fetch_inbox_comments()
        if err:
            return {"status": "ERROR", "error": err, "processed_count": 0}

        if not isinstance(comments, list):
            return {"status": "ERROR", "error": "Invalid response format from gh api", "processed_count": 0}

        # Sort comments by ID ascending
        comments = sorted(comments, key=lambda c: c.get("id", 0))

        last_watermark = self.get_watermark()
        if last_watermark is None:
            # First enrollment: record current highest comment ID without executing existing comments
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
                results.append({"comment_id": cid, "valid": False, "reason": f"Claim failed: {claim_msg}"})
                continue

            if payload.get("action") == "status_request":
                self._update_claim_status(claim_path, "COMPLETED", "Status requested")
                results.append({"comment_id": cid, "valid": True, "action": "status_request", "dispatched": False})
                # Trigger status report attempt
                self.post_status_receipt(force=True, last_result=f"Status request {command_id} processed")
            elif payload.get("action") == "continue_issue":
                disp_ok, disp_msg = self.dispatch_command(payload, claim_path)
                results.append({"comment_id": cid, "valid": True, "action": "continue_issue", "dispatched": disp_ok, "detail": disp_msg})
                self.post_status_receipt(force=False, last_result=f"Continue issue {command_id}: {disp_msg}")

        self.set_watermark(new_highest_id)
        return {"status": "SUCCESS", "processed_count": len(results), "results": results}

    def post_status_receipt(self, force=False, last_result="None"):
        if not self.config.get("status_enabled", False):
            return False, "status_enabled is False"

        now = self.now_fn()
        now_ts = now.timestamp()

        # Check rate limit (15 min)
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
        if not force and elapsed < min_interval:
            return False, f"Throttled. Last post was {int(elapsed)}s ago (min {min_interval}s)"

        git_info = self.get_local_git_info()

        receipt_data = {
            "observation_utc": now.isoformat(),
            "configured_branch": self.config["expected_branch"],
            "git_head_sha": git_info["head_sha"],
            "git_dirty": git_info["is_dirty"],
            "bridge_process_alive": True,
            "last_command_result": last_result,
            "antigravity_execution": "UNKNOWN",
            "antigravity_model": "UNKNOWN",
            "antigravity_quota": "UNKNOWN",
            "astra_review": "UNKNOWN",
            "astra_usage": "UNKNOWN",
        }

        body_text = f"### Antigravity Bridge Status Receipt\n```json\n{json.dumps(receipt_data, indent=2)}\n```"

        status_issue = self.config["status_issue"]
        repo = self.config["repo"]
        endpoint = f"repos/{repo}/issues/{status_issue}/comments"

        ret, stdout, stderr = self.runner(["gh", "api", endpoint, "-f", f"body={body_text}"])
        if ret == 0:
            try:
                with open(self.status_file, "w", encoding="utf-8") as f:
                    json.dump({"last_posted_timestamp": now_ts, "last_posted_utc": now.isoformat()}, f)
            except Exception:
                pass
            return True, "Status posted successfully"
        else:
            return False, f"gh api status post failed: {stderr.strip()}"


def main():
    bridge = AntigravityRemoteBridge()
    print("Running Antigravity Remote Bridge poll...")
    res = bridge.process_inbox()
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
