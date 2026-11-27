#!/usr/bin/env python3
"""
test_tracker_spend.py — gasto por proyecto × día y por sesión
(openspec add-session-spend-report)

- project_daily_spend[proj][day]: coste efectivo, coste real sub_cost y
  tokens por proyecto y día (sparse, agregación pura).
- Cuadres: contra el bucket diario global y contra project_monthly.
- Schema: la sección es requerida por specs/usage-report-v3.schema.json.

Run: python3 tests/test_tracker_spend.py
"""

import importlib.util
import json
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TRACKER_PATH = REPO / "scripts" / "usage-tracker.py"
SCHEMA_PATH = REPO / "specs" / "usage-report-v3.schema.json"


def load_tracker():
    spec = importlib.util.spec_from_file_location("usage_tracker", TRACKER_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ut = load_tracker()


def row(ts="2026-05-10T14:23:00+00:00", project="charly-coffee", tool="claude-cli",
        model_raw="claude-sonnet-4-6", session_file=None, **kw):
    """Row sintético con el shape exacto que producen los extractores."""
    base = {
        "source": "test", "tool": tool, "model_raw": model_raw,
        "model_family": "claude", "model_version": "sonnet-4.6",
        "project": project, "timestamp": ts,
        "hour": ut.hour_key(ut.parse_ts(ts)),
        "input_tokens": 1000, "output_tokens": 500,
        "cache_read_tokens": 100, "cache_write_tokens": 20,
        "cost_effective": 0.01,
    }
    if session_file:
        base["session_file"] = session_file
    base.update(kw)
    return base


def spend_rows():
    """2 proyectos × 2 días × 2 meses; claude-cli en suscripción y codex p2p."""
    return [
        # día 1, proyecto A: 2 interacciones claude-cli (suscripción → real 0)
        row(ts="2026-05-10T14:23:00+00:00", project="charly-coffee",
            session_file="019eaaaa-1111-2222-3333-444444444444.jsonl"),
        row(ts="2026-05-10T15:40:00+00:00", project="charly-coffee",
            session_file="019eaaaa-1111-2222-3333-444444444444.jsonl"),
        # día 1, proyecto B: codex pay-per-token (real = efectivo)
        row(ts="2026-05-10T16:10:00+00:00", project="charly-atril", tool="codex",
            model_raw="gpt-5.4", model_family="codex", model_version="gpt-5.4",
            session_file="rollout-2026-05-10-019ebbbb.jsonl"),
        # día 2, proyecto A: mes siguiente (rollup mensual)
        row(ts="2026-06-01T09:00:00+00:00", project="charly-coffee",
            session_file="019ecccc.jsonl"),
    ]


class TestProjectDailySpend(unittest.TestCase):

    def setUp(self):
        self.rep = ut.aggregate(spend_rows(), [])
        self.pds = self.rep.get("project_daily_spend")

    def test_seccion_existe_y_es_sparse(self):
        self.assertIsNotNone(self.pds, "falta project_daily_spend")
        self.assertEqual(
            sorted(self.pds.keys()), ["charly-atril", "charly-coffee"])
        # día sin actividad del proyecto: no hay clave
        self.assertNotIn("2026-05-11", self.pds["charly-coffee"])

    def test_coste_y_tokens_por_proyecto_dia(self):
        a = self.pds["charly-coffee"]["2026-05-10"]
        self.assertEqual(2, a["interactions"])
        self.assertAlmostEqual(0.02, a["cost_effective"], places=8)
        self.assertEqual(
            {"in": 2000, "out": 1000, "cache_read": 200, "cache_write": 40},
            a["tokens"])
        # claude-cli en suscripción → coste real 0 (get_sub_cost)
        self.assertAlmostEqual(0.0, a["cost_real"], places=8)
        b = self.pds["charly-atril"]["2026-05-10"]
        self.assertEqual(1, b["interactions"])
        # coste real consistente con get_sub_cost (misma semántica que buckets)
        expected_real, _, _ = ut.get_sub_cost("codex", "2026-05-10T16:10:00+00:00",
                                              b["cost_effective"])
        self.assertAlmostEqual(expected_real, b["cost_real"], places=8)

    def test_cuadre_con_bucket_diario_global(self):
        for day, bucket in self.rep["daily"].items():
            total = sum(
                self.pds[p].get(day, {}).get("cost_effective", 0.0)
                for p in self.pds)
            self.assertAlmostEqual(
                bucket["cost_effective"], total, places=2,
                msg=f"día {day}: project_daily_spend no cuadra con daily")

    def test_rollup_mensual_cuadra_con_project_monthly(self):
        pm = self.rep["project_monthly"]
        for proj, days in self.pds.items():
            months = sorted({d[:7] for d in days})
            for month in months:
                m_days = {d: v for d, v in days.items() if d[:7] == month}
                pm_m = pm[proj][month]
                self.assertAlmostEqual(
                    sum(v["cost_effective"] for v in m_days.values()),
                    pm_m["cost_effective"], places=8,
                    msg=f"{proj}/{month}: coste efectivo no cuadra")
                for t in ("in", "out", "cache_read", "cache_write"):
                    self.assertEqual(
                        sum(v["tokens"][t] for v in m_days.values()),
                        pm_m["tokens"][t],
                        msg=f"{proj}/{month}: tokens[{t}] no cuadra")
                self.assertAlmostEqual(
                    sum(v["cost_real"] for v in m_days.values()),
                    pm_m["cost_real"], places=8,
                    msg=f"{proj}/{month}: coste real no cuadra")


class TestSessionSpend(unittest.TestCase):

    def setUp(self):
        self.rep = ut.aggregate(spend_rows(), [])
        self.ss = self.rep.get("session_spend")
        self.by_id = {r["session_id"]: r for r in (self.ss or [])}

    def test_seccion_existe_una_fila_por_sesion(self):
        self.assertIsNotNone(self.ss, "falta session_spend")
        self.assertEqual(3, len(self.ss))
        self.assertIn("019eaaaa-1111-2222-3333-444444444444", self.by_id)

    def test_campos_minimos_y_coste(self):
        s = self.by_id["019eaaaa-1111-2222-3333-444444444444"]
        self.assertEqual("charly-coffee", s["project"])
        self.assertEqual("2026-05-10", s["day"])
        self.assertEqual("claude-cli", s["source"])
        self.assertEqual("claude-sonnet-4-6", s["model"])
        self.assertEqual(2, s["n_interactions"])
        self.assertAlmostEqual(0.02, s["cost_effective"], places=8)
        self.assertEqual(
            {"in": 2000, "out": 1000, "cache_read": 200, "cache_write": 40},
            s["tokens"])
        self.assertIsNone(s["cost_reason"])
        # start/end (drill-down: hora de inicio + flag medianoche)
        self.assertIn("2026-05-10T14:23", s["start"])
        self.assertIn("2026-05-10T15:40", s["end"])
        self.assertFalse(s["crosses_midnight"])

    def test_medianoche_flag_explicito(self):
        rows = [
            row(ts="2026-05-10T23:59:00-03:00", project="charly-coffee",
                session_file="019eeee.jsonl"),
            row(ts="2026-05-11T00:01:00-03:00", project="charly-coffee",
                session_file="019eeee.jsonl"),
        ]
        rep = ut.aggregate(rows, [])
        ss = rep["session_spend"]
        self.assertTrue(ss[0]["crosses_midnight"])
        self.assertEqual("2026-05-10", ss[0]["day"])

    def test_modelo_cambiado_es_mixed(self):
        rows = spend_rows() + [
            row(ts="2026-05-10T15:50:00+00:00", project="charly-coffee",
                session_file="019eaaaa-1111-2222-3333-444444444444.jsonl",
                model_raw="claude-opus-4-6", model_version="opus-4.6")]
        ss = {r["session_id"]: r for r in ut.aggregate(rows, [])["session_spend"]}
        self.assertEqual("mixed", ss["019eaaaa-1111-2222-3333-444444444444"]["model"])

    def test_coste_ausente_es_null_con_razon(self):
        rows = [row(ts="2026-05-10T14:23:00+00:00", cost_effective=0,
                    input_tokens=0, output_tokens=0, cache_read_tokens=0,
                    cache_write_tokens=0,
                    session_file="019edddd.jsonl")]
        ss = ut.aggregate(rows, [])["session_spend"]
        self.assertEqual(1, len(ss))
        self.assertIsNone(ss[0]["cost_effective"])
        self.assertIsNone(ss[0]["cost_real"])
        self.assertIn("sin usage.cost", ss[0]["cost_reason"])

    def test_sin_archivo_de_sesion_fallback_hash(self):
        rows = [row(ts="2026-05-10T14:23:00+00:00")]  # sin session_file
        ss = ut.aggregate(rows, [])["session_spend"]
        self.assertEqual(1, len(ss))
        self.assertTrue(ss[0]["session_id_fallback"])
        self.assertTrue(ss[0]["session_id"])

    def test_sesion_cruza_medianoche_atribuye_primer_dia(self):
        # TZ local del runner es -03: 23:59 y 00:01 locales son días distintos
        rows = [
            row(ts="2026-05-10T23:59:00-03:00", project="charly-coffee",
                session_file="019eeee.jsonl"),
            row(ts="2026-05-11T00:01:00-03:00", project="charly-coffee",
                session_file="019eeee.jsonl"),
        ]
        rep = ut.aggregate(rows, [])
        ss = rep["session_spend"]
        self.assertEqual(1, len(ss))
        self.assertEqual("2026-05-10", ss[0]["day"])
        self.assertAlmostEqual(0.02, ss[0]["cost_effective"], places=8)
        # el bucket diario reparta por interacción: día 1 tiene solo 0.01
        self.assertAlmostEqual(
            0.01, rep["daily"]["2026-05-10"]["cost_effective"], places=8)

    def test_cuadre_mismo_dia_con_project_daily_spend(self):
        rep = ut.aggregate(spend_rows(), [])
        for day in {r["day"] for r in rep["session_spend"]}:
            total_ss = sum(r["cost_effective"] for r in rep["session_spend"]
                           if r["day"] == day)
            total_pds = sum(v["cost_effective"]
                            for days in rep["project_daily_spend"].values()
                            for d, v in days.items() if d == day)
            self.assertAlmostEqual(total_pds, total_ss, places=8,
                                   msg=f"día {day}: session_spend no cuadra")

    def test_schema_requiere_session_spend(self):
        schema = json.loads(SCHEMA_PATH.read_text())
        self.assertIn("session_spend", schema["required"])


class TestSchemaDailySpend(unittest.TestCase):

    def test_schema_requiere_project_daily_spend(self):
        schema = json.loads(SCHEMA_PATH.read_text())
        self.assertIn("project_daily_spend", schema["required"])


# --- coffe-dnx.2: señales de calidad/enrutamiento en session_spend -----------

import shutil
import tempfile

FIXTURES = REPO / "tests" / "fixtures"
FIX_PI = FIXTURES / "pi_latency_session.jsonl"
FIX_CLAUDE = FIXTURES / "claude-cli_no_duration_session.jsonl"


def spend_row(ts="2026-05-10T14:23:00+00:00", session_file="s1.jsonl", **kw):
    return row(ts=ts, session_file=session_file, **kw)


class TestSessionSpendSignals(unittest.TestCase):
    """coffe-dnx.2: duration_ms/stop_reasons/tool_errors/model_chain/
    cache_share/cost_provenance en la fila de session_spend."""

    def _aggregate(self, rows):
        return ut.aggregate(rows, [])["session_spend"]

    def test_duraciones_conocidas_p50_p90(self):
        s = self._aggregate([
            spend_row(ts="2026-05-10T14:23:00+00:00", duration_ms=1000),
            spend_row(ts="2026-05-10T14:24:00+00:00", duration_ms=2000),
            spend_row(ts="2026-05-10T14:25:00+00:00", duration_ms=4000),
        ])[0]
        self.assertEqual(s["duration_ms"]["n"], 3)
        self.assertEqual(s["duration_ms"]["p50"], 2000)
        self.assertEqual(s["duration_ms"]["p90"], 3600)  # p90 interpolado: idx 1.8 → 2000+0.8*2000
        self.assertIsNone(s["duration_reason"])

    def test_sin_duration_ms_null_con_razon_nunca_cero(self):
        # rows sin la clave (fuente no-Pi) → null + razón, nunca 0
        s = self._aggregate([spend_row(), spend_row(ts="2026-05-10T14:24:00+00:00")])[0]
        self.assertIsNone(s["duration_ms"])
        self.assertTrue(s["duration_reason"])

    def test_stop_reasons_por_categoria(self):
        s = self._aggregate([
            spend_row(stop_reason="toolUse"),
            spend_row(ts="2026-05-10T14:24:00+00:00", stop_reason="aborted"),
            spend_row(ts="2026-05-10T14:25:00+00:00", stop_reason="stop"),
        ])[0]
        self.assertEqual(s["stop_reasons"],
                         {"toolUse": 1, "aborted": 1, "stop": 1})

    def test_tool_errors_calls_errors_p50(self):
        s = self._aggregate([
            spend_row(tool_calls=2, tool_errors=1, tool_durations_ms=[100, 300]),
            spend_row(ts="2026-05-10T14:24:00+00:00",
                      tool_calls=1, tool_errors=0, tool_durations_ms=[50]),
        ])[0]
        self.assertEqual(s["tool_errors"]["calls"], 3)
        self.assertEqual(s["tool_errors"]["errors"], 1)
        self.assertEqual(s["tool_errors"]["p50_ms"], 100)

    def test_model_switches_dos_model_change(self):
        # 2 model_change (el 1ro fija el modelo inicial) → 1 switch real
        s = self._aggregate([
            spend_row(model_change_idx=1, model_chain=["m-a"]),
            spend_row(ts="2026-05-10T14:24:00+00:00", model_change_idx=2,
                      model_chain=["m-a", "m-b"]),
        ])[0]
        self.assertEqual(s["model_switches"], 1)
        self.assertEqual(s["model_chain"], ["m-a", "m-b"])

    def test_model_switches_un_model_change_es_cero(self):
        # guarda del 100% falso: el model_change INICIAL no cuenta
        s = self._aggregate([spend_row(model_change_idx=1, model_chain=["m-a"])])[0]
        self.assertEqual(s["model_switches"], 0)
        self.assertEqual(s["model_chain"], ["m-a"])

    def test_cache_share(self):
        s = self._aggregate([spend_row(input_tokens=1000, output_tokens=500,
                                       cache_read_tokens=300,
                                       cache_write_tokens=200)])[0]
        self.assertEqual(s["cache_share"], round(300 / 2000, 4))

    def test_cache_share_null_sin_tokens(self):
        s = self._aggregate([spend_row(input_tokens=0, output_tokens=0,
                                       cache_read_tokens=0,
                                       cache_write_tokens=0,
                                       cost_effective=None)])[0]
        self.assertIsNone(s["cache_share"])

    def test_cost_provenance_reported_solo_si_todos(self):
        s = self._aggregate([
            spend_row(cost_reported=True),
            spend_row(ts="2026-05-10T14:24:00+00:00", cost_reported=True),
        ])[0]
        self.assertEqual(s["cost_provenance"], "reported")

    def test_cost_provenance_mixed(self):
        s = self._aggregate([
            spend_row(cost_reported=True),
            spend_row(ts="2026-05-10T14:24:00+00:00", cost_reported=False),
        ])[0]
        self.assertEqual(s["cost_provenance"], "mixed")

    def test_cost_provenance_estimated_sin_senial(self):
        s = self._aggregate([spend_row(), spend_row(ts="2026-05-10T14:24:00+00:00")])[0]
        self.assertEqual(s["cost_provenance"], "estimated")

    def test_metadata_latency_coverage(self):
        rep = ut.aggregate(spend_rows(), [])
        lc = rep["metadata"]["latency_coverage"]
        self.assertEqual(lc["measured"]["openrouter"], 7.5)
        self.assertEqual(lc["measured"]["zai"], 100.0)
        self.assertEqual(lc["measured"]["claude-cli"], 0.0)
        self.assertEqual(lc["measured"]["openai-codex"], 0.0)
        self.assertEqual(lc["measured"]["google-gemini-cli"], 0.0)


class TestSessionSpendFixtures(unittest.TestCase):
    """coffe-dnx.2: aceptación con fixtures reales de Pi y claude-cli."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _copy(self, fixture, name="s1.jsonl"):
        d = self.tmp / "charly-coffee"
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy(fixture, d / name)

    def _run(self, fixture):
        self._copy(fixture)
        rows, _ = ut.extract_pi(sessions_dir=self.tmp)
        return ut.aggregate(rows, [])["session_spend"][0]

    def test_pi_fixture_signals(self):
        s = self._run(FIX_PI)
        self.assertEqual(s["duration_ms"]["p50"], 2000)   # 1000/2000/4000 ms
        self.assertEqual(s["duration_ms"]["n"], 3)
        self.assertEqual(s["duration_ms"]["p90"], 3600)  # p90 interpolado: idx 1.8 → 2000+0.8*2000
        self.assertEqual(s["stop_reasons"]["aborted"], 1)
        self.assertEqual(s["tool_errors"]["errors"], 1)
        self.assertEqual(s["tool_errors"]["calls"], 2)
        self.assertEqual(s["tool_errors"]["p50_ms"], 210)  # [120,300] interpolado
        self.assertEqual(s["model_switches"], 1)           # 2 model_change - 1
        self.assertEqual(s["model_chain"], ["glm-5.3", "glm-5.3-flash"])
        self.assertEqual(s["cost_provenance"], "reported")

    def test_pi_tool_error_atribuido_tras_ultimo_turno(self):
        # el toolResult de cola (post-abort, sin assistant posterior)
        # se atribuye al último turno emitido — no se pierde
        s = self._run(FIX_PI)
        self.assertEqual(s["tool_errors"]["calls"], 2)

    def test_claude_cli_fixture_sin_duration(self):
        s = self._run(FIX_CLAUDE)
        self.assertIsNone(s["duration_ms"])
        self.assertTrue(s["duration_reason"])
        self.assertEqual(s["cost_provenance"], "estimated")
        self.assertEqual(s["model_switches"], 0)  # solo model_change inicial
        self.assertEqual(s["model_chain"], ["claude-sonnet-4-6"])

    def test_fixture_schema_valido(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema no disponible")
        schema = json.loads(SCHEMA_PATH.read_text())
        self._copy(FIX_PI, name="s2.jsonl")
        rows, _ = ut.extract_pi(sessions_dir=self.tmp)
        rep = ut.aggregate(rows, [])
        for srow in rep["session_spend"]:
            jsonschema.validate(srow, schema["properties"]["session_spend"]["items"])


if __name__ == "__main__":
    unittest.main()
