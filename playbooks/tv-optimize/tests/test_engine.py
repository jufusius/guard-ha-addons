import json

import pytest
from conftest import MITV_IP, FakeTime, FakeTv, mitv_context

from tvopt.engine import STATE_KEY, TvPlaybook

TV = "media_player.obyvak_tv"


FT = FakeTime()


def pb(guard, clock, cid=5, conv="conv-1"):
    return TvPlaybook(guard, cid, conversation_id=conv, clock=clock, sleep=FT.sleep, monotonic=FT.monotonic)


def tv(guard, cid=5, ent=TV):
    return guard.tvs[(cid, ent)]


def say_consent(guard, clock, r, text="ano", **kw):
    return pb(guard, clock).consent(r.plan_hash, guard.say(5, text, **kw))


def done(guard, clock, text="hotovo"):
    return pb(guard, clock).user_done(guard.say(5, text))


def state(guard, cid=5):
    return guard.store.get((cid, STATE_KEY)) or {}


def writes(t):
    return [c for c in t.log if c.startswith(("settings put", "settings delete", "pm disable", "pm enable"))]


# ---------- základní průběh ----------
def test_zrychleni_cely_prubeh_se_souhlasem(guard, clock):
    r = pb(guard, clock).plan(["speed"])
    assert r.status == "need_consent"
    assert f"Televize: MiTV-MOOQ0 ({MITV_IP})" in r.message
    assert "Zkrátím animace" in r.message and "Netflix" in r.message and "přesně „ano“" in r.message
    assert writes(tv(guard)) == []                      # před souhlasem nic

    d = say_consent(guard, clock, r)
    assert d.status == "done"
    t = tv(guard)
    assert t.settings["global/window_animation_scale"] == "0.5"
    assert "com.google.android.backdrop" in t.disabled
    for p in ("com.netflix.ninja", "com.google.android.youtube.tv", "net.cme.voyo.cz.tvapp", "com.miui.tv.analytics"):
        assert p not in t.disabled
    assert t.settings["global/adb_enabled"] == "0" and not t.adb_on
    assert "Zkrátil jsem animace" in d.message and "Odvolat autorizace ladění USB" in d.message
    assert state(guard)["adb_closed"] is True


def test_varovani_jde_adminovi_ne_zakaznikovi(guard, clock):
    r = pb(guard, clock).plan(["speed"])
    assert "navrhuji" not in r.message
    assert guard.admin and "navrhuji opravit" in guard.admin[0][2]["warning"]
    pb(guard, clock).plan(["speed"])
    assert len(guard.admin) == 1                         # jen jednou
    assert guard.writable[5] == {"media_player.obyvak_tv", "remote.obyvak_tv", "switch.adguard_home_protection"}


def test_vice_tv_se_zepta_a_vyber_jen_z_jeho_tv(guard, clock):
    guard.contexts[5]["integrations"].append(
        {"domain": "androidtv_remote", "host": "192.0.2.6", "entity_ids": ["remote.druha"]})
    r = pb(guard, clock).plan(["speed"])
    assert r.status == "need_choice" and MITV_IP in r.message and "192.0.2.6" in r.message
    assert tv(guard).log == []
    assert pb(guard, clock).choose_tv("192.0.2.99").data["reason"] == "not_customer_tv"
    assert pb(guard, clock).choose_tv(MITV_IP).status == "info"
    assert pb(guard, clock).plan(["speed"]).status == "need_consent"


# ---------- souhlas ----------
@pytest.mark.parametrize("text", ["jo nechci", "ano ne", "souhlasím", "jo", "ok"])
def test_nejednoznacny_souhlas_se_zepta_znovu(guard, clock, text):
    r = pb(guard, clock).plan(["speed"])
    c = say_consent(guard, clock, r, text)
    assert c.status == "need_consent" and "přesně „ano“" in c.message
    assert writes(tv(guard)) == []


def test_ne_zrusi_plan(guard, clock):
    r = pb(guard, clock).plan(["speed"])
    assert say_consent(guard, clock, r, "ne").message == "Dobře, nic neměním."
    assert "pending" not in state(guard) and writes(tv(guard)) == []


