import json

import pytest
from conftest import MITV_IP, FakeTv, mitv_context

from tvopt.engine import STATE_KEY, TvPlaybook

TV = ("media_player.obyvak_tv")


def pb(guard, clock, cid=5):
    return TvPlaybook(guard, cid, conversation_id="conv-1", clock=clock)


def tv(guard, cid=5, ent=TV):
    return guard.tvs[(cid, ent)]


def test_zrychleni_cely_prubeh_se_souhlasem(guard, clock):
    p = pb(guard, clock)
    r = p.plan(["speed"])
    assert r.status == "need_consent"
    assert "Zkrátím animace" in r.message and "Netflix" in r.message and "vrať to zpátky" in r.message
    # před souhlasem se nic nezměnilo
    assert tv(guard).settings == {"global/window_animation_scale": "1.0"}
    assert not [c for c in tv(guard).log if c.startswith(("settings put", "pm disable"))]

    done = p.consent(r.plan_hash)
    assert done.status == "done"
    t = tv(guard)
    assert t.settings["global/window_animation_scale"] == "0.5"
    assert "com.google.android.backdrop" in t.disabled
    assert "com.netflix.ninja" not in t.disabled and "com.google.android.youtube.tv" not in t.disabled
    assert "com.miui.tv.analytics" not in t.disabled          # neověřený kandidát
    # úklid: ladění vypnuté, port 5555 zavřený
    assert t.settings["global/adb_enabled"] == "0" and not t.adb_on
    assert "vrať to zpátky" in done.message and "Zkrátil jsem animace" in done.message
    assert not guard.store[(5, STATE_KEY)].get("port_problems")


def test_bez_souhlasu_se_nic_neprovede(guard, clock):
    p = pb(guard, clock)
    p.plan(["speed"])
    assert p.run().data["reason"] == "no_consent"
    assert p.consent("spatny-hash").data["reason"] == "stale_plan"
    assert tv(guard).settings["global/window_animation_scale"] == "1.0"


def test_odmitnuti(guard, clock):
    p = pb(guard, clock)
    p.plan(["speed"])
    assert p.decline().message == "Dobře, nic neměním."
    assert "pending" not in guard.store[(5, STATE_KEY)]


def test_zakaznikova_aplikace_je_chranena(guard, clock):
    profile_cand = {"package": "com.google.android.backdrop", "label": "Backdrop"}
    p = pb(guard, clock)
    p.add_customer_apps(["com.google.android.backdrop", "Voyo"])
    r = p.plan(["speed"])
    assert "Voyo" in r.message
    p.consent(r.plan_hash)
    assert profile_cand["package"] not in tv(guard).disabled


def test_vraceni_zmen(guard, clock):
    p = pb(guard, clock)
    p.consent(p.plan(["speed", "ads"]).plan_hash)
    t = tv(guard)
    assert t.settings["global/private_dns_mode"] == "hostname"
    assert "com.google.android.play.games" in t.disabled  # byla vypnutá už předtím

    r = pb(guard, clock).plan_revert()
    assert r.status == "need_consent"
    assert "Vrátím" in r.message
    # ADB je po úklidu vypnuté → první je krok zákazníka
    w = pb(guard, clock).consent(r.plan_hash)
    assert w.status == "waiting_user" and w.user_key == "reenable_adb"
    t.adb_on = True                       # zákazník zapnul ladění
    t.settings["global/adb_enabled"] = "1"
    done = pb(guard, clock).user_done()
    assert done.status == "done"
    assert t.settings["global/window_animation_scale"] == "1.0"
    assert "global/transition_animation_scale" not in t.settings   # předtím neexistovalo → delete
    assert "global/private_dns_mode" not in t.settings
    assert "com.google.android.backdrop" not in t.disabled
    assert "com.google.android.play.games" in t.disabled            # vypnutá před námi → zůstává
    assert not t.adb_on                                             # po vrácení zase zavřeno
    assert pb(guard, clock).plan_revert().message.startswith("Na vaší TV jsem nic neměnil")


def test_entita_neni_zapisovatelna_pozada_o_schvaleni(guard, clock):
    guard.writable[5] = set()
    r = pb(guard, clock).plan(["speed"])
    assert r.status == "failed" and r.data["reason"] == "not_writable"
    assert r.approval_ticket == "APR-1"
    assert guard.approvals == [(5, TV, "tv-optimize/speed: ADB příkazy na TV")]
    assert "ručně" in r.message and "0,5×" in r.message
    assert guard.writable[5] == set()       # engine si oprávnění nepřidal
    assert tv(guard).log == []


def test_oddeleni_zakazniku(guard, clock):
    p = pb(guard, clock, cid=5)
    p.consent(p.plan(["speed"]).plan_hash)
    cizi = tv(guard, 7, "media_player.cizi_tv")
    assert cizi.log == []
    assert all(a["cid"] == 5 for a in guard.audits)
    assert all(s[0] == 5 for s in guard.services)


def test_adb_se_nepripoji(guard, clock):
    tv(guard).adb_on = False
    r = pb(guard, clock).plan(["speed"])
    assert r.status == "failed" and r.data["reason"] == "adb_unreachable"
    assert "ručně" in r.message


