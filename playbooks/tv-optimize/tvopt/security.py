"""Bezpečnostní pravidla playbooku. Kontroluje se dvakrát: tady (než se příkaz vůbec pošle)
a v Guard write-gateway (CustomerEntityMapping.IsWritable). Tahle vrstva gateway nenahrazuje.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .packages import PKG_RE, Protection


class PolicyError(Exception):
    """Akce porušuje pravidla playbooku — neprovádět, nahlásit."""


class NotWritable(PolicyError):
    """Entita není zapisovatelná → požádat o schválení v admin portálu, nikdy nezapisovat do DB."""

    def __init__(self, entity_id: str):
        super().__init__(f"entita {entity_id} není povolená pro zápis")
        self.entity_id = entity_id


@dataclass
class CommandPolicy:
    """Co je pro TÉHLE zákazníka a TENHLE model povolené. Sestavuje engine z profilu a stavu."""
    protection: Protection
    disable_allowed: set = field(default_factory=set)   # ověření/adminem schválení kandidáti profilu
    enable_allowed: set = field(default_factory=set)    # balíčky, které playbook vypnul (pro vrácení)
    dns_hosts: set = field(default_factory=set)         # hostname z konfigurace / AdGuard Home zákazníka
    restore_values: dict = field(default_factory=dict)  # "ns/key" -> původní hodnota ze snapshotu


ANIMATION_KEYS = {"window_animation_scale", "transition_animation_scale", "animator_duration_scale"}
ANIMATION_VALUES = {"0.5", "1.0", "1", "0", "0.0", "1.5", "2.0"}
ADB_KEYS = {"adb_enabled", "adb_wifi_enabled"}
DNS_MODES = {"off", "opportunistic", "hostname"}
FORBIDDEN_CHARS = re.compile(r"[;&|`$<>\\\n\r\t\"'(){}*]|[\x00-\x1f\x7f]")

_PKG = r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+"
_NS = r"(?P<ns>global|system|secure)"
# Jediný povolený test připojení z TV (po zapnutí Soukromého DNS). Název se překládá přes
# DNS resolver TV, takže ověří DNS i internet. Žádný jiný ping/host není povolený.
CONNECTIVITY_CHECK_CMD = "ping -c 1 -W 3 connectivitycheck.gstatic.com"

READ_ONLY = [
    re.compile(r"^getprop ro\.product\.model$"),
    re.compile("^" + re.escape(CONNECTIVITY_CHECK_CMD) + "$"),
    re.compile(r"^pm list packages( -d)?$"),
    re.compile(rf"^settings get {_NS} [a-z0-9_]+$"),
    re.compile(r"^dumpsys meminfo$"),
    re.compile(r"^cmd package resolve-activity --brief -a android\.intent\.action\.MAIN -c android\.intent\.category\.HOME$"),
]
MAINTENANCE = [re.compile(r"^pm trim-caches [1-9]G$"), re.compile(r"^am kill-all$")]
PUT_RE = re.compile(rf"^settings put {_NS} (?P<key>[a-z0-9_]+) (?P<val>[A-Za-z0-9._-]{{1,64}})$")
DEL_RE = re.compile(rf"^settings delete {_NS} (?P<key>[a-z0-9_]+)$")
DISABLE_RE = re.compile(rf"^pm disable-user --user 0 (?P<pkg>{_PKG})$")
ENABLE_RE = re.compile(rf"^pm enable( --user 0)? (?P<pkg>{_PKG})$")
PLAY_RE = re.compile(rf"^am start -a android\.intent\.action\.VIEW -d market://details\?id=(?P<pkg>{_PKG})$")


def _check_setting(ns: str, key: str, val: str | None, policy: CommandPolicy) -> None:
    if ns != "global":
        raise PolicyError(f"nastavení {ns}/{key} playbook měnit nesmí")
    restore = policy.restore_values.get(f"{ns}/{key}")
    if key in ANIMATION_KEYS:
        if val is not None and val not in ANIMATION_VALUES and val != restore:
            raise PolicyError(f"neplatná hodnota animace: {val!r}")
    elif key in ADB_KEYS:
        # ladění smí playbook jen VYPNOUT; zapíná ho vždy zákazník na TV
        if val != "0":
            raise PolicyError(f"{key} smí playbook nastavit jen na 0")
    elif key == "private_dns_mode":
        if val is not None and val not in DNS_MODES:
            raise PolicyError(f"neplatný režim DNS: {val!r}")
    elif key == "private_dns_specifier":
        if val is not None and val not in policy.dns_hosts and val != restore:
            raise PolicyError(f"DNS hostname {val!r} není z konfigurace ani AdGuard Home zákazníka")
    else:
        raise PolicyError(f"nastavení {ns}/{key} playbook měnit nesmí")


def check_adb_command(cmd: str, policy: CommandPolicy) -> str:
    """Vrátí příkaz, pokud je povolený; jinak PolicyError."""
    cmd = cmd if isinstance(cmd, str) else ""
    if not cmd or cmd != cmd.strip() or FORBIDDEN_CHARS.search(cmd) or "  " in cmd:
        raise PolicyError(f"zakázané znaky v příkazu: {cmd!r}")
    if any(rx.match(cmd) for rx in READ_ONLY + MAINTENANCE):
        return cmd
    if m := PUT_RE.match(cmd):
        _check_setting(m["ns"], m["key"], m["val"], policy)
        return cmd
    if m := DEL_RE.match(cmd):
        if m["key"] in ADB_KEYS:
            raise PolicyError("nastavení ladění se nemaže")
        _check_setting(m["ns"], m["key"], None, policy)
        return cmd
    if m := DISABLE_RE.match(cmd):
        pkg = m["pkg"]
        if policy.protection.protects(pkg):
            raise PolicyError(f"{pkg} je chráněná aplikace — nevypínat")
        if pkg not in policy.disable_allowed:
            raise PolicyError(f"{pkg} není ověřený kandidát profilu ani schválený adminem")
        return cmd
    if m := ENABLE_RE.match(cmd):
        if m["pkg"] not in policy.enable_allowed:
            raise PolicyError(f"{m['pkg']} playbook nevypínal — nezapínat")
        return cmd
    if m := PLAY_RE.match(cmd):
        return cmd
    raise PolicyError(f"příkaz není na seznamu povolených: {cmd!r}")


def check_install_source(url: str) -> str:
    """Instalovat se smí jen přes Google Play (zákazník potvrdí na TV). Žádné APK odjinud."""
    m = re.match(rf"^(market://details\?id=|https://play\.google\.com/store/apps/details\?id=)({_PKG})$",
                 url if isinstance(url, str) else "")
    if not m:
        raise PolicyError("instalace je povolená jen z Google Play")
    return m.group(2)


ALLOWED_SERVICES = {("switch", "turn_on"), ("switch", "turn_off"), ("remote", "turn_on")}


def check_service(domain: str, service: str, data: dict, entity: str) -> None:
    """Jen vyjmenované služby, data přesně pro danou entitu, remote.turn_on jen s market:// URL."""
    if (domain, service) not in ALLOWED_SERVICES:
        raise PolicyError(f"služba {domain}.{service} není povolená")
    if not isinstance(data, dict) or data.get("entity_id") != entity or not entity.startswith(f"{domain}."):
        raise PolicyError("data.entity_id neodpovídá entitě kroku")
    if domain == "switch":
        if set(data) != {"entity_id"}:
            raise PolicyError("switch služba nesmí mít další parametry")
    else:
        if set(data) != {"entity_id", "activity"} or not str(data["activity"]).startswith("market://"):
            raise PolicyError("remote.turn_on je povolený jen s market:// odkazem")
        check_install_source(data["activity"])


