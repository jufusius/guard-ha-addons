"""Rozpoznání záměru zákazníka z věty v chatu (česky, s diakritikou i bez)."""
from __future__ import annotations

import re

from .packages import norm

# pořadí = priorita (revert vyhrává: „vrať to, nefunguje mi Netflix“)
INTENT_PATTERNS = [
    ("revert", [r"\bvrat", r"zpatky", r"\bzpet\b", r"puvodn", r"zrus(it)? (vsechny )?zmen",
                r"udelej to jak bylo", r"jak to bylo", r"undo"]),
    ("kodi", [r"\bkodi", r"\bxbmc"]),
    ("ads", [r"reklam", r"(bez|proti|vypn\w*|zakaz\w*) sledovan", r"\bads?\b", r"soukrom", r"bez doporuc", r"spehov"]),
    ("speed", [r"zrychl", r"pomal", r"\bsek[aá]", r"zasek", r"\blag", r"\btrva\b.*nacit", r"nacita",
               r"rychlejsi", r"optimaliz", r"vyladit", r"reaguje"]),
    ("status", [r"co (jsi|jste) (zmenil|udelal)", r"jake zmeny", r"stav (tv|televize)"]),
]
TV_WORDS = re.compile(r"\b(tv|televiz\w*|telku|telka|obrazovk\w*)\b")


def detect_intents(text: str) -> list[str]:
    """Vrátí záměry ve větě (může jich být víc: „zrychli TV a dej pryč reklamy“).
    Slouží jen k výběru modulu — nikdy jako souhlas."""
    t = norm(text)
    found = [name for name, pats in INTENT_PATTERNS if any(re.search(p, t) for p in pats)]
    if "revert" in found and len(t.split()) > 8 and not TV_WORDS.search(t) and "zmen" not in t:
        found.remove("revert")
    return found


def _short(text) -> str:
    if not isinstance(text, str):
        return ""
    return re.sub(r"[\s!.,?]+$", "", norm(text)).strip()


def parse_consent(text) -> str:
    """Přesná shoda: „ano“ → yes, „ne“ → no, cokoli jiného → ambiguous (zeptat se znovu).
    „jo nechci“, „ano ne“, „souhlasím, ale…“ jsou nejednoznačné."""
    t = _short(text)
    if t == "ano":
        return "yes"
    if t == "ne":
        return "no"
    return "ambiguous"


def is_yes(text) -> bool:
    return parse_consent(text) == "yes"


def is_no(text) -> bool:
    return parse_consent(text) == "no"


def is_done(text) -> bool:
    """Krok zákazníka je hotový jen při přesném „hotovo“."""
    return _short(text) == "hotovo"
