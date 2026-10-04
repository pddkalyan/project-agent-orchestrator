#!/usr/bin/env python3
"""Adversarial deterministic offline tests for TASK-0003 reviewer bridge."""

import importlib.util
import json
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "chatgpt_plan_reviewer_bridge__created_by_worker_gemini_cli__model_gemini-3.5-flash-lite__task_TASK-0003.py"
SCHEMA = ROOT / "reviewer" / "REVIEW_CONTRACT.schema.json"

spec = importlib.util.spec_from_file_location("chatgpt_plan_reviewer_bridge", SCRIPT)
bridge = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(bridge)


class TestChatGPTPlanReviewerBridge(unittest.TestCase):
    def setUp(self):
        self.sha = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"
        self.base = "b1b2c3d4e5f6b1b2c3d4e5f6b1b2c3d4e5f6b1b2"
        self.snapshot = {
            "task_id": "TASK-0003",
            "repository": "pddkalyan/project-agent-orchestrator",
            "pr_number": 4,
            "base_sha": self.base,
            "candidate_sha": self.sha,
            "reviewer_context_version": "task0003-v2",
            "changed_files": ["a.py", "reviewer/PROJECT_CONTEXT.md"],
            "required_ci": [
                {
                    "workflow": "ChatGPT Plan Reviewer Regression",
                    "run_id": 123456,
                    "head_sha": self.sha,
                    "result": "PASS",
                    "digest": "sha256:" + "c" * 64,
                },
                {
                    "workflow": "PC Cleanup Audit Regression",
                    "run_id": 123457,
                    "head_sha": self.sha,
                    "result": "PASS",
                    "digest": "sha256:" + "d" * 64,
                },
            ],
        }
        self.request = deepcopy(self.snapshot)
        self.snapshot_digest = bridge.review_identity_digest(self.snapshot)
        self.catalog = {"authorized_models": ["gpt-6-astra", "other-model"], "user_authorized": True}
        self.auth = {"active": True, "expired": False, "profile_id": "local-profile-1"}
        self.allowance = {
            "billing_mode": "ZERO_SPEND_PLAN",
            "separately_billed": False,
            "credits_enabled": False,
            "remaining_requests": 5,
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

    def approval(self, **changes):
        verdict = {
            "status": "APPROVED",
            "reviewed_sha": self.sha,
            "review_snapshot_digest": self.snapshot_digest,
            "findings": [],
        }
        verdict.update(changes)
        return verdict

    def rejection(self, finding=None, **changes):
        verdict = {
            "status": "REJECTED",
            "reviewed_sha": self.sha,
            "review_snapshot_digest": self.snapshot_digest,
            "findings": [deepcopy(finding or self.finding)],
        }
        verdict.update(changes)
        return verdict

    def evaluate(self, verdict=None, **overrides):
        args = dict(
            expected_snapshot=self.snapshot,
            review_request=self.request,
            model_catalog=self.catalog,
            auth_profile=self.auth,
            plan_allowance=self.allowance,
            verdict_payload=verdict if verdict is not None else self.approval(),
            current_attempt=0,
            max_retries=3,
        )
        args.update(overrides)
        return bridge.evaluate_review(**args)

    def assertBlockedEvidence(self, request):
        result = self.evaluate(review_request=request)
        self.assertEqual(result["status"], bridge.BLOCKED_EVIDENCE_MISMATCH)
        self.assertTrue(bridge.validate_emitted_result(result)[0])

    # Model/auth/allowance boundaries.
    def test_01_exact_astra_allowed(self):
        self.assertEqual(bridge.validate_model_catalog(self.catalog), (True, "MODEL_ALLOWED"))

    def test_02_missing_exact_astra_blocks(self):
        result = self.evaluate(model_catalog={"authorized_models": ["gpt-6-astra-preview"], "user_authorized": True})
        self.assertEqual(result["status"], bridge.BLOCKED_NO_ASTRA)

    def test_03_missing_auth_blocks(self):
        self.assertEqual(self.evaluate(auth_profile=None)["status"], bridge.BLOCKED_AUTH_REQUIRED)

    def test_04_expired_auth_blocks(self):
        auth = {"active": True, "expired": True, "profile_id": "local"}
        self.assertEqual(self.evaluate(auth_profile=auth)["status"], bridge.BLOCKED_AUTH_REQUIRED)

    def test_05_auth_extra_top_level_secret_field_blocks(self):
        auth = dict(self.auth, refresh_token="synthetic")
        self.assertEqual(self.evaluate(auth_profile=auth)["status"], bridge.BLOCKED_AUTH_REQUIRED)

    def test_06_auth_nested_material_blocks_by_allowlist(self):
        auth = dict(self.auth)
        auth["metadata"] = {"refresh_token": "NESTED_SECRET_SENTINEL"}
        self.assertEqual(self.evaluate(auth_profile=auth)["status"], bridge.BLOCKED_AUTH_REQUIRED)

    def test_07_paid_billing_blocks(self):
        allowance = dict(self.allowance, billing_mode="PAID_API_KEY")
        self.assertEqual(self.evaluate(plan_allowance=allowance)["status"], bridge.BLOCKED_PLAN_ALLOWANCE)

    def test_08_credit_fallback_blocks(self):
        allowance = dict(self.allowance, credits_enabled=True)
        self.assertEqual(self.evaluate(plan_allowance=allowance)["status"], bridge.BLOCKED_PLAN_ALLOWANCE)

    def test_09_separate_billing_blocks(self):
        allowance = dict(self.allowance, separately_billed=True)
        self.assertEqual(self.evaluate(plan_allowance=allowance)["status"], bridge.BLOCKED_PLAN_ALLOWANCE)

    def test_10_exhausted_allowance_blocks(self):
        allowance = dict(self.allowance, remaining_requests=0)
        self.assertEqual(self.evaluate(plan_allowance=allowance)["status"], bridge.BLOCKED_PLAN_ALLOWANCE)

    # Immutable expected snapshot binding: every field independently.
    def test_11_repository_substitution_blocks(self):
        req = deepcopy(self.request); req["repository"] = "other/repo"
        self.assertBlockedEvidence(req)

    def test_12_pr_substitution_blocks(self):
        req = deepcopy(self.request); req["pr_number"] = 99
        self.assertBlockedEvidence(req)

    def test_13_base_sha_substitution_blocks(self):
        req = deepcopy(self.request); req["base_sha"] = "0" * 40
        self.assertBlockedEvidence(req)

    def test_14_candidate_sha_substitution_blocks(self):
        req = deepcopy(self.request); req["candidate_sha"] = "1" * 40
        req["required_ci"] = [dict(x, head_sha="1" * 40) for x in req["required_ci"]]
        self.assertBlockedEvidence(req)

    def test_15_context_version_substitution_blocks(self):
        req = deepcopy(self.request); req["reviewer_context_version"] = "stale-v1"
        self.assertBlockedEvidence(req)

    def test_16_missing_changed_file_blocks(self):
        req = deepcopy(self.request); req["changed_files"] = req["changed_files"][:-1]
        self.assertBlockedEvidence(req)

    def test_17_extra_changed_file_blocks(self):
        req = deepcopy(self.request); req["changed_files"].append("extra.txt")
        self.assertBlockedEvidence(req)

    def test_18_changed_file_order_is_canonical_not_identity_drift(self):
        req = deepcopy(self.request); req["changed_files"] = list(reversed(req["changed_files"]))
        self.assertEqual(bridge.validate_review_request(self.snapshot, req), (True, "SNAPSHOT_BOUND"))

    def test_19_missing_ci_entry_blocks(self):
        req = deepcopy(self.request); req["required_ci"] = req["required_ci"][:-1]
        self.assertBlockedEvidence(req)

    def test_20_extra_ci_entry_blocks(self):
        req = deepcopy(self.request)
        req["required_ci"].append({
            "workflow": "Extra Regression",
            "run_id": 123458,
            "head_sha": self.sha,
            "result": "PASS",
            "digest": "sha256:" + "e" * 64,
        })
        self.assertBlockedEvidence(req)

    def test_21_substituted_ci_run_id_blocks(self):
        req = deepcopy(self.request); req["required_ci"][0]["run_id"] = 888888
        self.assertBlockedEvidence(req)

    def test_22_substituted_ci_digest_blocks(self):
        req = deepcopy(self.request); req["required_ci"][0]["digest"] = "sha256:" + "f" * 64
        self.assertBlockedEvidence(req)

    def test_23_substituted_ci_workflow_blocks(self):
        req = deepcopy(self.request); req["required_ci"][0]["workflow"] = "Different Workflow"
        self.assertBlockedEvidence(req)

    def test_24_ci_order_is_canonical(self):
        req = deepcopy(self.request); req["required_ci"] = list(reversed(req["required_ci"]))
        self.assertEqual(bridge.validate_review_request(self.snapshot, req), (True, "SNAPSHOT_BOUND"))

    def test_25_exact_snapshot_and_request_pass(self):
        self.assertEqual(bridge.validate_review_request(self.snapshot, self.request), (True, "SNAPSHOT_BOUND"))

    # Verdict identity binding.
    def test_26_stale_verdict_sha_blocks(self):
        result = self.evaluate(self.approval(reviewed_sha="0" * 40))
        self.assertEqual(result["status"], bridge.BLOCKED_INVALID_VERDICT)

    def test_27_stale_snapshot_digest_blocks(self):
        result = self.evaluate(self.approval(review_snapshot_digest="f" * 64))
        self.assertEqual(result["status"], bridge.BLOCKED_INVALID_VERDICT)

    def test_28_exact_approval_passes_and_is_schema_valid(self):
        result = self.evaluate(self.approval())
        self.assertEqual(result["status"], "APPROVED")
        self.assertTrue(bridge.validate_emitted_result(result)[0])
        self.assertTrue(result["approval_confirmation"]["exact_snapshot_bound"])
        self.assertFalse(result["approval_confirmation"]["merge_allowed"])

    # Sanitization.
    def test_29_block_scalar_redacts_all_indented_secret_lines(self):
        text = "before\napi_key: |\n  SECRET_LINE_ONE\n  SECRET_LINE_TWO\nafter"
        clean = bridge.sanitize_text(text)
        self.assertNotIn("SECRET_LINE_ONE", clean)
        self.assertNotIn("SECRET_LINE_TWO", clean)
        self.assertIn("before", clean)
        self.assertIn("after", clean)

    def test_30_label_only_redacts_unlabelled_continuations_until_blank(self):
        text = "password:\nSECRET_ONE\nSECRET_TWO\n\nordinary diagnostic"
        clean = bridge.sanitize_text(text)
        self.assertNotIn("SECRET_ONE", clean)
        self.assertNotIn("SECRET_TWO", clean)
        self.assertIn("ordinary diagnostic", clean)

    def test_31_token_bearing_dictionary_key_is_not_retained(self):
        token_key = "github_pat_" + "Z" * 40
        clean = bridge.sanitize_object({token_key: "value", "ordinary": "diagnostic"})
        encoded = json.dumps(clean)
        self.assertNotIn(token_key, encoded)
        self.assertNotIn("value", encoded)
        self.assertIn("ordinary", encoded)

    def test_32_nested_refresh_token_is_removed_by_sanitizer(self):
        clean = bridge.sanitize_object({"nested": {"refresh_token": "NESTED_SENTINEL"}, "ordinary": "ok"})
        encoded = json.dumps(clean)
        self.assertNotIn("refresh_token", encoded)
        self.assertNotIn("NESTED_SENTINEL", encoded)
        self.assertIn("ordinary", encoded)

    def test_33_private_key_and_fine_grained_pat_leave_no_payload(self):
        pat = "github_pat_" + "P" * 40
        text = f"diagnostic\n{pat}\n-----BEGIN PRIVATE KEY-----\nPRIVATE_SENTINEL\n-----END PRIVATE KEY-----\ndone"
        clean = bridge.sanitize_text(text)
        self.assertNotIn(pat, clean)
        self.assertNotIn("PRIVATE_SENTINEL", clean)
        self.assertIn("diagnostic", clean)
        self.assertIn("done", clean)

    def test_34_rejected_secret_finding_has_no_sentinel_in_result_or_correction(self):
        finding = deepcopy(self.finding)
        finding["exact_problem"] = "api_key: |\n  FIRST_SENTINEL\n  SECOND_SENTINEL\nordinary"
        result = self.evaluate(self.rejection(finding))
        encoded = json.dumps(result)
        self.assertNotIn("FIRST_SENTINEL", encoded)
        self.assertNotIn("SECOND_SENTINEL", encoded)

    # Strict finding validation.
    def test_35_null_required_finding_field_blocks(self):
        finding = deepcopy(self.finding); finding["exact_location"] = None
        self.assertEqual(self.evaluate(self.rejection(finding))["status"], bridge.BLOCKED_INVALID_VERDICT)

    def test_36_object_required_finding_field_blocks(self):
        finding = deepcopy(self.finding); finding["exact_problem"] = {}
        self.assertEqual(self.evaluate(self.rejection(finding))["status"], bridge.BLOCKED_INVALID_VERDICT)

    def test_37_numeric_required_finding_field_blocks(self):
        finding = deepcopy(self.finding); finding["required_correction"] = 123
        self.assertEqual(self.evaluate(self.rejection(finding))["status"], bridge.BLOCKED_INVALID_VERDICT)

    def test_38_blank_required_finding_field_blocks(self):
        finding = deepcopy(self.finding); finding["why_it_matters"] = "   "
        self.assertEqual(self.evaluate(self.rejection(finding))["status"], bridge.BLOCKED_INVALID_VERDICT)

    def test_39_bad_acceptance_criteria_blocks(self):
        finding = deepcopy(self.finding); finding["acceptance_criteria"] = ["ok", " "]
        self.assertEqual(self.evaluate(self.rejection(finding))["status"], bridge.BLOCKED_INVALID_VERDICT)

    def test_40_blank_optional_guidance_blocks_when_present(self):
        finding = deepcopy(self.finding); finding["implementation_guidance"] = ""
        self.assertEqual(self.evaluate(self.rejection(finding))["status"], bridge.BLOCKED_INVALID_VERDICT)

    def test_41_malformed_approval_findings_blocks(self):
        self.assertEqual(self.evaluate(self.approval(findings=[self.finding]))["status"], bridge.BLOCKED_INVALID_VERDICT)

    def test_42_valid_rejection_builds_bounded_package_and_is_schema_valid(self):
        result = self.evaluate(self.rejection(), current_attempt=1, max_retries=3)
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["correction_package"]["action"], "QUEUE_CORRECTION")
        self.assertEqual(result["correction_package"]["next_attempt"], 2)
        self.assertTrue(bridge.validate_emitted_result(result)[0])

    def test_43_retry_ceiling_blocks_correction_queue(self):
        result = self.evaluate(self.rejection(), current_attempt=3, max_retries=3)
        self.assertEqual(result["correction_package"]["action"], bridge.BLOCKED_RETRY_LIMIT)

    def test_44_all_supported_blocked_outputs_are_contract_valid(self):
        cases = [
            self.evaluate(auth_profile=None),
            self.evaluate(model_catalog={"authorized_models": [], "user_authorized": True}),
            self.evaluate(plan_allowance=dict(self.allowance, remaining_requests=0)),
            self.evaluate(review_request=dict(self.request, repository="wrong/repo")),
            self.evaluate(self.approval(reviewed_sha="0" * 40)),
        ]
        for result in cases:
            self.assertTrue(bridge.validate_emitted_result(result)[0], result)

    # Idempotency and conflict policy.
    def test_45_identical_delivery_is_duplicate_noop(self):
        result = self.evaluate(self.approval())
        decision = bridge.deduplicate_result(result, [result])
        self.assertEqual(decision["action"], "DUPLICATE_NOOP")

    def test_46_distinct_repository_identity_does_not_collide(self):
        other_snapshot = deepcopy(self.snapshot)
        other_snapshot["repository"] = "other/project-agent-orchestrator"
        other_request = deepcopy(other_snapshot)
        digest = bridge.review_identity_digest(other_snapshot)
        other_verdict = {
            "status": "APPROVED",
            "reviewed_sha": self.sha,
            "review_snapshot_digest": digest,
            "findings": [],
        }
        first = self.evaluate(self.approval())
        second = self.evaluate(
            other_verdict,
            expected_snapshot=other_snapshot,
            review_request=other_request,
        )
        self.assertNotEqual(first["review_identity_digest"], second["review_identity_digest"])
        self.assertNotEqual(first["idempotency_key"], second["idempotency_key"])
        self.assertEqual(bridge.deduplicate_result(second, [first])["action"], "PERSIST_ONCE")

    def test_47_distinct_pr_identity_does_not_collide(self):
        other_snapshot = deepcopy(self.snapshot); other_snapshot["pr_number"] = 99
        other_request = deepcopy(other_snapshot)
        digest = bridge.review_identity_digest(other_snapshot)
        second = self.evaluate(
            {"status": "APPROVED", "reviewed_sha": self.sha, "review_snapshot_digest": digest, "findings": []},
            expected_snapshot=other_snapshot,
            review_request=other_request,
        )
        first = self.evaluate(self.approval())
        self.assertNotEqual(first["idempotency_key"], second["idempotency_key"])

    def test_48_conflicting_result_same_identity_is_explicitly_blocked(self):
        approved = self.evaluate(self.approval())
        rejected = self.evaluate(self.rejection())
        decision = bridge.deduplicate_result(rejected, [approved])
        self.assertEqual(decision["action"], "CONFLICT_BLOCKED")
        self.assertNotEqual(decision["existing_result_digest"], decision["incoming_result_digest"])

    def test_49_different_findings_same_identity_are_conflict_not_duplicate(self):
        first = self.evaluate(self.rejection())
        second_finding = deepcopy(self.finding); second_finding["exact_problem"] = "Different valid problem"
        second = self.evaluate(self.rejection(second_finding))
        self.assertNotEqual(first["result_digest"], second["result_digest"])
        self.assertEqual(bridge.deduplicate_result(second, [first])["action"], "CONFLICT_BLOCKED")

    def test_50_schema_declares_status_specific_contract(self):
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        self.assertFalse(schema["additionalProperties"])
        self.assertIn("reason", schema["properties"])
        self.assertIn("review_identity_digest", schema["properties"])
        self.assertIn("result_digest", schema["properties"])
        self.assertIn("idempotency_key", schema["properties"])
        self.assertGreaterEqual(len(schema["oneOf"]), 3)

    def test_51_self_check_preserves_offline_zero_spend_boundaries(self):
        state = bridge.self_check()
        self.assertEqual(state["mode"], "OFFLINE_SCAFFOLD_DISABLED")
        self.assertFalse(state["live_authorization_enabled"])
        self.assertFalse(state["paid_api_allowed"])
        self.assertFalse(state["local_inference"])
        self.assertFalse(state["conversation_access"])
        self.assertFalse(state["merge_allowed"])
        self.assertEqual(state["required_model"], "gpt-6-astra")


if __name__ == "__main__":
    unittest.main()
