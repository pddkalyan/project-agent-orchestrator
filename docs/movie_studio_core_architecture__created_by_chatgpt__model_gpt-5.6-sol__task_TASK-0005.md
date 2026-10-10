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

Entity and fact map writes now advance the Bible revision and SHA-256 content digest. Namespace construction and restore require mappings, without list/pair coercion or duplicate-ID collapse. All keys and string values must encode as UTF-8. Each write computes the complete proposed content digest and next revision/history before publishing any state, so rejected encoding or digest computation leaves the existing checkpoint intact. Entity values are copied on write and exposed as read-only mappings, so nested edits cannot silently alter the persisted identity. Batch map updates validate before committing one revision. An explicit entity update can compare an expected revision. Frozen plans reject Bible writes; after any generation job exists, Bible and referenced shot structure remain fixed because historical jobs have no independent full-content snapshot. A later historical-snapshot contract would be needed to safely permit post-generation Bible edits.

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


## Guarded episode and scene lifecycle — package 1

New episodes and scenes start PLANNED. Lifecycle fields and histories reject direct assignment; public constructors cannot fabricate advanced states. `transition_episode` permits PLANNED → IN_PRODUCTION → ASSEMBLING → FINAL_QC, or BLOCKED from an intermediate state. `transition_scene` permits PLANNED → GENERATING → REVIEW → APPROVED, or BLOCKED before approval. Exact transition replay is idempotent. Episode production requires nonempty scene-bound shots; scene generation requires an episode in production. Scene review requires generated assets and no active jobs. Scene approval, episode assembly and entry into final QC require current canonical shots with applicable QA. Entering FINAL_QC does not claim that master-level QC has passed.

Package 4 now enables ARCHIVING and COMPLETED only through typed timeline/master QC and archive verification evidence. The deprecated `episode_can_complete` boolean API always returns False: caller booleans cannot establish completion evidence. This intentional safety change removes the former false certification path.

`resume_episode` and `resume_scene` recover BLOCKED state only to the immediately preceding status, rechecking its current evidence; generating scenes require episode recovery first. Resume replay is idempotent. Prior-status tuples persist every transition including BLOCKED and resume; restore rejects missing, skipped or impossible history. These histories are deterministic records, not authenticated signatures against an actor rewriting the entire checkpoint.

Reviewed/approved scene content and assembling/final-QC episode content reject incompatible asset replacement, new generation authorization/submission and public planning changes before mutation. Exact historic callback and canonical-asset replay remain harmless. Existing in-flight callbacks may settle blocked production without advancing its lifecycle. No controlled re-edit of an approved scene is implemented; that requires a future revision/invalidation contract rather than silently discarding approvals.

Checkpoint episode statuses and every present scene status must be explicit; deleting them rejects rather than defaulting to PLANNED. Only explicitly PLANNED legacy records may default empty lifecycle histories. Advanced legacy checkpoints without histories reject instead of acquiring invented transitions. This is an unreleased offline schema-version-2 extension, without a live checkpoint migration. Provider activation and full Phase 2 approval remain separate boundaries; packages 3 and 4 below supply offline durability and master evidence contracts.


Package 1 Astra correction: scene/shot insertion and owned shot structural writes validate a detached prospective ledger before publishing data or ownership. Active production cannot acquire an unbound shot or lose a generating scene's last shot. Existing ShotPlan agreement and lifecycle evidence are checked in the prospective graph. Owned scene/shot IDs cannot be renamed independently of their map keys. Planned scaffolding still permits unbound shots and later scene assignment; legal active moves require the source generating scene to retain a shot. Rejected methods, map writes and attributes preserve state, histories and original ownership.


## Complete planning handoff — package 2

Planning scaffolds may remain unfrozen and incomplete while being constructed. Production submission requires a frozen, valid, nonempty ledger: every shot must belong to a present scene and have a matching ShotPlan and continuity binding to the exact current Bible revision/content digest. Submission and authorization preflight the full ledger before changing jobs, indexes, epochs, attempt history or ownership. Job plan identity includes the plan revision as well as exact shot, scene, audio policy, plan, binding and Bible content. Every restored job state requires the same complete frozen handoff; old job checkpoints lacking it reject without invented planning evidence. Legacy empty/planned scaffolds retain their documented restore routes.

Exact submission replay returns the original job in queued, authorized, running, retryable and terminal states; exact authorization replay preserves the existing immutable authorization. A new plan revision is conservatively blocked whenever any generation job exists, including queued and terminal history. Historical snapshots are required before safe post-generation revision can be supported; the current revision digest is an integrity record rather than a complete historical planning snapshot.

The schema expresses that nonempty generation jobs require frozen nonempty planning maps; exact key/reference/Bible equality and current lifecycle evidence remain runtime invariants. This is an unreleased offline schema-version-2 contract extension. Earlier incomplete production checkpoints are intentionally rejected, and no live migration, provider call or full Phase 2 approval is claimed.


## Durable offline recovery — package 3

