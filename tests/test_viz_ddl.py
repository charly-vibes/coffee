#!/usr/bin/env python3
"""Tests del esqueleto viz-ddl.py (coffe-31l, add-ddl-effectiveness-analysis).

Stdlib-only: smoke Chromium llega en coffe-x1s (5.7). Igual que test_ddl_effect:
módulo cargado vía importlib por el dash en el nombre.
"""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "viz_ddl", REPO / "scripts" / "viz-ddl.py")
viz_ddl = importlib.util.module_from_spec(_spec)
sys.modules["viz_ddl"] = viz_ddl
_spec.loader.exec_module(viz_ddl)

_spec2 = importlib.util.spec_from_file_location(
    "ddl_effect_tracker", REPO / "scripts" / "ddl-effect-tracker.py")
ddl = importlib.util.module_from_spec(_spec2)
sys.modules["ddl_effect_tracker"] = ddl
_spec2.loader.exec_module(ddl)


class VizDdlTest(unittest.TestCase):
    """Esqueleto: autocontenido, tema, 6 vistas, hash deep-link, export."""

    @classmethod
    def setUpClass(cls):
        import tempfile
        cls.tmp = tempfile.TemporaryDirectory()
        cls.report_path = Path(cls.tmp.name) / "ddl_effect.json"
        rc = ddl.main(["--output", str(cls.report_path)])
        assert rc == 0
        cls.report = json.loads(cls.report_path.read_text())
        cls.out = Path(cls.tmp.name) / "ddl-dashboard.html"
        assert viz_ddl.main(["--report", str(cls.report_path),
                             "--output", str(cls.out)]) == 0
        cls.html = cls.out.read_text()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_html_autocontenido_sin_recursos_externos(self):
        self.assertNotIn("<script src", self.html)
        self.assertNotIn("<link ", self.html)
        self.assertNotIn("chart.js", self.html.lower())

    def test_tema_compartido_site_theme(self):
        import site_theme
        self.assertIn(site_theme.base_css(), self.html)

    def test_seis_vistas_con_tab_y_seccion(self):
        for v in viz_ddl.VIEWS:
            self.assertEqual(self.html.count(f'data-view="{v}"'), 2,
                             f"vista {v}: tab + section")

    def test_deep_link_por_hash_con_guardia(self):
        self.assertIn("location.hash", self.html)
        self.assertIn("hashchange", self.html)
        # hash inválido ignorado → vista default (patrón FPA-171)
        self.assertIn("indexOf", self.html)

    def test_default_view_es_adopcion(self):
        self.assertEqual(viz_ddl.VIEWS[0], "adopcion")

    def test_export_csv_svg_js(self):
        for needle in ("tableToCSV", "svgXml", "export-csv", "dl-svg",
                       "downloadText"):
            self.assertIn(needle, self.html)

    def test_add_export_buttons_sintetico(self):
        html = viz_ddl.add_export_buttons("<table><tr><td>x</td></tr></table>")
        self.assertIn('class="export-csv"', html)
        self.assertTrue(html.index("export-csv") < html.index("<table"))
        svg = viz_ddl.add_export_buttons('<svg class="wf chart">')
        self.assertIn('class="dl-svg"', svg)

    def test_provenance_tags_en_html(self):
        self.assertIn('class="prov-tag"', self.html)  # helper presente
        self.assertIn('title="procedencia: missing"', self.html)

    def test_vistas_placeholder_sin_datos(self):
        # esqueleto: vistas vacías muestran placeholder visible, no tabs muertos
        self.assertIn("placeholder", self.html)

    def test_disclosure_count_not_required_yet(self):
        # 5.6 (coffe-8yf) agrega la banda fija; el esqueleto no la lleva
        self.assertNotIn("confusor", self.html.lower())


class VistaPrePostTest(unittest.TestCase):
    """5.4 (coffe-470): vista pre/post + DiD con IC bootstrap."""

    REPORT = {
        "metadata": {"managed_tools": ["wai"] * 13, "fleet_definition": "x",
                     "seed_bootstrap": 42},
        "repos": [],
        "monthly": [],
        "analysis": {
            "its": [{"repo": "wai", "metric": "ah_check_findings",
                     "delta": 2.0, "ci_low": 1.2, "ci_high": 2.8,
                     "n_pre": 4, "n_post": 4},
                    {"repo": "ghost", "metric": "ci_fail_rate",
                     "delta": None, "n_pre": 2, "n_post": 3,
                     "reason": "ventana_pre_insuficiente"}],
            "did": [{"metric": "ah_check_findings", "did": 2.0,
                     "ci_low": 1.0, "ci_high": 3.0,
                     "delta_treatment": 2.0, "delta_control": 0.0,
                     "n_treatment": 3, "n_control": 3}],
            "dose_response": [],
        },
    }

    @classmethod
    def setUpClass(cls):
        cls.html = viz_ddl.build_html(cls.REPORT)

    def test_its_delta_y_ci_visibles(self):
        self.assertIn("2.00", self.html)
        self.assertIn("1.20", self.html)
        self.assertIn("2.80", self.html)

    def test_its_omitido_con_razon_visible(self):
        self.assertIn("ventana_pre_insuficiente", self.html)

    def test_did_punto_con_ic_y_n(self):
        self.assertIn("n_treatment: 3", self.html)
        self.assertIn("3.00", self.html)  # ci_high del DiD

    def test_provenance_derived_en_los_estimadores(self):
        self.assertIn('data-provenance="derived"', self.html)

    def test_analysis_vacio_placeholder(self):
        empty = json.loads(json.dumps(viz_ddl.SKELETON_REPORT))
        html = viz_ddl.build_html(empty)
        self.assertIn("sin datos", html)


