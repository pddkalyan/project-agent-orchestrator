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

Only one queued, running, or retryable generation job may own a shot. Starting that job invalidates prior reviews, canonical state, and upscale authorization before any new callback can be accepted. This prevents an older concurrent callback from overwriting a newer result. A future adapter increment will add immutable per-attempt provider authorization receipts and restart reconciliation for in-flight cloud jobs.

## Storage and completion

Google Drive is the intended permanent master store. An episode reaches completed only after every shot is canonical, final QC passes, and an authorized future Drive adapter verifies that the final master is readable and integral. Temporary assets cannot be deleted before verification.

## Activation boundary

This core performs no provider calls, media generation, Drive upload, purchase, local inference, merge, auto-merge, or TASK-0004/PR #6 change.
