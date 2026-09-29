"""
Dataset loader for the Refined Deep-SAR Oil Spill (SOS) dataset.

Expected directory layout after extracting the dataset archive:

    ml/datasets/sos_raw/
        images/
            0001.png (or .tif)
            0002.png
            ...
        masks/
            0001.png
            0002.png
            ...

Each image/mask pair must share the same filename stem. Masks are single-
channel with pixel values {0, 255} (or {0, 1}) — 0 = ocean/background,
255/1 = oil spill.

If your extracted copy of the dataset uses a different layout (some SOS
distributions ship train/ and test/ subfolders, or images/masks already
split), point `image_dir` / `mask_dir` directly at the correct folders —
the loader only cares about matching filename stems.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import torch
from PIL import Image
from torch.utils.data import Dataset

logger = logging.getLogger(__name__)

_VALID_EXTENSIONS = (".png", ".tif", ".tiff", ".jpg", ".jpeg")


class SOSOilSpillDataset(Dataset):
    """
    PyTorch Dataset for the Refined Deep-SAR SOS oil-spill segmentation dataset.

    Args:
        image_dir: folder containing SAR input images.
        mask_dir: folder containing corresponding binary masks.
        image_size: (H, W) to resize every sample to, for batching.
        transform: optional callable applied to (image_array, mask_array)
                   returning (image_array, mask_array); use this to inject
                   augmentation (flips, rotations, noise) during training.
        normalize: if True, scale image pixel values to [0, 1] float32.
    """

    def __init__(
        self,
        image_dir: str | Path,
        mask_dir: str | Path,
        image_size: tuple[int, int] = (256, 256),
        transform: Optional[Callable] = None,
        normalize: bool = True,
    ):
        self.image_dir = Path(image_dir)
        self.mask_dir = Path(mask_dir)
        self.image_size = image_size
        self.transform = transform
        self.normalize = normalize

        if not self.image_dir.exists():
            raise FileNotFoundError(f"Image directory not found: {self.image_dir}")
        if not self.mask_dir.exists():
            raise FileNotFoundError(f"Mask directory not found: {self.mask_dir}")

        image_files = {
            p.stem: p for p in self.image_dir.iterdir() if p.suffix.lower() in _VALID_EXTENSIONS
        }
        mask_files = {
            p.stem: p for p in self.mask_dir.iterdir() if p.suffix.lower() in _VALID_EXTENSIONS
        }

        common_stems = sorted(set(image_files) & set(mask_files))
        missing_masks = sorted(set(image_files) - set(mask_files))
        missing_images = sorted(set(mask_files) - set(image_files))

        if missing_masks:
            logger.warning("%d images have no matching mask and were skipped.", len(missing_masks))
        if missing_images:
            logger.warning("%d masks have no matching image and were skipped.", len(missing_images))
        if not common_stems:
            raise RuntimeError(
                "No matching image/mask pairs found. Check that filenames "
                "in image_dir and mask_dir share the same stem."
            )

        self.samples: list[tuple[Path, Path]] = [
            (image_files[stem], mask_files[stem]) for stem in common_stems
        ]
        logger.info("Loaded %d image/mask pairs from %s", len(self.samples), self.image_dir)

    def __len__(self) -> int:
        return len(self.samples)

    def _load_image(self, path: Path) -> np.ndarray:
        img = Image.open(path).convert("L")  # single-band grayscale SAR intensity
        img = img.resize(self.image_size[::-1], resample=Image.BILINEAR)
        arr = np.array(img, dtype=np.float32)
        if self.normalize:
            arr = arr / 255.0
        return arr

    def _load_mask(self, path: Path) -> np.ndarray:
        mask = Image.open(path).convert("L")
        mask = mask.resize(self.image_size[::-1], resample=Image.NEAREST)
        arr = np.array(mask, dtype=np.float32)
        # Binarize regardless of whether source used 0/255 or 0/1 encoding.
        arr = (arr > 0).astype(np.float32)
        return arr

    def __getitem__(self, idx: int) -> tuple[torch.Tensor, torch.Tensor]:
        image_path, mask_path = self.samples[idx]
        image = self._load_image(image_path)
        mask = self._load_mask(mask_path)

        if self.transform is not None:
            image, mask = self.transform(image, mask)

        # (H, W) -> (1, H, W) for single-band input.
        image_tensor = torch.from_numpy(image).unsqueeze(0).float()
        mask_tensor = torch.from_numpy(mask).unsqueeze(0).float()
        return image_tensor, mask_tensor


def build_dataloaders(
    train_image_dir: str | Path,
    train_mask_dir: str | Path,
    val_image_dir: str | Path,
    val_mask_dir: str | Path,
    batch_size: int = 8,
    image_size: tuple[int, int] = (256, 256),
    num_workers: int = 2,
):
    """Convenience factory returning (train_loader, val_loader)."""
    from torch.utils.data import DataLoader

    train_ds = SOSOilSpillDataset(train_image_dir, train_mask_dir, image_size=image_size)
    val_ds = SOSOilSpillDataset(val_image_dir, val_mask_dir, image_size=image_size)

    train_loader = DataLoader(
        train_ds, batch_size=batch_size, shuffle=True, num_workers=num_workers, pin_memory=True
    )
    val_loader = DataLoader(
        val_ds, batch_size=batch_size, shuffle=False, num_workers=num_workers, pin_memory=True
    )
    return train_loader, val_loader


if __name__ == "__main__":
    # Quick manual sanity check: point this at your extracted SOS folders
    # and confirm shapes/ranges look right before wiring up training.
    import sys

    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) != 3:
        print("Usage: python sos_dataset.py <image_dir> <mask_dir>")
        sys.exit(1)

    ds = SOSOilSpillDataset(sys.argv[1], sys.argv[2])
    print(f"Dataset size: {len(ds)}")
    img, mask = ds[0]
    print(f"Image tensor: shape={tuple(img.shape)} dtype={img.dtype} range=({img.min():.3f}, {img.max():.3f})")
    print(f"Mask tensor:  shape={tuple(mask.shape)} dtype={mask.dtype} unique={torch.unique(mask).tolist()}")
