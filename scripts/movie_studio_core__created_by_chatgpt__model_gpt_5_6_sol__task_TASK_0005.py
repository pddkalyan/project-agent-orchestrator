"""TASK-0005 deterministic Movie Studio production gate core.

Creator: ChatGPT
Model: GPT-5.6 Sol
Run: 20261007_TASK_0005_PROVIDER_AUTHORIZATION
No provider/network calls belong in this module.
"""
from dataclasses import asdict, dataclass, field, replace
from enum import Enum
from hashlib import sha256
import json
from typing import Dict, FrozenSet, Iterable, Mapping, Optional, Protocol, Tuple

SCHEMA_VERSION = 2
PRODUCTION_CONTRACT = {
    "aspect_ratio": "16:9",
    "target_episode_minutes": 20,
    "local_video_inference": False,
    "final_storage_provider": "Google Drive",
}


class ProviderCapability(str, Enum):
    TEXT = "TEXT"
    IMAGE = "IMAGE"
    VIDEO = "VIDEO"
    AUDIO = "AUDIO"
    LIP_SYNC = "LIP_SYNC"
    UPSCALE = "UPSCALE"


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


class EpisodeStatus(str, Enum):
    PLANNED = "PLANNED"
    IN_PRODUCTION = "IN_PRODUCTION"
    ASSEMBLING = "ASSEMBLING"
    FINAL_QC = "FINAL_QC"
    ARCHIVING = "ARCHIVING"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"


class SceneStatus(str, Enum):
    PLANNED = "PLANNED"
    GENERATING = "GENERATING"
    REVIEW = "REVIEW"
    APPROVED = "APPROVED"
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
    AUTHORIZED = "AUTHORIZED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    RETRYABLE = "RETRYABLE"
    EXHAUSTED = "EXHAUSTED"


class AttemptStatus(str, Enum):
    AUTHORIZED = "AUTHORIZED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class FailureClass(str, Enum):
    RETRYABLE_PROVIDER = "RETRYABLE_PROVIDER"
    NON_RETRYABLE_PROVIDER = "NON_RETRYABLE_PROVIDER"
    POLICY = "POLICY"
    INVALID_INPUT = "INVALID_INPUT"


class ProductionPolicyError(ValueError):
    pass


@dataclass(frozen=True)
class Review:
    gate: Gate
    asset_version: str
    verdict: Verdict


@dataclass
class Scene:
    scene_id: str
    status: SceneStatus = SceneStatus.PLANNED


@dataclass(frozen=True)
class ShotPlan:
    shot_id: str
    scene_id: str
    sequence_index: int
    planned_duration_ms: int
    prompt_fingerprint: str
    has_dialogue_or_audio: bool


