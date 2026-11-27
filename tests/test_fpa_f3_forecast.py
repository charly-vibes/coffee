#!/usr/bin/env python3
"""
test_fpa_f3_forecast.py — FPA-070…077: forecast con run-rate y escenarios,
y render HTML del F3.

Parte de la antigua test_fpa_f3.py (coffe-kav.5); el fixture y el config
viven en tests/test_fpa_f2.py.
"""

import importlib.util
import json
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, REPO / relpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


f2 = _load("test_fpa_f2", "tests/test_fpa_f2.py")
viz = f2.viz
CONFIG = f2.CONFIG


class _ForecastBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = f2.f2_fixture()
        cls.model = viz.build_model(cls.fixture, CONFIG)


class TestForecast(_ForecastBase):
    """FPA-070…077: run-rate, escenarios, outlook, mes parcial separado."""

    def test_runrate_media_fme_ultimos_3(self):
        """FPA-070: base = media FME de los últimos 3 meses con datos.
        FME eff: 50, 65, 30×31/10=93 → 69.3333; Q: 100,100,310 → 170."""
        fc = self.model["forecast"]
        self.assertFalse(fc.get("n_a_reason"))
        self.assertAlmostEqual((50.0 + 65.0 + 93.0) / 3, fc["base"]["eff_base"], places=6)
        self.assertAlmostEqual(170.0, fc["base"]["q_base"], places=6)
        self.assertAlmostEqual(fc["base"]["eff_base"] / 170.0,
                               fc["base"]["rate_base"], places=6)
        self.assertEqual(["2026-05", "2026-06", "2026-07"], fc["base"]["months_used"])

    def test_na_con_menos_de_3_meses(self):
        """FPA-070/008: <3 meses → n/a con razón, nunca forecast inventado."""
        fixture = f2.f2_fixture()
        del fixture["monthly"]["2026-05"]
        fc = viz.build_model(fixture, CONFIG)["forecast"]
        self.assertTrue(fc.get("n_a_reason"))
        self.assertIn("3", fc["n_a_reason"])
        self.assertEqual([], fc["rows"])

    def test_formula_efectivo(self):
        """FPA-072: eff_n = base × (1+g)^n × (1+r)."""
        fc = self.model["forecast"]
        out = viz.apply_scenario(fc, 0.10, 0.05, fc["default_plan"])
        base = fc["base"]["eff_base"]
        self.assertAlmostEqual(round(base * 1.1 * 1.05, 2), out["rows"][0]["eff"],
                               places=6)
        self.assertAlmostEqual(round(base * 1.1 ** 2 * 1.05, 2), out["rows"][1]["eff"],
                               places=6)

    def test_formula_cash(self):
        """FPA-073: cash_n = cuota del plan + p2p escalado por volumen."""
        fc = json.loads(json.dumps(self.model["forecast"]))
        fc["base"]["p2p_base"] = 5.0
        out = viz.apply_scenario(fc, 0.10, 0.0, fc["default_plan"])
        fee = next(p["fee"] for p in fc["plans"] if p["key"] == fc["default_plan"])
        self.assertAlmostEqual(fee + 5.0 * 1.1, out["rows"][0]["cash"], places=6)
        self.assertAlmostEqual(fee + 5.0 * 1.1 ** 2, out["rows"][1]["cash"], places=6)
        # el rate change no afecta el cash de suscripción
        out2 = viz.apply_scenario(fc, 0.10, 0.20, fc["default_plan"])
        self.assertAlmostEqual(out["rows"][0]["cash"], out2["rows"][0]["cash"], places=6)

    def test_escenario_default_precalculado(self):
        """F3: filas del escenario default (g=0, r=0) ya vienen en el modelo."""
        fc = self.model["forecast"]
        self.assertAlmostEqual(round(fc["base"]["eff_base"], 2),
                               fc["rows"][0]["eff"], places=6)
        self.assertEqual(len(fc["future_months"]), len(fc["rows"]))
        self.assertEqual({"2026-08", "2026-09", "2026-10", "2026-11", "2026-12"},
                         {r["ym"] for r in fc["rows"]})

    def test_mes_parcial_fila_separada(self):
        """FPA-075: el resto del mes parcial es una fila forecast separada."""
        fc = self.model["forecast"]
        rem = fc["remainder"]
        self.assertEqual("2026-07", rem["ym"])
        # FME 93 − real 30 = 63 de efectivo por devengar
        self.assertAlmostEqual(93.0 - 30.0, rem["eff"], places=6)
        self.assertNotIn(rem["ym"], {r["ym"] for r in fc["rows"]})

    def test_ytd_outlook_vs_budget(self):
        """FPA-074: YTD real + outlook vs presupuesto, con varianza."""
        fc = self.model["forecast"]
        out = fc["outlook"]
        # budget = pro-rata meses del reporte + futuros (ago-dic = 5 meses)
        budget_cash = 100 + 100 + 100 * 10 / 31 + 5 * 100
        self.assertAlmostEqual(budget_cash, out["budget_cash"], places=2)
        outlook_cash = fc["remainder"]["cash"] + sum(r["cash"] for r in fc["rows"])
        ytd_cash = 45.0
        self.assertAlmostEqual(ytd_cash + outlook_cash, out["projected_cash"], places=2)
        self.assertAlmostEqual(out["projected_cash"] - out["budget_cash"],
                               out["variance_cash"], places=2)
        # eff: actual 145 (50+65+30) + resto de julio (63) + meses futuros
        self.assertAlmostEqual(145.0 + fc["remainder"]["eff"]
                               + sum(r["eff"] for r in fc["rows"]),
                               out["projected_eff"], places=2)
        self.assertIn("marker_cash", out)

    def test_outlook_na_sin_runrate(self):
        """FPA-074/008: sin run-rate → outlook n/a con razón."""
        fixture = f2.f2_fixture()
        del fixture["monthly"]["2026-05"]
        fc = viz.build_model(fixture, CONFIG)["forecast"]
        self.assertTrue(fc.get("n_a_reason"))
        self.assertFalse(fc["outlook"])

    def test_actual_vs_forecast_no_solo_color(self):
        """FPA-076: distinción con símbolo + texto, no solo color."""
        fc = self.model["forecast"]
        self.assertEqual("△", fc["remainder"]["marker"]["symbol"])
        self.assertEqual("forecast", fc["remainder"]["marker"]["text"])
        for row in fc["rows"]:
            self.assertEqual("△", row["marker"]["symbol"])

    def test_provenance_assumed(self):
        """FPA-003: forecast y presupuestos derivan de config → assumed."""
        self.assertEqual("assumed", self.model["forecast"]["provenance"])
        self.assertEqual("assumed", self.model["budget"]["provenance"])

    def test_planes_del_config(self):
        """FPA-071: planes futuros desde las suscripciones del config.

        coffe-wo1: el primary es la tool con la suscripción que termina más
        tarde — con el trial de codex (fin 2026-11-06) es codex, no claude."""
        fc = self.model["forecast"]
        keys = {p["key"] for p in fc["plans"]}
        self.assertIn(fc["default_plan"], keys)
        labels = " ".join(p["label"] for p in fc["plans"])
        self.assertIn("ChatGPT Plus", labels)
        self.assertTrue(any("trial" in p["label"].lower() for p in fc["plans"]))
        for p in fc["plans"]:
            self.assertIsInstance(p["fee"], (int, float))

    def test_plan_default_sin_suscripcion_activa(self):
        """FPA-071 + FPA-082/reales: el calendario corregido a facturas no
        tiene ninguna suscripción activa al cierre del fixture (jul-10:
        claude cancelado, codex Free) → plan default = "sin suscripción"
        (fee $0; cash forecast = p2p escalado) y los planes del calendario
        quedan como escenarios hipotéticos."""
        fc = self.model["forecast"]
        default = next(p for p in fc["plans"] if p["key"] == fc["default_plan"])
        self.assertEqual(0.0, default["fee"])
        self.assertIn("sin suscripción", default["label"].lower())
        # los planes reales del calendario siguen listables como escenarios
        self.assertTrue(any(p["fee"] > 0 for p in fc["plans"]))

    def test_apply_scenario_cambia_filas(self):
        """FPA-077: el escenario feedea el recompute sin reload."""
        fc = self.model["forecast"]
        a = viz.apply_scenario(fc, 0.0, 0.0, fc["default_plan"])
        b = viz.apply_scenario(fc, 0.5, 0.0, fc["default_plan"])
        self.assertNotAlmostEqual(a["rows"][0]["eff"], b["rows"][0]["eff"])


