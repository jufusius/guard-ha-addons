"""Texty pro zákazníka. Jednoduše, bez technického žargonu, krok za krokem."""
from __future__ import annotations

MODULE_BENEFIT = {
    "speed": "TV bude rychleji reagovat na ovladač a rychleji otevírat aplikace.",
    "ads": "Na TV bude méně reklam a TV bude o vás posílat méně údajů.",
    "kodi": "Budete mít na TV přehrávač Kodi a půjde ovládat z Home Assistantu.",
    "cleanup": "Vypnu dočasný přístup k TV, který jsem potřeboval, a zkontroluji, že nic nezůstalo otevřené.",
}

USER_STEPS = {
    "enable_dev_mode": [
        "Na TV otevřete Nastavení → Předvolby zařízení → Informace.",
        "Najeďte na řádek „Sestavení“ a 7× rychle za sebou stiskněte OK na ovladači. Objeví se „Nyní jste vývojář“.",
        "Vraťte se zpět do Předvoleb zařízení, otevřete „Pro vývojáře“ a zapněte „Ladění USB“.",
        "Napište mi „hotovo“.",
    ],
    "approve_adb": [
        "Na TV se teď objeví okno „Povolit ladění?“.",
        "Zaškrtněte „Vždy povolit z tohoto počítače“ a potvrďte OK.",
        "Napište mi „hotovo“.",
    ],
    "reenable_adb": [
        "Abych mohl změny vrátit, potřebuji na chvíli znovu přístup k TV.",
        "Na TV otevřete Nastavení → Předvolby zařízení → Pro vývojáře a zapněte „Ladění USB“.",
        "Když se objeví okno „Povolit ladění?“, potvrďte OK.",
        "Napište mi „hotovo“. Po vrácení přístup zase vypnu.",
    ],
    "install_kodi": [
        "Na TV jsem vám otevřel Google Play se stránkou aplikace Kodi.",
        "Stiskněte „Instalovat“ a počkejte, až se instalace dokončí.",
        "Napište mi „hotovo“.",
    ],
    "install_kodi_manual": [
        "Na TV otevřete Google Play a vyhledejte „Kodi“ (vydavatel XBMC Foundation).",
        "Stiskněte „Instalovat“ a počkejte, až se instalace dokončí.",
        "Kodi instalujte jen z Google Play, ne ze stažených souborů z internetu.",
        "Napište mi „hotovo“.",
    ],
    "kodi_http": [
        "Otevřete na TV Kodi a vlevo nahoře zvolte ozubené kolo (Nastavení).",
        "Otevřete Služby → Ovládání.",
        "Zapněte „Povolit vzdálené ovládání přes HTTP“. Port nechte 8080.",
        "Uživatelské jméno nechte „kodi“ a nastavte si vlastní heslo. Heslo nikomu neposílejte, ani mně do chatu.",
        "Volbu „Povolit vzdálené ovládání z aplikací na jiných systémech“ nechte vypnutou.",
        "Napište mi „hotovo“.",
    ],
    "kodi_ha": [
        "V aplikaci Home Assistant otevřete Nastavení → Zařízení a služby → Přidat integraci → Kodi.",
        "Hostitel: {ip}, port: 8080, SSL nezaškrtávejte.",
        "Uživatel „kodi“ a heslo, které jste si nastavili v Kodi, zadejte přímo tam (ne do chatu).",
        "Když se zeptá na port WebSocketu, nechte pole prázdné.",
        "Napište mi „hotovo“.",
    ],
}

MANUAL_FALLBACK = {
    "speed": [
        "Nastavení → Předvolby zařízení → Pro vývojáře: „Měřítko animace okna“, „Měřítko animace přechodu“ a „Měřítko délky animace“ nastavte na 0,5×.",
        "Nastavení → Aplikace: aplikace, které nepoužíváte, otevřete a zvolte „Vypnout“. Streamovací aplikace (Netflix, YouTube, Oneplay…) nechte.",
        "Občas TV úplně vypněte ze zásuvky na 30 s — uvolní se paměť.",
    ],
    "ads": [
        "Nastavení → Síť a internet → Soukromé DNS → zadejte „dns.adguard-dns.com“ (pokud TV tuto volbu má).",
        "Nastavení → Předvolby zařízení → Domovská obrazovka: vypněte náhledy videí a zvuku.",
        "Na Google TV: Nastavení → Účty a přihlášení → váš účet → „Režim jen aplikace“.",
    ],
    "kodi": [
        "Na TV otevřete Google Play, vyhledejte „Kodi“ a nainstalujte ho.",
        "Pak pokračujte podle kroků pro zapnutí ovládání v Kodi, které vám pošlu.",
    ],
}

FAILURE = {
    "not_writable": ("K ovládání vaší TV zatím nemám povolení. Poslal jsem žádost správci Guardu ke schválení; "
                     "jakmile ji schválí, ozvu se. Mezitím to můžete udělat ručně:"),
    "adb_unreachable": ("Nepodařilo se mi k TV připojit. Většinou to znamená, že na TV není zapnuté ladění, "
                        "nebo nebylo potvrzené okno „Povolit ladění?“. Můžeme to zkusit znovu, nebo to uděláte ručně:"),
    "adb_switch_blocked": ("Na některých TV Xiaomi nejde „Ladění USB“ zapnout, když TV není přihlášená ke Google účtu "
                           "nebo má zapnutý rodičovský zámek. Zkontrolujte to prosím. Jinak to zvládneme ručně:"),
    "unsupported": "Tohle u vaší televize bohužel nejde.",
    "policy": "Tuhle změnu z bezpečnostních důvodů neudělám.",
}


def steps_text(key: str, **fmt) -> str:
    return "\n".join(f"{i}. {s.format(**fmt)}" for i, s in enumerate(USER_STEPS[key], 1))


def fallback_text(module: str) -> str:
    return "\n".join(f"• {s}" for s in MANUAL_FALLBACK.get(module, []))
