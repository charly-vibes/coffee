#!/usr/bin/env python3
"""
test_tracker_f2.py — emisiones nuevas del tracker para F2 (coffe-lat.3)

Cubre el alcance tracker del ticket coffe-lat.3:
- FPA-011: breakdown mensual por tool y model (interacciones + coste efectivo)
- FPA-012: project totals por mes (project_monthly)
- FPA-013: cargas pay-per-token separadas de suscripción (assumed)
- FPA-014: outcomes (commits/releases) donde haya GitHub token; si no, vacío
- FPA-019: tokens in/out/cache_read/cache_write por mes y modelo
- FPA-120: concurrencia etiquetada (proyectos/hora, pico de sesiones, switches)
- FPA-140: interacciones por kind (user prompts, assistant turns, tool calls)
- FPA-141: interacciones/coste excluidos por el filtro charly + share
- FPA-142: timezone registrada para bucketing
"""

import importlib.util
import json
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TRACKER_PATH = REPO / "scripts" / "usage-tracker.py"


def load_tracker():
    spec = importlib.util.spec_from_file_location("usage_tracker_f2", TRACKER_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ut = load_tracker()


def row(ts="2026-05-10T14:23:00+00:00", kind="assistant_turn", overrides=None):
    base = {
        "source": "test", "tool": "claude-cli",
        "model_raw": "claude-sonnet-4-6",
        "model_family": "claude", "model_version": "sonnet-4.6",
        "project": "charly-coffee", "timestamp": ts,
        "hour": ut.hour_key(ut.parse_ts(ts)),
        "input_tokens": 1000, "output_tokens": 500,
        "cache_read_tokens": 0, "cache_write_tokens": 0,
        "cost_effective": 0.01, "kind": kind,
    }
    if overrides:
        base.update(overrides)
    return base


def f2_rows():
    """2 meses, 2 tools, 2 modelos, 2 proyectos, kinds mezclados."""
    return [
        row('2026-05-10T14:23:00+00:00', 'tool_call', overrides=dict(project='charly-coffee')),
        row('2026-05-11T09:00:00+00:00', 'assistant_turn', overrides=dict(project='charly-atril', tool='codex', model_raw='gpt-5.4', model_family='codex', model_version='gpt-5.4', input_tokens=0, output_tokens=0)),
        row('2026-06-01T09:00:00+00:00', 'tool_call', overrides=dict(project='charly-coffee', input_tokens=2000, output_tokens=800)),
    ]


def f2_excluded():
    return [
        row('2026-05-10T15:00:00+00:00', 'tool_call', overrides=dict(project='otro-repo')),
        row('2026-06-02T10:00:00+00:00', 'assistant_turn', overrides=dict(project='trabajo-ajeno', tool='codex', model_raw='gpt-5.4', model_family='codex', model_version='gpt-5.4', input_tokens=0, output_tokens=0)),
    ]


def f2_sessions():
    return [
        {"project": "charly-coffee", "first_ts": "2026-05-10", "duration_msgs": 10,
         "n_turns": 5, "n_tools": 2, "n_skills": 1, "n_errors": 0,
         "n_compactions": 0, "has_agent": False},
        {"project": "charly-coffee", "first_ts": "2026-06-01", "duration_msgs": 30,
         "n_turns": 40, "n_tools": 3, "n_skills": 0, "n_errors": 0,
         "n_compactions": 0, "has_agent": True},
    ]


class TestToolModelBreakdown(unittest.TestCase):
    """FPA-011 + FPA-019: breakdown mensual por tool/model con coste y tokens."""

    @classmethod
    def setUpClass(cls):
        cls.report = ut.aggregate(f2_rows(), f2_sessions())

    def test_tools_con_interacciones_y_coste(self):
        tools = self.report["monthly"]["2026-05"]["tools"]
        self.assertEqual(2, len(tools))
        self.assertEqual(1, tools["claude-cli"]["interactions"])
        self.assertAlmostEqual(0.01, tools["claude-cli"]["cost_effective"])
        self.assertAlmostEqual(0.01, tools["codex"]["cost_effective"])

    def test_tools_anidan_modelos_para_arbol(self):
        """FPA-020: árbol Tool→Model necesita modelos por tool."""
        tools = self.report["monthly"]["2026-05"]["tools"]
        self.assertIn("claude-sonnet-4-6", tools["claude-cli"]["models"])
        self.assertIn("gpt-5.4", tools["codex"]["models"])

    def test_models_por_mes(self):
        models = self.report["monthly"]["2026-05"]["models"]
        self.assertEqual({"claude-sonnet-4-6", "gpt-5.4"}, set(models))
        self.assertEqual(1, models["gpt-5.4"]["interactions"])

    def test_tokens_by_model_por_mes(self):
        """FPA-019: in/out/cache-read/cache-write por mes y modelo."""
        tbm = self.report["monthly"]["2026-06"]["tokens_by_model"]
        self.assertIn("claude-sonnet-4-6", tbm)
        t = tbm["claude-sonnet-4-6"]
        self.assertEqual({"in", "out", "cache_read", "cache_write"}, set(t))
        self.assertEqual(2000, t["in"])
        self.assertEqual(800, t["out"])


class TestProjectMonthly(unittest.TestCase):
    """FPA-012: project totals por mes; FPA-021: project-by-model."""

    @classmethod
    def setUpClass(cls):
        cls.report = ut.aggregate(f2_rows(), f2_sessions())

    def test_project_monthly_existe_y_suma(self):
        pm = self.report["project_monthly"]
        self.assertEqual({"charly-coffee", "charly-atril"}, set(pm))
        self.assertEqual(1, pm["charly-coffee"]["2026-05"]["interactions"])
        self.assertEqual(1, pm["charly-coffee"]["2026-06"]["interactions"])

    def test_project_monthly_rollup_vs_monthly(self):
        """FPA-025 (base): suma de project_monthly = total mensual."""
        pm = self.report["project_monthly"]
        for ym, mo in self.report["monthly"].items():
            tot = sum(v[ym]["interactions"] for v in pm.values() if ym in v)
            self.assertEqual(mo["interactions"], tot)

    def test_project_models_para_drill(self):
        """FPA-021: proyecto → modelo con interacciones y coste."""
        pm = self.report["project_models"]
        self.assertIn("charly-coffee", pm)
        self.assertIn("claude-sonnet-4-6", pm["charly-coffee"])


class TestPayPerToken(unittest.TestCase):
    """FPA-013: cargas pay-per-token separadas de suscripción. Desde
    coffe-a31.2: *reported* cuando el ledger tiene cargas p2p reales del
    mes (créditos/reembolsos de data/charges.json); *assumed* si no."""

    def test_month_field_con_valor_assumed(self):
        rows = [row(ts="2026-05-10T14:23:00+00:00"),  # suscripción Max → real 0
                row(ts="2026-08-10T14:23:00+00:00")]  # post-Jun-19: Pro cubre → real 0
        rows.append(row(ts="2026-03-10T14:23:00+00:00"))  # pre-Pro → pay-per-token
        rep = ut.aggregate(rows, [])
        ppt = {m: mo.get("pay_per_token_charges") for m, mo in rep["monthly"].items()}
        prov = {m: mo.get("pay_per_token_provenance") for m, mo in rep["monthly"].items()}
        self.assertIsNotNone(ppt["2026-05"])
        self.assertAlmostEqual(0.01, ppt["2026-03"])
        self.assertEqual("assumed", prov["2026-03"])  # mar: solo cuota Max en el ledger
        # coffe-a31.2: may tiene cargas p2p reales (amp $10) → reported
        self.assertAlmostEqual(10.0, ppt["2026-05"])
        self.assertEqual("reported", prov["2026-05"])
        # nota de provenance en metadata
        self.assertIn("pay_per_token", json.dumps(rep["metadata"]))


class TestInteractionKinds(unittest.TestCase):
    """FPA-140: interacciones por kind."""

    def test_kinds_por_mes(self):
        report = ut.aggregate(f2_rows(), f2_sessions())
        kinds = report["monthly"]["2026-05"]["interaction_kinds"]
        self.assertEqual(1, kinds.get("tool_call", 0))
        self.assertEqual(1, kinds.get("assistant_turn", 0))

    def test_user_prompts_inyectados(self):
        report = ut.aggregate(f2_rows(), f2_sessions(),
                              interaction_kinds={"2026-05": {"user_prompt": 7}})
        kinds = report["monthly"]["2026-05"]["interaction_kinds"]
        self.assertEqual(7, kinds["user_prompt"])
        self.assertEqual(9, sum(kinds.values()))


class TestFilteredOut(unittest.TestCase):
    """FPA-141: interacciones y coste excluidos por el filtro charly."""

    def test_filtered_out_summary(self):
        report = ut.aggregate(f2_rows(), f2_sessions(), excluded=f2_excluded())
        filtered = report["filtered_out"]
        self.assertEqual(2, filtered["interactions"])
        self.assertAlmostEqual(0.02, filtered["cost_effective"])
        self.assertEqual({"claude-cli": 1, "codex": 1}, filtered["by_tool"])

    def test_share_total(self):
        report = ut.aggregate(f2_rows(), f2_sessions(), excluded=f2_excluded())
        total = report["metadata"]["total_interactions"]
        share = report["filtered_out"]["interactions"] / (total + 2)
        self.assertAlmostEqual(report["filtered_out"]["share"], round(2 / 5, 4))


class TestTimezone(unittest.TestCase):
    """FPA-142: timezone registrada para hourly/daily bucketing."""

    def test_metadata_timezone(self):
        report = ut.aggregate(f2_rows(), f2_sessions())
        self.assertTrue(report["metadata"].get("timezone"),
                        "timezone ausente en metadata")


class TestConcurrency(unittest.TestCase):
    """FPA-120: (a) proyectos/hora, (b) pico de sesiones, (c) switches/hora."""

    def test_concurrency_block_labeled(self):
        sessions = [
            {"project": "charly-coffee", "first_ts": "2026-05-10T14:00:00+00:00",
             "last_ts": "2026-05-10T14:30:00+00:00", "duration_msgs": 10,
             "n_turns": 5, "n_tools": 2, "n_skills": 1, "n_errors": 0,
             "n_compactions": 0, "has_agent": False},
            {"project": "charly-atril", "first_ts": "2026-05-10T14:10:00+00:00",
             "last_ts": "2026-05-10T15:00:00+00:00", "duration_msgs": 30,
             "n_turns": 20, "n_tools": 2, "n_skills": 0, "n_errors": 0,
             "n_compactions": 0, "has_agent": True},
        ]
        report = ut.aggregate(f2_rows(), sessions)
        c = report["concurrency"]
        self.assertIn("distinct_projects_per_hour", c)
        self.assertIn("peak_simultaneous_sessions", c)
        self.assertIn("project_switches_per_active_hour", c)
        # etiquetas paralelo vs contexto (FPA-120)
        self.assertEqual("parallel-agent", c["distinct_projects_per_hour"]["measure"])
        self.assertEqual("parallel-agent", c["peak_simultaneous_sessions"]["measure"])
        self.assertEqual("human-context-switching",
                         c["project_switches_per_active_hour"]["measure"])
        # 2 sesiones solapadas 14:10–14:30 → pico 2
        self.assertEqual(2, c["peak_simultaneous_sessions"]["peak"])

    def test_pico_de_sesiones_sin_timestamps_es_na(self):
        report = ut.aggregate(f2_rows(), f2_sessions())
        self.assertIsNone(report["concurrency"]["peak_simultaneous_sessions"]["peak"])


class TestSessionsMonthly(unittest.TestCase):
    """Sesiones por mes para KPIs (coste por sesión, autonomous share)."""

    def test_sessions_monthly(self):
        report = ut.aggregate(f2_rows(), f2_sessions())
        sm = report["sessions_monthly"]
        self.assertEqual(1, sm["2026-05"]["total"])
        self.assertEqual(1, sm["2026-06"]["total"])
        self.assertEqual(0, sm["2026-05"]["with_agent"])
        self.assertEqual(1, sm["2026-06"]["with_agent"])


class TestOutcomes(unittest.TestCase):
    """FPA-014: outcomes vacíos sin token, con nota (no crash)."""

    def test_outcomes_por_mes_sin_token(self):
        report = ut.aggregate(f2_rows(), f2_sessions())
        for mo in report["monthly"].values():
            self.assertEqual({}, mo.get("outcomes_by_project"))
        self.assertIn("outcomes", report["metadata"])


if __name__ == "__main__":
    unittest.main()
