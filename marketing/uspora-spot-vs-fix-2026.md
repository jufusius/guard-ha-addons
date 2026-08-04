# Úspora spot vs. fix — datový podklad pro marketing

**Zpracováno:** 2026-08-04
**Zdroj měřených dat:** Guard MCP, `data_energy_stats` (tabulka HourlyData)
**Status:** podklad pro banner. Čísla označená ODHAD nejsou ověřená a nesmí jít do inzerce bez doložení.

---

## 0. Dvě věci, které mění celý výpočet

### 0.1 Ceny v naší databázi NEOBSAHUJÍ distribuci

Ověřeno přes `sensor.current_market_price_czk_kwh`, 2026-08-02:

| Hodina (UTC) | Cena Kč/kWh |
|---|---|
| 09:00 | 0,02 |
| 11:00 | 0,03 |
| 12:00 | 0,06 |

Distribuce sama o sobě je ~2 Kč/kWh. Cena 0,02 Kč/kWh je tedy **čistá komodita (spot)**,
bez distribuce, bez systémových služeb, bez daně, pravděpodobně bez DPH a bez marže dodavatele.

Křížová kontrola: `ec0_min_soc` pro 2026-08-04 09:00 hlásí `price_czk = 3.55`, sensor pro
2026-08-04 07:00 UTC hlásí 3,55. Sedí (posun UTC→Praha = +2 h). Optimalizátor tedy počítá
se stejným komoditním základem.

**Důsledek:** každý údaj „Bilance: +1231 CZK (zisk)" z `data_energy_stats` je
komoditní bilance, ne faktura. Reálná faktura je o distribuci vyšší. Tohle číslo se
nesmí v žádné podobě dostat do marketingu jako „zákazník vydělal".

### 0.2 Chybí zima

Data u obou zákazníků začínají koncem února. Chybí říjen–únor, tedy měsíce s nejvyšším
importem a nejdražším spotem. **Jakákoli roční extrapolace z těchto dat je nadhodnocená.**

---

## 1. Měřená data (nic dopočítaného)

### Roman — cid 5, Majetín, SolaX X3-Hybrid G4, 9,86 kWp, 11,6 kWh baterie
Tarif: spot. `adaptive_learner_enabled = true`.

Okno 2026-01-01 → 2026-08-04, pokrytí **172 dnů / 3 923 hodin** (z 215 dnů okna):

| Veličina | Hodnota |
|---|---|
| FVE výroba | 6 261,5 kWh |
| Spotřeba domu | 5 458,6 kWh (z toho spirály 690,9) |
| Nákup ze sítě | 900,0 kWh za 1 734 Kč — ⌀ **1,93 Kč/kWh** |
| Prodej do sítě | 1 621,5 kWh za 2 965 Kč — ⌀ **1,83 Kč/kWh** |
| Soběstačnost | **84 %** |
| ⌀ teplota | 14,1 °C |

Dílčí okna téhož zákazníka:

| Okno | Dnů | Nákup | ⌀ nákup | Prodej | ⌀ prodej | ⌀ teplota |
|---|---|---|---|---|---|---|
| březen 2026 | 32 | 157,2 kWh / 584 Kč | 3,72 Kč/kWh | 215,2 kWh / 341 Kč | 1,59 Kč/kWh | 7,3 °C |
| 28.7.–4.8. | 8 | 15,2 kWh / 82 Kč | 5,36 Kč/kWh | 127,4 kWh / 219 Kč | 1,72 Kč/kWh | 25,2 °C |

Pozn.: v létě je vážená nákupní cena **vyšší** (5,36) než v březnu (3,72) — v létě se
nakupuje skoro výhradně ve večerní špičce. Objem je ale malý (15 kWh za 8 dnů).

### Mirek Kratochvíl — cid 6, Trutnov, RCT Power PS 10.0, 10 kWp, 11,5 kWh baterie
Tarif: spot. `adaptive_learner_enabled = false`. Tepelné čerpadlo Nibe.

Okno 2026-01-01 → 2026-08-04, pokrytí **142 dnů / 3 354 hodin**:

| Veličina | Hodnota |
|---|---|
| FVE výroba | 3 408,4 kWh |
| Spotřeba domu | 1 679,5 kWh |
| Nákup ze sítě | 411,5 kWh za 1 688 Kč — ⌀ **4,10 Kč/kWh** |
| Prodej do sítě | 2 027,7 kWh za 3 978 Kč — ⌀ **1,96 Kč/kWh** |
| Soběstačnost | **75 %** |
| ⌀ teplota | 16,5 °C |

### Kontrola konzistence

| | Vlastní spotřeba z FVE | + Export | = | Výroba | Rozdíl |
|---|---|---|---|---|---|
| Roman | 4 558,6 | 1 621,5 | 6 180,1 | 6 261,5 | 81,4 kWh (1,3 %) |
| Mirek | 1 268,0 | 2 027,7 | 3 295,7 | 3 408,4 | 112,7 kWh (3,3 %) |

