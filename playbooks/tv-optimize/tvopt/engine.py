"""Řízení playbooku: zjištění TV → přístup → plán → souhlas → provedení → úklid → vrácení.

Engine nikdy nesahá na síť sám. Všechno jde přes `GuardApi` (kontrakt: docs/GuardApi.md),
které implementuje Guard a které vrací data jen pro daný `cid` a zapisuje jen přes
write-gateway (CustomerEntityMapping.IsWritable).

Engine navíc vynucuje vlastní pravidla:
- souhlas a „hotovo“ jen z REÁLNÉ zprávy zákazníka (id zprávy ověřené přes chat vrstvu),
  ne z volání LLM; jedna zpráva = jeden souhlas,
- před každým příkazem znovu ověří entitu (patří cid + je zapisovatelná),
- allowlist ADB příkazů a HA služeb (security.py), chráněné aplikace,
- vrácení ze snapshotu, úklid s ověřením zavřených portů, audit každé akce.
"""
from __future__ import annotations

import functools
import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

from . import texts
from .config import load_models, load_protected, load_unsupported
from .device import TvDevice, detect_tv
from .intents import is_done, parse_consent
from .ratelimit import RateLimited, hit
from .packages import REPLACEMENT_LAUNCHERS, build_protection, disable_candidates, resolve_app_name, select_packages
from .revert import Change, Snapshot, build_snapshot, render_script, revert_commands, snapshot_commands
from .security import (CONNECTIVITY_CHECK_CMD, MAX_CUSTOMER_APPS, CommandPolicy, NotWritable, PolicyError, check_adb_command,
                       check_entity, check_ports, check_service, redact, sanitize_app_name)

STATE_KEY = "tv_optimize"
ADB_PORT = 5555
KODI_WS_PORT = 9090
DNS_KEYS = ["global/private_dns_mode", "global/private_dns_specifier"]
MAX_CALLS_PER_RUN = 60
MAX_USED_MESSAGES = 200
NET_CHECK_TIMEOUT = 30      # s: do kdy musí TV po změně DNS projít testem připojení
NET_CHECK_INTERVAL = 2      # s mezi pokusy


class AdbError(Exception):
    """ADB příkaz selhal (TV nedostupná, nepotvrzené ladění…)."""


class GuardApi(Protocol):
    def context(self, cid: int) -> dict: ...
    def writable_entities(self, cid: int) -> set: ...
    def request_write_approval(self, cid: int, target: str, reason: str) -> str: ...
    def approval_status(self, cid: int, ticket: str) -> str: ...
    def approved_packages(self, cid: int, profile_id: str) -> set: ...
    def customer_message(self, cid: int, conversation_id: str, message_id: str) -> dict | None: ...
    def notify_admin(self, cid: int, subject: str, detail: dict) -> str: ...
    def start_config_flow(self, cid: int, domain: str, data: dict) -> dict: ...
    def adb(self, cid: int, entity_id: str, command: str) -> str: ...
    def call_service(self, cid: int, domain: str, service: str, data: dict) -> Any: ...
    def port_status(self, cid: int, ip: str, ports: list) -> dict: ...
    def check_dot(self, cid: int, hostname: str) -> bool: ...
    def load(self, cid: int, key: str) -> Any: ...
    def save(self, cid: int, key: str, value: Any) -> None: ...
    def audit(self, record: dict) -> None: ...


@dataclass
class Step:
    kind: str                  # adb | service | user | verify_kodi | verify_home | check_dot | close_access
    module: str
    describe: str = ""         # řádek do shrnutí PŘED provedením ("" = do shrnutí nepatří)
    done: str = ""             # řádek do shrnutí PO provedení
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
    # info | need_choice | need_user | need_consent | need_dns_choice | waiting_user | done | failed | unsupported
    status: str
    message: str
    plan_hash: str = ""
    user_key: str = ""
    approval_ticket: str = ""
    data: dict = field(default_factory=dict)


class KodiNotReady(Exception):
    pass


class DotUnreachable(Exception):
    pass


class AccessStillOpen(Exception):
    def __init__(self, problems: list):
        super().__init__("; ".join(problems))
        self.problems = problems


class HomeCheckFailed(Exception):
    pass


class NetCheckFailed(Exception):
    pass


def _rate_limited(method):
    """Veřejné metody: překročený limit → klidná odpověď, rozpracovaný plán zůstane."""
    @functools.wraps(method)
    def wrapper(self, *a, **kw):
        try:
            return method(self, *a, **kw)
        except RateLimited as e:
            self._audit("rate", "rate_limited", "blocked", detail={"kind": e.kind, "retry_after": e.retry_after})
            return Reply("failed", texts.FAILURE["rate_limited"],
                         data={"reason": "rate_limited", "retry_after": e.retry_after, "kind": e.kind})
    return wrapper


