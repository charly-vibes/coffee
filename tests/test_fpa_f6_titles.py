#!/usr/bin/env python3
"""
test_fpa_f6_titles.py — F6: títulos-hallazgo con fallback (FPA-160/161),
idioma única (FPA-162) y CTAs con targets del config y fail si vacío
(FPA-165…168, 172).

Parte de la antigua test_fpa_f6.py (coffe-kav.5); el fixture F6 vive en
tests/test_fpa_f6.py.
"""

import importlib.util
import json
import re
import tempfile
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
f6 = _load("test_fpa_f6", "tests/test_fpa_f6.py")
fpa_config = _load("fpa_config_f6", "scripts/fpa_config.py")
CONFIG = json.loads((REPO / "config" / "fpa.json").read_text())


class F6Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = f6.f6_fixture()
        cls.model = viz.build_model(cls.fixture, CONFIG)
        cls.html = viz.render_html(cls.fixture, CONFIG, generated="T")

    def _section(self, sec_id):
        """Contenido <section id="...">…</section> del HTML."""
        m = re.search(rf'<section id="{sec_id}"[^>]*>(.*?)</section>',
                      self.html, re.S)
        self.assertIsNotNone(m, f"falta sección {sec_id}")
        return m.group(1)


# ======================================================================
# Títulos-hallazgo (FPA-160/161)
# ======================================================================

class TestFindingTitles(F6Base):

    def test_finding_heatmap(self):
        """FPA-160: título del heatmap computado del after-hours share con
        threshold del config (FPA-009), con métrica nombrada visible."""
        h = self._section("habits")
        m = re.search(r'<h2 class="finding">([^<]+)</h2>'
                      r'(?:</summary>)?\s*'
                      r'<p class="small finding-meta">([^<]+)</p>', h)
        self.assertIsNotNone(m)
        self.assertIn("fuera del horario laboral", m.group(1))
        self.assertIn("after_hours_share", m.group(2))
        self.assertIn("threshold", m.group(2))

    def test_finding_pareto(self):
        """FPA-160: concentración top-3 como título-hallazgo con threshold."""
        b = self._section("breakdown")
        m = re.search(r'<h2 class="finding">([^<]+)</h2>'
                      r'(?:</summary>)?\s*'
                      r'<p class="small finding-meta"[^>]*>([^<]+)</p>', b)
        self.assertIsNotNone(m)
        self.assertIn("concentran", m.group(1))
        self.assertIn("top3_share", m.group(2))
        self.assertIn("50%", m.group(2))

    def test_fallback_sin_valor(self):
        """FPA-161: valor no disponible → fallback al label descriptivo."""
        out = viz.finding(None, "after_hours_share", "25% del uso",
                          "El {0} del uso ocurre fuera del horario laboral",
                          "After-hours y weekend", _fmt=lambda v: f"{v:.1%}")
        self.assertIn("After-hours y weekend", out)
        self.assertIn("n/a", out)
        self.assertNotIn("El", out.split("</h2>")[0])

    def test_finding_con_valor(self):
        out = viz.finding(0.389, "after_hours_share", "25% del uso",
                          "El {0} del uso ocurre fuera del horario laboral",
                          "After-hours y weekend", _fmt=lambda v: f"{v:.1%}")
        self.assertIn("38.9%", out)
        self.assertIn("after_hours_share", out)


# ======================================================================
# Idioma única (FPA-162)
# ======================================================================

class TestIdioma(F6Base):

    def test_lang_es(self):
        self.assertIn('<html lang="es">', self.html)

    def test_sin_texto_de_ui_en_ingles(self):
        """FPA-162: ninguna cadena de UI en inglés (mostrar todo, leer la
        serie, compartir vista, exportar)."""
        js = self.html.split("<script>")[-1]
        for en in ("Show all", "Read the series", "Share view",
                   "Export CSV", "Download SVG"):
            self.assertNotIn(f">{en}<", self.html)
            self.assertNotIn(f'"{en}"', js)


# ======================================================================
# CTAs (FPA-165…168, 172)
# ======================================================================

class TestCTAs(F6Base):

    def test_summary_1_primario_mas_secundarios(self):
        """FPA-165: al final del Summary, 1 primario ('Leer la serie') +
        hasta 3 secundarios."""
        s = self._section("summary")
        self.assertEqual(1, s.count("cta-primary"))
        self.assertIn("Leer la serie", s)
        secundarios = s.count('class="cta"')
        self.assertGreaterEqual(secundarios, 1)
        self.assertLessEqual(secundarios, 3)

    def test_max_un_primario_en_todo_el_dashboard(self):
        """FPA-166/149: ningún view tiene más de un primario (solo Summary
        tiene CTAs de vista)."""
        main = self.html[self.html.index("<main"):self.html.index("</main>")]
        self.assertEqual(1, main.count("cta-primary"))

    def test_labels_verb_first_4_palabras(self):
        """FPA-172: labels de CTA con ≤4 palabras."""
        ctas = CONFIG["ctas"]
        labels = [ctas["primary"]["label"]]
        labels += [c["label"] for c in ctas["secondary"]]
        labels += [a["label"] for a in ctas.get("alert_actions", {}).values()]
        for lb in labels:
            self.assertLessEqual(len(lb.split()), 4, lb)

    def test_alertas_con_una_accion(self):
        """FPA-167: cada alerta lleva exactamente un botón de acción con
        target del config."""
        alerts = self.html[self.html.index('id="alerts"'):
                           self.html.index('id="plan-economy"')]
        n_alerts = alerts.count('<li class="alert')
        self.assertGreaterEqual(n_alerts, 1)
        self.assertEqual(n_alerts, alerts.count("alert-action"))

    def test_build_alerts_adjunta_accion(self):
        fx = f2.f2_fixture()
        alerts = viz.build_alerts(fx, CONFIG, today="2026-09-20")
        for a in alerts:
            self.assertIn("action", a, a["rule"])
            self.assertTrue(a["action"]["target"], a["rule"])

    def test_fpa168_target_vacio_falla(self):
        """FPA-168: target vacío → validate_config lo lista y el generador
        termina non-zero nombrando el CTA."""
        bad = json.loads(json.dumps(CONFIG))
        bad["ctas"]["secondary"][0]["target"] = ""
        errors = fpa_config.validate_config(bad)
        self.assertTrue(any("target vacío" in e for e in errors))

        viz_fpa168_target_vacio_falla(self)

    def test_fpa168_alert_action_vacio_falla(self):
        bad = json.loads(json.dumps(CONFIG))
        bad["ctas"]["alert_actions"]["budget"]["target"] = ""
        errors = fpa_config.validate_config(bad)
        self.assertTrue(any("budget" in e for e in errors), errors)


def viz_fpa168_target_vacio_falla(tc):
    """main() con config inválida → exit 1 nombrando el CTA."""
    import contextlib, io
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        report = tmp / "report.json"
        report.write_text(json.dumps(f2.f2_fixture(), ensure_ascii=False))
        bad_cfg = tmp / "bad.json"
        cfg = json.loads(json.dumps(CONFIG))
        cfg["ctas"]["secondary"][0]["target"] = ""
        bad_cfg.write_text(json.dumps(cfg))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = viz.main(["--report", str(report), "--config", str(bad_cfg),
                           "--out", str(tmp / "out.html")])
        tc.assertEqual(1, rc)
        tc.assertIn("target vacío", err.getvalue())
        tc.assertIn("Explorar el Gantt", err.getvalue())


if __name__ == "__main__":
    unittest.main()
