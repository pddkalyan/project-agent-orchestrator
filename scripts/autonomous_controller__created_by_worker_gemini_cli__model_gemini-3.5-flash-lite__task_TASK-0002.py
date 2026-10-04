#!/usr/bin/env python3
"""
Autonomous Controller Helper Script for TASK-0002.

Original worker: worker_gemini_cli / gemini-3.5-flash-lite
Recovery hardening: bootstrap_chatgpt / gpt-5.6-sol

Deterministic/offline safety helpers used by the reviewed GitHub workflows.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Iterable

DIRECT_SECRET_PATTERNS = [
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b", re.IGNORECASE),
    re.compile(r"\bAIzaSy[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b", re.IGNORECASE),
    re.compile(r"bearer\s+\S+", re.IGNORECASE),
]
CREDENTIAL_LABEL = re.compile(
    r"\b(api[_ -]?key|access[_ -]?key|token|password|passwd|secret|authorization|"
    r"private[_ -]?key|client[_ -]?secret|credential)\b",
    re.IGNORECASE,
)
PRIVATE_KEY_BEGIN = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
PRIVATE_KEY_END = re.compile(r"-----END [A-Z0-9 ]*PRIVATE KEY-----")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
TASK_FILE_RE = re.compile(r"^tasks/queue/[A-Za-z0-9._-]+\.json$")

METADATA_NAMES = {"changed-files.txt", "git-status.txt"}
METADATA_PATTERNS = (
    re.compile(r"^worker_result_TASK-[A-Za-z0-9._-]+\.json$"),
    re.compile(r"^worker_patch_TASK-[A-Za-z0-9._-]+\.patch$"),
)


def _has_direct_secret(value: str) -> bool:
    return any(pattern.search(value) for pattern in DIRECT_SECRET_PATTERNS)


def sanitize_structured_field(value: str, max_chars: int = 300) -> str:
    """Redact an entire structured metadata field if any credential marker appears.

    Job and step names are attacker/worker-controlled strings. If a field is
    multiline or uses YAML-style block syntax, trying to preserve individual
    continuation lines is unsafe. A credential label/token/private-key marker
    anywhere in the field therefore redacts the whole field.
    """
    text = str(value)
    if (
        _has_direct_secret(text)
        or CREDENTIAL_LABEL.search(text)
        or PRIVATE_KEY_BEGIN.search(text)
        or PRIVATE_KEY_END.search(text)
    ):
        return "[REDACTED-POTENTIAL-SECRET]"
    return sanitize_text(text, max_lines=4, max_chars=max_chars)


def sanitize_log_line(line: str) -> str:
    """Backward-compatible alias for structured metadata sanitization."""
    return sanitize_structured_field(line, max_chars=500)


def sanitize_text(text: str, max_lines: int = 80, max_chars: int = 8000) -> str:
    """Return bounded text with credentials and private-key blocks removed.

    A credential-only label such as api_key: causes the next non-empty line
    to be redacted as well. The controller persists structured diagnostics
    rather than arbitrary failed log bodies.
    """
    if max_lines < 1 or max_chars < 1:
        raise ValueError("max_lines and max_chars must be positive")

    output: list[str] = []
    redact_next_value = False
    in_private_key = False

    for raw in text.splitlines():
        if len(output) >= max_lines:
            break
        line = raw

        if in_private_key:
            output.append("[REDACTED-POTENTIAL-SECRET]")
            if PRIVATE_KEY_END.search(line):
                in_private_key = False
            continue

        if PRIVATE_KEY_BEGIN.search(line):
            output.append("[REDACTED-POTENTIAL-SECRET]")
            in_private_key = not bool(PRIVATE_KEY_END.search(line))
            redact_next_value = False
            continue

        if redact_next_value:
            if not line.strip():
                output.append("")
                continue
            output.append("[REDACTED-POTENTIAL-SECRET]")
            redact_next_value = False
            continue

        if _has_direct_secret(line):
            output.append("[REDACTED-POTENTIAL-SECRET]")
            continue

        if CREDENTIAL_LABEL.search(line):
            output.append("[REDACTED-POTENTIAL-SECRET]")
            if re.search(r"[:=]\s*$", line):
                redact_next_value = True
            continue

        output.append(line)

    result = "\n".join(output)
    if len(result) > max_chars:
        marker = "\n[TRUNCATED-SANITIZED]"
        result = result[: max(0, max_chars - len(marker))] + marker
    return result


def normalize_repo_path(path_str: str) -> str | None:
    """Normalize a repository-relative POSIX path; reject ambiguity."""
    if not isinstance(path_str, str) or not path_str or path_str.strip() != path_str:
        return None
    if "\x00" in path_str or "\\" in path_str or path_str.startswith("/"):
        return None
    if re.match(r"^[A-Za-z]:", path_str):
        return None
    if path_str.endswith("/") or "//" in path_str:
        return None
    pure = PurePosixPath(path_str)
    if not pure.parts or any(part in ("", ".", "..") for part in pure.parts):
        return None
    normalized = pure.as_posix()
    return normalized if normalized == path_str else None


def validate_path(path_str: str, allowed_paths: Iterable[str]) -> bool:
    normalized = normalize_repo_path(path_str)
    if normalized is None:
        return False
    allowed = {p for p in allowed_paths if normalize_repo_path(p) == p}
    return normalized in allowed


def _repo_path(root: Path, repo_path: str) -> Path:
    return root.joinpath(*PurePosixPath(repo_path).parts)


def _is_metadata(rel_posix: str) -> bool:
    if rel_posix in METADATA_NAMES:
        return True
    if "/" in rel_posix:
        return False
    return any(pattern.fullmatch(rel_posix) for pattern in METADATA_PATTERNS)


def _first_symlink_component(root: Path, candidate: Path) -> Path | None:
    try:
        parts = candidate.relative_to(root).parts
    except ValueError:
        return candidate
    current = root
    if current.is_symlink():
        return current
    for part in parts:
        current = current / part
        if current.is_symlink():
            return current
    return None


def validate_regular_repo_file(root_dir: str | Path, repo_path: str) -> tuple[bool, str | None]:
    """Reject symlink components before staging or copying a file."""
    normalized = normalize_repo_path(repo_path)
    if normalized is None:
        return False, f"Unsafe repository path: {repo_path}"
    root = Path(root_dir)
    if root.is_symlink() or not root.exists() or not root.is_dir():
        return False, "Staging root must be an existing non-symlink directory"
    candidate = _repo_path(root, normalized)
    if _first_symlink_component(root, candidate) is not None:
        return False, f"Symlink component is forbidden: {normalized}"
    try:
        mode = candidate.lstat().st_mode
    except OSError as exc:
        return False, f"Unable to stat staging file {normalized}: {exc}"
    if not stat.S_ISREG(mode):
        return False, f"Staging path is not a regular file: {normalized}"
    return True, None


def validate_staging_set(root_dir: str, manifest_path: str, metadata_paths: Iterable[str] = ()) -> dict:
    """Validate manifest, candidate files and metadata before worker staging."""
    ok, error = validate_regular_repo_file(root_dir, manifest_path)
    if not ok:
        return {"valid": False, "error": error}
    manifest = _repo_path(Path(root_dir), manifest_path)
    entries = [line.strip() for line in manifest.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(entries) != len(set(entries)):
        return {"valid": False, "error": "Duplicate staging manifest entries"}

    checked: list[str] = []
    for rel in [*entries, *metadata_paths]:
        ok, error = validate_regular_repo_file(root_dir, rel)
        if not ok:
            return {"valid": False, "error": error}
        checked.append(rel)
    return {"valid": True, "files": entries, "checked": checked}



def stage_worker_bundle(root_dir: str, manifest_path: str, metadata_path: str, bundle_dir: str) -> dict:
    """Validate and copy a success bundle using this immutable trusted helper.

    Validation and copy happen in one process so a worker-replaced helper in the
    mutable checkout can never take over between those operations.
    """
    result = validate_staging_set(root_dir, manifest_path, [metadata_path])
    if not result.get("valid"):
        return result

    root = Path(root_dir)
    manifest = _repo_path(root, manifest_path)
    metadata = _repo_path(root, metadata_path)
    bundle = Path(bundle_dir)

    if bundle.is_symlink() or bundle.exists():
        return {"valid": False, "error": "Bundle destination must not already exist"}
    if bundle.parent.is_symlink():
        return {"valid": False, "error": "Bundle destination parent must not be a symlink"}

    bundle.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(manifest, bundle / "changed-files.txt", follow_symlinks=False)
    shutil.copyfile(metadata, bundle / Path(metadata_path).name, follow_symlinks=False)

    for rel in result["files"]:
        source = _repo_path(root, rel)
        dest = _repo_path(bundle, rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest, follow_symlinks=False)

    return {"valid": True, "files": list(result["files"]), "bundle_dir": str(bundle)}


def context_execute_value(context_data: dict) -> str:
    """Return true/false text for a strictly boolean execute field."""
    value = context_data.get("execute")
    if not isinstance(value, bool):
        raise ValueError("execute must be boolean")
    return "true" if value else "false"



def write_worker_failure_evidence(output_dir: str, task_id: str, task_sha: str, run_id: str) -> dict:
    """Write safe failure metadata without reading worker-controlled artifacts."""
    if not re.fullmatch(r"TASK-[0-9]+", task_id):
        return {"valid": False, "error": "invalid task_id"}
    if not SHA_RE.fullmatch(task_sha):
        return {"valid": False, "error": "invalid task_sha"}
    if not str(run_id).isdigit():
        return {"valid": False, "error": "invalid run_id"}

    out = Path(output_dir)
    if out.is_symlink():
        return {"valid": False, "error": "failure evidence output must not be a symlink"}
    out.mkdir(parents=True, exist_ok=True)
    payload = {
        "task_id": task_id,
        "task_sha": task_sha,
        "worker_run_id": str(run_id),
        "note": "Worker pipeline failed; raw worker-controlled files are intentionally omitted from failure evidence.",
    }
    target = out / "failure-metadata.json"
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return {"valid": True, "path": str(target)}


def promote_validated_bundle(bundle_dir: str, validation_json: str, destination_root: str) -> dict:
    """Copy already-validated bundle files using this immutable trusted helper.

    The trusted helper validates every destination before copying any file.
    Candidate-provided helper replacements are copied only as inert bytes and
    are never imported or executed during this operation.
    """
    bundle = Path(bundle_dir)
    destination = Path(destination_root)
    validation = _load_json(validation_json)

    if validation.get("valid") is not True or not isinstance(validation.get("files"), list):
        return {"valid": False, "error": "validation JSON is not an approved file list"}
    if bundle.is_symlink() or not bundle.is_dir():
        return {"valid": False, "error": "bundle directory is invalid"}
    if destination.is_symlink() or not destination.is_dir():
        return {"valid": False, "error": "destination root is invalid"}

    files = validation["files"]
    for rel in files:
        normalized = normalize_repo_path(rel)
        if normalized is None:
            return {"valid": False, "error": f"unsafe promotion path: {rel}"}
        source = _repo_path(bundle, normalized)
        if _first_symlink_component(bundle, source) is not None or not source.is_file():
            return {"valid": False, "error": f"unsafe promotion source: {normalized}"}
        check = validate_promotion_destination(str(destination), normalized)
        if not check.get("valid"):
            return check

    copied: list[str] = []
    for rel in files:
        source = _repo_path(bundle, rel)
        target = _repo_path(destination, rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target, follow_symlinks=False)
        copied.append(rel)
    return {"valid": True, "files": copied}



def validate_promotion_destination(root_dir: str, repo_path: str) -> dict:
    """Reject symlink and non-directory components before promotion copy."""
    normalized = normalize_repo_path(repo_path)
    if normalized is None:
        return {"valid": False, "error": f"Unsafe promotion path: {repo_path}"}
    root = Path(root_dir)
    if root.is_symlink() or not root.exists() or not root.is_dir():
        return {"valid": False, "error": "Promotion root must be an existing non-symlink directory"}
    current = root
    parts = PurePosixPath(normalized).parts
    for index, part in enumerate(parts):
        current = current / part
        if current.is_symlink():
            return {"valid": False, "error": f"Symlink promotion component is forbidden: {normalized}"}
        if current.exists() and index < len(parts) - 1 and not current.is_dir():
            return {"valid": False, "error": f"Non-directory promotion parent is forbidden: {normalized}"}
    return {"valid": True, "path": normalized}


def validate_bundle(bundle_dir: str, allowed_paths: Iterable[str]) -> dict:
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

    entries = [line.strip() for line in manifest.read_text(encoding="utf-8").splitlines() if line.strip()]
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
        full = _repo_path(bundle, normalized)
        if _first_symlink_component(bundle, full) is not None:
            return {"valid": False, "error": f"Symlinks are not allowed in worker bundle: {normalized}"}
        if not full.exists() or not full.is_file():
            return {"valid": False, "error": f"Manifest entry missing from bundle files: {normalized}"}
        manifest_set.add(normalized)

    actual_candidates: set[str] = set()
    for item in bundle.rglob("*"):
        rel = item.relative_to(bundle).as_posix()
        if item.is_symlink():
            return {"valid": False, "error": f"Symlinks are not allowed in worker bundle: {rel}"}
        if item.is_dir() or _is_metadata(rel):
            continue
        normalized = normalize_repo_path(rel)
        if normalized is None:
            return {"valid": False, "error": f"Unsafe bundle path: {rel}"}
        if normalized in actual_candidates:
            return {"valid": False, "error": f"Duplicate/conflicting bundle path: {normalized}"}
        actual_candidates.add(normalized)

    missing = sorted(manifest_set - actual_candidates)
    extra = sorted(actual_candidates - manifest_set)
    if missing:
        return {"valid": False, "error": f"Manifest entry missing from bundle files: {missing[0]}"}
    if extra:
        return {"valid": False, "error": f"Extra bundle file not present in changed-files.txt manifest: {extra[0]}"}
    return {"valid": True, "files": sorted(manifest_set)}


def check_retry_decision(task_data: dict) -> dict:
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
    payload = f"{task_id}\n{workflow_run_id}\n{action}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:24]


def compute_dispatch_key(task_id: str, task_sha: str, retry_attempt: int, source_worker_run_id: str | int) -> str:
    if not re.fullmatch(r"TASK-[0-9]+", str(task_id)):
        raise ValueError("invalid task_id")
    if not SHA_RE.fullmatch(str(task_sha)):
        raise ValueError("invalid task_sha")
    if not isinstance(retry_attempt, int) or isinstance(retry_attempt, bool) or retry_attempt < 0:
        raise ValueError("invalid retry_attempt")
    source = str(source_worker_run_id)
    if not source.isdigit():
        raise ValueError("invalid source_worker_run_id")
    payload = f"{task_id}\n{task_sha}\n{retry_attempt}\n{source}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:32]


def validate_task_binding(task_data: dict, task_file: str, task_sha: str, expected_task_id: str,
                          expected_attempt: int, dispatch_key: str | None = None,
                          source_worker_run_id: str | int | None = None) -> dict:
    if not TASK_FILE_RE.fullmatch(task_file):
        return {"valid": False, "error": "invalid task_file"}
    if not SHA_RE.fullmatch(task_sha):
        return {"valid": False, "error": "invalid task_sha"}
    if task_data.get("task_id") != expected_task_id:
        return {"valid": False, "error": "task_id binding mismatch"}
    if task_data.get("retry_attempt", 0) != expected_attempt:
        return {"valid": False, "error": "retry_attempt binding mismatch"}
    if dispatch_key is not None:
        if source_worker_run_id is None:
            return {"valid": False, "error": "dispatch binding missing source worker run"}
        try:
            expected_key = compute_dispatch_key(expected_task_id, task_sha, expected_attempt, source_worker_run_id)
        except ValueError as exc:
            return {"valid": False, "error": str(exc)}
        if dispatch_key != expected_key:
            return {"valid": False, "error": "dispatch_key binding mismatch"}
    return {"valid": True}


def validate_retry_record(task_data: dict, source_worker_run_id: str | int, expected_attempt: int) -> dict:
    source = str(source_worker_run_id)
    state = task_data.get("controller_retry_state")
    if not isinstance(state, dict):
        return {"valid": False, "error": "missing controller_retry_state"}
    if str(state.get("source_worker_run_id")) != source:
        return {"valid": False, "error": "retry source run mismatch"}
    if state.get("attempt") != expected_attempt:
        return {"valid": False, "error": "retry attempt mismatch"}
    if state.get("dispatch_status") != "PENDING_DISPATCH":
        return {"valid": False, "error": "retry dispatch state mismatch"}
    if task_data.get("retry_attempt") != expected_attempt:
        return {"valid": False, "error": "task retry_attempt mismatch"}
    return {"valid": True}



def claim_worker_execution(repository: str, dispatch_key: str, task_sha: str, gh_bin: str = "gh") -> dict:
    """Atomically claim one logical worker execution using a GitHub ref.

    This is the exact production receipt-claim algorithm used by worker_task.yml.
    GitHub ref creation is the atomic compare-and-create boundary: one claimant
    creates the ref; concurrent/repeated claimants observe the same immutable
    task SHA and return execute=false. A conflicting existing SHA fails closed.
    """
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("invalid repository")
    if not re.fullmatch(r"[0-9a-f]{32}", dispatch_key):
        raise ValueError("invalid dispatch_key")
    if not SHA_RE.fullmatch(task_sha):
        raise ValueError("invalid task_sha")
    if not gh_bin:
        raise ValueError("invalid gh binary")

    api_ref = f"heads/worker-execution-receipts/{dispatch_key}"
    full_ref = f"refs/heads/worker-execution-receipts/{dispatch_key}"
    endpoint = f"repos/{repository}/git/ref/{api_ref}"

    def run(args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [gh_bin, *args],
            text=True,
            capture_output=True,
            check=False,
        )

    def observed_sha(proc: subprocess.CompletedProcess[str]) -> str:
        try:
            payload = json.loads(proc.stdout)
            sha = payload["object"]["sha"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ValueError("invalid execution receipt response") from exc
        if not SHA_RE.fullmatch(str(sha)):
            raise ValueError("invalid execution receipt sha")
        return str(sha)

    existing = run(["api", endpoint])
    if existing.returncode == 0:
        sha = observed_sha(existing)
        if sha != task_sha:
            raise ValueError("execution receipt points at unexpected sha")
        return {"valid": True, "execute": False, "receipt_sha": sha}

    created = run([
        "api", "--method", "POST",
        f"repos/{repository}/git/refs",
        "-f", f"ref={full_ref}",
        "-f", f"sha={task_sha}",
    ])
    if created.returncode == 0:
        return {"valid": True, "execute": True, "receipt_sha": task_sha}

    # A concurrent claimant may have won between our GET and POST.
    existing = run(["api", endpoint])
    if existing.returncode != 0:
        raise ValueError("unable to create or observe execution receipt")
    sha = observed_sha(existing)
    if sha != task_sha:
        raise ValueError("concurrent execution receipt mismatch")
    return {"valid": True, "execute": False, "receipt_sha": sha}



def summarize_jobs(jobs_payload: dict) -> dict:
    """Persist only allowlisted structured diagnostics, never raw log bodies."""
    result = []
    for job in jobs_payload.get("jobs", []):
        if job.get("conclusion") not in ("failure", "cancelled", "timed_out", "action_required"):
            continue
        steps = []
        for step in job.get("steps") or []:
            if step.get("conclusion") in ("failure", "cancelled", "timed_out", "action_required"):
                steps.append({
                    "name": sanitize_structured_field(str(step.get("name", "")), max_chars=300),
                    "conclusion": step.get("conclusion"),
                    "number": step.get("number"),
                })
        result.append({
            "name": sanitize_structured_field(str(job.get("name", "")), max_chars=300),
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
            result = validate_bundle(sys.argv[2], _load_json(sys.argv[3]).get("allowed_paths", []))
        elif cmd == "validate-staging":
            if len(sys.argv) < 4:
                raise ValueError("validate-staging requires root, manifest and optional metadata")
            result = validate_staging_set(sys.argv[2], sys.argv[3], sys.argv[4:])
        elif cmd == "validate-destination":
            result = validate_promotion_destination(sys.argv[2], sys.argv[3])
        elif cmd == "promote-bundle":
            if len(sys.argv) != 5:
                raise ValueError("promote-bundle requires bundle_dir validation_json destination_root")
            result = promote_validated_bundle(sys.argv[2], sys.argv[3], sys.argv[4])
        elif cmd == "write-worker-failure-evidence":
            if len(sys.argv) != 6:
                raise ValueError("write-worker-failure-evidence requires output_dir task_id task_sha run_id")
            result = write_worker_failure_evidence(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5])
        elif cmd == "stage-bundle":
            if len(sys.argv) != 6:
                raise ValueError("stage-bundle requires root manifest metadata bundle_dir")
            result = stage_worker_bundle(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5])
        elif cmd == "context-execute":
            if len(sys.argv) != 3:
                raise ValueError("context-execute requires context_json")
            print(context_execute_value(_load_json(sys.argv[2])))
            return 0
        elif cmd == "check-retry":
            result = check_retry_decision(_load_json(sys.argv[2]))
        elif cmd == "sanitize":
            text = Path(sys.argv[2]).read_text(encoding="utf-8", errors="replace") if len(sys.argv) >= 3 else sys.stdin.read()
            print(sanitize_text(text))
            return 0
        elif cmd == "idempotency-key":
            print(compute_idempotency_key(sys.argv[2], sys.argv[3], sys.argv[4]))
            return 0
        elif cmd == "dispatch-key":
            print(compute_dispatch_key(sys.argv[2], sys.argv[3], int(sys.argv[4]), sys.argv[5]))
            return 0
        elif cmd == "verify-binding":
            if len(sys.argv) not in (7, 9):
                raise ValueError("verify-binding requires task_json task_file task_sha task_id attempt and optional dispatch_key source_run")
            key = sys.argv[7] if len(sys.argv) == 9 else None
            source = sys.argv[8] if len(sys.argv) == 9 else None
            result = validate_task_binding(_load_json(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5], int(sys.argv[6]), key, source)
        elif cmd == "validate-retry-record":
            result = validate_retry_record(_load_json(sys.argv[2]), sys.argv[3], int(sys.argv[4]))
        elif cmd == "summarize-jobs":
            result = summarize_jobs(_load_json(sys.argv[2]))
        elif cmd == "claim-worker-execution":
            if len(sys.argv) not in (5, 6):
                raise ValueError("claim-worker-execution requires repository dispatch_key task_sha [gh_bin]")
            result = claim_worker_execution(
                sys.argv[2],
                sys.argv[3],
                sys.argv[4],
                sys.argv[5] if len(sys.argv) == 6 else "gh",
            )
        else:
            raise ValueError(f"Unknown command: {cmd}")

        print(json.dumps(result, indent=2))
        if isinstance(result, dict) and (result.get("valid") is False or result.get("action") == "ERROR"):
            return 2
        return 0
    except (IndexError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": sanitize_text(str(exc), max_lines=4, max_chars=500)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
