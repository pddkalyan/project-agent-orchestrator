# Movie Studio Core Architecture — TASK-0005

Creator: ChatGPT  
Model: GPT-5.6 Sol  
Run: 20261006_TASK_0005_RESUMABLE_LEDGER

## Purpose

Define a durable, cloud-first orchestration contract for cinematic web-series episodes while remaining provider-neutral and zero-spend-by-default.

## Production contract

- Aspect ratio is 16:9 throughout.
- Episode runtime targets approximately 20 minutes; the final timeline owns exact runtime.
- Spend fails closed at USD 0.00 unless the user explicitly changes it.
- Local AI/video inference is prohibited.
- Video providers are replaceable adapters; movie logic contains no provider assumptions.
- Secrets and private movie assets are prohibited from this public repository.

## Deterministic authority and review

The Executive Producer is the deterministic authority. Specialist agents propose work and scores but cannot canonicalize assets independently. Applicable Visual, Continuity, Audio, and Technical QA evidence must bind to the exact asset version. A different generated version clears old reviews, canonical state, and upscale authorization. Upscaling follows semantic approval.

## Durable Movie Bible

The Movie Bible persists revisioned story rules, character identity, voice identity, locations, and continuity facts outside conversational memory. Episode, scene, and shot ledgers refer to this durable baseline. A later increment will add reference-asset digests and explicit council evidence without weakening these bindings.

Entity and fact map writes now advance the Bible revision and SHA-256 content digest. Entity values are copied on write and exposed as read-only mappings, so nested edits cannot silently alter the persisted identity. Batch map updates validate before committing one revision. An explicit entity update can compare an expected revision. Frozen plans reject Bible writes; after any generation job exists, Bible and referenced shot structure remain fixed because historical jobs have no independent full-content snapshot. A later historical-snapshot contract would be needed to safely permit post-generation Bible edits.

Before any meaningful Bible change with continuity bindings, the ledger requires an explicit unfrozen new plan revision and preflights every bound shot for safe replacement. Revision-1 bindings, generated assets, reviews, ownership, epochs, or generation history reject the change before maps, revision, digest, or history change. Exact map assignment/update and an empty clear remain idempotent. After a permitted change, replace every stale binding before validating or freezing the plan.

Continuity bindings persist the exact Bible content digest as well as its revision. A new plan revision can replace a binding for an untouched shot after the Bible changes; stale bindings fail validation. The old empty revision-1 Bible checkpoint may receive a deterministic empty digest on restore. A populated or revisioned checkpoint lacking digest/history, and any old job or binding checkpoint lacking the new exact-content evidence, fails closed instead of receiving invented history.

## Resumable generation jobs

Generation jobs store stable job IDs, idempotency keys, input fingerprints, provider job identifiers, output versions, attempt counts, retry ceilings, and failure reasons. Resubmission returns the existing job only when shot, fingerprint, and retry policy match exactly. Key reuse with changed input is rejected.

Each job also records a deterministic digest of its actual shot structure, ShotPlan, continuity binding, and Bible content. The authorization and provider request key carry this digest alongside the caller's input fingerprint. Authorization, receipt replay, provider callbacks, and checkpoint restore reject changed content; terminal job history remains verifiable because the referenced content cannot be publicly rewritten. These digests are deterministic integrity records, not authenticated signatures against an attacker who can rewrite an entire checkpoint.

Retryable failures return a shot to planned state. Exhaustion blocks it. Restored ledgers fail closed when a job references an unknown shot, an idempotency index references a missing job, or attempts exceed the ceiling.

The versioned checkpoint envelope and the checked-in JSON Schema are one contract. It includes the immutable zero-spend and production-policy constants, Movie Bible, shot map, generation-job map, and exact idempotency index. Unknown schema versions, changed production constants, key/embedded-ID mismatches, impossible job evidence, stale canonical evidence, and non-bijective indexes fail closed.

Only one queued, authorized, running, or retryable generation job may own a shot. Authorization advances a monotonic shot generation epoch and invalidates prior candidate/review/canonical state before provider submission.

Every provider attempt now requires a persisted, immutable authorization bound to the exact job, shot, epoch, attempt number, provider, adapter/version, quote, input fingerprint, stable provider request key, zero maximum cost, cloud execution, and an enforced charge cap. A matching provider submission receipt is the only path into RUNNING. Retry attempts append frozen history instead of overwriting earlier provider IDs, failures, or outputs. In this offline core, quotes, authorizations, and receipts are deterministic policy records—not external proof. A trusted adapter registry and issuer boundary are mandatory before live activation.

Checkpoint schema version 2 intentionally replaces the unreleased version-1 prototype. Version-1 checkpoints fail closed rather than receiving fabricated authorization evidence; no live production was activated on version 1.

Provider callbacks carry attempt number and provider job ID. Late events from an earlier retry or old generation epoch fail without changing state; identical terminal callbacks are idempotent and conflicting duplicates are rejected. Restored AUTHORIZED state retains its stable request key for provider-side lookup. Restored RUNNING state accepts only an exact idempotent replay of its recorded receipt and rejects conflicting submission evidence.

This is still an offline contract. No provider adapter is registered or called, no credentials are persisted, and live activation remains blocked until a legitimate adapter implements idempotent submit/reconcile and passes counting-spy plus real account tests.

## Storage and completion

Google Drive is the intended permanent master store. An episode reaches completed only after every shot is canonical, final QC passes, and an authorized future Drive adapter verifies that the final master is readable and integral. Temporary assets cannot be deleted before verification.

## Activation boundary

This core performs no provider calls, media generation, Drive upload, purchase, local inference, merge, auto-merge, or TASK-0004/PR #6 change.
