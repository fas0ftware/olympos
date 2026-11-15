"""Collect everything into one JSON-able snapshot, with caching.

Local data (git, units, disks, backups, Cerberus) is cheap and refreshed every LOCAL_TTL
seconds. GitHub and website checks go over the internet: they are refreshed in a
background thread every `github_cache_seconds`, so a page load never waits for them.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from . import __version__, github, health, projects, systems
from .config import Settings

LOCAL_TTL = 20


class State:
    def __init__(self, settings: Settings, *, fetch_github=github.fetch, check_site=systems.check_site,
                 show_units=None, get_json=None):
        self.s = settings
        self._fetch_github = fetch_github
        self._check_site = check_site
        self._show_units = show_units
        self._get_json = get_json
        self._lock = threading.Lock()
        self._remote_lock = threading.Lock()
        self._local: dict | None = None
        self._local_at = 0.0
        self._remote: dict = {"repos": [], "sites": [], "errors": [], "at": ""}
        self._remote_at: float | None = None   # never fetched (not 0.0: monotonic time starts at boot)
        self._remote_running = False

    # --- remote (GitHub, sites) ------------------------------------------------------------

    def refresh_remote(self, owners: list[str]) -> None:
        repos, errors = [], []
        if self.s.github:
            for owner in owners:
                try:
                    repos += [r.to_dict() for r in self._fetch_github(owner)]
                except (RuntimeError, OSError) as exc:
                    errors.append(f"GitHub ({owner}): {exc}")
        with ThreadPoolExecutor(max_workers=4) as pool:
            sites = [c.to_dict() for c in pool.map(lambda st: self._check_site(st.name, st.url), self.s.sites)]
        with self._remote_lock:
            self._remote = {"repos": repos, "sites": sites, "errors": errors,
                            "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            self._remote_at = time.monotonic()
            self._remote_running = False

    def _maybe_refresh_remote(self, owners: list[str], force: bool, wait: bool) -> None:
        with self._remote_lock:
            stale = force or self._remote_at is None or time.monotonic() - self._remote_at > self.s.github_cache_seconds
            if not stale or self._remote_running:
                return
            self._remote_running = True
        if wait:
            self.refresh_remote(owners)
        else:
            threading.Thread(target=self.refresh_remote, args=(owners,), daemon=True).start()

    # --- local -----------------------------------------------------------------------------

    def _collect_local(self) -> dict:
        errors = []
        items = [p.to_dict() for p in projects.collect(self.s.roots, self.s.projects)]
        try:
            kw = {"show": self._show_units} if self._show_units else {}
            units = [u.to_dict() for u in systems.units(self.s.units, **kw)]
        except (RuntimeError, OSError) as exc:
            units, errors = [], errors + [f"systemd: {exc}"]
        try:
            backups = [b.to_dict() for b in systems.backups(self.s.mnemosyne_status)]
        except RuntimeError as exc:
            backups, errors = [], errors + [f"Mnemosyne: {exc}"]
        cerberus, cerberus_error = None, None
        if self.s.cerberus_url:
            try:
                kw = {"get": self._get_json} if self._get_json else {}
                cerberus = systems.cerberus(self.s.cerberus_url, **kw)
            except (OSError, ValueError) as exc:
                cerberus_error = str(getattr(exc, "reason", exc))[:200]
        return {"projects": items, "units": units, "backups": backups, "cerberus": cerberus,
                "cerberus_error": cerberus_error, "disks": [d.to_dict() for d in systems.disks(self.s.disks)],
                "errors": errors}

    def snapshot(self, force: bool = False, wait_remote: bool = False) -> dict:
        with self._lock:
            if force or self._local is None or time.monotonic() - self._local_at > LOCAL_TTL:
                self._local = self._collect_local()
                self._local_at = time.monotonic()
            local = self._local
        owners = sorted({p["remote"].split("/")[0] for p in local["projects"] if p.get("remote")})
        self._maybe_refresh_remote(owners, force, wait_remote)
        with self._remote_lock:
            remote = dict(self._remote)
        return build(self.s, local, remote)


def build(s: Settings, local: dict, remote: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    repos = {f"{r['owner']}/{r['name']}".lower(): r for r in remote["repos"]}
    local_remotes = {p["remote"].lower() for p in local["projects"] if p.get("remote")}
    for p in local["projects"]:
        r = repos.get((p.get("remote") or "").lower())
        p["github"] = r
        if r and r.get("archived") and p["stage_source"] == "activity":
            p["stage"] = "archived"
    remote_only = [r for key, r in sorted(repos.items()) if key not in local_remotes]
    expects_backups = any(u["name"].startswith("mnemosyne") for u in local["units"]) or bool(local["backups"])
    findings = (
        health.projects(local["projects"], repos if remote["repos"] else {})
        + health.units(local["units"])
        + health.backups(local["backups"], s.backup_max_age_hours, s.check_max_age_days, now, expects_backups)
        + health.cerberus(local["cerberus"], local["cerberus_error"])
        + health.disks(local["disks"], s.disk_warn_percent)
        + health.sites(remote["sites"])
        + [health.Finding("warn", "olympos", "collector", e) for e in local["errors"] + remote["errors"]]
    )
    findings = health.sort(findings)
    stages: dict[str, int] = {}
    for p in local["projects"]:
        stages[p["stage"] or "unknown"] = stages.get(p["stage"] or "unknown", 0) + 1
    return {
        "version": __version__,
        "generated": now.isoformat(timespec="seconds"),
        "github_checked": remote["at"],
        "level": health.worst(findings),
        "findings": [f.to_dict() for f in findings],
        "counts": {
            "projects": len(local["projects"]),
            "stages": stages,
            "public": sum(1 for r in remote["repos"] if r.get("visibility") == "PUBLIC"),
            "private": sum(1 for r in remote["repos"] if r.get("visibility") == "PRIVATE"),
            "ci_failing": sum(1 for r in remote["repos"] if r.get("ci") in ("FAILURE", "ERROR")),
            "open_prs": sum(r.get("open_prs", 0) for r in remote["repos"]),
            "bad": sum(1 for f in findings if f.level == "bad"),
            "warn": sum(1 for f in findings if f.level == "warn"),
        },
        "projects": local["projects"],
        "remote_only": remote_only,
        "units": local["units"],
        "backups": local["backups"],
        "cerberus": local["cerberus"],
        "cerberus_error": local["cerberus_error"],
        "disks": local["disks"],
        "sites": remote["sites"],
    }
