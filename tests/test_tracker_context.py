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


class TestCostePorSesion(unittest.TestCase):
    """coffe-5uu (1.3): cost.in/out/cacheRead/cacheWrite por sesión;
    coste ausente → null con razón (patrón 'sin telemetría', no 0 fake);
    context_source: pi vs claude-cli (usage inválido, coste de suscripción)."""

    def test_coste_completo_soma_componentes(self):
        entries = [
            e_user("q"),
            e_assistant(text="a", usage=usage(cost={"input": 0.01,
                                                    "output": 0.02,
                                                    "cacheRead": 0.001,
                                                    "cacheWrite": 0.002})),
            e_assistant(text="b", usage=usage(cost={"input": 0.03,
                                                    "output": 0.04,
                                                    "cacheRead": 0.003,
                                                    "cacheWrite": 0.004})),
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
            e_assistant(text="ok", usage=usage(cost={"input": 0.05,
                                                     "output": 0.01,
                                                     "cacheRead": 0.0,
                                                     "cacheWrite": 0.0})),
        ]
        m = ut.reduce_context(entries)
        self.assertAlmostEqual(0.05, m["cost_in"])
        self.assertAlmostEqual(0.01, m["cost_out"])
        self.assertEqual(1, m["turns_no_usage"])

    def test_context_source_claude_cli(self):
        entries = [{"type": "model_change", "provider": "claude-cli",
                    "modelId": "claude-sonnet-4-6"}]
        entries += [e_user("q"),
                    e_assistant(text="a", provider="claude-cli",
                                usage=usage())]
        m = ut.reduce_context(entries)
        self.assertEqual("claude-cli", m["context_source"])

    def test_context_source_pi_default(self):
        m = ut.reduce_context([e_user("q"),
                               e_assistant(text="a", provider="anthropic",
                                           usage=usage())])
        self.assertEqual("pi", m["context_source"])


class TestPatologias(unittest.TestCase):
    """coffe-gp8 (1.4): sesión con bloque assistant >
    context_thresholds.pathological_chars (config, default 500k) → flag
    pathological en la fila; el emisor la excluye de percentiles y la
    cuenta en meta.excluded_pathological. Umbrales desde config (CLAR-002)."""

    def test_sesion_glitch_flag(self):
        """Bloque assistant de 16M chars > 500k default → pathological True
        con el tamaño del bloque."""
        entries = [e_user("q"),
                   e_assistant(text="g" * 16_000_000, usage=usage())]
        m = ut.reduce_context(entries)
        self.assertTrue(m["pathological"])
        self.assertEqual(16_000_000, m["max_assistant_block_chars"])

    def test_sesion_normal_bajo_umbral(self):
        entries = [e_user("q"), e_assistant(text="ok " * 1000, usage=usage()),
                   e_tool_result("bash", "r" * 200_000)]  # toolResult no cuenta
        m = ut.reduce_context(entries)
        self.assertFalse(m["pathological"])

    def test_umbral_desde_config(self):
        """Umbral 10 chars de config → bloque de 11 chars ya es patológico."""
        cfg = dict(DEFAULT_THRESHOLDS, pathological_chars=10)
        m = ut.reduce_context([e_user("q"), e_assistant(text="abcdefghijk")],
                              config=cfg)
        self.assertTrue(m["pathological"])

    def test_toolresult_grande_no_es_patologico(self):
        """Solo bloques ASSISTANT disparan la patología (un toolResult grande
        es big_result, no glitch)."""
        entries = [e_user("q"), e_tool_result("read", "r" * 600_000)]
        m = ut.reduce_context(entries)
        self.assertFalse(m["pathological"])
        self.assertEqual(1, m["big_results"])


class TestTokensReported(unittest.TestCase):
    """coffe-2xj (1.7): uso real reportado por sesión para el ratio de
    validación del estimador chars/4 (solo sesiones pi, usage confiable)."""

    def test_tokens_reported_soma_usage(self):
        entries = [e_user("q"),
                   e_assistant(text="a", usage=usage(inp=1000, out=500,
                                                     cr=100, cw=50)),
                   e_assistant(text="b", usage=usage(inp=200, out=100))]
        m = ut.reduce_context(entries)
        self.assertEqual(1000 + 500 + 100 + 50 + 200 + 100,
                         m["tokens_reported"])

    def test_tokens_reported_sin_usage(self):
        m = ut.reduce_context([e_user("q"), e_assistant(text="a")])
        self.assertEqual(0, m["tokens_reported"])


