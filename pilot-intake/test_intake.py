import json
import pathlib
import unittest

from intake import build_form, build_prompt

FIX = pathlib.Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


class ContractTest(unittest.TestCase):
    def test_bez_fve_zadne_kc_consult(self):
        form, audit = build_form(load("bez_fve_bug20.json"))
        self.assertEqual(form["plan"], "consult")
        self.assertIsNone(form["odhad_kc_mes"])
        self.assertEqual(form["duveryhodnost"], "nelze_spocitat")
        typy = {a["typ"] for a in audit}
        self.assertIn("plan_rozpor", typy)
        self.assertIn("odhad_zahozen", typy)

    def test_fve_bez_baterie_start_bez_kc(self):
        form, _ = build_form(load("fve_bez_baterie.json"))
        self.assertEqual(form["plan"], "start")
        self.assertIsNone(form["odhad_kc_mes"])
        self.assertNotIn("baterie_kwh", [o["pole"] for o in form["otevrene"]])

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
        self.assertIn({"typ": "chybi_hidden_pole", "pole": ["kraj"]}, audit)

    def test_co_chce_bez_vety_o_baterii(self):
        copy = {"start": ["Vidíte výrobu FVE.", "Baterie se nabíjí levně.", "Hlídáme přetoky.", "Report měsíčně."]}
        form, _ = build_form(load("fve_bez_baterie.json"), copy)
        self.assertEqual(form["co_chce"], ["Vidíte výrobu FVE.", "Hlídáme přetoky.", "Report měsíčně."])

    def test_bez_plan_copy_co_chce_null(self):
        form, _ = build_form(load("fve_bez_baterie.json"))
        self.assertIsNone(form["co_chce"])

    def test_kriticnost(self):
        p = load("fve_baterie.json")
        del p["kontakt"]
        form, _ = build_form(p)
        krit = {o["pole"]: o["kriticnost"] for o in form["otevrene"]}
        self.assertEqual(krit["kontakt"], "blokuje_hovor")
        self.assertEqual(krit["faktura"], "blokuje_odhad")
        self.assertEqual(krit["kraj"], "muze_pockat")

    def test_prompt_nese_null_ne_nulu(self):
        form, _ = build_form(load("bez_fve_bug20.json"))
        prompt = build_prompt(form)
        self.assertIn('"odhad_kc_mes": null', prompt)
        self.assertIn("částku spočítáme z faktury", prompt)


if __name__ == "__main__":
    unittest.main()
