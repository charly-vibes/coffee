#!/usr/bin/env python3
"""
test_fpa_charges.py — tests de viz-fpa.py con el ledger de cargos reales
(ticket coffe-a31.3, CRG-F2; epic coffe-a31 FPA-013/082)

Alcance:
- cash cost = cargos reales del ledger `data/charges.json` (*reported*)
  en KPIs, árboles y presupuestos (FPA-031 cambia de semántica); el
  efectivo queda API-equivalente estimado; jamás se suman (FPA-002).
- proveedor sin facturas en el periodo → n/a con razón (FPA-008).
- golden del modelo con ledger (FPA-104, REGEN_GOLDEN=1).

(coffe-kav.5: alertas verify-plan/reconciliación, sección de
reconciliación y economía de planes viven en test_fpa_charges_alerts.py,
test_fpa_charges_reconcile.py y test_fpa_charges_economy.py.)
"""

import importlib.util
import json
import os

import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GOLDEN = Path(__file__).resolve().parent / "golden" / "fpa-charges-snapshot.json"
REGEN_GOLDEN = os.environ.get("REGEN_GOLDEN") == "1"


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


def _kpi_value(kpis, key):
    return next(k for k in kpis if k["key"] == key)


class TestCashLedger(unittest.TestCase):
    """FPA-031 con cargos reales: cash mensual = ledger (*reported*)."""

    def test_cash_mensual_del_ledger(self):
        """may $120 (claude 100 + codex 20), jun $20 (créditos), jul $0."""
        months = {m["ym"]: m for m in viz.build_months(_charged_fixture())}
        self.assertAlmostEqual(120.0, months["2026-05"]["cost_cash"])
        self.assertAlmostEqual(20.0, months["2026-06"]["cost_cash"])
        self.assertAlmostEqual(0.0, months["2026-07"]["cost_cash"])
        for ym in months:
            self.assertEqual("reported", months[ym]["cash_provenance"])

    def test_charges_por_proveedor_en_el_mes(self):
        months = {m["ym"]: m for m in viz.build_months(_charged_fixture())}
        self.assertEqual({"claude-cli": 100.0, "codex": 20.0},
                         months["2026-05"]["charges_by_provider"])
        self.assertEqual({"claude-cli": 20.0},
                         months["2026-06"]["charges_by_provider"])
        self.assertEqual({}, months["2026-07"]["charges_by_provider"])

    def test_refund_descuenta(self):
        """FPA-003 escenario: el refund negativo descuenta el cash del mes."""
        fx = _charged_fixture()
        fx["charges_real_by_month"]["claude-cli"]["2026-06"] = -28.22
        months = {m["ym"]: m for m in viz.build_months(fx)}
        self.assertAlmostEqual(-28.22, months["2026-06"]["cost_cash"])

    def test_fallback_sin_ledger_es_assumed(self):
        """Reporte previo a coffe-a31.2 (sin ledger): cash = suma del
        tracker con provenance *assumed* y razón (FPA-008)."""
        fx = f2.f2_fixture()
        months = {m["ym"]: m for m in viz.build_months(fx)}
        self.assertAlmostEqual(20.0, months["2026-05"]["cost_cash"])
        self.assertEqual("assumed", months["2026-05"]["cash_provenance"])
        self.assertTrue(months["2026-05"]["cash_reason"])
        self.assertIsNone(months["2026-05"]["charges_by_provider"])

    def test_cash_y_efectivo_no_se_suman(self):
        """FPA-002: headlines separados, sin cifra que mezcle ambos."""
        heads = viz.build_headlines(_charged_fixture())
        keys = {h["key"] for h in heads}
        self.assertIn("cost_cash", keys)
        self.assertIn("cost_effective", keys)
        self.assertNotIn("cost_total", keys)


