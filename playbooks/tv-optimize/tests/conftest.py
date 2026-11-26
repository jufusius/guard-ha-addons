# Spuštění: python -m pytest playbooks/tv-optimize/tests -q
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tvopt.engine import AdbError  # noqa: E402

MITV_IP = "192.0.2.14"   # dokumentační rozsah, žádná reálná IP
OTHER_CID_IP = "192.0.2.99"


class FakeTv:
    """Simulace Android TV za ADB: nastavení, nainstalované a vypnuté balíčky."""

    def __init__(self, installed, disabled=(), settings=None):
        self.installed = set(installed)
        self.disabled = set(disabled)
        self.settings = dict(settings or {})
        self.adb_on = True
        self.log = []

    def shell(self, cmd):
        if not self.adb_on:
            raise AdbError("connection refused")
        self.log.append(cmd)
        parts = cmd.split()
        if cmd == "getprop ro.product.model":
            return "MiTV-MOOQ0"
        if cmd == "pm list packages":
            return "\n".join(f"package:{p}" for p in sorted(self.installed))
        if cmd == "pm list packages -d":
            return "\n".join(f"package:{p}" for p in sorted(self.disabled))
        if parts[:2] == ["settings", "get"]:
            return self.settings.get(f"{parts[2]}/{parts[3]}", "null")
        if parts[:2] == ["settings", "put"]:
            key = f"{parts[2]}/{parts[3]}"
            self.settings[key] = parts[4]
            if key == "global/adb_enabled" and parts[4] == "0":
                self.adb_on = False
                raise AdbError("device offline")
            return ""
        if parts[:2] == ["settings", "delete"]:
            self.settings.pop(f"{parts[2]}/{parts[3]}", None)
            return ""
        if parts[:2] == ["pm", "disable-user"]:
            self.disabled.add(parts[-1])
            return ""
        if parts[:2] == ["pm", "enable"]:
            self.disabled.discard(parts[-1])
            return ""
        return ""


class FakeGuard:
    """Implementace GuardApi pro testy. Data jsou oddělená podle cid."""

    def __init__(self):
        self.contexts = {}
        self.writable = {}
        self.tvs = {}          # (cid, entity) -> FakeTv
        self.store = {}
        self.audits = []
        self.approvals = []
        self.services = []
        self.flows = []
        self.ports = {}

    def context(self, cid):
        return self.contexts.get(cid, {"devices": [], "states": [], "integrations": []})

    def writable_entities(self, cid):
        return set(self.writable.get(cid, set()))

    def request_write_approval(self, cid, entity_id, reason):
        self.approvals.append((cid, entity_id, reason))
        return f"APR-{len(self.approvals)}"

    def start_config_flow(self, cid, domain, data):
        self.flows.append((cid, domain, data))
        return {"flow_id": "f1", "type": "form"}

    def adb(self, cid, entity_id, command):
        if entity_id not in self.writable.get(cid, set()):
            raise AssertionError("gateway: unknown_entity — engine to měl zachytit dřív")
        return self.tvs[(cid, entity_id)].shell(command)

    def call_service(self, cid, domain, service, data):
        self.services.append((cid, domain, service, data))
        return {}

    def port_status(self, cid, ip, ports):
        tv = next((t for (c, _), t in self.tvs.items() if c == cid), None)
        res = {p: False for p in ports}
        if tv and tv.adb_on:
            res[5555] = True
        res.update(self.ports.get(cid, {}))
        return res

    def load(self, cid, key):
        return self.store.get((cid, key))

    def save(self, cid, key, value):
        import copy
        self.store[(cid, key)] = copy.deepcopy(value)

    def audit(self, record):
        self.audits.append(record)


MITV_INSTALLED = {
    "com.google.android.youtube.tv", "com.netflix.ninja", "com.android.vending", "com.google.android.gms",
    "com.google.android.tvlauncher", "com.google.android.backdrop", "com.google.android.play.games",
    "com.android.printspooler", "com.google.android.feedback", "com.miui.tv.analytics",
    "com.google.android.katniss", "com.wbd.stream",
}


def mitv_context(with_adb=True, with_remote=True):
    ents = []
    integrations = []
    states = [{"entity_id": "media_player.obyvak_tv", "state": "on", "attributes": {}}]
    if with_adb:
        integrations.append({"domain": "androidtv", "host": MITV_IP, "entity_ids": ["media_player.obyvak_tv"]})
    if with_remote:
        integrations.append({"domain": "androidtv_remote", "host": MITV_IP, "entity_ids": ["remote.obyvak_tv"]})
        states.append({"entity_id": "remote.obyvak_tv", "state": "on", "attributes": {}})
    return {
        "devices": [
            {"ip": "192.0.2.6", "hostname": "Android-2", "vendor": "", "model": ""},
            {"ip": MITV_IP, "hostname": "MITV", "vendor": "Tuya", "model": "", "category": "smart_socket",
             "mac": "02:00:00:00:00:01"},
            {"ip": "192.0.2.50", "hostname": "ESP-boiler", "vendor": "Espressif"},
        ],
        "states": states + ents,
        "integrations": integrations,
    }


@pytest.fixture
def guard():
    g = FakeGuard()
    g.contexts[5] = mitv_context()
    g.writable[5] = {"media_player.obyvak_tv", "remote.obyvak_tv"}
    g.tvs[(5, "media_player.obyvak_tv")] = FakeTv(
        MITV_INSTALLED, disabled={"com.google.android.play.games"},
        settings={"global/window_animation_scale": "1.0"})
    # jiný zákazník se stejnou TV — nesmí se ho nic dotknout
    g.contexts[7] = {"devices": [{"ip": OTHER_CID_IP, "hostname": "MITV"}], "states": [
        {"entity_id": "media_player.cizi_tv", "state": "on", "attributes": {}}],
        "integrations": [{"domain": "androidtv", "host": OTHER_CID_IP, "entity_ids": ["media_player.cizi_tv"]}]}
    g.writable[7] = {"media_player.cizi_tv"}
    g.tvs[(7, "media_player.cizi_tv")] = FakeTv(MITV_INSTALLED)
    return g


@pytest.fixture
def clock():
    return lambda: datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)
