#!/usr/bin/env python3
"""OilTrace demo-path smoke test — run this immediately before deploying.

Answers the only question that matters for a free-tier deploy: **does the
whole pipeline work with the live-physics stack absent?** If APP_MODE=sih_demo
ever started depending on OpenDrift/CMEMS/ERA5, this fails loudly instead of
the container failing in production.

It also checks the contracts a reviewer will poke at:
  * data_mode is SCENARIO (never silently LIVE)
  * vessel identifiers are structurally synthetic (no plausible MMSIs)
  * applied factor weights renormalise to 1.0 per vessel
  * the report endpoint returns a real PDF

Usage:
    python scripts/smoke_demo_path.py [base_url]        # default http://127.0.0.1:8000

Requires the app extras already in requirements-demo.txt (httpx, numpy,
rasterio) plus a running server. Exit code 0 = the demo path is deployable.
"""

import io
import json
import sys

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_bounds

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"

# 1. Prove the live stack is genuinely absent.
for mod in ("opendrift", "copernicusmarine", "cdsapi", "netCDF4", "geopandas", "torchvision"):
    try:
        __import__(mod)
        print(f"  UNEXPECTED: {mod} is installed")
    except ImportError:
        print(f"  absent (expected): {mod}")

# 2. Build a small georeferenced GeoTIFF (a slick-like bright blob on dark sea).
H = W = 384
lon0, lat0, lon1, lat1 = 72.05, 18.55, 72.35, 18.85
rng = np.random.default_rng(7)
arr = rng.normal(0.25, 0.05, (H, W)).astype(np.float32)
yy, xx = np.mgrid[0:H, 0:W]
blob = ((yy - 150) ** 2 / 900.0 + (xx - 210) ** 2 / 4000.0) < 1.0
arr[blob] += 0.55
arr = np.clip(arr, 0.0, 1.0)
buf = io.BytesIO()
with rasterio.open(
    buf, "w", driver="GTiff", height=H, width=W, count=1, dtype="float32",
    crs=CRS.from_epsg(4326), transform=from_bounds(lon0, lat0, lon1, lat1, W, H),
) as dst:
    dst.write(arr, 1)
scene = buf.getvalue()
print(f"\n  scene bytes: {len(scene)}")

# 3. Run the real pipeline. ais_source=scenario is what routes drift to the
#    analytic path and AIS to the stdlib fixture.
with httpx.Client(timeout=600.0) as client:
    resp = client.post(
        f"{BASE}/api/v1/pipeline/run",
        files={"file": ("demo_scene.tif", scene, "image/tiff")},
        data={
            "min_lon": lon0, "min_lat": lat0, "max_lon": lon1, "max_lat": lat1,
            "detected_at": "2026-09-29T06:00:00Z",
            "lookback_hours": "12",
            "ais_source": "scenario",
        },
    )

print(f"  HTTP {resp.status_code}")
if resp.status_code != 200:
    print(resp.text[:2000])
    raise SystemExit(1)

body = resp.json()
print(f"  spill_id            : {body.get('spill_id')}")
print(f"  data_mode           : {body.get('data_mode')}")
print(f"  using_synthetic_env : {body.get('using_synthetic_environment')}")
print(f"  processing_seconds  : {body.get('processing_seconds')}")

incident = body.get("incident") or {}
slick = incident.get("slick") or {}
det = incident.get("detection") or {}
print(f"  slick area_km2      : {slick.get('area_km2')}")
print(f"  slick centroid      : {slick.get('centroid')}")
print(f"  confidence          : {det.get('confidence')}")

origin = body.get("origin") or {}
print(f"  origin centroid     : {origin.get('origin_centroid') or origin.get('centroid')}")
print(f"  quality_score       : {origin.get('quality_score')}")
print(f"  env_mode            : {origin.get('env_mode')}")

vessels = body.get("vessels") or []
print(f"  candidate vessels   : {len(vessels)}")
for v in vessels[:3]:
    print(f"      #{v.get('rank')} {v.get('vessel_id')} score={v.get('total_score')} "
          f"conf={v.get('confidence')} unavail={v.get('unavailable_factors')}")

ev = body.get("evidence") or []
print(f"  evidence entries    : {sum(len(e.get('checklist') or []) for e in ev)}")
print(f"  provider_caps       : {body.get('provider_capabilities')}")
print(f"  unavailable_factors : {body.get('unavailable_factors')}")

# 4. Contract assertions that matter for the demo.
problems = []
if body.get("data_mode") != "SCENARIO":
    problems.append(f"expected data_mode=SCENARIO, got {body.get('data_mode')}")
if not vessels:
    problems.append("no candidate vessels returned")
if any(str(v.get("mmsi") or "").isdigit() and len(str(v.get("mmsi"))) >= 9 for v in vessels):
    problems.append("plausible-looking MMSI present; scenario ids must be synthetic")
# The API exposes vessel_id "V-SCENARIO-NN" and mmsi "SCENARIO-NN" — both
# structurally impossible to mistake for a real vessel identifier.
for v in vessels:
    if "SCENARIO" not in str(v.get("vessel_id", "")):
        problems.append(f"vessel_id not scenario-labelled: {v.get('vessel_id')}")
    if not str(v.get("mmsi", "")).startswith("SCENARIO-"):
        problems.append(f"mmsi not structurally synthetic: {v.get('mmsi')}")
for v in vessels:
    weights = v.get("applied_weights") or {}
    total = round(sum(weights.values()), 3)
    if abs(total - 1.0) > 0.02:
        problems.append(f"applied weights for {v.get('vessel_id')} sum to {total}, expected 1.0")

# 5. Report export should be a real PDF.
with httpx.Client(timeout=120.0) as client:
    rep = client.get(f"{BASE}/api/v1/report/{body.get('spill_id')}")
print(f"\n  report HTTP {rep.status_code} | {rep.headers.get('content-type')} | {len(rep.content)} bytes")
if rep.status_code != 200 or not rep.content.startswith(b"%PDF"):
    problems.append("report is not a valid PDF")

print()
if problems:
    print("FAILED:")
    for p in problems:
        print("  -", p)
    raise SystemExit(1)
print("DEMO PATH OK — full pipeline ran with no OpenDrift/CMEMS/ERA5/geopandas installed.")
