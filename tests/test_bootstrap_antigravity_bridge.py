"""
Unit tests for Windows Bridge Bootstrap Repair Installer (bootstrap_antigravity_bridge.py).
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from control_center.remote_bridge.bootstrap_antigravity_bridge import (
    AUTHORIZED_USER_ID,
    EXPECTED_BRANCH,
    REPO_NAME,
    BackupManager,
    execute_bootstrap,
    find_agentapi_executable,
    retrieve_bridge_source,
    run_subprocess_with_retry,
    sanitize_text,
    validate_workspace,
    verify_gh_auth,
)


class TestBootstrapAntigravityBridge(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.tmp_dir, ignore_errors=True))

    def test_sanitize_text_redacts_secrets(self):
        text = "Error with token ghp_1234567890abcdef1234567890abcdef and PAT github_pat_11AAAAAAA0123456789_abcdef"
        sanitized = sanitize_text(text)
        self.assertNotIn("ghp_1234567890abcdef1234567890abcdef", sanitized)
        self.assertNotIn("github_pat_11AAAAAAA0123456789_abcdef", sanitized)
        self.assertIn("[REDACTED_SECRET]", sanitized)

    def test_find_agentapi_executable_paths(self):
        custom_exec = os.path.join(self.tmp_dir, "custom_agentapi.bat")
        Path(custom_exec).write_text("@echo off", encoding="utf-8")
        self.assertEqual(find_agentapi_executable(custom_exec), custom_exec)

        fake_home = Path(self.tmp_dir) / "home"
        cli_bat = fake_home / ".gemini" / "antigravity-cli" / "bin" / "agentapi.bat"
        cli_bat.parent.mkdir(parents=True, exist_ok=True)
        cli_bat.write_text("@echo off", encoding="utf-8")

        with patch("pathlib.Path.home", return_value=fake_home):
            found = find_agentapi_executable("")
            self.assertEqual(found, str(cli_bat))

    def test_run_subprocess_with_retry_success(self):
        calls = []

        def mock_runner(cmd, cwd=None):
            calls.append(cmd)
            return 0, "OUTPUT_OK", "", "SUCCESS"

        ret, out, err, category = run_subprocess_with_retry(
            ["echo", "hello"], max_retries=3, runner=mock_runner
        )
        self.assertEqual(ret, 0)
        self.assertEqual(out, "OUTPUT_OK")
        self.assertEqual(category, "SUCCESS")
        self.assertEqual(len(calls), 1)

    def test_verify_gh_auth_success(self):
        def mock_runner(cmd, cwd=None):
            stdout = json.dumps({"id": AUTHORIZED_USER_ID, "login": "pddkalyan"})
            return 0, stdout, "", "SUCCESS"

        ok, status, diag = verify_gh_auth(runner=mock_runner)
        self.assertTrue(ok)
        self.assertEqual(status, "AUTH_SUCCESS")
        self.assertEqual(diag["user_id"], AUTHORIZED_USER_ID)

    def test_verify_gh_auth_missing_cli(self):
        def mock_runner(cmd, cwd=None):
            return 127, "", "gh: command not found", "BINARY_NOT_FOUND"

        ok, status, diag = verify_gh_auth(runner=mock_runner)
        self.assertFalse(ok)
        self.assertEqual(status, "BLOCKED: GH_CLI_MISSING")

    def test_verify_gh_auth_child_failed_and_wrong_user(self):
        # Child subprocess temporary/permanent failure
        def mock_fail_runner(cmd, cwd=None):
            return 1, "", "gh: HTTP 401 Unauthorized", "UNAUTHORIZED"

        ok, status, diag = verify_gh_auth(runner=mock_fail_runner)
        self.assertFalse(ok)
        self.assertEqual(status, "BLOCKED: GH_API_AUTH_FAILED")
        self.assertEqual(diag["category"], "UNAUTHORIZED")

        # Wrong owner user ID
        def mock_wrong_user_runner(cmd, cwd=None):
            stdout = json.dumps({"id": 999999, "login": "wronguser"})
            return 0, stdout, "", "SUCCESS"

        ok, status, diag = verify_gh_auth(runner=mock_wrong_user_runner)
        self.assertFalse(ok)
        self.assertEqual(status, "BLOCKED: GH_API_AUTH_FAILED")
        self.assertEqual(diag["category"], "WRONG_USER")

    def test_retrieve_bridge_source_local_checkout(self):
        ws_dir = Path(self.tmp_dir) / "ws"
        bridge_file = ws_dir / "control_center" / "remote_bridge" / "bridge.py"
        bridge_file.parent.mkdir(parents=True, exist_ok=True)
        sample_code = "class AntigravityRemoteBridge:\n    pass\n"
        bridge_file.write_text(sample_code, encoding="utf-8")

        code, status, diag = retrieve_bridge_source(str(ws_dir))
        self.assertEqual(code, sample_code)
        self.assertEqual(status, "SOURCE_RETRIEVED")
        self.assertEqual(diag["source"], "LOCAL_CHECKOUT")

    def test_retrieve_bridge_source_pinned_download_failure(self):
        ws_dir = Path(self.tmp_dir) / "empty_ws"
        ws_dir.mkdir(parents=True, exist_ok=True)

        def mock_fail_runner(cmd, cwd=None):
            return 1, "", "API rate limit exceeded", "RATE_LIMITED"

        def mock_fail_http(url):
            raise RuntimeError("Connection refused")

        code, status, diag = retrieve_bridge_source(
            str(ws_dir), max_retries=1, runner=mock_fail_runner, http_fetcher=mock_fail_http, check_script_dir=False
        )
        self.assertIsNone(code)
        self.assertEqual(status, "BLOCKED: PINNED_SOURCE_DOWNLOAD_FAILED")

    def test_backup_and_rollback_on_failure(self):
        state_dir = Path(self.tmp_dir) / "state"
        state_dir.mkdir(parents=True, exist_ok=True)

        config_file = state_dir / "config.json"
        original_config = '{"original": true}'
        config_file.write_text(original_config, encoding="utf-8")

        mgr = BackupManager(state_dir)
        mgr.backup_file(config_file)

        # Overwrite with bad content
        config_file.write_text('{"corrupted": true}', encoding="utf-8")
        self.assertIn("corrupted", config_file.read_text(encoding="utf-8"))

        # Restore
        mgr.restore()
        self.assertEqual(config_file.read_text(encoding="utf-8"), original_config)

    def test_validate_workspace(self):
        ws_dir = Path(self.tmp_dir) / "repo"
        ws_dir.mkdir(parents=True, exist_ok=True)

        def mock_git_runner(cmd, cwd=None):
            if "remote" in cmd:
                return 0, f"https://github.com/{REPO_NAME}.git", "", "SUCCESS"
            if "rev-parse" in cmd:
                return 0, EXPECTED_BRANCH, "", "SUCCESS"
            if "status" in cmd:
                return 0, " M modified_file.py", "", "SUCCESS"
            return 0, "", "", "SUCCESS"

        ok, msg, info = validate_workspace(str(ws_dir), runner=mock_git_runner)
        self.assertTrue(ok)
        self.assertEqual(msg, "WORKSPACE_VALID")
        self.assertTrue(info["is_dirty"])
        self.assertEqual(info["branch"], EXPECTED_BRANCH)

    def test_execute_bootstrap_end_to_end(self):
        ws_dir = Path(self.tmp_dir) / "repo"
        bridge_file = ws_dir / "control_center" / "remote_bridge" / "bridge.py"
        bridge_file.parent.mkdir(parents=True, exist_ok=True)
        valid_code = "class AntigravityRemoteBridge:\n    def __init__(self):\n        pass\n"
        bridge_file.write_text(valid_code, encoding="utf-8")

        fake_home = Path(self.tmp_dir) / "user_home"

        def mock_runner(cmd, cwd=None):
            if "user" in cmd:
                return 0, json.dumps({"id": AUTHORIZED_USER_ID, "login": "pddkalyan"}), "", "SUCCESS"
            if "remote" in cmd:
                return 0, f"https://github.com/{REPO_NAME}.git", "", "SUCCESS"
            if "rev-parse" in cmd:
                return 0, EXPECTED_BRANCH, "", "SUCCESS"
            if "status" in cmd:
                return 0, "", "", "SUCCESS"
            return 0, "", "", "SUCCESS"

        with patch("pathlib.Path.home", return_value=fake_home):
            exit_code, res = execute_bootstrap(
                workspace_dir=str(ws_dir),
                defer_metadata_to_sidecar=True,
                activate_ping=True,
                runner=mock_runner,
            )

            self.assertEqual(exit_code, 0)
            self.assertEqual(res["status"], "BOOTSTRAP_ACTIVATED")
            self.assertEqual(res["native_metadata"], "DEFERRED_TO_SIDECAR_RUNTIME")
            self.assertFalse(res["worktree_dirty_preserved"])

            config_path = fake_home / ".antigravity_bridge_state" / "config.json"
            self.assertTrue(config_path.is_file())
            cfg = json.loads(config_path.read_text(encoding="utf-8"))

            self.assertTrue(cfg["ping_enabled"])
            self.assertTrue(cfg["status_enabled"])
            self.assertFalse(cfg["dispatch_enabled"])
            self.assertFalse(cfg["allow_dirty_continue"])

    def test_corrupted_downloaded_source_rejection(self):
        ws_dir = Path(self.tmp_dir) / "repo"
        ws_dir.mkdir(parents=True, exist_ok=True)

        fake_home = Path(self.tmp_dir) / "user_home"

        def mock_runner(cmd, cwd=None):
            if "user" in cmd:
                return 0, json.dumps({"id": AUTHORIZED_USER_ID, "login": "pddkalyan"}), "", "SUCCESS"
            if "remote" in cmd:
                return 0, f"https://github.com/{REPO_NAME}.git", "", "SUCCESS"
            if "rev-parse" in cmd:
                return 0, EXPECTED_BRANCH, "", "SUCCESS"
            if "status" in cmd:
                return 0, "", "", "SUCCESS"
            return 0, "", "", "SUCCESS"

        # Return invalid python syntax as source
        def mock_corrupt_http(url):
            return "class AntigravityRemoteBridge: INVALID SYNTAX HERE !!!"

        with patch("pathlib.Path.home", return_value=fake_home):
            exit_code, res = execute_bootstrap(
                workspace_dir=str(ws_dir),
                defer_metadata_to_sidecar=True,
                runner=mock_runner,
                http_fetcher=mock_corrupt_http,
                check_script_dir=False,
            )

            self.assertEqual(exit_code, 1)
            self.assertEqual(res["status"], "BLOCKED: CORRUPTED_SOURCE_CODE")


if __name__ == "__main__":
    unittest.main()
