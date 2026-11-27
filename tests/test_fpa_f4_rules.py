#!/usr/bin/env python3
"""
test_fpa_f4_rules.py — FPA-084/085/086: reglas unit-cost, mix premium y
concentración del motor de alertas.

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


def _fixture_with(**over):
    fx = f2.f2_fixture()
    for key, val in over.items():
        if val is None:
            fx.pop(key, None)
        else:
            fx[key] = val
    return fx


def _rules(alerts):
    return [a["rule"] for a in alerts]


def _by_rule(alerts, rule):
    return [a for a in alerts if a["rule"] == rule]


class TestUnitCost(unittest.TestCase):
    """FPA-084: coste por 1k interacciones +15% MoM → alerta."""

    def setUp(self):
        self.fx = f2.f2_fixture()
        # unit cost may=500, jun=650 (+30%), jul=300 (−54%)

    def test_fira_en_may_to_jun(self):
        alerts = viz.build_alerts(self.fx, CONFIG, today=TODAY)
        ev = _by_rule(alerts, "unit-cost")
        self.assertEqual(1, len(ev))
        self.assertEqual("2026-06", ev[0]["evidence"]["month"])
        self.assertAlmostEqual(500.0, ev[0]["evidence"]["prev_per_1k"])
        self.assertAlmostEqual(650.0, ev[0]["evidence"]["cur_per_1k"])

    def test_umbral_configurable(self):
        alerts = viz.build_alerts(self.fx, _cfg(unit_cost_rise_pct=50.0), today=TODAY)
        self.assertNotIn("unit-cost", _rules(alerts))

    def test_mes_sin_datos_excluido(self):
        """FPA-017: un mes sin datos no genera comparación MoM."""
        fx = self.fx
        fx["monthly"]["2026-06"]["interactions"] = 0
        alerts = viz.build_alerts(fx, CONFIG, today=TODAY)
        self.assertNotIn("unit-cost", _rules(alerts))

    def test_base_casi_nula_no_dispara(self):
        """coffe-wxc: base previa bajo el mínimo ($5/1k default) no dispara
        — el MoM sobre base casi nula es falso positivo (+3871% sobre $1.45)."""
        fx = self.fx
        fx["monthly"]["2026-06"]["cost_effective"] = 0.4  # $4/1k < $5
        alerts = viz.build_alerts(fx, CONFIG, today=TODAY)
        self.assertNotIn("unit-cost", _rules(alerts))

    def test_base_sobre_el_minimo_sigue_disparando(self):
        """Base $500/1k >> mínimo → el alerta may→jun se mantiene."""
        alerts = viz.build_alerts(self.fx, CONFIG, today=TODAY)
        self.assertEqual(1, len(_by_rule(alerts, "unit-cost")))

    def test_base_minima_configurable(self):
        fx = self.fx
        fx["monthly"]["2026-06"]["cost_effective"] = 0.4  # $4/1k
        alerts = viz.build_alerts(
            fx, _cfg(unit_cost_min_base_per_1k=1.0), today=TODAY)
        self.assertIn("unit-cost", _rules(alerts))


class TestPremiumMix(unittest.TestCase):
    """FPA-085: share premium +5pts en 3 meses → alerta."""

    def _four_month_fixture(self, jul_opus_cost):
        """Fixture de 4 meses (abr–jul) para tener ventana de 3 meses."""
        fx = _fixture_with()
        abr = {
            "interactions": 100, "input_tokens": 100000, "output_tokens": 50000,
            "cache_read_tokens": 40000, "cache_write_tokens": 10000,
            "cost_effective": 40.0, "cost_real": 20.0,
            "tools": {"claude-cli": {"interactions": 100, "cost_effective": 40.0,
                                     "models": {}}},
            "models": {"claude-sonnet-4-6": {"interactions": 100,
                                             "cost_effective": 40.0}},
            "subscription_fees": 20.0,
        }
        fx["monthly"]["2026-04"] = abr
        if jul_opus_cost is not None:
            fx["monthly"]["2026-07"]["models"]["claude-opus-4.7"] = {
                "interactions": 10, "cost_effective": jul_opus_cost}
        return fx

    def test_no_fira_con_pocos_meses(self):
        """Ventana de 3 meses requiere ≥4 meses con datos."""
        alerts = viz.build_alerts(f2.f2_fixture(), CONFIG, today=TODAY)
        self.assertNotIn("mix", _rules(alerts))

    def test_fira_con_salto_premium(self):
        # abr 0% premium; jul 12/42 = 28.6% → +28.6pts > 5pts
        alerts = viz.build_alerts(self._four_month_fixture(12.0), CONFIG,
                                  today=TODAY)
        ev = _by_rule(alerts, "mix")
        self.assertEqual(1, len(ev))
        self.assertEqual("2026-07", ev[0]["evidence"]["month"])
        self.assertAlmostEqual(28.57, ev[0]["evidence"]["rise_pts"], places=1)

    def test_no_fira_si_plano(self):
        alerts = viz.build_alerts(self._four_month_fixture(0.5), CONFIG,
                                  today=TODAY)
        self.assertNotIn("mix", _rules(alerts))

    def test_umbral_configurable(self):
        alerts = viz.build_alerts(self._four_month_fixture(12.0),
                                  _cfg(premium_share_rise_pts=50.0), today=TODAY)
        self.assertNotIn("mix", _rules(alerts))


if __name__ == "__main__":
    unittest.main()
