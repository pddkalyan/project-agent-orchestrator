#!/usr/bin/env python3
"""Deterministic offline tests for TASK-0002 autonomous controller helper."""

import importlib.util
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
        self.assertTrue(ac.validate_path("README.md", self.allowed))

    def test_02_disallowed_path_rejection(self):
        self.assertFalse(ac.validate_path("unauthorized.txt", self.allowed))

    def test_03_absolute_path_rejection(self):
        self.assertFalse(ac.validate_path("/etc/passwd", self.allowed))
        self.assertFalse(ac.validate_path("C:\\Windows\\system.ini", self.allowed))

    def test_04_traversal_and_non_normalized_rejection(self):
        for value in ("../README.md", "docs/../../README.md", "docs//sample.md", "./README.md", "docs\\sample.md"):
            self.assertFalse(ac.validate_path(value, self.allowed), value)

    def test_05_relative_symlink_rejection(self):
        b = self.bundle()
        outside = self.temp / "outside.py"
        outside.write_text("secret", encoding="utf-8")
        link = b / "scripts" / "sample.py"
        link.parent.mkdir(parents=True)
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest("symlinks unavailable on this filesystem")
        (b / "changed-files.txt").write_text("scripts/sample.py\n", encoding="utf-8")
        result = ac.validate_bundle(str(b), self.allowed)
        self.assertFalse(result["valid"])
        self.assertIn("Symlinks are not allowed", result["error"])

    def test_06_nested_symlink_directory_rejection(self):
        b = self.bundle()
        outside = self.temp / "outside-dir"
        outside.mkdir()
        (outside / "sample.md").write_text("x", encoding="utf-8")
        link = b / "docs"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("symlinks unavailable on this filesystem")
        (b / "changed-files.txt").write_text("docs/sample.md\n", encoding="utf-8")
        result = ac.validate_bundle(str(b), self.allowed)
        self.assertFalse(result["valid"])
        self.assertIn("Symlinks are not allowed", result["error"])

    def test_07_missing_manifest_rejection(self):
        result = ac.validate_bundle(str(self.bundle()), self.allowed)
        self.assertFalse(result["valid"])
        self.assertIn("Missing changed-files.txt", result["error"])

    def test_08_duplicate_manifest_entries_rejection(self):
        b = self.bundle()
        (b / "README.md").write_text("x", encoding="utf-8")
        (b / "changed-files.txt").write_text("README.md\nREADME.md\n", encoding="utf-8")
        result = ac.validate_bundle(str(b), self.allowed)
        self.assertFalse(result["valid"])
        self.assertIn("Duplicate", result["error"])

    def test_09_extra_bundle_file_rejection(self):
        b = self.bundle()
        (b / "README.md").write_text("x", encoding="utf-8")
        (b / "extra.txt").write_text("x", encoding="utf-8")
        (b / "changed-files.txt").write_text("README.md\n", encoding="utf-8")
        result = ac.validate_bundle(str(b), self.allowed)
        self.assertFalse(result["valid"])
        self.assertIn("Extra bundle file", result["error"])

    def test_10_manifest_entry_missing_from_bundle(self):
        b = self.bundle()
        (b / "changed-files.txt").write_text("README.md\n", encoding="utf-8")
        result = ac.validate_bundle(str(b), self.allowed)
        self.assertFalse(result["valid"])
        self.assertIn("Manifest entry missing", result["error"])

    def test_11_exact_manifest_and_metadata_pass(self):
        b = self.bundle()
        (b / "README.md").write_text("x", encoding="utf-8")
        (b / "changed-files.txt").write_text("README.md\n", encoding="utf-8")
        (b / "worker_result_TASK-0002.json").write_text("{}", encoding="utf-8")
        (b / "worker_patch_TASK-0002.patch").write_text("", encoding="utf-8")
        result = ac.validate_bundle(str(b), self.allowed)
        self.assertEqual(result, {"valid": True, "files": ["README.md"]})

    def test_12_retry_under_limit(self):
        result = ac.check_retry_decision({"retry_attempt": 1, "max_controller_retries": 3})
        self.assertEqual(result["action"], "RETRY")
        self.assertEqual(result["next_attempt"], 2)

    def test_13_retry_at_limit(self):
        result = ac.check_retry_decision({"retry_attempt": 3, "max_controller_retries": 3})
        self.assertEqual(result["action"], "BLOCKED")

    def test_14_invalid_retry_values_fail_closed(self):
        self.assertEqual(ac.check_retry_decision({"retry_attempt": -1, "max_controller_retries": 3})["action"], "ERROR")
        self.assertEqual(ac.check_retry_decision({"retry_attempt": 0, "max_controller_retries": "3"})["action"], "ERROR")

    def test_15_idempotency_key_is_deterministic_and_scoped(self):
        a = ac.compute_idempotency_key("TASK-0002", 123, "retry")
        b = ac.compute_idempotency_key("TASK-0002", 123, "retry")
        c = ac.compute_idempotency_key("TASK-0002", 124, "retry")
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_16_secret_sanitization(self):
        raw = "ok\nGEMINI_API_KEY=AIzaSyFakeFakeFakeFakeFakeFake123456\nAuthorization: Bearer ghp_abcdefghijklmnopqrstuvwxyz1234567890\npassword=abc\nend"
        sanitized = ac.sanitize_text(raw)
        self.assertIn("ok", sanitized)
        self.assertIn("end", sanitized)
        self.assertNotIn("AIza", sanitized)
        self.assertNotIn("ghp_", sanitized)
        self.assertNotIn("password=abc", sanitized)
        self.assertGreaterEqual(sanitized.count("[REDACTED-POTENTIAL-SECRET]"), 3)

    def test_17_sanitized_excerpt_is_bounded(self):
        sanitized = ac.sanitize_text("\n".join(f"line-{i}" for i in range(200)), max_lines=5, max_chars=1000)
        self.assertEqual(len(sanitized.splitlines()), 5)

    def test_18_failed_job_summary_is_structured(self):
        payload = {"jobs": [{"name": "worker", "conclusion": "failure", "steps": [{"name": "Run tests", "number": 8, "conclusion": "failure"}]}]}
        result = ac.summarize_jobs(payload)
        self.assertEqual(result["failed_jobs"][0]["steps"][0]["name"], "Run tests")


if __name__ == "__main__":
    unittest.main()
