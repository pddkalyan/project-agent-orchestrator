"""TASK-0005 deterministic Movie Studio production gate core.

Creator: ChatGPT
Model: GPT-5.6 Sol
Run: 20261006_TASK_0005_RESUMABLE_LEDGER
No provider/network calls belong in this module.
"""
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Dict, FrozenSet, Iterable, Mapping, Optional


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
    estimated_cost_usd: float
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
        if self.status not in {JobStatus.QUEUED, JobStatus.RETRYABLE}:
            raise ProductionPolicyError("generation job cannot start from current status")
        if self.attempts >= self.max_attempts:
            raise ProductionPolicyError("generation retry ceiling reached")
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
        if max_attempts < 1:
            raise ProductionPolicyError("max attempts must be positive")
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
        job = self.jobs[job_id]
        job.start(provider_job_id)
        self.shots[job.shot_id].status = ShotStatus.GENERATING

    def finish_generation(self, job_id: str, asset_version: str) -> None:
        job = self.jobs[job_id]
        job.succeed(asset_version)
        self.shots[job.shot_id].bind_generated_asset(asset_version)

    def fail_generation(self, job_id: str, reason: str) -> None:
        job = self.jobs[job_id]
        job.fail(reason)
        self.shots[job.shot_id].status = (
            ShotStatus.BLOCKED
            if job.status is JobStatus.EXHAUSTED
            else ShotStatus.PLANNED
        )

    def to_dict(self) -> dict:
        def enum_value(value):
            if isinstance(value, Enum):
                return value.value
            if isinstance(value, dict):
                return {enum_value(k): enum_value(v) for k, v in value.items()}
            if isinstance(value, list):
                return [enum_value(v) for v in value]
            return value

        return enum_value(asdict(self))

    @classmethod
    def from_dict(cls, data: Mapping) -> "ProductionLedger":
        ledger = cls(project_id=data["project_id"], bible=MovieBible(**data["bible"]))
        for shot_id, raw in data.get("shots", {}).items():
            reviews = {
                Gate(gate): Review(
                    Gate(review["gate"]),
                    review["asset_version"],
                    Verdict(review["verdict"]),
                )
                for gate, review in raw.get("reviews", {}).items()
            }
            ledger.shots[shot_id] = Shot(
                shot_id=raw["shot_id"],
                asset_version=raw["asset_version"],
                has_dialogue_or_audio=raw["has_dialogue_or_audio"],
                status=ShotStatus(raw["status"]),
                reviews=reviews,
                canonical=raw["canonical"],
                upscale_allowed=raw["upscale_allowed"],
            )
        for job_id, raw in data.get("jobs", {}).items():
            raw = dict(raw)
            raw["status"] = JobStatus(raw["status"])
            ledger.jobs[job_id] = GenerationJob(**raw)
        ledger.idempotency_index = dict(data.get("idempotency_index", {}))
        ledger.validate()
        return ledger

    def validate(self) -> None:
        if not self.project_id:
            raise ProductionPolicyError("project id must not be empty")
        for key, job_id in self.idempotency_index.items():
            if job_id not in self.jobs or self.jobs[job_id].idempotency_key != key:
                raise ProductionPolicyError("corrupt idempotency index")
        for job in self.jobs.values():
            if job.shot_id not in self.shots:
                raise ProductionPolicyError("job references unknown shot")
            if job.attempts > job.max_attempts:
                raise ProductionPolicyError("job exceeds retry ceiling")


REQUIRED_BASE: FrozenSet[Gate] = frozenset(
    {Gate.VISUAL_QA, Gate.CONTINUITY_QA, Gate.TECHNICAL_QA}
)


def required_gates(shot: Shot) -> FrozenSet[Gate]:
    return REQUIRED_BASE | ({Gate.AUDIO_QA} if shot.has_dialogue_or_audio else set())


def validate_review_binding(shot: Shot) -> None:
    if not shot.asset_version:
        raise ProductionPolicyError("shot has no generated asset")
    for gate, review in shot.reviews.items():
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
        q for q in quotes if q.available and q.estimated_cost_usd == 0
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
    return all_shots_canonical and final_qc_passed and drive_master_verified
