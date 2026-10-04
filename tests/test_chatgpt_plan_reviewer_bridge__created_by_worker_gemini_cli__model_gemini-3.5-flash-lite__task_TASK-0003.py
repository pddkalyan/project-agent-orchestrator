#!/usr/bin/env python3
"""Deterministic offline tests for TASK-0003 ChatGPT-plan reviewer bridge."""

import importlib.util
import json
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "chatgpt_plan_reviewer_bridge__created_by_worker_gemini_cli__model_gemini-3.5-flash-lite__task_TASK-0003.py"
spec = importlib.util.spec_from_file_location("chatgpt_plan_reviewer_bridge", SCRIPT)
bridge = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(bridge)


class TestChatGPTPlanReviewerBridge(unittest.TestCase):
    def setUp(self):
        self.sha = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"
        self.base = "b1b2c3d4e5f6b1b2c3d4e5f6b1b2c3d4e5f6b1b2"
        self.catalog = {"authorized_models": ["gpt-6-astra", "other-model"], "user_authorized": True}
        self.auth = {"active": True, "expired": False, "profile_id": "local-profile-1"}
        self.allowance = {
            "billing_mode": "ZERO_SPEND_PLAN",
            "separately_billed": False,
            "credits_enabled": False,
            "remaining_requests": 5,
        }
        self.request = {
            "repository": "pddkalyan/project-agent-orchestrator",
            "pr_number": 3,
            "base_sha": self.base,
            "candidate_sha": self.sha,
            "reviewer_context_version": "task0003-v1",
            "changed_files": ["a.py", "reviewer/PROJECT_CONTEXT.md"],
            "required_ci": [
                {
                    "run_id": 123456,
                    "head_sha": self.sha,
                    "result": "PASS",
                    "digest": "sha256:" + "c" * 64,
                }
            ],
        }
        self.finding = {
            "severity": "P1",
            "exact_location": "scripts/example.py:10",
            "exact_problem": "Example problem",
            "why_it_matters": "It breaks the safety boundary.",
            "required_correction": "Fix the example deterministically.",
            "acceptance_criteria": ["Regression reproduces the old failure.", "Corrected path passes."],
            "implementation_guidance": "Use a deterministic helper.",
        }

    def evaluate(self, verdict, **overrides):
        args = dict(
            task_id="TASK-0003",
            expected_sha=self.sha,
            review_request=self.request,
            model_catalog=self.catalog,
            auth_profile=self.auth,
            plan_allowance=self.allowance,
            verdict_payload=verdict,
            current_attempt=0,
            max_retries=3,
        )
        args.update(overrides)
        return bridge.evaluate_review(**args)

    def test_01_exact_astra_model_allowed(self):
        self.assertEqual(bridge.validate_model_catalog(self.catalog), (True, "MODEL_ALLOWED"))

    def test_02_missing_astra_blocks(self):
        ok, status = bridge.validate_model_catalog({"authorized_models": ["gpt-5"], "user_authorized": True})
        self.assertFalse(ok)
        self.assertEqual(status, bridge.BLOCKED_NO_ASTRA)

    def test_03_similar_model_name_is_not_astra(self):
        ok, _ = bridge.validate_model_catalog({"authorized_models": ["gpt-6-astra-preview"], "user_authorized": True})
        self.assertFalse(ok)

    def test_04_missing_auth_blocks_before_review(self):
        result = self.evaluate({"status": "APPROVED"}, auth_profile=None)
        self.assertEqual(result["status"], bridge.BLOCKED_AUTH_REQUIRED)

    def test_05_expired_auth_blocks(self):
        auth = {"active": True, "expired": True, "profile_id": "local"}
        self.assertEqual(self.evaluate({"status": "APPROVED"}, auth_profile=auth)["status"], bridge.BLOCKED_AUTH_REQUIRED)

    def test_06_auth_metadata_rejects_secret_fields(self):
        auth = {"active": True, "expired": False, "profile_id": "local", "refresh_token": "synthetic"}
        self.assertEqual(self.evaluate({"status": "APPROVED"}, auth_profile=auth)["status"], bridge.BLOCKED_AUTH_REQUIRED)

    def test_07_paid_billing_mode_blocks(self):
        allowance = dict(self.allowance, billing_mode="PAID_API_KEY")
        self.assertEqual(self.evaluate({"status": "APPROVED"}, plan_allowance=allowance)["status"], bridge.BLOCKED_PLAN_ALLOWANCE)

    def test_08_separately_billed_or_credits_enabled_blocks(self):
        self.assertEqual(
            self.evaluate({"status": "APPROVED"}, plan_allowance=dict(self.allowance, separately_billed=True))["status"],
            bridge.BLOCKED_PLAN_ALLOWANCE,
        )
        self.assertEqual(
            self.evaluate({"status": "APPROVED"}, plan_allowance=dict(self.allowance, credits_enabled=True))["status"],
            bridge.BLOCKED_PLAN_ALLOWANCE,
        )

    def test_09_exhausted_allowance_blocks(self):
        self.assertEqual(
            self.evaluate({"status": "APPROVED"}, plan_allowance=dict(self.allowance, remaining_requests=0))["status"],
            bridge.BLOCKED_PLAN_ALLOWANCE,
        )

    def test_10_review_request_exact_sha_and_ci_pass(self):
        self.assertEqual(bridge.validate_review_request(self.sha, self.request), (True, "CI_BOUND"))

    def test_11_candidate_sha_drift_blocks(self):
        request = dict(self.request, candidate_sha="0" * 40)
        result = self.evaluate({"status": "APPROVED"}, review_request=request)
        self.assertEqual(result["status"], bridge.BLOCKED_EVIDENCE_MISMATCH)
        self.assertEqual(result["reason"], "SHA_MISMATCH")

    def test_12_ci_sha_drift_blocks(self):
        request = dict(self.request)
        request["required_ci"] = [dict(self.request["required_ci"][0], head_sha="0" * 40)]
        result = self.evaluate({"status": "APPROVED"}, review_request=request)
        self.assertEqual(result["status"], bridge.BLOCKED_EVIDENCE_MISMATCH)
        self.assertEqual(result["reason"], "CI_SHA_MISMATCH")

    def test_13_ci_failure_or_bad_digest_blocks(self):
        request = dict(self.request)
        request["required_ci"] = [dict(self.request["required_ci"][0], result="FAIL")]
        self.assertEqual(self.evaluate({"status": "APPROVED"}, review_request=request)["status"], bridge.BLOCKED_EVIDENCE_MISMATCH)
        request["required_ci"] = [dict(self.request["required_ci"][0], digest="sha256:bad")]
        self.assertEqual(self.evaluate({"status": "APPROVED"}, review_request=request)["status"], bridge.BLOCKED_EVIDENCE_MISMATCH)

    def test_14_context_and_changed_files_are_required(self):
        request = dict(self.request)
        request.pop("reviewer_context_version")
        self.assertFalse(bridge.validate_review_request(self.sha, request)[0])
        request = dict(self.request, changed_files=["a.py", "a.py"])
        self.assertEqual(bridge.validate_review_request(self.sha, request)[1], "DUPLICATE_CHANGED_FILES")

    def test_15_approved_verdict_is_exact_sha_and_never_mergeable(self):
        result = self.evaluate({"status": "APPROVED"})
        self.assertEqual(result["status"], "APPROVED")
        self.assertEqual(result["reviewed_sha"], self.sha)
        self.assertTrue(result["approval_confirmation"]["exact_sha_bound"])
        self.assertFalse(result["approval_confirmation"]["merge_allowed"])
        self.assertFalse(result["approval_confirmation"]["auto_merge_allowed"])

    def test_16_rejected_verdict_builds_bounded_correction_package(self):
        result = self.evaluate({"status": "REJECTED", "findings": [self.finding]}, current_attempt=1, max_retries=3)
        self.assertEqual(result["status"], "REJECTED")
        package = result["correction_package"]
        self.assertEqual(package["action"], "QUEUE_CORRECTION")
        self.assertEqual(package["next_attempt"], 2)
        self.assertEqual(package["max_retries"], 3)
        self.assertEqual(package["corrections"][0]["severity"], "P1")

    def test_17_retry_ceiling_blocks_correction_queue(self):
        result = self.evaluate({"status": "REJECTED", "findings": [self.finding]}, current_attempt=3, max_retries=3)
        self.assertEqual(result["correction_package"]["action"], bridge.BLOCKED_RETRY_LIMIT)
        self.assertNotIn("next_attempt", result["correction_package"])

    def test_18_malformed_verdict_fails_closed(self):
        self.assertEqual(self.evaluate({"status": "MAYBE"})["status"], bridge.BLOCKED_INVALID_VERDICT)
        self.assertEqual(self.evaluate({"status": "REJECTED", "findings": []})["status"], bridge.BLOCKED_INVALID_VERDICT)
        self.assertEqual(self.evaluate({"status": "APPROVED", "findings": [self.finding]})["status"], bridge.BLOCKED_INVALID_VERDICT)

    def test_19_secret_like_finding_payload_is_redacted_before_persistence(self):
        token = "github_pat_" + "Z" * 40
        finding = dict(self.finding, exact_problem="failed with " + token)
        result = self.evaluate({"status": "REJECTED", "findings": [finding]})
        encoded = json.dumps(result)
        self.assertNotIn(token, encoded)
        self.assertIn("[REDACTED-POTENTIAL-SECRET]", encoded)

    def test_20_recursive_secret_keys_are_redacted(self):
        token = "synthetic-refresh-value"
        clean = bridge.sanitize_object({"nested": {"refresh_token": token}, "normal": "diagnostic"})
        self.assertNotIn(token, json.dumps(clean))
        self.assertEqual(clean["normal"], "diagnostic")

    def test_21_private_key_and_block_scalar_text_are_redacted(self):
        payload = "api_key: |\nVALUE_SHOULD_NOT_SURVIVE\n-----BEGIN PRIVATE KEY-----\nPRIVATEPAYLOAD\n-----END PRIVATE KEY-----"
        clean = bridge.sanitize_text(payload)
        self.assertNotIn("VALUE_SHOULD_NOT_SURVIVE", clean)
        self.assertNotIn("PRIVATEPAYLOAD", clean)

    def test_22_idempotency_key_is_stable_and_status_sensitive(self):
        a = bridge.compute_idempotency_key("TASK-0003", self.sha, "APPROVED", 0)
        b = bridge.compute_idempotency_key("TASK-0003", self.sha, "APPROVED", 0)
        c = bridge.compute_idempotency_key("TASK-0003", self.sha, "REJECTED", 0)
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_23_duplicate_delivery_is_noop(self):
        result = self.evaluate({"status": "APPROVED"})
        key = result["idempotency_key"]
        duplicate = bridge.deduplicate_result(result, [key])
        self.assertEqual(duplicate["action"], "DUPLICATE_NOOP")
        self.assertNotIn("record", duplicate)

    def test_24_first_delivery_persists_sanitized_record_once(self):
        result = self.evaluate({"status": "APPROVED"})
        decision = bridge.deduplicate_result(result, [])
        self.assertEqual(decision["action"], "PERSIST_ONCE")
        self.assertEqual(decision["record"]["reviewed_sha"], self.sha)

    def test_25_self_check_is_disabled_zero_spend_cloud_only(self):
        state = bridge.self_check()
        self.assertEqual(state["mode"], "OFFLINE_SCAFFOLD_DISABLED")
        self.assertFalse(state["live_authorization_enabled"])
        self.assertFalse(state["paid_api_allowed"])
        self.assertFalse(state["local_inference"])
        self.assertEqual(state["required_model"], "gpt-6-astra")


if __name__ == "__main__":
    unittest.main()
