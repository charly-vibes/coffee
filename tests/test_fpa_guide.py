#!/usr/bin/env python3
"""
test_fpa_guide.py — ingesta de la guía de análisis (coffe-gen.3, change
update-fpa-site-integration: guía a 4 niveles en el sitio)

Cubre el alcance del ticket coffe-gen.3:
- parser stdlib del markdown español (19 análisis × 4 niveles, IDs FPA-xxx
  extraídos del heading)
- render de data/fpa-guide.html autocontenida en el tema compartido, con
  site_name del config, sin 'FP&A' visible, anchors por análisis y sin
  cifras del snapshot 10-jun
- mapping análisis→superficie declarado: todo análisis en ≥1 superficie y
  cada análisis del mapeo existente en la guía
- --check-docs extendido: mapeo con análisis inexistente o umbral citado
  distinto del real en config/código → error (exit non-zero lo maneja el
  caller de viz-fpa.py)
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


guide = _load("viz_fpa_guide", "scripts/viz_fpa_guide.py")
viz = _load("viz_fpa", "scripts/viz-fpa.py")
theme = _load("site_theme", "scripts/site_theme.py")

REPORT = json.loads((REPO / "data" / "usage_report_v3.json").read_text())
CONFIG = json.loads((REPO / "config" / "fpa.json").read_text())
GUIDE_MD = (REPO / "docs" / "fpa-analyses-guide.md").read_text()

NIVELS = ("1 · ELI5", "2 · Cotidiano", "3 · Practicante", "4 · Experto")


class TestParser(unittest.TestCase):
    """El markdown español se parsea completo, sin contentido perdido."""

    def setUp(self):
        self.guide = guide.load_guide(GUIDE_MD)

    def test_21_entradas_20_numeros(self):
        """21 números de análisis (coffe-8kx suma el 20, coffe-6io el 21);
        el 10 tiene variantes a/b (22 entradas)."""
        flat = [a for s in self.guide["sections"] for a in s["analyses"]]
        self.assertEqual(22, len(flat))
        nums = {a["num"] for a in flat}
        self.assertEqual({str(i) for i in range(1, 10)} | {"10a", "10b"}
                         | {str(i) for i in range(11, 22)}, nums)

    def test_seccion_f_efectividad_existe(self):
        """coffe-6io: la sección F (efectividad ddl) se parsea como sección
        propia, no absorbida por D/E (regex de secciones A–F)."""
        keys = [s["key"] for s in self.guide["sections"]]
        self.assertIn("E", keys)
        self.assertIn("F", keys)
        f = next(s for s in self.guide["sections"] if s["key"] == "F")
        self.assertEqual(["21"], [a["num"] for a in f["analyses"]])

    def test_analisis_21_declara_disclosure(self):
        """Spec 'guía a 4 niveles repite el disclaimer': asociativo no causal
        y confusor de atención del mantenedor en el texto."""
        flat = [a for s in self.guide["sections"] for a in s["analyses"]]
        a21 = next(a for a in flat if a["num"] == "21")
        text = " ".join(a21["levels"].values()).lower()
        self.assertIn("asociativo", text)
        self.assertIn("mantenedor", text)
        self.assertIn("sin score", text)

    def test_cuatro_niveles_por_analisis(self):
        for s in self.guide["sections"]:
            for a in s["analyses"]:
                self.assertEqual(4, len(a["levels"]),
                                 f"análisis {a['num']} sin 4 niveles")

    def test_ids_fpa_extraidos(self):
        flat = [a for s in self.guide["sections"] for a in s["analyses"]]
        by_num = {a["num"]: a for a in flat}
        self.assertEqual("FPA-002", by_num["1"]["ids"][0])
        self.assertEqual("FPA-116", by_num["10a"]["ids"][0])
        self.assertIn("FPA-080", by_num["13"]["ids"])
        self.assertIn("FPA-180", by_num["20"]["ids"])  # coffe-8kx

    def test_titulos_y_secciones(self):
        self.assertEqual("A", self.guide["sections"][0]["key"])
        titles = [a["title"] for s in self.guide["sections"]
                  for a in s["analyses"]]
        self.assertIn("Costo efectivo, costo real y apalancamiento", titles)


class TestMapping(unittest.TestCase):
    """Mapeo análisis→superficie: cobertura total y referencias válidas."""

    def test_todos_los_analisis_en_al_menos_una_superficie(self):
        flat = {a["num"] for s in guide.load_guide(GUIDE_MD)["sections"]
                for a in s["analyses"]}
        mapped = {num for num, _ in guide.ANALYSIS_SURFACES}
        self.assertEqual(flat, mapped)

    def test_superficies_validas(self):
        valid = {"summary", "cost", "breakdown", "habits", "outlook",
                 "energy", "data", "gantt", "ddl"}
        for _, surfaces in guide.ANALYSIS_SURFACES:
            self.assertTrue(set(surfaces) <= valid)

    def test_efectividad_en_vista_ddl(self):
        """coffe-6io: el análisis 21 surfacea al dashboard ddl, no al FPA."""
        surfaces = dict(guide.ANALYSIS_SURFACES)
        self.assertEqual(["ddl"], surfaces["21"])

    def test_energia_en_su_vista(self):
        """coffe-8kx: la vista Energía tiene su propio análisis (20)."""
        surfaces = dict(guide.ANALYSIS_SURFACES)
        self.assertEqual(["energy"], surfaces["20"])

    def test_multitasking_y_ritmo_tambien_en_gantt(self):
        surfaces = dict(guide.ANALYSIS_SURFACES)
        self.assertIn("gantt", surfaces["8"])
        self.assertIn("gantt", surfaces["9"])


class TestMarginalia(unittest.TestCase):
    """Marginalia progressive disclosure (coffe-gen.4): chip 44px con
    tooltip ELI5, expansión in situ (nivel 2 + nivel 3 colapsado + enlace),
    cobertura total por superficie, FPA + Gantt."""

    def setUp(self):
        self.guide = guide.load_guide(GUIDE_MD)

    def test_chip_estructura(self):
        html = guide.marginalia_html(self.guide, CONFIG, "summary")
        self.assertIn('<details class="mchip"', html)
        self.assertIn('title="Imaginá un buffet libre', html)
        self.assertIn('¿Qué es esto?', html)

    def test_expansion_in_situ(self):
        html = guide.marginalia_html(self.guide, CONFIG, "summary")
        self.assertIn("Imaginá un buffet libre", html)  # nivel 1 en tooltip
        self.assertIn("Algunas herramientas cobran", html)  # nivel 2 visible
        self.assertIn("Efectivo = Σ tokens", html)  # nivel 3 en nested
        self.assertIn('<details class="mchip-deep"', html)
        self.assertIn('href="fpa-guide.html#analisis-1"', html)

    def test_cobertura_por_superficie(self):
        """Todo análisis aparece en ≥1 superficie; las strips cubren todas."""
        surfaces = {s for _, ss in guide.ANALYSIS_SURFACES for s in ss}
        for surface in surfaces:
            html = guide.marginalia_html(self.guide, CONFIG, surface)
            expected = {num for num, ss in guide.ANALYSIS_SURFACES
                        if surface in ss}
            for num in expected:
                self.assertIn(f"#analisis-{num}", html,
                              f"análisis {num} ausente en {surface}")

    def test_render_dashboard_incluye_strips(self):
        html = viz.render_html(REPORT, CONFIG, generated="2026-09-19 12:00")
        for surface in ("summary", "cost", "breakdown", "habits", "outlook",
                        "energy", "data"):
            self.assertIn('<div class="marginalia"', html)
            self.assertTrue(html.count(f'class="marginalia" id="m-{surface}"')
                            == 1, f"strip de {surface} ausente")

    def test_render_energy_strip_linkea_analisis_20(self):
        """coffe-8kx: la vista Energía anuncia su análisis con chip."""
        html = viz.render_html(REPORT, CONFIG, generated="2026-09-19 12:00")
        self.assertIn('id="m-energy"', html)
        self.assertIn('href="fpa-guide.html#analisis-20"', html)

    def test_render_gantt_incluye_strips(self):
        gantt = _load("viz_gantt", "scripts/viz-gantt.py")
        html = gantt.marginalia_gantt()
        self.assertIn('#analisis-8', html)
        self.assertIn('#analisis-9', html)
        self.assertIn('marginalia', html)

    def test_smoke_playwright_chip_expansion(self):
        """Interacción a 390×844: el chip expande in situ sin pageerrors.
        Skip si playwright/chromium no está (igual que test_viz.py)."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest("playwright no disponible")
        import glob
        chrome = None
        for pat in ("~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome",
                    "~/.cache/ms-playwright/chromium_headless_shell-*/"
                    "chrome-linux*/headless_shell"):
            found = sorted(glob.glob(str(Path(pat).expanduser())))
            if found:
                chrome = found[-1]
                break
        if not chrome:
            self.skipTest("chromium de playwright no encontrado")
        html_path = REPO / "data" / "fpa-dashboard.html"
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=chrome,
                                        args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 390, "height": 844})
            errors = []
            page.on("pageerror", lambda e: errors.append(e))
            page.goto(html_path.resolve().as_uri())
            chip = page.locator("#m-summary details.mchip summary").first
            chip.scroll_into_view_if_needed()
            self.assertTrue(chip.is_visible())
            box = chip.bounding_box()
            self.assertGreaterEqual(box["height"], 40)  # target táctil
            chip.click()
            expanded = page.locator("#m-summary details.mchip[open]").first
            self.assertTrue(expanded.is_visible())
            self.assertEqual([], errors)
            browser.close()


