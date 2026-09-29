"""
Inference service.

Wraps the U-Net model so the API layer never touches PyTorch directly.
On startup it tries to load a trained checkpoint from
`models/checkpoints/unet_oilspill.pt`. The shipped checkpoint is mandatory in the final build; there is no dummy detector fallback.
"""

from __future__ import annotations

import logging

import numpy as np
import torch
from PIL import Image

from app.config import settings
from app.errors import RealDataUnavailableError
from app.models.unet import UNet

logger = logging.getLogger(__name__)

CHECKPOINT_PATH = settings.MODELS_DIR / "unet_oilspill.pt"
MANIFEST_PATH = settings.MODELS_DIR.parent / "manifests" / "MODEL_VERSION.json"
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")

# Confine intra-op parallelism when asked. On a fractional-vCPU free instance
# the default pool is sized from the host's core count and causes contention
# (and extra per-thread memory) without making inference any faster.
if settings.TORCH_NUM_THREADS > 0:
    try:
        torch.set_num_threads(int(settings.TORCH_NUM_THREADS))
        logger.info("MODEL OPTIMIZATION | torch intra-op threads pinned to %d", settings.TORCH_NUM_THREADS)
    except Exception:  # pragma: no cover - defensive only
        logger.warning("MODEL OPTIMIZATION | could not pin torch threads", exc_info=True)


