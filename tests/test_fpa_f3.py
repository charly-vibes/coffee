#!/usr/bin/env python3
"""
test_fpa_f3.py — tests de viz-fpa.py F3 (ticket coffe-lat.4)

Alcance: presupuestos con pro-rating y varianza (FPA-050…056), bridge
precio-volumen-mix con identidad (FPA-060…068, 101), forecast con
run-rate y escenarios (FPA-070…077, 102), y los goldens de la fase.
Reutiliza el fixture de meses parciales de test_fpa_f2 (FPA-102).
Golden: REGEN_GOLDEN=1.
"""

import importlib.util
import json
import os
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GOLDEN = Path(__file__).resolve().parent / "golden" / "fpa-f3-snapshot.json"
REGEN_GOLDEN = os.environ.get("REGEN_GOLDEN") == "1"


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, REPO / relpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


viz = _load("viz_fpa_f3", "scripts/viz-fpa.py")
f2 = _load("test_fpa_f2", "tests/test_fpa_f2.py")
CONFIG = json.loads((REPO / "config" / "fpa.json").read_text())


def _cfg(**budget_over):
    cfg = json.loads(json.dumps(CONFIG))
    cfg["budgets"].update(budget_over)
    return cfg


def _mini_month(interactions, cost, models=None):
    """Mes mínimo para tests de bridge directo."""
    return {
        "interactions": interactions, "cost_effective": cost, "cost_real": 0.0,
        "models": models or {}, "tools": {}, "subscription_fees": 0.0,
        "ym": "2026-06",
    }


def _mini_meta(partial, elapsed, total_days, has_data=True):
    return {"partial": partial, "elapsed": elapsed, "total_days": total_days,
            "has_data": has_data}


class F3Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = f2.f2_fixture()
        cls.model = viz.build_model(cls.fixture, CONFIG)


# ======================================================================
# Presupuestos (FPA-050…056)
# ======================================================================

class TestBudget(F3Base):
    """FPA-050…056: pro-rating, varianza con signo, markers, YTD."""

    def test_budget_desde_start_month(self):
        """FPA-051: se aplican solo desde budgets.start_month (2026-04)."""
        for row in self.model["budget"]["rows"]:
            self.assertTrue(row["in_budget"], row["ym"])
            self.assertIsNotNone(row["budget_cash"])

    def test_fuera_del_periodo_presupuestado(self):
        """FPA-051: mes anterior al start → presupuesto None con razón."""
        cfg = _cfg(start_month="2026-06")
        model = viz.build_model(self.fixture, cfg)
        may = next(r for r in model["budget"]["rows"] if r["ym"] == "2026-05")
        self.assertFalse(may["in_budget"])
        self.assertIsNone(may["budget_cash"])
        self.assertIsNone(may["variance_cash"])
        self.assertTrue(may["reason_cash"])

    def test_prorating_mes_parcial(self):
        """FPA-052: julio parcial (10/31) → presupuesto 100×10/31 = 32.26."""
        jul = next(r for r in self.model["budget"]["rows"] if r["ym"] == "2026-07")
        self.assertAlmostEqual(100.0 * 10 / 31, jul["budget_cash"], places=2)
        self.assertAlmostEqual(200.0 * 10 / 31, jul["budget_eff"], places=2)
        self.assertAlmostEqual(10 / 31, jul["pro_rate"], places=6)
        # meses completos: factor 1.0
        jun = next(r for r in self.model["budget"]["rows"] if r["ym"] == "2026-06")
        self.assertEqual(1.0, jun["pro_rate"])
        self.assertAlmostEqual(100.0, jun["budget_cash"], places=2)

    def test_varianza_con_signo(self):
        """FPA-053: varianza = actual − presupuesto; positivo = over budget."""
        jun = next(r for r in self.model["budget"]["rows"] if r["ym"] == "2026-06")
        self.assertAlmostEqual(25.0 - 100.0, jun["variance_cash"], places=6)
        self.assertEqual("-$75.00", jun["variance_display_cash"])
        self.assertAlmostEqual(-0.75, jun["variance_pct_cash"], places=6)
        self.assertEqual("-75.0%", jun["variance_pct_display_cash"])

    def test_varianza_over_budget_positiva(self):
        """FPA-053: actual > presupuesto → varianza positiva con '+$'."""
        cfg = _cfg(cash_monthly=10.0, effective_monthly=10.0)
        model = viz.build_model(self.fixture, cfg)
        may = next(r for r in model["budget"]["rows"] if r["ym"] == "2026-05")
        self.assertAlmostEqual(10.0, may["variance_cash"], places=6)
        self.assertEqual("+$10.00", may["variance_display_cash"])
        self.assertEqual("+100.0%", may["variance_pct_display_cash"])

    def test_marker_no_solo_color(self):
        """FPA-054: favorable/desfavorable con símbolo Y texto, no solo color."""
        may = next(r for r in self.model["budget"]["rows"] if r["ym"] == "2026-05")
        mk = may["marker_cash"]
        self.assertEqual("▼", mk["symbol"])
        self.assertEqual("bajo", mk["text"])  # favorable
        cfg = _cfg(cash_monthly=10.0)
        model = viz.build_model(self.fixture, cfg)
        over = next(r for r in model["budget"]["rows"] if r["ym"] == "2026-05")
        self.assertEqual("▲", over["marker_cash"]["symbol"])
        self.assertEqual("sobre", over["marker_cash"]["text"])  # desfavorable

    def test_tabla_ytd(self):
        """FPA-055: totales YTD por medida: actual, presupuesto, varianza."""
        ytd = self.model["budget"]["ytd_cash"]
        self.assertAlmostEqual(45.0, ytd["actual"], places=2)      # 20+25+0
        self.assertAlmostEqual(100 + 100 + 100 * 10 / 31, ytd["budget"], places=2)
        self.assertAlmostEqual(ytd["actual"] - ytd["budget"], ytd["variance"], places=2)
        ytd_eff = self.model["budget"]["ytd_eff"]
        self.assertAlmostEqual(145.0, ytd_eff["actual"], places=2)  # 50+65+30
        self.assertAlmostEqual(200 + 200 + 200 * 10 / 31, ytd_eff["budget"], places=2)

    def test_ytd_varianza_pct(self):
        ytd = self.model["budget"]["ytd_cash"]
        self.assertAlmostEqual(ytd["variance"] / ytd["budget"],
                               ytd["variance_pct"], places=3)
        self.assertIn("marker", ytd)

    def test_efectivo_y_cash_nunca_sumados(self):
        """FPA-002: sin campo que combine efectivo+cash."""
        blob = json.dumps(self.model["budget"])
        self.assertNotIn("total\"", blob.replace("total_days", ""))
        for row in self.model["budget"]["rows"]:
            self.assertNotIn("combined", row)

    def test_mes_sin_datos_na(self):
        """FPA-017/008: mes sin datos → actual n/a con razón, varianza n/a."""
        fixture = f2.f2_fixture()
        fixture["monthly"]["2026-06"]["interactions"] = 0
        model = viz.build_model(fixture, CONFIG)
        jun = next(r for r in model["budget"]["rows"] if r["ym"] == "2026-06")
        self.assertIsNone(jun["actual_cash"])
        self.assertIsNone(jun["variance_cash"])
        self.assertTrue(jun["reason_cash"])

    def test_inputs_en_modelo(self):
        """FPA-050: presupuestos del config expuestos para el editor JS."""
        inputs = self.model["budget"]["inputs"]
        self.assertEqual(100.0, inputs["cash_monthly"])
        self.assertEqual(200.0, inputs["effective_monthly"])
        self.assertEqual(3.0, inputs["target_per_1k"])
        self.assertEqual("2026-04", inputs["start_month"])


