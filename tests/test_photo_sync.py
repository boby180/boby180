import random
import shutil
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from photo_sync.cache import ScanCache
from photo_sync.cli import main
from photo_sync.compare import EXACT, MISSING, SIMILAR, compare_images
from photo_sync.config import ConfigError, load_config
from photo_sync.scanner import scan_folder
from photo_sync.uploader import CopyUploader, upload_missing

EXTS = [".jpg", ".png"]
EXCLUDE = ["@eaDir"]


def make_photo(path: Path, seed: int, size=(320, 240), taken=None, description=None,
               camera=None, quality=95):
    """Create a JPEG with a distinctive pattern and optional EXIF metadata."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Smooth shapes (like a real photo) so that resizing keeps the visual hash stable.
    rnd = random.Random(seed)
    img = Image.new("RGB", size, (rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)))
    draw = ImageDraw.Draw(img)
    w, h = size
    for _ in range(6):
        x0, y0 = rnd.uniform(0, 0.7) * w, rnd.uniform(0, 0.7) * h
        x1, y1 = x0 + rnd.uniform(0.2, 0.5) * w, y0 + rnd.uniform(0.2, 0.5) * h
        draw.ellipse((x0, y0, x1, y1), fill=(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)))
    exif = Image.Exif()
    if description:
        exif[0x010E] = description
    if camera:
        exif[0x0110] = camera
    if taken:
        exif.get_ifd(0x8769)[0x9003] = taken
    img.save(path, quality=quality, exif=exif)
    return path


def scan(root):
    return scan_folder(root, EXTS, EXCLUDE, workers=2)


@pytest.fixture
def dirs(tmp_path):
    local, server = tmp_path / "pc" / "Pictures", tmp_path / "nas"
    local.mkdir(parents=True)
    server.mkdir()
    return local, server


def test_metadata_extraction(dirs):
    local, _ = dirs
    make_photo(local / "a.jpg", 1, taken="2023:05:01 10:20:30", description="Family trip", camera="Pixel 7")
    (info,) = scan(local)
    assert info.taken == "2023-05-01 10:20:30"
    assert info.description == "Family trip"
    assert info.camera == "Pixel 7"
    assert (info.width, info.height) == (320, 240)
    assert info.phash and info.sha256


def test_exact_similar_missing(dirs):
    local, server = dirs
    make_photo(local / "same.jpg", 1, taken="2023:01:01 00:00:00")
    shutil.copy2(local / "same.jpg", server / "renamed.jpg")

    # Same photo resized + re-compressed + different description on the server.
    make_photo(local / "trip.jpg", 2, taken="2023:02:02 12:00:00", description="Paris")
    (server / "2023").mkdir()
    with Image.open(local / "trip.jpg") as img:
        exif = img.getexif()
        exif[0x010E] = "Paris trip"
        img.resize((160, 120)).save(server / "2023" / "trip_small.jpg", quality=70, exif=exif)

    make_photo(local / "sub" / "new.jpg", 3)
    # Ignored Synology thumbnail folder must not count as "present".
    make_photo(server / "@eaDir" / "new.jpg", 3)

    results = {Path(r.local.path).name: r for r in compare_images(scan(local), scan(server))}
    assert results["same.jpg"].status == EXACT
    assert results["trip.jpg"].status == SIMILAR
    assert any(d.startswith("description:") for d in results["trip.jpg"].differences)
    assert results["new.jpg"].status == MISSING


def test_burst_shot_with_different_time_is_not_a_match(dirs):
    local, server = dirs
    make_photo(local / "burst1.jpg", 4, taken="2023:03:03 10:00:01", quality=90)
    make_photo(server / "burst2.jpg", 4, taken="2023:03:03 10:00:02", quality=85)
    (result,) = compare_images(scan(local), scan(server))
    assert result.status == MISSING


def test_name_conflict_is_reported(dirs):
    local, server = dirs
    make_photo(local / "IMG_1.jpg", 5)
    make_photo(server / "IMG_1.jpg", 6)
    (result,) = compare_images(scan(local), scan(server))
    assert result.status == MISSING and result.name_on_server


def test_upload_copy_never_overwrites_and_skips_duplicates(dirs):
    local, server = dirs
    make_photo(local / "IMG_1.jpg", 7)
    shutil.copy2(local / "IMG_1.jpg", local / "copy_of_IMG_1.jpg")
    existing = make_photo(server / "up" / "Pictures" / "IMG_1.jpg", 8)
    before = existing.read_bytes()

    results = compare_images(scan(local), scan(server))
    uploader = CopyUploader(str(server), "up")

    dry = upload_missing(results, uploader, dry_run=True, log=lambda m: None)
    assert [r["status"] for r in dry] == ["would-upload", "skipped"]
    assert not (server / "up" / "Pictures" / "copy_of_IMG_1.jpg").exists()

    records = upload_missing(results, uploader, dry_run=False, log=lambda m: None)
    statuses = sorted(r["status"] for r in records)
    assert statuses == ["skipped", "uploaded"]
    assert existing.read_bytes() == before
    uploaded = [r for r in records if r["status"] == "uploaded"][0]["target"]
    assert Path(uploaded).exists()

    # After upload, a new comparison finds everything on the server.
    assert {r.status for r in compare_images(scan(local), scan(server))} == {EXACT}


def test_cache_reuses_results(dirs, tmp_path):
    local, _ = dirs
    make_photo(local / "a.jpg", 9)
    with ScanCache(tmp_path / "cache.sqlite") as cache:
        first = scan_folder(local, EXTS, EXCLUDE, cache=cache)
    with ScanCache(tmp_path / "cache.sqlite") as cache:
        second = scan_folder(local, EXTS, EXCLUDE, cache=cache)
    assert first[0].sha256 == second[0].sha256


def test_cli_end_to_end(dirs, tmp_path):
    local, server = dirs
    make_photo(local / "a.jpg", 10)
    make_photo(local / "b.jpg", 11)
    shutil.copy2(local / "a.jpg", server / "a.jpg")
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        f"local:\n  paths: ['{local.as_posix()}']\n"
        f"server:\n  path: '{server.as_posix()}'\n  upload_subfolder: up\n",
        encoding="utf-8",
    )
    assert main(["-c", str(cfg), "check"]) == 0
    assert main(["-c", str(cfg), "compare"]) == 0
    assert list((tmp_path / "reports").glob("compare-*.html"))
    assert main(["-c", str(cfg), "upload"]) == 0
    assert not (server / "up").exists()
    assert main(["-c", str(cfg), "upload", "--execute"]) == 0
    assert (server / "up" / "Pictures" / "b.jpg").exists()
    assert not (server / "up" / "Pictures" / "a.jpg").exists()


def test_config_errors(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "missing.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("local:\n  paths: [x]\nserver:\n  path: y\n  bogus: 1\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(bad)
    ok = tmp_path / "ok.yaml"
    ok.write_text("local:\n  paths: [/nope]\nserver:\n  path: /nope2\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(ok).validate()
