# project-agent-orchestrator

Public, zero-spend-by-default orchestration layer for a cloud-first cinematic R&D agent system.

## Core design
- Free cloud workers perform routine research, coding, experiments, and cloud-render orchestration.
- GPT-6 Astra is reserved for milestone/final review and correction.
- AI/video inference stays in the cloud; the user's PC is not a rendering host.
- Media providers are replaceable through provider-neutral adapters.
- Persistent memory records successes, failures, reviewer corrections, provenance, and next actions.
- PC cleanup stays audit-only unless a separate explicit removal approval is given.
- Secrets and private movie assets must never be committed to this public repository.

## Autonomous worker loop
TASK-0002 provides bounded free-worker retries, immutable task binding, safe bundle promotion, draft-PR-only candidate creation, structured failure evidence, and no auto-merge.

## Zero-cost Astra reviewer bridge
TASK-0003 is approved and merged. It provides the offline exact-SHA/snapshot/schema/idempotency review boundary. The reviewer workflow remains disabled until a separate activation gate.

## Sign in with ChatGPT live adapter
TASK-0004 adds a Windows-first, user-initiated Sign in with ChatGPT adapter and CLI while keeping CI offline.

The candidate now follows the current OpenAI OSS SIWC flow:
- first-time authorization uses `dynamic_agent_client`, a stable `ext_agent_host_id`, `agent_name_hint`, PKCE S256, fresh state/nonce, and a `127.0.0.1/.../auth/callback` loopback URI;
- the callback-issued `client_id` is saved and reused for token exchange and later sign-ins;
- token exchange/refresh use `https://auth.openai.com/api/accounts/oauth/token` with no client secret or API key;
- ID tokens require cryptographic signature verification against OpenAI JWKS plus exact issuer/audience/nonce/expiry/subject checks;
- Windows credentials are stored outside the repo behind DPAPI with atomic replacement;
- rotating refresh tokens are serialized;
- model discovery uses the signed-in account and accepts exact `gpt-6-astra` only;
- Responses requests are stateless with `store=false`, `stream=true`, and no conversation-history dependency;
- live reviewer output must still pass the approved TASK-0003 immutable review gate.

TASK-0004 does **not** enable automated inference yet. The real browser consent is a post-merge user action, and a later activation gate must confirm zero-extra-spend usage controls before automatic reviewer requests are enabled.

## Current phase
TASK-0001, TASK-0002, and TASK-0003 are approved and merged.

TASK-0004 free-worker run `37209531438` succeeded. Controller run `37210052342` validated and pushed its candidate branch but GitHub Actions repository policy blocked Actions from creating a pull request. Draft PR #6 was recovered through the trusted GitHub connector and then hardened against current OpenAI SIWC documentation. It is pending Astra review.

Paid API-key fallback, local inference, ChatGPT conversation access, cleanup mutation, and auto-merge remain disabled.

See `AGENTS.md` for hard rules and `memory/` for current project state.