class TestRenderF3(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = f2.f2_fixture()
        cls.model = viz.build_model(cls.fixture, CONFIG)
        cls.html = viz.render_html(cls.fixture, CONFIG, generated="2026-09-20 12:00")

    def test_seccion_presupuesto(self):
        self.assertIn('id="budget"', self.html)
        self.assertIn("YTD", self.html)
        self.assertIn('id="budget-cash"', self.html)  # FPA-056: input editable
        self.assertIn('id="budget-eff"', self.html)
        self.assertIn('id="budget-target"', self.html)

    def test_markers_en_tabla(self):
        """FPA-054: símbolo y texto del marker en el HTML."""
        self.assertIn("▼", self.html)
        self.assertIn("bajo", self.html)

    def test_seccion_bridge(self):
        self.assertIn('id="bridge"', self.html)
        self.assertEqual(2, self.html.count('class="wf chart"'))  # un waterfall por par
        self.assertIn("Mix", self.html)
        self.assertIn('id="mix-stack"', self.html)

    def test_eje_truncado_label(self):
        """FPA-067: par truncado lleva la etiqueta visible."""
        model = viz.build_model(self.fixture, CONFIG)
        self.assertIn("axis_label", model["bridge"]["pairs"]["2026-06"])

    def test_seccion_forecast(self):
        self.assertIn('id="forecast"', self.html)
        self.assertIn('id="fc-growth"', self.html)   # FPA-071: inputs escenario
        self.assertIn('id="fc-rate"', self.html)
        self.assertIn('id="fc-plan"', self.html)
        self.assertIn("△", self.html)                 # FPA-076: marker forecast
        self.assertIn("forecast", self.html)

    def test_forecast_na_render(self):
        """FPA-008: sin run-rate la sección muestra la razón, no cifras."""
        fixture = f2.f2_fixture()
        del fixture["monthly"]["2026-05"]
        html = viz.render_html(fixture, CONFIG, generated="2026-09-20 12:00")
        self.assertIn("se requieren 3 meses", html)

    def test_soft_budget_efectivo(self):
        """design.md OQ-1: el presupuesto efectivo se muestra como informativo."""
        self.assertIn("informativo", self.html)

    def test_js_sin_identificadores_prohibidos(self):
        """design.md: las fórmulas JS no exponen claves del modelo crudo."""
        js = self.html.split("<script>")[-1]
        for forbidden in ("cost_effective", "cache_read", "budgets", "fme("):
            self.assertNotIn(forbidden, js)

    def test_determinismo_salvo_timestamp(self):
        a = viz.render_html(self.fixture, CONFIG, generated="T")
        b = viz.render_html(self.fixture, CONFIG, generated="T")
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
