import json
import subprocess
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from olympos import config, demo, github, health, history, projects, state, systems
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


def svg(*lines):
    """A terminal image like the ones in the projects' docs/ (spaces as NBSP)."""
    nbsp = "\u00a0"
    return "<svg>" + "".join(f'<text x="0">{line.replace(" ", nbsp)}</text>' for line in lines) + "</svg>"


def test_test_counts_from_terminal_images():
    assert projects.test_counts(svg("$ pytest -q", "....", "196 passed in 3.1s")) == [196]
    assert projects.test_counts(svg("Test Suites: 5 passed, 5 total", "Tests:       42 passed, 42 total")) == [42]
    assert projects.test_counts(svg("Ran 86 tests in 0.4s", "OK")) == [86]
    assert projects.test_counts(svg("test result: ok. 34 passed; 0 failed", "test result: ok. 0 passed; 0 failed",
                                    "test result: ok. 3 passed; 0 failed")) == [34, 3]
    assert projects.test_counts(svg("12 passed, 4 subtests passed")) == [12]
    assert projects.test_counts("<svg><text>no tests here</text></svg>") == []


def test_inspect_reads_test_image_and_staleness(tmp_path):
    r = make_repo(tmp_path / "tool", {"pyproject.toml": '[project]\nname="t"\nversion="1.0.0"\n',
                                      "docs/test-results.svg": svg("23 passed in 1.0s")})
    for i in range(3):
        (r / f"code{i}.py").write_text("x")
        git(r, "add", "-A")
        git(r, "commit", "-q", "-m", f"code {i}")
    (r / "docs" / "notes.md").write_text("docs only")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "docs")
    p = projects.inspect(r, {})
    assert p.tests == [23] and p.tests_updated
    assert p.tests_behind == 3, "commits that only touch docs/ do not count"
    f = health.projects([{**p.__dict__, "tests_behind": 5}], {})
    assert any("test image is 5 commits behind" in x.text for x in f)
    assert not any("test image" in x.text for x in health.projects([p.__dict__], {}))


# --- github -----------------------------------------------------------------------------

def test_github_parse():
    data = {"data": {"repositoryOwner": {"repositories": {"nodes": [
        {"name": "a", "visibility": "PUBLIC", "isArchived": False, "pushedAt": "x", "url": "https://github.com/me/a",
         "defaultBranchRef": {"name": "main", "target": {"statusCheckRollup": {"state": "FAILURE"}}},
         "pullRequests": {"totalCount": 2}, "issues": {"totalCount": 1}},
        {"name": "b", "visibility": "PRIVATE", "defaultBranchRef": None, "pullRequests": None},
        "junk", {"visibility": "PUBLIC"},
    ]}}}}
    data["data"]["repositoryOwner"]["repositories"]["nodes"][0]["defaultBranchRef"]["target"]["statusCheckRollup"]["contexts"] = {
        "nodes": [{"__typename": "CheckRun", "name": "test (3.12)", "conclusion": "FAILURE", "status": "COMPLETED"},
                  {"__typename": "CheckRun", "name": "lint", "conclusion": "SUCCESS", "status": "COMPLETED"},
                  {"__typename": "StatusContext", "context": "ci/legacy", "state": "ERROR"}, "junk"]}
    repos = github.parse("me", data)
    assert [(r.name, r.ci, r.open_prs) for r in repos] == [("a", "FAILURE", 2), ("b", "", 0)]
    assert (repos[0].checks, repos[0].failed_checks) == (3, ["ci/legacy", "test (3.12)"])
    assert (repos[1].checks, repos[1].failed_checks) == (0, [])
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
    named = health.projects(items[:1], {"me/a": {"ci": "FAILURE", "failed_checks": ["test", "lint"]}})
    assert named[-1].text == "CI is failing on the default branch: test, lint"
    assert ("a", "warn", "1 commit(s) not pushed") in levels
    assert ("b", "info", "no GitHub repository yet") in levels
    assert health.worst(f) == "bad" and health.worst([]) == "ok"
    assert health.sort(f)[0].level == "bad"


