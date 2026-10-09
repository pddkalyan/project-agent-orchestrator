# TASK-0005 Reconstructed Phase 2 — Astra Review

Creator: ChatGPT Work Mode  
Reviewer: GPT-6 Astra  
Run: 20261009_PHASE2_MATRIX_REVIEW  
Verdict: **STILL_REQUIRES_CORRECTIONS**

## Exact review identity

- Reviewed matrix commit: `6e5d5b8b9fd373dfec4c10c125495fabb7f88674`.
- Reviewed matrix tree: `2af930aa567b4d27eeefc50edbf136b9ca599f24`.
- Matrix: `docs/reconstructed_phase2_acceptance_matrix__created_by_chatgpt__model_gpt-6.1-sol__task_TASK-0005.md`.
- Reviewed executable source: `953e72ee82582b9f7afea5ae11113bad62d89b64`.
- Supervisor-supplied PR #38 published source head: `9e09d3ff`; source tree: `74e47c905c1d018ca61e60bc98435b5fef1eeb87`.
- Supervisor-supplied published matrix commit: `9fd75e75baf8017a8c5f9d74633b457dd4fca411`, with the same matrix tree.

I independently checked the local matrix commit/tree and confirmed that its difference from the reviewed executable source contains only the new matrix and provenance entry. The code is unchanged. I did not independently fetch the supervisor-supplied remote identifiers during this review.

## Decision and scope

**Accept the matrix as an honest reconstructed assessment framework; do not approve full Phase 2 exit.** The user authorized reconstruction when the original checklist could not be recovered. R2-01 through R2-08 are new review groups, not recovered original A–H requirements. The missing original checklist is therefore no longer the reason to withhold a decision.

For this reconstructed gate, Phase 2 means an executable, durable, provider-neutral offline planning and production-control scaffold. It must enforce its declared planning, identity, recovery, completion and cleanup invariants with deterministic evidence. It need not generate a real movie, contact a provider, upload to Drive, authenticate a real account, or demonstrate final rendered quality.

The original task and October 7 state review explicitly require completion QC and evidence-backed storage/cleanup contracts, and leave them outstanding before PR #8 can leave draft. These are offline contract obligations. Classifying actual rendering and uploading as later work does not defer those contracts. This review includes them in the reconstructed offline exit gate; that classification is a present review decision, not a claim about the missing original A–H.

The prior **APPROVE** decision for source `953e72e` remains valid for the bounded Bible/binding increment. Both reproduced P2 defects—Unicode mutation atomicity and checkpoint namespace coercion—were corrected. The earlier binding-deadlock correction remains accepted. No new regression in that increment was found.

## Assessment by group

| Group | Phase decision | Current assessment |
|---|---|---|
| R2-01 — Durable story/Bible | Revisioned facts/entities, exact content identity and atomic controlled writes are mandatory. A formal multi-agent story/council workflow is deferred. | **Pass for the offline data contract.** No screenplay quality or council execution is certified. |
| R2-02 — Continuity/references | Exact supplied bindings are mandatory and pass. A minimum character-to-voice association and content-identified reference registry are adopted as inferred offline acceptance for the consistent-character/voice goal. | **Partial.** Current sets do not establish which voice belongs to which character; arbitrary reference-version strings do not establish media identity. |
| R2-03 — Controlled planning | The six recovered original D points pass in their documented safe scope. Complete, frozen, valid planning before production authorization is adopted as an inferred handoff requirement. | **Partial.** Normal APIs permit generation authorization with no ShotPlan or continuity record and an unfrozen ledger. |
| R2-04 — Provider authorization/content identity | Mandatory offline zero-spend, cloud-only, adapter and exact-content authorization records. Real trusted issuers/account verification remain activation prerequisites. | **Pass within offline policy-record scope**, subject to closing the planning handoff in R2-03. No real account or cost guarantee is certified. |
| R2-05 — Persistence/reconciliation | Exact restoration and stale replay rejection are mandatory and pass. An executable fake-provider recovery path and atomic durable checkpoint publication are required to substantiate durable/crash-recoverable operation. | **Partial.** A protocol method and receipt replay are not a coordinator or durable writer. Real adapter/account crash tests remain deferred activation work. |
| R2-06 — QA/lifecycle | Existing current-asset canonicalization/upscale gates pass. Evidence-derived episode/scene transitions and attributable QA records are required for auditable control. | **Partial.** Enum persistence does not guard transitions. Real visual/audio evaluators and human-quality judgments remain deferred. |
| R2-07 — Timeline/master QC | Mandatory original outstanding offline contract. | **Missing beyond constants and booleans.** No exact timeline/master evidence model establishes duration/aspect/audio/continuity/technical QC. |
| R2-08 — Archive/completion/cleanup | Mandatory original outstanding offline contract. | **Missing beyond booleans.** Completion does not derive from master-bound QC and storage verification; cleanup authorization lacks that evidence contract. |

Formal council roles, a screenplay-generation workflow, licensing adjudication, cryptographic authentication of external issuers, distributed execution and actual rendered character/voice/lipsync quality are not added as mandatory implementation work for this offline gate. Reference identity and associations are minimum data-integrity requirements, not proof of those production outcomes.

## Independent evidence