def test_chybi_adb_integrace_navede_zakaznika(guard, clock):
    guard.contexts[5] = mitv_context(with_adb=False)
    p = pb(guard, clock)
    r = p.plan(["speed"])
    assert r.status == "need_user" and r.user_key == "enable_dev_mode"
    assert "7×" in r.message
    r = p.user_enabled_dev_mode()
    assert r.user_key == "approve_adb"
    assert guard.flows == [(5, "androidtv", {"host": MITV_IP, "device_class": "androidtv", "port": 5555})]
    # HA TV pořád nevidí → zkusí znovu, pak ruční postup
    assert p.user_approved_adb().user_key == "approve_adb"
    assert p.user_approved_adb().data["reason"] == "adb_unreachable"


def test_ladeni_nejde_zapnout(guard, clock):
    r = pb(guard, clock).adb_switch_blocked("speed")
    assert "Google účtu" in r.message and "0,5×" in r.message


def test_kodi_pres_remote_a_kroky_zakaznika(guard, clock):
    p = pb(guard, clock)
    r = p.plan(["kodi"])
    assert r.status == "need_consent"
    w = p.consent(r.plan_hash)
    assert guard.services[0] == (5, "remote", "turn_on",
                                 {"entity_id": "remote.obyvak_tv", "activity": "market://details?id=org.xbmc.kodi"})
    assert w.user_key == "install_kodi"
    w = p.user_done()
    assert w.user_key == "kodi_http"
    assert "vypnutou" in w.message and "ani mně" in w.message
    w = p.user_done()
    assert w.user_key == "kodi_ha" and MITV_IP in w.message and "WebSocket" in w.message
    # zákazník přidal integraci, ale Kodi neběží
    ctx = guard.contexts[5]
    ctx["integrations"].append({"domain": "kodi", "host": MITV_IP, "entity_ids": ["media_player.kodi"]})
    ctx["states"].append({"entity_id": "media_player.kodi", "state": "off", "attributes": {}})
    r = p.user_done()
    assert r.data["reason"] == "kodi_not_ready"
    ctx["states"][-1]["state"] = "idle"
    done = p.run()
    assert done.status == "done"
    # žádné heslo nikde v auditu ani ve stavu
    dump = json.dumps(guard.audits) + json.dumps({str(k): v for k, v in guard.store.items()})
    assert "password" not in dump.lower()


def test_kodi_bez_pristupu_rucne(guard, clock):
    guard.contexts[5] = mitv_context(with_adb=False, with_remote=False)
    r = pb(guard, clock).plan(["kodi"])
    assert r.status == "need_consent"
    w = pb(guard, clock).consent(r.plan_hash)
    assert w.user_key == "install_kodi_manual" and "Google Play" in w.message
    assert guard.services == [] and guard.flows == []


def test_port_9090_otevreny_se_nahlasi(guard, clock):
    guard.ports[5] = {9090: True}
    p = pb(guard, clock)
    done = p.consent(p.plan(["speed"]).plan_hash)
    assert "9090" in done.message


def test_tizen_nic_nezkousi(guard, clock):
    guard.contexts[5] = {"devices": [{"ip": "192.0.2.30", "hostname": "Samsung-TV", "vendor": "Samsung"}],
                         "states": [], "integrations": [{"domain": "samsungtv", "host": "192.0.2.30"}]}
    r = pb(guard, clock).plan(["speed", "kodi"])
    assert r.status == "unsupported" and "Kodi — na Tizenu není" in r.message
    assert guard.flows == [] and guard.services == [] and guard.approvals == []


def test_audit_zaznam(guard, clock):
    p = pb(guard, clock)
    p.consent(p.plan(["speed"]).plan_hash)
    adb = [a for a in guard.audits if a["action"] == "adb"]
    assert adb and {"ts", "cid", "actor", "conversation_id", "device", "module", "command", "result"} <= adb[0].keys()
    assert adb[0]["ts"] == "2026-11-26T12:00:00+00:00" and adb[0]["actor"] == "guard-bot"
    assert any(a["action"] == "consent" for a in guard.audits)
    assert any(a["action"] == "port_check" for a in guard.audits)


def test_uz_vse_nastaveno(guard, clock):
    t = tv(guard)
    t.settings.update({f"global/{k}": "0.5" for k in
                       ("window_animation_scale", "transition_animation_scale", "animator_duration_scale")})
    t.disabled |= {"com.google.android.backdrop", "com.android.printspooler", "com.google.android.feedback"}
    profile = pb(guard, clock).models["xiaomi-mitv"]
    profile["speed"]["maintenance"] = []
    r = TvPlaybook(guard, 5, models={"xiaomi-mitv": profile, **{k: v for k, v in pb(guard, clock).models.items()
                                                               if k != "xiaomi-mitv"}}, clock=clock).plan(["speed"])
    assert r.status == "info" and "není co měnit" in r.message


def test_stav(guard, clock):
    p = pb(guard, clock)
    assert "nemám" in p.status().message
    p.consent(p.plan(["speed"]).plan_hash)
    assert "window_animation_scale = 0.5" in pb(guard, clock).status().message


def test_vlastni_dns_zakaznika_se_neprepisuje(guard, clock):
    tv(guard).settings.update({"global/private_dns_mode": "hostname", "global/private_dns_specifier": "dns.example"})
    r = pb(guard, clock).plan(["ads"])
    assert "dns.example" in r.message and "AdGuard DNS" not in r.message
    assert tv(guard).settings["global/private_dns_specifier"] == "dns.example"
    assert not tv(guard).adb_on           # ani bez změn nezůstane ladění zapnuté
