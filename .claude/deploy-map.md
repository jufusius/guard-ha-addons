# Deploy map — guard-ha-addons

## Co se nasazuje
- **Co:** Home Assistant addons (`guard-agent`, `guard-scanner`) jako Docker images. Distribuovány přes GitHub repo `jufusius/guard-ha-addons` (HA addon Repository pattern).
- **Kam:** **NE na náš server** — runtime běží na **každém zákazníkově HA hostu** (RPi, HA OS, Supervised, atd.). My deployujeme **release** (= bump verze + push tag).
- **Kdo:** HA Supervisor na cílovém RPi (stáhne image z GHCR / lokálně buildne, restartne container).
- **Dva paralelní lifecycle:**
  - **Repo release** = `git push` → zákazníci v HA udělají *Check for updates* → addon update.
  - **Backend kontrakt** = endpointy na MCP serveru (`/api/v2/devices`, `/api/agent/enroll`, ...). Změna API kontraktu vyžaduje souběžný McpHomeServer deploy.

## Cesty (pořadí preference)

### 1. Standardní release (bump + push, primární)
1. Edituj `<addon>/config.yaml` → `version: "X.Y.Z"` (semver bump).
2. Přidej řádek do `<addon>/CHANGELOG.md`.
3. `git add <addon>/config.yaml <addon>/CHANGELOG.md <addon>/...` + commit zpráva ve formátu `<addon> vX.Y.Z: <téma> — <one-line>`.
4. `git push origin main` + tag `<addon>-vX.Y.Z` (`git push origin <tag>`). **Výchozí větev je `main`, ne master** (`git push origin master` → „src refspec master does not match any").
5. Pro každého aktivního zákazníka:
   - Pokud má **auto-update zapnutý**: ~5-30 min se mu objeví notifikace v HA, sám aplikuje.
   - Pokud **manuální**: musí v HA jít *Settings → Add-ons → Guard <addon> → Update*.

Verify: viz "Verify příkazy". Pro vlastní HA (Roman) je nejjednodušší přes `guard-agent` MCP tool `restart_self_addon` po update notifikaci.

### 2. Hot-fix přes Guard Agent (jen na vlastní HA / debug)
Pro rychlou ad-hoc změnu **bez release** — overwrite souboru v běžícím containeru:
```
# přes MCP guard-agent tools (write_file + restart_self_addon)
# NEPOUŽÍVAT pro zákazníky, jen pro Roman/Jakub debug
```
Při příštím auto-pullu se to přepíše originálem z GHCR.

### 3. Lokální dev — `addon_devcontainer` (nebo `vscode-server` v HA)
Pro vývoj nového feature bez release cyklu:
- Mountni lokální repo do HA `/addons/local/guard-agent/`
- `Add-on Store → Local add-ons → Guard Agent → Install`
- Edituj kód, **Restart** addonu z HA UI = okamžitý reload (Python `run.sh` re-execne).
- Po dokončení → bump verze + push (cesta 1).

## Verify příkazy

- **Repo úroveň:**
  ```
  curl -s https://raw.githubusercontent.com/jufusius/guard-ha-addons/main/guard-agent/config.yaml | grep version
  ```
- **HA Supervisor úroveň** (na cílovém RPi, přes guard-agent endpoint):
  ```
  curl -s http://<HA_HOST>:8123/api/hassio/addons/<slug>/info \
    -H "Authorization: Bearer <SUPERVISOR_TOKEN>" | jq '.data.version, .data.version_latest'
  ```
  `version == version_latest` = update aplikován.
- **Backend ingest verify** (po release co změnil kontrakt):
  Sleduj MCP log `Services/AgentCommandService.cs` nebo `Services/TelemetryService.cs` — agent musí poslat heartbeat s novou verzí v `X-Agent-Version` headeru (TODO: header zatím není povinný, jen `X-Agent-Key`).
  Alternativně DB query:
  ```sql
  SELECT TOP 5 CustomerId, LastSeenUtc, AgentVersion FROM dbo.CustomerAutomations
    WHERE AgentVersion IS NOT NULL ORDER BY LastSeenUtc DESC
  ```
- **Per-feature** (změnila se telemetrie /v2/devices?): grep MCP log po novém polem v ingest payloadu.

## Failure modes

| Symptom | Diagnostika | Fix / fallback |
|---|---|---|
| Push proběhl, ale zákazníkům se update neukazuje | HA Supervisor checkuje repo ~hodina; manuální *Check for updates* v Add-on Store / Repositories | Vyžádej si u zákazníka *Reload* repository — okamžitě se mu zobrazí. Doc v `customer-onboarding-checklist.md`. |
| Addon update padne při `apk add` / `pip install` | Alpine package mirror down, nebo PyPI rate-limit | Bump build z `armv7` + zkus znovu; pro permanent fix přidat retry do `Dockerfile` `RUN apk update --quiet \|\| apk update`. |
| Addon startuje a okamžitě crashne | `run.sh` exit > 0, supervisor reportuje stopped | Logy v HA → Add-ons → Guard Agent → Log. Často: chybí env var (`API_KEY`, `MCP_URL`), špatný JSON v `options.json` → fix v config schema. |
| Změna API kontraktu (např. `/api/v2/devices` přidá required pole) → starý addon na zákazníkovi posílá nekompletní payload | MCP vrátí 400, agent retryuje, telemetrie dropy | **Vždy** addon update **NEJDŘÍV**, MCP backend **DRUHÝ** s tolerancí starého schématu (server-first stop bez tolerance = mass dropout všech zákazníků). Po týdnu zruš toleranci v MCP. Detail: pattern viz `feedback_telemetry_pipeline.md`. |
| `version` nezvednutá v `config.yaml`, ale kód změněn | HA Supervisor nepustí update (myslí že nic nového) | Bump aspoň patch (`1.7.0` → `1.7.1`). HA si DOCKER image cachuje podle SHA, ale Supervisor UI rozhoduje podle config.yaml version. |
| `arch:` v `config.yaml` neobsahuje cílovou platformu | Zákazníkův HW nedostane image | Defaultně držet všech 5 (`amd64, aarch64, armv7, armhf, i386`). Roman & Jakub = `aarch64` (RPi4). |
| Zákazník na starém HA OS (< 10.0) | Addon Store API drift | Min HA verze v `config.yaml: homeassistant: "2024.1.0"` (TODO: nastavit) |
| Push s otevřeným secrets v Dockerfile/run.sh | GitHub secret scanning email | Rotace tokenu okamžitě + amend commit nelze (public history) → force-push *jen* na master, vysvětlit zákazníkovi že je v repo a musí refetchnout. Pro Guard prevence: secrets jen v HA `options.json` (per-installation), nikdy v image. |
| `update_addon` přes guard-agent na sebe sama → HTTP 403 „App cea3d93e_guard-agent can't update itself!" | Supervisor zakazuje self-update addonu | Kanonicky: `agent-cmd.py <cid> refresh_store` (consent+reason), pak `guard-api.py POST /api/admin/customers/<cid>/ha-fleet/updates/install '{"entityId":"update.guard_agent_aktualizovat","backup":false,"reason":"…","consent":"…"}'` → job, sleduj `GET /api/admin/ha-fleet/jobs/<id>` (`verify.installed`). Read-back: `agent-cmd.py <cid> supervisor_get --payload '{"path":"addons/cea3d93e_guard-agent/info"}'` (version, state) + log `addons/cea3d93e_guard-agent/logs` („Guard Agent vX.Y.Z running", „Telemetry push … → True"). |
| Auto-update zákazníka spadl uprostřed | HA Supervisor partial state | `ha addons reload <slug>` přes SSH addon nebo `ha core restart` |

## Známá omezení
- **Nemáme telemetrii o úspěchu update** — zákazník může mít starou verzi a my to nevíme, dokud sám neřekne. TODO: poslat `X-Agent-Version` header v každém ingest requestu + endpoint `/api/admin/agent-versions` se přehledem.
- **Žádná staging/canary** — push = hned všichni zákazníci dostanou. Pro risky change: udělej release kandidát v privátní fork, nechej Roman přetestovat, pak teprve push do `jufusius/guard-ha-addons`.
- **Build je client-side** (HA Supervisor stahuje Dockerfile + buildne lokálně). Žádný GHCR push z naší strany; pomalejší update na pomalých RPi, ale nevyžaduje od nás registry infra.
- **Zákazníci s offline HA** (např. RPi za NAT bez UPnP cestou k internetu) update nedostanou — viz Cloudflare Tunnel onboarding (`project_onboarding_strategy.md`).

## Changelog mapy
- 2026-05-11: založeno (po S2 release `guard-agent v1.7.0` + `guard-scanner v1.2.0`).
- 2026-10-18: `guard-agent v1.9.1` (8702138, tag `guard-agent-v1.9.1`) — telemetrie bez pattern fallbacku pro namapované role + `sources`. cid=6: self-update 403 → fleet `updates/install` job 3e55badacd57 succeeded (09:01:16Z OVĚŘENO 1.9.1), addon started, push 09:01:36Z SoC 11,5 %. Push jde na `main`. Známé: „Charge servo not started: No module named 'regulators'" je od 1.8.1 (adresář `agent/regulators/` není v gitu), ne regrese 1.9.1.
