import copy
import json
import unittest
from pathlib import Path

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
            choose_zero_cost_provider([ProviderQuote("paid", 1)])

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
        self.assertEqual({}, restored.shots["S1"].reviews)

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

    def test_future_schema_version_fails_closed(self):
        data = ProductionLedger("movie").to_dict()
        data["schema_version"] = 2
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
        ledger.start_generation("J1", "provider-1")
        self.assertEqual({}, shot.reviews)
        self.assertFalse(shot.canonical)
        self.assertFalse(shot.upscale_allowed)
        self.assertIs(shot.status, ShotStatus.GENERATING)

    def test_zero_cost_routing_rejects_zero_like_non_integer(self):
        with self.assertRaises(ProductionPolicyError):
            choose_zero_cost_provider([ProviderQuote("invalid", False)])
        with self.assertRaises(ProductionPolicyError):
            choose_zero_cost_provider([ProviderQuote("", 0)])
        with self.assertRaises(ProductionPolicyError):
            choose_zero_cost_provider([ProviderQuote("invalid", 0, available="yes")])

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

    def test_generation_start_rejects_mutated_retry_numbers(self):
        mutations = [
            ("attempts", -100),
            ("attempts", False),
            ("attempts", 0.5),
            ("max_attempts", False),
            ("max_attempts", 1.5),
        ]
        for field, value in mutations:
            job = GenerationJob("J1", "S1", "K1", "F1")
            setattr(job, field, value)
            with self.subTest(field=field, value=value), self.assertRaises(
                ProductionPolicyError
            ):
                job.start("provider-1")

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


if __name__ == "__main__":
    unittest.main()
