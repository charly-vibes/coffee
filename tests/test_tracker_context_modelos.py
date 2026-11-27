#!/usr/bin/env python3
"""
test_tracker_modelos.py — modelos por turno y coste por sesión (coffe-sht, coffe-5uu)

Hermanado con test_tracker_context.py (mismos helpers, archivo
autocontenido: sin conftest).
"""

import importlib.util
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
    spec = importlib.util.spec_from_file_location("usage_tracker_modelos",
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


class TestModelosTurnos(unittest.TestCase):
    """coffe-sht (1.2): atribución por modelo — CORR-002 del review R5:
    66 model_change en 40 archivos muestreados; un solo modelo por fila
    no alcanza. models_turns es fuente de verdad, model es el dominante."""

    def _turnos(self, modelo, n, model_change=True):
        out = []
        if model_change:
            out.append({"type": "model_change", "provider": "anthropic",
                        "modelId": modelo})
        for i in range(n):
            out.append(e_assistant('r', usage(), overrides=dict(model=modelo)))
        return out

    def test_model_change_mid_sesion(self):
        """20 turnos A + 10 turnos B → models_turns {A:20,B:10}, model A."""
        entries = (self._turnos("modelo-a", 20)
                   + self._turnos("modelo-b", 10))
        m = ut.reduce_context(entries)
        self.assertEqual({"modelo-a": 20, "modelo-b": 10}, m["models_turns"])
        self.assertEqual("modelo-a", m["model"])
        self.assertEqual(30, m["turns"])

    def test_modelo_desde_mensaje_sin_model_change(self):
        """Sin entradas model_change: el modelo viene del campo message.model
        de cada assistant (presente en los archivos reales verificados)."""
        entries = self._turnos("solo-b", 3, model_change=False)
        m = ut.reduce_context(entries)
        self.assertEqual({"solo-b": 3}, m["models_turns"])
        self.assertEqual("solo-b", m["model"])

    def test_provider_ultima_model_change(self):
        """provider se lleva en paralelo al modelo (context_source en 5uu):
        mensajes sin provider propio caen al último model_change."""
        entries = [{"type": "model_change", "provider": "openrouter",
                    "modelId": "m-x"}]
        for i in range(2):
            entries.append(e_assistant('r', usage(), overrides=dict(model='m-x', provider=None)))
        m = ut.reduce_context(entries)
        self.assertEqual("openrouter", m["provider"])
        self.assertEqual({"m-x": 2}, m["models_turns"])

    def test_sin_turnos_model_none(self):
        m = ut.reduce_context([e_user("p")])
        self.assertIsNone(m["model"])
        self.assertEqual({}, m["models_turns"])

    def test_month_desde_filename(self):
        """month viene del filename del archivo de sesión (documentado):
        el reducer lo recibe, no lo deriva."""
        m = ut.reduce_context([e_user("p")], month="2026-10")
        self.assertEqual("2026-10", m["month"])
        m2 = ut.reduce_context([e_user("p")])
        self.assertNotIn("month", m2)


class TestCostePorSesion(unittest.TestCase):
    """coffe-5uu (1.3): cost.in/out/cacheRead/cacheWrite por sesión;
    coste ausente → null con razón (patrón 'sin telemetría', no 0 fake);
    context_source: pi vs claude-cli (usage inválido, coste de suscripción)."""

    def test_coste_completo_soma_componentes(self):
        entries = [
            e_user("q"),
            e_assistant(text="a", usage=usage(overrides=dict(cost={'input': 0.01, 'output': 0.02, 'cacheRead': 0.001, 'cacheWrite': 0.002}))),
            e_assistant(text="b", usage=usage(overrides=dict(cost={'input': 0.03, 'output': 0.04, 'cacheRead': 0.003, 'cacheWrite': 0.004}))),
        ]
        m = ut.reduce_context(entries)
        self.assertAlmostEqual(0.04, m["cost_in"])
        self.assertAlmostEqual(0.06, m["cost_out"])
        self.assertAlmostEqual(0.004, m["cost_cr"])
        self.assertAlmostEqual(0.006, m["cost_cw"])
        self.assertNotIn("cost_null_reason", m)

    def test_coste_ausente_null_con_razon(self):
        """Ningún turno trae usage.cost → los 4 campos null + razón."""
        entries = [e_user("q"), e_assistant(text="a", usage=usage()),
                   e_assistant(text="b")]  # abortado: ni usage
        m = ut.reduce_context(entries)
        self.assertIsNone(m["cost_in"])
        self.assertIsNone(m["cost_out"])
        self.assertIsNone(m["cost_cr"])
        self.assertIsNone(m["cost_cw"])
        self.assertIn("cost_null_reason", m)
        self.assertTrue(m["cost_null_reason"])

    def test_coste_parcial_solo_turnos_con_coste(self):
        """Turno abortado (sin coste) + turno con coste → suma del disponible;
        el resto visible vía turns_no_usage (loss visible, no 0 fake)."""
        entries = [
            e_user("q"),
            e_assistant(text="abortado"),
            e_assistant(text="ok", usage=usage(overrides=dict(cost={'input': 0.05, 'output': 0.01, 'cacheRead': 0.0, 'cacheWrite': 0.0}))),
        ]
        m = ut.reduce_context(entries)
        self.assertAlmostEqual(0.05, m["cost_in"])
        self.assertAlmostEqual(0.01, m["cost_out"])
        self.assertEqual(1, m["turns_no_usage"])

    def test_context_source_claude_cli(self):
        entries = [{"type": "model_change", "provider": "claude-cli",
                    "modelId": "claude-sonnet-4-6"}]
        entries += [e_user("q"),
                    e_assistant('a', usage(), overrides=dict(provider='claude-cli'))]
        m = ut.reduce_context(entries)
        self.assertEqual("claude-cli", m["context_source"])

    def test_context_source_pi_default(self):
        m = ut.reduce_context([e_user("q"),
                               e_assistant('a', usage(), overrides=dict(provider='anthropic'))])
        self.assertEqual("pi", m["context_source"])



if __name__ == "__main__":
    unittest.main()
