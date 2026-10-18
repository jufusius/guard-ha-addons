# CC- 2026-10-18 (ANALYZA_cid6-poller-soc-95-spike_2026-10-15 §5): cid=6 briefing „Baterie teď 95 %" při 6,2 %.
# CC-   Namapovaná SoC entita byla krátce unavailable a _match_entity spadl do pattern fallbacku `_state_of_charge`
# CC-   → poslal `…minimum_state_of_charge_island` = 95 (CustomerTelemetry Id 785956, 15. 10. 06:04:17 SELČ).
# CC-   Spuštění: python -m pytest guard-agent/tests -q
import io
import json
import re
import sys
from pathlib import Path

import pytest

AGENT_DIR = Path(__file__).resolve().parents[1] / "agent"
sys.path.insert(0, str(AGENT_DIR))
import server  # noqa: E402

SOC = "sensor.rct_power_storage_battery_state_of_charge"
ISLAND = "sensor.rct_power_storage_battery_minimum_state_of_charge_island"
MINIMUM = "sensor.rct_power_storage_battery_minimum_state_of_charge"

CID6_MAPPING = {
    "battery_soc": SOC,
    "battery_power": "sensor.rct_power_storage_battery_power",
    "fve_production": "sensor.rct_power_storage_generator_power",
    "house_consumption": "sensor.rct_power_storage_household_load",
}


def _st(eid, state, **attrs):
    return {"entity_id": eid, "state": state, "attributes": attrs}


def _cid6_states(soc_state):
    return [
        _st(SOC, soc_state),
        _st(ISLAND, "95"),
        _st(MINIMUM, "7"),
        _st("sensor.rct_power_storage_battery_power", "-200"),
        _st("sensor.rct_power_storage_generator_power", "0"),
        _st("sensor.rct_power_storage_household_load", "420"),
    ]


@pytest.fixture(autouse=True)
def cid6_mapping():
    server.KEY_ENTITIES.clear()
    server.KEY_ENTITIES.update(CID6_MAPPING)
    yield
    server.KEY_ENTITIES.clear()


@pytest.mark.parametrize("state", ["unavailable", "unknown", ""])
def test_fixtura_785956_namapovany_soc_nedostupny_je_none_ne_island(state):
    t = server._build_telemetry(_cid6_states(state))
    assert t["battery_soc_pct"] is None
    assert "battery_soc_pct" not in t["sources"]


def test_namapovany_soc_dostupny_nese_zdroj_mapy():
    t = server._build_telemetry(_cid6_states("6.2"))
    assert t["battery_soc_pct"] == 6.2
    assert t["sources"]["battery_soc_pct"] == SOC


def test_role_bez_mapy_smi_pattern_fallback_a_hlasi_zdroj():
    server.KEY_ENTITIES.clear()
    t = server._build_telemetry([_st("sensor.goodwe_battery_state_of_charge", "55")])
    assert t["battery_soc_pct"] == 55.0
    assert t["sources"]["battery_soc_pct"] == "sensor.goodwe_battery_state_of_charge"


def test_grid_z_bilance_ma_zdroj_derived():
    t = server._build_telemetry(_cid6_states("6.2"))
    # load 420 − pv 0 − baterie (−200) = 620 W import
    assert t["grid_import_w"] == 620.0
    assert t["grid_export_w"] == 0.0
    assert t["sources"]["grid_import_w"] == server.DERIVED_BALANCE_SOURCE
    assert t["sources"]["grid_export_w"] == server.DERIVED_BALANCE_SOURCE


def test_payload_je_json_a_sources_obsahuji_jen_poslana_pole():
    t = server._build_telemetry(_cid6_states("unavailable"))
    json.dumps(t)
    for field in t["sources"]:
        assert t[field] is not None


class _Resp(io.BytesIO):
    pass


def test_fetch_key_entities_mapu_nahrazuje(monkeypatch):
    monkeypatch.setattr(server, "API_KEY", "k")
    payload = {"key_entities": {"battery_soc": "sensor.nova_soc"}}
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=15: _Resp(json.dumps(payload).encode()))
    server._fetch_key_entities()
    assert server.KEY_ENTITIES == {"battery_soc": "sensor.nova_soc"}


def test_jedine_sestaveni_telemetrie():
    # CC- PRAVIDLO Č.6: push i telemetry_loop sestavují payload jen přes _build_telemetry
    src = (AGENT_DIR / "server.py").read_text(encoding="utf-8")
    calls = [m.start() for m in re.finditer(r"(?<!def )_match_entity\(", src)]
    assert len(calls) == 1, "_match_entity se smí volat jen z _build_telemetry"
    assert len(re.findall(r"(?<!def )_build_telemetry\(", src)) == 2
