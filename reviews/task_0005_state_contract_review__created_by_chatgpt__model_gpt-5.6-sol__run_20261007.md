# TASK-0005 State Contract Review Packet

Creator: ChatGPT  
Model: GPT-5.6 Sol  
Run: 20261007_TASK_0005_STATE_CONTRACT

## Independent review lanes

- Architecture reviewed persistence boundaries and missing episode/timeline/archive evidence.
- State integrity reproduced concurrent callback overwrite, corrupt restore acceptance, and incomplete idempotency indexing.
- QA compared all 18 baseline tests against the runtime and published schema.
- Security/spend review challenged zero-cost routing, numeric retry fields, archive proof, and provider isolation.

No reviewer modified the repository. Integration accepted only findings reproducible from head 7907dfe2b023d705ac7e0e033d0d74f7061c0e21.

## Corrections in this packet

- Runtime checkpoint and JSON Schema now use the same versioned field names and map shapes.
- Schema version, USD 0 spend limit, 16:9, 20-minute target, cloud-only inference flag, and Google Drive final-storage contract fail closed on restore.
- Restore checks map keys against embedded IDs, validates job status evidence and retry numbers, requires an exact idempotency bijection, and rejects stale canonical/upscale evidence.
- Only one active generation job can target a shot.
- Generation start clears earlier reviews, canonical state, and upscale authorization.
- Zero-cost quotes use integer USD micros and reject booleans, empty provider IDs, and non-integer zero-like values.

## Deterministic evidence

- Baseline: 18 tests passed at reviewed head.
- Corrected candidate: 32 unit tests, Python compilation, and JSON parsing must pass before commit.
- Added regressions cover schema-root and nested shape agreement, future-version rejection, changed production contract, key/ID mismatch, missing idempotency index entries, negative attempts, impossible success evidence, competing active jobs, approval invalidation, and invalid zero-like quotes.

## Deliberately outstanding

This is not final approval. The next review packets must cover:

1. Mandatory zero-cost provider authorization bound to each attempt, with immutable attempt history and adapter identity.
2. Reconciliation of restored RUNNING cloud jobs without duplicate submission.
3. Controlled Movie Bible entity mutations with revision/digest binding.
4. Episode and scene referential integrity.
5. Timeline/master digests, duration and aspect-ratio QC.
6. Evidence-backed Google Drive archive verification and cleanup authorization.

PR #8 must remain draft until these contracts and their negative tests exist.
