# Šablona: Kodi na Android/Google TV → Home Assistant

> Stav: **INTERNÍ**. Ověřováno jen u vlastníka (cid=5). Do Guard portálu (zákaznické flow)
> se nasazuje až po výslovném potvrzení. Do té doby nic z toho nespouštět u zákazníků.

## K čemu
Kodi na TV jako `media_player` v HA: stav přehrávání (zapnuto/pauza/co běží), ovládání
(play/pause/stop/hlasitost), notifikace na TV (`kodi.notify`-like přes `notify`), automatizace
(např. ztlumit světla při přehrávání).

## Předpoklady (zjistit před začátkem)
| Co | Jak zjistit (Guard nástroje) | Pozn. |
|---|---|---|
| IP TV, ideálně pevná (DHCP rezervace) | `device_list(cid)` → hostname `Android*`, `MITV`, `*tv*` | Bez rezervace se IP mění → integrace „off“. |
| TV zapnutá a v síti | `ping_device(ip)` | |
| Existuje `androidtv_remote` integrace? | `ha_list_integrations(cid, "androidtv_remote")` | Umožní spustit Kodi / otevřít Play Store na TV. |
| Existující `kodi` záznamy | `ha_list_integrations(cid, "kodi")` | Pozor na staré/osiřelé záznamy (viz Úklid). |

## Kroky

### 1. Instalace Kodi na TV (vyžaduje člověka u TV)
- Otevřít Play Store stránku Kodi na TV přes `androidtv_remote`:
  `remote.turn_on` na `remote.<tv>` s `{"activity": "market://details?id=org.xbmc.kodi"}`.
- Zákazník/technik potvrdí **Instalovat** ovladačem. (Tichou instalaci HA neumí; ADB by vyžadovalo
  vývojářský režim na TV — pro zákazníky ne.)
- Ověření: `remote.turn_on` s `{"activity": "org.xbmc.kodi"}` → Kodi se spustí.

### 2. Zapnout vzdálené ovládání v Kodi (člověk u TV, jednorázově)
Kodi → Nastavení → Služby → Ovládání:
- **Povolit vzdálené ovládání přes HTTP** = zapnuto (port **8080**)
- Uživatel `kodi`, **heslo nastavit** (nenechávat prázdné)
- **Povolit vzdálené ovládání z aplikací na jiných systémech** = zapnuto (WebSocket **9090**)

### 3. Integrace do HA
`ha_config_flow_start(cid, "kodi")` → `ha_config_flow_submit`:
```json
{"host": "<ip>", "port": 8080, "ssl": false}
```
další krok (pokud vyžaduje přihlášení):
```json
{"username": "kodi", "password": "<heslo>"}
```
a WS port `9090`, pokud se flow zeptá. Heslo se **neukládá** do Guard DB ani do logů.

### 4. Ověření (read-back)
- `ha_get_states(cid, "media_player")` → nová entita `media_player.<název>` ve stavu `idle`/`playing`
  (ne `off`/`unavailable`), když Kodi běží.
- Pokud `off`: Kodi neběží, nebo HTTP ovládání vypnuté, nebo změněná IP.

## Optimalizace TV na vyžádání (input lag při načítání)

