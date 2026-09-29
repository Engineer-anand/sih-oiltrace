# OilTrace — Oil Spill Detection & Attribution System

End-to-end pipeline: **SAR detection → geospatial extraction → backward
drift simulation → AIS vessel matching → attribution scoring →
explainability → dashboard**, one modular monolith.

OilTrace is an **independent research prototype** — not a government
service, and it makes no official-authority claims. Attribution scores
express statistical association; they are investigative evidence, not proof
of responsibility.

## Quick start

```bash
cd oiltrace
cp .env.example .env        # placeholders only — fill what you have
cd backend
python -m venv venv
source venv/bin/activate    # Windows: venv\Scripts\activate
pip install -r requirements.txt
python -m uvicorn app.main:app --app-dir . --host 0.0.0.0 --port 8000
```

Or with Docker Compose:

```bash
docker compose up --build
```

Frontend:

```bash
cd frontend
npm install
npm run build
```

Then open `http://localhost:8000/app/dashboard`. For SIH demos, see
`docs/SIH_DEMO.md` (`cp .env.sih_demo.example .env` for a deterministic
scenario run against a scratch database).

## Application modes

`APP_MODE`: `development` | `sih_demo` | `staging` | `production`.

Server-derived `data_mode` on every result: `LIVE` | `ARCHIVE` |
`FALLBACK` (development only) | `SCENARIO` (sih_demo only) |
`DEGRADED` | `FAILED`. The mode is computed server-side, persisted
(`spills.data_mode`), and shown in the API, UI banners, map legend, and
PDF. Synthetic substitution is refused in staging/production at startup.
Without provider credentials, development serves labeled fallback data;
staging/production fail honestly instead.

## Pipeline

```
SAR image (GeoTIFF/PNG/JPG) or Sentinel-1 scene
    → U-Net segmentation → binary mask                         [Detection]
    → mask → valid polygon → EPSG:4326 → geodesic area/centroid [Geospatial]
    → seeded OpenDrift backward run → clustered origin          [Drift]
    → AIS presence near origin, in release window               [AIS]
    → capability-gated weighted scoring per vessel              [Attribution]
    → evidence checklist with availability statuses             [Evidence]
    → map + pages + PDF/GeoJSON/CSV exports                     [Dashboard]
```

Key endpoints: `POST /api/v1/pipeline/jobs` (async, SSE progress at
`.../jobs/{id}/events`, poll at `.../jobs/{id}`, cancel/retry included),
`POST /api/v1/pipeline/run` (legacy sync), `POST /api/v1/detect`,
`POST /api/v1/drift/simulate`, `POST /api/v1/ais/search`,
`GET /api/v1/ais/history`, `POST /api/v1/scores/compute`,
`POST /api/v1/evidence/build`, `GET /api/v1/spills`,
`GET /api/v1/report/{id}`, `GET /api/v1/sentinel1/search|scene|download`,
`GET /health` (liveness), `GET /api/v1/health/ready`, `GET /metrics`.

## Data sources and honesty rules

| Stage | Real source | Without credentials |
|---|---|---|
| Detection | Trained U-Net (`models/checkpoints/unet_oilspill.pt`) | Startup refuses to pretend; missing checkpoint is a typed error |
| Currents | CMEMS | Mode-gated synthetic field (dev/demo) or honest failure |
| Wind | ERA5/CDS | Same as currents; month-crossing windows handled |
| AIS | Global Fishing Watch **presence** (not tracks) | Same gating; scenario fleet only in sih_demo |

GFW presence carries no speed/heading, so trajectory and behavior factors
are `UNAVAILABLE` (excluded with renormalized weights), never neutral
constants. Scenario/fallback identifiers (`SCENARIO-NN`, `FALLBACK-NN`)
are never mapped to plausible MMSIs anywhere — API, UI, or PDF.

## Attribution scoring — exact formula

```
Total = Σ Weight_i × FactorScore_i over AVAILABLE factors (renormalized)
Proximity 25% · Timing 20% · Trajectory 20% · Drift overlap 15% ·
Behavior 10% · Vessel type 10%
```

Weights live in validated settings (`WEIGHT_*`, must sum to 1.0) and are
the single source consumed by both scoring and reports. Every candidate
exposes per-factor status (`COMPUTED`/`UNAVAILABLE`/`DEGRADED`),
confidence, data quality, and evidence references.

## Project structure

