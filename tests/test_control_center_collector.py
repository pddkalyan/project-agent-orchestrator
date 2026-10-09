"""
Offline deterministic unit tests for Control Center GitHub Public Activity Collector.
"""

import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock

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

    def test_rate_limit_exceeded_403(self):
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

    def test_missing_fields_graceful_fallbacks(self):
        collector = GitHubPublicActivityCollector()

        # Partial action run
        raw_run = {"id": 99}
        norm_run = collector.normalize_event(raw_run, "action_run")
        self.assertEqual(norm_run["event_id"], "action_run_99")
        self.assertEqual(norm_run["timestamp"], "1970-01-01T00:00:00Z")
        self.assertEqual(norm_run["title"], "Actions Run")
        self.assertEqual(norm_run["status"], "unknown")
        self.assertEqual(norm_run["metadata"]["model"], "unknown")
        self.assertEqual(norm_run["metadata"]["quota"], "unknown")
        self.assertEqual(norm_run["metadata"]["think"], "unknown")
        self.assertEqual(norm_run["metadata"]["activity"], "unknown")

    def test_local_task_state_collection(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = os.path.join(tmpdir, "memory")
            tasks_dir = os.path.join(tmpdir, "tasks", "queue")
            os.makedirs(memory_dir, exist_ok=True)
            os.makedirs(tasks_dir, exist_ok=True)

            with open(os.path.join(memory_dir, "CURRENT_STATE.json"), "w", encoding="utf-8") as f:
                json.dump({"phase": "CONTROL_CENTER", "model": "google-jules"}, f)

            with open(os.path.join(tasks_dir, "task_001.json"), "w", encoding="utf-8") as f:
                json.dump({"task_id": "TASK-0010", "title": "Collector task"}, f)

            with open(os.path.join(tmpdir, "PROVENANCE_REGISTRY.jsonl"), "w", encoding="utf-8") as f:
                f.write('{"file":"test.py","actor":"test-user","model":"google-jules"}\n')

            collector = GitHubPublicActivityCollector(repo_root=tmpdir)
            res = collector.collect_local_task_state()

            self.assertEqual(len(res["errors"]), 0)
            self.assertEqual(len(res["raw_local_events"]), 3)

            events = [collector.normalize_event(item, "task_state") for item in res["raw_local_events"]]
            self.assertTrue(any(e["metadata"]["model"] == "google-jules" for e in events))

    def test_authoritative_metadata_vs_unknown(self):
        collector = GitHubPublicActivityCollector()

        # Action run without metadata fields -> defaults to "unknown"
        raw_run = {"id": 123, "name": "Run"}
        norm_run = collector.normalize_event(raw_run, "action_run")
        self.assertEqual(norm_run["metadata"]["model"], "unknown")
        self.assertEqual(norm_run["metadata"]["quota"], "unknown")

        # Task state with authoritative model & quota fields -> preserved
        raw_state = {
            "kind": "local_state",
            "source": "memory/CURRENT_STATE.json",
            "data": {
                "model": "google-jules",
                "quota": "5000",
                "think": "active",
                "activity": "indexing"
            }
        }
        norm_state = collector.normalize_event(raw_state, "task_state")
        self.assertEqual(norm_state["metadata"]["model"], "google-jules")
        self.assertEqual(norm_state["metadata"]["quota"], "5000")
        self.assertEqual(norm_state["metadata"]["think"], "active")
        self.assertEqual(norm_state["metadata"]["activity"], "indexing")

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
