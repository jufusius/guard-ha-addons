import pytest

from tvopt.intents import detect_intents, is_done, is_no, is_yes, parse_consent


@pytest.mark.parametrize("text,intents", [
    ("zrychli mi televizi", ["speed"]),
    ("Televize se strašně seká při načítání", ["speed"]),
    ("chci TV bez reklam", ["ads"]),
    ("vypni sledování na televizi", ["ads"]),
    ("nainstaluj mi Kodi", ["kodi"]),
    ("vrať to zpátky", ["revert"]),
    ("Vrat to, nefunguje mi Netflix", ["revert"]),
    ("zrychli tv a dej pryč reklamy", ["ads", "speed"]),
    ("co jsi změnil na TV?", ["status"]),
    ("dobrý den, jak se máte", []),
    ("dívám se na sledování ptáků", []),
])
def test_zamery(text, intents):
    assert detect_intents(text) == intents


@pytest.mark.parametrize("t", ["ano", "Ano", "ano.", "ANO!", " ano "])
def test_ano(t):
    assert parse_consent(t) == "yes" and is_yes(t) and not is_no(t)


@pytest.mark.parametrize("t", ["ne", "Ne.", "NE!"])
def test_ne(t):
    assert parse_consent(t) == "no" and is_no(t) and not is_yes(t)


@pytest.mark.parametrize("t", ["jo", "jo nechci", "ano ne", "ne ano", "souhlasím", "ok", "ano, ale nejdřív…",
                               "anoo", "neee", "ano?ne", "", None, 1, "nechci", "do toho"])
def test_nejednoznacne(t):
    assert parse_consent(t) == "ambiguous" and not is_yes(t) and not is_no(t)


@pytest.mark.parametrize("t,ok", [("hotovo", True), ("Hotovo!", True), ("mám to", False),
                                  ("ještě ne, hotovo zítra", False), ("nehotovo", False)])
def test_hotovo(t, ok):
    assert is_done(t) is ok
