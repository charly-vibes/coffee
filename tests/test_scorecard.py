#!/usr/bin/env python3
"""
test_scorecard.py — tests del bloque model_scorecard[] (coffe-dnx.8).

Casos test-first del ticket:
- Rojo: cost_weighted_per_1m con 90% cache_read difiere de (y es mayor que)
  cost/tokens_totales; el test falla si la implementación usa tokens totales.
- Modelo de la cohorte sin datos en una ventana ⇒ entrada null + razón
  (nunca 0, patrón FPA-008).
- rule_verdict = "datos_insuficientes" cuando falta cost_per_accepted_task.
- provenance presente en cada cifra del bloque; sin provenance el test falla.
- Consistencia: benchmark_cohort (config/fpa.json) == cohort
  (config/model_eval.json, coffe-dnx.9).
"""

import contextlib
import importlib.util
import json
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TRACKER_PATH = REPO / "scripts" / "usage-tracker.py"


def load_tracker():
    spec = importlib.util.spec_from_file_location("usage_tracker_scorecard",
                                                  TRACKER_PATH)
    m = importlib.util.module_from_spec(spec)
    with contextlib.redirect_stdout(__import__("io").StringIO()):
        spec.loader.exec_module(m)
    return m


ut = load_tracker()


SROW_DEFAULTS = {
    "source": "test", "tool": "pi", "model_raw": "z-ai/glm-5.3-flash",
    "model_family": "glm", "model_version": "glm-5.3-flash",
    "project": "charly-coffee", "ts": "2026-11-10T14:23:00+00:00",
    "input_tokens": 1000, "output_tokens": 500,
    "cache_read_tokens": 0, "cache_write_tokens": 0,
    "cost_effective": 0.01, "duration_ms": None, "stop_reason": "toolUse",
    "tool_calls": 1, "tool_errors": 0,
}


def srow(overrides=None):
    """Row sintético con el shape exacto que producen los extractores."""
    o = dict(SROW_DEFAULTS)
    o.update(overrides or {})
    ts = o.pop("ts")
    model_raw = o["model_raw"]
    return {
        **o,
        "timestamp": ts,
        "hour": ut.hour_key(ut.parse_ts(ts)),
        "model_chain": [model_raw], "model_change_idx": None,
    }


def glm_rows_90pct_cache():
    """Fixture del ticket: 90% de los tokens son cache_read.

    10 turnos: in=1000, out=500, cache_read=13500 por turno (9 de cada
    10 tokens = cache_read). Usa deepseek-v4-flash porque es el único
    modelo de la cohorte con tarifa explícita en model_pricing
    (glm/qwen van a default_rates). Coste calculado con el pricing real
    del config vía estimate_cost() para que el fixture sea
    auto-consistente.
    """
    rows = []
    for i in range(10):
        rows.append(srow({
            "ts": f"2026-11-1{i % 10}T10:00:00+00:00",
            "model_raw": "deepseek/deepseek-v4-flash",
            "model_family": "deepseek", "model_version": "v4-flash",
            "input_tokens": 1000, "output_tokens": 500,
            "cache_read_tokens": 13500,
            "duration_ms": 1500 + i, "stop_reason": "toolUse",
            "cost_effective": None,  # recalculado abajo con el pricing config
        }))
    rates = ut._pricing_for().get("deepseek", {}).get("v4-flash")
    assert rates is not None, "deepseek/v4-flash debe tener tarifa en config"
    for r in rows:
        r["cost_effective"] = ut.estimate_cost(
            "deepseek", "v4-flash", r["input_tokens"], r["output_tokens"],
            cache_read=r["cache_read_tokens"])
    return rows


def build_scorecard(rows, spend_rows=None):
    if spend_rows is None:
        spend_rows = ut._session_spend(rows)
    return ut._model_scorecard(rows, spend_rows)


class TestCacheNormalization(unittest.TestCase):
    """Rojo del ticket: la normalización por composición de tokens."""

    def test_cost_weighted_differs_from_and_exceeds_naive(self):
        rows = glm_rows_90pct_cache()
        entries, _meta = build_scorecard(rows)
        todo = [e for e in entries
                if e["window"] == "todo" and e["model"] == "deepseek-v4-flash"]
        self.assertEqual(len(todo), 1)
        data = todo[0]["data"]
        self.assertIsNotNone(data)
        weighted = data["cost_weighted_per_1m"]
        naive = data["cost_per_1m_total_tokens"]
        self.assertIsNotNone(weighted)
        self.assertIsNotNone(naive)
        # 90% cache_read a ~0.1x del input ⇒ el $/Mtok normalizado es
        # MAYOR que el naive sobre tokens totales. Con la implementación
        # rota (denominador = tokens totales) weighted == naive y falla.
        self.assertGreater(weighted, naive)
        self.assertNotAlmostEqual(weighted, naive)
        # el cache_factor explícito es < 1 (nunca un 1.0 silencioso)
        self.assertIsNotNone(data["cache_factor"])
        self.assertLess(data["cache_factor"], 1.0)


class TestMissingModelData(unittest.TestCase):
    """Modelo de la cohorte sin datos en la ventana ⇒ null + razón."""

    def test_model_without_data_is_null_with_reason(self):
        rows = glm_rows_90pct_cache()
        entries, _meta = build_scorecard(rows)
        # qwen3.8-max no aparece en el fixture
        for e in entries:
            if e["model"] == "qwen3.8-max":
                self.assertIsNone(e["data"],
                                  "nunca 0 fake: data null (FPA-008)")
                self.assertTrue(e["reason"])
                self.assertIsInstance(e["nulls"], list)
                return
        self.fail("qwen3.8-max debe aparecer en el scorecard (cohorte completa)")

    def test_zero_rows_never_zero_cost(self):
        rows = glm_rows_90pct_cache()
        entries, _meta = build_scorecard(rows)
        empty = [e for e in entries if e["data"] is None]
        self.assertTrue(empty)
        for e in empty:
            self.assertTrue(e["reason"])


