#!/usr/bin/env python3
"""
test_viz.py — smoke test de los HTML generados en un Chromium real.

Este test existe porque el smoke test con DOM stub dejó pasar 4 bugs de
runtime JS a producción (createElementNS sin namespace, fmtK indefinido,
TDZ, id equivocado). Solo un navegador real ejecuta el JS de verdad.

Requiere playwright + chromium (opcional en CI):
    uv tool install playwright  # o pip install playwright
    python3 -m playwright install chromium

Uso: python3 tests/test_viz.py
Salta con SKIPPED si playwright no está instalado.
"""

import html as html_mod
import importlib.util
import json
import re
import sys
import tempfile
import unittest
from html.parser import HTMLParser
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

    def test_page_renders_without_errors(self):
        """Cada página: 0 pageerror, y produce su contenido esperado."""
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=self.chrome,
                                        args=["--no-sandbox"])
            for page_path in PAGES:
                if not page_path.exists():
                    continue  # viz no generada aún; no es fallo de este test
                pg = browser.new_page()
                errors = []
                pg.on("pageerror", lambda e: errors.append(str(e)))
                pg.goto(f"file://{page_path}")
                pg.wait_for_timeout(500)
                self.assertEqual(errors, [],
                                 f"{page_path.name}: errores de JS en runtime: {errors}")
                if page_path.name == "fpa-dashboard.html":
                    # FPA-108: resumen ejecutivo no vacío y sin placeholders sin valor
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
                elif page_path.name == "context-dashboard.html":
                    # ctx-viz (coffe-0tw): header Pi-only + histograma con barras
                    self.assertIn("Pi", pg.inner_text("h1"))
                    n = pg.eval_on_selector_all(".bar", "els => els.length")
                    self.assertGreater(n, 0, "ctx: histograma sin barras")
                    band = pg.eval_on_selector_all(".pctl", "els => els.length")
                    self.assertGreaterEqual(band, 1, "ctx: banda de percentiles ausente")
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
                        pg.evaluate(f"window.__ctx.setView('{view}')")
                        n = pg.eval_on_selector_all(
                            f"#{view} {sel}", "els => els.length")
                        self.assertGreater(
                            n, 0, f"ctx: vista {view} sin SVG ({sel})")
                    pg.evaluate("window.__ctx.setView('dist')")
                else:  # gantt
                    n = pg.eval_on_selector_all(".cell", "els => els.length")
                    self.assertGreater(n, 100, f"gantt: solo {n} celdas")
                    # sticky labels: pinned a CUALQUIER scrollLeft (viewport móvil,
                    # donde el bug era visible). Con containing block = fila flex
                    # completa el label queda en el borde izquierdo del scroller.
                    pg.set_viewport_size({"width": 375, "height": 800})
                    for sl in (600, 1500):
                        pg.evaluate(f"document.querySelector('.wrap').scrollLeft = {sl}")
                        pg.wait_for_timeout(100)
                        off = pg.evaluate("""() => {
                          const wrap = document.querySelector('.wrap');
                          const l = document.querySelectorAll('.row')[1].querySelector('.label');
                          return Math.round(l.getBoundingClientRect().left - wrap.getBoundingClientRect().left);
                        }""")
                        self.assertGreaterEqual(off, 0,
                            f"gantt: sticky label se despega a scrollLeft={sl} (offset {off})")
                pg.close()
            browser.close()


