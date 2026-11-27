"""Turn findings into tasks: not "the firewall is off" but "turn the firewall on: run this".

`olympos tasks` prints them; `olympos tasks --push` sends the whole list to Iris
(PUT /api/tasks/olympos), which announces urgent ones at once, lists the rest in the morning
plan, and closes a task by itself when its finding goes away.

Every task has a stable id (the same problem keeps the same id while its numbers change), a
title, what to do, and when there is one, the exact command. Info findings are not tasks.
"""

from __future__ import annotations

import json
import re
import urllib.request

LANGS = ("en", "tr")


def _slug(text: str) -> str:
    text = re.sub(r"\d+", "", text.lower())
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:40] or "x"


def _num(text: str) -> str:
    m = re.search(r"\d+", text)
    return m.group(0) if m else ""


class _T:
    """Pick the language once; tr(...) / en(...) pairs stay next to each other in the rules."""

    def __init__(self, lang: str):
        self.lang = lang if lang in LANGS else "en"

    def __call__(self, en: str, tr: str) -> str:
        return tr if self.lang == "tr" else en


def task(id: str, title: str, action: str = "", command: str = "", priority: str = "default",
         link: str = "", why: str = "") -> dict:
    return {"id": id[:80], "title": title[:200], "action": action, "command": command, "priority": priority,
            "link": link, "why": why}


def _security(f: dict, t: _T) -> dict | None:
    subj, text = f["subject"], f["text"]
    if subj == "Firewall":
        return task("aegis:firewall", t("Turn the firewall on", "Güvenlik duvarını aç"),
                    t("Block what comes in, allow what goes out.", "Gelen bağlantıları engelle, gidenlere izin ver."),
                    "sudo ufw default deny incoming && sudo ufw default allow outgoing && sudo ufw enable",
                    "high", why=text)
    if subj == "Disk encryption":
        return task("aegis:disk-encryption", t("Plan disk encryption", "Disk şifrelemeyi planla"),
                    t("Cannot be done in place: at the next clean install tick \"Encrypt the new installation\" "
                      "(after a full backup and a test restore). Snooze this for a long time.",
                      "Yerinde yapılamıyor: bir sonraki temiz kurulumda \"Yeni kurulumu şifrele\" (LUKS) seçeneğini "
                      "işaretle (önce tam yedek ve geri yükleme denemesi). Bunu uzun süre ertele."),
                    priority="low", why=text)
    if subj == "Security updates waiting":
        return task("aegis:updates", t("Install the waiting updates", "Bekleyen güncellemeleri kur"),
                    t("Then restart if a new kernel came.", "Yeni çekirdek geldiyse sonra yeniden başlat."),
                    "sudo apt update && sudo apt upgrade", "default", why=text)
    if subj == "Kernel hardening":
        return task("aegis:kernel", t("Fix the kernel switches", "Çekirdek ayarlarını düzelt"),
                    t("Aegis shows the exact lines to write to /etc/sysctl.d/ and how to load them.",
                      "Aegis /etc/sysctl.d/ içine yazılacak satırları ve nasıl yükleneceğini gösteriyor."),
                    "python3 -m aegis explain kernel", "default", why=text)
    if subj == "Restart after updates":
        return task("aegis:restart", t("Restart the computer", "Bilgisayarı yeniden başlat"),
                    t("Installed updates (often a new kernel) only take effect after a restart.",
                      "Kurulan güncellemeler (çoğu zaman yeni çekirdek) ancak yeniden başlatınca devreye girer."),
                    "systemctl reboot", "default", why=text)
    if subj == "Secure Boot":
        return task("aegis:secure-boot", t("Turn Secure Boot on", "Secure Boot'u aç"),
                    t("In the firmware settings (F2/Del at power-on). Best together with disk encryption.",
                      "Firmware ayarlarından (açılışta F2/Del). En çok disk şifrelemeyle birlikte anlamlı."),
                    priority="low", why=text)
    if subj == "Aegis":
        if "old" in text or "no audit" in text:
            return task("aegis:run", t("Run the security check", "Güvenlik denetimini çalıştır"),
                        t("The last saved audit is too old.", "Son kaydedilen denetim çok eski."),
                        "systemctl --user start aegis.service || python3 -m aegis check --save", why=text)
        return None
    if subj == "Themis":
        if "old" in text or "no scan" in text:
            return task("themis:run", t("Themis is not scanning", "Themis tarama yapmıyor"),
                        t("Check its timer.", "Zamanlayıcısını kontrol et."),
                        "systemctl --user status themis.timer themis.service", why=text)
        if f["level"] == "bad":
            return task(f"themis:event:{_slug(text)}", t("Look at a security event", "Bir güvenlik olayına bak"),
                        text, priority="high", link="http://127.0.0.1:8093/")
        return task("themis:review", t("Review this laptop's events", "Bilgisayardaki olaylara göz at"),
                    text, priority="low", link="http://127.0.0.1:8093/")
    # any other Aegis check
    return task(f"aegis:{_slug(subj)}", t(f"Security: {subj}", f"Güvenlik: {subj}"),
                t("See what to do with: python3 -m aegis explain", "Ne yapılacağını gör: python3 -m aegis explain"),
                priority="high" if f["level"] == "bad" else "default", why=text)


