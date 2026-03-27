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
