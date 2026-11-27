#!/usr/bin/env python3
"""Tests de extract_adoption en scripts/ddl-effect-tracker.py (add-ddl-effectiveness-analysis).

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


class ExtractAdoptionTest(unittest.TestCase):
    def test_repo_con_commit_y_primer_uso(self):
        commits = [
            {"repo": "wai", "date": "2026-03-01", "files": ["README.md"]},
            {"repo": "wai", "date": "2026-02-01", "files": [".ddl/config.json"]},
        ]
        uses = [{"repo": "wai", "tool": "wai", "date": "2026-02-15"}]
        out = ddl.extract_adoption(commits, uses, REGISTRY)
        self.assertEqual(out["wai"]["first_ddl_commit"], "2026-02-01")
        self.assertEqual(out["wai"]["tools_first_use"]["wai"], "2026-02-15")

    def test_repo_sin_senal_nulo_con_provenance(self):
        out = ddl.extract_adoption([], [], REGISTRY, repos=["ghost"])
        self.assertIsNone(out["ghost"]["first_ddl_commit"])
        self.assertIsNone(out["ghost"]["tools_first_use"]["wai"])
        self.assertEqual(out["ghost"]["provenance"]["first_ddl_commit"], "missing")

    def test_primer_ddl_commit_es_el_mas_antiguo(self):
        commits = [
            {"repo": "turu", "date": "2026-05-01", "files": [".ddl/config.json"]},
            {"repo": "turu", "date": "2026-01-01", "files": [".ddl/x", "src/main.py"]},
        ]
        out = ddl.extract_adoption(commits, [], REGISTRY)
        self.assertEqual(out["turu"]["first_ddl_commit"], "2026-01-01")

    def test_repo_aliases_match_exacto_sin_fuzzy(self):
        commits = []
        uses = [
            {"repo": "/home/sasha/para/areas/dev/gh/charly/coffee", "tool": "wai",
             "date": "2026-04-01"},
        ]
        out = ddl.extract_adoption(
            commits, uses, REGISTRY, repos=["coffee"],
            repo_aliases={"coffee": ["/home/sasha/para/areas/dev/gh/charly/coffee"]},
        )
        self.assertEqual(out["coffee"]["tools_first_use"]["wai"], "2026-04-01")
        # sin alias, el cwd largo no matchea nada (sin fuzzy matching)
        out2 = ddl.extract_adoption(commits, uses, REGISTRY, repos=["coffee"])
        self.assertIsNone(out2["coffee"]["tools_first_use"]["wai"])

    def test_herramienta_no_gestionada_se_ignora(self):
        uses = [{"repo": "wai", "tool": "vim", "date": "2026-02-15"}]
        out = ddl.extract_adoption([], uses, REGISTRY, repos=["wai"])
        self.assertNotIn("vim", out["wai"]["tools_first_use"])
        self.assertIsNone(out["wai"]["tools_first_use"]["wai"])

    def test_primer_uso_repite_conteo_y_toma_el_minimo(self):
        uses = [
            {"repo": "dont", "tool": "dont", "date": "2026-06-10"},
            {"repo": "dont", "tool": "dont", "date": "2026-06-01"},
        ]
        out = ddl.extract_adoption([], uses, REGISTRY, repos=["dont"])
        self.assertEqual(out["dont"]["tools_first_use"]["dont"], "2026-06-01")

    def test_uses_de_repo_fuera_de_flota_ignorados(self):
        uses = [{"repo": "otro-proyecto", "tool": "wai", "date": "2026-06-01"}]
        out = ddl.extract_adoption([], uses, REGISTRY, repos=["wai"])
        self.assertIsNone(out["wai"]["tools_first_use"]["wai"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
