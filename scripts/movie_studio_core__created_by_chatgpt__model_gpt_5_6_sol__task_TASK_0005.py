"""TASK-0005 deterministic Movie Studio production gate core.

Creator: ChatGPT
Model: GPT-5.6 Sol
Run: 20261006_TASK_0005_CORE
No provider/network calls belong in this module.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, FrozenSet, Iterable

class Gate(str, Enum):
    VISUAL_QA="VISUAL_QA"; CONTINUITY_QA="CONTINUITY_QA"; AUDIO_QA="AUDIO_QA"; TECHNICAL_QA="TECHNICAL_QA"

class Verdict(str, Enum):
    PENDING="PENDING"; PASS="PASS"; FAIL="FAIL"; BLOCKED="BLOCKED"

@dataclass(frozen=True)
class Review:
    gate: Gate
    asset_version: str
    verdict: Verdict

@dataclass
class Shot:
    shot_id: str
    asset_version: str
    has_dialogue_or_audio: bool = False
    reviews: Dict[Gate, Review] = field(default_factory=dict)
    canonical: bool = False
    upscale_allowed: bool = False

class ProductionPolicyError(ValueError): pass

REQUIRED_BASE: FrozenSet[Gate] = frozenset({Gate.VISUAL_QA, Gate.CONTINUITY_QA, Gate.TECHNICAL_QA})

def required_gates(shot: Shot) -> FrozenSet[Gate]:
    return REQUIRED_BASE | ({Gate.AUDIO_QA} if shot.has_dialogue_or_audio else set())

def validate_review_binding(shot: Shot) -> None:
    for gate, review in shot.reviews.items():
        if review.gate != gate:
            raise ProductionPolicyError("review gate/key mismatch")
        if review.asset_version != shot.asset_version:
            raise ProductionPolicyError("stale review evidence")

def semantic_gates_pass(shot: Shot) -> bool:
    validate_review_binding(shot)
    return all(shot.reviews.get(g) and shot.reviews[g].verdict is Verdict.PASS
               for g in (Gate.VISUAL_QA, Gate.CONTINUITY_QA))

def all_required_gates_pass(shot: Shot) -> bool:
    validate_review_binding(shot)
    return all(shot.reviews.get(g) and shot.reviews[g].verdict is Verdict.PASS
               for g in required_gates(shot))

def authorize_upscale(shot: Shot) -> None:
    if not semantic_gates_pass(shot):
        raise ProductionPolicyError("upscale blocked until visual and continuity QA pass")
    shot.upscale_allowed = True

def canonicalize(shot: Shot) -> None:
    if not all_required_gates_pass(shot):
        raise ProductionPolicyError("canonicalization blocked until every applicable QA gate passes")
    shot.canonical = True

@dataclass(frozen=True)
class ProviderQuote:
    provider: str
    estimated_cost_usd: float
    available: bool = True

def choose_zero_cost_provider(quotes: Iterable[ProviderQuote]) -> ProviderQuote:
    eligible=[q for q in quotes if q.available and q.estimated_cost_usd == 0]
    if not eligible:
        raise ProductionPolicyError("BLOCKED_ZERO_COST_PROVIDER_UNAVAILABLE")
    return sorted(eligible, key=lambda q:q.provider)[0]

def episode_can_complete(*, all_shots_canonical: bool, final_qc_passed: bool,
                         drive_master_verified: bool) -> bool:
    return all_shots_canonical and final_qc_passed and drive_master_verified
