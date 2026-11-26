"""Bezpečnostní pravidla playbooku. Kontroluje se dvakrát: tady (než se příkaz vůbec pošle)
a v Guard write-gateway (CustomerEntityMapping.IsWritable). Tahle vrstva gateway nenahrazuje.
"""
from __future__ import annotations

import re

from .packages import PKG_RE, Protection


class PolicyError(Exception):
    """Akce porušuje pravidla playbooku — neprovádět, nahlásit."""


# Jen tyhle klíče nastavení smí playbook měnit (všechny vratné).
ALLOWED_SETTINGS = {
    ("global", "window_animation_scale"),
    ("global", "transition_animation_scale"),
    ("global", "animator_duration_scale"),
    ("global", "private_dns_mode"),
    ("global", "private_dns_specifier"),
    ("global", "adb_enabled"),
    ("global", "adb_wifi_enabled"),
}
SETTING_VALUE_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
FORBIDDEN_CHARS = re.compile(r"[;&|`$<>\\\n\r\"']")

_PKG = r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+"
_NS = r"(global|system|secure)"
ALLOWED_COMMANDS = [
    re.compile(r"^getprop( ro\.[a-z0-9._]+)?$"),
    re.compile(r"^pm list packages( -d| -e| -3)?$"),
    re.compile(rf"^settings get {_NS} [a-z0-9_]+$"),
    re.compile(rf"^settings list {_NS}$"),
    re.compile(rf"^settings put {_NS} (?P<key>[a-z0-9_]+) (?P<val>\S+)$"),
    re.compile(rf"^settings delete {_NS} (?P<key>[a-z0-9_]+)$"),
    re.compile(rf"^pm disable-user --user 0 (?P<pkg>{_PKG})$"),
    re.compile(rf"^pm enable( --user 0)? (?P<pkg>{_PKG})$"),
    re.compile(r"^pm trim-caches \d{1,3}G$"),
    re.compile(r"^am kill-all$"),
    re.compile(r"^dumpsys meminfo$"),
    # Jediná povolená „instalace“: otevřít stránku aplikace v Google Play, potvrzuje zákazník.
    re.compile(rf"^am start -a android\.intent\.action\.VIEW -d market://details\?id=(?P<pkg>{_PKG})$"),
    re.compile(rf"^monkey -p (?P<pkg>{_PKG}) 1$"),
]
FORBIDDEN_WORDS = ("install", "uninstall", "sideload", "curl", "wget", "rm ", "reboot", "su ",
                   "chmod", "setprop", "content ", "http://", "https://")


def check_adb_command(cmd: str, protection: Protection) -> str:
    """Vrátí příkaz, pokud je povolený; jinak PolicyError. Žádné řetězení, žádné instalace."""
    cmd = (cmd or "").strip()
    if not cmd or FORBIDDEN_CHARS.search(cmd):
        raise PolicyError(f"zakázané znaky v příkazu: {cmd!r}")
    low = cmd.lower()
    for w in FORBIDDEN_WORDS:
        if w in low and not low.startswith("am start -a android.intent.action.view -d market://"):
            raise PolicyError(f"zakázaný příkaz ({w.strip()}): {cmd!r}")
    for rx in ALLOWED_COMMANDS:
        m = rx.match(cmd)
        if not m:
            continue
        gd = m.groupdict()
        if gd.get("key") is not None:
            ns = cmd.split()[2]
            if (ns, gd["key"]) not in ALLOWED_SETTINGS:
                raise PolicyError(f"nastavení {ns}/{gd['key']} playbook měnit nesmí")
            if gd.get("val") is not None and not SETTING_VALUE_RE.match(gd["val"]):
                raise PolicyError(f"neplatná hodnota nastavení: {gd['val']!r}")
        if gd.get("pkg") and cmd.startswith("pm disable-user") and protection.protects(gd["pkg"]):
            raise PolicyError(f"{gd['pkg']} je chráněná aplikace — nevypínat")
        return cmd
    raise PolicyError(f"příkaz není na seznamu povolených: {cmd!r}")


def check_install_source(url: str) -> str:
    """Instalovat se smí jen přes Google Play (zákazník potvrdí na TV). Žádné APK odjinud."""
    m = re.match(rf"^(market://details\?id=|https://play\.google\.com/store/apps/details\?id=)({_PKG})$", url or "")
    if not m:
        raise PolicyError("instalace je povolená jen z Google Play")
    return m.group(2)


def check_entity(cid: int, entity_id: str, owned: set, writable: set, write: bool = True) -> None:
    """Entita musí patřit zákazníkovi (cid) a pro zápis být v CustomerEntityMapping zapisovatelná."""
    if entity_id not in owned:
        raise PolicyError(f"entita {entity_id} nepatří zákazníkovi cid={cid}")
    if write and entity_id not in writable:
        raise NotWritable(entity_id)


class NotWritable(PolicyError):
    """Entita není zapisovatelná → požádat o schválení v admin portálu, nikdy nezapisovat do DB."""

    def __init__(self, entity_id: str):
        super().__init__(f"entita {entity_id} není povolená pro zápis")
        self.entity_id = entity_id


def check_ports(open_ports: dict) -> list[str]:
    """Po dokončení: ADB (5555) a Kodi JSON-RPC/WebSocket (9090) musí být zavřené."""
    problems = []
    if open_ports.get(5555):
        problems.append("Síťové ladění (port 5555) je pořád zapnuté.")
    if open_ports.get(9090):
        problems.append("Kodi má zapnuté ovládání z jiných aplikací (port 9090) — má zůstat vypnuté.")
    return problems


SECRET_KEYS = re.compile(r"(pass(word)?|heslo|token|secret|psk|api[_-]?key|credential|\bpin\b)", re.I)
SECRET_INLINE = re.compile(r"(?i)\b(pass(word)?|heslo|token|secret|psk|\bpin)\s*[=:]\s*\S+")


def redact(value):
    """Odstraní citlivé údaje z audit záznamu (rekurzivně)."""
    if isinstance(value, dict):
        return {k: ("***" if SECRET_KEYS.search(str(k)) else redact(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return SECRET_INLINE.sub(lambda m: m.group(1) + "=***", value)
    return value
