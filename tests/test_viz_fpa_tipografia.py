#!/usr/bin/env python3
"""
test_viz_fpa_tipografia.py — contractos estáticos del fpa-dashboard
(parte del ex test_viz.py, sin navegador):

- coffe-6lz F4: jerarquía tipográfica — los ids FPA-xxx dejan de competir
  con las cifras primarias (cero matches en el texto visible) y siguen
  machine-readable en atributos title.
- coffe-x6l F5 (D5): disclosure por defecto y summaries estilizados.
"""

import html as html_mod
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def find_chromium():
    """Busca un chromium instalado por playwright (varias versiones)."""
    import glob
    for pat in ("~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome",
                "~/.cache/ms-playwright/chromium_headless_shell-*/chrome-linux*/headless_shell"):
        found = sorted(glob.glob(str(Path(pat).expanduser())))
        if found:
            return found[-1]
    return None


class _StackParser(HTMLParser):
    """HTMLParser con stack de (tag, skip_subtree): los tags de SKIP_TAGS
    y lo que diga _skip_extra() se ignoran con todo su subárbol (los ids
    viajan a title, <script>/<style> son CDATA)."""

    SKIP_TAGS = frozenset()

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.parts = []     # _VisibleText: texto visible recolectado
        self.details = {}   # _DisclosureMap: data-tree -> bool open

    def _skip_extra(self, tag, attrs):
        """Gancho de subclases: subárbol adicional a ignorar."""
        return False

    def handle_starttag(self, tag, attrs):
        skip = (tag in self.SKIP_TAGS or self._skip_extra(tag, attrs)
                or any(s for _, s in self.stack))
        self.stack.append((tag, skip))

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                return


class _VisibleText(_StackParser):
    """Recolecta el texto visible: ignora atributos (los ids viajan a
    title) y los chips de marginalia (que sí llevan ids por diseño —
    coffe-6lz anti-goal)."""

    SKIP_TAGS = {"title", "script", "style"}

    def _skip_extra(self, tag, attrs):
        return "marginalia" in dict(attrs).get("class", "").split()

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


class _DisclosureMap(_StackParser):
    """Recolecta los <details data-tree> de sección y su estado open por
    defecto (coffe-x6l F5). Ignora <script>/<style> (el JS de viz-fpa.py
    re-crea trees en re-render) y los details anidados de filas (tlabel,
    sin data-tree por diseño)."""

    SKIP_TAGS = {"script", "style"}

    def handle_starttag(self, tag, attrs):
        super().handle_starttag(tag, attrs)
        if tag == "details" and not any(s for _, s in self.stack):
            d = dict(attrs)
            if "data-tree" in d:
                self.details[d["data-tree"]] = "open" in d


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
    # (coffe-dnx.6: la vista Modelos abre su scorecard)
    OPEN = {"budget", "portfolio", "heatmap", "forecast", "data-method",
            "models-scorecard"}
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
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
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


if __name__ == "__main__":
    unittest.main()
