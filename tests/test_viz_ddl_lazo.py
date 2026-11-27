#!/usr/bin/env python3
"""Tests de las vistas lazo de feedback, errores y calidad de viz-ddl.py
(5.5, coffe-46h): 4 proxies lado a lado sin score compuesto, denominadores
en el row, censura de recovery visible, CI null ≠ 0.
"""
import re
import unittest
import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "viz_ddl", REPO / "scripts" / "viz-ddl.py")
viz_ddl = importlib.util.module_from_spec(_spec)
sys.modules["viz_ddl"] = viz_ddl
_spec.loader.exec_module(viz_ddl)


def _row(repo, month, metrics, tags):
    """Fila de monthly[] sintética con la forma del panel (coffe-w02):
    métricas + src {campo: tag}. Las razones opcionales van tras la
    llamada: row["reasons"] = {campo: texto}."""
    row = {"repo": repo, "month": month}
    row.update(metrics)
    row["src"] = dict(tags)
    return row


def _lazo_report():
    """Fixture del panel: un row wai completo (recovery, latencia, CI,
    proxies) y un row ghost todo-missing con razones."""
    wai = _row(
        "wai", "2026-09",
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
         "dose": "reported"})
    wai["reasons"] = {"fix_ratio": "sin commits"}
    ghost = _row(
        "ghost", "2026-09",
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
         "ah_check_findings": "missing"})
    ghost["reasons"] = {"ci_fail_rate": "rate_limit"}
    return {
        "metadata": {"managed_tools": ["wai"], "fleet_definition": "x",
                     "seed_bootstrap": 42},
        "repos": [],
        "monthly": [wai, ghost],
        "analysis": {"its": [], "did": [], "dose_response": []},
    }


class VistaLazoErroresCalidadTest(unittest.TestCase):
    """5.5 (coffe-46h): vistas lazo de feedback, errores y calidad.

    4 proxies lado a lado sin score compuesto, denominadores en el row,
    censura de recovery visible, CI null ≠ 0.
    """

    @classmethod
    def setUpClass(cls):
        cls.html = viz_ddl.build_html(_lazo_report())

    # --- lazo de feedback -------------------------------------------------

    def test_recovery_y_latencia_p50_visibles(self):
        self.assertIn("3.00", self.html)
        self.assertIn("n: 5", self.html)
        self.assertIn("60%", self.html)  # censura >= 0.5 visible (spec)
        self.assertIn("1.50", self.html)  # latencia p50 (mismo row)

    def test_recovery_missing_con_tag(self):
        self.assertGreaterEqual(
            self.html.count('data-provenance="missing"'), 3)

    def test_ci_fail_rate_con_runs(self):
        self.assertIn("0.25", self.html)
        self.assertIn("2/8", self.html)

    def test_ci_null_con_razon_no_es_cero(self):
        self.assertIn("rate_limit", self.html)
        self.assertNotIn("0.00%", self.html)  # nunca 0 fake

    # --- errores -----------------------------------------------------------

    def test_denominador_y_clases_en_el_mismo_row(self):
        # spec 'denominador siempre presente': interactions recalcables,
        # clases de terapia desglosadas (bash 0.20).
        self.assertIn("1000", self.html)
        self.assertIn("0.30", self.html)
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


if __name__ == "__main__":
    unittest.main(verbosity=1)
