#!/usr/bin/env python3
"""
test_fpa_f3.py — tests de viz-fpa.py F3: presupuestos con pro-rating y
varianza (FPA-050…056) y golden de la fase (FPA-101/102, REGEN_GOLDEN=1).

Reutiliza el fixture de meses parciales de test_fpa_f2 (FPA-102).
(coffe-kav.5: bridge y forecast/render viven en test_fpa_f3_bridge.py y
test_fpa_f3_forecast.py.)
"""

import importlib.util
import json
import os
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GOLDEN = Path(__file__).resolve().parent / "golden" / "fpa-f3-snapshot.json"
REGEN_GOLDEN = os.environ.get("REGEN_GOLDEN") == "1"


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, REPO / relpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


viz = _load("viz_fpa_f3", "scripts/viz-fpa.py")
f2 = _load("test_fpa_f2", "tests/test_fpa_f2.py")
CONFIG = json.loads((REPO / "config" / "fpa.json").read_text())


def _cfg(**budget_over):
    cfg = json.loads(json.dumps(CONFIG))
    cfg["budgets"].update(budget_over)
    return cfg


class F3Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = f2.f2_fixture()
        cls.model = viz.build_model(cls.fixture, CONFIG)


# ======================================================================
# Presupuestos (FPA-050…056)
# ======================================================================