class _VisibleText(HTMLParser):
    """Recolecta el texto visible: ignora atributos (los ids viajan a
    title), <title>, <script>/<style> (CDATA) y los chips de marginalia
    (que sí llevan ids por diseño — coffe-6lz anti-goal)."""

    SKIP_TAGS = {"title", "script", "style"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.stack = []  # (tag, skip_subtree: bool)

    def handle_starttag(self, tag, attrs):
        marg = "marginalia" in dict(attrs).get("class", "").split()
        skip = tag in self.SKIP_TAGS or marg or any(s for _, s in self.stack)
        self.stack.append((tag, skip))

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        if any(s for _, s in self.stack):
            return
        self.parts.append(data)


class TestFpaIdHierarchy(unittest.TestCase):
    """coffe-6lz F4: jerarquía tipográfica — los ids FPA-xxx dejan de
    competir con las cifras primarias: cero matches en el texto visible;
    siguen machine-readable en atributos title."""

    PAGE = REPO / "data" / "fpa-dashboard.html"

    def setUp(self):
        if not self.PAGE.exists():
            self.skipTest("fpa-dashboard.html no generado")
        self.html = self.PAGE.read_text()

    def test_fpa_ids_out_of_visible_body(self):
        p = _VisibleText()
        p.feed(self.html)
        visible = " ".join(p.parts)
        refs = re.findall(r"FPA-\d{3}", visible)
        self.assertEqual(
            refs, [],
            f"FPA-ids en texto visible del cuerpo ({len(refs)}): "
            + ", ".join(sorted(set(refs))[:8]))

    def test_fpa_ids_still_machine_readable(self):
        """Anti-goal: no borrar ids — deben quedar en title/footnotes."""
        titles = " ".join(re.findall(r'title="([^"]*)"', self.html))
        ids = re.findall(r"FPA-\d{3}", html_mod.unescape(titles))
        self.assertGreaterEqual(
            len(ids), 10,
            f"solo {len(ids)} ids FPA en titles; el movimiento los perdió")


class _DisclosureMap(HTMLParser):
    """Recolecta los <details data-tree> de sección y su estado open por
    defecto (coffe-x6l F5). Ignora <script>/<style> (el JS de viz-fpa.py
    re-crea trees en re-render) y los details anidados de filas (tlabel,
    sin data-tree por diseño)."""

    SKIP_TAGS = {"script", "style"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.details = {}  # data-tree -> bool open
        self.stack = []

    def handle_starttag(self, tag, attrs):
        skip = tag in self.SKIP_TAGS or any(s for _, s in self.stack)
        self.stack.append((tag, skip))
        if tag == "details" and not skip:
            d = dict(attrs)
            if "data-tree" in d:
                self.details[d["data-tree"]] = "open" in d

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                return


class TestFpaDisclosure(unittest.TestCase):
    """coffe-x6l F5 (D5): disclosure por defecto y summaries estilizados.

    - Solo la sección primaria por vista abre expandida (Cost→Presupuesto,
      Breakdown→Árbol Portfolio, Habits→Heatmap, Outlook→Forecast,
      Data→Data y método); el resto colapsado.
    - Sin marcador huérfano: ningún nodo de texto de disclosure (▼/▸/▾)
      fuera de un <summary> — el marcador vive en el ::before del CSS.
    - Summaries colapsados con target táctil >=44px (group-box 90s).
    """

    PAGE = REPO / "data" / "fpa-dashboard.html"

    # Sección primaria por vista → abierta por defecto
    OPEN = {"budget", "portfolio", "heatmap", "forecast", "data-method"}
    # Resto de secciones disclosure → colapsadas por defecto
    CLOSED = {"bridge", "alerts", "plan-economy", "reconciliation",
              "time", "tool", "pareto", "weekly", "skills-commands",
              "sessions", "concurrency", "lifecycle", "timeline"}

    def setUp(self):
        if not self.PAGE.exists():
            self.skipTest("fpa-dashboard.html no generado")
        self.html = self.PAGE.read_text()

    def test_fpa_disclosure_defaults_por_vista(self):
        p = _DisclosureMap()
        p.feed(self.html)
        for name in self.OPEN:
            self.assertIn(name, p.details, f"{name}: sección sin data-tree")
            self.assertTrue(p.details[name], f"{name}: primaria, debe abrir")
        for name in self.CLOSED:
            if name in p.details:  # secciones condicionales (n/a no emiten)
                self.assertFalse(p.details[name],
                                 f"{name}: no-primaria, debe colapsar")
        desconocidas = set(p.details) - self.OPEN - self.CLOSED
        self.assertEqual(desconocidas, set(),
                         f"details[data-tree] sin política declarada: "
                         f"{sorted(desconocidas)}")

    def test_fpa_no_orphan_disclosure_marker(self):
        """Ningún nodo de texto de disclosure fuera de un <summary>: el
        marcador ▸/▾ va integrado al summary vía ::before del CSS; los
        markers de varianza (▼) viajan con su texto, nunca solos."""

        class _Orphans(_VisibleText):
            SUMMARY_TAGS = {"summary"}

            def __init__(self):
                super().__init__()
                self.in_summary = 0
                self.orphans = []

            def handle_starttag(self, tag, attrs):
                if tag in self.SUMMARY_TAGS and not any(
                        s for _, s in self.stack):
                    self.in_summary += 1
                super().handle_starttag(tag, attrs)

            def handle_endtag(self, tag):
                if tag in self.SUMMARY_TAGS and self.in_summary:
                    self.in_summary -= 1
                super().handle_endtag(tag)

            def handle_data(self, data):
                super().handle_data(data)
                if not self.in_summary and data.strip() in {"▼", "▸", "▾"}:
                    self.orphans.append(data.strip())

        p = _Orphans()
        p.feed(self.html)
        self.assertEqual(p.orphans, [],
                         "marcadores de disclosure huérfanos (fuera de "
                         f"summary): {p.orphans[:8]}")
        # y el marcador integrado existe en el CSS del summary
        self.assertIn("summary::before", self.html,
                      "summary sin marcador integrado (::before)")

    def test_fpa_summaries_target_44px(self):
        """Summaries (abiertos y colapsados) con target táctil >=44px:
        group-box 90s con min-height en el summary mismo."""
        if not HAS_PLAYWRIGHT:
            self.skipTest("playwright no instalado (opcional)")
        chrome = find_chromium()
        if not chrome:
            self.skipTest("chromium de playwright no encontrado")
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=chrome,
                                         args=["--no-sandbox"])
            pg = browser.new_page()
            pg.set_viewport_size({"width": 1280, "height": 900})
            pg.goto(f"file://{self.PAGE}")
            pg.wait_for_timeout(300)
            n_collapsed = 0
            for view in ("cost", "breakdown", "habits", "outlook", "data"):
                pg.click(f"a.tab[data-view='{view}']")
                pg.wait_for_timeout(150)
                heights = pg.evaluate(
                    "Array.from(document.querySelectorAll("
                    f"'#{view} details.tree > summary')).map(s => "
                    "s.getBoundingClientRect().height)")
                self.assertTrue(heights,
                                f"{view}: sin summaries de secciones tree")
                for h in heights:
                    self.assertGreaterEqual(h, 44,
                        f"{view}: summary con {h}px (< 44px)")
                n_collapsed += pg.evaluate(
                    f"document.querySelectorAll(" 
                    f"'#{view} details.tree:not([open]) > summary').length")
            browser.close()
        self.assertGreaterEqual(
            n_collapsed, 5,
            f"solo {n_collapsed} summaries colapsados; "
            "los defaults no-primarios debían colapsar")


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

    def _open_bridge(self, pg):
        pg.click("a.tab[data-view='cost']")
        pg.click("#bridge > summary")
        pg.wait_for_timeout(100)

    def test_fpa_bridge_un_waterfall_visible(self):
        """Exactamente un waterfall visible a la vez; cambiar de mes
        re-renderiza sin recarga; los meses sin mix quedan fuera del
        selector (cada template tiene su opción y viceversa)."""
        if not HAS_PLAYWRIGHT:
            self.skipTest("playwright no instalado (opcional)")
        chrome = find_chromium()
        if not chrome:
            self.skipTest("chromium de playwright no encontrado")
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=chrome,
                                         args=["--no-sandbox"])
            pg = browser.new_page()
            pg.set_viewport_size({"width": 1280, "height": 900})
            pg.goto(f"file://{self.PAGE}")
            pg.wait_for_timeout(300)
            self._open_bridge(pg)
            svgs = "document.querySelectorAll('#bridge svg.wf.chart')"
            self.assertEqual(1, pg.evaluate(f"{svgs}.length"),
                "fpa bridge: debe haber exactamente un waterfall en el DOM")
            visibles = pg.evaluate(
                f"[...{svgs}].filter(e => e.getClientRects().length > 0).length")
            self.assertEqual(1, visibles,
                "fpa bridge: el waterfall montado no es visible")
            opts = pg.eval_on_selector_all(
                "#bridge-month option", "els => els.map(e => e.value)")
            tms = pg.evaluate(
                "[...document.querySelectorAll(" 
                "'template[data-bridge-month]')].map(t => "
                "t.getAttribute('data-bridge-month'))")
            self.assertGreaterEqual(len(opts), 1,
                "fpa bridge: selector vacío (debería ser n/a sin selector)")
            self.assertEqual(sorted(opts), sorted(tms),
                "fpa bridge: templates y opciones del selector no casan 1:1")
            # cambiar mes re-renderiza sin recarga (sigue habiendo 1 solo)
            label0 = pg.get_attribute("#bridge svg.wf.chart", "aria-label")
            otro = next(o for o in opts if o != pg.input_value("#bridge-month"))
            pg.select_option("#bridge-month", otro)
            pg.wait_for_timeout(100)
            self.assertEqual(1, pg.evaluate(f"{svgs}.length"),
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
        chrome = find_chromium()
        if not chrome:
            self.skipTest("chromium de playwright no encontrado")
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=chrome,
                                         args=["--no-sandbox"])
            pg = browser.new_page()
            pg.set_viewport_size({"width": 1280, "height": 900})
            pg.goto(f"file://{self.PAGE}")
            pg.wait_for_timeout(300)
            self._open_bridge(pg)
            opts = pg.eval_on_selector_all(
                "#bridge-month option", "els => els.map(e => e.value)")
            self.assertGreaterEqual(len(opts), 1,
                "fpa bridge: sin selector de mes, no hay identidad que verificar")
            for opt in opts:
                pg.select_option("#bridge-month", opt)
                pg.wait_for_timeout(50)
                vals = pg.evaluate(
                    "() => { const out = {}; document.querySelector("
                    "'#bridge svg.wf.chart').querySelectorAll("
                    "'rect[data-label]').forEach(r => "
                    "out[r.getAttribute('data-label')] = "
                    "r.getAttribute('data-value')); return out; }")
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
@unittest.skipUnless(HAS_PLAYWRIGHT, "playwright no instalado (opcional)")
class TestFpaMobileCoffe26b(unittest.TestCase):
    """coffe-26b: hallazgos móviles del smoke 390x844 (verificación uyd).

    Verificado contra el build actual: (1) heatmap sticky YA cubierto por
    table.small en <=599px — test de regresión; (2) touch targets WCAG
    2.5.8: inputs 44px (Apple HIG, pocos elementos) y summaries anidados
    del ttree >=24px (AA real; 44px duplicaría el scroll del Desglose);
    (3) tab-change: el h2 de la vista debe quedar bajo la topbar, no a
    350px bajo un header idéntico; (4) favicon inline + labels del
    forecast en una sola línea de texto.
    """

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


