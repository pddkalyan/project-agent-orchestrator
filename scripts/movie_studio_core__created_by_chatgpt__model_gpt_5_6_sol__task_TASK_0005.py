"""TASK-0005 deterministic Movie Studio production gate core.

Creator: ChatGPT
Model: GPT-5.6 Sol
Run: 20261006_TASK_0005_RESUMABLE_LEDGER
No provider/network calls belong in this module.
"""
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Dict, FrozenSet, Iterable, Mapping, Optional

SCHEMA_VERSION = 1
PRODUCTION_CONTRACT = {
    "aspect_ratio": "16:9",
    "target_episode_minutes": 20,
    "local_video_inference": False,
    "final_storage_provider": "Google Drive",
}


class Gate(str, Enum):
    VISUAL_QA = "VISUAL_QA"
    CONTINUITY_QA = "CONTINUITY_QA"
    AUDIO_QA = "AUDIO_QA"
    TECHNICAL_QA = "TECHNICAL_QA"


class Verdict(str, Enum):
    PENDING = "PENDING"
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"


class ShotStatus(str, Enum):
    PLANNED = "PLANNED"
    GENERATING = "GENERATING"
    GENERATED = "GENERATED"
    APPROVED = "APPROVED"
    CANONICAL = "CANONICAL"
    REJECTED = "REJECTED"
    BLOCKED = "BLOCKED"


class JobStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    RETRYABLE = "RETRYABLE"
    EXHAUSTED = "EXHAUSTED"


class ProductionPolicyError(ValueError):
    pass


@dataclass(frozen=True)
class Review:
    gate: Gate
    asset_version: str
    verdict: Verdict


@dataclass
class Shot:
    shot_id: str
    asset_version: str = ""
    has_dialogue_or_audio: bool = False
    status: ShotStatus = ShotStatus.PLANNED
    reviews: Dict[Gate, Review] = field(default_factory=dict)
    canonical: bool = False
    upscale_allowed: bool = False

    def bind_generated_asset(self, asset_version: str) -> None:
        if not asset_version:
            raise ProductionPolicyError("asset version must not be empty")
        if asset_version != self.asset_version:
            self.reviews.clear()
            self.canonical = False
            self.upscale_allowed = False
        self.asset_version = asset_version
        self.status = ShotStatus.GENERATED


@dataclass(frozen=True)
class ProviderQuote:
    provider: str
    estimated_cost_usd_micros: int
    available: bool = True


@dataclass
class GenerationJob:
    job_id: str
    shot_id: str
    idempotency_key: str
    input_fingerprint: str
    max_attempts: int = 3
    attempts: int = 0
    status: JobStatus = JobStatus.QUEUED
    provider_job_id: Optional[str] = None
    output_asset_version: Optional[str] = None
    last_error: Optional[str] = None

    def start(self, provider_job_id: str) -> None:
        if type(self.max_attempts) is not int or self.max_attempts < 1:
            raise ProductionPolicyError("invalid generation retry ceiling")
        if (
            type(self.attempts) is not int
            or self.attempts < 0
            or self.attempts >= self.max_attempts
        ):
            raise ProductionPolicyError("invalid generation attempt count")
        if self.status not in {JobStatus.QUEUED, JobStatus.RETRYABLE}:
            raise ProductionPolicyError("generation job cannot start from current status")
        if not provider_job_id:
            raise ProductionPolicyError("provider job id must not be empty")
        self.attempts += 1
        self.provider_job_id = provider_job_id
        self.status = JobStatus.RUNNING
        self.last_error = None

    def succeed(self, asset_version: str) -> None:
        if self.status is not JobStatus.RUNNING:
            raise ProductionPolicyError("only a running generation job can succeed")
        if not asset_version:
            raise ProductionPolicyError("asset version must not be empty")
        self.output_asset_version = asset_version
        self.status = JobStatus.SUCCEEDED

    def fail(self, reason: str) -> None:
        if self.status is not JobStatus.RUNNING:
            raise ProductionPolicyError("only a running generation job can fail")
        if not reason:
            raise ProductionPolicyError("failure reason must not be empty")
        self.last_error = reason
        self.status = (
            JobStatus.RETRYABLE
            if self.attempts < self.max_attempts
            else JobStatus.EXHAUSTED
        )


