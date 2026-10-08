#!/usr/bin/env python3
"""Pilot intake: řádek dbo.Leads (#pilot / kvíz) -> formulář zákazníka + prompt pro agenta.

Formulář se plní jen z řádku a jeho DetailsJson. Co tam není, je null a jde do
`otevrene`. Kč se přebírá z odhadu kvízu jen tam, kde to kontrakt dovoluje
(FVE + baterie + známé kWp); generátor sám nic nedopočítává. Rozpory s
kontraktem se vrací v `audit`, výsledek se řídí kontraktem, ne payloadem.

Použití: python3 intake.py lead_row.json [--plan-copy plan_copy.json]
"""
import argparse
import json
import sys

FVE = {"ano", "ne", "nevim"}
TARIF = {"spot", "vtnt", "nevim"}
ROZSAH = {"start", "bojler", "tc", "ev", "full"}
PLANS = {"start", "opt", "full", "consult"}

# Interní pole -> klíč v DetailsJson. Jediné místo, kde se opravuje název klíče,
# až bude k dispozici tělo requestu z fvesmart-app.js (opravuje se generátor, ne web).
DETAILS_KEYS = {
    "plan": "plan",
    "odhad_kc_mes": "odhadKcMes",
    "fve": "fve",
    "kwp": "kwp",
    "baterie_kwh": "baterieKwh",
    "tarif": "tarif",
    "rozsah": "rozsah",
    "kraj": "kraj",
}
QUIZ_KEY = "quiz"
QUIZ_QUESTIONS = ["q1", "q2", "q3", "q4", "q5", "q6"]

# Údaje, které kvíz nezjišťuje vůbec — vždy se doplňují hovorem.
ALWAYS_OPEN = ["faktura", "stridac", "ean", "vt_nt_casy", "ic"]

# Foto faktury chodí přes Formspree mimo dbo.Leads a s leadem se nepáruje.
OPEN_NOTES = {"faktura": "Formspree xeereava, nespárováno s leadem"}

CRITICALITY = {
    "jmeno": "blokuje_hovor",
    "kontakt": "blokuje_hovor",
    "plan": "blokuje_hovor",
    "faktura": "blokuje_odhad",
    "tarif": "blokuje_odhad",
    "fve": "blokuje_odhad",
    "kwp": "blokuje_odhad",
    "baterie_kwh": "blokuje_odhad",
    "vt_nt_casy": "blokuje_odhad",
    "stridac": "muze_pockat",
    "ean": "muze_pockat",
    "obec": "muze_pockat",
    "kraj": "muze_pockat",
    "rozsah": "muze_pockat",
    "ic": "muze_pockat",
    "co_chce": "muze_pockat",
}

CO_NECHCE = ["instalace elektro", "závazek", "ceník (beta)"]


def _num(v):
    if v is None or v == "" or isinstance(v, bool):
        return None
    try:
        n = float(str(v).replace(",", ".").replace(" ", ""))
    except ValueError:
        return None
    return n if n > 0 else None


def _enum(v, allowed):
    if v is None:
        return None
    s = str(v).strip().lower().replace("í", "i").replace("/", "")
    return s if s in allowed else None


def _str(v):
    s = str(v).strip() if v is not None else ""
    return s or None


def from_lead_row(row):
    """Řádek dbo.Leads -> (plochý payload s interními názvy, audit děr).

    Klíč, který v DetailsJson chybí, v payloadu není vůbec (ne null) a audit ho
    vypíše pod jeho názvem v DetailsJson.
    """
    audit = []
    details = row.get("DetailsJson")
    if isinstance(details, str):
        try:
            details = json.loads(details) if details.strip() else {}
        except ValueError:
            audit.append({"typ": "details_json_neplatny"})
            details = {}
    if not isinstance(details, dict):
        details = {}

    payload = {"jmeno": row.get("Name"),
               "kontakt": row.get("Phone") or row.get("Email"),
               "obec": details.get("obec")}
    missing = []
    for field, key in DETAILS_KEYS.items():
        if key in details:
            payload[field] = details[key]
        else:
            missing.append(key)

    quiz = details.get(QUIZ_KEY)
    if not isinstance(quiz, dict):
        missing.append(QUIZ_KEY)
    else:
        missing += [f"{QUIZ_KEY}.{q}" for q in QUIZ_QUESTIONS
                    if not _str(quiz.get(q))]
    if missing:
        audit.append({"typ": "chybi_hidden_pole", "pole": missing})
    return payload, audit


def contract_plan(fve, baterie_kwh, rozsah):
    """Plán podle kontraktu. Rozsah 'full' -> full, jinak opt (jen s baterií)."""
    if fve != "ano":
        return "consult"
    if baterie_kwh is None:
        return "start"
    return "full" if rozsah == "full" else "opt"


