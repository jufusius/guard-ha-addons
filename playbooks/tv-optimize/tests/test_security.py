import pytest

from tvopt.packages import build_protection
from tvopt.security import (NotWritable, PolicyError, check_adb_command, check_entity,
                            check_install_source, check_ports, redact)

P = build_protection()


@pytest.mark.parametrize("cmd", [
    "settings put global window_animation_scale 0.5",
    "settings delete global transition_animation_scale",
    "settings put global private_dns_specifier dns.adguard-dns.com",
    "settings put global private_dns_mode hostname",
    "settings put global adb_enabled 0",
    "pm disable-user --user 0 com.google.android.backdrop",
    "pm enable com.google.android.backdrop",
    "pm list packages -d",
    "pm trim-caches 2G",
    "am kill-all",
    "am start -a android.intent.action.VIEW -d market://details?id=org.xbmc.kodi",
])
def test_povolene_prikazy(cmd):
    assert check_adb_command(cmd, P) == cmd


@pytest.mark.parametrize("cmd", [
    "pm uninstall com.google.android.backdrop",
    "pm install /sdcard/x.apk",
    "pm disable-user --user 0 com.netflix.ninja",
    "pm disable-user --user 0 com.google.android.youtube.tv",
    "pm disable-user --user 0 org.xbmc.kodi",
    "settings put secure install_non_market_apps 1",
    "settings put global window_animation_scale 0.5; reboot",
    "settings put global window_animation_scale 0.5 && rm -rf /",
    "settings put global window_animation_scale $(id)",
    "reboot",
    "am start -a android.intent.action.VIEW -d https://example.com/kodi.apk",
    "curl http://example.com",
    "settings put global http_proxy 1.2.3.4:8080",
])
def test_zakazane_prikazy(cmd):
    with pytest.raises(PolicyError):
        check_adb_command(cmd, P)


def test_instalace_jen_z_google_play():
    assert check_install_source("market://details?id=org.xbmc.kodi") == "org.xbmc.kodi"
    for bad in ["https://mirrors.kodi.tv/releases/android/kodi.apk", "file:///sdcard/kodi.apk",
                "market://details?id=org.xbmc.kodi&referrer=x"]:
        with pytest.raises(PolicyError):
            check_install_source(bad)


def test_entita_jineho_zakaznika():
    with pytest.raises(PolicyError, match="nepatří"):
        check_entity(5, "media_player.cizi_tv", owned={"media_player.obyvak_tv"}, writable={"media_player.cizi_tv"})


def test_entita_bez_zapisu():
    with pytest.raises(NotWritable):
        check_entity(5, "media_player.obyvak_tv", owned={"media_player.obyvak_tv"}, writable=set())


def test_kontrola_portu():
    assert check_ports({5555: False, 9090: False}) == []
    assert len(check_ports({5555: True, 9090: True})) == 2


def test_redakce_citlivych_udaju():
    rec = {"password": "tajne", "detail": {"kodi_password": "x", "token": "t"}, "cmd": "login password=abc",
           "list": [{"heslo": "1"}], "ip": "192.0.2.1"}
    r = redact(rec)
    assert r["password"] == "***" and r["detail"] == {"kodi_password": "***", "token": "***"}
    assert "abc" not in r["cmd"] and r["list"] == [{"heslo": "***"}] and r["ip"] == "192.0.2.1"
