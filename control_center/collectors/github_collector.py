"""
GitHub Public Activity Collector adapter for Movie Studio Control Center.
Normalizes public GitHub Actions runs, jobs, PRs, commit links, and task/state files
into honest timestamped events for dashboard consumption.
"""

import datetime
import glob
import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple, Union


class RateLimitExceededException(Exception):
    """Raised when GitHub API rate limits are exceeded."""
    def __init__(self, message: str, reset_timestamp: Optional[int] = None):
        super().__init__(message)
        self.reset_timestamp = reset_timestamp


class GitHubPublicActivityCollector:
    """
    Zero-cost read-only Python adapter to collect and normalize GitHub public
    activity and local task/state files without authentication or paid API calls.
    """

    def __init__(
        self,
        repo_owner: str = "pddkalyan",
        repo_name: str = "project-agent-orchestrator",
        api_base_url: str = "https://api.github.com",
        http_client: Optional[Callable[..., Any]] = None,
        repo_root: str = ".",
        max_retries: int = 3,
        backoff_factor: float = 0.5,
    ):
        self.repo_owner = repo_owner
        self.repo_name = repo_name
        self.api_base_url = api_base_url.rstrip("/")
        self.http_client = http_client or self._default_http_client
        self.repo_root = repo_root
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self.rate_limit_info: Dict[str, Optional[int]] = {
            "limit": None,
            "remaining": None,
            "reset": None,
        }

    def _default_http_client(self, url: str, headers: Optional[Dict[str, str]] = None) -> Tuple[int, Dict[str, str], bytes]:
        req = urllib.request.Request(url, headers=headers or {})
        req.add_header("User-Agent", "ControlCenterCollector/1.0")
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                status_code = response.getcode()
                res_headers = dict(response.info())
                content = response.read()
                return status_code, res_headers, content
        except urllib.error.HTTPError as e:
            res_headers = dict(e.headers) if e.headers else {}
            content = e.read() if hasattr(e, "read") else b""
            return e.code, res_headers, content
        except urllib.error.URLError as e:
            raise IOError(f"Network error accessing {url}: {e.reason}") from e

    def _update_rate_limit_info(self, headers: Dict[str, str]) -> None:
        # Normalize header keys to lowercase
        norm_headers = {k.lower(): v for k, v in headers.items()}
        if "x-ratelimit-limit" in norm_headers:
            try:
                self.rate_limit_info["limit"] = int(norm_headers["x-ratelimit-limit"])
            except (ValueError, TypeError):
                pass
        if "x-ratelimit-remaining" in norm_headers:
            try:
                self.rate_limit_info["remaining"] = int(norm_headers["x-ratelimit-remaining"])
            except (ValueError, TypeError):
                pass
        if "x-ratelimit-reset" in norm_headers:
            try:
                self.rate_limit_info["reset"] = int(norm_headers["x-ratelimit-reset"])
            except (ValueError, TypeError):
                pass

    def _parse_link_header(self, link_header: Optional[str]) -> Dict[str, str]:
        """Parses GitHub Link header for pagination URLs."""
        links = {}
        if not link_header:
            return links
        parts = link_header.split(",")
        for part in parts:
            match = re.search(r'<([^>]+)>;\s*rel="([^"]+)"', part.strip())
            if match:
                url, rel = match.groups()
                links[rel] = url
        return links

    def _request_json_with_retry(self, url: str) -> Tuple[Optional[Any], Dict[str, str], Optional[str]]:
        """
        Executes HTTP GET request with retries for transient failures and rate limit detection.
        Returns tuple of (parsed_json, headers, error_message).
        """
        headers_sent = {"Accept": "application/vnd.github.v3+json"}
        last_error = None

        for attempt in range(self.max_retries + 1):
            try:
                status_code, resp_headers, content = self.http_client(url, headers=headers_sent)
                self._update_rate_limit_info(resp_headers)

                if status_code in (403, 429):
                    remaining = self.rate_limit_info.get("remaining")
                    if remaining is not None and remaining == 0:
                        reset_ts = self.rate_limit_info.get("reset")
                        err_msg = f"Rate limit exceeded (reset at {reset_ts})"
                        return None, resp_headers, err_msg
                    err_msg = f"HTTP {status_code}: Request forbidden or rate limited."
                    return None, resp_headers, err_msg

                if status_code in (500, 502, 503, 504):
                    last_error = f"HTTP {status_code} transient server error."
                    if attempt < self.max_retries:
                        time.sleep(self.backoff_factor * (2 ** attempt))
                        continue
                    return None, resp_headers, last_error

                if status_code >= 400:
                    return None, resp_headers, f"HTTP {status_code} client error."

                try:
                    data = json.loads(content.decode("utf-8")) if content else {}
                    return data, resp_headers, None
                except json.JSONDecodeError as e:
                    return None, resp_headers, f"JSON decode error: {e}"

            except Exception as e:
                last_error = str(e)
                if attempt < self.max_retries:
                    time.sleep(self.backoff_factor * (2 ** attempt))
                    continue
                return None, {}, last_error

        return None, {}, last_error or "Unknown failure"

    def fetch_public_actions_runs(self, page: int = 1, per_page: int = 30) -> Dict[str, Any]:
        """Fetches workflow runs for the public repository."""
        url = f"{self.api_base_url}/repos/{self.repo_owner}/{self.repo_name}/actions/runs?page={page}&per_page={per_page}"
        data, headers, error = self._request_json_with_retry(url)
        link_header = headers.get("link") or headers.get("Link")
        next_page = "next" in self._parse_link_header(link_header)

        runs = data.get("workflow_runs", []) if isinstance(data, dict) else []
        return {
            "page": page,
            "has_next_page": next_page,
            "error": error,
            "raw_runs": runs,
        }

    def fetch_run_jobs(self, run_id: Union[int, str]) -> Dict[str, Any]:
        """Fetches jobs for a specific workflow run ID."""
        url = f"{self.api_base_url}/repos/{self.repo_owner}/{self.repo_name}/actions/runs/{run_id}/jobs"
        data, headers, error = self._request_json_with_retry(url)
        jobs = data.get("jobs", []) if isinstance(data, dict) else []
        return {
            "run_id": run_id,
            "error": error,
            "raw_jobs": jobs,
        }

    def fetch_pull_requests(self, state: str = "all", page: int = 1, per_page: int = 30) -> Dict[str, Any]:
        """Fetches pull requests for the repository."""
        url = f"{self.api_base_url}/repos/{self.repo_owner}/{self.repo_name}/pulls?state={state}&page={page}&per_page={per_page}"
        data, headers, error = self._request_json_with_retry(url)
        link_header = headers.get("link") or headers.get("Link")
        next_page = "next" in self._parse_link_header(link_header)

        prs = data if isinstance(data, list) else []
        return {
            "page": page,
            "has_next_page": next_page,
            "error": error,
            "raw_prs": prs,
        }

    def fetch_commits(self, page: int = 1, per_page: int = 30) -> Dict[str, Any]:
        """Fetches recent commits for the repository."""
        url = f"{self.api_base_url}/repos/{self.repo_owner}/{self.repo_name}/commits?page={page}&per_page={per_page}"
        data, headers, error = self._request_json_with_retry(url)
        link_header = headers.get("link") or headers.get("Link")
        next_page = "next" in self._parse_link_header(link_header)

        commits = data if isinstance(data, list) else []
        return {
            "page": page,
            "has_next_page": next_page,
            "error": error,
            "raw_commits": commits,
        }

    def collect_local_task_state(self) -> Dict[str, Any]:
        """
        Ingests local repository task queue, memory state files, and provenance records safely.
        """
        local_events = []
        errors = []

        # 1. Memory files
        current_state_path = os.path.join(self.repo_root, "memory", "CURRENT_STATE.json")
        if os.path.exists(current_state_path):
            try:
                with open(current_state_path, "r", encoding="utf-8") as f:
                    cstate = json.load(f)
                    local_events.append({
                        "kind": "local_state",
                        "source": "memory/CURRENT_STATE.json",
                        "data": cstate,
                    })
            except Exception as e:
                errors.append(f"Error reading {current_state_path}: {e}")

        next_actions_path = os.path.join(self.repo_root, "memory", "NEXT_ACTIONS.json")
        if os.path.exists(next_actions_path):
            try:
                with open(next_actions_path, "r", encoding="utf-8") as f:
                    nactions = json.load(f)
                    local_events.append({
                        "kind": "local_next_actions",
                        "source": "memory/NEXT_ACTIONS.json",
                        "data": nactions,
                    })
            except Exception as e:
                errors.append(f"Error reading {next_actions_path}: {e}")

        # 2. Task queue
        queue_pattern = os.path.join(self.repo_root, "tasks", "queue", "*.json")
        for qfile in glob.glob(queue_pattern):
            try:
                with open(qfile, "r", encoding="utf-8") as f:
                    task_data = json.load(f)
                    local_events.append({
                        "kind": "local_task",
                        "source": os.path.relpath(qfile, self.repo_root),
                        "data": task_data,
                    })
            except Exception as e:
                errors.append(f"Error reading {qfile}: {e}")

        # 3. Provenance registry
        prov_path = os.path.join(self.repo_root, "PROVENANCE_REGISTRY.jsonl")
        if os.path.exists(prov_path):
            try:
                prov_records = []
                with open(prov_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line_str = line.strip()
                        if line_str:
                            prov_records.append(json.loads(line_str))
                local_events.append({
                    "kind": "local_provenance",
                    "source": "PROVENANCE_REGISTRY.jsonl",
                    "data": prov_records,
                })
            except Exception as e:
                errors.append(f"Error reading {prov_path}: {e}")

        return {
            "raw_local_events": local_events,
            "errors": errors,
        }

    def normalize_event(self, raw_item: Dict[str, Any], event_type: str) -> Dict[str, Any]:
        """
        Normalizes a raw event item into a standard timestamped event contract.
        Authoritative metadata checks ensure model/quota/think/activity fields are
        populated ONLY if present in raw authoritative source data, else set to 'unknown' or None.
        """
        event_id = "unknown"
        timestamp = "1970-01-01T00:00:00Z"
        source = f"{self.repo_owner}/{self.repo_name}"
        title = "Untitled Event"
        status = "unknown"
        url = None
        author = None

        meta_model: Optional[str] = "unknown"
        meta_quota: Optional[str] = "unknown"
        meta_think: Optional[str] = "unknown"
        meta_activity: Optional[str] = "unknown"

        if event_type == "action_run":
            event_id = f"action_run_{raw_item.get('id', 'unknown')}"
            timestamp = raw_item.get("created_at") or raw_item.get("updated_at") or timestamp
            title = raw_item.get("name") or raw_item.get("display_title") or "Actions Run"
            status = raw_item.get("status") or raw_item.get("conclusion") or "unknown"
            url = raw_item.get("html_url")
            actor = raw_item.get("actor")
            if isinstance(actor, dict):
                author = actor.get("login")

        elif event_type == "action_job":
            event_id = f"action_job_{raw_item.get('id', 'unknown')}"
            timestamp = raw_item.get("started_at") or raw_item.get("completed_at") or timestamp
            title = raw_item.get("name") or "Actions Job"
            status = raw_item.get("status") or raw_item.get("conclusion") or "unknown"
            url = raw_item.get("html_url")

        elif event_type == "pull_request":
            event_id = f"pr_{raw_item.get('number', raw_item.get('id', 'unknown'))}"
            timestamp = raw_item.get("created_at") or raw_item.get("updated_at") or timestamp
            title = raw_item.get("title") or "Pull Request"
            status = raw_item.get("state") or "unknown"
            if raw_item.get("merged"):
                status = "merged"
            url = raw_item.get("html_url")
            user = raw_item.get("user")
            if isinstance(user, dict):
                author = user.get("login")

        elif event_type == "commit":
            sha = raw_item.get("sha", "unknown")
            event_id = f"commit_{sha[:7]}"
            url = raw_item.get("html_url")
            commit_info = raw_item.get("commit", {})
            if isinstance(commit_info, dict):
                title = commit_info.get("message", "Commit").split("\n")[0]
                committer = commit_info.get("committer", {})
                if isinstance(committer, dict):
                    timestamp = committer.get("date") or timestamp
                author_info = commit_info.get("author", {})
                if isinstance(author_info, dict):
                    author = author_info.get("name") or author_info.get("email")
            status = "committed"

        elif event_type == "task_state":
            kind = raw_item.get("kind", "local_state")
            source = raw_item.get("source", "local_state")
            data = raw_item.get("data", {})
            event_id = f"task_state_{hash(source) & 0xffffffff:08x}"
            status = "active"
            title = f"Task State: {os.path.basename(source)}"

            # Check for authoritative model / quota / think fields if present
            if isinstance(data, dict):
                if "model" in data and data["model"]:
                    meta_model = str(data["model"])
                elif "created_by" in data and "model" in data:
                    meta_model = str(data["model"])
                if "quota" in data and data["quota"]:
                    meta_quota = str(data["quota"])
                if "think" in data and data["think"]:
                    meta_think = str(data["think"])
                if "activity" in data and data["activity"]:
                    meta_activity = str(data["activity"])
                if "actor" in data and data["actor"]:
                    author = str(data["actor"])
                elif "created_by" in data and data["created_by"]:
                    author = str(data["created_by"])

        # Sanitize metadata to avoid leaking secrets/private info
        normalized_event = {
            "event_id": str(event_id),
            "event_type": str(event_type),
            "timestamp": str(timestamp),
            "source": str(source),
            "title": str(title),
            "status": str(status),
            "url": url,
            "author": author,
            "metadata": {
                "model": meta_model,
                "quota": meta_quota,
                "think": meta_think,
                "activity": meta_activity,
            },
        }

        return normalized_event

    def collect_all(self, include_jobs: bool = False, fetch_remote: bool = True) -> Dict[str, Any]:
        """
        Executes full activity collection across remote GitHub APIs and local state files.
        """
        events = []
        errors = []

        if fetch_remote:
            # 1. Fetch Actions runs
            runs_res = self.fetch_public_actions_runs(page=1, per_page=30)
            if runs_res.get("error"):
                errors.append({"source": "actions_runs", "error": runs_res["error"]})
            for raw_run in runs_res.get("raw_runs", []):
                events.append(self.normalize_event(raw_run, "action_run"))
                if include_jobs and "id" in raw_run:
                    jobs_res = self.fetch_run_jobs(raw_run["id"])
                    if jobs_res.get("error"):
                        errors.append({"source": f"run_jobs_{raw_run['id']}", "error": jobs_res["error"]})
                    for raw_job in jobs_res.get("raw_jobs", []):
                        events.append(self.normalize_event(raw_job, "action_job"))

            # 2. Fetch Pull Requests
            prs_res = self.fetch_pull_requests(page=1, per_page=30)
            if prs_res.get("error"):
                errors.append({"source": "pull_requests", "error": prs_res["error"]})
            for raw_pr in prs_res.get("raw_prs", []):
                events.append(self.normalize_event(raw_pr, "pull_request"))

            # 3. Fetch Commits
            commits_res = self.fetch_commits(page=1, per_page=30)
            if commits_res.get("error"):
                errors.append({"source": "commits", "error": commits_res["error"]})
            for raw_commit in commits_res.get("raw_commits", []):
                events.append(self.normalize_event(raw_commit, "commit"))

        # 4. Fetch Local Task State
        local_res = self.collect_local_task_state()
        if local_res.get("errors"):
            for l_err in local_res["errors"]:
                errors.append({"source": "local_task_state", "error": l_err})
        for raw_local in local_res.get("raw_local_events", []):
            events.append(self.normalize_event(raw_local, "task_state"))

        collected_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        overall_status = "success"
        if errors and events:
            overall_status = "partial_success"
        elif errors and not events:
            overall_status = "failed"

        return {
            "collector": "GitHubPublicActivityCollector",
            "repo": f"{self.repo_owner}/{self.repo_name}",
            "collected_at": collected_at,
            "status": overall_status,
            "rate_limit": self.rate_limit_info,
            "events_count": len(events),
            "events": events,
            "errors": errors,
        }
