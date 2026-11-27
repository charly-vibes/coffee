# Tests de ops.concurrency / ops.stall / ops.queue_wait (coffe-qi7).
# Entradas sintéticas: turnos con ses falsas y doc mínimo con metadata.
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from agent_metrics_ops import merge_ops as merge_ops_free  # noqa: E402
from agent_metrics_ops_concurrency import (  # noqa: E402
    QUEUE_WAIT_BLOCK, compute_concurrency, compute_queue_wait, compute_stall,
    concurrency_block, queue_wait_block, stall_block)


def ts(h, m=0, day=1):
    return datetime(2026, 5, day, h, m, tzinfo=timezone.utc)


def ses(sid="s1", source="pi"):
    return {"source": source, "project": "p", "session_id": sid}


def a_turn(start, dur_s, sid="s1", source="pi", stop=None, day=None):
    if day is not None:
        start = start.replace(day=day)
    return {"ts": start, "role": "assistant", "model": "m", "stop": stop,
            "dur_s": dur_s, "ses": ses(sid, source)}


def doc(gap_cap=30, tz="-03"):
    return {"metadata": {"gap_cap_minutes": gap_cap, "timezone": tz}}


class TestConcurrency(unittest.TestCase):
    def test_overlap_depth_counted(self):
        # dos turnos solapados 10:00-10:10 y 10:05-10:15 → profundidad 2
        turns = [a_turn(ts(10, 0), 600), a_turn(ts(10, 5), 600, sid="s2")]
        block = concurrency_block(turns, [], doc())
        daily = block["daily"]
        self.assertEqual(len(daily), 1)
        self.assertEqual(daily[0]["requests_p95"], 2.0)
        self.assertEqual(daily[0]["agents_p95"], 2.0)  # sesiones distintas
        self.assertEqual(block["requests_p95"], 2.0)
        self.assertEqual(block["agents_p95"], 2.0)

    def test_same_session_overlap_counts_requests_not_agents(self):
        # mismo session_id solapado: requests 2, agentes 1
        turns = [a_turn(ts(10, 0), 600), a_turn(ts(10, 5), 600)]
        block = concurrency_block(turns, [], doc())
        self.assertEqual(block["daily"][0]["requests_p95"], 2.0)
        self.assertEqual(block["daily"][0]["agents_p95"], 1.0)

    def test_no_overlap_is_one(self):
        turns = [a_turn(ts(10, 0), 60), a_turn(ts(11, 0), 60)]
        block = concurrency_block(turns, [], doc())
        self.assertEqual(block["daily"][0]["requests_p95"], 1.0)

    def test_p95_across_days(self):
        # día A pico 1, día B pico 2, día C pico 4 → p95 sobre máximos diarios
        turns = [a_turn(ts(10, 0), 60, sid="a"),               # día 1: 1
                 a_turn(ts(10, 0), 60, sid="a", day=2),        # día 2: pico 2
                 a_turn(ts(10, 5), 60, sid="b", day=2),
                 a_turn(ts(10, 0), 60, sid="a", day=3),        # día 3: pico 4
                 a_turn(ts(10, 0), 60, sid="b", day=3),
                 a_turn(ts(10, 0), 60, sid="c", day=3),
                 a_turn(ts(10, 0), 60, sid="d", day=3)]
        block = concurrency_block(turns, [], doc())
        self.assertEqual([d["day"] for d in block["daily"]],
                         ["2026-05-01", "2026-05-02", "2026-05-03"])
        self.assertEqual(block["daily"][2]["requests_p95"], 4.0)
        # p95 de [1,2,4] con interpolación lineal > 3.5 y ≤ 4
        self.assertIsNotNone(block["requests_p95"])
        self.assertGreaterEqual(block["requests_p95"], 3.5)
        self.assertLessEqual(block["requests_p95"], 4.0)

    def test_no_duration_day_reason_not_zero(self):
        # día sin ninguna duración: no entra al daily ni cuenta como 0
        turns = [a_turn(ts(10, 0), None), a_turn(ts(10, 5), None, sid="s2")]
        block = concurrency_block(turns, [], doc())
        self.assertEqual(block["daily"], [])
        self.assertIsNone(block["requests_p95"])
        self.assertIsNone(block["agents_p95"])
        self.assertEqual(block["reason"], "no_overlap_data")

    def test_coverage_pct_with_and_without_duration(self):
        turns = [a_turn(ts(10, 0), 60), a_turn(ts(11, 0), None)]
        block = concurrency_block(turns, [], doc())
        cov = {c["source"]: c for c in block["coverage"]}
        self.assertEqual(cov["pi"]["coverage_pct"], 50.0)
        self.assertEqual(block["provenance"], {"concurrency": "derived"})

    def test_mixed_days_partial_duration(self):
        # un día con duración y otro sin: el diario solo incluye el primero,
        # y el bloque queda con razón declarada
        turns = [a_turn(ts(10, 0), 60), a_turn(ts(10, 0), None, day=2)]
        block = concurrency_block(turns, [], doc())
        self.assertEqual([d["day"] for d in block["daily"]], ["2026-05-01"])
        self.assertEqual(block["reason"], "no_overlap_data")
        self.assertEqual(block["requests_p95"], 1.0)

    def test_respects_metadata_timezone(self):
        # 01:00 UTC del día 2 = 22:00 del día 1 en TZ -03
        turns = [a_turn(ts(1, 0, day=2), 60)]
        block = concurrency_block(turns, [], doc())
        self.assertEqual(block["daily"][0]["day"], "2026-05-01")


