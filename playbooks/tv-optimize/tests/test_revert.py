from tvopt.revert import Change, build_snapshot, render_script, revert_commands, snapshot_commands

KEYS = ["global/window_animation_scale", "global/transition_animation_scale"]


def _snap():
    out = {
        "pm list packages": "package:a.b.c\npackage:d.e.f\npackage:g.h.i",
        "pm list packages -d": "package:g.h.i",
        "settings get global window_animation_scale": "1.0",
        "settings get global transition_animation_scale": "null",
    }
    assert set(snapshot_commands(KEYS)) == set(out)
    return build_snapshot(out, KEYS, "2026-11-26T12:00:00+00:00")


def test_snapshot():
    s = _snap()
    assert s.settings == {"global/window_animation_scale": "1.0", "global/transition_animation_scale": None}
    assert s.disabled_before == ["g.h.i"] and "a.b.c" in s.installed


def test_vraceni_nastaveni_a_balicku():
    changes = [
        Change("setting", "global/window_animation_scale", "1.0", "0.5"),
        Change("setting", "global/transition_animation_scale", None, "0.5"),
        Change("disable", "a.b.c", None, "disabled"),
        Change("disable", "g.h.i", None, "disabled"),   # byl vypnutý už předtím → nezapínat
        Change("maintenance", "am kill-all"),
    ]
    cmds = revert_commands(_snap(), changes)
    assert cmds == [
        "pm enable a.b.c",
        "settings delete global transition_animation_scale",
        "settings put global window_animation_scale 1.0",
    ]


def test_opakovana_zmena_se_vraci_na_puvodni_ze_snapshotu():
    changes = [Change("setting", "global/window_animation_scale", "1.0", "0.5"),
               Change("setting", "global/window_animation_scale", "0.5", "0.25")]
    assert revert_commands(_snap(), changes) == ["settings put global window_animation_scale 1.0"]


def test_skript_pro_technika():
    script = render_script("192.0.2.14", ["pm enable a.b.c", "settings delete global x_y"])
    assert script.startswith("#!/usr/bin/env bash")
    assert "TV=192.0.2.14:5555" in script
    assert "adb -s \"$TV\" shell 'pm enable a.b.c'" in script
    assert script.rstrip().endswith('adb disconnect "$TV"')


def test_skript_bez_zmen():
    assert "shell" not in render_script("192.0.2.14", [])
