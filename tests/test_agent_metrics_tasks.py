#!/usr/bin/env python3
"""test_agent_metrics_tasks.py — reconstrucción de tareas, series y cobertura.

Archivo derivado de test_agent_metrics.py (split por tema, coffe-kav.4).
Cada archivo del split es autocontenido: importa el guion como módulo y
define sus propios fixtures.
"""

import importlib.util
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "agent-metrics-tracker.py"

spec = importlib.util.spec_from_file_location("agent_tracker", SCRIPT)
am = importlib.util.module_from_spec(spec)
spec.loader.exec_module(am)

T0 = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)


def at(seconds, role="assistant", **kw):
    turn = {"ts": T0 + timedelta(seconds=seconds), "role": role}
    turn.update(kw)
    return turn


class BuildTasksTest(unittest.TestCase):
    def test_prompt_abre_y_siguiente_prompt_cierra(self):
        tasks = am.build_tasks([
            at(0, "user_prompt"),
            at(10),
            at(60, "user_prompt"),
            at(70),
        ])
        self.assertEqual(len(tasks), 2)
        self.assertAlmostEqual(tasks[0]["busy_s"], 10.0)
        self.assertAlmostEqual(tasks[1]["busy_s"], 10.0)

    def test_turnos_sin_prompt_no_crean_tarea(self):
        self.assertEqual(am.build_tasks([at(0), at(5)]), [])

    def test_gap_cap_recorta_espera_larga(self):
        tasks = am.build_tasks([at(0, "user_prompt"), at(3600)],
                               gap_cap_s=1800)
        self.assertAlmostEqual(tasks[0]["busy_s"], 1800.0)
        self.assertAlmostEqual(tasks[0]["span_s"], 3600.0)

    def test_duration_ms_gana_al_gap(self):
        tasks = am.build_tasks([at(0, "user_prompt"),
                                at(600, dur_s=42.0)])
        self.assertAlmostEqual(tasks[0]["busy_s"], 42.0)

    def test_aborto_y_error_marcan_flags(self):
        tasks = am.build_tasks([
            at(0, "user_prompt"), at(5, stop="aborted"), at(10, stop="error"),
            at(50, "user_prompt"), at(55),
        ])
        self.assertEqual(am.task_flags(tasks[0]), am.FLAG_ABORTED | am.FLAG_ERROR)
        # el prompt que sigue a un fallo dentro de la ventana es reintento
        self.assertEqual(am.task_flags(tasks[1]), am.FLAG_RETRY)

    def test_tool_error_marca_flag(self):
        tasks = am.build_tasks([
            at(0, "user_prompt"), at(3, "tool", is_error=True), at(6),
            at(20, "user_prompt"), at(25),
        ])
        self.assertEqual(am.task_flags(tasks[0]), am.FLAG_TOOL_ERROR)

    def test_retry_solo_si_la_tarea_previa_termino_en_fallo(self):
        tasks = am.build_tasks([
            at(0, "user_prompt"), at(5, stop="error"),      # termina en fallo
            at(30, "user_prompt"), at(35),                  # reintento
            at(400, "user_prompt"), at(405),                # fuera de ventana
            at(800, "user_prompt"), at(805),                # previa limpia
        ], retry_window_s=300)
        self.assertEqual(am.task_flags(tasks[0]), am.FLAG_ERROR)
        self.assertEqual(am.task_flags(tasks[1]), am.FLAG_RETRY)
        self.assertFalse(am.task_flags(tasks[2]) & am.FLAG_RETRY)
        self.assertFalse(am.task_flags(tasks[3]) & am.FLAG_RETRY)

    def test_retry_fuera_de_ventana_no_se_marca(self):
        # la tarea falla a los 5 s; el prompt llega 10 min después
        tasks = am.build_tasks([
            at(0, "user_prompt"), at(5, stop="error"),
            at(600, "user_prompt"), at(605),
        ], retry_window_s=300)
        self.assertEqual(am.task_flags(tasks[1]), 0)

    def test_retry_no_se_marca_si_la_tarea_previa_no_tuvo_actividad(self):
        # tarea con error pero sin turnos con tiempo (busy 0) → no es reintento
        tasks = am.build_tasks([
            at(0, "user_prompt"), at(0, stop="error"),
            at(10, "user_prompt"), at(15),
        ], retry_window_s=300)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(am.task_flags(tasks[0]), 0)

    def test_orphan_turns_se_cuentan(self):
        stats = {}
        am.build_tasks([at(0), at(5), at(10, "user_prompt"), at(15)], stats=stats)
        self.assertEqual(stats["orphan_turns"], 2)

    def test_span_mide_hasta_el_ultimo_turno_de_la_tarea(self):
        tasks = am.build_tasks([at(0, "user_prompt"), at(5),
                                at(10 * 3600, "user_prompt"), at(10 * 3600 + 5)])
        self.assertEqual(tasks[0]["span_s"], 5)
        self.assertFalse(tasks[0]["clipped"])

    def test_span_se_recorta_a_max_task(self):
        tasks = am.build_tasks([at(0, "user_prompt"), at(10 * 3600),
                                at(10 * 3600 + 1, "user_prompt"),
                                at(10 * 3600 + 2)],
                               max_task_s=14400)
        self.assertEqual(tasks[0]["span_s"], 14400)
        self.assertTrue(tasks[0]["clipped"])

    def test_busy_nunca_supera_el_span(self):
        tasks = am.build_tasks([at(0, "user_prompt"), at(5, dur_s=3600)])
        self.assertLessEqual(tasks[0]["busy_s"], tasks[0]["span_s"])

    def test_turnos_fuera_de_orden_se_ordenan(self):
        tasks = am.build_tasks([at(60, "user_prompt"), at(0, "user_prompt"),
                                at(10)])
        # ordenado: prompt 0 → assistant 10 → prompt 60 (sin turnos = sin tarea)
        self.assertEqual(len(tasks), 1)
        self.assertAlmostEqual(tasks[0]["busy_s"], 10.0)