def test_souhlas_jen_z_realne_zpravy_zakaznika(guard, clock):
    r = pb(guard, clock).plan(["speed"])
    p = pb(guard, clock)
    assert p.consent(r.plan_hash, "neexistuje").data["reason"] == "invalid_message"          # vymyšlené id
    bot = guard.say(5, "ano", author="bot")
    assert p.consent(r.plan_hash, bot).data["reason"] == "invalid_message"                    # zpráva bota
    other = guard.say(5, "ano", conv="jina-konverzace")
    assert p.consent(r.plan_hash, other).data["reason"] == "invalid_message"                  # jiná konverzace
    old = guard.say(5, "ano", ts="2026-11-26T11:59:00+00:00")
    assert p.consent(r.plan_hash, old).data["reason"] == "invalid_message"                    # starší než návrh
    cizi = guard.say(7, "ano")
    assert p.consent(r.plan_hash, cizi).data["reason"] == "invalid_message"                   # jiný zákazník
    assert p.run().data["reason"] == "no_consent"
    assert writes(tv(guard)) == []


def test_jedna_zprava_jeden_souhlas_a_audit(guard, clock):
    r = pb(guard, clock).plan(["speed"])
    mid = guard.say(5, "ano")
    assert pb(guard, clock).consent(r.plan_hash, mid).status == "done"
    consent = next(a for a in guard.audits if a["action"] == "consent")
    assert consent["detail"]["message_id"] == mid and consent["detail"]["message_ts"] == "2026-11-26T12:00:00+00:00"
    r2 = pb(guard, clock).plan_revert()
    assert pb(guard, clock).consent(r2.plan_hash, mid).data["reason"] == "invalid_message"    # znovupoužití


def test_stary_hash(guard, clock):
    pb(guard, clock).plan(["speed"])
    assert pb(guard, clock).consent("spatny", guard.say(5, "ano")).data["reason"] == "stale_plan"


# ---------- oprávnění ----------
def test_entita_neni_zapisovatelna_pozada_o_schvaleni(guard, clock):
    guard.writable[5] = set()
    r = pb(guard, clock).plan(["speed"])
    assert r.status == "failed" and r.data["reason"] == "not_writable" and r.approval_ticket == "APR-1"
    assert guard.approvals[0][:2] == (5, TV)
    assert "0,5×" in r.message and guard.writable[5] == set() and tv(guard).log == []


def test_odebrane_opravneni_mezi_navrhem_a_provedenim(guard, clock):
    r = pb(guard, clock).plan(["speed"])
    guard.writable[5] = set()
    c = say_consent(guard, clock, r)
    assert c.data["reason"] == "not_writable"
    assert writes(tv(guard)) == []


def test_oddeleni_zakazniku(guard, clock):
    say_consent(guard, clock, pb(guard, clock).plan(["speed"]))
    assert tv(guard, 7, "media_player.cizi_tv").log == []
    assert all(a["cid"] == 5 for a in guard.audits) and all(s[0] == 5 for s in guard.services)


def test_config_flow_jen_se_schvalenim(guard, clock):
    guard.contexts[5] = mitv_context(with_adb=False)
    guard.writable[5] = set()                          # ani remote není zapisovatelný
    p = pb(guard, clock)
    assert p.plan(["speed"]).user_key == "enable_dev_mode"
    r = p.user_enabled_dev_mode(guard.say(5, "hotovo"))
    assert r.data["reason"] == "not_writable" and guard.flows == []
    assert guard.approvals[-1][1] == f"config_flow:androidtv:{MITV_IP}"
    guard.approval_states[r.approval_ticket] = "approved"
    r = p.user_enabled_dev_mode(guard.say(5, "hotovo"))
    assert r.user_key == "approve_adb"
    assert guard.flows == [(5, "androidtv", {"host": MITV_IP, "device_class": "androidtv", "port": 5555})]
    flow_audit = next(a for a in guard.audits if a["action"] == "start_config_flow_androidtv")
    assert flow_audit["detail"]["proof"] == {"ticket": "APR-1"}


def test_config_flow_s_doklad_iswritable(guard, clock):
    guard.contexts[5] = mitv_context(with_adb=False)
    p = pb(guard, clock)
    r = p.user_enabled_dev_mode(guard.say(5, "hotovo"))
    assert r.user_key == "approve_adb" and guard.flows[0][2]["host"] == MITV_IP
    assert p.user_approved_adb(guard.say(5, "hotovo")).user_key == "approve_adb"   # HA TV nevidí → znovu
    assert p.user_approved_adb(guard.say(5, "hotovo")).data["reason"] == "adb_unreachable"


def test_hotovo_musi_byt_presne(guard, clock):
    guard.contexts[5] = mitv_context(with_adb=False)
    r = pb(guard, clock).user_enabled_dev_mode(guard.say(5, "mám to"))
    assert r.status == "waiting_user" and guard.flows == []


