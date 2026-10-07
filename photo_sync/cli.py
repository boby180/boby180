"""Command line interface.

    python -m photo_sync check   --config config.yaml   # verify config and paths
    python -m photo_sync compare --config config.yaml   # scan both sides and write a report
    python -m photo_sync upload  --config config.yaml   # dry run: show what would be uploaded
    python -m photo_sync upload  --config config.yaml --execute   # really upload
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .cache import ScanCache
from .compare import EXACT, MISSING, SIMILAR, compare_images, summarize
from .config import Config, ConfigError, load_config
from .enhance import enhance_folder
from .duplicates import EXACT as DUP_EXACT, find_duplicates
from .report import write_compare_report, write_enhance_report, write_duplicates_report, write_upload_report
from .scanner import ImageInfo, imagehash, scan_folder
from .uploader import make_uploader, upload_missing


# English in the console: the Windows console prints Hebrew reversed. The HTML report is in Hebrew.
CONSOLE_LABELS = {
    EXACT: "on server - identical copy",
    SIMILAR: "on server - same photo, other version",
    MISSING: "missing on server",
}


def _progress(label: str):
    def report(done: int, total: int) -> None:
        print(f"\r  {label}: {done}/{total}", end="" if done < total else "\n", flush=True)

    return report


def _scan(config: Config, cache: ScanCache, paths: list[str], label: str) -> list[ImageInfo]:
    images: list[ImageInfo] = []
    for path in paths:
        print(f"Scanning {label}: {path}")
        images += scan_folder(
            path,
            config.compare.extensions,
            config.compare.exclude,
            cache=cache,
            use_perceptual_hash=config.compare.use_perceptual_hash,
            workers=config.compare.workers,
            progress=_progress(label),
        )
    return images


def _run_compare(config: Config):
    if config.compare.use_perceptual_hash and imagehash is None:
        print("warning: ImageHash is not installed - visual similarity is disabled", file=sys.stderr)
    with ScanCache(config.output.cache_file) as cache:
        local = _scan(config, cache, config.local_paths, "computer")
        server = _scan(config, cache, config.server_scan_paths(), "server")
    results = compare_images(local, server, config.compare.similarity_threshold)
    summary = summarize(results)
    print()
    print(f"Photos on computer: {len(local)}   photos on server: {len(server)}")
    for status in (EXACT, SIMILAR, MISSING):
        print(f"  {CONSOLE_LABELS[status]:<38} {summary[status]}")
        if status == SIMILAR:
            print(f"    {'of these, better quality on computer':<36} {summary['better_on_computer']}")
    if summary["errors"]:
        print(f"  read errors: {summary['errors']}")
    return results


def cmd_check(config: Config, args) -> int:
    config.validate()
    print("Config OK")
    print("  computer:", ", ".join(config.local_paths))
    print("  server:  ", ", ".join(config.server.paths))
    target = config.server.filestation.target_folder if config.server.upload_method == "filestation" \
        else config.server.upload_path
    print("  upload:  ", config.server.upload_method, "->", target)
    if config.server.upload_method == "filestation":
        uploader = make_uploader(config)  # logs in, raises on bad credentials
        uploader.close()
        print("  File Station login OK")
    return 0


def cmd_compare(config: Config, args) -> int:
    config.validate()
    results = _run_compare(config)
    paths = write_compare_report(results, config.output.report_dir)
    print(f"\nReport: {paths['html']}\n        {paths['csv']}")
    return 0


def cmd_upload(config: Config, args) -> int:
    config.validate()
    results = _run_compare(config)
    dry_run = not args.execute
    uploader = None if dry_run else make_uploader(config)
    try:
        records = upload_missing(results, uploader, dry_run=dry_run, include_better=args.include_better)
    finally:
        if uploader is not None:
            uploader.close()
    report = write_upload_report(records, config.output.report_dir, dry_run)
    counts: dict[str, int] = {}
    for r in records:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print("\nUpload summary:", ", ".join(f"{k}={v}" for k, v in counts.items()) or "nothing to upload")
    print(f"Report: {report}")
    if dry_run and records:
        print("This was a dry run. Run again with --execute to actually upload.")
    return 1 if counts.get("failed") else 0


def cmd_duplicates(config: Config, args) -> int:
    config.validate()
    paths = {
        "server": config.server_scan_paths(),
        "computer": config.local_paths,
        "all": [*config.local_paths, *config.server_scan_paths()],
    }[args.where]
    with ScanCache(config.output.cache_file) as cache:
        images = _scan(config, cache, paths, args.where)
    print("Looking for duplicates...")
    groups = find_duplicates(
        images,
        config.compare.similarity_threshold,
        include_similar=config.compare.use_perceptual_hash,
        progress=_progress("comparing"),
    )
    exact = [g for g in groups if g.kind == DUP_EXACT]
    similar = [g for g in groups if g.kind != DUP_EXACT]
    wasted_mb = sum(g.wasted_bytes for g in exact) / 1024 / 1024
    print()
    print(f"Photos checked: {len(images)}")
    print(f"  identical copies:     {len(exact)} groups, {sum(len(g.extra) for g in exact)} extra files, {wasted_mb:.0f} MB")
    print(f"  same photo, other version: {len(similar)} groups")
    paths = write_duplicates_report(groups, len(images), config.output.report_dir)
    print(f"\nReport (nothing was deleted): {paths['html']}\n                              {paths['csv']}")
    return 0


def cmd_enhance(config: Config, args) -> int:
    source = Path(args.source)
    if not source.is_dir():
        raise ConfigError(f"Folder not found: {source}")
    output = Path(args.output) if args.output else Path(config.output.report_dir).parent / "enhanced"
    print(f"Enhancing underwater photos in: {source}")
    print(f"Corrected copies go to:         {output / source.name}  (originals are not changed)")
    results = enhance_folder(
        source, output, config.compare.extensions, config.compare.exclude,
        strength=args.strength, only_underwater=not args.all, progress=_progress("photos"),
    )
    counts: dict[str, int] = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    print("\nSummary:", ", ".join(f"{k}={v}" for k, v in counts.items()) or "no photos found")
    report = write_enhance_report(results, config.output.report_dir)
    print(f"Before/after report: {report}")
    return 1 if counts.get("failed") else 0


def main(argv: list[str] | None = None) -> int:
    # Hebrew file names must not crash printing on a Windows console with a legacy code page.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(
        prog="photo_sync",
        description="Compare photos on this computer with a Synology NAS and upload what is missing.",
    )
    parser.add_argument("--config", "-c", default="config.yaml", help="path to config file")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="validate config and access to the folders")
    sub.add_parser("compare", help="compare and write an HTML/CSV report")
    dup = sub.add_parser("duplicates", help="find duplicate photos (report only, nothing is deleted)")
    dup.add_argument("--where", choices=["server", "computer", "all"], default="server",
                     help="where to look for duplicates (default: server)")
    enh = sub.add_parser("enhance", help="colour-correct underwater photos into a separate folder")
    enh.add_argument("source", help="folder with the photos to enhance")
    enh.add_argument("--output", help="where to write the corrected copies (default: 'enhanced' next to the config)")
    enh.add_argument("--strength", type=float, default=1.0, help="0.5 = gentle, 1 = normal (default), 1.3 = strong")
    enh.add_argument("--all", action="store_true", help="also process photos that do not look underwater")
    up = sub.add_parser("upload", help="upload photos that are missing on the server")
    up.add_argument("--execute", action="store_true", help="really upload (default is a dry run)")
    up.add_argument("--include-better", action="store_true",
                    help="also upload photos whose copy on the server is of lower quality")
    args = parser.parse_args(argv)

    handlers = {"check": cmd_check, "compare": cmd_compare, "upload": cmd_upload, "duplicates": cmd_duplicates, "enhance": cmd_enhance}
    try:
        config = load_config(args.config)
        return handlers[args.command](config, args)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nInterrupted", file=sys.stderr)
        return 130
