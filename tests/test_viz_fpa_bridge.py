#!/usr/bin/env python3
"""
test_viz_fpa_bridge.py — coffe-8nw F6/D6: bridge con selector de mes en el
fpa-dashboard (parte del ex test_viz.py).

- Un único waterfall visible, montado desde <template data-bridge-month>
  sin recarga; cambiar de mes re-renderiza.
- Identidad Volume+Mix+Rate por mes (golden existente, FPA-065).
- Sin ningún mes con mix → n/a con razón y sin selector vacío.
"""

import importlib.util
import json
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False


def find_chromium():
    """Busca un chromium instalado por playwright (varias versiones)."""
    import glob
    for pat in ("~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome",
                "~/.cache/ms-playwright/chromium_headless_shell-*/chrome-linux*/headless_shell"):
        found = sorted(glob.glob(str(Path(pat).expanduser())))
        if found:
            return found[-1]
    return None


def _money(s):
    """'+$1,110.03' / '-$455.17' -> float."""
    return float(s.replace("$", "").replace(",", ""))


class TestFpaBridgeSelector(unittest.TestCase):
    """coffe-8nw F6/D6: bridge con selector de mes — un único waterfall
    visible, montado desde <template data-bridge-month> sin recarga;
    identidad Volume+Mix+Rate por mes; sin ningún mes con mix → n/a con
    razón y sin selector vacío."""

    PAGE = REPO / "data" / "fpa-dashboard.html"
    CONFIG = json.loads((REPO / "config" / "fpa.json").read_text())

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "viz_fpa_bridge", REPO / "scripts" / "viz-fpa.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        cls.viz = mod

    def setUp(self):
        if not self.PAGE.exists():
            self.skipTest("fpa-dashboard.html no generado")

    def _open_bridge(self, pw):
        """Browser+pestaña con el bridge de Cost abierto → (browser, pg)."""
        browser = pw.chromium.launch(executable_path=find_chromium(),
                                     args=["--no-sandbox"])
        pg = browser.new_page()
        pg.set_viewport_size({"width": 1280, "height": 900})
        pg.goto(f"file://{self.PAGE}")
        pg.wait_for_timeout(300)
        pg.click("a.tab[data-view='cost']")
        pg.click("#bridge > summary")
        pg.wait_for_timeout(100)
        return browser, pg

    def _bridge_waterfalls(self, pg):
        """{'total', 'visible'} de los svg.wf.chart dentro de #bridge."""
        svgs = "document.querySelectorAll('#bridge svg.wf.chart')"
        return {
            "total": pg.evaluate(f"{svgs}.length"),
            "visible": pg.evaluate(
                f"[...{svgs}].filter(e => e.getClientRects().length > 0).length"),
        }

    def _selector_y_templates(self, pg):
        """Opciones del selector de mes y data-bridge-month de templates."""
        opts = pg.eval_on_selector_all(
            "#bridge-month option", "els => els.map(e => e.value)")
        tms = pg.evaluate(
            "[...document.querySelectorAll("
            "'template[data-bridge-month]')].map(t => "
            "t.getAttribute('data-bridge-month'))")
        return opts, tms

    def _bridge_vals(self, pg):
        """data-label -> data-value de los rect del waterfall montado."""
        return pg.evaluate(
            "() => { const out = {}; document.querySelector("
            "'#bridge svg.wf.chart').querySelectorAll("
            "'rect[data-label]').forEach(r => "
            "out[r.getAttribute('data-label')] = "
            "r.getAttribute('data-value')); return out; }")

    def test_fpa_bridge_un_waterfall_visible(self):
        """Exactamente un waterfall visible a la vez; cambiar de mes
        re-renderiza sin recarga; los meses sin mix quedan fuera del
        selector (cada template tiene su opción y viceversa)."""
        if not HAS_PLAYWRIGHT:
            self.skipTest("playwright no instalado (opcional)")
        if not find_chromium():
            self.skipTest("chromium de playwright no encontrado")
        with sync_playwright() as pw:
            browser, pg = self._open_bridge(pw)
            svgs = self._bridge_waterfalls(pg)
            self.assertEqual(1, svgs["total"],
                "fpa bridge: debe haber exactamente un waterfall en el DOM")
            self.assertEqual(1, svgs["visible"],
                "fpa bridge: el waterfall montado no es visible")
            opts, tms = self._selector_y_templates(pg)
            self.assertGreaterEqual(len(opts), 1,
                "fpa bridge: selector vacío (debería ser n/a sin selector)")
            self.assertEqual(sorted(opts), sorted(tms),
                "fpa bridge: templates y opciones del selector no casan 1:1")
            # cambiar mes re-renderiza sin recarga (sigue habiendo 1 solo)
            label0 = pg.get_attribute("#bridge svg.wf.chart", "aria-label")
            otro = next(o for o in opts if o != pg.input_value("#bridge-month"))
            pg.select_option("#bridge-month", otro)
            pg.wait_for_timeout(100)
            self.assertEqual(1, self._bridge_waterfalls(pg)["total"],
                "fpa bridge: cambiar de mes dejó más de un waterfall")
            label1 = pg.get_attribute("#bridge svg.wf.chart", "aria-label")
            self.assertNotEqual(label0, label1,
                "fpa bridge: el waterfall no cambió de mes al seleccionar")
            browser.close()

    def test_fpa_bridge_identidad_por_mes(self):
        """Identidad Volume+Mix+Rate = Δ (cost₁ − cost₀) dentro de $0.01
        para cada mes del selector (golden existente, FPA-065)."""
        if not HAS_PLAYWRIGHT:
            self.skipTest("playwright no instalado (opcional)")
        if not find_chromium():
            self.skipTest("chromium de playwright no encontrado")
        with sync_playwright() as pw:
            browser, pg = self._open_bridge(pw)
            opts = pg.eval_on_selector_all(
                "#bridge-month option", "els => els.map(e => e.value)")
            self.assertGreaterEqual(len(opts), 1,
                "fpa bridge: sin selector de mes, no hay identidad que verificar")
            for opt in opts:
                pg.select_option("#bridge-month", opt)
                pg.wait_for_timeout(50)
                vals = self._bridge_vals(pg)
                total = (_money(vals["cost₀"])
                         + _money(vals["Volumen"]) + _money(vals["Mix"])
                         + _money(vals["Rate"]))
                # Los componentes display están redondeados a 2dp por su
                # cuenta: la identidad FPA-065 se verifica exacta (sin
                # redondeo) en el generador — acá tolera 0.02 (4 redondeos).
                self.assertLessEqual(abs(total - _money(vals["cost₁"])), 0.02,
                    f"fpa bridge {opt}: identidad PVM rota ({vals})")
            browser.close()

    def test_fpa_bridge_na_sin_meses_con_mix(self):
        """Con ningún mes con datos de mix, la sección muestra n/a con la
        razón — sin selector vacío ni templates (FPA-008)."""
        spec = importlib.util.spec_from_file_location(
            "test_fpa_f2_bridge", REPO / "tests" / "test_fpa_f2.py")
        f2 = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(f2)
        fx = f2.f2_fixture()
        # el caso real del reporte: modelos con coste $0 en todos los meses
        for mo in fx["monthly"].values():
            mo["models"] = {"amp": {"interactions": mo["interactions"],
                                    "cost_effective": 0.0}}
            mo["cost_effective"] = 0.0
        html = self.viz.render_html(fx, self.CONFIG, generated="T")
        i = html.index('data-tree="bridge"')
        section = html[i:html.index("</details>", i)]
        self.assertIn("n/a", section,
            "fpa bridge: sin meses con mix debe mostrar n/a con razón")
        self.assertIn("ningún mes con datos de mix", section)
        self.assertNotIn('id="bridge-month"', html,
            "fpa bridge: selector vacío emitido sin meses con mix")
        self.assertNotIn("<template data-bridge-month", html,
            "fpa bridge: templates emitidos sin meses con mix")


if __name__ == "__main__":
    unittest.main()
