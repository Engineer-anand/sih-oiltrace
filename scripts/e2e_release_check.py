#!/usr/bin/env python3
"""OilTrace release E2E validation (final SIH audit, Phases 12-16).

Runs against a live server (default http://localhost:8000) in APP_MODE=sih_demo:

  1. health: APP_MODE reported by the server + trained U-Net loaded
  2. full pipeline job A (local scene upload, ais_source=scenario) while
     consuming the SSE job stream through its `event: terminal`
  3. full pipeline job B with byte-identical input -> determinism comparison
  4. contract checks on the results: server-derived data_mode, UTC Z
     timestamps, honest satellite attribution, structurally synthetic
     SCENARIO-* vessel ids (never plausible MMSIs), motion factors
     UNAVAILABLE under AIS_PRESENCE, applied weights renormalized to 1,
     stored score == recomputation from stored components
  5. report PDF fetch, spills list provenance, SPA entry served
  6. optional real-provider probe: Sentinel-1 STAC search (network dependent,
     reported as WARN when egress is unavailable)

Stdlib only. Exits 0 iff every REQUIRED check passes. Measured stage
durations are reported as-is; no target values are invented here.
"""
from __future__ import annotations

import argparse
import binascii
import json
import re
import struct
import sys
import threading
import time
import urllib.error
import urllib.request
import zlib

TERMINAL = ("done", "error", "cancelled")
FAILURES: list[str] = []
WARNINGS: list[str] = []
CHECKS: list[str] = []

_ISO_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")


def strip_times(value):
    """Replace UTC-Z timestamps with a placeholder.

    Release-window bounds and trajectory times are anchored to each run's
    wall clock, so byte-comparability between identical-input runs is only
    required for the time-independent content (origin geometry,
    uncertainty, factors, ensemble positions, metadata) — the M7 contract.
    """
    if isinstance(value, dict):
        return {k: strip_times(v) for k, v in value.items()}
    if isinstance(value, list):
        return [strip_times(v) for v in value]
    if isinstance(value, str) and _ISO_Z.match(value):
        return "<UTC-Z>"
    return value


def check(name: str, ok: bool, detail: str = "", required: bool = True) -> bool:
    tag = "PASS" if ok else ("WARN" if not required else "FAIL")
    line = f"[{tag}] {name}" + (f" | {detail}" if detail else "")
    print(line, flush=True)
    if ok:
        CHECKS.append(name)
    elif required:
        FAILURES.append(f"{name}: {detail}")
    else:
        WARNINGS.append(f"{name}: {detail}")
    return ok


# ---------------------------------------------------------------- HTTP utils

def request(method: str, url: str, body: bytes | None = None,
            headers: dict | None = None, timeout: float = 30.0):
    req = urllib.request.Request(url, data=body, method=method,
                                 headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read()


def get_json(url: str, timeout: float = 30.0):
    status, _, body = request("GET", url, timeout=timeout)
    if status != 200:
        raise RuntimeError(f"GET {url} -> HTTP {status}: {body[:300]!r}")
    return json.loads(body)


def post_multipart(url: str, fields: dict, file_field: str,
                   filename: str, content: bytes, timeout: float = 60.0):
    boundary = "----oiltracee2e7f3a9"
    parts: list[bytes] = []
    for key, value in fields.items():
        if value is None:
            continue
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; "
            f'name="{key}"\r\n\r\n{value}\r\n'.encode()
        )
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; "
        f'name="{file_field}"; filename="{filename}"\r\n'
        f"Content-Type: image/png\r\n\r\n".encode()
        + content + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    status, _, body = request(
        "POST", url, body=b"".join(parts),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        timeout=timeout,
    )
    if status not in (200, 201):
        raise RuntimeError(f"POST {url} -> HTTP {status}: {body[:500]!r}")
    return json.loads(body)


