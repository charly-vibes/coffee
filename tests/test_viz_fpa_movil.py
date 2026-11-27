#!/usr/bin/env python3
"""
test_viz_fpa_movil.py — coffe-26b: hallazgos móviles del smoke 390x844 del
fpa-dashboard (parte del ex test_viz.py, verificación uyd).

Verificado contra el build actual: (1) heatmap sticky YA cubierto por
table.small en <=599px — test de regresión; (2) touch targets WCAG
2.5.8: inputs 44px (Apple HIG, pocos elementos) y summaries anidados
del ttree >=24px (AA real; 44px duplicaría el scroll del Desglose);
(3) tab-change: el h2 de la vista debe quedar bajo la topbar, no a
350px bajo un header idéntico; (4) favicon inline + labels del
forecast en una sola línea de texto.
"""

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


@unittest.skipUnless(HAS_PLAYWRIGHT, "playwright no instalado (opcional)")
class TestFpaMobileCoffe26b(unittest.TestCase):
    """coffe-26b: hallazgos móviles del smoke 390x844 (verificación uyd)."""

    @classmethod
    def setUpClass(cls):
        cls.chrome = find_chromium()
        if not cls.chrome:
            raise unittest.SkipTest("chromium de playwright no encontrado")
        cls.page_path = REPO / "data" / "fpa-dashboard.html"
        if not cls.page_path.exists():
            raise unittest.SkipTest("fpa-dashboard.html no generado")

    def _mobile(self, p):
        pg = p.chromium.launch(executable_path=self.chrome,
                               args=["--no-sandbox"]).new_page()
        pg.set_viewport_size({"width": 390, "height": 844})
        pg.goto(f"file://{self.page_path}")
        pg.wait_for_timeout(300)
        return pg

    def test_fpa_mobile_inputs_44px(self):
        """coffe-26b: .b-input/.fc-input >= 44px de alto a 390x844."""
        with sync_playwright() as p:
            pg = self._mobile(p)
            pg.click("a.tab[data-view='cost']")
            pg.wait_for_timeout(200)
            h = pg.evaluate(
                "document.querySelector('.b-input')"
                ".getBoundingClientRect().height")
            self.assertGreaterEqual(h, 44,
                f"fpa 390x844: .b-input mide {h:.0f}px (< 44)")
            pg.click("a.tab[data-view='outlook']")
            pg.wait_for_timeout(200)
            h = pg.evaluate(
                "document.querySelector('.fc-input')"
                ".getBoundingClientRect().height")
            self.assertGreaterEqual(h, 44,
                f"fpa 390x844: .fc-input mide {h:.0f}px (< 44)")
            pg.close()

    def test_fpa_mobile_ttree_summaries_24px(self):
        """coffe-26b: summaries anidados del ttree >= 24px (WCAG 2.5.8 AA)
        a 390x844, sin duplicar la altura del Desglose (44px HIG no aplica
        a 73 summaries densos por diseño)."""
        with sync_playwright() as p:
            pg = self._mobile(p)
            pg.click("a.tab[data-view='breakdown']")
            pg.wait_for_timeout(200)
            h = pg.evaluate(
                "document.querySelector('.ttree details > summary')"
                ".getBoundingClientRect().height")
            self.assertGreaterEqual(h, 24,
                f"fpa 390x844: summary del ttree mide {h:.0f}px (< 24)")
            pg.close()

    def test_fpa_mobile_tab_change_brings_view_heading_up(self):
        """coffe-26b: al cambiar de tab en móvil el h2 de la vista queda
        bajo la topbar (percibe el cambio), no a ~350px de header común."""
        with sync_playwright() as p:
            pg = self._mobile(p)
            pg.evaluate("window.scrollTo(0, 0)")
            pg.click("a.tab[data-view='cost']")
            pg.wait_for_timeout(200)
            top = pg.evaluate(
                "document.querySelector('#cost > h2')"
                ".getBoundingClientRect().top")
            self.assertLess(top, 90,
                f"fpa 390x844: #cost>h2 quedó a {top:.0f}px del top "
                "tras cambiar de tab")
            pg.close()

    def test_fpa_favicon_icon_link(self):
        """coffe-26b: <head> declara un icono inline (data:) — sin 404 de
        /favicon.ico en el deploy."""
        with sync_playwright() as p:
            pg = self._mobile(p)
            href = pg.evaluate(
                "document.head.querySelector('link[rel~=icon]')"
                "?.getAttribute('href') || ''")
            self.assertTrue(href.startswith("data:"),
                f"fpa: sin link de icono inline (href={href!r})")
            pg.close()

    def test_fpa_outlook_rate_label_single_line(self):
        """coffe-26b: el texto del label 'Cambio de rate %' no se parte en
        dos líneas a 390x844 (labels inline del forecast fluyen mal)."""
        with sync_playwright() as p:
            pg = self._mobile(p)
            pg.click("a.tab[data-view='outlook']")
            pg.wait_for_timeout(200)
            tops = pg.evaluate("""() => {
              const l = [...document.querySelectorAll('label')]
                .find(x => x.textContent.includes('Cambio de rate'));
              const rg = document.createRange();
              rg.selectNodeContents(l.firstChild);
              return [...rg.getClientRects()]
                .filter(r => r.width > 0).map(r => Math.round(r.top));
            }""")
            self.assertEqual(len(set(tops)), 1,
                f"fpa 390x844: texto del label en {len(set(tops))} "
                f"líneas (tops={tops})")
            pg.close()

    def test_fpa_heatmap_sticky_mobile_regression(self):
        """coffe-26b (hallazgo 1, stale): la primera columna del heatmap
        día×hora YA es sticky a 390x844 via table.small — test de
        regresión para que no se pierda al retocar los selectores."""
        with sync_playwright() as p:
            pg = self._mobile(p)
            pg.click("a.tab[data-view='habits']")
            pg.wait_for_timeout(300)
            pos, left = pg.evaluate("""() => {
              const th = document.querySelector(
                'table.heatmap tbody tr:first-child th');
              th.closest('table').scrollLeft = 324;
              return [getComputedStyle(th).position,
                      Math.round(th.getBoundingClientRect().left)];
            }""")
            self.assertEqual(pos, "sticky",
                "fpa 390x844: th del heatmap sin position:sticky")
            self.assertLess(left, 20,
                f"fpa 390x844: th del heatmap a {left}px tras scroll "
                "horizontal de 324px")
            pg.close()


if __name__ == "__main__":
    unittest.main()