class TestFiguresDerivadas(unittest.TestCase):
    """coffe-udt/0zp: las cifras de la guía se derivan del reporte vigente
    (tokens {{fig:...}} resueltos en generación), no hardcodeadas."""

    STALE = ("3,814.72", "1,086.73", "26.89", "7.66", "(346 de 1,884",
             "54.0%", "$1,110.03", "7 umbrales")

    def test_markdown_sin_cifras_del_snapshot_viejo(self):
        for s in self.STALE:
            self.assertNotIn(s, GUIDE_MD, s)

    def test_resolucion_cubre_todos_los_tokens(self):
        resolved = guide.resolve_figures(GUIDE_MD, REPORT, CONFIG)
        self.assertNotIn("{{fig:", resolved)

    def test_figura_desconocida_falla_loud(self):
        with self.assertRaises(SystemExit):
            guide.resolve_figures("{{fig:no_existe}}", REPORT, CONFIG)

    def test_figuras_del_reporte(self):
        figs = guide.derive_figures(REPORT, CONFIG)
        md = REPORT["metadata"]
        self.assertEqual(f"${md['cost_total_effective']:,.2f}",
                         figs["efectivo_total"])
        self.assertEqual(f"{md['total_interactions']:,}",
                         figs["interacciones_total"])
        self.assertEqual(str(REPORT["sessions"]["total_sessions"]),
                         figs["sesiones_total"].replace(",", ""))

    def test_render_y_chips_sin_tokens(self):
        gd = guide.load_guide(guide.resolve_figures(GUIDE_MD, REPORT, CONFIG))
        self.assertNotIn("{{fig:", guide.render_guide_html(gd, CONFIG))
        for s in ("summary", "cost", "breakdown", "habits", "outlook",
                  "energy", "data"):
            self.assertNotIn("{{fig:", guide.marginalia_html(gd, CONFIG, s))

    def test_figuras_de_energia_derivadas_del_reporte(self):
        """coffe-8kx: los tokens fig:* de energía salen del reporte vigente,
        recomputando el total del periodo como lo hace la vista F8."""
        figs = guide.derive_figures(REPORT, CONFIG)
        tot = lo = hi = 0.0
        for mo in REPORT["monthly"].values():
            if "energy_kwh" not in mo:
                continue
            tot += mo.get("energy_kwh") or 0.0
            band = mo.get("energy_kwh_band") or {}
            lo += band.get("low", 0.0)
            hi += band.get("high", 0.0)
        self.assertEqual(f"{tot:.1f}", figs["energia_total"])
        self.assertEqual(f"{lo:.1f}", figs["energia_low"])
        self.assertEqual(f"{hi:.1f}", figs["energia_high"])
        self.assertEqual(
            str(REPORT["metadata"]["energy_cache_read_factor"]),
            figs["cache_factor"])

    def test_header_cuenta_analisis_reales(self):
        gd = guide.load_guide(GUIDE_MD)
        n = len(guide.analyses_flat(gd))
        self.assertIn(f"{n} análisis × 4 niveles",
                      guide.render_guide_html(gd, CONFIG))

    def test_readme_cita_la_cantidad_de_analisis_reales(self):
        """coffe-4j9: la fila de fpa-guide.html en el README cita la misma
        cantidad derivada que el header del HTML — nunca hardcodeada."""
        gd = guide.load_guide(GUIDE_MD)
        n = len(guide.analyses_flat(gd))
        readme = (REPO / "README.md").read_text()
        row = [l for l in readme.splitlines() if "fpa-guide.html" in l
               and "Guía de análisis" in l]
        self.assertEqual(1, len(row), "fila de fpa-guide.html no encontrada")
        self.assertIn(f"Los {n} análisis", row[0])