@unittest.skipUnless(HAS_PLAYWRIGHT, "playwright no instalado (opcional)")
class TestVizDdlChromium(unittest.TestCase):
    """coffe-x1s (5.7): smoke Chromium de ddl-dashboard.html — 0 pageerrors,
    charts render, disclosure presente con datos vacíos."""

    DDL_VIEWS = ("adopcion", "dosis-respuesta", "pre-post", "lazo-feedback",
                 "errores", "calidad")

    @classmethod
    def setUpClass(cls):
        cls.chrome = find_chromium()
        if not cls.chrome:
            raise unittest.SkipTest("chromium de playwright no encontrado")
        spec = importlib.util.spec_from_file_location(
            "viz_ddl_smoke", REPO / "scripts" / "viz-ddl.py")
        cls.viz_ddl = importlib.util.module_from_spec(spec)
        sys.modules["viz_ddl_smoke"] = cls.viz_ddl
        spec.loader.exec_module(cls.viz_ddl)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.empty_path = Path(cls.tmp.name) / "ddl-empty.html"
        cls.empty_path.write_text(
            cls.viz_ddl.build_html({"metadata": {}, "repos": [],
                                    "monthly": [],
                                    "analysis": {"its": [], "did": [],
                                                 "dose_response": []}}))
        report = {"metadata": {"managed_tools": ["wai"],
                               "fleet_definition": "x",
                               "seed_bootstrap": 42},
                  "repos": [],
                  "monthly": [
                      {"repo": "r1", "month": "2026-09",
                       "revert_ratio": 0.1, "fix_ratio": None,
                       "test_commit_ratio": 0.4, "ah_check_findings": 2.0,
                       "interactions": 100, "errors_api_per_100": 0.3,
                       "recovery_p50_interactions": 3.0,
                       "recovery_censored_rate": 0.2, "recovery_n": 5,
                       "session_commit_latency_p50_h": 1.5,
                       "ci_fail_rate": None,
                       "src": {"revert_ratio": "reported",
                               "fix_ratio": "missing",
                               "test_commit_ratio": "reported",
                               "ah_check_findings": "reported",
                               "interactions": "reported",
                               "errors_api_per_100": "reported",
                               "recovery_p50_interactions": "reported",
                               "recovery_censored_rate": "reported",
                               "recovery_n": "reported",
                               "session_commit_latency_p50_h": "reported",
                               "ci_fail_rate": "missing"}},
                      {"repo": "r2", "month": "2026-09",
                       "revert_ratio": 0.2, "fix_ratio": 0.3,
                       "test_commit_ratio": 0.5, "ah_check_findings": 1.0,
                       "src": {"revert_ratio": "reported",
                               "fix_ratio": "reported",
                               "test_commit_ratio": "reported",
                               "ah_check_findings": "reported"}}],
                  "analysis": {
                      "its": [], "did": [],
                      "dose_response": [
                          {"metric": "ah_check_findings",
                           "spearman_rho": 0.6, "n": 4,
                           "points": [{"repo": "r1", "dose": 0.0,
                                       "outcome": 1.0},
                                      {"repo": "r2", "dose": 0.5,
                                       "outcome": 2.0},
                                      {"repo": "r3", "dose": 1.0,
                                       "outcome": 3.0},
                                      {"repo": "r4", "dose": 0.25,
                                       "outcome": 1.5}]}]}}
        cls.data_path = Path(cls.tmp.name) / "ddl-data.html"
        cls.data_path.write_text(cls.viz_ddl.build_html(report))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _open(self, path, url_extra=""):
        pg = self._browser.new_page()
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.set_viewport_size({"width": 1280, "height": 900})
        pg.goto(f"file://{path}{url_extra}")
        pg.wait_for_timeout(300)
        return pg, errors

    @classmethod
    def setUp(cls):
        from playwright.sync_api import sync_playwright
        cls._pw = sync_playwright().start()
        cls._browser = cls._pw.chromium.launch(
            executable_path=cls.chrome, args=["--no-sandbox"])

    @classmethod
    def tearDown(cls):
        cls._browser.close()
        cls._pw.stop()

    def test_ddl_sin_datos_0_pageerrors_y_disclosure(self):
        """Spec 'disclosure no removible': banda presente aunque todas las
        métricas tengan provenance missing; 0 pageerrors con tab vacío."""
        pg, errors = self._open(self.empty_path)
        self.assertEqual(errors, [], f"pageerrors: {errors}")
        self.assertTrue(pg.is_visible("aside.disclosure"),
                        "disclosure ausente con datos vacíos")
        for v in self.DDL_VIEWS:
            pg.click(f"a.tab[data-view='{v}']")
            pg.wait_for_timeout(100)
            self.assertTrue(pg.is_visible(f"section#{v}"),
                            f"vista {v} no visible tras click")
        self.assertEqual(errors, [])
        pg.close()

    def test_ddl_deeplink_hash(self):
        """Deep-link por hash (#calidad) activa la vista al cargar."""
        pg, errors = self._open(self.empty_path, "#calidad")
        self.assertEqual(errors, [])
        self.assertTrue(pg.is_visible("section#calidad"))
        pg.close()

    def test_ddl_charts_render_scatter(self):
        """Con datos: scatter SVG inline renderiza (bbox > 0) y 0 pageerrors."""
        pg, errors = self._open(self.data_path)
        self.assertEqual(errors, [], f"pageerrors: {errors}")
        pg.click("a.tab[data-view='dosis-respuesta']")
        pg.wait_for_timeout(200)
        box = pg.locator("svg.ddl-chart").first.bounding_box()
        self.assertIsNotNone(box, "scatter .ddl-chart sin bbox")
        self.assertGreater(box["width"], 100, "scatter no renderizó")
        self.assertEqual(errors, [])
        pg.close()

    def test_ddl_export_csv_funciona(self):
        """El botón Exportar CSV dispara una descarga con contenido."""
        pg, errors = self._open(self.data_path)
        pg.click("a.tab[data-view='calidad']")
        pg.wait_for_timeout(200)
        with pg.expect_download() as dl_info:
            pg.click("section#calidad button.export-csv")
        dl = dl_info.value
        path = Path(dl.path())
        contenido = path.read_text()
        self.assertIn("revert_ratio", contenido)
        self.assertEqual(errors, [])
        pg.close()


