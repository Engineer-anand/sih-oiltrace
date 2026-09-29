"""Shared SAR preprocessing — canonical implementation lives in
backend/app/preprocessing.py (importable wherever the backend is).
This module re-exports it for training scripts; `ml/train.py` already puts
`backend/` on sys.path. Any behavior change must bump PREPROCESSING_VERSION
in the canonical module and models/manifests/MODEL_VERSION.json.
"""
from __future__ import annotations

try:
    from app.preprocessing import (  # noqa: F401
        PREPROCESSING_VERSION,
        normalize_minmax,
        normalize_unit_scale,
        resize_grayscale,
    )
except ImportError:  # standalone use without backend/ on path
    from backend.app.preprocessing import (  # type: ignore[no-redef]  # noqa: F401
        PREPROCESSING_VERSION,
        normalize_minmax,
        normalize_unit_scale,
        resize_grayscale,
    )
