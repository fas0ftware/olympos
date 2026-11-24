"""Settings from olympos.toml (machine-specific, not in git; see olympos.example.toml)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

STAGES = ("idea", "active", "maintained", "paused", "archived")


class ConfigError(ValueError):
    pass


@dataclass
class Site:
    name: str
    url: str


@dataclass
class ProjectNote:
    stage: str = ""          # one of STAGES; empty = guessed from activity
    note: str = ""           # free text shown under the project
    hidden: bool = False


@dataclass
class Settings:
    roots: list[Path] = field(default_factory=list)
    units: list[str] = field(default_factory=list)
    mnemosyne_status: Path = Path("~/.local/state/mnemosyne/status.json").expanduser()
    backup_max_age_hours: float = 3.0
    check_max_age_days: float = 3.0
    cerberus_url: str = ""
    pythia_status: Path | None = None   # Pythia's status file; None = not watched
    aegis_status: Path | None = None    # Aegis's status file; None = not watched
    aegis_max_age_days: float = 2.0
    themis_status: Path | None = None   # Themis's status file; None = not watched
    themis_max_age_minutes: float = 30.0
    disks: list[Path] = field(default_factory=list)
    disk_warn_percent: int = 85
    sites: list[Site] = field(default_factory=list)
    github: bool = True
    github_cache_seconds: int = 300
    projects: dict[str, ProjectNote] = field(default_factory=dict)


def default_path() -> Path:
    env = os.environ.get("OLYMPOS_CONFIG")
    if env:
        return Path(env).expanduser()
    here = Path(__file__).resolve().parent.parent / "olympos.toml"
    if here.exists():
        return here
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "olympos" / "olympos.toml"


def _list(raw, key, kind=str):
    value = raw.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, kind) for v in value):
        raise ConfigError(f"{key} must be a list of {kind.__name__}s")
    return value


def load(path: Path | None = None) -> Settings:
    path = path or default_path()
    if not path.exists():
        raise ConfigError(f"no settings file at {path}; copy olympos.example.toml there and edit it")
    try:
        raw = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from None
    return parse(raw)


def parse(raw: dict) -> Settings:
    s = Settings()
    s.roots = [Path(p).expanduser() for p in _list(raw, "roots")]
    s.units = _list(raw.get("services", {}), "units")
    b = raw.get("backups", {})
    if "mnemosyne_status" in b:
        s.mnemosyne_status = Path(b["mnemosyne_status"]).expanduser()
    s.backup_max_age_hours = float(b.get("max_age_hours", s.backup_max_age_hours))
    s.check_max_age_days = float(b.get("check_max_age_days", s.check_max_age_days))
    c = raw.get("cerberus", {})
    s.cerberus_url = str(c.get("url", "")).rstrip("/")
    if s.cerberus_url and not s.cerberus_url.startswith(("http://127.0.0.1:", "http://localhost:")):
        # It is read without a token, which Cerberus only allows on the machine itself.
        raise ConfigError("cerberus.url must be a local address (http://127.0.0.1:PORT)")
    py = raw.get("pythia", {})
    if py:
        s.pythia_status = Path(py.get("status_file", "~/.local/state/pythia/status.json")).expanduser()
    ae = raw.get("aegis", {})
    if ae:
        s.aegis_status = Path(ae.get("status_file", "~/.local/state/aegis/status.json")).expanduser()
        s.aegis_max_age_days = float(ae.get("max_age_days", s.aegis_max_age_days))
    th = raw.get("themis", {})
    if th:
        s.themis_status = Path(th.get("status_file", "~/.local/state/themis/status.json")).expanduser()
        s.themis_max_age_minutes = float(th.get("max_age_minutes", s.themis_max_age_minutes))
    d = raw.get("disks", {})
    s.disks = [Path(p).expanduser() for p in _list(d, "paths")]
    s.disk_warn_percent = int(d.get("warn_percent", s.disk_warn_percent))
    for site in raw.get("sites", []):
        url = str(site.get("url", ""))
        if not url.startswith(("https://", "http://")):
            raise ConfigError(f"site {site!r}: url must start with http:// or https://")
        s.sites.append(Site(str(site.get("name", url)), url))
    g = raw.get("github", {})
    s.github = bool(g.get("enabled", True))
    s.github_cache_seconds = int(g.get("cache_seconds", s.github_cache_seconds))
    for name, p in raw.get("projects", {}).items():
        stage = str(p.get("stage", ""))
        if stage and stage not in STAGES:
            raise ConfigError(f"projects.{name}.stage must be one of {', '.join(STAGES)}")
        s.projects[name.lower()] = ProjectNote(stage, str(p.get("note", "")), bool(p.get("hidden", False)))
    return s
