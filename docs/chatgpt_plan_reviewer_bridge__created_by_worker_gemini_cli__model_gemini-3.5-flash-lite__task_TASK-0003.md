# ChatGPT Plan Reviewer Bridge Architecture (TASK-0003)

## Objective
Provide a zero-extra-spend, disabled-by-default reviewer bridge scaffold that can later use user-authorized ChatGPT-plan access to review exact PR SHAs automatically. Tested entirely offline without live authorization or paid API calls.

## Key Design Principles
1. **Zero Spend ($0):** No paid API billing, no credit purchases, no fallback to paid models.
2. **Disabled by Default:** The workflow is configured with `if: false` and runs only on self-hosted runners when a host-local user authorization profile is present.
3. **Exact-Model Policy:** Requires exact `gpt-6-astra` model identifier from an authorized catalog; otherwise fails closed with `BLOCKED_NO_ASTRA`.
4. **Auth & Plan Allowance Checking:** Fails closed with `BLOCKED_AUTH_REQUIRED` or `BLOCKED_PLAN_ALLOWANCE` if authorization or plan quota is missing.
5. **Exact-SHA & CI Evidence Binding:** Validates exact PR SHAs to prevent drift.
6. **Review-Only:** Reviewer outputs `APPROVED` or `REJECTED` verdicts but never implements fixes or performs merges. Rejections yield a deterministic bounded correction package.

## Future One-Time User Authorization
After successful offline validation and review of this scaffold, the operator can perform a one-time host-local authorization ("Continue with ChatGPT") on their self-hosted environment to enable automated plan-based PR reviews.
