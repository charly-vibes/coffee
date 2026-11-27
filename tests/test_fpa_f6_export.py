#!/usr/bin/env python3
"""
test_fpa_f6_export.py — F6: numeración de figuras/tablas (FPA-144/106),
export CSV/SVG y share view (FPA-169/170/171) y accesibilidad estructural
(FPA-175/176/177/179, 094/092).

Parte de la antigua test_fpa_f6.py (coffe-kav.5); el fixture F6 vive en
tests/test_fpa_f6.py.
"""

import importlib.util
import json
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, REPO / relpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


viz = _load("viz_fpa_f6", "scripts/viz-fpa.py")
f2 = _load("test_fpa_f2", "tests/test_fpa_f2.py")
CONFIG = json.loads((REPO / "config" / "fpa.json").read_text())


class F6Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = f2.f2_fixture()
        cls.model = viz.build_model(cls.fixture, CONFIG)
        cls.html = viz.render_html(cls.fixture, CONFIG, generated="T")

    def _section(self, sec_id):
        """Contenido <section id="...">…</section> del HTML."""
        m = re.search(rf'<section id="{sec_id}"[^>]*>(.*?)</section>',
                      self.html, re.S)
        self.assertIsNotNone(m, f"falta sección {sec_id}")
        return m.group(1)


# ======================================================================
# Numeración de figuras/tablas (FPA-144/106)
# ======================================================================

class TestNumeracion(F6Base):

    def test_numeros_unicos(self):
        """FPA-106: la suite falla si hay números de figura o tabla
        duplicados."""
        for kind in ("Tabla", "Figura"):
            nums = re.findall(rf"{kind} (\d+)", self.html)
            self.assertGreater(len(nums), 0, kind)
            self.assertEqual(len(nums), len(set(nums)),
                             f"{kind} duplicado: {nums}")

    def test_captions_numerados(self):
        """FPA-144: numeración automática sobre todos los captions."""
        n_caps = self.html.count("<caption>")
        self.assertEqual(n_caps,
                         len(re.findall(r"<caption>Tabla \d+", self.html)))


# ======================================================================
# Export CSV / Download SVG (FPA-169)
# ======================================================================

class TestExportShare(F6Base):

    def test_export_csv_en_toda_tabla(self):
        """FPA-169: cada <table> del main lleva un botón Exportar CSV
        delante (JS descarga sin recargar)."""
        main = self.html[self.html.index("<main"):self.html.index("</main>")]
        n_tables = len(re.findall(r"<table[ >]", main))
        n_btns = main.count('class="export-csv"')
        self.assertGreater(n_tables, 5)
        self.assertEqual(n_tables, n_btns)

    def test_download_svg_en_chart(self):
        """FPA-169: el chart SVG (waterfall) lleva botón Descargar SVG."""
        self.assertIn('class="dl-svg"', self.html)
        self.assertIn('class="wf chart"', self.html)

    def test_js_export_sin_deps(self):
        js = self.html.split("<script>")[-1]
        for fn in ("tableToCSV", "downloadText"):
            self.assertIn(fn, js)
        self.assertNotIn("http", js.replace("http://www.w3.org", ""))

    def test_share_view_js(self):
        """FPA-170: comparte URL con view, period y expansión; la restaura
        al cargar validando contra el modelo (FPA-171)."""
        js = self.html.split("<script>")[-1]
        self.assertIn("URLSearchParams", js)
        self.assertIn('id="share-view"', self.html)
        self.assertIn("MODEL.views[", js)      # periodo validado
        self.assertIn("VIEWS.indexOf(", js)    # vista validada
        self.assertIn("data-tree", js)         # expansión de árbol


# ======================================================================
# Accesibilidad estructural (FPA-175/176/177/179, 094/092)
# ======================================================================

class TestAccesibilidad(F6Base):

    def test_skip_link_y_lang(self):
        """FPA-179: skip-to-content + lang del documento."""
        self.assertIn('<a class="skip"', self.html)
        self.assertIn('href="#summary"', self.html)
        self.assertIn('<html lang="es">', self.html)

    def test_targets_44px(self):
        """FPA-176: targets táctiles ≥ 44×44 px en CSS."""
        self.assertIn("min-height:44px", self.html)

    def test_primera_columna_fija(self):
        """FPA-177: <600px la primera columna queda fija con scroll."""
        self.assertIn("position:sticky", self.html)
        self.assertIn("th:first-child", self.html)

    def test_scroll_horizontal_contenedores(self):
        """FPA-092: contenedores con scroll horizontal < 600px."""
        self.assertIn("overflow-x:auto", self.html)


if __name__ == "__main__":
    unittest.main()
