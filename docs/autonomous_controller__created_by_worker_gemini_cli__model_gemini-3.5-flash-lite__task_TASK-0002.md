# Autonomous Controller Architecture & Operations (TASK-0002)

**Original worker:** `worker_gemini_cli` / `gemini-3.5-flash-lite`  
**Recovery hardening after bounded worker retries:** `bootstrap_chatgpt` / `gpt-5.6-sol`  
**Task:** `TASK-0002`

## Purpose

TASK-0002 builds the bounded orchestration layer between `Cloud Worker Task` and the Astra review gate. It removes routine manual babysitting while preserving fail-closed repository boundaries, zero spend, no local AI inference, and no automatic merge.

## Event and trust boundary

`.github/workflows/autonomous_controller.yml` listens only to completed runs of `Cloud Worker Task`. The controller job proceeds only when the triggering workflow repository exactly equals the current repository, the triggering branch equals the repository default branch, the triggering event is `push` or the controller's `repository_dispatch` retry, and the triggering run is not skipped.

Before any write, the workflow verifies the exact triggering `head_sha` is on the trusted default-branch lineage. It then uses `git diff-tree` on that immutable commit and requires **exactly one** `tasks/queue/*.json` change. The source task is read with `git show <sha>:<path>` rather than from an ambiguous current working tree.

Permissions are deliberately scoped to `actions: read`, `contents: write`, and `pull-requests: write`.

## Success path

The controller downloads the exact `worker-bundle-<TASK_ID>` artifact from the triggering worker run using `run-id` and `github-token`. The Python helper validates `changed-files.txt` against the actual bundle and the task's exact `allowed_paths`.

Validation rejects absolute or Windows-drive paths, backslash/non-normalized paths and `..` traversal, symlink files or directories, duplicate manifest entries, manifest entries absent from the bundle, candidate files absent from the manifest, and any candidate path outside `task.allowed_paths`.

After validation, the controller creates a deterministic branch `candidate/<TASK_ID>/worker-<RUN_ID>` from the exact worker source commit, copies only validated files, commits with a source-run idempotency marker, pushes the branch, and creates or reuses a **draft** PR. Existing non-draft PR state is never silently changed.

The controller then sends a `repository_dispatch` event named `controller_candidate_regression`. The default-branch regression workflow checks out the candidate ref and independently runs the unit/static regression suite. No auto-merge action exists.

## Failure and bounded retry path

For a failed worker run, the controller obtains GitHub Actions job/failed-step metadata and captures failed logs only into a temporary file. The helper sanitizes secret-like lines and bounds the excerpt before anything is persisted.

The retry decision uses `retry_attempt` and `max_controller_retries` from the exact current task. Below the limit, only that queue task may change; the controller commits an idempotency marker, pushes the default branch, then emits `repository_dispatch: worker_task_retry` with the exact task path. At the limit, no task-file mutation or worker dispatch occurs. The controller writes `BLOCKED` evidence to the job summary/artifact and stops.

A prior `[controller-run:<RUN_ID>]` marker or a moved task attempt causes duplicate/stale retry delivery to stop without another mutation.

## Why repository_dispatch is used

Controller-created `GITHUB_TOKEN` pushes are not relied upon to recursively start another workflow. Retries and candidate regression are explicitly requested with `repository_dispatch`, while the normal human/bootstrap task path continues to support `push`.

## Deterministic verification

The Python unit suite covers allowlist acceptance/rejection, absolute/traversal/non-normalized paths, relative and directory symlinks, missing/duplicate manifests, extra/missing bundle files, metadata allowance, retry below/at limit, invalid retry values, idempotency keys, secret sanitization/bounds, and failed-job summarization.

The separate `Autonomous Controller Regression` workflow executes the tests on the exact candidate ref and statically checks controller source guards, cross-run artifact inputs, fail-closed writes, repository-dispatch retries/regression, absence of merge/auto-merge behavior, and preserved zero-spend/local-PC boundaries.

## Remaining gate

TASK-0002 is not self-approving. GPT-6 Astra must review the exact tested candidate. Merge remains an explicit post-review action.
