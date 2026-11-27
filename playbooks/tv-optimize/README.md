# Playbook `tv-optimize` — zrychlení TV, méně reklam, Kodi, vrácení změn

Znovupoužitelný postup pro Guard bota. Spouští se, když zákazník v chatu napíše třeba
„zrychli mi televizi“, „chci TV bez reklam“, „nainstaluj mi Kodi“ nebo „vrať to zpátky“.

> **Stav: interní.** Playbook je v tomto repozitáři jako knihovna + instrukce pro bota.
> Napojení na Guard MCP server (implementace `GuardApi`) a zapnutí pro zákazníky je
> samostatný krok — viz [Co je potřeba udělat v Guardu](#co-je-potřeba-udělat-v-guardu).

## Struktura

```
playbooks/tv-optimize/
├── README.md               ← tento soubor (struktura, bezpečnost, kontrakt pro Guard)
├── SKILL.md                ← instrukce pro Guard bota: záměry, rozhovor, souhlas, chyby
├── docs/
│   ├── GuardApi.md         ← kontrakt pro Guard: metody, oprávnění, audit, limity, chyby, testy
│   └── ukazka-rozhovoru.md ← „chci TV bez reklam“ → hotovo → „vrať to zpátky“
├── config/
│   ├── protected_apps.json ← chráněné aplikace (výchozí) + systémové + nápovědy „streaming“
│   ├── unsupported.json    ← Samsung Tizen / LG webOS / neznámá TV: co jde a co ne
│   └── models/
│       ├── android-tv.json ← obecný Android TV profil (animace, kandidáti, DNS, Kodi)
│       ├── google-tv.json  ← Google TV (dědí android-tv)
│       └── xiaomi-mitv.json← Xiaomi Mi TV (dědí android-tv, vlastní kandidáti)
├── tvopt/                  ← Python knihovna (jen stdlib, žádná síť)
│   ├── device.py           ← detekce TV z dat Guardu a HA (nic napevno)
│   ├── packages.py         ← výběr balíčků k vypnutí, chráněné aplikace
│   ├── security.py         ← allowlist ADB příkazů, entity podle cid, porty, redakce
│   ├── revert.py           ← snapshot, záznam změn, příkazy a skript pro vrácení
│   ├── intents.py          ← rozpoznání záměru z věty zákazníka
│   ├── texts.py            ← texty pro zákazníka (kroky u TV, ruční postupy, chyby)
│   └── engine.py           ← TvPlaybook: plán → souhlas → provedení → úklid → vrácení
└── tests/                  ← pytest, FakeGuard simuluje TV a oddělení zákazníků
```

Spuštění testů: `python -m pytest playbooks/tv-optimize/tests -q` (CI: `.github/workflows/tv-optimize.yml`,
Python 3.11 a 3.13).

## Moduly (každý jde spustit samostatně)

| Modul | Záměr zákazníka | Co dělá | Potřebuje |
|---|---|---|---|
| `speed` | „zrychli“, „seká se“, „pomalá“ | animace 0,5×, vypnutí ověřených nepoužívaných aplikací, vyčištění cache, zavření aplikací na pozadí | ADB (`androidtv`) |
| `ads` | „bez reklam“, „bez sledování“ | **výchozí:** AdGuard Home v síti zákazníka (zapne ochranu, pokud je vypnutá a entita zapisovatelná; DNS přes DHCP/router nastavuje správce po domluvě). Bez AdGuard Home jen návrh. **Volba:** veřejný `dns.adguard-dns.com` jako Soukromé DNS jen po výslovném „ano“ na upozornění „DNS dotazy půjdou přes AdGuard (třetí strana)“ a po ověření DoT. Vlastní DNS zákazníka nepřepisuje. | HA (AdGuard Home); ADB jen pro veřejný DNS / ověřené kandidáty |
| `kodi` | „nainstaluj Kodi“ | otevře Kodi v Google Play, zákazník potvrdí, nastaví HTTP ovládání s heslem, přidá integraci v HA sám, bot ověří | `androidtv_remote` nebo ADB; bez nich ruční návod |
| `cleanup` | (vždy na konci) / „vrať to zpátky“ | vypne síťové ladění, zkontroluje porty 5555 a 9090; vrácení ze snapshotu | ADB (při vrácení ho zákazník na chvíli zapne) |

`TvPlaybook.plan(["speed"])`, `plan(["ads"])`, `plan(["kodi"])` i kombinace. Vrácení: `plan_revert()`.

Pozdější změny bez vracení všeho:
- `add_customer_apps` / `remove_customer_apps`: zákazníkovy chráněné aplikace;
- `plan_enable_apps`: zapnout zpátky jen vybranou aplikaci, kterou playbook vypnul;
- `plan_disable_apps`: vypnout další aplikaci, jen ověřenou nebo schválenou;
- `list_apps`: přehled.

Každá změna na TV jde přes souhlas zákazníka.

> Striktní Soukromé DNS (`hostname`) odpojí TV od internetu, když DNS-over-TLS neběží. Proto se
> veřejný DNS nabízí jen po `check_dot` (při návrhu i těsně před provedením). Hned po zapnutí se
> připojení ověří přímo z TV (`ping -c 1 -W 3 connectivitycheck.gstatic.com`, jediný povolený ping).
> Když neprojde do 30 s, DNS se okamžitě vrátí na původní hodnotu ze snapshotu a zákazník se to dozví.

## Podporované TV

- **Xiaomi Mi TV** (Android TV) — profil `xiaomi-mitv`, rozpozná se i podle hostname `MITV`
  (Guard ji může mít chybně jako Tuya zásuvku → bot to nahlásí jako varování pro admina).
- **Google TV** — profil `google-tv` (pozná se podle launcheru `launcherx`).
- **Android TV obecně** — profil `android-tv`.
- **Samsung Tizen, LG webOS, neznámá TV** — bot nic nezkouší, řekne co jde a co ne (`unsupported.json`).

Nový model = nový soubor v `config/models/` s `extends: "android-tv"` a vlastními kandidáty.
Kandidát s `verified: false` se zákazníkům nenabízí, dokud ho admin neověří na skutečném kusu
(`pm list packages`) a neschválí (`GuardApi.approved_packages`) nebo nepřepne na `true`.
Všichni kandidáti Xiaomi jsou zatím `verified: false`. PatchWall (`com.mitv.tvhome.atv`) jde vypnout
jen s nainstalovaným náhradním launcherem. Hned po vypnutí se ověří, že Home otevře náhradní launcher,
jinak se okamžitě udělá `pm enable`.

Detekce TV: integrace TV v HA na IP zařízení, nebo profil modelu (např. hostname `MITV`), nebo úzké
nápovědy v názvu (`mitv`, `androidtv`, `googletv`, `bravia`, `samsungtv`, `webostv`…). Samotné „tv“
ani „android“ nestačí. Má-li zákazník víc TV, bot se zeptá a vybrat jde jen jeho TV. Chybné zařazení
(Mi TV vedená jako zásuvka) jde adminovi jako návrh opravy a oprávnění nikdy nerozšiřuje.

## Chráněné aplikace

Výchozí (`protected_apps.json`) podle **přesných balíčků**:

| Aplikace | Balíčky |
|---|---|
| Oneplay (alias Voyo) | `net.cme.voyo.cz`, `net.cme.voyo.cz.tvapp` |
| Netflix | `com.netflix.ninja` |
| HBO Max | `com.wbd.stream` |
| Disney+ | `com.disney.disneyplus` |
| Prime Video | `com.amazon.amazonvideo.livingroom` |
| YouTube | `com.google.android.youtube.tv` |
| Kodi | `org.xbmc.kodi` |
| Projectivy Launcher | `com.spocky.projengmenu` |
| SmartTube | `org.smarttube.stable`, `org.smarttube.beta`, `app.smarttube`, `app.smarttube.fdroid` (ověřeno v `yuliskov/SmartTube` build.gradle), `com.teamsmart.videomanager.tv`, `com.liskovsoft.smarttubetv.beta` (starší ID) |
| Google | Play (`com.android.vending`), Play Services, Google app, Asistent, Google TV, launcher, Chromecast |

Systémové (nejdou odebrat ani zákazníkem): Android TV základ a Xiaomi/MediaTek (`mitv.service`,
`com.mitv.overseaservice`, `com.mi.bluetooth`, `com.mitv.setup`, `com.mitv.livetv`, vše `com.mediatek.*`,
`com.xiaomi.android.tvsetup.partnercustomizer`, aktualizace `com.xiaomi.mitv.updateservice`,
`com.mitv.upgrader`, `com.mediatek.tv.systemupdater`).

Podřetězec v názvu (`warn_keywords`, volný text zákazníka) je **jen záložní varování**: takový kandidát
jde do dotazu, nikdy do plánu. Zákazník si seznam doplní v chatu. Když napíše „Oneplay“ nebo „Voyo“,
chrání se oba balíčky. Názvy se čistí (max. 64 znaků, max. 50 aplikací, jen písmena, číslice, mezera
a `. + - _`, bez řídicích znaků). Do příkazů na TV se nikdy nedostanou. Playbook aplikace jen vypíná
(`pm disable-user`) a vrací (`pm enable`), nikdy neodinstalovává.

## Bezpečnost a oddělení zákazníků

Vynucuje engine (`security.py`) **a** zároveň Guard write-gateway. Jedno nenahrazuje druhé.

- **Jen zákazníkova zařízení.** Data jen přes `GuardApi.context(cid)`. Před **každým** ADB příkazem
  a voláním služby se znovu ověří, že entita patří cid a je zapisovatelná. Žádné skenování sítě.
- **Jen zapisovatelné entity.** Když chybí `IsWritable`, engine založí `request_write_approval`
  a nabídne ruční postup. Oprávnění si nikdy nepřidává. Config flow `androidtv` jen pro IP TV z kontextu
  a jen s doložitelným schválením (zapisovatelná `remote.*` téže TV, nebo schválený ticket).
- **Souhlas jen od zákazníka.** `consent(plan_hash, message_id)` ověří přes `customer_message`, že
  zpráva existuje, je od zákazníka v této konverzaci, je novější než návrh, ještě nebyla použita
  a zní přesně „ano“. „ne“ plán zruší, cokoli jiného („jo nechci“, „ano ne“, „souhlasím“) → dotaz znovu.
  Totéž pro „hotovo“ a pro volbu veřejného DNS. Audit obsahuje `message_id` a čas zprávy.
- **Allowlist ADB podle kontextu:**
  - `pm disable-user`: jen ověřené / adminem schválené kandidáty profilu, nikdy chráněné;
  - `pm enable`: jen balíčky, které playbook vypnul (nebo kandidáty profilu);
  - `private_dns_specifier`: jen hostname z konfigurace, nebo vrácení na původní hodnotu ze snapshotu;
  - `adb_enabled` a `adb_wifi_enabled`: jen hodnota `0`;
  - animace jen z výčtu hodnot;
  - jinak čtení (`pm list`, `settings get`, `getprop ro.product.model`, `dumpsys meminfo`, kontrola HOME),
    `pm trim-caches`, `am kill-all` a otevření Google Play;
  - zakázané: instalace a odinstalace, řetězení, řídicí znaky, ostatní nastavení.
- **Whitelist HA služeb:** `switch.turn_on/off` (jen `entity_id`), `remote.turn_on` jen s `market://`
  URL. `data.entity_id` musí odpovídat entitě kroku.
- **Instalace jen z Google Play**, potvrzuje zákazník. Žádné APK odjinud, žádné doplňky Kodi.
- **Hesla nikdy v chatu.** Kodi heslo zákazník zadá přímo v HA. Pole „WebSocket port“ maže (port 9090
  zůstává zavřený).
- **Úklid s ověřením.** Vypne `adb_wifi_enabled` i `adb_enabled` a ověří přes `port_status`, že 5555 i 9090
  jsou zavřené. Neověřitelné = problém → eskalace adminovi a krok pro zákazníka („Odvolat autorizace
  ladění USB“, vypnout Ladění USB), žádné bezvýhradné „hotovo“. Zákazník na TV nemá zaškrtávat
  „Vždy povolit z tohoto počítače“. Další akce vyžaduje znovu zapnout ladění.
- **Vrácení** ze snapshotu před prvním zásahem. Při selhání se změny ani snapshot nemažou, plán zůstane
  rozpracovaný (`retry()`), admin dostane eskalaci a skript pro technika (`manual_revert_script()`).
- **Audit každé akce** bez tajemství (viz `docs/GuardApi.md`).
- **Rate limity na zákazníka** (klouzavé okno, napříč konverzacemi): ADB 30/min, služby 10/min,
  config flow 3/h, testy portů a DoT 10/min. Při překročení plán zůstane a jde zopakovat.
- **Úklid bez plánu** (není co měnit, ale přístup zůstal otevřený) čeká na „hotovo“ stejně přísně
  jako jinde (reálná zpráva zákazníka, přesné „hotovo“) a pak znovu ověří porty.
- **AdGuard a router:** jen v síti zákazníka a jen s jeho souhlasem. Pravidla routeru playbook nenastavuje.

## Kontrakt pro Guard (`GuardApi`)

Podrobně v [docs/GuardApi.md](docs/GuardApi.md): signatury, oprávnění, audit, rate limity,
chování při chybě a testovací kontrakt (testy musí projít proti `FakeGuard` i reálné implementaci).

## Co je potřeba udělat v Guardu

Viz [SKILL.md → Co bot sám neumí](SKILL.md#co-bot-sám-neumí).
