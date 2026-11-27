#!/usr/bin/env python3
"""
test_fpa_f5_sessions.py — FPA-116…118: buckets de sesión, coste/mediana/p90
n/a y /clear por 100 sesiones.

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


class TestSessions(F5Base):

    def test_buckets_fpa116(self):
        """FPA-116: buckets 1–10, 11–50, 51–100, 100+ (101-300 y mayores
        se agregan en 100+)."""
        buckets = self.usage["sessions"]["buckets"]
        self.assertEqual(["1–10", "11–50", "51–100", "100+"],
                         [b["label"] for b in buckets])
        self.assertEqual([8, 7, 3, 2], [b["count"] for b in buckets])

    def test_longest_con_coste_na(self):
        """FPA-116: sesiones largas con turns/date/project; el coste por
        sesión no lo emite el tracker → None con razón (FPA-008)."""
        longest = self.usage["sessions"]["longest"]
        self.assertEqual(260, longest[0]["turns"])
        self.assertEqual("charly-coffee", longest[0]["project"])
        self.assertIsNone(longest[0]["cost"])
        self.assertTrue(longest[0]["cost_reason"])

    def test_coste_por_bucket_na(self):
        """FPA-117: sin coste por sesión → n/a con razón; igual mediana/p90."""
        s = self.usage["sessions"]
        self.assertIsNone(s["cost_by_bucket"])
        self.assertTrue(s["cost_by_bucket_reason"])
        self.assertIsNone(s["median_p90"])
        self.assertTrue(s["median_p90_reason"])

    def test_clear_por_100_sesiones(self):
        """FPA-118: /clear (5) / sesiones (20) × 100 = 25.0."""
        self.assertAlmostEqual(25.0, self.usage["sessions"]["clear_per_100"],
                               places=6)

    def test_clear_mensual_na_con_razon(self):
        """FPA-118: sin /clear por mes → n/a con razón."""
        s = self.usage["sessions"]
        self.assertIsNone(s["clear_monthly"])
        self.assertTrue(s["clear_monthly_reason"])

    def test_largas_sin_clear_na_con_razon(self):
        """FPA-118: el tracker no marca /clear por sesión → n/a con razón."""
        s = self.usage["sessions"]
        self.assertIsNone(s["long_no_clear"])
        self.assertTrue(s["long_no_clear_reason"])

    def test_umbral_largas_configurable(self):
        """FPA-118: el umbral de sesión larga viene del config (default 100)."""
        cfg = json.loads(json.dumps(CONFIG))
        cfg["sessions"] = {"long_turns": 200}
        usage = viz.build_usage_patterns(f5_fixture(), cfg)
        self.assertEqual(200, usage["sessions"]["long_turns"])


if __name__ == "__main__":
    unittest.main()
