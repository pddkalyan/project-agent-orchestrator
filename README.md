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
5. A failed worker run is summarized and sanitized. If the retry ceiling has not been reached, only the exact queue task is revised and `repository_dispatch` starts the next worker attempt.
6. At the retry ceiling the controller stops and surfaces `BLOCKED`; it does not loop indefinitely.
7. GPT-6 Astra remains the final reviewer. The controller never merges or enables auto-merge.

`repository_dispatch` is used deliberately for controller-created retries/regressions so the system does not depend on a `GITHUB_TOKEN` push creating a second workflow run.

## Current phase
TASK-0001 is approved and merged. TASK-0002 reached its bounded worker retry ceiling and was recovered without another model call; the hardened controller candidate is awaiting deterministic regression and Astra review. Paid spend remains disabled.

See `AGENTS.md` for hard rules and `memory/` for current project state.