def test_demo_snapshot_is_consistent():
    s = demo.snapshot(NOW)
    assert s["counts"]["projects"] == 13 and s["counts"]["ci_failing"] == 0
    assert [r["name"] for r in s["remote_only"]] == ["fas0ftware"]
    texts = {(f["subject"], f["level"]) for f in s["findings"]}
    assert ("olympos", "warn") in texts and ("/media/backup-disk/Mnemosyne", "bad") in texts
    pythia = next(p for p in s["projects"] if p["name"] == "pythia")
    assert pythia["github"]["checks"] == 4 and pythia["tests"] == [32] and s["pythia"]["blocking"] is True
    assert ("maze", "info") in texts, "the demo shows a stale test image"
    json.dumps(s)


def test_state_collects_with_fakes(tmp_path):
    make_repo(tmp_path / "root" / "app", {"pyproject.toml": '[project]\nname="app"\nversion="1.0.0"\n'})
    settings = config.parse({"roots": [str(tmp_path / "root")], "services": {"units": ["x.timer"]},
                             "backups": {"mnemosyne_status": str(tmp_path / "status.json")},
                             "sites": [{"name": "w", "url": "https://example.org"}]})
    calls = []
    st = state.State(settings, fetch_github=lambda owner: calls.append(owner) or [],
                     check_site=lambda n, u: systems.SiteCheck(n, u, status=503), show_units=fake_show)
    # Right after boot time.monotonic() is small: the first fetch must still happen.
    import olympos.state as state_mod
    real = state_mod.time.monotonic
    state_mod.time.monotonic = lambda: 5.0
    try:
        snap = st.snapshot(wait_remote=True)
    finally:
        state_mod.time.monotonic = real
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
    assert status == 200 and json.loads(body)["counts"]["projects"] == 13
    assert request(server, "/api/state", headers={"Host": "evil.example"})[0] == 403
    assert request(server, "/static/../olympos/config.py")[0] == 404
    origin = {"Origin": f"http://127.0.0.1:{server}", "Content-Type": "application/json"}
    assert request(server, "/api/refresh", "POST", origin, b"{}")[0] == 200
    assert request(server, "/api/refresh", "POST", {**origin, "Origin": "http://evil.example"}, b"{}")[0] == 403
    assert request(server, "/api/refresh", "POST", {**origin, "Content-Type": "text/plain"}, b"{}")[0] == 415


def test_pythia_status_and_rules(tmp_path):
    assert health.pythia(None, False) == []
    assert health.pythia(None, True)[0].level == "warn"
    f = tmp_path / "status.json"
    f.write_text(json.dumps({"version": "0.1.0", "blocking": False, "domains_on_lists": 50, "at_home": False,
                             "upstreams": [{"server": "1.1.1.1:53", "failures_in_a_row": 5}], "secret": "x"}))
    st = systems.pythia(f)
    assert st["age_s"] < 5 and "secret" not in st
    texts = {(x.level, x.text) for x in health.pythia(st, True)}
    assert ("warn", "blocking is paused") in texts and ("warn", "block lists are empty (update failed?)") in texts
    assert ("warn", "upstream 1.1.1.1:53 is failing") in texts
    assert ("bad", "stopped: last status 10 min ago") in {(x.level, x.text) for x in health.pythia({**st, "age_s": 600}, True)}
    assert systems.pythia(tmp_path / "missing.json") is None
    assert config.parse({"pythia": {}}).pythia_status is None
    assert config.parse({"pythia": {"status_file": "/x/s.json"}}).pythia_status == Path("/x/s.json")


