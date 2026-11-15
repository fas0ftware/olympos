import json
import subprocess
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from olympos import config, demo, github, health, projects, state, systems
from olympos.web import create_app

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)


def iso(**d):
    return (NOW - timedelta(**d)).isoformat()


# --- config -----------------------------------------------------------------------------

def test_config_parse_and_validation(tmp_path):
    s = config.parse({"roots": ["~/x"], "services": {"units": ["a.timer"]}, "cerberus": {"url": "http://127.0.0.1:8470/"},
                      "sites": [{"name": "w", "url": "https://example.org"}],
                      "projects": {"Old": {"stage": "archived", "note": "n"}}})
    assert s.roots[0].name == "x" and s.units == ["a.timer"] and s.cerberus_url == "http://127.0.0.1:8470"
    assert s.projects["old"].stage == "archived" and s.sites[0].url == "https://example.org"
    for bad in ({"cerberus": {"url": "http://192.0.2.5:8470"}}, {"sites": [{"url": "file:///etc/passwd"}]},
                {"projects": {"x": {"stage": "done"}}}, {"roots": "not a list"}):
        with pytest.raises(config.ConfigError):
            config.parse(bad)
    with pytest.raises(config.ConfigError):
        config.load(tmp_path / "missing.toml")
    (tmp_path / "o.toml").write_text('roots = ["/a"]\n[github]\nenabled = false\n')
    assert config.load(tmp_path / "o.toml").github is False


def test_example_config_parses():
    example = Path(__file__).resolve().parent.parent / "olympos.example.toml"
    assert config.load(example).roots


# --- projects ---------------------------------------------------------------------------

def git(path, *args):
    subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.org", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@example.org", "PATH": "/usr/bin:/bin", "HOME": str(path)})


def make_repo(path: Path, files: dict) -> Path:
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "main")
    for name, text in files.items():
        (path / name).parent.mkdir(parents=True, exist_ok=True)
        (path / name).write_text(text)
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "first")
    return path


def test_inspect_repo(tmp_path):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    r = make_repo(tmp_path / "root" / "tool", {
        "Cargo.toml": '[package]\nname = "tool"\nversion = "1.4.2"\n',
        "README.md": "# Tool\n\n## Roadmap\n\n- ~~**v1:** done~~\n- [x] shipped thing\n- Phase 2 – done: setup\n"
                     "- **v2:** sync with [the server](https://x)\n- v3\n",
    })
    git(r, "remote", "add", "origin", str(origin))
    git(r, "push", "-q", "-u", "origin", "main")
    (r / "new.txt").write_text("x")
    git(r, "add", "new.txt")
    git(r, "commit", "-q", "-m", "second")
    (r / "dirty.txt").write_text("y")

    notes = {"tool": config.ProjectNote(note="hello")}
    p = projects.inspect(r, notes)
    assert (p.language, p.version, p.branch) == ("Rust", "1.4.2", "main")
    assert (p.dirty, p.ahead, p.behind, p.upstream, p.commits) == (1, 1, 0, True, 2)
    assert p.last_subject == "second" and p.stage == "active" and p.note == "hello"
    assert p.next_step == "v2: sync with the server"
    assert p.remote == ""   # a local path, not GitHub

    empty = tmp_path / "root" / "fresh"
    empty.mkdir()
    git(empty, "init", "-q", "-b", "main")
    q = projects.inspect(empty, {})
    assert q.errors == [] and q.branch == "main" and q.last_commit == ""

    found = projects.find_repos([tmp_path / "root", tmp_path / "root" / "tool"])
    assert sorted(f.name for f in found) == ["fresh", "tool"], "overlapping roots list a repo once"


def test_detect_version_sources(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "pyproject.toml").write_text('[project]\nname="a"\nversion="0.3.0"\n')
    (tmp_path / "b" / "pkg").mkdir(parents=True)
    (tmp_path / "b" / "pkg" / "__init__.py").write_text('__version__ = "2.1.0"\n')
    (tmp_path / "c").mkdir()
    (tmp_path / "c" / "package.json").write_text('{"version": "1.1.1"}')
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "README.md").write_text("![Version](https://img.shields.io/badge/version-0.9.0-blue)")
    (tmp_path / "e" / "core").mkdir(parents=True)
    (tmp_path / "e" / "Cargo.toml").write_text('[workspace]\nmembers = ["core"]\n')
    (tmp_path / "e" / "core" / "Cargo.toml").write_text('[package]\nname="core"\nversion="0.3.0"\n')
    got = [projects.detect_version(tmp_path / x) for x in "abcde"]
    assert got == ["0.3.0", "2.1.0", "1.1.1", "0.9.0", "0.3.0"]
    assert projects.detect_language(tmp_path / "b") == "Python"


