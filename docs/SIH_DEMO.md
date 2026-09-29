# OilTrace SIH Demo Mode (M17)

Deterministic, honest, one-command demonstration. Scenario outputs are
structurally labeled at every layer and never mix with production rows.

## One-command bootstrap

```bash
cp .env.sih_demo.example .env   # scratch DB: data/oiltrace_demo.db
cd backend && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
cd frontend && npm run build
```

Docker equivalent (PostGIS + app, `APP_MODE=sih_demo`):

```bash
docker compose -f docker-compose.yml -f docker-compose.sih.yml up -d
```

Open `http://localhost:8000/app/dashboard`. The health pill reports
`APP_MODE=sih_demo`; the dashboard defaults new runs to the scenario fleet.

## What is scenario vs real in a demo run

| Layer | Demo behavior |
|---|---|
| Detection | Real trained U-Net on your uploaded scene (or a fixture scene) |
| Drift | Analytic scenario trajectory (seed `DRIFT_SEED`), labeled SCENARIO |
| AIS | Built-in 7-vessel scenario fleet (`SCENARIO-NN`, never plausible MMSIs) |
| Attribution | Same transparent weighted sum; motion factors UNAVAILABLE |
| Provenance | `data_mode=SCENARIO` in API, `spills.data_mode`, UI banners, PDF |

## Determinism

Fixed seeds: drift `DRIFT_SEED`, scenario fleet `20260905`, fallback
location-hash. Two identical demo runs produce byte-comparable origins.

## Namespace separation

- Demo writes go to `data/oiltrace_demo.db` (see `.env.sih_demo.example`).
- Every row carries `data_mode`; filter production queries with
  `WHERE data_mode IS DISTINCT FROM 'SCENARIO'`.
- Never copy the demo database over a production database.

## Offline resilience

With no provider credentials, sih_demo completes end-to-end because every
substitution is explicit scenario data — not silent fallback. If the STAC
search is unreachable, upload a local scene instead; the pipeline needs no
network in scenario mode.

## Failure injections to rehearse

1. Unset `GFW_API_TOKEN` in development → job ends with honest AIS
   `424`/`FALLBACK`, never synthetic winners.
2. Upload a non-image → typed `422`, no traceback in the response.
3. Request `source=scenario` outside sih_demo → `424` at job entry.

## Release E2E check

With a sih_demo server running (e.g. the Docker stack with
`APP_MODE=sih_demo`):

```bash
backend/venv/Scripts/python scripts/e2e_release_check.py --base http://localhost:8000
```

Runs the pipeline twice on a byte-identical local scene and asserts the
release contracts: terminal SSE event, byte-comparable drift origins,
identical scoring, `data_mode=SCENARIO` end-to-end, UTC `Z` timestamps,
`Unspecified sensor` satellite attribution for an unmissioned filename,
`SCENARIO-*` vessel ids (no plausible MMSIs), motion factors UNAVAILABLE,
applied weights summing to 1, stored score == recomputation, report PDF,
and measured stage durations (reported as-is, no invented targets).
Exit 0 means every required check passed.
