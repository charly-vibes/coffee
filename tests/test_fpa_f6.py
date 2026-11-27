#!/usr/bin/env python3
"""
test_fpa_f6.py — tests de viz-fpa.py F6 (ticket coffe-lat.7):
arquitectura de información 5 vistas + Data & method (FPA-150/151/152/157/
158), presets de periodo y selector único (FPA-090/153), top-5 + Mostrar
todo (FPA-155) y merges (FPA-156).

(coffe-kav.5: títulos-hallazgo/idioma/CTAs en test_fpa_f6_titles.py;
numeración/export/accesibilidad en test_fpa_f6_export.py y
test_fpa_f6_a11y.py; golden/check-docs en test_fpa_f6_golden.py.)
Smoke Playwright (FPA-109, share restaurado) en test_fpa_f6_smoke.py.
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


def f6_fixture():
    """Fixture F6: f2 + 7 skills y 7 proyectos (para probar top-5 + Mostrar
    todo, FPA-155)."""
    fx = f2.f2_fixture()
    fx["skills"] = {f"skill-{i}": 100 - i * 10 for i in range(7)}
    fx["projects"] = {
        f"charly-proy-{i}": {"cost_effective": 90.0 - i * 10,
                             "first_seen": "2026-05-01",
                             "last_seen": "2026-07-10"}
        for i in range(7)
    }
    return fx


class F6Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = f6_fixture()
        cls.model = viz.build_model(cls.fixture, CONFIG)
        cls.html = viz.render_html(cls.fixture, CONFIG, generated="T")

    def _section(self, sec_id):
        """Contenido <section id="...">…</section> del HTML."""
        m = re.search(rf'<section id="{sec_id}"[^>]*>(.*?)</section>',
                      self.html, re.S)
        self.assertIsNotNone(m, f"falta sección {sec_id}")
        return m.group(1)


# ======================================================================
# Arquitectura de información (FPA-150/151/152/157/158)
# ======================================================================

class TestInfoArchitecture(F6Base):

    def test_cinco_vistas_en_orden(self):
        """FPA-150: Summary, Cost, Breakdown, Habits, Outlook + Data & method,
        en ese orden."""
        ids = ["summary", "cost", "breakdown", "habits", "outlook"]
        pos = [self.html.index(f'<section id="{i}"') for i in ids]
        dm = self.html.index('id="data-method"')
        self.assertEqual(pos + [dm], sorted(pos + [dm]))

    def test_pregunta_encabezando_cada_vista(self):
        """FPA-151: cada vista abre con la pregunta que responde (es)."""
        preguntas = ["¿Cuál es el estado de las cosas?",
                     "¿Qué estoy gastando?",
                     "¿A dónde va el gasto?",
                     "¿Cómo trabajo?",
                     "¿Qué viene después?"]
        for p in preguntas:
            self.assertIn(p, self.html)

    def test_data_method_primario_abierto(self):
        """FPA-157 (política update-fpa-dashboard-ux F5): Data & method es la
        sección primaria de la vista Data → abierta por default (`open`)."""
        m = re.search(r'<details[^>]*id="data-method"[^>]*>', self.html)
        self.assertIsNotNone(m)
        self.assertIn("open", m.group(0))

    def test_timeline_en_data_method(self):
        """FPA-157: el timeline de tools vive dentro de Data & method."""
        dm = self.html.index('id="data-method"')
        tl = self.html.index('id="timeline"')
        self.assertGreater(tl, dm)
        self.assertLess(tl, self.html.index("</main>"))

    def test_pareto_sin_duplicar_top_n(self):
        """FPA-158: solo una vista Pareto de proyectos; ningún chart top-N
        aparte que duplique árbol + Pareto."""
        self.assertEqual(1, self.html.count('id="pareto"'))
        self.assertEqual(1, self.html.count('data-tree="pareto"'))
        self.assertNotIn("Top proyectos", self.html)

    def test_tabs_y_secuencia(self):
        """FPA-152: tablist de vistas + CSS mobile una-vista-a-la-vez,
        desktop secuencia completa."""
        self.assertIn('role="tablist"', self.html)
        self.assertIn("body[data-view", self.html)
        self.assertIn("@media (max-width:599px)", self.html)


# ======================================================================
# Presets de periodo y selector único (FPA-090/153)
# ======================================================================

class TestPeriodo(F6Base):

    def test_presets_ytd_y_trimestres(self):
        """FPA-090: presets YTD y Q1–Q3 en el selector (los que tengan datos)."""
        for key in ("ytd:2026", "q:2026Q2", "q:2026Q3"):
            self.assertIn(f'<option value="{key}"', self.html)

    def test_rango_custom_precalculado(self):
        """FPA-090: rango custom contiguo disponible y con KPIs y árboles."""
        v = self.model["views"]["range:2026-05..2026-06"]
        self.assertEqual(["2026-05", "2026-06"], v["months"])
        self.assertTrue(v["kpis"])
        self.assertTrue(v["trees"]["time"]["children"])
        self.assertIn('value="range:2026-05..2026-06"', self.html)

    def test_rango_custom_prior_misma_longitud(self):
        """El delta del rango custom compara contra el rango inmediato
        anterior de la misma longitud."""
        wins = {k: (w, p) for k, _l, w, p in
                viz._windows(["2026-04", "2026-05", "2026-06", "2026-07"])}
        self.assertEqual(["2026-04", "2026-05"],
                         wins["range:2026-06..2026-07"][1])
        self.assertIsNone(wins["range:2026-05..2026-06"][1])

    def test_un_solo_selector_fijo_arriba(self):
        """FPA-153: un único period selector, fijo al inicio de la página
        (topbar), que aplica a todas las vistas."""
        self.assertEqual(1, self.html.count('id="period-select"'))
        topbar = self.html.index('class="topbar"')
        sel = self.html.index('id="period-select"')
        summary = self.html.index('<section id="summary"')
        self.assertLess(topbar, sel)
        self.assertLess(sel, summary)


# ======================================================================
# Top-5 + Mostrar todo (FPA-155)
# ======================================================================

class TestTop5(F6Base):

    def test_pareto_top5_mostrar_todo(self):
        """FPA-155: 7 proyectos → 5 visibles + filas ocultas + botón
        'Mostrar todo (2)'."""
        p = self._section("breakdown")
        self.assertIn("Mostrar todo (2)", p)
        self.assertIn('class="extra-row" hidden', p)

    def test_skills_top5(self):
        """FPA-155: skills con 7 filas → top-5 + Mostrar todo."""
        h = self._section("habits")
        self.assertIn("Mostrar todo (2)", h)

    def test_tabla_corta_sin_boton(self):
        """Tablas con ≤5 filas no llevan botón."""
        self.assertEqual(0, self._section("outlook").count("show-all"))

    def test_js_muestra_todo(self):
        """El botón revela las filas extra (JS)."""
        self.assertIn("extra-row", self.html.split("<script>")[-1])


# ======================================================================
# Merges (FPA-156)
# ======================================================================

class TestMerges(F6Base):

    def test_skills_y_comandos_una_vista_toggle(self):
        """FPA-156: skills y comandos en una sola vista con toggle."""
        m = re.search(r'<details[^>]*id="skills-commands".*?</details>',
                      self.html, re.S)
        self.assertIsNotNone(m)
        blob = m.group(0)
        self.assertEqual(2, blob.count('class="ptoggle'))
        self.assertIn('id="skills-panel"', blob)
        self.assertIn('id="commands-panel" hidden', blob)
        # ya no hay secciones separadas
        self.assertEqual(0, self.html.count('id="skills"'))
        self.assertEqual(0, self.html.count('id="commands"'))

    def test_sesiones_distribucion_y_largas_juntas(self):
        """FPA-156: buckets de longitud + sesiones más largas en una vista."""
        s = self._section("habits")
        i = s.index('id="sessions"')
        self.assertIn("Distribución de longitud", s[i:])
        self.assertIn("Sesiones más largas", s[i:])

    def test_coste_por_tool_sin_duplicar(self):
        """FPA-156: un solo visual de coste por tool (el árbol)."""
        main = self.html[self.html.index("<main"):self.html.index("</main>")]
        self.assertEqual(1, main.count("Árbol Tool"))


if __name__ == "__main__":
    unittest.main()