def test_github_remote_and_stage():
    assert projects.github_remote("git@github.com:me/tool.git") == "me/tool"
    assert projects.github_remote("https://github.com/me/tool") == "me/tool"
    assert projects.github_remote("https://gitlab.com/me/tool") == ""
    assert projects.stage_from_activity(iso(days=3), NOW) == "active"
    assert projects.stage_from_activity(iso(days=30), NOW) == "maintained"
    assert projects.stage_from_activity(iso(days=300), NOW) == "paused"


# --- github -----------------------------------------------------------------------------

def test_github_parse():
    data = {"data": {"repositoryOwner": {"repositories": {"nodes": [
        {"name": "a", "visibility": "PUBLIC", "isArchived": False, "pushedAt": "x", "url": "https://github.com/me/a",
         "defaultBranchRef": {"name": "main", "target": {"statusCheckRollup": {"state": "FAILURE"}}},
         "pullRequests": {"totalCount": 2}, "issues": {"totalCount": 1}},
        {"name": "b", "visibility": "PRIVATE", "defaultBranchRef": None, "pullRequests": None},
        "junk", {"visibility": "PUBLIC"},
    ]}}}}
    repos = github.parse("me", data)
    assert [(r.name, r.ci, r.open_prs) for r in repos] == [("a", "FAILURE", 2), ("b", "", 0)]
    assert repos[0].full == "me/a"
    assert github.parse("me", {"errors": ["x"]}) == []


# --- systems ----------------------------------------------------------------------------

SHOW = {
    "x.timer": "Id=x.timer\nActiveState=active\nSubState=waiting\nResult=success\nUnitFileState=enabled\n"
               "LoadState=loaded\nTriggers=x.service\nLastTriggerUSec=@1763200000\nNextElapseUSecRealtime=\n",
    "x.service": "Id=x.service\nActiveState=inactive\nSubState=dead\nResult=exit-code\nExecMainStatus=1\nLoadState=loaded\n",
    "w.service": "Id=w.service\nActiveState=active\nSubState=running\nResult=success\nUnitFileState=enabled\n"
                 "LoadState=loaded\nActiveEnterTimestamp=@1763100000\n",
    "gone.service": "Id=gone.service\nLoadState=not-found\nActiveState=inactive\n",
}


def fake_show(*names):
    return "\n".join(SHOW[n] for n in names)


def test_units_and_their_findings():
    us = systems.units(["x.timer", "w.service", "gone.service"], show=fake_show)
    t, w, g = us
    assert (t.last_result, t.last_status, t.triggers) == ("exit-code", 1, "x.service")
    assert t.last_run.startswith("2025-11-15") and w.last_run and g.load == "not-found"
    f = health.units([u.to_dict() for u in us])
    assert [(x.level, x.subject) for x in f] == [("bad", "x.timer"), ("bad", "gone.service")]


def test_backups_file(tmp_path):
    assert systems.backups(tmp_path / "none.json") == []
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"version": 1, "repos": {"/r": {"snapshots": 3, "last_backup": {"ok": True}}}}))
    [b] = systems.backups(p)
    assert b.repo == "/r" and b.snapshots == 3
    p.write_text("{broken")
    with pytest.raises(RuntimeError):
        systems.backups(p)


def test_cerberus_and_disks(tmp_path):
    got = systems.cerberus("http://127.0.0.1:1", get=lambda url: {"level": "high", "risky_ports": 2, "secret": "x"})
    assert got["level"] == "high" and "secret" not in got
    [root, missing] = systems.disks([Path("/"), tmp_path / "not-mounted"])
    assert root.mounted and root.total > 0 and not missing.mounted


# --- health -----------------------------------------------------------------------------

