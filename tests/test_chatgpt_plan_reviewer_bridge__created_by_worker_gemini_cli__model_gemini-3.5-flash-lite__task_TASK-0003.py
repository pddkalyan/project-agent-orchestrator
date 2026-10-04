#!/usr/bin/env python3
"""Deterministic offline tests for TASK-0003 ChatGPT plan reviewer bridge."""

import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "chatgpt_plan_reviewer_bridge__created_by_worker_gemini_cli__model_gemini-3.5-flash-lite__task_TASK-0003.py"
spec = importlib.util.spec_from_file_location("chatgpt_plan_reviewer_bridge", SCRIPT)
bridge = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(bridge)


class TestChatGPTPlanReviewerBridge(unittest.TestCase):
    def setUp(self):
        self.valid_sha = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"
        self.valid_packet = {
            "task_id": "TASK-0003",
            "reviewed_sha": self.valid_sha,
        }
        self.valid_catalog = {
            "authorized_models": ["gpt-6-astra", "other-model"],
            "user_authorized": True,
        }
        self.valid_auth = {
            "active": True,
            "expired": False,
        }
        self.valid_allowance = {
            "billing_mode": "ZERO_SPEND_PLAN",
            "remaining_requests": 10,
        }

    def test_01_model_catalog_missing_astra_blocks(self):
        catalog = {"authorized_models": ["gpt-4"], "user_authorized": True}
        res = bridge.evaluate_review(
            self.valid_sha, self.valid_packet, catalog, self.valid_auth, self.valid_allowance, {"status": "APPROVED"}
        )
        self.assertEqual(res["status"], "BLOCKED_NO_ASTRA")

    def test_02_model_catalog_unauthorized_user_blocks(self):
        catalog = {"authorized_models": ["gpt-6-astra"], "user_authorized": False}
        res = bridge.evaluate_review(
            self.valid_sha, self.valid_packet, catalog, self.valid_auth, self.valid_allowance, {"status": "APPROVED"}
        )
        self.assertEqual(res["status"], "BLOCKED_NO_ASTRA")

    def test_03_missing_auth_profile_blocks(self):
        res = bridge.evaluate_review(
            self.valid_sha, self.valid_packet, self.valid_catalog, None, self.valid_allowance, {"status": "APPROVED"}
        )
        self.assertEqual(res["status"], "BLOCKED_AUTH_REQUIRED")

    def test_04_expired_auth_profile_blocks(self):
        auth = {"active": True, "expired": True}
        res = bridge.evaluate_review(
            self.valid_sha, self.valid_packet, self.valid_catalog, auth, self.valid_allowance, {"status": "APPROVED"}
        )
        self.assertEqual(res["status"], "BLOCKED_AUTH_REQUIRED")

    def test_05_invalid_plan_allowance_blocks(self):
        allowance = {"billing_mode": "PAID_API_KEY", "remaining_requests": 10}
        res = bridge.evaluate_review(
            self.valid_sha, self.valid_packet, self.valid_catalog, self.valid_auth, allowance, {"status": "APPROVED"}
        )
        self.assertEqual(res["status"], "BLOCKED_PLAN_ALLOWANCE")

    def test_06_exhausted_plan_allowance_blocks(self):
        allowance = {"billing_mode": "ZERO_SPEND_PLAN", "remaining_requests": 0}
        res = bridge.evaluate_review(
            self.valid_sha, self.valid_packet, self.valid_catalog, self.valid_auth, allowance, {"status": "APPROVED"}
        )
        self.assertEqual(res["status"], "BLOCKED_PLAN_ALLOWANCE")

    def test_07_sha_mismatch_rejects(self):
        packet = {"task_id": "TASK-0003", "reviewed_sha": "0000000000000000000000000000000000000000"}
        res = bridge.evaluate_review(
            self.valid_sha, packet, self.valid_catalog, self.valid_auth, self.valid_allowance, {"status": "APPROVED"}
        )
        self.assertEqual(res["status"], "REJECTED")
        self.assertIn("SHA binding validation failed", res["reason"])

    def test_08_approved_verdict_succeeds(self):
        res = bridge.evaluate_review(
            self.valid_sha, self.valid_packet, self.valid_catalog, self.valid_auth, self.valid_allowance, {"status": "APPROVED"}
        )
        self.assertEqual(res["status"], "APPROVED")
        self.assertEqual(res["reviewed_sha"], self.valid_sha)
        self.assertFalse(res["approval_confirmation"]["merge_allowed"])

    def test_09_rejected_verdict_returns_correction_package(self):
        verdict = {
            "status": "REJECTED",
            "findings": [{"severity": "P1", "issue": "test issue", "correction": "fix test issue"}],
        }
        res = bridge.evaluate_review(
            self.valid_sha, self.valid_packet, self.valid_catalog, self.valid_auth, self.valid_allowance, verdict
        )
        self.assertEqual(res["status"], "REJECTED")
        self.assertIn("correction_package", res)
        self.assertEqual(len(res["findings"]), 1)

    def test_10_idempotency_key_is_deterministic(self):
        k1 = bridge.compute_idempotency_key("TASK-0003", self.valid_sha, 1)
        k2 = bridge.compute_idempotency_key("TASK-0003", self.valid_sha, 1)
        k3 = bridge.compute_idempotency_key("TASK-0003", self.valid_sha, 2)
        self.assertEqual(k1, k2)
        self.assertNotEqual(k1, k3)

    def test_11_credential_sanitization(self):
        token = "github_pat_" + "C" * 40
        sanitized = bridge.sanitize_text(f"log header\ntoken: {token}\nlog footer")
        self.assertNotIn(token, sanitized)
        self.assertIn("log header", sanitized)
        self.assertIn("log footer", sanitized)


if __name__ == "__main__":
    unittest.main()
