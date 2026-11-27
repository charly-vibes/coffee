#!/usr/bin/env python3
"""
test_report_refresh.py — coffe-6i8: el dataset commiteado es previo a F2, y
regenerarlo expone 2 bugs latentes del schema:

1. definitions.monthlyBucket.tokens_by_model exige 3 niveles
   (tool → modelo → tokens) pero el tracker emite 2 (modelo → tokens);
2. concurrency.peak_simultaneous_sessions.reason exige string pero el tracker
   emite None cuando el pico SÍ es computable.

Convención del repo: el schema describe la emisión real del tracker (fuente:
tests/golden/aggregate-snapshot.json y viz-fpa.py, que ya consumen 2 niveles).
"""

import importlib.util
import json
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TRACKER_PATH = REPO / "scripts" / "usage-tracker.py"
SCHEMA_PATH = REPO / "specs" / "usage-report-v3.schema.json"
DATASET_PATH = REPO / "data" / "usage_report_v3.json"


def load_tracker():
    spec = importlib.util.spec_from_file_location("usage_tracker", TRACKER_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ut = load_tracker()


def _schema_errors(self, doc):
    """Violaciones al schema usage-report-v3 ([] si el doc valida)."""
    try:
        import jsonschema
    except ImportError:
        self.skipTest("jsonschema no instalado")
    schema = json.loads(SCHEMA_PATH.read_text())
    return sorted(
        f"{'/'.join(str(c) for c in e.absolute_path)}: {e.message}"
        for e in jsonschema.Draft7Validator(schema).iter_errors(doc))


def _validate(self, doc):
    errors = _schema_errors(self, doc)
    self.assertEqual([], errors, "violaciones al schema usage-report-v3")


def rows():
    """Dos interacciones, dos modelos, un mes."""
    base = {"source": "test", "tool": "claude-cli", "project": "charly-coffee",
            "model_family": "claude", "model_version": "sonnet-4.6",
            "hour": "2026-05-10T14", "input_tokens": 1000, "output_tokens": 500,
            "cache_read_tokens": 0, "cache_write_tokens": 0,
            "cost_effective": 0.01}
    return [dict(base, model_raw="claude-sonnet-4-6",
                 timestamp="2026-05-10T14:23:00+00:00"),
            dict(base, model_raw="claude-opus-4.7",
                 timestamp="2026-05-10T15:23:00+00:00")]


class TestTokensByModelShape(unittest.TestCase):
    """coffe-6i8: el schema describe la emisión real del tracker (2 niveles)."""

    @classmethod
    def setUpClass(cls):
        cls.report = ut.aggregate(rows(), [])

    def test_emision_dos_niveles(self):
        """FPA-019: modelo → {in, out, cache_read, cache_write}."""
        for ym, mo in self.report["monthly"].items():
            for model, tokens in mo["tokens_by_model"].items():
                self.assertEqual({"in", "out", "cache_read", "cache_write"},
                                 set(tokens.keys()), f"{ym}/{model}")

    def test_emision_valida_schema(self):
        errors = _schema_errors(self, self.report)
        self.assertEqual([], errors,
                         "el reporte generado viola el schema")


def _session(project, first_full, last_full, first="2026-05-10"):
    """Sesión con el shape del extractor (extract_session_stats)."""
    return {"project": project, "first_ts": first,
            "first_ts_full": first_full, "last_ts_full": last_full,
            "duration_msgs": 10, "n_turns": 5, "n_tools": 2,
            "n_skills": 1, "n_errors": 0, "n_compactions": 0,
            "has_agent": False}


class TestConcurrencyReason(unittest.TestCase):
    """coffe-6i8: reason es null cuando el pico SÍ es computable."""

    def test_peak_computable_reason_null_valida(self):
        sessions = [_session("charly-coffee", "2026-05-10T14:00:00+00:00",
                             "2026-05-10T15:00:00+00:00"),
                    _session("charly-atril", "2026-05-10T14:30:00+00:00",
                             "2026-05-10T15:30:00+00:00")]
        report = ut.aggregate(rows(), sessions)
        ps = report["concurrency"]["peak_simultaneous_sessions"]
        self.assertEqual(2, ps["peak"])
        self.assertIsNone(ps["reason"])
        _validate(self, report)

    def test_peak_na_razon_no_vacia(self):
        """FPA-008: n/a siempre con razón — nunca vacío."""
        sessions = [_session("charly-coffee", None, None)]
        ps = ut.aggregate(rows(), sessions)["concurrency"]["peak_simultaneous_sessions"]
        self.assertIsNone(ps["peak"])
        self.assertTrue(ps["reason"])
        _validate(self, ut.aggregate(rows(), sessions))


class TestDatasetComiteado(unittest.TestCase):
    """El dataset regenerado post-F2: tokens_by_model presente y concurrencia."""

    @classmethod
    def setUpClass(cls):
        if not DATASET_PATH.exists():
            raise unittest.SkipTest("no hay dataset commiteado")
        cls.report = json.loads(DATASET_PATH.read_text())

    def test_dataset_valida_schema(self):
        self.assertFalse(_schema_errors(self, self.report),
                         "el dataset commiteado viola el schema")

    def test_tokens_by_model_presente_y_dos_niveles(self):
        meses = [mo for mo in self.report["monthly"].values()
                 if mo.get("tokens_by_model")]
        self.assertTrue(meses, "el dataset no tiene tokens_by_model (pre-F2)")
        for mo in meses:
            for model, tokens in mo["tokens_by_model"].items():
                self.assertEqual({"in", "out", "cache_read", "cache_write"},
                                 set(tokens.keys()), model)

    def test_peak_simultaneous_sessions_emitido(self):
        ps = (self.report.get("concurrency") or {}).get(
            "peak_simultaneous_sessions")
        self.assertIsNotNone(ps, "el tracker no emite concurrency")
        if ps["peak"] is None:
            self.assertTrue(ps["reason"])  # FPA-008
        else:
            self.assertIsNone(ps["reason"])

    def test_dataset_post_f2(self):
        """coffe-6i8: regeneración lleva el total de 139,735 a ~140,47x."""
        self.assertGreaterEqual(self.report["metadata"]["total_interactions"],
                                140_000)


if __name__ == "__main__":
    unittest.main()