# ======================================================================
# Bridge precio-volumen-mix (FPA-060…068, FPA-101)
# ======================================================================

class TestBridge(F3Base):
    """FPA-060…068: fórmulas Volume/Mix/Rate, identidad, FME, eje truncado."""

    def test_formulas_volume_mix_rate(self):
        """FPA-061…063: may→jun del fixture, valores a mano.
        may: sonnet q40 p=.75, opus q20 p=.5 → Q0=100, cost0=50, rate0=.5
        jun: opus q80 p=.75, gpt q20 p=.25 → Q1=100, cost1=65
        Volume = 0×.5 = 0; Mix = (80×.5+20×.25) − 100×.5 = −5; Rate = 80×.25 = 20."""
        b = self.model["bridge"]["pairs"]["2026-06"]
        self.assertAlmostEqual(0.0, b["volume"], places=6)
        self.assertAlmostEqual(-5.0, b["mix"], places=6)
        self.assertAlmostEqual(20.0, b["rate"], places=6)
        self.assertAlmostEqual(15.0, b["delta"], places=6)

    def test_modelo_nuevo_contribuye_solo_a_mix(self):
        """FPA-064: gpt sin interacciones previas → rate prior = rate actual."""
        b = self.model["bridge"]["pairs"]["2026-06"]
        # la contribución de gpt al Rate es 20×(.25−.25) = 0; solo Mix (−5)
        self.assertAlmostEqual(20.0, b["rate"], places=6)

    def test_identidad_todos_los_pares(self):
        """FPA-065/101: Volume+Mix+Rate = Δcoste dentro de $0.01."""
        pairs = self.model["bridge"]["pairs"]
        self.assertEqual({"2026-06", "2026-07"}, set(pairs))
        for ym, b in pairs.items():
            residual = abs(b["volume"] + b["mix"] + b["rate"] - b["delta"])
            self.assertLessEqual(residual, 0.01, ym)
            self.assertLessEqual(b["identity_residual"], 0.01, ym)

    def test_fme_en_meses_parciales(self):
        """FPA-066: jun→jul (jul parcial 10/31) usa valores FME y lo etiqueta.
        jul escalado ×3.1: Q1=310, cost1=93 → Volume=(310−100)×.65=136.5;
        jul escalado ×3.1: Q1=310, cost1=93; sonnet q93 (nuevo: p0=p1=1.0,
        FPA-064), gpt q217 p1=0; opus del mes previo no está en jul.
        rate₀ = 0.65 → Volume=(310−100)×0.65=136.5;
        Mix = (217×0.25 + 93×1.0) − 310×0.65 = −54.25;
        Rate = 217×(0−0.25) = −54.25; Δ = 28."""
        b = self.model["bridge"]["pairs"]["2026-07"]
        self.assertTrue(b["fme"])
        self.assertIn("FME", b["label"])
        self.assertAlmostEqual(136.5, b["volume"], places=6)
        self.assertAlmostEqual(-54.25, b["mix"], places=6)
        self.assertAlmostEqual(-54.25, b["rate"], places=6)
        self.assertAlmostEqual(28.0, b["delta"], places=6)

    def test_sin_fme_meses_completos(self):
        b = self.model["bridge"]["pairs"]["2026-06"]
        self.assertFalse(b["fme"])

    def test_eje_truncado_cuando_delta_pequeno(self):
        """FPA-067: |Δ| < 10% de los totales → eje truncado etiquetado."""
        meta = {"ym": "2026-06", "partial": False, "elapsed": 31,
                "total_days": 31, "has_data": True}
        prev = _mini_month(100, 1000.0,
                           {"m": {"interactions": 100, "cost_effective": 1000.0}})
        cur = _mini_month(100, 1005.0,
                          {"m": {"interactions": 100, "cost_effective": 1005.0}})
        b = viz.build_bridge(prev, cur, meta, meta)
        self.assertTrue(b["truncated"])
        self.assertIn("truncado", b["axis_label"].lower())
        # Δ grande relativo → sin truncar
        cur2 = _mini_month(100, 1500.0,
                           {"m": {"interactions": 100, "cost_effective": 1500.0}})
        b2 = viz.build_bridge(prev, cur2, meta, meta)
        self.assertFalse(b2["truncated"])

    def test_waterfall_svg(self):
        """FPA-060: waterfall renderizado por par (SVG inline)."""
        svg = self.model["bridge"]["pairs"]["2026-06"]["svg"]
        self.assertIn("<svg", svg)
        self.assertIn("Volumen", svg)
        self.assertIn("Mix", svg)
        self.assertIn("Rate", svg)
        # eje truncado solo donde aplica
        self.assertNotIn("truncado", self.model["bridge"]["pairs"]["2026-06"]["svg"])

    def test_mix_stack_100_por_ciento(self):
        """FPA-068: mix 100% stacked por mes en share de coste."""
        stack = self.model["bridge"]["mix_stack"]
        jun = stack["2026-06"]
        self.assertAlmostEqual(1.0, sum(s["share"] for s in jun["segments"]), places=6)
        self.assertFalse(jun["proxy"])
        labels = {s["model"] for s in jun["segments"]}
        self.assertEqual({"claude-opus-4.7", "gpt-5.4"}, labels)

    def test_mix_stack_proxy_sin_coste_por_modelo(self):
        """Reporte real (modelos sin coste) → proxy por interacciones, visible."""
        fixture = f2.f2_fixture()
        fixture["monthly"]["2026-06"]["models"] = {
            "claude-opus-4.7": 80, "gpt-5.4": 20}  # counters, sin coste
        stack = viz.build_mix_stack(fixture)
        jun = stack["2026-06"]
        self.assertTrue(jun["proxy"])
        self.assertAlmostEqual(0.8, jun["segments"][0]["share"], places=6)
        self.assertTrue(jun["proxy_reason"])


# ======================================================================
# Forecast (FPA-070…077)
# ======================================================================

class TestForecast(F3Base):
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


# ======================================================================
# Render HTML + JS
# ======================================================================

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


# ======================================================================
# Golden (FPA-101/102)
# ======================================================================

class TestGolden(unittest.TestCase):
    """FPA-101/102: golden de budget+bridge+forecast sobre el fixture con
    meses parciales. Regenerar: REGEN_GOLDEN=1 python3 tests/test_fpa_f3.py"""

    def test_golden_f3(self):
        model = viz.build_model(f2.f2_fixture(), CONFIG)
        snapshot = {k: model[k] for k in ("budget", "bridge", "forecast")}
        if REGEN_GOLDEN:
            GOLDEN.parent.mkdir(parents=True, exist_ok=True)
            GOLDEN.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False,
                                         sort_keys=True) + "\n")
            self.skipTest("golden regenerado; revisar diff antes de aceptar")
        if not GOLDEN.exists():
            self.fail("Falta golden; corre REGEN_GOLDEN=1 python3 tests/test_fpa_f3.py")
        expected = json.loads(GOLDEN.read_text())
        self.assertEqual(expected, snapshot)


if __name__ == "__main__":
    unittest.main()
