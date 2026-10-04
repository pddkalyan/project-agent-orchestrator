#!/usr/bin/env bash
set -euo pipefail

: "${TASK_ID:?TASK_ID is required}"
: "${TASK_FILE:?TASK_FILE is required}"
: "${TASK_SHA:?TASK_SHA is required}"
: "${SOURCE_ATTEMPT:?SOURCE_ATTEMPT is required}"
: "${SOURCE_RUN_ID:?SOURCE_RUN_ID is required}"
: "${SOURCE_CONCLUSION:?SOURCE_CONCLUSION is required}"
: "${DEFAULT_BRANCH:?DEFAULT_BRANCH is required}"
: "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"
: "${TRUSTED_HELPER:?TRUSTED_HELPER is required}"
: "${EVIDENCE_OUT:?EVIDENCE_OUT is required}"

GH_BIN="${GH_BIN:-gh}"
MARKER="[controller-run:${SOURCE_RUN_ID}]"
TMP_ROOT="${RUNNER_TEMP:-/tmp}/task0002-controller-${SOURCE_RUN_ID}"
mkdir -p "$TMP_ROOT"
JOBS_JSON="$TMP_ROOT/jobs.json"
JOB_SUMMARY="$TMP_ROOT/job-summary.json"
EVIDENCE_JSON="$TMP_ROOT/controller_failure_evidence.json"
CURRENT_TASK="$TMP_ROOT/current-task.json"
RETRY_TASK="$TMP_ROOT/retry-task.json"
DISPATCH_JSON="$TMP_ROOT/worker-dispatch.json"

append_summary() {
  if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
    printf '%s\n' "$*" >> "$GITHUB_STEP_SUMMARY"
  fi
}

copy_evidence() {
  mkdir -p "$(dirname "$EVIDENCE_OUT")"
  cp -- "$EVIDENCE_JSON" "$EVIDENCE_OUT"
}

git fetch --no-tags origin "$DEFAULT_BRANCH"

# Persist only allowlisted structured GitHub job/failed-step metadata.
"$GH_BIN" api "repos/$GITHUB_REPOSITORY/actions/runs/$SOURCE_RUN_ID/jobs?per_page=100" > "$JOBS_JSON"
python "$TRUSTED_HELPER" summarize-jobs "$JOBS_JSON" > "$JOB_SUMMARY"

python - "$JOB_SUMMARY" "$EVIDENCE_JSON" "$SOURCE_RUN_ID" "$SOURCE_CONCLUSION" "$TASK_ID" "$TASK_SHA" "$SOURCE_ATTEMPT" <<'PY'
import json
import sys
from pathlib import Path

summary_path, output_path, source_run, conclusion, task_id, task_sha, attempt = sys.argv[1:]
jobs = json.loads(Path(summary_path).read_text(encoding="utf-8"))
evidence = {
    "source_worker_run_id": source_run,
    "source_conclusion": conclusion,
    "task_id": task_id,
    "bound_task_sha": task_sha,
    "bound_retry_attempt": int(attempt),
    "failed_jobs": jobs.get("failed_jobs", []),
}
Path(output_path).write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
PY
copy_evidence

NEXT_ATTEMPT="$((SOURCE_ATTEMPT + 1))"
RETRY_SHA="$(git log "origin/$DEFAULT_BRANCH" --fixed-strings --grep="$MARKER" -n 1 --format=%H || true)"

if [[ -n "$RETRY_SHA" ]]; then
  # Resume the exact already-committed pending retry after a prior dispatch
  # failure. Never increment the attempt again.
  git show "$RETRY_SHA:$TASK_FILE" > "$RETRY_TASK"
  python "$TRUSTED_HELPER" validate-retry-record "$RETRY_TASK" "$SOURCE_RUN_ID" "$NEXT_ATTEMPT" >/dev/null
else
  git show "origin/$DEFAULT_BRANCH:$TASK_FILE" > "$CURRENT_TASK"
  CURRENT_ID="$(jq -er '.task_id' "$CURRENT_TASK")"
  CURRENT_ATTEMPT="$(jq -er '.retry_attempt // 0' "$CURRENT_TASK")"
  if [[ "$CURRENT_ID" != "$TASK_ID" || "$CURRENT_ATTEMPT" != "$SOURCE_ATTEMPT" ]]; then
    echo "Task state moved since bound worker run; refusing stale retry mutation." >&2
    exit 40
  fi

  DECISION="$(python "$TRUSTED_HELPER" check-retry "$CURRENT_TASK")"
  ACTION="$(jq -r '.action' <<<"$DECISION")"
  if [[ "$ACTION" == "BLOCKED" ]]; then
    append_summary "### $TASK_ID BLOCKED"
    append_summary "Retry ceiling reached for source worker run $SOURCE_RUN_ID; no retry commit or dispatch was created."
    exit 0
  fi
  if [[ "$ACTION" != "RETRY" ]]; then
    echo "Retry decision failed closed: $DECISION" >&2
    exit 41
  fi

  NEXT_ATTEMPT="$(jq -r '.next_attempt' <<<"$DECISION")"
  REASON="$(python - "$EVIDENCE_JSON" <<'PY'
