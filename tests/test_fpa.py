#!/usr/bin/env python3
"""
test_fpa.py — tests de scripts/viz-fpa.py (F1: base + resumen ejecutivo)

Cubre el alcance del ticket coffe-lat.2 (FPA-001…009, 093, 095, 108):
- validación de schema stdlib-only con exit non-zero y campos fallidos (FPA-006)
- mes parcial con días transcurridos/total (FPA-004)
- separación estricta efectivo/cash, jamás sumados (FPA-002)
- provenance tag reported/assumed en toda figura (FPA-003)
- resumen ejecutivo 3–5 headlines con interpretación y n/a con razón (FPA-007/008)
- claims solo con métrica + threshold visibles (FPA-009)
- tema claro/oscuro (FPA-093), formatos USD con separadores (FPA-095)
- placeholder-check: resumen nunca vacío, sin placeholders sin valor (FPA-108)
- golden test de las agregaciones base (FPA-100, fase 1): REGEN_GOLDEN=1
  contra el dataset congelado tests/fixtures/usage-report-frozen.json
  (coffe-xvt: el golden ya no amanece rojo cuando el nightly regenera el
  dataset vivo; regen del golden solo ante cambios de lógica/schema)
"""

import importlib.util
import io
import json
import os
import unittest
from contextlib import redirect_stderr
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GOLDEN = Path(__file__).resolve().parent / "golden" / "fpa-summary-snapshot.json"
REGEN_GOLDEN = os.environ.get("REGEN_GOLDEN") == "1"


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, REPO / relpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


viz = _load("viz_fpa", "scripts/viz-fpa.py")

REPORT = json.loads((REPO / "data" / "usage_report_v3.json").read_text())
CONFIG = json.loads((REPO / "config" / "fpa.json").read_text())


def _rendered():
    """HTML real del reporte del repo."""
    return viz.render_html(REPORT, CONFIG, generated="2026-09-19 12:00")


def _mini_report(**meta_over):
    """Reporte mínimo válido para tests de validación."""
    return {
        "metadata": {
            "date_range": {"start": "2026-06-01", "end": "2026-06-10"},
            "filter": "in-scope",
            "total_interactions": 10,
            "total_days": 10,
            "total_hours": 5,
            "total_projects": 1,
            "cost_total_effective": 1.0,
            "cost_total_real": 20.0,
            "subscription_fees": 20.0,
            **meta_over,
        },
        "hourly": {}, "daily": {},
        "monthly": {"2026-06": {"interactions": 10, "input_tokens": 1,
                                "output_tokens": 1, "cache_read_tokens": 1,
                                "cache_write_tokens": 0, "cost_effective": 1.0,
                                "cost_real": 20.0, "tools": {}, "models": {},
                                "subscription_fees": 20.0}},
        "projects": {}, "skills": {}, "commands": {}, "sessions": {},
        "multitasking": {}, "project_daily": {},
        "subscription_config": {}, "subscription_fees_by_month": {},
    }


class TestPureFuncs(unittest.TestCase):
    """Funciones puras de matemática sin IO (design.md: maths en Python)."""

    def test_month_total_days(self):
        self.assertEqual(31, viz.month_total_days("2026-01"))
        self.assertEqual(28, viz.month_total_days("2026-02"))  # 2026 no bisiesto
        self.assertEqual(30, viz.month_total_days("2026-06"))

    def test_fme_full_month_equivalent(self):
        """FME = valor parcial × días_del_mes / días_transcurridos."""
        self.assertAlmostEqual(30.0, viz.fme(10.0, 10, 30))
        self.assertAlmostEqual(10.0, viz.fme(10.0, 30, 30))

    def test_fme_denominador_cero_da_none(self):
        self.assertIsNone(viz.fme(10.0, 0, 30))


class TestMonthsMeta(unittest.TestCase):
    """FPA-004: mes parcial con días transcurridos/total."""

    def test_mes_final_parcial_coherente(self):
        """FPA-004: mes parcial (el último del rango) → elapsed = último día
        del rango y total_days = días del mes (coffe-6i8: antes clavado a
        19; coffe-9w0: derivado del reporte para sobrevivir regeneraciones
        nocturnas del dataset — antes asumía septiembre fijo)."""
        import calendar
        end = REPORT["metadata"]["date_range"]["end"]
        meta = viz.build_months(REPORT)
        ultimo = [m for m in meta if m["ym"] == end[:7]][0]
        self.assertTrue(ultimo["partial"])
        self.assertEqual(int(end[8:10]), ultimo["elapsed"])
        y, mo = map(int, ultimo["ym"].split("-"))
        self.assertEqual(calendar.monthrange(y, mo)[1], ultimo["total_days"])

    def test_meses_completos_no_parciales(self):
        end = REPORT["metadata"]["date_range"]["end"]
        meta = viz.build_months(REPORT)
        for m in meta:
            if m["ym"] != end[:7]:
                self.assertFalse(m["partial"], m["ym"])

    def test_dias_en_cada_mes(self):
        meta = {m["ym"]: m for m in viz.build_months(REPORT)}
        self.assertEqual(31, meta["2026-01"]["total_days"])

    def test_mes_sin_datos_flag(self):
        """FPA-017: mes sin datos se marca y se excluye de trends."""
        rep = _mini_report()
        rep["monthly"]["2026-07"] = dict(rep["monthly"]["2026-06"], interactions=0)
        meta = {m["ym"]: m for m in viz.build_months(rep)}
        self.assertFalse(meta["2026-07"]["has_data"])
        self.assertTrue(meta["2026-06"]["has_data"])


