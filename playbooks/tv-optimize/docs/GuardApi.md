# Kontrakt `GuardApi` (playbook tv-optimize)

`TvPlaybook` (tvopt/engine.py) nesahá na síť ani na databázi. Všechno dělá přes objekt
implementující `GuardApi`. Implementaci dodává Guard MCP server. V testech ji nahrazuje
`FakeGuard` (tests/conftest.py).

## Obecná pravidla (platí pro každou metodu)

1. **Jen data daného `cid`.** Každá metoda dostane `cid` a vrací nebo mění jen data toho
   zákazníka. Když neexistuje, vrátí prázdný výsledek, nikdy data jiného zákazníka.
2. **Zápis jen přes write-gateway.** `adb` a `call_service` musí jít přes stejnou bránu jako
   ostatní zápisy Guardu. Brána znovu ověří `CustomerEntityMapping.IsWritable` pro (cid, entity).
   Engine to kontroluje taky, ale brána je poslední pojistka.
3. **Oprávnění se nikdy nepřidávají automaticky.** Jen admin v portálu na základě
   `request_write_approval`. Žádná metoda nesmí zapisovat do `CustomerEntityMapping`.
4. **Žádné skenování.** `port_status` a `check_dot` testují jen zadaný cíl (IP TV zákazníka,
   resp. jeden DNS hostname), nikdy rozsahy.
5. **Tajemství nikdy.** Žádná metoda nepřijímá ani nevrací hesla. Kodi heslo zákazník zadává přímo v HA.

## Metody

| Metoda | Návrat | Popis a oprávnění |
|---|---|---|
| `context(cid) -> dict` | `{"devices": [...], "states": [...], "integrations": [...]}` | `devices`: řádky CustomerDevices (`ip, mac, hostname, vendor, model, category`). `states`: HA stavy (`entity_id, state, attributes`). `integrations`: HA config entries (`domain, title, host, entry_id, entity_ids`). `entity_ids` musí pocházet z entity registry, ne z odhadu podle názvu. |
| `writable_entities(cid) -> set[str]` | entity s `IsWritable=1` | Čerstvé čtení. Engine ho volá před každým zápisem. |
| `request_write_approval(cid, target, reason) -> str` | id žádosti | Založí schvalovací požadavek v admin portálu. `target` je `entity_id` nebo `config_flow:androidtv:<ip>`. Nesmí nic povolit sama. |
| `approval_status(cid, ticket) -> str` | `pending` / `approved` / `denied` / `unknown` | Stav žádosti. `approved` jen po rozhodnutí admina. |
| `approved_packages(cid, profile_id) -> set[str]` | balíčky | Balíčky, které admin pro daný model ověřil přes `pm list packages` na skutečné TV a schválil k vypnutí. Prázdné = jen `verified: true` z konfigurace. |
| `customer_message(cid, conversation_id, message_id) -> dict \| None` | `{"id", "author", "text", "ts", "cid", "conversation_id"}` | Vrací zprávu z úložiště chatu. `author` = `customer` jen u zprávy, kterou zákazník skutečně poslal. Souhlas a „hotovo“ se ověřují výhradně tudy: LLM nemůže vyrobit souhlas, protože nezná platné id zákaznické zprávy s textem „ano“. `ts` je ISO 8601 UTC. |
| `notify_admin(cid, subject, detail) -> str` | id eskalace | Eskalace adminovi: chybné zařazení zařízení, otevřený přístup po úklidu, selhané vrácení. |
| `start_config_flow(cid, domain, data) -> dict` | výsledek flow | Engine volá jen `androidtv` s `{"host": <IP TV z context>, "device_class": "androidtv", "port": 5555}`. Volá ho jen s doložitelným schválením (zapisovatelná `remote.*` téže TV, nebo `approval_status == approved`). Implementace má ověřit, že `host` je IP zařízení zákazníka. |
| `adb(cid, entity_id, command) -> str` | výstup příkazu | `androidtv.adb_command` přes write-gateway. Při nedostupnosti TV vyhodí `tvopt.engine.AdbError`. |
| `call_service(cid, domain, service, data)` | — | HA služba přes write-gateway. Engine posílá jen `switch.turn_on/off` a `remote.turn_on` s `market://` URL. |
| `port_status(cid, ip, ports) -> dict[int, bool]` | `{5555: False, 9090: False}` | TCP test jen na IP TV zákazníka a jen porty 5555 a 9090. Neznámý výsledek nevynechávat, ale vrátit bez klíče. Engine to bere jako „nepodařilo se ověřit“. |
| `check_dot(cid, hostname) -> bool` | dostupnost | Ověří z HA hostu zákazníka, že DNS-over-TLS (853) na `hostname` funguje. Volá se před zapnutím veřejného AdGuard DNS i těsně před provedením. |
| `load(cid, key) / save(cid, key, value)` | JSON | Stav playbooku per cid (snapshot, změny, chráněné aplikace, rozpracovaný plán, použité id zpráv). |
| `audit(record)` | — | Zápis do auditního logu, viz níže. |

