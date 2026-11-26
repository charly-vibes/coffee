#!/usr/bin/env python3
"""test_agent_metrics.py — contrato del tracker de comportamiento del agente.

Cubre las funciones puras (sin logs vivos): reconstrucción de tareas desde
turnos, flags de pushback, series compactas, agregados por día/mes, panel de
repos desde git y el schema del dataset.
"""

import importlib.util
import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "agent-metrics-tracker.py"
SCHEMA = REPO / "specs" / "agent-metrics.schema.json"

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

    def test_monthly_tasks_percentiles(self):
        mt = am.monthly_tasks(self.tasks)
        self.assertEqual(mt["2026-05"]["tasks"], 2)
        self.assertIn("p90_s", mt["2026-05"])


class ReposTest(unittest.TestCase):
    def test_repo_metrics_agrega_dias_y_commits_por_mes(self):
        def run(args, cwd):
            self.assertEqual(args[0], "log")
            return "2026-01-05\n2026-01-05\n2026-02-10\n"

        out = am.repo_metrics([Path("/tmp/x/uno")], run=run, prefix="/tmp/x")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["name"], "uno")
        self.assertEqual(out[0]["first_commit"], "2026-01-05")
        self.assertEqual(out[0]["days_by_month"], {"2026-01": 1, "2026-02": 1})
        self.assertEqual(out[0]["commits_by_month"], {"2026-01": 2, "2026-02": 1})

    def test_repo_sin_commits_se_omite(self):
        out = am.repo_metrics([Path("/tmp/x/vacio")], run=lambda a, c: "",
                              prefix="/tmp/x")
        self.assertEqual(out, [])

    def test_git_roto_se_omite(self):
        self.assertEqual(am.repo_metrics([Path("/tmp/x/y")],
                                        run=lambda a, c: None, prefix="/tmp/x"),
                         [])


class ConfigTest(unittest.TestCase):
    def test_config_tiene_defaults_y_bloque_del_sitio(self):
        cfg = am.load_config(REPO / "config" / "fpa.json")
        for key in ("gap_cap_minutes", "retry_window_minutes", "repo_roots",
                    "min_commit_days", "min_returning_months"):
            self.assertIn(key, cfg)
        self.assertEqual(cfg["config_source"], "config/fpa.json")

    def test_config_ausente_usa_defaults(self):
        cfg = am.load_config(REPO / "config" / "no-existe.json")
        self.assertEqual(cfg["gap_cap_minutes"], am.DEFAULTS["gap_cap_minutes"])
        self.assertIsNone(cfg["config_source"])


class SchemaTest(unittest.TestCase):
    def test_schema_declara_las_claves_del_dataset(self):
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        for key in ("metadata", "days", "sources", "models", "daily", "tasks",
                    "monthly_tasks", "repos", "months"):
            self.assertIn(key, schema["required"])
        self.assertIn("sp", schema["properties"]["tasks"]["required"])

    def test_dataset_real_valida_contra_schema(self):
        import sys
        sys.path.insert(0, str(REPO / "scripts"))
        from schema_validate import validate_against_schema
        data = REPO / "data" / "agent_metrics.json"
        if not data.exists():
            self.skipTest("dataset no generado")
        errors = validate_against_schema(
            json.loads(data.read_text(encoding="utf-8")),
            json.loads(SCHEMA.read_text(encoding="utf-8")))
        self.assertEqual(errors, [])


class CoverageTest(unittest.TestCase):
    def test_cobertura_declara_fuentes_sin_turnos(self):
        cov = am.summarize_coverage({"sessions": 3}, {"sessions": 2})
        by = {c["source"]: c for c in cov}
        self.assertTrue(by["pi"]["abort_signal"])
        self.assertFalse(by["claude-cli"]["abort_signal"])
        self.assertFalse(by["amp"]["turn_level"])
        self.assertIsNone(by["amp"]["sessions"])
        self.assertIn("turnos", by["amp"]["note"])


if __name__ == "__main__":
    unittest.main()
