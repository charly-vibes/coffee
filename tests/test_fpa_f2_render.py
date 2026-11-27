#!/usr/bin/env python3
"""
test_fpa_f2_render.py — render del HTML F2: KPI cards, árboles, data,
selector, sparklines.

Parte de la antigua test_fpa_f2.py (coffe-kav.5); el fixture y el config
viven en tests/test_fpa_f2.py.
"""

import importlib.util
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, REPO / relpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


f2 = _load("test_fpa_f2", "tests/test_fpa_f2.py")
viz = f2.viz
CONFIG = f2.CONFIG


class TestRenderF2(unittest.TestCase):
    """Render del HTML F2: KPI cards, árboles, data, selector, sparklines."""

    @classmethod
    def setUpClass(cls):
        cls.html = viz.render_html(f2.f2_fixture(), CONFIG, generated="2026-09-20 12:00")

    def test_kpi_cards_renderizadas(self):
        self.assertGreaterEqual(self.html.count('class="headline kpi"'), 12)

    def test_arboles_con_columnas(self):
        for col in ("% total", "$/1k", "Δ vs prior", "Presupuesto*", "Varianza"):
            self.assertIn(col, self.html)

    def test_selector_de_periodo(self):
        self.assertIn('id="period-select"', self.html)
        self.assertIn('<option value="all"', self.html)
        self.assertIn("month:2026-06", self.html)

    def test_sparklines_svg(self):
        self.assertIn('class="spark"', self.html)
        self.assertIn("<polyline", self.html)

    def test_provenance_en_kpis(self):
        self.assertIn('data-provenance="assumed"', self.html)

    def test_seccion_data(self):
        self.assertIn("Datos y definiciones", self.html)
        self.assertIn("tool calls", self.html)

    def test_js_solo_reescala(self):
        """design.md: sin fórmulas de negocio en JS — solo re-render."""
        js = self.html.split("<script>")[-1]
        for forbidden in ("cost_effective", "cache_read", "budgets", "fme("):
            self.assertNotIn(forbidden, js, f"lógica de negocio en JS: {forbidden}")

    def test_todos_los_kpis_llevan_valor_o_na(self):
        """FPA-108: ninguna card KPI vacía."""
        self.assertNotIn('<span class="val"></span>', self.html)


if __name__ == "__main__":
    unittest.main()
