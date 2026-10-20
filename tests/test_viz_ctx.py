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
           pathological=False, turns=10, project="charly-coffee"):
    """Fila de sesión con el shape emitido por reduce_context."""
    return {
        "month": month, "project": project, "model": model,
        "models_turns": {model: turns}, "provider": "anthropic",
        "turns": turns, "turns_no_usage": 0,
        "chars_by_source": {"asst_text": peak_est * 4 - 100,
                            "user_text": 100},
        "big_results": 0, "big_results_by_tool": {},
        "compactions": 0, "segment_count": 1,
        "peak_ctx_chars": peak_est * 4, "final_ctx_chars": peak_est * 4,
        "peak_ctx_est": peak_est, "final_ctx_est": peak_est,
        "max_assistant_block_chars": 1000, "chars_per_token": 4,
        "cost_in": None, "cost_out": None, "cost_cr": None, "cost_cw": None,
        "context_source": "pi", "pathological": pathological,
        "tokens_reported": peak_est * 3, "final_turn_tokens": peak_est,
    }


def report_sintetico(peaks=(50_000, 100_000, 150_000, 200_000, 400_000),
                     n_pathological=1, n_stubs=2, meta=None):
    sessions = [sesion(p) for p in peaks]
    sessions += [sesion(16_000_000, pathological=True)
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
