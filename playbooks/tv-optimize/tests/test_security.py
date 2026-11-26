import pytest

from tvopt.packages import build_protection
from tvopt.security import (CommandPolicy, NotWritable, PolicyError, check_adb_command, check_entity,
                            check_install_source, check_ports, check_service, redact, sanitize_app_name)

POL = CommandPolicy(protection=build_protection(),
                    disable_allowed={"com.google.android.backdrop"},
                    enable_allowed={"com.google.android.backdrop"},
                    dns_hosts={"dns.adguard-dns.com"},
                    restore_values={"global/private_dns_specifier": "dns.example"})


@pytest.mark.parametrize("cmd", [
    "settings put global window_animation_scale 0.5",
    "settings delete global transition_animation_scale",
    "settings put global private_dns_specifier dns.adguard-dns.com",
    "settings put global private_dns_specifier dns.example",       # vrácení na původní ze snapshotu
    "settings put global private_dns_mode hostname",
    "settings put global adb_enabled 0",
    "settings put global adb_wifi_enabled 0",
    "pm disable-user --user 0 com.google.android.backdrop",
    "pm enable com.google.android.backdrop",
    "pm list packages -d",
    "pm trim-caches 2G",
    "am kill-all",
    "cmd package resolve-activity --brief -a android.intent.action.MAIN -c android.intent.category.HOME",
    "am start -a android.intent.action.VIEW -d market://details?id=org.xbmc.kodi",
])
def test_povolene_prikazy(cmd):
    assert check_adb_command(cmd, POL) == cmd


@pytest.mark.parametrize("cmd,why", [
    ("pm disable-user --user 0 com.android.printspooler", "není ověřený kandidát"),   # nechráněný, ale ne v profilu
    ("pm disable-user --user 0 com.netflix.ninja", "chráněná"),
    ("pm disable-user --user 0 net.cme.voyo.cz.tvapp", "chráněná"),
    ("pm disable-user --user 0 com.mediatek.wwtv.tvcenter", "chráněná"),
    ("pm enable com.android.printspooler", "nevypínal"),
    ("settings put global private_dns_specifier evil.example.com", "DNS hostname"),
    ("settings put global adb_enabled 1", "jen na 0"),
    ("settings put global adb_wifi_enabled 1", "jen na 0"),
    ("settings delete global adb_enabled", "nemaže"),
    ("settings put secure install_non_market_apps 1", "měnit nesmí"),
    ("settings put global http_proxy 192.0.2.1:8080", "není na seznamu"),
    ("settings put global http_proxy x", "měnit nesmí"),
    ("settings put global window_animation_scale 10", "animace"),
    ("pm uninstall com.google.android.backdrop", "není na seznamu"),
    ("pm install /sdcard/x.apk", "není na seznamu"),
    ("settings put global window_animation_scale 0.5; reboot", "zakázané znaky"),
    ("settings put global window_animation_scale 0.5 && rm -rf /", "zakázané znaky"),
    ("settings put global window_animation_scale $(id)", "zakázané znaky"),
    ("settings put global window_animation_scale `id`", "zakázané znaky"),
    ("settings put global window_animation_scale 0.5\nreboot", "zakázané znaky"),
    ("reboot", "není na seznamu"),
    ("am start -a android.intent.action.VIEW -d https://example.com/kodi.apk", "není na seznamu"),
    ("curl http://example.com", "není na seznamu"),
    (" am kill-all", "zakázané znaky"),
])
def test_zakazane_prikazy(cmd, why):
    with pytest.raises(PolicyError, match=why):
        check_adb_command(cmd, POL)


def test_instalace_jen_z_google_play():
    assert check_install_source("market://details?id=org.xbmc.kodi") == "org.xbmc.kodi"
    for bad in ["https://mirrors.kodi.tv/releases/android/kodi.apk", "file:///sdcard/kodi.apk",
                "market://details?id=org.xbmc.kodi&referrer=x"]:
        with pytest.raises(PolicyError):
            check_install_source(bad)


@pytest.mark.parametrize("domain,service,data,entity", [
    ("switch", "turn_on", {"entity_id": "switch.adguard_home_protection"}, "switch.adguard_home_protection"),
    ("switch", "turn_off", {"entity_id": "switch.adguard_home_protection"}, "switch.adguard_home_protection"),
    ("remote", "turn_on", {"entity_id": "remote.tv", "activity": "market://details?id=org.xbmc.kodi"}, "remote.tv"),
])
def test_povolene_sluzby(domain, service, data, entity):
    check_service(domain, service, data, entity)


@pytest.mark.parametrize("domain,service,data,entity", [
    ("homeassistant", "restart", {}, ""),
    ("switch", "toggle", {"entity_id": "switch.a"}, "switch.a"),
    ("switch", "turn_on", {"entity_id": "switch.jina"}, "switch.a"),                    # entity_id != krok
    ("switch", "turn_on", {"entity_id": "switch.a", "extra": 1}, "switch.a"),
    ("remote", "turn_on", {"entity_id": "remote.tv", "activity": "org.xbmc.kodi"}, "remote.tv"),
    ("remote", "turn_on", {"entity_id": "remote.tv", "activity": "https://example.com/x.apk"}, "remote.tv"),
    ("remote", "turn_on", {"entity_id": "remote.tv", "activity": "market://details?id=a.b;reboot"}, "remote.tv"),
    ("remote", "send_command", {"entity_id": "remote.tv"}, "remote.tv"),
    ("switch", "turn_on", {"entity_id": "remote.tv"}, "remote.tv"),                      # doména neodpovídá
])
def test_zakazane_sluzby(domain, service, data, entity):
    with pytest.raises(PolicyError):
        check_service(domain, service, data, entity)


def test_entita_jineho_zakaznika():
    with pytest.raises(PolicyError, match="nepatří"):
        check_entity(5, "media_player.cizi_tv", owned={"media_player.obyvak_tv"}, writable={"media_player.cizi_tv"})


def test_entita_bez_zapisu():
    with pytest.raises(NotWritable):
        check_entity(5, "media_player.obyvak_tv", owned={"media_player.obyvak_tv"}, writable=set())


def test_kontrola_portu_neznamy_vysledek_je_problem():
    assert check_ports({5555: False, 9090: False}) == []
    assert len(check_ports({5555: True, 9090: True})) == 2
    assert len(check_ports({})) == 2


def test_redakce_citlivych_udaju():
    rec = {"password": "tajne", "detail": {"kodi_password": "x", "token": "t"}, "cmd": "login password=abc",
           "list": [{"heslo": "1"}], "ip": "192.0.2.1", "ctl": "a\nb\x1bc"}
    r = redact(rec)
    assert r["password"] == "***" and r["detail"] == {"kodi_password": "***", "token": "***"}
    assert "abc" not in r["cmd"] and r["list"] == [{"heslo": "***"}] and r["ip"] == "192.0.2.1"
    assert r["ctl"] == "a?b?c"


@pytest.mark.parametrize("raw,expect", [
    ("Netflix", "Netflix"), ("  Disney+  ", "Disney+"), ("Česká televize", "Česká televize"),
    ("HBO\tMax", "HBOMax"),                  # řídicí znak odstraněn
    ("x" * 64, "x" * 64), ("x" * 65, None),
    ("Voyo; reboot", None), ("$(id)", None), ("`id`", None), ("a && b", None), ("a\nreboot", "areboot"),
    ("a|b", None), ("<script>", None), ("", None), (None, None), (123, None),
])
def test_sanitize_app_name(raw, expect):
    assert sanitize_app_name(raw) == expect
