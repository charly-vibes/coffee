#!/usr/bin/env python3
"""
test_fpa_charges_economy.py — economía de planes (FPA-130…133) sobre el
cash del ledger, utilization gemini (coffe-8t8) y check-docs (FPA-143).

Parte de la antigua test_fpa_charges.py (coffe-kav.5).
"""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, REPO / relpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


viz = _load("viz_fpa_charges", "scripts/viz-fpa.py")
f2 = _load("test_fpa_f2", "tests/test_fpa_f2.py")
CONFIG = json.loads((REPO / "config" / "fpa.json").read_text())


def _charged_fixture():
    """Fixture f2 + emisiones del ledger (shape del tracker coffe-a31.2).

    Ledger del fixture: claude-cli may $100 (Max) + jun $20 (créditos
    p2p); codex may $20 (Plus); `otro-tool` solo tiene factura fuera del
    periodo (dic-2025) → n/a con razón en el periodo (FPA-008).
    """
    fx = f2.f2_fixture()
    fx["metadata"]["charges_provenance"] = "reported"
    fx["metadata"]["charges_source"] = "data/charges.json"
    fx["metadata"]["charges_total_real"] = 170.0
    fx["charges_real_by_month"] = {
        "claude-cli": {"2026-05": 100.0, "2026-06": 20.0},
        "codex": {"2026-05": 20.0},
        "otro-tool": {"2025-12": 30.0},  # fuera del periodo del reporte
    }
    # calendario corregido: may = claude Max $100 + codex Plus $20
    fx["subscription_fees_by_month"] = {"2026-05": 120.0}
    # p2p del ledger para jun (créditos claude $20) → *reported* (FPA-013)
    fx["monthly"]["2026-06"]["pay_per_token_charges"] = 20.0
    fx["monthly"]["2026-06"]["pay_per_token_provenance"] = "reported"
    return fx


class TestPlanEconomyLedger(unittest.TestCase):
    """FPA-130…133 sobre el cash real del ledger."""

    def test_caso_actual_usa_cash_del_ledger(self):
        econ = viz.build_plan_economy(_charged_fixture(), CONFIG)
        self.assertAlmostEqual(140.0, econ["four_cases"]["actual"])

    def test_caso_actual_fallback_tracker(self):
        econ = viz.build_plan_economy(f2.f2_fixture(), CONFIG)
        self.assertAlmostEqual(45.0, econ["four_cases"]["actual"])

    def test_planes_reales_del_calendario(self):
        """El calendario corregido: Max mar/abr, Pro may, AI Pro dic-may,
        Plus abr-may → un panel por periodo (FPA-130)."""
        econ = viz.build_plan_economy(_charged_fixture(), CONFIG)
        labels = [(p["tool"], p["label"]) for p in econ["plans"]]
        self.assertIn(("claude-cli", "Max $100/mes"), labels)
        self.assertIn(("claude-cli", "Pro $20/mes"), labels)
        self.assertIn(("gemini-cli", "Google AI Pro $19.99/mes"), labels)
        self.assertIn(("codex", "ChatGPT Plus $20/mes"), labels)


class TestPlanEconomySinCosteHorario(unittest.TestCase):
    """coffe-8t8: tool presente en hourly con coste 0 ≠ ausencia de registros.

    gemini-cli no reporta cost en los logs Pi: coste cero medido es
    utilización 0.0 del plan, no n/a 'sin datos horarios'."""

    def test_tool_presente_con_coste_cero_es_utilizacion_cero(self):
        fx = _charged_fixture()
        fx["hourly"]["2026-05-10 14:00"]["tools"]["gemini-cli"] = {
            "req": 10, "in": 1000, "out": 500, "cache_read": 0,
            "cache_write": 0, "cost_eff": 0.0, "cost_real": 0.0}
        econ = viz.build_plan_economy(fx, CONFIG)
        gem = [p for p in econ["plans"] if p["tool"] == "gemini-cli"]
        self.assertTrue(gem)
        for p in gem:
            self.assertEqual(0.0, p["eff_cost"])
            self.assertEqual(0.0, p["utilization"])
            self.assertIsNone(p["utilization_reason"])

    def test_tool_ausente_sigue_na(self):
        econ = viz.build_plan_economy(_charged_fixture(), CONFIG)
        gem = [p for p in econ["plans"] if p["tool"] == "gemini-cli"]
        self.assertTrue(gem)
        for p in gem:
            self.assertIsNone(p["utilization"])
            self.assertIsNotNone(p["utilization_reason"])


class TestCheckDocsLedger(unittest.TestCase):
    """FPA-143: la cifra 'Cash real (ledger)' sale del reporte."""

    def test_con_ledger_reported(self):
        expected = viz.expected_doc_figures(_charged_fixture())
        self.assertEqual("$140.00", expected["Cash real (ledger)"])

    def test_sin_ledger_n_a(self):
        expected = viz.expected_doc_figures(f2.f2_fixture())
        self.assertEqual("n/a", expected["Cash real (ledger)"])

    def test_check_docs_roundtrip(self):
        expected = viz.expected_doc_figures(_charged_fixture())
        block = "\n".join(f"- {k}: {v}" for k, v in expected.items())
        readme_tpl = (f"hola\n<!-- CHECK-DOCS:BEGIN -->\nfigs\n{block}\n"
                      f"<!-- CHECK-DOCS:END -->\n")
        with tempfile.TemporaryDirectory() as tmp:
            ok = Path(tmp) / "ok.md"
            bad = Path(tmp) / "bad.md"
            ok.write_text(readme_tpl)
            bad.write_text(readme_tpl.replace("$140.00", "$999.00"))
            self.assertEqual([], viz.check_docs(_charged_fixture(), ok))
            errors = viz.check_docs(_charged_fixture(), bad)
        self.assertTrue(any("Cash real (ledger)" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
