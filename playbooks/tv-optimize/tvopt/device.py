"""Detekce TV zákazníka z dat Guardu a HA. Nic napevno: IP, MAC, entity i cid jdou ze vstupu.

Vstup (`context`) dodává Guard jen pro daného zákazníka:
- `devices`: záznamy z Guard CustomerDevices (ip, mac, hostname, vendor, model, category)
- `states`: HA stavy (entity_id, state, attributes)
- `integrations`: HA config entries (domain, title, host, entry_id)
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import load_models

ANDROID_DOMAINS = ("androidtv", "androidtv_remote")
TV_HOST_HINTS = ("mitv", "android", "googletv", "bravia", "chromecast", "tv")


@dataclass
class TvDevice:
    platform: str                    # android_tv | google_tv | tizen | webos | unknown
    profile_id: str | None
    vendor: str = ""
    model: str = ""
    hostname: str = ""
    ip: str = ""
    mac: str = ""
    remote_entity: str | None = None     # remote.* z androidtv_remote
    adb_entity: str | None = None        # media_player.* z androidtv (ADB)
    kodi_entity: str | None = None       # media_player.* z kodi
    integrations: dict = field(default_factory=dict)  # domain -> entry
    warnings: list = field(default_factory=list)
    alternatives: list = field(default_factory=list)  # další možné TV: [{"ip", "hostname"}]

    @property
    def is_android(self) -> bool:
        return self.platform in ("android_tv", "google_tv")


def _low(x) -> str:
    return str(x or "").strip().lower()


def _looks_like_tv(dev: dict) -> bool:
    text = " ".join(_low(dev.get(k)) for k in ("hostname", "vendor", "model", "category", "name"))
    return any(h in text for h in TV_HOST_HINTS) or any(
        w in text for w in ("samsung", "lg", "webos", "tizen", "xiaomi")
    ) and ("tv" in text or "webos" in text or "tizen" in text)


def _match_profile(dev: dict, models: dict) -> str | None:
    vendor, model, host = _low(dev.get("vendor")), _low(dev.get("model")), _low(dev.get("hostname"))
    best, best_score = None, 0
    for mid, m in models.items():
        match = m.get("match", {})
        score = 0
        if any(v and v in vendor for v in map(_low, match.get("vendor", []))):
            score += 2
        if any(p and model.startswith(p) for p in map(_low, match.get("model_prefix", []))):
            score += 3
        if any(p and host.startswith(p) for p in map(_low, match.get("hostname_prefix", []))):
            score += 1 if mid == "android-tv" else 2
        if score > best_score:
            best, best_score = mid, score
    return best


def _entry_host(entry: dict) -> str:
    return str(entry.get("host") or entry.get("data", {}).get("host") or "")


def _entity_for_entry(prefix: str, entry: dict) -> str | None:
    """Entity patří ke config entry (Guard je dodá z entity registry, ne odhadem podle názvu)."""
    for eid in entry.get("entity_ids") or []:
        if eid.startswith(prefix):
            return eid
    return None


def detect_tv(context: dict, models: dict | None = None, ip: str | None = None) -> TvDevice:
    """Najde TV zákazníka. Když jich má víc, vybere podle `ip`; jinak první TV."""
    models = models or load_models()
    devices = context.get("devices", [])
    states = context.get("states", [])
    integrations = context.get("integrations", [])

    candidates = [d for d in devices if _looks_like_tv(d)]
    if ip:
        candidates = [d for d in devices if d.get("ip") == ip] or candidates
    hosts = {_entry_host(e) for e in integrations if _entry_host(e)}

    def rank(d):
        prof = _match_profile(d, models)
        # 1) konkrétní profil modelu (např. Xiaomi) před obecným „android“,
        # 2) TV, ke které už je v HA integrace
        return (prof in (None, "android-tv"), d.get("ip") not in hosts)

    candidates.sort(key=rank)

    if not candidates:
        return TvDevice(platform="unknown", profile_id=None,
                        warnings=["Mezi zařízeními zákazníka jsem nenašel televizi."])

    dev = candidates[0]
    alternatives = [{"ip": d.get("ip", ""), "hostname": d.get("hostname", "")} for d in candidates[1:]]
    dip = dev.get("ip", "")
    entries = {e["domain"]: e for e in integrations if _entry_host(e) == dip}
    text = " ".join(_low(dev.get(k)) for k in ("vendor", "model", "hostname", "name"))

    warnings = []
    if _low(dev.get("category")) in ("socket", "smart_socket", "plug", "switch") and _looks_like_tv(
        {k: v for k, v in dev.items() if k != "category"}
    ):
        warnings.append(
            f"Zařízení {dip} je v Guardu vedené jako „{dev.get('category')}“, ale podle názvu je to TV — "
            "opravit zařazení v admin portálu."
        )

    if "samsungtv" in entries or "tizen" in text or ("samsung" in text and "android" not in text):
        platform, profile = "tizen", None
    elif "webostv" in entries or "webos" in text or text.startswith("lg"):
        platform, profile = "webos", None
    else:
        profile = _match_profile(dev, models)
        is_android = profile is not None or any(d in entries for d in ANDROID_DOMAINS)
        if not is_android:
            platform, profile = "unknown", None
        else:
            gtv_launcher = any(
                "launcherx" in _low(s.get("attributes", {}).get("app_id")) for s in states
            )
            if profile is None or profile == "android-tv":
                profile = "google-tv" if gtv_launcher else "android-tv"
            platform = models[profile]["platform"]

    tv = TvDevice(
        platform=platform, profile_id=profile,
        vendor=dev.get("vendor", ""), model=dev.get("model", ""), hostname=dev.get("hostname", ""),
        ip=dip, mac=dev.get("mac", ""), integrations=entries, warnings=warnings,
        alternatives=alternatives,
    )
    if "androidtv_remote" in entries:
        tv.remote_entity = _entity_for_entry("remote.", entries["androidtv_remote"])
    if "androidtv" in entries:
        tv.adb_entity = _entity_for_entry("media_player.", entries["androidtv"])
    if "kodi" in entries:
        tv.kodi_entity = _entity_for_entry("media_player.", entries["kodi"])
    return tv


def parse_package_list(adb_output: str) -> set[str]:
    """Výstup `pm list packages` → množina balíčků."""
    out = set()
    for line in (adb_output or "").splitlines():
        line = line.strip()
        if line.startswith("package:"):
            out.add(line.split(":", 1)[1].strip())
    return out
