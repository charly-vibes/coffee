"""Tests de ops.completion + ops.intervention
(scripts/agent_metrics_ops_completion.py) — coffe-af2.

Sintéticos: sin dependencia de logs reales (la única lectura es la copia del
dataset base para el test de merge, que es un archivo del repo). Cubre
ambigüedad, matemática de rate/per_100, cohort vacío, gate de frescura,
conteo de retries y el merge independiente de ambos slices vía run_slice.
"""
import json
import shutil
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import agent_metrics_ops as ops_mod  # noqa: E402
import agent_metrics_ops_completion as m  # noqa: E402
from agent_metrics_common import (  # noqa: E402
    FLAG_ABORTED, FLAG_ERROR, FLAG_RETRY, FLAG_TOOL_ERROR)

TODAY = date(2026, 11, 27)
FRESH_EST = {"generated": "2026-11-20T10:00:00-03:00"}
STALE_EST = {"generated": "2026-09-01T10:00:00-03:00"}

# Serie de flags sintética: idx0 limpio, idx1 error, idx2 limpio,
# idx3 abort, idx4 limpio, idx5 retry.
FLAGS = [0, FLAG_ERROR, 0, FLAG_ABORTED, 0, FLAG_RETRY]


def bead(name, cost=1.0, nt=0, ns=1):
    return {"bead": name, "cost_per_bead": cost, "n_sessions": ns,
            "n_temporal": nt}


def tb(beads, rows):
    return {"beads": beads, "task_beads": rows}


def rows_of(*pairs):
    out = []
    for name, tasks in pairs:
        out += [{"bead": name, "task": t} for t in tasks]
    return out


def completion(est=FRESH_EST, flags=FLAGS, beads=None, rows=None):
    beads = beads if beads is not None else [
        bead("A"), bead("B", cost=None), bead("C", nt=1), bead("D", cost=3.0)]
    rows = rows if rows is not None else rows_of(
        ("A", [0, 1]), ("B", [3]), ("C", [4, 5]), ("D", [2]))
    return m.completion_block(tb(beads, rows), est, flags, TODAY)


def intervention(est=FRESH_EST, flags=FLAGS, beads=None, rows=None):
    beads = beads if beads is not None else [
        bead("A"), bead("D", cost=3.0)]
    rows = rows if rows is not None else rows_of(("A", [0, 1]), ("D", [2]))
    st = m._cohort_state(tb(beads, rows), est, flags, TODAY)
    return m.intervention_block(st, flags)


class TestCompletion(unittest.TestCase):
    def test_ambiguous_excluded(self):
        c = completion()
        # cohort = A, D (B sin coste, C temporal)
        self.assertEqual(c["cohort"], "unambiguous")
        self.assertEqual(c["started"], 2)
        self.assertEqual(c["ambiguous_share"], 50.0)

    def test_rate_math(self):
        # A tiene task idx1 con FLAG_ERROR → no autónoma; D limpia.
        c = completion()
        self.assertEqual(c["autonomous"], 1)
        self.assertEqual(c["rate"], 0.5)

    def test_started_zero_null_with_reason(self):
        c = completion(beads=[bead("B", cost=None), bead("C", nt=1)],
                       rows=rows_of(("B", [0]), ("C", [1])))
        self.assertEqual(c["started"], 0)
        self.assertIsNone(c["rate"])
        self.assertEqual(c["reason"], "no_unambiguous_beads")
        self.assertEqual(c["ambiguous_share"], 100.0)
        self.assertEqual(c["autonomous"], 0)

    def test_stale_estimator_gates_rate_only(self):
        c = completion(est=STALE_EST)
        self.assertIsNone(c["rate"])
        self.assertEqual(c["reason"], "estimator_stale")
        # el cohort y el share se siguen computando
        self.assertEqual(c["started"], 2)
        self.assertEqual(c["ambiguous_share"], 50.0)
        cov = {x["source"]: x for x in c["coverage"]}
        self.assertEqual(cov["model-cost-estimate-v0"]["coverage_pct"], 0.0)
        self.assertEqual(cov["model-cost-estimate-v0"]["reason"],
                         "estimator_stale")

    def test_freshness_boundary_30_days(self):
        est = {"generated": "2026-10-28T23:59:59-03:00"}
        self.assertFalse(m.estimator_stale(est, TODAY))
        est = {"generated": "2026-10-27T00:00:00-03:00"}
        self.assertTrue(m.estimator_stale(est, TODAY))

    def test_bead_without_rows_not_autonomous(self):
        # E en el cohort pero sin filas: no clasificable → no autónoma.
        c = completion(beads=[bead("E", cost=5.0)], rows=[])
        self.assertEqual(c["started"], 1)
        self.assertEqual(c["autonomous"], 0)
        self.assertEqual(c["rate"], 0.0)
        cov = {x["source"]: x for x in c["coverage"]}
        self.assertEqual(cov["agent_metrics.tasks"]["reason"],
                         "no_attributed_tasks")

    def test_provenance_and_coverage(self):
        c = completion()
        self.assertEqual(c["provenance"], {"completion": "derived"})
        cov = {x["source"]: x["coverage_pct"] for x in c["coverage"]}
        self.assertEqual(cov["task_beads"], 100.0)
        self.assertEqual(cov["agent_metrics.tasks"], 100.0)
        self.assertEqual(cov["model-cost-estimate-v0"], 100.0)
        for pct in cov.values():
            self.assertTrue(0 <= pct <= 100)


