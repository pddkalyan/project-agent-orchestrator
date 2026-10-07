# TASK-0005 Provider Authorization Review Packet

Creator: ChatGPT  
Model: GPT-5.6 Sol  
Run: 20261007_TASK_0005_PROVIDER_AUTHORIZATION

## Reviewed problem

The previous core could accept a late callback from retry attempt 1 while attempt 2 was running because callbacks named only the job. Provider selection was also advisory: the raw start path did not require persisted proof of zero maximum charge, cloud execution, or adapter identity.

## Independent correction input

- Architecture specified a mandatory persisted authorization between queued/retryable and running.
- State integrity supplied the reproduced stale-attempt callback and generation-epoch ownership model.
- QA supplied crash-window, immutable-history, malformed-checkpoint, and round-trip tests.
- Security required zero maximum charge, enforced charge cap, cloud-only execution, exact adapter identity, stable request keys, and a single submission-receipt gate.

## Implemented offline contract

- Checkpoint schema version 2.
- Version 1 was an unreleased offline prototype; it fails closed because authorization history cannot be truthfully invented during migration.
- Immutable attempt authorization and attempt-history records.
- Monotonic shot generation epoch and exact owner job.
- Exact authorization and provider-request indexes.
- Provider-neutral adapter protocol with no implementation or network call.
- Mandatory matching submission receipt before RUNNING.
- Callback identity includes job, epoch ownership, attempt number, and provider job ID.
- Retry keeps prior attempt evidence byte-for-byte.
- Identical terminal callbacks are idempotent; conflicts and stale callbacks fail closed.
- AUTHORIZED and RUNNING checkpoints round-trip without permitting a second authorization.

## Evidence required before commit

- 49 deterministic unit tests pass.
- Python production and test modules compile.
- JSON Schema parses.
- Runtime checkpoint root and nested attempt/authorization fields match the published schema.
- Independent reviewers re-run stale-attempt, zero-cost, receipt-binding, history, round-trip, and corrupt-index probes.

## Explicitly not activated

No live provider, credentials, media generation, paid fallback, quota consumption, or network adapter is enabled. The quote, authorization, and receipt records are deterministic offline policy claims, not external proof. A future activation increment must provide a trusted adapter registry and issuer boundary, a legitimate adapter with idempotent submit and lookup/reconcile, ensure rejected dispatches invoke the adapter zero times, test the crash-after-remote-accept window, and verify free-only/no-overage behavior using the actual account.

PR #8 remains draft and must not be merged on this packet alone.