# ------------------------------------------------------------- deterministic
# Pure-stdlib PNG writer. Three fixed patterns; no RNG anywhere, so the same
# pattern always yields the identical image file.

def _png(width: int, height: int, pixel) -> bytes:
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter: none
        for x in range(width):
            r, g, b = pixel(x, y)
            raw.extend((r, g, b))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", binascii.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b""))


def _speckle(x: int, y: int, lo: int, spread: int) -> int:
    v = ((x * 73856093) ^ (y * 19349663) ^ ((x * y * 2654435761) & 0xFFFFFFFF))
    return lo + (v & 0xFFFF) % (spread + 1)


def scene_pattern(variant: int, size: int = 256) -> bytes:
    cx = cy = size // 2
    rx, ry = size * 0.34, size * 0.20

    if variant == 0:  # dark elongated slick on speckled ocean (classic SAR)
        def pixel(x, y):
            base = _speckle(x, y, 92, 58)
            dx, dy = (x - cx) / rx, (y - cy) / ry
            if dx * dx + dy * dy < 1.0:
                base = 16 + _speckle(x, y, 0, 12)
            return (base, base, base)
    elif variant == 1:  # bright feature on dark water (inverted contrast)
        def pixel(x, y):
            base = _speckle(x, y, 30, 40)
            dx, dy = (x - cx) / rx, (y - cy) / ry
            if dx * dx + dy * dy < 1.0:
                base = 195 + _speckle(x, y, 0, 40)
            return (base, base, base)
    else:  # uniform textured field (max coverage attempt)
        def pixel(x, y):
            base = _speckle(x, y, 110, 30)
            return (base, base, base)

    return _png(size, size, pixel)


# ------------------------------------------------------------------ job flow

def run_job(base: str, image: bytes, bbox: dict, fields: dict,
            with_sse: bool = False, poll_timeout: float = 900.0):
    created = post_multipart(
        f"{base}/api/v1/pipeline/jobs",
        {**bbox, **fields},
        file_field="file", filename="release_e2e_scene.png", content=image,
    )
    job_id = created["job_id"]
    sse: dict = {"events": [], "ids": 0, "terminal": False, "error": None}
    if with_sse:
        def _consume():
            try:
                status, _, raw = request(
                    "GET", f"{base}/api/v1/pipeline/jobs/{job_id}/events",
                    headers={"Accept": "text/event-stream"}, timeout=poll_timeout + 60,
                )
                if status != 200:
                    sse["error"] = f"HTTP {status}"
                    return
                for line in raw.decode("utf-8", "replace").splitlines():
                    if line.startswith("event:"):
                        ev = line.split(":", 1)[1].strip()
                        sse["events"].append(ev)
                        if ev == "terminal":
                            sse["terminal"] = True
                    elif line.startswith("id:"):
                        sse["ids"] += 1
            except Exception as exc:  # noqa: BLE001 - captured for the report
                sse["error"] = str(exc)
        threading.Thread(target=_consume, daemon=True).start()

    deadline = time.monotonic() + poll_timeout
    job = None
    while time.monotonic() < deadline:
        job = get_json(f"{base}/api/v1/pipeline/jobs/{job_id}", timeout=30)
        if job.get("status") in TERMINAL:
            break
        time.sleep(1.0)
    else:
        raise RuntimeError(f"job {job_id} did not reach a terminal state")
    if with_sse:
        sse_deadline = time.monotonic() + 60
        while not sse["terminal"] and time.monotonic() < sse_deadline:
            time.sleep(1.0)
    return job_id, job, sse


# ------------------------------------------------------------------- compare

def score_map(detail: dict) -> dict:
    out = {}
    for v in detail.get("vessels", []):
        out[v["vessel_id"]] = {
            "total": v.get("total_score"),
            "status": {f["factor"]: f["status"] for f in v.get("factor_list", [])},
        }
    return out


