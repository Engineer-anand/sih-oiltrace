"""OilTrace backend test runner (M16).

Runs every backend/tests/test_*.py without requiring pytest (the SIH venv
has no test framework installed). Each module is imported with backend/tests
on sys.path; every test_* function runs in definition isolation (fresh
imports per module). Exit code 0 = all pass, 1 = any failure.

CI runs this; developers adopting pytest can run the same files with it
(pytest discovers test_* functions natively).
"""
import os
import sys
import traceback

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TESTS_DIR)
os.environ.setdefault("APP_MODE", "development")

# M16: must precede every app import — the settings singleton captures
# DATABASE_URL once, and db_models selects portable TEXT geometry iff the URL
# is SQLite at that moment. Per-module URLs still isolate each suite's data.
_RUN_TMP = os.path.join(__import__("tempfile").mkdtemp(prefix="oiltrace_run_"), "run_all.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_RUN_TMP}"

MODULES = [
    "test_contracts",
    "test_jobs",
    "test_env",
    "test_drift",
    "test_ais",
    "test_history",
    "test_scoring",
    "test_attribution",
    "test_integration_stage45",
    "test_indexing",
    "test_storage",
    "test_observability",
    "test_validation",
    "test_security",
    "test_model",
    "test_production",
    "test_release_audit",
]


def main() -> int:
    failures: list[str] = []
    counts: dict[str, int] = {}
    for name in MODULES:
        try:
            module = __import__(name)
        except Exception:
            print(f"[ERROR] import {name}")
            traceback.print_exc()
            failures.append(f"{name}::import")
            continue
        ran = 0
        for attr in dir(module):
            if not attr.startswith("test_"):
                continue
            try:
                getattr(module, attr)()
                ran += 1
            except Exception:
                print(f"[FAIL] {name}::{attr}")
                traceback.print_exc()
                failures.append(f"{name}::{attr}")
        counts[name] = ran
        print(f"[ok] {name}: {ran} passed")
    total = sum(counts.values())
    print(f"\n{total} tests passed, {len(failures)} failed")
    for f in failures:
        print("FAILED:", f)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
