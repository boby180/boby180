"""Discovering image files and extracting their fingerprint and metadata.

For every image we collect three layers of information:
  1. Basic file data: name, size, modification time.
  2. Content fingerprints: SHA-256 of the bytes (exact copy) and a perceptual
     hash (pHash) of the pixels, which stays almost the same when a photo was
     resized, re-compressed or had its metadata edited.
  3. Descriptive metadata: date taken, camera, dimensions, and the textual
     description/title/keywords stored in EXIF or XMP.
"""

from __future__ import annotations

import hashlib
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Callable, Iterable, Iterator

from PIL import Image, ImageOps

from .cache import ScanCache

try:  # Optional HEIC/HEIF (iPhone) support.
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:  # pragma: no cover - depends on environment
    pass

try:
    import imagehash
except ImportError:  # pragma: no cover - depends on environment
    imagehash = None

# Bump when the extracted fields change, so cached entries are recomputed.
SCHEMA_VERSION = 1

# EXIF tag ids
_TAG_IMAGE_DESCRIPTION = 0x010E
_TAG_MAKE = 0x010F
_TAG_MODEL = 0x0110
_TAG_ORIENTATION = 0x0112
_TAG_DATETIME = 0x0132
_TAG_XP_TITLE = 0x9C9B
_TAG_XP_COMMENT = 0x9C9C
_TAG_XP_KEYWORDS = 0x9C9E
_TAG_XP_SUBJECT = 0x9C9F
_IFD_EXIF = 0x8769
_TAG_DATETIME_ORIGINAL = 0x9003
_TAG_USER_COMMENT = 0x9286

_XMP_FIELDS = {
    "title": re.compile(rb"<dc:title>(.*?)</dc:title>", re.S),
    "description": re.compile(rb"<dc:description>(.*?)</dc:description>", re.S),
    "keywords": re.compile(rb"<dc:subject>(.*?)</dc:subject>", re.S),
}
_XML_TAG = re.compile(rb"<[^>]+>")


@dataclass
class ImageInfo:
    path: str
    rel_path: str
    root: str
    name: str
    size: int
    mtime: float
    sha256: str = ""
    phash: str | None = None
    width: int | None = None
    height: int | None = None
    taken: str | None = None
    camera: str | None = None
    title: str | None = None
    description: str | None = None
    keywords: str | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ImageInfo":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in names})


def iter_image_files(root: str | Path, extensions: Iterable[str], exclude: Iterable[str]) -> Iterator[Path]:
    """Yield image files under ``root``, skipping excluded folder/file names."""
    exts = {e.lower() for e in extensions}
    excluded = {e.lower() for e in exclude}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d.lower() not in excluded and not d.startswith("."))
        for filename in sorted(filenames):
            if filename.lower() in excluded or filename.startswith("._"):
                continue
            if os.path.splitext(filename)[1].lower() in exts:
                yield Path(dirpath) / filename


