import copy
import unittest

from scripts.movie_studio_core__created_by_chatgpt__model_gpt_5_6_sol__task_TASK_0005 import (
    Gate,
    GenerationJob,
    JobStatus,
    MovieBible,
    ProductionLedger,
    ProductionPolicyError,
    ProviderQuote,
    Review,
    Shot,
    ShotStatus,
    Verdict,
    authorize_upscale,
    canonicalize,
    choose_zero_cost_provider,
    episode_can_complete,
)


class MovieStudioCoreTests(unittest.TestCase):
    def review(self, gate, version="v1", verdict=Verdict.PASS):
        return Review(gate, version, verdict)

    def generated_shot(self, dialogue=False):
        return Shot("S1", "v1", dialogue, ShotStatus.GENERATED)

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
            choose_zero_cost_provider([ProviderQuote("paid", 0.01)])

    def test_zero_cost_provider_deterministic(self):
        quote = choose_zero_cost_provider(
            [ProviderQuote("zfree", 0), ProviderQuote("afree", 0)]
        )
        self.assertEqual("afree", quote.provider)

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
        ledger.start_generation("J1", "provider-1")
        ledger.finish_generation("J1", "asset-v1")
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
        ledger.start_generation("J1", "provider-1")
        ledger.fail_generation("J1", "transient")
        self.assertIs(ledger.jobs["J1"].status, JobStatus.RETRYABLE)
        ledger.start_generation("J1", "provider-2")
        ledger.fail_generation("J1", "still broken")
        self.assertIs(ledger.jobs["J1"].status, JobStatus.EXHAUSTED)
        self.assertIs(ledger.shots["S1"].status, ShotStatus.BLOCKED)

    def test_job_rejects_double_completion(self):
        job = GenerationJob("J1", "S1", "K1", "F1")
        job.start("provider-1")
        job.succeed("v1")
        with self.assertRaises(ProductionPolicyError):
            job.succeed("v2")

    def test_movie_bible_updates_are_revisioned(self):
        bible = MovieBible()
        bible.update_fact("continuity_facts", "amulet", "worn on left wrist")
        self.assertEqual(2, bible.revision)
        self.assertEqual("worn on left wrist", bible.continuity_facts["amulet"])

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
        ledger.start_generation("J1", "provider-1")
        restored = ProductionLedger.from_dict(copy.deepcopy(ledger.to_dict()))
        self.assertEqual(ledger.to_dict(), restored.to_dict())
        self.assertIs(restored.jobs["J1"].status, JobStatus.RUNNING)
        self.assertIn(Gate.VISUAL_QA, restored.shots["S1"].reviews)

    def test_corrupt_resume_state_is_rejected(self):
        ledger = ProductionLedger("movie")
        ledger.add_shot(Shot("S1"))
        data = ledger.to_dict()
        data["idempotency_index"]["K1"] = "missing"
        with self.assertRaises(ProductionPolicyError):
            ProductionLedger.from_dict(data)


if __name__ == "__main__":
    unittest.main()
