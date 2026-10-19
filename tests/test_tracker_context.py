#!/usr/bin/env python3
"""
test_tracker_context.py — reducer puro reduce_context() (ctx-tracker)

Cubre el alcance del epic add-context-dashboard (coffe-7gg, 1.1):
- acumulador de contexto con pico/final POR SEGMENTO de compactación
  (reset en cada entrada type=="compaction" — CORR-001: los archivos de
  Pi retienen los mensajes previos a la compactación, un pico continuo
  sobreestima el contexto real post-compactación)
- atribución de chars por fuente (asst_text, asst_thinking, asst_args,
  tool:<name>, user_text) que suma al transcript observado sin duplicar
  ni omitir roles
- big_results (> context_thresholds.big_result_chars de config)
- tolera assistant sin usage (turnos abortados → loss visible, no 0 fake)
- puro: no toca disco, líneas JSONL sintéticas ya parseadas

Sintéticos: helpers que construyen entries con el shape real de Pi v3
(verificado contra ~/.pi/agent/sessions: message.user/assistant/toolResult,
compaction con tokensBefore, model_change).
"""

import importlib.util
import json
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
    spec = importlib.util.spec_from_file_location("usage_tracker_context",
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


def e_assistant(text="", thinking="", tool_calls=None, usage=None,
                ts="2026-10-01T10:00:05.000Z", model="claude-sonnet-4-6",
                provider="anthropic"):
    content = []
    if thinking:
        content.append({"type": "thinking", "thinking": thinking})
    if text:
        content.append({"type": "text", "text": text})
    for tc in (tool_calls or []):
        content.append({"type": "toolCall", "id": tc.get("id", "c1"),
                        "name": tc["name"], "arguments": tc["arguments"]})
    msg = {"role": "assistant", "content": content, "model": model}
    if provider is not None:
        msg["provider"] = provider
    if usage is not None:
        msg["usage"] = usage
    return {"type": "message", "timestamp": ts, "message": msg}


def e_tool_result(name, text, ts="2026-10-01T10:00:10.000Z", is_error=False):
    return {"type": "message", "timestamp": ts,
            "message": {"role": "toolResult", "toolName": name,
                        "toolCallId": "c1", "isError": is_error,
                        "content": [{"type": "text", "text": text}]}}


def e_compaction(ts="2026-10-01T10:05:00.000Z"):
    return {"type": "compaction", "timestamp": ts,
            "tokensBefore": 100_000, "fromHook": False}


def usage(inp=1000, out=500, cr=0, cw=0, cost=None):
    u = {"input": inp, "output": out, "cacheRead": cr, "cacheWrite": cw,
         "totalTokens": inp + out + cr + cw}
    if cost is not None:
        u["cost"] = cost
    return u


def chars_of(s):
    return len(s)


# --- tests -------------------------------------------------------------------

class TestSesionNormal(unittest.TestCase):
    def test_turnos_y_pico_sesion_simple(self):
        """Sesión con 1 turno: pico == final == chars del turno."""
        entries = [
            e_user("hola" * 100),                       # 400 chars
            e_assistant(text="mundo" * 100,             # 500 chars text
                        thinking="pienso" * 50,         # 300 chars thinking
                        tool_calls=[{"name": "bash",
                                     "arguments": {"command": "ls" * 50}}],  # 100 chars
                        usage=usage()),
            e_tool_result("bash", "salida" * 50),       # 250 chars
        ]
        m = ut.reduce_context(entries)
        self.assertEqual(1, m["turns"])
        self.assertEqual(0, m["turns_no_usage"])
        self.assertEqual(0, m["compactions"])
        self.assertEqual(1, m["segment_count"])
        total = 400 + 500 + 300 + 115 + 300  # args json = 115, salida = 300
        self.assertEqual(total, m["peak_ctx_chars"])
        self.assertEqual(total, m["final_ctx_chars"])
        # estimado tokens = chars / chars_per_token (assumed, default 4)
        self.assertEqual(total // 4, m["peak_ctx_est"])

    def test_atribucion_por_fuente_suma_transcript(self):
        """La suma de fuentes == chars observados de user+assistant+toolResult,
        sin duplicar ni omitir roles (scenario del spec)."""
        text_u = "pregunta " * 40
        text_a = "respuesta " * 30
        think = "razonamiento " * 20
        args = json.dumps({"command": "echo hola"})
        tool_out = "resultado " * 25
        entries = [
            e_user(text_u),
            e_assistant(text=text_a, thinking=think,
                        tool_calls=[{"name": "bash",
                                     "arguments": json.loads(args)}],
                        usage=usage()),
            e_tool_result("bash", tool_out),
        ]
        m = ut.reduce_context(entries)
        f = m["chars_by_source"]
        self.assertEqual(chars_of(text_u), f["user_text"])
        self.assertEqual(chars_of(text_a), f["asst_text"])
        self.assertEqual(chars_of(think), f["asst_thinking"])
        self.assertEqual(chars_of(args), f["asst_args"])
        self.assertEqual(chars_of(tool_out), f["tool:bash"])
        self.assertEqual(
            sum(f.values()),
            chars_of(text_u) + chars_of(text_a) + chars_of(think)
            + chars_of(args) + chars_of(tool_out),
        )

    def test_tool_result_sin_toolname_va_a_unknown(self):
        m = ut.reduce_context([e_user("x"),
                               e_tool_result(None, "salida sin nombre")])
        self.assertEqual(17, m["chars_by_source"]["tool:unknown"])


class TestPicoPorSegmento(unittest.TestCase):
    def test_compaction_resetea_acumulador(self):
        """CORR-001: 200k chars, compaction, 50k más → peak 200k (no 250k
        continuo), final 50k, compactions 1, segment_count 2."""
        big = "x" * 200_000
        small = "y" * 25_000
        entries = [
            e_user(big),                      # 200k
            e_assistant(text="a", usage=usage()),
            e_compaction(),
            e_user(small),                    # 25k
            e_assistant(text="b", usage=usage()),
            e_tool_result("read", small),     # 25k
        ]
        m = ut.reduce_context(entries)
        self.assertEqual(1, m["compactions"])
        self.assertEqual(2, m["segment_count"])
        # peak incluye los chars del turno assistant del segmento (200k + 1)
        self.assertEqual(200_001, m["peak_ctx_chars"])
        self.assertEqual(50_001, m["final_ctx_chars"])  # 25k + 1 + 25k

    def test_compaction_de_segmento_mayor_gana_peak(self):
        """El pico es el mayor segmento, no el último: 50k → compaction →
        200k → peak 200k."""
        entries = [
            e_user("x" * 50_000),
            e_compaction(),
            e_user("y" * 100_000),
            e_assistant(text="z" * 100_000, usage=usage()),
        ]
        m = ut.reduce_context(entries)
        self.assertEqual(200_000, m["peak_ctx_chars"])
        self.assertEqual(200_000, m["final_ctx_chars"])
        self.assertEqual(1, m["compactions"])

    def test_multiples_compactions(self):
        entries = [e_compaction(), e_compaction(), e_compaction(),
                   e_user("s" * 1000)]
        m = ut.reduce_context(entries)
        self.assertEqual(3, m["compactions"])
        self.assertEqual(4, m["segment_count"])


class TestAssistantSinUsage(unittest.TestCase):
    def test_turno_abortado_cuenta_con_loss_visible(self):
        """Assistant sin usage (turno abortado): el turno cuenta y los chars
        se atribuyen igual; turns_no_usage lo hace visible (no 0 fake)."""
        entries = [
            e_user("p"),
            e_assistant(text="respuesta parcial", usage=None),  # abortado
            e_user("p2"),
            e_assistant(text="ok", usage=usage()),
        ]
        m = ut.reduce_context(entries)
        self.assertEqual(2, m["turns"])
        self.assertEqual(1, m["turns_no_usage"])
        # respuesta parcial (17, abortado) + ok (2) = asst_text total
        self.assertEqual(19, m["chars_by_source"]["asst_text"])


class TestStubs(unittest.TestCase):
    def test_stub_sin_turnos(self):
        """Sesión sin mensajes de assistant: turns==0 (el emisor la excluye
        del array y meta.stubs la cuenta — aquí el reducer solo reporta)."""
        m = ut.reduce_context([e_user("solo pregunta"), e_user("y otra")])
        self.assertEqual(0, m["turns"])
        self.assertEqual(19, m["chars_by_source"]["user_text"])  # 13 + 6


class TestBigResults(unittest.TestCase):
    def test_big_result_cuenta_por_tool(self):
        """toolResult > big_result_chars de config → cuenta en big_results
        por tool; otro resultado chico del mismo tool no suma."""
        entries = [
            e_user("q"),
            e_tool_result("read", "r" * 150_000),   # > default 100k
            e_tool_result("read", "r" * 100),
            e_tool_result("bash", "b" * 120_000),   # > umbral
        ]
        m = ut.reduce_context(entries)
        self.assertEqual({"read": 1, "bash": 1}, m["big_results_by_tool"])
        self.assertEqual(2, m["big_results"])

    def test_umbral_desde_config_no_hardcoded(self):
        """Umbrales desde config (CLAR-002): umbral 10 en config → un
        resultado de 11 chars ya es big_result."""
        cfg = dict(DEFAULT_THRESHOLDS, big_result_chars=10)
        entries = [e_user("q"), e_tool_result("bash", "x" * 11)]
        m = ut.reduce_context(entries, config=cfg)
        self.assertEqual({"bash": 1}, m["big_results_by_tool"])
        m2 = ut.reduce_context(entries)  # default 100k → no
        self.assertEqual(0, m2["big_results"])


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
            out.append(e_assistant(text="r", usage=usage(), model=modelo))
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
            entries.append(e_assistant(text="r", usage=usage(), model="m-x",
                                       provider=None))
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


class TestPureza(unittest.TestCase):
    def test_puro_no_escribe_ni_leo_disco(self):
        """El reducer no toca disco: dos pasadas sobre los mismos entries
        dan el mismo resultado (sin estado global observable)."""
        entries = [e_user("a"), e_assistant(text="b", usage=usage()),
                   e_tool_result("bash", "c")]
        m1 = ut.reduce_context(entries)
        m2 = ut.reduce_context(entries)
        self.assertEqual(m1, m2)

    def test_entries_vacios(self):
        m = ut.reduce_context([])
        self.assertEqual(0, m["turns"])
        self.assertEqual(0, m["peak_ctx_chars"])
        self.assertEqual(0, m["compactions"])


if __name__ == "__main__":
    unittest.main()
