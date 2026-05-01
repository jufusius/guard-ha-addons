# Changelog

Všechny podstatné změny tohoto addonu jsou dokumentovány v tomto souboru.
Formát vychází z [Keep a Changelog](https://keepachangelog.com/cs/1.1.0/),
verzování dle [SemVer](https://semver.org/lang/cs/).

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
