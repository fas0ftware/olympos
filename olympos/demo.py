"""Fictional data for screenshots and trying the interface: python -m olympos serve --demo"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .config import Settings
from .state import build


def _iso(now: datetime, **delta) -> str:
    return (now - timedelta(**delta)).isoformat(timespec="seconds")


def _project(now, name, lang, version, stage, commit, **extra):
    p = {"name": name, "path": f"/home/user/projects/{name}", "language": lang, "version": version, "branch": "main",
         "dirty": 0, "ahead": 0, "behind": 0, "upstream": True, "last_commit": _iso(now, **commit),
         "last_subject": "Update README", "commits": 42, "remote": f"example/{name}", "next_step": "",
         "stage": stage, "stage_source": "activity", "note": "", "errors": []}
    p.update(extra)
    return p


def _repo(now, name, visibility="PUBLIC", ci="SUCCESS", **extra):
    r = {"owner": "example", "name": name, "visibility": visibility, "archived": False,
         "pushed_at": _iso(now, hours=5), "url": f"https://github.com/example/{name}", "ci": ci,
         "open_prs": 0, "open_issues": 0}
    r.update(extra)
    return r


def snapshot(now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    s = Settings(backup_max_age_hours=3, check_max_age_days=3, disk_warn_percent=85)
    projects = [
        _project(now, "atlas", "Python", "2.4.0", "active", {"hours": 3},
                 next_step="Export reports as PDF"),
        _project(now, "beacon", "Rust", "0.9.1", "active", {"days": 2}, ahead=2, dirty=3,
                 next_step="Windows support"),
        _project(now, "chronicle", "TypeScript", "1.2.0", "maintained", {"days": 40},
                 next_step="Phase 3: sync between devices"),
        _project(now, "delta", "C", "1.0.3", "active", {"days": 1}),
        _project(now, "ember", "Python", "0.1.0", "idea", {"days": 6}, remote="", upstream=False,
                 stage_source="config", note="prototype; decide whether to continue"),
        _project(now, "forge", "Rust", "3.0.0", "paused", {"days": 200}),
    ]
    repos = [
        _repo(now, "atlas"), _repo(now, "beacon", open_prs=1), _repo(now, "chronicle", "PRIVATE"),
        _repo(now, "delta", ci="FAILURE"), _repo(now, "forge", ci="", archived=True),
        _repo(now, "old-website", ci=""),
    ]
    units = [
        {"name": "mnemosyne-backup.timer", "description": "Mnemosyne backup every hour", "load": "loaded",
         "active": "active", "sub": "waiting", "result": "success", "enabled": "enabled",
         "last_run": _iso(now, minutes=24), "next_run": _iso(now, minutes=-36), "triggers": "mnemosyne-backup.service",
         "last_result": "success", "last_status": 0},
        {"name": "mnemosyne-maintain.timer", "description": "Mnemosyne maintenance once a day", "load": "loaded",
         "active": "active", "sub": "waiting", "result": "success", "enabled": "enabled",
         "last_run": _iso(now, hours=9), "next_run": _iso(now, hours=-15), "triggers": "mnemosyne-maintain.service",
         "last_result": "success", "last_status": 0},
        {"name": "cerberus-web.service", "description": "Cerberus dashboard (127.0.0.1:8470)", "load": "loaded",
         "active": "active", "sub": "running", "result": "success", "enabled": "enabled",
         "last_run": _iso(now, days=3), "next_run": "", "triggers": "", "last_result": "", "last_status": 0},
    ]
    ok = {"ok": True, "files": 41230, "bytes": 18_400_000_000, "added_bytes": 3_100_000, "warnings": 0, "duration_ms": 9100}
    backups = [
        {"repo": "/home/user/Mnemosyne", "last_backup": {**ok, "time": _iso(now, minutes=24)},
         "last_good_backup": {**ok, "time": _iso(now, minutes=24)},
         "last_check": {"time": _iso(now, hours=9), "ok": True, "bytes": 1_840_000_000, "read_data": "10%"},
         "snapshots": 61, "repo_bytes": 14_900_000_000},
        {"repo": "/media/backup-disk/Mnemosyne",
         "last_backup": {"time": _iso(now, minutes=24), "ok": False,
                         "error": "/media/backup-disk/Mnemosyne is not a Mnemosyne repository"},
         "last_good_backup": {**ok, "time": _iso(now, days=2)},
         "last_check": {"time": _iso(now, days=2), "ok": True, "bytes": 1_790_000_000, "read_data": "10%"},
         "snapshots": 58, "repo_bytes": 14_700_000_000},
    ]
    cerberus = {"version": "0.9.0", "network": "192.0.2.0/24", "last_sweep": _iso(now, minutes=3), "hosts": 9,
                "online": 7, "new_devices_24h": 1, "untrusted": 2, "open_ports": 14, "risky_ports": 1,
                "decoy_touches_24h": 0, "events_24h": 12, "level": "medium"}
    disks = [{"path": "/", "mounted": True, "total": 250_000_000_000, "used": 61_000_000_000,
              "free": 189_000_000_000, "percent": 24.4},
             {"path": "/media/backup-disk", "mounted": True, "total": 1_000_000_000_000, "used": 880_000_000_000,
              "free": 120_000_000_000, "percent": 88.0}]
    sites = [{"name": "Website", "url": "https://example.github.io/", "status": 200, "ms": 412, "error": "",
              "checked": _iso(now, minutes=2)}]
    local = {"projects": projects, "units": units, "backups": backups, "cerberus": cerberus, "cerberus_error": None,
             "disks": disks, "errors": []}
    remote = {"repos": repos, "sites": sites, "errors": [], "at": _iso(now, minutes=2)}
    return build(s, local, remote, now)


class DemoState:
    def snapshot(self, force: bool = False, wait_remote: bool = False) -> dict:
        return snapshot()
