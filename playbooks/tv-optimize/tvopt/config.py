"""Načítání konfigurace: profily modelů TV a chráněné aplikace."""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_protected(config_dir: Path = CONFIG_DIR) -> dict:
    return _read(config_dir / "protected_apps.json")


def load_unsupported(config_dir: Path = CONFIG_DIR) -> dict:
    return _read(config_dir / "unsupported.json")


def _merge(base: dict, child: dict) -> dict:
    """Profil `extends` jiný: slovníky se slučují, seznamy kandidátů se sčítají."""
    out = deepcopy(base)
    for key, val in child.items():
        if key in ("id", "name", "platform", "match", "extends", "_comment"):
            out[key] = deepcopy(val)
        elif isinstance(val, dict) and isinstance(out.get(key), dict):
            merged = dict(out[key])
            for k2, v2 in val.items():
                if isinstance(v2, list) and isinstance(merged.get(k2), list):
                    merged[k2] = merged[k2] + [x for x in v2 if x not in merged[k2]]
                else:
                    merged[k2] = deepcopy(v2)
            out[key] = merged
        else:
            out[key] = deepcopy(val)
    out.pop("extends", None)
    return out


def load_models(config_dir: Path = CONFIG_DIR) -> dict[str, dict]:
    raw = {}
    for p in sorted((config_dir / "models").glob("*.json")):
        m = _read(p)
        raw[m["id"]] = m

    resolved: dict[str, dict] = {}

    def resolve(mid: str, seen: tuple = ()) -> dict:
        if mid in resolved:
            return resolved[mid]
        if mid in seen:
            raise ValueError(f"cyklus v extends: {' -> '.join(seen + (mid,))}")
        m = raw[mid]
        if m.get("extends"):
            m = _merge(resolve(m["extends"], seen + (mid,)), m)
        resolved[mid] = m
        return m

    for mid in raw:
        resolve(mid)
    return resolved
