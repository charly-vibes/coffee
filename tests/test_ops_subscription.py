"""Tests de ops.subscription (scripts/agent_metrics_ops_subscription.py) —
epic coffe-a5v, hijo coffe-9fp: utilización de suscripción sobre insumos
sintéticos, sin logs reales.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import agent_metrics_ops_subscription as sub  # noqa: E402

CONFIG = {
    "subscriptions": {
        "claude-cli": [
            {"start": "2026-04-01", "end": "2026-04-30",
             "label": "Max $100/mes", "monthly_fee": 100.0},
        ],
    },
}
MAPPING = sub.compile_mapping([
    {"priority": 10, "pattern": "claude", "family": "claude-cli", "version": "v"},
    {"priority": 20, "pattern": "gpt", "family": "codex", "version": "v"},
])


def row(source, day, model, cost):
    return {"source": source, "day": day, "model": model, "cost_effective": cost}


class TestUtilization(unittest.TestCase):
    def test_utilization_math(self):
        spend = [row("claude-cli", "2026-04-10", "claude-sonnet", 25.0),
                 row("claude-cli", "2026-04-20", "claude-sonnet", 50.0)]
        out = sub.compute_subscription(spend, CONFIG, MAPPING)
        p = out["periods"][0]
        self.assertEqual(p["cost_imputed"], 75.0)
        self.assertEqual(p["utilization"], 0.75)
        # headroom_daily = (100 - 75) / 30 días
        self.assertAlmostEqual(p["headroom_daily"], 25.0 / 30, places=2)
        self.assertNotIn("reason", p)

    def test_fee_zero_null_with_reason(self):
        cfg = {"subscriptions": {"codex": [
            {"start": "2026-10-06", "end": "2026-11-06", "label": "trial", "monthly_fee": 0}]}}
        spend = [row("codex", "2026-10-10", "gpt-5", 12.0)]
        out = sub.compute_subscription(spend, cfg, MAPPING)
        p = out["periods"][0]
        self.assertIsNone(p["utilization"])
        self.assertIn(sub.NO_FEE_REASON, p["reason"])
        self.assertEqual(p["cost_imputed"], 12.0)

    def test_absent_config_lists_all_models(self):
        spend = [row("claude-cli", "2026-04-10", "claude-sonnet", 5.0),
                 row("codex", "2026-04-11", "gpt-5", 3.0)]
        out = sub.compute_subscription(spend, {}, MAPPING)
        self.assertEqual(out["periods"], [])
        self.assertEqual(len(out["models_without_period"]), 2)
        self.assertTrue(all(m["reason"] == sub.NO_CONFIG_REASON
                            for m in out["models_without_period"]))

    def test_period_filter_respected(self):
        spend = [row("claude-cli", "2026-04-05", "claude-sonnet", 10.0),
                 row("claude-cli", "2026-05-05", "claude-sonnet", 99.0),
                 row("codex", "2026-04-05", "gpt-5", 7.0)]
        out = sub.compute_subscription(spend, CONFIG, MAPPING)
        self.assertEqual(out["periods"][0]["cost_imputed"], 10.0)

    def test_models_without_period_populated(self):
        spend = [row("claude-cli", "2026-04-05", "claude-sonnet", 1.0),
                 row("amp", "2026-04-05", "amp", 2.0),
                 row("gemini-cli", "2026-04-05", "gemini", 3.0)]
        out = sub.compute_subscription(spend, CONFIG, MAPPING)
        unmapped = {m["model"] for m in out["models_without_period"]}
        self.assertIn("amp", unmapped)
        self.assertIn("gemini", unmapped)
        self.assertNotIn("claude-sonnet", unmapped)
        self.assertTrue(all(m["reason"] == sub.NO_MODEL_REASON
                            for m in out["models_without_period"]))

    def test_provenance_and_coverage_emitted(self):
        spend = [row("claude-cli", "2026-04-01", "claude-sonnet", 10.0),
                 row("claude-cli", "2026-04-02", "claude-sonnet", 10.0)]
        out = sub.compute_subscription(spend, CONFIG, MAPPING)
        self.assertEqual(out["provenance"], {"subscription": "derived"})
        cov = out["coverage"][0]
        # 2 días con gasto de 30 del período → 6.7%
        self.assertAlmostEqual(cov["coverage_pct"], 100.0 * 2 / 30, places=1)
        self.assertIsNone(cov["reason"])

    def test_headroom_null_without_bounds(self):
        cfg = {"subscriptions": {"claude-cli": [
            {"start": None, "end": None, "label": "sin fechas", "monthly_fee": 50.0}]}}
        out = sub.compute_subscription([], cfg, MAPPING)
        p = out["periods"][0]
        self.assertIsNone(p["headroom_daily"])
        self.assertIn(sub.NO_BOUNDS_REASON, p["reason"])


if __name__ == "__main__":
    unittest.main()