class TestHeadlineCash(unittest.TestCase):

    def test_headline_cash_reported(self):
        heads = {h["key"]: h for h in viz.build_headlines(_charged_fixture())}
        cash = heads["cost_cash"]
        self.assertAlmostEqual(140.0, cash["value"])  # 120 + 20 + 0
        self.assertEqual("reported", cash["provenance"])
        self.assertIn("data/charges.json", cash["interp"])

    def test_headline_cash_sin_facturas_en_el_periodo(self):
        """Ledger ok pero cero facturas dentro del periodo → n/a con razón."""
        fx = _charged_fixture()
        fx["charges_real_by_month"] = {"otro-tool": {"2025-12": 30.0}}
        heads = {h["key"]: h for h in viz.build_headlines(fx)}
        cash = heads["cost_cash"]
        self.assertIsNone(cash["value"])
        self.assertEqual("reported", cash["provenance"])
        self.assertTrue(cash["reason"])


class TestKpisCashLedger(unittest.TestCase):
    """KPIs FPA-030/031 con cash del ledger."""

    def test_kpi_cost_cash_del_ledger(self):
        ing = viz.kpi_ingredients(_charged_fixture(), CONFIG)
        kpis = viz.kpis_for_window(ing, ["2026-05", "2026-06", "2026-07"], None)
        kpi = _kpi_value(kpis, "cost_cash")
        self.assertAlmostEqual(140.0, kpi["value"])
        self.assertEqual("reported", kpi["provenance"])

    def test_kpi_cost_cash_fallback_assumed(self):
        ing = viz.kpi_ingredients(f2.f2_fixture(), CONFIG)
        kpis = viz.kpis_for_window(ing, ["2026-05", "2026-06", "2026-07"], None)
        kpi = _kpi_value(kpis, "cost_cash")
        self.assertAlmostEqual(45.0, kpi["value"])  # 20 + 25 + 0
        self.assertEqual("assumed", kpi["provenance"])


class TestBudgetCashLedger(unittest.TestCase):
    """FPA-050…055: el actual de la tabla de varianza es el cash del ledger."""

    def test_actual_cash_del_ledger(self):
        budget = viz.build_budget(_charged_fixture(), CONFIG)
        rows = {r["ym"]: r for r in budget["rows"]}
        self.assertAlmostEqual(120.0, rows["2026-05"]["actual_cash"])
        self.assertAlmostEqual(20.0, rows["2026-06"]["actual_cash"])
        self.assertAlmostEqual(0.0, rows["2026-07"]["actual_cash"])

    def test_ytd_cash_del_ledger(self):
        budget = viz.build_budget(_charged_fixture(), CONFIG)
        self.assertAlmostEqual(140.0, budget["ytd_cash"]["actual"])


class TestGolden(unittest.TestCase):
    """FPA-104: golden del modelo con ledger (REGEN_GOLDEN=1)."""

    def test_golden_charges(self):
        model = viz.build_model(_charged_fixture(), CONFIG)
        snapshot = {k: model[k] for k in
                    ("months", "headlines", "reconciliation")}
        if REGEN_GOLDEN:
            GOLDEN.parent.mkdir(parents=True, exist_ok=True)
            GOLDEN.write_text(json.dumps(snapshot, indent=2,
                                         ensure_ascii=False,
                                         sort_keys=True) + "\n")
            self.skipTest("golden regenerado; revisar diff antes de aceptar")
        if not GOLDEN.exists():
            self.fail("Falta golden; corre "
                      "REGEN_GOLDEN=1 python3 tests/test_fpa_charges.py")
        expected = json.loads(GOLDEN.read_text())
        self.assertEqual(expected, snapshot)

    def test_determinismo_seccion(self):
        """FPA-104: build_reconciliation determinista."""
        a = viz.build_reconciliation(_charged_fixture())
        b = viz.build_reconciliation(_charged_fixture())
        self.assertEqual(json.dumps(a, sort_keys=True),
                         json.dumps(b, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