class TestBudget(F3Base):
    """FPA-050…056: pro-rating, varianza con signo, markers, YTD."""

    def test_budget_desde_start_month(self):
        """FPA-051: se aplican solo desde budgets.start_month (2026-04)."""
        for row in self.model["budget"]["rows"]:
            self.assertTrue(row["in_budget"], row["ym"])
            self.assertIsNotNone(row["budget_cash"])

    def test_fuera_del_periodo_presupuestado(self):
        """FPA-051: mes anterior al start → presupuesto None con razón."""
        cfg = _cfg(start_month="2026-06")
        model = viz.build_model(self.fixture, cfg)
        may = next(r for r in model["budget"]["rows"] if r["ym"] == "2026-05")
        self.assertFalse(may["in_budget"])
        self.assertIsNone(may["budget_cash"])
        self.assertIsNone(may["variance_cash"])
        self.assertTrue(may["reason_cash"])

    def test_prorating_mes_parcial(self):
        """FPA-052: julio parcial (10/31) → presupuesto 100×10/31 = 32.26."""
        jul = next(r for r in self.model["budget"]["rows"] if r["ym"] == "2026-07")
        self.assertAlmostEqual(100.0 * 10 / 31, jul["budget_cash"], places=2)
        self.assertAlmostEqual(200.0 * 10 / 31, jul["budget_eff"], places=2)
        self.assertAlmostEqual(10 / 31, jul["pro_rate"], places=6)
        # meses completos: factor 1.0
        jun = next(r for r in self.model["budget"]["rows"] if r["ym"] == "2026-06")
        self.assertEqual(1.0, jun["pro_rate"])
        self.assertAlmostEqual(100.0, jun["budget_cash"], places=2)

    def test_varianza_con_signo(self):
        """FPA-053: varianza = actual − presupuesto; positivo = over budget."""
        jun = next(r for r in self.model["budget"]["rows"] if r["ym"] == "2026-06")
        self.assertAlmostEqual(25.0 - 100.0, jun["variance_cash"], places=6)
        self.assertEqual("-$75.00", jun["variance_display_cash"])
        self.assertAlmostEqual(-0.75, jun["variance_pct_cash"], places=6)
        self.assertEqual("-75.0%", jun["variance_pct_display_cash"])

    def test_varianza_over_budget_positiva(self):
        """FPA-053: actual > presupuesto → varianza positiva con '+$'."""
        cfg = _cfg(cash_monthly=10.0, effective_monthly=10.0)
        model = viz.build_model(self.fixture, cfg)
        may = next(r for r in model["budget"]["rows"] if r["ym"] == "2026-05")
        self.assertAlmostEqual(10.0, may["variance_cash"], places=6)
        self.assertEqual("+$10.00", may["variance_display_cash"])
        self.assertEqual("+100.0%", may["variance_pct_display_cash"])

    def test_marker_no_solo_color(self):
        """FPA-054: favorable/desfavorable con símbolo Y texto, no solo color."""
        may = next(r for r in self.model["budget"]["rows"] if r["ym"] == "2026-05")
        mk = may["marker_cash"]
        self.assertEqual("▼", mk["symbol"])
        self.assertEqual("bajo", mk["text"])  # favorable
        cfg = _cfg(cash_monthly=10.0)
        model = viz.build_model(self.fixture, cfg)
        over = next(r for r in model["budget"]["rows"] if r["ym"] == "2026-05")
        self.assertEqual("▲", over["marker_cash"]["symbol"])
        self.assertEqual("sobre", over["marker_cash"]["text"])  # desfavorable

    def test_tabla_ytd(self):
        """FPA-055: totales YTD por medida: actual, presupuesto, varianza."""
        ytd = self.model["budget"]["ytd_cash"]
        self.assertAlmostEqual(45.0, ytd["actual"], places=2)      # 20+25+0
        self.assertAlmostEqual(100 + 100 + 100 * 10 / 31, ytd["budget"], places=2)
        self.assertAlmostEqual(ytd["actual"] - ytd["budget"], ytd["variance"], places=2)
        ytd_eff = self.model["budget"]["ytd_eff"]
        self.assertAlmostEqual(145.0, ytd_eff["actual"], places=2)  # 50+65+30
        self.assertAlmostEqual(200 + 200 + 200 * 10 / 31, ytd_eff["budget"], places=2)

    def test_ytd_varianza_pct(self):
        ytd = self.model["budget"]["ytd_cash"]
        self.assertAlmostEqual(ytd["variance"] / ytd["budget"],
                               ytd["variance_pct"], places=3)
        self.assertIn("marker", ytd)

    def test_efectivo_y_cash_nunca_sumados(self):
        """FPA-002: sin campo que combine efectivo+cash."""
        blob = json.dumps(self.model["budget"])
        self.assertNotIn("total\"", blob.replace("total_days", ""))
        for row in self.model["budget"]["rows"]:
            self.assertNotIn("combined", row)

    def test_mes_sin_datos_na(self):
        """FPA-017/008: mes sin datos → actual n/a con razón, varianza n/a."""
        fixture = f2.f2_fixture()
        fixture["monthly"]["2026-06"]["interactions"] = 0
        model = viz.build_model(fixture, CONFIG)
        jun = next(r for r in model["budget"]["rows"] if r["ym"] == "2026-06")
        self.assertIsNone(jun["actual_cash"])
        self.assertIsNone(jun["variance_cash"])
        self.assertTrue(jun["reason_cash"])

    def test_inputs_en_modelo(self):
        """FPA-050: presupuestos del config expuestos para el editor JS."""
        inputs = self.model["budget"]["inputs"]
        self.assertEqual(100.0, inputs["cash_monthly"])
        self.assertEqual(200.0, inputs["effective_monthly"])
        self.assertEqual(3.0, inputs["target_per_1k"])
        self.assertEqual("2026-04", inputs["start_month"])


# ======================================================================
# Golden (FPA-101/102)
# ======================================================================

class TestGolden(unittest.TestCase):
    """FPA-101/102: golden de budget+bridge+forecast sobre el fixture con
    meses parciales. Regenerar: REGEN_GOLDEN=1 python3 tests/test_fpa_f3.py"""

    def test_golden_f3(self):
        model = viz.build_model(f2.f2_fixture(), CONFIG)
        snapshot = {k: model[k] for k in ("budget", "bridge", "forecast")}
        if REGEN_GOLDEN:
            GOLDEN.parent.mkdir(parents=True, exist_ok=True)
            GOLDEN.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False,
                                         sort_keys=True) + "\n")
            self.skipTest("golden regenerado; revisar diff antes de aceptar")
        if not GOLDEN.exists():
            self.fail("Falta golden; corre REGEN_GOLDEN=1 python3 tests/test_fpa_f3.py")
        expected = json.loads(GOLDEN.read_text())
        self.assertEqual(expected, snapshot)


if __name__ == "__main__":
    unittest.main()