import json
import sys
from pathlib import Path

ev = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
jobs = "; ".join(
    f"{j.get('name')}:{j.get('conclusion')}" +
    (" steps=" + ",".join(
        f"{s.get('name')}:{s.get('conclusion')}" for s in j.get("steps", [])
    ) if j.get("steps") else "")
    for j in ev.get("failed_jobs", [])
)
print(
    f"Autonomous controller retry after Cloud Worker Task run "
    f"{ev['source_worker_run_id']} concluded {ev['source_conclusion']}. "
    f"Structured diagnostics: {jobs}".strip()
)
PY
)"

  git checkout -B "$DEFAULT_BRANCH" "origin/$DEFAULT_BRANCH"
  python - "$TASK_FILE" "$NEXT_ATTEMPT" "$REASON" "$SOURCE_RUN_ID" <<'PY'
import json
import sys
from pathlib import Path

path, attempt, reason, run_id = sys.argv[1:]
p = Path(path)
data = json.loads(p.read_text(encoding="utf-8"))
data["retry_attempt"] = int(attempt)
data["retry_reason"] = reason
data["controller_retry_state"] = {
    "source_worker_run_id": str(run_id),
    "attempt": int(attempt),
    "dispatch_status": "PENDING_DISPATCH",
}
evidence = data.setdefault("controller_retry_evidence", [])
marker = {"source_worker_run_id": str(run_id), "next_attempt": int(attempt)}
if marker not in evidence:
    evidence.append(marker)
p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
PY

  mapfile -t CHANGED < <(git status --porcelain -uall | awk '{print $2}')
  if [[ "${#CHANGED[@]}" -ne 1 || "${CHANGED[0]}" != "$TASK_FILE" ]]; then
    echo "Retry mutation touched unexpected paths: ${CHANGED[*]}" >&2
    exit 42
  fi

  git config user.name "Project Agent Autonomous Controller"
  git config user.email "autonomous-controller@users.noreply.github.com"
  git add -- "$TASK_FILE"
  git commit -m "controller: retry $TASK_ID attempt $NEXT_ATTEMPT $MARKER"
  git push origin "HEAD:refs/heads/$DEFAULT_BRANCH"
  RETRY_SHA="$(git rev-parse HEAD)"
fi

DISPATCH_KEY="$(python "$TRUSTED_HELPER" dispatch-key "$TASK_ID" "$RETRY_SHA" "$NEXT_ATTEMPT" "$SOURCE_RUN_ID")"
RECEIPT_BRANCH="controller-dispatch-receipts/$DISPATCH_KEY"
if RECEIPT="$(git ls-remote --heads origin "$RECEIPT_BRANCH")" && [[ -n "$RECEIPT" ]]; then
  RECEIPT_SHA="$(awk '{print $1}' <<<"$RECEIPT")"
  if [[ "$RECEIPT_SHA" != "$RETRY_SHA" ]]; then
    echo "Dispatch receipt SHA mismatch" >&2
    exit 43
  fi
  append_summary "Retry dispatch already completed for $DISPATCH_KEY; no duplicate dispatch."
  exit 0
fi

jq -n \
  --arg task_file "$TASK_FILE" \
  --arg task_sha "$RETRY_SHA" \
  --arg task_id "$TASK_ID" \
  --argjson retry_attempt "$NEXT_ATTEMPT" \
  --arg source_worker_run_id "$SOURCE_RUN_ID" \
  --arg dispatch_key "$DISPATCH_KEY" \
  '{event_type:"worker_task_retry",client_payload:{task_file:$task_file,task_sha:$task_sha,task_id:$task_id,retry_attempt:$retry_attempt,source_worker_run_id:$source_worker_run_id,dispatch_key:$dispatch_key}}' \
  > "$DISPATCH_JSON"

# Failure here deliberately leaves the retry commit marked PENDING_DISPATCH and
# creates no controller receipt. Reprocessing resumes this exact retry.
"$GH_BIN" api --method POST "repos/$GITHUB_REPOSITORY/dispatches" --input "$DISPATCH_JSON"

# Receipt is written only after a successful dispatch. If receipt creation
# itself fails, a repeated dispatch is harmless because the worker independently
# claims the same immutable dispatch key before model execution.
git push origin "$RETRY_SHA:refs/heads/$RECEIPT_BRANCH"
append_summary "Queued bounded retry $NEXT_ATTEMPT for $TASK_ID with immutable binding $DISPATCH_KEY."