class TestMobileViewportMeta(unittest.TestCase):
    """coffe-tmm.2: índice, Contexto y Gantt usan el viewport real del
    teléfono. Ambas plantillas omitían meta viewport → Chromium móvil con
    pantalla de 390px reportaba clientWidth=980 (fallback). Gate: las tres
    páginas declaran width=device-width, initial-scale=1 sin bloquear zoom,
    y en Chromium móvil 390x844 documentElement.clientWidth=390 CSS px.
    No se exige innerWidth=390: el overflow preexistente de Gantt (421px) es
    de coffe-tmm.3."""

    @classmethod
    def setUpClass(cls):
        cls.chrome = find_chromium()
        if not cls.chrome:
            raise unittest.SkipTest("chromium de playwright no encontrado")

    PAGES = (
        ("índice", REPO / "index.html"),
        ("contexto", REPO / "data" / "context-dashboard.html"),
        ("gantt", REPO / "data" / "gantt-multitasking.html"),
    )

    def test_clientwidth_es_viewport_real_y_meta_declarada(self):
        for nombre, page_path in self.PAGES:
            with self.subTest(pagina=nombre):
                if not page_path.exists():
                    self.fail(f"página no generada: {page_path}")
                with sync_playwright() as p:
                    browser = p.chromium.launch(executable_path=self.chrome,
                                                args=["--no-sandbox"])
                    pg = browser.new_page(
                        viewport={"width": 390, "height": 844},
                        is_mobile=True, has_touch=True)
                    pg.goto(f"file://{page_path}")
                    pg.wait_for_timeout(300)
                    meta = pg.evaluate(
                        "document.querySelector('meta[name=viewport]')"
                        "?.content || ''")
                    self.assertIn("width=device-width", meta,
                        f"{nombre}: meta viewport sin width=device-width")
                    self.assertIn("initial-scale=1", meta,
                        f"{nombre}: meta viewport sin initial-scale=1")
                    self.assertNotIn("user-scalable=no", meta,
                        f"{nombre}: meta viewport bloquea zoom")
                    self.assertNotIn("maximum-scale", meta,
                        f"{nombre}: meta viewport bloquea zoom (maximum-scale)")
                    client_width = pg.evaluate(
                        "document.documentElement.clientWidth")
                    self.assertEqual(
                        client_width, 390,
                        f"{nombre}: clientWidth={client_width}, esperado 390 "
                        "(viewport real del teléfono)")
                    browser.close()


