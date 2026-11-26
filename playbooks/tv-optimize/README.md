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

Spuštění testů: `python -m pytest playbooks/tv-optimize/tests -q`

## Moduly (každý jde spustit samostatně)

| Modul | Záměr zákazníka | Co dělá | Potřebuje |
|---|---|---|---|
| `speed` | „zrychli“, „seká se“, „pomalá“ | animace 0,5×, vypnutí ověřených nepoužívaných aplikací, vyčištění cache, zavření aplikací na pozadí | ADB (`androidtv`) |
| `ads` | „bez reklam“, „bez sledování“ | Soukromé DNS na AdGuard DNS (nepřepíše vlastní DNS zákazníka), vypnutí reklamních služeb z profilu, volitelně AdGuard v HA zákazníka, ruční tipy | ADB; AdGuard jen pokud ho zákazník má a entita je zapisovatelná |
| `kodi` | „nainstaluj Kodi“ | otevře Kodi v Google Play, zákazník potvrdí, nastaví HTTP ovládání s heslem, přidá integraci v HA sám, bot ověří | `androidtv_remote` nebo ADB; bez nich ruční návod |
| `cleanup` | (vždy na konci) / „vrať to zpátky“ | vypne síťové ladění, zkontroluje porty 5555 a 9090; vrácení ze snapshotu | ADB (při vrácení ho zákazník na chvíli zapne) |

`TvPlaybook.plan(["speed"])`, `plan(["ads"])`, `plan(["kodi"])` i kombinace. Vrácení: `plan_revert()`.

## Podporované TV

- **Xiaomi Mi TV** (Android TV) — profil `xiaomi-mitv`, rozpozná se i podle hostname `MITV`
  (Guard ji může mít chybně jako Tuya zásuvku → bot to nahlásí jako varování pro admina).
- **Google TV** — profil `google-tv` (pozná se podle launcheru `launcherx`).
- **Android TV obecně** — profil `android-tv`.
- **Samsung Tizen, LG webOS, neznámá TV** — bot nic nezkouší, řekne co jde a co ne (`unsupported.json`).

Nový model = nový soubor v `config/models/` s `extends: "android-tv"` a vlastními kandidáty.
Kandidát s `verified: false` se zákazníkům nenabízí, dokud ho admin neověří na skutečném kusu
(`pm list packages`) a nepřepne na `true`.

## Chráněné aplikace

Výchozí (`protected_apps.json`): YouTube (i pro Android TV), Google Play, Google TV a služby
Google (Play Services, Google app, Asistent), Oneplay, Netflix, HBO Max, Disney+, Prime Video,
Kodi, SmartTube, Projectivy Launcher + systémové části (launcher, nastavení, klávesnice,
aktualizace, Chromecast). Zákazník si seznam doplní v chatu („používám ještě Voyo“) → uloží se
k jeho cid. Kandidát, který vypadá jako streamovací služba, jde do dotazu, ne do plánu.
Playbook aplikace **nikdy neodinstalovává**, jen vypíná (`pm disable-user`), a vrací (`pm enable`).

## Bezpečnost a oddělení zákazníků

Vynucuje engine (`security.py`) **a** zároveň Guard write-gateway — jedno nenahrazuje druhé.

- **Jen zákazníkova zařízení.** Engine dostává data jen přes `GuardApi.context(cid)`. Entita
  musí být v HA stavech daného cid (`check_entity`), jinak `PolicyError`. Žádné skenování sítě.
- **Jen zapisovatelné entity.** Zápis jen na entity z `CustomerEntityMapping.IsWritable`.
  Když chybí, engine zavolá `request_write_approval` (schvalovací požadavek v admin portálu)
  a zákazníkovi nabídne ruční postup. Oprávnění si sám nikdy nepřidává.
- **Souhlas s konkrétním plánem.** Plán má hash; `consent(hash)` přijme jen aktuální plán.
  Bez souhlasu `run()` nic neprovede. Totéž platí pro vrácení.
- **Allowlist ADB příkazů.** Jen `settings get/put/delete` pro vyjmenované klíče, `pm list`,
  `pm disable-user` (ne chráněné), `pm enable`, `pm trim-caches`, `am kill-all`, `dumpsys meminfo`
  a otevření stránky v Google Play. Zakázané: instalace/odinstalace, řetězení (`; && | $()`),
  stahování, `reboot`, `setprop` a jakékoli jiné nastavení.
- **Instalace jen z Google Play** (`check_install_source`), potvrzuje zákazník na TV.
  Žádné APK odjinud, žádné doplňky Kodi z neověřených repozitářů (playbook doplňky neinstaluje).
- **Hesla nikdy v chatu.** Heslo pro Kodi zákazník nastaví v Kodi a zadá přímo v HA
  (nebo do zabezpečeného formuláře portálu, až bude). Audit prochází `redact()`.
- **Po dokončení se síťové ladění vždy vypne** (`adb_wifi_enabled 0`, `adb_enabled 0`) a zkontrolují
  se porty 5555 a 9090. Port 9090 v Kodi zůstává vypnutý (zákazník nezapíná „ovládání z aplikací
  na jiných systémech“, HA běží přes HTTP 8080 bez WebSocketu).
- **Audit každé akce:** `ts, cid, actor, conversation_id, device{ip, profile, entity}, module,
  action, command, result, detail` — bez hesel a tokenů.
- **AdGuard a router:** jen v síti zákazníka a jen s jeho souhlasem. Playbook umí zapnout ochranu
  AdGuard v HA zákazníka (je-li zapisovatelná); pravidla routeru nenastavuje.

## Kontrakt pro Guard (`GuardApi`)

Guard MCP server musí implementovat (viz `tvopt/engine.py`):

| Metoda | Co dělá | Pravidlo |
|---|---|---|
| `context(cid)` | `devices` (CustomerDevices), `states` (HA), `integrations` (config entries s `host` a `entity_ids` z entity registry) | jen data daného cid |
| `writable_entities(cid)` | entity s `IsWritable=1` | čtení z CustomerEntityMapping |
| `request_write_approval(cid, entity, reason)` | založí schvalovací požadavek v admin portálu | **ne** přímý zápis do DB |
| `start_config_flow(cid, domain, data)` | HA config flow (`androidtv`) | host = IP TV zákazníka |
| `adb(cid, entity, cmd)` | `androidtv.adb_command` přes write-gateway | gateway znovu ověří whitelist |
| `call_service(cid, domain, service, data)` | HA služba přes write-gateway | totéž |
| `port_status(cid, ip, ports)` | TCP test jen na IP TV zákazníka a jen porty 5555/9090 | žádné skenování rozsahů |
| `load/save(cid, key, value)` | stav playbooku (snapshot, změny, chráněné aplikace) | per cid |
| `audit(record)` | zápis do auditního logu | záznam je už redigovaný |

## Co je potřeba udělat v Guardu

Viz [SKILL.md → Co bot sám neumí](SKILL.md#co-bot-sám-neumí).
