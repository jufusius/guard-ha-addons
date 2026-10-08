import json
import pathlib
import unittest

from intake import build_form, build_prompt

FIX = pathlib.Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def with_details(name, ans=None, **changes):
    row = load(name)
    details = json.loads(row["DetailsJson"])
    for k, v in changes.items():
        if v is ...:
            details.pop(k, None)
        else:
            details[k] = v
    if ans:
        a = json.loads(details["answers"])
        for k, v in ans.items():
            if v is ...:
                a.pop(k, None)
            else:
                a[k] = v
        details["answers"] = json.dumps(a)
    row["DetailsJson"] = json.dumps(details)
    return row


def holes(audit):
    return next((a["pole"] for a in audit if a["typ"] == "chybi_hidden_pole"), [])


class ContractTest(unittest.TestCase):
    def test_bez_fve_zadne_kc_consult(self):
        form, audit = build_form(load("bez_fve_bug20.json"))
        self.assertEqual(form["plan"], "consult")
        self.assertIsNone(form["odhad_kc_mes"])
        self.assertEqual(form["duveryhodnost"], "nelze_spocitat")
        typy = {a["typ"] for a in audit}
        self.assertIn("plan_rozpor", typy)
        self.assertIn("odhad_zahozen", typy)

    def test_fve_baterie_nula_start_bez_kc(self):
        form, _ = build_form(load("fve_bez_baterie.json"))
        self.assertEqual(form["plan"], "start")
        self.assertIsNone(form["odhad_kc_mes"])
        self.assertNotIn("baterie_kwh", [o["pole"] for o in form["otevrene"]])

    def test_bez_baterie_20kc_je_chyba(self):
        form, audit = build_form(with_details("fve_bez_baterie.json", estimate="20"))
        self.assertIsNone(form["odhad_kc_mes"])
        self.assertIn("odhad_zahozen", {a["typ"] for a in audit})

    def test_nevim_nedostane_default_8kwp(self):
        form, audit = build_form(load("fve_nevim_default8.json"))
        self.assertEqual(form["plan"], "consult")
        self.assertIsNone(form["kwp"])
        self.assertIsNone(form["odhad_kc_mes"])
        self.assertIn("kwp_bez_fve", {a["typ"] for a in audit})

    def test_fve_baterie_orientacni_zaokrouhleno(self):
        form, audit = build_form(load("fve_baterie.json"))
        self.assertEqual(form["plan"], "opt")
        self.assertEqual(form["odhad_kc_mes"], 490)
        self.assertEqual(form["duveryhodnost"], "orientacni")
        self.assertEqual(form["kwp"], 12)
        self.assertEqual(form["kraj"], "Jihočeský")
        self.assertEqual(form["identita"]["obec"], "Tábor")
        self.assertEqual(holes(audit), [])

    def test_rozsah_full_dava_plan_full(self):
        form, _ = build_form(with_details("fve_baterie.json", ans={"scope": "full"}))
        self.assertEqual(form["plan"], "full")

    def test_bez_answers_je_dira(self):
        form, audit = build_form(with_details("fve_baterie.json", answers=...))
        self.assertIn("answers", holes(audit))
        # FVE z rádia formuláře, baterie neznámá -> start, žádné Kč
        self.assertEqual(form["fve"], "ano")
        self.assertEqual(form["plan"], "start")
        self.assertIsNone(form["odhad_kc_mes"])

    def test_chybejici_odpoved_je_dira(self):
        _, audit = build_form(with_details("fve_baterie.json", ans={"kraj": ...}))
        self.assertEqual(holes(audit), ["answers.kraj"])

    def test_fve_rozpor_kviz_vs_formular(self):
        form, audit = build_form(with_details("fve_baterie.json", hasFve="no"))
        self.assertEqual(form["fve"], "ano")
        self.assertIn("fve_rozpor", {a["typ"] for a in audit})

    def test_tarif_nevim_s_odhadem_je_v_auditu(self):
        form, audit = build_form(with_details("fve_baterie.json", ans={"tarif": "unknown"}))
        self.assertEqual(form["tarif"], "nevim")
        self.assertEqual(form["odhad_kc_mes"], 490)
        self.assertIn("odhad_tarif_nevim", {a["typ"] for a in audit})

    def test_tarif_fix_neni_vtnt(self):
        form, _ = build_form(load("bez_fve_bug20.json"))
        self.assertEqual(form["tarif"], "fix")

    def test_prazdny_details_consult_null_a_vse_chybi(self):
        row = {"Name": "TEST", "Email": "t@example.com", "Phone": None,
               "Source": "pilot", "DetailsJson": "{}"}
        form, audit = build_form(row)
        self.assertEqual(form["identita"]["kontakt"], "t@example.com")
        self.assertEqual(form["plan"], "consult")
        self.assertIsNone(form["odhad_kc_mes"])
        self.assertEqual(holes(audit), ["plan", "answers"])

    def test_neplatny_details_json(self):
        row = {"Name": "TEST", "Email": "t@example.com", "DetailsJson": "{nejson"}
        form, audit = build_form(row)
        self.assertEqual(form["plan"], "consult")
        self.assertIn("details_json_neplatny", {a["typ"] for a in audit})

    def test_co_chce_bez_vety_o_baterii(self):
        copy = {"start": ["Vidíte výrobu FVE.", "Baterie se nabíjí levně.", "Hlídáme přetoky.", "Report měsíčně."]}
        form, _ = build_form(load("fve_bez_baterie.json"), copy)
        self.assertEqual(form["co_chce"], ["Vidíte výrobu FVE.", "Hlídáme přetoky.", "Report měsíčně."])

    def test_bez_plan_copy_co_chce_null(self):
        form, _ = build_form(load("fve_bez_baterie.json"))
        self.assertIsNone(form["co_chce"])

    def test_kriticnost_a_faktura_formspree(self):
        row = load("fve_baterie.json")
        row["Email"] = None
        form, _ = build_form(row)
        krit = {o["pole"]: o for o in form["otevrene"]}
        self.assertEqual(krit["kontakt"]["kriticnost"], "blokuje_hovor")
        self.assertEqual(krit["faktura"]["kriticnost"], "blokuje_odhad")
        self.assertIn("xeereava", krit["faktura"]["pozn"])
        self.assertEqual(krit["ean"]["kriticnost"], "muze_pockat")

    def test_prompt_nese_null_ne_nulu(self):
        form, _ = build_form(load("bez_fve_bug20.json"))
        prompt = build_prompt(form)
        self.assertIn('"odhad_kc_mes": null', prompt)
        self.assertIn("pásma z kvízu", prompt)
        self.assertIn("částku spočítáme z faktury", prompt)


if __name__ == "__main__":
    unittest.main()
