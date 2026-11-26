"""Chráněné aplikace a výběr balíčků k vypnutí.

Ochrana = přesný balíček nebo prefix (výchozí seznam + systémové + aplikace zákazníka).
Podřetězec v názvu (`warn_keywords`, volný text zákazníka) je jen ZÁLOŽNÍ VAROVÁNÍ: takový
kandidát jde do dotazu zákazníkovi, nikdy do plánu — není jedinou ochranou.

Výběr kandidátů k vypnutí:
- jen balíček z profilu modelu s `verified: true` nebo schválený adminem (`approved`),
- jen nainstalovaný a ještě nevypnutý,
- nikdy chráněný; podezřelý (varování / možná streaming) → dotaz,
- `requires_replacement_launcher` jen s nainstalovaným náhradním launcherem.
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
    warn_keywords: set = field(default_factory=set)

    def protects(self, package: str) -> bool:
        """Tvrdá ochrana: přesný balíček nebo prefix."""
        p = package.lower()
        return p in self.packages or any(p.startswith(x) for x in self.prefixes)

    def suspect(self, package: str, label: str = "") -> bool:
        """Záložní varování: klíčové slovo v balíčku nebo názvu → zeptat se, nevypínat."""
        p, lab = package.lower(), norm(label)
        return any(k and (k in p or k in lab) for k in self.warn_keywords)


def _add_app(prot: Protection, app: dict) -> None:
    prot.packages.update(x.lower() for x in app.get("packages", []))
    prot.prefixes.update(x.lower() for x in app.get("prefixes", []))
    prot.warn_keywords.update(norm(x) for x in app.get("warn_keywords", []) if norm(x))


def build_protection(customer_apps: list | None = None, protected_cfg: dict | None = None) -> Protection:
    cfg = protected_cfg or load_protected()
    prot = Protection()
    for app in cfg["apps"]:
        _add_app(prot, app)
    _add_app(prot, cfg.get("system", {}))
    for item in customer_apps or []:
        resolved = resolve_app_name(item, cfg)
        if resolved:
            _add_app(prot, resolved)
        elif PKG_RE.match(str(item).strip()):
            prot.packages.add(str(item).strip().lower())
        elif len(norm(item)) >= 3:
            prot.warn_keywords.add(norm(item))
    return prot


def resolve_app_name(name: str, protected_cfg: dict | None = None) -> dict | None:
    """„netflix“, „Voyo“, „Disney+“ → záznam chráněné aplikace (nebo None)."""
    cfg = protected_cfg or load_protected()
    n = norm(name)
    if not n:
        return None
    for app in cfg["apps"]:
        if n in [norm(app["name"])] + [norm(a) for a in app.get("aliases", [])]:
            return app
    return None


def disable_candidates(profile: dict, approved: set | None = None) -> set:
    """Balíčky, které playbook u tohoto modelu SMÍ vypnout (pro allowlist v security.py)."""
    approved = approved or set()
    out = set()
    for mod in ("speed", "ads"):
        for c in profile.get(mod, {}).get("disable_candidates", []):
            if c.get("verified") or c["package"] in approved:
                out.add(c["package"])
    return out


def _maybe_streaming(cand: dict, hints: list) -> bool:
    if cand.get("kind") in ("bloat", "ads", "tracking", "launcher"):
        return False
    text = cand["package"].lower() + " " + norm(cand.get("label", ""))
    return any(h in text for h in hints)


@dataclass
class Selection:
    disable: list = field(default_factory=list)
    ask: list = field(default_factory=list)
    skipped: list = field(default_factory=list)


def select_packages(profile: dict, module: str, installed: set, disabled: set | None = None,
                    protection: Protection | None = None, approved: set | None = None,
                    protected_cfg: dict | None = None) -> Selection:
    cfg = protected_cfg or load_protected()
    protection = protection or build_protection(protected_cfg=cfg)
    disabled, approved = disabled or set(), approved or set()
    hints = cfg.get("streaming_hints", [])
    sel = Selection()
    for cand in profile.get(module, {}).get("disable_candidates", []):
        pkg = cand["package"]
        if not PKG_RE.match(pkg):
            sel.skipped.append((pkg, "neplatný název balíčku"))
        elif protection.protects(pkg):
            sel.skipped.append((pkg, "chráněná aplikace"))
        elif pkg not in installed:
            sel.skipped.append((pkg, "na TV není"))
        elif pkg in disabled:
            sel.skipped.append((pkg, "už je vypnutý"))
        elif not cand.get("verified", False) and pkg not in approved:
            sel.skipped.append((pkg, "neověřený kandidát (čeká na admina)"))
        elif cand.get("requires_replacement_launcher") and not any(
                lp in installed and lp not in disabled for lp in REPLACEMENT_LAUNCHERS):
            sel.skipped.append((pkg, "chybí náhradní launcher"))
        elif protection.suspect(pkg, cand.get("label", "")) or cand.get("kind") == "maybe_streaming" \
                or _maybe_streaming(cand, hints):
            sel.ask.append(cand)
        else:
            sel.disable.append(cand)
    return sel
