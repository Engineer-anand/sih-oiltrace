"""M10 attribution tests — neutral terminology lint, confidence, evidence refs."""
import os
import re
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("APP_MODE", "development")

REPO = os.path.join(os.path.dirname(__file__), "..", "..")

# Whole-word banned terms (case-insensitive) + banned phrase. "responsible"
# is intentionally absent: the non-liability disclaimer is required language.
BANNED_WORDS = ["culprit", "polluter", "guilty", "suspect", "suspects", "forensic", "liability"]
BANNED_PHRASES = ["prime suspect", "PrimeSuspect"]

SCAN_DIRS = ["backend/app", "frontend/src"]
SKIP_FILES = {"test_attribution.py"}


def _iter_files():
    for rel in SCAN_DIRS:
        root = os.path.join(REPO, rel)
        for dirpath, _, filenames in os.walk(root):
            if "__pycache__" in dirpath:
                continue
            for fn in filenames:
                if not fn.endswith((".py", ".jsx", ".js", ".css")):
                    continue
                if fn in SKIP_FILES:
                    continue
                yield os.path.join(dirpath, fn)


def test_no_banned_attribution_terms():
    patterns = [(w, re.compile(r"\b" + re.escape(w) + r"\b", re.IGNORECASE)) for w in BANNED_WORDS]
    phrases = [(p, re.compile(re.escape(p))) for p in BANNED_PHRASES]
    hits = []
    for path in _iter_files():
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        for label, rx in patterns + phrases:
            for m in rx.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                hits.append(f"{os.path.relpath(path, REPO)}:{line}: {label}")
    assert hits == [], f"banned attribution terms found:\n" + "\n".join(hits)


def test_confidence_and_data_quality():
    from app.contracts import grade_attribution

    assert grade_attribution(0, 0) == ("high", "high")
    assert grade_attribution(2, 0) == ("medium", "limited")
    assert grade_attribution(1, 1) == ("medium", "reduced")
    assert grade_attribution(3, 0) == ("low", "limited")

    from app.schemas.ais import VesselInfo, VesselTrackPoint
    from app.services import scoring_service as sc

    t0 = datetime(2026, 9, 1, tzinfo=timezone.utc)
    v = VesselInfo(vessel_id="V-1", mmsi="1", name="X", vessel_type="cargo", flag="PA",
                   length_m=100.0, track=[VesselTrackPoint(
                       time=t0 + timedelta(hours=12), lon=72.31, lat=18.72,
                       speed_kn=None, heading_deg=None)])
    (scored,) = sc.score_vessels([v], 72.3, 18.7, t0, t0 + timedelta(hours=24),
                                 [(72.3, 18.7)], capabilities=["AIS_PRESENCE"], trajectories=[])
    assert scored.confidence in ("high", "medium", "low")
    assert scored.data_quality in ("high", "reduced", "limited")
    # presence-only: 2 unavailable -> medium/limited (drift static -> degraded too)
    assert scored.confidence == "medium"
    assert scored.data_quality in ("limited", "reduced")


def test_evidence_refs_link_to_factors():
    from app.schemas.ais import VesselInfo, VesselTrackPoint
    from app.services import scoring_service as sc
    from app.services.evidence_service import build_evidence

    t0 = datetime(2026, 9, 1, tzinfo=timezone.utc)
    v = VesselInfo(vessel_id="V-9", mmsi="9", name="Y", vessel_type="tanker", flag="PA",
                   length_m=150.0, track=[VesselTrackPoint(
                       time=t0 + timedelta(hours=12), lon=72.31, lat=18.72,
                       speed_kn=None, heading_deg=None)])
    (scored,) = sc.score_vessels([v], 72.3, 18.7, t0, t0 + timedelta(hours=24),
                                 [(72.3, 18.7)], capabilities=["AIS_PRESENCE"], trajectories=[])
    ev = build_evidence(scored)
    factors = {f.factor for f in scored.factors}
    assert {i.factor for i in ev.checklist} == factors
    for item in ev.checklist:
        assert item.ref == f"V-9:{item.factor}"


def test_report_carries_non_liability_statement():
    try:
        from app.services import report_service as rs
    except ImportError:
        print("SKIP pdf statement (reportlab unavailable)")
        return
    import inspect

    src = inspect.getsource(rs.generate_incident_report_pdf)
    assert "do not establish responsibility" in src
    assert "Top Candidate Vessels" in src
    assert "Top Suspect" not in src
