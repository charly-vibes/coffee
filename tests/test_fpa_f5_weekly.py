#!/usr/bin/env python3
"""
test_fpa_f5_weekly.py — FPA-112: serie semanal WoW y varianza poblacional.

Parte de la antigua test_fpa_f5.py (coffe-kav.5); el fixture F5 vive en
tests/test_fpa_f5.py.
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


viz = _load("viz_fpa_f5", "scripts/viz-fpa.py")
f5 = _load("test_fpa_f5", "tests/test_fpa_f5.py")
f5_fixture = f5.f5_fixture
CONFIG = json.loads((REPO / "config" / "fpa.json").read_text())


class F5Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = f5_fixture()
        cls.usage = viz.build_usage_patterns(cls.fixture, CONFIG)

    def _by_name(self, items, name):
        return next(x for x in items if x["name"] == name)


class TestWeekly(F5Base):

    def test_series_semanal_excluye_bordes_parciales(self):
        """FPA-112: semanas ISO con datos; la primera (W19, solo dom) y la
        última (W28, solo vie) van marcadas partial y no entran a la
        varianza. Semanas sin datos se omiten (convención FPA-017)."""
        weeks = self.usage["rhythm"]["weeks"]
        self.assertEqual(["2026-05-04", "2026-05-11", "2026-06-01",
                          "2026-06-29", "2026-07-06"],
                         [w["start"] for w in weeks])
        self.assertTrue(weeks[0]["partial"])
        self.assertTrue(weeks[-1]["partial"])
        self.assertFalse(weeks[1]["partial"])

    def test_valores_semanales(self):
        weeks = self.usage["rhythm"]["weeks"]
        w20 = weeks[1]
        self.assertEqual(50, w20["interactions"])
        self.assertAlmostEqual(10.0, w20["cost_effective"], places=6)

    def test_wow(self):
        """FPA-112: WoW de W23 vs W20 = +100% int; W27 = -30% int y
        -100% coste. Primera semana sin prior → None."""
        weeks = self.usage["rhythm"]["weeks"]
        self.assertIsNone(weeks[0]["wow_interactions"])
        self.assertAlmostEqual(1.0, weeks[2]["wow_interactions"], places=6)
        self.assertAlmostEqual(-0.30, weeks[3]["wow_interactions"], places=6)
        self.assertAlmostEqual(-1.0, weeks[3]["wow_cost"], places=6)

    def test_varianza_semanal(self):
        """FPA-112: varianza poblacional sobre semanas completas.
        int [50,100,70] → 422.22; coste [10,60,0] → 688.89."""
        var = self.usage["rhythm"]["variance"]
        self.assertAlmostEqual(422.222, var["interactions"], places=2)
        self.assertAlmostEqual(688.889, var["cost_effective"], places=2)
        self.assertEqual(3, var["weeks_count"])

    def test_varianza_sin_semanas_suficientes(self):
        """FPA-008: con <3 semanas completas → n/a con razón."""
        fx = f5_fixture()
        fx["daily"] = {"2026-05-11": {"interactions": 50,
                                      "cost_effective": 10.0}}
        usage = viz.build_usage_patterns(fx, CONFIG)
        self.assertIsNone(usage["rhythm"]["variance"]["interactions"])
        self.assertTrue(usage["rhythm"]["variance"]["reason"])


if __name__ == "__main__":
    unittest.main()