def _clean_text(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        # XP* tags are UTF-16LE; UserComment starts with an 8 byte charset header.
        if value[:8] in (b"ASCII\x00\x00\x00", b"UNICODE\x00", b"\x00" * 8):
            header, value = value[:8], value[8:]
            encoding = "utf-16" if header.startswith(b"UNICODE") else "utf-8"
            text = value.decode(encoding, errors="ignore")
        else:
            try:
                text = value.decode("utf-16-le") if b"\x00" in value else value.decode("utf-8")
            except UnicodeDecodeError:
                text = value.decode("latin-1", errors="ignore")
    elif isinstance(value, (tuple, list)):
        # Some readers return XP tags as tuples of byte values.
        return _clean_text(bytes(value))
    else:
        text = str(value)
    text = text.replace("\x00", "").strip()
    return text or None


def _xmp_text(xmp: bytes, field: str) -> str | None:
    match = _XMP_FIELDS[field].search(xmp)
    if not match:
        return None
    items = re.findall(rb"<rdf:li[^>]*>(.*?)</rdf:li>", match.group(1), re.S)
    parts = items or [_XML_TAG.sub(b"", match.group(1))]
    text = ", ".join(p.decode("utf-8", errors="ignore").strip() for p in parts if p.strip())
    return text or None


def _normalize_date(value: str | None) -> str | None:
    """'2023:05:01 10:20:30' -> '2023-05-01 10:20:30'."""
    value = _clean_text(value)
    if not value:
        return None
    if len(value) >= 10 and value[4] == ":" and value[7] == ":":
        value = value[:4] + "-" + value[5:7] + "-" + value[8:]
    return value[:19]


def _first(*values):
    for v in values:
        if v:
            return v
    return None


def sha256_file(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()


def extract_image_details(path: str | Path, use_perceptual_hash: bool = True) -> dict:
    """Open the image and return its dimensions, metadata and perceptual hash."""
    details: dict = {}
    with Image.open(path) as img:
        exif = img.getexif()
        exif_ifd = exif.get_ifd(_IFD_EXIF) if exif else {}
        xmp = img.info.get("xmp") or img.info.get("XML:com.adobe.xmp") or b""
        if isinstance(xmp, str):
            xmp = xmp.encode("utf-8")

        make = _clean_text(exif.get(_TAG_MAKE))
        model = _clean_text(exif.get(_TAG_MODEL))
        if make and model and model.lower().startswith(make.lower()):
            make = None
        details["camera"] = " ".join(p for p in (make, model) if p) or None
        details["taken"] = _normalize_date(
            _first(exif_ifd.get(_TAG_DATETIME_ORIGINAL), exif.get(_TAG_DATETIME))
        )
        details["title"] = _first(_clean_text(exif.get(_TAG_XP_TITLE)), _xmp_text(xmp, "title"))
        details["description"] = _first(
            _clean_text(exif.get(_TAG_IMAGE_DESCRIPTION)),
            _clean_text(exif.get(_TAG_XP_COMMENT)),
            _clean_text(exif.get(_TAG_XP_SUBJECT)),
            _clean_text(exif_ifd.get(_TAG_USER_COMMENT)),
            _xmp_text(xmp, "description"),
        )
        details["keywords"] = _first(
            _clean_text(exif.get(_TAG_XP_KEYWORDS)), _xmp_text(xmp, "keywords")
        )

        width, height = img.size
        if exif.get(_TAG_ORIENTATION) in (5, 6, 7, 8):
            width, height = height, width
        details["width"], details["height"] = width, height

        if use_perceptual_hash and imagehash is not None:
            img.draft("RGB", (512, 512))  # fast JPEG decoding at reduced size
            # Apply EXIF rotation so that a rotated copy still matches visually.
            img = ImageOps.exif_transpose(img)
            details["phash"] = str(imagehash.phash(img.convert("RGB")))
    return details


def analyze_file(path: Path, root: Path, use_perceptual_hash: bool = True) -> ImageInfo:
    stat = path.stat()
    info = ImageInfo(
        path=str(path),
        rel_path=path.relative_to(root).as_posix(),
        root=str(root),
        name=path.name,
        size=stat.st_size,
        mtime=stat.st_mtime,
    )
    try:
        info.sha256 = sha256_file(path)
    except OSError as exc:
        info.error = f"read error: {exc}"
        return info
    try:
        for key, value in extract_image_details(path, use_perceptual_hash).items():
            setattr(info, key, value)
    except Exception as exc:  # corrupt / unsupported image - keep the byte hash
        info.error = f"image error: {exc}"
    return info


def scan_folder(
    root: str | Path,
    extensions: Iterable[str],
    exclude: Iterable[str],
    cache: ScanCache | None = None,
    use_perceptual_hash: bool = True,
    workers: int = 4,
    progress: Callable[[int, int], None] | None = None,
) -> list[ImageInfo]:
    """Scan ``root`` and return an :class:`ImageInfo` for each image.

    Unchanged files (same size and modification time) are served from the cache,
    which makes re-scanning a large NAS share fast.
    """
    root = Path(root)
    files = list(iter_image_files(root, extensions, exclude))
    results: list[ImageInfo] = []
    to_analyze: list[Path] = []
    cache_key = f"v{SCHEMA_VERSION}:phash={int(use_perceptual_hash)}"

    for path in files:
        cached = None
        if cache is not None:
            try:
                stat = path.stat()
            except OSError:
                continue
            cached = cache.get(str(path), stat.st_size, stat.st_mtime, cache_key)
        if cached is not None:
            info = ImageInfo.from_dict(cached)
            info.rel_path = path.relative_to(root).as_posix()
            info.root = str(root)
            results.append(info)
        else:
            to_analyze.append(path)

    done = len(results)
    if progress:
        progress(done, len(files))

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for info in pool.map(lambda p: analyze_file(p, root, use_perceptual_hash), to_analyze):
            results.append(info)
            if cache is not None and info.error is None:
                cache.put(info.path, info.size, info.mtime, cache_key, info.to_dict())
            done += 1
            if progress and (done % 50 == 0 or done == len(files)):
                progress(done, len(files))

    if cache is not None:
        cache.commit()
    results.sort(key=lambda i: i.path)
    return results
