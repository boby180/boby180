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
    uploader = CopyUploader(str(server / "up"))

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
    share2 = tmp_path / "nas2"
    share2.mkdir()
    make_photo(local / "a.jpg", 10)
    make_photo(local / "b.jpg", 11)
    make_photo(local / "c.jpg", 13)
    shutil.copy2(local / "a.jpg", server / "a.jpg")
    shutil.copy2(local / "c.jpg", share2 / "c.jpg")  # found in the second share
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        f"local:\n  paths: ['{local.as_posix()}']\n"
        f"server:\n  paths: ['{server.as_posix()}', '{share2.as_posix()}']\n"
        f"  upload_path: '{(server / 'up').as_posix()}'\n",
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
    assert not (server / "up" / "Pictures" / "c.jpg").exists()


def test_config_errors(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "missing.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("local:\n  paths: [x]\nserver:\n  paths: [y]\n  bogus: 1\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(bad)
    ok = tmp_path / "ok.yaml"
    ok.write_text("local:\n  paths: [/nope]\nserver:\n  paths: /nope2\n  upload_path: /nope2/up\n", encoding="utf-8")
    config = load_config(ok)
    assert config.server.paths == ["/nope2"]
    with pytest.raises(ConfigError):
        config.validate()


def test_hebrew_folder_names(tmp_path):
    local, server = tmp_path / "cd rom", tmp_path / "nas"
    server.mkdir()
    path = make_photo(local / "סוכות באילת" / "תמונה 1.jpg", 12)
    # Windows Explorer stores Hebrew title/comment in the UTF-16 XP tags.
    with Image.open(path) as img:
        exif = img.getexif()
        exif[0x9C9B] = "אילת".encode("utf-16-le") + b"\x00\x00"
        exif[0x9C9C] = "חוף הים".encode("utf-16-le") + b"\x00\x00"
        img.save(path, exif=exif)
    (result,) = compare_images(scan(local), scan(server))
    assert result.status == MISSING
    assert result.local.rel_path == "סוכות באילת/תמונה 1.jpg"
    assert result.local.description == "חוף הים"
    assert result.local.title == "אילת"


def test_interrupted_scan_keeps_progress(tmp_path):
    local = tmp_path / "pics"
    for i in range(60):
        make_photo(local / f"{i}.jpg", 100 + i, size=(40, 30))

    def stop(done, total):
        if done >= 50:
            raise KeyboardInterrupt

    with ScanCache(tmp_path / "cache.sqlite") as cache:
        with pytest.raises(KeyboardInterrupt):
            scan_folder(local, EXTS, EXCLUDE, cache=cache, workers=1, progress=stop)

    analyzed = []
    original = __import__("photo_sync.scanner", fromlist=["analyze_file"]).analyze_file

    def counting(path, *args):
        analyzed.append(path)
        return original(path, *args)

    import photo_sync.scanner as scanner
    scanner.analyze_file, saved = counting, scanner.analyze_file
    try:
        with ScanCache(tmp_path / "cache.sqlite") as cache:
            result = scan_folder(local, EXTS, EXCLUDE, cache=cache, workers=1)
    finally:
        scanner.analyze_file = saved
    assert len(result) == 60
    assert len(analyzed) <= 10  # only the files not reached before the interruption


def test_duplicates_on_server(dirs, tmp_path):
    from photo_sync.duplicates import EXACT as D_EXACT, SIMILAR as D_SIMILAR, find_duplicates

    _, server = dirs
    original = make_photo(server / "2020" / "a.jpg", 20, taken="2020:01:01 10:00:00")
    (server / "backup").mkdir()
    shutil.copy2(original, server / "backup" / "a.jpg")
    (server / "small").mkdir()
    with Image.open(original) as img:
        img.resize((160, 120)).save(server / "small" / "a_small.jpg", quality=70, exif=img.getexif())
    make_photo(server / "other.jpg", 21, taken="2021:01:01 10:00:00")
    # Burst shot: same picture, different time -> not a duplicate.
    make_photo(server / "burst.jpg", 20, taken="2020:01:01 10:00:05", quality=80)

    groups = find_duplicates(scan(server))
    kinds = sorted(g.kind for g in groups)
    assert kinds == [D_EXACT, D_SIMILAR]
    exact = next(g for g in groups if g.kind == D_EXACT)
    assert {Path(i.path).parent.name for i in exact.images} == {"2020", "backup"}
    similar = next(g for g in groups if g.kind == D_SIMILAR)
    assert len(similar.images) == 2
    assert similar.keep.width == 320  # higher resolution is suggested to keep

    from photo_sync.duplicates import location_summary
    (where,) = location_summary(groups)
    assert [Path(loc).name for loc in where.locations] == ["2020", "backup"]
    assert where.extra_files == 1

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        f"local:\n  paths: ['{dirs[0].as_posix()}']\n"
        f"server:\n  paths: ['{server.as_posix()}']\n  upload_path: '{(server / 'up').as_posix()}'\n",
        encoding="utf-8",
    )
    assert main(["-c", str(cfg), "duplicates"]) == 0
    assert list((tmp_path / "reports").glob("duplicates-*.html"))
    assert (server / "backup" / "a.jpg").exists()  # nothing deleted


