"""Global Fishing Watch presence provider (M7).

Classified strictly as AIS_PRESENCE: GFW 4Wings returns vessel presence,
not individual vessel tracks. Speed/heading are left unknown — never
fabricated — so downstream scoring must treat motion factors as UNAVAILABLE.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.config import settings
from app.contracts import AisCapability, to_iso_z
from app.errors import RealDataUnavailableError
from app.providers.ais.base import AISProvider, ProviderResult, ProviderShapeError, RawPosition

logger = logging.getLogger(__name__)

DATASET = "public-global-presence:latest"
ENDPOINT = "https://gateway.api.globalfishingwatch.org/v3/4wings/report"


class GfwPresenceProvider(AISProvider):
    name = "gfw"
    capabilities = [AisCapability.AIS_PRESENCE]

    def search(self, min_lon, min_lat, max_lon, max_lat, start_time, end_time) -> ProviderResult:
        if not settings.GFW_API_TOKEN:
            raise RealDataUnavailableError("GFW_API_TOKEN is not configured.")
        t0 = datetime.now(timezone.utc)
        import httpx

        headers = {"Authorization": f"Bearer {settings.GFW_API_TOKEN}"}
        polygon = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature", "properties": {},
                "geometry": {"type": "Polygon", "coordinates": [[
                    [min_lon, min_lat], [max_lon, min_lat],
                    [max_lon, max_lat], [min_lon, max_lat], [min_lon, min_lat]
                ]]},
            }],
        }
        params = {
            "spatial-resolution": "HIGH",
            "format": "JSON",
            "group-by": "MMSI",
            "temporal-resolution": "HOURLY",
            "datasets[0]": DATASET,
            "date-range": f"{start_time.astimezone(timezone.utc):%Y-%m-%d},{end_time.astimezone(timezone.utc):%Y-%m-%d}",
            "spatial-aggregation": "false",
        }
        try:
            resp = httpx.post(ENDPOINT, headers=headers, params=params, json={"geojson": polygon}, timeout=120.0)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.exception("GFW API ERROR | live request failed")
            raise RealDataUnavailableError(f"GFW live AIS unavailable: {type(exc).__name__}") from exc
        t1 = datetime.now(timezone.utc)

        # Schema-validate the payload shape (M7): a 200 with an unexpected
        # shape must raise, never silently become "zero vessels".
        if not isinstance(data, dict) or "entries" not in data or not isinstance(data["entries"], list):
            raise ProviderShapeError("GFW response missing 'entries' list; refusing to treat as zero vessels.")
        raw_entries = data["entries"]
        entries: list[dict] = []
        for raw_entry in raw_entries:
            if not isinstance(raw_entry, dict):
                continue
            if "mmsi" in raw_entry:
                entries.append(raw_entry)
                continue
            for value in raw_entry.values():
                if isinstance(value, dict):
                    entries.append(value)
                elif isinstance(value, list):
                    entries.extend(item for item in value if isinstance(item, dict))
        if raw_entries and not entries:
            raise ProviderShapeError("GFW response entries unparseable; refusing to treat as zero vessels.")

        positions: list[RawPosition] = []
        for entry in entries:
            mmsi = str(entry.get("mmsi") or "unknown")
            if mmsi == "unknown":
                continue
            timestamp = entry.get("entryTimestamp") or entry.get("date")
            if not timestamp or entry.get("lat") is None or entry.get("lon") is None:
                continue
            positions.append(RawPosition(
                mmsi=mmsi, name=entry.get("shipName") or "unknown",
                vessel_type=str(entry.get("vessel_type") or "unknown").lower(),
                flag=entry.get("flag") or "", length_m=0.0,
                time=datetime.fromisoformat(str(timestamp).replace("Z", "+00:00")),
                lon=float(entry["lon"]), lat=float(entry["lat"]),
                speed_kn=None, heading_deg=None,
            ))
        logger.info("GFW API RESPONSE VALID | rows=%d | positions=%d", len(entries), len(positions))
        return ProviderResult(
            positions=positions, mode="LIVE",
            provenance={
                "provider": "GFW", "dataset": DATASET,
                "request_time": to_iso_z(t0), "response_time": to_iso_z(t1),
                "data_time": to_iso_z(t1), "age_seconds": 0.0,
                "cache_hit": False, "mode": "LIVE",
            },
        )
