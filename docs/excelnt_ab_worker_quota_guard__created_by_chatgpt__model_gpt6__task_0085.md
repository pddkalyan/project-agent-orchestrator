# Excelnt A/B worker quota guard — Issue #85

Created by ChatGPT / GPT-6, TASK-0085, 2026-10-11. Incremental spend: USD 0.

## Implemented, not yet connected

This review candidate implements an **offline**, Python-standard-library, fail-closed quota guard with independent workers A and B, durable checkpoints and request intents, isolated worker leases, refusal/reset handling, and anti-replay. It makes NO model or network requests. No credentials, private paths or API tokens are included.

**Excelnt is not yet integrated. Neither worker is running.** The authenticated Excelnt Usage/Billing pages and VS Code extension are not accessible to this code. The provided Excelnt documentation advertises editor sign-in, models including a GPT 6 Astra-labelled option, shared plan counters and refusal at exhausted plan/bonus/paid balance. It does NOT establish an official programmatic usage endpoint, hard server spend limit, upstream model identity or concurrent-two-account terms.

A screenshot is not a live authenticated usage feed. Do not use another similarly named API provider's endpoints or invent Excelnt URLs. DO NOT automatically install scripts from remote websites or request paid top-ups.

## New owner-provided billing evidence — 2026-10-11

The **Excelnt Power billing page** supplied by the owner explicitly says that **Balance funds API usage once the plan allowance runs out**. It shows account balance **$600.00** available for API usage, separately from the included **$600 / five hours** and **$4,200 / week** counters, with **0 top-ups**, **$0 paid in total**, and a **$240 / 30-day** active Power plan until 2026-11-10.

**Critical distinction:** zero top-ups and zero paid-in-total do not mean that the $600 account balance cannot be automatically depleted; unused plan allowance, prepaid available balance and top-up purchases are different. The exact source/origin of that balance is unknown. The billing page also says *Plans are paid from balance*, so future plan renewal/switching may debit funds; automatic renewal/paid fallback controls are not documented by the supplied view.

Therefore `QuotaSnapshot.balance_fallback_disabled` is **independently required to be exactly True**, in addition to `paid_overage_disabled=True` and `auto_topup_disabled=True`. Its default is **False** (DENY). No adapter may set it True merely because Top-ups is 0 or the five-hour usage is 0. Only a verifiable official provider-enforced setting/account configuration preventing API spend from available balance can establish it. A client-side 90% cutoff is defense-in-depth, not a provider-enforced spending guarantee. We require **no deduction from existing $600 account balance**, not merely no new card charge.

Do not publish owner login/email, balances by user identity, tokens or keys in task logs. There is no proof of the official zero-balance-spend switch, so both Excelnt workers remain offline and unconnected.

## Per-worker algorithm

- Each of workers A and B requires its **own** future reviewed TrustedQuotaFeed adapter bound to its own account, its own private application-state directory, separate Git worktree/branch and nonoverlapping task assignment. No automatic switching between accounts or opening more accounts.
- Immediately before EVERY inference request, obtain a verified authenticated observation for that account, at most 5 minutes old, with accurate five-hour and weekly usage windows. Confirm paid overages, **balance-funded fallback**, and automatic top-ups are all independently and provider-enforced disabled. Missing/invalid/stale data **blocks** the request.
- The caller must supply a proven worst-case upper bound in the provider's same accounting units and the provider must offer an actual server-enforced nonpaid spending limit. No unbounded editor/model requests.
- At 80% of either allowance, return WARNING; at 90%, or if the next worst-case request could cross 90%, retain the task checkpoint and stop. On a quota refusal, WAITING_FOR_RESET with the current window identity retained.
- A genuinely newer authenticated reset window plus a fresh allowance can unblock the SAME worker and task. An unchanged window, fake low balance, telemetry outage or expired observation never unblocks waiting work.
- Before any caller sends a model request, the guard fsyncs an IN_FLIGHT intent to disk and records a unique request ID. Definitive success updates the durable journal. A crash, network timeout, unidentified 429 or uncertain provider result becomes NEEDS_RECONCILIATION and is never replayed blindly.
- Durable state writes use temporary file fsync plus atomic replace (directory fsync where supported). Existing corrupt state is never erased. A non-expiring cross-process lock intentionally fails closed after an interrupted owner; manual verification is required to remove an orphaned lock.
- A bounded 1024-entry SHA-256 request-ID journal prevents reuse of prior completed request IDs. Journal saturation stops work until an explicitly reviewed migration/archival process exists.
- A checkpoint records a task ID, branch, commit SHA, summarized tests and next action; it does not preserve the actual source changes. Workers must keep actual edits non-destructively in their own worktrees and publish safe tested commits. Never delete/stash/reset the user's existing Windows Movie Studio worktree.
- The redacted_status result is suitable for an explicitly approved future GitHub reporter. It never includes API keys, account email, dollar balance, private worktree paths or the raw request identifier.

## Local test (no Excelnt account necessary)

Use the repository root. This runs only deterministic fake usage observations.

    python -m unittest discover -s tests -p "test_excelnt_worker_quota_guard__created_by_chatgpt__model_gpt6__task_0085.py" -v
    python -m py_compile scripts/excelnt_worker_quota_guard__created_by_chatgpt__model_gpt6__task_0085.py

The 33 deterministic test methods cover both accounts; 80/90% thresholds; five-hour/weekly limits; request worst-case headroom; refusals; reset freshness; telemetry outage during wait; malformed counters; 403/429/clock problems; corrupt checkpoint; fsync/replace failure; independent leases; duplicate request IDs; journal saturation; ambiguous restarts; task reassignment refusal; and redacted status. They make zero network calls.

## Mandatory go-live blockers

1. Official provider model list, billing terms, concurrent account terms, extension publisher and VSIX SHA-256 need verification. No account rotation to circumvent restrictions.
2. Design/review a read-only authenticated Excelnt usage adapter using **actual documented** API or supported local extension events; no scraping authenticated pages, no embedded credentials or local private IDs in public issues.
3. Verify that API requests CANNOT spend any existing paid/prepaid account balance after included allowance runs out; also disable automatic top-ups and paid overages, PLUS require a hard per-request provider cap. If the VS Code extension offers no supported per-call guard/interceptor, this library cannot control its automatic usage and should remain OFFLINE ONLY.
4. Run the exact candidate offline suite in GitHub CI on Linux and Windows. After independent review and user consent, run one harmless included-allowance request on worker A, confirm request logs, actual model identity and usage decrement. Then authorize a tiny isolated task. Repeat for B separately if provider terms permit.
5. Connect only verified sanitized observations to the existing hourly Movie Studio supervisor; this scheduler cannot itself stop a request mid-execution. The worker-side synchronous guard is required.

Never claim a green offline test activates Excelnt, funds ChatGPT Work/Astra, approves Phase 2, or authorizes merges or Phase 3. No paid credentials, accounts, provider calls or media generation were used by this change.
