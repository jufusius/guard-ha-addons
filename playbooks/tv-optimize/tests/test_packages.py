import pytest

from tvopt.config import load_models, load_protected
from tvopt.packages import build_protection, disable_candidates, resolve_app_name, select_packages

MODELS = load_models()
MITV = MODELS["xiaomi-mitv"]

# Přesné balíčky ze zadání vlastníka (a SmartTube ověřený v github.com/yuliskov/SmartTube).
REQUIRED = {
    "Oneplay": ["net.cme.voyo.cz", "net.cme.voyo.cz.tvapp"],
    "Netflix": ["com.netflix.ninja"],
    "HBO Max": ["com.wbd.stream"],
    "Disney+": ["com.disney.disneyplus"],
    "Prime Video": ["com.amazon.amazonvideo.livingroom"],
    "YouTube": ["com.google.android.youtube.tv"],
    "Kodi": ["org.xbmc.kodi"],
    "Projectivy Launcher": ["com.spocky.projengmenu"],
    "SmartTube": ["com.teamsmart.videomanager.tv", "com.liskovsoft.smarttubetv.beta",
                  "org.smarttube.stable", "org.smarttube.beta"],
}
XIAOMI_SYSTEM = ["mitv.service", "com.mitv.overseaservice", "com.mi.bluetooth", "com.mitv.setup",
                 "com.mitv.livetv", "com.mediatek.tv.anything", "com.mediatek.wwtv.tvcenter",
                 "com.xiaomi.android.tvsetup.partnercustomizer", "com.xiaomi.mitv.updateservice",
                 "com.mitv.upgrader", "com.mediatek.tv.systemupdater"]


@pytest.mark.parametrize("name,pkgs", REQUIRED.items())
def test_presne_balicky_chranenych_aplikaci(name, pkgs):
    app = next(a for a in load_protected()["apps"] if a["name"] == name)
    assert set(pkgs) <= set(app["packages"])
    prot = build_protection()
    assert all(prot.protects(p) for p in pkgs)


@pytest.mark.parametrize("pkg", XIAOMI_SYSTEM)
def test_systemove_balicky_xiaomi_mediatek(pkg):
    assert build_protection().protects(pkg)


def test_ochrana_neni_jen_podle_podretezce():
    prot = build_protection()
    # klíčové slovo bez přesného balíčku je jen varování, ne ochrana
    assert not prot.protects("com.example.netflixclone")
    assert prot.suspect("com.example.netflixclone")


def test_voyo_je_alias_oneplay():
    assert resolve_app_name("Voyo")["name"] == "Oneplay"


def test_oneplay_tvapp_chraneny_kdyz_zakaznik_napise_jen_oneplay():
    profile = {"speed": {"disable_candidates": [
        {"package": "net.cme.voyo.cz.tvapp", "label": "TV aplikace", "why": "", "kind": "bloat", "verified": True}]}}
    cfg = load_protected()
    cfg = {**cfg, "apps": [a for a in cfg["apps"] if a["name"] != "Oneplay"]}   # bez výchozí ochrany
    unprotected = select_packages(profile, "speed", {"net.cme.voyo.cz.tvapp"}, protected_cfg=cfg,
                                  protection=build_protection(protected_cfg=cfg))
    assert unprotected.disable                                                   # kontrola testu
    prot = build_protection(["Oneplay"])
    assert prot.protects("net.cme.voyo.cz.tvapp")
    assert not select_packages(profile, "speed", {"net.cme.voyo.cz.tvapp"}, protection=prot).disable


def test_mitv_dedi_kandidaty_z_android_tv():
    pkgs = {c["package"] for c in MITV["speed"]["disable_candidates"]}
    assert "com.google.android.backdrop" in pkgs and "com.miui.tv.analytics" in pkgs


def test_xiaomi_kandidati_jsou_neovereni():
    for mod in ("speed", "ads"):
        for c in MITV[mod]["disable_candidates"]:
            if "xiaomi" in c["package"] or "miui" in c["package"] or "mitv" in c["package"]:
                assert c["verified"] is False, c["package"]


def test_zadny_kandidat_v_konfiguraci_neni_chraneny():
    prot = build_protection()
    for m in MODELS.values():
        for mod in ("speed", "ads"):
            for c in m.get(mod, {}).get("disable_candidates", []):
                assert not prot.protects(c["package"]), (m["id"], c["package"])


