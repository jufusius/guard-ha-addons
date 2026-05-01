# Guard Agent

Remote management agent pro Guard IoT platformu. Umožňuje vzdálenou správu Home Assistant instalace přes Cloudflare tunnel.

## Funkce

- **Síťový scanner** — ARP scan, Tuya UDP discovery, ping sweep, TCP port probe
- **Správa souborů** — čtení/zápis configuration.yaml a dalších konfiguračních souborů
- **Shell příkazy** — vzdálené spouštění příkazů
- **Supervisor proxy** — instalace addonů, správa integrací
- **Telemetrie** — automatické odesílání dat na Guard server

## Konfigurace

- **API key** — váš Guard API klíč (z portálu portal.jufusi.us)
- **Server URL** — URL Guard serveru
- **Scan interval** — interval skenování sítě (minuty)

## Cloudflare tunnel

Pro vzdálený přístup přidejte v Cloudflare Zero Trust Dashboard route:
- Subdomain: `guard`
- Service: `http://localhost:8300`

## REST API

Port 8300, autentizace přes `Authorization: Bearer {api_key}`.

| Endpoint | Metoda | Popis |
|----------|--------|-------|
| `/api/health` | GET | Stav agenta |
| `/api/scan/full` | GET | Kompletní síťový scan |
| `/api/files/read?path=...` | GET | Čtení souboru |
| `/api/files/write` | POST | Zápis souboru |
| `/api/shell/exec` | POST | Spuštění příkazu |
| `/api/supervisor/{path}` | GET/POST | Supervisor API proxy |
| `/api/ha/{path}` | GET/POST | HA Core API proxy |
| `/api/telemetry/push` | POST | Odeslat telemetrii |

## Changelog

### 1.6.0 (2026-05-01) — Bidirectional enrollment

Při prvním startu (a 1× za 7 dní) se agent sám zaregistruje na MCP serveru:

1. Přečte `external_url` / `internal_url`, verzi HA a timezone přes `/core/api/config`
2. Zjistí hostname (`/host/info`) a primární lokální IP (`/network/info`)
3. Detekuje typ instalace (HAOS / Supervised / Container)
4. Vygeneruje **Long-Lived Access Token** přes `POST /core/api/auth/long_lived_access_token` (platnost 3650 dní, klient `Guard Agent 1.6.0`)
5. Pošle vše na `POST {server_url}/api/agent/{api_key}/enroll`

MCP si HA token uloží zašifrovaný (AES-GCM), vyplní `Customers.HaBaseUrl` / `HaToken` / `HaVersion` / `AgentInstallType` / `AgentVersion` / `AgentHostname` / `AgentLocalIp` / `AgentTimezone` a nastaví `AgentEnrolledAt`. Tím odpadá ruční kopírování HA tokenu z portálu.

**Idempotence:** sentinel `/data/enrolled.json` — opakovaný start v rámci 7 dní enrollment přeskočí; po 7 dnech se pošle znovu (refresh metadat).

**Hang-prevention:** vnější `asyncio.wait_for` 30 s, per-request HTTP timeout 20 s, fail-soft (chyba se zaloguje, agent normálně pokračuje). Enrollment nikdy neblokuje start telemetrie ani polling smyček.

### 1.5.0 (2026-04-30)
- Sprint E `install_cloudflared` command (T2 auto-provisioning)
- Fix supervisor build (`build.yaml`)

### 1.4.x
- 1.4.2 — fix aarch64 build (apk `py3-pycryptodome`)
- 1.4.1 — grid power odvozovat z fyzikální bilance, ne z kumulativních senzorů

### 1.3.0
- Telemetrie čte explicitní `KeyEntitiesJson` ze serveru

### 1.2.0
- Strukturovaný telemetry push + automatický mapping entit
