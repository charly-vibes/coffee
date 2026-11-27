#!/usr/bin/env python3
"""
test_fpa_f4.py — tests de viz-fpa.py F4: motor de alertas con severity +
regla + evidencia (FPA-080/088), reglas verify-plan (FPA-081) y
reconciliación (FPA-082), y golden de la fase (FPA-105, REGEN_GOLDEN=1).

Reutiliza el fixture de meses parciales de test_fpa_f2 (FPA-102).
(coffe-kav.5: unit-cost/mix/concentración viven en test_fpa_f4_rules.py;
budget/concentration/staleness en test_fpa_f4_budget_alerts.py; economía
de suscripción y render en test_fpa_f4_economy.py.)
"""

import importlib.util
import json
import os
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GOLDEN = Path(__file__).resolve().parent / "golden" / "fpa-f4-snapshot.json"
REGEN_GOLDEN = os.environ.get("REGEN_GOLDEN") == "1"
TODAY = "2026-09-20"  # fecha fija para staleness determinista en tests/golden


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, REPO / relpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


viz = _load("viz_fpa_f4", "scripts/viz-fpa.py")
f2 = _load("test_fpa_f2", "tests/test_fpa_f2.py")
CONFIG = json.loads((REPO / "config" / "fpa.json").read_text())


def _cfg(**th_over):
    cfg = json.loads(json.dumps(CONFIG))
    cfg["alert_thresholds"].update(th_over)
    return cfg


def _rules(alerts):
    return [a["rule"] for a in alerts]


def _by_rule(alerts, rule):
    return [a for a in alerts if a["rule"] == rule]


# ======================================================================
# Motor de alertas (FPA-080, 088)
# ======================================================================

class TestAlertEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = f2.f2_fixture()
        cls.alerts = viz.build_alerts(cls.fixture, CONFIG, today=TODAY)

    def test_alert_estructura(self):
        """FPA-080: cada alerta lleva severity, rule name y evidence."""
        for a in self.alerts:
            self.assertIn(a["severity"], ("high", "medium", "low"), a)
            self.assertTrue(a["rule"], a)
            self.assertIsInstance(a["evidence"], dict)
            self.assertTrue(a["message"], a)

    def test_umbrales_configurables(self):
        """FPA-088: los umbrales salen de config.alert_thresholds."""
        fx = self.fixture
        alerts = viz.build_alerts(fx, _cfg(concentration_top3_pct=101.0),
                                  today=TODAY)
        self.assertNotIn("concentration", _rules(alerts))
        alerts = viz.build_alerts(fx, _cfg(concentration_top3_pct=0.0),
                                  today=TODAY)
        self.assertIn("concentration", _rules(alerts))


# ======================================================================
# Reglas individuales
# ======================================================================

class TestVerifyPlan(unittest.TestCase):
    """FPA-081: Claude efectivo ÷ precio del plan > 25× → alerta.

    coffe-a31.3 (calendario corregido): un mes con uso de Claude sin plan
    que lo cubra también dispara la alerta (jun/jul del fixture; el caso
    real es jun-sep 2026)."""

    def setUp(self):
        self.fx = f2.f2_fixture()

    def _with_claude_cost(self, cost, jun=None, jul=None):
        fx = self.fx
        fx["monthly"]["2026-05"]["tools"]["claude-cli"]["cost_effective"] = cost
        if jun is not None:
            fx["monthly"]["2026-06"]["tools"]["claude-cli"]["cost_effective"] = jun
        if jul is not None:
            fx["monthly"]["2026-07"]["tools"]["claude-cli"]["cost_effective"] = jul
        return fx

    def test_no_fira_bajo_umbral(self):
        """Sin excedente en ningún mes ni meses sin plan con uso → sin alerta."""
        alerts = viz.build_alerts(self._with_claude_cost(40.0, jun=0.0, jul=0.0),
                                  CONFIG, today=TODAY)
        self.assertNotIn("verify-plan", _rules(alerts))

    def test_fira_sobre_umbral(self):
        """3000 efectivo ÷ $20 (mayo inicia en Pro tras el 19-05,
        calendario corregido a facturas) = 150× > 25×; además jun/jul
        tienen uso de Claude sin plan activo → 3 alertas."""
        alerts = viz.build_alerts(self._with_claude_cost(3000.0), CONFIG, today=TODAY)
        ev = _by_rule(alerts, "verify-plan")
        self.assertEqual(3, len(ev))
        may = next(a for a in ev if a["evidence"]["month"] == "2026-05")
        self.assertEqual(150.0, may["evidence"]["multiple"])
        for a in ev:
            if a["evidence"]["month"] != "2026-05":
                self.assertIsNone(a["evidence"]["multiple"])
                self.assertEqual(0.0, a["evidence"]["plan_fee"])

    def test_umbral_configurable(self):
        fx = self._with_claude_cost(2000.0, jun=0.0, jul=0.0)  # 100× (no > 100)
        alerts = viz.build_alerts(fx, _cfg(plan_usage_multiple=100.0), today=TODAY)
        self.assertNotIn("verify-plan", _rules(alerts))

    def test_sin_plan_activo_fira(self):
        """coffe-a31.3: jun/jul con uso de Claude y sin plan que cubra el
        mes (calendario corregido: claude cancelado tras jun) → alerta."""
        alerts = viz.build_alerts(self._with_claude_cost(40.0), CONFIG, today=TODAY)
        ev = _by_rule(alerts, "verify-plan")
        meses = {a["evidence"]["month"] for a in ev}
        self.assertEqual({"2026-06", "2026-07"}, meses)
        for a in ev:
            self.assertEqual("high", a["severity"])
            self.assertEqual(0.0, a["evidence"]["plan_fee"])
            self.assertIsNone(a["evidence"]["multiple"])


