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


def _notify_summary(s: dict, body: str) -> None:
    import shutil
    import subprocess  # nosec B404
    exe = shutil.which("notify-send")
    if exe:
        from .history import markup_escape
        subprocess.run([exe, "-a", "Olympos", "-i", "dialog-information", "--",  # nosec B603
                        f"Olympos: the last week ({s['verdict']})", markup_escape(body[:900])], check=False, timeout=10)


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
    sm = sub.add_parser("summary", help="what the last days looked like and what is still open")
    sm.add_argument("--days", type=float, default=7)
    sm.add_argument("--notify", action="store_true", help="also as a desktop notification")
    sm.add_argument("--json", action="store_true")
    rec = sub.add_parser("record", help="save a history sample; notify when things got worse (run by a timer)")
    rec.add_argument("--no-notify", action="store_true")
    tk = sub.add_parser("tasks", help="what to do about the findings; --push sends them to Iris")
    tk.add_argument("--push", action="store_true", help="send the list to Iris ([tasks] iris in the settings)")
    tk.add_argument("--json", action="store_true")
    tk.add_argument("--lang", choices=("en", "tr"), help="default: [tasks] lang")
    for sp in (st, js, sv, rec, sm, tk):
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
    if args.cmd == "summary":
        from . import history, summary
        days = max(0.1, min(args.days, history.KEEP_DAYS))
        rows = state.history(days) if hasattr(state, "history") else history.read(history.default_path(), days)
        s = summary.build(rows, snap, days)
        body = summary.text(s)
        print(json.dumps(s, indent=2) if args.json else body)
        if args.notify:
            _notify_summary(s, body)
        return 0
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
    if args.cmd == "tasks":
        from . import tasks
        st_ = getattr(state, "s", None)
        lang = args.lang or (st_.tasks_lang if st_ else "en")
        items = tasks.from_snapshot(snap, lang)
        if args.json:
            print(json.dumps(items, indent=2, ensure_ascii=False))
        else:
            for t in items:
                print(f"{t['priority']:<7} {t['title']}  ({t['id']})")
                if t["command"]:
                    print(f"        $ {t['command']}")
            if not items:
                print("nothing to do")
        if args.push:
            url = st_.tasks_iris if st_ else ""
            if not url:
                print("olympos: set [tasks] iris = \"http://127.0.0.1:8090\" in the settings", file=sys.stderr)
                return 2
            try:
                r = tasks.push(url, items)
            except (OSError, ValueError) as exc:
                print(f"olympos: could not send the tasks to Iris: {exc}", file=sys.stderr)
                return 1
            print(f"Iris: {r.get('new', 0)} new, {r.get('reopened', 0)} reopened, {r.get('gone', 0)} resolved")
        return 0
    if args.cmd == "json":
        print(json.dumps(snap, indent=2))
    else:
        print_status(snap, getattr(args, "all", False))
    return {"ok": 0, "info": 0, "warn": 1, "bad": 3}[snap["level"]]


if __name__ == "__main__":
    sys.exit(main())
