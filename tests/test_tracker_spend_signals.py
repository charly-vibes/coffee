#!/usr/bin/env python3
"""
test_tracker_spend_signals.py — señales de calidad/enrutamiento en
session_spend (coffe-dnx.2): duration_ms/stop_reasons/tool_errors/
model_chain/cache_share/cost_provenance, más aceptación con fixtures
reales de Pi y claude-cli.

Run: python3 tests/test_tracker_spend_signals.py
"""

import importlib.util
import json
import shutil
import tempfile
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


# --- coffe-dnx.2: señales de calidad/enrutamiento en session_spend -----------

import shutil
import tempfile

FIXTURES = REPO / "tests" / "fixtures"
FIX_PI = FIXTURES / "pi_latency_session.jsonl"
FIX_CLAUDE = FIXTURES / "claude-cli_no_duration_session.jsonl"


def row(ts="2026-05-10T14:23:00+00:00", session_file=None, overrides=None):
    """Row sintética con el shape exacto que producen los extractores."""
    base = {
        "source": "test", "tool": "claude-cli",
        "model_raw": "claude-sonnet-4-6",
        "model_family": "claude", "model_version": "sonnet-4.6",
        "project": "charly-coffee", "timestamp": ts,
        "hour": ut.hour_key(ut.parse_ts(ts)),
        "input_tokens": 1000, "output_tokens": 500,
        "cache_read_tokens": 100, "cache_write_tokens": 20,
        "cost_effective": 0.01,
    }
    if session_file:
        base["session_file"] = session_file
    if overrides:
        base.update(overrides)
    return base


def spend_rows():
    """2 proyectos × 2 días × 2 meses; claude-cli en suscripción y codex p2p."""
    return [
        row('2026-05-10T14:23:00+00:00', '019eaaaa-1111-2222-3333-444444444444.jsonl',
            overrides=dict(project='charly-coffee')),
        row('2026-05-10T15:40:00+00:00', '019eaaaa-1111-2222-3333-444444444444.jsonl',
            overrides=dict(project='charly-coffee')),
        row('2026-05-10T16:10:00+00:00', 'rollout-2026-05-10-019ebbbb.jsonl',
            overrides=dict(project='charly-atril', tool='codex',
                           model_raw='gpt-5.4', model_family='codex',
                           model_version='gpt-5.4')),
        row('2026-06-01T09:00:00+00:00', '019ecccc.jsonl',
            overrides=dict(project='charly-coffee')),
    ]


def spend_row(ts="2026-05-10T14:23:00+00:00", session_file="s1.jsonl", overrides=None):
    ov = dict(session_file=session_file)
    if overrides:
        ov.update(overrides)
    return row(ts, overrides=ov)


class TestSessionSpendSignals(unittest.TestCase):
    """coffe-dnx.2: duration_ms/stop_reasons/tool_errors/model_chain/
    cache_share/cost_provenance en la fila de session_spend."""

    def _aggregate(self, rows):
        return ut.aggregate(rows, [])["session_spend"]

    def test_duraciones_conocidas_p50_p90(self):
        s = self._aggregate([
            spend_row('2026-05-10T14:23:00+00:00', overrides=dict(duration_ms=1000)),
            spend_row('2026-05-10T14:24:00+00:00', overrides=dict(duration_ms=2000)),
            spend_row('2026-05-10T14:25:00+00:00', overrides=dict(duration_ms=4000)),
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
            spend_row(overrides=dict(stop_reason='toolUse')),
            spend_row('2026-05-10T14:24:00+00:00', overrides=dict(stop_reason='aborted')),
            spend_row('2026-05-10T14:25:00+00:00', overrides=dict(stop_reason='stop')),
        ])[0]
        self.assertEqual(s["stop_reasons"],
                         {"toolUse": 1, "aborted": 1, "stop": 1})

    def test_tool_errors_calls_errors_p50(self):
        s = self._aggregate([
            spend_row(overrides=dict(tool_calls=2, tool_errors=1, tool_durations_ms=[100, 300])),
            spend_row('2026-05-10T14:24:00+00:00', overrides=dict(tool_calls=1, tool_errors=0, tool_durations_ms=[50])),
        ])[0]
        self.assertEqual(s["tool_errors"]["calls"], 3)
        self.assertEqual(s["tool_errors"]["errors"], 1)
        self.assertEqual(s["tool_errors"]["p50_ms"], 100)

    def test_model_switches_dos_model_change(self):
        # 2 model_change (el 1ro fija el modelo inicial) → 1 switch real
        s = self._aggregate([
            spend_row(overrides=dict(model_change_idx=1, model_chain=['m-a'])),
            spend_row('2026-05-10T14:24:00+00:00', overrides=dict(model_change_idx=2, model_chain=['m-a', 'm-b'])),
        ])[0]
        self.assertEqual(s["model_switches"], 1)
        self.assertEqual(s["model_chain"], ["m-a", "m-b"])

    def test_model_switches_un_model_change_es_cero(self):
        # guarda del 100% falso: el model_change INICIAL no cuenta
        s = self._aggregate([spend_row(overrides=dict(model_change_idx=1, model_chain=['m-a']))])[0]
        self.assertEqual(s["model_switches"], 0)
        self.assertEqual(s["model_chain"], ["m-a"])

    def test_cache_share(self):
        s = self._aggregate([spend_row(overrides=dict(input_tokens=1000, output_tokens=500, cache_read_tokens=300, cache_write_tokens=200))])[0]
        self.assertEqual(s["cache_share"], round(300 / 2000, 4))

    def test_cache_share_null_sin_tokens(self):
        s = self._aggregate([spend_row(overrides=dict(input_tokens=0, output_tokens=0, cache_read_tokens=0, cache_write_tokens=0, cost_effective=None))])[0]
        self.assertIsNone(s["cache_share"])

    def test_cost_provenance_reported_solo_si_todos(self):
        s = self._aggregate([
            spend_row(overrides=dict(cost_reported=True)),
            spend_row('2026-05-10T14:24:00+00:00', overrides=dict(cost_reported=True)),
        ])[0]
        self.assertEqual(s["cost_provenance"], "reported")

    def test_cost_provenance_mixed(self):
        s = self._aggregate([
            spend_row(overrides=dict(cost_reported=True)),
            spend_row('2026-05-10T14:24:00+00:00', overrides=dict(cost_reported=False)),
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
        srows = ut.aggregate(rows, [])["session_spend"]
        # el fixture real produce exactamente 1 sesión y valida contra el schema
        self.assertEqual(1, len(srows))
        jsonschema.validate(srows[0], schema["properties"]["session_spend"]["items"])


