from datetime import datetime, timedelta, timezone

import pytest
from conftest import FakeGuard, FakeTime

from tvopt.engine import STATE_KEY, TvPlaybook
from tvopt.packages import build_protection
from tvopt.ratelimit import LIMITS, RATE_KEY, RateLimited, hit
from tvopt.security import CONNECTIVITY_CHECK_CMD, CommandPolicy, PolicyError, check_adb_command


# ---------- rate limity ----------
def test_limit_v_okne_a_uvolneni_po_minute():
    g = FakeGuard()
    for i in range(30):
        hit(g, 5, "adb", 1000.0 + i)
    with pytest.raises(RateLimited) as e:
        hit(g, 5, "adb", 1030.0)
    assert e.value.kind == "adb" and 1 <= e.value.retry_after <= 61
    hit(g, 5, "adb", 1061.0)                    # nejstarší vypadl z okna


def test_limit_je_na_zakaznika():
    g = FakeGuard()
    for i in range(30):
        hit(g, 5, "adb", 1000.0)
    hit(g, 7, "adb", 1000.0)                    # jiný cid má vlastní limit
    with pytest.raises(RateLimited):
        hit(g, 5, "adb", 1000.0)


def test_odmitnute_volani_se_nezapocita():
    g = FakeGuard()
    for _ in range(3):
        hit(g, 5, "config_flow", 0.0)
    for _ in range(5):
        with pytest.raises(RateLimited):
            hit(g, 5, "config_flow", 10.0)
    assert len(g.store[(5, RATE_KEY)]["config_flow"]) == 3


def test_limity_odpovidaji_dokumentaci():
    assert LIMITS == {"adb": (30, 60), "service": (10, 60), "config_flow": (3, 3600), "probe": (10, 60)}


def test_engine_limit_napric_instancemi_a_plan_zustane(guard, clock):
    now = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)
    ft = FakeTime()
    mk = lambda: TvPlaybook(guard, 5, "conv-1", clock=lambda: now, sleep=ft.sleep, monotonic=ft.monotonic)
    guard.store[(5, RATE_KEY)] = {"adb": [now.timestamp()] * 28}       # jiná konverzace už skoro vyčerpala
    r = mk().plan(["speed"])
    assert r.status == "failed" and r.data["reason"] == "rate_limited" and "zkusit znovu" in r.message
    assert any(a["action"] == "rate_limited" for a in guard.audits)
    later = now + timedelta(seconds=61)
    p = TvPlaybook(guard, 5, "conv-1", clock=lambda: later, sleep=ft.sleep, monotonic=ft.monotonic)
    assert p.plan(["speed"]).status == "need_consent"


def test_limit_pri_provadeni_neztrati_plan(guard, clock):
    ft = FakeTime()
    p = TvPlaybook(guard, 5, "conv-1", clock=clock, sleep=ft.sleep, monotonic=ft.monotonic)
    r = p.plan(["speed"])
    guard.store[(5, RATE_KEY)]["adb"] = [clock().timestamp()] * 30
    c = p.consent(r.plan_hash, guard.say(5, "ano"))
    assert c.data["reason"] == "rate_limited"
    st = guard.store[(5, STATE_KEY)]
    assert st["pending"]["consented"] is True
    guard.store[(5, RATE_KEY)]["adb"] = []
    assert p.retry().status == "done"


# ---------- test připojení z TV po změně DNS ----------
def test_jediny_povoleny_ping():
    pol = CommandPolicy(protection=build_protection())
    assert check_adb_command(CONNECTIVITY_CHECK_CMD, pol) == CONNECTIVITY_CHECK_CMD
    for bad in ["ping -c 1 -W 3 example.com", "ping -c 100 connectivitycheck.gstatic.com",
                "ping 192.0.2.1", "ping -f connectivitycheck.gstatic.com", "cmd connectivity airplane-mode enable"]:
        with pytest.raises(PolicyError):
            check_adb_command(bad, pol)


def _public_dns_plan(guard, clock, ft):
    mk = lambda: TvPlaybook(guard, 5, "conv-1", clock=clock, sleep=ft.sleep, monotonic=ft.monotonic)
    mk().plan(["ads"])
    mk().public_dns_answer(guard.say(5, "ano"))
    r = mk().plan(["ads"])
    assert "30 s" in r.message
    return mk, r


