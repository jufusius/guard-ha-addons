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
- Pilot i kvíz: `POST https://mcp.jufusi.us/api/public/leads` (`submitToListina`) → `dbo.Leads`.
- Sloupce: `Name` (povinné), `Email`, `Phone`, `Source`, `Page`, `DetailsJson` + souhlasy a stav.
  Plán, odhad ani odpovědi kvízu sloupce nemají — žijí jen v `DetailsJson`.
- Foto faktury: Formspree `xeereava`, mimo `dbo.Leads`, nepárováno. Ve formuláři zůstává
  jako otevřená položka `faktura` (blokuje odhad) s poznámkou o kanálu.

## Vstup: tělo `Landing.submit` (ověřeno v `fvesmart-app.js`)

Přihláška z landingu (`source: "landing"`) posílá:

```json
{
  "source": "landing", "name": "…", "contact": "telefon nebo e-mail",
  "municipality": "…", "hasFve": "yes|no", "plan": "start|opt|full|consult|referral|partner",
  "estimate": "490",
  "answers": "{\"fve\":\"yes|no|unknown\",\"kwp\":\"3|5|8|12\",\"bat\":\"0|8|14\",\"tarif\":\"spot|fix|unknown\",\"scope\":\"start|opt|full\",\"kraj\":\"Jihomoravský\"}",
  "page": "/", "consent": true, "website": "", "policyVersion": "2026-10"
}
```

- `submitToListina` prázdné stringy **neposílá**: bez dokončeného kvízu chybí `answers`,
  bez shody plánu s výsledkem kvízu chybí `estimate`.
- `answers` je JSON **string**, ne objekt. Generátor ho rozbalí.
- `kwp` a `bat` jsou pásma (12 = „10+“, 8 = „do 10 kWh“, 14 = „10 kWh a víc“), ne změřené hodnoty.
  Prompt to agentovi říká.
- `kraj` je název kraje, ne kód. `tarif: fix` je fixní tarif, ne VT/NT — generátor ho nepřejmenovává.
- FVE: má přednost `answers.fve`; rádio `hasFve` jen když kvíz chybí, při rozporu `audit: fve_rozpor`.
- Generátor předpokládá, že server uloží klíče těla do `DetailsJson` pod stejnými názvy.
  **Neověřeno** — handler `LeadIntake` na MCP nevidím a `dbo.Leads` je prázdná.

Mapování je jen v `BODY_KEYS` / `ANSWER_KEYS` / `VALUE_MAP` v `intake.py`.
Díra = chybí `plan`, `answers` nebo některá ze 6 odpovědí (`answers.kraj` …) → `audit: chybi_hidden_pole`.

## Audit `JeanQuiz.result` + `Landing.estimate` proti kontraktu

| Bod kontraktu | Kód | Verdikt |
|---|---|---|
| fve=ne → null, consult | `result()`: `!hasFve` → consult, `amount: null` | SKIP |
| fve=ano, baterie 0 → null, start | `!hasBat` → start, `amount: null` | SKIP |
| fve=ano, baterie>0 → Kč na 10, opt/full podle rozsahu | `Math.round(x/12/10)*10`, `scope==='full' ? 'full' : 'opt'` | SKIP |
| „Nevím“ u FVE bez 8 kWp v částce | `unknown` → consult bez Kč; `kwp='8'` se jen předvyplní do odpovědí | SKIP (generátor kwp zahodí) |
| rozsah vstupuje do odhadu | opt = + bojler + TČ, full = + EV (`ABSORB_*`) | SKIP |
| 20 Kč bez baterie | odstraněno v K1 (2026-09-29) | SKIP |
| badge „orientační“ | `badge-orient` vždy | SKIP |
| tarif „Nevím“ | `estimate` ho bere jako spot → přičte spotovou arbitráž baterie | **otázka** — kontrakt to neřeší; generátor částku nechá a hlásí `odhad_tarif_nevim` |

## Ostatní cesty (mimo zadání, jen zjištění)

- `PilotModal` (`pilot_modal`) a `PilotPage` (`pilot_page`) jdou na listinu **bez** odpovědí kvízu —
  generátor je ohlásí jako díru `answers`.
- Na Formspree `xeereava` dál jdou: foto faktury, `Questionnaire` (/pro-domacnosti, `calculateOffer`),
  `SegmentWizard` (/wizard) a `ContactForm`. Do `dbo.Leads` se nedostanou.
- Jestli server přihlášku bez `answers` uloží, nebo ji zahodí, je v `LeadIntake` na MCP — neověřeno.

Fixtures mají tvar těla `Landing.submit`; hodnoty jsou testovací (`_pozn` v každém souboru).
`dbo.Leads` je prázdná a testovací lead se neposílá. `PLAN_COPY` je v `fvesmart-app.js`;
generátor ho bere přes `--plan-copy` (bez něj `co_chce` = null).

## Kritičnost mezer

- `blokuje_hovor`: jméno, kontakt, plán
- `blokuje_odhad`: faktura, tarif (nevím), FVE (nevím), kWp, baterie, VT/NT časy
- `muze_pockat`: kraj, obec, rozsah, střídač, EAN, IČ, co_chce

Ukázka pro akceptační případ bez FVE a bez baterie: `ukazka_bez_fve.json`
(`--plan-copy plan_copy.json`; `odhad_kc_mes: null`, plán `start` → `consult`, 20 Kč zahozeno).