@dataclass
class MovieBible:
    revision: int = 1
    story_rules: Dict[str, str] = field(default_factory=dict)
    characters: Dict[str, Dict[str, str]] = field(default_factory=dict)
    voices: Dict[str, Dict[str, str]] = field(default_factory=dict)
    locations: Dict[str, Dict[str, str]] = field(default_factory=dict)
    continuity_facts: Dict[str, str] = field(default_factory=dict)

    def update_fact(self, namespace: str, key: str, value: str) -> None:
        stores = {
            "story_rules": self.story_rules,
            "continuity_facts": self.continuity_facts,
        }
        if namespace not in stores:
            raise ProductionPolicyError("unsupported scalar movie-bible namespace")
        if not key or not value:
            raise ProductionPolicyError("movie-bible fact key and value are required")
        stores[namespace][key] = value
        self.revision += 1


@dataclass
class ProductionLedger:
    project_id: str
    bible: MovieBible = field(default_factory=MovieBible)
    shots: Dict[str, Shot] = field(default_factory=dict)
    jobs: Dict[str, GenerationJob] = field(default_factory=dict)
    idempotency_index: Dict[str, str] = field(default_factory=dict)

    def add_shot(self, shot: Shot) -> None:
        if not shot.shot_id or shot.shot_id in self.shots:
            raise ProductionPolicyError("shot id must be non-empty and unique")
        self.shots[shot.shot_id] = shot

    def submit_generation(
        self,
        *,
        job_id: str,
        shot_id: str,
        idempotency_key: str,
        input_fingerprint: str,
        max_attempts: int = 3,
    ) -> GenerationJob:
        if shot_id not in self.shots:
            raise ProductionPolicyError("unknown shot")
        if not job_id or not idempotency_key or not input_fingerprint:
            raise ProductionPolicyError("job id, idempotency key and fingerprint are required")
        existing_job_id = self.idempotency_index.get(idempotency_key)
        if existing_job_id:
            existing = self.jobs[existing_job_id]
            if (
                existing.shot_id != shot_id
                or existing.input_fingerprint != input_fingerprint
                or existing.max_attempts != max_attempts
            ):
                raise ProductionPolicyError("idempotency key reused with different input")
            return existing
        if job_id in self.jobs:
            raise ProductionPolicyError("job id already exists")
        if type(max_attempts) is not int or max_attempts < 1:
            raise ProductionPolicyError("max attempts must be positive")
        if any(
            job.shot_id == shot_id
            and job.status in {JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.RETRYABLE}
            for job in self.jobs.values()
        ):
            raise ProductionPolicyError("shot already has an active generation job")
        job = GenerationJob(
            job_id=job_id,
            shot_id=shot_id,
            idempotency_key=idempotency_key,
            input_fingerprint=input_fingerprint,
            max_attempts=max_attempts,
        )
        self.jobs[job_id] = job
        self.idempotency_index[idempotency_key] = job_id
        return job

    def start_generation(self, job_id: str, provider_job_id: str) -> None:
        job = self._job(job_id)
        job.start(provider_job_id)
        shot = self.shots[job.shot_id]
        shot.reviews.clear()
        shot.canonical = False
        shot.upscale_allowed = False
        shot.asset_version = ""
        shot.status = ShotStatus.GENERATING

    def finish_generation(self, job_id: str, asset_version: str) -> None:
        job = self._job(job_id)
        job.succeed(asset_version)
        self.shots[job.shot_id].bind_generated_asset(asset_version)

    def fail_generation(self, job_id: str, reason: str) -> None:
        job = self._job(job_id)
        job.fail(reason)
        self.shots[job.shot_id].status = (
            ShotStatus.BLOCKED
            if job.status is JobStatus.EXHAUSTED
            else ShotStatus.PLANNED
        )

    def _job(self, job_id: str) -> GenerationJob:
        try:
            return self.jobs[job_id]
        except KeyError as exc:
            raise ProductionPolicyError("unknown generation job") from exc

    def to_dict(self) -> dict:
        self.validate()

        def enum_value(value):
            if isinstance(value, Enum):
                return value.value
            if isinstance(value, dict):
                return {enum_value(k): enum_value(v) for k, v in value.items()}
            if isinstance(value, list):
                return [enum_value(v) for v in value]
            return value

        raw = enum_value(asdict(self))
        return {
            "schema_version": SCHEMA_VERSION,
            "project_id": raw["project_id"],
            "spend_limit_usd_micros": 0,
            "production": dict(PRODUCTION_CONTRACT),
            "movie_bible": raw["bible"],
            "shots": raw["shots"],
            "generation_jobs": raw["jobs"],
            "idempotency_index": raw["idempotency_index"],
        }

    @classmethod
    def from_dict(cls, data: Mapping) -> "ProductionLedger":
        try:
            return cls._from_dict(data)
        except ProductionPolicyError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise ProductionPolicyError("malformed production checkpoint") from exc

    @classmethod
    def _from_dict(cls, data: Mapping) -> "ProductionLedger":
        root_fields = {
            "schema_version",
            "project_id",
            "spend_limit_usd_micros",
            "production",
            "movie_bible",
            "shots",
            "generation_jobs",
            "idempotency_index",
        }
        if not isinstance(data, Mapping) or set(data) != root_fields:
            raise ProductionPolicyError("production checkpoint fields mismatch")
        if (
            type(data.get("schema_version")) is not int
            or data.get("schema_version") != SCHEMA_VERSION
        ):
            raise ProductionPolicyError("unsupported production-state schema version")
        if (
            type(data.get("spend_limit_usd_micros")) is not int
            or data.get("spend_limit_usd_micros") != 0
        ):
            raise ProductionPolicyError("production state violates zero-spend policy")
        production = data.get("production")
        if (
            not isinstance(production, Mapping)
            or set(production) != set(PRODUCTION_CONTRACT)
            or not isinstance(production.get("aspect_ratio"), str)
            or production.get("aspect_ratio") != "16:9"
            or type(production.get("target_episode_minutes")) is not int
            or production.get("target_episode_minutes") != 20
            or type(production.get("local_video_inference")) is not bool
            or production.get("local_video_inference") is not False
            or not isinstance(production.get("final_storage_provider"), str)
            or production.get("final_storage_provider") != "Google Drive"
        ):
            raise ProductionPolicyError("production contract mismatch")
        bible_raw = data["movie_bible"]
        bible_fields = {
            "revision",
            "story_rules",
            "characters",
            "voices",
            "locations",
            "continuity_facts",
        }
        if not isinstance(bible_raw, Mapping) or set(bible_raw) != bible_fields:
            raise ProductionPolicyError("movie-bible fields mismatch")
        if not isinstance(data["shots"], Mapping):
            raise ProductionPolicyError("shots must be a mapping")
        if not isinstance(data["generation_jobs"], Mapping):
            raise ProductionPolicyError("generation jobs must be a mapping")
        if not isinstance(data["idempotency_index"], Mapping):
            raise ProductionPolicyError("idempotency index must be a mapping")
        ledger = cls(
            project_id=data["project_id"],
            bible=MovieBible(**bible_raw),
        )
        for shot_id, raw in data.get("shots", {}).items():
            shot_fields = {
                "shot_id",
                "asset_version",
                "has_dialogue_or_audio",
                "status",
                "reviews",
                "canonical",
                "upscale_allowed",
            }
            if not isinstance(raw, Mapping) or set(raw) != shot_fields:
                raise ProductionPolicyError("shot fields mismatch")
            if not isinstance(raw["reviews"], Mapping):
                raise ProductionPolicyError("shot reviews must be a mapping")
            reviews = {
                Gate(gate): Review(
                    Gate(review["gate"]),
                    review["asset_version"],
                    Verdict(review["verdict"]),
                )
                for gate, review in raw.get("reviews", {}).items()
                if isinstance(review, Mapping)
                and set(review) == {"gate", "asset_version", "verdict"}
            }
            if len(reviews) != len(raw["reviews"]):
                raise ProductionPolicyError("review fields mismatch")
            ledger.shots[shot_id] = Shot(
                shot_id=raw["shot_id"],
                asset_version=raw["asset_version"],
                has_dialogue_or_audio=raw["has_dialogue_or_audio"],
                status=ShotStatus(raw["status"]),
                reviews=reviews,
                canonical=raw["canonical"],
                upscale_allowed=raw["upscale_allowed"],
            )
        for job_id, raw in data.get("generation_jobs", {}).items():
            job_fields = {
                "job_id",
                "shot_id",
                "idempotency_key",
                "input_fingerprint",
                "max_attempts",
                "attempts",
                "status",
                "provider_job_id",
                "output_asset_version",
                "last_error",
            }
            if not isinstance(raw, Mapping) or set(raw) != job_fields:
                raise ProductionPolicyError("generation-job fields mismatch")
            raw = dict(raw)
            raw["status"] = JobStatus(raw["status"])
            ledger.jobs[job_id] = GenerationJob(**raw)
        ledger.idempotency_index = dict(data.get("idempotency_index", {}))
        ledger.validate()
        return ledger

    def validate(self) -> None:
        if not isinstance(self.project_id, str) or not self.project_id:
            raise ProductionPolicyError("project id must not be empty")
        if type(self.bible.revision) is not int or self.bible.revision < 1:
            raise ProductionPolicyError("invalid movie-bible revision")
        self._validate_bible_maps()
        for shot_key, shot in self.shots.items():
            if not isinstance(shot_key, str) or not shot_key:
                raise ProductionPolicyError("shot id must not be empty")
            if shot_key != shot.shot_id:
                raise ProductionPolicyError("shot key/id mismatch")
            if type(shot.status) is not ShotStatus:
                raise ProductionPolicyError("invalid shot status")
            if (
                not isinstance(shot.asset_version, str)
                or type(shot.has_dialogue_or_audio) is not bool
                or type(shot.canonical) is not bool
                or type(shot.upscale_allowed) is not bool
            ):
                raise ProductionPolicyError("invalid shot field type")
            validate_review_binding(shot, allow_empty=True)
            if shot.canonical != (shot.status is ShotStatus.CANONICAL):
                raise ProductionPolicyError("canonical flag/status mismatch")
            if shot.canonical and not all_required_gates_pass(shot):
                raise ProductionPolicyError("canonical shot lacks current QA evidence")
            if shot.upscale_allowed and not semantic_gates_pass(shot):
                raise ProductionPolicyError("upscale authorization lacks current QA evidence")
            requires_asset = shot.status in {
                ShotStatus.GENERATED,
                ShotStatus.APPROVED,
                ShotStatus.CANONICAL,
                ShotStatus.REJECTED,
            }
            if requires_asset != bool(shot.asset_version):
                raise ProductionPolicyError("shot status/asset mismatch")
            if shot.status in {
                ShotStatus.PLANNED,
                ShotStatus.GENERATING,
                ShotStatus.BLOCKED,
            } and (shot.reviews or shot.canonical or shot.upscale_allowed):
                raise ProductionPolicyError("inactive shot retains approval evidence")

        derived_index: Dict[str, str] = {}
        for job_key, job in self.jobs.items():
            if not isinstance(job_key, str) or not job_key:
                raise ProductionPolicyError("job id must not be empty")
            if job_key != job.job_id:
                raise ProductionPolicyError("job key/id mismatch")
            if type(job.status) is not JobStatus:
                raise ProductionPolicyError("invalid generation-job status")
            if (
                not isinstance(job.shot_id, str)
                or not job.shot_id
                or not isinstance(job.idempotency_key, str)
                or not job.idempotency_key
                or not isinstance(job.input_fingerprint, str)
                or not job.input_fingerprint
            ):
                raise ProductionPolicyError("job identifiers must be non-empty strings")
            if any(
                value is not None and not isinstance(value, str)
                for value in (
                    job.provider_job_id,
                    job.output_asset_version,
                    job.last_error,
                )
            ):
                raise ProductionPolicyError("invalid generation evidence type")
            if job.shot_id not in self.shots:
                raise ProductionPolicyError("job references unknown shot")
            if type(job.max_attempts) is not int or job.max_attempts < 1:
                raise ProductionPolicyError("invalid job retry ceiling")
            if (
                type(job.attempts) is not int
                or job.attempts < 0
                or job.attempts > job.max_attempts
            ):
                raise ProductionPolicyError("invalid job attempt count")
            if job.idempotency_key in derived_index:
                raise ProductionPolicyError("duplicate job idempotency key")
            derived_index[job.idempotency_key] = job.job_id
            self._validate_job_status(job)
        if self.idempotency_index != derived_index:
            raise ProductionPolicyError("idempotency index is not an exact job bijection")
        if any(
            not isinstance(key, str)
            or not key
            or not isinstance(value, str)
            or not value
            for key, value in self.idempotency_index.items()
        ):
            raise ProductionPolicyError("invalid idempotency index entry")

        active_by_shot: Dict[str, str] = {}
        for job in self.jobs.values():
            if job.status in {JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.RETRYABLE}:
                if job.shot_id in active_by_shot:
                    raise ProductionPolicyError("multiple active jobs target one shot")
                active_by_shot[job.shot_id] = job.job_id
        running_by_shot = {
            job.shot_id
            for job in self.jobs.values()
            if job.status is JobStatus.RUNNING
        }
        for shot in self.shots.values():
            if (shot.status is ShotStatus.GENERATING) != (shot.shot_id in running_by_shot):
                raise ProductionPolicyError("generating shot/running job mismatch")

    def _validate_bible_maps(self) -> None:
        scalar_maps = (self.bible.story_rules, self.bible.continuity_facts)
        entity_maps = (
            self.bible.characters,
            self.bible.voices,
            self.bible.locations,
        )
        if any(
            not isinstance(mapping, dict)
            or any(
                not isinstance(key, str)
                or not key
                or not isinstance(value, str)
                for key, value in mapping.items()
            )
            for mapping in scalar_maps
        ):
            raise ProductionPolicyError("invalid scalar movie-bible map")
        if any(
            not isinstance(mapping, dict)
            or any(
                not isinstance(key, str)
                or not key
                or not isinstance(value, dict)
                or any(
                    not isinstance(field, str)
                    or not field
                    or not isinstance(content, str)
                    for field, content in value.items()
                )
                for key, value in mapping.items()
            )
            for mapping in entity_maps
        ):
            raise ProductionPolicyError("invalid entity movie-bible map")

    @staticmethod
    def _validate_job_status(job: GenerationJob) -> None:
        if job.status is JobStatus.QUEUED:
            valid = (
                job.attempts == 0
                and job.provider_job_id is None
                and job.output_asset_version is None
                and job.last_error is None
            )
        elif job.status is JobStatus.RUNNING:
            valid = (
                job.attempts > 0
                and bool(job.provider_job_id)
                and job.output_asset_version is None
                and job.last_error is None
            )
        elif job.status is JobStatus.SUCCEEDED:
            valid = (
                job.attempts > 0
                and bool(job.provider_job_id)
                and bool(job.output_asset_version)
                and job.last_error is None
            )
        elif job.status is JobStatus.RETRYABLE:
            valid = (
                0 < job.attempts < job.max_attempts
                and bool(job.provider_job_id)
                and job.output_asset_version is None
                and bool(job.last_error)
            )
        elif job.status is JobStatus.EXHAUSTED:
            valid = (
                job.attempts == job.max_attempts
                and bool(job.provider_job_id)
                and job.output_asset_version is None
                and bool(job.last_error)
            )
        else:
            valid = False
        if not valid:
            raise ProductionPolicyError("generation job status evidence is inconsistent")