def test_vyber_jen_nainstalovane_overene_a_nevypnute():
    installed = {"com.google.android.backdrop", "com.google.android.play.games", "com.miui.tv.analytics"}
    sel = select_packages(MITV, "speed", installed, disabled={"com.google.android.play.games"})
    assert [c["package"] for c in sel.disable] == ["com.google.android.backdrop"]
    reasons = dict(sel.skipped)
    assert reasons["com.google.android.play.games"] == "už je vypnutý"
    assert reasons["com.android.printspooler"] == "na TV není"
    assert reasons["com.miui.tv.analytics"].startswith("neověřený")


def test_adminem_schvaleny_neovereny_kandidat():
    sel = select_packages(MITV, "speed", {"com.miui.tv.analytics"}, approved={"com.miui.tv.analytics"})
    assert [c["package"] for c in sel.disable] == ["com.miui.tv.analytics"]
    assert "com.miui.tv.analytics" not in disable_candidates(MITV)
    assert "com.miui.tv.analytics" in disable_candidates(MITV, {"com.miui.tv.analytics"})


def test_zakaznikova_volna_aplikace_je_varovani_a_dotaz():
    profile = {"speed": {"disable_candidates": [
        {"package": "com.vendor.mojeiptv", "label": "Moje IPTV", "why": "", "kind": "bloat", "verified": True}]}}
    sel = select_packages(profile, "speed", {"com.vendor.mojeiptv"}, protection=build_protection(["Moje IPTV"]))
    assert not sel.disable and [c["package"] for c in sel.ask] == ["com.vendor.mojeiptv"]


def test_zakaznik_muze_zadat_balicek_primo():
    assert build_protection(["com.example.mojeiptv"]).protects("com.example.mojeiptv")


def test_nejista_streamovaci_aplikace_jde_do_dotazu():
    profile = {"speed": {"disable_candidates": [
        {"package": "com.vendor.videoportal", "label": "Video portál", "why": "", "verified": True}]}}
    sel = select_packages(profile, "speed", {"com.vendor.videoportal"})
    assert not sel.disable and [c["package"] for c in sel.ask] == ["com.vendor.videoportal"]


def test_patchwall_jen_s_nahradnim_launcherem():
    approved = {"com.mitv.tvhome.atv"}
    sel = select_packages(MITV, "ads", {"com.mitv.tvhome.atv"}, approved=approved)
    assert dict(sel.skipped)["com.mitv.tvhome.atv"] == "chybí náhradní launcher"
    sel = select_packages(MITV, "ads", {"com.mitv.tvhome.atv", "com.spocky.projengmenu"},
                          disabled={"com.spocky.projengmenu"}, approved=approved)
    assert dict(sel.skipped)["com.mitv.tvhome.atv"] == "chybí náhradní launcher"
    sel = select_packages(MITV, "ads", {"com.mitv.tvhome.atv", "com.spocky.projengmenu"}, approved=approved)
    assert [c["package"] for c in sel.disable] == ["com.mitv.tvhome.atv"]


@pytest.mark.parametrize("name,expect", [("netflix", "Netflix"), ("HBO Max", "HBO Max"), ("disney+", "Disney+"),
                                         ("Oneplay", "Oneplay"), ("voyo", "Oneplay"), ("smart tube", "SmartTube"),
                                         ("Moje IPTV", None)])
def test_preklad_nazvu_aplikace(name, expect):
    r = resolve_app_name(name)
    assert (r["name"] if r else None) == expect


def test_xiaomi_profil_podle_realne_tv():
    pkgs = {c["package"]: c for m in ("speed", "ads") for c in MITV[m]["disable_candidates"]}
    assert "com.miui.systemAdSolution" not in pkgs                      # na MiTV-MOOQ0 není
    for p in ("com.miui.tv.analytics", "com.xiaomi.statistic", "com.mitv.tvhome.atv", "fusion.android.tv.demo"):
        assert pkgs[p]["seen_on"] and pkgs[p]["verified"] is False       # ověřeno na TV, schválení = admin
    prot = build_protection()
    for p in ("com.xiaomi.wfdsinkhelperservice", "com.mitv.milinkservice", "com.mitv.livetv.ci_mediatek"):
        assert prot.protects(p)                                           # promítání a živé vysílání
