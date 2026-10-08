# Pilot intake — formulář zákazníka + prompt pro agenta

Z payloadu formuláře #pilot (staging, kvíz `JeanQuiz.estimate`) skládá:

1. **formulář zákazníka** — jen z dat, chybějící = `null` + položka v `otevrene` s kritičností,
2. **prompt pro Guard agenta** — formulář je jeho jediný vstup,
3. **audit** — kde payload odporuje kontraktu (plán, Kč, chybějící hidden pole).

```
python3 intake.py fixtures/fve_baterie.json [--plan-copy plan_copy.json]
python3 -m unittest -v test_intake
```

## Stav auditu: NEOVĚŘENO proti kódu

Kód kvízu ani reálný payload nebyly k dispozici:

- `staging.jufusi.us` je ze sandboxu blokovaný síťovou politikou (proxy 403),
- `JeanQuiz` není v `guard-ha-addons` ani `jufHome` (repo portálu/webu není připojené),
- v Gmailu není žádný Formspree submission z #pilot.

Fixtures jsou proto **sestavené z kontraktu**, ne ze stagingu (`_pozn` v každém souboru).
Názvy polí v payloadu (`fve`, `kwp`, `baterie_kwh`, `tarif`, `rozsah`, `kraj`,
`plan`, `odhad_kc_mes`, `jmeno`, `kontakt`, `obec`) jsou navržené — před nasazením
je třeba je namapovat na skutečné hidden pole formuláře.

### Checklist pro audit kódu (doplnit, až bude přístup)

| Pole | Odchází v payloadu? |
|---|---|
| plán, odhad | ? |
| 6 odpovědí kvízu (fve, kwp, baterie, tarif, rozsah, kraj) | ? |
| jméno, kontakt, obec | ? |
| FVE ano/ne | ? |
| `PLAN_COPY` textů plánu | ? (bez něj `co_chce` = null) |

Generátor chybějící pole detekuje sám (`audit: chybi_hidden_pole`), takže první reálný
payload díry ukáže.

## Kontrakt (generátor počítá podle něj, ne podle payloadu)

| Vstup | Plán | Kč |
|---|---|---|
| FVE ne / nevím | consult | null |
| FVE ano, bez baterie | start | null |
| FVE ano + baterie, rozsah `full` | full | odhad kvízu, zaokr. na 10 Kč, `orientacni` |
| FVE ano + baterie, jiný rozsah | opt | dtto |

- Kč přicházející u consult/start (např. 20 Kč) → zahozeno, `audit: odhad_zahozen`.
- kWp při FVE „nevím“ → zahozeno (`kwp_bez_fve`) — ochrana proti tichému defaultu 8 kWp.
- Generátor Kč nikdy nepočítá, jen přebírá odhad kvízu tam, kde to kontrakt dovoluje.
  Zda odhad zohledňuje rozsah (bojler/TČ/EV), lze ověřit jen v kódu kvízu.

**Předpoklady k potvrzení:** FVE „nevím“ → consult; opt vs. full rozhoduje jen `rozsah == full`.

## Kritičnost mezer

- `blokuje_hovor`: jméno, kontakt, plán
- `blokuje_odhad`: faktura, tarif (nevím), FVE (nevím), kWp, baterie, VT/NT časy
- `muze_pockat`: kraj, obec, rozsah, střídač, EAN, IČ, co_chce

Ukázkový výstup pro akceptační případ bez FVE a bez baterie: `ukazka_bez_fve.json`
(`odhad_kc_mes: null`, plán `start` → `consult`, 20 Kč zahozeno).
