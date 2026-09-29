"""
ORM models — mirrors the database schema in the architecture diagram:

    spills, drift_runs, vessels, vessel_tracks, scores, evidence_logs

GeoJSON is retained as a portable mirror (`geom_json`). When PostgreSQL is
used, `spills.geom` is a native PostGIS geometry column in EPSG:4326 with a
spatial index; SQLite uses the portable mirror.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text, Index
from sqlalchemy.orm import DeclarativeBase, relationship
try:
    from geoalchemy2 import Geometry
except Exception:
    Geometry = None
from app.config import settings


class Base(DeclarativeBase):
    pass


def _uuid() -> str:
    return uuid.uuid4().hex[:8].upper()


def _now() -> datetime:
    return datetime.now(timezone.utc)

def _geom_type():
    if Geometry is not None and settings.DATABASE_URL.startswith("postgresql"):
        return Geometry(geometry_type="GEOMETRY", srid=4326, spatial_index=True)
    return Text


class Spill(Base):
    __tablename__ = "spills"
    __table_args__ = (Index("idx_spills_created", "created_at"),)

    id = Column(String, primary_key=True, default=lambda: f"OS-{_uuid()}")
    detected_at = Column(DateTime(timezone=True), nullable=False)
    geom_json = Column(Text, nullable=False)          # Portable GeoJSON mirror
    geom = Column(_geom_type(), nullable=True)         # Native PostGIS geometry when PostgreSQL is used
    bbox_min_lon = Column(Float, nullable=True)
    bbox_min_lat = Column(Float, nullable=True)
    bbox_max_lon = Column(Float, nullable=True)
    bbox_max_lat = Column(Float, nullable=True)
    area_km2 = Column(Float, nullable=False)
    centroid_lon = Column(Float, nullable=False)
    centroid_lat = Column(Float, nullable=False)
    confidence = Column(Float, nullable=False)
    satellite = Column(String, default="Sentinel-1")
    source_filename = Column(String, nullable=True)
    using_dummy_model = Column(Boolean, default=False)
    source_type = Column(String, default="upload")
    aws_scene_id = Column(String, nullable=True)
    model_diagnostics_json = Column(Text, nullable=True)
    # M17: server-derived operational data mode (LIVE/ARCHIVE/FALLBACK/SCENARIO/DEGRADED)
    data_mode = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)

    drift_runs = relationship("DriftRun", back_populates="spill", cascade="all, delete-orphan")
    scores = relationship("Score", back_populates="spill", cascade="all, delete-orphan")
    evidence_logs = relationship("EvidenceLog", back_populates="spill", cascade="all, delete-orphan")


class DriftRun(Base):
    __tablename__ = "drift_runs"
    __table_args__ = (Index("idx_drift_runs_spill", "spill_id"),)

    id = Column(String, primary_key=True, default=lambda: f"DR-{_uuid()}")
    spill_id = Column(String, ForeignKey("spills.id"), nullable=False)
    start_time = Column(DateTime(timezone=True), nullable=False)
    end_time = Column(DateTime(timezone=True), nullable=False)
    origin_geom_json = Column(Text, nullable=False)   # GeoJSON Polygon — uncertainty ellipse/circle
    origin_lon = Column(Float, nullable=False)
    origin_lat = Column(Float, nullable=False)
    uncertainty_radius_km = Column(Float, nullable=False)
    particle_count = Column(Integer, nullable=False)
    ensemble_path_json = Column(Text, nullable=True)  # GeoJSON LineString/MultiPoint of final particles
    using_synthetic_environment = Column(Boolean, default=False)
    # M6: origin quality + reproducibility metadata (added via ensure_columns)
    seed = Column(Integer, nullable=True)
    quality_score = Column(Float, nullable=True)
    origin_probability = Column(Float, nullable=True)
    uncertainty_ellipse_json = Column(Text, nullable=True)
    env_mode = Column(String, nullable=True)
    warnings_json = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)

    spill = relationship("Spill", back_populates="drift_runs")


class Vessel(Base):
    __tablename__ = "vessels"

    id = Column(String, primary_key=True, default=lambda: f"V-{_uuid()}")
    mmsi = Column(String, nullable=False, index=True)
    name = Column(String, nullable=True)
    vessel_type = Column(String, nullable=True)       # e.g. tanker, cargo, fishing
    flag = Column(String, nullable=True)
    length_m = Column(Float, nullable=True)

    tracks = relationship("VesselTrack", back_populates="vessel", cascade="all, delete-orphan")


class VesselTrack(Base):
    __tablename__ = "vessel_tracks"
    __table_args__ = (Index("idx_vessel_tracks_vessel_time", "vessel_id", "track_time"), Index("idx_vessel_tracks_spill_vessel", "spill_id", "vessel_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    spill_id = Column(String, ForeignKey("spills.id"), nullable=False, index=True)
    vessel_id = Column(String, ForeignKey("vessels.id"), nullable=False)
    track_time = Column(DateTime(timezone=True), nullable=False)
    lon = Column(Float, nullable=False)
    lat = Column(Float, nullable=False)
    speed_kn = Column(Float, nullable=True)
    heading_deg = Column(Float, nullable=True)

    vessel = relationship("Vessel", back_populates="tracks")
    spill = relationship("Spill")


class AisPosition(Base):
    """M8: global historical AIS position store (cross-investigation).

    Spill-scoped VesselTrack rows remain the per-incident record; this table
    is the queryable history backing track reconstruction. Synthetic
    (SCENARIO/FALLBACK) positions are stored with quality='synthetic' and
    excluded from real-data queries by default.
    """

    __tablename__ = "ais_positions"
    __table_args__ = (
        Index("idx_ais_positions_mmsi_time", "mmsi", "ts"),
        Index("idx_ais_positions_time", "ts"),
        Index("idx_ais_positions_lon_lat_time", "lon", "lat", "ts"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    mmsi = Column(String, nullable=False, index=True)
    identity_key = Column(String, nullable=False, index=True)
    shipname = Column(String, nullable=True)
    vessel_type = Column(String, nullable=True)
    flag = Column(String, nullable=True)
    length_m = Column(Float, nullable=True)
    ts = Column(DateTime(timezone=True), nullable=False)
    lon = Column(Float, nullable=False)
    lat = Column(Float, nullable=False)
    speed_kn = Column(Float, nullable=True)
    heading_deg = Column(Float, nullable=True)
    source = Column(String, nullable=False, default="gfw")
    quality = Column(String, nullable=False, default="live")  # live | synthetic
    created_at = Column(DateTime(timezone=True), default=_now)


class Score(Base):
    __tablename__ = "scores"
    __table_args__ = (Index("idx_scores_spill", "spill_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    spill_id = Column(String, ForeignKey("spills.id"), nullable=False)
    vessel_id = Column(String, ForeignKey("vessels.id"), nullable=False)
    total_score = Column(Float, nullable=False)       # 0-100
    proximity_score = Column(Float, nullable=False)
    timing_score = Column(Float, nullable=False)
    trajectory_score = Column(Float, nullable=False)
    drift_overlap_score = Column(Float, nullable=False)
    behavior_score = Column(Float, nullable=False)
    vessel_type_score = Column(Float, nullable=False)
    rank = Column(Integer, nullable=True)
    # M9: factor availability + actually-applied weights (renormalized)
    unavailable_json = Column(Text, nullable=True)
    applied_weights_json = Column(Text, nullable=True)
    # Release audit: factors scored DEGRADED (real data, approximate) must keep
    # their status end-to-end (API/report), not be re-labeled COMPUTED.
    degraded_json = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)

    spill = relationship("Spill", back_populates="scores")
    vessel = relationship("Vessel")


class EvidenceLog(Base):
    __tablename__ = "evidence_logs"
    __table_args__ = (Index("idx_evidence_spill", "spill_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    spill_id = Column(String, ForeignKey("spills.id"), nullable=False)
    vessel_id = Column(String, ForeignKey("vessels.id"), nullable=False)
    factor = Column(String, nullable=False)           # e.g. "proximity", "timing"
    description = Column(Text, nullable=False)        # human-readable evidence line
    log_type = Column(String, default="info")         # info | positive | negative
    created_at = Column(DateTime(timezone=True), default=_now)

    spill = relationship("Spill", back_populates="evidence_logs")
    vessel = relationship("Vessel")


class Job(Base):
    """Durable pipeline job (M3). Replaces the in-memory job dictionary.

    status: queued | running | done | error | cancelled
    Stage-level state lives in JobStage rows; this row carries the envelope.
    """

    __tablename__ = "jobs"

    id = Column(String, primary_key=True)  # job_{12hex}, matches JOB_ID_RE
    investigation_id = Column(String, nullable=True, index=True)  # spill id, set on completion
    idempotency_key = Column(String, nullable=True, unique=True, index=True)
    status = Column(String, nullable=False, default="queued", index=True)
    stage = Column(String, nullable=True)  # current stage name
    params_json = Column(Text, nullable=True)  # sanitized pipeline params (no file bytes)
    upload_relpath = Column(String, nullable=True)  # data/jobs/<id>/input.* or None
    filename = Column(String, nullable=True)
    partial_json = Column(Text, nullable=True)
    result_json = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    cancel_requested = Column(Boolean, default=False)
    attempts = Column(Integer, default=0)
    max_attempts = Column(Integer, default=3)
    version = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    stages = relationship("JobStage", back_populates="job", cascade="all, delete-orphan")


class JobStage(Base):
    """Durable per-stage lifecycle (M3).

    status: pending | active | done | error | skipped | degraded
    """

    __tablename__ = "job_stages"
    __table_args__ = (Index("idx_job_stages_job_seq", "job_id", "seq"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String, ForeignKey("jobs.id"), nullable=False, index=True)
    stage = Column(String, nullable=False)
    seq = Column(Integer, nullable=False)
    status = Column(String, nullable=False, default="pending")
    detail = Column(Text, nullable=True)
    partial_json = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    attempt = Column(Integer, default=0)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    duration_ms = Column(Integer, nullable=True)

    job = relationship("Job", back_populates="stages")


class ReportArtifact(Base):
    """M14: stored report/export artifacts (URI + checksum + version).

    Bytes live in object storage (or local artifact dir for SIH); this row
    is the durable record. Trajectory/particle-cloud offload is deferred to
    the scaling milestone.
    """

    __tablename__ = "report_artifacts"
    __table_args__ = (Index("idx_artifacts_spill_type", "spill_id", "artifact_type"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    spill_id = Column(String, ForeignKey("spills.id"), nullable=False, index=True)
    artifact_type = Column(String, nullable=False)  # report-pdf | export-geojson | export-csv
    uri = Column(String, nullable=False)
    checksum = Column(String, nullable=False)
    version = Column(Integer, nullable=False, default=1)
    size_bytes = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)