class TestHeadlines(unittest.TestCase):
    """FPA-007/008: 3–5 headlines con interpretación; n/a con razón."""

    def test_entre_3_y_5_headlines(self):
        heads = viz.build_headlines(REPORT)
        self.assertTrue(3 <= len(heads) <= 5, f"{len(heads)} headlines")

    def test_headlines_obligatorios_presentes(self):
        heads = {h["key"] for h in viz.build_headlines(REPORT)}
        self.assertIn("cost_effective", heads)
        self.assertIn("cost_cash", heads)
        self.assertIn("leverage", heads)
        self.assertIn("cost_per_1k", heads)

    def test_efectivo_y_cash_separados_y_distinguidos(self):
        """FPA-002: dos medidas separadas, nunca sumadas."""
        heads = {h["key"]: h for h in viz.build_headlines(REPORT)}
        self.assertNotEqual(heads["cost_effective"]["value"],
                            heads["cost_cash"]["value"])
        # no hay ningún headline combinado efectivo+cash
        self.assertNotIn("total_cost", heads)

    def test_valores_contra_el_reporte(self):
        heads = {h["key"]: h for h in viz.build_headlines(REPORT)}
        eff = sum(m["cost_effective"] for m in REPORT["monthly"].values()
                  if m["interactions"] > 0)
        self.assertAlmostEqual(eff, heads["cost_effective"]["value"], places=2)
        # coffe-a31.3 (CRG-F2): cost_cash del headline = cash del ledger
        # (charges_real_by_month, *reported*), NO la suma mensual del tracker
        # (fees implícitos + p2p, *assumed*) — FPA-002 las mantiene separadas.
        meses_con_datos = {ym for ym, mo in REPORT["monthly"].items()
                           if mo["interactions"] > 0}
        cash = sum(v
                   for prov in REPORT.get("charges_real_by_month",
                                          {}).values()
                   for ym, v in prov.items() if ym in meses_con_datos)
        self.assertAlmostEqual(cash, heads["cost_cash"]["value"], places=2)
        self.assertAlmostEqual(eff / cash, heads["leverage"]["value"], places=2)

    def test_toda_headline_tiene_provenance(self):
        """FPA-003: reported o assumed, nunca vacío."""
        for h in viz.build_headlines(REPORT):
            self.assertIn(h["provenance"], ("reported", "assumed"), h["key"])

    def test_outcome_sin_datos_es_na_con_razon(self):
        """FPA-008: n/a con razón, nunca vacío ni cero inventado."""
        heads = viz.build_headlines(REPORT)
        outcome = [h for h in heads if h["key"] == "outcome"]
        if not outcome:
            self.fail("falta el headline outcome")
        h = outcome[0]
        if h["value"] is None:
            self.assertTrue(h["reason"], "n/a sin razón")
            self.assertEqual("n/a", h["display"])

    def test_outcome_con_datos_de_commits(self):
        rep = json.loads(json.dumps(REPORT))
        rep["monthly"]["2026-06"]["outcomes_by_project"] = {
            "charly-wai": {"commits": 10, "releases": 1}}
        heads = {h["key"]: h for h in viz.build_headlines(rep)}
        self.assertIsNotNone(heads["outcome"]["value"])
        self.assertIsNone(heads["outcome"]["reason"])

    def test_interpretacion_de_una_linea(self):
        for h in viz.build_headlines(REPORT):
            if h["value"] is not None:
                self.assertTrue(h["interp"], h["key"])
                self.assertNotIn("\n", h["interp"])

    def test_headline_na_cuando_no_hay_datos(self):
        rep = _mini_report()
        rep["monthly"]["2026-06"]["interactions"] = 0
        heads = {h["key"]: h for h in viz.build_headlines(rep)}
        self.assertIsNone(heads["cost_per_1k"]["value"])
        self.assertTrue(heads["cost_per_1k"]["reason"])


