#!/usr/bin/env python3
"""Tests del esqueleto viz-ddl.py (coffe-31l, add-ddl-effectiveness-analysis).

Stdlib-only: smoke Chromium llega en coffe-x1s (5.7). Igual que test_ddl_effect:
módulo cargado vía importlib por el dash en el nombre.
"""
import importlib.util
import json
import re
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


def _row(repo, month, metrics, tags, reasons=None, **extra):
    """Fila de monthly[] sintética con la forma del panel (coffe-w02):
    métricas + src {campo: tag} + reasons {campo: texto}."""
    row = {"repo": repo, "month": month}
    row.update(metrics)
    row["src"] = dict(tags)
    if reasons:
        row["reasons"] = dict(reasons)
    row.update(extra)
    return row


class VistaLazoErroresCalidadTest(unittest.TestCase):
    """5.5 (coffe-46h): vistas lazo de feedback, errores y calidad.

    4 proxies lado a lado sin score compuesto, denominadores en el row,
    censura de recovery visible, CI null ≠ 0.
    """

    REPORT = {
        "metadata": {"managed_tools": ["wai"], "fleet_definition": "x",
                     "seed_bootstrap": 42},
        "repos": [],
        "monthly": [
            _row("wai", "2026-09",
                 {"sessions": 12, "interactions": 1000,
                  "errors_api_per_100": 0.3,
                  "errors_terapia_per_100": {"bash": 0.2, "read": 0.1},
                  "recovery_p50_interactions": 3.0,
                  "recovery_censored_rate": 0.6, "recovery_n": 5,
                  "session_commit_latency_p50_h": 1.5,
                  "ci_fail_rate": 0.25, "runs_failed": 2, "runs_total": 8,
                  "revert_ratio": 0.1, "fix_ratio": None,
                  "test_commit_ratio": 0.4, "ah_check_findings": 2.0,
                  "dose": 1.0},
                 {"sessions": "reported", "interactions": "reported",
                  "errors_api_per_100": "reported",
                  "errors_terapia_per_100": "reported",
                  "recovery_p50_interactions": "reported",
                  "recovery_censored_rate": "reported",
                  "recovery_n": "reported",
                  "session_commit_latency_p50_h": "reported",
                  "ci_fail_rate": "cached", "runs_failed": "cached",
                  "runs_total": "cached",
                  "revert_ratio": "reported",
                  "fix_ratio": "missing",
                  "test_commit_ratio": "reported",
                  "ah_check_findings": "reported",
                  "dose": "reported"},
                 reasons={"fix_ratio": "sin commits"}),
            _row("ghost", "2026-09",
                 {"sessions": 1, "interactions": 10,
                  "errors_api_per_100": None,
                  "recovery_p50_interactions": None,
                  "recovery_censored_rate": None,
                  "recovery_n": None,
                  "ci_fail_rate": None, "runs_total": None,
                  "runs_failed": None,
                  "revert_ratio": None, "fix_ratio": None,
                  "test_commit_ratio": None, "ah_check_findings": None},
                 {"sessions": "reported", "interactions": "reported",
                  "errors_api_per_100": "missing",
                  "recovery_p50_interactions": "missing",
                  "recovery_censored_rate": "missing",
                  "recovery_n": "missing",
                  "ci_fail_rate": "missing", "runs_total": "missing",
                  "runs_failed": "missing",
                  "revert_ratio": "missing", "fix_ratio": "missing",
                  "test_commit_ratio": "missing",
                  "ah_check_findings": "missing"},
                 reasons={"ci_fail_rate": "rate_limit"}),
        ],
        "analysis": {"its": [], "did": [], "dose_response": []},
    }

    @classmethod
    def setUpClass(cls):
        cls.html = viz_ddl.build_html(cls.REPORT)

    # --- lazo de feedback -------------------------------------------------

    def test_recovery_p50_con_n_y_censura_visibles(self):
        self.assertIn("3.00", self.html)
        self.assertIn("n: 5", self.html)
        self.assertIn("60%", self.html)  # censura >= 0.5 visible (spec)

    def test_recovery_missing_con_tag(self):
        self.assertGreaterEqual(
            self.html.count('data-provenance="missing"'), 3)

    def test_latencia_sesion_commit(self):
        self.assertIn("1.50", self.html)

    def test_ci_fail_rate_con_runs(self):
        self.assertIn("0.25", self.html)
        self.assertIn("2/8", self.html)

    def test_ci_null_con_razon_no_es_cero(self):
        self.assertIn("rate_limit", self.html)
        self.assertNotIn("0.00%", self.html)  # nunca 0 fake

    # --- errores -----------------------------------------------------------

    def test_denominador_en_el_mismo_row(self):
        # spec 'denominador siempre presente': interactions recalcables
        self.assertIn("1000", self.html)
        self.assertIn("0.30", self.html)

    def test_clases_terapia_desglosadas(self):
        self.assertIn("bash", self.html)
        self.assertIn("0.20", self.html)

    def test_errores_missing_con_razon(self):
        self.assertGreaterEqual(
            self.html.count('data-provenance="missing"'), 3)

    # --- calidad ------------------------------------------------------------

    def test_cuatro_proxies_lado_a_lado(self):
        for needle in ("revert_ratio", "fix_ratio", "test_commit_ratio",
                       "ah_check_findings"):
            self.assertIn(needle, self.html)

    def test_sin_score_compuesto(self):
        self.assertIn("sin score", self.html.lower())

    def test_fix_ratio_null_con_razon_distinto_de_cero(self):
        self.assertIn("sin commits", self.html)

    def test_proxies_con_provenance_reported(self):
        self.assertIn('data-provenance="reported"', self.html)

    # --- vacío --------------------------------------------------------------

    def test_reporte_vacio_placeholders(self):
        empty = {"metadata": {}, "repos": [], "monthly": [],
                 "analysis": {"its": [], "did": [], "dose_response": []}}
        html = viz_ddl.build_html(empty)
        for v in ("lazo-feedback", "errores", "calidad"):
            m = re.search(
                rf'<section class="view" id="{v}"[^>]*>(.*?)</section>',
                html, re.S)
            self.assertIsNotNone(m, f"sección {v} ausente")
            self.assertIn('class="placeholder"', m.group(1))


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


class DisclosureBandTest(unittest.TestCase):
    """5.6 (coffe-8yf): banda fija de disclosure de confusores, no-colapsable."""

    def test_banda_presente_con_datos_completos(self):
        html = viz_ddl.build_html(VistaAdopcionDosisTest.REPORT)
        self.assertIn('class="disclosure"', html)

    def test_banda_presente_con_todo_missing(self):
        # spec: presente aunque todas las métricas tengan provenance missing
        html = viz_ddl.build_html(viz_ddl.SKELETON_REPORT)
        self.assertIn('class="disclosure"', html)

    def test_banda_no_colapsable(self):
        html = viz_ddl.build_html(viz_ddl.SKELETON_REPORT)
        self.assertNotIn("details", html.split('class="disclosure"')[1]
                         .split("</aside>")[0])

    def test_tres_puntos_del_disclaimer(self):
        html = viz_ddl.build_html(viz_ddl.SKELETON_REPORT)
        banda = html.split('class="disclosure"')[1].split("</aside>")[0]
        self.assertIn("asociativo", banda)          # (1) no causal
        self.assertIn("madurez", banda)             # (2) confusores medidos
        self.assertIn("mezcla de modelos", banda)   # (2)
        self.assertIn("atención del mantenedor", banda)  # (3) no controlable


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
