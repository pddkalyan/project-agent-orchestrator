# Project Context: Project Agent Orchestrator

The project is a zero-spend-by-default, cloud-first orchestration system.

Current hard boundaries:

- Free cloud workers implement routine work.
- GPT-6 Astra is reviewer-only and never implements fixes.
- No merge or auto-merge is performed by the reviewer.
- AI/video inference remains cloud-side; the user's PC is not an inference host.
- PC cleanup remains audit-only unless separately approved.
- Paid API billing, automatic credit purchases, and silent paid fallback are forbidden.
- Reviewer decisions bind to an exact candidate SHA and exact CI evidence.
- Rejections become bounded correction packages for the existing free-worker/controller loop.

## ChatGPT conversation boundary

The reviewer bridge does **not** access, scrape, export, or claim access to the user's existing ChatGPT conversations or ChatGPT memory. Continuity is supplied only by versioned repository reviewer context, safety rules, review packets, and review history.

## TASK-0003 scope

TASK-0003 is an offline, disabled-by-default scaffold. A future post-review setup may connect a host-local user-authorized ChatGPT-plan session, but no live authorization, token storage implementation, or live reviewer request is enabled in this candidate.
