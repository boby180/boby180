"""Matching local images against the server's images.

Every local image gets one of these statuses:

* ``exact``   - a byte-identical copy exists on the server (same SHA-256).
* ``similar`` - the server has a visually identical photo (perceptual hash
                within the threshold) whose metadata does not contradict it,
                e.g. a resized/re-compressed copy, or the same photo with an
                edited description. Not uploaded; differences are reported.
* ``missing`` - nothing matches; this photo should be uploaded.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .scanner import ImageInfo

EXACT = "exact"
SIMILAR = "similar"
MISSING = "missing"

# Metadata fields compared between a local photo and its server match.
META_FIELDS = ("taken", "camera", "title", "description", "keywords")


@dataclass
class MatchResult:
    local: ImageInfo
    status: str
    server: ImageInfo | None = None
    distance: int | None = None
    # Human readable list of differences between the local and server copies.
    differences: list[str] = field(default_factory=list)
    # True when the server already has a different file with the same name.
    name_on_server: bool = False


def _norm(value) -> str:
    return " ".join(str(value).split()).casefold() if value else ""


def metadata_differences(local: ImageInfo, server: ImageInfo) -> list[str]:
    diffs = []
    if local.name.casefold() != server.name.casefold():
        diffs.append(f"name: {local.name} != {server.name}")
    if local.size != server.size:
        diffs.append(f"size: {local.size} != {server.size}")
    if (local.width, local.height) != (server.width, server.height):
        diffs.append(f"dimensions: {local.width}x{local.height} != {server.width}x{server.height}")
    for name in META_FIELDS:
        a, b = getattr(local, name), getattr(server, name)
        if _norm(a) != _norm(b):
            diffs.append(f"{name}: {a or '-'} != {b or '-'}")
    return diffs


def metadata_conflicts(local: ImageInfo, server: ImageInfo) -> bool:
    """True when metadata proves these are two *different* photos.

    Burst shots or near-duplicates can have a very close perceptual hash, so a
    different capture time or camera means "not the same photo". A missing value
    on one side (e.g. EXIF stripped by an export) is not treated as a conflict.
    """
    if local.taken and server.taken and local.taken != server.taken:
        return True
    if local.camera and server.camera and _norm(local.camera) != _norm(server.camera):
        return True
    if local.width and local.height and server.width and server.height:
        ratio_local = local.width / local.height
        ratio_server = server.width / server.height
        if abs(ratio_local - ratio_server) > 0.02 * max(ratio_local, ratio_server):
            return True
    return False


def _popcount(values: np.ndarray) -> np.ndarray:
    if hasattr(np, "bitwise_count"):
        return np.bitwise_count(values)
    as_bytes = values.view(np.uint8).reshape(-1, 8)
    return np.unpackbits(as_bytes, axis=1).sum(axis=1)


class ServerIndex:
    """Lookup structure over the server images."""

    def __init__(self, images: list[ImageInfo]):
        self.images = images
        self.by_sha: dict[str, ImageInfo] = {}
        self.names: set[str] = set()
        hashed: list[tuple[int, ImageInfo]] = []
        for img in images:
            if img.sha256:
                self.by_sha.setdefault(img.sha256, img)
            self.names.add(img.name.casefold())
            if img.phash:
                hashed.append((int(img.phash, 16), img))
        self._phash_images = [img for _, img in hashed]
        self._phashes = np.array([h for h, _ in hashed], dtype=np.uint64)

    def similar(self, phash: str, threshold: int) -> list[tuple[int, ImageInfo]]:
        """Server images whose perceptual hash is within ``threshold`` bits, closest first."""
        if not len(self._phashes):
            return []
        distances = _popcount(np.bitwise_xor(self._phashes, np.uint64(int(phash, 16))))
        idx = np.nonzero(distances <= threshold)[0]
        idx = idx[np.argsort(distances[idx], kind="stable")]
        return [(int(distances[i]), self._phash_images[i]) for i in idx]


def compare_images(
    local_images: list[ImageInfo], server_images: list[ImageInfo], similarity_threshold: int = 6
) -> list[MatchResult]:
    index = ServerIndex(server_images)
    results = []
    for img in local_images:
        name_on_server = img.name.casefold() in index.names
        match = index.by_sha.get(img.sha256) if img.sha256 else None
        if match is not None:
            results.append(MatchResult(img, EXACT, match, 0, metadata_differences(img, match), name_on_server))
            continue

        result = MatchResult(img, MISSING, name_on_server=name_on_server)
        if img.phash:
            for distance, candidate in index.similar(img.phash, similarity_threshold):
                if not metadata_conflicts(img, candidate):
                    result = MatchResult(
                        img, SIMILAR, candidate, distance,
                        metadata_differences(img, candidate), name_on_server,
                    )
                    break
        results.append(result)
    return results


BETTER_COMPUTER = "computer"
BETTER_SERVER = "server"
BETTER_SAME = "same"


def better_copy(result: MatchResult) -> str | None:
    """For a ``similar`` match: which copy has the better quality.

    More pixels wins; at the same resolution, a clearly bigger file (less
    compression, e.g. vs. a Google Photos "storage saver" copy) wins.
    """
    if result.status != SIMILAR or result.server is None:
        return None
    local, server = result.local, result.server
    pixels_local = (local.width or 0) * (local.height or 0)
    pixels_server = (server.width or 0) * (server.height or 0)
    if pixels_local and pixels_server and abs(pixels_local - pixels_server) > 0.01 * max(pixels_local, pixels_server):
        return BETTER_COMPUTER if pixels_local > pixels_server else BETTER_SERVER
    if local.size > server.size * 1.1:
        return BETTER_COMPUTER
    if server.size > local.size * 1.1:
        return BETTER_SERVER
    return BETTER_SAME


def summarize(results: list[MatchResult]) -> dict[str, int]:
    summary = {EXACT: 0, SIMILAR: 0, MISSING: 0, "errors": 0, "better_on_computer": 0}
    for r in results:
        summary[r.status] += 1
        if r.local.error:
            summary["errors"] += 1
        if better_copy(r) == BETTER_COMPUTER:
            summary["better_on_computer"] += 1
    return summary
