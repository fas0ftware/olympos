"""Turn the collected state into a short list of things that need attention.

Each finding has a level (ok < info < warn < bad), an area and a sentence that says what
to do. The overall level is the worst finding.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone

LEVELS = ("ok", "info", "warn", "bad")


@dataclass
class Finding:
    level: str
    area: str
    subject: str
    text: str

    def to_dict(self) -> dict:
        return asdict(self)


def _age_hours(iso: str | None, now: datetime) -> float | None:
    if not iso:
        return None
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return (now - t).total_seconds() / 3600


def human_age(hours: float) -> str:
    if hours < 1:
        return f"{max(1, int(hours * 60))} min"
    if hours < 48:
        return f"{hours:.0f} h"
    return f"{hours / 24:.0f} days"


def projects(items: list[dict], repos: dict[str, dict]) -> list[Finding]:
    out = []
    for p in items:
        name = p["name"]
        for err in p.get("errors", []):
            out.append(Finding("warn", "project", name, f"git: {err}"))
        if p.get("dirty"):
            out.append(Finding("info", "project", name, f"{p['dirty']} uncommitted change(s)"))
        if p.get("ahead"):
            out.append(Finding("warn", "project", name, f"{p['ahead']} commit(s) not pushed"))
        if p.get("behind"):
            out.append(Finding("info", "project", name, f"{p['behind']} commit(s) on GitHub not pulled"))
        if p.get("tests") and p.get("tests_behind", 0) >= 5:
            out.append(Finding("info", "project", name,
                               f"test image is {p['tests_behind']} commits behind the code (docs/test-results.svg)"))
        if p.get("branch") and p["branch"] not in ("main", "master", "HEAD"):
            out.append(Finding("info", "project", name, f"working on branch {p['branch']}"))
        r = repos.get((p.get("remote") or "").lower())
        if r:
            if r.get("ci") in ("FAILURE", "ERROR"):
                jobs = r.get("failed_checks") or []
                which = f": {', '.join(jobs[:4])}" + (" …" if len(jobs) > 4 else "") if jobs else ""
                out.append(Finding("bad", "github", name, f"CI is failing on the default branch{which}"))
            if r.get("open_prs"):
                out.append(Finding("info", "github", name, f"{r['open_prs']} open pull request(s)"))
        elif p.get("remote") and repos:
            out.append(Finding("warn", "github", name, f"remote {p['remote']} not found on GitHub"))
        elif not p.get("remote"):
            out.append(Finding("info", "github", name, "no GitHub repository yet"))
    return out


def units(items: list[dict]) -> list[Finding]:
    out = []
    for u in items:
        name = u["name"]
        if u.get("load") == "not-found":
            out.append(Finding("bad", "service", name, "unit not installed"))
            continue
        is_timer = name.endswith(".timer")
        if u.get("active") == "failed":
            out.append(Finding("bad", "service", name, f"failed ({u.get('result')})"))
        elif is_timer and u.get("active") != "active":
            out.append(Finding("warn", "service", name, f"timer not running ({u.get('active')})"))
        elif not is_timer and u.get("active") == "inactive" and u.get("enabled") == "enabled":
            # An enabled service should be up; one-shot jobs started by timers are "static".
            out.append(Finding("warn", "service", name, "enabled but not running"))
        if name.endswith(".timer") and u.get("last_result") not in ("", "success"):
            out.append(Finding("bad", "service", name,
                               f"last run of {u.get('triggers')} failed (exit {u.get('last_status')})"))
    return out


def backups(items: list[dict], max_age_hours: float, check_max_age_days: float, now: datetime,
            expected: bool) -> list[Finding]:
    out = []
    if not items:
        if expected:
            out.append(Finding("bad", "backup", "Mnemosyne", "no backup has run yet"))
        return out
    for b in items:
        repo = b["repo"]
        last, good, check = b.get("last_backup") or {}, b.get("last_good_backup") or {}, b.get("last_check") or {}
        if last and not last.get("ok"):
            out.append(Finding("bad", "backup", repo, f"last backup failed: {last.get('error') or 'unknown error'}"))
        age = _age_hours(good.get("time"), now)
        if age is None:
            out.append(Finding("bad", "backup", repo, "no successful backup yet"))
        elif age > max_age_hours:
            out.append(Finding("bad", "backup", repo, f"last good backup is {human_age(age)} old"))
        if last.get("ok") and last.get("warnings"):
            out.append(Finding("info", "backup", repo, f"{last['warnings']} file(s) could not be read in the last backup"))
        cage = _age_hours(check.get("time"), now)
        if check and not check.get("ok"):
            out.append(Finding("bad", "backup", repo, f"verification found problems: {check.get('error') or ''}".strip()))
        elif good and (cage is None or cage > check_max_age_days * 24):
            out.append(Finding("warn", "backup", repo, "data not verified recently (mnemosyne check)"))
    return out


def cerberus(summary: dict | None, error: str | None) -> list[Finding]:
    if error:
        return [Finding("warn", "network", "Cerberus", f"dashboard not reachable: {error}")]
    if not summary:
        return []
    out = []
    level = summary.get("level")
    if level in ("high", "critical"):
        out.append(Finding("bad", "network", "Cerberus", f"{level} events in the last 24 h"))
    if summary.get("decoy_touches_24h"):
        out.append(Finding("bad", "network", "Cerberus", f"{summary['decoy_touches_24h']} decoy touch(es) in 24 h"))
    if summary.get("risky_ports"):
        out.append(Finding("warn", "network", "Cerberus", f"{summary['risky_ports']} risky port(s) open on the network"))
    if summary.get("new_devices_24h"):
        out.append(Finding("info", "network", "Cerberus", f"{summary['new_devices_24h']} new device(s) in 24 h"))
    return out


def pythia(status: dict | None, configured: bool) -> list[Finding]:
    if not configured:
        return []
    if status is None:
        return [Finding("warn", "dns", "Pythia", "not running yet (no status file)")]
    if status.get("age_s", 0) > 180:
        return [Finding("bad", "dns", "Pythia", f"stopped: last status {human_age(status['age_s'] / 3600)} ago")]
    out = []
    if not status.get("blocking"):
        out.append(Finding("warn", "dns", "Pythia", "blocking is paused"))
    if (status.get("domains_on_lists") or 0) < 1000:
        out.append(Finding("warn", "dns", "Pythia", "block lists are empty (update failed?)"))
    for u in status.get("upstreams") or []:
        if u.get("failures_in_a_row", 0) >= 3:
            out.append(Finding("warn", "dns", "Pythia", f"upstream {u.get('server')} is failing"))
    if status.get("at_home") is False:
        out.append(Finding("info", "dns", "Pythia", "away from home: serving this machine only"))
    return out


def aegis(status: dict | None, configured: bool, max_age_days: float) -> list[Finding]:
    if not configured:
        return []
    if status is None:
        return [Finding("warn", "security", "Aegis", "no audit saved yet (python -m aegis --save)")]
    out = []
    if status.get("age_s", 0) > max_age_days * 86400:
        out.append(Finding("warn", "security", "Aegis", f"last audit is {human_age(status['age_s'] / 3600)} old"))
    warns = 0
    for c in status.get("open") or []:
        if c.get("status") == "fail":
            level = "bad" if c.get("severity") == "high" else "warn"
            out.append(Finding(level, "security", c.get("title") or c.get("id") or "?", c.get("summary") or "failed"))
        else:
            warns += 1
    if warns:
        out.append(Finding("info", "security", "Aegis", f"{warns} check(s) with a warning (score {status.get('score')})"))
    return out


def disks(items: list[dict], warn_percent: int) -> list[Finding]:
    out = []
    for d in items:
        if not d.get("mounted"):
            out.append(Finding("bad", "disk", d["path"], "not mounted"))
        elif d["percent"] >= max(warn_percent, 95):
            out.append(Finding("bad", "disk", d["path"], f"{d['percent']:.0f}% full"))
        elif d["percent"] >= warn_percent:
            out.append(Finding("warn", "disk", d["path"], f"{d['percent']:.0f}% full"))
    return out


def sites(items: list[dict]) -> list[Finding]:
    out = []
    for s in items:
        if s.get("error"):
            out.append(Finding("bad", "site", s["name"], f"unreachable: {s['error']}"))
        elif not 200 <= s.get("status", 0) < 400:
            out.append(Finding("bad", "site", s["name"], f"HTTP {s.get('status')}"))
    return out


def worst(findings: list[Finding]) -> str:
    return max((f.level for f in findings), key=LEVELS.index, default="ok")


def sort(findings: list[Finding]) -> list[Finding]:
    return sorted(findings, key=lambda f: (-LEVELS.index(f.level), f.area, f.subject.lower()))