def test_better_quality_on_computer_can_be_uploaded(dirs):
    from photo_sync.compare import BETTER_COMPUTER, better_copy

    local, server = dirs
    original = make_photo(local / "trip.jpg", 30, taken="2022:02:02 12:00:00")
    with Image.open(original) as img:  # server only has a smaller version
        img.resize((160, 120)).save(server / "trip_small.jpg", quality=70, exif=img.getexif())
    make_photo(local / "new.jpg", 31)

    results = compare_images(scan(local), scan(server))
    similar = next(r for r in results if r.status == SIMILAR)
    assert better_copy(similar) == BETTER_COMPUTER

    log = lambda m: None
    plain = upload_missing(results, None, dry_run=True, log=log)
    assert [Path(r["local"]).name for r in plain] == ["new.jpg"]

    uploader = CopyUploader(str(server / "up"))
    records = upload_missing(results, uploader, dry_run=False, log=log, include_better=True)
    assert sorted(Path(r["local"]).name for r in records if r["status"] == "uploaded") == ["new.jpg", "trip.jpg"]
    assert (server / "trip_small.jpg").exists()  # the server copy is kept


def test_enhance_underwater_photos(tmp_path):
    import numpy as np
    from photo_sync.enhance import enhance_folder, looks_underwater

    src = tmp_path / "dive"
    land = make_photo(src / "land.jpg", 40)
    # Simulate water: red absorbed, blue-green haze.
    with Image.open(land) as img:
        arr = np.asarray(img, dtype=np.float32) / 255
    water = arr * np.array([0.3, 0.8, 0.9]) * 0.6 + np.array([0.05, 0.45, 0.55]) * 0.4
    uw_path = src / "fish.jpg"
    exif = Image.Exif()
    exif.get_ifd(0x8769)[0x9003] = "2002:03:21 14:38:19"
    Image.fromarray((water * 255).astype("uint8")).save(uw_path, exif=exif)
    before = uw_path.read_bytes()

    with Image.open(uw_path) as img:
        assert looks_underwater(img)
    with Image.open(land) as img:
        assert not looks_underwater(img)

    results = {Path(r.source).name: r for r in enhance_folder(src, tmp_path / "out", EXTS, EXCLUDE)}
    assert results["fish.jpg"].status == "enhanced"
    assert results["land.jpg"].status == "skipped-not-underwater"
    assert uw_path.read_bytes() == before  # original untouched

    out = tmp_path / "out" / "dive" / "fish.jpg"
    (info,) = scan(out.parent)
    assert info.taken == "2002-03-21 14:38:19"  # metadata kept
    with Image.open(out) as img:
        r, g, b = (np.asarray(img, dtype=np.float32)[..., i].mean() for i in range(3))
    assert r > 0.6 * max(g, b)  # the red cast is corrected

    # Running again does not overwrite.
    again = enhance_folder(src, tmp_path / "out", EXTS, EXCLUDE)
    assert {r.status for r in again} == {"skipped-exists", "skipped-not-underwater"}


