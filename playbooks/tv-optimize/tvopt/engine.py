"""Řízení playbooku: zjištění TV → přístup → plán → souhlas → provedení → úklid → vrácení.

Engine nikdy nesahá na síť sám. Všechno jde přes `GuardApi`, které implementuje Guard
(MCP server) a které musí:
- vracet data jen pro daný `cid` (zařízení, HA stavy, config entries s entity_ids),
- posílat zápisy přes write-gateway (CustomerEntityMapping.IsWritable),
- žádost o oprávnění zakládat jako schvalovací požadavek v admin portálu (ne zápisem do DB).

Engine navíc vynucuje vlastní pravidla (security.py): povolené ADB příkazy, chráněné aplikace,
entity jen zákazníka, souhlas zákazníka s konkrétním plánem (hash), audit každé akce.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

from . import texts
from .config import load_models, load_protected, load_unsupported
from .device import TvDevice, detect_tv
from .packages import build_protection, resolve_app_name, select_packages
from .revert import Change, Snapshot, build_snapshot, revert_commands, snapshot_commands
from .security import NotWritable, PolicyError, check_adb_command, check_entity, check_ports, redact

STATE_KEY = "tv_optimize"
ADB_PORT = 5555
KODI_HTTP_PORT = 8080
KODI_WS_PORT = 9090
DNS_KEYS = ["global/private_dns_mode", "global/private_dns_specifier"]


class AdbError(Exception):
    """ADB příkaz selhal (TV nedostupná, nepotvrzené ladění…)."""


class GuardApi(Protocol):
    def context(self, cid: int) -> dict: ...
    def writable_entities(self, cid: int) -> set: ...
    def request_write_approval(self, cid: int, entity_id: str, reason: str) -> str: ...
    def start_config_flow(self, cid: int, domain: str, data: dict) -> dict: ...
    def adb(self, cid: int, entity_id: str, command: str) -> str: ...
    def call_service(self, cid: int, domain: str, service: str, data: dict) -> Any: ...
    def port_status(self, cid: int, ip: str, ports: list) -> dict: ...
    def load(self, cid: int, key: str) -> Any: ...
    def save(self, cid: int, key: str, value: Any) -> None: ...
    def audit(self, record: dict) -> None: ...


@dataclass
class Step:
    kind: str                  # adb | service | user | verify_kodi | close_access
    module: str
    describe: str = ""         # řádek do shrnutí PŘED provedením ("" = do shrnutí nepatří)
    done: str = ""             # řádek do shrnutí PO provedení (minulý čas)
    command: str = ""
    domain: str = ""
    service: str = ""
    data: dict = field(default_factory=dict)
    entity: str = ""
    user_key: str = ""
    change: dict | None = None
    optional_failure: bool = False


@dataclass
class Plan:
    kind: str                  # apply | revert
    modules: list
    steps: list
    summary: str
    ask: list = field(default_factory=list)
    hash: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["steps"] = [asdict(s) if isinstance(s, Step) else s for s in self.steps]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Plan":
        d = dict(d)
        d["steps"] = [Step(**s) for s in d["steps"]]
        return cls(**d)


@dataclass
class Reply:
    status: str                # info | need_user | need_consent | waiting_user | done | failed | unsupported
    message: str
    plan_hash: str = ""
    user_key: str = ""
    approval_ticket: str = ""
    data: dict = field(default_factory=dict)


def _hash_steps(kind: str, steps: list) -> str:
    raw = json.dumps([kind] + [asdict(s) for s in steps], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class TvPlaybook:
    def __init__(self, api: GuardApi, cid: int, conversation_id: str = "", actor: str = "guard-bot",
                 models: dict | None = None, clock: Callable[[], datetime] | None = None,
                 admin_mode: bool = False):
        self.api, self.cid, self.conversation_id, self.actor = api, int(cid), conversation_id, actor
        self.models = models or load_models()
        self.protected_cfg = load_protected()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.admin_mode = admin_mode
        self._ctx: dict | None = None
        self._tv: TvDevice | None = None

    # ---------- stav ----------
    def _state(self) -> dict:
        st = self.api.load(self.cid, STATE_KEY) or {}
        st.setdefault("changes", [])
        st.setdefault("customer_apps", [])
        return st

    def _save(self, st: dict) -> None:
        self.api.save(self.cid, STATE_KEY, st)

    def _now(self) -> str:
        return self.clock().isoformat(timespec="seconds")

    def _audit(self, module: str, action: str, result: str, command: str = "", detail: Any = None,
               entity: str = "") -> None:
        tv = self._tv
        rec = {
            "ts": self._now(), "cid": self.cid, "actor": self.actor,
            "conversation_id": self.conversation_id, "playbook": "tv-optimize",
            "device": {"ip": tv.ip if tv else "", "profile": tv.profile_id if tv else None,
                       "entity": entity or (tv.adb_entity if tv else "")},
            "module": module, "action": action, "command": command, "result": result,
            "detail": detail,
        }
        self.api.audit(redact(rec))

    # ---------- zjištění TV ----------
    def refresh(self) -> TvDevice:
        self._ctx = self.api.context(self.cid)
        self._tv = detect_tv(self._ctx, self.models)
        return self._tv

    @property
    def tv(self) -> TvDevice:
        return self._tv or self.refresh()

    def _owned(self) -> set:
        ctx = self._ctx or self.api.context(self.cid)
        return {s["entity_id"] for s in ctx.get("states", [])}

    def _profile(self) -> dict:
        return self.models[self.tv.profile_id]

    def capabilities(self) -> Reply:
        """Co jde u TV zákazníka udělat. U Tizen/webOS/neznámé TV nic nezkouší."""
        tv = self.tv
        if tv.is_android:
            return Reply("info", "", data={"platform": tv.platform, "profile": tv.profile_id})
        info = load_unsupported()[tv.platform if tv.platform in ("tizen", "webos") else "unknown"]
        lines = [f"Vaše televize: {info['name']}."]
        if info["can"]:
            lines.append("Co pro vás udělat můžu:")
            lines += [f"• {x}" for x in info["can"]]
        lines.append("Co u této TV nejde:")
        lines += [f"• {x}" for x in info["cannot"]]
        self._audit("detect", "unsupported_platform", "info", detail=tv.platform)
        return Reply("unsupported", "\n".join(lines), data={"platform": tv.platform})

    # ---------- přístup ----------
    def _require_writable(self, entity: str, reason: str) -> Reply | None:
        try:
            check_entity(self.cid, entity, self._owned(), self.api.writable_entities(self.cid))
            return None
        except NotWritable:
            ticket = self.api.request_write_approval(self.cid, entity, reason)
            self._audit("access", "request_write_approval", "pending", detail={"ticket": ticket}, entity=entity)
            return Reply("failed", texts.FAILURE["not_writable"], approval_ticket=ticket,
                         data={"reason": "not_writable", "entity": entity})

    def ensure_adb(self, module: str) -> Reply:
        """Zajistí ADB k TV. Vrací need_user (krok u TV), failed (s ručním postupem) nebo info (OK)."""
        tv = self.tv
        if not tv.is_android:
            return self.capabilities()
        if not tv.adb_entity:
            st = self._state()
            if not st.get("adb_flow_started"):
                return Reply("need_user", texts.steps_text("enable_dev_mode"), user_key="enable_dev_mode")
            return Reply("need_user", texts.steps_text("approve_adb"), user_key="approve_adb")
        blocked = self._require_writable(tv.adb_entity, f"tv-optimize/{module}: ADB příkazy na TV")
        if blocked:
            blocked.message += "\n" + texts.fallback_text(module)
            return blocked
        try:
            self._adb("getprop ro.product.model", module)
        except AdbError:
            return Reply("failed", texts.FAILURE["adb_unreachable"] + "\n" + texts.fallback_text(module),
                         data={"reason": "adb_unreachable"})
        return Reply("info", "")

    def user_enabled_dev_mode(self) -> Reply:
        """Zákazník napsal „hotovo“ po zapnutí ladění → přidat ADB integraci v HA (host = IP jeho TV)."""
        tv = self.tv
        res = self.api.start_config_flow(self.cid, "androidtv",
                                         {"host": tv.ip, "device_class": "androidtv", "port": ADB_PORT})
        st = self._state()
        st["adb_flow_started"] = True
        st["adb_flow_attempts"] = st.get("adb_flow_attempts", 0) + 1
        self._save(st)
        self._audit("access", "start_config_flow_androidtv", "ok" if res else "error", detail=res)
        return Reply("need_user", texts.steps_text("approve_adb"), user_key="approve_adb")

    def user_approved_adb(self, module: str = "speed") -> Reply:
        """Zákazník potvrdil okno „Povolit ladění?“. Když HA TV pořád nevidí, zkusí se párování znovu (max. 2×)."""
        tv = self.refresh()
        if tv.adb_entity:
            st = self._state()
            st.pop("adb_flow_attempts", None)
            self._save(st)
            return Reply("info", "")
        if self._state().get("adb_flow_attempts", 0) >= 2:
            self._audit(module, "adb_pairing_failed", "manual_fallback")
            return Reply("failed", texts.FAILURE["adb_unreachable"] + "\n" + texts.fallback_text(module),
                         data={"reason": "adb_unreachable"})
        return self.user_enabled_dev_mode()

    def adb_switch_blocked(self, module: str) -> Reply:
        """Zákazník hlásí, že přepínač Ladění USB nejde zapnout."""
        self._audit(module, "adb_switch_blocked", "manual_fallback")
        return Reply("failed", texts.FAILURE["adb_switch_blocked"] + "\n" + texts.fallback_text(module),
                     data={"reason": "adb_switch_blocked"})

    def _adb(self, cmd: str, module: str) -> str:
        protection = build_protection(self._state()["customer_apps"], self.protected_cfg)
        cmd = check_adb_command(cmd, protection)
        try:
            out = self.api.adb(self.cid, self.tv.adb_entity, cmd)
            self._audit(module, "adb", "ok", command=cmd)
            return out or ""
        except AdbError as e:
            self._audit(module, "adb", "error", command=cmd, detail=str(e))
            raise

    # ---------- aplikace zákazníka ----------
    def add_customer_apps(self, names: list[str]) -> list[str]:
        """Aplikace, které zákazník používá → chráněné (uloží se k jeho cid)."""
        st = self._state()
        added = []
        for n in names:
            n = str(n).strip()
            if n and n.lower() not in (x.lower() for x in st["customer_apps"]):
                st["customer_apps"].append(n)
                added.append(n)
        self._save(st)
        if added:
            self._audit("protect", "add_customer_apps", "ok", detail=added)
        return added

    def _protected_names(self) -> list[str]:
        names = [a["name"] for a in self.protected_cfg["apps"]]
        for n in self._state()["customer_apps"]:
            r = resolve_app_name(n, self.protected_cfg)
            nm = r["name"] if r else n
            if nm not in names:
                names.append(nm)
        return names

    # ---------- snapshot ----------
    def _take_snapshot(self, keys: list[str], module: str) -> tuple[Snapshot, dict, set]:
        """Vrátí (původní snapshot, aktuální nastavení, aktuálně vypnuté balíčky)."""
        outputs = {c: self._adb(c, module) for c in snapshot_commands(keys)}
        fresh = build_snapshot(outputs, keys, self._now())
        st = self._state()
        if st.get("snapshot") and st["changes"]:
            # původní stav (před prvním zásahem) se nepřepisuje; doplní se jen nové klíče
            snap = Snapshot.from_dict(st["snapshot"])
            for k, v in fresh.settings.items():
                snap.settings.setdefault(k, v)
            snap.installed = fresh.installed
        else:
            snap = fresh
        st["snapshot"] = snap.to_dict()
        self._save(st)
        self._audit(module, "snapshot", "ok", detail={"settings": snap.settings,
                                                      "disabled_before": snap.disabled_before})
        return snap, fresh.settings, set(fresh.disabled_before)

    # ---------- plán ----------
    def plan(self, modules: list[str]) -> Reply:
        tv = self.refresh()
        if not tv.is_android:
            return self.capabilities()
        modules = [m for m in ("speed", "ads", "kodi") if m in modules]
        profile = self._profile()
        protection = build_protection(self._state()["customer_apps"], self.protected_cfg)
        steps: list[Step] = []
        ask: list = []
        notes: list[str] = []
        needs_adb = any(m in ("speed", "ads") for m in modules) or (
            "kodi" in modules and self._kodi_open_route()[0] == "adb")

        snap, cur, dis = None, {}, set()
        if needs_adb:
            r = self.ensure_adb(modules[0] if modules else "speed")
            if r.status != "info":
                return r
            keys = [f"{s['namespace']}/{s['key']}" for s in profile.get("speed", {}).get("settings", [])]
            keys += DNS_KEYS if "ads" in modules else []
            snap, cur, dis = self._take_snapshot(keys, modules[0])

        if "speed" in modules:
            steps += self._plan_speed(profile, snap, cur, dis, protection, ask)
        if "ads" in modules:
            steps += self._plan_ads(profile, snap, cur, dis, protection, ask, notes)
        if "kodi" in modules:
            steps += self._plan_kodi(snap)
        if needs_adb:
            steps.append(Step("close_access", "cleanup", texts.MODULE_BENEFIT["cleanup"],
                              done="Dočasný přístup k TV jsem zase vypnul."))

        if not [s for s in steps if s.kind != "close_access"]:
            if needs_adb:
                self._exec(Step("close_access", "cleanup"), "apply", st := self._state())
                self._save(st)
            msg = "\n".join(["Na vaší TV už je všechno nastavené, není co měnit."] + notes)
            return Reply("info", msg)

        summary = self._summary("apply", modules, steps, ask, notes)
        plan = Plan("apply", modules, steps, summary, ask=[a["label"] for a in ask])
        plan.hash = _hash_steps("apply", steps)
        st = self._state()
        st["pending"] = {"plan": plan.to_dict(), "cursor": 0, "consented": False}
        self._save(st)
        self._audit("plan", "proposed", "pending", detail={"hash": plan.hash, "modules": modules,
                                                           "steps": len(steps)})
        return Reply("need_consent", summary, plan_hash=plan.hash, data={"ask": plan.ask})

    def _plan_speed(self, profile: dict, snap: Snapshot, cur: dict, dis: set, protection,
                    ask: list) -> list[Step]:
        steps = []
        anim = []
        for s in profile.get("speed", {}).get("settings", []):
            k = f"{s['namespace']}/{s['key']}"
            if cur.get(k) != s["value"]:
                anim.append(Step("adb", "speed", "", command=f"settings put {s['namespace']} {s['key']} {s['value']}",
                                 change=Change("setting", k, cur.get(k), s["value"], "speed").to_dict()))
        if anim:
            anim[0].describe = "Zkrátím animace v menu na polovinu (TV bude reagovat svižněji)."
            anim[0].done = "Zkrátil jsem animace v menu na polovinu."
            steps += anim
        sel = select_packages(profile, "speed", set(snap.installed), dis,
                              protection, self.admin_mode, self.protected_cfg)
        for c in sel.disable:
            steps.append(Step("adb", "speed", f"Vypnu nepoužívanou aplikaci „{c['label']}“ — {c['why']}",
                              done=f"Vypnul jsem aplikaci „{c['label']}“.",
                              command=f"pm disable-user --user 0 {c['package']}",
                              change=Change("disable", c["package"], None, "disabled", "speed").to_dict()))
        ask += sel.ask
        for cmd in profile.get("speed", {}).get("maintenance", []):
            steps.append(Step("adb", "speed", "", command=cmd,
                              change=Change("maintenance", cmd, module="speed").to_dict(), optional_failure=True))
        if profile.get("speed", {}).get("maintenance"):
            first = steps[-len(profile["speed"]["maintenance"])]
            first.describe = "Vyčistím dočasné soubory a zavřu aplikace, které zbytečně běží na pozadí."
            first.done = "Vyčistil jsem dočasné soubory a zavřel aplikace na pozadí."
        return steps

    def _plan_ads(self, profile: dict, snap: Snapshot, cur: dict, dis: set, protection,
                  ask: list, notes: list) -> list[Step]:
        steps = []
        ads = profile.get("ads", {})
        dns = ads.get("private_dns", {})
        if dns.get("supported"):
            host = dns["default_hostname"]
            mode, spec = cur.get("global/private_dns_mode"), cur.get("global/private_dns_specifier")
            if mode == "hostname" and spec and spec != host:
                notes.append(f"Vaše TV už používá vlastní DNS ({spec}) — to měnit nebudu.")
            elif not (mode == "hostname" and spec == host):
                steps.append(Step("adb", "ads", "Zapnu na TV filtr, který blokuje reklamy a sledovací služby (AdGuard DNS).",
                                  done="Zapnul jsem na TV filtr reklam a sledování (AdGuard DNS).",
                                  command=f"settings put global private_dns_specifier {host}",
                                  change=Change("setting", "global/private_dns_specifier", spec, host, "ads").to_dict()))
                steps.append(Step("adb", "ads", "", command="settings put global private_dns_mode hostname",
                                  change=Change("setting", "global/private_dns_mode", mode, "hostname", "ads").to_dict()))
        sel = select_packages(profile, "ads", set(snap.installed), dis,
                              protection, self.admin_mode, self.protected_cfg)
        for c in sel.disable:
            steps.append(Step("adb", "ads", f"Vypnu „{c['label']}“ — {c['why']}", done=f"Vypnul jsem „{c['label']}“.",
                              command=f"pm disable-user --user 0 {c['package']}",
                              change=Change("disable", c["package"], None, "disabled", "ads").to_dict()))
        ask += sel.ask
        # AdGuard Home v HA zákazníka: jen když ho má, je jeho (cid) a je zapisovatelný
        ctx = self._ctx or {}
        writable = self.api.writable_entities(self.cid)
        for s in ctx.get("states", []):
            eid = s["entity_id"]
            if eid.startswith("switch.") and "adguard" in eid and "protection" in eid:
                if s.get("state") == "on":
                    break
                if eid in writable:
                    steps.append(Step("service", "ads", "Zapnu ochranu AdGuard ve vaší domácí síti.",
                                      done="Zapnul jsem ochranu AdGuard ve vaší domácí síti.",
                                      domain="switch", service="turn_on", data={"entity_id": eid}, entity=eid,
                                      change=Change("switch", eid, s.get("state"), "on", "ads").to_dict()))
                else:
                    notes.append("Ochranu AdGuard ve vaší síti zapnout nemohu (chybí povolení) — můžete ji zapnout v Home Assistantu.")
                break
        for m in ads.get("manual_steps", []):
            notes.append("Ručně na TV (volitelné): " + m)
        return steps

    def _kodi_open_route(self) -> tuple[str, str]:
        tv = self.tv
        writable = self.api.writable_entities(self.cid)
        if tv.remote_entity and tv.remote_entity in writable:
            return "remote", tv.remote_entity
        if tv.adb_entity:
            return "adb", tv.adb_entity
        return "manual", ""

    def _plan_kodi(self, snap: Snapshot | None) -> list[Step]:
        pkg = self._profile().get("kodi", {}).get("package", "org.xbmc.kodi")
        installed = snap is not None and pkg in snap.installed
        steps = []
        if not installed:
            route, ent = self._kodi_open_route()
            if route == "remote":
                steps.append(Step("service", "kodi", "Otevřu vám na TV Google Play s aplikací Kodi (instalaci potvrdíte vy).",
                                  done="Nainstalovali jsme Kodi z Google Play.",
                                  domain="remote", service="turn_on", entity=ent,
                                  data={"entity_id": ent, "activity": f"market://details?id={pkg}"},
                                  change=Change("install_prompt", pkg, module="kodi").to_dict()))
            elif route == "adb":
                steps.append(Step("adb", "kodi", "Otevřu vám na TV Google Play s aplikací Kodi (instalaci potvrdíte vy).",
                                  done="Nainstalovali jsme Kodi z Google Play.",
                                  command=f"am start -a android.intent.action.VIEW -d market://details?id={pkg}",
                                  change=Change("install_prompt", pkg, module="kodi").to_dict()))
            if route == "manual":
                steps.append(Step("user", "kodi", "Vy si na TV nainstalujete Kodi z Google Play (návod pošlu).",
                                  user_key="install_kodi_manual"))
            else:
                steps.append(Step("user", "kodi", "Vy na TV potvrdíte instalaci Kodi.", user_key="install_kodi"))
        steps.append(Step("user", "kodi", "Vy v Kodi zapnete ovládání a nastavíte si heslo.", user_key="kodi_http"))
        steps.append(Step("user", "kodi", "Vy přidáte Kodi do Home Assistantu (heslo zadáte tam, ne do chatu).",
                          user_key="kodi_ha"))
        steps.append(Step("verify_kodi", "kodi", "Ověřím, že Home Assistant Kodi vidí.",
                          done="Kodi je v Home Assistantu a dá se ovládat."))
        return steps

    def _summary(self, kind: str, modules: list, steps: list, ask: list, notes: list) -> str:
        lines = []
        if kind == "apply":
            lines.append("Co pro vás udělám:")
            for m in modules:
                lines.append(f"→ {texts.MODULE_BENEFIT[m]}")
            lines.append("")
            lines.append("Konkrétně:")
        else:
            lines.append("Vrátím vaši TV do stavu, ve kterém byla předtím:")
        lines += [f"• {s.describe}" for s in steps if s.describe]
        if kind == "apply":
            lines.append("")
            lines.append("Tyhle aplikace nechám být: " + ", ".join(self._protected_names()) + ".")
            if ask:
                lines.append("Nejsem si jistý těmito aplikacemi, používáte je? "
                             + ", ".join(a["label"] for a in ask) + " — bez vašeho ano na ně nesáhnu.")
            lines.append("Používáte ještě jiné aplikace, které mám nechat? Napište je a přidám je mezi chráněné.")
        if notes:
            lines.append("")
            lines += notes
        lines.append("")
        if kind == "apply":
            lines.append("Všechno půjde kdykoli vrátit — stačí napsat „vrať to zpátky“.")
        lines.append("Můžu začít? (ano / ne)")
        return "\n".join(lines)

    # ---------- souhlas a provedení ----------
    def consent(self, plan_hash: str) -> Reply:
        st = self._state()
        p = st.get("pending")
        if not p or p["plan"]["hash"] != plan_hash:
            return Reply("failed", "Plán se mezitím změnil, pošlu vám nové shrnutí.", data={"reason": "stale_plan"})
        p["consented"] = True
        p["consented_at"] = self._now()
        self._save(st)
        self._audit("plan", "consent", "ok", detail={"hash": plan_hash})
        return self.run()

    def decline(self) -> Reply:
        st = self._state()
        if st.pop("pending", None):
            self._save(st)
            self._audit("plan", "declined", "ok")
        return Reply("info", "Dobře, nic neměním.")

    def run(self) -> Reply:
        st = self._state()
        p = st.get("pending")
        if not p:
            return Reply("info", "Nemám rozpracovanou žádnou změnu.")
        if not p.get("consented"):
            return Reply("failed", "Bez vašeho souhlasu nic neměním.", data={"reason": "no_consent"})
        plan = Plan.from_dict(p["plan"])
        self.refresh()
        failures = []
        while p["cursor"] < len(plan.steps):
            step = plan.steps[p["cursor"]]
            if step.kind == "user":
                p["waiting"] = step.user_key
                self._save(st)
                return Reply("waiting_user", texts.steps_text(step.user_key, ip=self.tv.ip), user_key=step.user_key)
            try:
                self._exec(step, plan.kind, st)
            except NotWritable as e:
                r = self._require_writable(e.entity_id, "tv-optimize")
                self._save(st)
                return r or Reply("failed", texts.FAILURE["not_writable"])
            except PolicyError as e:
                self._audit(step.module, "policy_block", "blocked", command=step.command, detail=str(e))
                failures.append(f"{step.describe or step.command}: {texts.FAILURE['policy']}")
            except AdbError as e:
                if step.optional_failure:
                    failures.append(step.describe or step.command)
                elif step.kind == "close_access":
                    pass  # vypnutí ladění shodí spojení — očekávané
                else:
                    self._save(st)
                    return Reply("failed", texts.FAILURE["adb_unreachable"] + "\n"
                                 + texts.fallback_text(step.module), data={"reason": "adb_unreachable",
                                                                           "error": str(e)})
            except KodiNotReady:
                self._save(st)
                return Reply("failed", "Home Assistant zatím Kodi nevidí. Zkontrolujte prosím, že Kodi na TV běží "
                             "a že je v něm zapnuté ovládání přes HTTP. Až to bude, napište „hotovo“.",
                             user_key="kodi_ha", data={"reason": "kodi_not_ready"})
            p["cursor"] += 1
            p.pop("waiting", None)
            self._save(st)
        st.pop("pending", None)
        if plan.kind == "revert":
            st["changes"] = []
            st.pop("snapshot", None)
        self._save(st)
        self._audit("plan", "completed", "ok" if not failures else "partial",
                    detail={"hash": plan.hash, "failures": failures})
        return Reply("done", self._done_text(plan, failures), data={"failures": failures})

    def user_done(self) -> Reply:
        """Zákazník napsal „hotovo“ u kroku, na který se čeká."""
        st = self._state()
        p = st.get("pending")
        if not p or not p.get("waiting"):
            return Reply("info", "Teď na nic nečekám.")
        self._audit("user", "user_step_done", "ok", detail=p["waiting"])
        p["cursor"] += 1
        p.pop("waiting")
        self._save(st)
        return self.run()

    def _exec(self, step: Step, kind: str, st: dict) -> None:
        if step.kind == "adb":
            self._adb(step.command, step.module)
            if step.change:
                self._record(st, step.change, kind)
        elif step.kind == "service":
            check_entity(self.cid, step.entity, self._owned(), self.api.writable_entities(self.cid))
            self.api.call_service(self.cid, step.domain, step.service, step.data)
            self._audit(step.module, "service", "ok", command=f"{step.domain}.{step.service}",
                        detail=step.data, entity=step.entity)
            if step.change:
                self._record(st, step.change, kind)
        elif step.kind == "verify_kodi":
            self._verify_kodi()
        elif step.kind == "close_access":
            self._close_access(st)

    def _record(self, st: dict, change: dict, kind: str) -> None:
        if kind == "apply" and change["kind"] in ("setting", "disable", "switch"):
            st["changes"].append(change)

    def _verify_kodi(self) -> None:
        tv = self.refresh()
        states = {s["entity_id"]: s for s in (self._ctx or {}).get("states", [])}
        if not tv.kodi_entity or states.get(tv.kodi_entity, {}).get("state") in (None, "off", "unavailable"):
            self._audit("kodi", "verify", "not_ready", entity=tv.kodi_entity or "")
            raise KodiNotReady()
        self._audit("kodi", "verify", "ok", entity=tv.kodi_entity)

    def _close_access(self, st: dict) -> None:
        """Vypnout síťové ladění a zkontrolovat porty (5555 i 9090 musí být zavřené)."""
        try:
            self._adb("settings put global adb_wifi_enabled 0", "cleanup")
        except AdbError:
            pass
        try:
            self._adb("settings put global adb_enabled 0", "cleanup")
        except AdbError:
            pass  # spojení spadne, to je správně
        st["adb_closed"] = True
        st.pop("adb_flow_started", None)
        ports = self.api.port_status(self.cid, self.tv.ip, [ADB_PORT, KODI_WS_PORT])
        problems = check_ports(ports)
        st["port_problems"] = problems
        self._audit("cleanup", "port_check", "ok" if not problems else "problem",
                    detail={str(k): v for k, v in ports.items()})

    def _done_text(self, plan: Plan, failures: list) -> str:
        st = self._state()
        if plan.kind == "revert":
            lines = ["Hotovo, TV je zase ve stavu, ve kterém byla předtím."]
        else:
            lines = ["Hotovo. Co jsem změnil:"]
            lines += [f"• {s.done}" for s in plan.steps if s.done]
        if failures:
            lines.append("Tohle se nepovedlo (nic se tím nerozbilo): " + "; ".join(failures))
        for prob in st.get("port_problems", []) or []:
            lines.append("Pozor: " + prob + " Vypněte to prosím na TV, nebo mi napište a pomůžu vám.")
        if plan.kind == "apply":
            lines.append("Kdyby cokoli nefungovalo jako dřív, napište „vrať to zpátky“ a všechno vrátím.")
        return "\n".join(lines)

    # ---------- vrácení ----------
    def plan_revert(self) -> Reply:
        st = self._state()
        if not st.get("changes") or not st.get("snapshot"):
            return Reply("info", "Na vaší TV jsem nic neměnil, není co vracet.")
        snap = Snapshot.from_dict(st["snapshot"])
        changes = [Change(**c) for c in st["changes"]]
        steps = []
        for cmd in revert_commands(snap, changes):
            if cmd.startswith("pm enable "):
                desc = f"Znovu zapnu aplikaci {cmd.split()[-1]}."
            elif "animation" in cmd or "animator" in cmd:
                desc = "Vrátím animace na původní rychlost." if "window_animation" in cmd else ""
            elif "private_dns_mode" in cmd:
                desc = "Vypnu filtr reklam na TV (vrátím původní DNS)."
            else:
                desc = ""
            steps.append(Step("adb", "cleanup", desc, command=cmd))
        for ch in reversed(changes):
            if ch.kind == "switch" and ch.before in ("off",):
                steps.append(Step("service", "cleanup", "Vypnu ochranu AdGuard, jak byla předtím.",
                                  domain="switch", service="turn_off", data={"entity_id": ch.target},
                                  entity=ch.target))
        adb_steps = [s for s in steps if s.kind == "adb"]
        if adb_steps:
            steps.append(Step("close_access", "cleanup", "Potom zase vypnu dočasný přístup k TV.",
                              done="Dočasný přístup k TV jsem zase vypnul."))
        if st.get("adb_closed") and adb_steps:
            # ladění je vypnuté → zákazník ho musí na chvíli zapnout
            steps.insert(0, Step("user", "cleanup", "Vy na chvíli zapnete na TV ladění (návod pošlu).",
                                 user_key="reenable_adb"))
        summary = self._summary("revert", [], steps, [], [])
        plan = Plan("revert", ["cleanup"], steps, summary)
        plan.hash = _hash_steps("revert", steps)
        st["pending"] = {"plan": plan.to_dict(), "cursor": 0, "consented": False}
        self._save(st)
        self._audit("revert", "proposed", "pending", detail={"hash": plan.hash, "steps": len(steps)})
        return Reply("need_consent", summary, plan_hash=plan.hash)

    def status(self) -> Reply:
        st = self._state()
        if not st.get("changes"):
            return Reply("info", "Na vaší TV teď nemám žádné své změny.")
        lines = ["Na vaší TV jsem změnil:"]
        for c in st["changes"]:
            if c["kind"] == "disable":
                lines.append(f"• vypnutá aplikace {c['target']}")
            elif c["kind"] == "setting":
                lines.append(f"• nastavení {c['target'].split('/')[1]} = {c['after']}")
            elif c["kind"] == "switch":
                lines.append("• zapnutá ochrana AdGuard")
        lines.append("Vrátit to můžu kdykoli — napište „vrať to zpátky“.")
        return Reply("info", "\n".join(lines))


class KodiNotReady(Exception):
    pass
