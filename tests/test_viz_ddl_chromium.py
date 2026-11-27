#!/usr/bin/env python3
"""
test_viz_ddl_chromium.py — coffe-x1s (5.7): smoke Chromium de
ddl-dashboard.html (parte del ex test_viz.py) — 0 pageerrors, charts
render, disclosure presente con datos vacíos, deep-link por hash y
export CSV con contenido.
"""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False


def find_chromium():
    """Busca un chromium instalado por playwright (varias versiones)."""
    import glob
    for pat in ("~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome",
                "~/.cache/ms-playwright/chromium_headless_shell-*/chrome-linux*/headless_shell"):
        found = sorted(glob.glob(str(Path(pat).expanduser())))
        if found:
            return found[-1]
    return None


def _ddl_report_con_datos():
    """Reporte con 2 repos (provenance mixto) y dose_response con puntos."""
    return {"metadata": {"managed_tools": ["wai"],
                         "fleet_definition": "x",
                         "seed_bootstrap": 42},
            "repos": [],
            "monthly": [
                {"repo": "r1", "month": "2026-09",
                 "revert_ratio": 0.1, "fix_ratio": None,
                 "test_commit_ratio": 0.4, "ah_check_findings": 2.0,
                 "interactions": 100, "errors_api_per_100": 0.3,
                 "recovery_p50_interactions": 3.0,
                 "recovery_censored_rate": 0.2, "recovery_n": 5,
                 "session_commit_latency_p50_h": 1.5,
                 "ci_fail_rate": None,
                 "src": {"revert_ratio": "reported",
                         "fix_ratio": "missing",
                         "test_commit_ratio": "reported",
                         "ah_check_findings": "reported",
                         "interactions": "reported",
                         "errors_api_per_100": "reported",
                         "recovery_p50_interactions": "reported",
                         "recovery_censored_rate": "reported",
                         "recovery_n": "reported",
                         "session_commit_latency_p50_h": "reported",
                         "ci_fail_rate": "missing"}},
                {"repo": "r2", "month": "2026-09",
                 "revert_ratio": 0.2, "fix_ratio": 0.3,
                 "test_commit_ratio": 0.5, "ah_check_findings": 1.0,
                 "src": {"revert_ratio": "reported",
                         "fix_ratio": "reported",
                         "test_commit_ratio": "reported",
                         "ah_check_findings": "reported"}}],
            "analysis": {
                "its": [], "did": [],
                "dose_response": [
                    {"metric": "ah_check_findings",
                     "spearman_rho": 0.6, "n": 4,
                     "points": [{"repo": "r1", "dose": 0.0,
                                 "outcome": 1.0},
                                {"repo": "r2", "dose": 0.5,
                                 "outcome": 2.0},
                                {"repo": "r3", "dose": 1.0,
                                 "outcome": 3.0},
                                {"repo": "r4", "dose": 0.25,
                                 "outcome": 1.5}]}]}}


@unittest.skipUnless(HAS_PLAYWRIGHT, "playwright no instalado (opcional)")
class TestVizDdlChromium(unittest.TestCase):
    """coffe-x1s (5.7): smoke Chromium de ddl-dashboard.html — 0 pageerrors,
    charts render, disclosure presente con datos vacíos."""

    DDL_VIEWS = ("adopcion", "dosis-respuesta", "pre-post", "lazo-feedback",
                 "errores", "calidad")

    @classmethod
    def setUpClass(cls):
        cls.chrome = find_chromium()
        if not cls.chrome:
            raise unittest.SkipTest("chromium de playwright no encontrado")
        spec = importlib.util.spec_from_file_location(
            "viz_ddl_smoke", REPO / "scripts" / "viz-ddl.py")
        cls.viz_ddl = importlib.util.module_from_spec(spec)
        sys.modules["viz_ddl_smoke"] = cls.viz_ddl
        spec.loader.exec_module(cls.viz_ddl)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.empty_path = Path(cls.tmp.name) / "ddl-empty.html"
        cls.empty_path.write_text(
            cls.viz_ddl.build_html({"metadata": {}, "repos": [],
                                    "monthly": [],
                                    "analysis": {"its": [], "did": [],
                                                 "dose_response": []}}))
        cls.data_path = Path(cls.tmp.name) / "ddl-data.html"
        cls.data_path.write_text(cls.viz_ddl.build_html(_ddl_report_con_datos()))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @classmethod
    def setUp(cls):
        cls._pw = sync_playwright().start()
        cls._browser = cls._pw.chromium.launch(
            executable_path=cls.chrome, args=["--no-sandbox"])

    @classmethod
    def tearDown(cls):
        cls._browser.close()
        cls._pw.stop()

    def _open(self, path, url_extra=""):
        pg = self._browser.new_page()
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.set_viewport_size({"width": 1280, "height": 900})
        pg.goto(f"file://{path}{url_extra}")
        pg.wait_for_timeout(300)
        return pg, errors

    def test_ddl_sin_datos_0_pageerrors_y_disclosure(self):
        """Spec 'disclosure no removible': banda presente aunque todas las
        métricas tengan provenance missing; 0 pageerrors con tab vacío."""
        pg, errors = self._open(self.empty_path)
        self.assertEqual(errors, [], f"pageerrors: {errors}")
        self.assertTrue(pg.is_visible("aside.disclosure"),
                        "disclosure ausente con datos vacíos")
        for v in self.DDL_VIEWS:
            pg.click(f"a.tab[data-view='{v}']")
            pg.wait_for_timeout(100)
            self.assertTrue(pg.is_visible(f"section#{v}"),
                            f"vista {v} no visible tras click")
        self.assertEqual(errors, [])
        pg.close()

    def test_ddl_deeplink_hash(self):
        """Deep-link por hash (#calidad) activa la vista al cargar."""
        pg, errors = self._open(self.empty_path, "#calidad")
        self.assertEqual(errors, [])
        self.assertTrue(pg.is_visible("section#calidad"))
        pg.close()

    def test_ddl_charts_render_scatter(self):
        """Con datos: scatter SVG inline renderiza (bbox > 0) y 0 pageerrors."""
        pg, errors = self._open(self.data_path)
        self.assertEqual(errors, [], f"pageerrors: {errors}")
        pg.click("a.tab[data-view='dosis-respuesta']")
        pg.wait_for_timeout(200)
        box = pg.locator("svg.ddl-chart").first.bounding_box()
        self.assertIsNotNone(box, "scatter .ddl-chart sin bbox")
        self.assertGreater(box["width"], 100, "scatter no renderizó")
        self.assertEqual(errors, [])
        pg.close()

    def test_ddl_export_csv_funciona(self):
        """El botón Exportar CSV dispara una descarga con contenido."""
        pg, errors = self._open(self.data_path)
        pg.click("a.tab[data-view='calidad']")
        pg.wait_for_timeout(200)
        with pg.expect_download() as dl_info:
            pg.click("section#calidad button.export-csv")
        dl = dl_info.value
        path = Path(dl.path())
        contenido = path.read_text()
        self.assertIn("revert_ratio", contenido)
        self.assertEqual(errors, [])
        pg.close()


if __name__ == "__main__":
    unittest.main()
