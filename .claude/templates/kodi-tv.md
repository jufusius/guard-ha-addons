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

## Úklid (jen s potvrzením vlastníka/zákazníka)
- Osiřelé entity (`media_player.kodi_<ip>` bez config entry, `unavailable` dlouhodobě) a
  config entries na IP, které už neodpovídají.

## Poznámky pro portál (budoucí automatizace)
- Kroky 1–2 vždy potřebují člověka u TV → portál dá zákazníkovi návod se screenshoty a tlačítko
  „Hotovo, zkontrolovat“, které spustí kroky 3–4.
- Pokud TV nemá Play Store (Tizen/webOS), Kodi nejde → nabídnout `samsungtv` / `webostv` integraci.

## Historie
- 2026-11-26: založeno. cid=5: integrace `kodi` „Android-2“ (192.168.0.106) existuje, ale
  `media_player.android_2` je 30+ dní `off` → Kodi na TV neběží nebo nemá zapnuté HTTP ovládání.
  Další stará položka `kodi` 192.168.0.105 (IP offline) a osiřelá `media_player.kodi_192_168_0_106`.