class TestClaims(unittest.TestCase):
    """FPA-009: claim solo con métrica nombrada + threshold visibles."""

    def test_claim_coste_por_1k_vs_objetivo(self):
        claims = viz.build_claims(REPORT, CONFIG)
        self.assertTrue(claims, "se esperaba al menos un claim computable")
        for c in claims:
            self.assertIsNotNone(c["metric_value"])
            self.assertIsNotNone(c["threshold"])
            self.assertTrue(c["metric_name"])

    def test_claim_umbral_del_config_es_assumed(self):
        claims = viz.build_claims(REPORT, CONFIG)
        c = claims[0]
        self.assertEqual(CONFIG["budgets"]["target_per_1k"], c["threshold"])
        self.assertEqual("assumed", c["threshold_provenance"])

    def test_claim_sin_metrica_no_se_renderiza(self):
        rep = _mini_report()
        rep["monthly"]["2026-06"]["interactions"] = 0
        self.assertEqual([], viz.build_claims(rep, CONFIG))


class TestSchemaValidation(unittest.TestCase):
    """FPA-006: validación stdlib-only, exit non-zero, lista de campos fallidos."""

    def test_reporte_real_valido(self):
        schema = json.loads((REPO / "specs" / "usage-report-v3.schema.json").read_text())
        self.assertEqual([], viz.validate_against_schema(REPORT, schema))

    def test_campo_mal_tipado_detectado(self):
        schema = json.loads((REPO / "specs" / "usage-report-v3.schema.json").read_text())
        rep = _mini_report(total_interactions="mucho")
        errors = viz.validate_against_schema(rep, schema)
        self.assertTrue(any("total_interactions" in e for e in errors), errors)

    def test_campo_requerido_ausente_detectado(self):
        schema = json.loads((REPO / "specs" / "usage-report-v3.schema.json").read_text())
        rep = _mini_report()
        del rep["metadata"]["date_range"]
        errors = viz.validate_against_schema(rep, schema)
        self.assertTrue(any("date_range" in e for e in errors), errors)

    def test_enum_invalido_detectado(self):
        schema = json.loads((REPO / "specs" / "usage-report-v3.schema.json").read_text())
        rep = _mini_report(filter="todo")
        errors = viz.validate_against_schema(rep, schema)
        self.assertTrue(any("filter" in e for e in errors), errors)

    def test_main_reporte_invalido_exit_nonzero(self):
        broken = _mini_report(total_interactions=-5)
        broken_path = Path("/tmp/broken_fpa.json")
        broken_path.write_text(json.dumps(broken))
        try:
            err = io.StringIO()
            with redirect_stderr(err):
                code = viz.main(["--report", str(broken_path), "--out",
                                 "/tmp/broken_fpa.html"])
            self.assertNotEqual(0, code)
            out = err.getvalue()
            self.assertIn("total_interactions", out)
        finally:
            broken_path.unlink(missing_ok=True)

    def test_main_reporte_valido_exit_cero(self):
        out = Path("/tmp/fpa_ok_test.html")
        try:
            code = viz.main(["--out", str(out)])
            self.assertEqual(0, code)
            self.assertTrue(out.exists())
        finally:
            out.unlink(missing_ok=True)


