#!/usr/bin/env python3
"""
test_tracker_aggregate.py — aggregate(): proyectos, monthly, golden

Hermanado con test_tracker.py (mismo helper row()/synthetic_*,
archivo autocontenido: sin conftest).
"""

import importlib.util
import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TRACKER_PATH = REPO / "scripts" / "usage-tracker.py"
GOLDEN = Path(__file__).resolve().parent / "golden" / "aggregate-snapshot.json"

REGEN_GOLDEN = os.environ.get("REGEN_GOLDEN") == "1"


def load_tracker():
    """Importa usage-tracker.py (guión: no importable como módulo normal)."""
    spec = importlib.util.spec_from_file_location("usage_tracker", TRACKER_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ut = load_tracker()


def row(ts="2026-05-10T14:23:00+00:00", overrides=None):
    """Row sintética con el shape exacto que producen los extractores;
    overrides ajusta campos puntuales (project, tool, tokens, ...)."""
    base = {
        "source": "test", "tool": "claude-cli",
        "model_raw": "claude-sonnet-4-6",
        "model_family": "claude", "model_version": "sonnet-4.6",
        "project": "charly-coffee", "timestamp": ts,
        "hour": ut.hour_key(ut.parse_ts(ts)),
        "input_tokens": 1000, "output_tokens": 500,
        "cache_read_tokens": 0, "cache_write_tokens": 0,
        "cost_effective": 0.01,
    }
    if overrides:
        base.update(overrides)
    return base


def synthetic_rows():
    """3 rows: 2 proyectos, 2 meses, 1 hora compartida (multitasking)."""
    return [
        row(ts="2026-05-10T14:23:00+00:00"),
        row(ts="2026-05-10T14:40:00+00:00",
            overrides=dict(project="charly-atril", tool="codex",
                           model_raw="gpt-5.4", model_family="codex",
                           model_version="gpt-5.4")),
        row(ts="2026-06-01T09:00:00+00:00"),
    ]


def synthetic_sessions():
    return [
        {"project": "charly-coffee", "first_ts": "2026-05-10", "duration_msgs": 10,
         "n_turns": 5, "n_tools": 2, "n_skills": 1, "n_errors": 0,
         "n_compactions": 0, "has_agent": False},
        {"project": "charly-atril", "first_ts": "2026-05-10", "duration_msgs": 300,
         "n_turns": 120, "n_tools": 4, "n_skills": 0, "n_errors": 1,
         "n_compactions": 1, "has_agent": True},
    ]

# ============================ aggregate ============================

class TestAggregate(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.report = ut.aggregate(synthetic_rows(), synthetic_sessions())

    def test_metadata_totals(self):
        m = self.report["metadata"]
        self.assertEqual(m["total_interactions"], 3)
        self.assertEqual(m["total_projects"], 2)
        self.assertEqual(m["total_hours"], 2)
        self.assertEqual(m["total_days"], 2)
        self.assertEqual(m["total_months"], 2)

    def test_monthly(self):
        self.assertIn("2026-05", self.report["monthly"])
        self.assertIn("2026-06", self.report["monthly"])
        self.assertEqual(self.report["monthly"]["2026-05"]["interactions"], 2)
        self.assertEqual(self.report["monthly"]["2026-06"]["interactions"], 1)

    def test_projects_sorted_by_cost_desc(self):
        costs = [v["cost_effective"] for v in self.report["projects"].values()]
        self.assertEqual(costs, sorted(costs, reverse=True))

    def test_sessions_length_distribution(self):
        ld = self.report["sessions"]["length_distribution"]
        self.assertEqual(ld.get("1-10"), 1)
        self.assertEqual(ld.get("101-300"), 1)
        self.assertEqual(self.report["sessions"]["with_agent"], 1)
        self.assertEqual(self.report["sessions"]["total_api_errors"], 1)

    def test_multitasking_detects_shared_hour(self):
        mt = self.report["multitasking"]["hourly"]
        self.assertEqual(mt["total_active_hours"], 2)
        self.assertEqual(mt["hours_with_multiple_projects"], 1)
        self.assertEqual(mt["pct_hours_multitasking"], 50.0)

    def test_context_switches(self):
        # semántica real: cambio de proyecto entre requests consecutivos,
        # aunque crucen la medianoche (coffe→atril=1, atril→coffe=1)
        cs = self.report["multitasking"]["context_switches"]
        self.assertEqual(cs["total"], 2)
        self.assertEqual(cs["max_in_one_day"], 1)

    def test_project_daily_dense_matrix(self):
        pd_ = self.report["project_daily"]
        self.assertEqual(set(pd_["matrix"].keys()), {"charly-coffee", "charly-atril"})
        for p, series in pd_["matrix"].items():
            self.assertEqual(len(series), len(pd_["days"]))  # densa, sin huecos
        # días continuos: 2026-05-10 → 2026-06-01 = 23 días
        self.assertEqual(len(pd_["days"]), 23)

    def test_no_dead_keys(self):
        self.assertNotIn("tools_summary", self.report)


class TestProjectMonthlyTokens(unittest.TestCase):
    """add-project-cost-token-timeline (d1/t1.2): project_monthly acumula
    tokens (in/out/cache_read) y cost_real por proyecto×mes, para la timeline
    de volumen y coste por proyecto del dashboard (d2)."""

    @classmethod
    def setUpClass(cls):
        rows = [
            row('2026-05-10T14:23:00+00:00', overrides=dict(project='charly-coffee', tool='gemini-cli', model_raw='gemini-3-pro', model_family='gemini', model_version='gemini-3-pro', input_tokens=1000, output_tokens=500, cache_read_tokens=200, cost_effective=0.01)),
            row('2026-05-11T09:00:00+00:00', overrides=dict(project='charly-coffee', tool='gemini-cli', model_raw='gemini-3-pro', model_family='gemini', model_version='gemini-3-pro', input_tokens=0, output_tokens=0, cache_read_tokens=0, cost_effective=0.0)),
            row('2026-06-01T09:00:00+00:00', overrides=dict(project='charly-coffee', tool='gemini-cli', model_raw='gemini-3-pro', model_family='gemini', model_version='gemini-3-pro', input_tokens=100, output_tokens=50, cache_read_tokens=0, cost_effective=0.005)),
        ]
        rep = ut.aggregate(rows, synthetic_sessions())
        cls.pm = rep["project_monthly"]["charly-coffee"]

    def test_tokens_acumulan_por_proyecto_mes(self):
        # gemini-cli no está suscripto en el calendario sintético → real = effective
        may = self.pm["2026-05"]
        self.assertEqual(1000, may["tokens"]["in"])
        self.assertEqual(500, may["tokens"]["out"])
        self.assertEqual(200, may["tokens"]["cache_read"])

    def test_project_multiple_months(self):
        self.assertEqual(set(self.pm.keys()), {"2026-05", "2026-06"})
        self.assertEqual(100, self.pm["2026-06"]["tokens"]["in"])

    def test_mes_coste_cero_con_tokens(self):
        # mes con coste 0.0 pero tokens > 0: el volumen timeline no depende del coste
        # (aquí la row 2026-06 tiene coste pero 0 tokens: usa la row de 2026-05 con 0)
        # para la aserción dedicada usamos el mes donde el coste es 0 y tokens no:
        # tomamos la row 2 (2026-05, 0 tokens) vs mes completo: el caso limpio es
        # un mes entero sin coste — sintetizamos dentro del test.
        rows = [row('2026-07-01T09:00:00+00:00', overrides=dict(project='charly-coffee', tool='gemini-cli', model_raw='gemini-3-pro', model_family='gemini', model_version='gemini-3-pro', input_tokens=42, output_tokens=0, cache_read_tokens=0, cost_effective=0.0))]
        pm = ut.aggregate(rows, synthetic_sessions())["project_monthly"]
        july = pm["charly-coffee"]["2026-07"]
        self.assertEqual(0.0, july["cost_effective"])
        self.assertEqual(42, july["tokens"]["in"])


# ============================ golden ============================

class TestPiCostEstimate(unittest.TestCase):
    """coffe-8t8: logs Pi sin cost (gemini-cli) → estimación pay-per-token
    con estimate_cost (rates del config versionados por fecha)."""

    def _extract(self, usage):
        entry = {"type": "message", "timestamp": "2026-05-10T14:23:00Z",
                 "provider": "google-gemini-cli",
                 "message": {"role": "assistant", "model": "gemini-3-pro",
                             "usage": usage}}
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "charly-coffee"
            d.mkdir()
            (d / "s1.jsonl").write_text(json.dumps(entry) + "\n")
            rows, _ = ut.extract_pi(sessions_dir=Path(tmp))
        return rows

    def test_gemini_sin_cost_se_estima(self):
        rows = self._extract({"input_tokens": 1000, "output_tokens": 500,
                              "cacheRead": 100, "cacheWrite": 0, "cost": {}})
        self.assertEqual(1, len(rows))
        ts = date(2026, 5, 10)
        expected = ut.estimate_cost("gemini", "gemini-3-pro", 1000, 500,
                                    100, 0, when=ts)
        self.assertEqual(expected, rows[0]["cost_effective"])
        self.assertGreater(rows[0]["cost_effective"], 0)

    def test_cost_presente_pasa_directo(self):
        rows = self._extract({"input_tokens": 1000, "output_tokens": 500,
                              "cost": {"total": 0.42}})
        self.assertEqual(0.42, rows[0]["cost_effective"])

    def test_cero_coste_y_cero_tokens_queda_cero(self):
        rows = self._extract({"input_tokens": 0, "output_tokens": 0,
                              "cost": {}})
        self.assertEqual(0.0, rows[0]["cost_effective"])


class TestGolden(unittest.TestCase):

    @staticmethod
    def _injected_skills():
        """Skills/comandos herméticos: derivados del propio golden.

        aggregate() es pura e inyectable (project.md: probar con rows
        sintéticos, no logs reales) — inyectar evita que la colección en
        vivo de skills de la máquina contamine el snapshot (drift entre
        entornos). Sin golden todavía: colección en vivo (regen inicial).
        """
        if GOLDEN.exists():
            g = json.loads(GOLDEN.read_text())
            return (g.get("skills") or {},
                    {p: v.get("skills") or {}
                     for p, v in (g.get("projects") or {}).items()},
                    g.get("commands") or {})
        return None

    def test_aggregate_golden(self):
        skills = self._injected_skills()
        report = ut.aggregate(synthetic_rows(), synthetic_sessions(),
                              skills_total=skills[0],
                              skills_by_project=skills[1],
                              commands=skills[2])
        if REGEN_GOLDEN:
            GOLDEN.parent.mkdir(parents=True, exist_ok=True)
            GOLDEN.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n")
            self.skipTest("golden regenerado")
        self.assertTrue(GOLDEN.exists(),
                        "Falta golden; corre REGEN_GOLDEN=1 python3 tests/test_tracker.py")
        expected = json.loads(GOLDEN.read_text())
        actual = json.loads(json.dumps(report, default=str))
        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
