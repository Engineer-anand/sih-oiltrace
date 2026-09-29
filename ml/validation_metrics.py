"""Scientific validation metrics (M16) — pure numpy harnesses.

These compute segmentation/drift/ranking metrics on labeled data. They never
invent numbers: no accuracy appears anywhere unless produced by these
functions on real labeled inputs.
"""
from __future__ import annotations


def _np():
    import numpy as np

    return np


def segmentation_metrics(pred: object, truth: object) -> dict:
    """IoU, Dice, precision, recall, FP, FN for binary masks (M16)."""
    np = _np()
    p = np.asarray(pred).astype(bool)
    t = np.asarray(truth).astype(bool)
    if p.shape != t.shape:
        raise ValueError(f"shape mismatch: {p.shape} vs {t.shape}")
    tp = int(np.logical_and(p, t).sum())
    fp = int(np.logical_and(p, np.logical_not(t)).sum())
    fn = int(np.logical_and(np.logical_not(p), t).sum())
    denom_iou = tp + fp + fn
    denom_dice = 2 * tp + fp + fn
    return {
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "iou": round(tp / denom_iou, 4) if denom_iou else 0.0,
        "dice": round(2 * tp / denom_dice, 4) if denom_dice else 0.0,
        "precision": round(tp / (tp + fp), 4) if (tp + fp) else 0.0,
        "recall": round(tp / (tp + fn), 4) if (tp + fn) else 0.0,
    }


def origin_error_km(origin: tuple[float, float], truth: tuple[float, float]) -> float:
    """Geodesic error between estimated origin and known source (M16)."""
    from pyproj import Geod

    geod = Geod(ellps="WGS84")
    _, _, dist_m = geod.inv(origin[0], origin[1], truth[0], truth[1])
    return round(abs(dist_m) / 1000.0, 3)


def uncertainty_coverage(origin: tuple[float, float], uncertainty_radius_km: float,
                         truth: tuple[float, float]) -> bool:
    """Whether the known source falls inside the estimated uncertainty (M16)."""
    return origin_error_km(origin, truth) <= uncertainty_radius_km


def ranking_stability(scores_a: list[str], scores_b: list[str]) -> dict:
    """Rank-order stability between two runs over the same candidates (M16).

    Inputs are vessel_id lists ordered best-first. Returns top-1 agreement
    and Kendall-tau distance normalized to [0, 1].
    """
    if set(scores_a) != set(scores_b):
        raise ValueError("candidate sets differ; cannot compare rankings")
    n = len(scores_a)
    if n < 2:
        return {"top1_agree": True, "kendall_distance": 0.0}
    pos_b = {v: i for i, v in enumerate(scores_b)}
    discordant = 0
    total = 0
    for i in range(n):
        for j in range(i + 1, n):
            total += 1
            if (pos_b[scores_a[i]] - pos_b[scores_a[j]]) <= 0:
                continue
            discordant += 1
    return {"top1_agree": scores_a[0] == scores_b[0],
            "kendall_distance": round(discordant / total, 4)}