def test_dns_zustane_kdyz_tv_jde_na_internet(guard, clock):
    ft = FakeTime()
    t = guard.tvs[(5, "media_player.obyvak_tv")]
    t.online_after = 3                                   # projde na 4. pokus
    mk, r = _public_dns_plan(guard, clock, ft)
    d = mk().consent(r.plan_hash, guard.say(5, "ano"))
    assert d.status == "done"
    assert t.settings["global/private_dns_mode"] == "hostname"
    assert t.log.count(CONNECTIVITY_CHECK_CMD) == 4 and ft.sleeps == [2, 2, 2]


def test_dns_se_vrati_kdyz_tv_nejde_na_internet_do_30_s(guard, clock):
    ft = FakeTime()
    t = guard.tvs[(5, "media_player.obyvak_tv")]
    t.settings["global/private_dns_mode"] = "opportunistic"
    t.online = False
    mk, r = _public_dns_plan(guard, clock, ft)
    d = mk().consent(r.plan_hash, guard.say(5, "ano"))
    assert d.status == "done"
    assert "vrátil původní nastavení" in d.message
    assert t.settings["global/private_dns_mode"] == "opportunistic"      # původní hodnota
    assert "global/private_dns_specifier" not in t.settings                # předtím neexistovalo
    assert ft.t <= 30 and t.log.count(CONNECTIVITY_CHECK_CMD) >= 2
    i_ping = max(i for i, c in enumerate(t.log) if c == CONNECTIVITY_CHECK_CMD)
    assert t.log[i_ping + 1].startswith("settings ") and "private_dns" in t.log[i_ping + 1]   # vrácení hned
    st = guard.store[(5, STATE_KEY)]
    assert not any("private_dns" in c["target"] for c in st["changes"])
    assert any(a["action"] == "verify_net" and a["result"] == "rollback" for a in guard.audits)
    assert "Zapnul jsem na TV veřejný AdGuard DNS" not in d.message


# ---------- úklid bez plánu: „hotovo“ stejně přísně ----------
def _cleanup_only(guard, clock, ft):
    t = guard.tvs[(5, "media_player.obyvak_tv")]
    t.settings.update({f"global/{k}": "0.5" for k in
                       ("window_animation_scale", "transition_animation_scale", "animator_duration_scale")})
    t.disabled |= {"com.google.android.backdrop", "com.android.printspooler", "com.google.android.feedback"}
    guard.ports[5] = {9090: True}
    p = TvPlaybook(guard, 5, "conv-1", clock=clock, sleep=ft.sleep, monotonic=ft.monotonic)
    p.models["xiaomi-mitv"]["speed"]["maintenance"] = []
    r = p.plan(["speed"])
    assert r.status == "failed" and r.data["reason"] == "access_open" and r.user_key == "disable_adb_manual"
    return p


@pytest.mark.parametrize("text", ["mám to", "jo", "hotovo zítra"])
def test_uklid_bez_planu_nepresne_hotovo(guard, clock, text):
    ft = FakeTime()
    p = _cleanup_only(guard, clock, ft)
    guard.ports[5] = {}
    r = p.user_done(guard.say(5, text))
    assert r.status == "waiting_user" and "hotovo" in r.message
    assert guard.store[(5, STATE_KEY)]["pending"]["waiting"] == "disable_adb_manual"


def test_uklid_bez_planu_jen_realna_zprava(guard, clock):
    ft = FakeTime()
    p = _cleanup_only(guard, clock, ft)
    guard.ports[5] = {}
    assert p.user_done("vymyslene-id").data["reason"] == "invalid_message"
    assert p.user_done(guard.say(5, "hotovo", author="bot")).data["reason"] == "invalid_message"
    d = p.user_done(guard.say(5, "hotovo"))
    assert d.status == "done" and "zavřený" in d.message
    assert "pending" not in guard.store[(5, STATE_KEY)]


def test_uklid_bez_planu_porty_porad_otevrene(guard, clock):
    ft = FakeTime()
    p = _cleanup_only(guard, clock, ft)
    r = p.user_done(guard.say(5, "hotovo"))
    assert r.data["reason"] == "access_open"
    assert guard.store[(5, STATE_KEY)]["pending"]["waiting"] == "disable_adb_manual"
