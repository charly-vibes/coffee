#!/usr/bin/env python3
"""
test_fpa_gasto.py — vista "Gasto diario" con drill-down por sesión
(openspec add-session-spend-report, specs/fpa-dashboard)

- build_daily_spend(): día → total (delta vs media 7 días previos),
  desglose por proyecto, sesiones del día con flag medianoche.
- daily_spend_html(): selector de día con templates pre-calculados,
  provenance, nota de alcance del cash del ledger, razón en total 0.0
  con tokens positivos y en sesiones sin coste.
- render_html(): la vista existe como tab + sección (deep-link #gasto).

Run: python3 tests/test_fpa_gasto.py
"""

import importlib.util
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VIZ_PATH = REPO / "scripts" / "viz-fpa.py"


def load_viz():
    spec = importlib.util.spec_from_file_location("viz_fpa", VIZ_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


viz = load_viz()


def mini_report():
    """Reporte mínimo con las dos secciones nuevas (dataset sintético)."""
    tokens = {"in": 1000, "out": 500, "cache_read": 100, "cache_write": 20}
    return {
        "metadata": {"date_range": {"start": "2026-05-01", "end": "2026-05-12"},
                     "timezone": "-03"},
        "project_daily_spend": {
            "charly-coffee": {
                "2026-05-10": {"interactions": 2, "cost_effective": 0.02,
                               "cost_real": 0.0, "tokens": tokens},
                "2026-05-11": {"interactions": 1, "cost_effective": 0.01,
                               "cost_real": 0.0, "tokens": tokens},
            },
            "charly-atril": {
                "2026-05-10": {"interactions": 1, "cost_effective": 0.03,
                               "cost_real": 0.03, "tokens": tokens},
            },
        },
        "session_spend": [
            {"session_id": "s-1", "session_id_fallback": False,
             "project": "charly-coffee", "day": "2026-05-10",
             "start": "2026-05-10T14:23:00-03:00",
             "end": "2026-05-10T15:40:00-03:00", "crosses_midnight": False,
             "source": "claude-cli", "model": "claude-sonnet-4-6",
             "n_interactions": 2, "cost_effective": 0.02, "cost_real": 0.0,
             "cost_reason": None, "tokens": tokens},
            {"session_id": "s-2", "session_id_fallback": False,
             "project": "charly-atril", "day": "2026-05-10",
             "start": "2026-05-10T16:10:00-03:00",
             "end": "2026-05-10T16:30:00-03:00", "crosses_midnight": False,
             "source": "codex", "model": "gpt-5.4",
             "n_interactions": 1, "cost_effective": 0.03, "cost_real": 0.03,
             "cost_reason": None, "tokens": tokens},
            {"session_id": "s-3", "session_id_fallback": True,
             "project": "charly-coffee", "day": "2026-05-11",
             "start": "2026-05-11T09:00:00-03:00",
             "end": "2026-05-11T09:30:00-03:00", "crosses_midnight": False,
             "source": "claude-cli", "model": "mixed",
             "n_interactions": 1, "cost_effective": None, "cost_real": None,
             "cost_reason": "sin usage.cost en ningún turno (turnos abortados "
                            "o proveedor sin billing por token)",
             "tokens": {"in": 0, "out": 0, "cache_read": 0, "cache_write": 0}},
        ],
    }


class TestBuildDailySpend(unittest.TestCase):

    def setUp(self):
        self.ds = viz.build_daily_spend(mini_report())

    def test_dias_disponibles_ordenados(self):
        self.assertEqual(["2026-05-10", "2026-05-11"], self.ds["days_keys"])
        self.assertTrue(self.ds["available"])

    def test_total_y_delta_vs_media_7_previos(self):
        d1 = self.ds["days"]["2026-05-10"]
        self.assertAlmostEqual(0.05, d1["total_effective"], places=8)
        # sin 7 días previos → delta None (no 0 fake)
        self.assertIsNone(d1["delta7"])
        d2 = self.ds["days"]["2026-05-11"]
        self.assertAlmostEqual(0.01, d2["total_effective"], places=8)
        # media de los 7 días previos disponibles = 0.05 → delta -0.04
        self.assertAlmostEqual(-0.04, d2["delta7"], places=8)

    def test_desglose_por_proyecto(self):
        d1 = self.ds["days"]["2026-05-10"]
        projs = {p["project"]: p for p in d1["projects"]}
        self.assertEqual({"charly-coffee", "charly-atril"}, set(projs))
        self.assertAlmostEqual(0.02, projs["charly-coffee"]["cost_effective"],
                               places=8)
        self.assertAlmostEqual(0.0, projs["charly-coffee"]["cost_real"],
                               places=8)
        # la suma de la tabla cuadra con el total del día
        self.assertAlmostEqual(
            d1["total_effective"],
            sum(p["cost_effective"] for p in d1["projects"]), places=8)

    def test_drill_down_sesiones_del_dia(self):
        d1 = self.ds["days"]["2026-05-10"]
        self.assertEqual(2, len(d1["sessions"]))
        s1 = next(s for s in d1["sessions"] if s["session_id"] == "s-1")
        self.assertEqual("charly-coffee", s1["project"])
        self.assertIn("14:23", s1["start"])
        self.assertFalse(s1["crosses_midnight"])

    def test_sesion_sin_coste_muestra_razon(self):
        d2 = self.ds["days"]["2026-05-11"]
        s3 = d2["sessions"][0]
        self.assertIsNone(s3["cost_effective"])
        self.assertIn("sin usage.cost", s3["cost_reason"])

    def test_total_cero_con_tokens_positivos_tiene_razon(self):
        rep = mini_report()
        rep["session_spend"] = [
            s for s in rep["session_spend"] if s["day"] == "2026-05-11"]
        rep["project_daily_spend"] = {
            p: {d: v for d, v in days.items() if d == "2026-05-11"}
            for p, days in rep["project_daily_spend"].items()
            if "2026-05-11" in days}
        # día con tokens pero coste efectivo 0 (modelos sin pricing)
        day = rep["project_daily_spend"]["charly-coffee"]["2026-05-11"]
        day["cost_effective"] = 0.0
        ds = viz.build_daily_spend(rep)
        self.assertIn("razon", ds["days"]["2026-05-11"])
        self.assertTrue(ds["days"]["2026-05-11"]["razon"])

    def test_reporte_sin_secciones_no_disponible_con_razon(self):
        ds = viz.build_daily_spend({"metadata": {}})
        self.assertFalse(ds["available"])
        self.assertTrue(ds["reason"])


class TestDailySpendHtml(unittest.TestCase):

    def setUp(self):
        self.html = viz.daily_spend_html(viz.build_daily_spend(mini_report()))

    def test_selector_con_templates_por_dia(self):
        self.assertIn('id="daily-spend-day"', self.html)
        self.assertIn('data-daily-day="2026-05-10"', self.html)
        self.assertIn('data-daily-day="2026-05-11"', self.html)

    def test_provenance_y_alcance_cash(self):
        self.assertIn("assumed", self.html)
        self.assertIn("ledger", self.html)  # nota: cash del ledger concilia mensual

    def test_razon_visible_para_sesion_sin_coste(self):
        self.assertIn("sin usage.cost", self.html)

    def test_vacio_con_razon_cuando_no_hay_datos(self):
        html = viz.daily_spend_html(
            {"available": False, "reason": "sin secciones en el reporte"})
        self.assertIn("n/a", html)
        self.assertIn("sin secciones en el reporte", html)


class TestViewWiring(unittest.TestCase):

    def _full_mini(self):
        """Reporte del dataset congelado + secciones nuevas sintéticas.

        Usar el fixture congelado (mismo pin que los goldens del dashboard)
        evita duplicar el contrato completo del metadata en el test.
        """
        import json
        rep = json.loads((REPO / "tests" / "fixtures" / "usage-report-frozen.json")
                         .read_text())
        rep.update({k: v for k, v in mini_report().items()
                    if k in ("project_daily_spend", "session_spend")})
        return rep

    def test_render_html_incluye_vista_y_tab(self):
        import sys; sys.path.insert(0, str(REPO / 'scripts'))
        import fpa_config
        cfg = fpa_config.load_fpa_config()
        html = viz.render_html(self._full_mini(), cfg)
        self.assertIn('id="gasto"', html)
        self.assertIn("Gasto diario", html)
        self.assertIn('data-view="gasto"', html)
        self.assertIn('id="daily-spend-day"', html)
        # deep-link: ?view=gasto&day=... restaura el día (inválidos ignorados)
        self.assertIn('q.get("day")', html)
        self.assertIn('"breakdown", "gasto", "habits"', html)


if __name__ == "__main__":
    unittest.main()
