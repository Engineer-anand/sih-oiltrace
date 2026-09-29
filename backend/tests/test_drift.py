"""M6 drift/origin + geospatial tests — numpy/pyproj/shapely only, no opendrift."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("APP_MODE", "development")

from app.services.drift import (  # noqa: E402
    _quality_gate,
    cluster_origin,
    run_scenario_backward_drift,
)


def test_scenario_deterministic():
    from datetime import datetime, timezone

    t = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    a = run_scenario_backward_drift((72.3, 18.7), t, 24)
    b = run_scenario_backward_drift((72.3, 18.7), t, 24)
    assert a.origin_centroid == b.origin_centroid
    assert a.final_particle_positions == b.final_particle_positions
    assert a.seed == 20260905
    assert a.env_mode == "SCENARIO"
    assert a.warnings and a.quality_score is not None


def test_scenario_seed_changes_output():
    from datetime import datetime, timezone

    t = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    a = run_scenario_backward_drift((72.3, 18.7), t, 24, seed=1)
    b = run_scenario_backward_drift((72.3, 18.7), t, 24, seed=2)
    assert a.final_particle_positions != b.final_particle_positions
    assert a.seed == 1 and b.seed == 2


def test_cluster_origin_picks_larger_blob():
    rng = np.random.default_rng(7)
    big = np.column_stack([72.3 + rng.normal(0, 0.005, 60), 18.7 + rng.normal(0, 0.005, 60)])
    small = np.column_stack([73.5 + rng.normal(0, 0.005, 10), 19.5 + rng.normal(0, 0.005, 10)])
    lons = np.concatenate([big[:, 0], small[:, 0]])
    lats = np.concatenate([big[:, 1], small[:, 1]])
    origin, prob, ellipse = cluster_origin(lons, lats, 72.3, 18.7)
    assert abs(origin[0] - 72.3) < 0.05 and abs(origin[1] - 18.7) < 0.05
    assert 0.8 <= prob <= 0.9
    assert ellipse is not None
    assert ellipse["semi_major_km"] >= ellipse["semi_minor_km"] >= 0


def test_cluster_origin_fallback_without_cluster():
    origin, prob, ellipse = cluster_origin([72.3, 80.0], [18.7, 25.0], 72.3, 18.7)
    assert prob == 0.0 and ellipse is None
    assert origin == (72.3, 18.7)


def test_quality_gate_bands():
    w: list[str] = []
    assert _quality_gate(1.0, True, "LIVE", 0.9, w) == 100.0
    w2: list[str] = []
    low = _quality_gate(0.2, True, "FALLBACK", 0.3, w2)
    assert low < 60.0
    assert any("survival" in x or "FALLBACK" in x or "clustering" in x for x in w2)
    w3: list[str] = []
    mid = _quality_gate(1.0, True, "ARCHIVE", 0.9, w3)
    assert 60.0 <= mid < 100.0


def test_geodesic_centroid_and_validity():
    from shapely.geometry import Polygon

    from app.services import geospatial as gs

    poly = Polygon([(72.0, 18.0), (73.0, 18.0), (73.0, 19.0), (72.0, 19.0)])
    lon, lat = gs.geodesic_centroid(poly)
    assert 72.0 < lon < 73.0 and 18.0 < lat < 19.0
    # self-intersecting bowtie must be repaired, not crash
    mask = np.zeros((32, 32), dtype=np.uint8)
    mask[8:24, 8:24] = 1
    from affine import Affine
    from rasterio.crs import CRS

    polys = gs.mask_to_polygons(mask, Affine.translation(72, 19) * Affine.scale(0.01, -0.01), CRS.from_epsg(4326))
    assert polys and all(p.is_valid for p in polys)
    geom = gs.compute_geometry(gs.reproject_polygons_to_wgs84(polys, CRS.from_epsg(4326)))
    assert geom is not None and geom.area_km2 > 0


def test_ensure_columns_adds_drift_fields():
    import tempfile

    from sqlalchemy import create_engine, text

    from app.db import ensure_columns

    tmp = os.path.join(tempfile.mkdtemp(prefix="m6mig_"), "old.db")
    eng = create_engine(f"sqlite:///{tmp}", connect_args={"check_same_thread": False})
    with eng.begin() as conn:
        conn.execute(text(
            "CREATE TABLE drift_runs (id VARCHAR PRIMARY KEY, spill_id VARCHAR, "
            "start_time DATETIME, end_time DATETIME, origin_geom_json TEXT, "
            "origin_lon FLOAT, origin_lat FLOAT, uncertainty_radius_km FLOAT, "
            "particle_count INTEGER, ensemble_path_json TEXT, "
            "using_synthetic_environment BOOLEAN, created_at DATETIME)"
        ))
        conn.execute(text("CREATE TABLE spills (id VARCHAR PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE vessel_tracks (id INTEGER PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE jobs (id VARCHAR PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE job_stages (id INTEGER PRIMARY KEY)"))
    ensure_columns(eng)
    with eng.connect() as conn:
        cols = {r[1] for r in conn.execute(text("PRAGMA table_info(drift_runs)"))}
    for col in ("seed", "quality_score", "origin_probability", "uncertainty_ellipse_json", "env_mode", "warnings_json"):
        assert col in cols, f"missing {col}"
    with eng.connect() as conn:
        spills_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(spills)"))}
    assert "data_mode" in spills_cols
    ensure_columns(eng)  # idempotent re-run
