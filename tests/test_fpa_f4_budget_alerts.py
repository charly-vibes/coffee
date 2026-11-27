#!/usr/bin/env python3
"""
test_fpa_f4_budget_alerts.py — FPA-083/086/087: alertas budget,
concentración y staleness.

Parte de la antigua test_fpa_f4.py (coffe-kav.5); el fixture y el config
viven en tests/test_fpa_f2.py.
"""

import importlib.util
import json
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
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


def _cfg_budget(**budget_over):
    cfg = json.loads(json.dumps(CONFIG))
    cfg["budgets"].update(budget_over)
    return cfg


def _rules(alerts):
    return [a["rule"] for a in alerts]


def _by_rule(alerts, rule):
    return [a for a in alerts if a["rule"] == rule]


class TestBudgetAlert(unittest.TestCase):
    """FPA-083: mes sobre presupuesto → alerta con monto de overage."""

    def test_fira_con_cash_over(self):
        alerts = viz.build_alerts(f2.f2_fixture(), _cfg_budget(cash_monthly=15.0),
                                  today=TODAY)
        ev = _by_rule(alerts, "budget")
        months = {a["evidence"]["month"]: a for a in ev}
        self.assertIn("2026-05", months)
        self.assertAlmostEqual(5.0, months["2026-05"]["evidence"]["overage_cash"])

    def test_fira_con_efectivo_over(self):
        """coffe-ox3: el presupuesto efectivo es informativo (OQ-1) — no
        alerta; el gestionado y accionable es el cash."""
        alerts = viz.build_alerts(f2.f2_fixture(), _cfg_budget(effective_monthly=40.0),
                                  today=TODAY)
        self.assertNotIn("budget", _rules(alerts))

    def test_no_fira_en_presupuesto(self):
        alerts = viz.build_alerts(f2.f2_fixture(), CONFIG, today=TODAY)
        self.assertNotIn("budget", _rules(alerts))


class TestConcentration(unittest.TestCase):
    """FPA-086: concentración top-3 de proyectos > 50% → alerta."""

    def test_fira_con_fixture(self):
        # top-3 = todos los proyectos con coste → 100% > 50%
        alerts = viz.build_alerts(f2.f2_fixture(), CONFIG, today=TODAY)
        ev = _by_rule(alerts, "concentration")
        self.assertEqual(1, len(ev))
        self.assertGreater(ev[0]["evidence"]["top3_share"], 0.5)

    def test_umbral_configurable(self):
        alerts = viz.build_alerts(f2.f2_fixture(), _cfg(concentration_top3_pct=101.0),
                                  today=TODAY)
        self.assertNotIn("concentration", _rules(alerts))


class TestStaleness(unittest.TestCase):
    """FPA-087: reporte con >14 días de antigüedad → alerta."""

    def test_fira_con_reporte_viejo(self):
        alerts = viz.build_alerts(f2.f2_fixture(), CONFIG, today="2026-08-01")
        ev = _by_rule(alerts, "staleness")
        self.assertEqual(1, len(ev))
        self.assertEqual(22, ev[0]["evidence"]["age_days"])

    def test_no_fira_fresco(self):
        alerts = viz.build_alerts(f2.f2_fixture(), CONFIG, today="2026-07-20")
        self.assertNotIn("staleness", _rules(alerts))

    def test_umbral_configurable(self):
        alerts = viz.build_alerts(f2.f2_fixture(), _cfg(staleness_days=5),
                                  today="2026-07-20")
        self.assertIn("staleness", _rules(alerts))


if __name__ == "__main__":
    unittest.main()
