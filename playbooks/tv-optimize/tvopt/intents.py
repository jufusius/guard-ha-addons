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
YES = {"ano", "jo", "jj", "ok", "okay", "souhlasim", "muzes", "muzete", "pust se do toho",
       "do toho", "proved", "provedte", "udelej to", "jasne", "beru", "potvrzuji"}
NO = {"ne", "nechci", "zrus", "stop", "nedelej", "pockej", "nesouhlasim", "radsi ne"}
DONE = {"hotovo", "mam", "mam to", "je to", "hotove", "udelano", "done", "uz", "jo mam", "povoleno",
        "potvrzeno", "nainstalovano"}

TV_WORDS = re.compile(r"\b(tv|televiz\w*|telku|telka|obrazovk\w*)\b")


def detect_intents(text: str) -> list[str]:
    """Vrátí záměry ve větě (může jich být víc: „zrychli TV a dej pryč reklamy“)."""
    t = norm(text)
    found = [name for name, pats in INTENT_PATTERNS if any(re.search(p, t) for p in pats)]
    # „vrať“ bez kontextu TV / změn může znamenat cokoli — revert jen s kontextem nebo samostatně
    if "revert" in found and len(t.split()) > 8 and not TV_WORDS.search(t) and "zmen" not in t:
        found.remove("revert")
    return found


def _short(text: str) -> str:
    return re.sub(r"[!.,?]+", "", norm(text)).strip()


def is_yes(text: str) -> bool:
    t = _short(text)
    return t in YES or t.startswith(("ano", "jo ", "souhlasim", "ok "))


def is_no(text: str) -> bool:
    t = _short(text)
    return t in NO or t.startswith(("ne ", "nechci", "zrus"))


def is_done(text: str) -> bool:
    t = _short(text)
    return t in DONE or t.startswith("hotovo") or t.endswith("hotovo")
