"""
Report generation API router.

GET /api/v1/report/{spill_id}   Generate and download a PDF incident report
                                  from already-saved pipeline results.
"""

from __future__ import annotations

import io

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.pipeline import get_spill_detail
from app.db import get_db
from app.security import validate_incident_id
from app.schemas.evidence import EvidenceItem, VesselEvidence
from app.schemas.incident import DetectionInfo, IncidentResponse, SlickInfo
from app.schemas.origin import OriginResponse
from app.schemas.scoring import FactorScore, VesselScore
from app.contracts import grade_attribution, scoring_weights
from app.services.report_service import generate_incident_report_pdf

router = APIRouter(prefix="/api/v1", tags=["reports"])


@router.get("/report/{spill_id}")
def get_report(spill_id: str, db: Session = Depends(get_db)):
    validate_incident_id(spill_id)
    try:
        detail = get_spill_detail(spill_id, db)
    except HTTPException:
        raise
    if not detail or not detail.get("spill"):
        raise HTTPException(status_code=404, detail={"type": "not-found", "title": "Not found", "detail": "Spill not found."})
    spill = detail["spill"]
    drift = detail["drift"]

    incident = IncidentResponse(
        incident_id=spill_id, spill_detected=True, detected_at=spill["detected_at"],
        detection=DetectionInfo(
            satellite=spill["satellite"], source_filename=spill.get("source_filename") or "saved-record",
            confidence=spill["confidence"], using_dummy_model=False,
        ),
        slick=SlickInfo(
            area_km2=spill["area_km2"], centroid=tuple(spill["centroid"]),
            bbox=(spill.get("bbox_min_lon"), spill.get("bbox_min_lat"), spill.get("bbox_max_lon"), spill.get("bbox_max_lat"))
            if None not in (spill.get("bbox_min_lon"), spill.get("bbox_min_lat"), spill.get("bbox_max_lon"), spill.get("bbox_max_lat"))
            else None,
            geometry=spill["geometry"],
        ) if spill.get("geometry") else None,
    )

    origin = None
    if drift:
        origin = OriginResponse(
            incident_id=spill_id, origin_centroid=tuple(drift["origin_centroid"]),
            origin=list(drift["origin_centroid"]),
            uncertainty_radius_km=drift["uncertainty_radius_km"],
            release_time_window_start=drift["release_time_window"][0],
            release_time_window_end=drift["release_time_window"][1],
            release_window=drift.get("release_window"),
            particle_count=drift["particle_count"], final_particle_positions=[],
            using_synthetic_environment=drift["using_synthetic_environment"],
            seed=drift.get("seed"),
            origin_probability=drift.get("origin_probability"),
            uncertainty_ellipse=drift.get("uncertainty_ellipse"),
            quality_score=drift.get("quality_score"),
            warnings=list(drift.get("warnings") or []),
            env_mode=drift.get("env_mode"),
        )

    vessel_scores = []
    evidence = []
    weights = scoring_weights()
    for v in detail["vessels"]:
        # M9: report from the stored applied weights (renormalized at scoring
        # time), falling back to current settings only for legacy rows.
        w = dict(v.get("applied_weights") or {})
        if not w or abs(sum(w.values()) - 1.0) > 1e-6:
            w = weights
        unavailable = set(v.get("unavailable") or [])

        degraded = set(v.get("degraded") or [])

        def _fs(name):
            if name in unavailable:
                status = "UNAVAILABLE"
            elif name in degraded:
                status = "DEGRADED"
            else:
                status = "COMPUTED"
            wt = w.get(name, 0.0)
            raw = v["factors"][name]
            return FactorScore(factor=name, weight=wt, raw_score=raw,
                               weighted_score=round(wt * raw, 2) if status != "UNAVAILABLE" else 0.0,
                               status=status)

        factors = [_fs("proximity"), _fs("timing"), _fs("trajectory"),
                   _fs("drift_overlap"), _fs("behavior"), _fs("vessel_type")]
        confidence, data_quality = grade_attribution(
            sum(1 for f in factors if f.status == "UNAVAILABLE"),
            sum(1 for f in factors if f.status == "DEGRADED"))
        vessel_scores.append(VesselScore(
            vessel_id=v["vessel_id"], mmsi=v["mmsi"], name=v["name"], vessel_type=v["vessel_type"],
            total_score=v["total_score"], rank=v["rank"],
            factors=factors,
            unavailable_factors=sorted(unavailable),
            applied_weights={k: round(float(x), 4) for k, x in w.items()},
            confidence=confidence, data_quality=data_quality,
        ))
        evidence.append(VesselEvidence(
            vessel_id=v["vessel_id"], mmsi=v["mmsi"], name=v["name"], total_score=v["total_score"],
            checklist=[EvidenceItem(factor=e["factor"], description=e["description"], log_type=e["log_type"],
                                    ref=e.get("ref") or f"{v['vessel_id']}:{e['factor']}")
                       for e in v["evidence"]],
        ))

    pdf_bytes = generate_incident_report_pdf(
        incident, origin, vessel_scores, evidence,
        data_mode=spill.get("data_mode"),
    )
    # M14: persist the rendered report; serving continues from memory regardless.
    try:
        from app.services.storage import record_artifact

        record_artifact(db, spill_id, "report-pdf", f"{spill_id}_report.pdf", pdf_bytes)
    except Exception:
        pass
    return StreamingResponse(
        io.BytesIO(pdf_bytes), media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={spill_id}_report.pdf"},
    )