def _hash_steps(kind: str, tv_ip: str, steps: list) -> str:
    raw = json.dumps([kind, tv_ip] + [asdict(s) for s in steps], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class TvPlaybook:
    def __init__(self, api: GuardApi, cid: int, conversation_id: str, actor: str = "guard-bot",
                 models: dict | None = None, clock: Callable[[], datetime] | None = None,
                 tv_ip: str | None = None, sleep: Callable[[float], None] | None = None,
                 monotonic: Callable[[], float] | None = None):
        if not conversation_id:
            raise ValueError("conversation_id je povinné (souhlas se váže na konverzaci)")
        self.api, self.cid, self.conversation_id, self.actor = api, int(cid), conversation_id, actor
        self.models = models or load_models()
        self.protected_cfg = load_protected()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._tv_ip = tv_ip
        self._ctx: dict | None = None
        self._tv: TvDevice | None = None
        self._calls = 0
        self.sleep = sleep or time.sleep
        self.monotonic = monotonic or time.monotonic

    def _hit(self, kind: str) -> None:
        hit(self.api, self.cid, kind, self.clock().timestamp())

    # ---------- stav a audit ----------
    def _state(self) -> dict:
        st = self.api.load(self.cid, STATE_KEY) or {}
        st.setdefault("changes", [])
        st.setdefault("customer_apps", [])
        st.setdefault("used_messages", [])
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
                       "entity": entity or (tv.adb_entity if tv else "") or ""},
            "module": module, "action": action, "command": command, "result": result,
            "detail": detail,
        }
        self.api.audit(redact(rec))

    # ---------- reálná zpráva zákazníka ----------
    def _customer_message(self, message_id: str, not_before: str = "") -> tuple[str, dict | None]:
        """Ověří, že zpráva existuje, je od zákazníka v této konverzaci, je novější než návrh
        a ještě nebyla použita. Vrací (text|"", msg|None)."""
        if not isinstance(message_id, str) or not message_id:
            return "", None
        st = self._state()
        if message_id in st["used_messages"]:
            return "", None
        msg = self.api.customer_message(self.cid, self.conversation_id, message_id)
        if not msg or msg.get("author") != "customer" or msg.get("id") != message_id:
            return "", None
        if msg.get("cid", self.cid) != self.cid or msg.get("conversation_id", self.conversation_id) != self.conversation_id:
            return "", None
        if not_before and str(msg.get("ts", "")) < not_before:
            return "", None
        return str(msg.get("text", "")), msg

    def _use_message(self, st: dict, message_id: str) -> None:
        st["used_messages"] = (st["used_messages"] + [message_id])[-MAX_USED_MESSAGES:]

    def _invalid_message(self, what: str, message_id) -> Reply:
        self._audit("consent", f"invalid_{what}", "rejected", detail={"message_id": str(message_id)[:64]})
        return Reply("failed", texts.FAILURE["no_consent"], data={"reason": "invalid_message"})

    # ---------- zjištění TV ----------
    def refresh(self) -> TvDevice:
        self._ctx = self.api.context(self.cid)
        ip = self._tv_ip or self._state().get("tv_ip")
        self._tv = detect_tv(self._ctx, self.models, ip=ip)
        return self._tv

    @property
    def tv(self) -> TvDevice:
        return self._tv or self.refresh()

    def _owned(self) -> set:
        return {s["entity_id"] for s in (self._ctx or self.api.context(self.cid)).get("states", [])}

    def _profile(self) -> dict:
        return self.models[self.tv.profile_id]

    def _tv_label(self) -> str:
        tv = self.tv
        name = " ".join(x for x in (tv.vendor if tv.vendor.lower() != "tuya" else "", tv.model) if x)
        if not name:
            name = self.models[tv.profile_id]["name"] if tv.profile_id else "TV"
        return f"{name} ({tv.ip})"

    def _select_tv(self) -> Reply | None:
        """Vybere TV zákazníka. Více kandidátů bez volby → zeptat se. Varování → admin (ne zákazník)."""
        tv = self.refresh()
        st = self._state()
        if tv.alternatives and not (self._tv_ip or st.get("tv_ip")):
            options = [{"ip": tv.ip, "hostname": tv.hostname}] + tv.alternatives
            lines = ["Ve vaší síti vidím víc televizí. Kterou myslíte?"]
            lines += [f"{i}. {o['hostname'] or 'TV'} ({o['ip']})" for i, o in enumerate(options, 1)]
            self._audit("detect", "need_choice", "pending", detail={"options": options})
            return Reply("need_choice", "\n".join(lines), data={"options": options})
        notified = set(st.get("notified", []))
        for w in tv.warnings:
            if w not in notified:
                self.api.notify_admin(self.cid, "tv-optimize: návrh opravy", {"warning": w, "ip": tv.ip})
                self._audit("detect", "notify_admin", "ok", detail={"warning": w})
                notified.add(w)
        if notified != set(st.get("notified", [])):
            st["notified"] = sorted(notified)
            self._save(st)
        return None

    @_rate_limited
    def choose_tv(self, ip: str) -> Reply:
        """Zákazník vybral TV. Musí to být jedna z jeho TV (ne libovolná IP)."""
        ctx = self.api.context(self.cid)
        tv = detect_tv(ctx, self.models, ip=ip)
        if tv.platform == "unknown" and not tv.ip:
            self._audit("detect", "choose_tv", "rejected", detail={"ip": str(ip)[:45]})
            return Reply("failed", "Tuhle televizi mezi vašimi zařízeními nevidím.", data={"reason": "not_customer_tv"})
        st = self._state()
        st["tv_ip"] = tv.ip
        self._save(st)
        self._tv_ip, self._ctx, self._tv = tv.ip, ctx, tv
        self._audit("detect", "choose_tv", "ok", detail={"ip": tv.ip})
        return Reply("info", f"Dobře, budu pracovat s televizí {self._tv_label()}.")

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

    # ---------- oprávnění ----------
    def _guard_entity(self, entity: str) -> None:
        """Před KAŽDÝM příkazem: entita patří cid a je zapisovatelná (čerstvě z Guardu)."""
        check_entity(self.cid, entity, self._owned(), self.api.writable_entities(self.cid))

    def _request_approval(self, target: str, reason: str, module: str) -> Reply:
        ticket = self.api.request_write_approval(self.cid, target, reason)
        self._audit("access", "request_write_approval", "pending", detail={"ticket": ticket, "target": target})
        return Reply("failed", texts.FAILURE["not_writable"] + "\n" + texts.fallback_text(module),
                     approval_ticket=ticket, data={"reason": "not_writable", "entity": target})

    def _policy(self) -> CommandPolicy:
        st = self._state()
        profile = self._profile()
        approved = set(self.api.approved_packages(self.cid, self.tv.profile_id) or set())
        allowed = disable_candidates(profile, approved)
        changed = {c["target"] for c in st["changes"] if c["kind"] == "disable"}
        dns = set()
        pub = profile.get("ads", {}).get("private_dns", {}).get("public_hostname")
        if pub:
            dns.add(pub)
        snap = st.get("snapshot") or {}
        return CommandPolicy(protection=build_protection(st["customer_apps"], self.protected_cfg),
                             disable_allowed=allowed, enable_allowed=allowed | changed, dns_hosts=dns,
                             restore_values=dict(snap.get("settings", {})))

    def _adb(self, cmd: str, module: str) -> str:
        cmd = check_adb_command(cmd, self._policy())
        self._calls += 1
        if self._calls > MAX_CALLS_PER_RUN:
            raise PolicyError("příliš mnoho příkazů v jednom běhu")
        self._guard_entity(self.tv.adb_entity)
        self._hit("adb")
        try:
            out = self.api.adb(self.cid, self.tv.adb_entity, cmd)
            self._audit(module, "adb", "ok", command=cmd)
            return out or ""
        except AdbError as e:
            self._audit(module, "adb", "error", command=cmd, detail=str(e))
            raise

    # ---------- přístup k TV (ADB) ----------
    def ensure_adb(self, module: str) -> Reply:
        tv = self.tv
        if not tv.is_android:
            return self.capabilities()
        st = self._state()
        if not tv.adb_entity:
            if not st.get("adb_flow", {}).get("started"):
                return Reply("need_user", texts.steps_text("enable_dev_mode"), user_key="enable_dev_mode")
            return Reply("need_user", texts.steps_text("approve_adb"), user_key="approve_adb")
        if st.get("adb_closed"):
            return Reply("need_user", texts.steps_text("reenable_adb"), user_key="reenable_adb")
        try:
            self._guard_entity(tv.adb_entity)
        except NotWritable:
            return self._request_approval(tv.adb_entity, f"tv-optimize/{module}: příkazy na TV {tv.ip}", module)
        try:
            self._adb("getprop ro.product.model", module)
        except AdbError:
            return Reply("failed", texts.FAILURE["adb_unreachable"] + "\n" + texts.fallback_text(module),
                         data={"reason": "adb_unreachable"})
        return Reply("info", "")

    def _require_done(self, message_id: str) -> tuple[Reply | None, dict]:
        text, msg = self._customer_message(message_id)
        st = self._state()
        if msg is None:
            return self._invalid_message("done", message_id), st
        if not is_done(text):
            return Reply("waiting_user", "Až to budete mít, napište mi prosím „hotovo“."), st
        self._use_message(st, message_id)
        self._save(st)
        return None, st

    @_rate_limited
    def user_enabled_dev_mode(self, message_id: str) -> Reply:
        """Zákazník napsal „hotovo“ po zapnutí ladění → config flow `androidtv` na IP JEHO TV,
        jen s doložitelným schválením (zapisovatelná entita téže TV, nebo schválený ticket)."""
        r, st = self._require_done(message_id)
        if r:
            return r
        sel = self._select_tv()
        if sel:
            return sel
        tv = self.tv
        if not tv.is_android or not tv.ip:
            return self.capabilities()
        flow = st.get("adb_flow", {})
        writable = self.api.writable_entities(self.cid)
        proof = None
        if tv.remote_entity and tv.remote_entity in writable and tv.remote_entity in self._owned():
            proof = {"is_writable": tv.remote_entity}
        elif flow.get("ticket") and self.api.approval_status(self.cid, flow["ticket"]) == "approved":
            proof = {"ticket": flow["ticket"]}
        if not proof:
            r = self._request_approval(f"config_flow:androidtv:{tv.ip}",
                                       f"tv-optimize: přidat přístup k TV {tv.ip}", "speed")
            flow["ticket"] = r.approval_ticket
            st["adb_flow"] = flow
            self._save(st)
            return r
        self._hit("config_flow")
        res = self.api.start_config_flow(self.cid, "androidtv",
                                         {"host": tv.ip, "device_class": "androidtv", "port": ADB_PORT})
        flow.update({"started": True, "attempts": flow.get("attempts", 0) + 1, "ip": tv.ip})
        st["adb_flow"] = flow
        self._save(st)
        self._audit("access", "start_config_flow_androidtv", "ok" if res else "error",
                    detail={"proof": proof, "ip": tv.ip})
        return Reply("need_user", texts.steps_text("approve_adb"), user_key="approve_adb")

    @_rate_limited
    def user_approved_adb(self, message_id: str, module: str = "speed") -> Reply:
        r, st = self._require_done(message_id)
        if r:
            return r
        tv = self.refresh()
        if tv.adb_entity:
            st.pop("adb_flow", None)
            st.pop("adb_closed", None)
            self._save(st)
            return Reply("info", "")
        if st.get("adb_flow", {}).get("attempts", 0) >= 2:
            self._audit(module, "adb_pairing_failed", "manual_fallback")
            return Reply("failed", texts.FAILURE["adb_unreachable"] + "\n" + texts.fallback_text(module),
                         data={"reason": "adb_unreachable"})
        st["used_messages"].remove(message_id)  # stejné „hotovo“ spouští i nové párování
        self._save(st)
        return self.user_enabled_dev_mode(message_id)

    @_rate_limited
    def user_reenabled_adb(self, message_id: str, module: str = "speed") -> Reply:
        r, st = self._require_done(message_id)
        if r:
            return r
        st.pop("adb_closed", None)
        self._save(st)
        return self.ensure_adb(module)

    def adb_switch_blocked(self, module: str) -> Reply:
        self._audit(module, "adb_switch_blocked", "manual_fallback")
        return Reply("failed", texts.FAILURE["adb_switch_blocked"] + "\n" + texts.fallback_text(module),
                     data={"reason": "adb_switch_blocked"})

    # ---------- aplikace zákazníka ----------
    def add_customer_apps(self, names: list) -> dict:
        """Aplikace, které zákazník používá → chráněné. Vstup se čistí (délka, znaky, počet);
        nepovolený název se nepoužije nikde a do auditu jde jen jeho počet."""
        st = self._state()
        added, rejected = [], 0
        for raw in list(names or [])[:MAX_CUSTOMER_APPS * 2]:
            n = sanitize_app_name(raw)
            if n is None:
                rejected += 1
                continue
            if n.lower() in (x.lower() for x in st["customer_apps"]):
                continue
            if len(st["customer_apps"]) >= MAX_CUSTOMER_APPS:
                rejected += 1
                continue
            st["customer_apps"].append(n)
            added.append(n)
        self._save(st)
        self._audit("protect", "add_customer_apps", "ok", detail={"added": added, "rejected": rejected})
        return {"added": added, "rejected": rejected}

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
        outputs = {c: self._adb(c, module) for c in snapshot_commands(keys)}
        fresh = build_snapshot(outputs, keys, self._now())
        st = self._state()
        if st.get("snapshot") and st["changes"]:
            snap = Snapshot.from_dict(st["snapshot"])     # původní stav se nepřepisuje
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

    # ---------- AdGuard ----------
    def _local_adguard(self) -> dict | None:
        ctx = self._ctx or {}
        host = next((e.get("host") for e in ctx.get("integrations", []) if e.get("domain") == "adguard"), None)
        switch = next((s for s in ctx.get("states", []) if s["entity_id"].startswith("switch.")
                       and "adguard" in s["entity_id"] and "protection" in s["entity_id"]), None)
        if not host and not switch:
            return None
        return {"host": host, "switch": switch}

    @_rate_limited
    def public_dns_answer(self, message_id: str) -> Reply:
        """Odpověď zákazníka na nabídku veřejného AdGuard DNS (třetí strana). Jen přesné ano/ne."""
        st = self._state()
        offer = st.get("public_dns_offer")
        if not offer:
            return Reply("info", "Teď se na nic neptám.")
        text, msg = self._customer_message(message_id, not_before=offer["offered_at"])
        if msg is None:
            return self._invalid_message("dns_choice", message_id)
        ans = parse_consent(text)
        if ans == "ambiguous":
            return Reply("need_dns_choice", texts.FAILURE["ambiguous"])
        self._use_message(st, message_id)
        st.pop("public_dns_offer")
        st["public_dns"] = {"answer": ans, "message_id": message_id, "message_ts": msg.get("ts"),
                            "disclosure": texts.PUBLIC_DNS_DISCLOSURE}
        self._save(st)
        self._audit("ads", "public_dns_choice", ans, detail={"message_id": message_id, "message_ts": msg.get("ts")})
        return Reply("info", "Dobře." if ans == "yes" else "Dobře, veřejný DNS nezapnu.")

    # ---------- plán ----------
    @_rate_limited
    def plan(self, modules: list[str]) -> Reply:
        sel = self._select_tv()
        if sel:
            return sel
        tv = self.tv
        if not tv.is_android:
            return self.capabilities()
        modules = [m for m in ("speed", "ads", "kodi") if m in modules]
        if not modules:
            return Reply("info", "Nevím, co mám na TV udělat.")
        profile = self._profile()
        st = self._state()
        steps: list[Step] = []
        ask: list = []
        notes: list[str] = []

        public_dns = None
        if "ads" in modules:
            agh = self._local_adguard()
            choice = st.get("public_dns", {}).get("answer")
            if not agh and choice is None and profile.get("ads", {}).get("private_dns", {}).get("supported"):
                st["public_dns_offer"] = {"offered_at": self._now()}
                self._save(st)
                self._audit("ads", "public_dns_offered", "pending")
                return Reply("need_dns_choice", texts.NO_LOCAL_ADGUARD + "\n\n" + texts.PUBLIC_DNS_DISCLOSURE)
            if not agh and choice == "yes":
                public_dns = profile["ads"]["private_dns"]["public_hostname"]

        approved = set(self.api.approved_packages(self.cid, tv.profile_id) or set())
        ads_pkgs = any(c.get("verified") or c["package"] in approved
                       for c in profile.get("ads", {}).get("disable_candidates", []))
        needs_adb = ("speed" in modules or ("ads" in modules and (public_dns or ads_pkgs))
                     or ("kodi" in modules and self._kodi_open_route()[0] == "adb"))

        snap, cur, dis = None, {}, set()
        if needs_adb:
            r = self.ensure_adb(modules[0])
            if r.status != "info":
                return r
            keys = [f"{s['namespace']}/{s['key']}" for s in profile.get("speed", {}).get("settings", [])]
            keys += DNS_KEYS if "ads" in modules else []
            snap, cur, dis = self._take_snapshot(keys, modules[0])

        protection = build_protection(st["customer_apps"], self.protected_cfg)
        if "speed" in modules:
            steps += self._plan_speed(profile, snap, cur, dis, protection, approved, ask)
        if "ads" in modules:
            steps += self._plan_ads(profile, snap, cur, dis, protection, approved, ask, notes, public_dns)
        if "kodi" in modules:
            steps += self._plan_kodi(snap)
        if needs_adb:
            steps.append(Step("close_access", "cleanup", texts.MODULE_BENEFIT["cleanup"],
                              done="Dočasný přístup k TV jsem zase vypnul a ověřil, že je zavřený."))

        if not [s for s in steps if s.kind != "close_access"]:
            if needs_adb:
                st = self._state()
                try:
                    self._close_access(st)
                except AccessStillOpen as e:
                    # úklid bez plánu: „hotovo“ se ověří stejně přísně (user_done + reálná zpráva)
                    steps = [Step("close_access", "cleanup", done="Dočasný přístup k TV je zavřený.")]
                    cplan = Plan("cleanup", ["cleanup"], steps, "")
                    cplan.hash = _hash_steps("cleanup", tv.ip, steps)
                    st["pending"] = {"plan": cplan.to_dict(), "cursor": 0, "consented": True,
                                     "consent": {"reason": "safety_cleanup"}, "proposed_at": self._now(),
                                     "waiting": "disable_adb_manual"}
                    self._save(st)
                    return self._access_open_reply(e.problems)
                self._save(st)
            return Reply("info", "\n".join(["Na vaší TV už je všechno nastavené, není co měnit."] + notes))

        summary = self._summary("apply", modules, steps, ask, notes)
        plan = Plan("apply", modules, steps, summary, ask=[a["label"] for a in ask])
        plan.hash = _hash_steps("apply", tv.ip, steps)
        st = self._state()
        st["pending"] = {"plan": plan.to_dict(), "cursor": 0, "consented": False, "proposed_at": self._now()}
        self._save(st)
        self._audit("plan", "proposed", "pending", detail={"hash": plan.hash, "modules": modules,
                                                           "steps": len(steps), "tv": tv.ip})
        return Reply("need_consent", summary, plan_hash=plan.hash, data={"ask": plan.ask})

    def _plan_speed(self, profile, snap, cur, dis, protection, approved, ask) -> list[Step]:
        steps = []
        anim = []
        for s in profile.get("speed", {}).get("settings", []):
            k = f"{s['namespace']}/{s['key']}"
            if cur.get(k) != s["value"]:
                anim.append(Step("adb", "speed", command=f"settings put {s['namespace']} {s['key']} {s['value']}",
                                 change=Change("setting", k, cur.get(k), s["value"], "speed").to_dict()))
        if anim:
            anim[0].describe = "Zkrátím animace v menu na polovinu (TV bude reagovat svižněji)."
            anim[0].done = "Zkrátil jsem animace v menu na polovinu."
            steps += anim
        steps += self._disable_steps(profile, "speed", snap, dis, protection, approved, ask)
        maint = profile.get("speed", {}).get("maintenance", [])
        for i, cmd in enumerate(maint):
            steps.append(Step("adb", "speed", command=cmd, optional_failure=True,
                              describe="Vyčistím dočasné soubory a zavřu aplikace, které zbytečně běží na pozadí." if i == 0 else "",
                              done="Vyčistil jsem dočasné soubory a zavřel aplikace na pozadí." if i == 0 else ""))
        return steps

    def _disable_steps(self, profile, module, snap, dis, protection, approved, ask) -> list[Step]:
        steps = []
        sel = select_packages(profile, module, set(snap.installed), dis, protection, approved, self.protected_cfg)
        for c in sel.disable:
            steps.append(Step("adb", module, f"Vypnu aplikaci „{c['label']}“ — {c['why']}",
                              done=f"Vypnul jsem aplikaci „{c['label']}“.",
                              command=f"pm disable-user --user 0 {c['package']}",
                              change=Change("disable", c["package"], None, "disabled", module).to_dict()))
            if c.get("verify_home_after_disable"):
                steps.append(Step("verify_home", module, "Hned ověřím, že tlačítko Home otevře náhradní domovskou obrazovku.",
                                  data={"package": c["package"], "expect": list(REPLACEMENT_LAUNCHERS)}))
        ask += sel.ask
        return steps

    def _plan_ads(self, profile, snap, cur, dis, protection, approved, ask, notes, public_dns) -> list[Step]:
        steps = []
        agh = self._local_adguard()
        if agh:
            sw = agh["switch"]
            host_txt = f" ({agh['host']})" if agh.get("host") else ""
            if sw and sw.get("state") != "on":
                if sw["entity_id"] in self.api.writable_entities(self.cid):
                    data = {"entity_id": sw["entity_id"]}
                    check_service("switch", "turn_on", data, sw["entity_id"])
                    steps.append(Step("service", "ads", "Zapnu ochranu AdGuard Home ve vaší domácí síti.",
                                      done="Zapnul jsem ochranu AdGuard Home ve vaší domácí síti.",
                                      domain="switch", service="turn_on", data=data, entity=sw["entity_id"],
                                      change=Change("switch", sw["entity_id"], sw.get("state"), "on", "ads").to_dict()))
                else:
                    notes.append("Ochranu AdGuard Home zapnout nemohu (chybí povolení) — můžete ji zapnout v Home Assistantu.")
            notes.append(f"Reklamy blokuje AdGuard Home ve vaší síti{host_txt}. Na TV to platí, když váš router "
                         "rozdává AdGuard Home jako DNS — to s vámi případně nastaví správce Guardu, sám router neměním.")
            if cur.get("global/private_dns_mode") == "hostname":
                notes.append("Pozor: TV má zapnuté vlastní Soukromé DNS, které AdGuard Home obchází. Měnit ho nebudu.")
        elif public_dns:
            mode, spec = cur.get("global/private_dns_mode"), cur.get("global/private_dns_specifier")
            if mode == "hostname" and spec and spec != public_dns:
                notes.append(f"Vaše TV už používá vlastní DNS ({spec}) — to měnit nebudu.")
            elif not (mode == "hostname" and spec == public_dns):
                self._hit("probe")
                if not self.api.check_dot(self.cid, public_dns):
                    notes.append(texts.FAILURE["dot_unreachable"])
                else:
                    steps.append(Step("check_dot", "ads", data={"host": public_dns}))
                    steps.append(Step("adb", "ads",
                                      "Zapnu na TV veřejný AdGuard DNS (dotazy půjdou přes AdGuard, třetí stranu).",
                                      done="Zapnul jsem na TV veřejný AdGuard DNS.",
                                      command=f"settings put global private_dns_specifier {public_dns}",
                                      change=Change("setting", "global/private_dns_specifier", spec, public_dns, "ads").to_dict()))
                    steps.append(Step("adb", "ads", command="settings put global private_dns_mode hostname",
                                      change=Change("setting", "global/private_dns_mode", mode, "hostname", "ads").to_dict()))
                    steps.append(Step("verify_net", "ads",
                                      "Hned ověřím, že je TV připojená k internetu; když ne do 30 s, DNS vrátím."))
        else:
            notes.append(texts.NO_LOCAL_ADGUARD)
        if snap is not None:
            steps += self._disable_steps(profile, "ads", snap, dis, protection, approved, ask)
        for m in profile.get("ads", {}).get("manual_steps", []):
            notes.append("Ručně na TV (volitelné): " + m)
        return steps

    def _kodi_open_route(self) -> tuple[str, str]:
        tv = self.tv
        writable = self.api.writable_entities(self.cid)
        if tv.remote_entity and tv.remote_entity in writable and tv.remote_entity in self._owned():
            return "remote", tv.remote_entity
        if tv.adb_entity and tv.adb_entity in writable:
            return "adb", tv.adb_entity
        return "manual", ""

    def _plan_kodi(self, snap: Snapshot | None) -> list[Step]:
        pkg = self._profile().get("kodi", {}).get("package", "org.xbmc.kodi")
        installed = snap is not None and pkg in snap.installed
        steps = []
        if not installed:
            route, ent = self._kodi_open_route()
            url = f"market://details?id={pkg}"
            if route == "remote":
                data = {"entity_id": ent, "activity": url}
                check_service("remote", "turn_on", data, ent)
                steps.append(Step("service", "kodi", "Otevřu vám na TV Google Play s aplikací Kodi (instalaci potvrdíte vy).",
                                  done="Nainstalovali jsme Kodi z Google Play.",
                                  domain="remote", service="turn_on", entity=ent, data=data))
            elif route == "adb":
                steps.append(Step("adb", "kodi", "Otevřu vám na TV Google Play s aplikací Kodi (instalaci potvrdíte vy).",
                                  done="Nainstalovali jsme Kodi z Google Play.",
                                  command=f"am start -a android.intent.action.VIEW -d {url}"))
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
        lines = [f"Televize: {self._tv_label()}", ""]
        if kind == "apply":
            lines.append("Co pro vás udělám:")
            lines += [f"→ {texts.MODULE_BENEFIT[m]}" for m in modules]
            lines += ["", "Konkrétně:"]
        else:
            lines.append("Vrátím vaši TV do stavu, ve kterém byla předtím:")
        lines += [f"• {s.describe}" for s in steps if s.describe]
        if kind == "apply":
            lines += ["", "Tyhle aplikace nechám být: " + ", ".join(self._protected_names()) + "."]
            if ask:
                lines.append("Nejsem si jistý těmito aplikacemi, používáte je? "
                             + ", ".join(a["label"] for a in ask) + " — bez vašeho ano na ně nesáhnu.")
            lines.append("Používáte ještě jiné aplikace, které mám nechat? Napište je a přidám je mezi chráněné.")
        if notes:
            lines += [""] + notes
        lines.append("")
        if kind == "apply":
            lines.append("Všechno půjde kdykoli vrátit — stačí napsat „vrať to zpátky“.")
        lines.append("Můžu začít? Odpovězte přesně „ano“, nebo „ne“.")
        return "\n".join(lines)

    # ---------- souhlas ----------
    @_rate_limited
    def consent(self, plan_hash: str, message_id: str) -> Reply:
        """Souhlas jen z reálné zprávy zákazníka (id ověřené chat vrstvou), novější než návrh,
        s přesným „ano“. „ne“ → zrušit; cokoli jiného → zeptat se znovu."""
        st = self._state()
        p = st.get("pending")
        if not p or p["plan"]["hash"] != plan_hash:
            return Reply("failed", "Plán se mezitím změnil, pošlu vám nové shrnutí.", data={"reason": "stale_plan"})
        if p.get("consented"):
            return self.run()
        text, msg = self._customer_message(message_id, not_before=p.get("proposed_at", ""))
        if msg is None:
            return self._invalid_message("consent", message_id)
        ans = parse_consent(text)
        if ans == "ambiguous":
            self._audit("plan", "consent_ambiguous", "pending", detail={"hash": plan_hash, "message_id": message_id})
            return Reply("need_consent", texts.FAILURE["ambiguous"], plan_hash=plan_hash)
        self._use_message(st, message_id)
        if ans == "no":
            st.pop("pending", None)
            self._save(st)
            self._audit("plan", "declined", "ok", detail={"hash": plan_hash, "message_id": message_id})
            return Reply("info", "Dobře, nic neměním.")
        p["consented"] = True
        p["consent"] = {"message_id": message_id, "message_ts": msg.get("ts"), "recorded_at": self._now()}
        self._save(st)
        self._audit("plan", "consent", "ok", detail={"hash": plan_hash, "message_id": message_id,
                                                      "message_ts": msg.get("ts")})
        return self.run()

    @_rate_limited
    def user_done(self, message_id: str) -> Reply:
        st = self._state()
        p = st.get("pending")
        if not p or not p.get("waiting"):
            return Reply("info", "Teď na nic nečekám.")
        r, st = self._require_done(message_id)
        if r:
            return r
        p = st["pending"]
        key = p.pop("waiting")
        self._audit("user", "user_step_done", "ok", detail={"step": key, "message_id": message_id})
        if key == "reenable_adb":
            st.pop("adb_closed", None)
        if key != "disable_adb_manual":        # ruční zavření → znovu ověřit stejný krok
            p["cursor"] += 1
        self._save(st)
        return self.run()

    def retry(self) -> Reply:
        """Zopakovat rozpracovaný (už odsouhlasený) plán od kroku, který selhal."""
        return self.run()

    # ---------- provedení ----------
    @_rate_limited
    def run(self) -> Reply:
        st = self._state()
        p = st.get("pending")
        if not p:
            return Reply("info", "Nemám rozpracovanou žádnou změnu.")
        if not p.get("consented"):
            return Reply("failed", texts.FAILURE["no_consent"], data={"reason": "no_consent"})
        plan = Plan.from_dict(p["plan"])
        self.refresh()
        failures = []
        undone = set(p.get("undone", []))
        while p["cursor"] < len(plan.steps):
            step = plan.steps[p["cursor"]]
            if step.kind == "user":
                p["waiting"] = step.user_key
                self._save(st)
                return Reply("waiting_user", texts.steps_text(step.user_key, ip=self.tv.ip), user_key=step.user_key)
            try:
                self._exec(step, plan.kind, st)
            except NotWritable as e:
                self._save(st)
                return self._request_approval(e.entity_id, "tv-optimize: příkaz na TV", step.module)
            except PolicyError as e:
                self._audit(step.module, "policy_block", "blocked", command=step.command, detail=str(e))
                if plan.kind == "revert":
                    self._save(st)
                    return self._revert_failed(str(e))
                failures.append(f"{step.describe or 'krok'}: {texts.FAILURE['policy']}")
            except AdbError as e:
                if step.optional_failure:
                    failures.append(step.describe or step.command)
                else:
                    self._save(st)
                    if plan.kind == "revert":
                        return self._revert_failed(str(e))
                    return Reply("failed", texts.FAILURE["adb_unreachable"] + "\n" + texts.fallback_text(step.module)
                                 + "\nAž to bude, napište „zkusit znovu“.",
                                 data={"reason": "adb_unreachable", "error": str(e)})
            except DotUnreachable:
                failures.append(texts.FAILURE["dot_unreachable"])
                while p["cursor"] + 1 < len(plan.steps) and (
                        "private_dns" in plan.steps[p["cursor"] + 1].command
                        or plan.steps[p["cursor"] + 1].kind == "verify_net"):
                    p["cursor"] += 1
                    undone.add(p["cursor"])
            except NetCheckFailed:
                failures.append(texts.FAILURE["net_check_failed"])
                undone.update(i for i, s in enumerate(plan.steps) if "private_dns" in s.command)
            except HomeCheckFailed:
                pkg = step.data.get("package", "")
                undone.update(i for i, s in enumerate(plan.steps) if s.command.endswith(f" {pkg}") and pkg)
                failures.append("Domovská obrazovka po vypnutí PatchWall nefungovala, hned jsem ho zapnul zpět.")
            except KodiNotReady:
                p["waiting"] = "kodi_ha"
                p["cursor"] -= 1          # zpět na krok „kodi_ha“; po „hotovo“ se ověří znovu
                self._save(st)
                return Reply("waiting_user", "Home Assistant zatím Kodi nevidí. Zkontrolujte prosím, že Kodi na TV běží "
                             "a že je v něm zapnuté ovládání přes HTTP. Až to bude, napište „hotovo“.",
                             user_key="kodi_ha", data={"reason": "kodi_not_ready"})
            except AccessStillOpen as e:
                p["waiting"] = "disable_adb_manual"
                self._save(st)
                return self._access_open_reply(e.problems)
            p["cursor"] += 1
            p["undone"] = sorted(undone)
            self._save(st)
        st.pop("pending", None)
        if plan.kind == "revert":
            st["changes"] = []
            st.pop("snapshot", None)
        self._save(st)
        self._audit("plan", "completed", "ok" if not failures else "partial",
                    detail={"hash": plan.hash, "failures": failures})
        return Reply("done", self._done_text(plan, failures, undone), data={"failures": failures})

    def _exec(self, step: Step, kind: str, st: dict) -> None:
        if step.kind == "adb":
            self._adb(step.command, step.module)
            if step.change and kind == "apply" and step.change["kind"] in ("setting", "disable"):
                st["changes"].append(step.change)
        elif step.kind == "service":
            check_service(step.domain, step.service, step.data, step.entity)
            self._guard_entity(step.entity)
            self._hit("service")
            self.api.call_service(self.cid, step.domain, step.service, step.data)
            self._audit(step.module, "service", "ok", command=f"{step.domain}.{step.service}",
                        detail=step.data, entity=step.entity)
            if step.change and kind == "apply":
                st["changes"].append(step.change)
        elif step.kind == "check_dot":
            self._hit("probe")
            if not self.api.check_dot(self.cid, step.data["host"]):
                self._audit(step.module, "check_dot", "unreachable", detail=step.data)
                raise DotUnreachable()
            self._audit(step.module, "check_dot", "ok", detail=step.data)
        elif step.kind == "verify_net":
            self._verify_net(st)
        elif step.kind == "verify_home":
            self._verify_home(step, st)
        elif step.kind == "verify_kodi":
            self._verify_kodi()
        elif step.kind == "close_access":
            self._close_access(st)

    def _verify_home(self, step: Step, st: dict) -> None:
        """Po vypnutí launcheru: Home musí otevřít náhradní launcher, jinak hned `pm enable`."""
        pkg = step.data["package"]
        try:
            out = self._adb("cmd package resolve-activity --brief -a android.intent.action.MAIN "
                            "-c android.intent.category.HOME", step.module)
            lines = [x.strip() for x in out.splitlines() if x.strip()]
            home = lines[-1].split("/")[0] if lines else ""
        except AdbError:
            home = ""
        if home in step.data["expect"]:
            self._audit(step.module, "verify_home", "ok", detail={"home": home})
            return
        self._audit(step.module, "verify_home", "rollback", detail={"home": home, "package": pkg})
        self._adb(f"pm enable {pkg}", step.module)
        st["changes"] = [c for c in st["changes"] if not (c["kind"] == "disable" and c["target"] == pkg)]
        raise HomeCheckFailed()

    def _verify_net(self, st: dict) -> None:
        """Po zapnutí Soukromého DNS: test připojení přímo z TV. Když neprojde do 30 s,
        DNS se hned vrátí na původní hodnotu ze snapshotu."""
        deadline = self.monotonic() + NET_CHECK_TIMEOUT
        attempts = 0
        while True:
            attempts += 1
            try:
                out = self._adb(CONNECTIVITY_CHECK_CMD, "ads")
                ok = " 0% packet loss" in out or "1 received" in out
            except AdbError:
                ok = False
            if ok:
                self._audit("ads", "verify_net", "ok", detail={"attempts": attempts})
                return
            if self.monotonic() + NET_CHECK_INTERVAL >= deadline:
                break
            self.sleep(NET_CHECK_INTERVAL)
        self._audit("ads", "verify_net", "rollback", detail={"attempts": attempts})
        snap = Snapshot.from_dict(st["snapshot"])
        dns_changes = [Change(**c) for c in st["changes"] if c["kind"] == "setting" and c["target"] in DNS_KEYS]
        for cmd in revert_commands(snap, dns_changes):
            self._adb(cmd, "ads")
        st["changes"] = [c for c in st["changes"] if not (c["kind"] == "setting" and c["target"] in DNS_KEYS)]
        raise NetCheckFailed()

    def _verify_kodi(self) -> None:
        tv = self.refresh()
        states = {s["entity_id"]: s for s in (self._ctx or {}).get("states", [])}
        if not tv.kodi_entity or states.get(tv.kodi_entity, {}).get("state") in (None, "off", "unavailable"):
            self._audit("kodi", "verify", "not_ready", entity=tv.kodi_entity or "")
            raise KodiNotReady()
        self._audit("kodi", "verify", "ok", entity=tv.kodi_entity)

    def _close_access(self, st: dict) -> None:
        """Vypnout síťové ladění a OVĚŘIT, že 5555 i 9090 jsou zavřené. Jinak AccessStillOpen."""
        if self.tv.adb_entity:
            for cmd in ("settings put global adb_wifi_enabled 0", "settings put global adb_enabled 0"):
                try:
                    self._adb(cmd, "cleanup")
                except (AdbError, NotWritable, PolicyError):
                    pass  # vypnutí shodí spojení; o výsledku rozhoduje kontrola portů
        self._hit("probe")
        ports = self.api.port_status(self.cid, self.tv.ip, [ADB_PORT, KODI_WS_PORT]) or {}
        problems = check_ports(ports)
        self._audit("cleanup", "port_check", "ok" if not problems else "problem",
                    detail={str(k): v for k, v in ports.items()})
        if problems:
            st["adb_closed"] = False
            raise AccessStillOpen(problems)
        st["adb_closed"] = True
        st.pop("adb_flow", None)

    def _access_open_reply(self, problems: list) -> Reply:
        ticket = self.api.notify_admin(self.cid, "tv-optimize: přístup k TV zůstal otevřený",
                                       {"ip": self.tv.ip, "problems": problems})
        self._audit("cleanup", "escalate_access_open", "pending", detail={"ticket": ticket, "problems": problems})
        msg = texts.FAILURE["access_open"] + "\n" + texts.steps_text("disable_adb_manual")
        return Reply("failed", msg, user_key="disable_adb_manual", approval_ticket=ticket,
                     data={"reason": "access_open", "problems": problems})

    def _done_text(self, plan: Plan, failures: list, undone: set | None = None) -> str:
        undone = undone or set()
        if plan.kind == "revert":
            lines = ["Hotovo, TV je zase ve stavu, ve kterém byla předtím."]
        elif plan.kind == "cleanup":
            lines = ["Hotovo, ověřil jsem, že dočasný přístup k TV je zavřený."]
        else:
            lines = ["Hotovo. Co jsem změnil:"] + [f"• {s.done}" for i, s in enumerate(plan.steps)
                                                   if s.done and i not in undone]
        if failures:
            lines.append("Tohle se nepovedlo (nic se tím nerozbilo): " + "; ".join(failures))
        if plan.kind == "apply":
            lines.append("Kdyby cokoli nefungovalo jako dřív, napište „vrať to zpátky“ a všechno vrátím.")
        if any(s.kind == "close_access" for s in plan.steps):
            lines.append(texts.END_HINT)
        return "\n".join(lines)

    # ---------- vrácení ----------
    def manual_revert_script(self) -> str:
        """Skript pro technika (jen pro admina/technika, ne do chatu zákazníkovi)."""
        st = self._state()
        if not st.get("snapshot"):
            return ""
        cmds = revert_commands(Snapshot.from_dict(st["snapshot"]), [Change(**c) for c in st["changes"]])
        return render_script(self.tv.ip, cmds)

    def _revert_failed(self, error: str) -> Reply:
        script = self.manual_revert_script()
        ticket = self.api.notify_admin(self.cid, "tv-optimize: vrácení změn selhalo",
                                       {"ip": self.tv.ip, "error": error})
        self._audit("revert", "failed", "error", detail={"error": error, "ticket": ticket})
        return Reply("failed", texts.FAILURE["revert_incomplete"], approval_ticket=ticket,
                     data={"reason": "revert_incomplete", "script": script})

    @_rate_limited
    def plan_revert(self) -> Reply:
        sel = self._select_tv()
        if sel:
            return sel
        st = self._state()
        if not st.get("changes") or not st.get("snapshot"):
            return Reply("info", "Na vaší TV jsem nic neměnil, není co vracet.")
        snap = Snapshot.from_dict(st["snapshot"])
        changes = [Change(**c) for c in st["changes"]]
        steps = []
        for cmd in revert_commands(snap, changes):
            if cmd.startswith("pm enable "):
                desc = f"Znovu zapnu aplikaci {cmd.split()[-1]}."
            elif "window_animation" in cmd:
                desc = "Vrátím animace na původní rychlost."
            elif "private_dns_mode" in cmd:
                desc = "Vypnu na TV veřejný AdGuard DNS (vrátím původní DNS)."
            else:
                desc = ""
            steps.append(Step("adb", "cleanup", desc, command=cmd))
        for ch in reversed(changes):
            if ch.kind == "switch" and ch.before == "off":
                data = {"entity_id": ch.target}
                steps.append(Step("service", "cleanup", "Vypnu ochranu AdGuard Home, jak byla předtím.",
                                  domain="switch", service="turn_off", data=data, entity=ch.target))
        adb_steps = [s for s in steps if s.kind == "adb"]
        if adb_steps:
            steps.append(Step("close_access", "cleanup", "Potom zase vypnu dočasný přístup k TV a ověřím to.",
                              done="Dočasný přístup k TV jsem zase vypnul a ověřil, že je zavřený."))
            if st.get("adb_closed") or not self.tv.adb_entity:
                steps.insert(0, Step("user", "cleanup", "Vy na chvíli zapnete na TV ladění (návod pošlu).",
                                     user_key="reenable_adb"))
        summary = self._summary("revert", [], steps, [], [])
        plan = Plan("revert", ["cleanup"], steps, summary)
        plan.hash = _hash_steps("revert", self.tv.ip, steps)
        st["pending"] = {"plan": plan.to_dict(), "cursor": 0, "consented": False, "proposed_at": self._now()}
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
                lines.append("• zapnutá ochrana AdGuard Home")
        lines.append("Vrátit to můžu kdykoli — napište „vrať to zpátky“.")
        return Reply("info", "\n".join(lines))