Spouští se **jen na výslovnou žádost** (Guard TODO #284). Cíl: rychlejší odezva ovladače
při načítání aplikací a launcheru.

### Předpoklad: ADB přes síť (jednorázově, člověk u TV)
1. TV: Nastavení → Předvolby zařízení → Informace → 7× kliknout na **Sestavení** → vývojářské možnosti.
2. Předvolby zařízení → Pro vývojáře → **Ladění USB** zapnout (na Mi TV funguje i po síti, port 5555).
   Když přepínač nejde zapnout (Mi TV): ověřit, že je TV přihlášená k Google účtu a není
   aktivní rodičovský zámek / omezený profil; jinak optimalizovat ručně (animace v Pro vývojáře,
   vypnutí aplikací v Nastavení → Aplikace) a ADB vynechat.
3. HA: `ha_config_flow_start(cid, "androidtv")` → `{"host": "<ip TV>", "port": 5555}` →
   na TV potvrdit **Povolit ladění z tohoto počítače (vždy)**.
4. Admin: povolit `media_player.<tv>` (androidtv) ve write-gateway whitelistu pro služby
   `androidtv.adb_command` a `remote.turn_on`. Bez toho brána vrací `unknown_entity`.

### 0. Snapshot (vždy před změnou, kvůli vrácení)
`androidtv.adb_command`, výstup uložit do Guardu k zákazníkovi:
- `settings list global`, `settings list system`, `settings list secure`
- `pm list packages -d` (už vypnuté balíčky)
- `dumpsys meminfo | head -40` (RAM před optimalizací)

### 1. Změny (každou zvlášť, s read-back)
| Krok | Příkaz | Vrácení |
|---|---|---|
| Kratší animace | `settings put global window_animation_scale 0.5` (+ `transition_animation_scale`, `animator_duration_scale`) | hodnota `1.0` |
| Limit procesů na pozadí | `settings put global background_process_limit 2` *(jen pokud TV moc swapuje; ne vždy dostupné)* | `settings delete global background_process_limit` |
| Vypnout nepoužívané předinstalované aplikace | `pm disable-user --user 0 <balíček>`, jen ze seznamu odsouhlaseného zákazníkem | `pm enable <balíček>` |
| Uvolnit úložiště / cache | `pm trim-caches 2G` | — |
| Zastavit těžké aplikace na pozadí | `am kill-all` | — |

Nikdy nevypínat: launcher, Play Store/Play služby, systémové aktualizace, Chromecast, nastavení,
klávesnici, `org.xbmc.kodi`. Seznam balíčků k vypnutí vždy navrhnout zákazníkovi a čekat na souhlas.

### 2. Ověření
- `dumpsys meminfo` po změnách, porovnat volnou RAM se snapshotem.
- Zákazník potvrdí subjektivní odezvu; při zhoršení vrátit podle tabulky.

## Úklid (jen s potvrzením vlastníka/zákazníka)
- Osiřelé entity (`media_player.kodi_<ip>` bez config entry, `unavailable` dlouhodobě) a
  config entries na IP, které už neodpovídají.

## Poznámky pro portál (budoucí automatizace)
- Kroky 1–2 vždy potřebují člověka u TV → portál dá zákazníkovi návod se screenshoty a tlačítko
  „Hotovo, zkontrolovat“, které spustí kroky 3–4.
- Pokud TV nemá Play Store (Tizen/webOS), Kodi nejde → nabídnout `samsungtv` / `webostv` integraci.

## Historie
- 2026-11-26: TV obývák = **192.168.0.114**, Wi-Fi MAC c0:e7:bf:f1:47:01 (v CustomerDevices id=15 chybně jako Tuya „MITV“ socket), eth MAC 6c:0d:c4:fc:f9:30. Kodi na :8080 neodpovídá (cannot_connect). Integrace kodi „Android-2“ (.106) je jiné zařízení. Ladění USB na TV nejde zapnout. Write-whitelist = `CustomerEntityMapping.IsWritable` (JufMCP SQL je read-only → nastavuje admin v portálu).
- 2026-11-26: TV = Xiaomi Mi TV MiTV-MOOQ0, Android TV 11. Spuštění Kodi přes `remote.googletv1594` zablokovala write-gateway (`unknown_entity`). Guard TODO #284.
- 2026-11-26: založeno. cid=5: integrace `kodi` „Android-2“ (192.168.0.106) existuje, ale
  `media_player.android_2` je 30+ dní `off` → Kodi na TV neběží nebo nemá zapnuté HTTP ovládání.
  Další stará položka `kodi` 192.168.0.105 (IP offline) a osiřelá `media_player.kodi_192_168_0_106`.
