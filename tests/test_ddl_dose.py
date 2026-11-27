#!/usr/bin/env python3
"""Tests de dosis (compute_dose) y clasificación control/outlier en ddl-effect-tracker.py.

Solo stdlib. Sin disk I/O: los reducers reciben estructuras, no paths
(mismo estilo que test_tracker.py con aggregate()).
"""
import importlib.util
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "ddl_effect_tracker", REPO / "scripts" / "ddl-effect-tracker.py"
)
ddl = importlib.util.module_from_spec(_spec)
sys.modules["ddl_effect_tracker"] = ddl
_spec.loader.exec_module(ddl)

REGISTRY = ddl.MANAGED_TOOLS


class ComputeDoseTest(unittest.TestCase):
    def test_dosis_desde_ddl_status(self):
        status = {
            "wai": {"present": REGISTRY[:5], "healthy": REGISTRY[:4]},
        }
        out = ddl.compute_dose(status, {}, {}, REGISTRY)
        self.assertEqual(out["wai"]["dose"], 4 / 13)
        self.assertEqual(out["wai"]["dose_detail"]["healthy"], list(REGISTRY[:4]))
        self.assertEqual(out["wai"]["provenance"]["dose"], "reported")

    def test_fallbacks_y_prioridad_de_dosis(self):
        # sin señal → 0.0 con provenance missing (nunca fake); fallbacks
        # derivan; el ddl_status reportado gana sobre los fallbacks
        casos = [
            ("fallback_canarios_git",
             None, {"wai": [".ddl/wai/config.json", ".ddl/dont/config.json"]},
             {}, None, "wai", 2 / 13, "derived"),
            ("fallback_usos_de_sesion",
             None, {}, {"wai": {"turu", "ah"}}, None, "wai", 2 / 13,
             "derived"),
            ("sin_toda_senal_es_cero_con_missing",
             None, {}, {}, ["ghost"], "ghost", 0.0, "missing"),
            ("ddl_status_gana_sobre_fallback",
             {"wai": {"present": REGISTRY, "healthy": REGISTRY}},
             {"wai": [".ddl/wai/config.json"]}, {}, None, "wai", 1.0,
             "reported"),
        ]
        for nombre, status, canaries, uses, repos, repo, dosis, prov in casos:
            with self.subTest(caso=nombre):
                if repos is None:
                    out = ddl.compute_dose(status, canaries, uses, REGISTRY)
                else:
                    out = ddl.compute_dose(status, canaries, uses, REGISTRY,
                                           repos=repos)
                self.assertEqual(out[repo]["dose"], dosis)
                self.assertEqual(out[repo]["provenance"]["dose"], prov)


class ClassifyControlTest(unittest.TestCase):
    def test_control_por_ausencia_estructural(self):
        repos = {"ghost": {"has_ddl": False, "topic_ddl": False},
                 "wai": {"has_ddl": True, "topic_ddl": True}}
        out = ddl.classify_control(repos)
        self.assertTrue(out["ghost"]["is_control"])
        self.assertFalse(out["wai"]["is_control"])

    def test_ddl_sin_dosis_no_es_control(self):
        repos = {"wai": {"has_ddl": True, "topic_ddl": True}}
        out = ddl.classify_control(repos)
        self.assertFalse(out["wai"]["is_control"])

    def test_outlier_ddl_marcado(self):
        repos = {"charly-dulce-de-leche": {"has_ddl": True, "topic_ddl": True}}
        out = ddl.classify_control(repos, outlier="charly-dulce-de-leche")
        self.assertTrue(out["charly-dulce-de-leche"]["is_outlier"])
        self.assertFalse(out["charly-dulce-de-leche"]["is_control"])

    def test_clasificacion_reproducible(self):
        repos = {"wai": {"has_ddl": True, "topic_ddl": True},
                 "ghost": {"has_ddl": False, "topic_ddl": None}}
        self.assertEqual(ddl.classify_control(repos), ddl.classify_control(repos))


if __name__ == "__main__":
    unittest.main(verbosity=1)
