#!/usr/bin/env python3
"""Cuotas de suscripción por mes (extraído de usage-tracker.py).

Parte de la descomposición de scripts/usage-tracker.py (coffe-kav.1):
cada módulo conserva el código original verbatim; los nombres compartidos
viajan por _link() (mismo patrón que viz_fpa_common) y el estado mutable
por usage_state.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import usage_state

def get_sub_cost(tool, ts_str, eff_cost):
    """
    tool, timestamp_str, effective_cost -> (real_cost, effective_cost, sub_label)
    For subscription periods: real_cost = 0 (covered by subscription).
    For pay-per-token: real_cost = effective_cost.
    """
    if tool not in SUBSCRIPTIONS:
        return eff_cost, eff_cost, "pay-per-token"
    date = (ts_str or "")[:10]
    for period in SUBSCRIPTIONS.get(tool, []):
        if period["start"] <= date and (period["end"] is None or date < period["end"]):
            if period["monthly_fee"] > 0:
                return 0.0, eff_cost, period["label"]
            else:
                return eff_cost, eff_cost, period["label"]
    return eff_cost, eff_cost, "unknown"


def _period_fee_in_month(period, m_key):
    """Fee del período activo en el mes m_key (overlap por YYYY-MM), 0 si
    el período es gratis o no solapa."""
    if period["monthly_fee"] <= 0:
        return 0.0
    p_start = period["start"]
    p_end = period["end"] or "9999-12"
    # Does this month overlap with the subscription period?
    if m_key >= p_start[:7] and m_key < p_end[:7]:
        return period["monthly_fee"]
    return 0.0


def _month_sub_fee(tools, m_key):
    # Find which months had which subscriptions active
    total = 0.0
    for tool, periods in SUBSCRIPTIONS.items():
        if tool not in tools:
            continue
        for period in periods:
            total += _period_fee_in_month(period, m_key)
    return total


def calc_subscription_fees(monthly_data):
    """
    Calculate monthly subscription fees for each month.
    Returns dict: {month_key: total_sub_fee}
    """
    sub_fees = {}
    for m_key, m_data in monthly_data.items():
        total = _month_sub_fee(m_data.get("tools", {}), m_key)
        if total > 0:
            sub_fees[m_key] = total
    return sub_fees


# --- ctx-tracker: reducer puro de contexto (epic add-context-dashboard) ------
# Bloque aislado (co-modificación coordinada de los tickets ctx-tracker):
# reduce_context(entries) -> metrics, mismo estilo que aggregate(): no toca
# disco, testeado con líneas JSONL sintéticas (tests/test_tracker_context.py).

DEFAULT_CONTEXT_THRESHOLDS = {
    "big_result_chars": 100_000,      # toolResult que cuenta como big_result
    "big_session_tokens": 100_000,    # sesión grande para agregados monthly_model
    "pathological_chars": 500_000,    # bloque assistant glitch → excluida
    "chars_per_token": 4,             # factor assumed (subestima: español/code ~3)
}

usage_state._link(globals())