On executable source `953e72e`, I ran **107 Movie Studio tests, 34 controller tests and 68 reviewer-bridge tests**, all passing. I independently verified the Unicode-poison reproduction now rejects without changes, permits a subsequent legal write and round-trips. I also ran **98 malformed namespace probes** across all seven Bible namespaces and current/legacy-empty restoration; all rejected, while valid legacy restoration succeeded. Diff checks passed. The five controller production fixtures are supervisor evidence, not an independently repeated run by this reviewer.

Additional independent offline probes reproduced two open architectural gaps using ordinary APIs:

1. A ledger with one bare `Shot`, no ShotPlan, no continuity binding and `plan_frozen=False` accepted submission, authorization and a matching receipt, reached `RUNNING`, and passed validation.
2. An empty ledger assigned `EpisodeStatus.COMPLETED` passed validation and checkpoint round-trip. Separately, `episode_can_complete(all_shots_canonical=True, final_qc_passed=True, drive_master_verified=True)` returned true from caller assertions alone.

Neither probe invoked a provider or generated media. These are existing broader scaffold gaps, not regressions introduced by the approved Bible correction.

## Correction packages and acceptance

The following packages are a prioritized engineering queue. They do not authorize implementation, external actions or paid resources in this review turn.

1. **P1 — Guard lifecycle, final QC and archive authorization (R2-06/07/08).** Define the legal transitions and require evidence before terminal/completion states. Direct assignment, corrupted restore and helper calls must not certify an empty/unreviewed/unarchived episode. Require typed records or an explicit fail-closed unsupported state while the remaining evidence contracts are built. Test direct mutation, skipped transitions, empty completion and restored invalid completion; rejected actions must preserve state. Existing shot QA gates must remain intact.
2. **P2 — Require a complete planning handoff (R2-03/04).** Before a production attempt can be authorized, require a frozen valid plan, the target shot's valid scene/ShotPlan, and its current continuity binding. Prefer rejecting invalid new submissions before job/index changes. If low-level scaffold construction remains supported, make that mode explicit and unable to authorize production. Test each missing prerequisite, stale bindings, revision changes, unchanged replay and exact checkpoint restoration. Update positive fixtures rather than weakening negative assertions.
3. **P2 — Implement durable offline recovery (R2-05).** Add a checkpoint storage boundary and a coordinator exercised with a counting fake adapter. Persist authorization before dispatch; after simulated remote acceptance and loss of the receipt, restart, reconcile by the same request key, and prove no duplicate remote submission. Inject failures around checkpoint publication and recover either the prior complete checkpoint or the next complete checkpoint. Test queued, authorized, running and retry states, rejection with zero adapter calls, stale/conflicting receipts and a second recovery attempt. A single-process durable implementation is sufficient here; distributed ownership requires a separate contract before distributed activation.
4. **P1 — Implement exact timeline/master/QC/archive evidence (R2-07/08).** This completes package 1's evidence dependencies. Represent ordered canonical asset inputs, timeline identity and master content identity. Bind duration/aspect and applicable audio/continuity/technical QC to that exact master. Represent archive identity, master digest, readability/readback and integrity verification; derive completion and cleanup eligibility from valid matching records. Test reordered/substituted clips, wrong runtime/aspect, substituted masters, stale/missing QC, wrong remote identity, unreadable content, checksum mismatch and stale receipts. Use deterministic metadata and fixture bytes; no rendering, Drive call or deletion is required. Offline receipts remain test/policy evidence, with real adapter-issued verification explicitly required before production use.
5. **P2 — Complete minimum continuity and QA accountability (R2-02/06).** Define character-to-voice associations and a registry linking reference IDs/versions to content digest, entity and source identity. Add attributable QA evidence identifiers and role/policy context sufficient to trace an approval to the exact asset. Test wrong/dangling voice associations, unknown references, digest substitutions, stale evidence and preservation through revision/restore. Do not equate these records with externally authenticated provenance or actual visual/audio quality.

All packages must preserve the current zero-spend and cloud-only boundaries, exact-content job binding, historical-job immutability, explicit plan revisions and the accepted corruption tests. Schema changes need an explicit migration policy; missing evidence must not be fabricated for old checkpoints.

After the required packages, rerun the relevant deterministic regressions and request a new exact-source Astra phase review against this matrix. Record the actual decision and current next actions in authorized project-memory files; the existing TASK-0003 memory is not Phase 2 completion evidence.

## Remaining effort and boundaries

The remaining work is several coherent contract increments, not a small final patch. A rough planning allowance is **12–25 engineering days for one engineer familiar with this code**, including focused tests, migration work and review corrections across the five packages. This is a low-confidence estimate, not elapsed runtime, a quota/credit forecast, or an estimate for producing a movie; schema and persistence choices can materially change it. No actual model/account usage estimate is available.

PR #38's bounded source approval stands. **Full reconstructed Phase 2 still requires corrections; PR #8 remains draft.** No merge, Phase 3 commencement, provider activation, media generation, paid fallback, quota reset, Drive action or cleanup is authorized. A real 16:9 approximately 20-minute episode with consistent characters/voices, multi-character lipsync, SFX/music/subtitles, measured final QC and verified storage remains a future production outcome, not something demonstrated by these offline tests.