from datetime import datetime as _dt  # noqa: E402  (used in window checks)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--expect-mode", default="sih_demo")
    ap.add_argument("--skip-provider-probe", action="store_true")
    args = ap.parse_args()
    base = args.base.rstrip("/")
    summary: dict = {"base": base, "runs": {}, "measured": {}}

    # 1. health -------------------------------------------------------------
    health = get_json(f"{base}/health")
    check("health_status", health.get("status") == "ok", str(health.get("status")))
    check("health_app_mode", health.get("app_mode") == args.expect_mode,
          f"app_mode={health.get('app_mode')}")
    check("trained_model_loaded", bool(health.get("model_loaded")),
          str(health.get("model_loaded")))

    bbox = {"min_lon": 72.05, "min_lat": 18.55, "max_lon": 72.35, "max_lat": 18.85}
    fields = {"ais_source": "scenario", "ais_radius_km": 50, "lookback_hours": 48}

    # 2. run A (calibrate the first variant that yields a spill) ------------
    result_a = job_a = None
    image = None
    for variant in (0, 1, 2):
        image = scene_pattern(variant)
        print(f"[info] pipeline run A with scene pattern #{variant}", flush=True)
        job_id_a, job_a, sse_a = run_job(base, image, bbox, fields, with_sse=True)
        result_a = job_a.get("result") or {}
        if job_a.get("status") == "error":
            break
        if result_a.get("origin") is not None or not result_a:
            summary["runs"]["a_job"] = job_id_a
            summary["runs"]["scene_pattern"] = variant
            break

    check("job_a_done", bool(job_a) and job_a.get("status") == "done",
          f"status={job_a.get('status') if job_a else None} "
          f"stages={ {k: v.get('status') for k, v in (job_a or {}).get('stages', {}).items()} }")
    check("sse_terminal_event", bool(sse_a.get("terminal")),
          f"error={sse_a.get('error')} events={sse_a.get('events')[:6]}")
    check("sse_event_ids_present", sse_a.get("ids", 0) >= 1,
          f"id_lines={sse_a.get('ids')}")

    spill_detected = bool(result_a.get("origin"))
    if job_a and job_a.get("status") == "done" and not spill_detected:
        WARNINGS.append("scene patterns produced no spill; scoring/report "
                        "checks skipped (determinism limited to no-spill stop)")

    detail_a = None
    if spill_detected:
        spill_a = result_a.get("spill_id")
        # 3. run B: byte-identical input ------------------------------------
        print("[info] pipeline run B with identical scene", flush=True)
        job_id_b, job_b, _ = run_job(base, image, bbox, fields)
        result_b = job_b.get("result") or {}
        summary["runs"]["b_job"] = job_id_b
        check("job_b_done", job_b.get("status") == "done",
              f"status={job_b.get('status')}")

        # determinism ------------------------------------------------------
        # Time-anchored fields (release-window bounds, trajectory
        # timestamps) legitimately differ between wall-clock runs;
        # everything else must be byte-identical once UTC-Z timestamps are
        # normalized.
        norm_a = json.dumps(strip_times(result_a.get("origin")), sort_keys=True)
        norm_b = json.dumps(strip_times(result_b.get("origin")), sort_keys=True)
        oa = result_a.get("origin")
        ob = result_b.get("origin")
        if norm_a == norm_b:
            origin_detail = "time-independent fields byte-identical (times normalized)"
        elif isinstance(oa, dict) and isinstance(ob, dict):
            sa, sb = strip_times(oa), strip_times(ob)
            origin_detail = "differing keys=" + repr(sorted(
                k for k in set(sa) | set(sb) if sa.get(k) != sb.get(k)))
        else:
            origin_detail = "normalized payloads differ"
        check("origin_byte_comparable", norm_a == norm_b, origin_detail)
        check("data_mode_scenario_run_a", result_a.get("data_mode") == "SCENARIO",
              str(result_a.get("data_mode")))
        check("data_mode_scenario_run_b", result_b.get("data_mode") == "SCENARIO",
              str(result_b.get("data_mode")))
        check("run_message_identical",
              result_a.get("message") == result_b.get("message"),
              f"a={result_a.get('message')!r} b={result_b.get('message')!r}")

        detail_a = get_json(f"{base}/api/v1/spills/{spill_a}")
        detail_b = get_json(f"{base}/api/v1/spills/{result_b.get('spill_id')}")

        # drift provenance + determinism (detail layer) --------------------
        drift_a = detail_a.get("drift") or {}
        drift_b = detail_b.get("drift") or {}
        check("drift_origin_deterministic",
              json.dumps(drift_a.get("origin")) == json.dumps(drift_b.get("origin")),
              f"a={drift_a.get('origin')} b={drift_b.get('origin')}")
        check("uncertainty_deterministic",
              drift_a.get("uncertainty_radius_km") == drift_b.get("uncertainty_radius_km"),
              f"a={drift_a.get('uncertainty_radius_km')} b={drift_b.get('uncertainty_radius_km')}")
        win_a = drift_a.get("release_window") or {}
        win_b = drift_b.get("release_window") or {}
        check("release_window_utc_z",
              all(str(win_a.get(k, "")).endswith("Z") for k in ("start", "end"))
              and all(str(win_b.get(k, "")).endswith("Z") for k in ("start", "end")),
              json.dumps({"a": win_a, "b": win_b}))

        def _span(window: dict):
            try:
                fmt = lambda s: _dt.fromisoformat(s.replace("Z", "+00:00"))
                return (fmt(window["end"]) - fmt(window["start"])).total_seconds()
            except Exception:  # noqa: BLE001
                return None

        check("release_window_span_deterministic",
              _span(win_a) is not None and _span(win_a) == _span(win_b),
              f"a={_span(win_a)}s b={_span(win_b)}s")
        check("env_mode_scenario",
              drift_a.get("env_mode") == "SCENARIO"
              and drift_b.get("env_mode") == "SCENARIO",
              f"a={drift_a.get('env_mode')} b={drift_b.get('env_mode')}")
        check("scoring_deterministic",
              score_map(detail_a) == score_map(detail_b),
              "candidate totals/factor statuses differ between identical runs")
        check("candidate_set_deterministic",
              sorted(v["vessel_id"] for v in detail_a.get("vessels", []))
              == sorted(v["vessel_id"] for v in detail_b.get("vessels", [])),
              "")
        for label, detail in (("a", detail_a), ("b", detail_b)):
            check(f"timestamps_utc_z_{label}",
                  detail["spill"]["detected_at"].endswith("Z")
                  and "T" in detail["spill"]["detected_at"],
                  str(detail["spill"]["detected_at"]))
            check(f"spill_data_mode_{label}",
                  detail["spill"].get("data_mode") == "SCENARIO",
                  str(detail["spill"].get("data_mode")))
            _check_vessel_contracts(base, detail, label)

        # 4. satellite honesty for an unmissioned filename ------------------
        sat = detail_a["spill"].get("satellite")
        check("satellite_honest_for_unmissioned_upload",
              sat == "Unspecified sensor", str(sat))

        # 5. report + list ---------------------------------------------------
        status, headers, pdf = request("GET", f"{base}/api/v1/report/{spill_a}")
        check("report_pdf_served",
              status == 200 and pdf.startswith(b"%PDF") and len(pdf) > 2000,
              f"http={status} bytes={len(pdf)}")
        listing = get_json(f"{base}/api/v1/spills")
        rows = [r for r in listing if r.get("id") in
                {result_a.get("spill_id"), result_b.get("spill_id")}]
        check("spills_list_provenance",
              len(rows) == 2
              and all(r.get("data_mode") == "SCENARIO" for r in rows)
              and all(str(r.get("detected_at", "")).endswith("Z") for r in rows),
              json.dumps([(r.get("id"), r.get("data_mode"),
                           r.get("detected_at")) for r in rows]))

        # 6. SPA -------------------------------------------------------------
        status, headers, html = request("GET", f"{base}/app/dashboard")
        ctype = next((v for k, v in dict(headers).items()
                      if k.lower() == "content-type"), "")
        check("spa_dashboard_served",
              status == 200 and "text/html" in ctype,
              f"http={status} type={ctype}")

        # measured performance (real numbers only) --------------------------
        for label, job in (("a", job_a), ("b", job_b)):
            summary["measured"][f"job_{label}_stage_seconds"] = {
                k: v.get("duration_seconds") for k, v in job.get("stages", {}).items()
            }
            summary["measured"][f"job_{label}_processing_seconds"] = (
                (job.get("result") or {}).get("processing_seconds"))

    # 7. optional real-provider probe ---------------------------------------
    if not args.skip_provider_probe:
        try:
            scenes = get_json(
                f"{base}/api/v1/sentinel1/search?min_lon=72.0&min_lat=18.5"
                f"&max_lon=72.4&max_lat=19.0&limit=3", timeout=60)
            n = len(scenes) if isinstance(scenes, list) else len(scenes.get("scenes", []))
            check("sentinel1_stac_search", True, f"scenes={n}", required=False)
            summary["provider_probe"] = {"sentinel1_scenes": n}
        except Exception as exc:  # noqa: BLE001
            check("sentinel1_stac_search", False, str(exc), required=False)

    summary["checks_passed"] = len(CHECKS)
    summary["failures"] = FAILURES
    summary["warnings"] = WARNINGS
    print("\n=== E2E SUMMARY ===")
    print(json.dumps(summary, indent=2, default=str))
    if FAILURES:
        print(f"RESULT: FAIL ({len(FAILURES)} required checks failed)")
        return 1
    print("RESULT: PASS")
    return 0