def _row(turns_peak_chars, model="m-a", month="2026-10", turns=10,
         pathological=False, context_source="pi", tokens_reported=None,
         final_turn_tokens=None, costs=None, chars_by_source=None,
         project="charly-coffee"):
    """Fila de sesión sintética con el shape que emite reduce_context
    (más month y tokens_reported)."""
    r = {
        "turns": turns,
        "turns_no_usage": 0,
        "chars_by_source": chars_by_source or {"asst_text": turns_peak_chars},
        "big_results": 0,
        "big_results_by_tool": {},
        "compactions": 0,
        "segment_count": 1,
        "peak_ctx_chars": turns_peak_chars * 4,
        "final_ctx_chars": turns_peak_chars * 4,
        "peak_ctx_est": turns_peak_chars,
        "final_ctx_est": turns_peak_chars,
        "max_assistant_block_chars": 1000,
        "chars_per_token": 4,
        "model": model,
        "models_turns": {model: turns},
        "provider": "anthropic",
        "cost_in": None, "cost_out": None, "cost_cr": None, "cost_cw": None,
        "context_source": context_source,
        "pathological": pathological,
    }
    if tokens_reported is not None:
        r["tokens_reported"] = tokens_reported
    if final_turn_tokens is not None:
        r["final_turn_tokens"] = final_turn_tokens
    if costs:
        r.update({"cost_in": costs[0], "cost_out": costs[1],
                  "cost_cr": costs[2], "cost_cw": costs[3]})
    r["month"] = month
    r["project"] = project
    return r


class TestMonthlyModel(unittest.TestCase):
    """coffe-2xj (1.6): agregados monthly_model[] por mes × modelo dominante,
    sobre el set limpio (turns>0, sin patológicas) + meta completo (1.7)."""

    def test_dos_modelos_un_mes_dos_filas(self):
        rows = [_row(50_000, model="m-a"), _row(60_000, model="m-a"),
                _row(80_000, model="m-b")]
        agg = ut.aggregate_context(rows)
        filas = [f for f in agg["monthly_model"] if f["month"] == "2026-10"]
        self.assertEqual(2, len(filas))
        modelos = {f["model"] for f in filas}
        self.assertEqual({"m-a", "m-b"}, modelos)

    def test_p50_p90_del_pico(self):
        picos = [10_000, 20_000, 30_000, 40_000, 50_000]
        rows = [_row(p, model="m-a") for p in picos]
        agg = ut.aggregate_context(rows)
        f = agg["monthly_model"][0]
        self.assertAlmostEqual(30_000, f["peak_p50"])
        self.assertAlmostEqual(46_000, f["peak_p90"])  # interpolación lineal

    def test_pct_big_session(self):
        """% de sesiones > context_thresholds.big_session_tokens (config)."""
        rows = [_row(50_000), _row(150_000), _row(200_000)]  # 2/3 > 100k
        agg = ut.aggregate_context(rows)
        f = agg["monthly_model"][0]
        self.assertAlmostEqual(66.67, f["pct_big_session"], places=1)

    def test_tok_por_turno_por_fuente(self):
        """tok/turno por fuente: est tokens de la fuente / turns del grupo."""
        rows = [
            _row(0, turns=2,
                 chars_by_source={"asst_text": 8_000, "asst_thinking": 4_000,
                                  "tool:bash": 8_000}),
            _row(0, turns=2,
                 chars_by_source={"asst_text": 16_000,
                                  "asst_thinking": 4_000}),
        ]
        agg = ut.aggregate_context(rows)
        f = agg["monthly_model"][0]
        # est tokens por fuente / total turns del grupo (4 turnos):
        # asst_text (8000+16000)/4 = 6000 tok → 1500/turno
        self.assertAlmostEqual(1_500, f["tokens_per_turn_by_source"]["asst_text"])
        self.assertAlmostEqual(500, f["tokens_per_turn_by_source"]["asst_thinking"])
        self.assertAlmostEqual(500, f["tokens_per_turn_by_source"]["tool:bash"])

    def test_spend_por_componente(self):
        rows = [_row(10_000, costs=(1.0, 2.0, 0.1, 0.2)),
                _row(20_000, costs=(3.0, 4.0, 0.3, 0.4)),
                _row(30_000)]  # coste null: no suma, no ensucia
        agg = ut.aggregate_context(rows)
        f = agg["monthly_model"][0]
        self.assertAlmostEqual(4.0, f["spend_in"])
        self.assertAlmostEqual(6.0, f["spend_out"])
        self.assertAlmostEqual(0.4, f["spend_cr"])
        self.assertAlmostEqual(0.6, f["spend_cw"])

    def test_set_limpio_excluye_stubs_y_patologicas(self):
        rows = [_row(50_000), _row(0, turns=0),  # stub
                _row(16_000_000, pathological=True)]
        agg = ut.aggregate_context(rows)
        self.assertEqual(1, agg["meta"]["stubs"])
        self.assertEqual(1, agg["meta"]["excluded_pathological"])
        self.assertEqual(1, len(agg["monthly_model"]))
        self.assertEqual(50_000, agg["monthly_model"][0]["peak_p50"])

    def test_mes_vacio_sin_fila(self):
        rows = [_row(50_000, month="2026-10")]
        agg = ut.aggregate_context(rows)
        self.assertFalse(any(f["month"] == "2026-09"
                             for f in agg["monthly_model"]))

    def test_mes_parcial_marcado(self):
        """El mes máximo del dataset es parcial (patrón fpa-dashboard)."""
        rows = [_row(50_000, month="2026-09"),
                _row(60_000, month="2026-10")]
        agg = ut.aggregate_context(rows)
        por_mes = {f["month"]: f["partial"] for f in agg["monthly_model"]}
        self.assertFalse(por_mes["2026-09"])
        self.assertTrue(por_mes["2026-10"])


