#!/usr/bin/env python3
"""test_agent_metrics.py — contrato del tracker de comportamiento del agente.

Cubre las funciones puras (sin logs vivos): reconstrucción de tareas desde
turnos, flags de pushback, series compactas, agregados por día/mes, panel de
repos desde git y el schema del dataset.
"""

import importlib.util
import json
import tempfile
import unittest
from collections import defaultdict
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


class ReposScanTest(unittest.TestCase):
    def test_raiz_inexistente_se_reporta(self):
        paths, missing = am.find_git_repos(["/no/existe", str(REPO)])
        self.assertEqual(missing, ["/no/existe"])
        self.assertIsInstance(paths, list)


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


class ReposTest(unittest.TestCase):
    def test_repo_metrics_agrega_dias_y_commits_por_mes(self):
        def run(args, cwd):
            self.assertEqual(args[0], "log")
            return "2026-01-05\n2026-01-05\n2026-02-10\n"

        out = am.repo_metrics([Path("/tmp/x/uno")], run=run, prefixes=["/tmp/x"])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["name"], "uno")
        self.assertEqual(out[0]["first_commit"], "2026-01-05")
        self.assertEqual(out[0]["days_by_month"], {"2026-01": 1, "2026-02": 1})

    def test_repo_sin_commits_se_omite(self):
        out = am.repo_metrics([Path("/tmp/x/vacio")], run=lambda a, c: "",
                              prefixes=["/tmp/x"])
        self.assertEqual(out, [])

    def test_git_roto_se_omite(self):
        self.assertEqual(am.repo_metrics([Path("/tmp/x/y")],
                                        run=lambda a, c: None,
                                        prefixes=["/tmp/x"]), [])


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
                    "repos", "months"):
            self.assertIn(key, schema["required"])
        self.assertNotIn("monthly_tasks", schema["required"])
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


