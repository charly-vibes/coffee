#!/usr/bin/env python3
"""
test_fpa_f4_economy.py — economía de suscripción (FPA-130…133) y render
HTML del F4.

Parte de la antigua test_fpa_f4.py (coffe-kav.5); el fixture y el config
viven en tests/test_fpa_f2.py.
"""

import importlib.util
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
CONFIG = f2.CONFIG


class TestPlanEconomy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = f2.f2_fixture()
        cls.econ = viz.build_plan_economy(cls.fixture, CONFIG)

    def test_panel_por_plan(self):
        """FPA-130: un panel por cada periodo del calendario de suscripciones
        (calendario corregido a facturas — coffe-a31)."""
        keys = [(p["tool"], p["start"]) for p in self.econ["plans"]]
        self.assertIn(("claude-cli", "2026-03-19"), keys)  # Max $100
        self.assertIn(("claude-cli", "2026-04-19"), keys)  # Max $100
        self.assertIn(("claude-cli", "2026-05-19"), keys)  # Pro $20
        self.assertIn(("codex", "2026-04-02"), keys)  # ChatGPT Plus $20
        self.assertIn(("codex", "2026-05-02"), keys)  # ChatGPT Plus $20
        self.assertIn(("gemini-cli", "2025-12-03"), keys)  # Google AI Pro $19.99

    def test_utilizacion_formula(self):
        """FPA-130: utilización = coste efectivo del periodo ÷ precio del plan.

        Abr19–May19 (Max $100/mes, calendario corregido a facturas): el
        hourly del fixture registra $40 de efectivo claude en el periodo,
        precio pro-rateado ~$98 → util ≈ 0.41."""
        p = next(p for p in self.econ["plans"]
                 if p["tool"] == "claude-cli" and p["start"] == "2026-04-19")
        self.assertAlmostEqual(40.0, p["eff_cost"])
        self.assertAlmostEqual(p["eff_cost"] / p["price"], p["utilization"],
                               places=4)
        self.assertAlmostEqual(0.41, p["utilization"], places=2)
        self.assertEqual("assumed", p["provenance"])  # FPA-003

    def test_utilizacion_na_sin_datos_horarios(self):
        """FPA-008: sin hourly para el periodo → n/a con razón, nunca vacío."""
        p = next(p for p in self.econ["plans"]
                 if p["tool"] == "claude-cli" and p["start"] == "2026-03-19")
        self.assertIsNone(p["eff_cost"])
        self.assertIsNone(p["utilization"])
        self.assertTrue(p["utilization_reason"])

    def test_breakeven_y_headroom(self):
        """FPA-131: break-even = precio mensual del plan; headroom = fee − efectivo."""
        p = next(p for p in self.econ["plans"]
                 if p["tool"] == "claude-cli" and p["start"] == "2026-04-19")
        self.assertEqual(100.0, p["break_even"])
        self.assertAlmostEqual(60.0, p["headroom"], places=1)

    def test_cuatro_casos(self):
        """FPA-132: cash del periodo bajo 4 escenarios, jamás mezclados (FPA-002)."""
        cases = self.econ["four_cases"]
        self.assertEqual({"actual", "todo_pay_per_token", "todo_pro", "todo_max"},
                         set(cases))
        self.assertAlmostEqual(45.0, cases["actual"])          # Σ cost_real
        self.assertAlmostEqual(145.0, cases["todo_pay_per_token"])  # Σ efectivo
        # 3 meses con claude activo × $20 Pro + $15 cash no-claude
        self.assertAlmostEqual(75.0, cases["todo_pro"])
        # 3 meses × $100 Max + $15 cash no-claude
        self.assertAlmostEqual(315.0, cases["todo_max"])
        self.assertEqual("assumed", self.econ["four_cases_provenance"])

    def test_disclaimer_usage_limits(self):
        """FPA-133: el modelo y el HTML declaran que se ignoran usage limits."""
        self.assertTrue(self.econ["usage_limits_disclaimer"])
        html = viz.render_html(self.fixture, CONFIG, generated="2026-09-20 12:00")
        self.assertIn("usage limits", html)


class TestRenderF4(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = f2.f2_fixture()
        cls.html = viz.render_html(cls.fixture, CONFIG, generated="2026-09-20 12:00")

    def test_seccion_alertas(self):
        self.assertIn('id="alerts"', self.html)

    def test_seccion_economia(self):
        self.assertIn('id="plan-economy"', self.html)
        self.assertIn("todo_pro", self.html)

    def test_alertas_en_html(self):
        """FPA-086: la concentración del fixture debe verse en el HTML."""
        self.assertIn("concentration", self.html)

    def test_determinismo_salvo_timestamp(self):
        a = viz.render_html(self.fixture, CONFIG, generated="T",
                            today=TODAY)
        b = viz.render_html(self.fixture, CONFIG, generated="T",
                            today=TODAY)
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
