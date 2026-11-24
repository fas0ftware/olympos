"""History: one small sample per run of `olympos record` (a timer every 15 minutes),
appended to a JSON-lines file, and a desktop notification when things get worse.

A sample holds numbers only (no project names, paths or addresses), so the file is
safe to keep for months: 90 days by default, trimmed as it grows.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess  # nosec B404
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .health import LEVELS

KEEP_DAYS = 90


def default_path() -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return base / "olympos" / "history.jsonl"


def _hours_since(iso: str | None, now: datetime) -> float | None:
    if not iso:
        return None
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return round((now - t).total_seconds() / 3600, 2)


def sample(snap: dict, now: datetime | None = None) -> dict:
    """The numbers worth drawing over weeks, from one Olympos snapshot."""
    now = now or datetime.now(timezone.utc)
    c = snap["counts"]
    ages = [_hours_since((b.get("last_good_backup") or {}).get("time"), now) for b in snap["backups"]]
    mounted = [d["percent"] for d in snap["disks"] if d.get("mounted")]
    cer = snap.get("cerberus") or {}
    py = snap.get("pythia") or {}
    ae = snap.get("aegis") or {}
    th = snap.get("themis") or {}
    return {
        "t": now.isoformat(timespec="seconds"),
        "level": snap["level"],
        "bad": c["bad"],
        "warn": c["warn"],
        "projects": c["projects"],
        "ci_failing": c["ci_failing"],
        "unpushed": sum(1 for p in snap["projects"] if p.get("ahead")),
        "uncommitted": sum(1 for p in snap["projects"] if p.get("dirty")),
        # the backup that is furthest behind decides; None when a repository never succeeded
        "backup_age_h": None if not ages or None in ages else max(ages),
        "disk_max_percent": max(mounted) if mounted else None,
        "cerberus_online": cer.get("online"),
        "cerberus_risky": cer.get("risky_ports"),
        "pythia_queries_24h": py.get("queries_24h"),
        "pythia_blocked_24h": py.get("blocked_24h"),
        "aegis_score": ae.get("score"),
        "themis_events_24h": (sum(v for k, v in th["counts"].items() if k in ("medium", "high", "critical"))
                              if th.get("counts") is not None else None),
    }


def append(path: Path, record: dict, keep_days: int = KEEP_DAYS, now: datetime | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, separators=(",", ":")) + "\n")
    # Trim now and then (a sample every 15 min is ~100 lines a day).
    if path.stat().st_size > 512 * 1024:
        cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=keep_days)).isoformat(timespec="seconds")
        kept = [r for r in read(path) if r.get("t", "") >= cutoff]
        tmp = path.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in kept))
        os.replace(tmp, path)


def read(path: Path, days: float | None = None, now: datetime | None = None) -> list[dict]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    cutoff = None
    if days is not None:
        cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=days)).isoformat(timespec="seconds")
    out = []
    for line in lines:
        try:
            r = json.loads(line)
        except ValueError:
            continue          # a half-written last line after a crash
        if isinstance(r, dict) and (cutoff is None or r.get("t", "") >= cutoff):
            out.append(r)
    return out


def got_worse(previous: dict | None, current: dict) -> bool:
    """Worse level, or the same bad level with more problems."""
    if previous is None:
        return current["level"] == "bad"
    a, b = LEVELS.index(previous.get("level", "ok")), LEVELS.index(current["level"])
    return b > a or (b == a and current["level"] in ("warn", "bad") and current["bad"] > previous.get("bad", 0))


def notify(snap: dict, send=None) -> bool:
    """Desktop notification with the top findings. Returns True when one was shown."""
    top = [f for f in snap["findings"] if f["level"] in ("bad", "warn")][:4]
    title = "Olympos: " + ("problems" if snap["level"] == "bad" else "warnings")
    body = "\n".join(f"• {f['subject']}: {f['text']}" for f in top) or "Something changed."
    if send is not None:
        send(title, body)
        return True
    exe = shutil.which("notify-send")
    if not exe:
        return False
    urgency = "critical" if snap["level"] == "bad" else "normal"
    subprocess.run([exe, "-a", "Olympos", "-u", urgency, "-i", "dialog-warning", title, body[:900]],  # nosec B603
                   check=False, timeout=10)
    return True
