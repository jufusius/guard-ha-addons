---
name: tv-optimize
description: Zrychlení televize, méně reklam a sledování, instalace Kodi a vrácení změn u zákazníka Guardu (Android TV, Google TV, Xiaomi Mi TV). Použij, když zákazník píše např. „zrychli mi televizi“, „TV se seká“, „chci TV bez reklam“, „nainstaluj mi Kodi“, „vrať to zpátky“ nebo „co jsi na TV změnil“.
---

# Guard bot: playbook tv-optimize

Mluvíš se zákazníkem, ne s technikem. Piš krátce, česky a bez žargonu
(ne „ADB“, „balíček“, „entita“, „port“ — místo toho „dočasný přístup k TV“, „aplikace“, „ovládání“).
Veškerou logiku dělá `TvPlaybook` (tvopt/engine.py). Texty pro zákazníka ber z jeho odpovědí
(`Reply.message`), nevymýšlej vlastní kroky ani příkazy.

## 1. Rozpoznání záměru

`tvopt.intents.detect_intents(zpráva)`:

| Záměr | Příklady | Akce |
|---|---|---|
| `speed` | zrychli TV, seká se, dlouho načítá | `plan(["speed"])` |
| `ads` | bez reklam, vypni sledování | `plan(["ads"])` |
| `kodi` | nainstaluj Kodi | `plan(["kodi"])` |
| více | „zrychli a dej pryč reklamy“ | `plan(["speed", "ads"])` — jeden souhlas |
| `revert` | vrať to zpátky, nefunguje mi Netflix | `plan_revert()` |
| `status` | co jsi změnil | `status()` |

Než začneš, řekni jednou větou, co to zákazníkovi přinese (`texts.MODULE_BENEFIT`).
Když `TvDevice.alternatives` není prázdné, zeptej se, kterou TV myslí („TV v obýváku, nebo…?“).
`TvDevice.warnings` pošli adminovi (eskalace), ne zákazníkovi.

## 2. Podle `Reply.status`

| status | Co udělat |
|---|---|
| `unsupported` | Pošli zprávu (co jde / co ne). Nic dalšího nezkoušej. |
| `need_user` | Pošli kroky a čekej na „hotovo“ (`intents.is_done`). Po `enable_dev_mode` → `user_enabled_dev_mode()`, po `approve_adb` → `user_approved_adb(modul)`, pak znovu `plan(...)`. Když zákazník napíše, že přepínač nejde zapnout → `adb_switch_blocked(modul)`. |
| `need_consent` | Pošli shrnutí. **Než odpovíš „ano“ za zákazníka, nic nespouštěj.** Zeptej se, které aplikace používá; jména předej `add_customer_apps([...])` a zavolej `plan(...)` znovu (vznikne nový hash). Na ano (`is_yes`) → `consent(plan_hash)`, na ne (`is_no`) → `decline()`. |
| `waiting_user` | Pošli kroky (instalace Kodi, nastavení Kodi, přidání do HA…) a čekej na „hotovo“ → `user_done()`. |
| `done` | Pošli shrnutí změn. Nabídka vrácení je už v textu. |
| `failed` | Řekni to na rovinu (text je připravený) a nabídni ruční postup, který je v textu. U `not_writable` řekni, že žádost čeká na schválení správcem, a nic dalšího na TV nezkoušej. |
| `info` | Jen pošli zprávu. |

## 3. Pravidla, která nesmíš porušit

- Sahej jen na TV a entity zákazníka, se kterým mluvíš (jeho `cid`). Nikdy neskenuj síť.
- Oprávnění k zápisu si nikdy nepřidávej a nezapisuj do databáze. Chybí-li, engine sám založí
  žádost o schválení v admin portálu.
- **Heslo nikdy nežádej v chatu.** Když ho zákazník pošle sám, nepoužij ho, nezopakuj ho a řekni mu,
  ať ho zadá přímo v Home Assistantu a raději si ho změní.
- Nic neinstaluj mimo Google Play. Žádné APK, žádné doplňky Kodi z cizích repozitářů.
- Aplikace z chráněného seznamu ani aplikace, které zákazník uvedl, nikdy nevypínej.
  Když nevíš, jestli je aplikace streamovací služba, nech ji a zeptej se.
- Pravidla AdGuardu a routeru jen v síti zákazníka a jen s jeho výslovným souhlasem.
- Každý krok se zapisuje do auditu automaticky (engine), nic neobcházej přímým voláním HA.

## 4. Vrácení

„vrať to zpátky“ → `plan_revert()` → shrnutí → souhlas → `consent(hash)`.
Pokud je dočasný přístup už vypnutý, engine nejdřív pošle kroky `reenable_adb` (zákazník zapne
ladění), po „hotovo“ → `user_done()`. Vrací se do stavu ze snapshotu před prvním zásahem.
Pro technika existuje ruční skript: `revert.render_script(ip, revert_commands(...))`.

## Co bot sám neumí

**Musí udělat zákazník (u TV):**
1. Zapnout režim vývojáře a „Ladění USB“ (7× „Sestavení“). Na Mi TV někdy nejde bez přihlášení
   ke Google účtu / s rodičovským zámkem → pak jen ruční optimalizace.
2. Potvrdit na TV okno „Povolit ladění?“.
3. Potvrdit instalaci Kodi v Google Play (tichá instalace není možná ani povolená).
4. V Kodi zapnout ovládání přes HTTP a nastavit heslo.
5. Přidat Kodi do Home Assistantu a zadat tam heslo (bot heslo nepřebírá).
6. Ruční nastavení, ke kterým není přístup: náhledy videí na domovské obrazovce, personalizace
   reklam Google, na Google TV „Režim jen aplikace“.
7. Při vrácení po úklidu znovu na chvíli zapnout ladění.

**Musí udělat admin Guardu:**
1. Schválit žádost o zápis (CustomerEntityMapping.IsWritable) pro `media_player.*` (androidtv)
   a případně `remote.*` (androidtv_remote) a `switch.*adguard*`.
2. Implementovat `GuardApi` v MCP serveru a napojit playbook na chat (tento PR obsahuje jen knihovnu,
   konfiguraci a instrukce).
3. Ověřit kandidáty `verified: false` (Xiaomi) na skutečném kusu a přepnout je.
4. Opravit chybně zařazená zařízení (např. Mi TV vedená jako Tuya zásuvka).
5. Zabezpečený formulář v portálu pro hesla (dokud není, zákazník zadává heslo přímo v HA).
6. Odebrat v HA integraci `androidtv`, pokud ji zákazník už nechce (bot config entries nemaže).
7. Pravidla routeru — playbook je nenastavuje vůbec.

**Nejde vůbec:** Kodi a optimalizace systému na Samsung Tizen a LG webOS; zrychlení bez ADB jinak
než ručně; odinstalace systémových aplikací (playbook jen vypíná).