class TestMetaContexto(unittest.TestCase):
    def test_meta_chars_per_token_y_umbrales(self):
        agg = ut.aggregate_context([_row(50_000)])
        self.assertEqual(4, agg["meta"]["chars_per_token"])
        self.assertEqual("assumed", agg["meta"]["chars_per_token_tag"])
        self.assertIn("subestima", agg["meta"]["chars_per_token_bias"])
        self.assertEqual(100_000, agg["meta"]["thresholds"]["big_session_tokens"])

    def test_validation_ratio_median_solo_pi_con_usage(self):
        """ratio = tokens del ÚLTIMO turno con usage / final_ctx_est (el
        último turno ≈ contexto final real, no la suma acumulada de la
        sesión); mediana sobre sesiones pi con usage — claude-cli (usage
        inválido) fuera, sin usage fuera."""
        rows = [
            _row(1_000, tokens_reported=3_000, final_turn_tokens=3_000),
            _row(1_000, tokens_reported=5_000, final_turn_tokens=5_000),
            _row(1_000, tokens_reported=4_000, final_turn_tokens=4_000),  # mediana
            _row(1_000, context_source="claude-cli",
                 tokens_reported=99_000, final_turn_tokens=99_000),  # excluida
            _row(1_000),                           # sin usage → excluida
        ]
        agg = ut.aggregate_context(rows)
        self.assertAlmostEqual(4.0, agg["meta"]["validation_ratio_median"])

    def test_validation_ratio_sin_datos_null(self):
        agg = ut.aggregate_context([_row(1_000)])
        self.assertIsNone(agg["meta"]["validation_ratio_median"])

    def test_meta_umbrales_desde_config(self):
        cfg = dict(DEFAULT_THRESHOLDS, big_session_tokens=250_000)
        agg = ut.aggregate_context([_row(50_000)], config=cfg)
        self.assertEqual(250_000, agg["meta"]["thresholds"]["big_session_tokens"])


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