## Audit

Engine posílá už redigovaný záznam (`security.redact`). Hesla, tokeny a řídicí znaky jsou nahrazené.

```json
{
  "ts": "2026-11-26T12:00:00+00:00", "cid": 5, "actor": "guard-bot", "conversation_id": "…",
  "playbook": "tv-optimize",
  "device": {"ip": "192.0.2.14", "profile": "xiaomi-mitv", "entity": "media_player.…"},
  "module": "speed", "action": "adb", "command": "settings put global window_animation_scale 0.5",
  "result": "ok", "detail": null
}
```

Akce: `snapshot`, `adb`, `service`, `proposed`, `consent` (s `message_id` a `message_ts`), `declined`,
`consent_ambiguous`, `invalid_consent`, `public_dns_offered`, `public_dns_choice`, `check_dot`,
`verify_home`, `verify`, `port_check`, `escalate_access_open`, `request_write_approval`,
`start_config_flow_androidtv`, `notify_admin`, `completed`, `failed`, `policy_block`.

Implementace audit záznam nesmí doplnit o tajemství. Názvy aplikací od zákazníka jdou do auditu
až po `sanitize_app_name`. Odmítnuté názvy se zapisují jen jako počet.

## Rate limity

- Engine provede nejvýš 60 ADB příkazů v jednom běhu (`MAX_CALLS_PER_RUN`), pak skončí `PolicyError`.
- Implementace má navíc vynucovat limity na úrovni brány. Doporučení:
  - `adb`: 30 příkazů za minutu na cid,
  - `call_service`: 10 za minutu na cid,
  - `start_config_flow`: 3 za hodinu na cid,
  - `port_status` a `check_dot`: 10 za minutu na cid.
  Při překročení vyhodit výjimku. Engine ji nechá propadnout a bot zákazníkovi řekne, že to zkusí později.

## Chování při chybě

| Situace | Implementace | Engine |
|---|---|---|
| TV nedostupná / ladění vypnuté | `adb` vyhodí `AdbError` | ručně popsaný postup, plán zůstane rozpracovaný (`retry()`) |
| Entita není zapisovatelná | brána vrátí `unknown_entity` → vyhodit výjimku | `request_write_approval`, nic dalšího nezkouší |
| Zpráva neexistuje / není od zákazníka | `customer_message` vrátí `None` | souhlas odmítnut (`invalid_message`) |
| Port nejde ověřit | klíč v `port_status` chybí | `access_open` + `notify_admin`, žádné „hotovo“ |
| DoT nedostupné | `check_dot` vrátí `False` | veřejný DNS se nezapne |
| Vrácení selže | — | změny a snapshot zůstanou, `notify_admin`, skript pro technika v `Reply.data["script"]` |

## Testovací kontrakt

Testy v `tests/` jsou zároveň kontrakt implementace. Musí projít proti `FakeGuard` i proti reálné
implementaci nad testovacím zákazníkem a simulovanou TV:

- `test_oddeleni_zakazniku`: žádná akce u cizího cid.
- `test_entita_neni_zapisovatelna_pozada_o_schvaleni`, `test_odebrane_opravneni_mezi_navrhem_a_provedenim`.
- `test_souhlas_jen_z_realne_zpravy_zakaznika`: zpráva bota, cizí konverzace, starší zpráva, cizí cid, vymyšlené id.
- `test_jedna_zprava_jeden_souhlas_a_audit`.
- `test_config_flow_jen_se_schvalenim`.
- `test_port_9090_otevreny_eskaluje_a_neni_done`, `test_neznamy_stav_portu_neni_zavreno`.
- `test_selhani_vraceni_nic_nesmaze_a_jde_zopakovat`.
- `test_nazvy_aplikaci_od_zakaznika_se_ocisti`.

Reálná implementace se připojí tak, že se v `conftest.py` přidá fixture vracející ji místo
`FakeGuard`, např. podle proměnné prostředí `TVOPT_GUARD_IMPL`. Proti skutečné TV zákazníka se testy nikdy nepouštějí.
