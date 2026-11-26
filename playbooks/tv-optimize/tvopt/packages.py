"""Výběr balíčků k vypnutí: kandidáti z profilu modelu − chráněné aplikace − zákazníkovy aplikace.

Pravidla:
- Nabízí se jen balíček, který na TV opravdu je a ještě není vypnutý.
- Chráněné (výchozí + systémové + zákazníkovy) se nikdy nenabídnou.
- Neověřený kandidát (`verified: false`) se nabídne jen v admin režimu.
- Kandidát, který může být streamovací služba, jde do skupiny „zeptat se“, ne do plánu.
- Kandidát `requires_replacement_launcher` jen když je nainstalovaný náhradní launcher.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from .config import load_protected

PKG_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$")
REPLACEMENT_LAUNCHERS = ("com.spocky.projengmenu",)


def norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", t.lower()).strip()


@dataclass
class Protection:
    packages: set = field(default_factory=set)
    prefixes: set = field(default_factory=set)
    keywords: set = field(default_factory=set)

    def protects(self, package: str, label: str = "") -> bool:
        p, lab = package.lower(), norm(label)
        if p in self.packages or any(p.startswith(x) for x in self.prefixes):
            return True
        return any(k and (k in p or k in lab) for k in self.keywords)


def build_protection(customer_apps: list | None = None, protected_cfg: dict | None = None,
                     include_system: bool = True) -> Protection:
    """Sestaví ochranu z výchozího seznamu + aplikací, které zákazník uvedl (názvy nebo balíčky)."""
    cfg = protected_cfg or load_protected()
    prot = Protection()
    for app in cfg["apps"]:
        prot.packages.update(x.lower() for x in app.get("packages", []))
        prot.prefixes.update(x.lower() for x in app.get("prefixes", []))
        prot.keywords.update(norm(x) for x in app.get("keywords", []))
    if include_system:
        sysc = cfg.get("system", {})
        prot.packages.update(x.lower() for x in sysc.get("packages", []))
        prot.keywords.update(norm(x) for x in sysc.get("keywords", []))
    for item in customer_apps or []:
        resolved = resolve_app_name(item, cfg)
        if resolved:
            prot.packages.update(resolved.get("packages", []))
            prot.prefixes.update(resolved.get("prefixes", []))
            prot.keywords.update(resolved.get("keywords", []))
        elif PKG_RE.match(str(item).strip()):
            prot.packages.add(str(item).strip().lower())
        else:
            kw = norm(item)
            if len(kw) >= 3:
                prot.keywords.add(kw)
    return prot


def resolve_app_name(name: str, protected_cfg: dict | None = None) -> dict | None:
    """„netflix“, „HBO Max“, „Disney+“ → záznam chráněné aplikace (nebo None)."""
    cfg = protected_cfg or load_protected()
    n = norm(name)
    if not n:
        return None
    for app in cfg["apps"]:
        names = [norm(app["name"])] + [norm(a) for a in app.get("aliases", [])]
        if n in names:
            return {
                "name": app["name"],
                "packages": [x.lower() for x in app.get("packages", [])],
                "prefixes": [x.lower() for x in app.get("prefixes", [])],
                "keywords": [norm(x) for x in app.get("keywords", [])] or [n],
            }
    return None


def _maybe_streaming(cand: dict, hints: list) -> bool:
    if cand.get("kind") in ("bloat", "ads", "tracking", "launcher"):
        return False
    text = cand["package"].lower() + " " + norm(cand.get("label", ""))
    return any(h in text for h in hints)


@dataclass
class Selection:
    disable: list = field(default_factory=list)       # kandidáti do plánu (dict z profilu)
    ask: list = field(default_factory=list)           # nejisté → zeptat se zákazníka
    skipped: list = field(default_factory=list)       # (package, důvod)


def select_packages(profile: dict, module: str, installed: set, disabled: set | None = None,
                    protection: Protection | None = None, admin_mode: bool = False,
                    protected_cfg: dict | None = None) -> Selection:
    cfg = protected_cfg or load_protected()
    protection = protection or build_protection(protected_cfg=cfg)
    disabled = disabled or set()
    hints = cfg.get("streaming_hints", [])
    sel = Selection()
    for cand in profile.get(module, {}).get("disable_candidates", []):
        pkg = cand["package"]
        if not PKG_RE.match(pkg):
            sel.skipped.append((pkg, "neplatný název balíčku"))
        elif protection.protects(pkg, cand.get("label", "")):
            sel.skipped.append((pkg, "chráněná aplikace"))
        elif pkg not in installed:
            sel.skipped.append((pkg, "na TV není"))
        elif pkg in disabled:
            sel.skipped.append((pkg, "už je vypnutý"))
        elif not cand.get("verified", False) and not admin_mode:
            sel.skipped.append((pkg, "neověřený kandidát (čeká na admina)"))
        elif cand.get("requires_replacement_launcher") and not any(
            lp in installed for lp in REPLACEMENT_LAUNCHERS
        ):
            sel.skipped.append((pkg, "chybí náhradní launcher"))
        elif cand.get("kind") == "maybe_streaming" or _maybe_streaming(cand, hints):
            sel.ask.append(cand)
        else:
            sel.disable.append(cand)
    return sel
