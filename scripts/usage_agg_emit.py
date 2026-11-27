#!/usr/bin/env python3
"""Agregación: emisión mensual/hourly y concurrency (extraído de usage-tracker.py).

Parte de la descomposición de scripts/usage-tracker.py (coffe-kav.1):
cada módulo conserva el código original verbatim; los nombres compartidos
viajan por _link() (mismo patrón que viz_fpa_common) y el estado mutable
por usage_state.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import usage_state

def _agg_charges():
    # --- coffe-a31.2 (CRG-F1): ledger de cargos reales (FPA-013 reported) ---
    try:
        charges_by_month, charges_total, charges_p2p = cargar_charges()
        return charges_by_month, charges_total, charges_p2p, True, None
    except (OSError, ValueError, json.JSONDecodeError) as e:
        return ({}, 0.0, {}, False,
                f"unavailable: ledger ilegible ({e})")


def _agg_month_tools_emit(month_tools):
    # FPA-011: breakdown por tool con coste (interacciones + coste efectivo)
    return {t: {
        "interactions": st["interactions"],
        "cost_effective": round(st["cost_effective"], 8),
        "cost_real": round(st["cost_real"], 8),
        "models": {mm: {"interactions": ms["interactions"],
                        "cost_effective": round(ms["cost_effective"], 8)}
                   for mm, ms in sorted(st["models"].items())},
    } for t, st in sorted(month_tools.items())}


def _agg_month_dicts_emit(monthly_dicts, st, opts, charges):
    charges_ok, charges_p2p = charges
    interaction_kinds = opts.get("interaction_kinds")
    outcomes = opts.get("outcomes")
    # --- Emisiones F2 (coffe-lat.3) en monthly_dicts ---
    for m_key, mo in monthly_dicts.items():
        mo["tools"] = _agg_month_tools_emit(st.month_tools.get(m_key, {}))
        # FPA-011: breakdown por modelo con coste
        mo["models"] = {m: {
            "interactions": mm_st["interactions"],
            "cost_effective": round(mm_st["cost_effective"], 8),
        } for m, mm_st in sorted(st.month_models.get(m_key, {}).items())}
        # FPA-019: tokens por mes y modelo (in/out/cache_read/cache_write)
        mo["tokens_by_model"] = {m: {
            "in": mm_st["in"], "out": mm_st["out"],
            "cache_read": mm_st["cache_read"],
            "cache_write": mm_st["cache_write"],
        } for m, mm_st in sorted(st.month_models.get(m_key, {}).items())}
        _agg_month_p2p(mo, m_key, charges_ok, charges_p2p)
        # FPA-140: kinds del mes = filas + inyectados (user prompts)
        mo["interaction_kinds"] = dict(Counter(
            {**st.row_kinds.get(m_key, {}),
             **(interaction_kinds or {}).get(m_key, {})}))
        # FPA-014: outcomes donde haya GitHub token (vacío si no, con razón)
        mo["outcomes_by_project"] = (outcomes or {}).get(m_key, {})
    return monthly_dicts


def _agg_month_p2p(mo, m_key, charges_ok, charges_p2p):
    # FPA-013 (coffe-a31.2): cargas pay-per-token del mes. Con cargas reales
    # del ledger (créditos/reembolsos — FPA-013 deja de ser assumed) la cifra
    # es *reported*; si no, el estimado tracker (assumed). Los fees
    # implícitos del calendario NO se mezclan acá. p2p real del mes según el
    # ledger: por PRESENCIA de cargas, no por monto ≠ 0 (un crédito + un
    # refund que netean $0 siguen siendo facturas reales → reported)
    tiene_p2p = charges_ok and any(
        m_key in meses for meses in charges_p2p.values())
    if tiene_p2p:
        mo["pay_per_token_charges"] = round(sum(
            meses.get(m_key, 0.0) for meses in charges_p2p.values()), 8)
        mo["pay_per_token_provenance"] = "reported"
    else:
        mo["pay_per_token_charges"] = round(mo["cost_real_tracker"], 8)
    return mo


def _agg_concurrency(hour_projects, switches_by_day, sessions):
    # --- FPA-120: concurrencia etiquetada ---
    active_hours = len(hour_projects)
    total_switches = sum(switches_by_day.values())
    peak_projects = max((len(ps) for ps in hour_projects.values()), default=0)
    peak_sessions = _peak_simultaneous_sessions(sessions)
    return {
        "distinct_projects_per_hour": {
            "measure": "parallel-agent",  # proyectos/hora puede inflarse por agentes
            "peak": peak_projects,
            "avg": round(sum(len(ps) for ps in hour_projects.values()) / active_hours, 2)
            if active_hours else 0,
        },
        "peak_simultaneous_sessions": {
            "measure": "parallel-agent",
            "peak": peak_sessions,  # None → n/a en el dashboard (FPA-008)
            "reason": None if peak_sessions is not None
            else "sesiones sin timestamps completos (first_ts/last_ts)",
        },
        "project_switches_per_active_hour": {
            "measure": "human-context-switching",
            "value": round(total_switches / active_hours, 2) if active_hours else 0,
        },
    }


def _agg_excl_n(e):
    # fila completa (1 interacción) o resumen por proyecto (contador)
    return e["interactions"] if e.get("interactions") is not None else 1


def _agg_filtered_out(excluded, hourly):
    # --- FPA-141: excluidos por el filtro charly ---
    excluded = excluded or []
    filtered_interactions = sum(_agg_excl_n(e) for e in excluded)
    filtered_cost = sum(
        (e.get("cost_effective") or 0) * _agg_excl_n(e) for e in excluded
        if e.get("cost_effective") is not None)
    total_all = sum(b.interactions for b in hourly.values()) + filtered_interactions
    return {
        "interactions": filtered_interactions,
        "cost_effective": round(filtered_cost, 2) if filtered_cost else 0.0,
        "by_tool": dict(Counter({e.get("tool", "?"): _agg_excl_n(e)
                                 for e in excluded})),
        "share": round(filtered_interactions / total_all, 4) if total_all else 0.0,
    }

usage_state._link(globals())
