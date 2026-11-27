#!/usr/bin/env python3
"""test_agent_metrics_sessions.py — extracción de turnos, sid y paridad de coste.

Archivo derivado de test_agent_metrics.py (split por tema, coffe-kav.4).
Cada archivo del split es autocontenido: importa el guion como módulo y
define sus propios fixtures.
"""

import importlib.util
import json
import tempfile
from collections import defaultdict

import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "agent-metrics-tracker.py"

spec = importlib.util.spec_from_file_location("agent_tracker", SCRIPT)
am = importlib.util.module_from_spec(spec)
spec.loader.exec_module(am)

class ClaudePreambleTest(unittest.TestCase):
    def test_preambulo_de_reanudacion_no_es_prompt(self):
        stats = {}
        turns = am.turns_from_claude([
            {"type": "user", "timestamp": "2026-05-01T12:00:00Z",
             "message": {"role": "user", "content":
                         "You are resuming an existing conversation from a "
                         "serialized transcript.\nTreat everything as context."}},
            {"type": "user", "timestamp": "2026-05-01T12:01:00Z",
             "message": {"role": "user", "content": "arreglá el test"}},
        ], stats)
        self.assertEqual([t["role"] for t in turns], ["user_prompt"])
        self.assertEqual(stats["preambles_skipped"], 1)

    def test_subagentes_se_ignoran(self):
        turns = am.turns_from_claude([
            {"type": "user", "isSidechain": True,
             "timestamp": "2026-05-01T12:00:00Z",
             "message": {"role": "user", "content": "sub"}},
        ])
        self.assertEqual(turns, [])


