"""AIS provider interface (M7).

Every AIS source implements AISProvider and declares its capabilities.
No module outside providers/ may import a vendor SDK for AIS.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from app.contracts import AisCapability


@dataclass
class RawPosition:
    mmsi: str
    name: str
    vessel_type: str
    flag: str
    length_m: float
    time: datetime
    lon: float
    lat: float
    speed_kn: float | None
    heading_deg: float | None


@dataclass
class ProviderResult:
    positions: list[RawPosition]
    mode: str  # LIVE | ARCHIVE | FALLBACK | SCENARIO
    warnings: list[str] = field(default_factory=list)
    provenance: dict = field(default_factory=dict)


class AISProvider(Protocol):
    """Interface all AIS providers implement."""

    name: str
    capabilities: list[AisCapability]

    def search(
        self,
        min_lon: float, min_lat: float, max_lon: float, max_lat: float,
        start_time: datetime, end_time: datetime,
    ) -> ProviderResult:
        ...


class ProviderShapeError(RuntimeError):
    """Provider returned HTTP 200 with an unparseable/empty-shaped payload."""