class TestStall(unittest.TestCase):
    def test_idle_minutes_capped(self):
        # gap de 60 min con cap 30 → 30 min, no 60
        turns = [a_turn(ts(10, 0), 60), a_turn(ts(11, 0), 60)]
        block = stall_block(turns, [], doc())
        self.assertEqual(block["daily"][0]["minutes_idle"], 30.0)
        self.assertEqual(block["daily"][0]["minutes_error_adjacent"], 0.0)
        self.assertEqual(block["totals"]["minutes_idle"], 30.0)

    def test_error_adjacent_attribution(self):
        # gap tras un turno con stop error → error-adyacente, no idle
        turns = [a_turn(ts(10, 0), 60, stop="error"), a_turn(ts(10, 30), 60)]
        block = stall_block(turns, [], doc())
        self.assertEqual(block["daily"][0]["minutes_error_adjacent"], 30.0)
        self.assertEqual(block["daily"][0]["minutes_idle"], 0.0)
        self.assertEqual(block["totals"]["minutes_error_adjacent"], 30.0)

    def test_aborted_stop_and_tool_error_count(self):
        turns = [a_turn(ts(9, 0), 60, stop="aborted"), a_turn(ts(9, 20), 60),
                 a_turn(ts(10, 0), 60), a_turn(ts(10, 20), 60)]
        turns.insert(2, {"ts": ts(9, 30), "role": "tool", "is_error": True, "ses": ses()})
        block = stall_block(turns, [], doc())
        self.assertEqual(block["totals"]["minutes_error_adjacent"], 50.0)

    def test_error_of_other_session_not_attributed(self):
        turns = [a_turn(ts(10, 0), 60, sid="s1"), a_turn(ts(10, 30), 60, sid="s1")]
        turns.insert(1, {"ts": ts(10, 10), "role": "tool", "is_error": True,
                         "ses": ses("other")})
        block = stall_block(turns, [], doc())
        self.assertEqual(block["totals"]["minutes_error_adjacent"], 0.0)
        self.assertEqual(block["totals"]["minutes_idle"], 30.0)

    def test_gap_cap_from_metadata(self):
        turns = [a_turn(ts(10, 0), 60), a_turn(ts(11, 0), 60)]
        block = stall_block(turns, [], doc(gap_cap=90))
        self.assertEqual(block["totals"]["minutes_idle"], 60.0)

    def test_gap_cap_default_when_absent(self):
        turns = [a_turn(ts(10, 0), 60), a_turn(ts(11, 0), 60)]
        block = stall_block(turns, [], {"metadata": {"timezone": "-03"}})
        self.assertEqual(block["totals"]["minutes_idle"], 30.0)

    def test_days_split(self):
        # 02:50 UTC = 23:50 del día 1 (TZ -03); 03:10 UTC = 00:10 del día 2.
        # El gap que cruza la medianoche LOCAL se atribuye al día siguiente.
        turns = [a_turn(ts(2, 50), 60), a_turn(ts(2, 59), 60),
                 a_turn(ts(3, 10, day=2), 60)]
        block = stall_block(turns, [], doc())
        days = {d["day"]: d for d in block["daily"]}
        self.assertEqual(len(days), 2)
        self.assertEqual(days["2026-04-30"]["minutes_idle"], 9.0)
        self.assertEqual(days["2026-05-02"]["minutes_idle"], 30.0)


class TestQueueWaitAndMerges(unittest.TestCase):
    def test_queue_wait_constant_shape(self):
        for got in (queue_wait_block(), compute_queue_wait({})):
            self.assertEqual(got, QUEUE_WAIT_BLOCK)
            self.assertIsNone(got["value"])
            self.assertEqual(got["reason"], "no_ttft_in_local_logs")
            self.assertEqual(got["provenance"], {"queue_wait": "assumed"})

    def test_three_merges_do_not_clobber(self):
        # merge_ops consecutive (el contrato de run_slice re-carga el doc):
        # los tres slices conviven en ops sin pisarse
        d0 = {"ops": {"latency": {"p95_ms": 1}}}
        merge_ops_free(d0, {"concurrency": concurrency_block(
            [a_turn(ts(10, 0), 600), a_turn(ts(10, 5), 600, sid="s2")], [], d0)})
        merge_ops_free(d0, {"stall": stall_block(
            [a_turn(ts(10, 0), 60, stop="error"), a_turn(ts(10, 30), 60)], [], d0)})
        merge_ops_free(d0, {"queue_wait": queue_wait_block()})
        self.assertEqual(set(d0["ops"]), {"latency", "concurrency", "stall", "queue_wait"})
        self.assertEqual(d0["ops"]["latency"], {"p95_ms": 1})
        self.assertEqual(d0["ops"]["concurrency"]["requests_p95"], 2.0)
        self.assertEqual(d0["ops"]["stall"]["totals"]["minutes_error_adjacent"], 30.0)
        self.assertIsNone(d0["ops"]["queue_wait"]["value"])

    def test_empty_inputs_nulls_with_reason(self):
        for block in (concurrency_block([], [], doc()),):
            self.assertEqual(block["daily"], [])
            self.assertIsNone(block["requests_p95"])
            self.assertEqual(block["reason"], "no_overlap_data")
        block = stall_block([], [], doc())
        self.assertEqual(block["daily"], [])
        self.assertEqual(block["totals"], {"minutes_error_adjacent": 0.0, "minutes_idle": 0.0})


if __name__ == "__main__":
    unittest.main()
