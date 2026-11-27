"""Tests del bloque ops (scripts/agent_metrics_ops.py) — slices del epic coffe-a5v.

Hijo 1 (coffe-9ia): ops.latency — p50/p90/p95 sobre TODA la población de turnos
con duración, por fuente y por proveedor (familia del model_mapping de config),
con coverage_pct y null-con-razón para fuentes sin señal.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import agent_metrics_ops as ops  # noqa: E402


def T(ts, role="assistant", model="m", dur_s=None, stop="stop"):
    return {"ts": ts, "role": role, "model": model, "stop": stop, "dur_s": dur_s}


class TestPercentile(unittest.TestCase):
    def test_exact_interpolation(self):
        self.assertEqual(ops.percentile([10.0, 20.0, 30.0, 40.0], 0.5), 25.0)
        self.assertEqual(ops.percentile([10.0], 0.95), 10.0)
        self.assertEqual(ops.percentile([1.0, 2.0], 0.0), 1.0)
        self.assertEqual(ops.percentile([1.0, 2.0], 1.0), 2.0)

    def test_single_and_empty(self):
        self.assertEqual(ops.percentile([7.0], 0.9), 7.0)
        self.assertIsNone(ops.percentile([], 0.5))


class TestLatencyStats(unittest.TestCase):
    def test_coverage(self):
        st = ops.latency_stats([1000.0, 2000.0, 3000.0, 4000.0], turns_total=5)
        self.assertEqual(st["n"], 4)
        self.assertEqual(st["p50_ms"], 2500.0)
        self.assertEqual(st["coverage_pct"], 80.0)
        self.assertNotIn("reason", st)

    def test_no_duration_signal(self):
        st = ops.latency_stats([], turns_total=7)
        self.assertEqual(st["n"], 0)
        self.assertIsNone(st["p50_ms"])
        self.assertIsNone(st["p90_ms"])
        self.assertIsNone(st["p95_ms"])
        self.assertEqual(st["coverage_pct"], 0.0)
        self.assertTrue(st["reason"])


class TestProviderMapping(unittest.TestCase):
    def test_priority_order_wins(self):
        mapping = ops.compile_mapping([
            {"priority": 20, "pattern": "glm", "family": "glm-other", "version": "x"},
            {"priority": 10, "pattern": "glm-5\\.3", "family": "zai", "version": "5.3-flash"},
        ])
        self.assertEqual(ops.provider_for("z-ai/glm-5.3-flash", mapping), "zai")

    def test_no_match_is_other(self):
        mapping = ops.compile_mapping([
            {"priority": 10, "pattern": "glm", "family": "zai", "version": "v"},
        ])
        self.assertEqual(ops.provider_for("modelo-desconocido", mapping), "other")

    def test_compile_mapping_fails_loud(self):
        with self.assertRaises(ValueError):
            ops.compile_mapping([{"priority": 10, "pattern": "x"}])


class TestLatencyBlock(unittest.TestCase):
    MAPPING = [
        {"priority": 10, "pattern": "glm-5\\.3", "family": "zai", "version": "5.3-flash"},
        {"priority": 20, "pattern": "kimi", "family": "kimi", "version": "coding"},
    ]

    def test_pi_grouped_by_model(self):
        pi = [T(1, model="z-ai/glm-5.3-flash", dur_s=1.0),
              T(2, model="z-ai/glm-5.3-flash", dur_s=3.0),
              T(3, model="z-ai/glm-5.3-flash"),
              T(4, model="kimi-for-coding", dur_s=2.0),
              T(5, role="user", model=None)]
        cl = [T(6, model="claude-sonnet-4-5"), T(7, model="claude-sonnet-4-5")]
        block = ops.latency_block(pi_turns=pi, claude_turns=cl,
                                  mapping=ops.compile_mapping(self.MAPPING))

        pi_entry = block["sources"]["pi"]
        self.assertEqual(pi_entry["turns_with_duration"], 3)
        self.assertEqual(pi_entry["coverage_pct"], 75.0)
        glm = pi_entry["models"]["z-ai/glm-5.3-flash"]
        self.assertEqual(glm["n"], 2)
        self.assertEqual(glm["p50_ms"], 2000.0)
        self.assertEqual(glm["turns_total"], 3)
        self.assertEqual(block["providers"]["zai"]["p50_ms"], 2000.0)

        # claude-cli: sin señal de duración -> null con razón, coverage 0
        cl_entry = block["sources"]["claude-cli"]
        self.assertEqual(cl_entry["turns_with_duration"], 0)
        self.assertEqual(cl_entry["coverage_pct"], 0.0)
        self.assertTrue(cl_entry["reason"])
        cl_model = cl_entry["models"]["claude-sonnet-4-5"]
        self.assertIsNone(cl_model["p95_ms"])
        self.assertEqual(cl_model["n"], 0)
        self.assertTrue(cl_model["reason"])

        self.assertEqual(block["sources"]["pi"]["models"]["kimi-for-coding"]["n"], 1)

    def test_provenance_and_coverage_shape(self):
        block = ops.latency_block(pi_turns=[], claude_turns=[],
                                  mapping=ops.compile_mapping(self.MAPPING))
        self.assertEqual(block["provenance"]["latency"], "measured")
        self.assertIsInstance(block["coverage"], list)


class TestMergeOps(unittest.TestCase):
    def test_preserves_existing_keys(self):
        doc = {"metadata": {}, "ops": {"fallback": {"events": 3}}}
        block = ops.latency_block(pi_turns=[], claude_turns=[],
                                  mapping=ops.compile_mapping(
                                      [{"priority": 10, "pattern": "x",
                                        "family": "f", "version": "v"}]))
        ops.merge_ops(doc, {"latency": block})
        self.assertEqual(doc["ops"]["fallback"]["events"], 3)
        self.assertEqual(doc["ops"]["latency"]["provenance"]["latency"], "measured")


if __name__ == "__main__":
    unittest.main()
