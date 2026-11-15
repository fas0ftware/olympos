"""Local projects: every git repository under the configured roots.

Everything here is read-only and offline: no fetch, so "ahead/behind" is relative to the
last time the repository talked to its remote.
"""

from __future__ import annotations

import json
import re
import subprocess  # nosec B404
import tomllib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

GIT_TIMEOUT = 15
# A repository's own config could name a program to run (core.fsmonitor) on `git status`.
# Olympos only reads, so those are switched off for every call.
SAFE = ("-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false", "--no-pager")
ACTIVE_DAYS = 14
MAINTAINED_DAYS = 90


@dataclass
class Project:
    name: str
    path: str
    language: str = ""
    version: str = ""
    branch: str = ""
    dirty: int = 0                # changed or untracked files
    ahead: int = 0                # commits not pushed
    behind: int = 0               # commits on the remote not pulled (as of the last fetch)
    upstream: bool = False
    last_commit: str = ""         # ISO time
    last_subject: str = ""
    commits: int = 0
    remote: str = ""              # "owner/name" on GitHub, if any
    next_step: str = ""           # first open item of the README roadmap
    stage: str = ""
    stage_source: str = ""        # "config" or "activity"
    note: str = ""
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def git(path: Path, *args: str) -> str:
    out = subprocess.run(["git", *SAFE, "-C", str(path), *args], capture_output=True, text=True,  # nosec B603 B607
                         timeout=GIT_TIMEOUT, check=False)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip().splitlines()[-1] if out.stderr.strip() else f"git {args[0]} failed")
    return out.stdout


def find_repos(roots: list[Path], depth: int = 2) -> list[Path]:
    found = []

    def walk(d: Path, level: int):
        if (d / ".git").exists():
            found.append(d)
            return
        if level >= depth:
            return
        try:
            children = sorted(c for c in d.iterdir() if c.is_dir() and not c.name.startswith("."))
        except OSError:
            return
        for c in children:
            walk(c, level + 1)

    for r in roots:
        walk(r, 0)
    unique = []
    for f in found:   # overlapping roots must not list a repository twice
        if f.resolve() not in {u.resolve() for u in unique}:
            unique.append(f)
    return unique