class TestDocumentOverflowMatrix(unittest.TestCase):
    """coffe-tmm.3 (EDGE-002): matriz de overflow de documento. Las 6 páginas
    (índice, FPA, Contexto, DDL, Gantt, guía) a 320/390/768/1280px, con
    disclosures abiertos y cerrados: documentElement.scrollWidth <=
    clientWidth (0px overflow de documento). Tablas y Gantt desplazan solo
    dentro de sus contenedores (no se oculta contenido con
    overflow-x:hidden global). FPA se renderiza desde fixture (test_fpa_f6),
    el resto son los HTML versionados en data/."""

    @classmethod
    def setUpClass(cls):
        cls.chrome = find_chromium()
        if not cls.chrome:
            raise unittest.SkipTest("chromium de playwright no encontrado")
        # FPA desde fixture seguro (igual que test_fpa_f6_smoke), no de logs
        import importlib.util

        def _load(name, relpath):
            spec = importlib.util.spec_from_file_location(name, REPO / relpath)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod

        f6 = _load("test_fpa_f6", "tests/test_fpa_f6.py")
        viz = _load("viz_fpa_f6", "scripts/viz-fpa.py")
        config = json.loads((REPO / "config" / "fpa.json").read_text())
        cls.fpa_path = Path("/tmp/fpa_overflow_matrix.html")
        cls.fpa_path.write_text(
            viz.render_html(f6.f6_fixture(), config, generated="T"))

    PAGES = (
        ("índice", REPO / "index.html"),
        ("fpa", Path("/tmp/fpa_overflow_matrix.html")),
        ("contexto", REPO / "data" / "context-dashboard.html"),
        ("ddl", REPO / "data" / "ddl-dashboard.html"),
        ("gantt", REPO / "data" / "gantt-multitasking.html"),
        ("guía", REPO / "data" / "fpa-guide.html"),
    )
    VIEWPORTS = ((320, 844), (390, 844), (768, 1024), (1280, 900))

    def test_documento_sin_overflow_en_matriz_completa(self):
        violations = []
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=self.chrome,
                                        args=["--no-sandbox"])
            for vw, vh in self.VIEWPORTS:
                pg = browser.new_page(viewport={"width": vw, "height": vh})
                for nombre, path in self.PAGES:
                    if not path.exists():
                        violations.append(f"{nombre}: página no generada {path}")
                        continue
                    for disc in ("cerrado", "abierto"):
                        pg.goto(f"file://{path}")
                        pg.wait_for_timeout(200)
                        if disc == "abierto":
                            pg.evaluate(
                                "() => { document.querySelectorAll("
                                "'details:not([open])').forEach(d => "
                                "d.open = true); }")
                        sw = pg.evaluate(
                            "document.documentElement.scrollWidth")
                        cw = pg.evaluate(
                            "document.documentElement.clientWidth")
                        if sw > cw:
                            violations.append(
                                f"{nombre} {vw}px disclosures-{disc}: "
                                f"scrollWidth={sw} > clientWidth={cw}")
                pg.close()
            browser.close()
        self.assertEqual(
            violations, [],
            "overflow de documento (EDGE-002): " + "; ".join(violations))


if __name__ == "__main__":
    unittest.main()
