"""M16 validation tests — scientific harnesses on synthetic labeled inputs."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
os.environ.setdefault("APP_MODE", "development")

from ml.validation_metrics import (  # noqa: E402
    origin_error_km,
    ranking_stability,
    segmentation_metrics,
    uncertainty_coverage,
)


def test_segmentation_metrics_known_values():
    pred = np.zeros((10, 10), dtype=np.uint8)
    truth = np.zeros((10, 10), dtype=np.uint8)
    pred[0:5, 0:5] = 1
    truth[2:7, 2:7] = 1
    m = segmentation_metrics(pred, truth)
    # overlap 3x3=9; pred 25, truth 25 -> union 41
    assert (m["true_positives"], m["false_positives"], m["false_negatives"]) == (9, 16, 16)
    assert m["iou"] == round(9 / 41, 4)
    assert m["dice"] == round(18 / 50, 4)
    assert m["precision"] == round(9 / 25, 4) == m["recall"]
    try:
        segmentation_metrics(np.zeros((4, 4)), np.zeros((3, 3)))
        raise AssertionError("expected shape error")
    except ValueError:
        pass


def test_segmentation_perfect_and_empty():
    m = segmentation_metrics(np.ones((4, 4)), np.ones((4, 4)))
    assert (m["iou"], m["dice"], m["precision"], m["recall"]) == (1.0, 1.0, 1.0, 1.0)
    m0 = segmentation_metrics(np.zeros((4, 4)), np.zeros((4, 4)))
    assert (m0["iou"], m0["dice"]) == (0.0, 0.0)


def test_origin_error_and_coverage():
    err = origin_error_km((72.3, 18.7), (72.3, 18.7))
    assert err == 0.0
    err2 = origin_error_km((72.3, 18.7), (72.4, 18.7))
    assert 5.0 < err2 < 20.0
    assert uncertainty_coverage((72.3, 18.7), err2 + 1.0, (72.4, 18.7)) is True
    assert uncertainty_coverage((72.3, 18.7), 0.5, (72.4, 18.7)) is False


def test_ranking_stability():
    r = ranking_stability(["a", "b", "c"], ["a", "b", "c"])
    assert r == {"top1_agree": True, "kendall_distance": 0.0}
    r2 = ranking_stability(["a", "b", "c"], ["b", "a", "c"])
    assert r2["top1_agree"] is False and r2["kendall_distance"] == round(1 / 3, 4)
    try:
        ranking_stability(["a"], ["a", "b"])
        raise AssertionError("expected candidate-set error")
    except ValueError:
        pass