class TestCheckDocumentation(unittest.TestCase):
    """check_documentation(): mapeo roto o umbral divergente → errores."""

    def test_guia_ok_sin_errores(self):
        self.assertEqual([], guide.check_documentation(CONFIG, GUIDE_MD))

    def test_umbral_citado_distinto_del_config(self):
        """Valor del config que no está citado en la guía → error."""
        cfg = dict(CONFIG)
        cfg["alert_thresholds"] = dict(CONFIG["alert_thresholds"],
                                       plan_usage_multiple=99)
        errors = guide.check_documentation(cfg, GUIDE_MD)
        self.assertTrue(any("99" in e and "plan_usage_multiple" in e
                            for e in errors), errors)

    def test_lifecycle_citado(self):
        cfg = dict(CONFIG)
        cfg["lifecycle"] = dict(CONFIG["lifecycle"], dormant_days=97)
        errors = guide.check_documentation(cfg, GUIDE_MD)
        self.assertTrue(any("97" in e for e in errors), errors)

    def test_mapping_vs_guia(self):
        """Un análisis del mapeo que no exista en la guía → error."""
        errors = guide.check_documentation(CONFIG, GUIDE_MD,
                                           mapping_override=[("99", ["cost"])])
        self.assertTrue(any("99" in e for e in errors), errors)