class SeriesTest(unittest.TestCase):
    def setUp(self):
        self.tasks = am.build_tasks([
            at(0, "user_prompt"), at(30, stop="aborted"),
            at(86400, "user_prompt"), at(86430),
        ])
        self.tasks[0]["source"] = "pi"
        self.tasks[0]["model"] = "m1"
        self.tasks[1]["source"] = "pi"
        self.tasks[1]["model"] = "m2"

    def test_series_indices_y_flags(self):
        days = sorted({t["_start"].date().isoformat() for t in self.tasks})
        s = am.series(self.tasks, days, ["pi"], ["m1", "m2"])
        self.assertEqual(s["day"], [0, 1])
        self.assertEqual(s["s"], [30, 30])
        self.assertEqual(s["sp"], [30, 30])
        self.assertEqual(s["f"][0], am.FLAG_ABORTED)
        self.assertEqual(s["model"], [0, 1])

    def test_daily_suma_por_dia(self):
        daily = am.daily_from_tasks(self.tasks)
        self.assertEqual(daily["2026-05-01"]["prompts"], 1)
        self.assertEqual(daily["2026-05-01"]["aborted"], 1)
        self.assertEqual(daily["2026-05-02"]["aborted"], 0)

    def test_dia_local_usa_la_tz_del_reporte(self):
        tz = am.tz_from_offset("-03")
        # 01:00Z = 22:00 del día anterior en -03: cambia de día
        late = {"_start": T0.replace(hour=1, minute=0)}
        self.assertEqual(am.task_day(late), "2026-05-01")
        self.assertEqual(am.task_day(late, tz), "2026-04-30")
        # 12:00Z = 09:00 local: mismo día
        self.assertEqual(am.task_day({"_start": T0}, tz), "2026-05-01")

    def test_tz_from_offset_formatos(self):
        self.assertEqual(am.tz_from_offset("-03").utcoffset(None),
                         timedelta(hours=-3))
        self.assertEqual(am.tz_from_offset("+05:30").utcoffset(None),
                         timedelta(hours=5, minutes=30))
        self.assertIsNone(am.tz_from_offset(""))
        self.assertIsNone(am.tz_from_offset("no-tz"))


class CoverageTest(unittest.TestCase):
    def test_cobertura_declara_fuentes_sin_turnos(self):
        cov = am.summarize_coverage({"sessions": 3}, {"sessions": 2})
        by = {c["source"]: c for c in cov}
        self.assertTrue(by["pi"]["abort_signal"])
        self.assertFalse(by["claude-cli"]["abort_signal"])
        self.assertFalse(by["amp"]["turn_level"])
        self.assertIsNone(by["amp"]["sessions"])
        self.assertIn("turnos", by["amp"]["note"])


class TasklessPromptTest(unittest.TestCase):
    """Prompt abortado antes de la respuesta ⇒ cost == 0, s == 0, y ninguna
    fila en sessions[] (la sesión solo entra si tuvo alguna tarea)."""

    def test_prompt_sin_respuesta_no_deja_tarea_ni_sesion(self):
        ses = {"source": "pi", "project": "p", "session_id": "s1"}
        tasks = am.build_tasks([at(0, "user_prompt", ses=ses)])
        self.assertEqual(tasks, [])
        self.assertEqual(am._sessions_from_tasks(tasks), [])

    def test_sesion_con_una_tarea_y_un_prompt_abortado(self):
        ses = {"source": "pi", "project": "p", "session_id": "s1"}
        tasks = am.build_tasks([
            at(0, "user_prompt", ses=ses),          # abortado: sin respuesta
            at(10, "user_prompt", ses=ses),
            at(20, ses=ses, cost=0.5,
               usage={"in": 3, "out": 1, "cache_read": 0, "cache_write": 0}),
        ])
        self.assertEqual(len(tasks), 1)
        self.assertAlmostEqual(tasks[0]["cost"], 0.5)
        self.assertEqual(tasks[0]["busy_s"], 10.0)
        self.assertEqual(am._sessions_from_tasks(tasks), [
            {"source": "pi", "project": "p", "session_id": "s1"}])
        s = am.series(tasks, ["2026-05-01"], ["pi"], ["m1"])
        self.assertEqual(s["s"], [10])
        self.assertEqual(s["sid"], [0])
        self.assertEqual(s["cost"], [0.5])
        self.assertEqual(s["tokens"],
                         [{"in": 3, "out": 1, "cache_read": 0,
                           "cache_write": 0}])


class SessionBoundaryTest(unittest.TestCase):
    """Una tarea no cruza sesiones: el primer turno de otra sesión cierra la
    tarea abierta, aunque no haya prompt nuevo en el medio."""

    def test_respuesta_de_otra_sesion_cierra_la_tarea(self):
        sa = {"source": "pi", "project": "a", "session_id": "s-a"}
        sb = {"source": "pi", "project": "b", "session_id": "s-b"}
        stats = {}
        tasks = am.build_tasks([
            at(0, "user_prompt", ses=sa), at(5, ses=sa),
            at(10, ses=sb),                                  # otra sesión
            at(20, "user_prompt", ses=sb), at(25, ses=sb),
        ], stats=stats)
        self.assertEqual(len(tasks), 2)
        sessions = am._sessions_from_tasks(tasks)
        self.assertEqual([s["session_id"] for s in sessions], ["s-a", "s-b"])
        self.assertEqual([t["_sid"] for t in tasks], [0, 1])
        self.assertEqual([s["project"] for s in sessions], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
