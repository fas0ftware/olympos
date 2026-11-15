"""Weekly summary: what the history says about the last days, and what is still open now."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _sample_hours(rows: list[dict]) -> float:
    """Hours one sample stands for: the median gap between samples (15 min by default)."""
    times = []
    for r in rows:
        try:
            times.append(datetime.fromisoformat(r["t"].replace("Z", "+00:00")))
        except (KeyError, ValueError):
            pass
    gaps = sorted((b - a).total_seconds() / 3600 for a, b in zip(times, times[1:]) if b > a)
    return gaps[len(gaps) // 2] if gaps else 0.25


def _nums(rows: list[dict], key: str) -> list[float]:
    return [r[key] for r in rows if isinstance(r.get(key), (int, float))]


def build(rows: list[dict], snap: dict, days: float = 7, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    step = _sample_hours(rows)
    levels = [r.get("level", "ok") for r in rows]
    share = {lvl: round(100 * levels.count(lvl) / len(levels)) if levels else 0 for lvl in ("ok", "info", "warn", "bad")}
    share["ok"] += share.pop("info")
    first, last = (rows[0], rows[-1]) if rows else ({}, {})
    ages = _nums(rows, "backup_age_h")
    never = any(r.get("backup_age_h") is None for r in rows if "backup_age_h" in r)
    disk = _nums(rows, "disk_max_percent")
    blocked = _nums(rows, "pythia_blocked_24h")
    open_now = [f for f in snap.get("findings", []) if f["level"] in ("bad", "warn")]
    out = {
        "from": (now - timedelta(days=days)).isoformat(timespec="seconds"),
        "to": now.isoformat(timespec="seconds"),
        "samples": len(rows),
        "level_share": share,
        "problems_start": first.get("bad"),
        "problems_end": last.get("bad"),
        "problems_max": max(_nums(rows, "bad"), default=None),
        "hours_red": round(levels.count("bad") * step, 1),
        "ci_red_hours": round(sum(1 for r in rows if (r.get("ci_failing") or 0) > 0) * step, 1),
        "worst_backup_age_h": max(ages) if ages else None,
        "backup_never_succeeded": never and not ages,
        "disk_start": disk[0] if disk else None,
        "disk_end": disk[-1] if disk else None,
        "pythia_blocked_per_day": round(sum(blocked) / len(blocked)) if blocked else None,
        "still_open": [{"level": f["level"], "subject": f["subject"], "text": f["text"]} for f in open_now],
        "level_now": snap.get("level", "ok"),
    }
    out["verdict"] = verdict(out)
    return out


def verdict(s: dict) -> str:
    if not s["samples"]:
        return "no history yet"
    a, b = s["problems_start"] or 0, s["problems_end"] or 0
    if b < a:
        return "better"
    if b > a:
        return "worse"
    return "steady"


def text(s: dict) -> str:
    """Plain lines for the terminal and a notification."""
    if not s["samples"]:
        return "No history yet: `olympos record` (the timer) saves a sample every 15 minutes."
    lines = [f"Last {_days(s)}: {s['verdict']}. "
             f"Green {s['level_share']['ok']}%, yellow {s['level_share']['warn']}%, red {s['level_share']['bad']}% of the time."]
    if s["problems_start"] is not None:
        lines.append(f"Problems: {s['problems_start']} at the start, {s['problems_end']} now (at most {s['problems_max']}).")
    if s["ci_red_hours"]:
        lines.append(f"CI was red for about {s['ci_red_hours']:g} h.")
    if s["backup_never_succeeded"]:
        lines.append("Backups: none succeeded in this period.")
    elif s["worst_backup_age_h"] is not None:
        lines.append(f"Backups: the longest gap reached {s['worst_backup_age_h']:.1f} h.")
    if s["disk_start"] is not None:
        lines.append(f"Fullest disk: {s['disk_start']:g}% → {s['disk_end']:g}%.")
    if s["pythia_blocked_per_day"] is not None:
        lines.append(f"DNS filter: about {s['pythia_blocked_per_day']:,} blocked per day.")
    if s["still_open"]:
        lines.append("Still open:")
        lines += [f"  {'✗' if f['level'] == 'bad' else '!'} {f['subject']}: {f['text']}" for f in s["still_open"][:8]]
    else:
        lines.append("Nothing is open right now.")
    return "\n".join(lines)


def _days(s: dict) -> str:
    try:
        d = (datetime.fromisoformat(s["to"]) - datetime.fromisoformat(s["from"])).days
    except ValueError:
        d = 7
    return "24 hours" if d <= 1 else f"{d} days"

