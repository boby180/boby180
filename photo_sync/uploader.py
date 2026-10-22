"""Uploading missing photos to the Synology NAS.

Two methods are supported:

* ``copy``        - the shared folder is reachable as a normal folder (mapped
                    network drive such as ``Z:\\``, a UNC path such as
                    ``\\\\NAS\\photo``, or a mount point on Mac/Linux).
* ``filestation`` - upload through the Synology File Station Web API
                    (DSM 6/7), useful when the share is not mounted.

Existing files on the server are never overwritten: if the target name is
taken, a numeric suffix is added (``IMG_1.jpg`` -> ``IMG_1 (1).jpg``).
"""

from __future__ import annotations

import json
import os
import posixpath
import shutil
from pathlib import Path, PurePosixPath
from typing import Callable

import requests

from .compare import BETTER_COMPUTER, MISSING, MatchResult, better_copy
from .config import Config


def target_relative_path(result: MatchResult) -> str:
    """Path under the upload folder: <local root folder name>/<path inside it>."""
    root_name = Path(result.local.root).name or "photos"
    return posixpath.join(root_name, result.local.rel_path)


def _unique_name(name: str, exists: Callable[[str], bool]) -> str:
    if not exists(name):
        return name
    stem, ext = os.path.splitext(name)
    n = 1
    while exists(f"{stem} ({n}){ext}"):
        n += 1
    return f"{stem} ({n}){ext}"


class CopyUploader:
    def __init__(self, upload_path: str):
        self.base = Path(upload_path)

    def upload(self, local_path: str, rel_target: str) -> str:
        target = self.base / PurePosixPath(rel_target)
        target.parent.mkdir(parents=True, exist_ok=True)
        name = _unique_name(target.name, lambda n: (target.parent / n).exists())
        target = target.parent / name
        shutil.copy2(local_path, target)  # keeps the original modification time
        if os.path.getsize(target) != os.path.getsize(local_path):
            raise IOError(f"size mismatch after copy: {target}")
        return str(target)

    def close(self) -> None:
        pass


class FileStationError(Exception):
    pass


class FileStationUploader:
    """Minimal client for the Synology File Station API (SYNO.FileStation.*)."""

    def __init__(self, url: str, username: str, password: str, target_folder: str, verify_ssl: bool = True):
        self.url = url.rstrip("/")
        self.base = "/" + target_folder.strip("/")
        self.session = requests.Session()
        self.session.verify = verify_ssl
        self.sid = None
        self._paths = self._discover_paths()
        data = self._call(
            "SYNO.API.Auth", 6, "login",
            account=username, passwd=password, session="FileStation", format="sid",
        )
        self.sid = data["sid"]

    def _discover_paths(self) -> dict[str, str]:
        """Ask DSM which CGI serves each API (differs between DSM 6 and 7)."""
        resp = self.session.get(
            f"{self.url}/webapi/query.cgi",
            params={"api": "SYNO.API.Info", "version": 1, "method": "query",
                    "query": "SYNO.API.Auth,SYNO.FileStation.List,SYNO.FileStation.Upload"},
            timeout=30,
        )
        resp.raise_for_status()
        return {api: info["path"] for api, info in (resp.json().get("data") or {}).items()}

    def _endpoint(self, api: str) -> str:
        return f"{self.url}/webapi/{self._paths.get(api, 'entry.cgi')}"

    def _call(self, api: str, version: int, method: str, **params) -> dict:
        params = {"api": api, "version": version, "method": method, **params}
        if self.sid:
            params["_sid"] = self.sid
        resp = self.session.get(self._endpoint(api), params=params, timeout=60)
        resp.raise_for_status()
        body = resp.json()
        if not body.get("success"):
            raise FileStationError(f"{api}.{method} failed: {body.get('error')}")
        return body.get("data") or {}

    def _exists(self, path: str) -> bool:
        data = self._call("SYNO.FileStation.List", 2, "getinfo", path=json.dumps([path]))
        files = data.get("files") or []
        return bool(files) and "code" not in files[0]

    def upload(self, local_path: str, rel_target: str) -> str:
        folder = posixpath.dirname(posixpath.join(self.base, rel_target))
        name = _unique_name(posixpath.basename(rel_target), lambda n: self._exists(posixpath.join(folder, n)))
        mtime_ms = str(int(os.path.getmtime(local_path) * 1000))
        with open(local_path, "rb") as f:
            # Field order matters to DSM: the file part must come last.
            resp = self.session.post(
                self._endpoint("SYNO.FileStation.Upload"),
                params={"_sid": self.sid},
                data={
                    "api": "SYNO.FileStation.Upload",
                    "version": "2",
                    "method": "upload",
                    "path": folder,
                    "create_parents": "true",
                    "overwrite": "false",
                    "mtime": mtime_ms,
                },
                files={"file": (name, f, "application/octet-stream")},
                timeout=600,
            )
        resp.raise_for_status()
        body = resp.json()
        if not body.get("success"):
            raise FileStationError(f"upload failed: {body.get('error')}")
        return posixpath.join(folder, name)

    def close(self) -> None:
        try:
            self._call("SYNO.API.Auth", 6, "logout", session="FileStation")
        except Exception:
            pass


def make_uploader(config: Config):
    if config.server.upload_method == "filestation":
        fs = config.server.filestation
        return FileStationUploader(fs.url, fs.username, fs.password, fs.target_folder, fs.verify_ssl)
    return CopyUploader(config.server.upload_path)


def upload_missing(
    results: list[MatchResult],
    uploader,
    dry_run: bool = True,
    log: Callable[[str], None] = print,
    include_better: bool = False,
) -> list[dict]:
    """Upload every ``missing`` photo once (local duplicates are uploaded a single time).

    With ``include_better``, also upload photos whose server copy is of lower
    quality. The server copy is kept; the duplicates report can be used later
    to remove the weaker one.
    """
    done_hashes: dict[str, str] = {}
    records = []
    for r in results:
        better = include_better and better_copy(r) == BETTER_COMPUTER
        if r.status != MISSING and not better:
            continue
        rel = target_relative_path(r)
        reason = f"better quality than {r.server.path}" if better else "missing"
        record = {"local": r.local.path, "target": rel, "status": "", "detail": reason}
        if r.local.sha256 and r.local.sha256 in done_hashes:
            record.update(status="skipped", detail=f"duplicate of {done_hashes[r.local.sha256]}")
        elif dry_run:
            record.update(status="would-upload")
            log(f"[dry-run] {r.local.path} -> {rel}")
        else:
            try:
                record.update(status="uploaded", target=uploader.upload(r.local.path, rel))
                log(f"uploaded  {r.local.path} -> {record['target']}")
            except Exception as exc:
                record.update(status="failed", detail=f"{reason}; error: {exc}")
                log(f"FAILED    {r.local.path}: {exc}")
        if r.local.sha256 and record["status"] in ("uploaded", "would-upload"):
            done_hashes[r.local.sha256] = r.local.path
        records.append(record)
    return records