```
oiltrace/
├── backend/app/
│   ├── main.py · config.py · db.py · contracts.py · security.py
│   ├── observability.py
│   ├── api/            detection, drift, ais, scoring, evidence,
│   │                   pipeline (jobs), reports, sentinel1
│   ├── contracts.py    canonical IDs, UTC timestamps, (lon,lat),
│   │                   EPSG:4326, release window, provider metadata,
│   │                   data_mode, scoring weights, RFC-7807 errors
│   ├── models/         spills, drift_runs, vessels, vessel_tracks,
│   │                   ais_positions, scores, evidence_logs,
│   │                   jobs, job_stages, report_artifacts
│   ├── services/       inference, geospatial, ocean_data, drift,
│   │                   ais_service, scoring, evidence, report, storage
│   ├── providers/ais/  GFW presence + scenario fleet behind AISProvider
│   └── security.py     allowlisted SSRF guard, ID validation, optional
│                       API key (X-API-Key), rate limiting
├── backend/tests/      run_all.py + 17 suites (no pytest required)
├── frontend/src/       pages (Landing, Dashboard, Analysis, Detection,
│                       Drift, Tracking, Candidates, History, Reports,
│                       Docs), MapLibre workspace, institutional theme
├── ml/                 train.py, datasets/, validation_metrics.py
├── models/checkpoints/ trained weights + MODEL_VERSION manifest (M-model)
├── docs/SIH_DEMO.md    deterministic demo guide
├── scripts/            backup_postgres.sh, load_smoke.py
└── .github/workflows/ci.yml
```

## Model versioning

`models/manifests/MODEL_VERSION.json` records weights hash,
architecture, preprocessing version, threshold, dataset, training date,
metrics status, and torch/device config. Training and inference share one
preprocessing implementation (`ml/preprocessing.py`). Preprocessing or
threshold changes require a manifest bump. Until real-scene metrics are
measured with `ml/validation_metrics.py`, the model is **not validated**
for operational use — the system says so wherever confidence appears.

## Testing

```bash
cd backend
../backend/venv/Scripts/python tests/run_all.py   # 90 checks, no pytest needed
```

Suites cover contracts, durable jobs, environmental QC, drift
determinism/clustering/quality, AIS capabilities, history, scoring
statuses, terminology lint, stage 4→5 integration, indexing, storage,
observability, scientific harnesses, security (SSRF/traversal/rate
limit/upload), and production boot guards. CI runs compile + tests +
secret scan + frontend build + Docker build on every push. Final
live-stack validation: `backend\venv\Scripts\python
scripts\e2e_release_check.py` runs the full pipeline twice against a
started server and checks determinism, contracts, scoring
recomputation, and the report PDF.

## Deployment

- **Free-tier demo (single container, no credentials)**: build
  `Dockerfile.demo` with `backend/requirements-demo.txt`, `APP_MODE=sih_demo`
  and SQLite. One container serves the API and the built dashboard on the same
  origin, so there is no CORS surface and no second service to run. Configuration
  is codified in [`render.demo.yaml`](render.demo.yaml).
  The demo image omits the heavy live-physics stack (OpenDrift / CMEMS / ERA5)
  because `sih_demo` routes drift through `run_scenario_backward_drift`, which
  needs only numpy and pyproj — that removes the GDAL/NetCDF apt layer and the
  CUDA torch wheel entirely, which is what makes a 512 MB instance viable.
- **SIH (full stack)**: `docker compose up --build` (FastAPI + PostGIS). No paid provider is required for the demo
  path; scenario mode covers it with structural labeling.
- **Production**: Postgres/PostGIS only, explicit CORS, `OILTRACE_API_KEY`,
  all provider credentials, object storage via `OILTRACE_S3_BUCKET`
  (optional; local artifacts otherwise), non-root container, healthchecks,
  retention sweeps run at startup.
- Secrets: `.env.example` holds placeholders only. Rotate any credential
  that was ever committed before use.

### Resource-constrained instances

These settings exist for shared/fractional-vCPU hosts and all default to the
previous behaviour, so leaving them unset changes nothing:

| Setting | Default | Purpose |
|---|---|---|
| `TORCH_NUM_THREADS` | `0` (leave alone) | Set `1` to stop intra-op thread contention |
| `TORCHSCRIPT_TRACE` | `true` | Set `false` to skip the startup trace (saves time + memory) |
| `MODEL_PRELOAD` | `true` | Resolve the model before the server accepts traffic |
| `INFERENCE_TILE_BATCH` | `16` | Lower caps peak activation memory |
| `OILTRACE_DATA_DIR` / `OILTRACE_MODELS_DIR` | repo layout | Relocate runtime data / weights to a mounted volume |

## Observability

Structured logs carry request/job/investigation IDs. `GET /metrics`
exposes counters (jobs, stages, provider failures, synthetic fallbacks,
drift/storage failures) and latency percentiles. `/health` is liveness;
`/api/v1/health/ready` reports per-dependency readiness. Production
alerts on any synthetic fallback (expected count: 0).

## Repository hygiene

`.gitignore` excludes venvs, `node_modules`, `dist/`, `data/`, `*.db`,
`*.pt`, `.env`, `__pycache__`, and archives. Runtime data is never baked
into images. Large trajectory offload to object storage and table
partitioning are deferred until measured bottlenecks require them.
