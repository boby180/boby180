"""Colour correction for underwater photos.

Water absorbs red light first, so underwater photos come out blue/green and
flat. The correction follows the well known recipe of Ancuti et al. (2018):

1. Red channel compensation - red is rebuilt from the green channel, which
   survives better underwater (blue is compensated too for greenish water).
2. Gray-world white balance - removes the remaining colour cast.
3. Contrast stretch (percentile based, gain capped) - removes the haze,
   then a gamma curve to a natural brightness.
4. A mild saturation boost and sharpening.

Originals are never modified: corrected copies are written to a separate
folder, keeping EXIF (date taken, camera, description) so they can still be
matched to the original.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from .scanner import iter_image_files

# Upper limit for the contrast stretch, so dark noisy photos are not over-amplified.
MAX_STRETCH_GAIN = 2.2
# Average brightness (0..1) the corrected photo is brought to.
TARGET_BRIGHTNESS = 0.47

# Formats we can write back with EXIF intact.
OUTPUT_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}


def looks_underwater(img: Image.Image) -> bool:
    """Red clearly weaker than green/blue - the typical underwater colour cast."""
    small = np.asarray(img.convert("RGB").resize((64, 64)), dtype=np.float32)
    r, g, b = (small[..., i].mean() for i in range(3))
    return r < 0.75 * max(g, b) and max(g, b) > 40


def enhance_underwater(img: Image.Image, strength: float = 1.0) -> Image.Image:
    """Return a colour-corrected copy of ``img`` (RGB). ``strength`` 0..1.5 blends with the original."""
    original = np.asarray(img.convert("RGB"), dtype=np.float32) / 255.0
    rgb = original.copy()
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    mean_r, mean_g, mean_b = r.mean(), g.mean(), b.mean()

    # 1. Compensate the attenuated channels using green (Ancuti et al.).
    rgb[..., 0] = r + (mean_g - mean_r) * (1.0 - r) * g
    if mean_b < mean_g:
        rgb[..., 2] = b + (mean_g - mean_b) * (1.0 - b) * g
    rgb = np.clip(rgb, 0.0, 1.0)

    # 2. Gray-world white balance.
    means = rgb.reshape(-1, 3).mean(axis=0)
    rgb = np.clip(rgb * (means.mean() / np.maximum(means, 1e-6)), 0.0, 1.0)

    # 3. Contrast stretch to remove the haze. One shared range for all channels keeps the
    #    white balance from step 2; the gain is capped so noise is not blown up.
    sample = rgb[::4, ::4].reshape(-1, 3)  # a sample is enough and much faster on big photos
    low = np.percentile(sample, 1.0)
    high = np.percentile(sample, 99.0)
    gain = min(1.0 / max(high - low, 1e-6), MAX_STRETCH_GAIN)
    rgb = np.clip((rgb - low) * gain, 0.0, 1.0)

    # Brightness: a gamma curve brings the average luminance to a natural level.
    luminance = (rgb[::4, ::4] @ np.array([0.299, 0.587, 0.114], dtype=np.float32)).mean()
    if 0.01 < luminance < 0.99:
        gamma = float(np.clip(np.log(TARGET_BRIGHTNESS) / np.log(luminance), 0.55, 1.3))
        rgb = np.power(rgb, gamma)

    # Blend with the original according to strength.
    strength = float(np.clip(strength, 0.0, 1.5))
    rgb = np.clip(original + (rgb - original) * strength, 0.0, 1.0)

    out = Image.fromarray((rgb * 255.0 + 0.5).astype(np.uint8), "RGB")
    # 4. Gentle finishing touches.
    out = ImageEnhance.Color(out).enhance(1.0 + 0.1 * strength)
    out = out.filter(ImageFilter.UnsharpMask(radius=1.5, percent=int(40 * strength), threshold=4))
    return out


@dataclass
class EnhanceResult:
    source: str
    target: str
    status: str  # "enhanced", "skipped-not-underwater", "skipped-exists", "failed"
    detail: str = ""


def enhance_file(source: Path, target: Path, strength: float = 1.0, only_underwater: bool = True) -> EnhanceResult:
    if target.exists():
        return EnhanceResult(str(source), str(target), "skipped-exists")
    try:
        with Image.open(source) as img:
            exif = img.info.get("exif")
            icc = img.info.get("icc_profile")
            img = ImageOps.exif_transpose(img)
            if only_underwater and not looks_underwater(img):
                return EnhanceResult(str(source), str(target), "skipped-not-underwater")
            out = enhance_underwater(img, strength)
        if exif:
            # The pixels are already rotated upright, so reset the orientation tag.
            try:
                exif_obj = Image.Exif()
                exif_obj.load(exif)
                exif_obj[0x0112] = 1
                exif = exif_obj.tobytes()
            except Exception:
                exif = None  # damaged metadata: save the corrected photo without it
        target.parent.mkdir(parents=True, exist_ok=True)
        save_kwargs = {"exif": exif} if exif else {}
        if icc:
            save_kwargs["icc_profile"] = icc
        if target.suffix.lower() in (".jpg", ".jpeg"):
            save_kwargs.update(quality=95, subsampling=0)
        out.save(target, **save_kwargs)
        return EnhanceResult(str(source), str(target), "enhanced")
    except Exception as exc:
        return EnhanceResult(str(source), str(target), "failed", str(exc))


def enhance_folder(
    source_root: str | Path,
    output_root: str | Path,
    extensions: Iterable[str],
    exclude: Iterable[str],
    strength: float = 1.0,
    only_underwater: bool = True,
    progress: Callable[[int, int], None] | None = None,
) -> list[EnhanceResult]:
    """Write corrected copies of the images under ``source_root`` to ``output_root``/<folder name>/..."""
    source_root = Path(source_root)
    output_base = Path(output_root) / (source_root.name or "photos")
    files = list(iter_image_files(source_root, extensions, exclude))
    results = []
    for i, path in enumerate(files, 1):
        rel = path.relative_to(source_root)
        target = output_base / rel
        if target.suffix.lower() not in OUTPUT_EXTENSIONS:
            target = target.with_suffix(".jpg")  # e.g. HEIC -> JPEG
        results.append(enhance_file(path, target, strength, only_underwater))
        if progress and (i % 10 == 0 or i == len(files)):
            progress(i, len(files))
    return results
