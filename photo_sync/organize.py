"""Arranging photos into year / month folders, like Google Photos.

The date comes from the photo itself, not from the file's modification time
(which changes when a file is copied):

1. ``exif``     - the date the camera stored when the photo was taken.
2. ``filename`` - a date in the file name, e.g. IMG_20180731_182308.jpg,
                  PXL_20230101_..., IMG-20190505-WA0001.jpg (phones, WhatsApp).
3. ``folder``   - a year (and month) in a folder name, e.g. "03-2020" or
                  "2001 אילת" - common for old scans that have no EXIF.
4. otherwise the photo goes to ``unknown-date/<its folder name>`` instead of
   guessing from the file date.

Layout: ``<to>/2019/2019-05/IMG_1234.jpg``. Files are moved (an instant
rename inside the same shared folder) and every move is logged, so
``undo-dedupe <log>`` puts everything back. An identical copy of a photo
that already sits at the target goes to the review folder instead.
"""

from __future__ import annotations

import os
import re
from dataclasses import replace
from datetime import datetime
from typing import Iterable

from .dedupe import MovePlan, _norm, is_under, review_target
from .scanner import ImageInfo

UNKNOWN_DIR = "unknown-date"
MIN_YEAR = 1980

_DATE_IN_NAME = re.compile(r"(?<!\d)((?:19|20)\d{2})[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])(?!\d)")
_FOLDER_MONTH_YEAR = re.compile(r"(?<!\d)(0?[1-9]|1[0-2])[-_.]((?:19|20)\d{2})(?!\d)")  # 03-2020
_FOLDER_YEAR_MONTH = re.compile(r"(?<!\d)((?:19|20)\d{2})[-_.](0?[1-9]|1[0-2])(?!\d)")  # 2020-03
_FOLDER_YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")


def _valid_year(year: int) -> bool:
    return MIN_YEAR <= year <= datetime.now().year + 1


def photo_date(img: ImageInfo) -> tuple[int, int | None, str] | None:
    """(year, month or None, source) for a photo, or None when no reliable date is found."""
    if img.taken and len(img.taken) >= 7 and img.taken[:4].isdigit() and img.taken[5:7].isdigit():
        year, month = int(img.taken[:4]), int(img.taken[5:7])
        if _valid_year(year) and 1 <= month <= 12:
            return year, month, "exif"

    match = _DATE_IN_NAME.search(img.name)
    if match and _valid_year(int(match.group(1))):
        return int(match.group(1)), int(match.group(2)), "filename"

    folders = img.rel_path.replace("\\", "/").split("/")[:-1]
    for folder in reversed(folders):  # the deepest folder is the most specific
        for pattern, year_group, month_group in (
            (_FOLDER_YEAR_MONTH, 1, 2), (_FOLDER_MONTH_YEAR, 2, 1), (_FOLDER_YEAR, 1, None),
        ):
            match = pattern.search(folder)
            if match and _valid_year(int(match.group(year_group))):
                month = int(match.group(month_group)) if month_group else None
                return int(match.group(year_group)), month, "folder"
    return None


def target_folder(img: ImageInfo, to: str) -> tuple[str, str]:
    """(folder, date source) the photo belongs in under ``to``."""
    date = photo_date(img)
    if date is None:
        parts = img.rel_path.replace("\\", "/").split("/")
        name = parts[-2] if len(parts) > 1 else "other"
        return os.path.join(to, UNKNOWN_DIR, name), "none"
    year, month, source = date
    month_dir = f"{year}-{month:02d}" if month else f"{year}-unknown-month"
    return os.path.join(to, str(year), month_dir), source


def plan_organize(
    images: list[ImageInfo],
    move_from: Iterable[str],
    to: str,
    never_move: Iterable[str] = (),
) -> tuple[list[MovePlan], dict[str, str]]:
    """Plan the moves. Returns (plans, {source path: date source})."""
    folders = [_norm(f) for f in move_from]
    protected = [_norm(f) for f in never_move]
    dest = _norm(to)
    taken: dict[str, ImageInfo] = {}  # normalized target path -> the photo that ends up there
    for img in images:
        if is_under(img.path, [dest]):
            taken[_norm(img.path)] = img  # already organized

    plans, sources = [], {}
    for img in sorted(images, key=lambda i: i.path):
        if not is_under(img.path, folders) or is_under(img.path, protected) or is_under(img.path, [dest]):
            continue
        folder, source = target_folder(img, to)
        sources[img.path] = source
        target = os.path.join(folder, img.name)
        stem, ext = os.path.splitext(img.name)
        n = 0
        while _norm(target) in taken:
            other = taken[_norm(target)]
            if other.sha256 and other.sha256 == img.sha256:
                break  # identical copy already there
            n += 1
            target = os.path.join(folder, f"{stem} ({n}){ext}")
        if _norm(target) in taken:
            kept = taken[_norm(target)]
            plans.append(MovePlan(img, review_target(img), kept))
            sources[img.path] = "duplicate"
            continue
        moved = replace(img, path=target)
        taken[_norm(target)] = moved
        plans.append(MovePlan(img, target, img))  # "kept" = the file itself, checked before the move
    return plans, sources
