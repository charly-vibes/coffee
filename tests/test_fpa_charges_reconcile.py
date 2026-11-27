#!/usr/bin/env python3
"""
test_fpa_charges_reconcile.py — sección de reconciliación FPA-082: por
tool y mes, real pagado vs fee implícito vs efectivo estimado.

Parte de la antigua test_fpa_charges.py (coffe-kav.5).
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


class TestSeccionReconciliacion(unittest.TestCase):
    """Sección FPA-082: por tool y mes, real pagado vs fee implícito vs
    efectivo estimado; cash por proveedor con n/a con razón (FPA-008)."""

    def setUp(self):
        fx = _charged_fixture()
        fx["charges_reconciliation_by_month"] = {
            "2026-05": {
                "claude-cli": {"charges_real": 100.0,
                               "charges_provenance": "reported",
                               "subscription_fee_implicit": 100.0,
                               "cost_effective": 40.0,
                               "fee_month_total": 120.0},
                "codex": {"charges_real": 20.0,
                          "charges_provenance": "reported",
                          "subscription_fee_implicit": 20.0,
                          "cost_effective": 10.0,
                          "fee_month_total": 120.0},
            },
            "2026-06": {
                "claude-cli": {"charges_real": 20.0,
                               "charges_provenance": "reported",
                               "subscription_fee_implicit": 0.0,
                               "cost_effective": 60.0,
                               "fee_month_total": 0.0},
            },
        }
        self.rec = viz.build_reconciliation(fx)
        self.fx = fx

    def test_provenance_y_fuente(self):
        self.assertEqual("reported", self.rec["provenance"])
        self.assertEqual("data/charges.json", self.rec["source"])
        self.assertIsNone(self.rec["reason"])

    def test_proveedor_sin_facturas_en_el_periodo(self):
        """otro-tool solo factura fuera del periodo → n/a con razón."""
        otro = next(p for p in self.rec["providers"]
                    if p["provider"] == "otro-tool")
        self.assertIsNone(otro["total_in_period"])
        self.assertEqual("sin facturas en el periodo", otro["n_a_reason"])

    def test_proveedor_con_facturas_total_del_periodo(self):
        claude = next(p for p in self.rec["providers"]
                      if p["provider"] == "claude-cli")
        self.assertAlmostEqual(120.0, claude["total_in_period"])
        self.assertIsNone(claude["n_a_reason"])
        # mes sin factura pero proveedor activo → $0 (ausencia = dato)
        self.assertAlmostEqual(0.0, claude["months"]["2026-07"])

    def test_filas_tool_mes(self):
        row = next(r for r in self.rec["rows"]
                   if r["ym"] == "2026-05" and r["tool"] == "claude-cli")
        self.assertAlmostEqual(100.0, row["charges_real"])
        self.assertEqual("reported", row["charges_provenance"])
        self.assertAlmostEqual(100.0, row["subscription_fee_implicit"])
        self.assertAlmostEqual(40.0, row["cost_effective"])
        self.assertAlmostEqual(120.0, row["fee_month_total"])

    def test_filas_ordenadas_y_completas(self):
        keys = [(r["ym"], r["tool"]) for r in self.rec["rows"]]
        self.assertEqual(keys, sorted(keys))
        self.assertEqual(3, len(keys))  # may×2 + jun×1

    def test_sin_ledger_n_a_con_razon(self):
        rec = viz.build_reconciliation(f2.f2_fixture())
        self.assertEqual("unavailable", rec["provenance"])
        self.assertTrue(rec["reason"])
        self.assertEqual([], rec["providers"])
        self.assertEqual([], rec["rows"])

    def test_html_muestra_provenance_y_na(self):
        html = viz.reconciliation_html(self.rec)
        self.assertIn("data/charges.json", html)
        self.assertIn("reported", html)
        self.assertIn("assumed", html)
        self.assertIn("sin facturas en el periodo", html)
        self.assertNotIn("><td></td>", html)  # FPA-008: celda nunca vacía

    def test_html_sin_ledger(self):
        html = viz.reconciliation_html(viz.build_reconciliation(f2.f2_fixture()))
        self.assertIn("n/a", html)


if __name__ == "__main__":
    unittest.main()