def plan_digest(plan: ShotPlan) -> str:
    return sha256(json.dumps(asdict(plan), sort_keys=True).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ShotContinuityBinding:
    """Binds continuity elements (characters, voices, costumes, props, refs) to a shot idempotently."""
    shot_id: str
    bible_revision: int
    character_ids: FrozenSet[str]
    voice_ids: FrozenSet[str]
    location_id: str
    costume_ids: FrozenSet[str]
    prop_ids: FrozenSet[str]
    reference_asset_versions: FrozenSet[str]

    def __post_init__(self):
        for field_name in ["character_ids", "voice_ids", "costume_ids", "prop_ids", "reference_asset_versions"]:
            val = getattr(self, field_name)
            if type(val) is not frozenset:
                raise ProductionPolicyError(f"{field_name} must be a frozenset")
            for item in val:
                if type(item) is not str or not item:
                    raise ProductionPolicyError(f"{field_name} must contain non-empty strings")
        if type(self.bible_revision) is not int:
            raise ProductionPolicyError("bible_revision must be an int")
        if type(self.shot_id) is not str or not self.shot_id:
            raise ProductionPolicyError("shot_id must be a non-empty string")
        if type(self.location_id) is not str:
            raise ProductionPolicyError("location_id must be a string")


def continuity_binding_digest(binding: ShotContinuityBinding) -> str:
    data = asdict(binding)
    for key, value in data.items():
        if isinstance(value, frozenset):
            data[key] = sorted(list(value))
    return sha256(json.dumps(data, sort_keys=True).encode("utf-8")).hexdigest()


@dataclass
class Shot:
    shot_id: str
    asset_version: str = ""
    has_dialogue_or_audio: bool = False
    status: ShotStatus = ShotStatus.PLANNED
    reviews: Dict[Gate, Review] = field(default_factory=dict)
    canonical: bool = False
    upscale_allowed: bool = False
    generation_epoch: int = 0
    generation_owner_job_id: Optional[str] = None
    scene_id: str = ""

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
class ProviderAdapterRegistration:
    provider_id: str
    adapter_id: str
    adapter_version: str
    capabilities: FrozenSet[ProviderCapability]
    cloud_execution: bool = True
    charge_cap_enforced: bool = True
    maximum_cost_usd_micros: int = 0


@dataclass(frozen=True)
class ProviderQuote:
    quote_id: str
    provider_id: str
    adapter_id: str
    adapter_version: str
    request_fingerprint: str
    estimated_cost_usd_micros: int
    maximum_cost_usd_micros: int
    available: bool
    cloud_execution: bool
    charge_cap_enforced: bool


@dataclass(frozen=True)
class AttemptAuthorization:
    authorization_id: str
    job_id: str
    shot_id: str
    generation_epoch: int
    attempt_number: int
    provider_id: str
    adapter_id: str
    adapter_version: str
    quote_id: str
    input_fingerprint: str
    provider_request_key: str
    maximum_cost_usd_micros: int
    cloud_execution: bool
    charge_cap_enforced: bool


@dataclass(frozen=True)
class GenerationAttempt:
    attempt_number: int
    authorization: AttemptAuthorization
    status: AttemptStatus
    provider_job_id: Optional[str] = None
    output_asset_version: Optional[str] = None
    failure_class: Optional[FailureClass] = None
    failure_reason: Optional[str] = None


@dataclass(frozen=True)
class ProviderSubmissionReceipt:
    authorization_id: str
    job_id: str
    attempt_number: int
    provider_id: str
    adapter_id: str
    adapter_version: str
    provider_request_key: str
    provider_job_id: str


class GenerationProviderAdapter(Protocol):
    """Provider-neutral boundary; implementations keep credentials outside state."""

    provider_id: str
    adapter_id: str
    adapter_version: str

    def submit(
        self, authorization: AttemptAuthorization
    ) -> ProviderSubmissionReceipt: ...

    def reconcile(self, provider_request_key: str) -> ProviderSubmissionReceipt: ...


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
    generation_epoch: Optional[int] = None
    attempt_history: Tuple[GenerationAttempt, ...] = ()

    def _authorize(self, authorization: AttemptAuthorization) -> None:
        if type(self.max_attempts) is not int or self.max_attempts < 1:
            raise ProductionPolicyError("invalid generation retry ceiling")
        if self.status not in {JobStatus.QUEUED, JobStatus.RETRYABLE}:
            raise ProductionPolicyError("generation job cannot authorize from current status")
        expected_attempt = len(self.attempt_history) + 1
        if expected_attempt > self.max_attempts:
            raise ProductionPolicyError("generation retry ceiling reached")
        if (
            authorization.job_id != self.job_id
            or authorization.shot_id != self.shot_id
            or authorization.input_fingerprint != self.input_fingerprint
            or authorization.attempt_number != expected_attempt
            or (
                self.generation_epoch is not None
                and authorization.generation_epoch != self.generation_epoch
            )
            or (
                self.generation_epoch is None
                and (
                    expected_attempt != 1
                    or type(authorization.generation_epoch) is not int
                    or authorization.generation_epoch < 1
                )
            )
        ):
            raise ProductionPolicyError("authorization does not bind to generation job")
        self.generation_epoch = authorization.generation_epoch
        self.attempt_history = self.attempt_history + (
            GenerationAttempt(
                attempt_number=expected_attempt,
                authorization=authorization,
                status=AttemptStatus.AUTHORIZED,
            ),
        )
        self.attempts = len(self.attempt_history)
        self.status = JobStatus.AUTHORIZED
        self.provider_job_id = None
        self.output_asset_version = None
        self.last_error = None

    def _start(self, receipt: ProviderSubmissionReceipt) -> None:
        if self.status in {
            JobStatus.RUNNING,
            JobStatus.SUCCEEDED,
            JobStatus.RETRYABLE,
            JobStatus.EXHAUSTED,
        } and self.attempt_history:
            current = self.attempt_history[-1]
            auth = current.authorization
            if (
                current.status in {AttemptStatus.RUNNING, AttemptStatus.SUCCEEDED, AttemptStatus.FAILED}
                and receipt.authorization_id == auth.authorization_id
                and receipt.job_id == self.job_id
                and receipt.attempt_number == current.attempt_number
                and receipt.provider_id == auth.provider_id
                and receipt.adapter_id == auth.adapter_id
                and receipt.adapter_version == auth.adapter_version
                and receipt.provider_request_key == auth.provider_request_key
                and receipt.provider_job_id == current.provider_job_id
            ):
                return
            if self.status is JobStatus.RUNNING:
                raise ProductionPolicyError("conflicting duplicate provider receipt")
        if self.status is not JobStatus.AUTHORIZED or not self.attempt_history:
            raise ProductionPolicyError("generation job lacks current authorization")
        current = self.attempt_history[-1]
        auth = current.authorization
        if (
            current.status is not AttemptStatus.AUTHORIZED
            or type(receipt) is not ProviderSubmissionReceipt
            or receipt.authorization_id != auth.authorization_id
            or receipt.job_id != self.job_id
            or receipt.attempt_number != current.attempt_number
            or receipt.provider_id != auth.provider_id
            or receipt.adapter_id != auth.adapter_id
            or receipt.adapter_version != auth.adapter_version
            or receipt.provider_request_key != auth.provider_request_key
        ):
            raise ProductionPolicyError("provider receipt does not match authorization")
        if (
            not isinstance(receipt.provider_job_id, str)
            or not receipt.provider_job_id.strip()
        ):
            raise ProductionPolicyError("provider job id must not be empty")
        self.attempt_history = self.attempt_history[:-1] + (
            replace(
                current,
                status=AttemptStatus.RUNNING,
                provider_job_id=receipt.provider_job_id,
            ),
        )
        self.provider_job_id = receipt.provider_job_id
        self.status = JobStatus.RUNNING
        self.last_error = None

    def _succeed(
        self, attempt_number: int, provider_job_id: str, asset_version: str
    ) -> None:
        current = self._matching_running_attempt(attempt_number, provider_job_id)
        if not isinstance(asset_version, str) or not asset_version:
            raise ProductionPolicyError("asset version must not be empty")
        self.attempt_history = self.attempt_history[:-1] + (
            replace(
                current,
                status=AttemptStatus.SUCCEEDED,
                output_asset_version=asset_version,
            ),
        )
        self.output_asset_version = asset_version
        self.status = JobStatus.SUCCEEDED

    def _fail(
        self,
        attempt_number: int,
        provider_job_id: str,
        failure_class: FailureClass,
        reason: str,
    ) -> None:
        current = self._matching_running_attempt(attempt_number, provider_job_id)
        if type(failure_class) is not FailureClass:
            raise ProductionPolicyError("invalid failure class")
        if not isinstance(reason, str) or not reason:
            raise ProductionPolicyError("failure reason must not be empty")
        self.attempt_history = self.attempt_history[:-1] + (
            replace(
                current,
                status=AttemptStatus.FAILED,
                failure_class=failure_class,
                failure_reason=reason,
            ),
        )
        self.last_error = reason
        self.status = (
            JobStatus.RETRYABLE
            if failure_class is FailureClass.RETRYABLE_PROVIDER
            and self.attempts < self.max_attempts
            else JobStatus.EXHAUSTED
        )

    def _matching_running_attempt(
        self, attempt_number: int, provider_job_id: str
    ) -> GenerationAttempt:
        if self.status is not JobStatus.RUNNING or not self.attempt_history:
            raise ProductionPolicyError("only a running generation job accepts callbacks")
        current = self.attempt_history[-1]
        if (
            type(attempt_number) is not int
            or current.attempt_number != attempt_number
            or current.status is not AttemptStatus.RUNNING
            or current.provider_job_id != provider_job_id
        ):
            raise ProductionPolicyError("stale or mismatched provider callback")
        return current


@dataclass
class MovieBible:
    revision: int = 1
    story_rules: Dict[str, str] = field(default_factory=dict)
    characters: Dict[str, Dict[str, str]] = field(default_factory=dict)
    voices: Dict[str, Dict[str, str]] = field(default_factory=dict)
    locations: Dict[str, Dict[str, str]] = field(default_factory=dict)
    costumes: Dict[str, Dict[str, str]] = field(default_factory=dict)
    props: Dict[str, Dict[str, str]] = field(default_factory=dict)
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
    episode_id: str = ""
    episode_status: EpisodeStatus = EpisodeStatus.PLANNED
    plan_revision: int = 1
    plan_frozen: bool = False
    bible: MovieBible = field(default_factory=MovieBible)
    scenes: Dict[str, Scene] = field(default_factory=dict)
    shots: Dict[str, Shot] = field(default_factory=dict)
    shot_plans: Dict[str, ShotPlan] = field(default_factory=dict)
    shot_continuity_bindings: Dict[str, ShotContinuityBinding] = field(default_factory=dict)
    jobs: Dict[str, GenerationJob] = field(default_factory=dict)
    idempotency_index: Dict[str, str] = field(default_factory=dict)
    authorization_index: Dict[str, str] = field(default_factory=dict)
    provider_request_index: Dict[str, str] = field(default_factory=dict)
    provider_adapters: Dict[str, ProviderAdapterRegistration] = field(default_factory=dict)

    def register_provider_adapter(self, adapter: ProviderAdapterRegistration) -> None:
        if type(adapter) is not ProviderAdapterRegistration:
            raise ProductionPolicyError("invalid provider adapter registration")
        if any(
            not isinstance(val, str) or not val.strip()
            for val in (adapter.provider_id, adapter.adapter_id, adapter.adapter_version)
        ):
            raise ProductionPolicyError("adapter identifiers must be non-empty strings")
        if not adapter.cloud_execution:
            raise ProductionPolicyError("local execution not supported by provider registry")
        if not adapter.charge_cap_enforced:
            raise ProductionPolicyError("provider adapter must enforce charge cap")
        if type(adapter.maximum_cost_usd_micros) is not int or adapter.maximum_cost_usd_micros != 0:
            raise ProductionPolicyError("provider adapter must enforce zero cost ceiling")
        if (
            type(adapter.capabilities) is not frozenset
            or not adapter.capabilities
            or any(type(capability) is not ProviderCapability for capability in adapter.capabilities)
        ):
            raise ProductionPolicyError("provider adapter capabilities must be a non-empty ProviderCapability frozenset")
        key = f"{adapter.provider_id}:{adapter.adapter_id}:{adapter.adapter_version}"
        if key in self.provider_adapters:
            existing = self.provider_adapters[key]
            if (
                existing.capabilities != adapter.capabilities
                or existing.cloud_execution != adapter.cloud_execution
                or existing.charge_cap_enforced != adapter.charge_cap_enforced
                or existing.maximum_cost_usd_micros != adapter.maximum_cost_usd_micros
            ):
                raise ProductionPolicyError("conflicting provider adapter registration")
        self.provider_adapters[key] = adapter

    def freeze_plan(self) -> None:
        if self.plan_frozen:
            raise ProductionPolicyError("plan is already frozen")
        self.plan_frozen = True

    def create_new_plan_revision(self) -> None:
        self.plan_revision += 1
        self.plan_frozen = False

    def add_scene(self, scene: Scene) -> None:
        if self.plan_frozen:
            raise ProductionPolicyError("plan is frozen")
        if not scene.scene_id or scene.scene_id in self.scenes:
            raise ProductionPolicyError("scene id must be non-empty and unique")
        self.scenes[scene.scene_id] = scene

    def add_shot(self, shot: Shot) -> None:
        if self.plan_frozen:
            raise ProductionPolicyError("plan is frozen")
        if not shot.shot_id or shot.shot_id in self.shots:
            raise ProductionPolicyError("shot id must be non-empty and unique")
        if shot.scene_id and shot.scene_id not in self.scenes:
            raise ProductionPolicyError("shot references unknown scene")
        self.shots[shot.shot_id] = shot

    def add_shot_plan(self, plan: ShotPlan) -> None:
        if self.plan_frozen:
            raise ProductionPolicyError("plan is frozen")
        if type(plan) is not ShotPlan:
            raise ProductionPolicyError("plan must be a ShotPlan instance")
        if not plan.shot_id or plan.shot_id not in self.shots:
            raise ProductionPolicyError("plan must reference an existing shot")
        if not plan.scene_id or plan.scene_id not in self.scenes:
            raise ProductionPolicyError("plan must reference an existing scene")

        shot = self.shots[plan.shot_id]
        if shot.scene_id != plan.scene_id:
            raise ProductionPolicyError("plan scene_id must match runtime shot scene_id")
        if plan.has_dialogue_or_audio != shot.has_dialogue_or_audio:
            raise ProductionPolicyError("plan has_dialogue_or_audio must match runtime shot")

        if type(plan.sequence_index) is not int or plan.sequence_index < 0:
            raise ProductionPolicyError("plan sequence_index must be a non-negative integer")
        if type(plan.planned_duration_ms) is not int or plan.planned_duration_ms <= 0:
            raise ProductionPolicyError("plan planned_duration_ms must be a positive integer")
        if type(plan.prompt_fingerprint) is not str or not plan.prompt_fingerprint.strip():
            raise ProductionPolicyError("plan prompt_fingerprint must be a non-empty string")
        if type(plan.has_dialogue_or_audio) is not bool:
            raise ProductionPolicyError("plan has_dialogue_or_audio must be a strict boolean")

        for existing_plan in self.shot_plans.values():
            if existing_plan.scene_id == plan.scene_id and existing_plan.sequence_index == plan.sequence_index and existing_plan.shot_id != plan.shot_id:
                raise ProductionPolicyError("duplicate sequence_index within the same scene is not allowed")

        if plan.shot_id in self.shot_plans:
            existing = self.shot_plans[plan.shot_id]
            if existing != plan:
                raise ProductionPolicyError("conflicting shot plan replacement is not allowed")
            return

        self.shot_plans[plan.shot_id] = plan

    def add_shot_continuity_binding(self, binding: ShotContinuityBinding) -> None:
        if self.plan_frozen:
            raise ProductionPolicyError("plan is frozen")
        if type(binding) is not ShotContinuityBinding:
            raise ProductionPolicyError("invalid continuity binding type")
        if not isinstance(binding.shot_id, str) or not binding.shot_id:
            raise ProductionPolicyError("binding shot_id must be a non-empty string")
        if binding.shot_id not in self.shots:
            raise ProductionPolicyError("binding must reference an existing shot")
        if type(binding.bible_revision) is not int or binding.bible_revision != self.bible.revision:
            raise ProductionPolicyError("binding bible_revision must match current movie-bible revision exactly")

        if type(binding.character_ids) is not frozenset:
            raise ProductionPolicyError("character_ids must be a frozenset")
        for char_id in binding.character_ids:
            if not isinstance(char_id, str) or not char_id:
                raise ProductionPolicyError("character_ids must contain non-empty strings")
            if char_id not in self.bible.characters:
                raise ProductionPolicyError("binding references unknown character")

        if type(binding.voice_ids) is not frozenset:
            raise ProductionPolicyError("voice_ids must be a frozenset")
        for voice_id in binding.voice_ids:
            if not isinstance(voice_id, str) or not voice_id:
                raise ProductionPolicyError("voice_ids must contain non-empty strings")
            if voice_id not in self.bible.voices:
                raise ProductionPolicyError("binding references unknown voice")
        if binding.voice_ids and not binding.character_ids:
            raise ProductionPolicyError("voice continuity requires at least one character in the binding")

        if not isinstance(binding.location_id, str):
            raise ProductionPolicyError("location_id must be a string")
        if binding.location_id and binding.location_id not in self.bible.locations:
            raise ProductionPolicyError("binding references unknown location")

        if type(binding.costume_ids) is not frozenset:
            raise ProductionPolicyError("costume_ids must be a frozenset")
        for costume_id in binding.costume_ids:
            if not isinstance(costume_id, str) or not costume_id:
                raise ProductionPolicyError("costume_ids must contain non-empty strings")
            if costume_id not in self.bible.costumes:
                raise ProductionPolicyError("binding references unknown costume")

        if type(binding.prop_ids) is not frozenset:
            raise ProductionPolicyError("prop_ids must be a frozenset")
        for prop_id in binding.prop_ids:
            if not isinstance(prop_id, str) or not prop_id:
                raise ProductionPolicyError("prop_ids must contain non-empty strings")
            if prop_id not in self.bible.props:
                raise ProductionPolicyError("binding references unknown prop")

        if type(binding.reference_asset_versions) is not frozenset:
            raise ProductionPolicyError("reference_asset_versions must be a frozenset")
        for asset_version in binding.reference_asset_versions:
            if not isinstance(asset_version, str) or not asset_version:
                raise ProductionPolicyError("reference_asset_versions must contain non-empty strings")

        if binding.shot_id in self.shot_continuity_bindings:
            if self.shot_continuity_bindings[binding.shot_id] != binding:
                raise ProductionPolicyError("conflicting continuity binding replacement is not allowed")
            return

        self.shot_continuity_bindings[binding.shot_id] = binding

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
            and job.status
            in {
                JobStatus.QUEUED,
                JobStatus.AUTHORIZED,
                JobStatus.RUNNING,
                JobStatus.RETRYABLE,
            }
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

    def authorize_attempt(
        self,
        job_id: str,
        quote: ProviderQuote,
    ) -> AttemptAuthorization:
        job = self._job(job_id)
        shot = self.shots[job.shot_id]
        self._validate_quote(job, quote)

        adapter_key = f"{quote.provider_id}:{quote.adapter_id}:{quote.adapter_version}"
        if adapter_key not in self.provider_adapters:
            raise ProductionPolicyError("provider adapter not registered")
        adapter = self.provider_adapters[adapter_key]
        if ProviderCapability.VIDEO not in adapter.capabilities:
            raise ProductionPolicyError("registered adapter does not support video generation")
        if not adapter.cloud_execution or not adapter.charge_cap_enforced or adapter.maximum_cost_usd_micros != 0:
            raise ProductionPolicyError("registered adapter is not eligible for zero-cost cloud execution")

        if job.status is JobStatus.AUTHORIZED and job.attempt_history:
            existing = job.attempt_history[-1]
            auth = existing.authorization
            provider_request_key = self._provider_request_key(
                job, quote, existing.attempt_number, auth.generation_epoch
            )
            authorization_id = self._authorization_id(
                provider_request_key, quote.quote_id
            )
            if (
                auth.authorization_id != authorization_id
                or auth.job_id != job.job_id
                or auth.shot_id != job.shot_id
                or auth.input_fingerprint != job.input_fingerprint
                or auth.provider_request_key != provider_request_key
                or auth.provider_id != quote.provider_id
                or auth.adapter_id != quote.adapter_id
                or auth.adapter_version != quote.adapter_version
                or auth.quote_id != quote.quote_id
            ):
                raise ProductionPolicyError("authorization replay changed bound content")
            return auth
        if job.status not in {JobStatus.QUEUED, JobStatus.RETRYABLE}:
            raise ProductionPolicyError("generation job cannot authorize from current status")
        attempt_number = len(job.attempt_history) + 1
        claims_new_epoch = job.generation_epoch is None
        if claims_new_epoch:
            generation_epoch = shot.generation_epoch + 1
        elif (
            shot.generation_owner_job_id != job.job_id
            or shot.generation_epoch != job.generation_epoch
        ):
            raise ProductionPolicyError("generation ownership mismatch")
        else:
            generation_epoch = job.generation_epoch
        provider_request_key = self._provider_request_key(
            job, quote, attempt_number, generation_epoch
        )
        authorization_id = self._authorization_id(
            provider_request_key, quote.quote_id
        )
        if provider_request_key in self.provider_request_index:
            raise ProductionPolicyError("provider request key already used")

        authorization = AttemptAuthorization(
            authorization_id=authorization_id,
            job_id=job.job_id,
            shot_id=job.shot_id,
            generation_epoch=generation_epoch,
            attempt_number=attempt_number,
            provider_id=quote.provider_id,
            adapter_id=quote.adapter_id,
            adapter_version=quote.adapter_version,
            quote_id=quote.quote_id,
            input_fingerprint=job.input_fingerprint,
            provider_request_key=provider_request_key,
            maximum_cost_usd_micros=0,
            cloud_execution=True,
            charge_cap_enforced=True,
        )
        job._authorize(authorization)
        if claims_new_epoch:
            shot.generation_epoch = generation_epoch
            shot.generation_owner_job_id = job.job_id
        ref = self._attempt_ref(job.job_id, attempt_number)
        self.authorization_index[authorization_id] = ref
        self.provider_request_index[provider_request_key] = ref
        shot.reviews.clear()
        shot.canonical = False
        shot.upscale_allowed = False
        shot.asset_version = ""
        shot.status = ShotStatus.GENERATING
        return authorization

    def start_generation(
        self, job_id: str, receipt: ProviderSubmissionReceipt
    ) -> None:
        job = self._job(job_id)
        if not job.attempt_history:
            raise ProductionPolicyError("generation job lacks authorization history")
        attempt = job.attempt_history[-1]
        self._validate_attempt(job, attempt, len(job.attempt_history))
        auth = attempt.authorization
        expected_request_key = self._provider_request_key_from_authorization(auth)
        expected_authorization_id = self._authorization_id(
            expected_request_key, auth.quote_id
        )
        ref = self._attempt_ref(job.job_id, attempt.attempt_number)
        if (
            auth.provider_request_key != expected_request_key
            or auth.authorization_id != expected_authorization_id
            or self.authorization_index.get(auth.authorization_id) != ref
            or self.provider_request_index.get(auth.provider_request_key) != ref
        ):
            raise ProductionPolicyError("current authorization is not ledger-authenticated")
        if (
            job.generation_epoch != self.shots[job.shot_id].generation_epoch
            or self.shots[job.shot_id].generation_owner_job_id != job.job_id
        ):
            raise ProductionPolicyError("generation ownership mismatch")
        job._start(receipt)

    def finish_generation(
        self,
        job_id: str,
        attempt_number: int,
        provider_job_id: str,
        asset_version: str,
    ) -> None:
        job = self._job(job_id)
        if job.status is JobStatus.SUCCEEDED and job.attempt_history:
            latest = job.attempt_history[-1]
            if (
                latest.attempt_number == attempt_number
                and latest.provider_job_id == provider_job_id
                and latest.output_asset_version == asset_version
            ):
                return
            raise ProductionPolicyError("conflicting duplicate success callback")
        self._validate_generation_owner(job)
        job._succeed(attempt_number, provider_job_id, asset_version)
        self.shots[job.shot_id].bind_generated_asset(asset_version)

    def fail_generation(
        self,
        job_id: str,
        attempt_number: int,
        provider_job_id: str,
        failure_class: FailureClass,
        reason: str,
    ) -> None:
        job = self._job(job_id)
        if job.status in {JobStatus.RETRYABLE, JobStatus.EXHAUSTED} and job.attempt_history:
            latest = job.attempt_history[-1]
            if (
                latest.attempt_number == attempt_number
                and latest.provider_job_id == provider_job_id
                and latest.failure_class is failure_class
                and latest.failure_reason == reason
            ):
                return
            raise ProductionPolicyError("conflicting duplicate failure callback")
        self._validate_generation_owner(job)
        job._fail(attempt_number, provider_job_id, failure_class, reason)
        self.shots[job.shot_id].status = (
            ShotStatus.BLOCKED
            if job.status is JobStatus.EXHAUSTED
            else ShotStatus.PLANNED
        )

    def _validate_generation_owner(self, job: GenerationJob) -> None:
        shot = self.shots[job.shot_id]
        if (
            job.generation_epoch != shot.generation_epoch
            or shot.generation_owner_job_id != job.job_id
        ):
            raise ProductionPolicyError("stale generation owner")

    @staticmethod
    def _validate_quote(job: GenerationJob, quote: ProviderQuote) -> None:
        if type(quote) is not ProviderQuote:
            raise ProductionPolicyError("invalid provider quote type")
        string_values = (
            quote.quote_id,
            quote.provider_id,
            quote.adapter_id,
            quote.adapter_version,
            quote.request_fingerprint,
        )
        if any(
            not isinstance(value, str) or not value.strip()
            for value in string_values
        ):
            raise ProductionPolicyError("quote identifiers must be non-empty strings")
        if (
            type(quote.estimated_cost_usd_micros) is not int
            or quote.estimated_cost_usd_micros != 0
            or type(quote.maximum_cost_usd_micros) is not int
            or quote.maximum_cost_usd_micros != 0
            or quote.available is not True
            or quote.cloud_execution is not True
            or quote.charge_cap_enforced is not True
            or quote.request_fingerprint != job.input_fingerprint
        ):
            raise ProductionPolicyError("quote is not eligible for zero-cost cloud execution")

    @staticmethod
    def _attempt_ref(job_id: str, attempt_number: int) -> str:
        return f"{job_id}:{attempt_number}"

    def _provider_request_key(
        self,
        job: GenerationJob,
        quote: ProviderQuote,
        attempt_number: int,
        generation_epoch: int,
    ) -> str:
        material = json.dumps(
            [
                self.project_id,
                job.job_id,
                job.shot_id,
                generation_epoch,
                attempt_number,
                quote.provider_id,
                quote.adapter_id,
                quote.adapter_version,
                job.input_fingerprint,
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return "movie-studio-v2:" + sha256(material.encode("utf-8")).hexdigest()

    def _provider_request_key_from_authorization(
        self, authorization: AttemptAuthorization
    ) -> str:
        material = json.dumps(
            [
                self.project_id,
                authorization.job_id,
                authorization.shot_id,
                authorization.generation_epoch,
                authorization.attempt_number,
                authorization.provider_id,
                authorization.adapter_id,
                authorization.adapter_version,
                authorization.input_fingerprint,
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return "movie-studio-v2:" + sha256(material.encode("utf-8")).hexdigest()

    @staticmethod
    def _authorization_id(provider_request_key: str, quote_id: str) -> str:
        material = json.dumps(
            [provider_request_key, quote_id],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return "authorization-v2:" + sha256(material.encode("utf-8")).hexdigest()

    def _attempt_by_ref(self, ref: str) -> GenerationAttempt:
        try:
            job_id, attempt_text = ref.rsplit(":", 1)
            attempt_number = int(attempt_text)
            return self.jobs[job_id].attempt_history[attempt_number - 1]
        except (KeyError, IndexError, ValueError) as exc:
            raise ProductionPolicyError("corrupt attempt index") from exc

    def _job(self, job_id: str) -> GenerationJob:
        try:
            return self.jobs[job_id]
        except KeyError as exc:
            raise ProductionPolicyError("unknown generation job") from exc

    def to_dict(self) -> dict:
        self.validate()

        def to_json_types(value):
            if isinstance(value, Enum):
                return value.value
            if isinstance(value, dict):
                return {to_json_types(k): to_json_types(v) for k, v in value.items()}
            if isinstance(value, frozenset):
                return sorted(list(value))
            if isinstance(value, (list, tuple)):
                return [to_json_types(v) for v in value]
            return value

        raw = to_json_types(asdict(self))
        return {
            "schema_version": SCHEMA_VERSION,
            "project_id": raw["project_id"],
            "episode_id": raw["episode_id"],
            "episode_status": raw["episode_status"],
            "plan_revision": raw["plan_revision"],
            "plan_frozen": raw["plan_frozen"],
            "spend_limit_usd_micros": 0,
            "production": dict(PRODUCTION_CONTRACT),
            "movie_bible": raw["bible"],
            "scenes": raw["scenes"],
            "shots": raw["shots"],
            "shot_plans": raw["shot_plans"],
            "shot_continuity_bindings": raw["shot_continuity_bindings"],
            "generation_jobs": raw["jobs"],
            "idempotency_index": raw["idempotency_index"],
            "authorization_index": raw["authorization_index"],
            "provider_request_index": raw["provider_request_index"],
            "provider_adapters": raw["provider_adapters"],
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
        data = dict(data)
        data.setdefault("episode_id", "")
        data.setdefault("episode_status", EpisodeStatus.PLANNED.value)
        data.setdefault("plan_revision", 1)
        data.setdefault("plan_frozen", False)
        data.setdefault("scenes", {})
        data.setdefault("shot_plans", {})
        data.setdefault("shot_continuity_bindings", {})

        root_fields = {
            "schema_version",
            "project_id",
            "episode_id",
            "episode_status",
            "plan_revision",
            "plan_frozen",
            "spend_limit_usd_micros",
            "production",
            "movie_bible",
            "scenes",
            "shots",
            "shot_plans",
            "shot_continuity_bindings",
            "generation_jobs",
            "idempotency_index",
            "authorization_index",
            "provider_request_index",
            "provider_adapters",
        }
        if not isinstance(data, Mapping):
            raise ProductionPolicyError("production checkpoint fields mismatch")
        actual_fields = set(data.keys())
        if "provider_adapters" not in actual_fields:
            actual_fields.add("provider_adapters")
        if "shot_plans" not in actual_fields:
            actual_fields.add("shot_plans")
        if actual_fields != root_fields:
            raise ProductionPolicyError("production checkpoint fields mismatch")
        if (
            type(data.get("schema_version")) is not int
            or data.get("schema_version") != SCHEMA_VERSION
        ):
            raise ProductionPolicyError("unsupported production-state schema version")
        try:
            episode_status = EpisodeStatus(data["episode_status"])
        except ValueError as exc:
            raise ProductionPolicyError("invalid episode status") from exc

        if type(data.get("plan_revision")) is not int or data.get("plan_revision") < 1:
            raise ProductionPolicyError("invalid plan_revision")
        if type(data.get("plan_frozen")) is not bool:
            raise ProductionPolicyError("invalid plan_frozen")

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
            "costumes",
            "props",
            "continuity_facts",
        }
        if not isinstance(bible_raw, Mapping):
            raise ProductionPolicyError("movie-bible must be a mapping")
        if not set(bible_fields).issuperset(set(bible_raw)):
            raise ProductionPolicyError("movie-bible fields mismatch")
        # Ensure older checkpoints without costumes and props will work
        bible_raw.setdefault("costumes", {})
        bible_raw.setdefault("props", {})
        if set(bible_raw) != bible_fields:
            raise ProductionPolicyError("movie-bible fields mismatch")
        if not isinstance(data["scenes"], Mapping):
            raise ProductionPolicyError("scenes must be a mapping")
        if not isinstance(data["shots"], Mapping):
            raise ProductionPolicyError("shots must be a mapping")
        if not isinstance(data["shot_plans"], Mapping):
            raise ProductionPolicyError("shot plans must be a mapping")
        if not isinstance(data["shot_continuity_bindings"], Mapping):
            raise ProductionPolicyError("shot continuity bindings must be a mapping")
        if not isinstance(data["generation_jobs"], Mapping):
            raise ProductionPolicyError("generation jobs must be a mapping")
        if not isinstance(data["idempotency_index"], Mapping):
            raise ProductionPolicyError("idempotency index must be a mapping")
        if not isinstance(data["authorization_index"], Mapping):
            raise ProductionPolicyError("authorization index must be a mapping")
        if not isinstance(data["provider_request_index"], Mapping):
            raise ProductionPolicyError("provider request index must be a mapping")
        ledger = cls(
            project_id=data["project_id"],
            episode_id=data["episode_id"],
            episode_status=episode_status,
            plan_revision=data["plan_revision"],
            plan_frozen=data["plan_frozen"],
            bible=MovieBible(**bible_raw),
        )
        for scene_id, raw in data.get("scenes", {}).items():
            raw = dict(raw)
            raw.setdefault("status", SceneStatus.PLANNED.value)
            scene_fields = {"scene_id", "status"}
            if not isinstance(raw, Mapping) or set(raw) != scene_fields:
                raise ProductionPolicyError("scene fields mismatch")
            try:
                scene_status = SceneStatus(raw["status"])
            except ValueError as exc:
                raise ProductionPolicyError("invalid scene status") from exc
            ledger.scenes[scene_id] = Scene(scene_id=raw["scene_id"], status=scene_status)
        for shot_id, raw in data.get("shot_continuity_bindings", {}).items():
            raw = dict(raw)
            binding_fields = {
                "shot_id",
                "bible_revision",
                "character_ids",
                "voice_ids",
                "location_id",
                "costume_ids",
                "prop_ids",
                "reference_asset_versions",
            }
            if not isinstance(raw, Mapping) or set(raw) != binding_fields:
                raise ProductionPolicyError("shot continuity binding fields mismatch")
            for field_name in ["character_ids", "voice_ids", "costume_ids", "prop_ids", "reference_asset_versions"]:
                field_list = raw[field_name]
                if not isinstance(field_list, list):
                    raise ProductionPolicyError(f"shot continuity binding {field_name} must be a list")
                if len(field_list) != len(set(field_list)):
                    raise ProductionPolicyError(f"shot continuity binding {field_name} contains duplicates")
                raw[field_name] = frozenset(field_list)

            if type(raw.get("bible_revision")) is not int or raw.get("bible_revision") != ledger.bible.revision:
                raise ProductionPolicyError("binding bible_revision must match current movie-bible revision exactly")

            try:
                ledger.shot_continuity_bindings[shot_id] = ShotContinuityBinding(**raw)
            except (TypeError, ValueError) as exc:
                raise ProductionPolicyError("malformed shot continuity binding") from exc

        for shot_id, raw in data.get("shots", {}).items():
            raw = dict(raw)
            raw.setdefault("scene_id", "")

            shot_fields = {
                "shot_id",
                "scene_id",
                "asset_version",
                "has_dialogue_or_audio",
                "status",
                "reviews",
                "canonical",
                "upscale_allowed",
                "generation_epoch",
                "generation_owner_job_id",
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
                scene_id=raw["scene_id"],
                asset_version=raw["asset_version"],
                has_dialogue_or_audio=raw["has_dialogue_or_audio"],
                status=ShotStatus(raw["status"]),
                reviews=reviews,
                canonical=raw["canonical"],
                upscale_allowed=raw["upscale_allowed"],
                generation_epoch=raw["generation_epoch"],
                generation_owner_job_id=raw["generation_owner_job_id"],
            )
        for plan_id, raw in data.get("shot_plans", {}).items():
            plan_fields = {
                "shot_id",
                "scene_id",
                "sequence_index",
                "planned_duration_ms",
                "prompt_fingerprint",
                "has_dialogue_or_audio",
            }
            if not isinstance(raw, Mapping) or set(raw) != plan_fields:
                raise ProductionPolicyError("shot plan fields mismatch")
            ledger.shot_plans[plan_id] = ShotPlan(
                shot_id=raw["shot_id"],
                scene_id=raw["scene_id"],
                sequence_index=raw["sequence_index"],
                planned_duration_ms=raw["planned_duration_ms"],
                prompt_fingerprint=raw["prompt_fingerprint"],
                has_dialogue_or_audio=raw["has_dialogue_or_audio"],
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
                "generation_epoch",
                "attempt_history",
            }
            if not isinstance(raw, Mapping) or set(raw) != job_fields:
                raise ProductionPolicyError("generation-job fields mismatch")
            raw = dict(raw)
            raw["status"] = JobStatus(raw["status"])
            attempts = []
            for attempt_raw in raw["attempt_history"]:
                if not isinstance(attempt_raw, Mapping) or set(attempt_raw) != {
                    "attempt_number",
                    "authorization",
                    "status",
                    "provider_job_id",
                    "output_asset_version",
                    "failure_class",
                    "failure_reason",
                }:
                    raise ProductionPolicyError("generation-attempt fields mismatch")
                auth_raw = attempt_raw["authorization"]
                if not isinstance(auth_raw, Mapping) or set(auth_raw) != {
                    "authorization_id",
                    "job_id",
                    "shot_id",
                    "generation_epoch",
                    "attempt_number",
                    "provider_id",
                    "adapter_id",
                    "adapter_version",
                    "quote_id",
                    "input_fingerprint",
                    "provider_request_key",
                    "maximum_cost_usd_micros",
                    "cloud_execution",
                    "charge_cap_enforced",
                }:
                    raise ProductionPolicyError("attempt-authorization fields mismatch")
                attempts.append(
                    GenerationAttempt(
                        attempt_number=attempt_raw["attempt_number"],
                        authorization=AttemptAuthorization(**auth_raw),
                        status=AttemptStatus(attempt_raw["status"]),
                        provider_job_id=attempt_raw["provider_job_id"],
                        output_asset_version=attempt_raw["output_asset_version"],
                        failure_class=(
                            FailureClass(attempt_raw["failure_class"])
                            if attempt_raw["failure_class"] is not None
                            else None
                        ),
                        failure_reason=attempt_raw["failure_reason"],
                    )
                )
            raw["attempt_history"] = tuple(attempts)
            ledger.jobs[job_id] = GenerationJob(**raw)
        ledger.idempotency_index = dict(data.get("idempotency_index", {}))
        ledger.authorization_index = dict(data.get("authorization_index", {}))
        ledger.provider_request_index = dict(data.get("provider_request_index", {}))
        provider_adapters = data.get("provider_adapters", {})
        if not isinstance(provider_adapters, Mapping):
            raise ProductionPolicyError("provider adapters must be a mapping")
        for key, raw in provider_adapters.items():
            if not isinstance(raw, Mapping):
                raise ProductionPolicyError("provider adapter must be a mapping")
            try:
                capabilities = frozenset(
                    ProviderCapability(c) for c in raw.get("capabilities", [])
                )
                ledger.provider_adapters[key] = ProviderAdapterRegistration(
                    provider_id=raw["provider_id"],
                    adapter_id=raw["adapter_id"],
                    adapter_version=raw["adapter_version"],
                    capabilities=capabilities,
                    cloud_execution=raw.get("cloud_execution", True),
                    charge_cap_enforced=raw.get("charge_cap_enforced", True),
                    maximum_cost_usd_micros=raw.get("maximum_cost_usd_micros", 0),
                )
            except (KeyError, ValueError, TypeError) as exc:
                raise ProductionPolicyError("malformed provider adapter registration") from exc

        ledger.validate()
        return ledger

    def validate(self) -> None:
        if not isinstance(self.project_id, str) or not self.project_id:
            raise ProductionPolicyError("project id must not be empty")
        if not isinstance(self.episode_id, str):
            raise ProductionPolicyError("episode id must be a string")
        if type(self.episode_status) is not EpisodeStatus:
            raise ProductionPolicyError("invalid episode status")
        if type(self.plan_revision) is not int or self.plan_revision < 1:
            raise ProductionPolicyError("invalid plan_revision")
        if type(self.plan_frozen) is not bool:
            raise ProductionPolicyError("invalid plan_frozen")
        if type(self.bible.revision) is not int or self.bible.revision < 1:
            raise ProductionPolicyError("invalid movie-bible revision")
        self._validate_bible_maps()
        for scene_key, scene in self.scenes.items():
            if not isinstance(scene_key, str) or not scene_key:
                raise ProductionPolicyError("scene id must not be empty")
            if scene_key != scene.scene_id:
                raise ProductionPolicyError("scene key/id mismatch")
            if type(scene.status) is not SceneStatus:
                raise ProductionPolicyError("invalid scene status")
        for shot_key, shot in self.shots.items():
            if not isinstance(shot_key, str) or not shot_key:
                raise ProductionPolicyError("shot id must not be empty")
            if shot_key != shot.shot_id:
                raise ProductionPolicyError("shot key/id mismatch")
            if not isinstance(shot.scene_id, str):
                raise ProductionPolicyError("shot scene_id must be a string")
            if shot.scene_id and shot.scene_id not in self.scenes:
                raise ProductionPolicyError("shot references unknown scene")
            if type(shot.status) is not ShotStatus:
                raise ProductionPolicyError("invalid shot status")

        for binding_key, binding in self.shot_continuity_bindings.items():
            if type(binding) is not ShotContinuityBinding:
                raise ProductionPolicyError("shot continuity binding must be a ShotContinuityBinding instance")
            if binding_key != binding.shot_id:
                raise ProductionPolicyError("shot continuity binding key must match binding.shot_id")
            if not binding.shot_id or binding.shot_id not in self.shots:
                raise ProductionPolicyError("shot continuity binding has dangling shot reference")
            if type(binding.bible_revision) is not int or binding.bible_revision != self.bible.revision:
                raise ProductionPolicyError("binding bible_revision must match current movie-bible revision exactly")
            if type(binding.character_ids) is not frozenset:
                raise ProductionPolicyError("character_ids must be a frozenset")
            for char_id in binding.character_ids:
                if not isinstance(char_id, str) or not char_id or char_id not in self.bible.characters:
                    raise ProductionPolicyError("dangling character reference in binding")
            if type(binding.voice_ids) is not frozenset:
                raise ProductionPolicyError("voice_ids must be a frozenset")
            for voice_id in binding.voice_ids:
                if not isinstance(voice_id, str) or not voice_id or voice_id not in self.bible.voices:
                    raise ProductionPolicyError("dangling voice reference in binding")
            if binding.voice_ids and not binding.character_ids:
                raise ProductionPolicyError("voice continuity binding requires a character")
            if not isinstance(binding.location_id, str):
                raise ProductionPolicyError("binding location_id must be string")
            if binding.location_id and binding.location_id not in self.bible.locations:
                raise ProductionPolicyError("dangling location reference in binding")
            if type(binding.costume_ids) is not frozenset:
                raise ProductionPolicyError("costume_ids must be a frozenset")
            for costume_id in binding.costume_ids:
                if not isinstance(costume_id, str) or not costume_id or costume_id not in self.bible.costumes:
                    raise ProductionPolicyError("dangling costume reference in binding")
            if type(binding.prop_ids) is not frozenset:
                raise ProductionPolicyError("prop_ids must be a frozenset")
            for prop_id in binding.prop_ids:
                if not isinstance(prop_id, str) or not prop_id or prop_id not in self.bible.props:
                    raise ProductionPolicyError("dangling prop reference in binding")
            if type(binding.reference_asset_versions) is not frozenset:
                raise ProductionPolicyError("reference_asset_versions must be a frozenset")
            for asset_version in binding.reference_asset_versions:
                if not isinstance(asset_version, str) or not asset_version:
                    raise ProductionPolicyError("invalid reference asset version in binding")

        sequence_indices_by_scene = {}
        for plan_key, plan in self.shot_plans.items():
            if type(plan) is not ShotPlan:
                raise ProductionPolicyError("shot plan must be a ShotPlan instance")
            if plan_key != plan.shot_id:
                raise ProductionPolicyError("shot plan key must match plan.shot_id")
            if not plan.shot_id or plan.shot_id not in self.shots:
                raise ProductionPolicyError("shot plan has dangling shot reference")
            if not plan.scene_id or plan.scene_id not in self.scenes:
                raise ProductionPolicyError("shot plan has dangling scene reference")

            shot = self.shots[plan.shot_id]
            if shot.scene_id != plan.scene_id:
                raise ProductionPolicyError("shot plan scene_id must match runtime shot")
            if plan.has_dialogue_or_audio != shot.has_dialogue_or_audio:
                raise ProductionPolicyError("shot plan has_dialogue_or_audio must match runtime shot")

            if type(plan.sequence_index) is not int or plan.sequence_index < 0:
                raise ProductionPolicyError("shot plan sequence_index must be non-negative integer")
            if type(plan.planned_duration_ms) is not int or plan.planned_duration_ms <= 0:
                raise ProductionPolicyError("shot plan planned_duration_ms must be positive integer")
            if type(plan.prompt_fingerprint) is not str or not plan.prompt_fingerprint.strip():
                raise ProductionPolicyError("shot plan prompt_fingerprint must be non-empty string")
            if type(plan.has_dialogue_or_audio) is not bool:
                raise ProductionPolicyError("shot plan has_dialogue_or_audio must be strict boolean")

            scene_indices = sequence_indices_by_scene.setdefault(plan.scene_id, set())
            if plan.sequence_index in scene_indices:
                raise ProductionPolicyError("duplicate sequence_index within the same scene is not allowed")
            scene_indices.add(plan.sequence_index)

        for shot_key, shot in self.shots.items():
            if (
                not isinstance(shot.asset_version, str)
                or type(shot.has_dialogue_or_audio) is not bool
                or type(shot.canonical) is not bool
                or type(shot.upscale_allowed) is not bool
                or type(shot.generation_epoch) is not int
                or shot.generation_epoch < 0
                or (
                    shot.generation_owner_job_id is not None
                    and (
                        not isinstance(shot.generation_owner_job_id, str)
                        or not shot.generation_owner_job_id
                    )
                )
            ):
                raise ProductionPolicyError("invalid shot field type")
            if (shot.generation_epoch == 0) != (
                shot.generation_owner_job_id is None
            ):
                raise ProductionPolicyError("shot generation ownership is inconsistent")
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
        derived_authorizations: Dict[str, str] = {}
        derived_requests: Dict[str, str] = {}
        for adapter_key, adapter in self.provider_adapters.items():
            if not isinstance(adapter_key, str) or not adapter_key:
                raise ProductionPolicyError("provider adapter key must not be empty")
            if type(adapter) is not ProviderAdapterRegistration:
                raise ProductionPolicyError("invalid provider adapter registration")
            if any(
                not isinstance(val, str) or not val.strip()
                for val in (adapter.provider_id, adapter.adapter_id, adapter.adapter_version)
            ):
                raise ProductionPolicyError("adapter identifiers must be non-empty strings")
            expected_key = f"{adapter.provider_id}:{adapter.adapter_id}:{adapter.adapter_version}"
            if adapter_key != expected_key:
                raise ProductionPolicyError("provider adapter key mismatch")
            if type(adapter.cloud_execution) is not bool or not adapter.cloud_execution:
                raise ProductionPolicyError("cloud execution must be true")
            if type(adapter.charge_cap_enforced) is not bool or not adapter.charge_cap_enforced:
                raise ProductionPolicyError("charge cap enforced must be true")
            if type(adapter.maximum_cost_usd_micros) is not int or adapter.maximum_cost_usd_micros != 0:
                raise ProductionPolicyError("maximum cost must be zero")
            if (
                type(adapter.capabilities) is not frozenset
                or not adapter.capabilities
                or any(type(capability) is not ProviderCapability for capability in adapter.capabilities)
            ):
                raise ProductionPolicyError("invalid provider adapter capabilities")

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
            if job.attempts != len(job.attempt_history):
                raise ProductionPolicyError("attempt counter/history mismatch")
            if job.generation_epoch is not None and (
                type(job.generation_epoch) is not int or job.generation_epoch < 1
            ):
                raise ProductionPolicyError("invalid job generation epoch")
            if job.attempt_history and job.generation_epoch is None:
                raise ProductionPolicyError("attempt history lacks generation epoch")
            if job.idempotency_key in derived_index:
                raise ProductionPolicyError("duplicate job idempotency key")
            derived_index[job.idempotency_key] = job.job_id
            for expected_number, attempt in enumerate(job.attempt_history, start=1):
                self._validate_attempt(job, attempt, expected_number)
                ref = self._attempt_ref(job.job_id, expected_number)
                auth_id = attempt.authorization.authorization_id
                request_key = attempt.authorization.provider_request_key
                expected_request_key = self._provider_request_key_from_authorization(
                    attempt.authorization
                )
                expected_auth_id = self._authorization_id(
                    expected_request_key, attempt.authorization.quote_id
                )
                if request_key != expected_request_key or auth_id != expected_auth_id:
                    raise ProductionPolicyError(
                        "attempt authorization identifiers are not deterministic"
                    )
                if auth_id in derived_authorizations:
                    raise ProductionPolicyError("duplicate authorization id")
                if request_key in derived_requests:
                    raise ProductionPolicyError("duplicate provider request key")
                derived_authorizations[auth_id] = ref
                derived_requests[request_key] = ref
            self._validate_job_status(job)
        if self.idempotency_index != derived_index:
            raise ProductionPolicyError("idempotency index is not an exact job bijection")
        if any(
            not isinstance(key, str)
            or not key.strip()
            or not isinstance(value, str)
            or not value.strip()
            for key, value in self.idempotency_index.items()
        ):
            raise ProductionPolicyError("invalid idempotency index entry")
        if self.authorization_index != derived_authorizations:
            raise ProductionPolicyError("authorization index is not an exact bijection")
        if self.provider_request_index != derived_requests:
            raise ProductionPolicyError("provider request index is not an exact bijection")

        active_by_shot: Dict[str, str] = {}
        for job in self.jobs.values():
            if job.status in {
                JobStatus.QUEUED,
                JobStatus.AUTHORIZED,
                JobStatus.RUNNING,
                JobStatus.RETRYABLE,
            }:
                if job.shot_id in active_by_shot:
                    raise ProductionPolicyError("multiple active jobs target one shot")
                active_by_shot[job.shot_id] = job.job_id
                if job.status is not JobStatus.QUEUED:
                    shot = self.shots[job.shot_id]
                    if (
                        shot.generation_owner_job_id != job.job_id
                        or shot.generation_epoch != job.generation_epoch
                    ):
                        raise ProductionPolicyError("active job does not own shot epoch")
        generating_by_shot = {
            job.shot_id
            for job in self.jobs.values()
            if job.status in {JobStatus.AUTHORIZED, JobStatus.RUNNING}
        }
        for shot in self.shots.values():
            if (shot.status is ShotStatus.GENERATING) != (
                shot.shot_id in generating_by_shot
            ):
                raise ProductionPolicyError("generating shot/active attempt mismatch")
            epoch_jobs = [
                job
                for job in self.jobs.values()
                if job.shot_id == shot.shot_id and job.generation_epoch is not None
            ]
            if epoch_jobs:
                epochs = sorted(job.generation_epoch for job in epoch_jobs)
                if epochs != list(range(1, len(epoch_jobs) + 1)):
                    raise ProductionPolicyError(
                        "shot generation epochs must be unique and contiguous"
                    )
                latest_epoch = max(job.generation_epoch for job in epoch_jobs)
                latest_jobs = [
                    job for job in epoch_jobs if job.generation_epoch == latest_epoch
                ]
                if (
                    len(latest_jobs) != 1
                    or shot.generation_epoch != latest_epoch
                    or shot.generation_owner_job_id != latest_jobs[0].job_id
                ):
                    raise ProductionPolicyError(
                        "shot does not identify its unique latest generation owner"
                    )
            elif (
                shot.generation_epoch != 0
                or shot.generation_owner_job_id is not None
            ):
                raise ProductionPolicyError("shot has generation ownership without a job")
            if shot.generation_owner_job_id is not None:
                owner = self.jobs.get(shot.generation_owner_job_id)
                if (
                    owner is None
                    or owner.shot_id != shot.shot_id
                    or owner.generation_epoch != shot.generation_epoch
                ):
                    raise ProductionPolicyError("shot generation owner is invalid")
                if owner.status in {JobStatus.AUTHORIZED, JobStatus.RUNNING}:
                    valid_lifecycle = (
                        shot.status is ShotStatus.GENERATING
                        and not shot.asset_version
                    )
                elif owner.status is JobStatus.RETRYABLE:
                    valid_lifecycle = (
                        shot.status is ShotStatus.PLANNED and not shot.asset_version
                    )
                elif owner.status is JobStatus.EXHAUSTED:
                    valid_lifecycle = (
                        shot.status is ShotStatus.BLOCKED and not shot.asset_version
                    )
                elif owner.status is JobStatus.SUCCEEDED:
                    valid_lifecycle = (
                        shot.status
                        in {
                            ShotStatus.GENERATED,
                            ShotStatus.APPROVED,
                            ShotStatus.CANONICAL,
                            ShotStatus.REJECTED,
                        }
                        and bool(owner.output_asset_version)
                        and shot.asset_version == owner.output_asset_version
                    )
                else:
                    valid_lifecycle = False
                if not valid_lifecycle:
                    raise ProductionPolicyError(
                        "shot lifecycle does not match generation owner"
                    )

    @staticmethod
    def _validate_attempt(
        job: GenerationJob, attempt: GenerationAttempt, expected_number: int
    ) -> None:
        if type(attempt) is not GenerationAttempt:
            raise ProductionPolicyError("invalid generation-attempt type")
        auth = attempt.authorization
        if type(auth) is not AttemptAuthorization:
            raise ProductionPolicyError("invalid attempt-authorization type")
        if (
            type(attempt.attempt_number) is not int
            or attempt.attempt_number != expected_number
            or type(auth.attempt_number) is not int
            or auth.attempt_number != expected_number
            or type(auth.generation_epoch) is not int
            or auth.job_id != job.job_id
            or auth.shot_id != job.shot_id
            or auth.input_fingerprint != job.input_fingerprint
            or auth.generation_epoch != job.generation_epoch
            or type(auth.maximum_cost_usd_micros) is not int
            or auth.maximum_cost_usd_micros != 0
            or auth.cloud_execution is not True
            or auth.charge_cap_enforced is not True
            or any(
                not isinstance(value, str) or not value.strip()
                for value in (
                    auth.authorization_id,
                    auth.provider_id,
                    auth.adapter_id,
                    auth.adapter_version,
                    auth.quote_id,
                    auth.provider_request_key,
                )
            )
        ):
            raise ProductionPolicyError("attempt authorization binding is invalid")
        if type(attempt.status) is not AttemptStatus:
            raise ProductionPolicyError("invalid attempt status")
        if any(
            value is not None and not isinstance(value, str)
            for value in (
                attempt.provider_job_id,
                attempt.output_asset_version,
                attempt.failure_reason,
            )
        ):
            raise ProductionPolicyError("invalid attempt evidence type")
        if attempt.status is AttemptStatus.AUTHORIZED:
            valid = all(
                value is None
                for value in (
                    attempt.provider_job_id,
                    attempt.output_asset_version,
                    attempt.failure_class,
                    attempt.failure_reason,
                )
            )
        elif attempt.status is AttemptStatus.RUNNING:
            valid = (
                bool(attempt.provider_job_id)
                and attempt.output_asset_version is None
                and attempt.failure_class is None
                and attempt.failure_reason is None
            )
        elif attempt.status is AttemptStatus.SUCCEEDED:
            valid = (
                bool(attempt.provider_job_id)
                and bool(attempt.output_asset_version)
                and attempt.failure_class is None
                and attempt.failure_reason is None
            )
        else:
            valid = (
                bool(attempt.provider_job_id)
                and attempt.output_asset_version is None
                and type(attempt.failure_class) is FailureClass
                and bool(attempt.failure_reason)
            )
        if not valid:
            raise ProductionPolicyError("attempt status evidence is inconsistent")

    def _validate_bible_maps(self) -> None:
        scalar_maps = (self.bible.story_rules, self.bible.continuity_facts)
        entity_maps = (
            self.bible.characters,
            self.bible.voices,
            self.bible.locations,
            self.bible.costumes,
            self.bible.props,
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
        history = job.attempt_history
        if any(
            attempt.status is not AttemptStatus.FAILED
            or attempt.failure_class is not FailureClass.RETRYABLE_PROVIDER
            for attempt in history[:-1]
        ):
            raise ProductionPolicyError("nonterminal attempt history is invalid")
        latest = history[-1] if history else None
        if job.status is JobStatus.QUEUED:
            valid = (
                job.attempts == 0
                and not history
                and job.generation_epoch is None
                and job.provider_job_id is None
                and job.output_asset_version is None
                and job.last_error is None
            )
        elif job.status is JobStatus.AUTHORIZED:
            valid = (
                latest is not None
                and latest.status is AttemptStatus.AUTHORIZED
                and job.provider_job_id is None
                and job.output_asset_version is None
                and job.last_error is None
            )
        elif job.status is JobStatus.RUNNING:
            valid = (
                latest is not None
                and latest.status is AttemptStatus.RUNNING
                and job.provider_job_id == latest.provider_job_id
                and job.output_asset_version is None
                and job.last_error is None
            )
        elif job.status is JobStatus.SUCCEEDED:
            valid = (
                latest is not None
                and latest.status is AttemptStatus.SUCCEEDED
                and job.provider_job_id == latest.provider_job_id
                and job.output_asset_version == latest.output_asset_version
                and job.last_error is None
            )
        elif job.status is JobStatus.RETRYABLE:
            valid = (
                latest is not None
                and latest.status is AttemptStatus.FAILED
                and latest.failure_class is FailureClass.RETRYABLE_PROVIDER
                and job.attempts < job.max_attempts
                and job.provider_job_id == latest.provider_job_id
                and job.output_asset_version is None
                and job.last_error == latest.failure_reason
            )
        elif job.status is JobStatus.EXHAUSTED:
            valid = (
                latest is not None
                and latest.status is AttemptStatus.FAILED
                and (
                    job.attempts == job.max_attempts
                    or latest.failure_class is not FailureClass.RETRYABLE_PROVIDER
                )
                and job.provider_job_id == latest.provider_job_id
                and job.output_asset_version is None
                and job.last_error == latest.failure_reason
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
        if all(
            isinstance(value, str) and bool(value.strip())
            for value in (
                q.quote_id,
                q.provider_id,
                q.adapter_id,
                q.adapter_version,
                q.request_fingerprint,
            )
        )
        and type(q.estimated_cost_usd_micros) is int
        and q.estimated_cost_usd_micros == 0
        and type(q.maximum_cost_usd_micros) is int
        and q.maximum_cost_usd_micros == 0
        and q.available is True
        and q.cloud_execution is True
        and q.charge_cap_enforced is True
    ]
    if not eligible:
        raise ProductionPolicyError("BLOCKED_ZERO_COST_PROVIDER_UNAVAILABLE")
    return sorted(
        eligible,
        key=lambda q: (q.provider_id, q.adapter_id, q.adapter_version, q.quote_id),
    )[0]


def episode_can_complete(
    *,
    all_shots_canonical: bool,
    final_qc_passed: bool,
    drive_master_verified: bool,
) -> bool:
    values = (all_shots_canonical, final_qc_passed, drive_master_verified)
    return all(type(value) is bool and value for value in values)
