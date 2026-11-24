// Olympos dashboard. Everything from the server is inserted as text (el() uses textContent).
"use strict";

const LEVEL_TEXT = { ok: "all good", info: "all good", warn: "warnings", bad: "problems" };
const LEVEL_CLASS = { ok: "good", info: "good", warn: "warn", bad: "bad" };
let current = null;

function ago(iso) {
  if (!iso) return "never";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (Number.isNaN(s)) return "?";
  if (s < 90) return "just now";
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  if (s < 172800) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} days ago`;
}

function until(iso) {
  if (!iso) return "";
  const s = (new Date(iso).getTime() - Date.now()) / 1000;
  if (s < 0) return "due";
  if (s < 5400) return `in ${Math.round(s / 60)} min`;
  return `in ${Math.round(s / 3600)} h`;
}

function bytes(n) {
  const u = ["B", "KiB", "MiB", "GiB", "TiB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return i ? `${n.toFixed(1)} ${u[i]}` : `${n} B`;
}

const badge = (text, kind) => el("span", { class: `badge${kind ? ` badge--${kind}` : ""}` }, text);

function stat(value, label, sub, cls) {
  return el("div", { class: "stat" },
    el("div", { class: `stat__value ${cls || ""}` }, value),
    el("div", { class: "stat__label" }, label),
    sub ? el("div", { class: "stat__sub" }, sub) : null);
}

function renderStats(s) {
  const c = s.counts;
  const goodBackups = s.backups.map((b) => (b.last_good_backup || {}).time).filter(Boolean).sort();
  const oldest = goodBackups.length === s.backups.length && goodBackups.length ? goodBackups[0] : null;
  const stages = Object.entries(c.stages).map(([k, v]) => `${v} ${k}`).join(" · ");
  const cer = s.cerberus;
  const fullest = s.disks.filter((d) => d.mounted).sort((a, b) => b.percent - a.percent)[0];
  $("#stats").replaceChildren(
    stat(LEVEL_TEXT[s.level], "overall", `${c.bad} problem(s), ${c.warn} warning(s)`, LEVEL_CLASS[s.level]),
    stat(String(c.projects), "projects", stages),
    stat(`${c.public} / ${c.private}`, "public / private", c.ci_failing ? `${c.ci_failing} CI failing` : "CI all green",
      c.ci_failing ? "bad" : ""),
    stat(oldest ? ago(oldest) : "none", "oldest last backup", `${s.backups.length} repositor${s.backups.length === 1 ? "y" : "ies"}`,
      oldest ? "" : "bad"),
    stat(cer ? `${cer.online}/${cer.hosts}` : "–", "devices online", cer ? `${cer.risky_ports} risky port(s) · ${cer.level}` : (s.cerberus_error || "")),
    stat(fullest ? `${Math.round(fullest.percent)}%` : "–", "fullest disk", fullest ? fullest.path : ""),
  );
}

function renderFindings(s) {
  const showInfo = $("#show-info").checked;
  const list = s.findings.filter((f) => showInfo || f.level !== "info");
  const items = list.map((f) => el("li", {},
    badge(f.level === "bad" ? "problem" : f.level === "warn" ? "warning" : "note", LEVEL_CLASS[f.level] === "good" ? "" : LEVEL_CLASS[f.level]),
    el("span", { class: "area" }, f.area),
    el("span", {}, el("span", { class: "subject" }, f.subject), ": ", f.text)));
  if (!items.length) items.push(el("li", { class: "all-good" }, "Nothing needs attention."));
  $("#findings").replaceChildren(...items);
}

function testsCell(p) {
  if (!p.tests || !p.tests.length) return el("td", { class: "muted" }, "–");
  const total = p.tests.reduce((a, b) => a + b, 0);
  const stale = p.tests_behind >= 5;
  const title = `${p.tests.join(" + ")} passed (docs/test-results.svg, ${ago(p.tests_updated)})`
    + (p.tests_behind ? ` · ${p.tests_behind} commit(s) since` : "");
  return el("td", { class: `mono${stale ? " stale" : ""}`, title }, p.tests.length > 1 ? `${total} (${p.tests.join("+")})` : String(total));
}

function githubCell(p) {
  const g = p.github;
  if (!p.remote) return el("span", { class: "muted" }, "local only");
  if (!g) return el("span", { class: "muted" }, current.github_checked ? "not found" : "checking…");
  const ci = { SUCCESS: ["CI ✓", "good"], FAILURE: ["CI ✗", "bad"], ERROR: ["CI ✗", "bad"], PENDING: ["CI …", "warn"] }[g.ci];
  let ciTitle = "";
  if (ci && g.checks) {
    const failed = g.failed_checks || [];
    ci[0] += ` ${g.checks - failed.length}/${g.checks}`;
    ciTitle = failed.length ? `failing: ${failed.join(", ")}` : `${g.checks} CI job(s) passed`;
  }
  return el("div", { class: "badges" },
    badge(g.visibility.toLowerCase(), g.visibility === "PUBLIC" ? "accent" : ""),
    ci ? Object.assign(badge(ci[0], ci[1]), { title: ciTitle }) : badge("no CI"),
    g.open_prs ? badge(`${g.open_prs} PR`, "warn") : null,
    g.archived ? badge("archived") : null);
}

function renderProjects(s) {
  const q = $("#filter").value.trim().toLowerCase();
  const stage = $("#stage").value;
  const rows = s.projects
    .filter((p) => !stage || p.stage === stage)
    .filter((p) => !q || [p.name, p.language, p.stage, p.next_step, p.note, p.remote].join(" ").toLowerCase().includes(q))
    .map((p) => {
      const url = p.github && /^https:\/\/github\.com\//.test(p.github.url) ? p.github.url : null;
      const name = el("td", { class: "name" },
        url ? el("a", { href: url, target: "_blank", rel: "noopener noreferrer" }, p.name) : p.name,
        el("span", { class: "lang" }, p.language || ""),
        p.note ? el("span", { class: "note-line" }, p.note) : null);
      const local = el("div", { class: "badges" },
        p.dirty ? badge(`${p.dirty} changed`, "warn") : null,
        p.ahead ? badge(`${p.ahead} unpushed`, "warn") : null,
        p.behind ? badge(`${p.behind} behind`) : null,
        p.branch && !["main", "master"].includes(p.branch) ? badge(p.branch) : null,
        p.errors.length ? badge("git error", "bad") : null,
        !p.dirty && !p.ahead && !p.errors.length ? badge("clean", "good") : null);
      return el("tr", {},
        name,
        el("td", {}, p.stage ? el("span", { class: `badge stage--${p.stage}`, title: p.stage_source === "config" ? "set in olympos.toml" : "from commit activity" }, p.stage) : "–"),
        el("td", { class: "mono" }, p.version || "–"),
        testsCell(p),
        el("td", { title: p.last_subject || "" }, ago(p.last_commit)),
        el("td", {}, local),
        el("td", {}, githubCell(p)),
        el("td", { class: "next" }, p.next_step || ""));
    });
  for (const r of s.remote_only) {
    rows.push(el("tr", {},
      el("td", { class: "name" }, r.name, el("span", { class: "lang" }, "on GitHub only")),
      el("td", {}, "–"), el("td", {}, "–"), el("td", {}, "–"), el("td", {}, ago(r.pushed_at)),
      el("td", {}, badge("no local copy")), el("td", {}, githubCell({ remote: `${r.owner}/${r.name}`, github: r })), el("td", {})));
  }
  $("#projects").replaceChildren(...rows);
}

function item(title, right, body) {
  return el("div", { class: "item" },
    el("div", { class: "item__head" }, el("span", { class: "item__title" }, title), right),
    body ? el("div", { class: "item__body" }, body) : null);
}

function renderBackups(s) {
  if (!s.backups.length) {
    $("#backups").replaceChildren(el("p", { class: "muted" }, "No backup has run yet (Mnemosyne status file not found)."));
    return;
  }
  $("#backups").replaceChildren(...s.backups.map((b) => {
    const last = b.last_backup || {}, good = b.last_good_backup || {}, check = b.last_check || {};
    const state = !last.time ? badge("never", "bad") : last.ok ? badge("ok", "good") : badge("failed", "bad");
    const lines = [
      `last good backup ${ago(good.time)}` + (good.files ? ` · ${good.files} files, ${bytes(good.bytes)}` : ""),
      `verified ${ago(check.time)}${check.read_data ? ` (${check.read_data})` : ""}${check.time && !check.ok ? " · PROBLEMS" : ""}`,
      `${b.snapshots} snapshots · ${bytes(b.repo_bytes)} on disk`,
    ];
    if (last.time && !last.ok) lines.unshift(`last attempt ${ago(last.time)}: ${last.error || "failed"}`);
    return item(b.repo, state, lines.map((l) => el("div", {}, l)));
  }));
}

function renderCerberus(s) {
  const c = s.cerberus;
  if (!c) {
    $("#cerberus").replaceChildren(el("p", { class: "muted" }, s.cerberus_error ? `Not reachable: ${s.cerberus_error}` : "Not configured."));
    return;
  }
  const lvl = { info: "good", low: "good", medium: "warn", high: "bad", critical: "bad" }[c.level] || "";
  const rows = [["network", c.network], ["devices", `${c.online} online of ${c.hosts}`], ["untrusted", c.untrusted],
    ["open ports", c.open_ports], ["risky ports", c.risky_ports], ["new devices (24 h)", c.new_devices_24h],
    ["decoy touches (24 h)", c.decoy_touches_24h], ["events (24 h)", c.events_24h], ["last sweep", ago(c.last_sweep)]];
  $("#cerberus").replaceChildren(
    el("div", { class: "item__head" }, el("span", { class: "muted" }, `Cerberus ${c.version}`), badge(`level: ${c.level}`, lvl)),
    el("dl", { class: "kv" }, rows.flatMap(([k, v]) => [el("dt", {}, k), el("dd", {}, String(v ?? "–"))])));
}

function renderPythia(s) {
  $("#pythia-panel").hidden = !s.pythia_configured;
  if (!s.pythia_configured) return;
  const p = s.pythia;
  if (!p) {
    $("#pythia").replaceChildren(el("p", { class: "muted" }, "Not running yet: no status file."));
    return;
  }
  const stale = p.age_s > 180;
  const state = stale ? badge("stopped", "bad") : p.blocking ? badge("blocking", "good") : badge("paused", "warn");
  const rows = [["queries (24 h)", p.queries_24h], ["blocked (24 h)", `${p.blocked_24h} (${p.blocked_percent_24h}%)`],
    ["devices", p.clients], ["domains on lists", (p.domains_on_lists || 0).toLocaleString()],
    ["listening", (p.listening || []).join(", ")], ["at home", p.at_home ? "yes" : "no (this machine only)"],
    ["upstream errors (24 h)", p.errors_24h], ["status written", `${p.age_s} s ago`]];
  $("#pythia").replaceChildren(
    el("div", { class: "item__head" }, el("span", { class: "muted" }, `Pythia ${p.version}`), state),
    el("dl", { class: "kv" }, rows.flatMap(([k, v]) => [el("dt", {}, k), el("dd", {}, String(v ?? "–"))])));
}

function renderAegis(s) {
  $("#aegis-panel").hidden = !s.aegis_configured;
  if (!s.aegis_configured) return;
  const a = s.aegis;
  if (!a) {
    $("#aegis").replaceChildren(el("p", { class: "muted" }, "No audit saved yet: run python -m aegis --save."));
    return;
  }
  const cls = a.score == null ? "warn" : a.score >= 85 ? "good" : a.score >= 60 ? "warn" : "bad";
  const n = a.counts || {};
  const head = el("div", { class: "item__head" }, el("span", { class: "muted" }, `Aegis ${a.version} · ${ago(a.time)}`),
    badge(a.score == null ? "no score" : `${a.score}/100 ${a.verdict || ""}`.trim(), cls));
  const tally = el("p", { class: "muted" }, `${n.pass ?? 0} pass · ${n.warn ?? 0} warn · ${n.fail ?? 0} fail · ${n.unknown ?? 0} unknown`);
  const open = (a.open || []).map((c) => item(c.title, badge(c.status, c.status === "fail" ? "bad" : "warn"), `${c.severity} · ${c.summary}`));
  $("#aegis").replaceChildren(head, tally, ...open);
}

function renderIris(s) {
  $("#iris-panel").hidden = !s.iris_configured;
  if (!s.iris_configured) return;
  const i = s.iris;
  if (!i) {
    $("#iris").replaceChildren(el("p", { class: "muted" }, "Iris has not written its status yet."));
    return;
  }
  const d = i.decisions || {};
  const n = (k) => (d[k] ? d[k].messages : 0);
  const head = el("div", { class: "item__head" }, el("span", { class: "muted" }, `Iris ${i.version} · ${ago(i.time)}`),
    badge(i.undelivered ? `${i.undelivered} undelivered` : "delivering", i.undelivered ? "warn" : "good"));
  const tally = el("p", { class: "muted" }, `24 h: ${i.messages_24h} in · ${n("deliver")} sent · ${n("quiet")} held for the morning · ${n("below_min")} stored only`);
  const cls = { urgent: "bad", high: "bad", default: "warn", low: "good", min: "good" };
  const why = { deliver: "sent", quiet: "held", below_min: "stored", duplicate: "repeat" };
  const rows = (i.recent || []).slice(0, 6).map((m) => item(m.title, badge(m.priority, cls[m.priority] || "warn"),
    `${m.topic} · ${why[m.decision] || m.decision}${m.repeats ? ` · ×${m.repeats + 1}` : ""} · ${ago(new Date(m.ts * 1000).toISOString())}`));
  $("#iris").replaceChildren(head, tally, ...rows);
}

function renderHestia(s) {
  $("#hestia-panel").hidden = !s.hestia_configured;
  if (!s.hestia_configured) return;
  const h = s.hestia;
  if (!h) {
    $("#hestia").replaceChildren(el("p", { class: "muted" }, "No check saved yet: run hestia check --save."));
    return;
  }
  const cls = { ok: "good", unknown: "warn", warn: "warn", bad: "bad" };
  const head = el("div", { class: "item__head" }, el("span", { class: "muted" }, `Hestia ${h.version} · ${ago(h.time)}`),
    badge(h.status || "unknown", cls[h.status] || "warn"));
  const rows = (h.checks || []).map((c) => item(c.title, badge(c.status, cls[c.status] || "warn"),
    c.status === "ok" ? c.summary : [c.summary, ...(c.details || []).slice(0, 3)].join(" · ")));
  $("#hestia").replaceChildren(head, ...rows);
}

function renderThemis(s) {
  $("#themis-panel").hidden = !s.themis_configured;
  if (!s.themis_configured) return;
  const t = s.themis;
  if (!t) {
    $("#themis").replaceChildren(el("p", { class: "muted" }, "No scan saved yet: run themis scan --save."));
    return;
  }
  const cls = t.verdict === "alert" ? "bad" : t.verdict === "watch" ? "warn" : "good";
  const n = t.counts || {};
  const head = el("div", { class: "item__head" }, el("span", { class: "muted" }, `Themis ${t.version} · ${ago(t.time)}`),
    badge(t.verdict || "unknown", cls));
  const tally = el("p", { class: "muted" },
    `24 h: ${(n.high ?? 0) + (n.critical ?? 0)} high · ${n.medium ?? 0} medium · ${n.low ?? 0} low · ` +
    `${t.known_usb ?? "?"} USB devices and ${t.known_services ?? "?"} services known`);
  const sevCls = { critical: "bad", high: "bad", medium: "warn", low: "good", info: "good" };
  const recent = (t.recent || []).slice(0, 6).map((e) => item(e.message, badge(e.severity, sevCls[e.severity] || "warn"), `${e.kind} · ${ago(e.time)}`));
  if (!recent.length) recent.push(el("p", { class: "muted" }, "Nothing in the last 24 hours."));
  $("#themis").replaceChildren(head, tally, ...recent);
}

function renderUnits(s) {
  if (!s.units.length) {
    $("#units").replaceChildren(el("p", { class: "muted" }, "No units configured."));
    return;
  }
  $("#units").replaceChildren(...s.units.map((u) => {
    const timer = u.name.endsWith(".timer");
    let state;
    if (u.load === "not-found") state = badge("not installed", "bad");
    else if (u.active === "failed") state = badge("failed", "bad");
    else if (timer && u.last_result && u.last_result !== "success") state = badge("last run failed", "bad");
    else if (u.active === "active") state = badge(u.sub || "active", "good");
    else state = badge(u.active || "?", "warn");
    const body = timer
      ? `last run ${ago(u.last_run)}${u.next_run ? ` · next ${until(u.next_run)}` : ""}${u.triggers ? ` · starts ${u.triggers}` : ""}`
      : `${u.description}${u.last_run ? ` · since ${ago(u.last_run).replace(" ago", "")}` : ""}`;
    return item(u.name, state, body);
  }));
}

function renderDisksSites(s) {
  $("#disks").replaceChildren(...s.disks.map((d) => {
    if (!d.mounted) return item(d.path, badge("not mounted", "bad"));
    const cls = d.percent >= 95 ? "bad" : d.percent >= 85 ? "warn" : "";
    const bar = el("div", { class: "bar" }, el("span", { class: cls, style: null }));
    bar.firstChild.style.width = `${Math.min(100, d.percent)}%`;
    return item(d.path, el("span", { class: "mono" }, `${d.percent}%`), [`${bytes(d.free)} free of ${bytes(d.total)}`, bar]);
  }));
  $("#sites").replaceChildren(...s.sites.map((x) => {
    const ok = !x.error && x.status >= 200 && x.status < 400;
    return item(x.name, badge(x.error ? "down" : `HTTP ${x.status}`, ok ? "good" : "bad"), `${x.url} · ${x.ms} ms · checked ${ago(x.checked)}`);
  }));
}

const SERIES = [
  ["bad", "problems", (v) => String(v), "bad"],
  ["backup_age_h", "oldest last backup (h)", (v) => (v == null ? "never" : v.toFixed(1)), null],
  ["disk_max_percent", "fullest disk (%)", (v) => (v == null ? "–" : `${v}%`), null],
  ["pythia_blocked_24h", "DNS blocked (24 h)", (v) => (v == null ? "–" : String(v)), null],
  ["iris_messages_24h", "notifications (24 h)", (v) => (v == null ? "–" : String(v)), null],
  ["hestia_problems", "laptop health checks not ok", (v) => (v == null ? "–" : String(v)), null],
  ["themis_events_24h", "laptop events (24 h, medium+)", (v) => (v == null ? "–" : String(v)), null],
  ["aegis_score", "laptop security score", (v) => (v == null ? "–" : `${v}/100`), null],
  ["cerberus_online", "devices online", (v) => (v == null ? "–" : String(v)), null],
  ["unpushed", "projects with unpushed work", (v) => String(v), null],
];

function renderHistory(rows) {
  const box = $("#history");
  if (!rows.length) {
    box.replaceChildren(el("p", { class: "muted" }, "No history yet. The olympos-record timer saves a sample every 15 minutes."));
    return;
  }
  const step = Math.max(1, Math.ceil(rows.length / 240));   // at most ~240 points per line
  const pts = rows.filter((_, i) => i % step === 0 || i === rows.length - 1);
  const strip = el("div", { class: "strip", title: "overall level, oldest left" },
    pts.map((r) => el("span", { class: r.level, title: `${new Date(r.t).toLocaleString()}: ${r.level}` })));
  const first = new Date(rows[0].t).toLocaleString(), last = rows[rows.length - 1];
  const items = [el("div", { class: "hist__row hist__level" },
    el("div", { class: "hist__head" }, el("span", {}, `overall level since ${first}`), el("span", { class: "hist__now" }, last.level)), strip)];
  for (const [key, label, fmt, color] of SERIES) {
    const values = pts.map((r) => r[key]).filter((v) => typeof v === "number");
    if (!values.length) continue;
    items.push(el("div", { class: "hist__row" },
      el("div", { class: "hist__head" }, el("span", {}, label), el("span", { class: "hist__now" }, fmt(last[key]))),
      (() => { const sp = sparkline(values); if (color) sp.classList.add(`spark--${color}`); return sp; })()));
  }
  box.replaceChildren(el("div", { class: "hist" }, items));
}

async function loadHistory() {
  try {
    renderHistory(await api(`/api/history?days=${encodeURIComponent($("#days").value)}`));
  } catch (err) {
    $("#history").replaceChildren(el("p", { class: "error" }, `History: ${err.message}`));
  }
}

function render(s) {
  current = s;
  $("#error").hidden = true;
  const lvl = $("#level");
  lvl.textContent = LEVEL_TEXT[s.level];
  lvl.className = `badge badge--${LEVEL_CLASS[s.level]}`;
  document.title = `${s.level === "bad" ? "✗ " : s.level === "warn" ? "! " : ""}Olympos`;
  $("#updated").textContent = `updated ${new Date(s.generated).toLocaleTimeString()}${s.github_checked ? ` · GitHub ${ago(s.github_checked)}` : " · GitHub: checking…"}`;
  const stages = [...new Set(s.projects.map((p) => p.stage).filter(Boolean))].sort();
  const sel = $("#stage"), keep = sel.value;
  sel.replaceChildren(el("option", { value: "" }, "all stages"), ...stages.map((x) => el("option", { value: x }, x)));
  sel.value = stages.includes(keep) ? keep : "";
  renderStats(s);
  renderFindings(s);
  renderProjects(s);
  renderBackups(s);
  renderCerberus(s);
  renderPythia(s);
  renderIris(s);
  renderHestia(s);
  renderThemis(s);
  renderAegis(s);
  renderUnits(s);
  renderDisksSites(s);
}

async function load(force) {
  const btn = $("#refresh");
  btn.disabled = true;
  try {
    render(force ? await api("/api/refresh", {}) : await api("/api/state"));
    // GitHub is fetched in the background on first load: ask again shortly.
    if (!current.github_checked) setTimeout(() => load(false), 4000);
  } catch (err) {
    const e = $("#error");
    e.textContent = `Could not load: ${err.message}`;
    e.hidden = false;
  } finally {
    btn.disabled = false;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  $("#refresh").addEventListener("click", () => load(true));
  for (const id of ["#filter", "#stage", "#show-info"]) {
    $(id).addEventListener("input", () => current && render(current));
  }
  $("#days").addEventListener("input", loadHistory);
  load(false);
  loadHistory();
  setInterval(() => load(false), 60000);
  setInterval(loadHistory, 300000);
});
