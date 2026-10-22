"""Moving identical duplicate copies out of chosen folders - safely.

Nothing is deleted. A duplicate is *moved* into a review folder
(``_duplicates_to_review``) inside the same shared folder, so the move is an
instant rename on the NAS and can be undone. Rules:

* Only byte-identical copies (same SHA-256) are touched.
* Only files inside the folders the user names with ``--from`` are moved.
* At least one copy of every photo always stays in place: if all copies are
  inside the ``--from`` folders, the best one is kept.
* Every move is written to a log, which ``undo-dedupe`` uses to put files back.
"""

from __future__ import annotations

import csv
import os
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

from .duplicates import EXACT, DuplicateGroup
from .scanner import ImageInfo

REVIEW_DIR = "_duplicates_to_review"
LOG_COLUMNS = ["status", "source", "moved_to", "kept_copy", "size", "sha256", "detail"]


def _norm(path: str) -> str:
    return str(path).replace("\\", "/").rstrip("/").casefold()


def is_under(path: str, folders: Iterable[str]) -> bool:
    p = _norm(path)
    return any(p == f or p.startswith(f + "/") for f in folders)


@dataclass
class MovePlan:
    source: ImageInfo
    target: str
    kept: ImageInfo


def review_target(img: ImageInfo) -> str:
    """Where a duplicate is moved to.

    On a NAS share (\\\\server\\share\\...) the review folder sits at the top of
    the share, so the move stays a fast server-side rename *and* the files leave
    folders that apps index (e.g. Synology Photos shows everything under
    home/Photos, so a review folder there would still show the duplicates).
    """
    p = img.path.replace("\\", "/")
    if p.startswith("//"):
        parts = p[2:].split("/")
        if len(parts) > 2:
            share_root = "//" + parts[0] + "/" + parts[1]
            return os.path.join(share_root, REVIEW_DIR, *parts[2:])
    return os.path.join(img.root, REVIEW_DIR, *img.rel_path.split("/"))


def plan_moves(
    groups: list[DuplicateGroup],
    remove_from: Iterable[str],
    never_move: Iterable[str] = (),
    keep_one_inside: bool = False,
) -> list[MovePlan]:
    """Plan which identical copies to move out of ``remove_from``.

    ``never_move``: folders whose files always stay (e.g. the phone backup).
    ``keep_one_inside``: keep one copy inside ``remove_from`` even when other
    copies exist elsewhere - removes duplicates *within* a folder tree (such as
    a Synology Photos library) without emptying it.
    """
    folders = [_norm(f) for f in remove_from]
    protected = [_norm(f) for f in never_move]
    plans = []
    for group in groups:
        if group.kind != EXACT:
            continue
        inside = [i for i in group.images if is_under(i.path, folders)]
        removable = [i for i in inside if not is_under(i.path, protected)]
        if not removable:
            continue
        protected_inside = [i for i in inside if is_under(i.path, protected)]
        outside = [i for i in group.images if i not in inside]
        if protected_inside:
            kept = protected_inside[0]
        elif outside and not keep_one_inside:
            kept = outside[0]  # images are sorted best first
        else:
            kept, removable = removable[0], removable[1:]  # never remove the last copy
        for img in removable:
            plans.append(MovePlan(img, review_target(img), kept))
    return plans


def _unique(target: str) -> str:
    if not os.path.exists(target):
        return target
    stem, ext = os.path.splitext(target)
    n = 1
    while os.path.exists(f"{stem} ({n}){ext}"):
        n += 1
    return f"{stem} ({n}){ext}"


def _move(source: str, target: str) -> None:
    os.makedirs(os.path.dirname(target), exist_ok=True)
    try:
        os.rename(source, target)  # same shared folder: instant, server-side
    except OSError:
        shutil.move(source, target)


def execute_moves(plans: list[MovePlan], log_path: Path, log: Callable[[str], None] = print) -> dict[str, int]:
    counts: dict[str, int] = {}
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=LOG_COLUMNS)
        writer.writeheader()
        for plan in plans:
            src = plan.source
            row = {"source": src.path, "moved_to": "", "kept_copy": plan.kept.path,
                   "size": src.size, "sha256": src.sha256, "detail": ""}
            try:
                if not os.path.isfile(src.path) or os.path.getsize(src.path) != src.size:
                    row.update(status="skipped", detail="file changed or missing since the scan")
                elif not os.path.isfile(plan.kept.path) or os.path.getsize(plan.kept.path) != plan.kept.size:
                    row.update(status="skipped", detail="the copy to keep is missing or changed")
                else:
                    target = _unique(plan.target)
                    _move(src.path, target)
                    row.update(status="moved", moved_to=target)
                    log(f"moved   {src.path}")
            except OSError as exc:
                row.update(status="failed", detail=str(exc))
                log(f"FAILED  {src.path}: {exc}")
            writer.writerow(row)
            f.flush()  # an interrupted run still has a complete log of what was moved
            counts[row["status"]] = counts.get(row["status"], 0) + 1
    return counts


def undo_moves(log_csv: Path, log: Callable[[str], None] = print) -> dict[str, int]:
    counts: dict[str, int] = {}
    with log_csv.open(encoding="utf-8-sig", newline="") as f:
        rows = [r for r in csv.DictReader(f) if r.get("status") == "moved"]
    for row in rows:
        source, moved_to = row["source"], row["moved_to"]
        if os.path.exists(source):
            status = "skipped"
            log(f"skipped {source} (a file with this name exists again)")
        elif not os.path.isfile(moved_to):
            status = "missing"
            log(f"MISSING {moved_to}")
        else:
            _move(moved_to, source)
            status = "restored"
            log(f"restored {source}")
        counts[status] = counts.get(status, 0) + 1
    return counts


def new_log_path(report_dir: str | Path) -> Path:
    return Path(report_dir) / f"dedupe-{datetime.now():%Y%m%d-%H%M%S}.csv"