def test_aegis_status_and_rules(tmp_path):
    assert health.aegis(None, False, 2) == []
    assert health.aegis(None, True, 2)[0].text.startswith("no audit saved yet")
    f = tmp_path / "status.json"
    f.write_text(json.dumps({"version": "0.1.0", "time": "2026-11-20T08:00:00Z", "score": 66, "verdict": "fair",
                             "counts": {"pass": 10, "warn": 1, "fail": 2},
                             "checks": [
                                 {"id": "firewall", "title": "Firewall", "severity": "high", "status": "fail",
                                  "summary": "ufw is switched off", "details": ["/etc/ufw/ufw.conf"], "fix": "sudo ufw enable"},
                                 {"id": "path", "title": "Safe PATH", "severity": "medium", "status": "fail", "summary": "x"},
                                 {"id": "kernel", "title": "Kernel hardening", "severity": "medium", "status": "warn", "summary": "y"},
                                 {"id": "mac", "title": "AppArmor", "severity": "medium", "status": "pass", "summary": "ok"},
                                 "junk"]}))
    st = systems.aegis(f)
    assert st["score"] == 66 and st["age_s"] < 5 and [c["id"] for c in st["open"]] == ["firewall", "path", "kernel"]
    assert "details" not in st["open"][0] and "fix" not in st["open"][0], "no paths or commands leave the status file"
    got = {(x.level, x.subject, x.text) for x in health.aegis(st, True, 2)}
    assert got == {("bad", "Firewall", "ufw is switched off"), ("warn", "Safe PATH", "x"),
                   ("info", "Aegis", "1 check(s) with a warning (score 66)")}
    old = health.aegis({**st, "open": [], "age_s": 5 * 86400}, True, 2)
    assert [(x.level, x.text) for x in old] == [("warn", "last audit is 5 days old")]
    assert systems.aegis(tmp_path / "missing.json") is None
    f.write_text("[1, 2]")
    with pytest.raises(RuntimeError):
        systems.aegis(f)
    assert config.parse({}).aegis_status is None
    s = config.parse({"aegis": {"max_age_days": 7}})
    assert s.aegis_status.name == "status.json" and s.aegis_max_age_days == 7.0


def test_themis_status_and_rules(tmp_path):
    assert health.themis(None, False, 30) == []
    assert health.themis(None, True, 30)[0].text.startswith("no scan saved yet")
    f = tmp_path / "status.json"
    f.write_text(json.dumps({"version": "0.1.0", "time": "2026-11-24T16:00:00Z", "verdict": "alert", "level": "high",
                             "counts": {"low": 1, "medium": 2, "high": 4, "bogus": 9, "critical": "x"},
                             "recent": [{"time": "2026-11-24T15:00:00Z", "kind": "brute_force", "severity": "high",
                                         "message": "m" * 500},
                                        {"kind": "usb_keyboard", "severity": "high", "message": "BadUSB"},
                                        {"kind": "x", "severity": "nonsense", "message": "dropped"}, "junk"],
                             "known_usb": 6, "known_services": 123}))
    st = systems.themis(f)
    assert st["counts"] == {"low": 1, "medium": 2, "high": 4} and len(st["recent"]) == 2
    assert len(st["recent"][0]["message"]) == 300 and st["verdict"] == "alert"
    got = [(x.level, x.text) for x in health.themis(st, True, 30)]
    assert got == [("bad", "m" * 300), ("bad", "BadUSB"), ("bad", "2 more high event(s) in 24 h")]
    quiet = {**st, "counts": {"medium": 2}, "recent": [], "age_s": 2 * 3600}
    got = [(x.level, x.text) for x in health.themis(quiet, True, 30)]
    assert got[0] == ("warn", "last scan is 2 h old") and got[1][0] == "warn" and "2 event(s)" in got[1][1]
    assert systems.themis(tmp_path / "missing.json") is None
    f.write_text("[]")
    with pytest.raises(RuntimeError):
        systems.themis(f)
    assert config.parse({}).themis_status is None
    s = config.parse({"themis": {"max_age_minutes": 15}})
    assert s.themis_status.name == "status.json" and s.themis_max_age_minutes == 15.0
    rec = history.sample(demo.snapshot(NOW), NOW)
    assert rec["themis_events_24h"] == 4


