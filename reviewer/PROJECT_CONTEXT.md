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

## TASK-0003 & TASK-0004 scope

TASK-0003 provides the reviewer bridge scaffold. TASK-0004 implements the live Sign in with ChatGPT adapter and one-time Windows authorization CLI (`chatgpt_plan_auth_cli`), keeping CI offline and adhering strictly to zero extra spend.
