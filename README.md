# Guard IoT — Home Assistant Add-ons

Oficiální repozitář Home Assistant addonů [Guard IoT](https://guard.cz) platformy pro správu fotovoltaiky, tepelných čerpadel a chytré domácnosti.

## Přidání repozitáře do Home Assistant

1. **Settings → Add-ons → Add-on Store**
2. Klikni na ⋮ vpravo nahoře → **Repositories**
3. Vlož URL a klikni **Add**:

   ```
   https://github.com/jufusius/guard-ha-addons
   ```

4. Po přidání se v Add-on Store objeví sekce **Guard IoT Add-ons**.

## Dostupné addony

| Addon | Popis | Verze |
|-------|-------|-------|
| **[Guard Agent](guard-agent/)** | Vzdálená správa HA, auto-enrollment, telemetrie FVE/TČ, file management. | [1.6.0](guard-agent/CHANGELOG.md) |
| **[Guard Network Scanner](guard-scanner/)** | Automatický síťový scan + Tuya/Smart Life discovery. | 1.1.2 |

## Co je Guard IoT

Guard je platforma pro automatizaci a optimalizaci domácí FVE — řídí baterii podle spotových cen, koordinuje tepelné čerpadlo, ohřev TUV, spirály bojleru a další zátěže. Skládá se ze tří částí:

- **MCP server** ([mcp.jufusi.us](https://mcp.jufusi.us)) — řídicí logika, výpočty, integrace dodavatelů
- **Portál** ([portal.jufusi.us](https://portal.jufusi.us)) — UI pro zákazníka (přehled, ovládání, reporty)
- **HA Add-ons** _(tento repozitář)_ — komunikační vrstva v zákazníkově HA instalaci

Více info: [guard.cz](https://guard.cz)

## Bezpečnost a podpora

- Issues a feature requests: [GitHub Issues](https://github.com/jufusius/guard-ha-addons/issues)
- Bezpečnostní hlášení: `info@guard.cz`
- Provozní podpora: viz portál

## Licence

MIT
