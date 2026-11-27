#!/usr/bin/env python3
"""
test_viz_matriz.py — viewport y overflow de documento de las páginas
publicadas (parte del ex test_viz.py):

- coffe-tmm.2: índice, Contexto y Gantt usan el viewport real del teléfono
  (meta viewport declarado y clientWidth=390 en Chromium móvil).
- coffe-tmm.3 (EDGE-002): matriz de overflow de documento a 320/390/768/
  1280px con disclosures abiertos y cerrados: 0px de overflow.
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


def _load_module(name, relpath):
    """Carga un módulo del repo por ruta (fixture de fpa / generador)."""
    spec = importlib.util.spec_from_file_location(name, REPO / relpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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
        f6 = _load_module("test_fpa_f6", "tests/test_fpa_f6.py")
        viz = _load_module("viz_fpa_f6", "scripts/viz-fpa.py")
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

    def _overflow_documento(self, pg, nombre, path):
        """(disc, sw, cw) con sw > cw para la página, con el disclosure
        cerrado y con todos los details abiertos. [ ] si no hay overflow."""
        if not path.exists():
            return None
        hallazgos = []
        for disc in ("cerrado", "abierto"):
            pg.goto(f"file://{path}")
            pg.wait_for_timeout(200)
            if disc == "abierto":
                pg.evaluate(
                    "() => { document.querySelectorAll("
                    "'details:not([open])').forEach(d => d.open = true); }")
            sw = pg.evaluate("document.documentElement.scrollWidth")
            cw = pg.evaluate("document.documentElement.clientWidth")
            if sw > cw:
                hallazgos.append((disc, sw, cw))
        return hallazgos

    def _violations_viewport(self, pg, vw):
        """Violaciones de overflow (mensajes finales) de las 6 páginas a
        un viewport, disclosure cerrado y abierto."""
        viol = []
        for nombre, path in self.PAGES:
            if not path.exists():
                viol.append(f"{nombre}: página no generada {path}")
                continue
            for disc, sw, cw in self._overflow_documento(pg, nombre, path):
                viol.append(
                    f"{nombre} {vw}px disclosures-{disc}: "
                    f"scrollWidth={sw} > clientWidth={cw}")
        return viol

    def test_documento_sin_overflow_en_matriz_completa(self):
        violations = []
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=self.chrome,
                                        args=["--no-sandbox"])
            for vw, vh in self.VIEWPORTS:
                pg = browser.new_page(viewport={"width": vw, "height": vh})
                violations += self._violations_viewport(pg, vw)
                pg.close()
            browser.close()
        self.assertEqual(
            violations, [],
            "overflow de documento (EDGE-002): " + "; ".join(violations))

if __name__ == "__main__":
    unittest.main()
