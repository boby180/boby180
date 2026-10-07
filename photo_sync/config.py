"""Loading and validating the YAML configuration file."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_EXTENSIONS = [
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff", ".webp",
    ".heic", ".heif",
]

# Synology system folders (thumbnails, recycle bin) and OS junk that must never be compared.
DEFAULT_EXCLUDE = ["@eaDir", "#recycle", "#snapshot", ".DS_Store", "Thumbs.db", "desktop.ini"]


class ConfigError(Exception):
    pass


@dataclass
class FileStationConfig:
    url: str = ""
    username: str = ""
    password_env: str = "SYNOLOGY_PASSWORD"
    target_folder: str = ""
    verify_ssl: bool = True

    @property
    def password(self) -> str:
        value = os.environ.get(self.password_env, "")
        if not value:
            raise ConfigError(
                f"Environment variable {self.password_env} is empty - set it to the Synology password"
            )
        return value


@dataclass
class ServerConfig:
    path: str = ""
    upload_subfolder: str = "uploaded-from-pc"
    upload_method: str = "copy"  # "copy" (mapped drive / UNC / mount) or "filestation"
    filestation: FileStationConfig = field(default_factory=FileStationConfig)


@dataclass
class CompareConfig:
    extensions: list[str] = field(default_factory=lambda: list(DEFAULT_EXTENSIONS))
    exclude: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDE))
    use_perceptual_hash: bool = True
    similarity_threshold: int = 6
    workers: int = 4


@dataclass
class OutputConfig:
    report_dir: str = "reports"
    cache_file: str = ".photo_sync_cache.sqlite"


@dataclass
class Config:
    local_paths: list[str]
    server: ServerConfig
    compare: CompareConfig = field(default_factory=CompareConfig)
    output: OutputConfig = field(default_factory=OutputConfig)

    def validate(self, require_existing_paths: bool = True) -> None:
        if not self.local_paths:
            raise ConfigError("local.paths is empty - add at least one folder on the computer")
        if not self.server.path:
            raise ConfigError("server.path is empty - set the path to the Synology shared folder")
        if self.server.upload_method not in ("copy", "filestation"):
            raise ConfigError("server.upload_method must be 'copy' or 'filestation'")
        if self.server.upload_method == "filestation":
            fs = self.server.filestation
            if not (fs.url and fs.username and fs.target_folder):
                raise ConfigError("filestation requires url, username and target_folder")
        if not 0 <= self.compare.similarity_threshold <= 64:
            raise ConfigError("compare.similarity_threshold must be between 0 and 64")
        if require_existing_paths:
            for p in [*self.local_paths, self.server.path]:
                if not Path(p).is_dir():
                    raise ConfigError(f"Folder not found or not accessible: {p}")


def _section(data: dict, name: str) -> dict:
    value = data.get(name) or {}
    if not isinstance(value, dict):
        raise ConfigError(f"Section '{name}' must be a mapping")
    return value


def load_config(path: str | Path) -> Config:
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path} (copy config.example.yaml to config.yaml)")
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    local = _section(data, "local")
    server = _section(data, "server")
    compare = _section(data, "compare")
    output = _section(data, "output")

    try:
        return _build_config(path, local, server, compare, output)
    except TypeError as exc:  # unknown key in one of the sections
        raise ConfigError(f"Invalid config key: {exc}") from exc


def _build_config(path: Path, local: dict, server: dict, compare: dict, output: dict) -> Config:
    local_paths = local.get("paths") or []
    if isinstance(local_paths, str):
        local_paths = [local_paths]

    fs = FileStationConfig(**_section(server, "filestation"))
    server_cfg = ServerConfig(
        **{k: v for k, v in server.items() if k != "filestation" and v is not None},
        filestation=fs,
    )
    compare_cfg = CompareConfig(**{k: v for k, v in compare.items() if v is not None})
    compare_cfg.extensions = [
        e.lower() if e.startswith(".") else "." + e.lower() for e in compare_cfg.extensions
    ]
    output_cfg = OutputConfig(**{k: v for k, v in output.items() if v is not None})

    # Relative report/cache paths are resolved next to the config file.
    base = path.resolve().parent
    output_cfg.report_dir = str(base / output_cfg.report_dir)
    output_cfg.cache_file = str(base / output_cfg.cache_file)

    return Config(
        local_paths=[str(p) for p in local_paths],
        server=server_cfg,
        compare=compare_cfg,
        output=output_cfg,
    )
