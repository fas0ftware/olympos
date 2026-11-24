"""Systems: systemd user units, Mnemosyne backups, Cerberus, disks and websites."""

from __future__ import annotations

import json
import os
import shutil
import subprocess  # nosec B404
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# --- systemd user units ------------------------------------------------------------------

PROPS = ["Id", "Description", "ActiveState", "SubState", "Result", "ExecMainStatus", "UnitFileState",
         "LoadState", "Triggers", "LastTriggerUSec", "NextElapseUSecRealtime", "ExecMainExitTimestamp",
         "ActiveEnterTimestamp"]


@dataclass
class Unit:
    name: str
    description: str = ""
    load: str = ""
    active: str = ""          # active / inactive / failed / activating
    sub: str = ""             # running / waiting / dead / exited
    result: str = ""          # success / exit-code / ...
    enabled: str = ""
    last_run: str = ""        # ISO; timers: last trigger, services: last exit or start
    next_run: str = ""        # timers only
    triggers: str = ""        # timers: the service they start
    last_result: str = ""     # timers: result of that service's last run
    last_status: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def parse_show(text: str) -> list[dict]:
    """`systemctl show` output: blocks of key=value separated by blank lines."""
    blocks, cur = [], {}
    for line in text.splitlines():
        if not line.strip():
            if cur:
                blocks.append(cur)
                cur = {}
            continue
        k, _, v = line.partition("=")
        cur[k] = v
    if cur:
        blocks.append(cur)
    return blocks


def _ts(value: str) -> str:
    """systemctl --timestamp=unix gives "@1731690000"; empty or "n/a" when never."""
    value = value.strip()
    if value.startswith("@"):
        try:
            return datetime.fromtimestamp(int(value[1:]), timezone.utc).isoformat(timespec="seconds")
        except ValueError:
            return ""
    return ""


def _systemctl(*args: str) -> str:
    exe = shutil.which("systemctl")
    if not exe:
        raise RuntimeError("systemctl not found")
    out = subprocess.run([exe, "--user", "show", "--timestamp=unix", "-p", ",".join(PROPS), *args],  # nosec B603
                         capture_output=True, text=True, timeout=10, check=False)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip()[:200] or "systemctl failed")
    return out.stdout


def units(names: list[str], show=_systemctl) -> list[Unit]:
    if not names:
        return []
    blocks = parse_show(show(*names))
    result = []
    for b in blocks:
        u = Unit(name=b.get("Id", ""), description=b.get("Description", ""), load=b.get("LoadState", ""),
                 active=b.get("ActiveState", ""), sub=b.get("SubState", ""), result=b.get("Result", ""),
                 enabled=b.get("UnitFileState", ""), triggers=b.get("Triggers", "").split(" ")[0])
        try:
            u.last_status = int(b.get("ExecMainStatus", "0") or 0)
        except ValueError:
            pass
        if u.name.endswith(".timer"):
            u.last_run, u.next_run = _ts(b.get("LastTriggerUSec", "")), _ts(b.get("NextElapseUSecRealtime", ""))
        else:
            u.last_run = _ts(b.get("ExecMainExitTimestamp", "")) or _ts(b.get("ActiveEnterTimestamp", ""))
        result.append(u)
    # For timers, what matters is how the service they start ended last time.
    services = [u.triggers for u in result if u.name.endswith(".timer") and u.triggers]
    if services:
        by_name = {b.get("Id", ""): b for b in parse_show(show(*services))}
        for u in result:
            b = by_name.get(u.triggers)
            if b:
                u.last_result = b.get("Result", "")
                try:
                    u.last_status = int(b.get("ExecMainStatus", "0") or 0)
                except ValueError:
                    pass
    return result


# --- Mnemosyne ----------------------------------------------------------------------------

@dataclass
class Backup:
    repo: str
    last_backup: dict | None = None
    last_good_backup: dict | None = None
    last_check: dict | None = None
    snapshots: int = 0
    repo_bytes: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def backups(status_file: Path) -> list[Backup]:
    """Reads Mnemosyne's status file. Missing file = no backup has ever run: an empty list."""
    try:
        data = json.loads(status_file.read_text())
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"{status_file}: {exc}") from None
    out = []
    for repo, s in sorted((data.get("repos") or {}).items()):
        if not isinstance(s, dict):
            continue
        out.append(Backup(repo=repo, last_backup=s.get("last_backup"), last_good_backup=s.get("last_good_backup"),
                          last_check=s.get("last_check"), snapshots=int(s.get("snapshots") or 0),
                          repo_bytes=int(s.get("repo_bytes") or 0)))
    return out


# --- Cerberus ------------------------------------------------------------------------------

def _get_json(url: str, timeout: float = 5) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "olympos"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
        return json.loads(resp.read(1 << 20))


def cerberus(base_url: str, get=_get_json) -> dict:
    s = get(f"{base_url}/api/summary")
    keep = ("version", "network", "last_sweep", "hosts", "online", "new_devices_24h", "untrusted",
            "open_ports", "risky_ports", "decoy_touches_24h", "events_24h", "level")
    return {k: s.get(k) for k in keep}


# --- Pythia --------------------------------------------------------------------------------