def test_hestia_status_and_rules(tmp_path):
    assert health.hestia(None, False, 60) == []
    assert health.hestia(None, True, 60)[0].text.startswith("no check saved yet")
    f = tmp_path / "status.json"
    f.write_text(json.dumps({"version": "0.1.0", "time": "2026-11-24T16:00:00Z", "status": "bad",
                             "counts": {"ok": 5, "warn": 1, "bad": 1, "x": 3},
                             "checks": [
                                 {"id": "services", "title": "Services", "status": "bad", "summary": "1 service(s) failed",
                                  "details": ["backup.service (user): Nightly backup", 7]},
                                 {"id": "battery", "title": "Battery", "status": "warn", "summary": "34% worn",
                                  "details": ["d" * 400]},
                                 {"id": "memory", "title": "Memory", "status": "ok", "summary": "48% available"},
                                 {"id": "bogus", "status": "sideways"}, "junk"]}))
    st = systems.hestia(f)
    assert [c["id"] for c in st["checks"]] == ["services", "battery", "memory"]
    assert st["checks"][0]["details"] == ["backup.service (user): Nightly backup"]
    assert len(st["checks"][1]["details"][0]) == 160 and st["counts"] == {"ok": 5, "warn": 1, "bad": 1}
    got = [(x.level, x.subject, x.text) for x in health.hestia(st, True, 60)]
    assert got == [("bad", "Services", "1 service(s) failed: backup.service (user): Nightly backup"),
                   ("warn", "Battery", "34% worn")]
    stale = health.hestia({**st, "checks": [], "age_s": 3 * 3600}, True, 60)
    assert [(x.level, x.text) for x in stale] == [("warn", "last check is 3 h old")]
    assert systems.hestia(tmp_path / "missing.json") is None
    f.write_text("1")
    with pytest.raises(RuntimeError):
        systems.hestia(f)
    assert config.parse({}).hestia_status is None
    assert config.parse({"hestia": {"max_age_minutes": 90}}).hestia_status.name == "status.json"
    assert history.sample(demo.snapshot(NOW), NOW)["hestia_problems"] == 2


def test_iris_status_and_rules(tmp_path):
    assert health.iris(None, False, 10) == []
    assert health.iris(None, True, 10)[0].text.startswith("not running yet")
    f = tmp_path / "status.json"
    f.write_text(json.dumps({"version": "0.1.0", "time": "2026-11-24T17:00:00Z", "messages_24h": 9, "undelivered": 2,
                             "decisions": {"deliver": {"messages": 4, "repeats": 1}, "quiet": {"messages": 3, "repeats": 0},
                                           "bogus": {"messages": 9}},
                             "recent": [{"ts": 1, "topic": "hestia", "title": "t" * 400, "priority": "high",
                                         "decision": "deliver", "repeats": 0},
                                        {"ts": 2, "topic": "x", "title": "y", "priority": "loud", "decision": "deliver"},
                                        "junk"]}))
    st = systems.iris(f)
    assert set(st["decisions"]) == {"deliver", "quiet"} and len(st["recent"]) == 1
    assert len(st["recent"][0]["title"]) == 200 and st["undelivered"] == 2
    got = [(x.level, x.text) for x in health.iris(st, True, 10)]
    assert got == [("warn", "2 message(s) could not be delivered yet"), ("info", "3 message(s) held for the morning digest")]
    stale = health.iris({**st, "undelivered": 0, "decisions": {}, "age_s": 3600}, True, 10)
    assert [(x.level, x.text[:25]) for x in stale] == [("warn", "status is 1 h old: is the")]
    assert systems.iris(tmp_path / "missing.json") is None
    f.write_text('"x"')
    with pytest.raises(RuntimeError):
        systems.iris(f)
    assert config.parse({}).iris_status is None
    assert config.parse({"iris": {"max_age_minutes": 5}}).iris_max_age_minutes == 5.0
    assert history.sample(demo.snapshot(NOW), NOW)["iris_messages_24h"] == 24


