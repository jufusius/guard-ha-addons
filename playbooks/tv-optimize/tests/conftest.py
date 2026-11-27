# Spuštění: python -m pytest playbooks/tv-optimize/tests -q
import copy
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tvopt.engine import AdbError  # noqa: E402

MITV_IP = "192.0.2.14"   # dokumentační rozsahy (RFC 5737), žádné reálné adresy
OTHER_CID_IP = "192.0.2.99"
NOW = "2026-11-26T12:00:00+00:00"


class FakeTv:
    """Simulace Android TV za ADB: nastavení, nainstalované a vypnuté balíčky, HOME launcher."""

    def __init__(self, installed, disabled=(), settings=None, home="com.google.android.tvlauncher"):
        self.installed = set(installed)
        self.disabled = set(disabled)
        self.settings = dict(settings or {})
        self.adb_on = True
        self.home = home
        self.online = True          # odpovídá TV na test připojení?
        self.online_after = 0       # kolik pokusů o ping má selhat, než projde
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
        if cmd.startswith("ping "):
            if self.online and self.online_after <= 0:
                return "1 packets transmitted, 1 received, 0% packet loss, time 0ms"
            self.online_after -= 1
            return "1 packets transmitted, 0 received, 100% packet loss, time 0ms"
        if cmd.startswith("cmd package resolve-activity"):
            return f"priority=0 preferredOrder=0\n{self.home}/.Main"
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
    """Implementace GuardApi pro testy. Data jsou oddělená podle cid. Viz docs/GuardApi.md."""

    def __init__(self):
        self.contexts, self.writable, self.tvs, self.store = {}, {}, {}, {}
        self.audits, self.approvals, self.approval_states = [], [], {}
        self.services, self.flows, self.admin, self.ports = [], [], [], {}
        self.messages = {}       # (cid, conv, id) -> msg
        self.approved = {}       # cid -> set balíčků
        self.dot_ok = True
        self.port_unknown = False

    def say(self, cid, text, conv="conv-1", author="customer", ts=NOW):
        mid = f"m{len(self.messages) + 1}"
        self.messages[(cid, conv, mid)] = {"id": mid, "author": author, "text": text, "ts": ts,
                                           "cid": cid, "conversation_id": conv}
        return mid

    def context(self, cid):
        return copy.deepcopy(self.contexts.get(cid, {"devices": [], "states": [], "integrations": []}))

    def writable_entities(self, cid):
        return set(self.writable.get(cid, set()))

    def request_write_approval(self, cid, target, reason):
        self.approvals.append((cid, target, reason))
        t = f"APR-{len(self.approvals)}"
        self.approval_states[t] = "pending"
        return t

    def approval_status(self, cid, ticket):
        return self.approval_states.get(ticket, "unknown")

    def approved_packages(self, cid, profile_id):
        return set(self.approved.get(cid, set()))

    def customer_message(self, cid, conversation_id, message_id):
        m = self.messages.get((cid, conversation_id, message_id))
        return copy.deepcopy(m)

    def notify_admin(self, cid, subject, detail):
        self.admin.append((cid, subject, detail))
        return f"ADM-{len(self.admin)}"

    def start_config_flow(self, cid, domain, data):
        self.flows.append((cid, domain, data))
        return {"flow_id": "f1", "type": "form"}

    def adb(self, cid, entity_id, command):
        if entity_id not in self.writable.get(cid, set()):
            raise AssertionError("gateway: unknown_entity — engine to měl zachytit dřív")
        return self.tvs[(cid, entity_id)].shell(command)

    def call_service(self, cid, domain, service, data):
        self.services.append((cid, domain, service, data))
        for s in self.contexts[cid]["states"]:
            if s["entity_id"] == data.get("entity_id") and domain == "switch":
                s["state"] = "on" if service == "turn_on" else "off"
        return {}

    def port_status(self, cid, ip, ports):
        if self.port_unknown:
            return {}
        tv = next((t for (c, _), t in self.tvs.items() if c == cid), None)
        res = {p: False for p in ports}
        if tv and tv.adb_on:
            res[5555] = True
        res.update(self.ports.get(cid, {}))
        return res

    def check_dot(self, cid, hostname):
        return self.dot_ok

    def load(self, cid, key):
        return copy.deepcopy(self.store.get((cid, key)))

    def save(self, cid, key, value):
        self.store[(cid, key)] = copy.deepcopy(value)

    def audit(self, record):
        self.audits.append(record)


MITV_INSTALLED = {
    "com.google.android.youtube.tv", "com.netflix.ninja", "com.android.vending", "com.google.android.gms",
    "com.google.android.tvlauncher", "com.google.android.backdrop", "com.google.android.play.games",
    "com.android.printspooler", "com.google.android.feedback", "com.miui.tv.analytics",
    "com.google.android.katniss", "com.wbd.stream", "net.cme.voyo.cz.tvapp",
}


def mitv_context(with_adb=True, with_remote=True, adguard=None):
    integrations = []
    states = [{"entity_id": "media_player.obyvak_tv", "state": "on", "attributes": {}}]
    if with_adb:
        integrations.append({"domain": "androidtv", "host": MITV_IP, "entity_ids": ["media_player.obyvak_tv"]})
    if with_remote:
        integrations.append({"domain": "androidtv_remote", "host": MITV_IP, "entity_ids": ["remote.obyvak_tv"]})
        states.append({"entity_id": "remote.obyvak_tv", "state": "on", "attributes": {}})
    if adguard:
        integrations.append({"domain": "adguard", "host": "192.0.2.2"})
        states.append({"entity_id": "switch.adguard_home_protection", "state": adguard, "attributes": {}})
    return {
        "devices": [
            {"ip": "192.0.2.6", "hostname": "Android-2", "vendor": "", "model": ""},
            {"ip": MITV_IP, "hostname": "MITV", "vendor": "Tuya", "model": "MiTV-MOOQ0",
             "category": "smart_socket", "mac": "02:00:00:00:00:01"},
            {"ip": "192.0.2.50", "hostname": "ESP-boiler", "vendor": "Espressif"},
        ],
        "states": states,
        "integrations": integrations,
    }


@pytest.fixture
def guard():
    g = FakeGuard()
    g.contexts[5] = mitv_context()
    g.writable[5] = {"media_player.obyvak_tv", "remote.obyvak_tv", "switch.adguard_home_protection"}
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


class FakeTime:
    """Falešný monotónní čas: sleep jen posune hodiny."""

    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


@pytest.fixture
def clock():
    return lambda: datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)