def build_form(row, plan_copy=None):
    payload, audit = from_lead_row(row)

    fve = _enum(payload.get("fve"), FVE)
    kwp = _num(payload.get("kwp"))
    baterie = _num(payload.get("baterie_kwh"))
    tarif = _enum(payload.get("tarif"), TARIF)
    rozsah = _enum(payload.get("rozsah"), ROZSAH)

    if fve != "ano" and kwp is not None:
        audit.append({"typ": "kwp_bez_fve", "detail": f"kwp={kwp} při fve={fve}; zahozeno"})
        kwp = None

    plan = contract_plan(fve, baterie, rozsah)
    sent_plan = _enum(payload.get("plan"), PLANS)
    if sent_plan is not None and sent_plan != plan:
        audit.append({"typ": "plan_rozpor", "payload": sent_plan, "kontrakt": plan})

    sent_kc = _num(payload.get("odhad_kc_mes"))
    kc = None
    if plan in ("opt", "full") and kwp is not None and sent_kc is not None:
        kc = int(round(sent_kc / 10.0)) * 10
    elif sent_kc is not None:
        if plan in ("opt", "full") and kwp is None:
            why = "FVE 'nevím'/bez kWp — podezření na tichý default 8 kWp"
        else:
            why = f"Kč u plánu {plan} (bez baterie / bez FVE) je chyba, ne fakt"
        audit.append({"typ": "odhad_zahozen", "payload_kc": sent_kc, "duvod": why})

    copy = (plan_copy or {}).get(plan)
    co_chce = None
    if copy:
        sentences = [s for s in copy if baterie is not None or "bateri" not in s.lower()]
        co_chce = sentences[:3] or None

    form = {
        "identita": {
            "jmeno": _str(payload.get("jmeno")),
            "kontakt": _str(payload.get("kontakt")),
            "obec": _str(payload.get("obec")),
        },
        "fve": fve,
        "kwp": kwp,
        "baterie_kwh": baterie,
        "tarif": tarif,
        "rozsah": rozsah,
        "kraj": _str(payload.get("kraj")),
        "plan": plan,
        "odhad_kc_mes": kc,
        "duveryhodnost": "orientacni" if kc is not None else "nelze_spocitat",
        "co_chce": co_chce,
        "co_nechce": CO_NECHCE,
    }

    open_items = []
    for key in ("jmeno", "kontakt", "obec"):
        if form["identita"][key] is None:
            open_items.append(key)
    for key in ("fve", "kwp", "baterie_kwh", "tarif", "rozsah", "kraj", "co_chce"):
        if form[key] is None:
            # kWp a baterie bez FVE nejsou mezera, jen důsledek odpovědi „ne“.
            if key in ("kwp", "baterie_kwh") and fve == "ne":
                continue
            # baterie null při FVE ano je odpověď „bez baterie“, ne mezera.
            if key == "baterie_kwh" and fve == "ano" and "baterie_kwh" in payload:
                continue
            open_items.append(key)
    if tarif == "nevim":
        open_items.append("tarif")
    if fve == "nevim":
        open_items.append("fve")
    open_items += ALWAYS_OPEN
    form["otevrene"] = [
        {"pole": k, "kriticnost": CRITICALITY.get(k, "muze_pockat"),
         **({"pozn": OPEN_NOTES[k]} if k in OPEN_NOTES else {})}
        for k in dict.fromkeys(open_items)
    ]
    return form, audit


PROMPT = """Jsi Guard agent pro přihlášku do pilotu. Máš jen tento záznam. Nic si nedomýšlej.

ZÁKAZNÍK
{form}

PRAVIDLA
- Kč říkej jen když odhad_kc_mes není null, a vždy jako „orientačně“. Jinak: „částku spočítáme z faktury“.
- Plán neměň. consult ≠ Start s baterií.
- Neslibuj úsporu, kterou kvíz nepočítal.
- První krok: zavolat do 24 h, doplnit otevřené položky, vyfotit fakturu.
- Výstup: potvrzení plánu, seznam chybějících údajů, návrh dalšího kroku (konzultace / zapojení krabičky / nic).
- Žádný zápis do zařízení, žádný export, žádná automatizace.

CO NESMÍŠ
- Ovládat dům ani měnit režim baterie.
- Slíbit cenu pilotu.
- Použít 0 Kč místo null.
- Počítat arbitráž bez η a bez faktury.
- Doplnit null pole odhadem — null zůstává null, dokud ho zákazník neřekne.

KRITIČNOST MEZER
- blokuje_hovor: bez toho nevoláš, eskaluj člověku.
- blokuje_odhad: zeptej se v hovoru, bez toho žádné Kč.
- muze_pockat: doplň, když na to přijde řeč."""


def build_prompt(form):
    return PROMPT.format(form=json.dumps(form, ensure_ascii=False, indent=2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("payload")
    ap.add_argument("--plan-copy")
    args = ap.parse_args()
    with open(args.payload, encoding="utf-8") as f:
        row = json.load(f)
    plan_copy = None
    if args.plan_copy:
        with open(args.plan_copy, encoding="utf-8") as f:
            plan_copy = json.load(f)
    form, audit = build_form(row, plan_copy)
    json.dump({"formular": form, "audit": audit, "prompt": build_prompt(form)},
              sys.stdout, ensure_ascii=False, indent=2)
    print()


if __name__ == "__main__":
    main()