def test_dedupe_moves_only_from_chosen_folder_and_undo(tmp_path):
    server = tmp_path / "nas" / "Photos"
    a = make_photo(server / "2019" / "a.jpg", 50)
    b = make_photo(server / "temp-1" / "only_here.jpg", 51)
    (server / "temp-1" / "2019").mkdir(parents=True)
    shutil.copy2(a, server / "temp-1" / "2019" / "a.jpg")          # copy of a photo kept in 2019
    shutil.copy2(b, server / "temp-1" / "only_here_copy.jpg")      # both copies inside temp-1
    make_photo(server / "2019" / "unique.jpg", 52)

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        f"local:\n  paths: ['{server.as_posix()}']\n"
        f"server:\n  paths: ['{server.as_posix()}']\n  upload_path: '{(server / 'up').as_posix()}'\n",
        encoding="utf-8",
    )
    temp = (server / "temp-1").as_posix()

    assert main(["-c", str(cfg), "dedupe", "--from", temp]) == 0       # dry run
    assert (server / "temp-1" / "2019" / "a.jpg").exists()

    assert main(["-c", str(cfg), "dedupe", "--from", temp, "--execute"]) == 0
    review = server / "_duplicates_to_review" / "temp-1"
    assert not (server / "temp-1" / "2019" / "a.jpg").exists()
    assert (review / "2019" / "a.jpg").exists()
    assert (server / "2019" / "a.jpg").exists()                        # the kept copy
    # Both copies of only_here were inside temp-1: exactly one stays.
    left = [p.name for p in (server / "temp-1").glob("only_here*.jpg")]
    assert len(left) == 1
    assert (server / "2019" / "unique.jpg").exists()

    # The review folder is not counted as photos any more.
    assert main(["-c", str(cfg), "duplicates"]) == 0

    (log,) = (tmp_path / "reports").glob("dedupe-*.csv")
    assert main(["-c", str(cfg), "undo-dedupe", str(log)]) == 0
    assert (server / "temp-1" / "2019" / "a.jpg").exists()
    assert len(list((server / "temp-1").glob("only_here*.jpg"))) == 2


def test_dedupe_refuses_folders_outside_server(tmp_path):
    server = tmp_path / "nas"
    server.mkdir()
    other = tmp_path / "elsewhere"
    other.mkdir()
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        f"local:\n  paths: ['{server.as_posix()}']\n"
        f"server:\n  paths: ['{server.as_posix()}']\n  upload_path: '{(server / 'up').as_posix()}'\n",
        encoding="utf-8",
    )
    assert main(["-c", str(cfg), "dedupe", "--from", other.as_posix(), "--execute"]) == 2


def _img(path, sha="x", width=100):
    from photo_sync.scanner import ImageInfo
    p = path.replace("\\", "/")
    return ImageInfo(path=path, rel_path=p.split("/", 5)[-1], root="//NAS/home/Photos", name=p.rsplit("/", 1)[-1],
                     size=10, mtime=0, sha256=sha, width=width, height=width)


def test_plan_moves_keep_one_inside_and_never_move():
    from photo_sync.dedupe import plan_moves, review_target
    from photo_sync.duplicates import EXACT as D_EXACT, DuplicateGroup

    shared = _img("//NAS/photo/2019/a.jpg")
    lib_a = _img("//NAS/home/Photos/g-drive/a.jpg")
    lib_b = _img("//NAS/home/Photos/my-olimpus/a.jpg")
    phone = _img("//NAS/home/Photos/MobileBackup/a.jpg")
    group = DuplicateGroup(D_EXACT, [shared, lib_a, lib_b])
    lib = ["//NAS/home/Photos"]

    # Default: copies exist outside the library, so all library copies go.
    assert {p.source.path for p in plan_moves([group], lib)} == {lib_a.path, lib_b.path}
    # keep-one-inside: the library keeps exactly one copy.
    plans = plan_moves([group], lib, keep_one_inside=True)
    assert [p.source.path for p in plans] == [lib_b.path] and plans[0].kept is lib_a
    # The phone backup is never moved and becomes the copy that is kept.
    group2 = DuplicateGroup(D_EXACT, [lib_a, phone, lib_b])
    plans = plan_moves([group2], lib, never_move=["//NAS/home/Photos/MobileBackup"], keep_one_inside=True)
    assert {p.source.path for p in plans} == {lib_a.path, lib_b.path}
    assert all(p.kept is phone for p in plans)

    # On a NAS share the review folder is at the top of the share, outside the Photos library.
    assert review_target(lib_b).replace("\\", "/") == "//NAS/home/_duplicates_to_review/Photos/my-olimpus/a.jpg"
