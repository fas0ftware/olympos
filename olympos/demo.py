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
         "last_subject": "Update README", "commits": 42, "remote": f"fas0ftware/{name}", "next_step": "",
         "stage": stage, "stage_source": "activity", "note": "", "errors": []}
    p.update(extra)
    return p


def _repo(now, name, visibility="PUBLIC", ci="SUCCESS", **extra):
    r = {"owner": "fas0ftware", "name": name, "visibility": visibility, "archived": False,
         "pushed_at": _iso(now, hours=5), "url": f"https://github.com/fas0ftware/{name}", "ci": ci,
         "open_prs": 0, "open_issues": 0}
    r.update(extra)
    return r


def snapshot(now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    s = Settings(backup_max_age_hours=3, check_max_age_days=3, disk_warn_percent=85)
    # The author's own public projects (names, versions, roadmaps as on GitHub); paths, times
    # and the machine around them are made up.
    projects = [
        _project(now, "ariadne", "TypeScript", "1.1.1", "active", {"hours": 2},
                 next_step="Phase 2: milestones (Category → Project → Sub-milestone, monthly/weekly/daily views)"),
        _project(now, "daedalus", "Python", "0.2.0", "active", {"days": 1}),
        _project(now, "janus", "C", "1.9.0", "active", {"days": 1},
                 next_step="Integration as the scanning engine of Lynceus (via --json; Cerberus already uses it)"),
        _project(now, "JUDA", "Python", "0.2.2", "active", {"days": 4}),
        _project(now, "lynceus", "Python", "2.2.0", "active", {"days": 1}, next_step="Docker image"),
        _project(now, "maze", "Python", "0.3.1", "active", {"days": 6}),
        _project(now, "mnemosyne", "Rust", "0.1.0", "active", {"hours": 1},
                 next_step="Packs: many small chunks in one file (fewer files on slow USB sticks and network shares)."),
        _project(now, "olympos", "Python", "0.3.0", "active", {"minutes": 20}, ahead=1, dirty=2,
                 next_step="Tests and coverage per project, read from the CI artifacts."),
        _project(now, "Project-Enigma", "Python", "2.0.0", "maintained", {"days": 35}),
        _project(now, "Project_Hugnin", "Python", "3.1.0", "active", {"days": 2}),
        _project(now, "Project_Orion", "Python", "2.1.0", "active", {"days": 2}),
        _project(now, "Zarathustra_TTS", "Python", "2.1.0", "maintained", {"days": 20}),
        _project(now, "pythia", "Python", "0.1.0", "active", {"hours": 3}, remote="", upstream=False,
                 next_step="Encrypted upstream (DNS over TLS) so the provider cannot read the names."),
    ]
    repos = [_repo(now, p["name"]) for p in projects if p["remote"]] + [_repo(now, "fas0ftware", ci="")]
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
    sites = [{"name": "FA Software website", "url": "https://fas0ftware.github.io/FA_Software_web/", "status": 200, "ms": 412, "error": "",
              "checked": _iso(now, minutes=2)}]
    pythia = {"version": "0.1.0", "listening": ["127.0.0.1:53", "192.168.1.10:53"], "at_home": True, "blocking": True,
              "paused_until": "", "domains_on_lists": 77309, "queries_24h": 18240, "blocked_24h": 3310,
              "blocked_percent_24h": 18.1, "clients": 7, "errors_24h": 2,
              "upstreams": [{"server": "1.1.1.1:53", "failures_in_a_row": 0}], "lists_updated": _iso(now, hours=6),
              "age_s": 12}
    local = {"projects": projects, "units": units, "backups": backups, "cerberus": cerberus, "cerberus_error": None,
             "pythia": pythia, "pythia_configured": True,
             "disks": disks, "errors": []}
    remote = {"repos": repos, "sites": sites, "errors": [], "at": _iso(now, minutes=2)}
    return build(s, local, remote, now)


def history(days: float = 7, now: datetime | None = None) -> list[dict]:
    """A plausible week: a backup disk unplugged for two days, a CI failure fixed, a
    growing disk, more ads blocked in the evenings."""
    import math
    now = now or datetime.now(timezone.utc)
    out = []
    steps = int(days * 24 * 4)
    for i in range(steps, -1, -1):
        t = now - timedelta(minutes=15 * i)
        h = t.hour + t.minute / 60
        unplugged = 48 >= i / 4 >= 0      # last two days
        ci_red = 120 >= i / 4 >= 100
        bad = (2 if unplugged else 0) + (1 if ci_red else 0)
        evening = 0.5 + 0.5 * math.sin((h - 15) / 24 * 2 * math.pi)
        out.append({
            "t": t.isoformat(timespec="seconds"),
            "level": "bad" if bad else "warn",
            "bad": bad, "warn": 3, "projects": 13, "ci_failing": int(ci_red), "unpushed": 1 if 30 < i / 4 < 70 or i < 8 else 0,
            "uncommitted": 1, "backup_age_h": round((48 - i / 4) if unplugged else (i % 4) * 0.25 + 0.2, 2),
            "disk_max_percent": round(84 + 4 * (steps - i) / steps, 1),
            "cerberus_online": 6 + int(evening * 3), "cerberus_risky": 1,
            "pythia_queries_24h": int(15000 + 3000 * evening), "pythia_blocked_24h": int(2600 + 900 * evening),
        })
    return out


class DemoState:
    def snapshot(self, force: bool = False, wait_remote: bool = False) -> dict:
        return snapshot()

    def history(self, days: float = 7) -> list[dict]:
        return history(days)