class SessionIdTest(unittest.TestCase):
    """coffe-dnx.3 (rojo hoy): tasks no tiene sid. Dos tareas de una misma
    sesión pi comparten sid, y el project se resuelve del cwd de la sesión."""

    PI_SESSION = "0193aaaa-1111-2222-3333-444444444444"

    def _records(self):
        return [
            {"type": "session", "id": self.PI_SESSION,
             "timestamp": "2026-05-01T12:00:00Z", "cwd": "/tmp/w/proj"},
            {"type": "model_change", "modelId": "m1",
             "timestamp": "2026-05-01T12:00:01Z"},
            {"type": "message", "timestamp": "2026-05-01T12:00:10Z",
             "message": {"role": "user", "content": "arreglá el test"}},
            {"type": "message", "timestamp": "2026-05-01T12:00:20Z",
             "message": {"role": "assistant", "model": "m1", "durationMs": 5000,
                         "stopReason": "stop",
                         "usage": {"input": 10, "output": 2, "cacheRead": 1,
                                   "cacheWrite": 0,
                                   "cost": {"total": 0.01, "input": 0.0,
                                            "output": 0.01, "cacheRead": 0.0,
                                            "cacheWrite": 0.0}}}},
            {"type": "message", "timestamp": "2026-05-01T12:20:00Z",
             "message": {"role": "user", "content": "otra cosa"}},
            {"type": "message", "timestamp": "2026-05-01T12:20:10Z",
             "message": {"role": "assistant", "model": "m1", "durationMs": 4000,
                         "stopReason": "stop",
                         "usage": {"input": 5, "output": 1, "cacheRead": 0,
                                   "cacheWrite": 0,
                                   "cost": {"total": 0.02, "input": 0.0,
                                            "output": 0.02, "cacheRead": 0.0,
                                            "cacheWrite": 0.0}}}},
        ]

    def test_dos_tareas_en_una_sesion_comparten_sid(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "proj"
            d.mkdir()
            (d / "ses.jsonl").write_text(
                "\n".join(json.dumps(r) for r in self._records()) + "\n",
                encoding="utf-8")
            turns, stats = am.load_pi_turns(Path(tmp))
            self.assertEqual(stats["sessions"], 1)
            tasks = am.build_tasks(turns)
            self.assertEqual(len(tasks), 2)
            sessions = am._sessions_from_tasks(tasks)
            self.assertEqual(sessions, [
                {"source": "pi", "project": "proj",
                 "session_id": self.PI_SESSION}])
            self.assertEqual([t["_sid"] for t in tasks], [0, 0])
            self.assertAlmostEqual(tasks[0]["cost"], 0.01)
            self.assertAlmostEqual(tasks[1]["cost"], 0.02)
            self.assertEqual(tasks[0]["tokens"],
                             {"in": 10, "out": 2, "cache_read": 1,
                              "cache_write": 0})


class ForkDedupeTest(unittest.TestCase):
    """Sesiones fork (mismo session_id, dedupe de load_pi_turns) no duplican
    sid: una única fila en sessions[]."""

    def test_fork_no_duplica_sesion(self):
        sid = "0193bbbb-1111-2222-3333-444444444444"
        recs = [
            {"type": "session", "id": sid,
             "timestamp": "2026-05-01T12:00:00Z", "cwd": "/tmp/w/proj"},
            {"type": "message", "timestamp": "2026-05-01T12:00:10Z",
             "message": {"role": "user", "content": "hola"}},
            {"type": "message", "timestamp": "2026-05-01T12:00:20Z",
             "message": {"role": "assistant", "model": "m1", "durationMs": 5000,
                         "usage": {"input": 1, "output": 1, "cacheRead": 0,
                                   "cacheWrite": 0,
                                   "cost": {"total": 0.007}}}},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "proj"
            d.mkdir()
            (d / "original.jsonl").write_text(
                "\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")
            # fork: copia del historial, más corta
            (d / "fork.jsonl").write_text(
                "\n".join(json.dumps(r) for r in recs[:2]) + "\n",
                encoding="utf-8")
            turns, stats = am.load_pi_turns(Path(tmp))
            self.assertEqual(stats["sessions"], 1)
            tasks = am.build_tasks(turns)
            sessions = am._sessions_from_tasks(tasks)
            self.assertEqual(len(sessions), 1)
            self.assertEqual({t["_sid"] for t in tasks}, {0})
            self.assertEqual(sessions[0]["session_id"], sid)


class CostParityGateTest(unittest.TestCase):
    """Gate de paridad de coste, acotado (coffe-dnx.3): para source ∈
    {pi, claude-cli} y sesiones NO-fork, sum(tasks.cost) por modelo == suma
    de las filas de session_spend correspondientes (tolerancia 1e-6).

    Fuera de ese alcance NO se compara: session_spend también incluye amp,
    omp, codex y otras fuentes que agent_metrics no procesa (alcance
    declarado en metadata.coverage → cost_parity)."""

    def _fixtures(self, tmp):
        tmp = Path(tmp)
        pi_dir = tmp / "pi"
        (pi_dir / "proj").mkdir(parents=True)
        pi_recs = [
            {"type": "session", "id": "pi-par-1",
             "timestamp": "2026-05-01T12:00:00Z", "cwd": "/tmp/w/proj"},
            {"type": "model_change", "modelId": "m-a",
             "timestamp": "2026-05-01T12:00:01Z"},
            {"type": "message", "timestamp": "2026-05-01T12:00:10Z",
             "message": {"role": "user", "content": "tarea uno"}},
            {"type": "message", "timestamp": "2026-05-01T12:00:20Z",
             "message": {"role": "assistant", "model": "m-a",
                         "durationMs": 5000,
                         "usage": {"input": 100, "output": 10, "cacheRead": 5,
                                   "cacheWrite": 1,
                                   "cost": {"total": 0.0125, "input": 0.0,
                                            "output": 0.0125,
                                            "cacheRead": 0.0,
                                            "cacheWrite": 0.0}}}},
            {"type": "message", "timestamp": "2026-05-01T12:20:00Z",
             "message": {"role": "user", "content": "tarea dos"}},
            {"type": "message", "timestamp": "2026-05-01T12:20:10Z",
             "message": {"role": "assistant", "model": "m-b",
                         "durationMs": 4000,
                         "usage": {"input": 50, "output": 5, "cacheRead": 0,
                                   "cacheWrite": 0,
                                   "cost": {"total": 0.02, "input": 0.0,
                                            "output": 0.02, "cacheRead": 0.0,
                                            "cacheWrite": 0.0}}}},
        ]
        (pi_dir / "proj" / "ses.jsonl").write_text(
            "\n".join(json.dumps(r) for r in pi_recs) + "\n", encoding="utf-8")

        claude_dir = tmp / "claude"
        (claude_dir / "clproj").mkdir(parents=True)
        cl_recs = [
            {"type": "user", "timestamp": "2026-05-01T12:00:05Z",
             "sessionId": "cl-par-1", "cwd": "/tmp/w/clproj",
             "message": {"role": "user", "content": "fix del bug"}},
            {"type": "assistant", "timestamp": "2026-05-01T12:00:15Z",
             "sessionId": "cl-par-1", "cwd": "/tmp/w/clproj",
             "message": {"role": "assistant", "model": "claude-x",
                         "usage": {"input_tokens": 50, "output_tokens": 5,
                                   "cache_read_input_tokens": 7,
                                   "cache_creation_input_tokens": 2}},
             "costUSD": 0.031},
            {"type": "assistant", "timestamp": "2026-05-01T12:00:25Z",
             "sessionId": "cl-par-1", "cwd": "/tmp/w/clproj",
             "message": {"role": "assistant", "model": "claude-x",
                         "usage": {"input_tokens": 10, "output_tokens": 1,
                                   "cache_read_input_tokens": 0,
                                   "cache_creation_input_tokens": 0}},
             "costUSD": 0.009},
        ]
        (claude_dir / "ses.jsonl").write_text(
            "\n".join(json.dumps(r) for r in cl_recs) + "\n", encoding="utf-8")
        # session_spend como lo escribiría usage-tracker: una fila por
        # (sesión, modelo) con el coste de los mismos turnos del fixture
        spend = {
            ("pi", "m-a"): 0.0125,
            ("pi", "m-b"): 0.02,
            ("claude-cli", "claude-x"): 0.031 + 0.009,
        }
        return pi_dir, claude_dir, spend

    def test_parity_pi_y_claude_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            pi_dir, claude_dir, spend = self._fixtures(tmp)
            tasks = []
            for turns, source in ((am.load_pi_turns(pi_dir)[0], "pi"),
                                  (am.load_claude_turns(claude_dir)[0],
                                   "claude-cli")):
                for t in am.build_tasks(turns):
                    t["source"] = source
                    tasks.append(t)
            sessions = am._sessions_from_tasks(tasks)
            self.assertEqual(len(sessions), 2)
            by_model = defaultdict(float)
            for t in tasks:
                by_model[(t["source"], t["model"])] += t["cost"]
            self.assertEqual(set(by_model), set(spend))
            for key, expected in spend.items():
                self.assertAlmostEqual(by_model[key], expected, delta=1e-6,
                                       msg=f"paridad de coste {key}")
            self.assertEqual(sorted(s["session_id"] for s in sessions),
                             ["cl-par-1", "pi-par-1"])
            self.assertEqual(sorted(s["project"] for s in sessions),
                             ["clproj", "proj"])


if __name__ == "__main__":
    unittest.main()