def load_model_manifest() -> dict:
    """Read the model manifest (M-model); unknown fields when absent."""
    import json

    try:
        with open(MANIFEST_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

class OilSpillDetector:
    """Loads (or falls back for) the segmentation model and runs inference."""

    def __init__(self, checkpoint_path=CHECKPOINT_PATH, in_channels: int = 1):
        self.checkpoint_path = checkpoint_path
        self.in_channels = in_channels
        self.model: UNet | None = None
        self.traced_model = None
        self.using_dummy = False
        self.last_mean_confidence: float = 0.5
        self.last_probability = None
        self.last_model_input = None
        self.last_threshold = 0.5
        self.last_positive_pixels = 0
        self.last_positive_percent = 0.0
        self.last_probability_min = 0.0
        self.last_probability_max = 0.0
        self.last_probability_mean = 0.0
        self.last_prediction_rejected = False
        self.last_guard_reason: str | None = None
        self.manifest = load_model_manifest()
        self.model_version = str(self.manifest.get("model_version", "unknown"))
        self.preprocessing_version = str(self.manifest.get("preprocessing_version", "unknown"))
        self._load()

    def _load(self) -> None:
        if not self.checkpoint_path.exists():
            msg = f"Trained U-Net checkpoint not found at {self.checkpoint_path}."
            logger.error("MODEL ERROR | %s", msg)
            raise RuntimeError(msg + " OilTrace is configured to use the trained model only.")
        try:
            model = UNet(in_channels=self.in_channels, out_channels=1)
            state_dict = torch.load(self.checkpoint_path, map_location=DEVICE, weights_only=True)
            model.load_state_dict(state_dict)
            model.to(DEVICE)
            model.eval()
            self.model = model
            self.using_dummy = False
            if settings.TORCHSCRIPT_TRACE:
                try:
                    dummy_input = torch.zeros((1, self.in_channels, 256, 256), dtype=torch.float32, device=DEVICE)
                    with torch.inference_mode():
                        self.traced_model = torch.jit.trace(model, dummy_input)
                        self.traced_model.eval()
                    logger.info("MODEL OPTIMIZATION | TorchScript JIT tracing enabled for U-Net")
                except Exception as trace_exc:
                    logger.warning("MODEL OPTIMIZATION | TorchScript JIT trace failed, using eager model: %s", trace_exc)
                    self.traced_model = None
            else:
                # Free-tier profile: skip tracing. It costs a second copy of the
                # graph in memory plus a warm-up pass at startup, and buys
                # nothing for single-image, small-tile inference.
                self.traced_model = None
                logger.info("MODEL OPTIMIZATION | TorchScript tracing disabled by config; using eager model")
            logger.info("MODEL SUCCESS | trained U-Net loaded | checkpoint=%s | device=%s", self.checkpoint_path, DEVICE)
        except Exception as exc:
            logger.exception("MODEL ERROR | checkpoint load failed")
            raise RuntimeError(f"Trained U-Net checkpoint could not be loaded: {exc}") from exc

    def _forward(self, tensor: torch.Tensor) -> torch.Tensor:
        m = self.traced_model if self.traced_model is not None else self.model
        return m(tensor)

    def predict(self, image: np.ndarray) -> np.ndarray:
        """
        Run oil-spill segmentation on a single-band normalized image.

        Args:
            image: 2D float32 array, values in [0, 1], shape (H, W).

        Returns:
            2D uint8 binary mask, same shape as input: 1 = oil, 0 = background.
        """
        if image.ndim != 2:
            raise ValueError(f"Expected a 2D single-band image, got shape {image.shape}")

        if self.model is None:
            raise RuntimeError("Trained U-Net model is unavailable.")
        return self._predict_unet(image)

    def _predict_unet(self, image: np.ndarray) -> np.ndarray:
        """Run tiled U-Net inference while preserving source image geometry."""
        tile = int(settings.INFERENCE_TILE_SIZE)
        overlap = min(int(settings.INFERENCE_TILE_OVERLAP), tile // 2)
        stride = max(1, tile - overlap)
        src_h, src_w = image.shape

        if src_h <= tile and src_w <= tile:
            model_input = image.astype(np.float32)
            padded = np.pad(model_input, ((0, tile-src_h if src_h < tile else 0),(0, tile-src_w if src_w < tile else 0)), mode='edge')
            tensor = torch.from_numpy(padded[:tile,:tile]).float().unsqueeze(0).unsqueeze(0).to(DEVICE)
            with torch.inference_mode():
                probs = torch.sigmoid(self._forward(tensor))[0,0].cpu().numpy()[:src_h,:src_w]
            probability = probs.astype(np.float32)
        else:
            probability = np.zeros((src_h, src_w), dtype=np.float32)
            weights = np.zeros((src_h, src_w), dtype=np.float32)
            batches = []
            locs = []
            tile_batch_size = max(1, int(settings.INFERENCE_TILE_BATCH))
            for y in range(0, src_h, stride):
                for x in range(0, src_w, stride):
                    y2 = min(y + tile, src_h)
                    x2 = min(x + tile, src_w)
                    y1 = max(0, y2 - tile)
                    x1 = max(0, x2 - tile)
                    patch = image[y1:y2, x1:x2]
                    if patch.shape != (tile, tile):
                        patch = np.pad(patch, ((0, tile - patch.shape[0]), (0, tile - patch.shape[1])), mode='edge')
                    batches.append(patch.astype(np.float32))
                    locs.append((y1, y2, x1, x2))
                    if len(batches) >= tile_batch_size:
                        self._accumulate_batch(batches, locs, probability, weights, tile)
                        batches = []
                        locs = []
            if batches:
                self._accumulate_batch(batches, locs, probability, weights, tile)
            probability /= np.maximum(weights, 1e-6)

        mask=(probability>0.5).astype(np.uint8)
        self.last_prediction_rejected = False
        self.last_guard_reason = None
        # A near-total positive mask is a model/input failure, not a credible
        # slick. Refuse to turn it into a scene-sized red polygon.
        positive_percent = float(mask.mean() * 100.0)
        if positive_percent >= 90.0:
            logger.warning(
                "MODEL GUARD | rejected scene-sized mask | positive_percent=%.2f",
                positive_percent,
            )
            mask = np.zeros_like(mask, dtype=np.uint8)
            self.last_prediction_rejected = True
            self.last_guard_reason = "scene-sized positive mask; checkpoint/preprocessing calibration required"
        pos=mask.astype(bool)
        self.last_mean_confidence=float(probability[pos].mean()) if pos.any() else float(1.0-probability.mean())
        self.last_probability=probability
        self.last_model_input=image.astype(np.float32)
        self.last_threshold=0.5
        self.last_positive_pixels=int(mask.sum())
        self.last_positive_percent=float(mask.mean()*100.0)
        self.last_probability_min=float(probability.min())
        self.last_probability_max=float(probability.max())
        self.last_probability_mean=float(probability.mean())
        return mask

    def _accumulate_batch(self, patches, locs, probability, weights, tile):
        tensor = torch.from_numpy(np.stack(patches)).float().unsqueeze(1).to(DEVICE)
        with torch.inference_mode():
            preds = torch.sigmoid(self._forward(tensor)).squeeze(1).cpu().numpy()
        for pred, (y1, y2, x1, x2) in zip(preds, locs):
            h = y2 - y1
            w = x2 - x1
            probability[y1:y2,x1:x2]+=pred[:h,:w]
            weights[y1:y2,x1:x2]+=1.0

_detector_singleton: OilSpillDetector | None = None

def get_detector() -> OilSpillDetector:
    global _detector_singleton
    if _detector_singleton is None:
        _detector_singleton = OilSpillDetector()
    return _detector_singleton
