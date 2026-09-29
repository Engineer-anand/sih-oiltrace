"""
Phase 1 training script — trains the U-Net oil-spill segmentation model.

Usage:
    python ml/train.py \
        --train-images data/sos/train/images --train-masks data/sos/train/masks \
        --val-images   data/sos/val/images   --val-masks   data/sos/val/masks \
        --epochs 50 --batch-size 8 --lr 1e-4

Dataset: Refined Deep-SAR Oil Spill (SOS) dataset — same one you already
extracted for milestone 1 (Zenodo 15298010). Split it 80/20 into
train/ and val/ folders (images/ + masks/ each) before running this.

Output: models/checkpoints/unet_oilspill.pt
    The FastAPI backend (app/services/inference.py) auto-loads this file
    on next restart. No code changes needed anywhere else.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

import torch
import torch.nn as nn
from torch.optim import Adam
from torch.optim.lr_scheduler import ReduceLROnPlateau

# Allow running as `python ml/train.py` from the project root.
sys.path.append(str(Path(__file__).resolve().parents[1] / "backend"))

from app.models.unet import UNet  # noqa: E402
from ml.datasets.sos_dataset import build_dataloaders  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

CHECKPOINT_DIR = Path(__file__).resolve().parents[1] / "models" / "checkpoints"
CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
BEST_CKPT = CHECKPOINT_DIR / "unet_oilspill.pt"
LAST_CKPT = CHECKPOINT_DIR / "unet_oilspill_last.pt"

DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")


def dice_coefficient(preds: torch.Tensor, targets: torch.Tensor, eps: float = 1e-6) -> torch.Tensor:
    """Dice score on binary masks — the standard metric for segmentation quality.
    IoU/accuracy are misleading here because oil pixels are a small minority
    class (mostly ocean background), so Dice is what you should watch, not loss alone.
    """
    preds = (torch.sigmoid(preds) > 0.5).float()
    intersection = (preds * targets).sum(dim=(1, 2, 3))
    union = preds.sum(dim=(1, 2, 3)) + targets.sum(dim=(1, 2, 3))
    dice = (2 * intersection + eps) / (union + eps)
    return dice.mean()


def run_epoch(model, loader, criterion, optimizer=None) -> tuple[float, float]:
    is_train = optimizer is not None
    model.train() if is_train else model.eval()

    total_loss, total_dice, n_batches = 0.0, 0.0, 0
    context = torch.enable_grad() if is_train else torch.no_grad()

    with context:
        for images, masks in loader:
            images, masks = images.to(DEVICE), masks.to(DEVICE)

            logits = model(images)
            loss = criterion(logits, masks)

            if is_train:
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()

            total_loss += loss.item()
            total_dice += dice_coefficient(logits, masks).item()
            n_batches += 1

    return total_loss / n_batches, total_dice / n_batches


def main():
    parser = argparse.ArgumentParser(description="Train U-Net for SAR oil-spill segmentation")
    parser.add_argument("--train-images", required=True)
    parser.add_argument("--train-masks", required=True)
    parser.add_argument("--val-images", required=True)
    parser.add_argument("--val-masks", required=True)
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--lr", type=float, default=1e-4)
    parser.add_argument("--image-size", type=int, default=256)
    parser.add_argument("--patience", type=int, default=8, help="Early stopping patience (epochs)")
    args = parser.parse_args()

    logger.info("Device: %s", DEVICE)

    train_loader, val_loader = build_dataloaders(
        args.train_images, args.train_masks,
        args.val_images, args.val_masks,
        batch_size=args.batch_size,
        image_size=(args.image_size, args.image_size),
    )
    logger.info("Train batches: %d | Val batches: %d", len(train_loader), len(val_loader))

    # in_channels=1 matches sos_dataset.py (single-band grayscale SAR).
    # If you later train on VV+VH dual-pol GeoTIFFs, change this to 2
    # in BOTH here and inference.py's OilSpillDetector(in_channels=...).
    model = UNet(in_channels=1, out_channels=1, base_filters=32, bilinear=True).to(DEVICE)

    criterion = nn.BCEWithLogitsLoss()
    optimizer = Adam(model.parameters(), lr=args.lr)
    scheduler = ReduceLROnPlateau(optimizer, mode="max", factor=0.5, patience=3)

    best_val_dice = 0.0
    epochs_without_improvement = 0

    for epoch in range(1, args.epochs + 1):
        t0 = time.time()
        train_loss, train_dice = run_epoch(model, train_loader, criterion, optimizer)
        val_loss, val_dice = run_epoch(model, val_loader, criterion, optimizer=None)
        scheduler.step(val_dice)
        elapsed = time.time() - t0

        logger.info(
            "Epoch %02d/%d | train_loss=%.4f train_dice=%.4f | val_loss=%.4f val_dice=%.4f | %.1fs",
            epoch, args.epochs, train_loss, train_dice, val_loss, val_dice, elapsed,
        )

        torch.save(model.state_dict(), LAST_CKPT)

        if val_dice > best_val_dice:
            best_val_dice = val_dice
            epochs_without_improvement = 0
            torch.save(model.state_dict(), BEST_CKPT)
            logger.info("  -> New best val_dice=%.4f, saved to %s", val_dice, BEST_CKPT)
        else:
            epochs_without_improvement += 1
            if epochs_without_improvement >= args.patience:
                logger.info("Early stopping: no improvement for %d epochs.", args.patience)
                break

    logger.info("Training done. Best val_dice=%.4f. Checkpoint: %s", best_val_dice, BEST_CKPT)
    logger.info("Restart the FastAPI backend to auto-load this checkpoint.")


if __name__ == "__main__":
    main()
