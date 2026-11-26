import pytest

from tvopt.config import load_models, load_protected
from tvopt.packages import build_protection, resolve_app_name, select_packages

MODELS = load_models()
MITV = MODELS["xiaomi-mitv"]
DEFAULT_PROTECTED = [
    "com.google.android.youtube.tv", "com.google.android.youtube", "com.android.vending",
    "com.google.android.gms", "com.google.android.googlequicksearchbox", "com.google.android.katniss",
    "com.google.android.videos", "com.google.android.apps.tv.launcherx", "com.netflix.ninja",
    "com.wbd.stream", "com.disney.disneyplus", "com.amazon.amazonvideo.livingroom", "org.xbmc.kodi",
    "com.liskovsoft.smarttubetv.beta", "com.spocky.projengmenu", "cz.oneplay.androidtv",
]


@pytest.mark.parametrize("pkg", DEFAULT_PROTECTED)
def test_vychozi_chranene_aplikace(pkg):
    assert build_protection().protects(pkg)


def test_mitv_dedi_kandidaty_z_android_tv():
    pkgs = {c["package"] for c in MITV["speed"]["disable_candidates"]}
    assert "com.google.android.backdrop" in pkgs and "com.miui.tv.analytics" in pkgs
    assert MITV["speed"]["settings"], "animace se dědí z android-tv"


def test_zadny_kandidat_v_konfiguraci_neni_chraneny():
    prot = build_protection()
    for m in MODELS.values():
        for mod in ("speed", "ads"):
            for c in m.get(mod, {}).get("disable_candidates", []):
                assert not prot.protects(c["package"], c["label"]), (m["id"], c["package"])


def test_vyber_jen_nainstalovane_overene_a_nevypnute():
    installed = {"com.google.android.backdrop", "com.google.android.play.games", "com.miui.tv.analytics"}
    sel = select_packages(MITV, "speed", installed, disabled={"com.google.android.play.games"})
    assert [c["package"] for c in sel.disable] == ["com.google.android.backdrop"]
    reasons = dict(sel.skipped)
    assert reasons["com.google.android.play.games"] == "už je vypnutý"
    assert reasons["com.android.printspooler"] == "na TV není"
    assert reasons["com.miui.tv.analytics"].startswith("neověřený")


def test_admin_rezim_nabidne_i_neovereny():
    sel = select_packages(MITV, "speed", {"com.miui.tv.analytics"}, admin_mode=True)
    assert [c["package"] for c in sel.disable] == ["com.miui.tv.analytics"]


def test_chranena_aplikace_se_nevybere_ani_kdyz_je_v_kandidatech():
    profile = {"speed": {"disable_candidates": [
        {"package": "com.netflix.ninja", "label": "Netflix", "why": "", "kind": "bloat", "verified": True}]}}
    sel = select_packages(profile, "speed", {"com.netflix.ninja"})
    assert not sel.disable and sel.skipped == [("com.netflix.ninja", "chráněná aplikace")]


def test_zakaznikova_aplikace_se_prida_do_chranenych():
    profile = {"speed": {"disable_candidates": [
        {"package": "cz.voyo.tv", "label": "Voyo", "why": "", "kind": "bloat", "verified": True}]}}
    assert select_packages(profile, "speed", {"cz.voyo.tv"}).disable
    prot = build_protection(["Voyo"])
    assert not select_packages(profile, "speed", {"cz.voyo.tv"}, protection=prot).disable


def test_zakaznik_muze_zadat_balicek_primo():
    assert build_protection(["com.example.mojeiptv"]).protects("com.example.mojeiptv")


def test_nejista_streamovaci_aplikace_jde_do_dotazu():
    profile = {"speed": {"disable_candidates": [
        {"package": "com.vendor.videoportal", "label": "Video portál", "why": "", "verified": True}]}}
    sel = select_packages(profile, "speed", {"com.vendor.videoportal"})
    assert not sel.disable and [c["package"] for c in sel.ask] == ["com.vendor.videoportal"]


def test_launcher_jen_s_nahradnim():
    sel = select_packages(MITV, "ads", {"com.mitv.tvhome.atv"}, admin_mode=True)
    assert dict(sel.skipped)["com.mitv.tvhome.atv"] == "chybí náhradní launcher"
    sel = select_packages(MITV, "ads", {"com.mitv.tvhome.atv", "com.spocky.projengmenu"}, admin_mode=True)
    assert "com.mitv.tvhome.atv" in [c["package"] for c in sel.disable]


@pytest.mark.parametrize("name,expect", [("netflix", "Netflix"), ("HBO Max", "HBO Max"), ("disney+", "Disney+"),
                                         ("Oneplay", "Oneplay"), ("smart tube", "SmartTube"), ("Voyo", None)])
def test_preklad_nazvu_aplikace(name, expect):
    r = resolve_app_name(name)
    assert (r["name"] if r else None) == expect


def test_konfigurace_chranenych_obsahuje_pozadovane_aplikace():
    names = {a["name"] for a in load_protected()["apps"]}
    for n in ["YouTube", "Google Play", "Google TV", "Oneplay", "Netflix", "HBO Max", "Disney+",
              "Prime Video", "Kodi", "SmartTube", "Projectivy Launcher", "Asistent Google", "Služby Google Play"]:
        assert n in names
