"""
Ocean current + wind data acquisition service — Brain 2 input.

Two real, free data sources, matching the architecture doc:

    1. Copernicus Marine Service (CMEMS)  -> surface ocean currents (u, v)
    2. ECMWF ERA5 (via Copernicus CDS API) -> 10m wind (u10, v10)

Both are pulled as small NetCDF subsets clipped to the incident's bounding
box + a time window, using each provider's OFFICIAL python client.

LIVE-FIRST / FALLBACK-ON-FAILURE MODE:
    If credentials are missing, OR if the real download fails for any
    reason (expired dataset ID, network block, auth error, empty
    response), this module transparently falls back to a synthetic
    current/wind field so the rest of the pipeline is always testable.
    `using_synthetic` on the returned object tells the caller which
    happened — check it, don't assume real data was used just because
    credentials were present.

WHY THIS MATTERS FOR "DRIFT NOT WORKING":
    The single most common real-world failure mode here is silent, not
    loud: a downloaded NetCDF that opens fine but has variables named
    without CF `standard_name` attributes (some CMEMS/ERA5 export
    configurations, or a corrupted/partial download). OpenDrift's
    `reader_netCDF_CF_generic` identifies variables by CF standard_name,
    NOT by variable name — a file with wrong/missing metadata loads with
    zero usable variables, so every particle just sits at its seed point
    for the whole run. The simulation "succeeds" (no exception) but the
    origin comes back equal to the input centroid, which looks exactly
    like "drift isn't working" without ever raising an error. This module
    validates variable coverage immediately after every real download and
    falls back to the (always-valid) synthetic field rather than handing
    OpenDrift a reader with nothing in it.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import scenario_allowed, settings, synthetic_fallback_allowed
from app.errors import RealDataUnavailableError

# NOTE (M5): numpy/xarray are imported lazily inside functions because some
# locked-down environments block their native DLLs at import time. The pure
# helpers (month groups, mode logic, retry, failure resolution) must stay
# importable without them.


def _np():
    import numpy as np

    return np


def _xr():
    import xarray as xr

    return xr

logger = logging.getLogger(__name__)

CACHE_DIR = settings.OCEAN_CACHE_DIR

# Pad the incident bbox by this many degrees so drifting particles don't
# immediately exit the downloaded data extent.
BBOX_PAD_DEG = settings.DRIFT_ENV_PAD_DEG

_CURRENT_STD_NAMES = ("eastward_sea_water_velocity", "northward_sea_water_velocity")
_WIND_STD_NAMES = ("eastward_wind", "northward_wind")

# Sanity caps for range validation (m/s). Values beyond these are treated as
# corrupt rather than real ocean/atmosphere data.
_RANGE_LIMITS = {"current": 5.0, "wind": 150.0}

# Per-cache-key locks so concurrent jobs requesting the same window serialize
# the download instead of colliding on one file (M5).
_FETCH_LOCKS: dict[str, threading.Lock] = defaultdict(threading.Lock)

# Mode precedence: the "least live" of currents/wind wins for the bundle.
_MODE_RANK = {"LIVE": 0, "ARCHIVE": 1, "FALLBACK": 2, "SCENARIO": 3}


@dataclass
class FetchResult:
    """One provider fetch: file path + honesty metadata (M5)."""

    path: Path
    using_synthetic: bool
    mode: str  # LIVE | ARCHIVE | FALLBACK | SCENARIO
    provenance: dict


@dataclass
class EnvironmentalData:
    """Bundled current + wind fields, ready to hand to the OpenDrift reader."""

    currents_path: Path
    wind_path: Path
    using_synthetic: bool
    mode: str = "LIVE"  # worst of the two fetches (M5)
    provenance: list[dict] = field(default_factory=list)


def _has_cmems_credentials() -> bool:
    return bool(settings.CMEMS_USERNAME and settings.CMEMS_PASSWORD)


def _has_cds_credentials() -> bool:
    return bool(settings.CDSAPI_KEY)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _with_retry(label: str, fn, attempts: int = 3, base_delay_s: float = 2.0):
    """Exponential-backoff retry for provider calls (M5)."""
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            last = exc
            logger.warning("%s attempt %d/%d failed: %s", label, attempt, attempts, exc)
            if attempt < attempts:
                time.sleep(base_delay_s * (2 ** (attempt - 1)))
    raise last  # type: ignore[misc]


def _data_time_range(path: Path) -> tuple[datetime | None, datetime | None]:
    try:
        xr = _xr()
        with xr.open_dataset(path) as ds:
            if "time" not in ds.coords:
                return None, None
            times = ds["time"].values
            if len(times) == 0:
                return None, None
            lo = datetime.fromtimestamp(float(times[0].astype("datetime64[s]").astype(int)), tz=timezone.utc)
            hi = datetime.fromtimestamp(float(times[-1].astype("datetime64[s]").astype(int)), tz=timezone.utc)
            return lo, hi
    except Exception:
        return None, None


def _time_coverage_ok(path: Path, start: datetime, end: datetime, tol_hours: float) -> bool:
    lo, hi = _data_time_range(path)
    if lo is None or hi is None:
        return False
    s = start if start.tzinfo else start.replace(tzinfo=timezone.utc)
    e = end if end.tzinfo else end.replace(tzinfo=timezone.utc)
    tol = timedelta(hours=tol_hours)
    return (lo - tol) <= s and e <= (hi + tol)


def _spatial_coverage_ok(path: Path, min_lon: float, min_lat: float, max_lon: float, max_lat: float) -> bool:
    try:
        xr = _xr()
        with xr.open_dataset(path) as ds:
            lons = ds["lon"].values if "lon" in ds.coords else ds["longitude"].values
            lats = ds["lat"].values if "lat" in ds.coords else ds["latitude"].values
            return (float(lons.min()) <= min_lon and float(lons.max()) >= max_lon
                    and float(lats.min()) <= min_lat and float(lats.max()) >= max_lat)
    except Exception:
        return False


def _range_ok(path: Path, std_names: tuple[str, str], kind: str) -> bool:
    limit = _RANGE_LIMITS[kind]
    try:
        np = _np()
        xr = _xr()
        with xr.open_dataset(path) as ds:
            for name in std_names:
                var = next(v for v in ds.data_vars.values() if v.attrs.get("standard_name") == name)
                vals = np.asarray(var.values, dtype=float)
                finite = vals[np.isfinite(vals)]
                if finite.size == 0 or np.abs(finite).max() > limit:
                    logger.warning("%s range check failed for %s (limit %s m/s).", path.name, name, limit)
                    return False
            return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s range check error: %s", path.name, exc)
        return False


def _file_quality_ok(path: Path, std_names: tuple[str, str], kind: str,
                     min_lon: float, min_lat: float, max_lon: float, max_lat: float,
                     start: datetime, end: datetime, tol_hours: float) -> bool:
    return (
        _file_has_cf_variables(path, std_names)
        and _time_coverage_ok(path, start, end, tol_hours)
        and _spatial_coverage_ok(path, min_lon, min_lat, max_lon, max_lat)
        and _range_ok(path, std_names, kind)
    )


def _cache_fresh(path: Path) -> bool:
    try:
        age_h = (time.time() - path.stat().st_mtime) / 3600.0
        return age_h <= settings.ENV_CACHE_TTL_HOURS
    except OSError:
        return False


def _data_age_seconds(path: Path) -> float | None:
    _, hi = _data_time_range(path)
    if hi is None:
        return None
    return max(0.0, (_utcnow() - hi).total_seconds())


def _prov(provider: str, dataset: str | None, t0: datetime, t1: datetime,
          path: Path, cache_hit: bool, mode: str) -> dict:
    age = _data_age_seconds(path)
    return {
        "provider": provider,
        "dataset": dataset,
        "request_time": t0.isoformat().replace("+00:00", "Z"),
        "response_time": t1.isoformat().replace("+00:00", "Z"),
        "data_time": (_data_time_range(path)[1] or t1).isoformat().replace("+00:00", "Z"),
        "age_seconds": age,
        "cache_hit": cache_hit,
        "mode": mode,
    }


def _resolve_failure(label: str, reason: str, synth_fn, provenance_extra: dict) -> FetchResult:
    """Map a provider failure onto the A1 data-mode machine (M5).

    SCENARIO only in sih_demo, FALLBACK only in development; staging and
    production raise RealDataUnavailableError instead of substituting data.
    """
    if scenario_allowed():
        mode = "SCENARIO"
    elif synthetic_fallback_allowed():
        mode = "FALLBACK"
    else:
        raise RealDataUnavailableError(
            f"{label} unavailable ({reason}) and synthetic substitution is forbidden "
            f"in APP_MODE={settings.APP_MODE}."
        )
    from app.observability import metrics as _metrics

    _metrics.incr("synthetic_fallback_total")
    _metrics.incr(f"provider_failure_{label}")
    logger.warning("ENV %s | reason=%s | mode=%s", label, reason, mode)
    path = synth_fn()
    now = _utcnow()
    prov = {"provider": f"{label}-synthetic", "dataset": "analytic-gyre",
            "request_time": now.isoformat().replace("+00:00", "Z"),
            "response_time": now.isoformat().replace("+00:00", "Z"),
            "data_time": now.isoformat().replace("+00:00", "Z"),
            "age_seconds": 0.0, "cache_hit": False, "mode": mode}
    prov.update(provenance_extra)
    return FetchResult(path=path, using_synthetic=True, mode=mode, provenance=prov)


def _worst_mode(a: str, b: str) -> str:
    return a if _MODE_RANK.get(a, 0) >= _MODE_RANK.get(b, 0) else b


def _era5_month_groups(start_time: datetime, end_time: datetime) -> list[dict]:
    """Split a time window into per-(year, month) CDS request groups (M5).

    The previous code built a single year/month with every day in the window,
    silently requesting wrong dates for windows crossing month boundaries.
    """
    groups: dict[tuple[int, int], set[str]] = {}
    cur = start_time.replace(hour=0, minute=0, second=0, microsecond=0)
    end_day = end_time.replace(hour=0, minute=0, second=0, microsecond=0)
    while cur <= end_day:
        groups.setdefault((cur.year, cur.month), set()).add(f"{cur.day:02d}")
        cur += timedelta(days=1)
    return [{"year": str(y), "month": f"{m:02d}", "day": sorted(days)} for (y, m), days in sorted(groups.items())]


def _file_has_cf_variables(path: Path, expected_std_names: tuple[str, str]) -> bool:
    """
    Open a NetCDF file and confirm it exposes at least one variable per
    expected CF standard_name. Returns False (never raises) on any problem
    so callers can safely fall back to synthetic data.
    """
    try:
        np = _np()
        xr = _xr()
        with xr.open_dataset(path) as ds:
            found = {
                var.attrs.get("standard_name")
                for var in ds.data_vars.values()
            }
            missing = [name for name in expected_std_names if name not in found]
            if missing:
                logger.warning(
                    "%s is missing CF standard_name(s) %s — treating as invalid, "
                    "falling back to synthetic data instead of feeding OpenDrift an "
                    "unusable reader.",
                    path, missing,
                )
                return False
            # Also confirm there's at least one non-NaN timestep of real data.
            for name in expected_std_names:
                var = next(v for v in ds.data_vars.values() if v.attrs.get("standard_name") == name)
                if not np.isfinite(var.values).any():
                    logger.warning("%s variable for standard_name=%s is entirely NaN.", path, name)
                    return False
            return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not validate %s (%s) — treating as invalid.", path, exc)
        return False


def fetch_currents(
    min_lon: float, min_lat: float, max_lon: float, max_lat: float,
    start_time: datetime, end_time: datetime,
) -> FetchResult:
    """Download surface currents (u, v) from CMEMS with retry, TTL cache, and QC (M5)."""
    cache_key = f"currents_{min_lon:.3f}_{min_lat:.3f}_{max_lon:.3f}_{max_lat:.3f}_{start_time:%Y%m%dT%H}_{end_time:%Y%m%dT%H}.nc"
    out_path = CACHE_DIR / cache_key
    lock = _FETCH_LOCKS[cache_key]
    with lock:
        t0 = _utcnow()
        if out_path.exists() and _cache_fresh(out_path) and _file_quality_ok(
                out_path, _CURRENT_STD_NAMES, "current",
                min_lon, min_lat, max_lon, max_lat, start_time, end_time, tol_hours=6):
            age = _data_age_seconds(out_path) or 0.0
            mode = "LIVE" if age <= settings.ENV_FRESH_HOURS * 3600 else "ARCHIVE"
            logger.info("CMEMS CACHE HIT | path=%s | mode=%s", out_path, mode)
            return FetchResult(path=out_path, using_synthetic=False, mode=mode,
                               provenance=_prov("CMEMS", "cmems_mod_glo_phy_anfc_0.083deg_PT1H-m",
                                                t0, _utcnow(), out_path, True, mode))
        if out_path.exists():
            out_path.unlink(missing_ok=True)

        if not _has_cmems_credentials():
            logger.error("CMEMS API UNAVAILABLE | credentials are not configured")
            return _resolve_failure(
                "CMEMS", "missing_credentials",
                lambda: _synthetic_field(out_path, min_lon, min_lat, max_lon, max_lat, start_time, end_time, var_names=("uo", "vo")),
                {},
            )

        def _download():
            import copernicusmarine as cm  # official CMEMS python client

            tmp = out_path.with_suffix(".part.nc")
            cm.subset(
                dataset_id="cmems_mod_glo_phy_anfc_0.083deg_PT1H-m",  # global 1/12deg physics analysis
                variables=["uo", "vo"],
                minimum_longitude=min_lon - BBOX_PAD_DEG,
                maximum_longitude=max_lon + BBOX_PAD_DEG,
                minimum_latitude=min_lat - BBOX_PAD_DEG,
                maximum_latitude=max_lat + BBOX_PAD_DEG,
                start_datetime=start_time.isoformat(),
                end_datetime=end_time.isoformat(),
                minimum_depth=0, maximum_depth=1,
                output_filename=str(tmp),
                username=settings.CMEMS_USERNAME,
                password=settings.CMEMS_PASSWORD,
                overwrite=True,
            )
            tmp.replace(out_path)

        try:
            _with_retry("CMEMS subset", _download)
            if not out_path.exists() or not _file_quality_ok(
                    out_path, _CURRENT_STD_NAMES, "current",
                    min_lon, min_lat, max_lon, max_lat, start_time, end_time, tol_hours=6):
                raise RuntimeError("CMEMS subset produced no usable current data for this bbox/time window.")
            logger.info("CMEMS API SUCCESS | currents=%s", out_path)
            return FetchResult(path=out_path, using_synthetic=False, mode="LIVE",
                               provenance=_prov("CMEMS", "cmems_mod_glo_phy_anfc_0.083deg_PT1H-m",
                                                t0, _utcnow(), out_path, False, "LIVE"))
        except RealDataUnavailableError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("CMEMS API ERROR | live request failed")
            if out_path.exists():
                out_path.unlink(missing_ok=True)
            return _resolve_failure(
                "CMEMS", f"api_error: {type(exc).__name__}",
                lambda: _synthetic_field(out_path, min_lon, min_lat, max_lon, max_lat, start_time, end_time, var_names=("uo", "vo")),
                {},
            )

def fetch_wind(
    min_lon: float, min_lat: float, max_lon: float, max_lat: float,
    start_time: datetime, end_time: datetime,
) -> FetchResult:
    """Download 10m wind (u10, v10) from ERA5 via CDS with retry, TTL cache, QC (M5)."""
    cache_key = f"wind_{min_lon:.3f}_{min_lat:.3f}_{max_lon:.3f}_{max_lat:.3f}_{start_time:%Y%m%dT%H}_{end_time:%Y%m%dT%H}.nc"
    out_path = CACHE_DIR / cache_key
    lock = _FETCH_LOCKS[cache_key]
    with lock:
        t0 = _utcnow()
        if out_path.exists() and _cache_fresh(out_path) and _file_quality_ok(
                out_path, _WIND_STD_NAMES, "wind",
                min_lon, min_lat, max_lon, max_lat, start_time, end_time, tol_hours=24):
            age = _data_age_seconds(out_path) or 0.0
            mode = "LIVE" if age <= settings.ENV_FRESH_HOURS * 3600 else "ARCHIVE"
            logger.info("ERA5 CACHE HIT | path=%s | mode=%s", out_path, mode)
            return FetchResult(path=out_path, using_synthetic=False, mode=mode,
                               provenance=_prov("ERA5/CDS", "reanalysis-era5-single-levels",
                                                t0, _utcnow(), out_path, True, mode))
        if out_path.exists():
            out_path.unlink(missing_ok=True)

        if not _has_cds_credentials():
            logger.error("ERA5/CDS API UNAVAILABLE | credentials are not configured")
            return _resolve_failure(
                "ERA5", "missing_credentials",
                lambda: _synthetic_field(out_path, min_lon, min_lat, max_lon, max_lat, start_time, end_time, var_names=("u10", "v10")),
                {},
            )

        def _download():
            import cdsapi  # official CDS python client

            client = cdsapi.Client(url=settings.CDSAPI_URL, key=settings.CDSAPI_KEY)
            area = [max_lat + BBOX_PAD_DEG, min_lon - BBOX_PAD_DEG,
                    min_lat - BBOX_PAD_DEG, max_lon + BBOX_PAD_DEG]  # N, W, S, E
            # M5: one request per (year, month) so windows crossing month
            # boundaries retrieve correct days for each month.
            parts: list[Path] = []
            try:
                for i, group in enumerate(_era5_month_groups(start_time, end_time)):
                    part = out_path.with_name(out_path.stem + f".part{i}.nc")
                    client.retrieve(
                        "reanalysis-era5-single-levels",
                        {
                            "product_type": "reanalysis",
                            "variable": ["10m_u_component_of_wind", "10m_v_component_of_wind"],
                            "year": group["year"],
                            "month": group["month"],
                            "day": group["day"],
                            "time": [f"{h:02d}:00" for h in range(24)],
                            "area": area,
                            "format": "netcdf",
                        },
                        str(part),
                    )
                    parts.append(part)
                if len(parts) == 1:
                    parts[0].replace(out_path)
                else:
                    xr = _xr()
                    datasets = [xr.open_dataset(p) for p in parts]
                    try:
                        merged = xr.concat(datasets, dim="time").sortby("time")
                        merged.to_netcdf(out_path)
                    finally:
                        for ds in datasets:
                            ds.close()
            finally:
                for part in parts:
                    if part.exists() and part != out_path:
                        part.unlink(missing_ok=True)

        try:
            _with_retry("ERA5 retrieve", _download)
            if not out_path.exists() or not _file_quality_ok(
                    out_path, _WIND_STD_NAMES, "wind",
                    min_lon, min_lat, max_lon, max_lat, start_time, end_time, tol_hours=24):
                raise RuntimeError("CDS/ERA5 retrieve produced no usable wind data for this bbox/time window.")
            logger.info("Downloaded ERA5 wind to %s", out_path)
            return FetchResult(path=out_path, using_synthetic=False, mode="LIVE",
                               provenance=_prov("ERA5/CDS", "reanalysis-era5-single-levels",
                                                t0, _utcnow(), out_path, False, "LIVE"))
        except RealDataUnavailableError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("ERA5/CDS API ERROR | live request failed")
            if out_path.exists():
                out_path.unlink(missing_ok=True)
            return _resolve_failure(
                "ERA5", f"api_error: {type(exc).__name__}",
                lambda: _synthetic_field(out_path, min_lon, min_lat, max_lon, max_lat, start_time, end_time, var_names=("u10", "v10")),
                {},
            )

def _synthetic_field(
    out_path: Path, min_lon: float, min_lat: float, max_lon: float, max_lat: float,
    start_time: datetime, end_time: datetime, var_names: tuple[str, str],
) -> Path:
    """
    Build a smooth, deterministic analytic vector field (a mild gyre + drift
    bias) as a stand-in for real ocean/wind data. This is ONLY for exercising
    the pipeline before real credentials are configured — never use for a
    real attribution case.
    """
    np = _np()
    xr = _xr()
    lons = np.linspace(min_lon - BBOX_PAD_DEG, max_lon + BBOX_PAD_DEG, 40)
    lats = np.linspace(min_lat - BBOX_PAD_DEG, max_lat + BBOX_PAD_DEG, 40)
    hours = int((end_time - start_time).total_seconds() // 3600) + 2  # +2 = safety margin either side
    # np.datetime64, NOT python datetime objects -- xarray/netCDF4 cannot
    # infer a serializable dtype from a plain list of datetime.datetime.
    times = np.array(
        [start_time - timedelta(hours=1) + timedelta(hours=h) for h in range(max(hours, 2))],
        dtype="datetime64[ns]",
    )

    lon_grid, lat_grid = np.meshgrid(lons, lats)
    u = -0.15 * (lat_grid - lat_grid.mean())  # mild rotational component
    v = 0.15 * (lon_grid - lon_grid.mean())
    u_field = np.stack([u + 0.05 for _ in times])  # (time, lat, lon), + small eastward bias
    v_field = np.stack([v + 0.02 for _ in times])

    # OpenDrift's reader_netCDF_CF_generic identifies variables by CF
    # standard_name, NOT by variable name — this must be set explicitly.
    is_wind = var_names[0].startswith("u10")
    std_names = _WIND_STD_NAMES if is_wind else _CURRENT_STD_NAMES

    ds = xr.Dataset(
        {
            var_names[0]: (("time", "lat", "lon"), u_field.astype("float32")),
            var_names[1]: (("time", "lat", "lon"), v_field.astype("float32")),
        },
        coords={"time": times, "lat": lats, "lon": lons},
    )
    ds[var_names[0]].attrs = {"standard_name": std_names[0], "units": "m/s"}
    ds[var_names[1]].attrs = {"standard_name": std_names[1], "units": "m/s"}
    ds["lat"].attrs = {"standard_name": "latitude", "units": "degrees_north"}
    ds["lon"].attrs = {"standard_name": "longitude", "units": "degrees_east"}
    ds.to_netcdf(out_path)
    logger.info("Wrote synthetic field (%s, %s) covering %s", *var_names, out_path.name)
    return out_path


def get_environmental_data(
    min_lon: float, min_lat: float, max_lon: float, max_lat: float,
    start_time: datetime, end_time: datetime,
) -> EnvironmentalData:
    """Convenience wrapper: fetch both currents and wind for one incident."""
    # CMEMS and ERA5 are independent network-bound operations. Fetch them
    # concurrently so live mode spends roughly max(CMEMS, ERA5), not the sum.
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as pool:
        current_future = pool.submit(fetch_currents, min_lon, min_lat, max_lon, max_lat, start_time, end_time)
        wind_future = pool.submit(fetch_wind, min_lon, min_lat, max_lon, max_lat, start_time, end_time)
        currents = current_future.result()
        wind = wind_future.result()
    using_synthetic = currents.using_synthetic or wind.using_synthetic
    mode = _worst_mode(currents.mode, wind.mode)
    return EnvironmentalData(
        currents_path=currents.path,
        wind_path=wind.path,
        using_synthetic=using_synthetic,
        mode=mode,
        provenance=[currents.provenance, wind.provenance],
    )
