# project-agent-orchestrator

Public, zero-spend-by-default orchestration layer for a cloud-first cinematic R&D agent system.

## Core design
- Free cloud workers perform routine research, coding, experiments, and cloud-render orchestration.
- GPT-6 Astra is reserved for milestone/final review and correction.
- AI video inference stays in the cloud; the user's PC is not a rendering host.
- Media providers are replaceable through provider-neutral adapters.
- Persistent memory records successes, failures, reviewer corrections, provenance, and next actions.
- PC cleanup stays audit-only unless a separate explicit removal approval is given.
- Secrets and private movie assets must never be committed to this public repository.

## Autonomous worker loop
TASK-0002 adds a bounded controller around `Cloud Worker Task`:

1. A queue task starts the free cloud worker.
2. Worker output is checked by deterministic task-specific preflight tests.
3. A successful worker bundle is validated against the task allowlist, promoted to a deterministic candidate branch, and opened as a **draft** pull request.
4. The controller dispatches the dedicated candidate regression workflow.
5. A failed worker run persists structured job/failed-step diagnostics only. If retry is allowed, the exact queue task is revised, the retry is bound to an immutable task SHA/attempt, and `repository_dispatch` starts that bound attempt.
6. At the retry ceiling the controller stops and surfaces `BLOCKED`; it does not loop indefinitely.
7. GPT-6 Astra remains the final reviewer. The controller never merges or enables auto-merge.

`repository_dispatch` is used deliberately for controller-created retries/regressions so the system does not depend on a `GITHUB_TOKEN` push creating a second workflow run. Immutable `worker-context` metadata, dispatch/execution receipt refs, and pre-copy symlink checks prevent task drift, repeated logical execution, and staging-time dereference of external files.

## Zero-cost Astra reviewer bridge
TASK-0003 is building the next layer: a disabled-by-default reviewer bridge intended to remove the manual copy/paste review step without adding separately billed API usage.

The TASK-0003 candidate currently provides only an **offline scaffold**:

- self-hosted Windows reviewer workflow;
- exact `gpt-6-astra` catalog policy;
- zero-extra-spend allowance checks;
- exact repository/PR/SHA/CI-evidence binding;
- structured APPROVED/REJECTED contract;
- bounded rejection-to-correction packaging;
- idempotent result persistence decisions;
- secret redaction before persistence;
- explicit prohibition on ChatGPT conversation scraping, local inference, merge, and auto-merge.

Live Sign in with ChatGPT authorization and live reviewer inference are **not enabled in TASK-0003**. After the scaffold passes deterministic regression and Astra review, a separate activation task will handle the one-time user authorization and real account model-catalog verification.

## Current phase
TASK-0001 and TASK-0002 are approved and merged. TASK-0003 worker generation succeeded, but automatic promotion was blocked by the GitHub App's workflow-file push permission. The exact worker bundle was recovered to a dedicated review branch and recovery-hardened. It is awaiting exact-head regression and Astra review. Paid spend remains disabled.

See `AGENTS.md` for hard rules and `memory/` for current project state.
