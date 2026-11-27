#!/usr/bin/env python3
"""
test_fpa_headlines.py — FPA-007/008: resumen ejecutivo.

Parte de la antigua test_fpa.py (coffe-kav.5): 3–5 headlines con
interpretación; n/a con razón, nunca vacío ni cero inventado.

- resumen ejecutivo 3–5 headlines con interpretación y n/a con razón (FPA-007/008)
- provenance tag reported/assumed en toda figura (FPA-003)
"""

import importlib.util
import json
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, REPO / relpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


viz = _load("viz_fpa", "scripts/viz-fpa.py")

REPORT = json.loads((REPO / "data" / "usage_report_v3.json").read_text())


def _mini_report(**meta_over):
    """Reporte mínimo válido para tests de validación."""
    return {
        "metadata": {
            "date_range": {"start": "2026-06-01", "end": "2026-06-10"},
            "filter": "in-scope",
            "total_interactions": 10,
            "total_days": 10,
            "total_hours": 5,
            "total_projects": 1,
            "cost_total_effective": 1.0,
            "cost_total_real": 20.0,
            "subscription_fees": 20.0,
            **meta_over,
        },
        "hourly": {}, "daily": {},
        "monthly": {"2026-06": {"interactions": 10, "input_tokens": 1,
                                "output_tokens": 1, "cache_read_tokens": 1,
                                "cache_write_tokens": 0, "cost_effective": 1.0,
                                "cost_real": 20.0, "tools": {}, "models": {},
                                "subscription_fees": 20.0}},
        "projects": {}, "skills": {}, "commands": {}, "sessions": {},
        "multitasking": {}, "project_daily": {},
        "subscription_config": {}, "subscription_fees_by_month": {},
    }


class TestHeadlines(unittest.TestCase):
    """FPA-007/008: 3–5 headlines con interpretación; n/a con razón."""

    def test_entre_3_y_5_headlines(self):
        heads = viz.build_headlines(REPORT)
        self.assertTrue(3 <= len(heads) <= 5, f"{len(heads)} headlines")

    def test_headlines_obligatorios_presentes(self):
        heads = {h["key"] for h in viz.build_headlines(REPORT)}
        self.assertIn("cost_effective", heads)
        self.assertIn("cost_cash", heads)
        self.assertIn("leverage", heads)
        self.assertIn("cost_per_1k", heads)

    def test_efectivo_y_cash_separados_y_distinguidos(self):
        """FPA-002: dos medidas separadas, nunca sumadas."""
        heads = {h["key"]: h for h in viz.build_headlines(REPORT)}
        self.assertNotEqual(heads["cost_effective"]["value"],
                            heads["cost_cash"]["value"])
        # no hay ningún headline combinado efectivo+cash
        self.assertNotIn("total_cost", heads)

    def test_valores_contra_el_reporte(self):
        heads = {h["key"]: h for h in viz.build_headlines(REPORT)}
        eff = sum(m["cost_effective"] for m in REPORT["monthly"].values()
                  if m["interactions"] > 0)
        self.assertAlmostEqual(eff, heads["cost_effective"]["value"], places=2)
        # coffe-a31.3 (CRG-F2): cost_cash del headline = cash del ledger
        # (charges_real_by_month, *reported*), NO la suma mensual del tracker
        # (fees implícitos + p2p, *assumed*) — FPA-002 las mantiene separadas.
        meses_con_datos = {ym for ym, mo in REPORT["monthly"].items()
                           if mo["interactions"] > 0}
        cash = sum(v
                   for prov in REPORT.get("charges_real_by_month",
                                          {}).values()
                   for ym, v in prov.items() if ym in meses_con_datos)
        self.assertAlmostEqual(cash, heads["cost_cash"]["value"], places=2)
        self.assertAlmostEqual(eff / cash, heads["leverage"]["value"], places=2)

    def test_toda_headline_tiene_provenance(self):
        """FPA-003: reported o assumed, nunca vacío."""
        for h in viz.build_headlines(REPORT):
            self.assertIn(h["provenance"], ("reported", "assumed"), h["key"])

    def test_outcome_sin_datos_es_na_con_razon(self):
        """FPA-008: n/a con razón, nunca vacío ni cero inventado."""
        heads = viz.build_headlines(REPORT)
        outcome = [h for h in heads if h["key"] == "outcome"]
        if not outcome:
            self.fail("falta el headline outcome")
        h = outcome[0]
        if h["value"] is None:
            self.assertTrue(h["reason"], "n/a sin razón")
            self.assertEqual("n/a", h["display"])

    def test_outcome_con_datos_de_commits(self):
        rep = json.loads(json.dumps(REPORT))
        rep["monthly"]["2026-06"]["outcomes_by_project"] = {
            "charly-wai": {"commits": 10, "releases": 1}}
        heads = {h["key"]: h for h in viz.build_headlines(rep)}
        self.assertIsNotNone(heads["outcome"]["value"])
        self.assertIsNone(heads["outcome"]["reason"])

    def test_interpretacion_de_una_linea(self):
        for h in viz.build_headlines(REPORT):
            if h["value"] is not None:
                self.assertTrue(h["interp"], h["key"])
                self.assertNotIn("\n", h["interp"])

    def test_headline_na_cuando_no_hay_datos(self):
        rep = _mini_report()
        rep["monthly"]["2026-06"]["interactions"] = 0
        heads = {h["key"]: h for h in viz.build_headlines(rep)}
        self.assertIsNone(heads["cost_per_1k"]["value"])
        self.assertTrue(heads["cost_per_1k"]["reason"])


if __name__ == "__main__":
    unittest.main()