def _check_vessel_contracts(base: str, detail: dict, label: str) -> None:
    vessels = detail.get("vessels", [])
    if not vessels:
        check(f"candidates_present_{label}", False, "no candidates returned")
        return
    plausible = re.compile(r"^\d{9}$")
    bad_ids = [v.get("mmsi") for v in vessels
               if v.get("mmsi") and plausible.match(str(v.get("mmsi")))]
    check(f"no_plausible_mmsi_{label}", not bad_ids, str(bad_ids))
    scenario_ok = all(
        str(v.get("vessel_id", "")).startswith("SCENARIO-")
        or str(v.get("mmsi", "")).startswith("SCENARIO-")
        for v in vessels
    )
    check(f"scenario_ids_{label}", scenario_ok,
          str([v.get("vessel_id") for v in vessels]))
    for v in vessels:
        statuses = {f["factor"]: f["status"] for f in v.get("factor_list", [])}
        check(f"motion_unavailable_{label}_{v['vessel_id']}",
              statuses.get("trajectory") == "UNAVAILABLE"
              and statuses.get("behavior") == "UNAVAILABLE",
              json.dumps(statuses))
        applied = [f for f in v.get("factor_list", [])
                   if f["status"] != "UNAVAILABLE" and f.get("weight") is not None]
        wsum = sum(f["weight"] for f in applied)
        check(f"weights_sum_{label}_{v['vessel_id']}", abs(wsum - 1.0) < 1e-9,
              f"sum={wsum}")
        # Recompute mirrors how the total is itemized: each factor's
        # weighted contribution is rounded to cents from the published
        # weight x raw_score, then summed (report rows use the same
        # arithmetic), so the identity must hold exactly.
        recomputed = round(
            sum(round(f["weight"] * f["raw_score"], 2) for f in applied), 2)
        check(f"score_recompute_{label}_{v['vessel_id']}",
              recomputed == v.get("total_score"),
              f"recomputed={recomputed} stored={v.get('total_score')}")


if __name__ == "__main__":
    sys.exit(main())
