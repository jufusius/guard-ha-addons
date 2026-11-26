from conftest import MITV_IP, mitv_context

from tvopt.device import detect_tv, parse_package_list


def test_mitv_rozpoznana_podle_hostname_i_kdyz_je_vedena_jako_zasuvka():
    tv = detect_tv(mitv_context())
    assert tv.platform == "android_tv"
    assert tv.profile_id == "xiaomi-mitv"
    assert tv.ip == MITV_IP
    assert tv.adb_entity == "media_player.obyvak_tv"
    assert tv.remote_entity == "remote.obyvak_tv"
    assert any("vedené jako" in w for w in tv.warnings)


def test_konkretni_model_ma_prednost_pred_obecnym_android_zarizenim():
    ctx = mitv_context()
    ctx["integrations"].append({"domain": "kodi", "host": "192.0.2.6", "entity_ids": ["media_player.android_2"]})
    tv = detect_tv(ctx)
    assert tv.ip == MITV_IP
    assert {"ip": "192.0.2.6", "hostname": "Android-2"} in tv.alternatives


def test_vyber_podle_ip_kdyz_ma_zakaznik_vic_tv():
    tv = detect_tv(mitv_context(), ip="192.0.2.6")
    assert tv.ip == "192.0.2.6"
    assert tv.profile_id == "android-tv"


def test_nic_neni_napevno_ip_a_entity_jdou_ze_vstupu():
    ctx = mitv_context()
    ctx["devices"][1]["ip"] = "198.51.100.7"
    for e in ctx["integrations"]:
        e["host"] = "198.51.100.7"
        e["entity_ids"] = [x.replace("obyvak", "loznice") for x in e["entity_ids"]]
    tv = detect_tv(ctx)
    assert tv.ip == "198.51.100.7"
    assert tv.adb_entity == "media_player.loznice_tv"


def test_google_tv_podle_launcheru():
    ctx = {"devices": [{"ip": "192.0.2.20", "hostname": "android-abc"}],
           "states": [{"entity_id": "media_player.gtv", "state": "on",
                       "attributes": {"app_id": "com.google.android.apps.tv.launcherx"}}],
           "integrations": [{"domain": "androidtv_remote", "host": "192.0.2.20", "entity_ids": ["remote.gtv"]}]}
    tv = detect_tv(ctx)
    assert tv.platform == "google_tv" and tv.profile_id == "google-tv"


def test_samsung_tizen():
    ctx = {"devices": [{"ip": "192.0.2.30", "hostname": "Samsung-TV", "vendor": "Samsung"}],
           "states": [], "integrations": [{"domain": "samsungtv", "host": "192.0.2.30"}]}
    tv = detect_tv(ctx)
    assert tv.platform == "tizen" and not tv.is_android and tv.profile_id is None


def test_lg_webos():
    ctx = {"devices": [{"ip": "192.0.2.31", "hostname": "LGwebOSTV", "vendor": "LG Electronics"}],
           "states": [], "integrations": []}
    assert detect_tv(ctx).platform == "webos"


def test_bez_tv():
    tv = detect_tv({"devices": [{"ip": "192.0.2.50", "hostname": "ESP-boiler"}], "states": [], "integrations": []})
    assert tv.platform == "unknown" and tv.warnings


def test_neznama_tv_bez_android_integrace_neni_android():
    ctx = {"devices": [{"ip": "192.0.2.40", "hostname": "Philips-TV", "vendor": "TP Vision"}],
           "states": [], "integrations": []}
    assert detect_tv(ctx).platform == "unknown"


def test_parse_package_list():
    out = "package:com.a.b\npackage:org.xbmc.kodi\n\nWARNING: x\n"
    assert parse_package_list(out) == {"com.a.b", "org.xbmc.kodi"}
