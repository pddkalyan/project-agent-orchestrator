# ChatGPT-Plan Reviewer Bridge Architecture (TASK-0003)

## Scope

TASK-0003 builds a **zero-extra-spend, disabled-by-default scaffold** for replacing the current manual Astra copy/paste review step. It is intentionally safe to merge before any live authorization exists.

This task does **not** perform OAuth, store tokens, call OpenAI, access existing ChatGPT conversations, run local AI inference, or merge code.

## Offline decision boundary

Before a future live reviewer call can be accepted, the bridge requires all of the following:

1. a host-local authorization profile is active;
2. the account-authorized model catalog contains the exact identifier `gpt-6-astra`;
3. allowance metadata explicitly represents included-plan, zero-extra-spend usage with separately billed credits disabled;
4. repository, PR, base SHA, candidate SHA, changed-file manifest, reviewer-context version, and required CI run IDs/digests are bound to the same exact candidate SHA;
5. the reviewer response satisfies the structured verdict contract.

Any auth/model/allowance/evidence failure becomes a BLOCKED state rather than a code-review verdict.

## Review results

`APPROVED` records exact-SHA approval only. Merge and auto-merge stay disabled.

`REJECTED` requires structured findings and is deterministically converted into a bounded correction package for the existing free-worker/controller loop. At the correction retry ceiling the package becomes `BLOCKED_RETRY_LIMIT`.

Duplicate delivery is handled with a deterministic idempotency key so a result is persisted only once.

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
