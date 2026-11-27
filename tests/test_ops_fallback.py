"""Tests de ops.fallback (coffe-8mh): eventos de fallback de modelo.

omp: método reported (resolvedModelIsFallback en registros model_change de
JSONL). pi/claude-cli: proxy derived (cambio de modelo a mitad de sesión,
grouping por session_id — sin falsos positivos en el borde de sesiones).
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import agent_metrics_ops_fallback as fb  # noqa: E402


def T(ts, model="m", ses="s1"):
    return {"ts": ts, "role": "assistant", "model": model,
            "ses": {"source": "pi", "project": "p", "session_id": ses}}


def omp_rec(flag):
    tail = '"model":"openrouter/google/gemini-3.8-flash","resolvedModelIsFallback":%s}' % ("true" if flag else "false")
    return ('{"type":"model_change","id":"x","timestamp":"2026-11-12T18:32:47.475Z",' + tail)


def write_omp(root, name, *records):
    d = root / "proj" / name
    d.mkdir(parents=True)
    (d / "session.jsonl").write_text("\n".join(records) + "\n", encoding="utf-8")


class TestOmpReported(unittest.TestCase):
    def test_true_counted_false_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_omp(Path(tmp), "s_a", omp_rec(True), omp_rec(False), omp_rec(True))
            cell = fb.omp_source(Path(tmp))
        self.assertEqual(cell["method"], "reported-resolvedModelIsFallback")
        self.assertEqual(cell["events"], 2)
        self.assertEqual(cell["denominator"], 3)
        self.assertAlmostEqual(cell["rate"], round(2 / 3, 4))

    def test_missing_dir_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            cell = fb.omp_source(Path(tmp) / "nope")
        self.assertEqual(cell["events"], 0)
        self.assertEqual(cell["denominator"], 0)
        self.assertIsNone(cell["rate"])
        self.assertEqual(cell["reason"], "no_omp_sessions_dir")


class TestProxy(unittest.TestCase):
    def test_mid_session_switch_once(self):
        turns = [T("t1", "a"), T("t2", "a"), T("t3", "b"), T("t4", "b")]
        cell = fb.proxy_source(turns, "pi")
        self.assertEqual(cell["method"], "proxy-mid-task-switch")
        self.assertEqual(cell["events"], 1)
        self.assertEqual(cell["denominator"], 4)
        self.assertAlmostEqual(cell["rate"], 0.25)

    def test_no_false_positive_across_sessions(self):
        # Dos sesiones con 2+ assistant: modelos iguales dentro de cada una;
        # el modelo difiere ENTRE sesiones (borde) → sin eventos.
        turns = [T("t1", "a", "s1"), T("t2", "a", "s1"),
                 T("t3", "b", "s2"), T("t4", "b", "s2")]
        cell = fb.proxy_source(turns, "pi")
        self.assertEqual(cell["events"], 0)
        self.assertEqual(cell["denominator"], 4)

    def test_single_turn_session_excluded(self):
        turns = [T("t1", "a", "s1"), T("t2", "b", "s2")]
        cell = fb.proxy_source(turns, "pi")
        self.assertEqual(cell["events"], 0)
        self.assertEqual(cell["denominator"], 0)
        self.assertEqual(cell["reason"], "no_turns_signal")


class TestFallbackBlock(unittest.TestCase):
    def test_events_total_and_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_omp(Path(tmp), "s_a", omp_rec(True))
            block = fb.fallback_block(
                Path(tmp), [T("t1", "a"), T("t2", "b")], [])
        self.assertEqual(block["events_total"], 2)  # 1 omp + 1 proxy pi
        self.assertEqual(block["provenance"]["omp"], "reported")
        self.assertEqual(block["provenance"]["pi"], "derived")
        self.assertEqual(block["provenance"]["claude-cli"], "derived")
        self.assertEqual(
            {c["source"] for c in block["coverage"]},
            {"omp", "pi", "claude-cli"})

    def test_render_na_and_table(self):
        self.assertIn("n/a", fb.render_section({}))
        html = fb.render_section({"fallback": {
            "by_source": {"omp": {"method": "reported-resolvedModelIsFallback",
                                  "events": 1, "denominator": 2, "rate": 0.5}},
            "events_total": 1, "provenance": {"omp": "reported"},
            "coverage": [{"source": "omp", "coverage_pct": 100.0,
                          "reason": None}]}})
        self.assertIn("<table>", html)
        self.assertIn("reported-resolvedModelIsFallback", html)
        self.assertIn("50.0%", html)


if __name__ == "__main__":
    unittest.main()
