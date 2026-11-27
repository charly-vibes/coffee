#!/usr/bin/env python3
"""
test_tracker_monthly.py — agregados monthly_model y meta (coffe-2xj)

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
    spec = importlib.util.spec_from_file_location("usage_tracker_monthly",
                                                  TRACKER_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ut = load_tracker()


# --- helpers: shape real de Pi v3 -------------------------------------------

def _row(turns_peak_chars, overrides=None):
    """Fila de sesión sintética con el shape que emite reduce_context
    (más month y tokens_reported); overrides ajusta campos puntuales."""
    ov = dict(overrides or {})
    costs = ov.pop("costs", None)
    tr = ov.pop("tokens_reported", None)
    ftt = ov.pop("final_turn_tokens", None)
    model = ov.pop("model", "m-a")
    turns = ov.pop("turns", 10)
    r = {
        "turns": turns,
        "turns_no_usage": 0,
        "chars_by_source": {"asst_text": turns_peak_chars},
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
        "context_source": "pi",
        "pathological": False,
        "month": "2026-10",
        "project": "charly-coffee",
    }
    r.update(ov)
    if costs:
        r.update(cost_in=costs[0], cost_out=costs[1],
                 cost_cr=costs[2], cost_cw=costs[3])
    if tr is not None:
        r["tokens_reported"] = tr
    if ftt is not None:
        r["final_turn_tokens"] = ftt
    return r


class TestMonthlyModel(unittest.TestCase):
    """coffe-2xj (1.6): agregados monthly_model[] por mes × modelo dominante,
    sobre el set limpio (turns>0, sin patológicas) + meta completo (1.7)."""

    def test_dos_modelos_un_mes_dos_filas(self):
        rows = [_row(50000, overrides=dict(model='m-a')), _row(60000, overrides=dict(model='m-a')),
                _row(80000, overrides=dict(model='m-b'))]
        agg = ut.aggregate_context(rows)
        filas = [f for f in agg["monthly_model"] if f["month"] == "2026-10"]
        self.assertEqual(2, len(filas))
        modelos = {f["model"] for f in filas}
        self.assertEqual({"m-a", "m-b"}, modelos)

    def test_p50_p90_del_pico(self):
        picos = [10_000, 20_000, 30_000, 40_000, 50_000]
        rows = [_row(p, overrides=dict(model='m-a')) for p in picos]
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
            _row(0, overrides=dict(turns=2, chars_by_source={'asst_text': 8000, 'asst_thinking': 4000, 'tool:bash': 8000})),
            _row(0, overrides=dict(turns=2, chars_by_source={'asst_text': 16000, 'asst_thinking': 4000})),
        ]
        agg = ut.aggregate_context(rows)
        f = agg["monthly_model"][0]
        # est tokens por fuente / total turns del grupo (4 turnos):
        # asst_text (8000+16000)/4 = 6000 tok → 1500/turno
        self.assertAlmostEqual(1_500, f["tokens_per_turn_by_source"]["asst_text"])
        self.assertAlmostEqual(500, f["tokens_per_turn_by_source"]["asst_thinking"])
        self.assertAlmostEqual(500, f["tokens_per_turn_by_source"]["tool:bash"])

    def test_spend_por_componente(self):
        rows = [_row(10000, overrides=dict(costs=(1.0, 2.0, 0.1, 0.2))),
                _row(20000, overrides=dict(costs=(3.0, 4.0, 0.3, 0.4))),
                _row(30_000)]  # coste null: no suma, no ensucia
        agg = ut.aggregate_context(rows)
        f = agg["monthly_model"][0]
        self.assertAlmostEqual(4.0, f["spend_in"])
        self.assertAlmostEqual(6.0, f["spend_out"])
        self.assertAlmostEqual(0.4, f["spend_cr"])
        self.assertAlmostEqual(0.6, f["spend_cw"])

    def test_set_limpio_excluye_stubs_y_patologicas(self):
        rows = [_row(50_000), _row(0, overrides=dict(turns=0)),  # stub
                _row(16000000, overrides=dict(pathological=True))]
        agg = ut.aggregate_context(rows)
        self.assertEqual(1, agg["meta"]["stubs"])
        self.assertEqual(1, agg["meta"]["excluded_pathological"])
        self.assertEqual(1, len(agg["monthly_model"]))
        self.assertEqual(50_000, agg["monthly_model"][0]["peak_p50"])

    def test_mes_vacio_sin_fila(self):
        rows = [_row(50000, overrides=dict(month='2026-10'))]
        agg = ut.aggregate_context(rows)
        self.assertFalse(any(f["month"] == "2026-09"
                             for f in agg["monthly_model"]))

    def test_mes_parcial_marcado(self):
        """El mes máximo del dataset es parcial (patrón fpa-dashboard)."""
        rows = [_row(50000, overrides=dict(month='2026-09')),
                _row(60000, overrides=dict(month='2026-10'))]
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
            _row(1000, overrides=dict(tokens_reported=3000, final_turn_tokens=3000)),
            _row(1000, overrides=dict(tokens_reported=5000, final_turn_tokens=5000)),
            _row(1000, overrides=dict(tokens_reported=4000, final_turn_tokens=4000)),  # mediana
            _row(1000, overrides=dict(context_source='claude-cli', tokens_reported=99000, final_turn_tokens=99000)),  # excluida
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



if __name__ == "__main__":
    unittest.main()
