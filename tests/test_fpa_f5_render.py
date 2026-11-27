#!/usr/bin/env python3
"""
test_fpa_f5_render.py — F5: Pareto con cola agrupada (FPA-028) y
concentración top-3 (FPA-036), render HTML de la vista Habits, edge cases
y golden de build_usage_patterns (FPA-101, REGEN_GOLDEN=1).

Parte de la antigua test_fpa_f5.py (coffe-kav.5); el fixture F5 vive en
tests/test_fpa_f5.py.
"""

import importlib.util
import json
import os
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GOLDEN = Path(__file__).resolve().parent / "golden" / "fpa-f5-snapshot.json"
REGEN_GOLDEN = os.environ.get("REGEN_GOLDEN") == "1"


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, REPO / relpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


viz = _load("viz_fpa_f5", "scripts/viz-fpa.py")
f2 = _load("test_fpa_f2", "tests/test_fpa_f2.py")
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


class TestPareto(F5Base):

    def test_filas_con_share_acumulado(self):
        """FPA-028: proyectos ordenados por coste desc con share y
        cumulative share."""
        rows = self.usage["pareto"]["rows"]
        self.assertEqual(["charly-coffee", "charly-atril", "charly-dont",
                          "proyecto-externo"], [r["name"] for r in rows])
        self.assertAlmostEqual(0.45, rows[0]["share"], places=6)
        self.assertAlmostEqual(0.45, rows[0]["cumulative_share"], places=6)
        self.assertAlmostEqual(0.75, rows[1]["cumulative_share"], places=6)
        self.assertAlmostEqual(1.0, rows[-1]["cumulative_share"], places=6)
        self.assertAlmostEqual(200.0, self.usage["pareto"]["total"], places=6)

    def test_cola_agrupada(self):
        """FPA-028: >10 proyectos → la cola se agrupa en una fila."""
        projects = {f"p{i:02d}": {"cost_effective": float(i * 10),
                                  "first_seen": "2026-05-01",
                                  "last_seen": "2026-07-01"}
                    for i in range(1, 13)}
        fx = f5_fixture()
        fx["projects"] = projects
        usage = viz.build_usage_patterns(fx, CONFIG)
        pareto = usage["pareto"]
        self.assertEqual(10, len(pareto["rows"]))
        self.assertEqual(2, pareto["tail"]["count"])
        self.assertAlmostEqual(30.0, pareto["tail"]["cost"], places=6)
        self.assertAlmostEqual(1.0, pareto["tail"]["cumulative_share"],
                               places=6)

    def test_concentracion_top3(self):
        """FPA-036: top-3 = 195 de 200 → 97.5% del coste efectivo."""
        self.assertAlmostEqual(0.975, self.usage["pareto"]["top3_share"],
                               places=6)

    def test_render_pareto(self):
        html = viz.render_html(f5_fixture(), CONFIG, generated="T")
        self.assertIn('id="pareto"', html)
        self.assertIn("Cola agrupada", html)  # FPA-028 en la sección


class TestRender(F5Base):
    @classmethod
    def setUpClass(cls):
        cls.html = viz.render_html(f5_fixture(), CONFIG, generated="T")

    def test_gap_days_configurable(self):
        """FPA-119: umbral de gap del config; 60 días → 52 ya no flag."""
        cfg = json.loads(json.dumps(CONFIG))
        cfg["timeline"] = {"gap_days": 60}
        usage = viz.build_usage_patterns(f5_fixture(), cfg)
        tl = {t["name"]: t for t in usage["timeline"]["tools"]}
        self.assertFalse(tl["codex"]["flagged"])

    def test_seccion_usage_patterns(self):
        # F6: los patrones viven en la vista Habits y skills/comandos van
        # mergeados en una sola vista (FPA-150/156)
        self.assertIn('id="habits"', self.html)
        self.assertIn('id="heatmap"', self.html)
        self.assertIn('id="skills-commands"', self.html)
        self.assertIn('id="timeline"', self.html)
        self.assertIn('id="lifecycle"', self.html)
        self.assertIn('id="weekly"', self.html)
        self.assertIn('id="concurrency"', self.html)

    def test_razones_na_en_html(self):
        """FPA-008: las razones n/a llegan al HTML, nunca celdas vacías."""
        self.assertIn("no emite", self.html)

    def test_etiquetas_concurrencia(self):
        """FPA-120: measures visibles en el HTML."""
        self.assertIn("parallel-agent", self.html)
        self.assertIn("human-context-switching", self.html)

    def test_determinismo(self):
        a = viz.render_html(f5_fixture(), CONFIG, generated="T")
        b = viz.render_html(f5_fixture(), CONFIG, generated="T")
        self.assertEqual(a, b)

    def test_provenance_tags(self):
        """FPA-003: cifras del modelo con tag; umbrales de config assumed."""
        self.assertIn('data-provenance="reported"', self.html)
        self.assertIn('data-provenance="assumed"', self.html)


class TestEdgeCases(unittest.TestCase):

    def test_reporte_minimo_no_explota(self):
        """Reporte casi vacío: sin hourly/daily/sessions/projects todo sale
        n/a con razón y el HTML renderiza sin placeholders."""
        fx = f2.f2_fixture()
        for key in ("hourly", "daily", "sessions", "projects", "skills",
                    "commands", "sessions_monthly", "project_monthly",
                    "multitasking"):
            fx.pop(key, None)
        usage = viz.build_usage_patterns(fx, CONFIG)
        self.assertEqual(0, usage["heatmap"]["total"])
        self.assertIsNone(usage["rhythm"]["after_hours_share"])
        self.assertIsNone(usage["rhythm"]["variance"]["interactions"])
        self.assertEqual([], usage["timeline"]["tools"])
        self.assertIsNone(usage["pareto"]["top3_share"])
        html = viz.render_html(fx, CONFIG, generated="T")
        self.assertEqual([], viz.check_placeholders(html))


class TestGolden(unittest.TestCase):
    """FPA-101: golden de build_usage_patterns sobre el fixture F5 (rico en
    hourly/daily/skills/sessions/projects). Regenerar:
    REGEN_GOLDEN=1 python3 tests/test_fpa_f5.py"""

    def test_golden_f5(self):
        model = viz.build_model(f5_fixture(), CONFIG)
        snapshot = {"usage": model["usage"]}
        if REGEN_GOLDEN:
            GOLDEN.parent.mkdir(parents=True, exist_ok=True)
            GOLDEN.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False,
                                         sort_keys=True) + "\n")
            self.skipTest("golden regenerado; revisar diff antes de aceptar")
        if not GOLDEN.exists():
            self.fail("Falta golden; corre REGEN_GOLDEN=1 python3 tests/test_fpa_f5.py")
        golden = json.loads(GOLDEN.read_text())
        self.assertEqual(golden, snapshot)


if __name__ == "__main__":
    unittest.main()