def test_adb_se_nepripoji(guard, clock):
    tv(guard).adb_on = False
    r = pb(guard, clock).plan(["speed"])
    assert r.data["reason"] == "adb_unreachable" and "ručně" in r.message


def test_po_uklidu_je_treba_znovu_zapnout_ladeni(guard, clock):
    say_consent(guard, clock, pb(guard, clock).plan(["speed"]))
    r = pb(guard, clock).plan(["speed"])
    assert r.status == "need_user" and r.user_key == "reenable_adb"
    tv(guard).adb_on = True
    assert pb(guard, clock).user_reenabled_adb(guard.say(5, "hotovo")).status == "info"


def test_ladeni_nejde_zapnout(guard, clock):
    r = pb(guard, clock).adb_switch_blocked("speed")
    assert "Google účtu" in r.message and "0,5×" in r.message


# ---------- úklid ----------
def test_port_9090_otevreny_eskaluje_a_neni_done(guard, clock):
    guard.ports[5] = {9090: True}
    c = say_consent(guard, clock, pb(guard, clock).plan(["speed"]))
    assert c.status == "failed" and c.data["reason"] == "access_open" and c.user_key == "disable_adb_manual"
    assert any("otevřený" in a[1] for a in guard.admin)
    assert "Odvolat autorizace" in c.message
    guard.ports[5] = {}
    d = done(guard, clock)
    assert d.status == "done"


def test_neznamy_stav_portu_neni_zavreno(guard, clock):
    guard.port_unknown = True
    c = say_consent(guard, clock, pb(guard, clock).plan(["speed"]))
    assert c.data["reason"] == "access_open"


# ---------- vrácení ----------
def test_vraceni_zmen(guard, clock):
    say_consent(guard, clock, pb(guard, clock).plan(["speed"]))
    t = tv(guard)
    r = pb(guard, clock).plan_revert()
    assert r.status == "need_consent" and "Vrátím" in r.message and MITV_IP in r.message
    w = say_consent(guard, clock, r)
    assert w.status == "waiting_user" and w.user_key == "reenable_adb"
    t.adb_on = True
    t.settings["global/adb_enabled"] = "1"
    d = done(guard, clock)
    assert d.status == "done"
    assert t.settings["global/window_animation_scale"] == "1.0"
    assert "global/transition_animation_scale" not in t.settings
    assert "com.google.android.backdrop" not in t.disabled
    assert "com.google.android.play.games" in t.disabled           # vypnutá před námi → zůstává
    assert not t.adb_on
    assert pb(guard, clock).plan_revert().message.startswith("Na vaší TV jsem nic neměnil")


def test_selhani_vraceni_nic_nesmaze_a_jde_zopakovat(guard, clock):
    say_consent(guard, clock, pb(guard, clock).plan(["speed"]))
    t = tv(guard)
    changes_before = state(guard)["changes"]
    say_consent(guard, clock, pb(guard, clock).plan_revert())
    f = done(guard, clock)                                         # ladění pořád vypnuté → selže
    assert f.status == "failed" and f.data["reason"] == "revert_incomplete"
    assert "pm enable com.google.android.backdrop" in f.data["script"] and MITV_IP in f.data["script"]
    assert "zkusit znovu" in f.message and f.data["script"] not in f.message
    st = state(guard)
    assert st["changes"] == changes_before and st.get("snapshot") and st.get("pending")
    assert any("vrácení" in a[1] for a in guard.admin)
    t.adb_on = True
    assert pb(guard, clock).retry().status == "done"
    assert state(guard)["changes"] == []


# ---------- AdGuard ----------
def test_bez_adguard_home_jen_navrh_a_volba_verejneho_dns(guard, clock):
    r = pb(guard, clock).plan(["ads"])
    assert r.status == "need_dns_choice"
    assert "DNS dotazy půjdou přes AdGuard (třetí strana)" in r.message and "nepřipojí k internetu" in r.message
    assert tv(guard).log == []
    a = pb(guard, clock).public_dns_answer(guard.say(5, "ne"))
    assert "nezapnu" in a.message
    r = pb(guard, clock).plan(["ads"])
    assert r.status == "info" and "AdGuard Home" in r.message
    assert writes(tv(guard)) == []