def pythia(status_file: Path) -> dict | None:
    """Pythia's status file plus its age in seconds; None when it does not exist yet."""
    try:
        data = json.loads(status_file.read_text())
        age = time.time() - status_file.stat().st_mtime
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"{status_file}: {exc}") from None
    keep = ("version", "listening", "at_home", "blocking", "paused_until", "domains_on_lists", "queries_24h",
            "blocked_24h", "blocked_percent_24h", "clients", "errors_24h", "upstreams", "lists_updated")
    out = {k: data.get(k) for k in keep}
    out["age_s"] = int(age)
    return out


# --- Aegis ---------------------------------------------------------------------------------

def aegis(status_file: Path) -> dict | None:
    """Aegis's last saved audit: the score and the checks that did not pass (no fixes, no
    details: those can hold paths and package names). None when no audit was saved yet."""
    try:
        data = json.loads(status_file.read_text())
        age = time.time() - status_file.stat().st_mtime
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"{status_file}: {exc}") from None
    if not isinstance(data, dict):
        raise RuntimeError(f"{status_file}: not an Aegis status file")
    open_checks = []
    for c in data.get("checks") or []:
        if isinstance(c, dict) and c.get("status") in ("fail", "warn"):
            open_checks.append({k: str(c.get(k) or "") for k in ("id", "title", "severity", "status", "summary")})
    score = data.get("score")
    return {"version": data.get("version"), "time": data.get("time"),
            "score": score if isinstance(score, int) else None, "verdict": data.get("verdict"),
            "counts": data.get("counts") or {}, "open": open_checks, "age_s": int(age)}


SEVERITY_RANK = ("info", "low", "medium", "high", "critical")


def themis(status_file: Path) -> dict | None:
    """Themis's last scan: verdict, counts and the newest events (Themis already reduced their
    text to capped printable ASCII; it is capped again here). None before the first --save."""
    try:
        data = json.loads(status_file.read_text())
        age = time.time() - status_file.stat().st_mtime
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"{status_file}: {exc}") from None
    if not isinstance(data, dict):
        raise RuntimeError(f"{status_file}: not a Themis status file")
    recent = []
    for e in data.get("recent") or []:
        if isinstance(e, dict) and e.get("severity") in SEVERITY_RANK:
            recent.append({"time": str(e.get("time") or "")[:20], "kind": str(e.get("kind") or "")[:32],
                           "severity": e["severity"], "message": str(e.get("message") or "")[:300]})
    counts = data.get("counts") if isinstance(data.get("counts"), dict) else {}
    return {"version": data.get("version"), "time": data.get("time"),
            "verdict": data.get("verdict") if data.get("verdict") in ("quiet", "watch", "alert") else None,
            "level": data.get("level") if data.get("level") in SEVERITY_RANK else None,
            "counts": {k: v for k, v in counts.items() if k in SEVERITY_RANK and isinstance(v, int)},
            "recent": recent[:10], "known_usb": data.get("known_usb"), "known_services": data.get("known_services"),
            "age_s": int(age)}


HESTIA_STATUS = ("ok", "unknown", "warn", "bad")


def hestia(status_file: Path) -> dict | None:
    """Hestia's last health check: overall status and every check (id, title, status, summary,
    at most five details, all capped). None before the first --save."""
    try:
        data = json.loads(status_file.read_text())
        age = time.time() - status_file.stat().st_mtime
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"{status_file}: {exc}") from None
    if not isinstance(data, dict):
        raise RuntimeError(f"{status_file}: not a Hestia status file")
    checks = []
    for c in data.get("checks") or []:
        if isinstance(c, dict) and c.get("status") in HESTIA_STATUS:
            details = [str(d)[:160] for d in (c.get("details") or []) if isinstance(d, str)][:5]
            checks.append({"id": str(c.get("id") or "")[:32], "title": str(c.get("title") or "")[:40],
                           "status": c["status"], "summary": str(c.get("summary") or "")[:200], "details": details})
    counts = data.get("counts") if isinstance(data.get("counts"), dict) else {}
    return {"version": data.get("version"), "time": data.get("time"),
            "status": data.get("status") if data.get("status") in HESTIA_STATUS else None,
            "counts": {k: v for k, v in counts.items() if k in HESTIA_STATUS and isinstance(v, int)},
            "checks": checks, "age_s": int(age)}


# --- disks ---------------------------------------------------------------------------------

@dataclass
class Disk:
    path: str
    mounted: bool = False
    total: int = 0
    used: int = 0
    free: int = 0
    percent: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def disks(paths: list[Path]) -> list[Disk]:
    out = []
    for p in paths:
        d = Disk(path=str(p))
        if p.exists() and (str(p) == "/" or os.path.ismount(p)):
            u = shutil.disk_usage(p)
            d.mounted, d.total, d.used, d.free = True, u.total, u.used, u.free
            d.percent = round(100 * u.used / u.total, 1) if u.total else 0.0
        out.append(d)
    return out


# --- websites ------------------------------------------------------------------------------

@dataclass
class SiteCheck:
    name: str
    url: str
    status: int = 0
    ms: int = 0
    error: str = ""
    checked: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))

    def to_dict(self) -> dict:
        return asdict(self)


def check_site(name: str, url: str, timeout: float = 10) -> SiteCheck:
    c = SiteCheck(name, url)
    start = time.monotonic()
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "olympos"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
            c.status = resp.status
    except urllib.error.HTTPError as exc:
        c.status = exc.code
    except (urllib.error.URLError, OSError, ValueError) as exc:
        c.error = str(getattr(exc, "reason", exc))[:200]
    c.ms = int((time.monotonic() - start) * 1000)
    return c