def test_backup_health():
    good = {"time": iso(hours=1), "ok": True}
    fresh = [{"repo": "/a", "last_backup": good, "last_good_backup": good, "last_check": {"time": iso(hours=5), "ok": True}}]
    assert health.backups(fresh, 3, 3, NOW, True) == []
    stale = [{"repo": "/a", "last_backup": {"time": iso(hours=1), "ok": False, "error": "disk gone"},
              "last_good_backup": {"time": iso(days=2), "ok": True}, "last_check": {"time": iso(days=9), "ok": True}}]
    texts = [(f.level, f.text) for f in health.backups(stale, 3, 3, NOW, True)]
    assert ("bad", "last backup failed: disk gone") in texts
    assert ("bad", "last good backup is 2 days old") in texts
    assert any(lvl == "warn" and "verified" in t for lvl, t in texts)
    assert health.backups([], 3, 3, NOW, True)[0].level == "bad"
    assert health.backups([], 3, 3, NOW, False) == []


def test_project_health_and_worst():
    repos = {"me/a": {"ci": "FAILURE", "open_prs": 1}}
    items = [{"name": "a", "dirty": 2, "ahead": 1, "behind": 0, "branch": "main", "remote": "me/a", "errors": []},
             {"name": "b", "dirty": 0, "ahead": 0, "behind": 0, "branch": "main", "remote": "", "errors": []}]
    f = health.projects(items, repos)
    levels = {(x.subject, x.level, x.text) for x in f}
    assert ("a", "bad", "CI is failing on the default branch") in levels
    assert ("a", "warn", "1 commit(s) not pushed") in levels
    assert ("b", "info", "no GitHub repository yet") in levels
    assert health.worst(f) == "bad" and health.worst([]) == "ok"
    assert health.sort(f)[0].level == "bad"


def test_demo_snapshot_is_consistent():
    s = demo.snapshot(NOW)
    assert s["level"] == "bad" and s["counts"]["projects"] == 6 and s["counts"]["ci_failing"] == 1
    assert [r["name"] for r in s["remote_only"]] == ["old-website"]
    forge = next(p for p in s["projects"] if p["name"] == "forge")
    assert forge["stage"] == "archived", "archived on GitHub overrides the activity guess"
    json.dumps(s)


def test_state_collects_with_fakes(tmp_path):
    make_repo(tmp_path / "root" / "app", {"pyproject.toml": '[project]\nname="app"\nversion="1.0.0"\n'})
    settings = config.parse({"roots": [str(tmp_path / "root")], "services": {"units": ["x.timer"]},
                             "backups": {"mnemosyne_status": str(tmp_path / "status.json")},
                             "sites": [{"name": "w", "url": "https://example.org"}]})
    calls = []
    st = state.State(settings, fetch_github=lambda owner: calls.append(owner) or [],
                     check_site=lambda n, u: systems.SiteCheck(n, u, status=503), show_units=fake_show)
    snap = st.snapshot(wait_remote=True)
    assert snap["projects"][0]["version"] == "1.0.0" and calls == []   # no GitHub remotes, nobody asked
    subjects = {(f["subject"], f["level"]) for f in snap["findings"]}
    assert ("w", "bad") in subjects and ("x.timer", "bad") in subjects
    # no Mnemosyne units and no status file: backups are not expected, so no alarm about them
    assert not any(s == "Mnemosyne" for s, _ in subjects)


# --- web --------------------------------------------------------------------------------

@pytest.fixture
def server():
    from http.server import ThreadingHTTPServer
    app = create_app(demo.DemoState())
    srv = ThreadingHTTPServer(("127.0.0.1", 0), app.handler(0))
    port = srv.server_address[1]
    srv.RequestHandlerClass = app.handler(port)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield port
    srv.shutdown()


def request(port, path, method="GET", headers=None, body=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def test_web_serves_and_guards(server):
    status, headers, body = request(server, "/")
    assert status == 200 and b"Olympos" in body and "script-src 'self'" in headers["Content-Security-Policy"]
    status, _, body = request(server, "/api/state")
    assert status == 200 and json.loads(body)["counts"]["projects"] == 6
    assert request(server, "/api/state", headers={"Host": "evil.example"})[0] == 403
    assert request(server, "/static/../olympos/config.py")[0] == 404
    origin = {"Origin": f"http://127.0.0.1:{server}", "Content-Type": "application/json"}
    assert request(server, "/api/refresh", "POST", origin, b"{}")[0] == 200
    assert request(server, "/api/refresh", "POST", {**origin, "Origin": "http://evil.example"}, b"{}")[0] == 403
    assert request(server, "/api/refresh", "POST", {**origin, "Content-Type": "text/plain"}, b"{}")[0] == 415