REQUIRED_BASE: FrozenSet[Gate] = frozenset(
    {Gate.VISUAL_QA, Gate.CONTINUITY_QA, Gate.TECHNICAL_QA}
)


def required_gates(shot: Shot) -> FrozenSet[Gate]:
    return REQUIRED_BASE | ({Gate.AUDIO_QA} if shot.has_dialogue_or_audio else set())


def validate_review_binding(shot: Shot, *, allow_empty: bool = False) -> None:
    if not shot.asset_version:
        if allow_empty and not shot.reviews and not shot.canonical and not shot.upscale_allowed:
            return
        raise ProductionPolicyError("shot has no generated asset")
    for gate, review in shot.reviews.items():
        if (
            type(gate) is not Gate
            or type(review) is not Review
            or type(review.gate) is not Gate
            or type(review.verdict) is not Verdict
            or not isinstance(review.asset_version, str)
            or not review.asset_version
        ):
            raise ProductionPolicyError("invalid review evidence type")
        if review.gate != gate:
            raise ProductionPolicyError("review gate/key mismatch")
        if review.asset_version != shot.asset_version:
            raise ProductionPolicyError("stale review evidence")


def semantic_gates_pass(shot: Shot) -> bool:
    validate_review_binding(shot)
    return all(
        shot.reviews.get(g) and shot.reviews[g].verdict is Verdict.PASS
        for g in (Gate.VISUAL_QA, Gate.CONTINUITY_QA)
    )


