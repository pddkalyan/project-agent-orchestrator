"""
Deterministic Offline Unit Tests for Antigravity Remote Bridge
"""

import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from control_center.remote_bridge.bridge import (
    AUTHORIZED_USER_ID,
    ENVELOPE_MARKER,
    EXPECTED_BRANCH,
    INBOX_ISSUE_NUMBER,
    REPO_NAME,
    STATUS_ISSUE_NUMBER,
    AntigravityRemoteBridge,
)

HEAD_SHA_40 = "1234567890abcdef1234567890abcdef12345678"


class TestAntigravityRemoteBridge(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.state_dir = os.path.join(self.test_dir, "state")
        self.now = datetime(2026, 10, 10, 12, 0, 0, tzinfo=timezone.utc)

        self.mock_runner_calls = []
        self.mock_gh_comments = []
        self.mock_gh_api_post_result = (0, "{}", "")
        self.mock_agentapi_result = (0, "Message sent", "")
        self.mock_git_branch = EXPECTED_BRANCH
        self.mock_git_sha = HEAD_SHA_40
        self.mock_git_dirty = False
        self.mock_sidecar_ok = True

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def mock_now(self):
        return self.now

    def mock_sidecar_checker(self):
        return self.mock_sidecar_ok

    def mock_runner(self, args, input_data=None):
        self.mock_runner_calls.append((args, input_data))
        cmd = args[0]
        subcmd = args[1] if len(args) > 1 else ""

        if cmd == "git":
            if subcmd == "rev-parse":
                if "--abbrev-ref" in args:
                    return 0, self.mock_git_branch, ""
                else:
                    return 0, self.mock_git_sha, ""
            elif subcmd == "status":
                return 0, " M file.py" if self.mock_git_dirty else "", ""
        elif cmd == "gh":
            if subcmd == "api":
                if len(args) > 2 and args[2].endswith("/comments") and "-f" not in args:
                    return 0, json.dumps(self.mock_gh_comments), ""
                elif "-f" in args:
                    return self.mock_gh_api_post_result
        elif cmd == "agentapi":
            if subcmd == "send-message":
                return self.mock_agentapi_result
            elif subcmd == "--version":
                return 0 if self.mock_sidecar_ok else 1, "agentapi v1.0", ""

        return 1, "", f"Unrecognized mock command: {args}"

    def create_bridge(self, config_override=None):
        cfg = {
            "dispatch_enabled": True,
            "status_enabled": True,
            "conversation_id": "conv-test-123",
            "authorized_user_id": AUTHORIZED_USER_ID,
            "repo": REPO_NAME,
            "inbox_issue": INBOX_ISSUE_NUMBER,
            "status_issue": STATUS_ISSUE_NUMBER,
            "expected_branch": EXPECTED_BRANCH,
        }
        if config_override:
            cfg.update(config_override)

        return AntigravityRemoteBridge(
            config=cfg,
            state_dir=self.state_dir,
            runner=self.mock_runner,
            sidecar_checker=self.mock_sidecar_checker,
            now_fn=self.mock_now,
        )

    def make_comment(
        self,
        comment_id,
        user_id=AUTHORIZED_USER_ID,
        command_id="cmd-1",
        action="continue_issue",
        issue_number=47,
        expected_branch=EXPECTED_BRANCH,
        expected_head_sha=HEAD_SHA_40,
        issued_offset_sec=-10,
        expiry_offset_sec=300,
        issue_url=None,
        marker=ENVELOPE_MARKER,
        raw_body_override=None,
    ):
        if issue_url is None:
            issue_url = f"https://api.github.com/repos/{REPO_NAME}/issues/{INBOX_ISSUE_NUMBER}"

        if raw_body_override is not None:
            body = raw_body_override
        else:
            payload = {
                "marker": marker,
                "command_id": command_id,
                "action": action,
                "issue_number": issue_number,
                "expected_branch": expected_branch,
                "expected_head_sha": expected_head_sha,
                "issued_at": (self.now + timedelta(seconds=issued_offset_sec)).isoformat(),
                "expires_at": (self.now + timedelta(seconds=expiry_offset_sec)).isoformat(),
            }
            body = f"Here is command:\n```json\n{json.dumps(payload)}\n```"

        return {
            "id": comment_id,
            "user": {"id": user_id, "login": "testuser"},
            "issue_url": issue_url,
            "body": body,
            "created_at": self.now.isoformat(),
        }

    def test_first_enrollment_watermark_set(self):
        bridge = self.create_bridge()
        self.mock_gh_comments = [
            self.make_comment(101),
            self.make_comment(102),
        ]
        res = bridge.process_inbox()
        self.assertEqual(res["status"], "INITIALIZED")
        self.assertEqual(res["processed_count"], 0)
        self.assertEqual(bridge.get_watermark(), 102)

    def test_positive_continue_issue_dispatch(self):
        bridge = self.create_bridge()
        bridge.set_watermark(100)
        self.mock_gh_comments = [self.make_comment(101, command_id="cmd-101", issue_number=47)]

        res = bridge.process_inbox()
        self.assertEqual(res["status"], "SUCCESS")
        self.assertEqual(res["processed_count"], 1)
        self.assertTrue(res["results"][0]["valid"])
        self.assertTrue(res["results"][0]["dispatched"])

        # Verify claim recorded
        claim_file = os.path.join(self.state_dir, "claims", "cmd-101.json")
        self.assertTrue(os.path.exists(claim_file))
        with open(claim_file, "r") as f:
            claim_data = json.load(f)
        self.assertEqual(claim_data["status"], "SENT")

    def test_positive_status_request(self):
        bridge = self.create_bridge()
        bridge.set_watermark(100)
        self.mock_gh_comments = [
            self.make_comment(101, command_id="cmd-status", action="status_request", issue_number=44)
        ]

        res = bridge.process_inbox()
        self.assertEqual(res["status"], "SUCCESS")
        self.assertEqual(res["results"][0]["action"], "status_request")

        # Verify gh api post status was called for issue #55
        post_calls = [c for c in self.mock_runner_calls if c[0][0] == "gh" and "-f" in c[0]]
        self.assertEqual(len(post_calls), 1)
        self.assertIn(f"repos/{REPO_NAME}/issues/{STATUS_ISSUE_NUMBER}/comments", post_calls[0][0][2])

    def test_unauthorized_user_id_rejected(self):
        bridge = self.create_bridge()
        bridge.set_watermark(100)
        self.mock_gh_comments = [self.make_comment(101, user_id=999999)]

        res = bridge.process_inbox()
        self.assertEqual(res["processed_count"], 1)
        self.assertFalse(res["results"][0]["valid"])
        self.assertIn("Unauthorized user ID", res["results"][0]["reason"])

    def test_wrong_issue_context_rejected(self):
        bridge = self.create_bridge()
        bridge.set_watermark(100)
        self.mock_gh_comments = [
            self.make_comment(101, issue_url=f"https://api.github.com/repos/{REPO_NAME}/issues/99")
        ]

        res = bridge.process_inbox()
        self.assertEqual(res["processed_count"], 1)
        self.assertFalse(res["results"][0]["valid"])
        self.assertIn("Comment not on exact inbox issue", res["results"][0]["reason"])

    def test_malformed_json_rejected(self):
        bridge = self.create_bridge()
        bridge.set_watermark(100)
        bad_body = f"{ENVELOPE_MARKER} ```json\n{{bad_json: true\n```"
        self.mock_gh_comments = [self.make_comment(101, raw_body_override=bad_body)]

        res = bridge.process_inbox()
        self.assertFalse(res["results"][0]["valid"])
        self.assertIn("Malformed JSON", res["results"][0]["reason"])

    def test_unauthorized_action_rejected(self):
        bridge = self.create_bridge()
        bridge.set_watermark(100)
        self.mock_gh_comments = [self.make_comment(101, action="shell_exec")]

        res = bridge.process_inbox()
        self.assertFalse(res["results"][0]["valid"])
        self.assertIn("Unauthorized action: shell_exec", res["results"][0]["reason"])

    def test_unauthorized_issue_number_rejected(self):
        bridge = self.create_bridge()
        bridge.set_watermark(100)
        self.mock_gh_comments = [self.make_comment(101, issue_number=99)]

        res = bridge.process_inbox()
        self.assertFalse(res["results"][0]["valid"])
        self.assertIn("Issue #99 not in allowlist", res["results"][0]["reason"])

    def test_stale_branch_or_sha_rejected(self):
        bridge = self.create_bridge()
        bridge.set_watermark(100)

        # Local branch mismatch
        self.mock_git_branch = "feature/other"
        self.mock_gh_comments = [self.make_comment(101, command_id="cmd-branch")]
        res1 = bridge.process_inbox()
        self.assertFalse(res1["results"][0]["valid"])
        self.assertIn("Local branch mismatch", res1["results"][0]["reason"])

        # Local HEAD SHA mismatch
        self.mock_git_branch = EXPECTED_BRANCH
        self.mock_git_sha = "0000000000000000000000000000000000000000"
        self.mock_gh_comments = [self.make_comment(102, command_id="cmd-sha")]
        res2 = bridge.process_inbox()
        self.assertFalse(res2["results"][0]["valid"])
        self.assertIn("Local HEAD SHA mismatch", res2["results"][0]["reason"])

    def test_expired_or_future_command_rejected(self):
        bridge = self.create_bridge()
        bridge.set_watermark(100)

        # Expired
        self.mock_gh_comments = [self.make_comment(101, command_id="cmd-exp", issued_offset_sec=-600, expiry_offset_sec=-10)]
        res1 = bridge.process_inbox()
        self.assertFalse(res1["results"][0]["valid"])
        self.assertIn("Command expired", res1["results"][0]["reason"])

        # Future issued
        self.mock_gh_comments = [self.make_comment(102, command_id="cmd-fut", issued_offset_sec=600, expiry_offset_sec=1200)]
        res2 = bridge.process_inbox()
        self.assertFalse(res2["results"][0]["valid"])
        self.assertIn("Command issued in future", res2["results"][0]["reason"])

    def test_missing_consent_dispatch_disabled(self):
        bridge = self.create_bridge({"dispatch_enabled": False})
        bridge.set_watermark(100)
        self.mock_gh_comments = [self.make_comment(101, command_id="cmd-disabled")]

        res = bridge.process_inbox()
        self.assertTrue(res["results"][0]["valid"])
        self.assertFalse(res["results"][0]["dispatched"])

        # Claim marked FAILED on disk
        claim_file = os.path.join(self.state_dir, "claims", "cmd-disabled.json")
        with open(claim_file, "r") as f:
            cdata = json.load(f)
        self.assertEqual(cdata["status"], "FAILED")
        self.assertIn("dispatch_enabled is False", cdata["dispatch_result"])

    def test_duplicate_claim_prevented(self):
        bridge = self.create_bridge()
        bridge.set_watermark(100)

        # Pre-create claim
        claim_file = os.path.join(self.state_dir, "claims", "cmd-dup.json")
        with open(claim_file, "w") as f:
            json.dump({"command_id": "cmd-dup", "status": "SENT"}, f)

        self.mock_gh_comments = [self.make_comment(101, command_id="cmd-dup")]
        res = bridge.process_inbox()
        self.assertFalse(res["results"][0]["valid"])
        self.assertIn("Duplicate claim ID", res["results"][0]["reason"])

    def test_crash_between_claim_and_dispatch(self):
        bridge = self.create_bridge()
        payload = {
            "marker": ENVELOPE_MARKER,
            "command_id": "cmd-crash",
            "action": "continue_issue",
            "issue_number": 47,
            "expected_branch": EXPECTED_BRANCH,
            "expected_head_sha": HEAD_SHA_40,
            "issued_at": self.now.isoformat(),
            "expires_at": (self.now + timedelta(seconds=300)).isoformat(),
        }
        claimed, msg, claim_path = bridge._claim_command("cmd-crash", payload)
        self.assertTrue(claimed)

        # Check pending claim exists
        with open(claim_path, "r") as f:
            cdata = json.load(f)
        self.assertEqual(cdata["status"], "PENDING")

        # Mock agentapi failure (crash or non-zero ret)
        self.mock_agentapi_result = (1, "", "agentapi crashed")
        disp_ok, disp_msg = bridge.dispatch_command(payload, claim_path)
        self.assertFalse(disp_ok)

        # Claim marked FAILED, not automatically retried
        with open(claim_path, "r") as f:
            cdata = json.load(f)
        self.assertEqual(cdata["status"], "FAILED")

    def test_status_rate_limiting_and_sanitization(self):
        bridge = self.create_bridge()

        # First status post
        ok1, msg1 = bridge.post_status_receipt(force=True)
        self.assertTrue(ok1)

        # Immediate second status post (not forced) -> throttled
        ok2, msg2 = bridge.post_status_receipt(force=False)
        self.assertFalse(ok2)
        self.assertIn("Throttled", msg2)

        # Inspect posted body for sanitization
        post_calls = [c for c in self.mock_runner_calls if c[0][0] == "gh" and "-f" in c[0]]
        self.assertEqual(len(post_calls), 1)
        posted_arg = [arg for arg in post_calls[0][0] if arg.startswith("body=")][0]
        self.assertIn("antigravity_execution", posted_arg)
        self.assertIn("UNKNOWN", posted_arg)


if __name__ == "__main__":
    unittest.main()