class VistaAdopcionDosisTest(unittest.TestCase):
    """5.2+5.3 (coffe-het): línea de adopción + scatter dosis-respuesta."""

    REPORT = {
        "metadata": {"managed_tools": ["x"] * 13, "fleet_definition": "f",
                     "seed_bootstrap": 42},
        "repos": [
            {"repo": "turu", "is_control": False, "is_outlier": False,
             "adoption": {"first_ddl_commit": "2026-02-01", "dose": 0.6,
                          "dose_detail": {"present": ["wai", "turu"],
                                          "healthy": ["wai"],
                                          "missing": ["dont"]},
                          "provenance": {"first_ddl_commit": "reported",
                                         "tools_first_use": "reported",
                                         "dose": "reported"}}},
            {"repo": "ghost", "is_control": True, "is_outlier": True,
             "adoption": {"first_ddl_commit": None, "dose": None,
                          "provenance": {"first_ddl_commit": "missing",
                                         "dose": "missing"}}},
        ],
        "monthly": [],
        "analysis": {
            "its": [], "did": [],
            "dose_response": [
                {"metric": "ah_check_findings", "spearman_rho": 0.7, "n": 4,
                 "points": [{"repo": "wai", "dose": 1.0, "outcome": 3.0},
                            {"repo": "turu", "dose": 0.6, "outcome": 2.0},
                            {"repo": "ah", "dose": 0.3, "outcome": 2.5},
                            {"repo": "bd", "dose": 0.0, "outcome": 1.0}]},
                {"metric": "ci_fail_rate", "spearman_rho": None, "n": 2,
                 "reason": "n_insuficiente", "points": []},
            ],
        },
    }

    @classmethod
    def setUpClass(cls):
        cls.html = viz_ddl.build_html(cls.REPORT)

    def test_adopcion_fecha_visible(self):
        self.assertIn("2026-02-01", self.html)

    def test_adopcion_sin_senal_visible(self):
        self.assertIn("sin señal", self.html)

    def test_adopcion_ordenada_por_fecha_nulls_last(self):
        pos = [self.html.index(f'data-repo="{r}"') for r in ("turu", "ghost")]
        self.assertLess(pos[0], pos[1])

    def test_outlier_marcado(self):
        self.assertIn("outlier", self.html)

    def test_dosis_scatter_con_puntos(self):
        self.assertIn("<svg", self.html)
        self.assertEqual(self.html.count('class="scatter-pt"'), 4)

    def test_dosis_rho_y_n_visibles(self):
        self.assertIn("ρ = 0.70", self.html)
        self.assertIn("n = 4", self.html)

    def test_dosis_celda_n3_no_graficada(self):
        self.assertIn("n_insuficiente", self.html)
        # la celda chica no aporta círculos
        self.assertEqual(self.html.count('class="scatter-pt"'), 4)

    def test_dosis_reported_en_puntos(self):
        self.assertIn('data-provenance="reported"', self.html)


class VizDdlCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.report_path = Path(self.tmp.name) / "ddl_effect.json"
        ddl.main(["--output", str(self.report_path)])

    def tearDown(self):
        self.tmp.cleanup()

    def test_fail_loud_sin_reporte(self):
        with self.assertRaises(SystemExit):
            viz_ddl.main(["--report", str(Path(self.tmp.name) / "nope.json")])

    def test_validate_valido_rc0(self):
        self.assertEqual(viz_ddl.main(["--validate", str(self.report_path)]), 0)

    def test_validate_invalido_rc1(self):
        corrupt = Path(self.tmp.name) / "corrupt.json"
        corrupt.write_text(json.dumps({"metadata": {}}))
        self.assertEqual(viz_ddl.main(["--validate", str(corrupt)]), 1)

    def test_validate_schema_path_no_duplicado(self):
        # la fuente del schema es la del tracker (única)
        self.assertEqual(viz_ddl.DDL_SCHEMA, ddl.DDL_SCHEMA)


if __name__ == "__main__":
    unittest.main(verbosity=1)
