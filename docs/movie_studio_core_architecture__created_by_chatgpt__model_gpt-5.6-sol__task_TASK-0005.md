# Movie Studio Core Architecture — TASK-0005

Creator: ChatGPT
Model: GPT-5.6 Sol
Run: 20261006_TASK_0005_BOOTSTRAP

## Purpose
Define a durable, cloud-first orchestration contract for producing cinematic web-series episodes while remaining provider-neutral and zero-spend-by-default.

## Executive Producer
The Executive Producer is the deterministic authority. Specialist agents propose work and scores; they do not independently canonicalize assets. Hard constraints override votes.

## Specialist council
Director plans screenplay-to-scene-to-shot execution. Continuity tracks identity, wardrobe, props, location, time, camera geography and adjacent approved frames. Character manages canonical visual reference packs. Voice/Dialogue manages voice identity, pronunciation and timing. Generation submits provider-neutral jobs. Visual QA checks identity, anatomy, artifacts, composition, motion and technical framing. Continuity QA compares against the Movie Bible and adjacent canonical shots. Audio QA checks dialogue, voice consistency, lip sync and mix. Editor assembles the timeline. Upscale/Restoration runs only after semantic approval. Final QC checks the complete episode. Learning/Failure Memory stores reusable corrections. Monitor/Quota tracks free allowances and queues. Drive Archivist verifies final master persistence before temporary cleanup.

## Production contract
- Aspect ratio: 16:9 throughout the production contract.
- Episode target: approximately 20 minutes; the timeline owns exact runtime.
- Initial delivery target: 1920x1080 when the free pipeline can support it. Lower-resolution source material may be approved for later upscale; no false promise of 4K.
- Video providers are replaceable adapters. Movie logic must not contain provider-specific assumptions.
- Spend guard is fail-closed at USD 0.00 unless the user explicitly changes it.
- Local AI/video inference is prohibited.
- Private movie assets and secrets are prohibited from this public repository.

## Review gates
A shot cannot become canonical merely because generation succeeded. Applicable gates are Visual QA, Continuity QA, Audio QA for dialogue/audio shots, and technical-format validation. Rejection records a structured reason and correction package. Re-generation must carry forward the relevant failure lesson and approved references.

Upscaling is intentionally after semantic/continuity approval to avoid wasting scarce free compute. The final episode separately requires timeline/pacing QC, continuity QC, audio QC, technical master QC and archive verification.

## Durable state
Production state must be externalized rather than relying on conversational memory. Each project has a Movie Bible plus episode/scene/shot ledgers. Jobs use stable IDs and idempotency keys so interrupted cloud work can resume safely. Review evidence binds to the exact asset version it evaluated.

## Learning
Learning is controlled: record failure category, prompt/reference/provider settings, reviewer correction and outcome; then reuse validated lessons. Agents may tune routing and prompt heuristics from this ledger, but cannot silently rewrite hard safety/spend rules.

## Storage
Google Drive is the intended permanent master store. An episode reaches COMPLETED only after the final master is uploaded by a future authorized adapter and verified readable/integral. Temporary assets must not be deleted before that verification.

## Activation boundary
TASK-0005 is architecture/scaffolding only. It does not authorize a media provider, generate media, upload to Drive, purchase compute, modify TASK-0004, merge PR #6, or enable auto-merge.