`AtomicCheckpointStore` is a single-writer local checkpoint boundary. Saving validates the entire ledger and roundtrip before opening a temporary file, fully encodes UTF-8 JSON, writes an adjacent temporary file, flushes and fsyncs it, then atomically replaces the checkpoint. Platforms exposing `O_DIRECTORY` additionally fsync the parent directory after replacement. The tested Linux boundary recovers either the prior complete checkpoint or the next complete checkpoint under injected file-sync, pre/post-replace and directory-sync interruptions. Corrupt/truncated checkpoints and duplicate JSON object keys at any nesting level reject on load. Temporary files created by the writer are removed on caught failures; abrupt process termination may leave an unpublished temporary file, which is never used as a checkpoint. This boundary does not establish distributed execution ownership, multi-writer concurrency or an untested platform's crash guarantees.

`OfflineGenerationCoordinator` loads validated checkpoints and accepts only adapters explicitly marked as offline simulations whose exact identity matches a registered eligible authorization. Dispatch persists authorization and generation ownership before any adapter invocation. Restart recovery accepts AUTHORIZED or RUNNING jobs and reconciles by the persisted stable provider request key. The adapter contract returns an exact receipt, or `None` only for authoritative absence. AUTHORIZED absence may be submitted with that same stable key; RUNNING absence rejects without submission. Conflicting/stale receipts reject without checkpoint publication. Retry authorization creates a new key while preserving the first attempt. Terminal and QUEUED recovery do not invoke the adapter.

Counting fake-provider tests simulate remote acceptance followed by receipt loss and interruption before receipt checkpoint publication, then restart and recover without another submit/remote acceptance. Repeated AUTHORIZED/RUNNING recovery, pre-submit interruption, retry keys, stale callbacks, malformed/semantically invalid checkpoint loads and policy rejection are tested. Offline markers and receipts are deterministic test contracts rather than authenticated provider evidence. The coordinator cannot activate an actual provider; real adapter idempotency, authoritative absence semantics, account policy and platform durability require separately authorized activation validation. No paid API, rendering, upload or user-file cleanup is performed.


## Exact timeline, master, archive and cleanup evidence — package 4

`TimelineRecord` persists explicit scene order and ordered canonical asset inputs. The implementation policy orders scenes lexicographically by their stable IDs and shots by each scene's validated ShotPlan sequence index, independently of dictionary/checkpoint serialization order. Every shot appears exactly once with its current canonical asset version and exact planned duration. The timeline requires the complete frozen plan and exact Bible continuity bindings. A content digest identifies this ordered manifest; substituting/reordering a clip, changing a duration, scene order, revision or canonical asset invalidates the evidence.

The offline runtime policy remains approximately 20 minutes: total planned duration must be within 60 seconds of 1,200,000 milliseconds (19–21 minutes inclusive). This explicit tolerance is an implementation choice for Astra review, not a recovered original threshold. Short demonstration timelines reject. `MasterRecord` binds an exact master byte digest and length (computed against supplied fixture bytes on publication) to that timeline digest, exact measured/planned duration agreement and positive integer dimensions satisfying 16:9. `MasterQCRecord` must match that exact master/timeline and measured duration/dimensions, include passing audio, continuity and technical gates, and identify its reviewer and evidence. Fixture metadata and byte checks establish the offline control contract, not a rendered movie's measured quality.

The guarded flow is ASSEMBLING (publish timeline/master) → FINAL_QC (publish matching QC) → ARCHIVING (publish matching archive receipt) → COMPLETED. Every advance rechecks current canonical assets and dependencies. Archive evidence binds requested and readback Drive file identity/version, the master digest/byte count, readability, integrity result, verifier and evidence identity; publication hashes supplied readback fixture bytes. Missing, stale, unreadable or mismatched evidence cannot certify completion. BLOCKED recovery from ARCHIVING retains and rechecks the required prior master/QC evidence. Direct field assignments, conflicting replacement and constructor fabrication reject. Exact record/transition replay and checkpoint restoration remain idempotent.

Cleanup additionally requires an explicit immutable registry of temporary artifacts with stable IDs, agent creator, owner run and ownership provenance evidence IDs. A cleanup authorization can select only exact registered records, is bound to the matching master/archive evidence and identifies its authorizer/evidence. It can be published only for a completed episode; `cleanup_eligible` derives eligibility from the full validated ledger. The registry/authorization are offline policy evidence. No method deletes files, uploads to Drive or grants production cleanup permission; real trusted adapter receipts, reviewed ownership and applicable review approval remain required for actual actions. Caller booleans in the legacy completion helper continue to return False.

New nullable master-evidence fields and an empty temporary-artifact registry are version-2 offline extensions. Legacy checkpoints may default only absent evidence/empty registry; advanced lifecycle states without their required records reject. No timeline, QC, archive or ownership evidence is synthesized during migration. Full Phase 2 approval and minimum character/voice/reference/QA accountability remain outstanding.
