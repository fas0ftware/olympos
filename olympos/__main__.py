"""python -m olympos [status | json | serve]"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, config
from .health import human_age
from .state import State

COLORS = {"ok": "32", "info": "36", "warn": "33", "bad": "31"}
SYMBOLS = {"ok": "✓", "info": "·", "warn": "!", "bad": "✗"}


def paint(text: str, level: str) -> str:
    if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
        return text
    return f"\033[{COLORS.get(level, '0')}m{text}\033[0m"


def print_status(snap: dict, all_projects: bool) -> None:
    c = snap["counts"]
    print(paint(f"Olympos {snap['version']}: {snap['level'].upper()}", snap["level"]),
          f"· {c['projects']} projects · {c['public']} public / {c['private']} private on GitHub"
          f" · {c['bad']} problem(s), {c['warn']} warning(s)")
    if snap["findings"]:
        print()
    for f in snap["findings"]:
        if f["level"] == "info" and not all_projects:
            continue
        print(" ", paint(SYMBOLS[f["level"]], f["level"]), f"{f['subject']}: {f['text']}")
    print()
    for p in snap["projects"]:
        gh = p.get("github") or {}
        ci = {"SUCCESS": "ci ✓", "FAILURE": "ci ✗", "ERROR": "ci ✗", "PENDING": "ci …"}.get(gh.get("ci", ""), "")
        flags = " ".join(x for x in (
            f"{p['dirty']} changed" if p["dirty"] else "",
            f"{p['ahead']} unpushed" if p["ahead"] else "",
            ci, (gh.get("visibility") or "").lower(),
        ) if x)
        print(f"  {p['name']:<20} {p['version'] or '-':<8} {p['stage'] or '-':<11} {flags}")
    if snap["backups"]:
        print()
    for b in snap["backups"]:
        good = (b.get("last_good_backup") or {}).get("time")
        age = ""
        if good:
            t = datetime.fromisoformat(good.replace("Z", "+00:00"))
            age = human_age((datetime.now(timezone.utc) - t).total_seconds() / 3600) + " ago"
        print(f"  backup {b['repo']}: last good {age or 'never'}, {b['snapshots']} snapshots")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="olympos", description="How every project and home system is doing.")
    p.add_argument("--version", action="version", version=f"olympos {__version__}")
    p.add_argument("--config", type=Path, help="settings file (default: ./olympos.toml or ~/.config/olympos/olympos.toml)")
    sub = p.add_subparsers(dest="cmd")
    st = sub.add_parser("status", help="summary in the terminal (default)")
    st.add_argument("-a", "--all", action="store_true", help="also show notes (info level)")
    js = sub.add_parser("json", help="everything as JSON")
    sv = sub.add_parser("serve", help="the browser interface on 127.0.0.1")
    sv.add_argument("--port", type=int, default=8746)
    rec = sub.add_parser("record", help="save a history sample; notify when things got worse (run by a timer)")
    rec.add_argument("--no-notify", action="store_true")
    for sp in (st, js, sv, rec):
        sp.add_argument("--demo", action="store_true", help="fictional data, no settings needed")
    args = p.parse_args(argv)
    if getattr(args, "demo", False):
        from .demo import DemoState
        state = DemoState()
    else:
        try:
            settings = config.load(args.config)
        except config.ConfigError as exc:
            print(f"olympos: {exc}", file=sys.stderr)
            return 2
        state = State(settings)
    if args.cmd == "serve":
        from .web import serve
        serve(state, args.port)
        return 0
    snap = state.snapshot(wait_remote=True)
    if args.cmd == "record":
        from . import history
        path = history.default_path()
        past = history.read(path)
        current = history.sample(snap)
        history.append(path, current)
        if not args.no_notify and history.got_worse(past[-1] if past else None, current):
            history.notify(snap)
        print(f"recorded: {current['level']}, {current['bad']} problem(s), {current['warn']} warning(s)")
        return 0
    if args.cmd == "json":
        print(json.dumps(snap, indent=2))
    else:
        print_status(snap, getattr(args, "all", False))
    return {"ok": 0, "info": 0, "warn": 1, "bad": 3}[snap["level"]]


if __name__ == "__main__":
    sys.exit(main())
