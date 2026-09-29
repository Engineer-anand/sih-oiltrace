"""
Explainability layer — Brain 6.

Maps each vessel's raw factor scores (Brain 4 output) into a human-readable
evidence checklist: "every candidate has a WHY". Purely a presentation layer
over already-computed, already-auditable numbers — no new scoring logic
lives here.
"""

from __future__ import annotations

from app.schemas.evidence import EvidenceItem, VesselEvidence
from app.schemas.scoring import VesselScore

# Raw-score thresholds (0-100) for calling a factor a clear positive,
# a clear negative, or just informational.
POSITIVE_THRESHOLD = 65.0
NEGATIVE_THRESHOLD = 25.0

_FACTOR_TEMPLATES = {
    "proximity": {
        "positive": "Vessel position recorded within a few km of the estimated spill origin.",
        "negative": "Vessel stayed well outside the origin search radius.",
        "info": "Vessel proximity to the origin was moderate.",
    },
    "timing": {
        "positive": "Vessel was present near the origin during the estimated release time window.",
        "negative": "Vessel's recorded presence falls outside the release time window.",
        "info": "Vessel's presence partially overlaps the release time window.",
    },
    "trajectory": {
        "positive": "Vessel heading is consistent with having just departed the origin point.",
        "negative": "Vessel heading points back toward the origin (consistent with passing through, not departing).",
        "info": "Vessel heading is not strongly indicative either way.",
    },
    "drift_overlap": {
        "positive": "Vessel track intersects the backward-drift particle ensemble closely.",
        "negative": "Vessel track does not overlap the drift ensemble.",
        "info": "Vessel track passes near, but not through, the drift ensemble.",
    },
    "behavior": {
        "positive": "Vessel showed sustained low speed (loitering), consistent with a discharge event.",
        "negative": "Vessel maintained normal transit speed throughout.",
        "info": "Vessel showed a moderate, inconclusive speed profile.",
    },
    "vessel_type": {
        "positive": "Vessel type carries elevated spill risk (tanker / high-risk classification).",
        "negative": "Vessel type carries low inherent spill risk.",
        "info": "Vessel type carries moderate inherent spill risk.",
    },
}


def _classify(raw_score: float) -> str:
    if raw_score >= POSITIVE_THRESHOLD:
        return "positive"
    if raw_score <= NEGATIVE_THRESHOLD:
        return "negative"
    return "info"


def build_evidence(vessel_score: VesselScore) -> VesselEvidence:
    checklist: list[EvidenceItem] = []
    for factor in vessel_score.factors:
        ref = f"{vessel_score.vessel_id}:{factor.factor}"
        # M9: unavailable factors get honest info entries, never scored claims.
        if getattr(factor, "status", "COMPUTED") == "UNAVAILABLE":
            note = getattr(factor, "note", None) or "no evidence available from the provider"
            checklist.append(EvidenceItem(
                factor=factor.factor,
                description=f"{factor.factor}: unavailable — {note}. Excluded from the total score.",
                log_type="info", ref=ref,
            ))
            continue
        log_type = _classify(factor.raw_score)
        template = _FACTOR_TEMPLATES.get(factor.factor, {})
        description = template.get(log_type, f"{factor.factor}: raw score {factor.raw_score:.0f}/100.")
        suffix = f" (raw score: {factor.raw_score:.0f}/100, weight: {factor.weight:.0%})"
        if getattr(factor, "status", "COMPUTED") == "DEGRADED":
            suffix += f" [degraded: {getattr(factor, 'note', '') or 'approximate'}]"
        checklist.append(EvidenceItem(
            factor=factor.factor,
            description=f"{description}{suffix}",
            log_type=log_type, ref=ref,
        ))
    return VesselEvidence(
        vessel_id=vessel_score.vessel_id, mmsi=vessel_score.mmsi, name=vessel_score.name,
        total_score=vessel_score.total_score, checklist=checklist,
    )


def build_evidence_for_all(vessel_scores: list[VesselScore]) -> list[VesselEvidence]:
    return [build_evidence(vs) for vs in vessel_scores]
