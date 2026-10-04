#!/usr/bin/env python3
"""
Autonomous Controller Helper Script for TASK-0002.

Original worker: worker_gemini_cli / gemini-3.5-flash-lite
Recovery hardening: bootstrap_chatgpt / gpt-5.6-sol

This helper performs deterministic, offline validation/sanitization only. GitHub
network mutations remain explicit in the reviewed workflow.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Iterable

SECRET_PATTERNS = [
    re.compile(r"api[_-]?key", re.IGNORECASE),
    re.compile(r"\btoken\b", re.IGNORECASE),
    re.compile(r"password", re.IGNORECASE),
    re.compile(r"secret", re.IGNORECASE),
    re.compile(r"bearer\s+\S+", re.IGNORECASE),
    re.compile(r"authorization", re.IGNORECASE),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b", re.IGNORECASE),
    re.compile(r"\bAIzaSy[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
]

METADATA_NAMES = {"changed-files.txt", "git-status.txt"}
METADATA_PATTERNS = (
    re.compile(r"^worker_result_TASK-[A-Za-z0-9._-]+\.json$"),
    re.compile(r"^worker_patch_TASK-[A-Za-z0-9._-]+\.patch$"),
)


def sanitize_log_line(line: str) -> str:
    """Replace an entire line when it looks credential-bearing."""
    for pattern in SECRET_PATTERNS:
        if pattern.search(line):
            return "[REDACTED-POTENTIAL-SECRET]"
    if re.search(r"(key|token|password|secret)\s*[:=]\s*\S+", line, re.IGNORECASE):
        return "[REDACTED-POTENTIAL-SECRET]"
    return line


def sanitize_text(text: str, max_lines: int = 80, max_chars: int = 8000) -> str:
    """Return a bounded sanitized excerpt suitable for retry metadata."""
    if max_lines < 1 or max_chars < 1:
        raise ValueError("max_lines and max_chars must be positive")
    lines = [sanitize_log_line(line) for line in text.splitlines()[:max_lines]]
    result = "\n".join(lines)
    if len(result) > max_chars:
        result = result[: max_chars - 24] + "\n[TRUNCATED-SANITIZED]"
    return result


def normalize_repo_path(path_str: str) -> str | None:
    """Normalize a repository-relative POSIX path; reject ambiguity/traversal."""
    if not isinstance(path_str, str) or not path_str or path_str.strip() != path_str:
        return None
    if "\x00" in path_str or "\\" in path_str or path_str.startswith("/"):
        return None
    if re.match(r"^[A-Za-z]:", path_str):
        return None
    if path_str.endswith("/") or "//" in path_str:
        return None
    pure = PurePosixPath(path_str)
    parts = pure.parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        return None
    normalized = pure.as_posix()
    if normalized != path_str:
        return None
    return normalized


def validate_path(path_str: str, allowed_paths: Iterable[str]) -> bool:
    """Return True only for an exact allowed, unambiguous repo-relative path."""
    normalized = normalize_repo_path(path_str)
    if normalized is None:
        return False
    allowed = {p for p in allowed_paths if normalize_repo_path(p) == p}
    return normalized in allowed


def _bundle_path(bundle: Path, repo_path: str) -> Path:
    return bundle.joinpath(*PurePosixPath(repo_path).parts)


def _is_metadata(rel_posix: str) -> bool:
    if rel_posix in METADATA_NAMES:
        return True
    if "/" in rel_posix:
        return False
    return any(pattern.fullmatch(rel_posix) for pattern in METADATA_PATTERNS)


def _first_symlink_component(bundle: Path, candidate: Path) -> Path | None:
    """Return a symlink component at/below bundle without following it."""
    try:
        rel_parts = candidate.relative_to(bundle).parts
    except ValueError:
        return candidate
    current = bundle
    for part in rel_parts:
        current = current / part
        if current.is_symlink():
            return current
    return None


def validate_bundle(bundle_dir: str, allowed_paths: Iterable[str]) -> dict:
    """Validate exact manifest-to-bundle correspondence and reject symlinks."""
    bundle = Path(bundle_dir)
    if bundle.is_symlink():
        return {"valid": False, "error": "Bundle directory itself must not be a symlink"}
    if not bundle.exists() or not bundle.is_dir():
        return {"valid": False, "error": "Bundle directory does not exist or is not a directory"}

    manifest = bundle / "changed-files.txt"
    if manifest.is_symlink():
        return {"valid": False, "error": "changed-files.txt must not be a symlink"}
    if not manifest.exists() or not manifest.is_file():
        return {"valid": False, "error": "Missing changed-files.txt manifest"}

    try:
        entries = [line.strip() for line in manifest.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, UnicodeError) as exc:
        return {"valid": False, "error": f"Failed to read manifest: {exc}"}

    if len(entries) != len(set(entries)):
        return {"valid": False, "error": "Duplicate entries found in changed-files.txt manifest"}

    allowed = list(allowed_paths)
    manifest_set: set[str] = set()
    for entry in entries:
        normalized = normalize_repo_path(entry)
        if normalized is None:
            return {"valid": False, "error": f"Unsafe absolute/traversal/non-normalized manifest path: {entry}"}
        if not validate_path(normalized, allowed):
            return {"valid": False, "error": f"Path rejected by task allowed_paths: {entry}"}
        full = _bundle_path(bundle, normalized)
        symlink = _first_symlink_component(bundle, full)
        if symlink is not None:
            return {"valid": False, "error": f"Symlinks are not allowed in worker bundle: {normalized}"}
        if not full.exists() or not full.is_file():
            return {"valid": False, "error": f"Manifest entry missing from bundle files: {normalized}"}
        manifest_set.add(normalized)

    actual_candidates: set[str] = set()
    for item in bundle.rglob("*"):
        rel = item.relative_to(bundle).as_posix()
        if item.is_symlink():
            return {"valid": False, "error": f"Symlinks are not allowed in worker bundle: {rel}"}
        if item.is_dir():
            continue
        if _is_metadata(rel):
            continue
        normalized = normalize_repo_path(rel)
        if normalized is None:
            return {"valid": False, "error": f"Unsafe bundle path: {rel}"}
        if normalized in actual_candidates:
            return {"valid": False, "error": f"Duplicate/conflicting bundle path: {normalized}"}
        actual_candidates.add(normalized)

    missing = sorted(manifest_set - actual_candidates)
    if missing:
        return {"valid": False, "error": f"Manifest entry missing from bundle files: {missing[0]}"}
    extra = sorted(actual_candidates - manifest_set)
    if extra:
        return {"valid": False, "error": f"Extra bundle file not present in changed-files.txt manifest: {extra[0]}"}

    return {"valid": True, "files": sorted(manifest_set)}


def check_retry_decision(task_data: dict) -> dict:
    """Return RETRY only below the configured retry ceiling; otherwise BLOCKED."""
    retry_attempt = task_data.get("retry_attempt", 0)
    max_retries = task_data.get("max_controller_retries", 3)
    if not isinstance(retry_attempt, int) or isinstance(retry_attempt, bool) or retry_attempt < 0:
        return {"action": "ERROR", "error": "retry_attempt must be a non-negative integer"}
    if not isinstance(max_retries, int) or isinstance(max_retries, bool) or max_retries < 0:
        return {"action": "ERROR", "error": "max_controller_retries must be a non-negative integer"}
    if retry_attempt < max_retries:
        return {"action": "RETRY", "next_attempt": retry_attempt + 1, "max_retries": max_retries}
    return {"action": "BLOCKED", "attempt": retry_attempt, "max_retries": max_retries}


def compute_idempotency_key(task_id: str, workflow_run_id: str | int, action: str) -> str:
    """Return a deterministic non-secret key for a controller side effect."""
    payload = f"{task_id}\n{workflow_run_id}\n{action}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:24]


def summarize_jobs(jobs_payload: dict) -> dict:
    """Extract compact failed-job/step metadata without log bodies."""
    result = []
    for job in jobs_payload.get("jobs", []):
        if job.get("conclusion") not in ("failure", "cancelled", "timed_out", "action_required"):
            continue
        steps = []
        for step in job.get("steps") or []:
            if step.get("conclusion") in ("failure", "cancelled", "timed_out", "action_required"):
                steps.append({
                    "name": sanitize_log_line(str(step.get("name", ""))),
                    "conclusion": step.get("conclusion"),
                    "number": step.get("number"),
                })
        result.append({
            "name": sanitize_log_line(str(job.get("name", ""))),
            "conclusion": job.get("conclusion"),
            "steps": steps,
        })
    return {"failed_jobs": result}


def _load_json(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: autonomous_controller.py <command> [args]", file=sys.stderr)
        return 1
    cmd = sys.argv[1]
    try:
        if cmd == "validate-bundle":
            if len(sys.argv) != 4:
                raise ValueError("validate-bundle requires <bundle_dir> <task_json_path>")
            task = _load_json(sys.argv[3])
            result = validate_bundle(sys.argv[2], task.get("allowed_paths", []))
            print(json.dumps(result, indent=2))
            return 0 if result.get("valid") else 2
        if cmd == "check-retry":
            if len(sys.argv) != 3:
                raise ValueError("check-retry requires <task_json_path>")
            result = check_retry_decision(_load_json(sys.argv[2]))
            print(json.dumps(result, indent=2))
            return 0 if result.get("action") != "ERROR" else 2
        if cmd == "sanitize":
            text = Path(sys.argv[2]).read_text(encoding="utf-8", errors="replace") if len(sys.argv) >= 3 else sys.stdin.read()
            print(sanitize_text(text))
            return 0
        if cmd == "idempotency-key":
            if len(sys.argv) != 5:
                raise ValueError("idempotency-key requires <task_id> <workflow_run_id> <action>")
            print(compute_idempotency_key(sys.argv[2], sys.argv[3], sys.argv[4]))
            return 0
        if cmd == "summarize-jobs":
            if len(sys.argv) != 3:
                raise ValueError("summarize-jobs requires <jobs_json_path>")
            print(json.dumps(summarize_jobs(_load_json(sys.argv[2])), separators=(",", ":")))
            return 0
        raise ValueError(f"Unknown command: {cmd}")
    except Exception as exc:
        print(json.dumps({"ok": False, "error": sanitize_log_line(str(exc))}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