def test_github_owner_is_validated(monkeypatch):
    calls = []
    monkeypatch.setattr(github.shutil, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(github.subprocess, "run", lambda argv, **kw: calls.append(argv) or
                        type("R", (), {"returncode": 0, "stdout": '{"data": {}}', "stderr": ""})())
    for bad in ("@/etc/passwd", "@secrets", "a/b", "", "-x", "x" * 40):
        with pytest.raises(RuntimeError):
            github.fetch(bad)
    assert calls == [], "nothing unsafe reaches gh"
    github.fetch("fas0ftware")
    assert "-f" in calls[0] and "-F" not in calls[0] and "owner=fas0ftware" in calls[0]


def test_git_runs_without_repository_hooks(tmp_path):
    r = make_repo(tmp_path / "evil", {"a.txt": "x"})
    marker = tmp_path / "pwned"
    git(r, "config", "core.fsmonitor", f"touch {marker}; false")
    projects.inspect(r, {})
    assert not marker.exists(), "core.fsmonitor from the repository must not run"


# --- history and notifications ---------------------------------------------------------------

def test_history_sample_append_read_trim(tmp_path):
    from olympos import history
    snap = demo.snapshot(NOW)
    rec = history.sample(snap, NOW)
    assert rec["level"] == "bad" and rec["projects"] == 13 and rec["disk_max_percent"] == 88.0
    assert rec["backup_age_h"] == 48.0, "the backup furthest behind decides"
    assert rec["pythia_blocked_24h"] == 3310 and rec["aegis_score"] == 74 and "projects" in rec
    assert not any(isinstance(v, str) and "/" in v for k, v in rec.items() if k != "t"), "numbers only, no paths"
    path = tmp_path / "h.jsonl"
    for days in (100, 50, 1):
        history.append(path, {**rec, "t": iso(days=days)}, now=NOW)
    with open(path, "a") as f:
        f.write('{"t": "broken')                        # a crash mid-write
    assert len(history.read(path)) == 3
    assert len(history.read(path, days=60, now=NOW)) == 2
    path.write_text("".join(json.dumps({**rec, "t": iso(days=d)}) + "\n" for d in range(120, 0, -1)) * 30)
    history.append(path, {**rec, "t": iso()}, keep_days=90, now=NOW)
    kept = history.read(path)
    assert kept and min(r["t"] for r in kept) >= iso(days=90), "trimmed to 90 days"


def test_got_worse_and_notify():
    from olympos import history
    ok, warn = {"level": "ok", "bad": 0}, {"level": "warn", "bad": 0}
    bad1, bad2 = {"level": "bad", "bad": 1}, {"level": "bad", "bad": 2}
    assert history.got_worse(ok, warn) and history.got_worse(warn, bad1) and history.got_worse(bad1, bad2)
    assert not history.got_worse(bad2, bad1) and not history.got_worse(bad1, bad1) and not history.got_worse(warn, ok)
    assert history.got_worse(None, bad1) and not history.got_worse(None, warn)
    sent = []
    history.notify(demo.snapshot(NOW), send=lambda t, b: sent.append((t, b)))
    title, body = sent[0]
    assert title == "Olympos: problems" and body.count("•") == 4


def test_record_command(tmp_path, monkeypatch):
    from olympos import __main__ as cli, history
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    shown = []
    monkeypatch.setattr(history, "notify", lambda snap, send=None: shown.append(snap["level"]) or True)
    assert cli.main(["record", "--demo"]) == 0
    assert cli.main(["record", "--demo"]) == 0
    assert len(history.read(tmp_path / "olympos" / "history.jsonl")) == 2
    assert shown == ["bad"], "notified once, not again while nothing got worse"


def test_history_api(server):
    status, _, body = request(server, "/api/history?days=1")
    rows = json.loads(body)
    assert status == 200 and 90 <= len(rows) <= 100 and rows[-1]["level"] == "bad"
    assert request(server, "/api/history?days=abc")[0] == 200


def test_weekly_summary():
    from olympos import summary
    snap = demo.snapshot(NOW)
    rows = demo.history(7, NOW)
    s = summary.build(rows, snap, 7, NOW)
    assert s["verdict"] == "worse" and s["problems_start"] == 0 and s["problems_end"] == 2
    assert 19 <= s["ci_red_hours"] <= 22, "CI failed for about 20 h of the demo week"
    assert s["worst_backup_age_h"] == 48.0 and s["disk_start"] < s["disk_end"] == 88.0
    assert sum(s["level_share"].values()) in (99, 100, 101)
    assert len(s["still_open"]) == 9
    assert (s["aegis_start"], s["aegis_end"]) == (61, 74)
    body = summary.text(s)
    assert body.startswith("Last 7 days: worse.") and "CI was red for about" in body and "Still open:" in body
    empty = summary.build([], {"findings": [], "level": "ok"}, 7, NOW)
    assert empty["verdict"] == "no history yet" and "No history yet" in summary.text(empty)
    better = summary.build([{"t": iso(days=2), "bad": 3, "level": "bad"}, {"t": iso(), "bad": 0, "level": "ok"}],
                           {"findings": [], "level": "ok"}, 7, NOW)
    assert better["verdict"] == "better" and "Nothing is open right now." in summary.text(better)


def test_summary_command(capsys):
    from olympos import __main__ as cli
    assert cli.main(["summary", "--demo", "--days", "1"]) == 0
    assert capsys.readouterr().out.startswith("Last 24 hours:")
    assert cli.main(["summary", "--demo", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["verdict"] == "worse"