class TestRender(unittest.TestCase):
    """HTML: header, tema, formatos, provenance, placeholder-check."""

    def test_header_generacion_y_tracker(self):
        """FPA-005: fecha de generación y versión del tracker en el header."""
        html = _rendered()
        self.assertIn("2026-09-19 12:00", html)
        self.assertIn("Tracker", html)

    def test_tracker_version_ausente_na_con_razon(self):
        """FPA-005/008: si el reporte no registra versión, n/a con razón."""
        html = viz.render_html(REPORT, CONFIG, generated="2026-09-19 12:00")
        self.assertTrue("no registrada" in html or "tracker_version" in html)

    def test_tema_claroy_oscuro(self):
        """Tema: decisión D2 del change update-fpa-dashboard-ux — SOLO tema
        claro (Corporate Infographics silver), sin dark mode. El assert
        histórico de prefers-color-scheme quedó obsoleto con D2."""
        html = _rendered()
        self.assertNotIn("prefers-color-scheme", html)  # D2: sin dark mode
        self.assertIn("--bg", html)  # tokens del tema compartido presentes

    def test_tabular_nums_en_css(self):
        """FPA-095: cifras tabulares en tablas/cards."""
        self.assertIn("tabular-nums", _rendered())

    def test_fmt_usd(self):
        self.assertEqual("$1,234.50", viz.fmt_usd(1234.5))
        self.assertEqual("$0.27", viz.fmt_usd(0.273))
        self.assertEqual("$3,810.94", viz.fmt_usd(3810.94))

    def test_fmt_int_miles(self):
        self.assertEqual("139,735", viz.fmt_int(139735))
        self.assertEqual("1,397,354", viz.fmt_int(1397354))
        self.assertEqual("42", viz.fmt_int(42))

    def test_figuras_con_provenance_tag(self):
        """FPA-003: toda figura lleva tag reported/assumed via envoltorio."""
        html = _rendered()
        self.assertIn('data-provenance="reported"', html)
        self.assertIn('data-provenance="assumed"', html)
        # ninguna cifra sin tag: los spans de valor siempre van envueltos
        self.assertNotIn('<span class="fig">', html.replace(
            '<span class="fig" data-provenance="reported">', "")
            .replace('<span class="fig" data-provenance="assumed">', ""))

    def test_mes_parcial_marcado_en_html(self):
        """FPA-004: marker 'n/total' + 'parcial' en el HTML (coffe-6i8: n se
        deriva del reporte en vez de clavar 19/30)."""
        end = REPORT["metadata"]["date_range"]["end"]
        meta = viz.build_months(REPORT)
        sep = [m for m in meta if m["ym"] == end[:7]][0]
        html = _rendered()
        self.assertIn(f"{sep['elapsed']}/{sep['total_days']}", html)
        self.assertIn("parcial", html.lower())

    def test_resumen_no_vacio(self):
        """FPA-108: el resumen ejecutivo nunca queda vacío."""
        html = _rendered()
        self.assertGreaterEqual(html.count('class="headline'), 3)

    def test_check_placeholders_detecta_vacio(self):
        """FPA-108: placeholder-check falla si hay placeholder sin valor."""
        self.assertEqual([], viz.check_placeholders(_rendered()))
        html_vacio = '<section id="summary"><div class="ph"></div></section>'
        problems = viz.check_placeholders(html_vacio)
        self.assertTrue(problems, "no detectó placeholder vacío")

    def test_claim_renderizado_con_metrica_y_threshold(self):
        """FPA-009: métrica y threshold visibles junto al claim."""
        html = _rendered()
        self.assertIn("umbral", html.lower())
        self.assertIn("por 1k", html.lower())

    def test_html_autocontenido_sin_recursos_externos(self):
        """FPA-001: un solo HTML autocontenido, sin <script src>/<link href>."""
        html = _rendered()
        self.assertNotIn("<script src=", html)
        self.assertNotIn('<link rel="stylesheet"', html)

    def test_sin_js_de_calculo(self):
        """design.md: maths pre-calculadas en Python; JS solo re-escala (F2+)."""
        html = _rendered()
        # el modelo va embebido como JSON, no hay lógica de negocio en JS
        self.assertIn('id="fpa-model"', html)

    def test_determinismo_salvo_timestamp(self):
        """FPA-104 (semilla): mismo input → mismo HTML salvo fecha de generación."""
        a = viz.render_html(REPORT, CONFIG, generated="T")
        b = viz.render_html(REPORT, CONFIG, generated="T")
        self.assertEqual(a, b)
        self.assertNotEqual(a, viz.render_html(REPORT, CONFIG, generated="T2"))


FROZEN_REPORT = json.loads((REPO / "tests" / "fixtures"
                            / "usage-report-frozen.json").read_text())


class TestGolden(unittest.TestCase):
    """FPA-100: golden de las agregaciones base (fase 1).

    coffe-xvt: corre contra el dataset congelado (fixtures), no contra el
    dataset vivo del nightly — mismo dataset, misma regresión, sin drift
    diario. Regenerar: REGEN_GOLDEN=1 python3 tests/test_fpa.py"""

    def test_dataset_congelado_existe_y_es_valido(self):
        """El pin existe y es un reporte válido (guarda del fixture)."""
        schema = json.loads((REPO / "specs" / "usage-report-v3.schema.json").read_text())
        self.assertEqual([], viz.validate_against_schema(FROZEN_REPORT, schema))

    def test_golden_agregaciones(self):
        model = viz.build_model(FROZEN_REPORT, CONFIG)
        model.pop("usage", None)  # F5 (coffe-lat.6): golden propio en test_fpa_f5
        if REGEN_GOLDEN:
            GOLDEN.write_text(json.dumps(model, indent=2, ensure_ascii=False,
                                         sort_keys=True) + "\n")
            self.skipTest("golden regenerado; revisar diff antes de aceptar")
        if not GOLDEN.exists():
            self.fail("Falta golden; corre REGEN_GOLDEN=1 python3 tests/test_fpa.py")
        expected = json.loads(GOLDEN.read_text())
        self.assertEqual(expected, model)


if __name__ == "__main__":
    unittest.main()
