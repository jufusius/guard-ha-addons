# Changelog

Všechny podstatné změny tohoto addonu jsou dokumentovány v tomto souboru.
Formát vychází z [Keep a Changelog](https://keepachangelog.com/cs/1.1.0/),
verzování dle [SemVer](https://semver.org/lang/cs/).

## [1.9.0] — 2026-07-04

### Fixed

- **`install_cloudflared` — discovery slugu místo hardcode (FIX a).** Default slug `a0d7b954_cloudflared` neexistoval (reálně `9074a9fa_cloudflared`, ale hash je závislý na repo). Nově se po přidání community repo + `store/reload` skutečný slug **discoveruje** z `store/addons` (slug končící `_cloudflared` nebo name/repository obsahující cloudflared/brenner). Explicitní `payload.addon_slug` má přednost jako override. `data_folder` už nehardcoduje slug (`addon_configs/{slug}`). Když discovery nic nenajde → `ok:False` + error (nehádá se hardcode).
- **`install_cloudflared` — verifikace instalace (FIX a).** Po install/options/restart se kontroluje `GET addons`, že discovered slug je mezi nainstalovanými a addon je started/running. Do návratu přidán `installed_verified`. Bez ověření se už nehlásí success naslepo.
- **Auto-inject `http:`/`trusted_proxies` do configuration.yaml (FIX b).** Cloudflared vždy přidává `X-Forwarded-For` → HA bez `use_x_forwarded_for`+`trusted_proxies` vrací HTTP 400. Nový helper `_ensure_http_proxy_config()` idempotentně přidá blok s hassio rozsahem `172.30.32.0/23`, dělá `.bak` zálohu, spustí `core/check` a při nevalidním configu ROLLBACKuje. HA Core restart NEdělá automaticky (vrací `ha_restart_required`), volitelně `auto_restart_ha` v payloadu. PyYAML se v addonu neinstaluje → běží konzervativní textová větev (existující `http:` blok needituje naslepo → `needs_manual`).
- **LLAT mint přes servisní účet (FIX c).** Původní REST mint `POST core/api/auth/long_lived_access_token` se SUPERVISOR_TOKENem nefungoval (systémový user "Supervisor" nesmí vlastnit LLAT). Nový helper `_mint_llat_via_service_account()` vytvoří dedikovaný admin účet "Guard" přes WebSocket (`config/auth/*`), přihlásí se přes `login_flow` a mintne LLAT jako reálný user. Idempotentní (přeskočí create pokud účet existuje), recovery backup hesla+LLAT do `/share/guard/ha-service-account.json` (0o600). Starý REST mint zůstává jako fallback.
- **`_supervisor_cmd` — mimetype + status propagace (FIX d).** `await resp.json()` padalo na non-JSON odpovědích (`core/check` vrací text, prázdné 200) a HTTP status se nepropagoval (4xx/5xx s JSON body vypadalo jako success). Nově `resp.json(content_type=None)` s fallbackem na `{"raw": text}` a status vždy pod klíčem `_status` (existující callery na `.get("data")`/`.get("result")` se nerozbíjí).

## [1.8.1] — 2026-06-29

### Added

- **Nativní command `list_directory`** — výpis adresáře v sandboxu `/homeassistant` (formát shodný s REST `handle_files_list`). Dřív bylo nutné obcházet přes `shell_exec ls`.
- **Command alias `restart`** — synonymum k `restart_ha` (restart HA Core přes Supervisor). Obě jména teď fungují.

### Fixed

- **Sjednocení verze** — `server.py` VERSION byl rozjetý na `1.7.0`, zatímco `config.yaml` byl `1.8.0`. Obojí teď `1.8.1`.

## [1.8.0] — 2026-05-14

### Improved

- **Command poll cadence zrychlen z 60s na 10s** — onboarding a interaktivní debugging už nečeká minutu na pickup. Provoz vyrostl ze 60 reqs/h na 360 reqs/h per addon (zanedbatelné).

## [1.7.0] — 2026-05-06

### Bezpečnost (S2)

**Přesun API klíče z URL do hlavičky pro agent command queue.**

Telemetrie + device scanner už používaly v2 endpointy s `X-Agent-Key` headerem od dřívějška. Tento release dokončuje migraci pro zbývající callsity:

- `GET /api/agent/{key}/commands` → `GET /api/v2/agent/commands` (X-Agent-Key)
- `POST /api/agent/{key}/result` → `POST /api/v2/agent/result` (X-Agent-Key)
- `POST /api/agent/{key}/enroll` → `POST /api/v2/agent/enroll` (X-Agent-Key)

API klíč se už nikdy neobjeví v URL path → mizí z access logů Cloudflare tunelu i Kestrel HTTP logu na MCP serveru.

Server (McpHomeServer) zachovává v1 routes pro backwards-compatibility do **2026-08-05** (Sunset header). Po tomto datu budou v1 routes odstraněny — všichni zákazníci by měli být na 1.7.0+.

## [1.6.1] — 2026-05-01

### Co je nového

**Auto-add Cloudflared repository** — onboarding na čerstvou HA OS instalaci už nevyžaduje ruční přidání community repa. Stačí nainstalovat Guard Agent a vše ostatní si zařídí.

### Přidáno

- `install_cloudflared` handler před instalací Cloudflared addonu nejprve ověří, že je community repo `brenner-tobias/ha-addons` přidané v Add-on Store. Pokud ne, přidá ho přes `POST /store/repositories`, zavolá `store/reload` a počká 8 s na propagaci.
- Payload `install_cloudflared` přijímá volitelné `repo_url` (default `https://github.com/brenner-tobias/ha-addons`) a `addon_slug` (default `a0d7b954_cloudflared`) — lze přepsat z MCP server commandu.

### Opraveno

- Eliminace `addon not found` chyby na čerstvé HA OS — předtím Cloudflared install padl pokud uživatel nepřidal repo manuálně.

### Idempotence

- Repo-add krok je no-op pokud je repo už přidané (kontrola přes `GET /store` a porovnání `source` URL).
- Fail-soft: pokud repo-add selže (síť, Supervisor problém), agent loguje warning a pokračuje na install — uživatel uvidí přesnou chybu místo timeoutu.

---

## [1.6.0] — 2026-05-01

### Co je nového

**Bidirectional enrollment** — agent se po instalaci sám zaregistruje na Guard serveru. Odpadá manuální kopírování Home Assistant tokenu z portálu.

### Přidáno

- **Auto-enrollment při startu** (a 1× za 7 dní jako refresh):
  - Přečte `external_url` / `internal_url`, verzi HA a timezone (`/core/api/config`)
  - Zjistí hostname (`/host/info`) a primární lokální IP (`/network/info`)
  - Detekuje typ instalace (HAOS / Supervised / Container)
  - Vygeneruje **Long-Lived Access Token** přes `POST /core/api/auth/long_lived_access_token`
    (platnost 3650 dní, klient `Guard Agent 1.6.0`)
  - Pošle vše šifrovaně na `POST {server_url}/api/agent/{api_key}/enroll`
- **Idempotence** — sentinel `/data/enrolled.json`, opakovaný start v rámci 7 dní enrollment přeskočí.
- **Hang-prevention** — vnější `asyncio.wait_for` 30 s, per-request HTTP timeout 20 s, fail-soft (chyba se zaloguje, agent normálně pokračuje). Enrollment nikdy neblokuje start telemetrie ani polling smyček.

### Server-side

Guard server uloží HA token zašifrovaný (AES-GCM) a vyplní `Customers.HaBaseUrl` / `HaToken` / `HaVersion` / `AgentInstallType` / `AgentVersion` / `AgentHostname` / `AgentLocalIp` / `AgentTimezone`. Po prvním startu jste plně provisioned bez dalších kroků.

---

## [1.5.0] — 2026-04-30

### Přidáno

- Command `install_cloudflared` — Sprint E auto-provisioning (HA addon Cloudflared installation přes Supervisor API).

### Opraveno

- Supervisor build (`build.yaml`) — chybějící base image mapping pro arm-architektury.

---

## [1.4.2] — 2026-04-29

### Opraveno

- aarch64 build — přechod z pip `pycryptodome` na apk `py3-pycryptodome` (rychlejší build, menší image).

---

## [1.4.1] — 2026-04-28

### Změněno

- Grid power se odvozuje z fyzikální bilance (`fve - house + battery_in - battery_out`), ne z kumulativních senzorů. Eliminuje drift a jitter na neúplných instalacích.

---

## [1.3.0] — 2026-04-25

### Přidáno

- Telemetrie čte explicitní `KeyEntitiesJson` ze serveru — per-customer mapping bez nutnosti hardcodu na klientovi.

---

## [1.2.0] — 2026-04-22

### Přidáno

- Strukturovaný telemetry push.
- Automatický mapping entit podle `domain` + `device_class`.

---

[1.6.1]: https://github.com/jufusius/guard-ha-addons/releases/tag/guard-agent-v1.6.1
[1.6.0]: https://github.com/jufusius/guard-ha-addons/releases/tag/guard-agent-v1.6.0
[1.5.0]: https://github.com/jufusius/guard-ha-addons/releases/tag/guard-agent-v1.5.0
[1.4.2]: https://github.com/jufusius/guard-ha-addons/releases/tag/guard-agent-v1.4.2
[1.4.1]: https://github.com/jufusius/guard-ha-addons/releases/tag/guard-agent-v1.4.1
[1.3.0]: https://github.com/jufusius/guard-ha-addons/releases/tag/guard-agent-v1.3.0
[1.2.0]: https://github.com/jufusius/guard-ha-addons/releases/tag/guard-agent-v1.2.0