def test_verejny_dns_po_vyslovnem_souhlasu(guard, clock):
    pb(guard, clock).plan(["ads"])
    assert pb(guard, clock).public_dns_answer(guard.say(5, "jo")).status == "need_dns_choice"
    pb(guard, clock).public_dns_answer(guard.say(5, "ano"))
    r = pb(guard, clock).plan(["ads"])
    assert r.status == "need_consent" and "třetí stranu" in r.message
    assert say_consent(guard, clock, r).status == "done"
    t = tv(guard)
    assert t.settings["global/private_dns_specifier"] == "dns.adguard-dns.com"
    assert t.settings["global/private_dns_mode"] == "hostname"
    assert any(a["action"] == "public_dns_choice" and a["detail"]["message_id"] for a in guard.audits)


def test_verejny_dns_jen_kdyz_dot_funguje(guard, clock):
    pb(guard, clock).plan(["ads"])
    pb(guard, clock).public_dns_answer(guard.say(5, "ano"))
    guard.dot_ok = False
    r = pb(guard, clock).plan(["ads"])
    assert "nezapnu" in r.message and "private_dns" not in json.dumps(writes(tv(guard)))


def test_dot_vypadne_mezi_navrhem_a_provedenim(guard, clock):
    pb(guard, clock).plan(["ads"])
    pb(guard, clock).public_dns_answer(guard.say(5, "ano"))
    r = pb(guard, clock).plan(["ads"])
    guard.dot_ok = False
    d = say_consent(guard, clock, r)
    assert d.status == "done" and "nezapnu" in d.message
    assert "global/private_dns_mode" not in tv(guard).settings


def test_lokalni_adguard_home_bez_adb(guard, clock):
    guard.contexts[5] = mitv_context(adguard="off")
    r = pb(guard, clock).plan(["ads"])
    assert r.status == "need_consent" and "AdGuard Home" in r.message and "router" in r.message
    assert say_consent(guard, clock, r).status == "done"
    assert guard.services == [(5, "switch", "turn_on", {"entity_id": "switch.adguard_home_protection"})]
    assert tv(guard).log == []                                    # na TV se nesahalo


def test_vlastni_dns_zakaznika_se_neprepisuje(guard, clock):
    tv(guard).settings.update({"global/private_dns_mode": "hostname", "global/private_dns_specifier": "dns.example"})
    pb(guard, clock).plan(["ads"])
    pb(guard, clock).public_dns_answer(guard.say(5, "ano"))
    r = pb(guard, clock).plan(["ads"])
    assert "dns.example" in r.message
    assert tv(guard).settings["global/private_dns_specifier"] == "dns.example"
    assert not tv(guard).adb_on


# ---------- PatchWall ----------
def _patchwall(guard, home):
    guard.approved[5] = {"com.mitv.tvhome.atv"}
    t = tv(guard)
    t.installed |= {"com.mitv.tvhome.atv", "com.spocky.projengmenu"}
    t.home = home
    guard.contexts[5] = mitv_context(adguard="on")
    return t


def test_patchwall_overeni_home_ok(guard, clock):
    t = _patchwall(guard, "com.spocky.projengmenu")
    d = say_consent(guard, clock, pb(guard, clock).plan(["ads"]))
    assert d.status == "done" and "com.mitv.tvhome.atv" in t.disabled


def test_patchwall_home_nefunguje_okamzite_zapnout(guard, clock):
    t = _patchwall(guard, "com.mitv.tvhome.atv")
    d = say_consent(guard, clock, pb(guard, clock).plan(["ads"]))
    assert "com.mitv.tvhome.atv" not in t.disabled
    assert t.log.index("pm enable com.mitv.tvhome.atv") > t.log.index("pm disable-user --user 0 com.mitv.tvhome.atv")
    assert "PatchWall" in d.message
    assert "Vypnul jsem aplikaci „PatchWall“" not in d.message
    assert not any(c["target"] == "com.mitv.tvhome.atv" for c in state(guard)["changes"])


# ---------- vstup zákazníka ----------
EVIL = ["; reboot", "$(id)", "`id`", "a && b", "Voyo\n; reboot", "x" * 65, "a|b"]


def test_nazvy_aplikaci_od_zakaznika_se_ocisti(guard, clock):
    res = pb(guard, clock).add_customer_apps(EVIL + ["Oneplay", "Moje IPTV"])
    assert res["added"] == ["Oneplay", "Moje IPTV"] and res["rejected"] == len(EVIL)
    assert all(e not in res["added"] for e in EVIL)
    say_consent(guard, clock, pb(guard, clock).plan(["speed"]))
    log = "\n".join(tv(guard).log)
    dump = json.dumps(guard.audits, ensure_ascii=False)
    for bad in ("reboot", "$(", "`", "&&", "|", "x" * 65):
        assert bad not in log
        assert bad not in dump
    assert "\\n;" not in dump


