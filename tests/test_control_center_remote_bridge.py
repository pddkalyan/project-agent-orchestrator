"""
Deterministic Offline Unit Tests for Antigravity Remote Bridge
"""

import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

from control_center.remote_bridge.bridge import (
    AUTHORIZED_USER_ID,
    ENVELOPE_MARKER,
    EXACT_INBOX_ISSUE_URL,
    EXPECTED_BRANCH,
    INBOX_ISSUE_NUMBER,
    REPO_NAME,
    STATUS_CODES,
    STATUS_ISSUE_NUMBER,
    AntigravityRemoteBridge,
    normalize_github_remote,
    parse_iso_timestamp,
    run_polling_loop,
)

HEAD_SHA_40 = "1234567890abcdef1234567890abcdef12345678"
VALID_UUID = "a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d"


class TestAntigravityRemoteBridge(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.state_dir = os.path.join(self.test_dir, "state")
        self.workspace_dir = os.path.join(self.test_dir, "workspace")
        os.makedirs(self.workspace_dir, exist_ok=True)
        os.makedirs(self.state_dir, exist_ok=True)

        self.now = datetime(2026, 10, 10, 12, 0, 0, tzinfo=timezone.utc)

        # Write valid default local config.json
        self.write_local_config({
            "workspace_dir": self.workspace_dir,
            "dispatch_enabled": True,
            "status_enabled": True,
            "conversation_id": "conv-test-123",
            "poll_interval_sec": 900,
            "min_status_interval_sec": 900,
            "allow_dirty_continue": False,
        })

        self.mock_runner_calls = []
        self.mock_gh_comments_pages = [[
            self.make_comment(101, command_id=VALID_UUID)
        ]]
        self.mock_gh_api_post_result = (0, "{}", "")
        self.mock_agentapi_result = (0, "Message sent", "")
        self.mock_git_remote_url = f"https://github.com/{REPO_NAME}.git"
        self.mock_git_branch = EXPECTED_BRANCH
        self.mock_git_sha = HEAD_SHA_40
        self.mock_git_dirty = False

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def write_local_config(self, cfg_data):
        cfg_file = os.path.join(self.state_dir, "config.json")
        with open(cfg_file, "w", encoding="utf-8") as f:
            json.dump(cfg_data, f)

    def mock_now(self):
        return self.now

    def mock_runner(self, args, cwd=None, input_data=None):
        self.mock_runner_calls.append({"args": args, "cwd": cwd, "input_data": input_data})
        cmd = args[0]
        subcmd = args[1] if len(args) > 1 else ""

        if cmd == "git":
            if subcmd == "remote":
                return 0, self.mock_git_remote_url, ""
            elif subcmd == "rev-parse":
                if "--abbrev-ref" in args:
                    return 0, self.mock_git_branch, ""
                else:
                    return 0, self.mock_git_sha, ""
            elif subcmd == "status":
                return 0, " M file.py" if self.mock_git_dirty else "", ""
        elif cmd == "gh":
            if subcmd == "api":
                endpoint = args[2] if len(args) > 2 else ""
                if "/comments" in endpoint and "-f" not in args:
                    parsed_url = urlparse(endpoint)
                    query_params = parse_qs(parsed_url.query)
                    try:
                        page = int(query_params.get("page", [1])[0])
                    except Exception:
                        page = 1
                    page_idx = page - 1
                    if page_idx < len(self.mock_gh_comments_pages):
                        return 0, json.dumps(self.mock_gh_comments_pages[page_idx]), ""
                    else:
                        return 0, "[]", ""
                elif "-f" in args:
                    return self.mock_gh_api_post_result
            elif subcmd == "--version":
                return 0, "gh version 2.40.0", ""
        elif cmd == "agentapi":
            if subcmd == "send-message":
                return self.mock_agentapi_result
            elif subcmd == "get-conversation-metadata":
                return 0, json.dumps({"conversation_id": args[2]}), ""
            elif subcmd == "--version":
                return 0, "agentapi 1.0.0", ""

        return 1, "", f"Unrecognized mock command: {args}"

    def create_bridge(self, config_override=None):
        b = AntigravityRemoteBridge(
            state_dir=self.state_dir,
            runner=self.mock_runner,
            now_fn=self.mock_now,
        )
        if config_override:
            b.config.update(config_override)
        return b

    def make_comment(
        self,
        comment_id,
        user_id=AUTHORIZED_USER_ID,
        command_id=VALID_UUID,
        action="continue_issue",
        issue_number=47,
        expected_branch=EXPECTED_BRANCH,
        expected_head_sha=HEAD_SHA_40,
        issued_offset_sec=-10,
        expiry_offset_sec=300,
        issue_url=EXACT_INBOX_ISSUE_URL,
        marker=ENVELOPE_MARKER,
        raw_body_override=None,
    ):
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

    def test_malformed_or_missing_config_fails_closed(self):
        # Missing config file
        os.remove(os.path.join(self.state_dir, "config.json"))
        bridge = AntigravityRemoteBridge(state_dir=self.state_dir, runner=self.mock_runner, now_fn=self.mock_now)
        valid, msg = bridge.is_config_valid()
        self.assertFalse(valid)
        self.assertEqual(bridge.process_inbox()["error"], STATUS_CODES["CONFIG_INVALID"])

        # Malformed JSON config
        self.write_local_config("invalid json")
        bridge = AntigravityRemoteBridge(state_dir=self.state_dir, runner=self.mock_runner, now_fn=self.mock_now)
        valid, msg = bridge.is_config_valid()
        self.assertFalse(valid)

        # Invalid workspace path (non-existent)
        self.write_local_config({"workspace_dir": "/nonexistent/path/for/test"})
        bridge = AntigravityRemoteBridge(state_dir=self.state_dir, runner=self.mock_runner, now_fn=self.mock_now)
        valid, msg = bridge.is_config_valid()
        self.assertFalse(valid)

    def test_interval_bounds_and_non_bool_config_validation(self):
        # poll_interval_sec < 60
        self.write_local_config({
            "workspace_dir": self.workspace_dir,
            "poll_interval_sec": 0,
        })
        bridge = AntigravityRemoteBridge(state_dir=self.state_dir, runner=self.mock_runner, now_fn=self.mock_now)
        valid, msg = bridge.is_config_valid()
        self.assertFalse(valid)
        self.assertIn("poll_interval_sec", msg)

        # poll_interval_sec > 3600
        self.write_local_config({
            "workspace_dir": self.workspace_dir,
            "poll_interval_sec": 3601,
        })
        bridge = AntigravityRemoteBridge(state_dir=self.state_dir, runner=self.mock_runner, now_fn=self.mock_now)
        valid, msg = bridge.is_config_valid()
        self.assertFalse(valid)

        # min_status_interval_sec < 900
        self.write_local_config({
            "workspace_dir": self.workspace_dir,
            "min_status_interval_sec": 0,
        })
        bridge = AntigravityRemoteBridge(state_dir=self.state_dir, runner=self.mock_runner, now_fn=self.mock_now)
        valid, msg = bridge.is_config_valid()
        self.assertFalse(valid)
        self.assertIn("min_status_interval_sec", msg)

        # Boolean in place of integer for intervals
        self.write_local_config({
            "workspace_dir": self.workspace_dir,
            "poll_interval_sec": True,
        })
        bridge = AntigravityRemoteBridge(state_dir=self.state_dir, runner=self.mock_runner, now_fn=self.mock_now)
        valid, msg = bridge.is_config_valid()
        self.assertFalse(valid)

    def test_string_boolean_consent_fails_closed(self):
        # String "false" or "true" for dispatch_enabled must fail config validation
        self.write_local_config({
            "workspace_dir": self.workspace_dir,
            "dispatch_enabled": "false",
            "status_enabled": True,
        })
        bridge = AntigravityRemoteBridge(state_dir=self.state_dir, runner=self.mock_runner, now_fn=self.mock_now)
        valid, msg = bridge.is_config_valid()
        self.assertFalse(valid)
        self.assertIn("dispatch_enabled must be a JSON boolean", msg)

    def test_unknown_config_keys_fail_closed(self):
        self.write_local_config({
            "workspace_dir": self.workspace_dir,
            "dispatch_enabled": False,
            "status_enabled": False,
            "unauthorized_extra_key": True,
        })
        bridge = AntigravityRemoteBridge(state_dir=self.state_dir, runner=self.mock_runner, now_fn=self.mock_now)
        valid, msg = bridge.is_config_valid()
        self.assertFalse(valid)
        self.assertIn("Unknown configuration key", msg)

    def test_corrupt_watermark_fails_closed(self):
        bridge = self.create_bridge()
        watermark_file = os.path.join(self.state_dir, "watermark.json")
        with open(watermark_file, "w") as f:
            f.write("{corrupt_json: true}")

        res = bridge.process_inbox()
        self.assertEqual(res["status"], "ERROR")
        self.assertEqual(res["error"], "WATERMARK_CORRUPT")

    def test_corrupt_status_file_retains_throttle(self):
        bridge = self.create_bridge()
        # Post initial status to write status_state.json
        ok1, msg1 = bridge.post_status_receipt(force=True)
        self.assertTrue(ok1)

        # Corrupt status_state.json with bad JSON
        status_file = os.path.join(self.state_dir, "status_state.json")
        with open(status_file, "w") as f:
            f.write("{corrupt_json: true}")

        # Immediate status post must remain rate limited, NOT reset throttle to 0
        ok2, msg2 = bridge.post_status_receipt(force=True)
        self.assertFalse(ok2)
        self.assertEqual(msg2, STATUS_CODES["RATE_LIMITED"])

    def test_first_enrollment_watermark_set_and_status_posted(self):
        bridge = self.create_bridge()
        self.mock_gh_comments_pages = [[
            self.make_comment(101),
            self.make_comment(102),
        ]]
        res = bridge.process_inbox()
        self.assertEqual(res["status"], "INITIALIZED")
        self.assertEqual(res["processed_count"], 0)
        self.assertTrue(res["status_posted"])
        wm, err = bridge.get_watermark()
        self.assertEqual(wm, 102)
        self.assertIsNone(err)

    def test_path_traversal_command_id_rejected(self):
        bridge = self.create_bridge()
        bridge.set_watermark(100)

        traversal_id = "../../etc/passwd"
        self.mock_gh_comments_pages = [[self.make_comment(101, command_id=traversal_id)]]

        res = bridge.process_inbox()
        self.assertEqual(res["processed_count"], 1)
        self.assertFalse(res["results"][0]["valid"])
        self.assertEqual(res["results"][0]["reason"], "INVALID_COMMAND_ID")

    def test_invalid_issue_url_or_user_id_rejected(self):
        bridge = self.create_bridge()
        bridge.set_watermark(100)

        # Substring issue URL attack
        bad_url = f"{EXACT_INBOX_ISSUE_URL}/subpath"
        self.mock_gh_comments_pages = [[self.make_comment(101, issue_url=bad_url)]]
        res1 = bridge.process_inbox()
        self.assertEqual(len(res1["results"]), 1)
        self.assertFalse(res1["results"][0]["valid"])
        self.assertEqual(res1["results"][0]["reason"], "INVALID_ISSUE_CONTEXT")

        # Unauthorized user ID
        self.mock_gh_comments_pages = [[self.make_comment(102, user_id=999999)]]
        res2 = bridge.process_inbox()
        self.assertEqual(len(res2["results"]), 1)
        self.assertFalse(res2["results"][0]["valid"])
        self.assertEqual(res2["results"][0]["reason"], "UNAUTHORIZED_USER")

    def test_exact_git_remote_normalization_and_lookalike_rejection(self):
        self.assertEqual(normalize_github_remote("https://github.com/pddkalyan/project-agent-orchestrator.git"), "pddkalyan/project-agent-orchestrator")
        self.assertEqual(normalize_github_remote("git@github.com:pddkalyan/project-agent-orchestrator.git"), "pddkalyan/project-agent-orchestrator")

        # HTTP rejected
        self.assertIsNone(normalize_github_remote("http://github.com/pddkalyan/project-agent-orchestrator.git"))

        # Embedded user:pass credentials rejected
        self.assertIsNone(normalize_github_remote("https://user:pass@github.com/pddkalyan/project-agent-orchestrator"))

        # Rejections for lookalikes and alternate owners/suffixes
        self.assertIsNone(normalize_github_remote("https://github.com.attacker.com/pddkalyan/project-agent-orchestrator"))
        self.assertNotEqual(normalize_github_remote("https://github.com/hacker/project-agent-orchestrator.git"), "pddkalyan/project-agent-orchestrator")
        self.assertNotEqual(normalize_github_remote("https://github.com/pddkalyan/project-agent-orchestrator-fake.git"), "pddkalyan/project-agent-orchestrator")
        self.assertIsNone(normalize_github_remote("not_a_url"))

        bridge = self.create_bridge()
        bridge.set_watermark(100)

        # Wrong origin URL
        self.mock_git_remote_url = "https://github.com/hacker/malicious-repo.git"
        self.mock_gh_comments_pages = [[self.make_comment(101)]]

        res = bridge.process_inbox()
        self.assertEqual(len(res["results"]), 1)
        self.assertFalse(res["results"][0]["valid"])
        self.assertEqual(res["results"][0]["reason"], "LOCAL_GIT_UNVERIFIED")

        # Verify git commands were called with explicit cwd equal to resolved workspace_dir
        git_calls = [c for c in self.mock_runner_calls if c["args"][0] == "git"]
        self.assertTrue(len(git_calls) > 0)
        for call in git_calls:
            self.assertEqual(call["cwd"], os.path.realpath(os.path.abspath(self.workspace_dir)))

    def test_direct_dispatch_bypass_blocked(self):
        bridge = self.create_bridge()
        payload = {"command_id": "11111111-2222-3333-4444-555555555555", "issue_number": 47}
        claim_path = os.path.join(self.state_dir, "claims", "11111111-2222-3333-4444-555555555555.json")

        # Calling _dispatch_command without auth_context must fail
        ok, reason = bridge._dispatch_command(payload, claim_path, auth_context=None)
        self.assertFalse(ok)
        self.assertEqual(reason, "UNAUTHORIZED_DISPATCH_CALL")

    def test_dirty_git_tree_blocks_dispatch_by_default(self):
        bridge = self.create_bridge()
        bridge.set_watermark(100)
        self.mock_git_dirty = True

        self.mock_gh_comments_pages = [[self.make_comment(101, command_id=VALID_UUID, issue_number=47)]]
        res = bridge.process_inbox()

        self.assertEqual(res["processed_count"], 1)
        self.assertTrue(res["results"][0]["valid"])
        self.assertFalse(res["results"][0]["dispatched"])
        self.assertEqual(res["results"][0]["code"], STATUS_CODES["LOCAL_TREE_DIRTY"])

    def test_dirty_git_tree_allows_opted_in_continue_issue(self):
        self.write_local_config({
            "workspace_dir": self.workspace_dir,
            "dispatch_enabled": True,
            "status_enabled": True,
            "allow_dirty_continue": True,
            "conversation_id": "conv-test-123",
            "poll_interval_sec": 900,
            "min_status_interval_sec": 900,
        })
        bridge = AntigravityRemoteBridge(state_dir=self.state_dir, runner=self.mock_runner, now_fn=self.mock_now)
        bridge.set_watermark(100)
        self.mock_git_dirty = True

        self.mock_gh_comments_pages = [[self.make_comment(101, command_id=VALID_UUID, issue_number=47, action="continue_issue")]]
        res = bridge.process_inbox()

        self.assertEqual(res["processed_count"], 1)
        self.assertTrue(res["results"][0]["valid"])
        self.assertTrue(res["results"][0]["dispatched"])

    def test_timezone_naive_timestamps_rejected(self):
        self.assertIsNone(parse_iso_timestamp("2026-10-10T12:00:00"))
        self.assertIsNotNone(parse_iso_timestamp("2026-10-10T12:00:00Z"))
        self.assertIsNotNone(parse_iso_timestamp("2026-10-10T12:00:00+00:00"))

        bridge = self.create_bridge()
        bridge.set_watermark(100)

        payload = {
            "marker": ENVELOPE_MARKER,
            "command_id": VALID_UUID,
            "action": "continue_issue",
            "issue_number": 47,
            "expected_branch": EXPECTED_BRANCH,
            "expected_head_sha": HEAD_SHA_40,
            "issued_at": "2026-10-10T11:59:00",  # Naive, no timezone offset
            "expires_at": "2026-10-10T12:05:00",  # Naive, no timezone offset
        }
        body = f"Here is command:\n```json\n{json.dumps(payload)}\n```"
        self.mock_gh_comments_pages = [[self.make_comment(101, raw_body_override=body)]]

        res = bridge.process_inbox()
        self.assertEqual(len(res["results"]), 1)
        self.assertFalse(res["results"][0]["valid"])
        self.assertEqual(res["results"][0]["reason"], "INVALID_TIMESTAMPS")

    def test_positive_continue_issue_dispatch(self):
        bridge = self.create_bridge()
        bridge.set_watermark(100)
        cmd_id = "11111111-2222-3333-4444-555555555555"
        self.mock_gh_comments_pages = [[self.make_comment(101, command_id=cmd_id, issue_number=47)]]

        res = bridge.process_inbox()
        self.assertEqual(res["status"], "SUCCESS")
        self.assertEqual(res["processed_count"], 1)
        self.assertTrue(res["results"][0]["valid"])
        self.assertTrue(res["results"][0]["dispatched"])

        claim_file = os.path.join(self.state_dir, "claims", f"{cmd_id}.json")
        self.assertTrue(os.path.exists(claim_file))
        with open(claim_file, "r") as f:
            cdata = json.load(f)
        self.assertEqual(cdata["status"], "SENT")
        self.assertEqual(cdata["dispatch_result"], STATUS_CODES["DISPATCH_SUCCESS"])

    def test_replay_blocked_across_restart(self):
        bridge = self.create_bridge()
        bridge.set_watermark(100)
        cmd_id = "11111111-2222-3333-4444-555555555555"
        self.mock_gh_comments_pages = [[self.make_comment(101, command_id=cmd_id, issue_number=47)]]

        res = bridge.process_inbox()
        self.assertEqual(res["status"], "SUCCESS")

        claim_file = os.path.join(self.state_dir, "claims", f"{cmd_id}.json")
        auth_context = {
            "authorized_user_id": AUTHORIZED_USER_ID,
            "command_id": cmd_id,
            "expected_branch": EXPECTED_BRANCH,
            "expected_head_sha": HEAD_SHA_40,
        }
        payload = {"command_id": cmd_id, "issue_number": 47}

        # Attempt re-dispatch on SENT claim must be blocked
        ok, reason = bridge._dispatch_command(payload, claim_file, auth_context=auth_context)
        self.assertFalse(ok)
        self.assertEqual(reason, STATUS_CODES["REPLAY_BLOCKED"])

    def test_status_rate_limiting_with_force_true(self):
        bridge = self.create_bridge()

        # First status post
        ok1, msg1 = bridge.post_status_receipt(force=True)
        self.assertTrue(ok1)
        self.assertEqual(msg1, "STATUS_POSTED")

        # Immediate second status post even with force=True MUST BE RATE LIMITED
        ok2, msg2 = bridge.post_status_receipt(force=True)
        self.assertFalse(ok2)
        self.assertEqual(msg2, STATUS_CODES["RATE_LIMITED"])

    def test_public_status_receipt_sanitization(self):
        bridge = self.create_bridge()

        ok, msg = bridge.post_status_receipt(force=True, last_result_code=STATUS_CODES["DISPATCH_SUCCESS"])
        self.assertTrue(ok)

        post_calls = [c for c in self.mock_runner_calls if c["args"][0] == "gh" and "-f" in c["args"]]
        self.assertEqual(len(post_calls), 1)
        args = post_calls[0]["args"]
        body_arg = [a for a in args if a.startswith("body=")][0]

        json_str = body_arg.split("```json\n")[1].split("\n```")[0]
        data = json.loads(json_str)

        self.assertIn("receipt_id", data)
        self.assertEqual(data["last_command_result_code"], STATUS_CODES["DISPATCH_SUCCESS"])
        self.assertEqual(data["antigravity_execution"], "UNKNOWN")
        self.assertEqual(data["antigravity_model"], "UNKNOWN")

        self.assertNotIn(self.workspace_dir, body_arg)
        self.assertNotIn("conv-test-123", body_arg)
        self.assertNotIn("stderr", body_arg)

    def test_duplicate_and_atomic_claim(self):
        bridge = self.create_bridge()

        payload = {"test": 1}
        cmd_id = "22222222-3333-4444-5555-666666666666"

        ok1, msg1, path1 = bridge._claim_command(cmd_id, payload)
        self.assertTrue(ok1)

        ok2, msg2, path2 = bridge._claim_command(cmd_id, payload)
        self.assertFalse(ok2)
        self.assertEqual(msg2, "DUPLICATE_CLAIM")

    def test_github_api_pagination(self):
        bridge = self.create_bridge()
        bridge.set_watermark(100)

        p1 = [self.make_comment(100 + i, command_id=f"10000000-0000-0000-0000-{i:012d}") for i in range(1, 101)]
        p2 = [self.make_comment(201, command_id="20120120-1201-2012-0120-120120120120")]
        self.mock_gh_comments_pages = [p1, p2]

        comments, err = bridge.fetch_inbox_comments()
        self.assertIsNone(err)
        self.assertEqual(len(comments), 101)

    def test_periodic_polling_heartbeat_when_idle(self):
        bridge = self.create_bridge()
        bridge.set_watermark(100)
        self.mock_gh_comments_pages = [[]]

        sleep_calls = []

        def mock_sleep(secs):
            sleep_calls.append(secs)

        run_polling_loop(bridge, max_iterations=1, sleep_fn=mock_sleep)

        post_calls = [c for c in self.mock_runner_calls if c["args"][0] == "gh" and "-f" in c["args"]]
        self.assertEqual(len(post_calls), 1)

    def test_dry_run_verifier(self):
        bridge = self.create_bridge()
        verifier = bridge.verify_sidecar_setup()

        self.assertIsInstance(verifier, dict)
        self.assertIn("valid", verifier)
        self.assertIn("checks", verifier)
        self.assertTrue(verifier["checks"]["config_valid"])
        self.assertTrue(verifier["checks"]["git_repo_valid"])
        self.assertTrue(verifier["checks"]["gh_cli_available"])

    def test_missing_cli_executables_handled(self):
        def missing_runner(args, cwd=None, input_data=None):
            return 127, "", f"Binary not found: {args[0]}"

        bridge = AntigravityRemoteBridge(
            state_dir=self.state_dir,
            runner=missing_runner,
            now_fn=self.mock_now,
        )
        comments, err = bridge.fetch_inbox_comments()
        self.assertIsNone(comments)
        self.assertEqual(err, "GH_CLI_MISSING")

        ok, err_code = bridge.post_status_receipt(force=True)
        self.assertFalse(ok)
        self.assertEqual(err_code, "GH_CLI_MISSING")

    def test_ping_conversation_works_with_dispatch_disabled_when_ping_enabled(self):
        self.write_local_config({
            "workspace_dir": self.workspace_dir,
            "dispatch_enabled": False,  # dispatch_enabled disabled!
            "ping_enabled": True,       # ping_enabled enabled independently!
            "status_enabled": True,
            "conversation_id": "conv-ping-standalone",
            "poll_interval_sec": 900,
            "min_status_interval_sec": 900,
            "allow_dirty_continue": False,
        })
        bridge = AntigravityRemoteBridge(state_dir=self.state_dir, runner=self.mock_runner, now_fn=self.mock_now)
        bridge.set_watermark(100)

        cmd_id = "33333333-4444-5555-6666-777777777777"
        self.mock_gh_comments_pages = [[self.make_comment(101, command_id=cmd_id, action="ping_conversation", issue_number=47)]]

        res = bridge.process_inbox()
        self.assertEqual(res["status"], "SUCCESS")
        self.assertEqual(res["processed_count"], 1)
        self.assertTrue(res["results"][0]["valid"])
        self.assertTrue(res["results"][0]["dispatched"])
        self.assertEqual(res["results"][0]["code"], STATUS_CODES["PING_DISPATCHED"])

    def test_ping_conversation_positive_dispatch(self):
        self.write_local_config({
            "workspace_dir": self.workspace_dir,
            "dispatch_enabled": True,
            "ping_enabled": True,
            "status_enabled": True,
            "conversation_id": "conv-ping-123",
            "poll_interval_sec": 900,
            "min_status_interval_sec": 900,
            "allow_dirty_continue": False,
        })
        bridge = AntigravityRemoteBridge(state_dir=self.state_dir, runner=self.mock_runner, now_fn=self.mock_now)
        bridge.set_watermark(100)

        cmd_id = "33333333-4444-5555-6666-777777777777"
        self.mock_gh_comments_pages = [[self.make_comment(101, command_id=cmd_id, action="ping_conversation", issue_number=47)]]

        res = bridge.process_inbox()
        self.assertEqual(res["status"], "SUCCESS")
        self.assertEqual(res["processed_count"], 1)
        self.assertTrue(res["results"][0]["valid"])
        self.assertTrue(res["results"][0]["dispatched"])
        self.assertEqual(res["results"][0]["code"], STATUS_CODES["PING_DISPATCHED"])

        # Check agentapi call received exact prewritten prompt
        agent_calls = [c for c in self.mock_runner_calls if c["args"][0] == "agentapi" and c["args"][1] == "send-message"]
        self.assertEqual(len(agent_calls), 1)
        self.assertEqual(agent_calls[0]["args"][2], "conv-ping-123")
        self.assertIn("Controller connectivity check only.", agent_calls[0]["args"][3])

    def test_ping_conversation_disabled_ping_enabled_rejected(self):
        self.write_local_config({
            "workspace_dir": self.workspace_dir,
            "dispatch_enabled": True,
            "ping_enabled": False,  # Disabled
            "status_enabled": True,
            "conversation_id": "conv-ping-123",
            "poll_interval_sec": 900,
            "min_status_interval_sec": 900,
            "allow_dirty_continue": False,
        })
        bridge = AntigravityRemoteBridge(state_dir=self.state_dir, runner=self.mock_runner, now_fn=self.mock_now)
        bridge.set_watermark(100)

        cmd_id = "33333333-4444-5555-6666-777777777777"
        self.mock_gh_comments_pages = [[self.make_comment(101, command_id=cmd_id, action="ping_conversation", issue_number=47)]]

        res = bridge.process_inbox()
        self.assertEqual(res["processed_count"], 1)
        self.assertTrue(res["results"][0]["valid"])
        self.assertFalse(res["results"][0]["dispatched"])
        self.assertEqual(res["results"][0]["code"], STATUS_CODES["PING_FAILED"])

    def test_ping_requires_status_receipts_enabled(self):
        # A native message must not be sent silently when the owner has
        # opted out of the public status/acknowledgement channel.
        self.write_local_config({
            "workspace_dir": self.workspace_dir,
            "dispatch_enabled": False, "ping_enabled": True,
            "status_enabled": False, "conversation_id": "conv-existing",
        })
        bridge = AntigravityRemoteBridge(
            state_dir=self.state_dir, runner=self.mock_runner, now_fn=self.mock_now
        )
        bridge.set_watermark(100)
        self.mock_gh_comments_pages = [[
            self.make_comment(101, command_id="11112222-3333-4444-5555-666677778888",
                              action="ping_conversation", issue_number=47)
        ]]
        result = bridge.process_inbox()
        self.assertEqual(result["results"][0]["code"], STATUS_CODES["PING_FAILED"])
        self.assertFalse(result["results"][0]["dispatched"])
        self.assertFalse(any(
            call["args"][:2] == ["agentapi", "send-message"]
            for call in self.mock_runner_calls
        ))

    def test_ping_conversation_allowed_when_dirty_on_validated_sha(self):
        self.write_local_config({
            "workspace_dir": self.workspace_dir,
            "dispatch_enabled": True,
            "ping_enabled": True,
            "status_enabled": True,
            "conversation_id": "conv-ping-123",
            "poll_interval_sec": 900,
            "min_status_interval_sec": 900,
            "allow_dirty_continue": False,  # False, but ping on valid SHA/branch is allowed
        })
        bridge = AntigravityRemoteBridge(state_dir=self.state_dir, runner=self.mock_runner, now_fn=self.mock_now)
        bridge.set_watermark(100)
        self.mock_git_dirty = True

        cmd_id = "33333333-4444-5555-6666-777777777777"
        self.mock_gh_comments_pages = [[self.make_comment(101, command_id=cmd_id, action="ping_conversation", issue_number=47)]]

        res = bridge.process_inbox()
        self.assertEqual(res["processed_count"], 1)
        self.assertTrue(res["results"][0]["valid"])
        self.assertTrue(res["results"][0]["dispatched"])
        self.assertEqual(res["results"][0]["code"], STATUS_CODES["PING_DISPATCHED"])

    def test_ping_conversation_rejected_on_wrong_issue_number(self):
        self.write_local_config({
            "workspace_dir": self.workspace_dir,
            "dispatch_enabled": True,
            "ping_enabled": True,
            "status_enabled": True,
            "conversation_id": "conv-ping-123",
        })
        bridge = AntigravityRemoteBridge(state_dir=self.state_dir, runner=self.mock_runner, now_fn=self.mock_now)
        bridge.set_watermark(100)

        # Target Issue #44 instead of #47 must be rejected for ping_conversation
        cmd_id = "33333333-4444-5555-6666-777777777777"
        self.mock_gh_comments_pages = [[self.make_comment(101, command_id=cmd_id, action="ping_conversation", issue_number=44)]]

        res = bridge.process_inbox()
        self.assertEqual(res["processed_count"], 1)
        self.assertFalse(res["results"][0]["valid"])
        self.assertEqual(res["results"][0]["reason"], "UNAUTHORIZED_ISSUE_TARGET")

    def test_ping_conversation_metadata_mismatch_fails(self):
        self.write_local_config({
            "workspace_dir": self.workspace_dir,
            "dispatch_enabled": True,
            "ping_enabled": True,
            "status_enabled": True,
            "conversation_id": "conv-ping-123",
        })

        def mock_runner_with_meta_mismatch(args, cwd=None, input_data=None):
            if args[0] == "agentapi" and args[1] == "get-conversation-metadata":
                # Return mismatched conversation ID
                return 0, json.dumps({"conversation_id": "conv-DIFFERENT-999"}), ""
            return self.mock_runner(args, cwd=cwd, input_data=input_data)

        bridge = AntigravityRemoteBridge(state_dir=self.state_dir, runner=mock_runner_with_meta_mismatch, now_fn=self.mock_now)
        bridge.set_watermark(100)

        cmd_id = "33333333-4444-5555-6666-777777777777"
        self.mock_gh_comments_pages = [[self.make_comment(101, command_id=cmd_id, action="ping_conversation", issue_number=47)]]

        res = bridge.process_inbox()
        self.assertEqual(res["processed_count"], 1)
        self.assertTrue(res["results"][0]["valid"])
        self.assertFalse(res["results"][0]["dispatched"])
        self.assertEqual(res["results"][0]["code"], STATUS_CODES["PING_FAILED"])

    def test_ping_conversation_blocks_failed_metadata_lookup(self):
        self.write_local_config({
            "workspace_dir": self.workspace_dir,
            "dispatch_enabled": False, "ping_enabled": True,
            "status_enabled": True, "conversation_id": "conv-existing",
        })

        def metadata_failure(args, cwd=None, input_data=None):
            if args[:2] == ["agentapi", "get-conversation-metadata"]:
                return 1, "", "not available"
            return self.mock_runner(args, cwd=cwd, input_data=input_data)

        bridge = AntigravityRemoteBridge(
            state_dir=self.state_dir, runner=metadata_failure, now_fn=self.mock_now
        )
        bridge.set_watermark(100)
        self.mock_gh_comments_pages = [[
            self.make_comment(101, command_id="33333333-4444-5555-6666-777777777777",
                              action="ping_conversation", issue_number=47)
        ]]
        result = bridge.process_inbox()
        self.assertEqual(result["results"][0]["code"], STATUS_CODES["PING_FAILED"])
        self.assertFalse(result["results"][0]["dispatched"])
        self.assertFalse(any(c["args"][:2] == ["agentapi", "send-message"]
                             for c in self.mock_runner_calls))

    def test_ping_conversation_blocks_malformed_or_missing_metadata(self):
        for output in ["garbled", "{}", "[]", '{"conversation_id": ""}',
                       '{"conversation_id": "wrong"}']:
            with self.subTest(output=output):
                self.mock_runner_calls = []
                self.write_local_config({
                    "workspace_dir": self.workspace_dir,
                    "dispatch_enabled": False, "ping_enabled": True,
                    "status_enabled": True, "conversation_id": "conv-existing",
                })

                def bad_metadata(args, cwd=None, input_data=None):
                    if args[:2] == ["agentapi", "get-conversation-metadata"]:
                        return 0, output, ""
                    return self.mock_runner(args, cwd=cwd, input_data=input_data)

                bridge = AntigravityRemoteBridge(
                    state_dir=self.state_dir, runner=bad_metadata, now_fn=self.mock_now
                )
                bridge.set_watermark(100)
                command_id = "44444444-5555-6666-7777-888888888888"
                claim_path = os.path.join(self.state_dir, "claims", command_id + ".json")
                if os.path.exists(claim_path):
                    os.remove(claim_path)
                self.mock_gh_comments_pages = [[
                    self.make_comment(101, command_id=command_id,
                                      action="ping_conversation", issue_number=47)
                ]]
                result = bridge.process_inbox()
                self.assertEqual(result["results"][0]["code"], STATUS_CODES["PING_FAILED"])
                self.assertFalse(result["results"][0]["dispatched"])
                self.assertFalse(any(c["args"][:2] == ["agentapi", "send-message"]
                                     for c in self.mock_runner_calls))

    def test_throttled_ping_result_survives_until_next_public_heartbeat(self):
        bridge = self.create_bridge()
        first_ok, _ = bridge.post_status_receipt(force=True)
        self.assertTrue(first_ok)
        blocked_ok, blocked_code = bridge.post_status_receipt(
            force=False, last_result_code=STATUS_CODES["PING_DISPATCHED"]
        )
        self.assertFalse(blocked_ok)
        self.assertEqual(blocked_code, STATUS_CODES["RATE_LIMITED"])
        self.assertTrue(os.path.exists(bridge.pending_status_file))

        # The following scheduled poll is idle; it must not overwrite the
        # queued ping outcome with NO_OP.
        self.now += timedelta(seconds=901)
        second_ok, _ = bridge.post_status_receipt(
            force=False, last_result_code=STATUS_CODES["NO_OP"]
        )
        self.assertTrue(second_ok)
        posts = [call for call in self.mock_runner_calls
                 if call["args"][:2] == ["gh", "api"] and "-f" in call["args"]]
        self.assertEqual(len(posts), 2)
        body = next(arg[5:] for arg in posts[-1]["args"]
                    if arg.startswith("body="))
        data = json.loads(body.split("```json\n")[1].split("\n```")[0])
        self.assertEqual(data["last_command_result_code"], STATUS_CODES["PING_DISPATCHED"])
        self.assertFalse(os.path.exists(bridge.pending_status_file))

    def test_pending_result_survives_github_outage(self):
        bridge = self.create_bridge()
        self.mock_gh_api_post_result = (1, "", "API offline")
        sent_ok, reason = bridge.post_status_receipt(
            force=False, last_result_code=STATUS_CODES["PING_FAILED"]
        )
        self.assertFalse(sent_ok)
        self.assertEqual(reason, "GH_API_STATUS_POST_FAILED")
        self.assertTrue(os.path.exists(bridge.pending_status_file))

        self.mock_gh_api_post_result = (0, "{}", "")
        next_ok, _ = bridge.post_status_receipt(
            force=False, last_result_code=STATUS_CODES["NO_OP"]
        )
        self.assertTrue(next_ok)
        posts = [call for call in self.mock_runner_calls
                 if call["args"][:2] == ["gh", "api"] and "-f" in call["args"]]
        body = next(arg[5:] for arg in posts[-1]["args"]
                    if arg.startswith("body="))
        data = json.loads(body.split("```json\n")[1].split("\n```")[0])
        self.assertEqual(data["last_command_result_code"], STATUS_CODES["PING_FAILED"])
        self.assertFalse(os.path.exists(bridge.pending_status_file))

    def test_ping_rejects_head_moving_between_validation_and_dispatch(self):
        self.write_local_config({
            "workspace_dir": self.workspace_dir,
            "dispatch_enabled": False, "ping_enabled": True,
            "status_enabled": False, "conversation_id": "conv-existing",
        })
        bridge = AntigravityRemoteBridge(
            state_dir=self.state_dir, runner=self.mock_runner, now_fn=self.mock_now
        )
        bridge.set_watermark(100)
        self.mock_gh_comments_pages = [[
            self.make_comment(101, command_id="99999999-aaaa-bbbb-cccc-dddddddddddd",
                              action="ping_conversation", issue_number=47)
        ]]
        get_info = bridge.get_local_git_info
        calls = [0]

        def simulated_head_change():
            calls[0] += 1
            if calls[0] == 2:
                self.mock_git_sha = "f" * 40
            return get_info()

        bridge.get_local_git_info = simulated_head_change
        res = bridge.process_inbox()
        self.assertFalse(res["results"][0]["dispatched"])
        self.assertEqual(res["results"][0]["code"], STATUS_CODES["INVALID_PAYLOAD"])
        self.assertFalse(any(
            call["args"][:2] == ["agentapi", "send-message"]
            for call in self.mock_runner_calls
        ))

    def test_ping_rejects_remote_change_between_validation_and_dispatch(self):
        self.write_local_config({
            "workspace_dir": self.workspace_dir,
            "dispatch_enabled": False, "ping_enabled": True,
            "status_enabled": False, "conversation_id": "conv-existing",
        })
        bridge = AntigravityRemoteBridge(
            state_dir=self.state_dir, runner=self.mock_runner, now_fn=self.mock_now
        )
        bridge.set_watermark(100)
        self.mock_gh_comments_pages = [[
            self.make_comment(101, command_id="88888888-aaaa-bbbb-cccc-dddddddddddd",
                              action="ping_conversation", issue_number=47)
        ]]
        get_info = bridge.get_local_git_info
        calls = [0]

        def swapped_origin():
            calls[0] += 1
            if calls[0] == 2:
                self.mock_git_remote_url = "https://github.com/attacker/repository.git"
            return get_info()

        bridge.get_local_git_info = swapped_origin
        res = bridge.process_inbox()
        self.assertFalse(res["results"][0]["dispatched"])
        self.assertEqual(res["results"][0]["code"], STATUS_CODES["INVALID_PAYLOAD"])
        self.assertFalse(any(
            call["args"][:2] == ["agentapi", "send-message"]
            for call in self.mock_runner_calls
        ))

    def test_ping_conversation_windows_path_and_mock(self):
        windows_workspace = os.path.join(self.test_dir, "C_drive", "project")
        os.makedirs(windows_workspace, exist_ok=True)
        self.write_local_config({
            "workspace_dir": windows_workspace,
            "dispatch_enabled": True,
            "ping_enabled": True,
            "status_enabled": True,
            "conversation_id": "conv-win-123",
        })
        bridge = AntigravityRemoteBridge(state_dir=self.state_dir, runner=self.mock_runner, now_fn=self.mock_now)
        bridge.set_watermark(100)

        cmd_id = "44444444-5555-6666-7777-888888888888"
        self.mock_gh_comments_pages = [[self.make_comment(101, command_id=cmd_id, action="ping_conversation", issue_number=47)]]

        res = bridge.process_inbox()
        self.assertEqual(res["processed_count"], 1)
        self.assertTrue(res["results"][0]["dispatched"])
        self.assertEqual(res["results"][0]["code"], STATUS_CODES["PING_DISPATCHED"])


if __name__ == "__main__":
    unittest.main()
