"""Pozdější úpravy: zákazník přidá/odebere používanou aplikaci, zapne zpátky jednu vypnutou,
nebo nechá vypnout další (jen ověřenou)."""
import pytest
from conftest import FakeTime

from tvopt.engine import STATE_KEY, TvPlaybook
from tvopt.intents import detect_app_action

FT = FakeTime()


def pb(guard, clock):
    return TvPlaybook(guard, 5, conversation_id="conv-1", clock=clock, sleep=FT.sleep, monotonic=FT.monotonic)


def ano(guard, clock, r, text="ano"):
    return pb(guard, clock).consent(r.plan_hash, guard.say(5, text))


def tv(guard):
    return guard.tvs[(5, "media_player.obyvak_tv")]


def st(guard):
    return guard.store[(5, STATE_KEY)]


def _apply_speed(guard, clock):
    assert ano(guard, clock, pb(guard, clock).plan(["speed"])).status == "done"
    tv(guard).adb_on = True          # pro další akci zákazník znovu zapnul ladění
    s = st(guard)
    s.pop("adb_closed", None)
    guard.store[(5, STATE_KEY)] = s


@pytest.mark.parametrize("text,action", [
    ("zapni zpátky Spořič obrazovky", "enable"),
    ("chci zpátky Tisk", "enable"),
    ("Spotify už nepoužívám", "unprotect"),
    ("vypni ještě Google Play Hry", "disable"),
    ("používám i Voyo", "protect"),
    ("nevypínej Spotify", "protect"),
    ("jaké aplikace máš chráněné?", "list"),
    ("nechci tam aplikaci WallPaper", "disable"),
    ("dobrý den", None),
    ("vrať to zpátky", None),          # celé vrácení řeší detect_intents → revert
])
def test_rozpoznani_akce(text, action):
    assert detect_app_action(text) == action


def test_pridat_a_odebrat_pouzivanou_aplikaci(guard, clock):
    p = pb(guard, clock)
    assert p.add_customer_apps(["Voyo", "Moje IPTV"])["added"] == ["Voyo", "Moje IPTV"]
    r = p.remove_customer_apps(["moje iptv", "Netflix", "; reboot"])
    assert r == {"removed": ["Moje IPTV"], "not_found": 2}      # výchozí Netflix odebrat nejde
    assert st(guard)["customer_apps"] == ["Voyo"]
    lst = p.list_apps()
    assert "Voyo" in lst.message and "Netflix" in lst.message and "Moje IPTV" not in lst.message


def test_zapnout_zpatky_jen_jednu_aplikaci(guard, clock):
    _apply_speed(guard, clock)
    t = tv(guard)
    assert {"com.google.android.backdrop", "com.android.printspooler"} <= t.disabled
    r = pb(guard, clock).plan_enable_apps(["Spořič obrazovky Backdrop"])
    assert r.status == "need_consent" and "com.google.android.backdrop" in r.message
    assert ano(guard, clock, r).status == "done"
    assert "com.google.android.backdrop" not in t.disabled
    assert "com.android.printspooler" in t.disabled                     # ostatní zůstává
    assert t.settings["global/window_animation_scale"] == "0.5"         # animace zůstávají
    s = st(guard)
    assert all(c["target"] != "com.google.android.backdrop" for c in s["changes"])
    assert s.get("snapshot")                                            # zbylé změny jdou vrátit
    assert "com.google.android.backdrop" in s["customer_apps"]          # příště ji nevypne


def test_zapnout_zpatky_aplikaci_kterou_jsem_nevypnul(guard, clock):
    _apply_speed(guard, clock)
    r = pb(guard, clock).plan_enable_apps(["Netflix"])
    assert r.status == "info" and "nevypínal" in r.message
    assert pb(guard, clock).plan_enable_apps(["$(id)"]).status == "info"


def test_vypnout_dalsi_overenou_aplikaci(guard, clock):
    t = tv(guard)
    t.installed.add("com.google.android.play.games")
    t.disabled.discard("com.google.android.play.games")
    r = pb(guard, clock).plan_disable_apps(["Google Play Hry"])
    assert r.status == "need_consent" and "Google Play Hry" in r.message
    assert writes_none(t)
    assert ano(guard, clock, r).status == "done"
    assert "com.google.android.play.games" in t.disabled
    assert t.settings.get("global/window_animation_scale") == "1.0"     # nic jiného se nezměnilo


def writes_none(t):
    return not [c for c in t.log if c.startswith(("settings put", "pm disable", "pm enable"))]


@pytest.mark.parametrize("name,reason", [
    ("Netflix", "not_candidate"),              # chráněná, není kandidát
    ("Xiaomi analytika", "not_candidate"),     # neověřený kandidát Xiaomi
    ("Neexistující appka", "not_candidate"),
])
def test_vypnout_nejde_mimo_overene_kandidaty(guard, clock, name, reason):
    r = pb(guard, clock).plan_disable_apps([name])
    assert r.data["reason"] == reason and writes_none(tv(guard))


def test_vypnout_pouzivanou_az_po_odebrani(guard, clock):
    t = tv(guard)
    t.installed.add("com.google.android.play.games")
    t.disabled.discard("com.google.android.play.games")
    p = pb(guard, clock)
    p.add_customer_apps(["com.google.android.play.games"])
    assert p.plan_disable_apps(["Google Play Hry"]).data["reason"] == "protected"
    p.remove_customer_apps(["com.google.android.play.games"])
    assert p.plan_disable_apps(["Google Play Hry"]).status == "need_consent"
