# Guard Network Scanner

Automaticky skenuje vaši domácí síť a reportuje nalezená zařízení na Guard IoT server.

## Co addon dělá

- Každých 30 minut (nastavitelné) prohledá vaši síť
- Najde všechna připojená zařízení (počítače, telefony, chytré zásuvky, žárovky...)
- Identifikuje Tuya/Smart Life zařízení pro automatický onboarding
- Odešle seznam na Guard server pro správu a monitoring

## Konfigurace

| Parametr | Popis | Výchozí |
|----------|-------|---------|
| **API key** | Váš Guard API klíč (najdete v Guard portálu) | povinné |
| **Server URL** | Adresa Guard serveru | https://mcp.jufusi.us |
| **Scan interval** | Interval skenování v minutách | 30 |
| **Tuya scan** | Hledat Tuya/Smart Life zařízení | zapnuto |

## Jak získat API klíč

1. Přihlaste se do Guard portálu
2. V sekci "Můj účet" najdete váš API klíč
3. Zkopírujte ho do konfigurace addonu

## Řešení problémů

**Addon se nespustí:**
- Zkontrolujte že je vyplněný API klíč

**Žádná zařízení:**
- Addon potřebuje přístup k síti (host_network je zapnutý automaticky)
- Zařízení musí být na stejné síti jako váš Home Assistant

**Tuya zařízení se nezobrazují:**
- Zařízení musí být spárované v Smart Life apce a připojené k WiFi
- Některá starší zařízení nemusí podporovat automatickou detekci