class TestRuleVerdict(unittest.TestCase):
    """rule_verdict según model_decision_rule."""

    def test_datos_insuficientes_when_cost_per_accepted_task_missing(self):
        rows = glm_rows_90pct_cache()
        # sin data/agent_metrics.json cruzable ⇒ cost_per_accepted_task null
        # (el path real no contiene tareas de este fixture sintético)
        spend = ut._session_spend(rows)
        entries, meta = build_scorecard(rows, spend)
        self.assertIsNotNone(meta["rule_verdict"])
        missing = [e for e in entries if e["window"] == "todo"
                   and (e["data"] or {}).get("cost_per_accepted_task") is None]
        if missing:
            self.assertEqual(meta["rule_verdict"], "datos_insuficientes")
            self.assertTrue(meta["rule_verdict_reason"])
        self.assertIn(meta["rule_verdict"],
                      ("mantener", "cambiar", "datos_insuficientes"))

    def test_verdict_per_model_covers_cohort(self):
        rows = glm_rows_90pct_cache()
        _entries, meta = build_scorecard(rows)
        pm = meta["rule_verdict_per_model"]
        self.assertIsNotNone(pm)
        self.assertEqual(set(pm), set(meta["models"]))
        for m, v in pm.items():
            self.assertIn(v["verdict"],
                          ("mantener", "cambiar", "datos_insuficientes"))
            self.assertTrue(v["reason"])


class TestProvenance(unittest.TestCase):
    """Provenance en cada cifra del bloque; sin provenance el test falla."""

    FIGURES = ("interactions", "cost_effective", "cost_weighted_per_1m",
               "cost_per_1m_total_tokens", "cache_factor", "cache_share",
               "tokens", "cost_per_task", "cost_per_accepted_task",
               "latency", "stop_reasons", "aborts_per_100_turns",
               "tool_errors_per_100_turns", "model_switches",
               "subscription_vs_api")

    def test_provenance_on_every_figure(self):
        rows = glm_rows_90pct_cache()
        entries, _meta = build_scorecard(rows)
        for e in entries:
            data = e["data"]
            if data is None:
                continue
            prov = data.get("provenance")
            self.assertIsInstance(prov, dict, e["model"])
            for fig in self.FIGURES:
                self.assertIn(fig, prov,
                              f"falta provenance de {fig} en {e['model']}")
                self.assertIn(prov[fig], ("reported", "estimated", "assumed"),
                              f"provenance inválido para {fig} en {e['model']}")

    def test_nulls_have_reasons(self):
        rows = glm_rows_90pct_cache()
        entries, _meta = build_scorecard(rows)
        for e in entries:
            if e["data"] is None:
                continue
            for n in e["data"]["nulls"]:
                self.assertTrue(n.get("field"))
                self.assertTrue(n.get("reason"))


class TestCohortConsistency(unittest.TestCase):
    """coffe-dnx.9: benchmark_cohort (fpa.json) == cohort (model_eval.json)."""

    def test_benchmark_cohort_matches_model_eval(self):
        fpa = json.loads((REPO / "config" / "fpa.json").read_text())
        ev = json.loads((REPO / "config" / "model_eval.json").read_text())
        cohort_cfg = fpa.get("benchmark_cohort")
        self.assertIsNotNone(cohort_cfg)
        self.assertEqual(cohort_cfg.get("models"),
                         [m["name"] for m in ev.get("cohort", [])])
        refs = [m["name"] for m in ev.get("cohort", [])
                if m.get("role") == "reference"]
        self.assertEqual(cohort_cfg.get("reference"),
                         refs[0] if refs else None)

    def test_provisional_rule_flagged(self):
        fpa = json.loads((REPO / "config" / "fpa.json").read_text())
        rule = fpa.get("model_decision_rule") or {}
        self.assertIs(rule.get("provisional"), True,
                      "umbrales sin calibrar deben ir flageados "
                      "provisional: true (EXCL-003)")


class TestWindows(unittest.TestCase):
    """Scorecard por ventana todo/YTD/mes."""

    def test_three_windows_per_model(self):
        rows = glm_rows_90pct_cache()
        entries, _meta = build_scorecard(rows)
        by_model = {}
        for e in entries:
            by_model.setdefault(e["model"], []).append(e["window"])
        for model, wins in by_model.items():
            self.assertEqual(sorted(wins), ["mes", "todo", "ytd"], model)

    def test_ytd_is_subset_of_todo(self):
        rows = glm_rows_90pct_cache()
        # fila fuera del año del último dato ⇒ ytd < todo
        rows.append(srow({"ts": "2025-06-01T10:00:00+00:00",
                          "model_raw": "deepseek/deepseek-v4-flash",
                          "model_family": "deepseek",
                          "model_version": "v4-flash"}))
        entries, _meta = build_scorecard(rows)
        glm = {e["window"]: e for e in entries if e["model"] == "deepseek-v4-flash"}
        self.assertIsNotNone(glm["todo"]["data"])
        self.assertIsNotNone(glm["ytd"]["data"])
        self.assertLess(glm["ytd"]["data"]["interactions"],
                        glm["todo"]["data"]["interactions"])


if __name__ == "__main__":
    unittest.main()
