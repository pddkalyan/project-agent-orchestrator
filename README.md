# project-agent-orchestrator

Public, zero-spend-by-default orchestration layer for a cloud-first cinematic R&D agent system.

## Core design
- Free cloud workers perform routine research, coding, experiments, and cloud-render orchestration.
- GPT-6 Astra is reserved for milestone/final review and correction.
- AI video inference stays in the cloud; the user's PC is not a rendering host.
- Media providers are replaceable through provider-neutral adapters.
- Persistent memory records successes, failures, reviewer corrections, provenance, and next actions.
- PC cleanup starts in audit-only mode.
- Secrets and private movie assets must never be committed to this public repository.

## Current phase
Bootstrap. Add the `GEMINI_API_KEY` repository secret, then run the manual read-only smoke workflow.

See `AGENTS.md` for hard rules and `memory/` for project state.
