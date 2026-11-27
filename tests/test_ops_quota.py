"""Tests de ops.quota_events (scripts/agent_metrics_ops_quota.py) — coffe-t6e.

Sintéticos: sin dependencia de logs reales. Cubre match de firma,
case-insensitivity, sin-match, config ausente y el hilado de error_message
en agent_metrics_turns.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import agent_metrics_ops_quota as q  # noqa: E402
from agent_metrics_turns import _pi_assistant_turn  # noqa: E402

SIGS = [{"provider": "anthropic", "patterns": ["credit balance is too low",
                                               "billing"]},
        {"provider": "openai", "patterns": ["insufficient_quota"]}]


def A(ts, err=None, model="m"):
    return _pi_assistant_turn({"errorMessage": err, "model": model}, ts,
                              "s", model)


def block(pi_turns, sigs=SIGS, claude_turns=None):
    return q.quota_block({"pi": pi_turns, "claude-cli": claude_turns or []},
                         sigs)


class TestQuotaEvents(unittest.TestCase):
    def test_signature_match_makes_event(self):
        b = block([A("2026-01-01T00:00:00Z",
                     "Error: Credit balance is too low to continue")])
        self.assertEqual(len(b["events"]), 1)
        ev = b["events"][0]
        self.assertEqual(ev["provider"], "anthropic")
        self.assertEqual(ev["signature"], "credit balance is too low")
        self.assertEqual(ev["source"], "pi")
        self.assertEqual(ev["ts"], "2026-01-01T00:00:00Z")
        self.assertEqual(b["counts"], {"anthropic": 1})

    def test_case_insensitive(self):
        b = block([A("t", "INSUFFICIENT_QUOTA: limit reached")])
        self.assertEqual([e["provider"] for e in b["events"]], ["openai"])

    def test_no_match_no_event(self):
        b = block([A("t", "some unrelated error"),
                   A("t2", None)])
        self.assertEqual(b["events"], [])
        self.assertEqual(b["counts"], {})

    def test_config_absent_lists_pi(self):
        b = block([A("t", "Credit balance is too low")], sigs=None)
        reasons = {s["source"]: s["reason"] for s in b["sources_without_signature"]}
        self.assertEqual(reasons["pi"], "no_quota_signatures_configured")
        self.assertEqual(b["events"], [])

    def test_claude_always_without_signature(self):
        b = block([], claude_turns=[{"ts": "t", "role": "assistant"}])
        reasons = {s["source"]: s["reason"] for s in b["sources_without_signature"]}
        self.assertEqual(reasons["claude-cli"], "no_quota_signatures_in_source")

    def test_provenance_and_coverage(self):
        b = block([A("t", "billing error"), A("t2", None)])
        self.assertEqual(b["provenance"], {"quota_events": "derived"})
        cov = {c["source"]: c for c in b["coverage"]}
        self.assertEqual(cov["pi"]["coverage_pct"], 50.0)
        self.assertIsNone(cov["pi"]["reason"])
        self.assertEqual(cov["claude-cli"]["coverage_pct"], 0.0)
        self.assertTrue(cov["claude-cli"]["reason"])

    def test_schema_shape(self):
        b = block([A("t", "billing")])
        from schema_validate import validate_against_schema
        from agent_metrics_ops import SCHEMA_PATH
        import json
        base = Path(__file__).resolve().parent.parent / "data" / "agent_metrics.json"
        doc = json.loads(base.read_text(encoding="utf-8"))
        doc.setdefault("ops", {})
        doc["ops"]["latency"] = {"sources": {}, "providers": {},
                                 "provenance": {"latency": "measured"},
                                 "coverage": []}
        doc["ops"]["quota_events"] = b
        self.assertEqual(validate_against_schema(
            doc, json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))), [])


class TestTurnsErrorMessage(unittest.TestCase):
    def test_pi_assistant_threads_error_message(self):
        t = _pi_assistant_turn({"errorMessage": "quota exceeded"}, "ts", "s", "m")
        self.assertEqual(t["error_message"], "quota exceeded")

    def test_absent_error_message_is_none(self):
        t = _pi_assistant_turn({}, "ts", "s", "m")
        self.assertIsNone(t["error_message"])


class TestRenderSection(unittest.TestCase):
    def test_missing_key_is_na(self):
        html = q.render_section({})
        self.assertIn("n/a", html)
        self.assertIn("quota_events", html)

    def test_present_key_renders_table(self):
        html = q.render_section({"quota_events": block([A("t", "billing")])})
        self.assertIn("<table>", html)
        self.assertIn("anthropic", html)
        self.assertIn("claude-cli", html)


if __name__ == "__main__":
    unittest.main()
