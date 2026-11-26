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

## 2. Podle `Reply.status`

**Souhlas a „hotovo“ nikdy nepředávej jako svůj závěr.** Předávej `message_id` zprávy zákazníka.
Engine si text ověří přes chat vrstvu a přijme jen přesné „ano“, „ne“ nebo „hotovo“. Když nemáš
id skutečné zákaznické zprávy, nic nevolej.

| status | Co udělat |
|---|---|
| `need_choice` | Zákazník má víc TV: pošli seznam, po odpovědi `choose_tv(ip)` a znovu `plan(...)`. |
| `unsupported` | Pošli zprávu (co jde / co ne). Nic dalšího nezkoušej. |
| `need_dns_choice` | Pošli text beze změny (obsahuje upozornění na třetí stranu). Odpověď → `public_dns_answer(message_id)`, pak znovu `plan(["ads"])`. |
| `need_user` | Pošli kroky a čekej na zprávu „hotovo“. `enable_dev_mode` → `user_enabled_dev_mode(message_id)`; `approve_adb` → `user_approved_adb(message_id, modul)`; `reenable_adb` → `user_reenabled_adb(message_id, modul)`; pak znovu `plan(...)`. Když přepínač nejde zapnout → `adb_switch_blocked(modul)`. |
| `need_consent` | Pošli shrnutí (obsahuje model a IP TV). Zeptej se, které aplikace zákazník používá. Jména předej `add_customer_apps([...])` a zavolej `plan(...)` znovu (nový hash). Odpověď zákazníka → `consent(plan_hash, message_id)`. |
| `waiting_user` | Pošli kroky (instalace Kodi, nastavení Kodi, přidání do HA…). Po odpovědi → `user_done(message_id)`. |
| `done` | Pošli shrnutí. Nabídka vrácení i tip na „Odvolat autorizace ladění USB“ jsou v textu. |
| `failed` | Řekni to na rovinu (text je připravený, obsahuje ruční postup). `not_writable`: žádost čeká na schválení, nic dalšího nezkoušej. `access_open`: admin už je informovaný, čekej na „hotovo“ → `user_done`. `revert_incomplete`: na „zkusit znovu“ → `retry()`. Skript z `data["script"]` patří adminovi nebo technikovi, ne do chatu. |
| `info` | Jen pošli zprávu. |

`TvDevice.warnings` posílá engine adminovi sám (`notify_admin`). Zákazníkovi je neříkej.

## 3. Pravidla, která nesmíš porušit

- Sahej jen na TV a entity zákazníka, se kterým mluvíš (jeho `cid`). Nikdy neskenuj síť.
- Oprávnění k zápisu si nikdy nepřidávej a nezapisuj do databáze. Chybí-li, engine sám založí
  žádost o schválení v admin portálu.
- **Heslo nikdy nežádej v chatu.** Když ho zákazník pošle sám, nepoužij ho, nezopakuj ho a řekni mu,
  ať ho zadá přímo v Home Assistantu a raději si ho změní.
- Nic neinstaluj mimo Google Play. Žádné APK, žádné doplňky Kodi z cizích repozitářů.
- Aplikace z chráněného seznamu ani aplikace, které zákazník uvedl, nikdy nevypínej.
  Když nevíš, jestli je aplikace streamovací služba, nech ji a zeptej se.
- AdGuard Home a router jen v síti zákazníka a jen s jeho výslovným souhlasem. Veřejný DNS jen po
  „ano“ na upozornění o třetí straně.
- Každý krok se zapisuje do auditu automaticky (engine). Nic neobcházej přímým voláním HA.

## 4. Vrácení

„vrať to zpátky“ → `plan_revert()` → shrnutí → `consent(plan_hash, message_id)`.
Pokud je dočasný přístup vypnutý, engine nejdřív pošle kroky `reenable_adb`. Po „hotovo“ → `user_done(message_id)`.
Vrací se do stavu ze snapshotu před prvním zásahem. Když vrácení selže, nic se nesmaže
a jde zopakovat (`retry()`). Skript pro technika: `manual_revert_script()`.

## Co bot sám neumí

**Musí udělat zákazník (u TV):**
1. Zapnout režim vývojáře a „Ladění USB“ (7× „Sestavení“). Na Mi TV někdy nejde bez přihlášení
   ke Google účtu / s rodičovským zámkem → pak jen ruční optimalizace.
2. Potvrdit na TV okno „Povolit ladění?“ (bez „Vždy povolit“) a po skončení případně „Odvolat autorizace ladění USB“.
3. Potvrdit instalaci Kodi v Google Play (tichá instalace není možná ani povolená).
4. V Kodi zapnout ovládání přes HTTP a nastavit heslo.
5. Přidat Kodi do Home Assistantu a zadat tam heslo (bot heslo nepřebírá). Pole „WebSocket port“ (9090) smazat.
6. Ruční nastavení, ke kterým není přístup: náhledy videí na domovské obrazovce, personalizace
   reklam Google, na Google TV „Režim jen aplikace“.
7. Při vrácení po úklidu znovu na chvíli zapnout ladění.

**Musí udělat admin Guardu:**
1. Schválit žádost o zápis (CustomerEntityMapping.IsWritable) pro `media_player.*` (androidtv)
   a případně `remote.*` (androidtv_remote) a `switch.*adguard*`, nebo žádost `config_flow:androidtv:<ip>`.
2. Implementovat `GuardApi` v MCP serveru podle docs/GuardApi.md. Hlavně `customer_message`
   (souhlas z reálné zprávy), `approval_status`, `approved_packages`, `notify_admin`, `port_status`
   a `check_dot`. Pak napojit playbook na chat.
3. Ověřit kandidáty `verified: false` (Xiaomi) přes `pm list packages` na skutečné TV a schválit je.
4. Opravit chybně zařazená zařízení (např. Mi TV vedená jako Tuya zásuvka).
5. Zabezpečený formulář v portálu pro hesla (dokud není, zákazník zadává heslo přímo v HA).
6. Odebrat v HA integraci `androidtv`, pokud ji zákazník už nechce (bot config entries nemaže).
7. Pravidla routeru a DHCP (rozdávání AdGuard Home jako DNS). Playbook je nenastavuje vůbec.
8. Řešit eskalace: otevřený přístup k TV po úklidu, selhané vrácení (skript pro technika).

**Nejde vůbec:** Kodi a optimalizace systému na Samsung Tizen a LG webOS; zrychlení bez ADB jinak
než ručně; odinstalace systémových aplikací (playbook jen vypíná).
