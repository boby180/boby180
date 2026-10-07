"""Finding duplicate photos inside one collection (e.g. the server).

Two kinds of groups are reported:

* ``exact``   - byte-identical files (same SHA-256) stored in several places.
* ``similar`` - the same photo saved in different versions (resized,
                re-compressed, edited metadata): perceptual hashes within the
                threshold and metadata that does not prove they are different
                shots (see :func:`compare.metadata_conflicts`).

Nothing is deleted - each group only suggests which copy to keep.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Callable

import numpy as np

from .compare import _popcount, metadata_conflicts
from .scanner import ImageInfo

EXACT = "exact"
SIMILAR = "similar"


def keep_score(img: ImageInfo):
    """Higher is better: most pixels, then biggest file, then has a description, then shortest path."""
    pixels = (img.width or 0) * (img.height or 0)
    has_text = bool(img.description or img.title or img.keywords)
    return (pixels, img.size, has_text, -len(img.path))


@dataclass
class DuplicateGroup:
    kind: str
    images: list[ImageInfo]  # best copy first

    @property
    def keep(self) -> ImageInfo:
        return self.images[0]

    @property
    def extra(self) -> list[ImageInfo]:
        return self.images[1:]

    @property
    def wasted_bytes(self) -> int:
        return sum(i.size for i in self.extra)


def _sorted_group(kind: str, images: list[ImageInfo]) -> DuplicateGroup:
    return DuplicateGroup(kind, sorted(images, key=keep_score, reverse=True))


def find_duplicates(
    images: list[ImageInfo],
    similarity_threshold: int = 6,
    include_similar: bool = True,
    progress: Callable[[int, int], None] | None = None,
) -> list[DuplicateGroup]:
    by_sha: dict[str, list[ImageInfo]] = defaultdict(list)
    for img in images:
        if img.sha256:
            by_sha[img.sha256].append(img)

    groups = [_sorted_group(EXACT, imgs) for imgs in by_sha.values() if len(imgs) > 1]

    if include_similar:
        # One representative per distinct file content; exact copies are already grouped above.
        reps = [_sorted_group(EXACT, imgs).keep for imgs in by_sha.values()]
        reps = [r for r in reps if r.phash]
        groups += _similar_groups(reps, similarity_threshold, progress)

    groups.sort(key=lambda g: (g.kind != EXACT, -g.wasted_bytes, g.keep.path))
    return groups


def _similar_groups(reps, threshold, progress) -> list[DuplicateGroup]:
    n = len(reps)
    if n < 2:
        return []
    hashes = np.array([int(r.phash, 16) for r in reps], dtype=np.uint64)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n - 1):
        distances = _popcount(np.bitwise_xor(hashes[i + 1:], hashes[i]))
        for j in np.nonzero(distances <= threshold)[0]:
            j = int(j) + i + 1
            if not metadata_conflicts(reps[i], reps[j]):
                parent[find(i)] = find(j)
        if progress and (i % 1000 == 0 or i == n - 2):
            progress(i + 1, n - 1)

    clusters: dict[int, list[ImageInfo]] = defaultdict(list)
    for i, rep in enumerate(reps):
        clusters[find(i)].append(rep)
    return [_sorted_group(SIMILAR, c) for c in clusters.values() if len(c) > 1]
