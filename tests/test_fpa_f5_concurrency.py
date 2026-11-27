#!/usr/bin/env python3
"""
test_fpa_f5_concurrency.py — FPA-120/121: concurrencia etiquetada
(paralelo vs context switching), share de sesiones con Agent y lifecycle
new/active/dormant con coste dormante + overlay de repos (FPA-098).

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


class TestConcurrency(F5Base):

    def test_proyectos_por_hora_parallel_agent(self):
        """FPA-120a: proyectos distintos/hora etiquetado parallel-agent.
        Horas activas: 2,1,1,1 → pico 2, media 1.25."""
        c = self.usage["concurrency"]["projects_per_hour"]
        self.assertEqual("parallel-agent", c["measure"])
        self.assertEqual(2, c["peak"])
        self.assertAlmostEqual(1.25, c["avg"], places=6)

    def test_sesiones_simultaneas_na_con_razon(self):
        """FPA-120b: el tracker no lo emite → n/a con razón, etiqueta
        parallel-agent."""
        c = self.usage["concurrency"]["peak_simultaneous_sessions"]
        self.assertEqual("parallel-agent", c["measure"])
        self.assertIsNone(c["peak"])
        self.assertTrue(c["reason"])

    def test_concurrencia_emitida_por_tracker(self):
        """FPA-120: si el tracker emite `concurrency`, se usa tal cual."""
        fx = f5_fixture()
        fx["concurrency"] = {
            "distinct_projects_per_hour": {"measure": "parallel-agent",
                                           "peak": 5, "avg": 2.0},
            "peak_simultaneous_sessions": {"measure": "parallel-agent",
                                           "peak": 3, "reason": None},
            "project_switches_per_active_hour": {
                "measure": "human-context-switching", "value": 7.5},
        }
        usage = viz.build_usage_patterns(fx, CONFIG)
        c = usage["concurrency"]
        self.assertEqual(5, c["projects_per_hour"]["peak"])
        self.assertEqual(3, c["peak_simultaneous_sessions"]["peak"])
        self.assertAlmostEqual(7.5, c["switches_per_hour"]["value"], places=6)

    def test_switches_context_switching(self):
        """FPA-120c: switches/hora activa etiquetado human-context-switching.
        30 switches / 6 horas activas = 5.0."""
        c = self.usage["concurrency"]["switches_per_hour"]
        self.assertEqual("human-context-switching", c["measure"])
        self.assertAlmostEqual(5.0, c["value"], places=6)


class TestAgentShare(F5Base):

    def test_share_total(self):
        self.assertAlmostEqual(5 / 20, self.usage["agent_share"]["share"],
                               places=6)

    def test_trend_mensual(self):
        """FPA-121: con sessions_monthly, trend mensual de shares."""
        monthly = self.usage["agent_share"]["monthly"]
        self.assertEqual(["2026-05", "2026-06"], [m["ym"] for m in monthly])
        self.assertAlmostEqual(0.2, monthly[0]["share"], places=6)
        self.assertAlmostEqual(0.3, monthly[1]["share"], places=6)

    def test_trend_na_con_razon(self):
        """FPA-008/121: sin sessions_monthly → n/a con razón."""
        fx = f5_fixture()
        del fx["sessions_monthly"]
        usage = viz.build_usage_patterns(fx, CONFIG)
        self.assertIsNone(usage["agent_share"]["monthly"])
        self.assertTrue(usage["agent_share"]["monthly_reason"])


class TestLifecycle(F5Base):

    def test_clasificacion(self):
        """FPA-122: vs fecha fin del periodo (2026-07-10), new ≤30 días,
        dormant >30 días sin actividad."""
        status = {p["name"]: p["status"]
                  for p in self.usage["lifecycle"]["projects"]}
        self.assertEqual("new", status["charly-coffee"])       # 9 días
        self.assertEqual("dormant", status["charly-atril"])   # 39 días
        self.assertEqual("active", status["charly-dont"])
        self.assertEqual("active", status["proyecto-externo"])

    def test_coste_dormante(self):
        """FPA-123: coste efectivo de dormantes, total y por proyecto."""
        d = self.usage["lifecycle"]["dormant"]
        self.assertEqual(1, d["count"])
        self.assertAlmostEqual(60.0, d["cost_total"], places=6)
        self.assertAlmostEqual(60.0, d["cost_per_project"], places=6)

    def test_activos_por_mes(self):
        """FPA-123: con project_monthly, activos por mes (proyectos con
        actividad)."""
        by_month = {m["ym"]: m["count"]
                    for m in self.usage["lifecycle"]["active_by_month"]}
        self.assertEqual(3, by_month["2026-05"])
        self.assertEqual(2, by_month["2026-07"])

    def test_activos_por_mes_na(self):
        fx = f5_fixture()
        del fx["project_monthly"]
        usage = viz.build_usage_patterns(fx, CONFIG)
        self.assertIsNone(usage["lifecycle"]["active_by_month"])
        self.assertTrue(usage["lifecycle"]["active_by_month_reason"])

    def test_new_dormant_configurable(self):
        """FPA-122: umbrales del config.lifecycle (new_days/dormant_days)."""
        cfg = json.loads(json.dumps(CONFIG))
        cfg["lifecycle"] = {"new_days": 5, "dormant_days": 10}
        usage = viz.build_usage_patterns(f5_fixture(), cfg)
        status = {p["name"]: p["status"]
                  for p in usage["lifecycle"]["projects"]}
        # coffe: 9 días ya no es new (new_days=5); atril: 39 días dormant
        self.assertEqual("active", status["charly-coffee"])
        self.assertEqual("dormant", status["charly-atril"])

    def test_overlay_repos_na(self):
        """FPA-098: sin fechas de creación de repos → overlay omitido con
        razón (requisito condicional)."""
        ov = self.usage["repo_overlay"]
        self.assertFalse(ov["available"])
        self.assertTrue(ov["reason"])
        html = viz.render_html(f5_fixture(), CONFIG, generated="T")
        self.assertNotIn('id="repo-overlay"', html)
        self.assertIn("creación de repos", html)


if __name__ == "__main__":
    unittest.main()