REMOTE = re.compile(r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?/?$")


def github_remote(url: str) -> str:
    m = REMOTE.search(url.strip())
    return f"{m.group(1)}/{m.group(2)}" if m else ""


VERSION_BADGE = re.compile(r"badge/version-([0-9][0-9A-Za-z.\-]*?)-[a-z]+\)")
INIT_VERSION = re.compile(r"""^__version__\s*=\s*["']([^"']+)["']""", re.M)


def detect_language(path: Path) -> str:
    if (path / "Cargo.toml").exists():
        return "Rust"
    if (path / "package.json").exists():
        return "TypeScript" if (path / "tsconfig.json").exists() else "JavaScript"
    if (path / "pyproject.toml").exists() or (path / "requirements.txt").exists() or any(path.glob("*.py")) \
            or any(path.glob("*/__init__.py")):
        return "Python"
    if any(path.glob("*.c")) or any(path.glob("src/*.c")):
        return "C"
    if (path / "index.html").exists():
        return "HTML"
    return ""


def detect_version(path: Path) -> str:
    def toml(p: Path) -> dict:
        try:
            return tomllib.loads(p.read_text())
        except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
            return {}

    cargo = toml(path / "Cargo.toml")
    v = cargo.get("package", {}).get("version") or cargo.get("workspace", {}).get("package", {}).get("version")
    if not v:
        for member in cargo.get("workspace", {}).get("members", []):
            v = toml(path / member / "Cargo.toml").get("package", {}).get("version")
            if v:
                break
    if not v:
        v = toml(path / "pyproject.toml").get("project", {}).get("version")
    if not v and (path / "package.json").exists():
        try:
            v = json.loads((path / "package.json").read_text()).get("version")
        except (OSError, ValueError):
            v = None
    if not v:
        for init in sorted(path.glob("*/__init__.py")):
            m = INIT_VERSION.search(init.read_text(errors="replace"))
            if m:
                v = m.group(1)
                break
    if not v and (path / "README.md").exists():
        m = VERSION_BADGE.search((path / "README.md").read_text(errors="replace"))
        v = m.group(1) if m else None
    return str(v) if isinstance(v, str) else ""


ROADMAP = re.compile(r"^#{2,3}\s+(roadmap|yol haritası|next steps?|todo)\b.*$", re.I | re.M)
DONE = re.compile(r"\bdone\b|✅|\(shipped\)", re.I)
ITEM = re.compile(r"^\s*(?:[-*]|\d+\.)\s+(?:\[( |x|X)\]\s+)?(.+)$")


def next_step(readme: str) -> str:
    """First open item under a Roadmap heading: not struck through (~~...~~), not checked [x]."""
    m = ROADMAP.search(readme)
    if not m:
        return ""
    for line in readme[m.end():].splitlines():
        if line.startswith("#"):
            break
        item = ITEM.match(line)
        if not item:
            continue
        box, text = item.group(1), item.group(2).strip()
        if (box and box.lower() == "x") or text.startswith("~~") or DONE.search(text):
            continue
        text = re.sub(r"\*\*|__|`", "", text)
        text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
        return text[:160]
    return ""


def stage_from_activity(last_commit: str, now: datetime) -> str:
    try:
        t = datetime.fromisoformat(last_commit)
    except ValueError:
        return ""
    days = (now - t).total_seconds() / 86400
    if days <= ACTIVE_DAYS:
        return "active"
    if days <= MAINTAINED_DAYS:
        return "maintained"
    return "paused"


def inspect(path: Path, notes: dict, now: datetime | None = None) -> Project:
    now = now or datetime.now(timezone.utc)
    p = Project(name=path.name, path=str(path))
    p.language = detect_language(path)
    p.version = detect_version(path)
    try:
        # symbolic-ref also works before the first commit; detached HEAD falls back to a hash.
        sym = subprocess.run(["git", *SAFE, "-C", str(path), "symbolic-ref", "--short", "-q", "HEAD"],  # nosec B603 B607
                             capture_output=True, text=True, timeout=GIT_TIMEOUT, check=False)
        p.branch = sym.stdout.strip() or git(path, "rev-parse", "--short", "HEAD").strip()
        p.dirty = len([line for line in git(path, "status", "--porcelain").splitlines() if line.strip()])
        has_commits = subprocess.run(["git", *SAFE, "-C", str(path), "rev-parse", "--verify", "-q", "HEAD"],  # nosec B603 B607
                                     capture_output=True, timeout=GIT_TIMEOUT, check=False).returncode == 0
        log = git(path, "log", "-1", "--format=%cI%x00%s").strip() if has_commits else ""
        if log:
            p.last_commit, _, p.last_subject = log.partition("\x00")
            p.commits = int(git(path, "rev-list", "--count", "HEAD").strip() or 0)
    except (RuntimeError, subprocess.TimeoutExpired, ValueError) as exc:
        p.errors.append(str(exc)[:200])
    try:
        counts = git(path, "rev-list", "--left-right", "--count", "@{u}...HEAD").split()
        p.behind, p.ahead = int(counts[0]), int(counts[1])
        p.upstream = True
    except (RuntimeError, subprocess.TimeoutExpired, ValueError, IndexError):
        pass   # no upstream: a local-only project
    try:
        p.remote = github_remote(git(path, "remote", "get-url", "origin"))
    except (RuntimeError, subprocess.TimeoutExpired):
        pass
    readme = path / "README.md"
    if readme.exists():
        p.next_step = next_step(readme.read_text(errors="replace"))
    note = notes.get(p.name.lower())
    if note and note.stage:
        p.stage, p.stage_source = note.stage, "config"
    else:
        p.stage, p.stage_source = stage_from_activity(p.last_commit, now), "activity"
    if note:
        p.note = note.note
    return p


def collect(roots: list[Path], notes: dict) -> list[Project]:
    repos = [r for r in find_repos(roots) if not (notes.get(r.name.lower()) and notes[r.name.lower()].hidden)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        projects = list(pool.map(lambda r: inspect(r, notes), repos))
    return sorted(projects, key=lambda p: p.name.lower())
