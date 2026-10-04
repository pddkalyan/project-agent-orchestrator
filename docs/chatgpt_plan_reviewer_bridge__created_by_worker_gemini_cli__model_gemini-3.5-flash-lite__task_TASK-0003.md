# ChatGPT-Plan Reviewer Bridge Architecture (TASK-0003)

## Scope

TASK-0003 builds a **zero-extra-spend, disabled-by-default scaffold** for replacing the current manual Astra copy/paste review step. It is intentionally safe to merge before any live authorization exists.

This task does **not** perform OAuth, store tokens, call OpenAI, access existing ChatGPT conversations, run local AI inference, or merge code.

## Offline decision boundary

Before a future live reviewer call can be accepted, the bridge requires all of the following:

1. a host-local authorization profile is active;
2. the account-authorized model catalog contains the exact identifier `gpt-6-astra`;
3. allowance metadata explicitly represents included-plan, zero-extra-spend usage with separately billed credits disabled;
4. an independently supplied immutable review snapshot exactly matches repository, PR, base SHA, candidate SHA, changed-file manifest, reviewer-context version, and the complete required CI set (workflow, run ID, candidate SHA, PASS result, digest);
5. the reviewer response repeats the exact candidate SHA and the canonical review-snapshot digest before its verdict is accepted;
6. the reviewer response satisfies the strict structured verdict contract.

Any auth/model/allowance/evidence failure becomes a BLOCKED state rather than a code-review verdict.

## Review results

`APPROVED` records exact-SHA approval only. Merge and auto-merge stay disabled.

`REJECTED` requires structured findings and is deterministically converted into a bounded correction package for the existing free-worker/controller loop. At the correction retry ceiling the package becomes `BLOCKED_RETRY_LIMIT`.

Duplicate delivery is scoped to the canonical immutable review-identity digest plus the canonical result digest. Exact repeats become `DUPLICATE_NOOP`; a different result for the same immutable review identity becomes explicit `CONFLICT_BLOCKED`, never a silent duplicate.

## Secret boundary

Access tokens, refresh tokens, authorization codes, PKCE material, cookies, API keys, or other credentials must never be committed, printed, uploaded as artifacts, or included in review history. Future live authorization state must stay in OS-protected host-local storage outside the repository.

## Self-hosted reviewer workflow

The reviewer job is self-hosted Windows only and currently has `if: false`. The regression workflow runs on GitHub-hosted Ubuntu but performs only offline deterministic checks.

## Future one-time setup after approval

After TASK-0003 itself is reviewed and approved:

1. configure the dedicated self-hosted Windows reviewer runner;
2. perform the one-time user interaction for **Continue with ChatGPT** using the reviewed adapter implementation from a later activation step;
3. keep authorization state outside the repository;
4. query the account-authorized model catalog;
5. enable live reviewer mode only if exact `gpt-6-astra` is available and the zero-extra-spend plan boundary is satisfied.

Until that separate activation step is approved, the bridge remains offline and disabled.

## Astra Review #1 corrections

The first TASK-0003 Astra review rejected four gaps. The corrected scaffold now:

- compares the complete runtime review request against an independently supplied immutable snapshot, including exact changed-file and CI sets;
- requires the returned verdict to bind both `reviewed_sha` and `review_snapshot_digest`;
- redacts complete credential blocks and secret-bearing mapping keys, while authorization metadata uses an exact three-field allowlist;
- rejects non-string/blank finding fields before sanitization and aligns all emitted BLOCKED/APPROVED/REJECTED shapes with the published schema;
- scopes idempotency to full immutable review identity plus result content and exposes `CONFLICT_BLOCKED` for contradictory results.