def _one(f: dict, t: _T, paths: dict[str, str]) -> dict | None:
    area, subj, text, level = f["area"], f["subject"], f["text"], f["level"]
    urgent = "high" if level == "bad" else "default"
    if level not in ("warn", "bad"):
        return None
    if area == "project":
        if "not pushed" in text:
            return task(f"project:{_slug(subj)}:push", t(f"Push {subj}", f"{subj}: push et"),
                        t(f"{text}.", f"{_num(text)} commit GitHub'a gönderilmedi."),
                        f"git -C '{paths[subj]}' push" if subj in paths else "git push")
        return task(f"project:{_slug(subj)}:{_slug(text)}", f"{subj}: {text}", priority="default")
    if area == "github":
        if "CI is failing" in text:
            return task(f"github:{_slug(subj)}:ci", t(f"Fix CI of {subj}", f"{subj}: CI'ı düzelt"),
                        t(text, "Ana daldaki CI kırmızı" + text.removeprefix("CI is failing on the default branch")),
                        f"gh run list -R fas0ftware/{subj} -L 3", "default")
        return task(f"github:{_slug(subj)}:{_slug(text)}", f"{subj}: {text}")
    if area == "service":
        name = subj
        if "not installed" in text:
            return task(f"service:{_slug(name)}", t(f"Install {name}", f"{name} kurulu değil"),
                        t("A unit Olympos watches is missing.", "Olympos'un izlediği bir birim eksik."),
                        f"systemctl --user status {name}", "default")
        return task(f"service:{_slug(name)}", t(f"{name} is not working", f"{name} çalışmıyor"),
                    text, f"systemctl --user status {name}; journalctl --user -u {name.removesuffix('.timer')} -n 30",
                    urgent)
    if area == "backup":
        if "not verified" in text:
            return task(f"backup:{_slug(subj)}:check", t("Verify the backups", "Yedekleri doğrula"),
                        t("Read back a part of the data.", "Verinin bir kısmını geri okuyup kontrol et."),
                        f"mnemosyne --repo {subj} check --read-data-subset 10")
        return task(f"backup:{_slug(subj)}", t("Backup is not working", "Yedekleme çalışmıyor"),
                    t(f"{subj}: {text}. Is the disk plugged in and mounted?",
                      f"{subj}: {text}. Disk takılı ve bağlı mı?"),
                    "journalctl --user -u mnemosyne-backup -n 40", "high")
    if area == "network":
        if "risky port" in text:
            return task("network:risky-ports", t("Close risky ports on the network", "Ağdaki riskli portları kapat"),
                        t(f"{text}. See which device and port in Cerberus, then switch the service off "
                          "(often the router: FTP, SMB, UPnP).",
                          f"Ağda {_num(text)} riskli port açık. Hangi cihaz ve port olduğunu Cerberus'ta gör, sonra o "
                          "servisi kapat (çoğu zaman modem: FTP, SMB, UPnP)."),
                        link="http://127.0.0.1:8470/")
        if "not reachable" in text:
            return task("network:cerberus-down", t("Cerberus is not running", "Cerberus çalışmıyor"), text,
                        "systemctl --user restart cerberus-web.service")
        return task(f"network:{_slug(text)}", t("Look at the network events", "Ağ olaylarına bak"),
                    text, priority="high", link="http://127.0.0.1:8470/")
    if area == "dns":
        if "not running yet" in text:
            return task("dns:install", t("Install Pythia", "Pythia'yı kur"),
                        t("The DNS filter is ready but not installed (needs sudo once).",
                          "DNS filtresi hazır ama kurulmadı (bir kez sudo gerekiyor). Adımlar: Rehber/19-pythia.md"),
                        priority="low")
        return task(f"dns:{_slug(text)}", f"Pythia: {text}", priority=urgent)
    if area == "security":
        return _security(f, t)
    if area == "health":
        if subj == "Disk errors":
            dev = re.search(r"\b(sd[a-z]|nvme\d+n\d+)\b", text)
            return task("health:disk-errors", t("Check the disk that had errors", "Hata veren diski kontrol et"),
                        t(f"{text}.", f"{text}. Önce kabloyu/bağlantıyı kontrol et."),
                        f"sudo smartctl -a /dev/{dev.group(1)}" if dev else "sudo smartctl -a /dev/sdX")
        if subj in ("Failed services", "Services"):
            return task("health:services", t("Some services failed", "Bazı servisler hata verdi"), text,
                        "systemctl --failed; systemctl --user --failed", urgent)
        if subj == "Battery":
            return task("health:battery", t("Plan a new battery", "Yeni pil planla"), text, priority="low")
        if subj == "Wireless devices":
            return task("health:device-batteries", t("Charge a wireless device", "Kablosuz bir cihazı şarj et"),
                        text, priority="default")
        if subj == "Hestia":
            return task("health:hestia", t("Hestia is not checking", "Hestia kontrol yapmıyor"), text,
                        "systemctl --user status hestia.timer")
        return task(f"health:{_slug(subj)}", f"{subj}: {text}", priority=urgent)
    if area == "notifications":
        return task(f"iris:{_slug(text)}", f"Iris: {text}", command="systemctl --user status iris.service")
    if area == "disk":
        if "not mounted" in text:
            return task(f"disk:{_slug(subj)}:mount", t(f"Mount {subj}", f"{subj} bağlı değil"),
                        t("Plug the disk in or mount it: the backups need it.", "Diski tak ya da bağla: yedekler ona yazıyor."))
        return task(f"disk:{_slug(subj)}:full", t(f"Free space on {subj}", f"{subj} üzerinde yer aç"), text,
                    f"du -xh --max-depth=1 {subj} | sort -h | tail", urgent)
    if area == "site":
        return task(f"site:{_slug(subj)}", t(f"{subj} is down", f"{subj} erişilemiyor"), text, priority="high")
    return task(f"{_slug(area)}:{_slug(subj)}:{_slug(text)}", f"{subj}: {text}", priority=urgent)


