#!/usr/bin/env python3
"""Deterministic offline tests for TASK-0002 autonomous controller helper."""

import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "autonomous_controller__created_by_worker_gemini_cli__model_gemini-3.5-flash-lite__task_TASK-0002.py"
spec = importlib.util.spec_from_file_location("task0002_controller", SCRIPT)
ac = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(ac)


class TestAutonomousController(unittest.TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp())
        self.allowed = ["README.md", "docs/sample.md", "scripts/sample.py"]

    def tearDown(self):
        shutil.rmtree(self.temp, ignore_errors=True)

    def bundle(self):
        b = self.temp / "bundle"
        b.mkdir()
        return b

    def test_01_allowed_path_acceptance(self):
        self.assertTrue(ac.validate_path("docs/sample.md", self.allowed))

    def test_02_disallowed_path_rejection(self):
        self.assertFalse(ac.validate_path("unauthorized.txt", self.allowed))

    def test_03_absolute_path_rejection(self):
        self.assertFalse(ac.validate_path("/etc/passwd", self.allowed))
        self.assertFalse(ac.validate_path("C:\\Windows\\system.ini", self.allowed))

    def test_04_traversal_and_non_normalized_rejection(self):
        for value in ("../README.md", "docs/../../README.md", "docs//sample.md", "./README.md", "docs\\sample.md"):
            self.assertFalse(ac.validate_path(value, self.allowed), value)

    def test_05_bundle_symlink_file_rejection(self):
        b = self.bundle()
        outside = self.temp / "outside.py"
        outside.write_text("sentinel", encoding="utf-8")
        link = b / "scripts" / "sample.py"
        link.parent.mkdir(parents=True)
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest("symlinks unavailable")
        (b / "changed-files.txt").write_text("scripts/sample.py\n", encoding="utf-8")
        result = ac.validate_bundle(str(b), self.allowed)
        self.assertFalse(result["valid"])
        self.assertIn("Symlinks", result["error"])

    def test_06_bundle_symlink_parent_rejection(self):
        b = self.bundle()
        outside = self.temp / "outside-dir"
        outside.mkdir()
        (outside / "sample.md").write_text("sentinel", encoding="utf-8")
        try:
            (b / "docs").symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("symlinks unavailable")
        (b / "changed-files.txt").write_text("docs/sample.md\n", encoding="utf-8")
        result = ac.validate_bundle(str(b), self.allowed)
        self.assertFalse(result["valid"])

    def test_07_missing_manifest_rejection(self):
        result = ac.validate_bundle(str(self.bundle()), self.allowed)
        self.assertFalse(result["valid"])

    def test_08_duplicate_manifest_entries_rejection(self):
        b = self.bundle()
        (b / "README.md").write_text("x", encoding="utf-8")
        (b / "changed-files.txt").write_text("README.md\nREADME.md\n", encoding="utf-8")
        self.assertFalse(ac.validate_bundle(str(b), self.allowed)["valid"])

    def test_09_extra_bundle_file_rejection(self):
        b = self.bundle()
        (b / "README.md").write_text("x", encoding="utf-8")
        (b / "extra.txt").write_text("x", encoding="utf-8")
        (b / "changed-files.txt").write_text("README.md\n", encoding="utf-8")
        self.assertFalse(ac.validate_bundle(str(b), self.allowed)["valid"])

    def test_10_exact_manifest_and_metadata_pass(self):
        b = self.bundle()
        (b / "README.md").write_text("x", encoding="utf-8")
        (b / "changed-files.txt").write_text("README.md\n", encoding="utf-8")
        (b / "worker_result_TASK-0002.json").write_text("{}", encoding="utf-8")
        (b / "worker_patch_TASK-0002.patch").write_text("", encoding="utf-8")
        self.assertEqual(ac.validate_bundle(str(b), self.allowed), {"valid": True, "files": ["README.md"]})

    def test_11_retry_under_limit(self):
        result = ac.check_retry_decision({"retry_attempt": 1, "max_controller_retries": 3})
        self.assertEqual(result["action"], "RETRY")
        self.assertEqual(result["next_attempt"], 2)

    def test_12_retry_at_limit(self):
        self.assertEqual(ac.check_retry_decision({"retry_attempt": 3, "max_controller_retries": 3})["action"], "BLOCKED")

    def test_13_invalid_retry_values_fail_closed(self):
        self.assertEqual(ac.check_retry_decision({"retry_attempt": -1, "max_controller_retries": 3})["action"], "ERROR")
        self.assertEqual(ac.check_retry_decision({"retry_attempt": 0, "max_controller_retries": "3"})["action"], "ERROR")

    def test_14_idempotency_key_is_deterministic(self):
        self.assertEqual(
            ac.compute_idempotency_key("TASK-0002", 123, "retry"),
            ac.compute_idempotency_key("TASK-0002", 123, "retry"),
        )

    def test_15_github_fine_grained_token_is_redacted(self):
        token = "github_pat_" + "A" * 40
        sanitized = ac.sanitize_text("diagnostic\nvalue=" + token + "\nend")
        self.assertNotIn(token, sanitized)
        self.assertIn("diagnostic", sanitized)
        self.assertIn("end", sanitized)

    def test_16_multiline_credential_value_is_redacted(self):
        secret = "SYNTHETIC_VALUE_SHOULD_NOT_SURVIVE"
        sanitized = ac.sanitize_text("before\napi_key:\n" + secret + "\nafter")
        self.assertNotIn(secret, sanitized)
        self.assertIn("before", sanitized)
        self.assertIn("after", sanitized)

    def test_17_private_key_block_is_redacted(self):
        payload = "before\n-----BEGIN PRIVATE KEY-----\nABCDEF0123456789\n-----END PRIVATE KEY-----\nafter"
        sanitized = ac.sanitize_text(payload)
        self.assertNotIn("ABCDEF0123456789", sanitized)
        self.assertNotIn("BEGIN PRIVATE KEY", sanitized)
        self.assertIn("after", sanitized)

    def test_18_sanitized_excerpt_is_bounded(self):
        sanitized = ac.sanitize_text("\n".join(f"line-{i}" for i in range(200)), max_lines=5, max_chars=1000)
        self.assertEqual(len(sanitized.splitlines()), 5)

    def test_19_job_and_step_metadata_are_sanitized(self):
        token = "github_pat_" + "B" * 40
        payload = {"jobs": [{"name": "worker " + token, "conclusion": "failure", "steps": [{"name": "api_key:\nSECRET_VALUE", "number": 8, "conclusion": "failure"}]}]}
        text = json.dumps(ac.summarize_jobs(payload))
        self.assertNotIn(token, text)
        self.assertNotIn("SECRET_VALUE", text)

    def test_20_staging_rejects_allowed_file_symlink_before_copy(self):
        root = self.temp / "repo"
        root.mkdir()
        outside = self.temp / "external.txt"
        outside.write_text("EXTERNAL_SENTINEL", encoding="utf-8")
        link = root / "README.md"
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest("symlinks unavailable")
        (root / "changed-files.txt").write_text("README.md\n", encoding="utf-8")
        (root / "worker_result_TASK-0002.json").write_text("{}", encoding="utf-8")
        result = ac.validate_staging_set(str(root), "changed-files.txt", ["worker_result_TASK-0002.json"])
        self.assertFalse(result["valid"])

    def test_21_staging_rejects_linked_parent_directory(self):
        root = self.temp / "repo"
        root.mkdir()
        outside = self.temp / "outside"
        outside.mkdir()
        (outside / "sample.md").write_text("EXTERNAL_SENTINEL", encoding="utf-8")
        try:
            (root / "docs").symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("symlinks unavailable")
        (root / "changed-files.txt").write_text("docs/sample.md\n", encoding="utf-8")
        (root / "worker_result_TASK-0002.json").write_text("{}", encoding="utf-8")
        self.assertFalse(ac.validate_staging_set(str(root), "changed-files.txt", ["worker_result_TASK-0002.json"])["valid"])

    def test_22_staging_rejects_metadata_symlink(self):
        root = self.temp / "repo"
        root.mkdir()
        (root / "README.md").write_text("ok", encoding="utf-8")
        (root / "changed-files.txt").write_text("README.md\n", encoding="utf-8")
        outside = self.temp / "metadata.json"
        outside.write_text("EXTERNAL_SENTINEL", encoding="utf-8")
        try:
            (root / "worker_result_TASK-0002.json").symlink_to(outside)
        except OSError:
            self.skipTest("symlinks unavailable")
        self.assertFalse(ac.validate_staging_set(str(root), "changed-files.txt", ["worker_result_TASK-0002.json"])["valid"])

    def test_23_staging_regular_files_pass(self):
        root = self.temp / "repo"
        root.mkdir()
        (root / "README.md").write_text("ok", encoding="utf-8")
        (root / "changed-files.txt").write_text("README.md\n", encoding="utf-8")
        (root / "worker_result_TASK-0002.json").write_text("{}", encoding="utf-8")
        self.assertTrue(ac.validate_staging_set(str(root), "changed-files.txt", ["worker_result_TASK-0002.json"])["valid"])

    def test_24_promotion_destination_rejects_symlink_parent(self):
        root = self.temp / "repo"
        root.mkdir()
        outside = self.temp / "outside"
        outside.mkdir()
        try:
            (root / "docs").symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("symlinks unavailable")
        self.assertFalse(ac.validate_promotion_destination(str(root), "docs/sample.md")["valid"])

    def test_25_dispatch_key_binds_task_sha_attempt_and_source_run(self):
        sha = "a" * 40
        k1 = ac.compute_dispatch_key("TASK-0002", sha, 2, 77)
        self.assertEqual(k1, ac.compute_dispatch_key("TASK-0002", sha, 2, 77))
        self.assertNotEqual(k1, ac.compute_dispatch_key("TASK-0002", "b" * 40, 2, 77))
        self.assertNotEqual(k1, ac.compute_dispatch_key("TASK-0002", sha, 3, 77))

    def test_26_task_binding_rejects_wrong_task_after_intervening_commit(self):
        sha = "a" * 40
        task = {"task_id": "TASK-0002", "retry_attempt": 2}
        key = ac.compute_dispatch_key("TASK-0002", sha, 2, 77)
        self.assertTrue(ac.validate_task_binding(task, "tasks/queue/task2.json", sha, "TASK-0002", 2, key, 77)["valid"])
        self.assertFalse(ac.validate_task_binding(task, "tasks/queue/task2.json", sha, "TASK-0003", 2, key, 77)["valid"])

    def test_27_task_binding_rejects_stale_attempt_or_key(self):
        sha = "c" * 40
        task = {"task_id": "TASK-0002", "retry_attempt": 2}
        good = ac.compute_dispatch_key("TASK-0002", sha, 2, 88)
        self.assertFalse(ac.validate_task_binding(task, "tasks/queue/task2.json", sha, "TASK-0002", 1, good, 88)["valid"])
        self.assertFalse(ac.validate_task_binding(task, "tasks/queue/task2.json", sha, "TASK-0002", 2, "0" * 32, 88)["valid"])

    def test_28_pending_retry_record_can_resume_without_increment(self):
        task = {
            "task_id": "TASK-0002",
            "retry_attempt": 2,
            "controller_retry_state": {
                "source_worker_run_id": "12345",
                "attempt": 2,
                "dispatch_status": "PENDING_DISPATCH",
            },
        }
        self.assertTrue(ac.validate_retry_record(task, "12345", 2)["valid"])
        self.assertEqual(task["retry_attempt"], 2)

    def test_29_retry_record_rejects_unrelated_source(self):
        task = {
            "retry_attempt": 2,
            "controller_retry_state": {
                "source_worker_run_id": "12345",
                "attempt": 2,
                "dispatch_status": "PENDING_DISPATCH",
            },
        }
        self.assertFalse(ac.validate_retry_record(task, "99999", 2)["valid"])

    def test_30_structured_diagnostics_do_not_include_raw_log_field(self):
        result = ac.summarize_jobs({"jobs": [{"name": "worker", "conclusion": "failure", "steps": []}]})
        self.assertNotIn("log", json.dumps(result).lower())


if __name__ == "__main__":
    unittest.main()
