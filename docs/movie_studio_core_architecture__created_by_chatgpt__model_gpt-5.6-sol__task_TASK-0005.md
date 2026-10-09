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

## Resumable generation jobs

Generation jobs store stable job IDs, idempotency keys, input fingerprints, provider job identifiers, output versions, attempt counts, retry ceilings, and failure reasons. Resubmission returns the existing job only when shot, fingerprint, and retry policy match exactly. Key reuse with changed input is rejected.

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
