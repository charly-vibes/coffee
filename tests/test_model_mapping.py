#!/usr/bin/env python3
"""
test_model_mapping.py — coffe-dnx.1: mapeo modelo → familia/versión desde
config (bloque model_mapping) + visibilidad de default_rates fallbacks.

Test-first del ticket coffe-dnx.1 (casos rojo):
- model_details("deepseek-v4-pro") → ("deepseek", "v4-pro") (antes pisaba
  a "v4-flash": facturación a tarifa flash).
- model_details("z-ai/glm-5.3-flash") → familia glm (antes "other").
- estimate_cost() de una versión sin tarifa incrementa
  default_rates_fallbacks (y avisa a stderr una vez por familia/versión).
- Un modelo nuevo desconocido sigue al fallback ("other", ...) sin crashear.
"""

import contextlib
import importlib.util
import io
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TRACKER_PATH = REPO / "scripts" / "usage-tracker.py"


def load_tracker():
    spec = importlib.util.spec_from_file_location("usage_tracker_model_mapping",
                                                  TRACKER_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ut = load_tracker()


ROW_DEFAULTS = {
    "source": "test", "tool": "test", "model_raw": "x",
    "model_family": "other", "model_version": "unknown",
    "project": "charly-coffee", "ts": "2026-05-10T14:23:00+00:00",
    "input_tokens": 1000, "output_tokens": 500,
    "cache_read_tokens": 0, "cache_write_tokens": 0,
    "cost_effective": 0.0,
}


def row(overrides=None):
    """Row sintético con el shape exacto que producen los extractores."""
    o = dict(ROW_DEFAULTS)
    o.update(overrides or {})
    ts = o.pop("ts")
    return {
        **o,
        "timestamp": ts,
        "hour": ut.hour_key(ut.parse_ts(ts)),
    }


class TestModelMapping(unittest.TestCase):

    def test_deepseek_versions_distinct(self):
        # Rojo del ticket: antes ("deepseek", "v4-flash") para todos.
        self.assertEqual(ut.model_details("deepseek-v4-pro"),
                         ("deepseek", "v4-pro"))
        self.assertEqual(ut.model_details("deepseek-v4.1-pro"),
                         ("deepseek", "v4.1-pro"))
        self.assertEqual(ut.model_details("deepseek-v4-flash"),
                         ("deepseek", "v4-flash"))
        # deepseek sin versión conocida → tarifa v4-flash (comportamiento
        # preexistente, test_tracker.py:109 — no regresa).
        self.assertEqual(ut.model_details("deepseek-v4"),
                         ("deepseek", "v4-flash"))

    def test_glm_family(self):
        # Rojo del ticket: antes ("other", "z-ai/glm-5.3-flash").
        self.assertEqual(ut.model_details("z-ai/glm-5.3-flash"),
                         ("glm", "glm-5.3-flash"))
        self.assertEqual(ut.model_details("glm-5.3-flash"),
                         ("glm", "glm-5.3-flash"))

    def test_kimi_k25_vs_k2(self):
        self.assertEqual(ut.model_details("kimi-k2.5"), ("kimi", "k2.5"))
        self.assertEqual(ut.model_details("moonshotai/kimi-k2.5"), ("kimi", "k2.5"))
        self.assertEqual(ut.model_details("kimi-k2"), ("kimi", "k2"))

    def test_claude_opus_48(self):
        self.assertEqual(ut.model_details("claude-opus-4.8"),
                         ("claude", "opus-4.8"))
        self.assertEqual(ut.model_details("claude-opus-4-7"),
                         ("claude", "opus-4.7"))

    def test_new_families_covered(self):
        self.assertEqual(ut.model_details("MiniMax-M2.7"), ("minimax", "m2.7"))
        self.assertEqual(ut.model_details("MiniMax-M3.1-Flash"),
                         ("minimax", "m3.1-flash"))
        self.assertEqual(ut.model_details("qwen3.8-flash"),
                         ("qwen", "qwen3.8-flash"))
        self.assertEqual(ut.model_details("qwen3.8-max"), ("qwen", "qwen3.8-max"))
        self.assertEqual(ut.model_details("mistral-large"), ("mistral", "mistral"))
        self.assertEqual(ut.model_details("devstral-small"), ("mistral", "devstral"))
        self.assertEqual(ut.model_details("openrouter/deepseek-v4-pro:free"),
                         ("openrouter", "free"))

    def test_unknown_model_falls_back_without_crash(self):
        fam, ver = ut.model_details("modelo-marca-nueva-9000")
        self.assertEqual(fam, "other")
        self.assertEqual(ver, "modelo-marca-nueva-9000"[:20])

    def test_estimate_cost_fallback_increments_counter(self):
        ut.reset_default_rates_fallbacks()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            cost = ut.estimate_cost("glm", "glm-5.3-flash", 1000, 1000)
        self.assertGreater(cost, 0.0)  # default_rates, no KeyError
        snap = ut.default_rates_fallbacks()
        self.assertEqual(snap.get("glm", {}).get("glm-5.3-flash"), 1)
        self.assertIn("WARNING: default_rates fallback", err.getvalue())

    def test_estimate_cost_warning_once_per_version(self):
        ut.reset_default_rates_fallbacks()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            ut.estimate_cost("glm", "glm-5.3-flash", 10, 10)
            ut.estimate_cost("glm", "glm-5.3-flash", 10, 10)
        self.assertEqual(err.getvalue().count("WARNING: default_rates fallback"), 1)

    def test_estimate_cost_known_version_no_fallback(self):
        ut.reset_default_rates_fallbacks()
        ut.estimate_cost("claude", "sonnet-4.6", 1000, 1000)
        self.assertEqual(ut.default_rates_fallbacks(), {})


class TestMappingCoverage(unittest.TestCase):

    def test_metadata_model_mapping_coverage(self):
        rows = [
            row({"model_raw": "z-ai/glm-5.3-flash", "model_family": "glm",
                 "model_version": "glm-5.3-flash",
                 "input_tokens": 900, "output_tokens": 100}),
            row({"ts": "2026-06-01T09:00:00+00:00",
                 "model_raw": "claude-sonnet-4-6",
                 "model_family": "claude", "model_version": "sonnet-4.6"}),
            row({"model_raw": "coso-raro", "model_family": "other",
                 "model_version": "coso-raro",
                 "input_tokens": 100, "output_tokens": 100}),
        ]
        report = ut.aggregate(rows, [])
        cov = report["metadata"]["model_mapping_coverage"]
        self.assertEqual(set(cov), {"by_family", "tokens_total", "tokens_other",
                                    "tokens_other_pct",
                                    "default_rates_fallbacks", "mapping_source"})
        self.assertEqual(cov["by_family"]["glm"]["tokens"], 1000)
        self.assertEqual(cov["by_family"]["other"]["rows"], 1)
        # in+out+cache de las 3 filas, contadas una vez:
        # 1000 (glm) + 1500 (claude) + 200 (other) = 2700
        self.assertEqual(cov["tokens_total"], 2700)
        self.assertEqual(cov["tokens_other"], 200)
        self.assertEqual(cov["tokens_other_pct"], 7.41)
        # glm/glm-5.3-flash no tiene tarifa → fila con default_rates
        self.assertEqual(cov["default_rates_fallbacks"], 1)

    def test_coverage_no_tokens_is_zero_pct(self):
        report = ut.aggregate([], [])
        cov = report["metadata"]["model_mapping_coverage"]
        self.assertEqual(cov["tokens_other_pct"], 0.0)
        self.assertEqual(cov["by_family"], {})


if __name__ == "__main__":
    unittest.main()