def from_findings(findings: list[dict], lang: str = "en", projects: list[dict] | None = None) -> list[dict]:
    """Every warn/bad finding as a task; duplicates (same id) merge, highest priority wins.
    `projects` (the snapshot's) gives the folder for a push command."""
    t = _T(lang)
    paths = {p["name"]: p["path"] for p in projects or [] if p.get("path") and "'" not in p["path"]}
    order = ("min", "low", "default", "high", "urgent")
    out: dict[str, dict] = {}
    for f in findings:
        item = _one(f, t, paths)
        if item is None:
            continue
        old = out.get(item["id"])
        if old is None or order.index(item["priority"]) > order.index(old["priority"]):
            out[item["id"]] = item
    return sorted(out.values(), key=lambda t: -order.index(t["priority"]))


def push(base_url: str, tasks: list[dict], source: str = "olympos", timeout: float = 10) -> dict:
    """PUT the whole list to Iris. base_url like http://127.0.0.1:8090 (loopback http, or https)."""
    if not base_url.startswith(("http://127.0.0.1", "http://localhost", "https://")):
        raise ValueError("Iris address must be http://127.0.0.1:<port> or https")
    req = urllib.request.Request(base_url.rstrip("/") + f"/api/tasks/{source}", method="PUT",
                                 data=json.dumps({"tasks": tasks}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310 - scheme checked above
        return json.loads(resp.read() or b"{}")


def from_snapshot(snap: dict, lang: str = "en") -> list[dict]:
    """The findings, plus every Aegis check that only warns (the dashboard folds those into one
    note, but each is something to do: install the updates, fix a kernel switch)."""
    findings = list(snap.get("findings") or [])
    for c in (snap.get("aegis") or {}).get("open") or []:
        if c.get("status") == "warn" and c.get("title"):
            findings.append({"level": "warn", "area": "security", "subject": c["title"], "text": c.get("summary") or ""})
    return from_findings(findings, lang, snap.get("projects"))