class TestReconciliation(unittest.TestCase):
    """FPA-082: coste real reportado vs cargas implícitas del calendario."""

    def test_caso_readme_65_vs_220(self):
        """Caso motivador: reporte con $65.58 reales vs ~$220 implícitos."""
        fx = f2.f2_fixture()
        fx["subscription_fees_by_month"] = {"2026-05": 110.0, "2026-06": 110.0}
        fx["monthly"]["2026-05"]["cost_real"] = 55.58
        fx["monthly"]["2026-06"]["cost_real"] = 10.0
        alerts = viz.build_alerts(fx, CONFIG, today=TODAY)
        ev = _by_rule(alerts, "reconciliation")
        self.assertEqual(1, len(ev))
        self.assertAlmostEqual(65.58, ev[0]["evidence"]["reported"])
        self.assertAlmostEqual(220.0, ev[0]["evidence"]["implied"])
        self.assertIn("high", ev[0]["severity"])

    def test_no_fira_si_coinciden(self):
        """Tracker actual: fees plegados en cost_real → reportado = implícito."""
        fx = f2.f2_fixture()
        fx["subscription_fees_by_month"] = {"2026-05": 110.0, "2026-06": 110.0}
        fx["monthly"]["2026-05"]["cost_real"] = 120.0   # 110 fee + 10 p2p
        fx["monthly"]["2026-06"]["cost_real"] = 110.0   # 110 fee + 0 p2p
        alerts = viz.build_alerts(fx, CONFIG, today=TODAY)
        self.assertNotIn("reconciliation", _rules(alerts))

    def test_tolerancia_configurable(self):
        fx = f2.f2_fixture()
        fx["subscription_fees_by_month"] = {"2026-05": 100.0}
        fx["monthly"]["2026-05"]["cost_real"] = 85.0  # 15% off, tolerancia 10%
        alerts = viz.build_alerts(fx, _cfg(reconcile_tolerance_pct=20.0), today=TODAY)
        self.assertNotIn("reconciliation", _rules(alerts))

    def test_efectivo_y_cash_no_se_suman(self):
        """FPA-002: la evidencia jamás mezcla efectivo con cash."""
        fx = f2.f2_fixture()
        fx["subscription_fees_by_month"] = {"2026-05": 110.0}
        alerts = viz.build_alerts(fx, CONFIG, today=TODAY)
        ev = _by_rule(alerts, "reconciliation")
        if ev:
            for key in ev[0]["evidence"]:
                self.assertNotIn("effective", key.lower().replace("_", ""))


# ======================================================================
# Golden (FPA-105)
# ======================================================================

class TestGolden(unittest.TestCase):
    """FPA-105: golden de alertas + utilización/break-even/comparación.
    Regenerar: REGEN_GOLDEN=1 python3 tests/test_fpa_f4.py"""

    def test_golden_f4(self):
        """FPA-105: golden de alertas + economía sobre el fixture con meses
        parciales (FPA-102). today fijo para staleness determinista."""
        fx = f2.f2_fixture()
        snapshot = {
            "alerts": viz.build_alerts(fx, CONFIG, today=TODAY),
            "plan_economy": viz.build_plan_economy(fx, CONFIG),
        }
        if REGEN_GOLDEN:
            GOLDEN.parent.mkdir(parents=True, exist_ok=True)
            GOLDEN.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False,
                                         sort_keys=True) + "\n")
            self.skipTest("golden regenerado; revisar diff antes de aceptar")
        if not GOLDEN.exists():
            self.fail("Falta golden; corre REGEN_GOLDEN=1 python3 tests/test_fpa_f4.py")
        expected = json.loads(GOLDEN.read_text())
        self.assertEqual(expected, snapshot)


if __name__ == "__main__":
    unittest.main()
