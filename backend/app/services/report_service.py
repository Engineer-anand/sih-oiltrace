"""
PDF report generation — final dashboard export.

Builds a single-page-per-section incident report (detection summary, drift
origin, top candidate vessels, evidence checklist) using reportlab, which has
no system dependencies beyond the pure-Python package itself.
"""

from __future__ import annotations

import io
from datetime import datetime, timezone

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.schemas.evidence import VesselEvidence
from app.schemas.incident import IncidentResponse
from app.schemas.origin import OriginResponse
from app.schemas.scoring import VesselScore



def _display_mmsi(val: str | None) -> str:
    """M10: render identifiers honestly — never synthesize plausible MMSIs.

    Synthetic SCENARIO-/FALLBACK- identifiers are shown as-is; non-numeric
    ids render as-is; empty renders as an em dash.
    """
    if not val:
        return "—"
    return str(val).strip() or "—"


def generate_incident_report_pdf(
    incident: IncidentResponse,
    origin: OriginResponse | None,
    vessel_scores: list[VesselScore],
    evidence: list[VesselEvidence],
    data_mode: str | None = None,
) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=1.5 * cm, bottomMargin=1.5 * cm)
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph("OilTrace — Oil Spill Detection & Attribution Report", styles["Title"]))
    story.append(Paragraph(f"Generated: {datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')}", styles["Normal"]))
    if data_mode:
        # Release audit: the PDF must carry the server-derived data mode
        # (documented in docs/SIH_DEMO.md), never implying all-live evidence.
        story.append(Paragraph(f"Data mode: {data_mode}", styles["Normal"]))
    story.append(Spacer(1, 0.5 * cm))

    story.append(Paragraph(f"Incident ID: {incident.incident_id}", styles["Heading2"]))
    story.append(Paragraph(f"Spill detected: {incident.spill_detected}", styles["Normal"]))
    story.append(Paragraph(
        f"Satellite: {incident.detection.satellite} | Confidence: {incident.detection.confidence:.2f} | "
        f"Model: {'trained U-Net' if not incident.detection.using_dummy_model else 'standard threshold detector'}",
        styles["Normal"],
    ))
    if incident.slick:
        story.append(Paragraph(
            f"Slick area: {incident.slick.area_km2} km² | Centroid: {incident.slick.centroid}",
            styles["Normal"],
        ))
    story.append(Spacer(1, 0.4 * cm))

    if origin:
        story.append(Paragraph("Backward Drift — Probable Origin", styles["Heading2"]))
        story.append(Paragraph(
            f"Origin centroid: {origin.origin_centroid} | Uncertainty radius: "
            f"{origin.uncertainty_radius_km} km",
            styles["Normal"],
        ))
        story.append(Paragraph(
            f"Estimated release window: {origin.release_time_window_start} to "
            f"{origin.release_time_window_end} (method: {origin.release_window_method or 'conservative-full-lookback'})",
            styles["Normal"],
        ))
        if origin.quality_score is not None:
            story.append(Paragraph(
                f"Drift quality score: {origin.quality_score}/100 "
                f"(confidence: {origin.confidence or 'unknown'}; seed {origin.seed})",
                styles["Normal"],
            ))
        for warn in (origin.warnings or []):
            story.append(Paragraph(f"Warning: {warn}", styles["Normal"]))
        if origin.using_synthetic_environment:
            story.append(Paragraph(
                "⚠ LIVE CMEMS/ERA5 unavailable — runtime synthetic ocean/wind fallback was used for this simulation.",
                styles["Normal"],
            ))
        story.append(Spacer(1, 0.4 * cm))

    if vessel_scores:
        story.append(Paragraph("Top Candidate Vessels", styles["Heading2"]))
        table_data = [["Rank", "MMSI", "Name", "Type", "Score"]]
        for vs in vessel_scores[:10]:
            clean_m = _display_mmsi(vs.mmsi)
            clean_name = vs.name or "—"
            table_data.append([str(vs.rank), clean_m, clean_name, vs.vessel_type or "-",
                                f"{vs.total_score:.1f}"])
        table = Table(table_data, colWidths=[1.5 * cm, 3 * cm, 4 * cm, 4 * cm, 2.5 * cm])
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2d3d")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
        ]))
        story.append(table)
        story.append(Paragraph(
            "Scores express statistical association between vessels and the estimated origin; "
            "they are investigative evidence and do not establish responsibility.",
            styles["Normal"],
        ))
        story.append(Spacer(1, 0.4 * cm))

    if evidence:
        story.append(Paragraph("Evidence Checklist", styles["Heading2"]))
        for ve in evidence[:10]:
            v_title = ve.name or _display_mmsi(ve.mmsi)
            story.append(Paragraph(f"<b>{v_title}</b> — total score {ve.total_score:.1f}",
                                    styles["Heading4"]))
            for item in ve.checklist:
                bullet = {"positive": "✓", "negative": "✗", "info": "•"}.get(item.log_type, "•")
                story.append(Paragraph(f"{bullet} {item.description}", styles["Normal"]))
            story.append(Spacer(1, 0.2 * cm))

    doc.build(story)
    return buffer.getvalue()
