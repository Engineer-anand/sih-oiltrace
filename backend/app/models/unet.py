"""
U-Net architecture for binary SAR oil-spill segmentation.

Input:  (B, in_channels, H, W)  — in_channels=2 for VV/VH SAR polarizations,
                                   or 1 if only a single grayscale band is available.
Output: (B, 1, H, W)            — raw logits. Apply torch.sigmoid() to get
                                   a per-pixel probability of "oil" in [0, 1].

This is a standard encoder-decoder U-Net with skip connections, batch norm,
and bilinear upsampling. It is intentionally dependency-light (pure PyTorch)
so it can run in the FastAPI process without extra ML-serving infrastructure.
"""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F


class DoubleConv(nn.Module):
    """(Conv2d -> BatchNorm -> ReLU) x 2"""

    def __init__(self, in_channels: int, out_channels: int, mid_channels: int | None = None):
        super().__init__()
        if mid_channels is None:
            mid_channels = out_channels
        self.block = nn.Sequential(
            nn.Conv2d(in_channels, mid_channels, kernel_size=3, padding=1, bias=False),
            nn.BatchNorm2d(mid_channels),
            nn.ReLU(inplace=True),
            nn.Conv2d(mid_channels, out_channels, kernel_size=3, padding=1, bias=False),
            nn.BatchNorm2d(out_channels),
            nn.ReLU(inplace=True),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.block(x)


class Down(nn.Module):
    """Downscaling with maxpool then double conv."""

    def __init__(self, in_channels: int, out_channels: int):
        super().__init__()
        self.block = nn.Sequential(
            nn.MaxPool2d(2),
            DoubleConv(in_channels, out_channels),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.block(x)


class Up(nn.Module):
    """Upscaling then double conv, with skip connection concatenation."""

    def __init__(self, in_channels: int, out_channels: int, bilinear: bool = True):
        super().__init__()
        if bilinear:
            self.up = nn.Upsample(scale_factor=2, mode="bilinear", align_corners=True)
            self.conv = DoubleConv(in_channels, out_channels, mid_channels=in_channels // 2)
        else:
            self.up = nn.ConvTranspose2d(in_channels, in_channels // 2, kernel_size=2, stride=2)
            self.conv = DoubleConv(in_channels, out_channels)

    def forward(self, x1: torch.Tensor, x2: torch.Tensor) -> torch.Tensor:
        x1 = self.up(x1)
        # Pad x1 so its spatial dims match x2 exactly (handles odd input sizes).
        diff_y = x2.size(2) - x1.size(2)
        diff_x = x2.size(3) - x1.size(3)
        x1 = F.pad(x1, [diff_x // 2, diff_x - diff_x // 2, diff_y // 2, diff_y - diff_y // 2])
        x = torch.cat([x2, x1], dim=1)
        return self.conv(x)


class OutConv(nn.Module):
    def __init__(self, in_channels: int, out_channels: int):
        super().__init__()
        self.conv = nn.Conv2d(in_channels, out_channels, kernel_size=1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.conv(x)


class UNet(nn.Module):
    """
    Standard U-Net, depth 4.

    Args:
        in_channels: number of input bands (2 for VV+VH, 1 for single-band).
        out_channels: number of output classes. 1 for binary oil/no-oil
                      segmentation trained with BCEWithLogitsLoss.
        base_filters: number of filters in the first encoder block; doubles
                      at each downsampling stage.
        bilinear: use bilinear upsampling (lighter, no learned upsampling
                  artifacts) instead of transposed convolutions.
    """

    def __init__(
        self,
        in_channels: int = 2,
        out_channels: int = 1,
        base_filters: int = 32,
        bilinear: bool = True,
    ):
        super().__init__()
        self.in_channels = in_channels
        self.out_channels = out_channels
        self.bilinear = bilinear

        f = base_filters
        factor = 2 if bilinear else 1

        self.inc = DoubleConv(in_channels, f)
        self.down1 = Down(f, f * 2)
        self.down2 = Down(f * 2, f * 4)
        self.down3 = Down(f * 4, f * 8)
        self.down4 = Down(f * 8, f * 16 // factor)

        self.up1 = Up(f * 16, f * 8 // factor, bilinear)
        self.up2 = Up(f * 8, f * 4 // factor, bilinear)
        self.up3 = Up(f * 4, f * 2 // factor, bilinear)
        self.up4 = Up(f * 2, f, bilinear)
        self.outc = OutConv(f, out_channels)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x1 = self.inc(x)
        x2 = self.down1(x1)
        x3 = self.down2(x2)
        x4 = self.down3(x3)
        x5 = self.down4(x4)

        x = self.up1(x5, x4)
        x = self.up2(x, x3)
        x = self.up3(x, x2)
        x = self.up4(x, x1)
        logits = self.outc(x)
        return logits

    @torch.no_grad()
    def predict_mask(self, x: torch.Tensor, threshold: float = 0.5) -> torch.Tensor:
        """Convenience wrapper: forward pass + sigmoid + threshold -> binary mask (0/1)."""
        self.eval()
        logits = self.forward(x)
        probs = torch.sigmoid(logits)
        return (probs > threshold).float()