def all_required_gates_pass(shot: Shot) -> bool:
    validate_review_binding(shot)
    return all(
        shot.reviews.get(g) and shot.reviews[g].verdict is Verdict.PASS
        for g in required_gates(shot)
    )


def authorize_upscale(shot: Shot) -> None:
    if not semantic_gates_pass(shot):
        raise ProductionPolicyError(
            "upscale blocked until visual and continuity QA pass"
        )
    shot.upscale_allowed = True


def canonicalize(shot: Shot) -> None:
    if not all_required_gates_pass(shot):
        raise ProductionPolicyError(
            "canonicalization blocked until every applicable QA gate passes"
        )
    shot.canonical = True
    shot.status = ShotStatus.CANONICAL


def choose_zero_cost_provider(quotes: Iterable[ProviderQuote]) -> ProviderQuote:
    eligible = [
        q
        for q in quotes
        if q.available is True
        and isinstance(q.provider, str)
        and bool(q.provider.strip())
        and type(q.estimated_cost_usd_micros) is int
        and q.estimated_cost_usd_micros == 0
    ]
    if not eligible:
        raise ProductionPolicyError("BLOCKED_ZERO_COST_PROVIDER_UNAVAILABLE")
    return sorted(eligible, key=lambda q: q.provider)[0]


def episode_can_complete(
    *,
    all_shots_canonical: bool,
    final_qc_passed: bool,
    drive_master_verified: bool,
) -> bool:
    values = (all_shots_canonical, final_qc_passed, drive_master_verified)
    return all(type(value) is bool and value for value in values)
