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
        model_raw="claude-sonnet-4-6", **kw):
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
    base.update(kw)
    return base


def spend_rows():
    """2 proyectos × 2 días × 2 meses; claude-cli en suscripción y codex p2p."""
    return [
        # día 1, proyecto A: 2 interacciones claude-cli (suscripción → real 0)
        row(ts="2026-05-10T14:23:00+00:00", project="charly-coffee"),
        row(ts="2026-05-10T15:40:00+00:00", project="charly-coffee"),
        # día 1, proyecto B: codex pay-per-token (real = efectivo)
        row(ts="2026-05-10T16:10:00+00:00", project="charly-atril", tool="codex",
            model_raw="gpt-5.4", model_family="codex", model_version="gpt-5.4"),
        # día 2, proyecto A: mes siguiente (rollup mensual)
        row(ts="2026-06-01T09:00:00+00:00", project="charly-coffee"),
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


if __name__ == "__main__":
    unittest.main()
