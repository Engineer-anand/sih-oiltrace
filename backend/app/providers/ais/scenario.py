"""Scenario AIS fleet provider (M7).

Quarantined demo fixture: reachable only when APP_MODE=sih_demo.
Identifiers are structurally synthetic (SCENARIO-NN, never plausible MMSIs)
and names carry a scenario suffix so they cannot be confused with live data.
"""
from __future__ import annotations

import logging
import math
import random
from datetime import datetime, timedelta, timezone

from app.contracts import AisCapability
from app.providers.ais.base import AISProvider, ProviderResult, RawPosition

logger = logging.getLogger(__name__)

EARTH_RADIUS_KM = 6371.0088


class ScenarioFleetProvider(AISProvider):
    name = "scenario"
    # Synthetic positions only; no real motion evidence.
    capabilities = [AisCapability.AIS_PRESENCE]

    def search(self, min_lon, min_lat, max_lon, max_lat, start_time, end_time) -> ProviderResult:
        origin_lon = (min_lon + max_lon) / 2.0
        origin_lat = (min_lat + max_lat) / 2.0
        rng = random.Random(20260905 + round(origin_lon * 100) + round(origin_lat * 100))
        names = [
            ("MV Meridian", "tanker", "PA"),
            ("Ocean Venture", "cargo", "LR"),
            ("Blue Current", "tanker", "SG"),
            ("Sea Lantern", "fishing", "IN"),
            ("Northstar Trader", "cargo", "MH"),
            ("Coral Wind", "tug", "PA"),
            ("Pacific Atlas", "high-risk tanker", "LR"),
        ]
        midpoint = start_time + (end_time - start_time) / 2
        rows: list[RawPosition] = []
        for index, (name, vessel_type, flag) in enumerate(names):
            mmsi = f"SCENARIO-{index + 1:02d}"
            distance_km = 1.5 + index * 2.4
            bearing = math.radians(28 + index * 47)
            speed = 1.8 + (index % 4) * 3.1
            length_m = 82 + index * 27
            for point_index, offset_hours in enumerate((-0.75, 0, 0.75)):
                distance = distance_km + (point_index - 1) * speed * 0.35
                d_over_r = distance / EARTH_RADIUS_KM
                lat1, lon1 = math.radians(origin_lat), math.radians(origin_lon)
                lat2 = math.asin(math.sin(lat1) * math.cos(d_over_r) + math.cos(lat1) * math.sin(d_over_r) * math.cos(bearing))
                lon2 = lon1 + math.atan2(math.sin(bearing) * math.sin(d_over_r) * math.cos(lat1), math.cos(d_over_r) - math.sin(lat1) * math.sin(lat2))
                rows.append(RawPosition(
                    mmsi=mmsi, name=f"{name} (scenario)", vessel_type=vessel_type,
                    flag=flag, length_m=float(length_m),
                    time=(midpoint + timedelta(hours=offset_hours)).astimezone(timezone.utc),
                    lon=round(math.degrees(lon2), 6), lat=round(math.degrees(lat2), 6),
                    speed_kn=round(speed, 1), heading_deg=round((math.degrees(bearing) + 180) % 360, 1),
                ))
        logger.info("AIS SCENARIO SOURCE | generated_positions=%d", len(rows))
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        return ProviderResult(
            positions=rows, mode="SCENARIO",
            warnings=["Scenario fleet: synthetic vessels for demonstration only."],
            provenance={"provider": "scenario", "dataset": "builtin-fleet-v1",
                        "request_time": now, "response_time": now, "data_time": now,
                        "age_seconds": 0.0, "cache_hit": False, "mode": "SCENARIO"},
        )
