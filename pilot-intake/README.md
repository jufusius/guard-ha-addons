# Pilot intake — formulář zákazníka + prompt pro agenta

Z řádku `dbo.Leads` (pilot / kvíz FveSmart `#uspora`) skládá:

1. **formulář zákazníka** — jen z dat, chybějící = `null` + položka v `otevrene` s kritičností,
2. **prompt pro Guard agenta** — formulář je jeho jediný vstup,
3. **audit** — díry v `DetailsJson` a rozpory s kontraktem estimate.

```
python3 intake.py fixtures/fve_baterie.json [--plan-copy plan_copy.json]
python3 -m unittest -v test_intake
```

## Odkud data chodí

- Kvíz: FveSmart, sekce `#uspora` (markup `Content__LandingPage.liquid:98`, logika `fvesmart-app.js`).
- Pilot i kvíz: `POST https://mcp.jufusi.us/api/public/leads` (`fvesmart-app.js:1737`) → `dbo.Leads`.
- Sloupce: `Name` (povinné), `Email`, `Phone`, `Source`, `Page`, `DetailsJson` + souhlasy a stav.
  Plán, odhad ani odpovědi kvízu sloupce nemají — žijí jen v `DetailsJson`.
- Foto faktury: Formspree `xeereava`, mimo `dbo.Leads`, nepárováno. Ve formuláři zůstává
  jako otevřená položka `faktura` (blokuje odhad) s poznámkou o kanálu.

## Kontrakt `DetailsJson`

```json
{
  "plan": "start|opt|full|consult",
  "odhadKcMes": null,
  "fve": "ano|ne|nevim",
  "kwp": null,
  "baterieKwh": null,
  "tarif": "spot|vtnt|nevim",
  "rozsah": "start|bojler|tc|ev|full",
  "kraj": null,
  "quiz": { "q1": "", "q2": "", "q3": "", "q4": "", "q5": "", "q6": "" }
}
```

- Chybějící klíč → `audit: chybi_hidden_pole` (pod názvem z `DetailsJson`), hodnota null,
  plán podle kontraktu (bez `fve` = consult), částka null.
- Chybějící `quiz` nebo prázdná otázka (`quiz.q6`) je díra. **Server je nesmí tiše zahodit** —
  dnes `/api/public/leads` validuje jen `name`; požadavek na MCP: odmítnout nebo označit lead
  bez `quiz`, ne ho přijmout s 202 bez odpovědí.
- Mapování názvů je v `DETAILS_KEYS` / `QUIZ_KEY` v `intake.py`. Až bude k dispozici tělo
  requestu z `fvesmart-app.js`, při nesouladu se opravuje **generátor na skutečný klíč**, ne web.

## Kontrakt estimate (platí, i když kód kvízu počítá jinak)

| Vstup | Plán | `odhad_kc_mes` |
|---|---|---|
| `fve` = ne / nevím / chybí | consult | null |
| `fve` = ano, `baterieKwh` = 0 / null | start | null |
| `fve` = ano, `baterieKwh` > 0, `rozsah` = full | full | odhad kvízu zaokr. na 10 Kč, `orientacni` |
| `fve` = ano, `baterieKwh` > 0, jiný rozsah | opt | dtto |

- Kč u consult/start (např. 20 Kč bez baterie) → zahozeno, `audit: odhad_zahozen`.
- `kwp` při FVE „nevím“ → zahozeno (`kwp_bez_fve`) — ochrana proti tichému defaultu 8 kWp.
- Generátor Kč nepočítá, jen přebírá odhad kvízu tam, kde to kontrakt dovoluje.

## Čeká na kód `JeanQuiz.estimate` a tělo requestu

| Kontrola | Stav |
|---|---|
| Klíče těla requestu = `DetailsJson` výše (všechna 3 volání) | neověřeno |
| `quiz` (q1–q6) v requestu | neověřeno |
| bez FVE → null / consult | neověřeno |
| baterie 0 → null / start | neověřeno |
| „nevím“ nedosadí 8 kWp | neověřeno |
| rozsah (bojler, tc, ev) vstupuje do odhadu | neověřeno — generátor to z dat nepozná |
| zaokrouhlení na 10 Kč, badge „orientační“ | neověřeno |
| `PLAN_COPY` (bez něj `co_chce` = null) | chybí |

Fixtures jsou sestavené z kontraktu (`_pozn` v každém souboru), ne ze stagingu.
`dbo.Leads` je prázdná a testovací lead se neposílá.

## Kritičnost mezer

- `blokuje_hovor`: jméno, kontakt, plán
- `blokuje_odhad`: faktura, tarif (nevím), FVE (nevím), kWp, baterie, VT/NT časy
- `muze_pockat`: kraj, obec, rozsah, střídač, EAN, IČ, co_chce

Ukázka pro akceptační případ bez FVE a bez baterie: `ukazka_bez_fve.json`
(`odhad_kc_mes: null`, plán `start` → `consult`, 20 Kč zahozeno).
