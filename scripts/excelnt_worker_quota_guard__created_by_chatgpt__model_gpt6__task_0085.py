"""Offline fail-closed quota gate for two *separately authorized* Excelnt workers.

Creator: ChatGPT / GPT-6, TASK-0085. NO Excelnt API endpoints or credentials.
A separately reviewed trusted adapter must provide authenticated QuotaSnapshot values.
This module intentionally makes no network/model requests and cannot activate agents.
"""

from __future__ import annotations

import contextlib
import hashlib
import dataclasses
import json
import math
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

VERSION = 1
WARN_RATIO = 0.80
STOP_RATIO = 0.90
MAX_OBSERVATION_AGE = timedelta(minutes=5)
MAX_FUTURE_CLOCK_SKEW = timedelta(seconds=30)
MAX_COMPLETED_REQUESTS = 1024  # fail closed on journal exhaustion
WORKERS = frozenset({"A", "B"})
STATES = frozenset({"READY", "QUOTA_WARNING", "WAITING_FOR_RESET", "IN_FLIGHT",
                    "AUTH_ERROR", "UNKNOWN", "NEEDS_RECONCILIATION"})
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
TASK_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,159}$")


class GuardBlocked(RuntimeError):
    """The model request has NOT been authorized by this guard."""


class StateCorrupt(GuardBlocked):
    """Existing on-disk state is unreadable; never replace or discard it."""


class WorkerBusy(GuardBlocked):
    """Another process owns this worker's non-expiring lock."""


class FeedUnavailable(GuardBlocked):
    """A provider quota feed is not connected or cannot be authenticated."""


def _utc_datetime(raw: str) -> datetime:
    if not isinstance(raw, str) or not raw:
        raise GuardBlocked("Timestamp missing or malformed")
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GuardBlocked("Timestamp missing or malformed") from exc
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise GuardBlocked("Naive timestamp is unsafe")
    return ts.astimezone(timezone.utc)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(ts: datetime) -> str:
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise GuardBlocked("Clock must be timezone aware")
    return ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _number(value: object, *, allow_zero: bool = True) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GuardBlocked("Quota values must be numeric")
    result = float(value)
    if not math.isfinite(result) or result < 0 or (not allow_zero and result == 0):
        raise GuardBlocked("Quota values must be finite and nonnegative")
    return result


@dataclass(frozen=True)
class QuotaSnapshot:
    """Normalized, authenticated provider observations, NOT a proven Excelnt schema.

    Amounts are in identical *provider-reported accounting units*. Code never assumes
    dollars are cash. Future adapter MUST prove paid top-ups and overages are disabled.
    """
    worker: str
    observed_at: str
    five_hour_reset_at: str
    weekly_reset_at: str
    five_hour_used: float
    five_hour_limit: float
    weekly_used: float
    weekly_limit: float
    paid_overage_disabled: bool
    auto_topup_disabled: bool


class TrustedQuotaFeed(Protocol):
    def read_snapshot(self, worker: str) -> QuotaSnapshot:
        """Must use a reviewed, authenticated, read-only, OFFICIAL provider source."""


@dataclass(frozen=True)
class TaskCheckpoint:
    task_id: str
    branch: str
    head_sha: str
    tests_summary: str
    next_action: str

    def validate(self) -> None:
        if not TASK_RE.fullmatch(self.task_id):
            raise GuardBlocked("Invalid task ID")
        if (not BRANCH_RE.fullmatch(self.branch) or ".." in self.branch or
                self.branch.startswith("/") or self.branch.endswith("/")):
            raise GuardBlocked("Invalid branch")
        if not isinstance(self.head_sha, str) or not SHA_RE.fullmatch(self.head_sha):
            raise GuardBlocked("Invalid commit SHA")
        for name in ("tests_summary", "next_action"):
            s = getattr(self, name)
            if not isinstance(s, str) or not s or len(s) > 600 or any(ord(c) < 32 for c in s):
                raise GuardBlocked("Invalid checkpoint text")


