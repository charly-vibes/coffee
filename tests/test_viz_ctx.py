#!/usr/bin/env python3
"""
test_viz_ctx.py — tests python-side de scripts/viz-ctx.py (ctx-viz)

Cubre coffe-0tw (tasks 2.1+2.2):
- build_model(): pre-cálculo en Python (FPA-145: el JS solo re-escala) —
  histograma del pico de contexto, banda p50/p75/p90/p95/p99, conteo de
  excluidas/stubs, tags assumed del factor chars/4
- render(): HTML autocontenido stdlib-only (sin <script src>/<link href>
  externos), SVG inline, tema compartido (site_theme.py), header que
  declara alcance Pi-only, conteo de excluidas junto al título
- main(): reporte sin context_metrics disponible → placeholder honesto

El smoke en Chromium real vive en tests/test_viz.py (pageerrors).
Fixture: context_metrics sintético con el shape que emite el tracker
(reduce_context → aggregate_context, epic add-context-dashboard).
"""

import importlib.util
import json
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VIZ_PATH = REPO / "scripts" / "viz-ctx.py"


def load_viz():
    spec = importlib.util.spec_from_file_location("viz_ctx", VIZ_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def sesion(peak_est, model="claude-sonnet-4-6", month="2026-09",
           pathological=False, turns=10, project="charly-coffee",
           chars_by_source=None, big_tool_results=0, context_source="pi",
           costs=None, cost_null_reason=None, tokens_reported=None,
           final_turn_tokens=None, max_assistant_block_chars=1000):
    """Fila de sesión con el shape emitido por reduce_context."""
    r = {
        "month": month, "project": project, "model": model,
        "models_turns": {model: turns}, "provider": "anthropic",
        "turns": turns, "turns_no_usage": 0,
        "chars_by_source": chars_by_source or {"asst_text": peak_est * 4 - 100,
                                               "user_text": 100},
        "big_results": big_tool_results,
        "big_results_by_tool": {"read": big_tool_results} if big_tool_results else {},
        "compactions": 0, "segment_count": 1,
        "peak_ctx_chars": peak_est * 4, "final_ctx_chars": peak_est * 4,
        "peak_ctx_est": peak_est, "final_ctx_est": peak_est,
        "max_assistant_block_chars": max_assistant_block_chars,
        "chars_per_token": 4,
        "cost_in": None, "cost_out": None, "cost_cr": None, "cost_cw": None,
        "context_source": context_source, "pathological": pathological,
        "tokens_reported": tokens_reported if tokens_reported is not None
                           else peak_est * 3,
        "final_turn_tokens": final_turn_tokens if final_turn_tokens is not None
                             else peak_est,
    }
    if costs:
        r.update({"cost_in": costs[0], "cost_out": costs[1],
                  "cost_cr": costs[2], "cost_cw": costs[3]})
    if cost_null_reason:
        r["cost_null_reason"] = cost_null_reason
    return r


def report_sintetico(peaks=(50_000, 100_000, 150_000, 200_000, 400_000),
                     n_pathological=1, n_stubs=2, meta=None):
    sessions = [sesion(p) for p in peaks]
    # sesiones grandes (>100k) con composición distinta para la vista
    sessions[2]["chars_by_source"] = {"asst_text": 400_000,
                                      "tool:read": 150_000,
                                      "asst_thinking": 50_000}
    # coste: 2 sesiones pagas (1ª con cache), 1 claude-cli (suscripción),
    # 1 sin coste (null con razón)
    sessions[0].update({"cost_in": 1.0, "cost_out": 2.0, "cost_cr": 0.1,
                        "cost_cw": 0.2})
    sessions[1].update({"cost_in": 3.0, "cost_out": 4.0, "cost_cr": 0.0,
                        "cost_cw": 0.0})
    sessions[3] = sesion(200_000, context_source="claude-cli",
                         costs=(0.0, 0.0, 0.0, 0.0))
    sessions[4] = sesion(400_000, cost_null_reason="sin usage.cost en "
                         "ningún turno (turnos abortados)")
    sessions += [sesion(16_000_000, pathological=True,
                        max_assistant_block_chars=16_000_000)
                 for _ in range(n_pathological)]
    cm = {
        "available": True, "reason": None,
        "sessions": sessions,
        "monthly_model": [{
            "month": "2026-09", "model": "claude-sonnet-4-6",
            "sessions": len(sessions) - n_pathological,
            "peak_p50": 150_000, "peak_p90": 380_000,
            "pct_big_session": 40.0,
            "tokens_per_turn_by_source": {"asst_text": 900.0},
            "spend_in": 0.0, "spend_out": 0.0, "spend_cr": 0.0,
            "spend_cw": 0.0, "partial": True,
        }],
        "meta": meta or {
            "chars_per_token": 4,
            "chars_per_token_tag": "assumed",
            "chars_per_token_bias": "subestima el contexto real",
            "validation_ratio_median": 1.27,
            "thresholds": {"big_result_chars": 100_000,
                           "big_session_tokens": 100_000,
                           "pathological_chars": 500_000,
                           "chars_per_token": 4},
            "stubs": n_stubs,
            "excluded_pathological": n_pathological,
        },
    }
    return {"context_metrics": cm}


class TestBuildModelDistribucion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.viz = load_viz()
        cls.model = cls.viz.build_model(report_sintetico())

    def test_histograma_sobre_set_limpio(self):
        """El histograma usa el set limpio: las patológicas (16M) no
        inflan el bin superior — suma de conteos == sesiones limpias."""
        hist = self.model["dist"]["histogram"]
        total = sum(h["count"] for h in hist)
        self.assertEqual(5, total)

    def test_banda_percentiles(self):
        band = self.model["dist"]["band"]
        self.assertEqual(["p50", "p75", "p90", "p95", "p99"],
                         sorted(band.keys()))
        self.assertAlmostEqual(150_000, band["p50"])
        # interpolación lineal: p99 = 200k + 0.96*(400k-200k)
        self.assertAlmostEqual(392_000, band["p99"])
        # monótona
        vals = [band[k] for k in ("p50", "p75", "p90", "p95", "p99")]
        self.assertEqual(vals, sorted(vals))

    def test_conteos_excluidas_y_stubs(self):
        d = self.model["dist"]
        self.assertEqual(1, d["excluded_pathological"])
        self.assertEqual(2, d["stubs"])
        self.assertEqual(5, d["n_clean"])

    def test_umbral_patologico_en_modelo(self):
        """El umbral de config va al modelo (visible en el histograma)."""
        self.assertEqual(500_000,
                         self.model["dist"]["thresholds"]["pathological_chars"])

    def test_tag_assumed_en_modelo(self):
        """El factor chars/4 llega al modelo con su tag assumed + bias."""
        meta = self.model["meta"]
        self.assertEqual(4, meta["chars_per_token"])
        self.assertEqual("assumed", meta["chars_per_token_tag"])
        self.assertIn("subestima", meta["chars_per_token_bias"])
        self.assertAlmostEqual(1.27, meta["validation_ratio_median"])

    def test_scoped_pi_only(self):
        """El header declara alcance Pi-only (spec: alcance visible)."""
        self.assertIn("Pi", self.model["scope_note"])


class TestComposicion(unittest.TestCase):
    """coffe-opo (2.3): share por fuente global y en sesiones grandes
    (>100k), leyenda completa en ambas, SVG stacked."""

    @classmethod
    def setUpClass(cls):
        cls.viz = load_viz()
        cls.model = cls.viz.build_model(report_sintetico())
        cls.comp = cls.model["comp"]

    def test_shares_global_normalizados(self):
        shares = self.comp["global"]
        self.assertAlmostEqual(1.0, sum(shares.values()), places=6)
        self.assertIn("asst_text", shares)
        self.assertIn("user_text", shares)

    def test_sesiones_grandes_por_umbral_de_config(self):
        """>100k (big_session_tokens de config): las sesiones pico 150k,
        200k, 400k entran; 50k y 100k no (el umbral es estricto)."""
        self.assertEqual(100_000, self.comp["big_threshold"])
        self.assertEqual(3, self.comp["n_big"])
        # en las grandes aparece tool:read (solo ellas lo tienen)
        self.assertIn("tool:read", self.comp["big"])
        self.assertAlmostEqual(1.0, sum(self.comp["big"].values()), places=6)

    def test_leyenda_fuentes_unidas(self):
        """Leyenda completa: ninguna fuente desaparece (spec) — la unión de
        fuentes global∪grandes es la leyenda y va al modelo."""
        union = set(self.comp["global"]) | set(self.comp["big"])
        self.assertEqual(union, set(self.comp["legend"]))

    def test_svg_stacked_en_render(self):
        html = self.viz.render(self.model)
        self.assertIn("Composición", html)
        self.assertIn("stack", html)
        self.assertIn("tool:read", html)  # fuente de las grandes en leyenda


class TestTiempo(unittest.TestCase):
    """coffe-opo (2.4): mes × modelo — pico mediano, % > umbral, tok/turno,
    meses parciales marcados con días transcurridos/total (FPA-041)."""

    @classmethod
    def setUpClass(cls):
        cls.viz = load_viz()
        report = report_sintetico()
        # end del rango en el mes máximo del fixture (coherencia partial)
        report["metadata"] = {"date_range": {"start": "2025-12-23",
                                             "end": "2026-09-19"}}
        cls.model = cls.viz.build_model(report)
        cls.time_rows = cls.model["time"]["rows"]

    def test_fila_por_mes_modelo(self):
        f = self.time_rows[0]
        self.assertEqual("2026-09", f["month"])
        self.assertEqual("claude-sonnet-4-6", f["model"])
        self.assertAlmostEqual(150_000, f["peak_p50"])
        self.assertAlmostEqual(40.0, f["pct_big_session"])

    def test_tok_por_turno_por_fuente(self):
        f = self.time_rows[0]
        self.assertIn("asst_text", f["tokens_per_turn_by_source"])

    def test_mes_parcial_con_dias(self):
        """El mes máximo (2026-10… aquí el fixture tiene 2026-09) se marca
        parcial con días transcurridos/total como FPA-041."""
        parcial = [r for r in self.time_rows if r["partial"]]
        self.assertTrue(parcial)
        f = parcial[0]
        self.assertEqual("19/30", f["days_label"])  # FPA-041: elapsed/total

    def test_render_tabla_tiempo(self):
        html = self.viz.render(self.model)
        self.assertIn("Tiempo", html)
        self.assertIn("pico mediano", html.lower())


class TestCoste(unittest.TestCase):
    """coffe-dv6 (2.5): spend por modelo, split in/out/cache, spend mensual,
    $/sesión; claude-cli separado con provenancia subscription, sin suma
    combinada; coste null con razón visible."""

    @classmethod
    def setUpClass(cls):
        cls.viz = load_viz()
        cls.model = cls.viz.build_model(report_sintetico())
        cls.cost = cls.model["cost"]

    def test_spend_por_modelo_con_split(self):
        por_modelo = {m["model"]: m for m in self.cost["by_model"]}
        a = por_modelo["claude-sonnet-4-6"]
        # sesiones 0 y 1: in 1+3=4, out 2+4=6, cr 0.1, cw 0.2
        self.assertAlmostEqual(4.0, a["spend_in"])
        self.assertAlmostEqual(6.0, a["spend_out"])
        self.assertAlmostEqual(0.1, a["spend_cr"])
        self.assertAlmostEqual(0.2, a["spend_cw"])
        self.assertEqual(2, a["sessions"])   # la null-cost no entra
        self.assertAlmostEqual(5.15, a["spend_per_session"])  # 10.3/2

    def test_claude_cli_separado_sin_suma_combinada(self):
        """claude-cli NO entra en by_model de pago: fila aparte con
        provenancia subscription — el total combinado no la incluye."""
        sub = self.cost["subscription"]
        self.assertEqual(1, sub["sessions"])
        self.assertEqual("subscription", sub["provenance"])
        # spend_total (pago) = in 4 + out 6 = 10 — claude-cli ($0) aparte
        self.assertAlmostEqual(10.0,
                               self.cost["spend_total_in"]
                               + self.cost["spend_total_out"])

    def test_coste_null_con_razon_visible(self):
        # 3 sin coste: la de 400k (con razón), la de 150k, y la patológica
        self.assertEqual(3, self.cost["n_null_cost"])
        self.assertIn("abortados", self.cost["null_reason_sample"])

    def test_spend_mensual(self):
        by_month = {m["month"]: m for m in self.cost["by_month"]}
        self.assertAlmostEqual(10.0, by_month["2026-09"]["spend_in"]
                               + by_month["2026-09"]["spend_out"])

    def test_render_coste(self):
        html = self.viz.render(self.model)
        self.assertIn("Coste", html)
        self.assertIn("subscription", html)
        self.assertIn("abortados", html)  # la razón null llega al HTML


class TestPatologias(unittest.TestCase):
    """coffe-dv6 (2.7): outliers listados con tamaño, modelo y razón
    de exclusión (FPA-008: nada silencioso)."""

    @classmethod
    def setUpClass(cls):
        cls.viz = load_viz()
        cls.model = cls.viz.build_model(report_sintetico())
        cls.pats = cls.model["pathological_list"]

    def test_outlier_listado_con_tamano_y_modelo(self):
        self.assertEqual(1, len(self.pats))
        p = self.pats[0]
        self.assertEqual(16_000_000, p["max_assistant_block_chars"])
        self.assertEqual("claude-sonnet-4-6", p["model"])

    def test_razon_con_umbral_de_config(self):
        p = self.pats[0]
        self.assertEqual(500_000, p["threshold"])
        self.assertIn("bloque assistant", p["reason"])

    def test_render_patologias(self):
        html = self.viz.render(self.model)
        self.assertIn("Patologías", html)
        self.assertIn("16.0M", html)  # tamaño formateado


class TestRenderHtml(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.viz = load_viz()
        cls.model = cls.viz.build_model(report_sintetico())
        cls.html = cls.viz.render(cls.model)

    def test_sin_dependencias_externas(self):
        """Spec: no hay <script src> ni <link href> externos."""
        self.assertNotIn('<script src', self.html)
        self.assertNotIn('<link rel="stylesheet"', self.html)
        self.assertNotIn('http://cdn', self.html)

    def test_svg_inline_y_tema(self):
        self.assertIn("<svg", self.html)
        self.assertIn("--accent", self.html)  # tokens del tema compartido

    def test_conteo_excluidas_junto_al_titulo(self):
        self.assertIn("1 excluida", self.html)
        self.assertIn("2 stubs", self.html)

    def test_header_pi_only(self):
        self.assertIn("Pi", self.html)

    def test_tag_assumed_en_cifras_estimadas(self):
        """El factor chars/4 referenciado como assumed en el HTML (spec:
        toda cifra de contexto estimado lleva el tag)."""
        self.assertIn("assumed", self.html)

    def test_barra_del_histograma_con_tooltip(self):
        self.assertIn("<title>", self.html)  # tooltip nativo SVG (<title>)


class TestSinDatos(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.viz = load_viz()

    def test_reporte_sin_seccion(self):
        model = self.viz.build_model({})
        self.assertFalse(model["available"])
        self.assertTrue(model["reason"])

    def test_render_placeholder_honesto(self):
        model = self.viz.build_model({})
        html = self.viz.render(model)
        self.assertIn("sin datos", html.lower())
        self.assertIn("unavailable", html)  # la razón llega al HTML

    def test_seccion_unavailable_con_razon(self):
        cm = {"available": False, "reason": "unavailable: no existe …",
              "sessions": [], "monthly_model": [], "meta": {}}
        model = self.viz.build_model({"context_metrics": cm})
        self.assertFalse(model["available"])


if __name__ == "__main__":
    unittest.main()
