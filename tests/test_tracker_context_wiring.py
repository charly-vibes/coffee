#!/usr/bin/env python3
"""
test_tracker_wiring.py — wiring extract_context_metrics/aggregate (coffe-kup)

Hermanado con test_tracker_context.py (mismos helpers, archivo
autocontenido: sin conftest).
"""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TRACKER_PATH = REPO / "scripts" / "usage-tracker.py"

DEFAULT_THRESHOLDS = {
    "big_result_chars": 100_000,
    "big_session_tokens": 100_000,
    "pathological_chars": 500_000,
    "chars_per_token": 4,
}


def load_tracker():
    spec = importlib.util.spec_from_file_location("usage_tracker_wiring",
                                                  TRACKER_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ut = load_tracker()


# --- helpers: shape real de Pi v3 -------------------------------------------

def e_user(text, ts="2026-10-01T10:00:00.000Z"):
    return {"type": "message", "timestamp": ts,
            "message": {"role": "user",
                        "content": [{"type": "text", "text": text}]}}


def e_assistant(text="", thinking="", usage=None, overrides=None):
    """Entry assistant; overrides ajusta tool_calls/model/provider opcionales."""
    extra = overrides or {}
    content = []
    if thinking:
        content.append({"type": "thinking", "thinking": thinking})
    if text:
        content.append({"type": "text", "text": text})
    for tc in (extra.get("tool_calls") or []):
        content.append({"type": "toolCall", "id": tc.get("id", "c1"),
                        "name": tc["name"], "arguments": tc["arguments"]})
    msg = {"role": "assistant", "content": content,
           "model": extra.get("model", "claude-sonnet-4-6")}
    provider = extra.get("provider", "anthropic")
    if provider is not None:
        msg["provider"] = provider
    if usage is not None:
        msg["usage"] = usage
    return {"type": "message", "timestamp": "2026-10-01T10:00:05.000Z",
            "message": msg}


def usage(overrides=None):
    """usage de Pi v3 con defaults sintéticos; overrides ajusta campos."""
    u = {"input": 1000, "output": 500, "cacheRead": 0, "cacheWrite": 0}
    if overrides:
        u.update(overrides)
    u["totalTokens"] = u["input"] + u["output"] + u["cacheRead"] + u["cacheWrite"]
    return u


class TestWiringContext(unittest.TestCase):
    """coffe-kup (1.8+1.9): wiring — extract_context_metrics camina los JSONL
    de Pi (1 archivo = 1 sesión, month desde el filename) y aggregate()
    emite context_metrics en el reporte. Schema lo requiere."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write_session(self, proj, fname, entries):
        d = self.tmp / proj
        d.mkdir(parents=True, exist_ok=True)
        (d / fname).write_text(
            "\n".join(json.dumps(e) for e in entries) + "\n")

    def test_extract_context_metrics_desde_tmpdir(self):
        self._write_session("charly-coffee",
                            "2026-05-10T14-23-00-000Z_a.jsonl", [
                                e_user("hola" * 1000),
                                e_assistant(text="mundo" * 1000,
                                            usage=usage()),
                            ])
        cm = ut.extract_context_metrics(sessions_dir=self.tmp)
        self.assertTrue(cm["available"])
        self.assertEqual(1, len(cm["sessions"]))
        row = cm["sessions"][0]
        self.assertEqual("2026-05", row["month"])   # filename, no contenido
        self.assertEqual("charly-coffee", row["project"])
        self.assertEqual(1, row["turns"])
        self.assertEqual(1, len(cm["monthly_model"]))
        self.assertIn("chars_per_token", cm["meta"])

    def test_stubs_fuera_del_array_contados(self):
        self._write_session("charly-coffee",
                            "2026-05-10T14-23-00-000Z_a.jsonl",
                            [e_user("solo pregunta")])
        cm = ut.extract_context_metrics(sessions_dir=self.tmp)
        self.assertEqual(0, len(cm["sessions"]))
        self.assertEqual(1, cm["meta"]["stubs"])

    def test_fuera_de_scope_excluida(self):
        # proyectos no-charly quedan fuera (in_scope), como extract_pi_sessions
        self._write_session("otro-proyecto",
                            "2026-05-10T14-23-00-000Z_a.jsonl", [
                                e_user("hola"),
                                e_assistant(text="x", usage=usage()),
                            ])
        cm = ut.extract_context_metrics(sessions_dir=self.tmp)
        self.assertEqual(0, len(cm["sessions"]))

    def test_dir_ausente_unavailable_con_razon(self):
        cm = ut.extract_context_metrics(sessions_dir=self.tmp / "nope")
        self.assertFalse(cm["available"])
        self.assertTrue(cm["reason"])

    def test_ventana_since_until_aplicada(self):
        """coffe-snj: la ventana --since/--until filtra los archivos por la
        fecha del filename (solape), como el resto del reporte."""
        self._write_session("charly-coffee",
                            "2026-03-10T14-23-00-000Z_a.jsonl", [
                                e_user("hola"),
                                e_assistant(text="x", usage=usage()),
                            ])
        cm = ut.extract_context_metrics(sessions_dir=self.tmp)
        self.assertEqual(1, len(cm["sessions"]))  # sin ventana → entra
        ut.SINCE = ut.parse_ts("2026-05-01T00:00:00+00:00").date()
        try:
            cm2 = ut.extract_context_metrics(sessions_dir=self.tmp)
            self.assertEqual(0, len(cm2["sessions"]))
        finally:
            ut.SINCE = None

    def test_aggregate_emite_context_metrics(self):
        cm = {"available": True, "reason": None, "sessions": [],
              "monthly_model": [], "meta": {"chars_per_token": 4}}
        report = ut.aggregate([], [], context_metrics=cm)
        self.assertEqual(cm, report["context_metrics"])

    def test_aggregate_sin_context_emite_unavailable(self):
        report = ut.aggregate([], [])
        self.assertFalse(report["context_metrics"]["available"])
        self.assertTrue(report["context_metrics"]["reason"])



if __name__ == "__main__":
    unittest.main()
