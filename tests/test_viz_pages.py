#!/usr/bin/env python3
"""
test_viz_pages.py — smoke Chromium de los dashboards publicados (parte del
ex test_viz.py): toggles de vista FPA, grillas KPI/CTAs, marginalia y render
por página (0 pageerror + contenido esperado). El smoke con DOM stub dejó
pasar 4 bugs de runtime JS a producción; solo un navegador real ejecuta el
JS de verdad. Requiere playwright + chromium (opcional en CI).
"""

import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PAGES = [
    REPO / "data" / "gantt-multitasking.html",
    REPO / "data" / "fpa-dashboard.html",
    REPO / "data" / "context-dashboard.html",
]

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


@unittest.skipUnless(HAS_PLAYWRIGHT, "playwright no instalado (opcional)")
class TestVizPages(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.chrome = find_chromium()
        if not cls.chrome:
            raise unittest.SkipTest("chromium de playwright no encontrado")

    VIEWS = ("summary", "cost", "breakdown", "habits", "outlook", "data")

    def _fpa_view_state(self, pg):
        """Mapa vista -> visible para las 6 secciones .fpa-view."""
        return {v: pg.is_visible(f"#{v}") for v in self.VIEWS}

    def _open_page(self, browser, page_path, wait_ms=500):
        """Nueva pestaña con colector de pageerror → (pg, errors)."""
        pg = browser.new_page()
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(f"file://{page_path}")
        pg.wait_for_timeout(wait_ms)
        return pg, errors

    def test_fpa_desktop_one_view_at_a_time(self):
        """coffe-dqz F1: a 1280x900 el tab 'Hábitos' deja solo esa vista
        visible y la página mide < 2 viewports (toggle a todo ancho)."""
        page_path = REPO / "data" / "fpa-dashboard.html"
        if not page_path.exists():
            self.skipTest("fpa-dashboard.html no generado")
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=self.chrome,
                                        args=["--no-sandbox"])
            pg = browser.new_page()
            pg.set_viewport_size({"width": 1280, "height": 900})
            pg.goto(f"file://{page_path}")
            pg.wait_for_timeout(300)
            pg.click("a.tab[data-view='habits']")
            pg.wait_for_timeout(200)
            state = self._fpa_view_state(pg)
            self.assertEqual(state,
                dict.fromkeys(self.VIEWS, False) | {"habits": True},
                f"fpa 1280x900: tras click en Hábitos el estado es {state}")
            height = pg.evaluate("document.body.scrollHeight")
            self.assertLess(height, 2 * 900,
                f"fpa 1280x900: página mide {height}px (>= 2 viewports)")
            browser.close()

    def test_fpa_deeplink_view_param(self):
        """coffe-dqz F1: con ?view=habits la vista activa al cargar es
        Habits (contrato vigente de share-URL por query params)."""
        page_path = REPO / "data" / "fpa-dashboard.html"
        if not page_path.exists():
            self.skipTest("fpa-dashboard.html no generado")
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=self.chrome,
                                        args=["--no-sandbox"])
            pg = browser.new_page()
            pg.set_viewport_size({"width": 1280, "height": 900})
            pg.goto(f"file://{page_path}?view=habits")
            pg.wait_for_timeout(300)
            state = self._fpa_view_state(pg)
            self.assertEqual(state,
                dict.fromkeys(self.VIEWS, False) | {"habits": True},
                f"fpa ?view=habits: estado al cargar es {state}")
            self.assertEqual(
                pg.get_attribute("a.tab[data-view='habits']", "aria-current"),
                "true", "fpa ?view=habits: tab sin aria-current")
            browser.close()

    def test_fpa_mobile_toggle_regression(self):
        """coffe-dqz F1: el toggle a 390x844 (mobile) sigue funcionando."""
        page_path = REPO / "data" / "fpa-dashboard.html"
        if not page_path.exists():
            self.skipTest("fpa-dashboard.html no generado")
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=self.chrome,
                                        args=["--no-sandbox"])
            pg = browser.new_page()
            pg.set_viewport_size({"width": 390, "height": 844})
            pg.goto(f"file://{page_path}")
            pg.wait_for_timeout(300)
            pg.click("a.tab[data-view='cost']")
            pg.wait_for_timeout(200)
            state = self._fpa_view_state(pg)
            self.assertEqual(state,
                dict.fromkeys(self.VIEWS, False) | {"cost": True},
                f"fpa 390x844: tras click en Costo el estado es {state}")
            browser.close()

    def _tops(self, pg, sel):
        """Filas visuales: número de top-positions distintas de los hijos."""
        return pg.evaluate(
            f"[...document.querySelectorAll('{sel} > *')]"
            ".map(e => Math.round(e.getBoundingClientRect().top))")

    def test_fpa_summary_cards_grid(self):
        """coffe-efw F2: a 1280px los KPI cards renderizan en >=3 columnas
        y la grilla de Summary no contiene items no-card (marginalia,
        headings y CTAs quedan fuera de .cards-grid)."""
        page_path = REPO / "data" / "fpa-dashboard.html"
        if not page_path.exists():
            self.skipTest("fpa-dashboard.html no generado")
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=self.chrome,
                                        args=["--no-sandbox"])
            pg = browser.new_page()
            pg.set_viewport_size({"width": 1280, "height": 900})
            pg.goto(f"file://{page_path}")
            pg.wait_for_timeout(300)
            tops = self._tops(pg, "#kpi-cards")
            self.assertGreaterEqual(len(tops), 3,
                "fpa: #kpi-cards sin al menos 3 cards")
            max_row = max(tops.count(t) for t in set(tops))
            self.assertGreaterEqual(max_row, 3,
                f"fpa 1280: KPI cards en {max_row} por fila, se esperaban >=3")
            # La grilla dedicada existe y TODO su contenido es card
            self.assertIsNotNone(
                pg.query_selector("#summary .cards-grid"),
                "fpa: Summary sin .cards-grid dedicada")
            non_card = pg.evaluate(
                "[...document.querySelectorAll('#summary .cards-grid > *')]"
                ".filter(e => !e.classList.contains('headline')).length")
            self.assertEqual(non_card, 0,
                "fpa: .cards-grid contiene items no-card")
            # Marginalia, headings y CTAs fuera de la grilla
            outside = pg.evaluate(
                """() => {
                  const g = document.querySelector('#summary .cards-grid');
                  const out = (sel) => !g.contains(document.querySelector(sel));
                  return {m: out('#m-summary'), h: out('#summary h2'),
                          c: out('#view-ctas')};
                }""")
            self.assertEqual(outside, {"m": True, "h": True, "c": True},
                f"fpa: marginalia/headings/CTAs dentro de la grilla: {outside}")
            browser.close()

    def test_fpa_ctas_compact_row(self):
        """coffe-efw F2: los CTAs miden <=64px de alto y quedan en una sola
        linea a >=600px de viewport."""
        page_path = REPO / "data" / "fpa-dashboard.html"
        if not page_path.exists():
            self.skipTest("fpa-dashboard.html no generado")
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=self.chrome,
                                        args=["--no-sandbox"])
            for width in (1280, 600):
                pg = browser.new_page()
                pg.set_viewport_size({"width": width, "height": 900})
                pg.goto(f"file://{page_path}")
                pg.wait_for_timeout(300)
                h = pg.evaluate(
                    "Math.round(document.querySelector('#view-ctas')"
                    ".getBoundingClientRect().height)")
                self.assertLessEqual(h, 64,
                    f"fpa {width}px: fila de CTAs mide {h}px (> 64)")
                tops = self._tops(pg, "#view-ctas")
                self.assertLessEqual(len(set(tops)), 1,
                    f"fpa {width}px: CTAs en {len(set(tops))} lineas")
                pg.close()
            browser.close()

    def test_fpa_marginalia_single_strip(self):
        """coffe-efw F2: cada vista tiene un unico strip de marginalia."""
        page_path = REPO / "data" / "fpa-dashboard.html"
        if not page_path.exists():
            self.skipTest("fpa-dashboard.html no generado")
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=self.chrome,
                                        args=["--no-sandbox"])
            pg = browser.new_page()
            pg.set_viewport_size({"width": 1280, "height": 900})
            pg.goto(f"file://{page_path}")
            pg.wait_for_timeout(300)
            counts = pg.evaluate(
                "Object.fromEntries(%s.map(v => [v, document.querySelectorAll('#' + v + ' .marginalia').length]))"
                % repr(list(self.VIEWS)))
            for v, n in counts.items():
                self.assertLessEqual(n, 1,
                    f"fpa: vista {v} con {n} strips de marginalia")
            self.assertEqual(counts.get("summary"), 1,
                f"fpa: Summary sin strip de marginalia: {counts}")
            browser.close()

    def test_fpa_kpi_single_band(self):
        """coffe-esc F3: cada cifra KPI aparece en exactamente UN card del
        primer viewport de Summary. Los headline cards desaparecieron: la
        banda KPI ES el resumen ejecutivo. Los claims quedan fuera del
        check (sus cifras pueden coincidir legitimamente con un KPI)."""
        page_path = REPO / "data" / "fpa-dashboard.html"
        if not page_path.exists():
            self.skipTest("fpa-dashboard.html no generado")
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=self.chrome,
                                        args=["--no-sandbox"])
            pg = browser.new_page()
            pg.set_viewport_size({"width": 1280, "height": 900})
            pg.goto(f"file://{page_path}")
            pg.wait_for_timeout(300)
            # Todo card de Summary vive en la banda KPI (sin headline cards)
            cards = pg.eval_on_selector_all(
                "#summary .headline", "els => els.map(e => e.textContent)")
            kpi_cards = pg.eval_on_selector_all(
                "#kpi-cards .headline", "els => els.map(e => e.textContent)")
            self.assertEqual(cards, kpi_cards,
                "fpa: Summary tiene cards fuera de la banda KPI "
                "(headline cards residuales)")
            vals = pg.eval_on_selector_all(
                "#kpi-cards .val",
                "els => els.map(e => e.textContent.trim()).filter(t => t)")
            self.assertGreaterEqual(len(vals), 3,
                f"fpa: banda KPI con {len(vals)} cifras, se esperaban >= 3")
            for v in set(vals):
                hits = sum(1 for c in kpi_cards if v in c)
                self.assertEqual(hits, 1,
                    f"fpa: cifra {v!r} aparece en {hits} cards, se esperaba 1")
            browser.close()

    # Render por página: 0 pageerror + contenido esperado.
    def test_page_renders_without_errors(self):
        """Cada página: 0 pageerror en runtime."""
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=self.chrome,
                                        args=["--no-sandbox"])
            for page_path in PAGES:
                if not page_path.exists():
                    continue  # viz no generada aún; no es fallo de este test
                _, errors = self._open_page(browser, page_path)
                self.assertEqual(errors, [],
                                 f"{page_path.name}: errores de JS en runtime: {errors}")
            browser.close()

    def test_fpa_headlines_renderizados(self):
        """fpa-dashboard: 0 pageerror y resumen ejecutivo no vacío, sin
        placeholders sin valor (FPA-108)."""
        page_path = REPO / "data" / "fpa-dashboard.html"
        if not page_path.exists():
            self.skipTest("fpa-dashboard.html no generado")
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=self.chrome,
                                        args=["--no-sandbox"])
            pg, errors = self._open_page(browser, page_path)
            self.assertEqual(errors, [],
                             f"{page_path.name}: errores de JS en runtime: {errors}")
            vals = pg.eval_on_selector_all(
                ".headline .val",
                "els => els.map(e => e.textContent.trim()).filter(t => t !== '')")
            self.assertGreaterEqual(len(vals), 3,
                f"fpa: {len(vals)} headlines con valor, se esperaban ≥ 3")
            nas = pg.eval_on_selector_all(
                ".headline .na",
                "els => els.map(e => e.textContent.trim()).filter(t => t !== '')")
            headlines = pg.eval_on_selector_all(
                ".headline", "els => els.length")
            self.assertEqual(len(vals) + len(nas), headlines,
                "fpa: headline sin valor ni n/a con razón (FPA-108)")
            n_ph = pg.eval_on_selector_all(
                "[class~='ph']", "els => els.length")
            self.assertEqual(0, n_ph, "fpa: placeholders sin valor")
            browser.close()

    def test_ctx_histograma_y_banda(self):
        """context-dashboard: 0 pageerror, header Pi-only, histograma con
        barras y banda de percentiles (coffe-0tw)."""
        page_path = REPO / "data" / "context-dashboard.html"
        if not page_path.exists():
            self.skipTest("context-dashboard.html no generado")
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=self.chrome,
                                        args=["--no-sandbox"])
            pg, errors = self._open_page(browser, page_path)
            self.assertEqual(errors, [],
                             f"{page_path.name}: errores de JS en runtime: {errors}")
            self.assertIn("Pi", pg.inner_text("h1"))
            n = pg.eval_on_selector_all(".bar", "els => els.length")
            self.assertGreater(n, 0, "ctx: histograma sin barras")
            band = pg.eval_on_selector_all(".pctl", "els => els.length")
            self.assertGreaterEqual(band, 1, "ctx: banda de percentiles ausente")
            browser.close()

    def _ctx_svg_count(self, pg, view, sel):
        """Cuenta de SVGs del selector sel en la vista view (setView)."""
        pg.evaluate(f"window.__ctx.setView('{view}')")
        return pg.eval_on_selector_all(
            f"#{view} {sel}", "els => els.length")

    def test_ctx_tabs_deeplink_y_svg_por_vista(self):
        """context-dashboard: 0 pageerror, tabs una vista a la vez +
        deep-link por hash (coffe-r3c) + export CSV + 1 SVG por vista con
        chart (coffe-ruk 2.8)."""
        page_path = REPO / "data" / "context-dashboard.html"
        if not page_path.exists():
            self.skipTest("context-dashboard.html no generado")
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=self.chrome,
                                        args=["--no-sandbox"])
            pg, errors = self._open_page(browser, page_path, wait_ms=500)
            self.assertEqual(errors, [],
                             f"{page_path.name}: errores de JS en runtime: {errors}")
            # coffe-r3c: tabs una vista a la vez + deep-link por hash
            self.assertTrue(
                pg.is_visible("#dist"), "ctx: dist default visible")
            self.assertFalse(
                pg.is_visible("#cost"), "ctx: cost no visible en default")
            pg.evaluate("window.__ctx.setView('cost')")
            self.assertTrue(pg.is_visible("#cost"))
            self.assertFalse(pg.is_visible("#dist"))
            pg.evaluate("location.hash = '#ley'")
            pg.wait_for_timeout(200)
            self.assertTrue(
                pg.is_visible("#ley"), "ctx: hashchange no activa vista")
            n = pg.eval_on_selector_all(
                ".export-csv", "els => els.length")
            self.assertGreater(n, 0, "ctx: sin botones export CSV")
            # al menos 1 SVG por vista con chart (coffe-ruk 2.8)
            for view, sel in (("dist", ".ctx-hist"),
                              ("comp", ".stack"),
                              ("ley", ".scatter")):
                n = self._ctx_svg_count(pg, view, sel)
                self.assertGreater(
                    n, 0, f"ctx: vista {view} sin SVG ({sel})")
            pg.evaluate("window.__ctx.setView('dist')")
            browser.close()

    def _sticky_offset(self, pg, sl):
        """Offset del label sticky de la fila 1 respecto del scroller a
        scrollLeft=sl."""
        pg.evaluate(f"document.querySelector('.wrap').scrollLeft = {sl}")
        pg.wait_for_timeout(100)
        return pg.evaluate("""() => {
          const wrap = document.querySelector('.wrap');
          const l = document.querySelectorAll('.row')[1].querySelector('.label');
          return Math.round(l.getBoundingClientRect().left - wrap.getBoundingClientRect().left);
        }""")

    def test_gantt_celdas_y_sticky_labels(self):
        """gantt-multitasking: 0 pageerror, >100 celdas y sticky labels
        pinned a CUALQUIER scrollLeft (viewport móvil, donde el bug era
        visible). Con containing block = fila flex completa el label queda
        en el borde izquierdo del scroller."""
        page_path = REPO / "data" / "gantt-multitasking.html"
        if not page_path.exists():
            self.skipTest("gantt-multitasking.html no generado")
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=self.chrome,
                                        args=["--no-sandbox"])
            pg, errors = self._open_page(browser, page_path)
            self.assertEqual(errors, [],
                             f"{page_path.name}: errores de JS en runtime: {errors}")
            n = pg.eval_on_selector_all(".cell", "els => els.length")
            self.assertGreater(n, 100, f"gantt: solo {n} celdas")
            pg.set_viewport_size({"width": 375, "height": 800})
            for sl in (600, 1500):
                off = self._sticky_offset(pg, sl)
                self.assertGreaterEqual(off, 0,
                    f"gantt: sticky label se despega a scrollLeft={sl} (offset {off})")
            browser.close()


if __name__ == "__main__":
    unittest.main()
