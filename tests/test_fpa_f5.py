#!/usr/bin/env python3
"""
test_fpa_f5.py — tests de viz-fpa.py F5 (ticket coffe-lat.6): fixture F5,
heatmap día×hora + timezone (FPA-110/142) y after-hours/weekend (FPA-111).

(coffe-kav.5: weekly en test_fpa_f5_weekly.py; skills/commands y sesiones
en test_fpa_f5_skills.py y test_fpa_f5_sessions.py; concurrencia/agent
share/lifecycle en test_fpa_f5_concurrency.py; Pareto/render/golden en
test_fpa_f5_render.py.)
El drill Project→Model (FPA-021) ya está cubierto por test_fpa_f2
(árbol Portfolio, test_drill_model_solo_periodo_completo).
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
f2 = _load("test_fpa_f2", "tests/test_fpa_f2.py")
CONFIG = json.loads((REPO / "config" / "fpa.json").read_text())


def _hour(interactions, projects_active=1, tools=None, models=None):
    return {
        "interactions": interactions, "input_tokens": 0, "output_tokens": 0,
        "cache_read_tokens": 0, "cache_write_tokens": 0,
        "cost_effective": 0.0, "cost_real": 0.0,
        "tools": tools or {}, "models": models or {},
        "projects_active": projects_active,
    }


def _f5_hourly():
    """Hourly rica: dom 14h (60, weekend+after-hours), lun 9h (40),
    lun 22h (10, after-hours), jue 10h (70, en horario)."""
    return {
        "2026-05-10 14:00": _hour(
            60, 2,
            tools={"claude-cli": {"req": 60, "in": 0, "out": 0, "cache_read": 0,
                                  "cache_write": 0, "cost_eff": 0.0,
                                  "cost_real": 0.0}},
            models={"claude-sonnet-4-6": 40, "claude-opus-4.7": 20}),
        "2026-05-11 09:00": _hour(
            40, 1,
            tools={"codex": {"req": 40, "in": 0, "out": 0, "cache_read": 0,
                             "cache_write": 0, "cost_eff": 0.0,
                             "cost_real": 0.0}},
            models={"gpt-5.4": 40}),
        "2026-05-11 22:00": _hour(
            10, 1,
            tools={"claude-cli": {"req": 10, "in": 0, "out": 0, "cache_read": 0,
                                  "cache_write": 0, "cost_eff": 0.0,
                                  "cost_real": 0.0}},
            models={"claude-sonnet-4-6": 10}),
        "2026-07-02 10:00": _hour(
            70, 1,
            tools={"codex": {"req": 70, "in": 0, "out": 0, "cache_read": 0,
                             "cache_write": 0, "cost_eff": 0.0,
                             "cost_real": 0.0}},
            models={"gpt-5.4": 70}),
    }


def _f5_sessions():
    """Skills/commands, sesiones con shape multi-tool (coffe-i31) y
    sessions_monthly."""
    return {
        "skills": {"commit": 75, "tdd": 4, "ro5": 1, "wrap-up": 1},
        "commands": {"/clear": 5, "/model": 2},
        "sessions": {
            "total_sessions": 20, "with_agent": 5,
            # coffe-i31: shape multi-tool — clear_per_100 usa denominador
            # claude-only y el /clear viene de by_tool.claude-cli.resets
            "agent_semantics": "claude-cli: sesión con herramienta Agent",
            "by_tool": {
                "claude-cli": {"total": 20, "with_agent": 5,
                               "resets": {"signal": "/clear en history.jsonl",
                                          "count": 5}},
            },
            "length_distribution": {"1-10": 8, "11-50": 7, "51-100": 3,
                                    "101-300": 2},
            "top_longest_by_turns": [
                {"turns": 260, "msgs": 180, "date": "2026-05-12",
                 "project": "charly-coffee"},
                {"turns": 120, "msgs": 80, "date": "2026-06-03",
                 "project": "charly-atril"},
            ],
        },
        "sessions_monthly": {"2026-05": {"total": 10, "with_agent": 2},
                             "2026-06": {"total": 10, "with_agent": 3}},
    }


def _f5_projects():
    """Projects con first/last seen (lifecycle + Pareto) y multitasking."""
    return {
        "projects": {
            "charly-coffee": {"cost_effective": 90.0,
                              "first_seen": "2026-07-01",
                              "last_seen": "2026-07-10"},
            "charly-atril": {"cost_effective": 60.0,
                             "first_seen": "2026-05-01",
                             "last_seen": "2026-06-01"},
            "charly-dont": {"cost_effective": 45.0,
                            "first_seen": "2026-06-01",
                            "last_seen": "2026-07-08"},
            "proyecto-externo": {"cost_effective": 5.0,
                                 "first_seen": "2026-05-05",
                                 "last_seen": "2026-07-05"},
        },
        "multitasking": {
            "context_switches": {"total": 30},
            "hourly": {"total_active_hours": 6},
        },
    }


def f5_fixture():
    """Fixture F5: extiende f2_fixture con hourly rica (heatmap, after-hours,
    concurrencia, timeline), daily (semanas), skills/commands, sesiones y
    projects con first/last seen (lifecycle + Pareto).

    Horario laboral config: Mon–Fri 09:00–18:00."""
    fx = f2.f2_fixture()
    fx["hourly"] = _f5_hourly()
    fx["daily"] = {
        "2026-05-10": {"interactions": 60, "cost_effective": 40.0},
        "2026-05-11": {"interactions": 50, "cost_effective": 10.0},
        "2026-06-03": {"interactions": 100, "cost_effective": 60.0},
        "2026-07-02": {"interactions": 70, "cost_effective": 0.0},
        "2026-07-10": {"interactions": 80, "cost_effective": 30.0},
    }
    fx.update(_f5_sessions())
    fx.update(_f5_projects())
    fx.pop("concurrency", None)  # F5 computa desde hourly/multitasking
    return fx


class F5Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = f5_fixture()
        cls.usage = viz.build_usage_patterns(cls.fixture, CONFIG)

    def _by_name(self, items, name):
        return next(x for x in items if x["name"] == name)


# ======================================================================
# Heatmap día×hora (FPA-110) + timezone (FPA-142)
# ======================================================================

class TestHeatmap(F5Base):

    def test_grid_7x24(self):
        grid = self.usage["heatmap"]["grid"]
        self.assertEqual(7, len(grid))
        for row in grid:
            self.assertEqual(24, len(row))

    def test_celdas(self):
        """dom 14h=60, lun 9h=40, lun 22h=10, jue 10h=70."""
        grid = self.usage["heatmap"]["grid"]
        self.assertEqual(60, grid[6][14])   # domingo (isoweekday 7)
        self.assertEqual(40, grid[0][9])    # lunes
        self.assertEqual(10, grid[0][22])
        self.assertEqual(70, grid[3][10])   # jueves
        self.assertEqual(180, self.usage["heatmap"]["total"])

    def test_timezone_verificada(self):
        """FPA-110/142: timezone del tracker etiquetada."""
        hm = self.usage["heatmap"]
        self.assertTrue(hm["verified"])
        self.assertEqual("America/Argentina/Buenos_Aires", hm["timezone"])

    def test_timezone_ausente_unverified(self):
        """FPA-142: sin timezone, la vista hora/día va marcada unverified."""
        fx = f5_fixture()
        del fx["metadata"]["timezone"]
        usage = viz.build_usage_patterns(fx, CONFIG)
        self.assertFalse(usage["heatmap"]["verified"])
        self.assertTrue(usage["heatmap"]["unverified_reason"])
        self.assertFalse(usage["timeline"]["verified"])

    def test_render_unverified(self):
        """FPA-142: el HTML muestra la marca unverified, no la tz."""
        fx = f5_fixture()
        del fx["metadata"]["timezone"]
        html = viz.render_html(fx, CONFIG, generated="T")
        self.assertIn("unverified", html)
        self.assertNotIn("America/Argentina", html)


# ======================================================================
# After-hours / weekend (FPA-111)
# ======================================================================

class TestRhythm(F5Base):

    def test_after_hours_share(self):
        """FPA-111: fuera de Mon–Fri 09:00–18:00 = dom 14h (60) + lun 22h
        (10) = 70 de 180 → 38.9%."""
        self.assertAlmostEqual(70 / 180,
                               self.usage["rhythm"]["after_hours_share"],
                               places=4)

    def test_weekend_share(self):
        """FPA-111: domingo 60 de 180 → 33.3%."""
        self.assertAlmostEqual(60 / 180,
                               self.usage["rhythm"]["weekend_share"], places=4)

    def test_horario_configurable(self):
        """FPA-111: working_hours del config; con horario ampliado el
        share baja."""
        cfg = json.loads(json.dumps(CONFIG))
        cfg["working_hours"] = {"days": [1, 2, 3, 4, 5, 6, 7],
                                "start": "00:00", "end": "22:00"}
        usage = viz.build_usage_patterns(f5_fixture(), cfg)
        # todo cae "en horario" salvo la hora 22–23 (end excluyente)
        self.assertAlmostEqual(10 / 180, usage["rhythm"]["after_hours_share"],
                               places=4)


if __name__ == "__main__":
    unittest.main()
