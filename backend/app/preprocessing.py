"""Shared SAR preprocessing (M-model) — single implementation used by training
and inference so behavior can never drift silently between them.

Versions:
- pp-v1/infer-minmax: per-scene min-max to [0, 1] (inference path).
- pp-v1/train-scale: PNG / 255 unit scale + 256x256 bilinear (training path).

KNOWN PARITY GAP (documented, not hidden): the shipped checkpoint was
trained with train-scale on SOS PNGs but runs inference with infer-minmax
on GRD scenes. Any change that closes this gap MUST bump
PREPROCESSING_VERSION and the model manifest (models/manifests/).
"""
from __future__ import annotations

PREPROCESSING_VERSION = "pp-v1"


def normalize_minmax(arr):
    """Inference normalization (pp-v1): per-scene min-max to [0, 1] float32."""
    import numpy as np

    out = np.asarray(arr, dtype=np.float32)
    lo, hi = float(out.min()), float(out.max())
    if hi > lo:
        return ((out - lo) / (hi - lo)).astype(np.float32)
    return (out * 0.0).astype(np.float32)


def normalize_unit_scale(arr):
    """Training normalization (pp-v1): PNG 0..255 divided to [0, 1] float32."""
    import numpy as np

    return (np.asarray(arr, dtype=np.float32) / 255.0).astype(np.float32)


def resize_grayscale(image, size: tuple[int, int]):
    """Bilinear resize of a 2D grayscale array to (H, W) (pp-v1 training)."""
    import numpy as np
    from PIL import Image as _Image

    img = _Image.fromarray(np.asarray(image))
    if img.mode != "L":
        img = img.convert("L")
    return np.array(img.resize((size[1], size[0]), resample=_Image.BILINEAR), dtype=np.float32)