U Mirka 3,3 % odpovídá ztrátám v baterii. U Romana 1,3 % je na systém s 11,6 kWh baterií
nízké — buď jsou ztráty už započtené v „spotřebě domu", nebo je odvození cyklické.
**Nutno ověřit ve zdroji, než se z toho počítá účinnost.**

---

## 2. Nákladový model — všechny složky

### Regulované složky 2026, ČEZ Distribuce

| Složka | Kč/kWh | Zdroj | Jistota |
|---|---|---|---|
| Distribuce D02d | 2,079 (2 078,58 Kč/MWh) | kalkulator.cz, ERÚ CR 2026 | není jasné, zda vč. DPH |
| Systémové služby | 0,199 (198,73 Kč/MWh vč. DPH) | ceníky dodavatelů 2026 | dobrá |
| POZE | **0,00** — od 2026 hradí stát | ERÚ | dobrá (−599 Kč/MWh vs. 2025) |
| Daň z elektřiny | 0,028 (28,30 Kč/MWh) | z hlavy, NEOVĚŘENO | **ODHAD** |
| **Součet variabilní** | **≈ 2,31 Kč/kWh** | | ODHAD |

Stálý plat za jistič: nejmenší kategorie u ČEZ 2026 = **102 Kč/měs**. Domácnost s TČ
a jističem 3×25 A: nárůst o 18 %, tj. ~1 600 Kč/rok navíc oproti 2025. Konkrétní sazbu
pro 3×25 A se mi nepodařilo z veřejných zdrojů vytáhnout (většina českých webů vrací
HTTP 403). V modelu níže počítám **250 Kč/měs — ODHAD**.

### Komoditní ceny 2026

| Varianta | Kč/kWh | Zdroj |
|---|---|---|
| Fix — E.ON Variant, D02d, 2letá fixace, vč. DPH | 3,19 (3 186 Kč/MWh) | e15.cz, srovnání 2026 |
| Fix — nejlepší produkty na trhu | ~3,00 | e15.cz |
| Spot — prostý průměr duben 2026, bez DPH | 2,24 (2 235 Kč/MWh) | usetreno.cz |
| Spot — Roman, vážený skutečnou spotřebou | **1,93** | naše měření |
| Spot — Mirek, vážený skutečnou spotřebou | **4,10** | naše měření |

Výkupní cena u fixních dodavatelů: v modelu **1,00 Kč/kWh — ODHAD**. Nemám žádnou
reálnou smlouvu. Tohle je nejcitlivější vstup celého srovnání, viz sekce 4.

---

## 3. Plné srovnání spot+Guard vs. fix

### Roman, 172 dnů (≈ 5,7 měsíce)

| Položka | SPOT + Guard | FIX | Zdroj |
|---|---|---|---|
| Komodita — nákup | 1 734 Kč | 2 871 Kč (900 × 3,19) | měřeno / trh |
| Distribuce D02d (900 × 2,079) | 1 871 Kč | 1 871 Kč | ODHAD |
| Systémové služby (900 × 0,199) | 179 Kč | 179 Kč | ODHAD |
| Daň z elektřiny (900 × 0,028) | 25 Kč | 25 Kč | ODHAD |
| POZE | 0 Kč | 0 Kč | 2026 hradí stát |
| Stálý plat (5,7 × 250) | 1 425 Kč | 1 425 Kč | ODHAD |
| Výnos z prodeje | −2 965 Kč | −1 622 Kč (1 621,5 × 1,00) | měřeno / ODHAD |
| **Celkem** | **2 269 Kč** | **4 749 Kč** | |

**Rozdíl: 2 480 Kč za 172 dnů ve prospěch spotu.** Rozklad:
- levnější nákup: 900 × (3,19 − 1,93) = **1 134 Kč**
- lepší výkup: 1 621,5 × (1,83 − 1,00) = **1 346 Kč**

### Mirek, 142 dnů (≈ 4,7 měsíce)

| Položka | SPOT + Guard | FIX | Zdroj |
|---|---|---|---|
| Komodita — nákup | 1 688 Kč | 1 313 Kč (411,5 × 3,19) | měřeno / trh |
| Distribuce + SS + daň (411,5 kWh × 2,31) | 951 Kč | 951 Kč | ODHAD |
| Stálý plat (4,7 × 250) | 1 175 Kč | 1 175 Kč | ODHAD |
| Výnos z prodeje | −3 978 Kč | −2 028 Kč | měřeno / ODHAD |
| **Celkem** | **−164 Kč** | **1 411 Kč** | |

**Rozdíl: 1 575 Kč za 142 dnů ve prospěch spotu.** Rozklad:
- nákup: 411,5 × (3,19 − 4,10) = **−375 Kč — spot je u něj DRAŽŠÍ než fix**
- výkup: 2 027,7 × (1,96 − 1,00) = **+1 947 Kč**

