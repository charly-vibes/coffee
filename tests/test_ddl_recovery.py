#!/usr/bin/env python3
"""Tests de recovery_metrics y commit_latency en scripts/ddl-effect-tracker.py.

Solo stdlib. Sin disk I/O: los reducers reciben estructuras, no paths
(mismo estilo que test_tracker.py con aggregate()).
"""
import importlib.util
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "ddl_effect_tracker", REPO / "scripts" / "ddl-effect-tracker.py"
)
ddl = importlib.util.module_from_spec(_spec)
sys.modules["ddl_effect_tracker"] = ddl
_spec.loader.exec_module(ddl)


class RecoveryMetricsTest(unittest.TestCase):
    # convención de eventos: ("assistant",) | ("result", tool, ok)

    def test_recuperacion_cuenta_asistentes_hasta_exito(self):
        sessions = [[
            ("result", "bash", False),   # error
            ("assistant",),
            ("assistant",),
            ("result", "bash", True),    # éxito del mismo tool
        ]]
        out = ddl.recovery_metrics(sessions)
        self.assertEqual(out["recovery_p50_interactions"], 2)
        self.assertEqual(out["recovery_censored_rate"], 0.0)
        self.assertEqual(out["recovery_n"], 1)

    def test_error_censurado_por_fin_de_sesion(self):
        sessions = [[("result", "bash", False), ("assistant",)]]
        out = ddl.recovery_metrics(sessions)
        self.assertIsNone(out["recovery_p50_interactions"])
        self.assertEqual(out["recovery_censored_rate"], 1.0)

    def test_exito_de_otro_tool_no_resuelve(self):
        sessions = [[
            ("result", "bash", False),
            ("result", "read", True),
            ("result", "bash", True),
        ]]
        out = ddl.recovery_metrics(sessions)
        self.assertEqual(out["recovery_p50_interactions"], 0)  # 0 asistentes entre medio
        self.assertEqual(out["recovery_n"], 1)

    def test_dos_errores_resueltos_por_un_exito(self):
        sessions = [[
            ("result", "edit", False),
            ("assistant",),
            ("result", "edit", False),
            ("result", "edit", True),
        ]]
        out = ddl.recovery_metrics(sessions)
        self.assertEqual(out["recovery_p50_interactions"], 0.5)  # mediana de [1, 0]
        self.assertEqual(out["recovery_n"], 2)

    def test_sesion_vacia(self):
        out = ddl.recovery_metrics([[]])
        self.assertIsNone(out["recovery_p50_interactions"])
        self.assertEqual(out["recovery_censored_rate"], 0.0)
        self.assertEqual(out["recovery_n"], 0)

    def test_mediana_sobre_multiples_sesiones(self):
        sessions = [
            [("result", "bash", False), ("result", "bash", True)],   # 0
            [("result", "bash", False), ("assistant",), ("result", "bash", True)],  # 1
            [("result", "bash", False), ("assistant",), ("assistant",),
             ("assistant",), ("result", "bash", True)],             # 3
        ]
        out = ddl.recovery_metrics(sessions)
        self.assertEqual(out["recovery_p50_interactions"], 1)


class CommitLatencyTest(unittest.TestCase):
    def test_primer_commit_posterior_a_sesion(self):
        commits = [
            {"date": "2026-04-01T09:00:00Z", "message": "w: previo"},
            {"date": "2026-04-01T14:30:00Z", "message": "feat: trabajo asistido"},
        ]
        out = ddl.commit_latency("2026-04-01T13:00:00Z", commits)
        self.assertEqual(out["session_commit_latency_p50_h"], 1.5)
        self.assertEqual(out["latency_n"], 1)

    def test_sin_commit_posterior_es_none(self):
        out = ddl.commit_latency("2026-04-01T13:00:00Z",
                                 [{"date": "2026-04-01T09:00:00Z", "message": "x"}])
        self.assertIsNone(out["session_commit_latency_p50_h"])

    def test_sesgo_de_reloj_se_clampea(self):
        # commit "antes" de la sesión por skew → latencia 0, contada
        commits = [{"date": "2026-04-01T12:00:00Z", "message": "fix: a"}]
        out = ddl.commit_latency("2026-04-01T13:00:00Z", commits)
        self.assertEqual(out["session_commit_latency_p50_h"], 0.0)
        self.assertEqual(out["clamped_negative"], 1)

    def test_mediana_sobre_varios_pares(self):
        commits = [
            {"date": "2026-04-01T14:00:00Z", "message": "a"},  # 1h
            {"date": "2026-04-01T16:00:00Z", "message": "b"},  # 3h
        ]
        out = ddl.commit_latency("2026-04-01T13:00:00Z", commits)
        self.assertEqual(out["session_commit_latency_p50_h"], 2.0)


if __name__ == "__main__":
    unittest.main(verbosity=1)
