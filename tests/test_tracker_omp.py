#!/usr/bin/env python3
"""
Tests del extractor omp (~/.omp/agent/sessions).

Formato verificado 2026-11-11 (runtime 18.18.8):
  - record "session" con cwd REAL (no mungeado)
  - turno principal = "message" role=assistant con message.usage
    (input/output/cacheRead/cacheWrite + cost.total *reported*)
  - llamadas auxiliares del harness = record "model_usage" (purpose
    title/find/…) — facturan tokens, no son turnos de conversación
  - prompts = "message" role=user (attribution user)
  - tools = "custom" customType=tool_execution_start (data.toolName)
"""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TRACKER_PATH = REPO / "scripts" / "usage-tracker.py"


def load_tracker():
    spec = importlib.util.spec_from_file_location("usage_tracker_omp", TRACKER_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ut = load_tracker()


# ============================ fixtures ============================

def session_line(cwd, ts="2026-11-11T15-27-59.682Z", sid="01a1cb39"):
    return {"type": "session", "version": 3, "id": sid, "timestamp": ts,
            "cwd": cwd}


def user_msg_line(ts, text="hola"):
    return {"type": "message", "id": "u1", "timestamp": ts,
            "message": {"role": "user",
                        "content": [{"type": "text", "text": text}],
                        "attribution": "user"}}


def assistant_msg_line(ts, inp, out, cache_r=0, cost=0.002,
                       model="z-ai/glm-5.3-flash", error=False):
    msg = {"role": "assistant", "model": model, "stopReason": "stop",
           "usage": {"input": inp, "output": out, "cacheRead": cache_r,
                     "cacheWrite": 0, "totalTokens": inp + out,
                     "cost": {"input": cost * 0.9, "output": cost * 0.1,
                              "cacheRead": 0, "cacheWrite": 0, "total": cost}}}
    if error:
        msg["errorId"] = "e1"
        msg["errorMessage"] = "boom"
    return {"type": "message", "id": "a1", "timestamp": ts, "message": msg}


def model_usage_line(ts, purpose="title", model="z-ai/glm-5.3-flash",
                     inp=100, out=10, cost=0.0001):
    return {"type": "model_usage", "timestamp": ts, "purpose": purpose,
            "role": None, "api": "openrouter", "provider": "openrouter",
            "model": model,
            "usage": {"input": inp, "output": out, "cacheRead": 0,
                      "cacheWrite": 0, "totalTokens": inp + out,
                      "cost": {"input": cost * 0.9, "output": cost * 0.1,
                               "cacheRead": 0, "cacheWrite": 0,
                               "total": cost}},
            "stopReason": "stop"}


def tool_line(ts, tool="bash"):
    return {"type": "custom", "customType": "tool_execution_start",
            "timestamp": ts, "data": {"toolName": tool, "toolCallId": "t1"}}


def write_session(tmp, lines, proj="-para-areas-dev-gh-charly-coffee",
                  name="2026-11-11T15-27-59-682Z_01a1cb39.jsonl"):
    d = tmp / proj
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text("\n".join(json.dumps(l) for l in lines) + "\n")
    return p


CWD_COFFEE = "/var/home/sasha/para/areas/dev/gh/charly/coffee"
CWD_FUERA = "/var/home/sasha/para/areas/dev/gh/at/catherine"

BASE_LINES = [
    session_line(CWD_COFFEE),
    user_msg_line("2026-11-11T15:28:46.905Z"),
    assistant_msg_line("2026-11-11T15:28:57.060Z", 12000, 300, cost=0.002),
    tool_line("2026-11-11T15:28:58.000Z", tool="bash"),
    tool_line("2026-11-11T15:28:59.000Z", tool="read"),
    model_usage_line("2026-11-11T15:29:01.000Z", purpose="title"),
    assistant_msg_line("2026-11-11T15:29:10.000Z", 9000, 100, cost=0.001),
]


class OmpTestBase(unittest.TestCase):
    def setUp(self):
        self._old_filter = ut.CHARLY_FILTER
        ut.CHARLY_FILTER = True
        self._old_since, self._old_until = ut.SINCE, ut.UNTIL
        ut.SINCE = ut.UNTIL = None

    def tearDown(self):
        ut.CHARLY_FILTER = self._old_filter
        ut.SINCE, ut.UNTIL = self._old_since, self._old_until


# ============================ extract_omp ============================

class TestExtractOmp(OmpTestBase):
    """Una fila por llamada facturada; costo *reported*; proyecto por cwd."""

    def test_rows_per_billed_call(self):
        with tempfile.TemporaryDirectory() as td:
            write_session(Path(td), BASE_LINES)
            rows, excluded = ut.extract_omp(sessions_dir=Path(td))
            self.assertEqual(len(excluded), 0)
            # 2 assistant + 1 model_usage = 3 filas
            self.assertEqual(len(rows), 3)
            kinds = [r["kind"] for r in rows]
            self.assertEqual(kinds.count("assistant_turn"), 2)
            self.assertEqual(kinds.count("aux_model_call"), 1)

    def test_row_fields(self):
        with tempfile.TemporaryDirectory() as td:
            write_session(Path(td), BASE_LINES)
            rows, _ = ut.extract_omp(sessions_dir=Path(td))
            main = [r for r in rows if r["kind"] == "assistant_turn"][0]
            self.assertEqual(main["source"], "omp")
            self.assertEqual(main["tool"], "omp")
            self.assertEqual(main["project"], "charly-coffee")
            self.assertEqual(main["model_raw"], "z-ai/glm-5.3-flash")
            self.assertEqual(main["input_tokens"], 12000)
            self.assertEqual(main["output_tokens"], 300)
            self.assertEqual(main["cost_effective"], 0.002)  # reported, no estimated
            # hour_key usa la TZ local de la máquina (documentado en el
            # tracker): solo la fecha es estable entre máquinas
            self.assertTrue(main["hour"].startswith("2026-11-11 "))
            self.assertTrue(main["session_file"].endswith(".jsonl"))

    def test_user_prompts_do_not_generate_rows_but_count_kinds(self):
        with tempfile.TemporaryDirectory() as td:
            write_session(Path(td), BASE_LINES)
            kinds = {}
            rows, _ = ut.extract_omp(sessions_dir=Path(td), kinds=kinds)
            self.assertEqual(kinds["2026-11"]["user_prompt"], 1)
            self.assertNotIn("user_prompt", [r["kind"] for r in rows])

    def test_reported_cost_wins_over_estimate(self):
        # costo exacto del log, no una estimación por tokens
        with tempfile.TemporaryDirectory() as td:
            write_session(Path(td), BASE_LINES)
            rows, _ = ut.extract_omp(sessions_dir=Path(td))
            self.assertEqual(sum(r["cost_effective"] for r in rows), 0.0031)

    def test_cost_fallback_estimates_without_cost(self):
        line = assistant_msg_line("2026-11-11T15:28:57.060Z", 1000, 50, cost=0)
        line["message"]["usage"].pop("cost")
        with tempfile.TemporaryDirectory() as td:
            write_session(Path(td), [session_line(CWD_COFFEE), line])
            rows, _ = ut.extract_omp(sessions_dir=Path(td))
            self.assertEqual(len(rows), 1)
            self.assertGreater(rows[0]["cost_effective"], 0)

    def test_window_filter(self):
        from datetime import date
        ut.SINCE = date(2026, 11, 12)
        ut.UNTIL = None
        with tempfile.TemporaryDirectory() as td:
            write_session(Path(td), BASE_LINES)
            rows, _ = ut.extract_omp(sessions_dir=Path(td))
            self.assertEqual(rows, [])


# ============================ sessions ============================

class TestExtractOmpSessions(OmpTestBase):
    """1 archivo = 1 sesión; n_turns solo assistant; señal real de errores."""

    def test_one_file_one_session(self):
        with tempfile.TemporaryDirectory() as td:
            write_session(Path(td), BASE_LINES)
            sessions = ut.extract_omp_sessions(sessions_dir=Path(td))
            self.assertEqual(len(sessions), 1)
            s = sessions[0]
            self.assertEqual(s["tool"], "omp")
            self.assertEqual(s["source"], "omp_session_files")
            self.assertEqual(s["project"], "charly-coffee")
            self.assertEqual(s["first_ts"], "2026-11-11")
            self.assertEqual(s["duration_msgs"], 1)   # 1 prompt de usuario
            self.assertEqual(s["n_turns"], 2)         # solo assistant, no aux
            self.assertEqual(s["n_tools"], 2)         # bash + read
            self.assertFalse(s["has_agent"])

    def test_error_signal(self):
        lines = BASE_LINES + [
            assistant_msg_line("2026-11-11T15:29:20.000Z", 100, 10,
                               error=True),
        ]
        with tempfile.TemporaryDirectory() as td:
            write_session(Path(td), lines)
            sessions = ut.extract_omp_sessions(sessions_dir=Path(td))
            self.assertEqual(sessions[0]["n_errors"], 1)
            rows, _ = ut.extract_omp(sessions_dir=Path(td))
            self.assertEqual(len(rows), 4)  # la errónea también facturó

    def test_out_of_scope_not_listed(self):
        with tempfile.TemporaryDirectory() as td:
            write_session(Path(td), [session_line(CWD_FUERA),
                                     *BASE_LINES[1:]],
                          proj="-para-areas-dev-gh-at-catherine")
            sessions = ut.extract_omp_sessions(sessions_dir=Path(td))
            self.assertEqual(sessions, [])


# ============================ filtro / scope ============================

class TestExtractOmpExcluded(OmpTestBase):
    """FPA-141: lo fuera de scope se registra en excluded, no se descarta
    en silencio."""

    def test_excluded_rows_registered(self):
        with tempfile.TemporaryDirectory() as td:
            write_session(Path(td), [session_line(CWD_FUERA),
                                     *BASE_LINES[1:]],
                          proj="-para-areas-dev-gh-at-catherine")
            rows, excluded = ut.extract_omp(sessions_dir=Path(td))
            self.assertEqual(rows, [])
            self.assertEqual(len(excluded), 1)
            self.assertEqual(excluded[0]["tool"], "omp")
            self.assertEqual(excluded[0]["project"], "at-catherine")
            self.assertEqual(excluded[0]["interactions"], 3)

    def test_filter_all_includes_everything(self):
        ut.CHARLY_FILTER = False
        with tempfile.TemporaryDirectory() as td:
            write_session(Path(td), [session_line(CWD_FUERA),
                                     *BASE_LINES[1:]],
                          proj="-para-areas-dev-gh-at-catherine")
            rows, excluded = ut.extract_omp(sessions_dir=Path(td))
            self.assertEqual(len(rows), 3)
            self.assertEqual(excluded, [])

    def test_missing_dir_returns_empty(self):
        self.assertEqual(ut.extract_omp(sessions_dir=Path("/no-existe")),
                         ([], []))
        self.assertEqual(ut.extract_omp_sessions(sessions_dir=Path("/no-existe")),
                         [])


if __name__ == "__main__":
    unittest.main()