class TestIntervention(unittest.TestCase):
    def test_per_100_math(self):
        # aborts=1 (idx3), recovery=1 (idx5), accepted=2 → 100.0
        i = intervention()
        self.assertEqual(i["aborts"], 1)
        self.assertEqual(i["recovery_prompts"], 1)
        self.assertEqual(i["accepted_beads"], 2)
        self.assertEqual(i["per_100_accepted"], 100.0)

    def test_retry_count_ignores_tool_error(self):
        i = intervention(flags=[FLAG_RETRY, FLAG_TOOL_ERROR, FLAG_RETRY, 0],
                         beads=[bead("A")], rows=rows_of(("A", [0])))
        self.assertEqual(i["recovery_prompts"], 2)
        self.assertEqual(i["aborts"], 0)

    def test_accepted_zero_null_with_reason(self):
        i = intervention(beads=[bead("B", cost=None)],
                         rows=rows_of(("B", [0])))
        self.assertEqual(i["accepted_beads"], 0)
        self.assertIsNone(i["per_100_accepted"])
        self.assertEqual(i["reason"], "no_accepted_beads")

    def test_stale_estimator_null_accepted(self):
        i = intervention(est=STALE_EST)
        self.assertIsNone(i["accepted_beads"])
        self.assertIsNone(i["per_100_accepted"])
        self.assertEqual(i["reason"], "estimator_stale")

    def test_provenance_and_coverage(self):
        i = intervention()
        self.assertEqual(i["provenance"], {"intervention": "derived"})
        cov = {x["source"]: x["coverage_pct"] for x in i["coverage"]}
        self.assertEqual(cov["agent_metrics.tasks"], 100.0)


class TestMergeViaRunSlice(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / "agent_metrics.json"
        shutil.copyfile(ops_mod.OUT, self.tmp)
        self._out = ops_mod.OUT
        ops_mod.OUT = self.tmp
        ops_mod.SCHEMA = Path(__file__).resolve().parent.parent \
            / "specs" / "agent-metrics.schema.json"

    def tearDown(self):
        ops_mod.OUT = self._out
        shutil.rmtree(self.tmp.parent, ignore_errors=True)

    def test_both_slices_merge_independently(self):
        inputs = {"task_beads": tb([bead("A"), bead("D", cost=3.0)],
                                   rows_of(("A", [0, 1]), ("D", [2]))),
                  "estimate": FRESH_EST,
                  "flags": FLAGS,
                  "today": TODAY}
        _doc, c = ops_mod.run_slice("completion", lambda inp: m.completion_block(
            inp["task_beads"], inp["estimate"], inp["flags"], inp["today"]),
            inputs)
        _doc, i = ops_mod.run_slice(
            "intervention",
            lambda inp: m.intervention_block(
                m._cohort_state(inp["task_beads"], inp["estimate"],
                                inp["flags"], inp["today"]),
                inp["flags"]),
            inputs)
        self.assertEqual(c["rate"], 0.5)
        self.assertEqual(i["per_100_accepted"], 100.0)
        doc = json.loads(self.tmp.read_text(encoding="utf-8"))
        self.assertIn("completion", doc["ops"])
        self.assertIn("intervention", doc["ops"])
        self.assertEqual(doc["ops"]["completion"], c)
        self.assertEqual(doc["ops"]["intervention"], i)


class TestRenderSection(unittest.TestCase):
    def test_absent_blocks_render_na(self):
        html = m.render_section({})
        self.assertIn("n/a", html)
        self.assertIn("<section", html)
        self.assertIn("ops.completion", html)
        self.assertIn("ops.intervention", html)

    def test_present_blocks_render_values(self):
        html = m.render_section({"completion": completion(),
                                 "intervention": intervention()})
        self.assertIn("50.0%", html)  # ambiguous_share
        self.assertIn("100.00", html)  # per_100
        self.assertIn("<table", html)
        self.assertEqual(html.count("<section"), 2)


if __name__ == "__main__":
    unittest.main()
