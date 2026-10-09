import copy
import json
import unittest
from unittest.mock import patch
from pathlib import Path

from scripts.movie_studio_core__created_by_chatgpt__model_gpt_5_6_sol__task_TASK_0005 import (
    ShotPlan,
    plan_digest,
    ShotContinuityBinding,
    continuity_binding_digest,
    AttemptStatus,
    ProviderAdapterRegistration,
    ProviderCapability,
    FailureClass,
    Gate,
    GenerationJob,
    JobStatus,
    MovieBible,
    ProductionLedger,
    ProductionPolicyError,
    ProviderQuote,
    ProviderSubmissionReceipt,
    Review,
    Scene,
    Shot,
    ShotStatus,
    Verdict,
    authorize_upscale,
    canonicalize,
    choose_zero_cost_provider,
    episode_can_complete,
    required_gates,
)


class MovieStudioCoreTests(unittest.TestCase):
    def review(self, gate, version="v1", verdict=Verdict.PASS):
        return Review(gate, version, verdict)

    def generated_shot(self, dialogue=False):
        return Shot("S1", asset_version="v1", has_dialogue_or_audio=dialogue, status=ShotStatus.GENERATED)

    def quote(
        self,
        provider="afree",
        fingerprint="F1",
        estimated=0,
        maximum=0,
        available=True,
        cloud=True,
        cap=True,
    ):
        return ProviderQuote(
            quote_id=f"Q-{provider}",
            provider_id=provider,
            adapter_id=f"adapter-{provider}",
            adapter_version="1",
            request_fingerprint=fingerprint,
            estimated_cost_usd_micros=estimated,
            maximum_cost_usd_micros=maximum,
            available=available,
            cloud_execution=cloud,
            charge_cap_enforced=cap,
        )

    def register_provider(self, ledger, provider="afree"):
        quote = self.quote(provider=provider)
        adapter_key = f"{quote.provider_id}:{quote.adapter_id}:{quote.adapter_version}"
        if adapter_key not in ledger.provider_adapters:
            ledger.register_provider_adapter(
                ProviderAdapterRegistration(
                    provider_id=quote.provider_id,
                    adapter_id=quote.adapter_id,
                    adapter_version=quote.adapter_version,
                    capabilities=frozenset([ProviderCapability.VIDEO]),
                    cloud_execution=True,
                    charge_cap_enforced=True,
                    maximum_cost_usd_micros=0,
                )
            )
        return quote

    def authorize_and_start(
        self, ledger, job_id="J1", provider_job_id="provider-1", provider="afree"
    ):
        quote = self.register_provider(ledger, provider)
        attempt = len(ledger.jobs[job_id].attempt_history) + 1
        authorization = ledger.authorize_attempt(
            job_id,
            quote,
        )
        ledger.start_generation(
            job_id,
            ProviderSubmissionReceipt(
                authorization_id=authorization.authorization_id,
                job_id=job_id,
                attempt_number=attempt,
                provider_id=authorization.provider_id,
                adapter_id=authorization.adapter_id,
                adapter_version=authorization.adapter_version,
                provider_request_key=authorization.provider_request_key,
                provider_job_id=provider_job_id,
            ),
        )
        return attempt

    def test_freeze_plan(self):
        ledger = ProductionLedger("movie")
        self.assertFalse(ledger.plan_frozen)
        self.assertEqual(ledger.plan_revision, 1)
        ledger.freeze_plan()
        self.assertTrue(ledger.plan_frozen)
        with self.assertRaisesRegex(ProductionPolicyError, "plan is already frozen"):
            ledger.freeze_plan()

    def test_mutation_rejected_when_frozen(self):
        ledger = ProductionLedger("movie")
        ledger.freeze_plan()
        with self.assertRaisesRegex(ProductionPolicyError, "plan is frozen"):
            ledger.add_scene(Scene("SC1"))
        with self.assertRaisesRegex(ProductionPolicyError, "plan is frozen"):
            ledger.add_shot(Shot("S1", scene_id="SC1"))
        with self.assertRaisesRegex(ProductionPolicyError, "plan is frozen"):
            ledger.add_shot_plan(ShotPlan("S1", "SC1", 0, 1000, "prompt", False))
        with self.assertRaisesRegex(ProductionPolicyError, "plan is frozen"):
            ledger.add_shot_continuity_binding(
                ShotContinuityBinding("S1", 1, frozenset(), frozenset(), "", frozenset(), frozenset(), frozenset())
            )

    def test_create_new_plan_revision(self):
        ledger = ProductionLedger("movie")
        ledger.freeze_plan()
        self.assertTrue(ledger.plan_frozen)
        self.assertEqual(ledger.plan_revision, 1)
        ledger.create_new_plan_revision()
        self.assertFalse(ledger.plan_frozen)
        self.assertEqual(ledger.plan_revision, 2)

    def test_revision_replaces_validated_plan_and_binding_and_roundtrips(self):
        ledger = ProductionLedger("movie")
        ledger.bible.characters["A"] = {"name": "A"}
        ledger.bible.characters["B"] = {"name": "B"}
        ledger.add_scene(Scene("SC"))
        ledger.add_shot(Shot("S1", scene_id="SC"))
        ledger.add_shot(Shot("S2", scene_id="SC"))
        old = ShotPlan("S1", "SC", 0, 1000, "prompt-a", False)
        other = ShotPlan("S2", "SC", 1, 2000, "prompt-b", False)
        ledger.add_shot_plan(old)
        ledger.add_shot_plan(other)
        initial = ShotContinuityBinding("S1", ledger.bible.revision, frozenset({"A"}), frozenset(), "", frozenset(), frozenset(), frozenset({"asset:1"}))
        ledger.add_shot_continuity_binding(initial)
        ledger.freeze_plan()
        ledger.create_new_plan_revision()
        self.assertEqual(len(ledger.plan_revision_history), 1)
        revised = ShotPlan("S1", "SC", 0, 3000, "prompt-c", False)
        replacement = ShotContinuityBinding("S1", ledger.bible.revision, frozenset({"A", "B"}), frozenset(), "", frozenset(), frozenset(), frozenset({"asset:2"}))
        ledger.add_shot_plan(revised)
        ledger.add_shot_plan(revised)  # Exact replay has no further effect.
        ledger.add_shot_continuity_binding(replacement)
        ledger.add_shot_continuity_binding(replacement)
        ledger.shot_plans["S1"] = revised  # Public map follows the same validation.
        ledger.shot_continuity_bindings["S1"] = replacement
        self.assertNotEqual(plan_digest(old), plan_digest(revised))
        self.assertNotEqual(continuity_binding_digest(initial), continuity_binding_digest(replacement))
        self.assertEqual(ledger.shot_plans["S2"], other)
        ledger.freeze_plan()
        restored = ProductionLedger.from_dict(copy.deepcopy(ledger.to_dict()))
        self.assertEqual(restored.to_dict(), ledger.to_dict())
        self.assertEqual((restored.plan_revision, restored.plan_frozen), (2, True))

    def test_plan_replacement_requires_revision_and_untouched_generation(self):
        ledger = ProductionLedger("movie")
        ledger.add_scene(Scene("SC"))
        ledger.add_shot(Shot("S1", scene_id="SC"))
        ledger.add_shot(Shot("S2", scene_id="SC"))
        first = ShotPlan("S1", "SC", 0, 1000, "p1", False)
        ledger.add_shot_plan(first)
        with self.assertRaisesRegex(ProductionPolicyError, "conflicting shot plan replacement"):
            ledger.add_shot_plan(ShotPlan("S1", "SC", 0, 2000, "p2", False))
        with self.assertRaisesRegex(ProductionPolicyError, "conflicting shot plan replacement"):
            ledger.shot_plans["S1"] = ShotPlan("S1", "SC", 0, 2000, "p2", False)
        with self.assertRaisesRegex(ProductionPolicyError, "freeze the current plan"):
            ledger.create_new_plan_revision()
        ledger.freeze_plan()
        ledger.create_new_plan_revision()
        with self.assertRaisesRegex(ProductionPolicyError, "duplicate sequence_index"):
            ledger.add_shot_plan(ShotPlan("S2", "SC", 0, 1000, "p", False))
        with self.assertRaisesRegex(ProductionPolicyError, "planned_duration_ms"):
            ledger.add_shot_plan(ShotPlan("S1", "SC", 0, -1, "p2", False))
        binding = ShotContinuityBinding("S1", 1, frozenset(), frozenset(), "", frozenset(), frozenset(), frozenset())
        ledger.add_shot_continuity_binding(binding)
        ledger.submit_generation(job_id="J1", shot_id="S1", idempotency_key="I1", input_fingerprint="p1")
        with self.assertRaisesRegex(ProductionPolicyError, "conflicting shot plan replacement"):
            ledger.add_shot_plan(ShotPlan("S1", "SC", 0, 2000, "p2", False))
        with self.assertRaisesRegex(ProductionPolicyError, "conflicting continuity binding replacement"):
            ledger.add_shot_continuity_binding(ShotContinuityBinding("S1", 1, frozenset(), frozenset(), "", frozenset(), frozenset(), frozenset({"new-reference"})))
        with self.assertRaisesRegex(ProductionPolicyError, "conflicting continuity binding replacement"):
            ledger.shot_continuity_bindings["S1"] = ShotContinuityBinding("S1", 1, frozenset(), frozenset(), "", frozenset(), frozenset(), frozenset({"new-reference"}))
        self.assertEqual(ledger.shot_plans["S1"], first)
        ledger.freeze_plan()
        ledger.create_new_plan_revision()
        with self.assertRaisesRegex(ProductionPolicyError, "conflicting shot plan replacement"):
            ledger.add_shot_plan(ShotPlan("S1", "SC", 0, 2000, "p2", False))

    def test_frozen_plan_guards_public_mutation_routes(self):
        ledger = ProductionLedger("movie")
        scene = Scene("SC")
        shot = Shot("S1", scene_id="SC")
        ledger.add_scene(scene)
        ledger.add_shot(shot)
        ledger.freeze_plan()
        mutations = (
            lambda: ledger.scenes.update({"X": Scene("X")}),
            lambda: ledger.scenes.setdefault("X", Scene("X")),
            lambda: ledger.shots.pop("S1"),
            lambda: ledger.shots.popitem(),
            lambda: ledger.shot_plans.clear(),
            lambda: ledger.shot_continuity_bindings.__ior__({"S1": None}),
            lambda: setattr(ledger, "scenes", {}),
            lambda: setattr(ledger, "plan_revision", 3),
            lambda: setattr(ledger, "plan_frozen", False),
            lambda: setattr(ledger, "plan_revision_history", ("0" * 64,)),
            lambda: setattr(scene, "scene_id", "OTHER"),
            lambda: setattr(shot, "scene_id", "OTHER"),
            lambda: setattr(shot, "has_dialogue_or_audio", True),
        )
        before = ledger.to_dict()
        for mutate in mutations:
            with self.subTest(mutate=mutate), self.assertRaises(ProductionPolicyError):
                mutate()
            self.assertEqual(ledger.to_dict(), before)

    def test_deletion_cannot_downgrade_frozen_plan_or_audio_gate(self):
        ledger = ProductionLedger("movie")
        ledger.add_scene(Scene("SC"))
        ledger.add_shot(Shot("S1", scene_id="SC", has_dialogue_or_audio=True))
        ledger.freeze_plan()
        for current in (ledger, ProductionLedger.from_dict(copy.deepcopy(ledger.to_dict()))):
            scene, shot = current.scenes["SC"], current.shots["S1"]
            before = current.to_dict()
            self.assertIn(Gate.AUDIO_QA, required_gates(shot))
            deletions = (
                (current, "plan_frozen"),
                (current, "plan_revision"),
                (current, "plan_revision_history"),
                (current, "scenes"),
                (current, "shots"),
                (current, "shot_plans"),
                (current, "shot_continuity_bindings"),
                (current, "bible"),
                (scene, "scene_id"),
                (scene, "_plan_owner"),
                (shot, "shot_id"),
                (shot, "scene_id"),
                (shot, "has_dialogue_or_audio"),
                (shot, "_plan_owner"),
            )
            for target, name in deletions:
                with self.subTest(restored=current is not ledger, field=name), self.assertRaises(ProductionPolicyError):
                    delattr(target, name)
                self.assertEqual(current.to_dict(), before)
                current.validate()
                self.assertTrue(current.plan_frozen)
                self.assertIn(Gate.AUDIO_QA, required_gates(shot))
            with self.assertRaisesRegex(ProductionPolicyError, "plan is frozen"):
                current.add_shot_plan(ShotPlan("S1", "SC", 0, 1000, "p", True))

    def test_revision_checkpoint_missing_metadata_fails_closed(self):
        ledger = ProductionLedger("movie")
        ledger.add_scene(Scene("SC"))
        ledger.add_shot(Shot("S1", scene_id="SC"))
        ledger.add_shot_plan(ShotPlan("S1", "SC", 0, 1000, "p", False))
        ledger.freeze_plan()
        ledger.create_new_plan_revision()
        ledger.add_shot_plan(ShotPlan("S1", "SC", 0, 2000, "p2", False))
        ledger.freeze_plan()
        for field_name in ("plan_revision", "plan_frozen", "shot_plans"):
            corrupt = copy.deepcopy(ledger.to_dict())
            del corrupt[field_name]
            with self.subTest(field=field_name), self.assertRaises(ProductionPolicyError):
                ProductionLedger.from_dict(corrupt)
        for field_name, value in (("plan_revision", 1), ("plan_revision_history", []), ("plan_revision_history", ["bad-digest"]), ("plan_revision_history", None)):
            corrupt = copy.deepcopy(ledger.to_dict())
            corrupt[field_name] = value
            with self.subTest(field=field_name, value=value), self.assertRaises(ProductionPolicyError):
                ProductionLedger.from_dict(corrupt)
        corrupt = ledger.to_dict()
        del corrupt["plan_revision_history"]
        with self.assertRaisesRegex(ProductionPolicyError, "missing plan revision history"):
            ProductionLedger.from_dict(corrupt)
        corrupt = ledger.to_dict()
        corrupt["plan_revision"] = 0
        with self.assertRaisesRegex(ProductionPolicyError, "invalid plan_revision"):
            ProductionLedger.from_dict(corrupt)

    def test_frozen_scene_only_checkpoint_cannot_default_away_freeze(self):
        ledger = ProductionLedger("movie")
        ledger.add_scene(Scene("SC"))
        ledger.freeze_plan()
        for missing in ("plan_revision", "plan_frozen"):
            corrupt = copy.deepcopy(ledger.to_dict())
            del corrupt[missing]
            with self.subTest(field=missing), self.assertRaisesRegex(ProductionPolicyError, "missing plan revision or freeze state"):
                ProductionLedger.from_dict(corrupt)
        corrupt = copy.deepcopy(ledger.to_dict())
        del corrupt["scenes"]
        with self.assertRaisesRegex(ProductionPolicyError, "missing revised planning data"):
            ProductionLedger.from_dict(corrupt)
        restored = ProductionLedger.from_dict(copy.deepcopy(ledger.to_dict()))
        self.assertTrue(restored.plan_frozen)
        with self.assertRaisesRegex(ProductionPolicyError, "plan is frozen"):
            restored.add_scene(Scene("SC2"))

    def test_frozen_plan_checkpoint_roundtrip(self):
        ledger = ProductionLedger("movie")
        ledger.freeze_plan()
        data = ledger.to_dict()
        self.assertEqual(data["plan_revision"], 1)
        self.assertEqual(data["plan_frozen"], True)
        restored = ProductionLedger.from_dict(data)
        self.assertEqual(restored.plan_revision, 1)
        self.assertEqual(restored.plan_frozen, True)

    def test_frozen_plan_migration_safe_default(self):
        ledger = ProductionLedger("movie")
        data = ledger.to_dict()
        del data["plan_revision"]
        del data["plan_frozen"]
        restored = ProductionLedger.from_dict(data)
        self.assertEqual(restored.plan_revision, 1)
        self.assertEqual(restored.plan_frozen, False)

    def test_add_scene_rejects_blank_and_duplicate_ids(self):
        ledger = ProductionLedger("movie")
        ledger.add_scene(Scene("SC1"))
        with self.assertRaises(ProductionPolicyError):
            ledger.add_scene(Scene(""))
        with self.assertRaises(ProductionPolicyError):
            ledger.add_scene(Scene("SC1"))

    def test_add_shot_rejects_unknown_scene_allows_empty(self):
        ledger = ProductionLedger("movie")
        ledger.add_scene(Scene("SC1"))
        ledger.add_shot(Shot("S1", scene_id="SC1"))
        ledger.add_shot(Shot("S2", scene_id=""))
        with self.assertRaises(ProductionPolicyError):
            ledger.add_shot(Shot("S3", scene_id="SC-UNKNOWN"))

    def test_validate_rejects_corrupted_scene_maps_and_dangling_scene_references(self):
        ledger = ProductionLedger("movie", episode_id="ep1")
        ledger.add_scene(Scene("SC1"))
        ledger.add_shot(Shot("S1", scene_id="SC1"))

        # Corrupted scene map key
        corrupt_key = copy.deepcopy(ledger)
        # Simulate an internally corrupted map despite the public mutation guard.
        corrupt_key.scenes._data["bad_key"] = corrupt_key.scenes._data.pop("SC1")
        with self.assertRaises(ProductionPolicyError):
            corrupt_key.validate()

        # Dangling scene reference
        dangling_ref = copy.deepcopy(ledger)
        dangling_ref.shots["S1"].scene_id = "SC2"
        with self.assertRaises(ProductionPolicyError):
            dangling_ref.validate()

    def test_canonical_requires_visual_continuity_and_technical(self):
        shot = self.generated_shot()
        shot.reviews = {
            Gate.VISUAL_QA: self.review(Gate.VISUAL_QA),
            Gate.CONTINUITY_QA: self.review(Gate.CONTINUITY_QA),
        }
        with self.assertRaises(ProductionPolicyError):
            canonicalize(shot)

    def test_dialogue_requires_audio(self):
        shot = self.generated_shot(True)
        shot.reviews = {
            gate: self.review(gate)
            for gate in (Gate.VISUAL_QA, Gate.CONTINUITY_QA, Gate.TECHNICAL_QA)
        }
        with self.assertRaises(ProductionPolicyError):
            canonicalize(shot)

    def test_all_gates_make_canonical(self):
        shot = self.generated_shot(True)
        shot.reviews = {gate: self.review(gate) for gate in Gate}
        canonicalize(shot)
        self.assertTrue(shot.canonical)
        self.assertIs(shot.status, ShotStatus.CANONICAL)

    def test_stale_review_is_rejected(self):
        shot = Shot("S1", "v2", status=ShotStatus.GENERATED)
        shot.reviews = {Gate.VISUAL_QA: self.review(Gate.VISUAL_QA, "v1")}
        with self.assertRaises(ProductionPolicyError):
            authorize_upscale(shot)

    def test_upscale_requires_semantic_gates(self):
        shot = self.generated_shot()
        shot.reviews = {Gate.VISUAL_QA: self.review(Gate.VISUAL_QA)}
        with self.assertRaises(ProductionPolicyError):
            authorize_upscale(shot)

    def test_upscale_after_semantic_gates(self):
        shot = self.generated_shot()
        shot.reviews = {
            gate: self.review(gate)
            for gate in (Gate.VISUAL_QA, Gate.CONTINUITY_QA)
        }
        authorize_upscale(shot)
        self.assertTrue(shot.upscale_allowed)

    def test_new_asset_invalidates_all_old_approval_state(self):
        shot = self.generated_shot()
        shot.reviews = {gate: self.review(gate) for gate in Gate}
        shot.canonical = True
        shot.upscale_allowed = True
        shot.bind_generated_asset("v2")
        self.assertEqual({}, shot.reviews)
        self.assertFalse(shot.canonical)
        self.assertFalse(shot.upscale_allowed)

    def test_paid_provider_never_selected(self):
        with self.assertRaises(ProductionPolicyError):
            choose_zero_cost_provider([self.quote("paid", estimated=1, maximum=1)])

    def test_zero_cost_provider_deterministic(self):
        quote = choose_zero_cost_provider(
            [self.quote("zfree"), self.quote("afree")]
        )
        self.assertEqual("afree", quote.provider_id)

    def test_episode_requires_verified_drive_master(self):
        self.assertFalse(
            episode_can_complete(
                all_shots_canonical=True,
                final_qc_passed=True,
                drive_master_verified=False,
            )
        )
        self.assertTrue(
            episode_can_complete(
                all_shots_canonical=True,
                final_qc_passed=True,
                drive_master_verified=True,
            )
        )
        self.assertFalse(
            episode_can_complete(
                all_shots_canonical="yes",
                final_qc_passed="yes",
                drive_master_verified="yes",
            )
        )

    def test_generation_submission_is_idempotent(self):
        ledger = ProductionLedger("movie")
        ledger.add_shot(Shot("S1"))
        first = ledger.submit_generation(
            job_id="J1",
            shot_id="S1",
            idempotency_key="K1",
            input_fingerprint="F1",
        )
        second = ledger.submit_generation(
            job_id="ignored",
            shot_id="S1",
            idempotency_key="K1",
            input_fingerprint="F1",
        )
        self.assertIs(first, second)
        self.assertEqual(1, len(ledger.jobs))

    def test_idempotency_key_cannot_hide_changed_input(self):
        ledger = ProductionLedger("movie")
        ledger.add_shot(Shot("S1"))
        ledger.submit_generation(
            job_id="J1",
            shot_id="S1",
            idempotency_key="K1",
            input_fingerprint="F1",
        )
        with self.assertRaises(ProductionPolicyError):
            ledger.submit_generation(
                job_id="J2",
                shot_id="S1",
                idempotency_key="K1",
                input_fingerprint="changed",
            )

    def test_generation_success_binds_exact_asset(self):
        ledger = ProductionLedger("movie")
        ledger.add_shot(Shot("S1"))
        ledger.submit_generation(
            job_id="J1",
            shot_id="S1",
            idempotency_key="K1",
            input_fingerprint="F1",
        )
        attempt = self.authorize_and_start(ledger)
        ledger.finish_generation("J1", attempt, "provider-1", "asset-v1")
        self.assertIs(ledger.jobs["J1"].status, JobStatus.SUCCEEDED)
        self.assertEqual("asset-v1", ledger.shots["S1"].asset_version)
        self.assertIs(ledger.shots["S1"].status, ShotStatus.GENERATED)

    def test_retry_ceiling_blocks_shot(self):
        ledger = ProductionLedger("movie")
        ledger.add_shot(Shot("S1"))
        ledger.submit_generation(
            job_id="J1",
            shot_id="S1",
            idempotency_key="K1",
            input_fingerprint="F1",
            max_attempts=2,
        )
        attempt = self.authorize_and_start(ledger)
        ledger.fail_generation(
            "J1",
            attempt,
            "provider-1",
            FailureClass.RETRYABLE_PROVIDER,
            "transient",
        )
        self.assertIs(ledger.jobs["J1"].status, JobStatus.RETRYABLE)
        attempt = self.authorize_and_start(
            ledger, provider_job_id="provider-2", provider="zfree"
        )
        ledger.fail_generation(
            "J1",
            attempt,
            "provider-2",
            FailureClass.RETRYABLE_PROVIDER,
            "still broken",
        )
        self.assertIs(ledger.jobs["J1"].status, JobStatus.EXHAUSTED)
        self.assertIs(ledger.shots["S1"].status, ShotStatus.BLOCKED)

    def test_job_rejects_double_completion(self):
        ledger = ProductionLedger("movie")
        ledger.add_shot(Shot("S1"))
        job = ledger.submit_generation(
            job_id="J1",
            shot_id="S1",
            idempotency_key="K1",
            input_fingerprint="F1",
        )
        attempt = self.authorize_and_start(ledger)
        ledger.finish_generation("J1", attempt, "provider-1", "v1")
        with self.assertRaises(ProductionPolicyError):
            ledger.finish_generation("J1", attempt, "provider-1", "v2")

    def test_movie_bible_updates_are_revisioned(self):
        bible = MovieBible()
        bible.update_fact("continuity_facts", "amulet", "worn on left wrist")
        self.assertEqual(2, bible.revision)
        self.assertEqual("worn on left wrist", bible.continuity_facts["amulet"])

    def test_bible_entities_are_copy_on_read_revisioned_and_frozen(self):
        ledger = ProductionLedger("movie")
        original = {"name": "Alice"}
        ledger.bible.characters["A"] = original
        original["name"] = "external mutation"
        self.assertEqual(ledger.bible.characters["A"]["name"], "Alice")
        self.assertEqual(ledger.bible.revision, 2)
        old_digest = ledger.bible.content_digest
        with self.assertRaises(TypeError):
            ledger.bible.characters["A"]["name"] = "nested mutation"
        self.assertEqual(ledger.bible.content_digest, old_digest)
        with self.assertRaisesRegex(ProductionPolicyError, "stale movie-bible revision"):
            ledger.bible.update_entity("characters", "A", {"name": "Alicia"}, expected_revision=1)
        ledger.bible.update_entity("characters", "A", {"name": "Alicia"}, expected_revision=2)
        self.assertEqual(ledger.bible.revision, 3)
        self.assertEqual(len(ledger.bible.revision_history), 2)
        self.assertEqual(ledger.bible.revision_history[-1], old_digest)
        self.assertNotEqual(ledger.bible.content_digest, old_digest)
        ledger.freeze_plan()
        before = ledger.to_dict()
        for mutate in (
            lambda: ledger.bible.characters.__setitem__("A", {"name": "changed"}),
            lambda: ledger.bible.characters.__delitem__("A"),
            lambda: ledger.bible.update_fact("story_rules", "rule", "changed"),
            lambda: setattr(ledger.bible, "revision", 1),
            lambda: delattr(ledger.bible, "content_digest"),
        ):
            with self.assertRaises(ProductionPolicyError):
                mutate()
            self.assertEqual(ledger.to_dict(), before)

    def test_bible_batch_is_atomic_and_cannot_launder_corruption(self):
        ledger = ProductionLedger("movie")
        ledger.bible.characters["A"] = {"name": "Alice"}
        before = ledger.to_dict()
        with self.assertRaisesRegex(ProductionPolicyError, "entity"):
            ledger.bible.characters.update({"B": {"name": "Bob"}, "bad": {"": "invalid"}})
        self.assertEqual(ledger.to_dict(), before)
        ledger.bible.characters._data["A"]["name"] = "private tamper"
        with self.assertRaisesRegex(ProductionPolicyError, "revision/digest mismatch"):
            ledger.bible.characters["B"] = {"name": "Bob"}
        with self.assertRaisesRegex(ProductionPolicyError, "revision/digest mismatch"):
            ledger.to_dict()

    def test_bible_unicode_writes_reject_atomically_and_accept_valid_unicode(self):
        ledger = ProductionLedger("movie")
        ledger.bible.characters["A"] = {"name": "Alice"}
        before = ledger.to_dict()
        invalid = chr(0xD800)
        mutations = (
            lambda: ledger.bible.update_fact("story_rules", "rule", invalid),
            lambda: ledger.bible.update_fact("story_rules", invalid, "value"),
            lambda: ledger.bible.characters.__setitem__(invalid, {"name": "Bob"}),
            lambda: ledger.bible.characters.__setitem__("B", {invalid: "Bob"}),
            lambda: ledger.bible.characters.__setitem__("B", {"name": invalid}),
            lambda: ledger.bible.update_entity("characters", "A", {"name": invalid}, expected_revision=ledger.bible.revision),
            lambda: ledger.bible.characters.update({"B": {"name": "Bob"}, "C": {"name": invalid}}),
            lambda: ledger.bible.story_rules.update({"valid": "value", "invalid": invalid}),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                with self.assertRaisesRegex(ProductionPolicyError, "UTF-8"):
                    mutation()
                self.assertEqual(ledger.to_dict(), before)
                self.assertEqual(ProductionLedger.from_dict(copy.deepcopy(before)).to_dict(), before)
        for namespace in MovieBible.MAP_NAMES:
            value = invalid if namespace in {"story_rules", "continuity_facts"} else {"name": invalid}
            with self.subTest(constructor_namespace=namespace):
                with self.assertRaisesRegex(ProductionPolicyError, "UTF-8"):
                    MovieBible(**{namespace: {"key": value}})
                corrupt = copy.deepcopy(before)
                corrupt["movie_bible"][namespace]["key"] = value
                with self.assertRaisesRegex(ProductionPolicyError, "UTF-8"):
                    ProductionLedger.from_dict(corrupt)
        ledger.bible.update_fact("story_rules", "règle", "雪 🌙 café")
        ledger.bible.update_entity("characters", "英雄", {"名前": "Élodie 🐈"}, expected_revision=ledger.bible.revision)
        checkpoint = ledger.to_dict()
        self.assertEqual(ProductionLedger.from_dict(copy.deepcopy(checkpoint)).to_dict(), checkpoint)

    def test_bible_digest_failure_cannot_publish_partial_mutation(self):
        ledger = ProductionLedger("movie")
        ledger.bible.characters["A"] = {"name": "Alice"}
        before = ledger.to_dict()
        original_digest = MovieBible._digest
        def fail_proposed(bible, replacement_name=None, replacement_values=None):
            if replacement_name is not None:
                raise ProductionPolicyError("injected candidate digest failure")
            return original_digest(bible)
        mutations = (
            lambda: ledger.bible.characters.__setitem__("B", {"name": "Bob"}),
            lambda: ledger.bible.characters.update({"B": {"name": "Bob"}}),
            lambda: ledger.bible.characters.__delitem__("A"),
            lambda: ledger.bible.characters.clear(),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                with patch.object(MovieBible, "_digest", fail_proposed):
                    with self.assertRaisesRegex(ProductionPolicyError, "candidate digest failure"):
                        mutation()
                self.assertEqual(ledger.to_dict(), before)
                self.assertEqual(ProductionLedger.from_dict(copy.deepcopy(before)).to_dict(), before)
        # A failed write must not prevent a subsequent legal repair/update.
        ledger.bible.characters["B"] = {"name": "Bob"}
        ledger.validate()

    def test_bible_namespace_restore_and_constructor_require_mapping(self):
        for populated in (False, True):
            ledger = ProductionLedger("movie")
            if populated:
                ledger.bible.characters["A"] = {"name": "Alice"}
            checkpoint = ledger.to_dict()
            for namespace in MovieBible.MAP_NAMES:
                for invalid in ([], [("duplicate", {}), ("duplicate", {})], "", 0, None):
                    with self.subTest(populated=populated, namespace=namespace, invalid=invalid):
                        corrupt = copy.deepcopy(checkpoint)
                        corrupt["movie_bible"][namespace] = invalid
                        with self.assertRaisesRegex(ProductionPolicyError, "mapping"):
                            ProductionLedger.from_dict(corrupt)
                        with self.assertRaisesRegex(ProductionPolicyError, "mapping"):
                            MovieBible(**{namespace: invalid})
            if populated:
                corrupt = copy.deepcopy(checkpoint)
                corrupt["movie_bible"]["characters"] = [("A", {"name": "discarded"}), ("A", {"name": "Alice"})]
                with self.assertRaisesRegex(ProductionPolicyError, "mapping"):
                    ProductionLedger.from_dict(corrupt)

    def test_bible_checkpoint_integrity_and_legacy_boundary(self):
        empty = ProductionLedger("movie").to_dict()
        for field in ("content_digest", "revision_history"):
            empty["movie_bible"].pop(field)
        restored = ProductionLedger.from_dict(empty)
        self.assertEqual(restored.bible.revision, 1)
        ledger = ProductionLedger("movie")
        ledger.bible.characters["A"] = {"name": "Alice"}
        checkpoint = ledger.to_dict()
        self.assertEqual(ProductionLedger.from_dict(copy.deepcopy(checkpoint)).to_dict(), checkpoint)
        for change in (
            lambda d: d["movie_bible"]["characters"]["A"].__setitem__("name", "Mallory"),
            lambda d: d["movie_bible"].__setitem__("revision", 1),
            lambda d: d["movie_bible"]["revision_history"].clear(),
            lambda d: d["movie_bible"].pop("content_digest"),
            lambda d: d["movie_bible"].pop("revision_history"),
            lambda d: d["movie_bible"].__setitem__("content_digest", ""),
            lambda d: d["movie_bible"].__setitem__("content_digest", None),
        ):
            corrupt = copy.deepcopy(checkpoint)
            change(corrupt)
            with self.assertRaises(ProductionPolicyError):
                ProductionLedger.from_dict(corrupt)
        constructed = ProductionLedger("movie", bible=MovieBible(characters={"A": {"name": "Alice"}})).to_dict()
        self.assertEqual(constructed["movie_bible"]["revision"], 1)
        del constructed["movie_bible"]["content_digest"]
        with self.assertRaisesRegex(ProductionPolicyError, "lacks integrity evidence"):
            ProductionLedger.from_dict(constructed)

    def test_revised_binding_tracks_exact_bible_digest(self):
        ledger = ProductionLedger("movie")
        ledger.bible.characters["A"] = {"name": "Alice"}
        ledger.add_shot(Shot("S1"))
        binding = ShotContinuityBinding("S1", ledger.bible.revision, frozenset({"A"}), frozenset(), "", frozenset(), frozenset(), frozenset())
        ledger.add_shot_continuity_binding(binding)
        first_digest = ledger.shot_continuity_bindings["S1"].bible_digest
        before = ledger.to_dict()
        with self.assertRaisesRegex(ProductionPolicyError, "freeze the plan and create a new revision"):
            ledger.bible.update_entity("characters", "A", {"name": "Alicia"}, expected_revision=ledger.bible.revision)
        self.assertEqual(ledger.to_dict(), before)
        ledger.bible.update_entity("characters", "A", {"name": "Alice"}, expected_revision=ledger.bible.revision)
        ledger.bible.characters.update({"A": {"name": "Alice"}})
        self.assertEqual(ledger.to_dict(), before)  # Exact replay does not advance revision.
        ledger.freeze_plan()
        ledger.create_new_plan_revision()
        ledger.bible.update_entity("characters", "A", {"name": "Alicia"}, expected_revision=2)
        with self.assertRaisesRegex(ProductionPolicyError, "binding bible_revision"):
            ledger.validate()
        revised = ShotContinuityBinding("S1", ledger.bible.revision, frozenset({"A"}), frozenset(), "", frozenset(), frozenset(), frozenset())
        ledger.add_shot_continuity_binding(revised)
        self.assertNotEqual(ledger.shot_continuity_bindings["S1"].bible_digest, first_digest)
        ledger.freeze_plan()
        self.assertEqual(ProductionLedger.from_dict(copy.deepcopy(ledger.to_dict())).to_dict(), ledger.to_dict())

    def test_bible_change_rejects_unreplaceable_binding_before_mutation(self):
        ledger = ProductionLedger("movie")
        ledger.bible.characters["A"] = {"name": "Alice"}
        ledger.add_shot(Shot("S1"))
        ledger.add_shot_continuity_binding(ShotContinuityBinding("S1", ledger.bible.revision, frozenset({"A"}), frozenset(), "", frozenset(), frozenset(), frozenset()))
        ledger.freeze_plan()
        ledger.create_new_plan_revision()
        ledger.shots["S1"].bind_generated_asset("v1")
        before = ledger.to_dict()
        with self.assertRaisesRegex(ProductionPolicyError, "conflicting continuity binding replacement"):
            ledger.bible.update_entity("characters", "A", {"name": "Alicia"}, expected_revision=ledger.bible.revision)
        self.assertEqual(ledger.to_dict(), before)

    def test_bible_mutation_preflights_every_binding_and_public_write_path(self):
        ledger = ProductionLedger("movie")
        ledger.bible.characters["A"] = {"name": "Alice"}
        for shot_id in ("S1", "S2"):
            ledger.add_shot(Shot(shot_id))
            ledger.add_shot_continuity_binding(ShotContinuityBinding(shot_id, ledger.bible.revision, frozenset({"A"}), frozenset(), "", frozenset(), frozenset(), frozenset()))
        ledger.freeze_plan()
        ledger.create_new_plan_revision()
        # The first binding is replaceable; the second must block the whole write.
        ledger.shots["S2"].bind_generated_asset("v2")
        before = ledger.to_dict()
        mutations = (
            lambda: ledger.bible.characters.__setitem__("B", {"name": "Bob"}),
            lambda: ledger.bible.characters.update({"B": {"name": "Bob"}, "A": {"name": "Alicia"}}),
            lambda: ledger.bible.characters.__delitem__("A"),
            lambda: ledger.bible.characters.clear(),
            lambda: ledger.bible.characters.pop("A"),
            lambda: ledger.bible.update_fact("story_rules", "rule", "changed"),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                with self.assertRaisesRegex(ProductionPolicyError, "conflicting continuity binding replacement"):
                    mutation()
                self.assertEqual(ledger.to_dict(), before)
                self.assertEqual(ProductionLedger.from_dict(copy.deepcopy(before)).to_dict(), before)
        # Exact no-ops remain valid even when a meaningful change is prohibited.
        ledger.bible.characters["A"] = {"name": "Alice"}
        ledger.bible.characters.update({"A": {"name": "Alice"}})
        ledger.bible.story_rules.clear()
        self.assertEqual(ledger.to_dict(), before)

    def test_generation_authorization_binds_actual_plan_and_bible_content(self):
        authorizations = []
        for duration, name in ((1000, "Alice"), (2000, "Alice"), (1000, "Alicia")):
            ledger = ProductionLedger("movie")
            ledger.bible.characters["A"] = {"name": name}
            ledger.add_scene(Scene("SC"))
            ledger.add_shot(Shot("S1", scene_id="SC"))
            ledger.add_shot_plan(ShotPlan("S1", "SC", 0, duration, "same-prompt", False))
            ledger.add_shot_continuity_binding(ShotContinuityBinding("S1", ledger.bible.revision, frozenset({"A"}), frozenset(), "", frozenset(), frozenset(), frozenset()))
            ledger.freeze_plan()
            job = ledger.submit_generation(job_id="J1", shot_id="S1", idempotency_key="I1", input_fingerprint="F1")
            quote = self.register_provider(ledger)
            auth = ledger.authorize_attempt("J1", quote)
            self.assertEqual(job.plan_state_digest, auth.plan_state_digest)
            self.assertEqual(ProductionLedger.from_dict(copy.deepcopy(ledger.to_dict())).to_dict(), ledger.to_dict())
            authorizations.append(auth)
        self.assertNotEqual(authorizations[0].plan_state_digest, authorizations[1].plan_state_digest)
        self.assertNotEqual(authorizations[0].provider_request_key, authorizations[1].provider_request_key)
        self.assertNotEqual(authorizations[0].plan_state_digest, authorizations[2].plan_state_digest)
        self.assertNotEqual(authorizations[0].provider_request_key, authorizations[2].provider_request_key)

    def test_generation_rejects_stale_planning_and_bible_callbacks(self):
        ledger = ProductionLedger("movie")
        ledger.add_scene(Scene("SC"))
        ledger.add_shot(Shot("S1", scene_id="SC"))
        plan = ShotPlan("S1", "SC", 0, 1000, "prompt", False)
        ledger.add_shot_plan(plan)
        ledger.submit_generation(job_id="J1", shot_id="S1", idempotency_key="I1", input_fingerprint="F1")
        with self.assertRaisesRegex(ProductionPolicyError, "generation history"):
            ledger.bible.update_fact("story_rules", "new", "not while queued")
        with self.assertRaisesRegex(ProductionPolicyError, "after generation job submission"):
            ledger.add_shot_continuity_binding(ShotContinuityBinding("S1", ledger.bible.revision, frozenset(), frozenset(), "", frozenset(), frozenset(), frozenset()))
        self.assertIs(ledger.submit_generation(job_id="ignored", shot_id="S1", idempotency_key="I1", input_fingerprint="F1"), ledger.jobs["J1"])
        ledger.shot_plans._data["S1"] = ShotPlan("S1", "SC", 0, 2000, "prompt", False)
        quote = self.register_provider(ledger)
        with self.assertRaisesRegex(ProductionPolicyError, "stale plan"):
            ledger.authorize_attempt("J1", quote)
        self.assertEqual(ledger.jobs["J1"].status, JobStatus.QUEUED)
        ledger.shot_plans._data["S1"] = plan
        self.authorize_and_start(ledger)
        before = ledger.jobs["J1"].status
        ledger.bible.story_rules._data["rule"] = "private tamper"
        with self.assertRaisesRegex(ProductionPolicyError, "revision/digest mismatch"):
            ledger.finish_generation("J1", 1, "provider-1", "v1")
        self.assertIs(ledger.jobs["J1"].status, before)

    def test_generation_checkpoint_requires_plan_state_binding(self):
        ledger = ProductionLedger("movie")
        ledger.add_shot(Shot("S1"))
        ledger.submit_generation(job_id="J1", shot_id="S1", idempotency_key="I1", input_fingerprint="F1")
        self.authorize_and_start(ledger)
        checkpoint = ledger.to_dict()
        for path in ("job_missing", "job_changed", "authorization_missing", "authorization_changed"):
            corrupt = copy.deepcopy(checkpoint)
            job = corrupt["generation_jobs"]["J1"]
            auth = job["attempt_history"][0]["authorization"]
            if path == "job_missing":
                del job["plan_state_digest"]
            elif path == "job_changed":
                job["plan_state_digest"] = "0" * 64
            elif path == "authorization_missing":
                del auth["plan_state_digest"]
            else:
                auth["plan_state_digest"] = "0" * 64
            with self.subTest(path=path), self.assertRaises(ProductionPolicyError):
                ProductionLedger.from_dict(corrupt)

    def test_new_integrity_fields_match_published_checkpoint_schema(self):
        schema_path = Path("schemas/movie_studio_production_state__created_by_chatgpt__model_gpt-5.6-sol__task_TASK-0005.schema.json")
        schema = json.loads(schema_path.read_text())
        ledger = ProductionLedger("movie")
        ledger.bible.characters["A"] = {"name": "Alice"}
        ledger.add_shot(Shot("S1"))
        ledger.add_shot_continuity_binding(ShotContinuityBinding("S1", ledger.bible.revision, frozenset({"A"}), frozenset(), "", frozenset(), frozenset(), frozenset()))
        ledger.submit_generation(job_id="J1", shot_id="S1", idempotency_key="I1", input_fingerprint="F1")
        self.authorize_and_start(ledger)
        data = ledger.to_dict()
        samples = (
            ("movie_bible", data["movie_bible"], {"content_digest", "revision_history"}),
            ("shot_continuity_binding", data["shot_continuity_bindings"]["S1"], {"bible_digest"}),
            ("generation_job", data["generation_jobs"]["J1"], {"plan_state_digest"}),
            ("attempt_authorization", data["generation_jobs"]["J1"]["attempt_history"][0]["authorization"], {"plan_state_digest"}),
        )
        for definition, sample, new_fields in samples:
            contract = schema["$defs"][definition]
            self.assertTrue(new_fields.issubset(contract["properties"]))
            self.assertTrue(new_fields.issubset(contract["required"]))
            self.assertEqual(set(sample), set(contract["required"]))

    def test_terminal_generation_history_blocks_bible_and_structure_rewrite(self):
        for succeeded in (True, False):
            ledger = ProductionLedger("movie")
            ledger.add_scene(Scene("SC"))
            ledger.add_shot(Shot("S1", scene_id="SC"))
            ledger.submit_generation(job_id="J1", shot_id="S1", idempotency_key="I1", input_fingerprint="F1")
            self.authorize_and_start(ledger)
            if succeeded:
                ledger.finish_generation("J1", 1, "provider-1", "v1")
            else:
                ledger.fail_generation("J1", 1, "provider-1", FailureClass.NON_RETRYABLE_PROVIDER, "failed")
            ledger.validate()
            before = ledger.to_dict()
            for mutate in (
                lambda: ledger.bible.update_fact("story_rules", "new", "blocked"),
                lambda: setattr(ledger.scenes["SC"], "scene_id", "OTHER"),
                lambda: setattr(ledger.shots["S1"], "scene_id", "OTHER"),
                lambda: setattr(ledger.shots["S1"], "has_dialogue_or_audio", True),
                lambda: setattr(ledger, "project_id", "other-movie"),
            ):
                with self.subTest(succeeded=succeeded, mutate=mutate), self.assertRaisesRegex(ProductionPolicyError, "generation history"):
                    mutate()
                self.assertEqual(ledger.to_dict(), before)
            self.assertEqual(ProductionLedger.from_dict(copy.deepcopy(before)).to_dict(), before)
            corrupt = copy.deepcopy(before)
            corrupt["project_id"] = "other-movie"
            with self.assertRaisesRegex(ProductionPolicyError, "stale plan"):
                ProductionLedger.from_dict(corrupt)

    def test_ledger_round_trip_preserves_resume_state(self):
        ledger = ProductionLedger("movie")
        ledger.add_shot(self.generated_shot(True))
        ledger.shots["S1"].reviews = {
            Gate.VISUAL_QA: self.review(Gate.VISUAL_QA)
        }
        ledger.submit_generation(
            job_id="J1",
            shot_id="S1",
            idempotency_key="K1",
            input_fingerprint="F1",
        )
        self.authorize_and_start(ledger)
        restored = ProductionLedger.from_dict(copy.deepcopy(ledger.to_dict()))
        self.assertEqual(ledger.to_dict(), restored.to_dict())
        self.assertIs(restored.jobs["J1"].status, JobStatus.RUNNING)
        self.assertEqual({}, restored.shots["S1"].reviews)

    def test_corrupted_generation_job_map_rejects_key_mismatch(self):
        ledger = ProductionLedger("movie")
        ledger.add_shot(Shot("S1"))
        ledger.submit_generation(
            job_id="J1",
            shot_id="S1",
            idempotency_key="K1",
            input_fingerprint="F1",
        )
        data = ledger.to_dict()
        data["generation_jobs"]["J2"] = data["generation_jobs"].pop("J1")
        with self.assertRaisesRegex(ProductionPolicyError, "job key/id mismatch"):
            ProductionLedger.from_dict(data)

    def test_corrupted_generation_job_map_rejects_unknown_shot(self):
        ledger = ProductionLedger("movie")
        ledger.add_shot(Shot("S1"))
        ledger.submit_generation(
            job_id="J1",
            shot_id="S1",
            idempotency_key="K1",
            input_fingerprint="F1",
        )
        data = ledger.to_dict()
        data["generation_jobs"]["J1"]["shot_id"] = "nonexistent"
        with self.assertRaisesRegex(ProductionPolicyError, "job references unknown shot"):
            ProductionLedger.from_dict(data)

    def test_corrupted_generation_job_map_rejects_duplicate_idempotency_key(self):
        ledger = ProductionLedger("movie")
        ledger.add_shot(Shot("S1"))
        ledger.add_shot(Shot("S2"))
        ledger.submit_generation(
            job_id="J1",
            shot_id="S1",
            idempotency_key="K1",
            input_fingerprint="F1",
        )
        ledger.submit_generation(
            job_id="J2",
            shot_id="S2",
            idempotency_key="K2",
            input_fingerprint="F2",
        )
        data = ledger.to_dict()
        data["generation_jobs"]["J2"]["idempotency_key"] = "K1"
        del data["idempotency_index"]["K2"]
        with self.assertRaisesRegex(ProductionPolicyError, "duplicate job idempotency key"):
            ProductionLedger.from_dict(data)
    def test_corrupted_generation_job_map_rejects_invalid_attempt_count(self):
        ledger = ProductionLedger("movie")
        ledger.add_shot(Shot("S1"))
        ledger.submit_generation(
            job_id="J1",
            shot_id="S1",
            idempotency_key="K1",
            input_fingerprint="F1",
        )
        data = ledger.to_dict()
        data["generation_jobs"]["J1"]["attempts"] = 1
        with self.assertRaisesRegex(ProductionPolicyError, "attempt counter/history mismatch"):
            ProductionLedger.from_dict(data)

        data = ledger.to_dict()
        data["generation_jobs"]["J1"]["attempts"] = 5
        data["generation_jobs"]["J1"]["max_attempts"] = 3
        with self.assertRaisesRegex(ProductionPolicyError, "invalid job attempt count"):
            ProductionLedger.from_dict(data)

    def test_corrupt_resume_state_is_rejected(self):
        ledger = ProductionLedger("movie")
        ledger.add_shot(Shot("S1"))
        data = ledger.to_dict()
        data["idempotency_index"]["K1"] = "missing"
        with self.assertRaises(ProductionPolicyError):
            ProductionLedger.from_dict(data)

    def test_checkpoint_root_matches_published_schema_contract(self):
        ledger = ProductionLedger("movie")
        shot = self.generated_shot()
        shot.reviews = {Gate.VISUAL_QA: self.review(Gate.VISUAL_QA)}
        ledger.add_shot(shot)
        ledger.submit_generation(
            job_id="J1",
            shot_id="S1",
            idempotency_key="K1",
            input_fingerprint="F1",
        )
        payload = ledger.to_dict()
        schema_path = (
            Path(__file__).parents[1]
            / "schemas"
            / "movie_studio_production_state__created_by_chatgpt__model_gpt-5.6-sol__task_TASK-0005.schema.json"
        )
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self.assertEqual(set(schema["properties"]), set(payload))
        self.assertTrue(set(schema["required"]).issubset(payload))
        self.assertEqual(
            set(schema["$defs"]["movie_bible"]["properties"]),
            set(payload["movie_bible"]),
        )
        self.assertEqual(
            set(schema["$defs"]["shot"]["properties"]),
            set(payload["shots"]["S1"]),
        )
        self.assertEqual(
            set(schema["$defs"]["review"]["properties"]),
            set(payload["shots"]["S1"]["reviews"]["VISUAL_QA"]),
        )
        self.assertEqual(
            set(schema["$defs"]["generation_job"]["properties"]),
            set(payload["generation_jobs"]["J1"]),
        )
        ledger.authorize_attempt(
            "J1",
            self.register_provider(ledger),
        )
        authorized_payload = ledger.to_dict()
        attempt = authorized_payload["generation_jobs"]["J1"]["attempt_history"][0]
        self.assertEqual(
            set(schema["$defs"]["generation_attempt"]["properties"]),
            set(attempt),
        )
        self.assertEqual(
            set(schema["$defs"]["attempt_authorization"]["properties"]),
            set(attempt["authorization"]),
        )

    def test_future_schema_version_fails_closed(self):
        data = ProductionLedger("movie").to_dict()
        data["schema_version"] = 3
        with self.assertRaises(ProductionPolicyError):
            ProductionLedger.from_dict(data)

    def test_changed_production_contract_fails_closed(self):
        data = ProductionLedger("movie").to_dict()
        data["production"]["aspect_ratio"] = "9:16"
        with self.assertRaises(ProductionPolicyError):
            ProductionLedger.from_dict(data)

    def test_map_key_must_match_embedded_id(self):
        ledger = ProductionLedger("movie")
        ledger.add_shot(Shot("S1"))
        data = ledger.to_dict()
        data["shots"]["S1"]["shot_id"] = "S2"
        with self.assertRaises(ProductionPolicyError):
            ProductionLedger.from_dict(data)

    def test_idempotency_index_must_be_exact_bijection(self):
        ledger = ProductionLedger("movie")
        ledger.add_shot(Shot("S1"))
        ledger.submit_generation(
            job_id="J1",
            shot_id="S1",
            idempotency_key="K1",
            input_fingerprint="F1",
        )
        data = ledger.to_dict()
        data["idempotency_index"].clear()
        with self.assertRaises(ProductionPolicyError):
            ProductionLedger.from_dict(data)

    def test_negative_attempts_are_rejected_on_restore(self):
        ledger = ProductionLedger("movie")
        ledger.add_shot(Shot("S1"))
        ledger.submit_generation(
            job_id="J1",
            shot_id="S1",
            idempotency_key="K1",
            input_fingerprint="F1",
        )
        data = ledger.to_dict()
        data["generation_jobs"]["J1"]["attempts"] = -100
        with self.assertRaises(ProductionPolicyError):
            ProductionLedger.from_dict(data)

    def test_impossible_succeeded_job_is_rejected_on_restore(self):
        ledger = ProductionLedger("movie")
        ledger.add_shot(Shot("S1"))
        ledger.submit_generation(
            job_id="J1",
            shot_id="S1",
            idempotency_key="K1",
            input_fingerprint="F1",
        )
        data = ledger.to_dict()
        data["generation_jobs"]["J1"]["status"] = JobStatus.SUCCEEDED.value
        with self.assertRaises(ProductionPolicyError):
            ProductionLedger.from_dict(data)

    def test_second_active_job_for_same_shot_is_rejected(self):
        ledger = ProductionLedger("movie")
        ledger.add_shot(Shot("S1"))
        ledger.submit_generation(
            job_id="J1",
            shot_id="S1",
            idempotency_key="K1",
            input_fingerprint="F1",
        )
        with self.assertRaises(ProductionPolicyError):
            ledger.submit_generation(
                job_id="J2",
                shot_id="S1",
                idempotency_key="K2",
                input_fingerprint="F2",
            )

    def test_generation_start_invalidates_prior_approvals(self):
        ledger = ProductionLedger("movie")
        shot = self.generated_shot()
        shot.reviews = {gate: self.review(gate) for gate in Gate}
        shot.canonical = True
        shot.status = ShotStatus.CANONICAL
        shot.upscale_allowed = True
        ledger.add_shot(shot)
        ledger.submit_generation(
            job_id="J1",
            shot_id="S1",
            idempotency_key="K1",
            input_fingerprint="F1",
        )
        ledger.authorize_attempt(
            "J1",
            self.register_provider(ledger),
        )
        self.assertEqual({}, shot.reviews)
        self.assertFalse(shot.canonical)
        self.assertFalse(shot.upscale_allowed)
        self.assertIs(shot.status, ShotStatus.GENERATING)

    def test_zero_cost_routing_rejects_zero_like_non_integer(self):
        with self.assertRaises(ProductionPolicyError):
            choose_zero_cost_provider([self.quote("invalid", estimated=False)])
        with self.assertRaises(ProductionPolicyError):
            choose_zero_cost_provider([self.quote("")])
        with self.assertRaises(ProductionPolicyError):
            choose_zero_cost_provider([self.quote("   ")])
        with self.assertRaises(ProductionPolicyError):
            choose_zero_cost_provider([self.quote("invalid", available="yes")])

    def test_schema_boolean_constants_and_unknown_fields_fail_closed(self):
        ledger = ProductionLedger("movie")
        ledger.add_shot(Shot("S1"))
        baseline = ledger.to_dict()
        mutations = []
        data = copy.deepcopy(baseline)
        data["schema_version"] = True
        mutations.append(data)
        data = copy.deepcopy(baseline)
        data["spend_limit_usd_micros"] = False
        mutations.append(data)
        data = copy.deepcopy(baseline)
        data["unexpected"] = "field"
        mutations.append(data)
        data = copy.deepcopy(baseline)
        data["shots"]["S1"]["unexpected"] = "field"
        mutations.append(data)
        for data in mutations:
            with self.subTest(data=data), self.assertRaises(ProductionPolicyError):
                ProductionLedger.from_dict(data)
        with self.assertRaises(ProductionPolicyError):
            ProductionLedger(True).to_dict()

    def test_empty_ids_and_inconsistent_shot_lifecycle_fail_closed(self):
        ledger = ProductionLedger("movie")
        ledger.add_shot(Shot("S1"))
        ledger.submit_generation(
            job_id="J1",
            shot_id="S1",
            idempotency_key="K1",
            input_fingerprint="F1",
        )
        baseline = ledger.to_dict()
        mutations = []
        data = copy.deepcopy(baseline)
        raw_shot = data["shots"].pop("S1")
        raw_shot["shot_id"] = ""
        data["shots"][""] = raw_shot
        data["generation_jobs"]["J1"]["shot_id"] = ""
        mutations.append(data)
        data = copy.deepcopy(baseline)
        raw_job = data["generation_jobs"].pop("J1")
        raw_job["job_id"] = ""
        data["generation_jobs"][""] = raw_job
        data["idempotency_index"]["K1"] = ""
        mutations.append(data)
        data = copy.deepcopy(baseline)
        data["generation_jobs"]["J1"]["idempotency_key"] = ""
        data["idempotency_index"] = {"": "J1"}
        mutations.append(data)
        data = copy.deepcopy(baseline)
        data["generation_jobs"]["J1"]["input_fingerprint"] = ""
        mutations.append(data)
        data = copy.deepcopy(baseline)
        data["shots"]["S1"]["status"] = ShotStatus.GENERATED.value
        mutations.append(data)
        data = copy.deepcopy(baseline)
        data["shots"]["S1"]["status"] = ShotStatus.GENERATING.value
        mutations.append(data)
        for data in mutations:
            with self.subTest(data=data), self.assertRaises(ProductionPolicyError):
                ProductionLedger.from_dict(data)

    def test_attempt_authorization_rejects_ineligible_quotes(self):
        ledger = ProductionLedger("movie")
        ledger.add_shot(Shot("S1"))
        ledger.submit_generation(
            job_id="J1",
            shot_id="S1",
            idempotency_key="K1",
            input_fingerprint="F1",
        )
        quotes = [
            self.quote(estimated=1),
            self.quote(maximum=1),
            self.quote(available=False),
            self.quote(cloud=False),
            self.quote(cap=False),
            self.quote(fingerprint="changed"),
        ]
        for quote in quotes:
            with self.subTest(quote=quote), self.assertRaises(ProductionPolicyError):
                ledger.authorize_attempt(
                    "J1",
                    quote,
                )

    def test_checkpoint_rejects_corrupt_in_memory_enum_values(self):
        shot_status_ledger = ProductionLedger("movie")
        shot = Shot("S1")
        shot.status = "NOT_A_STATUS"
        shot_status_ledger.add_shot(shot)

        job_status_ledger = ProductionLedger("movie")
        job_status_ledger.add_shot(Shot("S1"))
        job = job_status_ledger.submit_generation(
            job_id="J1",
            shot_id="S1",
            idempotency_key="K1",
            input_fingerprint="F1",
        )
        job.status = "BOGUS"

        review_ledger = ProductionLedger("movie")
        review_shot = self.generated_shot()
        review_shot.reviews = {
            Gate.VISUAL_QA: Review(Gate.VISUAL_QA, "v1", "BOGUS")
        }
        review_ledger.add_shot(review_shot)

        for ledger in (shot_status_ledger, job_status_ledger, review_ledger):
            with self.subTest(ledger=ledger), self.assertRaises(
                ProductionPolicyError
            ):
                ledger.to_dict()

    def test_start_requires_persisted_zero_cost_authorization(self):
        ledger = ProductionLedger("movie")
        ledger.add_shot(Shot("S1"))
        ledger.submit_generation(
            job_id="J1",
            shot_id="S1",
            idempotency_key="K1",
            input_fingerprint="F1",
        )
        with self.assertRaises(ProductionPolicyError):
            ledger.start_generation(
                "J1",
                ProviderSubmissionReceipt(
                    "missing", "J1", 1, "p", "a", "1", "r", "provider-1"
                ),
            )

    def test_authorization_replay_is_exactly_idempotent(self):
        ledger = ProductionLedger("movie")
        ledger.add_shot(Shot("S1"))
        ledger.submit_generation(
            job_id="J1",
            shot_id="S1",
            idempotency_key="K1",
            input_fingerprint="F1",
        )
        first = ledger.authorize_attempt(
            "J1",
            self.register_provider(ledger),
        )
        second = ledger.authorize_attempt(
            "J1",
            self.register_provider(ledger),
        )
        self.assertEqual(first, second)
        self.assertEqual(1, len(ledger.jobs["J1"].attempt_history))
        with self.assertRaises(ProductionPolicyError):
            ledger.authorize_attempt(
                "J1",
                self.register_provider(ledger, provider="changed"),
            )

    def test_retry_preserves_first_attempt_and_rejects_late_callback(self):
        ledger = ProductionLedger("movie")
        ledger.add_shot(Shot("S1"))
        ledger.submit_generation(
            job_id="J1",
            shot_id="S1",
            idempotency_key="K1",
            input_fingerprint="F1",
            max_attempts=2,
        )
        first_number = self.authorize_and_start(ledger, provider_job_id="P1")
        ledger.fail_generation(
            "J1",
            first_number,
            "P1",
            FailureClass.RETRYABLE_PROVIDER,
            "retry",
        )
        first_snapshot = ledger.jobs["J1"].attempt_history[0]
        second_number = self.authorize_and_start(
            ledger, provider_job_id="P2", provider="zfree"
        )
        with self.assertRaises(ProductionPolicyError):
            ledger.finish_generation("J1", first_number, "P1", "stale")
        self.assertEqual(first_snapshot, ledger.jobs["J1"].attempt_history[0])
        self.assertEqual("", ledger.shots["S1"].asset_version)
        ledger.finish_generation("J1", second_number, "P2", "fresh")
        self.assertEqual("fresh", ledger.shots["S1"].asset_version)

    def test_provider_job_id_must_match_current_attempt(self):
        ledger = ProductionLedger("movie")
        ledger.add_shot(Shot("S1"))
        ledger.submit_generation(
            job_id="J1",
            shot_id="S1",
            idempotency_key="K1",
            input_fingerprint="F1",
        )
        attempt = self.authorize_and_start(ledger, provider_job_id="P1")
        with self.assertRaises(ProductionPolicyError):
            ledger.finish_generation("J1", attempt, "wrong", "asset")

    def test_duplicate_terminal_callback_is_idempotent_but_conflict_rejects(self):
        ledger = ProductionLedger("movie")
        ledger.add_shot(Shot("S1"))
        ledger.submit_generation(
            job_id="J1",
            shot_id="S1",
            idempotency_key="K1",
            input_fingerprint="F1",
        )
        attempt = self.authorize_and_start(ledger, provider_job_id="P1")
        ledger.finish_generation("J1", attempt, "P1", "asset")
        before = ledger.to_dict()
        ledger.finish_generation("J1", attempt, "P1", "asset")
        self.assertEqual(before, ledger.to_dict())
        with self.assertRaises(ProductionPolicyError):
            ledger.finish_generation("J1", attempt, "P1", "changed")

    def test_non_retryable_failure_exhausts_immediately(self):
        ledger = ProductionLedger("movie")
        ledger.add_shot(Shot("S1"))
        ledger.submit_generation(
            job_id="J1",
            shot_id="S1",
            idempotency_key="K1",
            input_fingerprint="F1",
            max_attempts=3,
        )
        attempt = self.authorize_and_start(ledger, provider_job_id="P1")
        ledger.fail_generation(
            "J1",
            attempt,
            "P1",
            FailureClass.POLICY,
            "policy rejected",
        )
        self.assertIs(ledger.jobs["J1"].status, JobStatus.EXHAUSTED)
        with self.assertRaises(ProductionPolicyError):
            ledger.authorize_attempt(
                "J1",
                self.register_provider(ledger),
            )

    def test_authorized_and_running_checkpoints_round_trip(self):
        ledger = ProductionLedger("movie")
        ledger.add_shot(Shot("S1"))
        ledger.submit_generation(
            job_id="J1",
            shot_id="S1",
            idempotency_key="K1",
            input_fingerprint="F1",
        )
        ledger.authorize_attempt(
            "J1",
            self.register_provider(ledger),
        )
        restored = ProductionLedger.from_dict(copy.deepcopy(ledger.to_dict()))
        self.assertEqual(ledger.to_dict(), restored.to_dict())
        authorization = restored.jobs["J1"].attempt_history[-1].authorization
        restored.start_generation(
            "J1",
            ProviderSubmissionReceipt(
                authorization.authorization_id,
                "J1",
                1,
                authorization.provider_id,
                authorization.adapter_id,
                authorization.adapter_version,
                authorization.provider_request_key,
                "P1",
            ),
        )
        running = ProductionLedger.from_dict(copy.deepcopy(restored.to_dict()))
        self.assertEqual(restored.to_dict(), running.to_dict())
        with self.assertRaises(ProductionPolicyError):
            running.authorize_attempt(
                "J1",
                self.register_provider(running),
            )

    def test_corrupt_attempt_indexes_and_history_fail_closed(self):
        ledger = ProductionLedger("movie")
        ledger.add_shot(Shot("S1"))
        ledger.submit_generation(
            job_id="J1",
            shot_id="S1",
            idempotency_key="K1",
            input_fingerprint="F1",
        )
        ledger.authorize_attempt(
            "J1",
            self.register_provider(ledger),
        )
        baseline = ledger.to_dict()
        corruptions = []
        data = copy.deepcopy(baseline)
        data["authorization_index"].clear()
        corruptions.append(data)
        data = copy.deepcopy(baseline)
        data["provider_request_index"].clear()
        corruptions.append(data)
        data = copy.deepcopy(baseline)
        data["generation_jobs"]["J1"]["attempt_history"][0]["attempt_number"] = 2
        corruptions.append(data)
        data = copy.deepcopy(baseline)
        data["generation_jobs"]["J1"]["attempt_history"][0]["authorization"][
            "maximum_cost_usd_micros"
        ] = 1
        corruptions.append(data)
        for data in corruptions:
            with self.subTest(data=data), self.assertRaises(ProductionPolicyError):
                ProductionLedger.from_dict(data)

    def test_submission_receipt_must_match_authorized_adapter_and_request(self):
        ledger = ProductionLedger("movie")
        ledger.add_shot(Shot("S1"))
        ledger.submit_generation(
            job_id="J1",
            shot_id="S1",
            idempotency_key="K1",
            input_fingerprint="F1",
        )
        authorization = ledger.authorize_attempt(
            "J1",
            self.register_provider(ledger),
        )
        before = ledger.to_dict()
        bad_receipt = ProviderSubmissionReceipt(
            authorization_id=authorization.authorization_id,
            job_id="J1",
            attempt_number=1,
            provider_id=authorization.provider_id,
            adapter_id="wrong-adapter",
            adapter_version=authorization.adapter_version,
            provider_request_key=authorization.provider_request_key,
            provider_job_id="P1",
        )
        with self.assertRaises(ProductionPolicyError):
            ledger.start_generation("J1", bad_receipt)
        self.assertEqual(before, ledger.to_dict())

    def test_provider_receipt_is_idempotent_and_rejects_blank_job_id(self):
        ledger = ProductionLedger("movie")
        ledger.add_shot(Shot("S1"))
        ledger.submit_generation(
            job_id="J1",
            shot_id="S1",
            idempotency_key="K1",
            input_fingerprint="F1",
        )
        authorization = ledger.authorize_attempt("J1", self.register_provider(ledger))
        blank = ProviderSubmissionReceipt(
            authorization.authorization_id,
            "J1",
            1,
            authorization.provider_id,
            authorization.adapter_id,
            authorization.adapter_version,
            authorization.provider_request_key,
            "   ",
        )
        with self.assertRaises(ProductionPolicyError):
            ledger.start_generation("J1", blank)
        receipt = ProviderSubmissionReceipt(
            authorization.authorization_id,
            "J1",
            1,
            authorization.provider_id,
            authorization.adapter_id,
            authorization.adapter_version,
            authorization.provider_request_key,
            "P1",
        )
        ledger.start_generation("J1", receipt)
        checkpoint = ledger.to_dict()
        restored = ProductionLedger.from_dict(copy.deepcopy(checkpoint))
        restored.start_generation("J1", receipt)
        self.assertEqual(checkpoint, restored.to_dict())

    def test_derived_authorization_identifiers_are_verified_on_restore(self):
        ledger = ProductionLedger("movie")
        ledger.add_shot(Shot("S1"))
        ledger.submit_generation(
            job_id="J1",
            shot_id="S1",
            idempotency_key="K1",
            input_fingerprint="F1",
        )
        ledger.authorize_attempt("J1", self.register_provider(ledger))
        baseline = ledger.to_dict()
        for field, index_name, forged in (
            ("authorization_id", "authorization_index", "forged-auth"),
            ("provider_request_key", "provider_request_index", "forged-request"),
        ):
            data = copy.deepcopy(baseline)
            authorization = data["generation_jobs"]["J1"]["attempt_history"][0][
                "authorization"
            ]
            old_value = authorization[field]
            authorization[field] = forged
            ref = data[index_name].pop(old_value)
            data[index_name][forged] = ref
            with self.subTest(field=field), self.assertRaises(ProductionPolicyError):
                ProductionLedger.from_dict(data)

    def test_start_revalidates_ledger_authenticated_zero_cost_authorization(self):
        ledger = ProductionLedger("movie")
        ledger.add_shot(Shot("S1"))
        ledger.submit_generation(
            job_id="J1",
            shot_id="S1",
            idempotency_key="K1",
            input_fingerprint="F1",
        )
        authorization = ledger.authorize_attempt("J1", self.register_provider(ledger))
        ledger.jobs["J1"].attempt_history = (
            ledger.jobs["J1"].attempt_history[0].__class__(
                attempt_number=1,
                authorization=authorization.__class__(
                    **{
                        **authorization.__dict__,
                        "maximum_cost_usd_micros": 1,
                    }
                ),
                status=AttemptStatus.AUTHORIZED,
            ),
        )
        receipt = ProviderSubmissionReceipt(
            authorization.authorization_id,
            "J1",
            1,
            authorization.provider_id,
            authorization.adapter_id,
            authorization.adapter_version,
            authorization.provider_request_key,
            "P1",
        )
        with self.assertRaises(ProductionPolicyError):
            ledger.start_generation("J1", receipt)

    def test_failed_authorization_preflight_does_not_claim_shot_epoch(self):
        ledger = ProductionLedger("movie")
        ledger.add_shot(Shot("S1"))
        job = ledger.submit_generation(
            job_id="J1",
            shot_id="S1",
            idempotency_key="K1",
            input_fingerprint="F1",
        )
        job.max_attempts = 0
        self.register_provider(ledger)
        before = copy.deepcopy(ledger)
        with self.assertRaises(ProductionPolicyError):
            ledger.authorize_attempt("J1", self.register_provider(ledger))
        self.assertEqual(before, ledger)

    def test_generation_owner_lifecycle_is_verified_on_restore(self):
        ledger = ProductionLedger("movie")
        ledger.add_shot(Shot("S1"))
        ledger.submit_generation(
            job_id="J1",
            shot_id="S1",
            idempotency_key="K1",
            input_fingerprint="F1",
        )
        self.authorize_and_start(ledger, provider_job_id="P1")
        ledger.finish_generation("J1", 1, "P1", "asset-good")
        succeeded = ledger.to_dict()
        corruptions = []
        data = copy.deepcopy(succeeded)
        data["shots"]["S1"]["asset_version"] = "asset-substituted"
        corruptions.append(data)
        data = copy.deepcopy(succeeded)
        data["shots"]["S1"].update(status="PLANNED", asset_version="")
        corruptions.append(data)
        data = copy.deepcopy(succeeded)
        data["shots"]["S1"].update(
            generation_epoch=0, generation_owner_job_id=None
        )
        corruptions.append(data)
        for data in corruptions:
            with self.subTest(data=data), self.assertRaises(ProductionPolicyError):
                ProductionLedger.from_dict(data)

    def test_provider_request_key_is_project_namespaced(self):
        authorizations = []
        for project_id in ("movie-a", "movie-b"):
            ledger = ProductionLedger(project_id)
            ledger.add_shot(Shot("S1"))
            ledger.submit_generation(
                job_id="J1",
                shot_id="S1",
                idempotency_key="K1",
                input_fingerprint="F1",
            )
            authorizations.append(ledger.authorize_attempt("J1", self.register_provider(ledger)))
        self.assertNotEqual(
            authorizations[0].provider_request_key,
            authorizations[1].provider_request_key,
        )

    def test_historical_generation_epochs_are_bound_and_contiguous(self):
        ledger = ProductionLedger("movie")
        ledger.add_shot(Shot("S1"))
        for number in (1, 2):
            job_id = f"J{number}"
            ledger.submit_generation(
                job_id=job_id,
                shot_id="S1",
                idempotency_key=f"K{number}",
                input_fingerprint="F1",
                max_attempts=1,
            )
            self.authorize_and_start(
                ledger, job_id=job_id, provider_job_id=f"P{number}"
            )
            ledger.fail_generation(
                job_id,
                1,
                f"P{number}",
                FailureClass.RETRYABLE_PROVIDER,
                "exhausted",
            )
        ledger.submit_generation(
            job_id="J3",
            shot_id="S1",
            idempotency_key="K3",
            input_fingerprint="F1",
        )
        ledger.authorize_attempt("J3", self.register_provider(ledger))
        baseline = ledger.to_dict()
        self.assertEqual(
            [1, 2, 3],
            [baseline["generation_jobs"][f"J{i}"]["generation_epoch"] for i in (1, 2, 3)],
        )
        for replacement in (1, 4):
            data = copy.deepcopy(baseline)
            data["generation_jobs"]["J2"]["generation_epoch"] = replacement
            data["generation_jobs"]["J2"]["attempt_history"][0]["authorization"][
                "generation_epoch"
            ] = replacement
            with self.subTest(replacement=replacement), self.assertRaises(
                ProductionPolicyError
            ):
                ProductionLedger.from_dict(data)

    def test_old_job_epoch_callback_cannot_mutate_new_job(self):
        ledger = ProductionLedger("movie")
        ledger.add_shot(Shot("S1"))
        ledger.submit_generation(
            job_id="J1",
            shot_id="S1",
            idempotency_key="K1",
            input_fingerprint="F1",
            max_attempts=1,
        )
        attempt = self.authorize_and_start(ledger, provider_job_id="P1")
        ledger.fail_generation(
            "J1",
            attempt,
            "P1",
            FailureClass.RETRYABLE_PROVIDER,
            "exhausted",
        )
        ledger.submit_generation(
            job_id="J2",
            shot_id="S1",
            idempotency_key="K2",
            input_fingerprint="F1",
        )
        self.authorize_and_start(
            ledger, job_id="J2", provider_job_id="P2", provider="zfree"
        )
        before = ledger.to_dict()
        with self.assertRaises(ProductionPolicyError):
            ledger.finish_generation("J1", 1, "P1", "stale")
        self.assertEqual(before, ledger.to_dict())

    def test_lifecycle_status_defaults(self):
        ledger = ProductionLedger("movie")
        self.assertEqual(ledger.episode_status, "PLANNED")
        scene = Scene("SC1")
        ledger.add_scene(scene)
        self.assertEqual(ledger.scenes["SC1"].status, "PLANNED")

    def test_lifecycle_status_roundtrip_default(self):
        ledger = ProductionLedger("movie")
        ledger.add_scene(Scene("SC1"))
        payload = ledger.to_dict()
        restored = ProductionLedger.from_dict(payload)
        self.assertEqual(restored.episode_status, "PLANNED")
        self.assertEqual(restored.scenes["SC1"].status, "PLANNED")

    def test_lifecycle_status_roundtrip_non_default(self):
        from scripts.movie_studio_core__created_by_chatgpt__model_gpt_5_6_sol__task_TASK_0005 import EpisodeStatus, SceneStatus
        ledger = ProductionLedger("movie")
        ledger.episode_status = EpisodeStatus.IN_PRODUCTION
        scene = Scene("SC1")
        scene.status = SceneStatus.GENERATING
        ledger.add_scene(scene)
        payload = ledger.to_dict()
        restored = ProductionLedger.from_dict(payload)
        self.assertEqual(restored.episode_status, EpisodeStatus.IN_PRODUCTION)
        self.assertEqual(restored.scenes["SC1"].status, SceneStatus.GENERATING)

    def test_unknown_lifecycle_status_fails_closed(self):
        ledger = ProductionLedger("movie")
        ledger.add_scene(Scene("SC1"))
        payload = ledger.to_dict()

        payload_malformed_ep = dict(payload)
        payload_malformed_ep["episode_status"] = "FAKE_STATUS"
        with self.assertRaises(ProductionPolicyError):
            ProductionLedger.from_dict(payload_malformed_ep)

        payload_malformed_scene = dict(payload)
        payload_malformed_scene["scenes"] = {"SC1": {"scene_id": "SC1", "status": "FAKE_STATUS"}}
        with self.assertRaises(ProductionPolicyError):
            ProductionLedger.from_dict(payload_malformed_scene)


    def test_register_provider_adapter_success_and_duplicates(self):
        ledger = ProductionLedger("movie")
        adapter = ProviderAdapterRegistration(
            provider_id="prov1",
            adapter_id="ad1",
            adapter_version="v1",
            capabilities=frozenset([ProviderCapability.VIDEO])
        )
        ledger.register_provider_adapter(adapter)
        # exact duplicate is fine
        ledger.register_provider_adapter(adapter)

        # conflicting duplicate fails
        conflict = ProviderAdapterRegistration(
            provider_id="prov1",
            adapter_id="ad1",
            adapter_version="v1",
            capabilities=frozenset([ProviderCapability.TEXT])
        )
        with self.assertRaises(ProductionPolicyError) as cx:
            ledger.register_provider_adapter(conflict)
        self.assertIn("conflicting", str(cx.exception))

    def test_register_provider_adapter_rejects_invalid_values(self):
        ledger = ProductionLedger("movie")
        # missing capability
        with self.assertRaises(ProductionPolicyError):
            ledger.register_provider_adapter(ProviderAdapterRegistration(
                "prov", "ad", "v1", frozenset()
            ))
        # local execution
        with self.assertRaises(ProductionPolicyError):
            ledger.register_provider_adapter(ProviderAdapterRegistration(
                "prov", "ad", "v1", frozenset([ProviderCapability.VIDEO]), cloud_execution=False
            ))
        # no charge cap
        with self.assertRaises(ProductionPolicyError):
            ledger.register_provider_adapter(ProviderAdapterRegistration(
                "prov", "ad", "v1", frozenset([ProviderCapability.VIDEO]), charge_cap_enforced=False
            ))
        # non-zero cost ceiling
        with self.assertRaises(ProductionPolicyError):
            ledger.register_provider_adapter(ProviderAdapterRegistration(
                "prov", "ad", "v1", frozenset([ProviderCapability.VIDEO]), maximum_cost_usd_micros=100
            ))
        # blank identifiers
        with self.assertRaises(ProductionPolicyError):
            ledger.register_provider_adapter(ProviderAdapterRegistration(
                "", "ad", "v1", frozenset([ProviderCapability.VIDEO])
            ))
        # capability collection must be an enum-only frozenset
        with self.assertRaises(ProductionPolicyError):
            ledger.register_provider_adapter(ProviderAdapterRegistration(
                "prov", "ad", "v1", frozenset(["VIDEO"])
            ))
        with self.assertRaises(ProductionPolicyError):
            ledger.register_provider_adapter(ProviderAdapterRegistration(
                "prov", "ad", "v1", [ProviderCapability.VIDEO]
            ))

    def test_provider_registry_validation_rejects_corrupted_capabilities(self):
        ledger = ProductionLedger("movie")
        ledger.provider_adapters["prov:ad:v1"] = ProviderAdapterRegistration(
            "prov", "ad", "v1", frozenset(["VIDEO"])
        )
        with self.assertRaises(ProductionPolicyError):
            ledger.validate()

    def test_provider_registry_migration_safe_restore(self):
        ledger = ProductionLedger("movie")
        adapter = ProviderAdapterRegistration(
            provider_id="prov1",
            adapter_id="ad1",
            adapter_version="v1",
            capabilities=frozenset([ProviderCapability.VIDEO])
        )
        ledger.register_provider_adapter(adapter)
        data = ledger.to_dict()

        # remove it to simulate old checkpoint
        del data["provider_adapters"]
        restored = ProductionLedger.from_dict(data)
        self.assertEqual(restored.provider_adapters, {})

        # full restore
        data2 = ledger.to_dict()
        restored2 = ProductionLedger.from_dict(data2)
        self.assertIn("prov1:ad1:v1", restored2.provider_adapters)
        self.assertEqual(restored2.provider_adapters["prov1:ad1:v1"], adapter)

    def test_authorize_attempt_blocked_without_registration(self):
        ledger = ProductionLedger("movie")
        ledger.add_scene(Scene("SC1"))
        ledger.add_shot(Shot("S1", scene_id="SC1"))
        job = ledger.submit_generation(
            job_id="J1",
            shot_id="S1",
            idempotency_key="ik1",
            input_fingerprint="fp1"
        )
        quote = ProviderQuote(
            quote_id="q1", provider_id="p1", adapter_id="a1", adapter_version="v1",
            request_fingerprint="fp1", estimated_cost_usd_micros=0,
            maximum_cost_usd_micros=0, available=True, cloud_execution=True, charge_cap_enforced=True
        )
        with self.assertRaises(ProductionPolicyError) as cx:
            ledger.authorize_attempt("J1", quote)
        self.assertIn("provider adapter not registered", str(cx.exception))

        # Register and try again
        ledger.register_provider_adapter(ProviderAdapterRegistration(
            "p1", "a1", "v1", frozenset([ProviderCapability.VIDEO])
        ))
        auth = ledger.authorize_attempt("J1", quote)
        self.assertEqual(auth.job_id, "J1")

        # Test capability enforcement
        ledger2 = ProductionLedger("movie")
        ledger2.add_scene(Scene("SC1"))
        ledger2.add_shot(Shot("S1", scene_id="SC1"))
        job = ledger2.submit_generation(
            job_id="J1", shot_id="S1", idempotency_key="ik1", input_fingerprint="fp1"
        )
        ledger2.register_provider_adapter(ProviderAdapterRegistration(
            "p1", "a1", "v1", frozenset([ProviderCapability.TEXT])
        ))
        with self.assertRaises(ProductionPolicyError) as cx:
            ledger2.authorize_attempt("J1", quote)
        self.assertIn("does not support video generation", str(cx.exception))

    def test_corrupted_in_memory_enum_values_fail_validation(self):
        ledger = ProductionLedger("movie")
        ledger.add_scene(Scene("SC1"))
        ledger.episode_status = "NOT_AN_ENUM"
        with self.assertRaises(ProductionPolicyError):
            ledger.validate()

        ledger2 = ProductionLedger("movie")
        scene = Scene("SC1")
        scene.status = "NOT_AN_ENUM"
        ledger2.add_scene(scene)
        with self.assertRaises(ProductionPolicyError):
            ledger2.validate()



    def test_shot_plan_happy_path(self):
        ledger = ProductionLedger(project_id="test_proj")
        scene = Scene(scene_id="scn1")
        shot = Shot(shot_id="sht1", scene_id="scn1", has_dialogue_or_audio=True)
        ledger.add_scene(scene)
        ledger.add_shot(shot)

        plan = ShotPlan(
            shot_id="sht1",
            scene_id="scn1",
            sequence_index=0,
            planned_duration_ms=5000,
            prompt_fingerprint="prompt1",
            has_dialogue_or_audio=True
        )
        ledger.add_shot_plan(plan)
        self.assertIn("sht1", ledger.shot_plans)
        self.assertEqual(ledger.shot_plans["sht1"], plan)

        # Test exact duplicate is idempotent
        ledger.add_shot_plan(plan)
        self.assertEqual(len(ledger.shot_plans), 1)

    def test_shot_plan_rejects_conflicting_replacement(self):
        ledger = ProductionLedger(project_id="test_proj")
        scene = Scene(scene_id="scn1")
        shot = Shot(shot_id="sht1", scene_id="scn1", has_dialogue_or_audio=True)
        ledger.add_scene(scene)
        ledger.add_shot(shot)

        plan1 = ShotPlan(
            shot_id="sht1",
            scene_id="scn1",
            sequence_index=0,
            planned_duration_ms=5000,
            prompt_fingerprint="prompt1",
            has_dialogue_or_audio=True
        )
        ledger.add_shot_plan(plan1)

        plan2 = ShotPlan(
            shot_id="sht1",
            scene_id="scn1",
            sequence_index=1, # conflicting sequence
            planned_duration_ms=5000,
            prompt_fingerprint="prompt1",
            has_dialogue_or_audio=True
        )
        with self.assertRaisesRegex(ProductionPolicyError, "conflicting shot plan replacement"):
            ledger.add_shot_plan(plan2)

    def test_shot_plan_rejects_duplicate_sequence_index(self):
        ledger = ProductionLedger(project_id="test_proj")
        scene = Scene(scene_id="scn1")
        shot1 = Shot(shot_id="sht1", scene_id="scn1", has_dialogue_or_audio=True)
        shot2 = Shot(shot_id="sht2", scene_id="scn1", has_dialogue_or_audio=True)
        ledger.add_scene(scene)
        ledger.add_shot(shot1)
        ledger.add_shot(shot2)

        plan1 = ShotPlan(
            shot_id="sht1",
            scene_id="scn1",
            sequence_index=0,
            planned_duration_ms=5000,
            prompt_fingerprint="prompt1",
            has_dialogue_or_audio=True
        )
        ledger.add_shot_plan(plan1)

        plan2 = ShotPlan(
            shot_id="sht2",
            scene_id="scn1",
            sequence_index=0, # duplicate index within same scene
            planned_duration_ms=5000,
            prompt_fingerprint="prompt2",
            has_dialogue_or_audio=True
        )
        with self.assertRaisesRegex(ProductionPolicyError, "duplicate sequence_index"):
            ledger.add_shot_plan(plan2)

    def test_shot_plan_malformed_state(self):
        ledger = ProductionLedger(project_id="test_proj")
        scene = Scene(scene_id="scn1")
        shot = Shot(shot_id="sht1", scene_id="scn1", has_dialogue_or_audio=True)
        ledger.add_scene(scene)
        ledger.add_shot(shot)

        # Missing shot reference
        with self.assertRaisesRegex(ProductionPolicyError, "reference an existing shot"):
            ledger.add_shot_plan(ShotPlan("sht2", "scn1", 0, 1000, "prompt", True))

        # Mismatch has_dialogue
        with self.assertRaisesRegex(ProductionPolicyError, "has_dialogue_or_audio must match"):
            ledger.add_shot_plan(ShotPlan("sht1", "scn1", 0, 1000, "prompt", False))

        # Negative duration
        with self.assertRaisesRegex(ProductionPolicyError, "must be a positive integer"):
            ledger.add_shot_plan(ShotPlan("sht1", "scn1", 0, -100, "prompt", True))

        # Empty prompt fingerprint
        with self.assertRaisesRegex(ProductionPolicyError, "non-empty string"):
            ledger.add_shot_plan(ShotPlan("sht1", "scn1", 0, 1000, "", True))

    def test_shot_plan_digest_stability(self):
        plan = ShotPlan("sht1", "scn1", 0, 1000, "prompt", True)
        digest1 = plan_digest(plan)

        plan_same = ShotPlan("sht1", "scn1", 0, 1000, "prompt", True)
        digest2 = plan_digest(plan_same)
        self.assertEqual(digest1, digest2)

        plan_diff = ShotPlan("sht1", "scn1", 1, 1000, "prompt", True)
        digest3 = plan_digest(plan_diff)
        self.assertNotEqual(digest1, digest3)

    def test_shot_plan_roundtrip_and_migration_safe(self):
        # Initial empty ledger
        ledger = ProductionLedger(project_id="test_proj")
        data = ledger.to_dict()

        # Manually remove shot_plans for migration-safe test
        if "shot_plans" in data:
            del data["shot_plans"]

        restored_ledger = ProductionLedger.from_dict(data)
        self.assertEqual(restored_ledger.shot_plans, {})

        # Add plans
        scene = Scene(scene_id="scn1")
        shot = Shot(shot_id="sht1", scene_id="scn1", has_dialogue_or_audio=True)
        restored_ledger.add_scene(scene)
        restored_ledger.add_shot(shot)

        plan = ShotPlan("sht1", "scn1", 0, 1000, "prompt", True)
        restored_ledger.add_shot_plan(plan)

        # Roundtrip
        data2 = restored_ledger.to_dict()
        self.assertIn("sht1", data2["shot_plans"])

        restored2 = ProductionLedger.from_dict(data2)
        self.assertIn("sht1", restored2.shot_plans)
        self.assertEqual(restored2.shot_plans["sht1"], plan)

    def test_corrupted_shot_plan_restore_fails(self):
        ledger = ProductionLedger(project_id="test_proj")
        scene = Scene(scene_id="scn1")
        shot = Shot(shot_id="sht1", scene_id="scn1", has_dialogue_or_audio=True)
        ledger.add_scene(scene)
        ledger.add_shot(shot)
        plan = ShotPlan("sht1", "scn1", 0, 1000, "prompt", True)
        ledger.add_shot_plan(plan)

        data = ledger.to_dict()
        # Corrupt key
        data["shot_plans"]["sht2"] = data["shot_plans"].pop("sht1")

        with self.assertRaisesRegex(ProductionPolicyError, "key must match plan.shot_id"):
            ProductionLedger.from_dict(data)

    def test_continuity_binding_rejections(self):
        ledger = ProductionLedger(project_id="test-rejections")
        ledger.add_shot(Shot(shot_id="shot-1"))
        ledger.bible.characters["char-1"] = {}
        ledger.bible.voices["voice-1"] = {}

        binding_no_shot = ShotContinuityBinding(
            shot_id="unknown-shot", bible_revision=ledger.bible.revision,
            character_ids=frozenset(), voice_ids=frozenset(), location_id="",
            costume_ids=frozenset(), prop_ids=frozenset(), reference_asset_versions=frozenset()
        )
        with self.assertRaisesRegex(ProductionPolicyError, "binding must reference an existing shot"):
            ledger.add_shot_continuity_binding(binding_no_shot)

        binding_dangling = ShotContinuityBinding(
            shot_id="shot-1", bible_revision=ledger.bible.revision,
            character_ids=frozenset(["unknown-char"]), voice_ids=frozenset(), location_id="",
            costume_ids=frozenset(), prop_ids=frozenset(), reference_asset_versions=frozenset()
        )
        with self.assertRaisesRegex(ProductionPolicyError, "binding references unknown character"):
            ledger.add_shot_continuity_binding(binding_dangling)

        binding_voice_no_char = ShotContinuityBinding(
            shot_id="shot-1", bible_revision=ledger.bible.revision,
            character_ids=frozenset(), voice_ids=frozenset(["voice-1"]), location_id="",
            costume_ids=frozenset(), prop_ids=frozenset(), reference_asset_versions=frozenset()
        )
        with self.assertRaisesRegex(ProductionPolicyError, "voice continuity requires at least one character in the binding"):
            ledger.add_shot_continuity_binding(binding_voice_no_char)

        binding_wrong_rev = ShotContinuityBinding(
            shot_id="shot-1", bible_revision=999,
            character_ids=frozenset(), voice_ids=frozenset(), location_id="",
            costume_ids=frozenset(), prop_ids=frozenset(), reference_asset_versions=frozenset()
        )
        with self.assertRaisesRegex(ProductionPolicyError, "binding bible_revision must match current"):
            ledger.add_shot_continuity_binding(binding_wrong_rev)

        binding_ok = ShotContinuityBinding(
            shot_id="shot-1", bible_revision=ledger.bible.revision,
            character_ids=frozenset(["char-1"]), voice_ids=frozenset(), location_id="",
            costume_ids=frozenset(), prop_ids=frozenset(), reference_asset_versions=frozenset()
        )
        ledger.add_shot_continuity_binding(binding_ok)
        ledger.add_shot_continuity_binding(binding_ok) # Idempotent

        binding_conflict = ShotContinuityBinding(
            shot_id="shot-1", bible_revision=ledger.bible.revision,
            character_ids=frozenset(), voice_ids=frozenset(), location_id="",
            costume_ids=frozenset(), prop_ids=frozenset(), reference_asset_versions=frozenset()
        )
        with self.assertRaisesRegex(ProductionPolicyError, "conflicting continuity binding replacement is not allowed"):
            ledger.add_shot_continuity_binding(binding_conflict)

        # Digest stability test
        digest1 = continuity_binding_digest(binding_ok)
        digest2 = continuity_binding_digest(binding_ok)
        self.assertEqual(digest1, digest2)
        digest3 = continuity_binding_digest(binding_conflict)
        self.assertNotEqual(digest1, digest3)

    def test_continuity_binding_stale_and_future_mismatches(self):
        ledger = ProductionLedger(project_id="test-mismatches")
        ledger.add_shot(Shot(shot_id="shot-1"))
        ledger.bible.characters["char-1"] = {}

        # Correct binding
        binding_ok = ShotContinuityBinding(
            shot_id="shot-1", bible_revision=ledger.bible.revision,
            character_ids=frozenset(["char-1"]), voice_ids=frozenset(), location_id="",
            costume_ids=frozenset(), prop_ids=frozenset(), reference_asset_versions=frozenset()
        )
        ledger.add_shot_continuity_binding(binding_ok)

        data = ledger.to_dict()

        # Explicit plan revision is required before a bound Bible can change.
        ledger.freeze_plan()
        ledger.create_new_plan_revision()
        ledger.bible.update_fact("story_rules", "rule1", "value1")
        self.assertEqual(ledger.bible.revision, 3)

        # Try to restore with stale revision (2 instead of 3)
        data["movie_bible"]["revision"] = 3
        data["movie_bible"]["story_rules"]["rule1"] = "value1"
        data["shot_continuity_bindings"]["shot-1"]["bible_revision"] = 2
        with self.assertRaisesRegex(ProductionPolicyError, "binding bible_revision must match current movie-bible revision exactly"):
            ProductionLedger.from_dict(data)

        # Try to restore with future revision (4 instead of 3)
        data["shot_continuity_bindings"]["shot-1"]["bible_revision"] = 4
        with self.assertRaisesRegex(ProductionPolicyError, "binding bible_revision must match current movie-bible revision exactly"):
            ProductionLedger.from_dict(data)

    def test_continuity_binding_rejects_mutable_collections(self):
        ledger = ProductionLedger(project_id="test-mutable")
        ledger.add_shot(Shot(shot_id="shot-1"))
        ledger.bible.characters["char-1"] = {}

        # Test character_ids
        with self.assertRaisesRegex(ProductionPolicyError, "character_ids must be a frozenset"):
            ShotContinuityBinding(
                shot_id="shot-1", bible_revision=ledger.bible.revision,
                character_ids=set(["char-1"]), voice_ids=frozenset(), location_id="",
                costume_ids=frozenset(), prop_ids=frozenset(), reference_asset_versions=frozenset()
            )

        # Test voice_ids
        with self.assertRaisesRegex(ProductionPolicyError, "voice_ids must be a frozenset"):
            ShotContinuityBinding(
                shot_id="shot-1", bible_revision=ledger.bible.revision,
                character_ids=frozenset(["char-1"]), voice_ids=set(), location_id="",
                costume_ids=frozenset(), prop_ids=frozenset(), reference_asset_versions=frozenset()
            )

        # Test costume_ids
        with self.assertRaisesRegex(ProductionPolicyError, "costume_ids must be a frozenset"):
            ShotContinuityBinding(
                shot_id="shot-1", bible_revision=ledger.bible.revision,
                character_ids=frozenset(["char-1"]), voice_ids=frozenset(), location_id="",
                costume_ids=list(), prop_ids=frozenset(), reference_asset_versions=frozenset()
            )

        # Test prop_ids
        with self.assertRaisesRegex(ProductionPolicyError, "prop_ids must be a frozenset"):
            ShotContinuityBinding(
                shot_id="shot-1", bible_revision=ledger.bible.revision,
                character_ids=frozenset(["char-1"]), voice_ids=frozenset(), location_id="",
                costume_ids=frozenset(), prop_ids=list(), reference_asset_versions=frozenset()
            )

        # Test reference_asset_versions
        with self.assertRaisesRegex(ProductionPolicyError, "reference_asset_versions must be a frozenset"):
            ShotContinuityBinding(
                shot_id="shot-1", bible_revision=ledger.bible.revision,
                character_ids=frozenset(["char-1"]), voice_ids=frozenset(), location_id="",
                costume_ids=frozenset(), prop_ids=frozenset(), reference_asset_versions=list()
            )

    def test_continuity_binding_duplicate_schema_list(self):
        ledger = ProductionLedger(project_id="test-dup-list")
        ledger.add_shot(Shot(shot_id="shot-1"))
        ledger.bible.characters["char-1"] = {}
        binding = ShotContinuityBinding(
            shot_id="shot-1", bible_revision=ledger.bible.revision,
            character_ids=frozenset(["char-1"]), voice_ids=frozenset(), location_id="",
            costume_ids=frozenset(), prop_ids=frozenset(), reference_asset_versions=frozenset()
        )
        ledger.add_shot_continuity_binding(binding)
        data = ledger.to_dict()
        data["shot_continuity_bindings"]["shot-1"]["character_ids"].append("char-1")
        with self.assertRaisesRegex(ProductionPolicyError, "contains duplicates"):
            ProductionLedger.from_dict(data)

    def test_continuity_binding_happy_path(self):
        ledger = ProductionLedger(project_id="test-happy")
        ledger.add_shot(Shot(shot_id="shot-1"))
        ledger.bible.characters["char-1"] = {"name": "Alice"}
        ledger.bible.voices["voice-1"] = {"style": "soft"}
        ledger.bible.locations["loc-1"] = {"setting": "park"}
        ledger.bible.costumes["costume-1"] = {"desc": "red jacket"}
        ledger.bible.props["prop-1"] = {"desc": "watch"}
        binding = ShotContinuityBinding(
            shot_id="shot-1",
            bible_revision=ledger.bible.revision,
            character_ids=frozenset(["char-1"]),
            voice_ids=frozenset(["voice-1"]),
            location_id="loc-1",
            costume_ids=frozenset(["costume-1"]),
            prop_ids=frozenset(["prop-1"]),
            reference_asset_versions=frozenset(["asset-v1"])
        )
        ledger.add_shot_continuity_binding(binding)
        self.assertEqual(len(ledger.shot_continuity_bindings), 1)

    def test_continuity_binding_migration_safe_restore(self):
        ledger = ProductionLedger(project_id="test-migration")
        ledger_dict = ledger.to_dict()
        del ledger_dict["movie_bible"]["costumes"]
        del ledger_dict["movie_bible"]["props"]
        del ledger_dict["shot_continuity_bindings"]
        restored = ProductionLedger.from_dict(ledger_dict)
        self.assertEqual(restored.bible.costumes, {})
        self.assertEqual(restored.bible.props, {})
        self.assertEqual(restored.shot_continuity_bindings, {})

    def test_continuity_binding_roundtrip(self):
        ledger = ProductionLedger(project_id="test-roundtrip")
        ledger.add_shot(Shot(shot_id="shot-1"))
        ledger.bible.characters["char-1"] = {"name": "Alice"}
        ledger.bible.voices["voice-1"] = {"style": "soft"}
        ledger.bible.locations["loc-1"] = {"setting": "park"}
        ledger.bible.costumes["costume-1"] = {"desc": "red jacket"}
        ledger.bible.props["prop-1"] = {"desc": "watch"}
        binding = ShotContinuityBinding(
            shot_id="shot-1",
            bible_revision=ledger.bible.revision,
            character_ids=frozenset(["char-1"]),
            voice_ids=frozenset(["voice-1"]),
            location_id="loc-1",
            costume_ids=frozenset(["costume-1"]),
            prop_ids=frozenset(["prop-1"]),
            reference_asset_versions=frozenset(["asset-v1"])
        )
        ledger.add_shot_continuity_binding(binding)
        ledger_dict = ledger.to_dict()
        restored = ProductionLedger.from_dict(ledger_dict)
        self.assertIn("shot-1", restored.shot_continuity_bindings)
        restored_binding = restored.shot_continuity_bindings["shot-1"]
        self.assertEqual(restored_binding, ledger.shot_continuity_bindings["shot-1"])
        self.assertEqual(restored_binding.bible_digest, ledger.bible.content_digest)
        self.assertEqual(restored_binding.character_ids, frozenset(["char-1"]))

    def test_start_generation_idempotent_after_success(self):
        ledger = ProductionLedger(project_id="test-idem-success")
        ledger.add_shot(Shot(shot_id="shot-1"))
        ledger.submit_generation(job_id="job-1", shot_id="shot-1", idempotency_key="ik1", input_fingerprint="f1")
        quote = ProviderQuote(
            quote_id="Q1", provider_id="P1", adapter_id="A1", adapter_version="V1",
            request_fingerprint="f1", estimated_cost_usd_micros=0, maximum_cost_usd_micros=0,
            available=True, cloud_execution=True, charge_cap_enforced=True
        )
        ledger.register_provider_adapter(ProviderAdapterRegistration(
            provider_id="P1", adapter_id="A1", adapter_version="V1",
            capabilities=frozenset([ProviderCapability.VIDEO]),
            cloud_execution=True, charge_cap_enforced=True, maximum_cost_usd_micros=0
        ))
        auth = ledger.authorize_attempt("job-1", quote)
        receipt = ProviderSubmissionReceipt(
            authorization_id=auth.authorization_id,
            job_id="job-1",
            attempt_number=1,
            provider_id="P1",
            adapter_id="A1",
            adapter_version="V1",
            provider_request_key=auth.provider_request_key,
            provider_job_id="prov-job-1"
        )
        ledger.start_generation("job-1", receipt)
        ledger.finish_generation("job-1", 1, "prov-job-1", "asset-1")

        ledger.start_generation("job-1", receipt)
        self.assertEqual(ledger.jobs["job-1"].status, JobStatus.SUCCEEDED)

    def test_start_generation_idempotent_after_failure(self):
        ledger = ProductionLedger(project_id="test-idem-fail")
        ledger.add_shot(Shot(shot_id="shot-1"))
        ledger.submit_generation(job_id="job-1", shot_id="shot-1", idempotency_key="ik1", input_fingerprint="f1")
        quote = ProviderQuote(
            quote_id="Q1", provider_id="P1", adapter_id="A1", adapter_version="V1",
            request_fingerprint="f1", estimated_cost_usd_micros=0, maximum_cost_usd_micros=0,
            available=True, cloud_execution=True, charge_cap_enforced=True
        )
        ledger.register_provider_adapter(ProviderAdapterRegistration(
            provider_id="P1", adapter_id="A1", adapter_version="V1",
            capabilities=frozenset([ProviderCapability.VIDEO]),
            cloud_execution=True, charge_cap_enforced=True, maximum_cost_usd_micros=0
        ))
        auth = ledger.authorize_attempt("job-1", quote)
        receipt = ProviderSubmissionReceipt(
            authorization_id=auth.authorization_id,
            job_id="job-1",
            attempt_number=1,
            provider_id="P1",
            adapter_id="A1",
            adapter_version="V1",
            provider_request_key=auth.provider_request_key,
            provider_job_id="prov-job-1"
        )
        ledger.start_generation("job-1", receipt)
        ledger.fail_generation("job-1", 1, "prov-job-1", FailureClass.RETRYABLE_PROVIDER, "failure")

        ledger.start_generation("job-1", receipt)
        self.assertEqual(ledger.jobs["job-1"].status, JobStatus.RETRYABLE)

if __name__ == "__main__":
    unittest.main()
