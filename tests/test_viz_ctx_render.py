#!/usr/bin/env python3
"""
test_viz_ctx_render.py — tests python-side de scripts/viz-ctx.py (ctx-viz):
render del HTML autocontenido, ley de crecimiento, tabs/deep-link,
--check-docs y placeholders sin datos (parte del ex test_viz_ctx.py).

- render(): HTML autocontenido stdlib-only (sin <script src>/<link href>
  externos), SVG inline, tema compartido (site_theme.py), header que
  declara alcance Pi-only
- coffe-r3c (2.6/2.8): dispersivo pico vs turnos con pendiente OLS
  pre-calculada (hallazgo: ~850 tok/turno constante entre eras;
  anti-goal: la pendiente se calcula, nunca constante fijada) y tabs una
  vista a la vez con deep-link por hash, export CSV/SVG
- coffe-ruk (3.2): --check-docs — consistencia README ↔ JSON al estilo
  expected_doc_figures() de viz-fpa, con bloque propio CTX-DOCS
- main(): reporte sin context_metrics disponible → placeholder honesto
"""

import importlib.util
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VIZ_PATH = REPO / "scripts" / "viz-ctx.py"


def load_viz():
    spec = importlib.util.spec_from_file_location("viz_ctx", VIZ_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


_SESION_DEFAULTS = {
    "month": "2026-09", "project": "charly-coffee",
    "model": "claude-sonnet-4-6", "provider": "anthropic",
    "turns": 10, "turns_no_usage": 0,
    "compactions": 0, "segment_count": 1,
    "max_assistant_block_chars": 1000, "chars_per_token": 4,
    "context_source": "pi", "pathological": False,
}


def sesion(peak_est, opts=None):
    """Fila de sesión con el shape emitido por reduce_context. `opts` pisa
    defaults puntuales; los campos derivados se recalculan en función de lo
    pisado (models_turns de model/turns, big_results de big_tool_results,
    costs expande los 4 cost_*, cost_null_reason solo si se da)."""
    opts = dict(opts or {})
    costs = opts.pop("costs", None)
    big = opts.pop("big_tool_results", 0)
    r = {**_SESION_DEFAULTS, **opts,
         "peak_ctx_chars": peak_est * 4, "final_ctx_chars": peak_est * 4,
         "peak_ctx_est": peak_est, "final_ctx_est": peak_est,
         "chars_by_source": opts.get("chars_by_source")
         or {"asst_text": peak_est * 4 - 100, "user_text": 100},
         "big_results": big,
         "big_results_by_tool": {"read": big} if big else {},
         "cost_in": None, "cost_out": None, "cost_cr": None, "cost_cw": None}
    if opts.get("tokens_reported") is None:
        r["tokens_reported"] = peak_est * 3
    if opts.get("final_turn_tokens") is None:
        r["final_turn_tokens"] = peak_est
    r["models_turns"] = {r["model"]: r["turns"]}
    if costs:
        r.update({"cost_in": costs[0], "cost_out": costs[1],
                  "cost_cr": costs[2], "cost_cw": costs[3]})
    if opts.get("cost_null_reason"):
        r["cost_null_reason"] = opts["cost_null_reason"]
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
    sessions[3] = sesion(200_000, {"context_source": "claude-cli",
                                   "costs": (0.0, 0.0, 0.0, 0.0)})
    sessions[4] = sesion(400_000, {"cost_null_reason": "sin usage.cost en "
                                   "ningún turno (turnos abortados)"})
    sessions += [sesion(16_000_000, {"pathological": True,
                                   "max_assistant_block_chars": 16_000_000})
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


def _growth_report():
    """Dos eras con pendiente distinta (m-a: 850×turnos exactos, m-b: 400×)
    más una sesión glitch (16M, 200 turnos) excluida del ajuste."""
    sessions = []
    for t in (20, 40, 60, 80):
        sessions.append(sesion(850 * t, {"model": "m-a", "turns": t}))
    for t in (20, 40, 60, 80):
        sessions.append(sesion(400 * t, {"model": "m-b", "turns": t}))
    sessions.append(sesion(16_000_000, {"pathological": True,
                                          "turns": 200,
                                          "max_assistant_block_chars"
                                          : 16_000_000}))
    return {"context_metrics": {
        "available": True, "reason": None, "sessions": sessions,
        "monthly_model": [],
        "meta": {"chars_per_token": 4, "chars_per_token_tag": "assumed",
                 "chars_per_token_bias": "subestima",
                 "validation_ratio_median": 1.0,
                 "thresholds": {"big_result_chars": 100_000,
                                "big_session_tokens": 100_000,
                                "pathological_chars": 500_000,
                                "chars_per_token": 4},
                 "stubs": 0, "excluded_pathological": 1}}}


VIZ = load_viz()
_REPORT = report_sintetico()
_MODEL = VIZ.build_model(_REPORT)
_HTML = VIZ.render(_MODEL)
_EXPECTED = VIZ.expected_ctx_figures(_REPORT)
_GROWTH_MODEL = VIZ.build_model(_growth_report())


class TestLeyCrecimiento(unittest.TestCase):
    """coffe-r3c (2.6): dispersivo pico vs turnos por modelo con pendiente
    OLS pre-calculada (hallazgo: ~850 tok/turno constante entre eras).
    Anti-goal: la pendiente se calcula, nunca constante fijada."""

    growth = {g["model"]: g for g in _GROWTH_MODEL["growth"]["by_model"]}

    def test_pendiente_ols_era_850(self):
        """m-a: pico = 850×turnos exactos → pendiente 850 (±1%)."""
        g = self.growth["m-a"]
        self.assertAlmostEqual(850, g["slope"], delta=8.5)
        self.assertEqual(4, g["n"])

    def test_orden_de_eras_comparado(self):
        """Dos eras con pendientes distintas: 850 > 400 — comparable."""
        self.assertGreater(self.growth["m-a"]["slope"],
                           self.growth["m-b"]["slope"])
        self.assertAlmostEqual(400, self.growth["m-b"]["slope"], delta=4)

    def test_patologica_fuera_del_ajuste(self):
        """La sesión glitch (16M, 200 turnos) no entra en el ajuste."""
        self.assertEqual(4, self.growth["m-a"]["n"])

    def test_puntos_del_scatter_en_render(self):
        html = VIZ.render(_GROWTH_MODEL)
        self.assertIn("Ley de crecimiento", html)
        self.assertIn("850", html)  # pendiente visible
        self.assertIn("scatter", html)


class TestTabs(unittest.TestCase):
    """coffe-r3c (2.8): tabs una vista a la vez a todo ancho, deep-link por
    hash, export CSV/SVG — como fpa-dashboard."""

    def test_tabs_nav_y_secciones(self):
        self.assertIn('class="tab"', _HTML)
        self.assertIn('data-view="dist"', _HTML)
        self.assertIn('id="dist"', _HTML)
        self.assertIn('body[data-view="dist"] #dist', _HTML)

    def test_deeplink_y_export(self):
        self.assertIn("hashchange", _HTML)
        self.assertIn("setView", _HTML)
        self.assertIn("window.__ctx", _HTML)
        self.assertIn("export-csv", _HTML)
        self.assertIn("dl-svg", _HTML)

    def test_sin_js_externo(self):
        self.assertNotIn("<script src", _HTML)


class TestRenderHtml(unittest.TestCase):
    def test_sin_dependencias_externas(self):
        """Spec: no hay <script src> ni <link href> externos."""
        self.assertNotIn('<script src', _HTML)
        self.assertNotIn('<link rel="stylesheet"', _HTML)
        self.assertNotIn('http://cdn', _HTML)

    def test_svg_tema_y_conteos(self):
        self.assertIn("<svg", _HTML)
        self.assertIn("--accent", _HTML)  # tokens del tema compartido
        self.assertIn("1 excluida", _HTML)
        self.assertIn("2 stubs", _HTML)

    def test_header_pi_con_tag_assumed(self):
        """El header declara alcance Pi-only (spec) y toda cifra de
        contexto estimado lleva el tag assumed."""
        self.assertIn("Pi", _HTML)
        self.assertIn("assumed", _HTML)

    def test_tooltip_del_histograma(self):
        self.assertIn("<title>", _HTML)  # tooltip nativo SVG (<title>)


class TestCheckDocs(unittest.TestCase):
    """coffe-ruk (3.2): --check-docs de viz-ctx.py — consistencia README ↔
    JSON al estilo expected_doc_figures() de viz-fpa, con bloque propio
    CTX-DOCS (el bloque CHECK-DOCS de viz-fpa no admite cifras extra)."""

    def test_expected_figures_shape(self):
        self.assertIn("Sesiones con contexto", _EXPECTED)
        self.assertIn("Excluidas (patológicas)", _EXPECTED)
        self.assertIn("Factor chars/token", _EXPECTED)

    def test_bloque_consistente_sin_errores(self):
        block = "\n".join(f"- {k}: {v}" for k, v in _EXPECTED.items())
        readme = self._readme(block)
        self.assertEqual([], VIZ.check_docs_ctx(_REPORT, readme))

    def test_cifra_desviada_da_error(self):
        k = next(iter(_EXPECTED))
        block = "\n".join(
            f"- {key}: {'1' if key == k else v}"
            for key, v in _EXPECTED.items())
        readme = self._readme(block)
        errors = VIZ.check_docs_ctx(_REPORT, readme)
        self.assertTrue(errors)
        self.assertIn(k, errors[0])

    def test_bloque_vacio_da_falta_cifras(self):
        readme = self._readme("")
        errors = VIZ.check_docs_ctx(_REPORT, readme)
        self.assertTrue(any("falta cifra" in e for e in errors))

    def test_sin_bloque_da_error(self):
        import tempfile
        f = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False)
        f.write("README sin bloque\n")
        f.close()
        self.addCleanup(lambda: Path(f.name).unlink(missing_ok=True))
        errors = VIZ.check_docs_ctx(_REPORT, f.name)
        self.assertTrue(any("CTX-DOCS" in e for e in errors))

    def _readme(self, block):
        # check_docs_ctx recibe una ruta (estilo viz-fpa): tmpfile
        import tempfile
        f = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False)
        f.write(VIZ.CTX_DOC_BEGIN + "\n" + block + "\n"
                + VIZ.CTX_DOC_END)
        f.close()
        self.addCleanup(lambda: Path(f.name).unlink(missing_ok=True))
        return f.name


class TestSinDatos(unittest.TestCase):
    def test_reporte_sin_seccion(self):
        model = VIZ.build_model({})
        self.assertFalse(model["available"])
        self.assertTrue(model["reason"])

    def test_render_placeholder_honesto(self):
        model = VIZ.build_model({})
        html = VIZ.render(model)
        self.assertIn("sin datos", html.lower())
        self.assertIn("unavailable", html)  # la razón llega al HTML

    def test_seccion_unavailable_con_razon(self):
        cm = {"available": False, "reason": "unavailable: no existe …",
              "sessions": [], "monthly_model": [], "meta": {}}
        model = VIZ.build_model({"context_metrics": cm})
        self.assertFalse(model["available"])


if __name__ == "__main__":
    unittest.main()