**U Mirka je celá výhoda spotu ve výkupu, ne v nákupu. Jeho nákupní optimalizace
prodělává.** Má `adaptive_learner_enabled = false`. To je konkrétní produktová akce,
ne marketingové sdělení.

---

## 4. Co tato čísla NEDOKAZUJÍ

1. **Rozdíl 1,93 vs. 4,10 není důkaz účinnosti optimalizátoru.** Roman a Mirek se liší
   spotřebou (31,7 vs. 11,8 kWh/den), lokalitou, střídačem, TČ, sezónním pokrytím dat
   i velikostí baterie. Jsou to dva domy, ne A/B test.
2. **Nemáme kontrolní skupinu.** Nikde neběží stejný dům bez Guardu. Bez toho je
   jakékoli „Guard vám ušetří X %" nedoložitelné.
3. **Výkupní cena u fixu (1,00 Kč/kWh) je odhad.** Přitom nese 54 % Romanovy a
   124 % Mirkovy vypočtené výhody. Když je reálná výkupní cena u fixu 1,50, výhoda
   Romana spadne na 1 669 Kč a Mirka na 602 Kč. Když je 2,00, Mirek je na −360 Kč,
   tedy fix vyhrává.
4. **Chybí zima.** Viz 0.2.

---

## 5. Ekonomika produktu — co z toho plyne pro cenu

Z `guard-financni-plan-2026.md`: plány Basic 499 / Standard 999 / Premium 1999 Kč/měs,
předpokládaný ⌀ MRR 700 Kč.

Romanova vypočtená výhoda spotu za 172 dnů: **2 480 Kč**.
Guard Basic za stejné období: 5,7 × 499 = **2 844 Kč**. Standard: 5,7 × 999 = **5 694 Kč**.

**Na samotném rozdílu spot vs. fix se Guard nezaplatí ani v nejlevnějším tarifu.**
To není chyba výpočtu, je to důsledek toho, že zákazník se 84% soběstačností nakupuje
jen ~1,9 MWh/rok. Na tom objemu prostě není co ušetřit.

### Kde peníze reálně jsou

Hodnota samotné FVE (vlastní spotřeba × ušetřená plná cena 5,50 Kč/kWh + tržby z prodeje):

| | Vlastní spotřeba | Ušetřeno | + Prodej | = Celkem | Období |
|---|---|---|---|---|---|
| Roman | 4 558,6 kWh | 25 072 Kč | 2 965 Kč | **28 037 Kč** | 172 dnů |
| Mirek | 1 268,0 kWh | 6 974 Kč | 3 978 Kč | **10 952 Kč** | 142 dnů |

**Poměr: FVE dělá desetitisíce, volba tarifu tisíce.** Řádový rozdíl.

Marketingový důsledek: prodávat Guard jako „ušetříme vám na spotu" je slabé a při
poctivém výpočtu neobhajitelné. Obhajitelná je pozice **„dostaneme z vaší FVE maximum,
které už máte zaplacené"** — tedy zvyšování soběstačnosti a řízení tepla, ne arbitráž
na 1,9 MWh.

---

## 6. Co potřebuju doplnit, aby banner unesl tvrdší tvrzení

| Co | Proč | Kde to vzít |
|---|---|---|
| Reálná faktura 1–2 zákazníků | jediné ověření celého nákladového modelu | od zákazníka |
| Distribuční sazba a jistič per EAN | dnes ODHAD 250 Kč/měs, může být 2× vedle | Mirek má EAN 859182400708245487 |
| Výkupní cena ve fixní smlouvě | nese >50 % celé vypočtené výhody | konkurenční ceníky |
| Data za říjen–únor | bez zimy nelze říct roční číslo | čekat, nebo backfill |
| Nákupní cena vs. fix u každého zákazníka měsíčně | odhalí, u koho spot prodělává (jako Mirek) | nový report v portálu |
| Zapnout `adaptive_learner` u Mirka a měřit | jediná cesta k doložitelnému „Guard efekt" | produkt |
| CAPEX FVE u zákazníků | bez toho nelze počítat návratnost | onboarding formulář |

---

## 7. Použitelné zdroje

- ERÚ — cenová rozhodnutí 2026: https://eru.gov.cz/eru-vydal-cenove-vymery-kterymi-stanovi-regulovane-ceny-elektriny-plynu-na-rok-2026-0
- Distribuční poplatky 2026 (kalkulator.cz): https://www.kalkulator.cz/clanky/682/distribucni-poplatky-2026
- Srovnání dodavatelů 2026 (e15.cz): https://www.e15.cz/ceny-elektriny-2026-srovnani-dodavatelu
- Regulované ceny 2026 (usetreno.cz): https://www.usetreno.cz/regulovane-ceny-elektriny-2023/
- Ceny za distribuci 2026 (penize.cz): https://www.penize.cz/vlastnictvi-nemovitosti/482022-ceny-za-distribuci-elektriny-v-roce-2026-vzrostou-spocitejte-si-o-kolik-zaplatite-vic
