# Guard Agent

[![Verze](https://img.shields.io/badge/version-1.6.0-blue.svg)](CHANGELOG.md)
[![Architektury](https://img.shields.io/badge/arch-amd64%20%7C%20aarch64%20%7C%20armv7%20%7C%20armhf%20%7C%20i386-green.svg)](#)
[![Licence](https://img.shields.io/badge/license-MIT-lightgrey.svg)](#)

Vzdálený management agent pro [Guard IoT](https://guard.cz) platformu — zjednodušuje správu Home Assistant instalace, monitoring fotovoltaiky a tepelných čerpadel a integraci s Guard cloud službami.

## Co addon dělá

- 🔌 **Auto-enrollment** — po prvním startu se sám zaregistruje u Guard serveru, vygeneruje Long-Lived Access Token a pošle metadata HA instance (verze, hostname, timezone, install-type).
- 📡 **Telemetrie** — pravidelný strukturovaný push klíčových entit (FVE, baterie, distribuce, tepelné čerpadlo) na Guard server.
- 🔍 **Síťový scanner** — ARP scan, Tuya UDP discovery, ping sweep, TCP port probe.
- 📁 **Správa souborů** — čtení/zápis `configuration.yaml` a dalších konfiguračních souborů přes REST API.
- ⚙️ **Supervisor proxy** — instalace addonů, správa integrací, restart HA.
- 🔐 **Cloudflare tunnel ready** — REST API na portu 8300 vystavitelný přes Cloudflared addon (`guard.{tvoje-doména}`).

## Instalace

1. V Home Assistant otevři **Settings → Add-ons → Add-on Store**.
2. Klikni na ⋮ (tři tečky vpravo nahoře) → **Repositories**.
3. Vlož `https://github.com/jufusius/guard-ha-addons` a klikni **Add**.
4. Najdi **Guard Agent** v seznamu, klikni **Install**.
5. Po instalaci v záložce **Configuration** vyplň `api_key` (získáš v Guard portálu) a klikni **Save**.
6. Klikni **Start**, případně zapni **Watchdog** a **Auto update**.

## Konfigurace

| Parametr        | Typ    | Default                  | Popis                                                          |
|-----------------|--------|--------------------------|----------------------------------------------------------------|
| `api_key`       | string | _(povinné)_              | Guard API klíč — najdeš v profilu na [portal.jufusi.us](https://portal.jufusi.us). |
| `server_url`    | url    | `https://mcp.jufusi.us`  | URL Guard MCP serveru. Měň jen pokud máš vlastní instanci.     |
| `scan_interval` | int    | `30`                     | Interval síťového skenu v minutách (5–1440).                   |
| `tuya_scan`     | bool   | `true`                   | Detekce Tuya/Smart Life zařízení přes UDP broadcast.           |

## Co je nového v 1.6.0

**Bidirectional enrollment** — manuální kopírování HA tokenu z portálu už není potřeba. Po instalaci se agent sám zaregistruje, vygeneruje token (platný 10 let) a předá Guard serveru kompletní metadata HA instance.

Plný changelog: [CHANGELOG.md](CHANGELOG.md)

## Podpora

- **Web:** https://guard.cz
- **E-mail:** info@guard.cz
- **Issues:** https://github.com/jufusius/guard-ha-addons/issues

## Bezpečnost

Agent vyžaduje rozšířená oprávnění (`hassio_role: manager`, `homeassistant_config:rw`, `NET_RAW`) — jsou nezbytná pro vzdálenou správu, file edity a síťový scan. Veškerá komunikace s Guard serverem probíhá přes HTTPS, HA token je na serveru uložen šifrovaně (AES-GCM).

## Licence

MIT — see [repository root](https://github.com/jufusius/guard-ha-addons).
