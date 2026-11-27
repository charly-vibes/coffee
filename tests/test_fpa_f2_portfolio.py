#!/usr/bin/env python3
"""
test_fpa_f2_portfolio.py — FPA-018/020/021/026/027/101: árbol Portfolio
con taxonomía y drill, y vistas pre-calculadas por periodo.

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


def _raw_tree(fixture, cfg, name):
    if name == "time":
        return viz.build_time_tree(fixture, cfg)
    if name == "tool":
        return viz.build_tool_tree(fixture)
    return viz.build_portfolio_tree(fixture, cfg)


class _PortfolioBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = f2.f2_fixture()
        cls.model = viz.build_model(cls.fixture, CONFIG)


class TestPortfolioTree(_PortfolioBase):
    """FPA-018/020/021/027: taxonomía, drill y limitación."""

    def test_categorias_y_unclassified(self):
        tree = self.model["views"]["all"]["trees"]["portfolio"]
        labels = {c["label"] for c in tree["children"]}
        self.assertIn("charly", labels)
        self.assertIn("Unclassified", labels)

    def test_drill_model_solo_periodo_completo(self):
        """FPA-021/027: drill Project→Model solo en la vista 'all'."""
        full = self.model["views"]["all"]["trees"]["portfolio"]
        charly = next(c for c in full["children"] if c["label"] == "charly")
        coffee = next(c for c in charly["children"] if c["label"] == "charly-coffee")
        self.assertEqual({"claude-sonnet-4-6", "claude-opus-4.7"},
                         {c["label"] for c in coffee["children"]})
        for c in coffee["children"]:
            self.assertTrue(c["totals_only"])

    def test_drill_cifras_de_project_models(self):
        """coffe-0d5/coffe-itp: las filas pfm del drill leen el total
        (__total__) — muestran las cifras de project_models, no n/a/0."""
        full = self.model["views"]["all"]["trees"]["portfolio"]
        charly = next(c for c in full["children"] if c["label"] == "charly")
        coffee = next(c for c in charly["children"] if c["label"] == "charly-coffee")
        for c in coffee["children"]:
            pm = self.fixture["project_models"]["charly-coffee"][c["label"]]
            self.assertEqual(pm["cost_effective"], c["cost"])
            self.assertEqual(pm["interactions"], c["interactions"])
            self.assertNotEqual("n/a", c["cost_display"])
            self.assertNotEqual("n/a", c["interactions_display"])
            self.assertEqual("n/a", c["delta_display"])  # totals_only: sin delta vs prior
        month_view = self.model["views"]["month:2026-06"]
        charly_m = next(c for c in month_view["trees"]["portfolio"]["children"]
                        if c["label"] == "charly")
        for proj in charly_m["children"]:
            self.assertEqual([], proj["children"])

    def test_limitation_en_vistas_no_full(self):
        """FPA-027: limitación visible en vistas que no son el periodo completo."""
        for key, view in self.model["views"].items():
            if key == "all":
                self.assertNotIn("limitation", view)
            else:
                self.assertIn("Project→Model", view["limitation"])

    def test_rollup_portfolio(self):
        """FPA-025: categoría = suma de proyectos (los totals_only se excluyen)."""
        self.assertEqual([], viz.check_rollup(viz.build_portfolio_tree(self.fixture, CONFIG)))


class TestViews(_PortfolioBase):
    """FPA-026: vistas pre-calculadas por periodo; JS solo re-escala."""

    def test_presets_disponibles(self):
        views = self.model["views"]
        for key in ("all", "year:2026", "q:2026Q2", "month:2026-06"):
            self.assertIn(key, views)

    def test_kpis_de_vista_mensual_igualan_ventana(self):
        eff = next(k for k in self.model["views"]["month:2026-06"]["kpis"]
                   if k["key"] == "cost_effective")
        self.assertAlmostEqual(65.0, eff["value"])

    def test_rollup_de_todas_las_vistas(self):
        """FPA-101 aplicado a cada árbol fuente (las vistas derivan de él)."""
        for name in ("time", "tool", "portfolio"):
            self.assertEqual([], viz.check_rollup(_raw_tree(self.fixture, CONFIG, name)),
                             name)


if __name__ == "__main__":
    unittest.main()
