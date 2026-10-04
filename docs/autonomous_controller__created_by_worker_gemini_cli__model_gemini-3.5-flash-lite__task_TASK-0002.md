# Autonomous Controller Architecture & Operations (TASK-0002)

**Original worker:** `worker_gemini_cli` / `gemini-3.5-flash-lite`  
**Recovery hardening:** `bootstrap_chatgpt` / `gpt-5.6-sol`  
**Task:** `TASK-0002`

## Purpose

TASK-0002 provides a bounded autonomous loop around `Cloud Worker Task` while preserving zero paid spend, no local AI/video inference, Astra as reviewer, audit-only PC cleanup, and no automatic merge.

## Immutable task binding

Every worker run now publishes a small `worker-context` artifact before model work. It binds:

- exact queue task path,
- immutable task commit SHA,
- task ID,
- retry attempt,
- triggering event type,
- dispatch key and previous worker-run ID for controller-created retries,
- whether this logical dispatch was actually claimed for execution.

For normal push runs, the task SHA must equal the workflow run head SHA and that exact commit must contain exactly one queue-task change.

For `repository_dispatch` retries, the worker checks out the `task_sha` from the dispatch payload, verifies the task ID/attempt and deterministic dispatch key, and reads that exact immutable task revision. The controller later downloads the same `worker-context` from the triggering run and validates the same binding before any artifact promotion or retry mutation. It does **not** derive a dispatched retry task from the workflow run's moving default-branch head.

This means unrelated commits or another task update can land between retry creation and execution without changing which task/attempt the retry belongs to.

## Duplicate dispatch protection

Controller processing of a source worker run is serialized with a GitHub Actions concurrency group.

Controller retry dispatches use a deterministic key derived from task ID, retry task SHA, retry attempt, and source worker-run ID. The worker claims that key through a dedicated `worker-execution-receipts/<dispatch-key>` ref before model execution. Repeated or concurrent delivery of the same dispatch therefore becomes a no-op worker run with `execute=false` in its immutable context.

The controller separately records completed dispatch delivery using `controller-dispatch-receipts/<dispatch-key>`.

## Failure and dispatch recovery

A failed worker run persists only structured GitHub job/failed-step metadata. Raw failed log bodies are not copied into queue-task commits or controller evidence artifacts.

If retry is allowed, the controller changes only the bound queue task and writes a `controller_retry_state` record with the source worker run, next attempt, and `PENDING_DISPATCH`. The retry commit includes the source-run marker.

If repository dispatch fails after the retry commit was pushed, a later processing of the same source run finds that marker, validates the pending retry record, reuses the same retry SHA and attempt, and retries dispatch. It does not increment the attempt again.

After successful dispatch, a controller receipt ref is written. Repeated processing sees the receipt and does not dispatch again. If a duplicate dispatch is nevertheless delivered, the worker execution-receipt claim prevents a second logical model execution.

## Credential handling

The controller uses allowlisted structured diagnostics rather than arbitrary failed logs.

The helper additionally redacts recognized credential forms including fine-grained `github_pat_...` tokens, classic GitHub tokens, Google/AWS/Slack-like token formats, bearer values, credential labels, multiline credential values following a bare label, and complete private-key blocks. Job and step names are passed through the same conservative sanitization before persistence.

## Worker staging and symlink boundary

Before any successful worker candidate is copied into an artifact bundle, the worker calls the reviewed helper to validate:

- `changed-files.txt` itself,
- every manifest candidate file,
- worker-result metadata,
- every path component from repository root to file.

Any symlink file, symlinked parent directory, non-regular file, unsafe path, or missing file fails before the copy/upload step. Failed-run evidence no longer copies arbitrary worker-changed files.

The controller still validates the downloaded bundle independently and also checks promotion destination path components before copying a candidate into a branch. This keeps the original filesystem type boundary from being lost by `cp`.

## Success path

For a real executed worker run that succeeds, the controller:

1. verifies immutable task binding from `worker-context`,
2. downloads the exact task worker bundle from that workflow run,
3. validates exact manifest-to-bundle correspondence and task allowlist,
4. checks promotion destination components,
5. creates a deterministic candidate branch from the bound task SHA,
6. opens or reuses a **draft** PR,
7. dispatches the candidate regression workflow.

No merge or auto-merge action exists.

## Deterministic verification

The Python suite contains at least 30 offline tests covering path normalization, manifest matching, symlink files/parents/metadata, staging safety, promotion destinations, retry ceilings, immutable task binding, deterministic dispatch keys, retry-resume records, fine-grained tokens, multiline credentials, private-key blocks, bounded diagnostics, and job/step sanitization.

The dedicated `Autonomous Controller Regression` also runs Linux workflow-level fixtures for the four Astra rejection cases:

1. TASK-0002 binding remains correct after an unrelated TASK-0003 commit.
2. synthetic fine-grained, multiline and private-key credentials do not survive sanitization.
3. a previously committed pending retry is rediscovered and reused without another attempt increment.
4. allowed-path symlink, symlink-parent and metadata-symlink fixtures fail before staging/copy, while ordinary files still stage.

Static regression checks also verify the actual worker/controller workflows use the binding, receipt, staging and structured-diagnostics mechanisms.

## Remaining gate

TASK-0002 remains pending Astra review. PR #2 stays draft. Merge is a separate explicit post-review action.