class WorkerQuotaGuard:
    """Durable authorizer; never calls a model, switches accounts, or buys usage.

    Require a verified quota feed per worker before any request can be approved.
    A durable IN_FLIGHT record is fsynced BEFORE handing a request to the caller.
    A crash during/after an external call is ambiguous and must never auto-replay.
    """

    def __init__(self, state_dir: Path, feed: TrustedQuotaFeed | None = None, *, clock=None):
        self.state_dir = Path(state_dir)
        self.feed = feed
        self.clock = clock or _utc_now

    @staticmethod
    def _worker(worker: str) -> str:
        if not isinstance(worker, str) or worker not in WORKERS:
            raise GuardBlocked("Only independent worker aliases A and B are configured")
        return worker

    def _file(self, worker: str) -> Path:
        return self.state_dir / f"worker_{self._worker(worker)}.json"

    def _check_dir(self) -> None:
        if self.state_dir.is_symlink():
            raise GuardBlocked("Symlink state directory not permitted")
        self.state_dir.mkdir(parents=True, exist_ok=True)
        if self.state_dir.is_symlink():
            raise GuardBlocked("Symlink state directory not permitted")
        if os.name != "nt":
            os.chmod(self.state_dir, 0o700)

    @contextlib.contextmanager
    def _lease(self, worker: str):
        self._check_dir()
        lock = self.state_dir / f"worker_{self._worker(worker)}.lock"
        try:
            lock.mkdir(mode=0o700)
        except FileExistsError as exc:
            raise WorkerBusy("Exclusive worker lock exists; manual inspection required") from exc
        try:
            yield
        finally:
            lock.rmdir()

    def _load(self, worker: str) -> dict | None:
        path = self._file(worker)
        if path.is_symlink():
            raise StateCorrupt("Symlink state file not permitted")
        if not path.exists():
            return None
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise StateCorrupt("State file unreadable; retain bytes for repair") from exc
        required = {"version", "worker", "status", "checkpoint", "pending_request_id",
                    "last_request_id", "last_observed_at", "last_five_hour_reset_at",
                    "last_weekly_reset_at", "reason", "completed_request_digests"}
        if (not isinstance(state, dict) or set(state) != required or
                type(state["version"]) is not int or state["version"] != VERSION or
                state["worker"] != worker or not isinstance(state["status"], str) or
                state["status"] not in STATES or
                (state["pending_request_id"] is not None and
                 (not isinstance(state["pending_request_id"], str) or
                  not TASK_RE.fullmatch(state["pending_request_id"])))):
            raise StateCorrupt("State schema invalid; no automatic recovery")
        if (not isinstance(state["completed_request_digests"], list) or
                len(state["completed_request_digests"]) > MAX_COMPLETED_REQUESTS or
                any(not isinstance(x, str) or not re.fullmatch(r"[0-9a-f]{64}", x)
                    for x in state["completed_request_digests"]) or
                len(set(state["completed_request_digests"])) != len(state["completed_request_digests"])):
            raise StateCorrupt("Completed request journal malformed")
        for key in ("last_request_id", "last_observed_at", "last_five_hour_reset_at",
                    "last_weekly_reset_at", "reason"):
            val = state[key]
            if val is not None and (not isinstance(val, str) or len(val) > 300):
                raise StateCorrupt("State metadata invalid")
        try:
            if state["checkpoint"] is not None:
                if not isinstance(state["checkpoint"], dict) or set(state["checkpoint"]) != {
                    "task_id", "branch", "head_sha", "tests_summary", "next_action"
                }:
                    raise GuardBlocked("Bad checkpoint schema")
                TaskCheckpoint(**state["checkpoint"]).validate()
        except (TypeError, GuardBlocked) as exc:
            raise StateCorrupt("Checkpoint malformed") from exc
        if state["status"] == "IN_FLIGHT" and state["pending_request_id"] is None:
            raise StateCorrupt("In-flight request missing durable intent")
        if state["pending_request_id"] is not None and state["status"] not in {
                "IN_FLIGHT", "NEEDS_RECONCILIATION"}:
            raise StateCorrupt("Unexpected pending request in state")
        return state

    def _write(self, worker: str, state: dict) -> None:
        self._check_dir()
        target = self._file(worker)
        if target.is_symlink():
            raise StateCorrupt("Symlink state file not permitted")
        payload = (json.dumps(state, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        fd, temp_path = tempfile.mkstemp(prefix=f".worker_{worker}.", suffix=".tmp", dir=self.state_dir)
        try:
            if os.name != "nt": os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, target)
            # On Windows no portable O_DIRECTORY; the file itself was flushed.
            if os.name != "nt" and hasattr(os, "O_DIRECTORY"):
                dfd = os.open(self.state_dir, os.O_RDONLY | os.O_DIRECTORY)
                try: os.fsync(dfd)
                finally: os.close(dfd)
        finally:
            if os.path.exists(temp_path): os.unlink(temp_path)

    @staticmethod
    def _blank(worker: str) -> dict:
        return {"version": VERSION, "worker": worker, "status": "UNKNOWN",
                "checkpoint": None, "pending_request_id": None,
                "last_request_id": None, "completed_request_digests": [], "last_observed_at": None,
                "last_five_hour_reset_at": None, "last_weekly_reset_at": None,
                "reason": "No authenticated Excelnt usage adapter configured"}

    def read_status(self, worker: str) -> dict:
        """Read only: safe for status monitoring, never modifies corrupted state."""
        state = self._load(self._worker(worker))
        return json.loads(json.dumps(state or self._blank(worker)))

    def _snapshot(self, worker: str, now: datetime) -> QuotaSnapshot:
        if self.feed is None:
            raise FeedUnavailable("Official authenticated Excelnt usage feed not configured")
        try:
            snap = self.feed.read_snapshot(worker)
        except Exception as exc:
            raise FeedUnavailable("Verified quota feed unavailable") from exc
        if type(snap) is not QuotaSnapshot or snap.worker != worker:
            raise FeedUnavailable("Trusted usage source returned invalid worker/account")
        try:
            observed = _utc_datetime(snap.observed_at)
            five_end = _utc_datetime(snap.five_hour_reset_at)
            week_end = _utc_datetime(snap.weekly_reset_at)
        except GuardBlocked as exc:
            raise FeedUnavailable("Quota observation timestamps invalid") from exc
        if (observed > now + MAX_FUTURE_CLOCK_SKEW or
                now - observed > MAX_OBSERVATION_AGE or five_end <= now or
                week_end <= now or five_end <= observed or week_end <= observed):
            raise FeedUnavailable("Quota observation missing, stale, clock-skewed or expired")
        u5, l5 = _number(snap.five_hour_used), _number(snap.five_hour_limit, allow_zero=False)
        uw, lw = _number(snap.weekly_used), _number(snap.weekly_limit, allow_zero=False)
        if u5 > l5 or uw > lw:
            raise FeedUnavailable("Invalid quota counters")
        if snap.paid_overage_disabled is not True or snap.auto_topup_disabled is not True:
            raise GuardBlocked("Paid balance/overage/top-up safety not confirmed disabled")
        return snap

    def request_preflight(self, worker: str, *, request_id: str,
                          checkpoint: TaskCheckpoint, worst_case_units: float) -> str:
        """Return APPROVED or WARNING only after durable intent; do NOT send request.

        Every caller must enforce an upstream hard spend cap separately. Never permit
        unbounded model/editor tasks whose worst case usage cannot be established.
        """
        worker = self._worker(worker)
        checkpoint.validate()
        if not isinstance(request_id, str) or not TASK_RE.fullmatch(request_id):
            raise GuardBlocked("Invalid request id")
        units = _number(worst_case_units, allow_zero=False)
        with self._lease(worker):
            state = self._load(worker) or self._blank(worker)
            if state["pending_request_id"] or state["status"] == "NEEDS_RECONCILIATION":
                raise GuardBlocked("Ambiguous or running request; never automatically replay")
            digest = hashlib.sha256(request_id.encode("utf-8")).hexdigest()
            if (state["last_request_id"] == request_id or
                    digest in state["completed_request_digests"]):
                raise GuardBlocked("Duplicate request id; no automatic replay")
            if len(state["completed_request_digests"]) >= MAX_COMPLETED_REQUESTS:
                raise GuardBlocked("Durable request journal full; refuse additional model calls")
            if state["checkpoint"] is not None:
                old = state["checkpoint"]
                if old["task_id"] != checkpoint.task_id or old["branch"] != checkpoint.branch:
                    raise GuardBlocked("Task/branch changed; explicit reassignment required")
            state["checkpoint"] = dataclasses.asdict(checkpoint)
            now = self.clock()
            if now.tzinfo is None or now.utcoffset() is None:
                raise GuardBlocked("Clock must be timezone aware")
            try:
                snap = self._snapshot(worker, now)
            except GuardBlocked as exc:
                # Never erase WAITING_FOR_RESET after a feed outage: doing so
                # would allow stale-window resumption when telemetry returns.
                if state["status"] != "WAITING_FOR_RESET":
                    state["status"] = "UNKNOWN" if isinstance(exc, FeedUnavailable) else "AUTH_ERROR"
                state["reason"] = str(exc)
                self._write(worker, state)
                raise
            observed = _utc_datetime(snap.observed_at)
            wend = _utc_datetime(snap.five_hour_reset_at)
            qend = _utc_datetime(snap.weekly_reset_at)
            if state["status"] == "WAITING_FOR_RESET":
                prior = state["last_five_hour_reset_at"]
                prior_week = state["last_weekly_reset_at"]
                if ((prior and prior_week and wend <= _utc_datetime(prior)
                     and qend <= _utc_datetime(prior_week)) or
                        (state["last_observed_at"] and observed <= _utc_datetime(state["last_observed_at"]))):
                    state["reason"] = "Waiting for a genuinely refreshed usage window"
                    self._write(worker, state)
                    raise GuardBlocked("Window reset not verified")
            state["last_observed_at"] = _iso(observed)
            state["last_five_hour_reset_at"] = _iso(wend)
            state["last_weekly_reset_at"] = _iso(qend)
            five_used = snap.five_hour_used / snap.five_hour_limit
            week_used = snap.weekly_used / snap.weekly_limit
            if (five_used >= STOP_RATIO or week_used >= STOP_RATIO or
                    (snap.five_hour_used + units) > STOP_RATIO * snap.five_hour_limit or
                    (snap.weekly_used + units) > STOP_RATIO * snap.weekly_limit):
                state["status"] = "WAITING_FOR_RESET"
                state["reason"] = "At 90% threshold or worst-case request exceeds safe headroom"
                self._write(worker, state)
                raise GuardBlocked("Insufficient verified included allowance")
            warning = five_used >= WARN_RATIO or week_used >= WARN_RATIO
            state["status"] = "IN_FLIGHT"
            state["pending_request_id"] = request_id
            state["reason"] = "Near 80% warning" if warning else None
            # Must persist and fsync BEFORE passing request to external model.
            self._write(worker, state)
            return "WARNING" if warning else "APPROVED"

    def record_outcome(self, worker: str, *, request_id: str, outcome: str) -> None:
        """No retries. An uncertain external result is always manual reconciliation."""
        worker = self._worker(worker)
        if outcome not in {"SUCCESS", "QUOTA_REFUSED", "AUTH_REFUSED", "RATE_LIMITED", "UNKNOWN"}:
            raise GuardBlocked("Invalid outcome")
        with self._lease(worker):
            state = self._load(worker)
            if state is None or state["status"] != "IN_FLIGHT" or state["pending_request_id"] != request_id:
                raise GuardBlocked("No matching in-flight record")
            state["last_request_id"] = request_id
            digest = hashlib.sha256(request_id.encode("utf-8")).hexdigest()
            if (len(state["completed_request_digests"]) >= MAX_COMPLETED_REQUESTS or
                    digest in state["completed_request_digests"]):
                raise GuardBlocked("Completed request journal full or duplicate")
            state["completed_request_digests"].append(digest)
            if outcome == "SUCCESS":
                state["status"] = "READY"
                state["reason"] = None
                state["pending_request_id"] = None
            elif outcome == "QUOTA_REFUSED":
                state["status"] = "WAITING_FOR_RESET"
                state["reason"] = "Provider refused included quota"
                state["pending_request_id"] = None
            elif outcome == "AUTH_REFUSED":
                state["status"] = "AUTH_ERROR"
                state["reason"] = "Provider authentication failed"
                state["pending_request_id"] = None
            else:
                state["status"] = "NEEDS_RECONCILIATION"
                state["reason"] = "Provider throttled or outcome unknown; do not replay"
                # Keep ambiguous request id, even on timeout.
            self._write(worker, state)

    def recover_after_restart(self, worker: str) -> dict:
        """Mark any pending send ambiguous. No implicit release, lease takeover, or replay."""
        worker = self._worker(worker)
        with self._lease(worker):
            state = self._load(worker) or self._blank(worker)
            if state["pending_request_id"] is not None:
                state["status"] = "NEEDS_RECONCILIATION"
                state["reason"] = "Interrupted external call needs human/provider reconciliation"
                self._write(worker, state)
            return json.loads(json.dumps(state))

    def update_checkpoint(self, worker: str, checkpoint: TaskCheckpoint) -> None:
        """Persist public task progress, never credentials, raw prompts, or private paths."""
        worker = self._worker(worker)
        checkpoint.validate()
        with self._lease(worker):
            state = self._load(worker) or self._blank(worker)
            if state["checkpoint"]:
                old = state["checkpoint"]
                if old["task_id"] != checkpoint.task_id or old["branch"] != checkpoint.branch:
                    raise GuardBlocked("Cannot silently reassign another task's checkpoint")
            state["checkpoint"] = dataclasses.asdict(checkpoint)
            self._write(worker, state)

    def redacted_status(self, worker: str) -> dict:
        """Safe-to-publish status; no private paths, quota dollars, account IDs or request ID."""
        state = self.read_status(worker)
        cp = state["checkpoint"] or {}
        return {"worker_alias": worker, "quota_status": state["status"],
                "task_id": cp.get("task_id"), "branch": cp.get("branch"),
                "head_sha": cp.get("head_sha"),
                "observed_at": state["last_observed_at"],
                "reset_at": state["last_five_hour_reset_at"],
                "has_ambiguous_request": state["pending_request_id"] is not None}
