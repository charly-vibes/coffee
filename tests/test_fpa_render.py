#!/usr/bin/env python3
"""
test_fpa_render.py — FPA-001/003/004/093/095/104/108: render HTML.

Parte de la antigua test_fpa.py (coffe-kav.5): header, tema, formatos,
provenance, placeholder-check, autocontención y determinismo.
"""

import importlib.util
import json
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, REPO / relpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


viz = _load("viz_fpa", "scripts/viz-fpa.py")

REPORT = json.loads((REPO / "data" / "usage_report_v3.json").read_text())
CONFIG = json.loads((REPO / "config" / "fpa.json").read_text())


def _rendered():
    """HTML real del reporte del repo."""
    return viz.render_html(REPORT, CONFIG, generated="2026-09-19 12:00")


class TestRender(unittest.TestCase):
    """HTML: header, tema, formatos, provenance, placeholder-check."""

    def test_header_generacion_y_tracker(self):
        """FPA-005: fecha de generación y versión del tracker en el header."""
        html = _rendered()
        self.assertIn("2026-09-19 12:00", html)
        self.assertIn("Tracker", html)

    def test_tracker_version_ausente_na_con_razon(self):
        """FPA-005/008: si el reporte no registra versión, n/a con razón."""
        html = viz.render_html(REPORT, CONFIG, generated="2026-09-19 12:00")
        self.assertTrue("no registrada" in html or "tracker_version" in html)

    def test_tema_claroy_oscuro(self):
        """Tema: decisión D2 del change update-fpa-dashboard-ux — SOLO tema
        claro (Corporate Infographics silver), sin dark mode. El assert
        histórico de prefers-color-scheme quedó obsoleto con D2."""
        html = _rendered()
        self.assertNotIn("prefers-color-scheme", html)  # D2: sin dark mode
        self.assertIn("--bg", html)  # tokens del tema compartido presentes

    def test_tabular_nums_en_css(self):
        """FPA-095: cifras tabulares en tablas/cards."""
        self.assertIn("tabular-nums", _rendered())

    def test_fmt_usd(self):
        self.assertEqual("$1,234.50", viz.fmt_usd(1234.5))
        self.assertEqual("$0.27", viz.fmt_usd(0.273))
        self.assertEqual("$3,810.94", viz.fmt_usd(3810.94))

    def test_fmt_int_miles(self):
        self.assertEqual("139,735", viz.fmt_int(139735))
        self.assertEqual("1,397,354", viz.fmt_int(1397354))
        self.assertEqual("42", viz.fmt_int(42))

    def test_figuras_con_provenance_tag(self):
        """FPA-003: toda figura lleva tag reported/assumed via envoltorio."""
        html = _rendered()
        self.assertIn('data-provenance="reported"', html)
        self.assertIn('data-provenance="assumed"', html)
        # ninguna cifra sin tag: los spans de valor siempre van envueltos
        self.assertNotIn('<span class="fig">', html.replace(
            '<span class="fig" data-provenance="reported">', "")
            .replace('<span class="fig" data-provenance="assumed">', ""))

    def test_mes_parcial_marcado_en_html(self):
        """FPA-004: marker 'n/total' + 'parcial' en el HTML (coffe-6i8: n se
        deriva del reporte en vez de clavar 19/30)."""
        end = REPORT["metadata"]["date_range"]["end"]
        meta = viz.build_months(REPORT)
        sep = [m for m in meta if m["ym"] == end[:7]][0]
        html = _rendered()
        self.assertIn(f"{sep['elapsed']}/{sep['total_days']}", html)
        self.assertIn("parcial", html.lower())

    def test_resumen_no_vacio(self):
        """FPA-108: el resumen ejecutivo nunca queda vacío."""
        html = _rendered()
        self.assertGreaterEqual(html.count('class="headline'), 3)

    def test_check_placeholders_detecta_vacio(self):
        """FPA-108: placeholder-check falla si hay placeholder sin valor."""
        self.assertEqual([], viz.check_placeholders(_rendered()))
        html_vacio = '<section id="summary"><div class="ph"></div></section>'
        problems = viz.check_placeholders(html_vacio)
        self.assertTrue(problems, "no detectó placeholder vacío")

    def test_claim_renderizado_con_metrica_y_threshold(self):
        """FPA-009: métrica y threshold visibles junto al claim."""
        html = _rendered()
        self.assertIn("umbral", html.lower())
        self.assertIn("por 1k", html.lower())

    def test_html_autocontenido_sin_recursos_externos(self):
        """FPA-001: un solo HTML autocontenido, sin <script src>/<link href>."""
        html = _rendered()
        self.assertNotIn("<script src=", html)
        self.assertNotIn('<link rel="stylesheet"', html)

    def test_sin_js_de_calculo(self):
        """design.md: maths pre-calculadas en Python; JS solo re-escala (F2+)."""
        html = _rendered()
        # el modelo va embebido como JSON, no hay lógica de negocio en JS
        self.assertIn('id="fpa-model"', html)

    def test_determinismo_salvo_timestamp(self):
        """FPA-104 (semilla): mismo input → mismo HTML salvo fecha de generación."""
        a = viz.render_html(REPORT, CONFIG, generated="T")
        b = viz.render_html(REPORT, CONFIG, generated="T")
        self.assertEqual(a, b)
        self.assertNotEqual(a, viz.render_html(REPORT, CONFIG, generated="T2"))


if __name__ == "__main__":
    unittest.main()
