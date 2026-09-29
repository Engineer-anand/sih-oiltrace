"""M-model tests — manifest integrity + shared preprocessing parity."""
import hashlib
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("APP_MODE", "development")

REPO = os.path.join(os.path.dirname(__file__), "..", "..")


def test_manifest_valid_and_hash_matches():
    path = os.path.join(REPO, "models", "manifests", "MODEL_VERSION.json")
    with open(path, encoding="utf-8") as fh:
        manifest = json.load(fh)
    for key in ("model_version", "weights_sha256", "architecture", "preprocessing_version",
                "threshold", "dataset", "metrics_status"):
        assert key in manifest, f"missing {key}"
    assert manifest["metrics_status"] == "not-validated"
    weights = os.path.join(REPO, "models", "checkpoints", manifest["weights_file"])
    h = hashlib.sha256()
    with open(weights, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    assert h.hexdigest() == manifest["weights_sha256"]
    assert manifest["weights_bytes"] == os.path.getsize(weights)


def test_preprocessing_parity_and_version():
    from app.preprocessing import (
        PREPROCESSING_VERSION,
        normalize_minmax,
        normalize_unit_scale,
    )

    assert PREPROCESSING_VERSION == "pp-v1"
    arr = np.array([[0.0, 5.0], [10.0, 10.0]], dtype=np.float32)
    out = normalize_minmax(arr)
    assert out.dtype == np.float32
    assert float(out.min()) == 0.0 and float(out.max()) == 1.0
    assert float(out[0, 1]) == 0.5
    flat = np.full((3, 3), 7.0, dtype=np.float32)
    assert float(normalize_minmax(flat).max()) == 0.0
    scaled = normalize_unit_scale(np.full((2, 2), 255.0))
    assert float(scaled.max()) == 1.0
    # ml shim exposes the same implementation
    sys.path.insert(0, REPO)
    try:
        import ml.preprocessing as mlp

        assert mlp.PREPROCESSING_VERSION == PREPROCESSING_VERSION
        assert float(mlp.normalize_minmax(arr).max()) == 1.0
    finally:
        sys.path.remove(REPO)
