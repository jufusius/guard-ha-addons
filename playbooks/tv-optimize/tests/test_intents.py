import pytest

from tvopt.intents import detect_intents, is_done, is_no, is_yes


@pytest.mark.parametrize("text,intents", [
    ("zrychli mi televizi", ["speed"]),
    ("Televize se strašně seká při načítání", ["speed"]),
    ("TV je pomalá", ["speed"]),
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


@pytest.mark.parametrize("t", ["ano", "Ano.", "jo", "souhlasím", "OK", "do toho"])
def test_ano(t):
    assert is_yes(t) and not is_no(t)


@pytest.mark.parametrize("t", ["ne", "Nechci", "zruš to"])
def test_ne(t):
    assert is_no(t) and not is_yes(t)


@pytest.mark.parametrize("t", ["hotovo", "Hotovo!", "mám to", "už je hotovo"])
def test_hotovo(t):
    assert is_done(t)
