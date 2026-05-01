# Guard Agent — dokumentace

Vzdálený management agent pro Guard IoT platformu. Tento dokument popisuje konfiguraci, REST API a řešení problémů. Quick-start a "What's new" najdeš v [README.md](README.md) a [CHANGELOG.md](CHANGELOG.md).

## Architektura

```
┌─────────────────────┐         HTTPS          ┌──────────────────────┐
│ Guard MCP server    │ ◀───── enrollment ────▶│ Guard Agent (8300)   │
│ mcp.jufusi.us       │ ◀───── telemetry ─────▶│ (HA addon)           │
│                     │ ─────── commands ─────▶│  ├── HA Core API     │
│                     │                        │  ├── Supervisor API  │
└─────────────────────┘                        │  ├── File system     │
                                               │  └── Network scanner │
                                               └──────────────────────┘
```

Agent běží jako standalone HA addon, vystavuje REST API na portu 8300 a zároveň pravidelně pollu Guard server pro nové příkazy. Pro vzdálený přístup z internetu je doporučená kombinace s [Cloudflared addon](https://github.com/brenner-tobias/ha-addons/tree/main/cloudflared).

## Konfigurace

| Parametr        | Typ    | Default                  | Popis                                                          |
|-----------------|--------|--------------------------|----------------------------------------------------------------|
| `api_key`       | string | _(povinné)_              | Guard API klíč. Najdeš v profilu na portal.jufusi.us.          |
| `server_url`    | url    | `https://mcp.jufusi.us`  | URL Guard MCP serveru. Měň jen pro vlastní instanci.           |
| `scan_interval` | int    | `30`                     | Interval síťového skenu v minutách (5–1440).                   |
| `tuya_scan`     | bool   | `true`                   | Detekce Tuya/Smart Life zařízení přes UDP broadcast.           |

## REST API

Autentizace: `Authorization: Bearer {api_key}` (stejný klíč jako v konfiguraci addonu).

### Health & info

| Endpoint                | Metoda | Popis                                |
|-------------------------|--------|--------------------------------------|
| `/api/health`           | GET    | Stav agenta + verze                  |
| `/api/info`             | GET    | HA verze, hostname, install-type, IP |

### Síťový scanner

| Endpoint                | Metoda | Popis                                                |
|-------------------------|--------|------------------------------------------------------|
| `/api/scan/full`        | GET    | Kompletní scan (ARP + Tuya + ping + TCP probe)       |
| `/api/scan/tuya`        | GET    | Pouze Tuya UDP discovery                             |
| `/api/scan/arp`         | GET    | Pouze ARP cache                                      |

### Souborový systém

| Endpoint                  | Metoda | Body / query                       | Popis                              |
|---------------------------|--------|------------------------------------|------------------------------------|
| `/api/files/read`         | GET    | `?path=configuration.yaml`         | Čtení souboru z `/homeassistant/`  |
| `/api/files/write`        | POST   | `{"path":"...","content":"..."}`   | Zápis souboru                      |
| `/api/files/list`         | GET    | `?path=.storage`                   | Listing adresáře                   |

### Shell

| Endpoint               | Metoda | Body                                      | Popis                            |
|------------------------|--------|-------------------------------------------|----------------------------------|
| `/api/shell/exec`      | POST   | `{"command":"...","timeout":30}`          | Spuštění shell příkazu           |

### Supervisor / HA Core proxy

| Endpoint                       | Metoda      | Popis                                           |
|--------------------------------|-------------|-------------------------------------------------|
| `/api/supervisor/{path}`       | GET / POST  | Proxy na Supervisor API (`/addons`, `/host/info`, …) |
| `/api/ha/{path}`               | GET / POST  | Proxy na HA Core API (`/states`, `/services/...`, …) |

### Telemetrie

| Endpoint                  | Metoda | Popis                                                  |
|---------------------------|--------|--------------------------------------------------------|
| `/api/telemetry/push`     | POST   | Vynucený push telemetrie na Guard server (debug)       |

## Cloudflare tunnel

Pro vzdálený přístup z mobilu nebo z venku LAN:

1. Nainstaluj [Cloudflared addon](https://github.com/brenner-tobias/ha-addons/tree/main/cloudflared).
2. V Cloudflare Zero Trust Dashboard přidej k tvému tunelu route:
   - **Subdomain:** `guard`
   - **Domain:** _(tvoje doména)_
   - **Service:** `http://localhost:8300`
3. Po pár minutách bude API dostupné na `https://guard.{tvoje-doména}/api/health`.

## Bezpečnost

Agent vyžaduje rozšířená oprávnění:

- `hassio_role: manager` — instalace addonů, restart HA
- `homeassistant_config:rw` — editace `configuration.yaml`, `.storage/*`
- `NET_RAW` + `NET_ADMIN` — raw sockets pro ARP/ping scan

Tato oprávnění jsou nezbytná pro vzdálenou správu. Veškerá komunikace s Guard serverem probíhá přes HTTPS. HA token vygenerovaný při enrollment je na serveru uložen šifrovaně (AES-GCM).

## Řešení problémů

**Addon se nespustí**
Zkontroluj v záložce **Log**:
- chybí `api_key` → vyplň v Configuration tabu
- `Cannot reach mcp.jufusi.us` → ověř internet a DNS na HA boxu

**Enrollment se nepodařil (1.6.0+)**
- Log: `Enrollment failed: ...` — chyba se loguje, ale agent normálně pokračuje (fail-soft).
- Re-trigger: smaž `/data/enrolled.json` v kontejneru a restartuj addon.
- Po 7 dnech od posledního úspěchu se enrollment spustí automaticky znovu.

**Telemetrie nepřichází na Guard server**
- Ověř `api_key` (špatný klíč → 401, viditelné v logu).
- V portálu zkontroluj, že je tvoje instalace přiřazena k tomu API klíči.

**Síťový scan nic nenajde**
- Tuya zařízení musí být v Smart Life apce a připojené k WiFi.
- ARP scan: pokud běžíš v Dockeru bez `host_network`, scan vidí jen Docker bridge — Tuya UDP funguje stejně, ARP ne. Doporučujeme HAOS / Supervised pro plný scan.

**Cloudflare tunnel routuje na port 8300, ale dostávám 502**
- `host_network` musí být **false** (default), jinak addon naváže port jen na host loopback.
- Cloudflared addon vidí `localhost:8300` jen pokud běží ve stejném Docker bridge.

## Podpora

- **Web:** https://guard.cz
- **E-mail:** info@guard.cz
- **Issues:** https://github.com/jufusius/guard-ha-addons/issues

## Licence

MIT — see [repository root](https://github.com/jufusius/guard-ha-addons).
