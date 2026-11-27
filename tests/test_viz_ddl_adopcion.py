#!/usr/bin/env python3
"""Tests de las vistas adopción + dosis-respuesta de viz-ddl.py (5.2+5.3,
coffe-het) y la banda de disclosure (5.6, coffe-8yf): línea de adopción
ordenada nulls-last con outlier marcado, scatter con rho/n visibles y
degradación a placeholder, banda fija no-colapsable con los 3 puntos del
disclaimer.
"""
import importlib.util
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "viz_ddl", REPO / "scripts" / "viz-ddl.py")
viz_ddl = importlib.util.module_from_spec(_spec)
sys.modules["viz_ddl"] = viz_ddl
_spec.loader.exec_module(viz_ddl)


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

    def test_adopcion_fecha_y_sin_senal_visibles(self):
        self.assertIn("2026-02-01", self.html)
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


class VistaDosisScatterVacioTest(unittest.TestCase):
    """coffe-q9z: regen real — rho presente pero points vacío (outcome null)
    debe degradar a placeholder, no crash en min() del scatter."""

    def test_rho_sin_puntos_placeholder_sin_crash(self):
        report = {"metadata": {"managed_tools": ["x"] * 13,
                               "fleet_definition": "f", "seed_bootstrap": 42},
                  "repos": [], "monthly": [],
                  "analysis": {"its": [], "did": [],
                               "dose_response": [
                                   {"metric": "m", "spearman_rho": 0.4,
                                    "n": 13, "points": []}]}}
        html = viz_ddl.build_html(report)
        self.assertIn("sin puntos graficables", html)

    def test_scatter_vacio_directo(self):
        self.assertIn("sin puntos graficables", viz_ddl._scatter_svg([]))


class DisclosureBandTest(unittest.TestCase):
    """5.6 (coffe-8yf): banda fija de disclosure de confusores, no-colapsable."""

    def test_banda_presente_con_datos_y_todo_missing(self):
        # spec: presente con datos completos Y aunque todas las métricas
        # tengan provenance missing (skeleton).
        html = viz_ddl.build_html(VistaAdopcionDosisTest.REPORT)
        self.assertIn('class="disclosure"', html)
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


if __name__ == "__main__":
    unittest.main(verbosity=1)