def check_entity(cid: int, entity_id: str, owned: set, writable: set, write: bool = True) -> None:
    """Entita musí patřit zákazníkovi (cid) a pro zápis být v CustomerEntityMapping zapisovatelná."""
    if not entity_id or entity_id not in owned:
        raise PolicyError(f"entita {entity_id} nepatří zákazníkovi cid={cid}")
    if write and entity_id not in writable:
        raise NotWritable(entity_id)


def check_ports(open_ports: dict) -> list[str]:
    """Po dokončení: ADB (5555) a Kodi JSON-RPC/WebSocket (9090) musí být zavřené.
    Neznámý výsledek (None / chybí) se bere jako problém, ne jako „zavřeno“."""
    problems = []
    if open_ports.get(5555) is not False:
        problems.append("Síťové ladění (port 5555) se nepodařilo ověřit jako vypnuté.")
    if open_ports.get(9090) is not False:
        problems.append("Kodi ovládání z jiných aplikací (port 9090) se nepodařilo ověřit jako vypnuté.")
    return problems


SECRET_KEYS = re.compile(r"(pass(word)?|heslo|token|secret|psk|api[_-]?key|credential|\bpin\b)", re.I)
SECRET_INLINE = re.compile(r"(?i)\b(pass(word)?|heslo|token|secret|psk|pin)\s*[=:]\s*\S+")
CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def redact(value):
    """Odstraní citlivé údaje a řídicí znaky z audit záznamu (rekurzivně)."""
    if isinstance(value, dict):
        return {k: ("***" if SECRET_KEYS.search(str(k)) else redact(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return CONTROL.sub("?", SECRET_INLINE.sub(lambda m: m.group(1) + "=***", value))
    return value


APP_NAME_RE = re.compile(r"^[\w .+-]{1,64}$", re.UNICODE)
MAX_APP_NAME = 64
MAX_CUSTOMER_APPS = 50


def sanitize_app_name(raw) -> str | None:
    """Název aplikace od zákazníka: odstranit řídicí znaky, povolit jen písmena, číslice, mezeru a . + - _.
    Vrací None, když název neprojde (pak se nikam nepoužije, ani do auditu)."""
    if not isinstance(raw, str):
        return None
    s = CONTROL.sub("", raw).strip()
    s = re.sub(r"\s+", " ", s)
    if not s or len(s) > MAX_APP_NAME or not APP_NAME_RE.match(s):
        return None
    return s