def test_limit_poctu_aplikaci(guard, clock):
    res = pb(guard, clock).add_customer_apps([f"App {i}" for i in range(80)])
    assert len(res["added"]) == 50 and len(state(guard)["customer_apps"]) == 50


def test_zakaznikova_aplikace_je_chranena(guard, clock):
    pb(guard, clock).add_customer_apps(["com.google.android.backdrop"])
    say_consent(guard, clock, pb(guard, clock).plan(["speed"]))
    assert "com.google.android.backdrop" not in tv(guard).disabled


# ---------- Kodi ----------
def test_kodi_pres_remote_a_kroky_zakaznika(guard, clock):
    r = pb(guard, clock).plan(["kodi"])
    w = say_consent(guard, clock, r)
    assert guard.services[0] == (5, "remote", "turn_on",
                                 {"entity_id": "remote.obyvak_tv", "activity": "market://details?id=org.xbmc.kodi"})
    assert w.user_key == "install_kodi"
    w = done(guard, clock)
    assert w.user_key == "kodi_http" and "vypnutou" in w.message and "ani mně" in w.message
    w = done(guard, clock)
    assert w.user_key == "kodi_ha" and MITV_IP in w.message and "SMAŽTE" in w.message and "9090" in w.message
    ctx = guard.contexts[5]
    ctx["integrations"].append({"domain": "kodi", "host": MITV_IP, "entity_ids": ["media_player.kodi"]})
    ctx["states"].append({"entity_id": "media_player.kodi", "state": "off", "attributes": {}})
    r = done(guard, clock)
    assert r.data["reason"] == "kodi_not_ready" and r.status == "waiting_user"
    ctx["states"][-1]["state"] = "idle"
    assert done(guard, clock).status == "done"
    dump = json.dumps(guard.audits) + json.dumps({str(k): v for k, v in guard.store.items()})
    assert "password" not in dump.lower()


def test_kodi_bez_pristupu_rucne(guard, clock):
    guard.contexts[5] = mitv_context(with_adb=False, with_remote=False)
    w = say_consent(guard, clock, pb(guard, clock).plan(["kodi"]))
    assert w.user_key == "install_kodi_manual" and guard.services == [] and guard.flows == []


def test_texty_approve_adb_nevybizi_k_vzdy_povolit(guard, clock):
    from tvopt import texts
    t = texts.steps_text("approve_adb")
    assert "nezaškrtávejte" in t and "Zaškrtněte" not in t


# ---------- ostatní ----------
def test_tizen_nic_nezkousi(guard, clock):
    guard.contexts[5] = {"devices": [{"ip": "192.0.2.30", "hostname": "Samsung-TV", "vendor": "Samsung"}],
                         "states": [], "integrations": [{"domain": "samsungtv", "host": "192.0.2.30"}]}
    r = pb(guard, clock).plan(["speed", "kodi"])
    assert r.status == "unsupported" and "Kodi — na Tizenu není" in r.message
    assert guard.flows == [] and guard.services == [] and guard.approvals == []


def test_audit_zaznam(guard, clock):
    say_consent(guard, clock, pb(guard, clock).plan(["speed"]))
    adb = [a for a in guard.audits if a["action"] == "adb"]
    assert {"ts", "cid", "actor", "conversation_id", "device", "module", "command", "result"} <= adb[0].keys()
    assert adb[0]["actor"] == "guard-bot" and adb[0]["conversation_id"] == "conv-1"
    assert any(a["action"] == "port_check" for a in guard.audits)


def test_uz_vse_nastaveno(guard, clock):
    t = tv(guard)
    t.settings.update({f"global/{k}": "0.5" for k in
                       ("window_animation_scale", "transition_animation_scale", "animator_duration_scale")})
    t.disabled |= {"com.google.android.backdrop", "com.android.printspooler", "com.google.android.feedback"}
    p = pb(guard, clock)
    p.models["xiaomi-mitv"]["speed"]["maintenance"] = []
    r = p.plan(["speed"])
    assert r.status == "info" and "není co měnit" in r.message and not t.adb_on


def test_stav(guard, clock):
    assert "nemám" in pb(guard, clock).status().message
    say_consent(guard, clock, pb(guard, clock).plan(["speed"]))
    assert "window_animation_scale = 0.5" in pb(guard, clock).status().message


def test_bez_conversation_id_nejde(guard):
    with pytest.raises(ValueError):
        TvPlaybook(guard, 5, conversation_id="")
