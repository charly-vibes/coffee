#!/usr/bin/env python3
"""Tests de viz-index.py (generador del index.html de Pages).

Cubre:
- build_stats: cifras derivadas del reporte/charges (función pura)
- render: enlaces a los 5 dashboards + JSON, título coffee, sin entradas
  retiradas (v2 sin filtrar, tool_timeline), determinismo byte-a-byte
"""

import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "viz_index", ROOT / "scripts" / "viz-index.py")
viz_index = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(viz_index)

FIXTURE_REPORT = {
    "metadata": {
        "date_range": {"start": "2026-01-11", "end": "2026-09-20"},
        "total_interactions": 141851,
        "total_projects": 42,
    }
}
FIXTURE_CHARGES = {"description": "ledger", "providers": {"claude-cli": [], "codex": []}}

# Nombre llano del dashboard (coffe-gen.2): valor del config real.
SITE_NAME = "Uso y costos de IA"


class TestBuildStats(unittest.TestCase):
    def test_derives_all_figures_from_report(self):
        s = viz_index.build_stats(FIXTURE_REPORT, FIXTURE_CHARGES)
        self.assertEqual(s["interacciones"], 141851)
        self.assertEqual(s["proyectos"], 42)
        self.assertEqual(s["actualizado"], "2026-09-20")
        self.assertEqual(s["edicion"], "Septiembre 2026")
        self.assertEqual(s["proveedores"], 2)

    def test_without_charges_zero_providers(self):
        s = viz_index.build_stats(FIXTURE_REPORT, None)
        self.assertEqual(s["proveedores"], 0)


class TestRender(unittest.TestCase):
    def setUp(self):
        self.html = viz_index.render(viz_index.build_stats(FIXTURE_REPORT, FIXTURE_CHARGES), SITE_NAME)

    def test_links_dashboards_and_json(self):
        for href in ("data/fpa-dashboard.html", "data/fpa-guide.html",
                     "data/gantt-multitasking.html",
                     "data/context-dashboard.html",
                     "data/ddl-dashboard.html",
                     "data/usage_report_v3.json"):
            self.assertIn(f'href="{href}"', self.html)

    def test_links_every_data_html(self):
        """Guard: todo dashboard HTML publicado en data/ aparece en el
        índice (los guardas finos viven en tests/test_site_nav.py)."""
        for page in sorted((ROOT / "data").glob("*.html")):
            self.assertIn(f'href="data/{page.name}"', self.html)

    def test_insights_eliminado_del_indice(self):
        """coffe-gen.5: el dashboard de insights se elimina — ni entrada
        en el índice ni HTML publicado."""
        self.assertNotIn("data/dashboard.html", self.html)
        self.assertNotIn("insights", self.html)
        self.assertFalse(
            (ROOT / "data" / "dashboard.html").exists(),
            "data/dashboard.html debe estar eliminado del repo/deploy")
        self.assertFalse(
            (ROOT / "scripts" / "viz-dashboard.py").exists(),
            "scripts/viz-dashboard.py debe estar eliminado del repo")

    def test_no_retired_entries(self):
        self.assertNotIn("usage_report_v2.json", self.html)
        self.assertNotIn("tool_timeline.json", self.html)
        self.assertNotIn("¡NUEVO!", self.html)

    def test_title_is_coffee(self):
        self.assertIn("<title>coffee — tracking de uso de IA</title>", self.html)
        self.assertNotIn("coffe ", self.html.replace("coffee ", ""))

    def test_marquee_carries_dataset_figures(self):
        # derivado del reporte, no hardcodeado: la fecha es date_range.end
        # y las interacciones el total del metadata (cambia con cada regen)
        end = FIXTURE_REPORT["metadata"]["date_range"]["end"]
        total = f"{FIXTURE_REPORT['metadata']['total_interactions']:,}".replace(",", ",")
        self.assertIn(f"datos actualizados al {end}", self.html)
        self.assertIn(f"{total} interacciones", self.html)

    def test_deterministic(self):
        again = viz_index.render(viz_index.build_stats(FIXTURE_REPORT, FIXTURE_CHARGES), SITE_NAME)
        self.assertEqual(self.html, again)


class TestGoldenRealData(unittest.TestCase):
    """El index generado desde el dataset real es estable byte-a-byte."""

    def test_real_dataset_renders(self):
        report = json.loads((ROOT / "data" / "usage_report_v3.json").read_text())
        html = viz_index.render(viz_index.build_stats(report, None), SITE_NAME)
        end = report["metadata"]["date_range"]["end"]
        self.assertIn(f"datos actualizados al {end}", html)
        self.assertNotIn("{", html.split("<marquee")[1].split("</marquee>")[0])


if __name__ == "__main__":
    unittest.main()
