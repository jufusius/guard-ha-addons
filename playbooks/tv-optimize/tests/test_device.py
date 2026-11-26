from conftest import MITV_IP, mitv_context

from tvopt.device import detect_tv, parse_package_list


def test_mitv_rozpoznana_podle_hostname_i_kdyz_je_vedena_jako_zasuvka():
    tv = detect_tv(mitv_context())
    assert tv.platform == "android_tv" and tv.profile_id == "xiaomi-mitv"
    assert tv.ip == MITV_IP
    assert tv.adb_entity == "media_player.obyvak_tv" and tv.remote_entity == "remote.obyvak_tv"
    assert any("navrhuji opravit" in w for w in tv.warnings)


def test_obecny_hostname_android_neni_tv():
    # „Android-2“ (telefon) bez TV integrace není kandidát → žádné alternativy
    tv = detect_tv(mitv_context())
    assert tv.alternatives == []


def test_vice_tv_vraci_alternativy():
    ctx = mitv_context()
    ctx["integrations"].append({"domain": "androidtv_remote", "host": "192.0.2.6", "entity_ids": ["remote.druha"]})
    tv = detect_tv(ctx)
    assert tv.ip == MITV_IP                       # konkrétní model má přednost
    assert tv.alternatives == [{"ip": "192.0.2.6", "hostname": "Android-2"}]


def test_vyber_podle_ip_jen_mezi_tv_zakaznika():
    ctx = mitv_context()
    ctx["integrations"].append({"domain": "androidtv_remote", "host": "192.0.2.6", "entity_ids": ["remote.druha"]})
    assert detect_tv(ctx, ip="192.0.2.6").ip == "192.0.2.6"
    cizi = detect_tv(ctx, ip="192.0.2.99")
    assert cizi.platform == "unknown" and cizi.ip == ""
    assert detect_tv(ctx, ip="192.0.2.50").ip == ""   # bojler není TV


def test_nic_neni_napevno_ip_a_entity_jdou_ze_vstupu():
    ctx = mitv_context()
    ctx["devices"][1]["ip"] = "198.51.100.7"
    for e in ctx["integrations"]:
        e["host"] = "198.51.100.7"
        e["entity_ids"] = [x.replace("obyvak", "loznice") for x in e["entity_ids"]]
    tv = detect_tv(ctx)
    assert tv.ip == "198.51.100.7" and tv.adb_entity == "media_player.loznice_tv"


def test_google_tv_podle_launcheru():
    ctx = {"devices": [{"ip": "192.0.2.20", "hostname": "obyvak"}],
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
    tv = detect_tv({"devices": [{"ip": "192.0.2.50", "hostname": "ESP-boiler"},
                                {"ip": "192.0.2.51", "hostname": "tvoje-nas"}], "states": [], "integrations": []})
    assert tv.platform == "unknown" and tv.warnings


def test_parse_package_list():
    out = "package:com.a.b\npackage:org.xbmc.kodi\n\nWARNING: x\n"
    assert parse_package_list(out) == {"com.a.b", "org.xbmc.kodi"}
