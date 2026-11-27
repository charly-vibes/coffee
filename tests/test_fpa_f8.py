#!/usr/bin/env python3
"""
test_fpa_f8.py — vista Energía con banda de caché (F8, coffe-5ng)

Cubre:
- tab/sección Energía registrada en tabs/VIEWS/CSS (patrón F6, toggle
  a todo ancho coffe-dqz) con h2 para el scroll mobile (coffe-26b)
- build_energy(): banda mensual low ≤ nominal ≤ high, total del periodo,
  meses sin telemetría visibles con razón (loss visible, FPA-008)
- chart SVG accesible: rects con tabindex/aria-label/data-label (readout
  FPA-175 reutilizado), clase chart para Descargar SVG (FPA-169)
- caption de banda (extremos 0% y 100% de acierto de caché, factor
  nominal del config) y tag assumed en toda cifra (FPA-003)
- determinismo: la vista no introduce fechas (FPA-104)
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


viz = _load("viz_fpa_f8", "scripts/viz-fpa.py")

REPORT = json.loads((REPO / "data" / "usage_report_v3.json").read_text())
CONFIG = json.loads((REPO / "config" / "fpa.json").read_text())


def _fixture():
    """f2_fixture (meses completos con toda la forma que exige el render)
    con campos de energía inyectados: 2 meses con telemetría + 1 mes solo
    con modelos sin telemetría (amp)."""
    f2 = _load("test_fpa_f2", "tests/test_fpa_f2.py")
    rep = f2.f2_fixture()
    rep["metadata"]["energy_provenance"] = "assumed"
    rep["metadata"]["energy_cache_read_factor"] = 0.1
    rep["metadata"]["energy_band_cache_factors"] = [0.0, 1.0]
    rep["monthly"]["2026-05"]["energy_kwh"] = 7.5
    rep["monthly"]["2026-05"]["energy_kwh_band"] = {"low": 2.0, "high": 40.0}
    rep["monthly"]["2026-05"]["energy_kwh_by_model"] = {
        "claude-sonnet-4-6": {"kwh": 7.5, "tier": "mid"}}
    rep["monthly"]["2026-06"]["energy_kwh"] = 1.25
    rep["monthly"]["2026-06"]["energy_kwh_band"] = {"low": 1.0, "high": 3.0}
    rep["monthly"]["2026-06"]["energy_kwh_by_model"] = {
        "gpt-5.4": {"kwh": 1.25, "tier": "mid"}}
    rep["monthly"]["2026-07"]["energy_kwh"] = 0.6
    rep["monthly"]["2026-07"]["energy_kwh_band"] = {"low": 0.2, "high": 1.0}
    rep["monthly"]["2026-07"]["energy_kwh_by_model"] = {
        "gpt-5.4": {"kwh": 0.6, "tier": "mid"},
        "amp": {"kwh": None, "reason": "sin telemetría de tokens"}}
    return rep


def _energy_section(html):
    m = re.search(r'<section id="energy".*?</section>', html, re.S)
    return m.group(0) if m else ""


class TestTabEnergia(unittest.TestCase):
    """Registro de la vista en tabs, labels, CSS y JS (patrón F6)."""

    @classmethod
    def setUpClass(cls):
        cls.html = viz.render_html(_fixture(), CONFIG, generated="2026-09-24 20:00")

    def test_tab_energia_en_tablist(self):
        self.assertIn('data-view="energy"', self.html)
        tab = re.search(r'<a class="tab"[^>]*data-view="energy"[^>]*>'
                        r'([^<]*)</a>', self.html)
        self.assertIsNotNone(tab)
        self.assertIn("Energ", tab.group(1))

    def test_seccion_fpa_view_con_h2(self):
        sec = _energy_section(self.html)
        self.assertTrue(sec, "falta <section id=energy class=fpa-view>")
        self.assertIn('<h2>', sec)

    def test_css_toggle_toda_vista(self):
        """coffe-dqz: la sección nueva entra al toggle CSS (oculta por
        defecto, visible solo con body[data-view=energy])."""
        self.assertIn(
            '#summary, #cost, #breakdown, #models, #gasto, #habits, #outlook, #energy,\n'
            '#data { display: none; }', self.html)
        self.assertRegex(
            self.html,
            r'body\[data-view="energy"\] #energy,\s*'
            r'body\[data-view="data"\] #data \{ display: block; \}')

    def test_js_views_incluye_energy(self):
        js = self.html.split("<script>")[1]
        self.assertIn('"energy"', js)
        self.assertIn('VIEWS.indexOf(', js)  # vista validada (FPA-171)


class TestModeloEnergia(unittest.TestCase):
    """build_energy(): banda mensual, total, nulls con razón."""

    @classmethod
    def setUpClass(cls):
        cls.e = viz.build_energy(_fixture())

    def test_meses_ordenados_con_banda(self):
        ms = self.e["months"]
        self.assertEqual([m["ym"] for m in ms],
                         ["2026-05", "2026-06", "2026-07"])
        may = ms[0]
        self.assertEqual((may["kwh"], may["low"], may["high"]),
                         (7.5, 2.0, 40.0))

    def test_total_del_periodo_suma_nominal_y_banda(self):
        t = self.e["total"]
        self.assertAlmostEqual(t["kwh"], 9.35, places=3)
        self.assertAlmostEqual(t["low"], 3.2, places=3)
        self.assertAlmostEqual(t["high"], 44.0, places=3)

    def test_mes_sin_telemetria_visible_con_razon(self):
        jul = self.e["months"][2]
        self.assertTrue(any("amp" in r for r in jul["no_telemetry"]))

    def test_provenance_y_factores_para_caption(self):
        self.assertEqual(self.e["provenance"], "assumed")
        self.assertEqual(self.e["factors"], [0.0, 1.0])
        self.assertEqual(self.e["nominal_factor"], 0.1)


class TestChartBanda(unittest.TestCase):
    """SVG accesible con banda low–high y nominal dentro."""

    @classmethod
    def setUpClass(cls):
        cls.html = viz.render_html(_fixture(), CONFIG, generated="2026-09-24 20:00")
        cls.sec = _energy_section(cls.html)

    def test_chart_con_clase_chart_para_dl_svg(self):
        self.assertRegex(self.sec, r'<svg class="[^"]*chart')

    def test_rects_accesibles_con_banda_en_aria_label(self):
        rects = re.findall(r'<rect [^>]*aria-label="([^"]*)"', self.sec)
        # 3 meses con banda > 0 (may, jun, jul con telemetría mixta);
        # los modelos sin telemetría quedan en el bloque de razones
        self.assertGreaterEqual(len(rects), 3, "un bar por mes con banda")
        join = " | ".join(rects)
        self.assertIn("kWh", join)
        self.assertIn("banda", join)

    def test_rects_tabindex_y_data_label_para_readout(self):
        """FPA-175: los rects usan el contrato del readout existente."""
        for rect in re.findall(r'<rect class="enb"[^>]*>', self.sec):
            self.assertIn('tabindex="0"', rect)
            self.assertIn('data-label=', rect)
            self.assertIn('aria-label=', rect)

    def test_nominal_dentro_de_banda_en_labels(self):
        for rect in re.findall(r'<rect [^>]*aria-label="([^"]*)"', self.sec):
            m = re.search(r'([\d.]+) kWh \(banda ([\d.]+)–([\d.]+)\)', rect)
            if m:
                kwh, low, high = map(float, m.groups())
                self.assertLessEqual(low, kwh)
                self.assertLessEqual(kwh, high)


class TestCaptionYProvenance(unittest.TestCase):
    """Caption de banda y tag assumed (FPA-003) en toda cifra."""

    @classmethod
    def setUpClass(cls):
        cls.html = viz.render_html(_fixture(), CONFIG, generated="2026-09-24 20:00")
        cls.sec = _energy_section(cls.html)

    def test_caption_explica_extremos(self):
        self.assertIn("0%", self.sec)
        self.assertIn("100%", self.sec)
        self.assertIn("acierto de cach", self.sec)

    def test_cifras_con_tag_assumed(self):
        self.assertIn('data-provenance="assumed"', self.sec)

    def test_meses_sin_telemetria_en_el_dom(self):
        """Loss visible: amp figura con su razón, no se omite."""
        self.assertIn("sin telemetría de tokens", self.sec)
        self.assertIn("amp", self.sec)


class TestDatasetReal(unittest.TestCase):
    """Integración con el dataset real (estilo F7, cifras del reporte)."""

    @classmethod
    def setUpClass(cls):
        cls.e = viz.build_energy(REPORT)

    def test_total_banda_cubre_nominal(self):
        t = self.e["total"]
        self.assertLess(t["low"], t["kwh"])
        self.assertLess(t["kwh"], t["high"])

    def test_identidad_por_mes(self):
        for m in self.e["months"]:
            if m["kwh"] and m["kwh"] > 0:
                self.assertLessEqual(m["low"], m["kwh"])
                self.assertLessEqual(m["kwh"], m["high"])

    def test_provenance_assumed(self):
        self.assertEqual(self.e["provenance"], "assumed")


class TestReviewFixes(unittest.TestCase):
    """Hallazgos rule-of-5 post-ship (ro5u): readout, export, fallback,
    meses parciales."""

    @classmethod
    def setUpClass(cls):
        cls.html = viz.render_html(_fixture(), CONFIG, generated="2026-09-24 20:00")
        cls.sec = _energy_section(cls.html)

    def test_readout_cubre_chart_energia(self):
        """EDGE-001: el selector del readout incluye .enb-chart (FPA-175)."""
        self.assertIn(".enb-chart rect[data-label]", self.html)

    def test_boton_descargar_svg_en_energia(self):
        """EDGE-002: el chart energía recibe botón Descargar SVG (FPA-169)."""
        self.assertIn('<button class="dl-svg" type="button">Descargar SVG</button>'
                      '<svg class="enb-chart chart"', self.html)

    def test_css_tema_para_barras_energia(self):
        """CLAR-001: las barras usan el tema compartido, no defaults."""
        self.assertIn(".enb-chart .enb { fill: var(--line)", self.html)
        self.assertIn(".enb-chart .enbn { stroke: var(--acc)", self.html)

    def test_mes_parcial_marcado(self):
        """CORR-003 (FPA-004): el fixture termina 2026-07-10 — julio parcial
        visible en aria-label y en el caption del total."""
        jul = re.search(r'aria-label="jul 26:[^"]*parcial', self.sec)
        self.assertIsNotNone(jul)
        self.assertIn("Meses parciales sin anualizar", self.sec)
        self.assertIn("jul 26 (10/31 días)", self.sec)

    def test_fallback_sin_config_na_con_razon(self):
        """CORR-001 (FPA-008): sin energía en el reporte la vista muestra
        n/a con razón, jamás '0.0 kWh' que parezca medición."""
        f2 = _load("test_fpa_f2_rf", "tests/test_fpa_f2.py")
        html = viz.render_html(f2.f2_fixture(), CONFIG,
                               generated="2026-09-24 20:00")
        sec = _energy_section(html)
        self.assertIn('data-provenance="unavailable"', sec)
        self.assertIn('class="na">n/a</span>', sec)
        self.assertIn("sin config de energía", sec)
        self.assertNotIn("0.0 kWh", sec)


class TestDeterminismo(unittest.TestCase):
    """FPA-104: la vista no introduce fechas ni no-determinismo."""

    def test_render_dos_timestamps_difiere_solo_en_ts(self):
        a = viz.render_html(_fixture(), CONFIG, generated="2026-09-24 10:00")
        b = viz.render_html(_fixture(), CONFIG, generated="2026-09-24 11:00")
        GEN = re.compile(r"Generado: \d{4}-\d{2}-\d{2} \d{2}:\d{2}")
        self.assertEqual(GEN.sub("T", a), GEN.sub("T", b))


if __name__ == "__main__":
    unittest.main()
