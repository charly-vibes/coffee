#!/usr/bin/env python3
"""Tests de la vista pre/post + DiD de viz-ddl.py (5.4, coffe-470):
delta y IC bootstrap visibles, ITS omitido con razón, provenance derived
en los estimadores y placeholder con analysis vacío.
"""
import importlib.util
import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "viz_ddl", REPO / "scripts" / "viz-ddl.py")
viz_ddl = importlib.util.module_from_spec(_spec)
sys.modules["viz_ddl"] = viz_ddl
_spec.loader.exec_module(viz_ddl)


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


if __name__ == "__main__":
    unittest.main(verbosity=1)