class ValidateCliTest(unittest.TestCase):
    """Escenario del spec: dataset inválido → --validate sale ≠ 0 (EDGE-003)."""

    def test_dataset_invalido_sale_con_error(self):
        import subprocess
        import tempfile
        data = json.loads((REPO / "data" / "agent_metrics.json").read_text(
            encoding="utf-8"))
        del data["daily"]
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(data, fh)
            path = fh.name
        try:
            proc = subprocess.run(
                ["python3", str(SCRIPT), "--validate-only", path],
                capture_output=True, text=True, timeout=120)
        finally:
            Path(path).unlink(missing_ok=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("schema:", proc.stderr)


class CoverageTest(unittest.TestCase):
    def test_cobertura_declara_fuentes_sin_turnos(self):
        cov = am.summarize_coverage({"sessions": 3}, {"sessions": 2})
        by = {c["source"]: c for c in cov}
        self.assertTrue(by["pi"]["abort_signal"])
        self.assertFalse(by["claude-cli"]["abort_signal"])
        self.assertFalse(by["amp"]["turn_level"])
        self.assertIsNone(by["amp"]["sessions"])
        self.assertIn("turnos", by["amp"]["note"])


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


class SchemaBumpTest(unittest.TestCase):
    """Schema bump coffe-dnx.3: sid/cost/tokens en tasks, sessions[] arriba y
    el alcance del gate en coverage.cost_parity. Opcionales: datasets
    anteriores al cambio no los traen (regen pendiente)."""

    def test_schema_declara_las_claves_nuevas(self):
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        tasks_props = schema["properties"]["tasks"]["properties"]
        for key in ("sid", "cost", "tokens"):
            self.assertIn(key, tasks_props)
        sessions = schema["properties"]["sessions"]
        self.assertEqual(sessions["type"], "array")
        self.assertEqual(sessions["items"]["required"],
                         ["source", "project", "session_id"])
        cov = schema["properties"]["metadata"]["properties"]["coverage"]["items"]
        self.assertIn("cost_parity", cov["properties"])


class BeadIndexFixtureTest(unittest.TestCase):
    """coffe-dnx.4: fixture de 2 repos — uno con lead_time medible y un bead
    sin started_at, otro fuera del filtro charly → excluded."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        cls.prefixes = [str(base)]

        def repo(name, issues):
            d = base / name
            (d / ".beads").mkdir(parents=True)
            (d / ".beads" / "issues.jsonl").write_text(
                "".join(json.dumps(i) + "\n" for i in issues),
                encoding="utf-8")
            return d / ".beads" / "issues.jsonl"

        repo("charly/uno", [{
            "_type": "issue", "id": "bead-1a2",
            "title": "SECRETO-title", "description": "SECRETO-desc",
            "created_at": "2026-05-01T10:00:00Z",
            "started_at": "2026-05-01T10:00:00Z",
            "closed_at": "2026-05-01T11:30:00Z", "status": "closed",
        }])
        repo("sk/dos", [{
            "_type": "issue", "id": "bead-3b4",
            "notes": "SECRETO-notes",
            "created_at": "2026-05-02T10:00:00Z",
            "closed_at": "2026-05-02T12:00:00Z", "status": "closed",
        }])
        repo("donp/otros", [{
            "_type": "issue", "id": "bead-9z9",
            "design": "SECRETO-design",
            "created_at": "2026-05-03T10:00:00Z", "status": "open",
        }])
        cls.doc = am.build_bead_index([base / "charly/uno", base / "sk/dos",
                                       base / "donp/otros"],
                                      prefixes=cls.prefixes)
        cls.by = {r["name"]: r for r in cls.doc["repos"]}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_lead_time_y_completeness(self):
        row = self.by["charly/uno"]["months"]["2026-05"]
        self.assertEqual(row["created"], 1)
        self.assertEqual(row["started"], 1)
        self.assertEqual(row["closed"], 1)
        self.assertEqual(row["lead_time_p50_min"], 90)
        self.assertEqual(row["lead_time_n"], 1)
        self.assertEqual(row["completeness"], 1.0)

    def test_bead_sin_started_at_no_entra_en_lead_time(self):
        row = self.by["sk/dos"]["months"]["2026-05"]
        self.assertEqual(row["closed"], 1)
        self.assertEqual(row["lead_time_n"], 0)
        self.assertIsNone(row["lead_time_p50_min"])
        self.assertEqual(row["completeness"], 0.0)  # lead_time_n / closed

    def test_reopened_es_null_con_razon(self):
        for repo in self.doc["repos"]:
            for row in repo["months"].values():
                self.assertIsNone(row["reopened"])
        self.assertEqual(self.doc["metadata"]["reasons"]["reopened"],
                         "no event log in no-db mode")

    def test_repo_fuera_de_is_charly_va_a_excluded(self):
        self.assertNotIn("donp/otros", self.by)
        ex = {e["name"]: e["issues"] for e in self.doc["excluded"]}
        self.assertEqual(ex.get("donp/otros"), 1)


class BeadPrivacyGuardTest(unittest.TestCase):
    """CLRT-003: title/description/notes/design de issues no se publican
    (los ids sí: son opacos). El guard falla si el artefacto los trae."""

    def test_guard_detecta_claves_de_texto(self):
        base = {"repos": [], "excluded": [], "metadata": {}}
        with_title = {**base, "repos": [
            {"name": "charly/x", "months": {"2026-05": {"title": "x"}}}]}
        self.assertTrue(am.bead_privacy_errors(with_title))
        self.assertTrue(am.bead_privacy_errors(
            {**base, "excluded": [{"name": "d/y", "issues": 1,
                                   "description": "x"}]}))
        self.assertEqual(am.bead_privacy_errors(base), [])

    def test_artefacto_real_sin_texto_de_issues(self):
        data = REPO / "data" / "bead_index.json"
        if not data.exists():
            self.skipTest("artefacto no generado")
        doc = json.loads(data.read_text(encoding="utf-8"))
        self.assertEqual(am.bead_privacy_errors(doc), [])


class BeadIndexSchemaTest(unittest.TestCase):
    """Schema del artefacto + CLI --validate-bead."""

    def test_schema_declara_las_claves_del_mes(self):
        schema = json.loads((REPO / "specs" / "bead-index.schema.json")
                            .read_text(encoding="utf-8"))
        self.assertEqual(schema["required"], ["metadata", "repos", "excluded"])
        month = schema["definitions"]["month"]
        for key in ("created", "started", "closed", "lead_time_p50_min",
                    "lead_time_n", "completeness", "reopened"):
            self.assertIn(key, month["required"])
        self.assertEqual(month["properties"]["reopened"]["type"], "null")

    def test_cli_valida_artefacto_contra_schema(self):
        import subprocess
        import tempfile
        data = REPO / "data" / "bead_index.json"
        if not data.exists():
            self.skipTest("artefacto no generado")
        self.assertEqual(subprocess.run(
            ["python3", str(SCRIPT), "--validate-bead", str(data)],
            capture_output=True, text=True, timeout=120).returncode, 0)
        bad = json.loads(data.read_text(encoding="utf-8"))
        del bad["excluded"]
        with tempfile.NamedTemporaryFile("w", suffix=".json",
                                         delete=False) as fh:
            json.dump(bad, fh)
            path = fh.name
        try:
            proc = subprocess.run(
                ["python3", str(SCRIPT), "--validate-bead", path],
                capture_output=True, text=True, timeout=120)
        finally:
            Path(path).unlink(missing_ok=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("schema:", proc.stderr)


class TaskBeadsTest(unittest.TestCase):
    """coffe-dnx.5: atribución tarea↔bead y fórmula de coste por bead."""

    SES = {"source": "pi", "project": "coffee", "session_id": "s1"}

    def bead(self, bid, started=-3600, closed=3600):
        return {"id": bid, "repo": "charly/coffee", "path": Path("/x/coffee"),
                "started_at": T0 + timedelta(seconds=started),
                "closed_at": (T0 + timedelta(seconds=closed)) if closed else None}

    def tasks_with_prompt(self, *prompts, session_id="s1"):
        turns, ts = [], 0
        ses = dict(self.SES, session_id=session_id)
        for p in prompts:
            turns.append(at(ts, "user_prompt", text=p, ses=ses))
            turns.append(at(ts + 10, ses=ses))
            ts += 30
        tasks = am.build_tasks(turns)
        am._sessions_from_tasks(tasks)
        return tasks

    def test_prompt_con_id_es_explicit_1(self):
        rows, _ = am.attribute_task_beads(
            self.tasks_with_prompt("arregla coffe-abc ya"),
            [self.bead("coffe-abc")])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["method"], "explicit")
        self.assertEqual(rows[0]["confidence"], 1.0)
        self.assertEqual(rows[0]["bead"], "coffe-abc")

    def test_temporal_dentro_de_ventana_nosuma_coste(self):
        tasks = self.tasks_with_prompt("sin ids acá")
        rows, sessions = am.attribute_task_beads(
            tasks, [self.bead("coffe-abc")])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["method"], "temporal")
        self.assertLessEqual(rows[0]["confidence"], 0.5)
        self.assertEqual(rows[0]["window"]["started_at"],
                         (T0 - timedelta(seconds=3600)).isoformat())
        per_bead, _ = am.compute_bead_costs(
            rows, sessions, [{"session_id": "s1", "cost_effective": 7.0}])
        # temporal NO contribuye al coste (solo n_temporal)
        self.assertEqual(per_bead["coffe-abc"]["cost_per_bead"], 0.0)
        self.assertEqual(per_bead["coffe-abc"]["n_sessions"], 0)
        self.assertEqual(per_bead["coffe-abc"]["n_temporal"], 1)

    def test_temporal_emitido_como_explicit_falla(self):
        # guarda: una fila temporal lleva method temporal y confianza <= 0.5;
        # emitirla como explicit (confidence 1.0) rompe ambos asserts
        rows, _ = am.attribute_task_beads(
            self.tasks_with_prompt("sin ids acá"), [self.bead("coffe-abc")])
        self.assertTrue(rows)
        for r in rows:
            self.assertNotEqual(r["method"], "explicit")
            self.assertLessEqual(r["confidence"], 0.5)

    def test_dos_sesiones_explicit_suman_coste_exacto(self):
        tasks = (self.tasks_with_prompt("toca coffe-abc", session_id="s1")
                 + self.tasks_with_prompt("toca coffe-abc", session_id="s2"))
        rows, sessions = am.attribute_task_beads(tasks, [self.bead("coffe-abc")])
        per_bead, _ = am.compute_bead_costs(rows, sessions, [
            {"session_id": "s1", "cost_effective": 2.0},
            {"session_id": "s2", "cost_effective": 3.5}])
        self.assertEqual(per_bead["coffe-abc"]["n_sessions"], 2)
        self.assertAlmostEqual(per_bead["coffe-abc"]["cost_per_bead"], 5.5)

    def test_sesion_en_dos_beads_se_reparte_split(self):
        tasks = self.tasks_with_prompt("coffe-abc y coffe-def")
        rows, sessions = am.attribute_task_beads(
            tasks, [self.bead("coffe-abc"), self.bead("coffe-def")])
        per_bead, _ = am.compute_bead_costs(
            rows, sessions, [{"session_id": "s1", "cost_effective": 10.0}])
        splits = [r for r in rows if r.get("split")]
        self.assertEqual(len(splits), 2)
        self.assertAlmostEqual(sum(per_bead[b]["cost_per_bead"]
                                   for b in ("coffe-abc", "coffe-def")),
                               10.0, places=6)
        self.assertAlmostEqual(per_bead["coffe-abc"]["cost_per_bead"], 5.0)
        self.assertAlmostEqual(per_bead["coffe-def"]["cost_per_bead"], 5.0)

    def test_sin_beads_la_tarea_no_aparece(self):
        rows, _ = am.attribute_task_beads(
            self.tasks_with_prompt("nada que ver"), [])
        self.assertEqual(rows, [])

    def test_doc_completo_valida_contra_schema(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "charly" / "coffee"
            (repo / ".beads").mkdir(parents=True)
            issues = [{"id": "coffe-abc",
                       "started_at": (T0 - timedelta(hours=1)).isoformat(),
                       "closed_at": (T0 + timedelta(hours=1)).isoformat()}]
            (repo / ".beads" / "issues.jsonl").write_text(
                "\n".join(json.dumps(i) for i in issues), encoding="utf-8")
            tasks = self.tasks_with_prompt("implemento coffe-abc")
            spend = [{"session_id": "s1", "cost_effective": 4.0}]
            doc = am.build_task_beads_doc(
                tasks, [repo], prefixes=[tmp], session_spend=spend,
                run=lambda args, cwd: "")
        self.assertEqual(len(doc["task_beads"]), 1)
        self.assertEqual(doc["task_beads"][0]["method"], "explicit")
        self.assertEqual(doc["beads"][0]["cost_per_bead"], 4.0)
        self.assertEqual(doc["n_beads_sin_coste"], 0)
        self.assertAlmostEqual(doc["cost_per_bead_p50"], 4.0)
        self.assertEqual(am._task_beads_schema_errors(doc), [])

    def test_cli_valida_task_beads_contra_schema(self):
        import subprocess
        data = REPO / "data" / "task_beads.json"
        if not data.exists():
            self.skipTest("artefacto no generado")
        self.assertEqual(subprocess.run(
            ["python3", str(SCRIPT), "--validate-task-beads", str(data)],
            capture_output=True, text=True, timeout=120).returncode, 0)


if __name__ == "__main__":
    unittest.main()
