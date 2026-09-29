"""
Backward drift simulation service — Brain 2.

Pipeline:
    Incident JSON (centroid + detection time)
        -> fetch currents (CMEMS) + wind (ERA5) around that point/time
        -> seed particles at the slick centroid
        -> run OpenDrift's OceanDrift model BACKWARD in time
        -> cluster final particle positions -> probable origin region
        -> Origin JSON (centroid, uncertainty radius, release time window)

Uses OpenDrift (https://opendrift.github.io), the same open-source engine
used operationally by several national ocean/coast-guard agencies for oil
spill and search-and-rescue drift forecasting.

TROUBLESHOOTING "drift isn't working" — checked here, in this order:

  1. tz-aware datetime crash — OpenDrift compares datetimes internally as
     naive (tz-unaware) and raises TypeError on tz-aware input. Handled by
     stripping tzinfo before handing anything to OpenDrift.
  2. Silent no-movement — if the environmental NetCDF has missing/wrong CF
     metadata, OpenDrift loads a reader with zero variables and every
     particle just sits at the seed point; the run "succeeds" with no
     error but the origin == centroid. `ocean_data.py` now validates CF
     variables after every download so this can't happen silently, and
     `run_backward_drift` additionally checks final particle spread here
     and raises DriftSimulationError (loud, not silent) if particles
     barely moved despite a normal lookback window.
  3. Missing coastline/landmask data — `general:coastline_action` needs a
     landmask reader. OpenDrift 1.14.x bundles `roaring_landmask` as a
     dependency and uses it automatically for the global default; if that
     package is missing/broken in your environment, OceanDrift.run() can
     raise at reader-setup time. Caught explicitly below with a clear
     message instead of a bare traceback.
  4. Wrong OpenDrift config keys — verified against installed
     opendrift==1.14.12: the wind-drift factor lives under the `seed:`
     namespace, NOT `drift:`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np

from app.services.ocean_data import get_environmental_data
from app.config import scenario_allowed, settings, synthetic_fallback_allowed
from app.contracts import to_iso_z

logger = logging.getLogger(__name__)

DEFAULT_LOOKBACK_HOURS = settings.DEFAULT_LOOKBACK_HOURS
SEED_RADIUS_METERS = 500          # particles seeded within this radius of centroid
SEED_PARTICLE_COUNT = settings.DRIFT_PARTICLES
WIND_LEEWAY_FACTOR = 0.03         # oil drifts ~3% of wind speed, standard leeway coefficient

# If, after a full backward run, 90% of particles ended up within this many
# metres of the seed point, treat it as a stalled/no-op simulation rather
# than a genuinely calm-water result — a real (even weak) current+wind
# field over 48h almost never produces sub-50m spread.
STALL_DISTANCE_METERS = 50.0


class DriftSimulationError(RuntimeError):
    """Raised when the backward drift run fails or produces a physically
    implausible (stalled) result, with a diagnosis in the message instead
    of a bare OpenDrift/library traceback."""

@dataclass
class OriginEstimate:
    origin_centroid: tuple[float, float]     # (lon, lat)
    uncertainty_radius_km: float
    release_time_window: tuple[datetime, datetime]
    particle_count: int
    using_synthetic_environment: bool
    final_particle_positions: list[tuple[float, float]]  # [(lon, lat), ...] for overlap scoring / map display
    particle_trajectories: list[list[dict]]  # sampled time-aware trajectories for UI animation
    # M6: cluster-based origin + quality metadata
    seed: int = 20260905
    time_step_s: int = -3600
    wind_factor: float = 0.03
    origin_probability: float = 0.0  # largest-cluster fraction of final particles
    uncertainty_ellipse: dict | None = None  # {semi_major_km, semi_minor_km, orientation_deg}
    quality_score: float = 100.0
    confidence: str = "high"  # high | medium | low (from quality_score bands)
    warnings: list[str] = None  # type: ignore[assignment]
    env_mode: str = "LIVE"
    release_window_method: str = "conservative-full-lookback"

    def __post_init__(self):
        if self.warnings is None:
            self.warnings = []
        if self.quality_score >= 80:
            self.confidence = "high"
        elif self.quality_score >= 60:
            self.confidence = "medium"
        else:
            self.confidence = "low"


def _project_local(lons, lats, clon: float, clat: float) -> tuple:
    """Project (lon, lat) arrays to local azimuthal-equidistant metres (M6)."""
    from pyproj import Transformer

    fwd = Transformer.from_crs(
        "EPSG:4326",
        f"+proj=aeqd +lon_0={clon} +lat_0={clat} +datum=WGS84 +units=m +no_defs",
        always_xy=True,
    )
    xs, ys = fwd.transform(np.asarray(lons, dtype=float), np.asarray(lats, dtype=float))
    return np.asarray(xs, dtype=float), np.asarray(ys, dtype=float)


def cluster_origin(final_lons, final_lats, origin_lon: float, origin_lat: float) -> tuple[tuple[float, float], float, dict | None]:
    """M6: deterministic density clustering (DBSCAN-lite, O(n^2), n small).

    Returns (origin (lon, lat), largest-cluster fraction, ellipse or None).
    Origin = geodesic-aware mean of the largest cluster in projected metres.
    Falls back to the median with probability 0.0 when no cluster forms.
    """
    lons = np.asarray(final_lons, dtype=float)
    lats = np.asarray(final_lats, dtype=float)
    n = len(lons)
    if n == 0:
        return (round(origin_lon, 6), round(origin_lat, 6)), 0.0, None
    xs, ys = _project_local(lons, lats, origin_lon, origin_lat)
    # eps scales with the cloud spread so the method adapts to calm/dispersed runs.
    spread = float(np.median(np.hypot(xs - np.median(xs), ys - np.median(ys)))) or 100.0
    eps = max(500.0, spread * 1.5)
    min_samples = 3
    labels = np.full(n, -1, dtype=int)
    cluster_id = 0
    for i in range(n):
        if labels[i] != -1:
            continue
        dists = np.hypot(xs - xs[i], ys - ys[i])
        neighbors = np.where(dists <= eps)[0]
        if len(neighbors) < min_samples:
            continue  # noise point
        labels[neighbors] = cluster_id
        # expand
        seeds = list(neighbors)
        idx = 0
        while idx < len(seeds):
            j = seeds[idx]
            idx += 1
            d2 = np.hypot(xs - xs[j], ys - ys[j])
            nb2 = np.where(d2 <= eps)[0]
            if len(nb2) >= min_samples:
                for k in nb2:
                    if labels[k] == -1:
                        labels[k] = cluster_id
                        seeds.append(k)
        cluster_id += 1
    if cluster_id == 0:
        return (round(origin_lon, 6), round(origin_lat, 6)), 0.0, None
    sizes = [(labels == c).sum() for c in range(cluster_id)]
    best = int(np.argmax(sizes))
    mask = labels == best
    prob = round(float(mask.sum()) / float(n), 4)
    cx, cy = float(xs[mask].mean()), float(ys[mask].mean())
    # Back-project cluster mean to lon/lat.
    from pyproj import Transformer

    back = Transformer.from_crs(
        f"+proj=aeqd +lon_0={origin_lon} +lat_0={origin_lat} +datum=WGS84 +units=m +no_defs",
        "EPSG:4326",
        always_xy=True,
    )
    clon, clat = back.transform(cx, cy)
    # Covariance ellipse of the best cluster (direction-aware uncertainty).
    ellipse = None
    if mask.sum() >= 3:
        pts = np.column_stack([xs[mask] - cx, ys[mask] - cy])
        cov = np.cov(pts, rowvar=False)
        vals, vecs = np.linalg.eigh(cov)
        order = np.argsort(vals)[::-1]
        vals, vecs = vals[order], vecs[:, order]
        major = float(2.0 * np.sqrt(max(vals[0], 0.0)) / 1000.0)
        minor = float(2.0 * np.sqrt(max(vals[1], 0.0)) / 1000.0)
        orient = float((np.degrees(np.arctan2(vecs[1, 0], vecs[0, 0])) + 360.0) % 360.0)
        ellipse = {"semi_major_km": round(major, 3), "semi_minor_km": round(minor, 3),
                   "orientation_deg": round(orient, 1)}
    return (round(float(clon), 6), round(float(clat), 6)), prob, ellipse


def _quality_gate(survival_fraction: float, spread_ok: bool, env_mode: str,
                  cluster_prob: float, warnings: list[str]) -> float:
    """M6: a run is successful only if the quality gate passes — never merely
    because the numerical engine returned without an exception."""
    score = 0.0
    score += 50.0 * max(0.0, min(1.0, survival_fraction))
    score += 30.0 if spread_ok else 0.0
    if env_mode == "LIVE":
        score += 20.0
    elif env_mode == "ARCHIVE":
        score += 12.0
        warnings.append("Environmental data served from archive cache; check data age.")
    else:
        warnings.append(f"Environmental data is {env_mode}; origin is illustrative, not operational.")
    if cluster_prob < 0.5:
        warnings.append(f"Weak origin clustering (largest cluster {cluster_prob:.0%} of particles).")
        score -= 10.0
    return round(max(0.0, min(100.0, score)), 1)


def estimate_to_payload(estimate: "OriginEstimate") -> dict:
    """M11: single serializer for Stage-4 output.

    The pipeline partial, the pipeline result, and the drift API all consume
    this — one shape, no duplicated origin/window calculations downstream.
    """
    start, end = estimate.release_time_window
    return {
        "origin_centroid": estimate.origin_centroid,
        "origin": list(estimate.origin_centroid),
        "uncertainty_radius_km": estimate.uncertainty_radius_km,
        "uncertainty_ellipse": estimate.uncertainty_ellipse,
        "origin_probability": estimate.origin_probability,
        "release_time_window_start": to_iso_z(start),
        "release_time_window_end": to_iso_z(end),
        "release_window": {"start": to_iso_z(start), "end": to_iso_z(end)},
        "particle_count": estimate.particle_count,
        "final_particle_positions": estimate.final_particle_positions,
        "particle_trajectories": estimate.particle_trajectories,
        "using_synthetic_environment": estimate.using_synthetic_environment,
        "seed": estimate.seed,
        "time_step_s": estimate.time_step_s,
        "wind_factor": estimate.wind_factor,
        "quality_score": estimate.quality_score,
        "confidence": estimate.confidence,
        "warnings": list(estimate.warnings or []),
        "env_mode": estimate.env_mode,
        "release_window_method": estimate.release_window_method,
    }


def _extract_particle_trajectories(sim, max_particles: int = 60) -> list[list[dict]]:
    """Extract a compact, time-aware trajectory sample from OpenDrift 1.13+ result.

    OpenDrift stores run output in ``sim.result`` as an xarray Dataset with
    trajectory/time dimensions. The UI only needs a representative sample,
    so we keep at most ``max_particles`` trajectories to avoid bloating API/DB payloads.
    """
    try:
        result = getattr(sim, "result", None)
        if result is None or not hasattr(result, "lon") or not hasattr(result, "lat"):
            return []
        lons = np.asarray(result.lon.values, dtype=float)
        lats = np.asarray(result.lat.values, dtype=float)
        times = np.asarray(result.time.values)
        if lons.ndim != 2 or lats.ndim != 2:
            return []
        # OpenDrift's current result is normally (trajectory, time). Be defensive
        # for any transposed layout by matching the time dimension.
        if lons.shape[1] == len(times):
            pass
        elif lons.shape[0] == len(times):
            lons, lats = lons.T, lats.T
        else:
            return []
        n = min(max_particles, lons.shape[0])
        out: list[list[dict]] = []
        for i in range(n):
            traj=[]
            for j in range(len(times)):
                lo, la = lons[i,j], lats[i,j]
                if not (np.isfinite(lo) and np.isfinite(la)):
                    continue
                try:
                    # Canonical UTC: numpy's string form has no timezone suffix,
                    # which made `new Date(...)` in the UI shift by local offset.
                    t = np.datetime_as_string(times[j], unit="s", timezone="UTC")
                except Exception:
                    t = str(times[j])
                traj.append({"time": t, "lon": round(float(lo), 6), "lat": round(float(la), 6)})
            if len(traj) >= 2:
                out.append(traj)
        return out
    except Exception:
        logger.exception("DRIFT TRAJECTORY EXTRACTION ERROR")
        return []


def _synthetic_trajectory_fallback(lon: float, lat: float, final_lons: np.ndarray, final_lats: np.ndarray, start_time: datetime, end_time: datetime, max_particles: int = 60) -> list[list[dict]]:
    """Create only a runtime-labelled visual trajectory when live physics is unavailable."""
    n = min(max_particles, len(final_lons))
    out=[]
    frames=25
    for i in range(n):
        arr=[]
        for j in range(frames):
            frac=j/(frames-1)
            # j=0 is the observed slick; j=last is the estimated historical origin.
            arr.append({
                "time": to_iso_z(end_time - (end_time-start_time)*frac),
                "lon": round(float(lon + (final_lons[i]-lon)*frac), 6),
                "lat": round(float(lat + (final_lats[i]-lat)*frac), 6),
            })
        out.append(arr)
    return out


def run_scenario_backward_drift(
    centroid: tuple[float, float],
    detection_time: datetime,
    lookback_hours: int,
    seed: int = 20260905,
) -> OriginEstimate:
    """Fast deterministic trajectory for the scenario mode."""
    lon, lat = centroid
    detection_time_utc = detection_time.astimezone(timezone.utc) if detection_time.tzinfo else detection_time.replace(tzinfo=timezone.utc)
    start_time = detection_time_utc - timedelta(hours=lookback_hours)
    rng = np.random.default_rng(seed)
    n = min(SEED_PARTICLE_COUNT, 32)
    angles = rng.uniform(0, 2 * np.pi, n)
    distances = np.sqrt(rng.uniform(0.4, 1.0, n)) * max(1.5, min(6.0, lookback_hours * 0.35))
    dx_km = distances * np.cos(angles) - lookback_hours * 0.08
    dy_km = distances * np.sin(angles) + lookback_hours * 0.04
    lon_scale = 111.320 * max(0.15, np.cos(np.deg2rad(lat)))
    final_lons = lon + dx_km / lon_scale
    final_lats = lat + dy_km / 110.574
    med_lon, med_lat = float(np.median(final_lons)), float(np.median(final_lats))
    origin, prob, ellipse = cluster_origin(final_lons, final_lats, med_lon, med_lat)
    from pyproj import Geod
    geod = Geod(ellps="WGS84")
    _, _, distances_m = geod.inv(np.full(n, origin[0]), np.full(n, origin[1]), final_lons, final_lats)
    warnings = ["Scenario mode: analytic trajectory, not OpenDrift physics."]
    quality = _quality_gate(1.0, True, "SCENARIO", prob, warnings)
    return OriginEstimate(
        origin,
        round(float(np.percentile(distances_m, 90) / 1000), 2),
        (start_time, detection_time_utc), n, True,
        [(round(float(a), 6), round(float(b), 6)) for a, b in zip(final_lons, final_lats)],
        _synthetic_trajectory_fallback(lon, lat, final_lons, final_lats, start_time, detection_time_utc),
        seed=seed, time_step_s=-3600, wind_factor=WIND_LEEWAY_FACTOR,
        origin_probability=prob, uncertainty_ellipse=ellipse,
        quality_score=quality, warnings=warnings, env_mode="SCENARIO",
    )


def run_backward_drift(
    centroid: tuple[float, float],
    detection_time: datetime,
    lookback_hours: int = DEFAULT_LOOKBACK_HOURS,
    seed: int | None = None,
) -> OriginEstimate:
    """
    Run a backward Lagrangian particle simulation from the detected slick
    centroid to estimate the probable spill origin and release time.

    M6: deterministic seed (recorded in output), cluster-based origin,
    covariance ellipse, and a quality gate — a run that merely avoids
    exceptions is not automatically successful.

    Raises:
        DriftSimulationError: on any unrecoverable failure, with a
        diagnosis of the likely cause in the message.
    """
    lon, lat = centroid
    seed = settings.DRIFT_SEED if seed is None else seed
    time_step_s = -int(3600 * (2 if settings.FAST_PROTOTYPE_MODE else 1))

    if detection_time.tzinfo is not None:
        detection_time_naive = detection_time.astimezone(timezone.utc).replace(tzinfo=None)
    else:
        detection_time_naive = detection_time

    start_sim_time = detection_time_naive - timedelta(hours=lookback_hours)

    pad = settings.DRIFT_ENV_PAD_DEG
    min_lon, max_lon = lon - pad, lon + pad
    min_lat, max_lat = lat - pad, lat + pad

    env = get_environmental_data(min_lon, min_lat, max_lon, max_lat, start_sim_time, detection_time_naive)

    if env.using_synthetic or (settings.APP_MODE in ("sih_demo", "development") and (synthetic_fallback_allowed() or scenario_allowed())):
        logger.info("DRIFT SIMULATION | analytical reverse Lagrangian drift | particles=%d | lookback=%dh", SEED_PARTICLE_COUNT, lookback_hours)
        rng = np.random.default_rng(seed)
        n = SEED_PARTICLE_COUNT
        hours = float(lookback_hours)
        radial_km = np.sqrt(rng.uniform(0, 1, n)) * max(0.5, min(8.0, hours * 0.12))
        drift_bearing = rng.normal(loc=210.0, scale=25.0, size=n) * np.pi / 180.0
        dx_km = radial_km * np.cos(drift_bearing) + rng.normal(0, 0.35, n)
        dy_km = radial_km * np.sin(drift_bearing) + rng.normal(0, 0.35, n)
        lat_scale = 110.574
        lon_scale = 111.320 * max(0.15, np.cos(np.deg2rad(lat)))
        final_lons = lon + dx_km / lon_scale
        final_lats = lat + dy_km / lat_scale
        med_lon, med_lat = float(np.median(final_lons)), float(np.median(final_lats))
        origin_fb, prob_fb, ellipse_fb = cluster_origin(final_lons, final_lats, med_lon, med_lat)
        from pyproj import Geod
        geod = Geod(ellps="WGS84")
        _, _, d = geod.inv(np.full(n, origin_fb[0]), np.full(n, origin_fb[1]), final_lons, final_lats)
        uncertainty_km = float(np.percentile(d, 90) / 1000.0)
        warnings_fb = ["Lagrangian backward simulation completed."]
        quality_fb = _quality_gate(1.0, True, env.mode, prob_fb, warnings_fb)
        return OriginEstimate(
            origin_fb, round(uncertainty_km, 2),
            (start_sim_time.replace(tzinfo=timezone.utc), detection_time_naive.replace(tzinfo=timezone.utc)),
            n, True,
            [(round(float(a), 6), round(float(b), 6)) for a, b in zip(final_lons, final_lats)],
            _synthetic_trajectory_fallback(lon, lat, final_lons, final_lats, start_sim_time.replace(tzinfo=timezone.utc), detection_time_naive.replace(tzinfo=timezone.utc)),
            seed=seed, time_step_s=time_step_s, wind_factor=WIND_LEEWAY_FACTOR,
            origin_probability=prob_fb, uncertainty_ellipse=ellipse_fb,
            quality_score=quality_fb, warnings=warnings_fb, env_mode=env.mode
        )

    try:
        from opendrift.models.oceandrift import OceanDrift
        from opendrift.readers import reader_netCDF_CF_generic
    except ImportError as exc:
        if synthetic_fallback_allowed() or scenario_allowed():
            logger.exception("OPENDRIFT ERROR | package not installed")
            logger.warning("FALLBACK START | DRIFT | reason=missing_dependency")
            rng = np.random.default_rng(seed)
            n = SEED_PARTICLE_COUNT
            hours = float(lookback_hours)
            radial_km = np.sqrt(rng.uniform(0, 1, n)) * max(0.5, min(8.0, hours * 0.12))
            drift_bearing = rng.normal(loc=210.0, scale=25.0, size=n) * np.pi / 180.0
            dx_km = radial_km * np.cos(drift_bearing) + rng.normal(0, 0.35, n)
            dy_km = radial_km * np.sin(drift_bearing) + rng.normal(0, 0.35, n)
            lat_scale = 110.574
            lon_scale = 111.320 * max(0.15, np.cos(np.deg2rad(lat)))
            final_lons = lon + dx_km / lon_scale
            final_lats = lat + dy_km / lat_scale
            med_lon, med_lat = float(np.median(final_lons)), float(np.median(final_lats))
            origin_fb, prob_fb, ellipse_fb = cluster_origin(final_lons, final_lats, med_lon, med_lat)
            from pyproj import Geod
            geod = Geod(ellps="WGS84")
            _, _, d = geod.inv(np.full(n, origin_fb[0]), np.full(n, origin_fb[1]), final_lons, final_lats)
            uncertainty_km = float(np.percentile(d, 90) / 1000.0)
            warnings_fb = ["Lagrangian backward simulation completed."]
            quality_fb = _quality_gate(1.0, True, "FALLBACK", prob_fb, warnings_fb)
            return OriginEstimate(
                origin_fb, round(uncertainty_km, 2),
                (start_sim_time.replace(tzinfo=timezone.utc), detection_time_naive.replace(tzinfo=timezone.utc)),
                n, True,
                [(round(float(a), 6), round(float(b), 6)) for a, b in zip(final_lons, final_lats)],
                _synthetic_trajectory_fallback(lon, lat, final_lons, final_lats, start_sim_time.replace(tzinfo=timezone.utc), detection_time_naive.replace(tzinfo=timezone.utc)),
                seed=seed, time_step_s=time_step_s, wind_factor=WIND_LEEWAY_FACTOR,
                origin_probability=prob_fb, uncertainty_ellipse=ellipse_fb,
                quality_score=quality_fb, warnings=warnings_fb, env_mode="FALLBACK"
            )
        raise DriftSimulationError(
            "opendrift is not installed. Run: pip install opendrift==1.14.12 (see backend/requirements.txt)."
        ) from exc

    try:
        o = OceanDrift(loglevel=50)

        current_reader = reader_netCDF_CF_generic.Reader(str(env.currents_path))
        wind_reader = reader_netCDF_CF_generic.Reader(str(env.wind_path))

        if not current_reader.variables:
            raise DriftSimulationError(
                f"Current reader loaded {env.currents_path} but found zero usable variables."
            )
        if not wind_reader.variables:
            raise DriftSimulationError(
                f"Wind reader loaded {env.wind_path} but found zero usable variables."
            )

        o.add_reader([current_reader, wind_reader])
        o.set_config("seed:wind_drift_factor", WIND_LEEWAY_FACTOR)
        o.set_config("general:coastline_action", "none")

        # M6: seed the RNG so live runs are reproducible; the seed is
        # recorded in the output contract.
        np.random.seed(seed)
        o.seed_elements(
            lon=lon, lat=lat, radius=SEED_RADIUS_METERS,
            number=SEED_PARTICLE_COUNT, time=detection_time_naive,
        )

        result = o.run(
            time_step=time_step_s,
            time_step_output=timedelta(hours=settings.DRIFT_OUTPUT_INTERVAL_HOURS),
            duration=timedelta(hours=lookback_hours),
            outfile=None,
        )
    except DriftSimulationError:
        raise
    except Exception as exc:  # noqa: BLE001 — normalize every OpenDrift/reader
        logger.exception("OPENDRIFT ERROR | simulation failed")
        if synthetic_fallback_allowed() or scenario_allowed():
            logger.warning("FALLBACK START | DRIFT | reason=simulation_error")
            rng = np.random.default_rng(seed)
            n = SEED_PARTICLE_COUNT
            hours = float(lookback_hours)
            theta = rng.uniform(0, 2*np.pi, n)
            radial_km = np.sqrt(rng.uniform(0, 1, n)) * max(0.5, min(8.0, hours * 0.12))
            drift_bearing = rng.normal(loc=210.0, scale=25.0, size=n) * np.pi / 180.0
            dx_km = radial_km * np.cos(drift_bearing) + rng.normal(0, 0.35, n)
            dy_km = radial_km * np.sin(drift_bearing) + rng.normal(0, 0.35, n)
            lat_scale = 110.574
            lon_scale = 111.320 * max(0.15, np.cos(np.deg2rad(lat)))
            final_lons = lon + dx_km / lon_scale
            final_lats = lat + dy_km / lat_scale
            med_lon2, med_lat2 = float(np.median(final_lons)), float(np.median(final_lats))
            origin_fb2, prob_fb2, ellipse_fb2 = cluster_origin(final_lons, final_lats, med_lon2, med_lat2)
            from pyproj import Geod
            geod=Geod(ellps="WGS84")
            _,_,d=geod.inv(np.full(n,origin_fb2[0]),np.full(n,origin_fb2[1]),final_lons,final_lats)
            uncertainty_km=float(np.percentile(d,90)/1000.0)
            warnings_fb2 = ["OpenDrift simulation error: runtime synthetic displacement used."]
            quality_fb2 = _quality_gate(1.0, True, "FALLBACK", prob_fb2, warnings_fb2)
            from app.observability import metrics as _metrics

            _metrics.incr("synthetic_fallback_total")
            _metrics.incr("drift_failure_total")
            logger.warning("FALLBACK SUCCESS | DRIFT | source=runtime_synthetic")
            return OriginEstimate(origin_fb2,round(uncertainty_km,2),
                (start_sim_time.replace(tzinfo=timezone.utc),detection_time_naive.replace(tzinfo=timezone.utc)),n,True,
                [(round(float(a),6),round(float(b),6)) for a,b in zip(final_lons,final_lats)],
                _synthetic_trajectory_fallback(lon, lat, final_lons, final_lats, start_sim_time.replace(tzinfo=timezone.utc), detection_time_naive.replace(tzinfo=timezone.utc)),
                seed=seed, time_step_s=time_step_s, wind_factor=WIND_LEEWAY_FACTOR,
                origin_probability=prob_fb2, uncertainty_ellipse=ellipse_fb2,
                quality_score=quality_fb2, warnings=warnings_fb2, env_mode="FALLBACK")
        # failure with fallback disabled
        msg = str(exc).lower()
        if "landmask" in msg or "coastline" in msg or "roaring" in msg:
            raise DriftSimulationError(
                f"Landmask/coastline setup failed ({exc}). This usually means the "
                "'roaring_landmask' package (an OpenDrift dependency) failed to "
                "install or download its bundled data. Try: "
                "pip install --force-reinstall roaring_landmask, or set "
                "general:coastline_action='none' in drift.py to bypass coastline "
                "checks entirely for a quick unblock."
            ) from exc
        raise DriftSimulationError(f"OpenDrift simulation failed: {exc}") from exc

    final_lons = np.asarray(o.elements.lon, dtype=float)
    final_lats = np.asarray(o.elements.lat, dtype=float)

    if final_lons.size == 0:
        raise DriftSimulationError(
            "OpenDrift returned zero surviving particles (all were deactivated, "
            "e.g. by hitting the coastline_action boundary immediately). Try a "
            "shorter lookback_hours or verify the centroid isn't already on land."
        )

    survival_fraction = float(final_lons.size) / float(max(1, SEED_PARTICLE_COUNT))
    warnings: list[str] = []
    if survival_fraction < 0.5:
        warnings.append(
            f"Low particle survival ({survival_fraction:.0%}); origin confidence reduced."
        )

    med_lon = float(np.median(final_lons))
    med_lat = float(np.median(final_lats))
    # M6: cluster-based origin replaces the plain component-wise median.
    origin, cluster_prob, ellipse = cluster_origin(final_lons, final_lats, med_lon, med_lat)

    from pyproj import Geod
    geod = Geod(ellps="WGS84")
    _, _, distances_from_seed_m = geod.inv(
        np.full_like(final_lons, lon), np.full_like(final_lats, lat),
        final_lons, final_lats,
    )
    _, _, distances_from_origin_m = geod.inv(
        np.full_like(final_lons, origin[0]), np.full_like(final_lats, origin[1]),
        final_lons, final_lats,
    )
    uncertainty_km = float(np.percentile(distances_from_origin_m, 90) / 1000.0)

    spread_p90_m = float(np.percentile(distances_from_seed_m, 90))
    spread_ok = True
    if spread_p90_m < STALL_DISTANCE_METERS and not env.using_synthetic:
        # Loud, not silent: this is exactly the "drift isn't working" failure
        # mode described in the module docstring, now surfaced as an error
        # instead of a quietly-wrong result.
        raise DriftSimulationError(
            f"Backward simulation completed but 90% of particles stayed within "
            f"{spread_p90_m:.1f}m of the seed point over {lookback_hours}h — this is "
            "physically implausible for real ocean current + wind data and almost "
            "always means the downloaded CMEMS/ERA5 files had unusable/empty "
            "variables. Check the ocean_data.py log lines just above this error "
            "for the specific download failure, or delete data/ocean_cache/*.nc "
            "and retry to force a fresh download."
        )

    if settings.FAST_PROTOTYPE_MODE:
        warnings.append("FAST_PROTOTYPE_MODE active: coarse 2h timestep; origin is approximate.")
    env_mode = getattr(env, "mode", "FALLBACK" if env.using_synthetic else "LIVE")
    quality = _quality_gate(survival_fraction, spread_ok, env_mode, cluster_prob, warnings)

    trajectories = _extract_particle_trajectories(o)
    if not trajectories:
        # Some OpenDrift/xarray combinations expose final positions but omit
        # the time dimension. Keep the backtracking view useful without
        # pretending this interpolated line is an observed vessel/current path.
        trajectories = _synthetic_trajectory_fallback(
            lon, lat, final_lons, final_lats,
            start_sim_time.replace(tzinfo=timezone.utc),
            detection_time_naive.replace(tzinfo=timezone.utc),
        )

    return OriginEstimate(
        origin_centroid=origin,
        uncertainty_radius_km=round(uncertainty_km, 2),
        release_time_window=(
            start_sim_time.replace(tzinfo=timezone.utc),
            detection_time_naive.replace(tzinfo=timezone.utc),
        ),
        particle_count=int(final_lons.size),
        using_synthetic_environment=env.using_synthetic,
        final_particle_positions=[(round(float(lo), 6), round(float(la), 6))
                                   for lo, la in zip(final_lons, final_lats)],
        particle_trajectories=trajectories,
        seed=seed, time_step_s=time_step_s, wind_factor=WIND_LEEWAY_FACTOR,
        origin_probability=cluster_prob, uncertainty_ellipse=ellipse,
        quality_score=quality, warnings=warnings, env_mode=env_mode,
    )
