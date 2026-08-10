# energyAccounting — závazná specifikace „jedné pravdy"

**Účel:** jediný modul, ze kterého Přehled, Statistiky KPI, rozpad „Den", Reporty
i Jean čtou hotová čísla. UI nepočítá nic. Max. odchylka mezi obrazovkami: 0,1
(zaokrouhlení), protože všechny čtou tentýž výsledek.

**Stack-agnostic:** vzorce a sémantika. Cílový soubor v portálu
(`lib/energyAccounting.ts` nebo ekvivalent v .NET) je port této specifikace.

---

## 1. Datový typ intervalu

Jeden záznam na interval (1 h dnes, připraveno na 15 min):

| Pole | Jednotka | Zdroj (priorita) | Klasifikace |
|---|---|---|---|
| `t_start`, `t_end` | ISO | — | — |
| `e_prod` | kWh | střídač | MEASURED |
| `e_import` | kWh | střídač/elektroměr | MEASURED |
| `e_export` | kWh | střídač/elektroměr | MEASURED |
| `e_load` | kWh | měřeno; jinak `prod − export + import ± bat` | MEASURED / DERIVED |
| `e_charge`, `e_discharge` | kWh | BMS, je-li | MEASURED / MISSING |
| `price_import` | Kč/kWh vč. DPH | spot(t)+marže / VT-NT dle pásma / fix | MEASURED / ESTIMATE |
| `price_export` | Kč/kWh | smlouva; spot(t) u spot výkupu | MEASURED / ESTIMATE |

Klasifikace **se propaguje**: metrika spočtená z ESTIMATE vstupu je ESTIMATE,
z MISSING vstupu je MISSING. MISSING se renderuje „—", nikdy 0, nikdy se
nesčítá do jistých Kč (jistý součet dostane sufix „+", viz §4).

## 2. Ceny — jediný resolver

```
resolvePrices(interval, customerTariff) -> {price_import, price_export, class}
```

- **spot:** `spot_h + marže_dodavatele + REG` pro import; `spot_h − poplatek` pro export.
  `REG` = distribuce VT/NT dle pásma + systémové služby + daň, z karty zákazníka.
  Dokud karta nemá reálné sazby, `REG` je ESTIMATE (dnes 2,306 Kč/kWh, ERÚ 2026 ČEZ).
- **VT/NT:** cena dle pásma v intervalu (kalendář HDO/sazby), plná koncová.
- **fix:** konstanta z karty, plná koncová.
- **Stálé platby (jistič, měsíční plat)** NIKDY nevstupují do „ušetřeno".
  Patří jen do řádku „zaplaceno za elektřinu" (§4).

Zákaz: self-consumption se nikdy neoceňuje „průměrnou" cenou, pokud je známá
cena v intervalu. Vždy `Σ po intervalech`, nikdy `Σ kWh × ⌀ cena`.

## 3. Metriky (vše `Σ` přes intervaly zvoleného období)

```
selfConsumption   = Σ max(0, e_prod − e_export − e_charge_z_FVE)      [kWh]
importCost        = Σ e_import × price_import(t)                       [Kč]
exportRevenue     = Σ e_export × price_export(t)                       [Kč]
selfConsValue     = Σ selfCons(t) × price_import(t)   ← PLNÁ cena vč. REG
savedTotal        = selfConsValue + exportRevenue                      [Kč]
paidTotal         = importCost + stálé platby za období                [Kč]
selfSufficiency   = Σ selfCons / Σ e_load                              [%]
```

Baterie (jsou-li data): `η_rt = Σ e_discharge / Σ e_charge` klouzavě za 30 dní
z měření, ne z datasheetu; datasheet jen jako ESTIMATE fallback. Energie
vybitá z baterie nabité ze sítě se oceňuje `price_import(t_nabíjení) / η_rt`,
ne cenou v hodině vybití.

Soběstačnost: interval s `e_prod == null ∧ e_load == null` se z čitatele
i jmenovatele VYNECHÁVÁ (viz Jakubův duben, §6/V4 — díra nesmí ředit průměr).

## 4. Výstupní kontrakt pro UI (všechny obrazovky čtou tohle)

```
{
  period, coverage: {days_total, days_with_data, hours},
  savedTotal:        {value, class, breakdown: {selfConsValue, exportRevenue}},
  timingSavings:     {value | null, class: MISSING_dokud_není_deník},   // §5
  inducedLoadValue:  {value | null, class: MISSING_dokud_není_palivo},  // §5
  paidTotal:         {value, class, breakdown: {importCost, fixedFees}},
  selfSufficiency:   {value, class},
  kwh: {prod, load, import, export, selfCons}
}
```

Render pravidlo: `class == MISSING` → „—" + tooltip proč; součet s MISSING
členem dostává sufix „+" („6 134 Kč +").

## 5. Co zatím NEUMÍME a modul to musí přiznat

- `timingSavings` (posunutá zátěž): vyžaduje deník rozhodnutí s baseline cenou
  (rozšíření SpiralyLog). Do té doby MISSING.
- `inducedLoadValue` (spirály→nádrž): vyžaduje pole „náhradní palivo + cena"
  v onboardingu. Vzorec: `Σ e_spiral × (cena_paliva − price_import(t) − ušlý výkup(t))`.
  Smí vyjít záporně. Do té doby MISSING.

## 6. Testovací vektory (MĚŘENO, cid 5, ověřeno proti stagingu 4. 8. 2026)

Každá implementace MUSÍ reprodukovat (tolerance ±0,5 % na kWh, ±1 Kč na
přesných shodách):

| # | Období | Vstup/metrika | Očekáváno |
|---|---|---|---|
| V1 | červenec 2026 | importCost | **446 Kč** (přesná shoda se stagingem) |
| V2 | červenec 2026 | exportRevenue | **639 Kč** (přesná shoda) |
| V3 | červenec 2026 | selfConsValue = staré „ušetřeno" 4 247 − 639 + selfCons×REG | **≈ 5 914 Kč**; savedTotal ≈ **6 553 Kč** |
| V4 | únor–červenec | savedTotal (nový) vs. staré app „ušetřeno" 17 507 | **28 162 Kč** (+61 %) |
| V5 | duben 2026 | vážená nákupní cena importu | **−0,31 Kč/kWh** (záporná — test, že se neořezává na 0) |
| V6 | květen 2026 | vážená nákupní cena | **−0,82 Kč/kWh** |
| V7 | cid 2, celé období | selfSufficiency s vynecháním děr (duben bez prod/load) | **≈ 62 %**, ne 42 % |
| V8 | libovolný | interval s price=null | metrika class=MISSING, ne 0 |

Pozn. V3: REG=2,306 je ESTIMATE — vektor testuje strukturu výpočtu, po dosazení
reálných sazeb z karty zákazníka se očekávané hodnoty přegenerují.

## 7. Migrace obrazovek

1. Modul + testy V1–V8 nad HourlyData.
2. Reporty „Dnešní souhrn" → čtou modul (dnes už metodicky správně, jen komodita).
3. Statistiky KPI a rozpad „Den" → smazat lokální výpočty, číst modul.
4. Přehled → dtto.
5. Jean věty → generují se z výstupního kontraktu, žádná vlastní aritmetika.
Po každém kroku diff proti stagingu: V1/V2 musí zůstat přesné.