class TestRenderGuideHtml(unittest.TestCase):
    """data/fpa-guide.html: autocontenida, en el tema, sin FP&A."""

    def setUp(self):
        self.html = guide.render_guide_html(
            guide.load_guide(GUIDE_MD), CONFIG, generated="2026-09-20 12:00")

    def test_anchors_por_analisis(self):
        self.assertIn('id="analisis-1"', self.html)
        self.assertIn('id="analisis-10a"', self.html)
        self.assertIn('id="analisis-19"', self.html)
        self.assertIn('id="analisis-20"', self.html)  # coffe-8kx

    def test_en_tema_compartido(self):
        self.assertEqual(dict(theme.TOKENS), _root := dict(re.findall(
            r"(--[a-z0-9-]+)\s*:\s*([^;]+)",
            re.search(r":root\s*\{(.*?)\}", self.html, flags=re.S).group(1))))

    def test_site_name_y_sin_fpa(self):
        self.assertIn(CONFIG["site_name"], self.html)
        body = re.sub(r"<(style|script)[^>]*>.*?</\1>", "", self.html,
                      flags=re.S)
        self.assertNotIn("FP&A", re.sub(r"<[^>]+>", " ", body))

    def test_contenido_niveles_presente(self):
        self.assertIn("ELI5", self.html)
        self.assertIn("Practicante", self.html)
        self.assertIn("Experto", self.html)

    def test_sin_cifras_del_snapshot(self):
        """El snapshot 10-jun no aparece como verdad (cifras reemplazadas)."""
        self.assertNotIn("$65.58", self.html)
        self.assertNotIn("$3,160.94", self.html)

    def test_determinista(self):
        again = guide.render_guide_html(
            guide.load_guide(GUIDE_MD), CONFIG, generated="2026-09-20 12:00")
        self.assertEqual(self.html, again)

if __name__ == "__main__":
    unittest.main()
