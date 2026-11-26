"""Snapshot před změnou, záznam provedených změn a výpočet vrácení.

Vrácení se počítá ze snapshotu (stav PŘED playbookem), ne z „výchozích hodnot“:
- nastavení, které předtím neexistovalo → `settings delete`, jinak `settings put <původní>`
- balíček, který vypnul playbook → `pm enable`; balíček vypnutý už před playbookem se nechá
"""
from __future__ import annotations

import shlex
from dataclasses import asdict, dataclass, field

from .device import parse_package_list

NULL_VALUES = ("", "null", None)


@dataclass
class Snapshot:
    settings: dict = field(default_factory=dict)      # "global/key" -> hodnota | None
    disabled_before: list = field(default_factory=list)
    installed: list = field(default_factory=list)
    taken_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Snapshot":
        return cls(**{k: d.get(k, v) for k, v in cls().__dict__.items()})


@dataclass
class Change:
    kind: str          # setting | disable | maintenance | install_prompt
    target: str        # "global/window_animation_scale" | balíček | příkaz
    before: str | None = None
    after: str | None = None
    module: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def snapshot_commands(setting_keys: list[str]) -> list[str]:
    cmds = ["pm list packages", "pm list packages -d"]
    cmds += [f"settings get {k.split('/')[0]} {k.split('/')[1]}" for k in setting_keys]
    return cmds


def build_snapshot(outputs: dict, setting_keys: list[str], taken_at: str = "") -> Snapshot:
    """`outputs`: příkaz → výstup ADB (výsledek snapshot_commands)."""
    snap = Snapshot(taken_at=taken_at)
    snap.installed = sorted(parse_package_list(outputs.get("pm list packages", "")))
    snap.disabled_before = sorted(parse_package_list(outputs.get("pm list packages -d", "")))
    for k in setting_keys:
        ns, key = k.split("/")
        val = (outputs.get(f"settings get {ns} {key}") or "").strip()
        snap.settings[k] = None if val in NULL_VALUES else val
    return snap


def revert_commands(snapshot: Snapshot, changes: list[Change]) -> list[str]:
    """Příkazy, které vrátí TV do stavu ze snapshotu. Pořadí: obráceně proti změnám."""
    cmds: list[str] = []
    seen = set()
    for ch in reversed(changes):
        key = (ch.kind, ch.target)
        if key in seen:
            continue
        seen.add(key)
        if ch.kind == "setting":
            ns, k = ch.target.split("/")
            orig = snapshot.settings.get(ch.target, ch.before)
            if orig in NULL_VALUES:
                cmds.append(f"settings delete {ns} {k}")
            else:
                cmds.append(f"settings put {ns} {k} {orig}")
        elif ch.kind == "disable":
            if ch.target not in snapshot.disabled_before:
                cmds.append(f"pm enable {ch.target}")
        # maintenance (cache, kill-all) a install_prompt se nevrací — nic trvale nemění
    return cmds


def render_script(ip: str, commands: list[str], port: int = 5555) -> str:
    """Ruční záložní skript pro technika (adb z počítače v síti zákazníka)."""
    target = f"{ip}:{port}"
    lines = [
        "#!/usr/bin/env bash",
        "# Guard tv-optimize — vrácení změn (vygenerováno ze snapshotu).",
        "# Spouští technik z počítače v síti zákazníka, TV musí mít zapnuté síťové ladění.",
        "set -euo pipefail",
        f"TV={shlex.quote(target)}",
        'adb connect "$TV"',
    ]
    lines += [f'adb -s "$TV" shell {shlex.quote(c)}' for c in commands]
    lines += ['adb disconnect "$TV"', ""]
    return "\n".join(lines)
