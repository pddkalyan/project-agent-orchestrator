import copy
import json
import unittest
from pathlib import Path

from scripts.movie_studio_core__created_by_chatgpt__model_gpt_5_6_sol__task_TASK_0005 import (
    AttemptStatus,
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
    Shot,
    ShotStatus,
    Verdict,
    authorize_upscale,
    canonicalize,
    choose_zero_cost_provider,
    episode_can_complete,
    Episode,
    Scene,
)


class MovieStudioCoreTests(unittest.TestCase):
    def review(self, gate, version="v1", verdict=Verdict.PASS):
        return Review(gate, version, verdict)

    def generated_shot(self, dialogue=False):
        return Shot("S1", "v1", dialogue, ShotStatus.GENERATED)

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

    def authorize_and_start(
        self, ledger, job_id="J1", provider_job_id="provider-1", provider="afree"
    ):
        attempt = len(ledger.jobs[job_id].attempt_history) + 1
        authorization = ledger.authorize_attempt(
            job_id,
            self.quote(provider=provider),
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

    def test_movie_bible_entity_updates_and_digest(self):
        bible = MovieBible()
        bible.update_entity("characters", "hero", "name", "Alice")
        self.assertEqual(2, bible.revision)
        self.assertEqual("Alice", bible.characters["hero"]["name"])
        digest1 = bible.digest

        bible.update_entity("characters", "hero", "role", "lead")
        self.assertEqual(3, bible.revision)
        digest2 = bible.digest
        self.assertNotEqual(digest1, digest2)

        with self.assertRaises(ProductionPolicyError):
            bible.update_entity("wrong", "hero", "name", "Bob")

    def test_episode_timeline_qc_and_verification(self):
        ep = Episode("E1")
        ep.submit_timeline_qc("digest-1", 1200, "16:9")
        self.assertEqual("digest-1", ep.timeline_digest)
        self.assertEqual(1200, ep.duration_seconds)
        self.assertEqual("16:9", ep.aspect_ratio)

        with self.assertRaises(ProductionPolicyError):
            ep.submit_timeline_qc("digest-2", 1200, "4:3")

        with self.assertRaises(ProductionPolicyError):
            ep.submit_timeline_qc("", 1200, "16:9")

        ep.verify_drive_master("digest-1", True, True)
        self.assertTrue(ep.cleanup_authorized)

        ep2 = Episode("E2")
        with self.assertRaises(ProductionPolicyError):
            ep2.verify_drive_master("digest-1", True, True)

        with self.assertRaises(ProductionPolicyError):
            ep.verify_drive_master("wrong-digest", True, True)

        with self.assertRaises(ProductionPolicyError):
            ep.verify_drive_master("digest-1", False, True)

    def test_ledger_rejects_missing_scene_or_shot(self):
        ledger = ProductionLedger("movie")
        ledger.shots["S1"] = Shot("S1")
        ledger.scenes["SC1"] = Scene("SC1", shot_ids=("S2",))
        with self.assertRaises(ProductionPolicyError):
            ledger.to_dict()

        ledger2 = ProductionLedger("movie")
        ledger2.scenes["SC1"] = Scene("SC1", shot_ids=("S1",))
        ledger2.shots["S1"] = Shot("S1")
        ledger2.episodes["E1"] = Episode("E1", scene_ids=("SC2",))
        with self.assertRaises(ProductionPolicyError):
            ledger2.to_dict()

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
            self.quote(),
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

        ledger.scenes["SC1"] = Scene("SC1", shot_ids=("S1",))
        ledger.episodes["E1"] = Episode("E1", scene_ids=("SC1",))
        ep_payload = ledger.to_dict()
        self.assertEqual(
            set(schema["$defs"]["scene"]["properties"]),
            set(ep_payload["scenes"]["SC1"])
        )
        self.assertEqual(
            set(schema["$defs"]["episode"]["properties"]),
            set(ep_payload["episodes"]["E1"])
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
            self.quote(),
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
            self.quote(),
        )
        second = ledger.authorize_attempt(
            "J1",
            self.quote(),
        )
        self.assertEqual(first, second)
        self.assertEqual(1, len(ledger.jobs["J1"].attempt_history))
        with self.assertRaises(ProductionPolicyError):
            ledger.authorize_attempt(
                "J1",
                self.quote(provider="changed"),
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
                self.quote(),
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
            self.quote(),
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
                self.quote(),
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
            self.quote(),
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
            self.quote(),
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
        authorization = ledger.authorize_attempt("J1", self.quote())
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
        ledger.authorize_attempt("J1", self.quote())
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
        authorization = ledger.authorize_attempt("J1", self.quote())
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
        before = copy.deepcopy(ledger)
        with self.assertRaises(ProductionPolicyError):
            ledger.authorize_attempt("J1", self.quote())
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
            authorizations.append(ledger.authorize_attempt("J1", self.quote()))
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
        ledger.authorize_attempt("J3", self.quote())
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


if __name__ == "__main__":
    unittest.main()
