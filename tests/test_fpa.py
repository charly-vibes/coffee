#!/usr/bin/env python3
"""
test_fpa.py — tests de scripts/viz-fpa.py (F1: base + resumen ejecutivo)

Cubre el alcance del ticket coffe-lat.2 (FPA-001…009, 093, 095, 108):
- validación de schema stdlib-only con exit non-zero y campos fallidos (FPA-006)
- mes parcial con días transcurridos/total (FPA-004)
- separación estricta efectivo/cash, jamás sumados (FPA-002)
- claims solo con métrica + threshold visibles (FPA-009)
- golden test de las agregaciones base (FPA-100, fase 1): REGEN_GOLDEN=1
  contra el dataset congelado tests/fixtures/usage-report-frozen.json
  (coffe-xvt: el golden ya no amanece rojo cuando el nightly regenera el
  dataset vivo; regen del golden solo ante cambios de lógica/schema)

(coffe-kav.5: los tests de headlines, render y vista Modelos viven ahora
en test_fpa_headlines.py, test_fpa_render.py y test_fpa_models.py.)
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

    def test_campos_malos_detectados(self):
        """Mal tipado, requerido ausente y enum inválido se listan campo por
        campo (FPA-006)."""
        schema = json.loads((REPO / "specs" / "usage-report-v3.schema.json").read_text())
        rep_mal = _mini_report(total_interactions="mucho")
        errors = viz.validate_against_schema(rep_mal, schema)
        self.assertTrue(any("total_interactions" in e for e in errors), errors)
        rep = _mini_report()
        del rep["metadata"]["date_range"]
        errors = viz.validate_against_schema(rep, schema)
        self.assertTrue(any("date_range" in e for e in errors), errors)
        errors = viz.validate_against_schema(_mini_report(filter="todo"), schema)
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
