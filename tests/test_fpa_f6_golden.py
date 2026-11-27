#!/usr/bin/env python3
"""
test_fpa_f6_golden.py — F6: golden de vistas/CTAs/hallazgos (FPA-104,
REGEN_GOLDEN=1), determinismo y check-docs (FPA-143/107).

Parte de la antigua test_fpa_f6.py (coffe-kav.5); el fixture F6 vive en
tests/test_fpa_f6.py.
"""

import importlib.util
import json
import os
import re
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GOLDEN = Path(__file__).resolve().parent / "golden" / "fpa-f6-snapshot.json"
REGEN_GOLDEN = os.environ.get("REGEN_GOLDEN") == "1"


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, REPO / relpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


viz = _load("viz_fpa_f6", "scripts/viz-fpa.py")
f6 = _load("test_fpa_f6", "tests/test_fpa_f6.py")
f6_fixture = f6.f6_fixture
CONFIG = json.loads((REPO / "config" / "fpa.json").read_text())


# ======================================================================
# Golden + determinismo (FPA-104)
# ======================================================================

class TestGolden(unittest.TestCase):
    """Golden F6: vistas del selector (keys+labels), CTAs y hallazgos sobre
    el fixture F6. Regenerar:
    REGEN_GOLDEN=1 python3 tests/test_fpa_f6.py"""

    def test_golden_f6(self):
        fx = f6_fixture()
        model = viz.build_model(fx, CONFIG)
        html = viz.render_html(fx, CONFIG, generated="T")
        snapshot = {
            "windows": [{"key": k, "label": v["label"],
                         "months": v["months"], "has_prior": v["has_prior"]}
                        for k, v in model["views"].items()],
            "ctas_html": viz.ctas_html(CONFIG),
            # orden de secciones: breakdown (pareto) antes que habits (heatmap)
            "findings": re.findall(
                r'<h2 class="finding">[^<]+</h2>'
                r'<p class="small finding-meta">[^<]+</p>', html),
        }
        if REGEN_GOLDEN:
            GOLDEN.parent.mkdir(parents=True, exist_ok=True)
            GOLDEN.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False,
                                         sort_keys=True) + "\n")
            self.skipTest("golden regenerado; revisar diff antes de aceptar")
        if not GOLDEN.exists():
            self.fail("Falta golden; corre REGEN_GOLDEN=1 python3 tests/test_fpa_f6.py")
        golden = json.loads(GOLDEN.read_text())
        self.assertEqual(golden, snapshot)

    def test_determinismo_salvo_timestamp(self):
        """FPA-104: doble corrida → idéntico salvo fecha de generación."""
        fx = f6_fixture()
        a = viz.render_html(fx, CONFIG, generated="2026-09-20 12:00")
        b = viz.render_html(fx, CONFIG, generated="2026-09-20 12:00")
        self.assertEqual(a, b)


# ======================================================================
# check-docs (FPA-143/107)
# ======================================================================

class TestCheckDocs(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.report = json.loads((REPO / "data" / "usage_report_v3.json")
                                .read_text())

    def test_readme_consistente(self):
        """FPA-143: las cifras del bloque CHECK-DOCS del README salen del
        JSON."""
        self.assertEqual([], viz.check_docs(
            self.report, REPO / "README.md"))

    def test_cifra_obsoleta_falla(self):
        """FPA-143: cifra distinta → check_docs la lista."""
        with tempfile.TemporaryDirectory() as tmp:
            readme = Path(tmp) / "README.md"
            text = (REPO / "README.md").read_text()
            # cifra mal → error nombrando la figura
            readme.write_text(text.replace(
                viz.expected_doc_figures(self.report)["Proyectos"],
                "9999"))
            errors = viz.check_docs(self.report, readme)
            self.assertTrue(any("Proyectos" in e for e in errors), errors)

    def test_readme_sin_bloque_falla(self):
        with tempfile.TemporaryDirectory() as tmp:
            readme = Path(tmp) / "README.md"
            readme.write_text("hola\n")
            errors = viz.check_docs(self.report, readme)
            self.assertTrue(any("CHECK-DOCS" in e for e in errors))

    def test_main_check_docs_exit(self):
        """FPA-143/107: --check-docs termina 0 en verde, 1 en rojo."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bad_readme = tmp / "README.md"
            text = (REPO / "README.md").read_text()
            bad_readme.write_text(text.replace(
                viz.expected_doc_figures(self.report)["Interacciones"],
                "12345"))
            rc = viz.main(["--check-docs", "--readme", str(bad_readme)])
            self.assertEqual(1, rc)
            rc = viz.main(["--check-docs"])
            self.assertEqual(0, rc)

    def test_ci_corre_check_docs(self):
        """FPA-107: el pipeline corre el chequeo de consistencia de docs y
        falla ante cualquier desvío."""
        yml = (REPO / ".github" / "workflows" / "deploy-pages.yml").read_text()
        self.assertIn("viz-fpa.py --check-docs", yml)


if __name__ == "__main__":
    unittest.main()
