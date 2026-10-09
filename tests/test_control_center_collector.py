"""
Offline deterministic unit tests for Control Center GitHub Public Activity Collector.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

from control_center.collectors.github_collector import (
    GitHubPublicActivityCollector,
)


class TestControlCenterCollector(unittest.TestCase):

    def setUp(self):
        self.maxDiff = None

    def test_init_defaults(self):
        collector = GitHubPublicActivityCollector()
        self.assertEqual(collector.repo_owner, "pddkalyan")
        self.assertEqual(collector.repo_name, "project-agent-orchestrator")
        self.assertEqual(collector.api_base_url, "https://api.github.com")

    def test_init_explicit_overrides(self):
        collector = GitHubPublicActivityCollector(repo_owner="custom-owner", repo_name="custom-repo")
        self.assertEqual(collector.repo_owner, "custom-owner")
        self.assertEqual(collector.repo_name, "custom-repo")

    def test_fetch_actions_runs_success_with_pagination(self):
        mock_runs_payload = {
            "total_count": 1,
            "workflow_runs": [
                {
                    "id": 1001,
                    "name": "Build & Test",
                    "status": "completed",
                    "conclusion": "success",
                    "created_at": "2026-10-10T12:00:00Z",
                    "html_url": "https://github.com/pddkalyan/project-agent-orchestrator/actions/runs/1001",
                    "actor": {"login": "test-user"},
                }
            ],
        }
        mock_headers = {
            "Link": '<https://api.github.com/repos/pddkalyan/project-agent-orchestrator/actions/runs?page=2>; rel="next"',
            "X-RateLimit-Limit": "60",
            "X-RateLimit-Remaining": "58",
            "X-RateLimit-Reset": "1700000000",
        }

        def mock_client(url, headers=None):
            return 200, mock_headers, json.dumps(mock_runs_payload).encode("utf-8")

        collector = GitHubPublicActivityCollector(http_client=mock_client)
        res = collector.fetch_public_actions_runs(page=1)

        self.assertIsNone(res["error"])
        self.assertTrue(res["has_next_page"])
        self.assertEqual(len(res["raw_runs"]), 1)
        self.assertEqual(res["raw_runs"][0]["id"], 1001)
        self.assertEqual(collector.rate_limit_info["remaining"], 58)

    def test_fetch_run_jobs(self):
        mock_jobs_payload = {
            "total_count": 1,
            "jobs": [
                {
                    "id": 2001,
                    "name": "test_job",
                    "status": "completed",
                    "conclusion": "success",
                    "started_at": "2026-10-10T12:01:00Z",
                    "html_url": "https://github.com/pddkalyan/project-agent-orchestrator/actions/jobs/2001",
                }
            ],
        }

        def mock_client(url, headers=None):
            return 200, {}, json.dumps(mock_jobs_payload).encode("utf-8")

        collector = GitHubPublicActivityCollector(http_client=mock_client)
        res = collector.fetch_run_jobs(1001)

        self.assertIsNone(res["error"])
        self.assertEqual(len(res["raw_jobs"]), 1)
        self.assertEqual(res["raw_jobs"][0]["id"], 2001)

    def test_fetch_pull_requests(self):
        mock_prs = [
            {
                "id": 3001,
                "number": 42,
                "title": "Control Center Feature",
                "state": "open",
                "created_at": "2026-10-10T10:00:00Z",
                "html_url": "https://github.com/pddkalyan/project-agent-orchestrator/pull/42",
                "user": {"login": "test-user"},
            }
        ]

        def mock_client(url, headers=None):
            return 200, {}, json.dumps(mock_prs).encode("utf-8")

        collector = GitHubPublicActivityCollector(http_client=mock_client)
        res = collector.fetch_pull_requests()

        self.assertIsNone(res["error"])
        self.assertEqual(len(res["raw_prs"]), 1)
        self.assertEqual(res["raw_prs"][0]["number"], 42)

    def test_fetch_commits(self):
        mock_commits = [
            {
                "sha": "1234567890abcdef1234567890abcdef12345678",
                "commit": {
                    "message": "feat: add control center collector",
                    "committer": {"date": "2026-10-10T11:00:00Z"},
                    "author": {"name": "Jules Agent"},
                },
                "html_url": "https://github.com/pddkalyan/project-agent-orchestrator/commit/1234567890abcdef1234567890abcdef12345678",
            }
        ]

        def mock_client(url, headers=None):
            return 200, {}, json.dumps(mock_commits).encode("utf-8")

        collector = GitHubPublicActivityCollector(http_client=mock_client)
        res = collector.fetch_commits()

        self.assertIsNone(res["error"])
        self.assertEqual(len(res["raw_commits"]), 1)
        self.assertEqual(res["raw_commits"][0]["sha"][:7], "1234567")

    def test_transient_retry_success(self):
        calls = []

        def mock_client(url, headers=None):
            calls.append(url)
            if len(calls) < 2:
                return 502, {}, b"Bad Gateway"
            return 200, {}, json.dumps({"workflow_runs": []}).encode("utf-8")

        collector = GitHubPublicActivityCollector(http_client=mock_client, backoff_factor=0.001)
        res = collector.fetch_public_actions_runs()

        self.assertEqual(len(calls), 2)
        self.assertIsNone(res["error"])

    def test_transient_retry_exhaustion(self):
        def mock_client(url, headers=None):
            return 500, {}, b"Internal Server Error"

        collector = GitHubPublicActivityCollector(http_client=mock_client, max_retries=2, backoff_factor=0.001)
        res = collector.fetch_public_actions_runs()

        self.assertIsNotNone(res["error"])
        self.assertIn("HTTP 500", res["error"])

    def test_rate_limit_exceeded_403_and_429(self):
        headers_rate_limit = {
            "X-RateLimit-Limit": "60",
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset": "1700000000",
        }

        def mock_client(url, headers=None):
            return 403, headers_rate_limit, b"API rate limit exceeded"

        collector = GitHubPublicActivityCollector(http_client=mock_client)
        res = collector.fetch_public_actions_runs()

        self.assertIsNotNone(res["error"])
        self.assertIn("Rate limit exceeded", res["error"])
        self.assertEqual(collector.rate_limit_info["remaining"], 0)

        # collect_all status should be rate_limited
        collected = collector.collect_all(fetch_remote=True)
        self.assertEqual(collected["status"], "rate_limited")

    def test_missing_and_invalid_timestamps_graceful_fallbacks(self):
        collector = GitHubPublicActivityCollector()

        # Partial action run without timestamp -> None, NOT 1970-01-01
        raw_run = {"id": 99}
        norm_run = collector.normalize_event(raw_run, "action_run")
        self.assertEqual(norm_run["event_id"], "action_run_99")
        self.assertIsNone(norm_run["timestamp"])
        self.assertEqual(norm_run["title"], "Actions Run")
        self.assertEqual(norm_run["status"], "unknown")
        self.assertEqual(norm_run["metadata"]["model"], "UNKNOWN")
        self.assertEqual(norm_run["metadata"]["quota"], "UNKNOWN")
        self.assertEqual(norm_run["metadata"]["think"], "UNKNOWN")
        self.assertEqual(norm_run["metadata"]["activity"], "UNKNOWN")

    def test_pr_merge_detection_and_negative_fixture(self):
        collector = GitHubPublicActivityCollector()

        # Merged PR with merged_at timestamp
        merged_pr = {
            "id": 101,
            "number": 10,
            "title": "Merged PR",
            "state": "closed",
            "merged_at": "2026-10-09T18:00:00Z",
            "draft": False,
        }
        norm_merged = collector.normalize_event(merged_pr, "pull_request")
        self.assertEqual(norm_merged["status"], "merged")

        # Negative fixture: closed unmerged PR (merged_at=None)
        closed_pr = {
            "id": 102,
            "number": 11,
            "title": "Closed Unmerged PR",
            "state": "closed",
            "merged_at": None,
            "draft": False,
        }
        norm_closed = collector.normalize_event(closed_pr, "pull_request")
        self.assertEqual(norm_closed["status"], "closed")

        # Draft PR
        draft_pr = {
            "id": 103,
            "number": 12,
            "title": "Draft PR",
            "state": "open",
            "merged_at": None,
            "draft": True,
        }
        norm_draft = collector.normalize_event(draft_pr, "pull_request")
        self.assertEqual(norm_draft["status"], "draft")

    def test_deterministic_task_state_receipts_across_hash_seeds(self):
        # Run subprocesses with different PYTHONHASHSEED values to ensure deterministic receipts
        cmd = [
            sys.executable,
            "-c",
            "import json; from control_center.collectors.github_collector import GitHubPublicActivityCollector; "
            "c = GitHubPublicActivityCollector(); "
            "e = c.normalize_event({'source': 'tasks/queue/task_001.json'}, 'task_state'); "
            "print(e['event_id'])"
        ]

        env1 = os.environ.copy()
        env1["PYTHONHASHSEED"] = "0"
        res1 = subprocess.check_output(cmd, env=env1, text=True).strip()

        env2 = os.environ.copy()
        env2["PYTHONHASHSEED"] = "12345"
        res2 = subprocess.check_output(cmd, env=env2, text=True).strip()

        env3 = os.environ.copy()
        env3["PYTHONHASHSEED"] = "random"
        res3 = subprocess.check_output(cmd, env=env3, text=True).strip()

        self.assertEqual(res1, res2)
        self.assertEqual(res2, res3)
        self.assertTrue(res1.startswith("task_state_"))

    def test_corrupted_local_files_resilience(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = os.path.join(tmpdir, "memory")
            tasks_dir = os.path.join(tmpdir, "tasks", "queue")
            os.makedirs(memory_dir, exist_ok=True)
            os.makedirs(tasks_dir, exist_ok=True)

            # Corrupted JSON file in memory
            with open(os.path.join(memory_dir, "CURRENT_STATE.json"), "w", encoding="utf-8") as f:
                f.write("INVALID JSON {{{")

            # Corrupted line in provenance registry
            with open(os.path.join(tmpdir, "PROVENANCE_REGISTRY.jsonl"), "w", encoding="utf-8") as f:
                f.write('{"valid": "record"}\nCORRUPTED LINE\n')

            collector = GitHubPublicActivityCollector(repo_root=tmpdir)
            res = collector.collect_local_task_state()

            # Should not crash and should record error for corrupted files
            self.assertTrue(len(res["errors"]) >= 2)
            self.assertEqual(len(res["raw_local_events"]), 1)

    def test_normalize_event_malformed_fields_safety(self):
        collector = GitHubPublicActivityCollector()

        # Commit with sha=None, commit.message=None, author=None
        raw_commit = {
            "sha": None,
            "commit": {
                "message": None,
                "committer": None,
                "author": None,
            },
            "html_url": "invalid_url_no_scheme",
        }
        norm_commit = collector.normalize_event(raw_commit, "commit")
        self.assertEqual(norm_commit["event_id"], "commit_unknown")
        self.assertEqual(norm_commit["title"], "Commit")
        self.assertIsNone(norm_commit["url"])
        self.assertIsNone(norm_commit["author"])

        # Non-dict raw_item
        norm_non_dict = collector.normalize_event(None, "action_run")  # type: ignore
        self.assertEqual(norm_non_dict["event_id"], "action_run_unknown")

        # Action run with non-string actor
        raw_run = {"id": "123", "actor": "not_a_dict"}
        norm_run = collector.normalize_event(raw_run, "action_run")
        self.assertEqual(norm_run["event_id"], "action_run_123")
        self.assertIsNone(norm_run["author"])

    def test_job_fanout_capping(self):
        mock_runs_payload = {
            "workflow_runs": [{"id": i} for i in range(10)],
        }
        fetched_jobs_for = []

        def mock_client(url, headers=None):
            if "actions/runs?" in url:
                return 200, {}, json.dumps(mock_runs_payload).encode("utf-8")
            if "/jobs" in url:
                run_id = url.split("/runs/")[1].split("/jobs")[0]
                fetched_jobs_for.append(run_id)
                return 200, {}, json.dumps({"jobs": []}).encode("utf-8")
            return 200, {}, b"{}"

        collector = GitHubPublicActivityCollector(http_client=mock_client)
        res = collector.collect_all(include_jobs=True, fetch_remote=True, max_job_fanout=3)

        self.assertEqual(len(fetched_jobs_for), 3)
        self.assertEqual(res["status"], "success")

    def test_authoritative_metadata_vs_unknown(self):
        collector = GitHubPublicActivityCollector()

        # Task state with disk assertions -> UNKNOWN live provider usage, private thoughts omitted
        raw_state = {
            "kind": "local_state",
            "source": "memory/CURRENT_STATE.json",
            "data": {
                "model": "google-jules",
                "quota": "5000",
                "think": "secret reasoning string",
                "activity": "indexing"
            }
        }
        norm_state = collector.normalize_event(raw_state, "task_state")
        self.assertEqual(norm_state["metadata"]["model"], "UNKNOWN")
        self.assertEqual(norm_state["metadata"]["quota"], "UNKNOWN")
        self.assertEqual(norm_state["metadata"]["think"], "UNKNOWN")
        self.assertEqual(norm_state["metadata"]["activity"], "UNKNOWN")
        self.assertNotIn("secret reasoning string", json.dumps(norm_state))

    def test_sample_fixture_contract_validation(self):
        contract_path = os.path.join(os.path.dirname(__file__), "..", "control_center", "collectors", "contract.json")
        with open(contract_path, "r", encoding="utf-8") as f:
            sample_data = json.load(f)

        self.assertEqual(sample_data["collector"], "GitHubPublicActivityCollector")
        self.assertEqual(sample_data["repo"], "pddkalyan/project-agent-orchestrator")
        self.assertEqual(sample_data["status"], "success")

        for event in sample_data["events"]:
            self.assertEqual(event["source"], "pddkalyan/project-agent-orchestrator")
            self.assertIn("pddkalyan", event["url"])
            self.assertIn("metadata", event)
            for k in ["model", "quota", "think", "activity"]:
                self.assertIn(k, event["metadata"])

    def test_collect_all_offline_mode(self):
        collector = GitHubPublicActivityCollector()
        res = collector.collect_all(fetch_remote=False)

        self.assertEqual(res["status"], "success")
        self.assertEqual(res["collector"], "GitHubPublicActivityCollector")
        self.assertIn("events", res)
        self.assertIn("collected_at", res)

    def test_json_contract_structure(self):
        collector = GitHubPublicActivityCollector()
        payload = collector.collect_all(fetch_remote=False)

        required_keys = ["collector", "repo", "collected_at", "status", "rate_limit", "events_count", "events", "errors"]
        for key in required_keys:
            self.assertIn(key, payload)

        for event in payload["events"]:
            event_required_keys = ["event_id", "event_type", "timestamp", "source", "title", "status", "url", "author", "metadata"]
            for ek in event_required_keys:
                self.assertIn(ek, event)
            meta_required_keys = ["model", "quota", "think", "activity"]
            for mk in meta_required_keys:
                self.assertIn(mk, event["metadata"])


if __name__ == "__main__":
    unittest.main()
