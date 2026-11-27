#!/usr/bin/env python3
"""Scorecard: contexto, tokens y métricas por ventana (extraído de usage-tracker.py).

Parte de la descomposición de scripts/usage-tracker.py (coffe-kav.1):
cada módulo conserva el código original verbatim; los nombres compartidos
viajan por _link() (mismo patrón que viz_fpa_common) y el estado mutable
por usage_state.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import usage_state

class _ScorecardCtx:
    """Contexto compartido por las entradas del scorecard.
    parts = (interactions, spend_rows, task_costs, task_reason, unresolved)."""

    def __init__(self, parts):
        (self.interactions, self.spend_rows, self.task_costs,
         self.task_reason, self.unresolved) = parts


def _scorecard_tokens_sums(rows):
    return {
        "in": sum(r.get("input_tokens", 0) or 0 for r in rows),
        "out": sum(r.get("output_tokens", 0) or 0 for r in rows),
        "cache_read": sum(r.get("cache_read_tokens", 0) or 0 for r in rows),
        "cache_write": sum(r.get("cache_write_tokens", 0) or 0 for r in rows),
    }


def _scorecard_when(rows):
    try:
        return date.fromisoformat(max(r["timestamp"][:10] for r in rows))
    except ValueError:
        return None


def _scorecard_tokens(rows):
    tokens = _scorecard_tokens_sums(rows)
    cost = sum(r.get("cost_effective", 0) or 0 for r in rows)
    when = _scorecard_when(rows)
    return tokens, cost, sum(tokens.values()), when


def _scorecard_cost_weighted(pricing, tokens, cost, nulls):
    """cost_weighted_per_1m + (cache_factor, provenance); nulls anotados.
    pricing = (fam, ver, when)."""
    fam, ver, when = pricing
    cache_factor, cf_reason, cf_prov = _scorecard_cache_factor(fam, ver, when)
    if cf_reason:
        nulls.append({"field": "cache_factor", "reason": cf_reason})
    denom = (tokens["in"] + tokens["out"]
             + tokens["cache_read"] * (cache_factor or 0.0))
    if cost > 0 and denom > 0 and cache_factor is not None:
        cost_weighted = round(cost / denom * 1e6, 4)
    else:
        cost_weighted = None
        nulls.append({
            "field": "cost_weighted_per_1m",
            "reason": ("sin coste en la ventana (turnos abortados o "
                       "proveedor sin billing por token)"
                       if cost <= 0 else
                       f"denominador 0 sin cache_factor usable — {cf_reason}"),
        })
    total_tok = sum(tokens.values())
    cost_naive = (round(cost / total_tok * 1e6, 4)
                  if cost > 0 and total_tok else None)
    return cost_weighted, cost_naive, cache_factor, cf_prov


def _task_window_list(ctx, fam_ver, window):
    since, until_prefix = window
    return [t for t in (ctx.task_costs.get(fam_ver) or [])
            if _in_scorecard_window(t[0] or "", since, until_prefix)]


def _task_metrics_empty(ctx, nulls):
    nulls.append({
        "field": "cost_per_task",
        "reason": ctx.task_reason or
                  "sin tareas de este modelo en agent_metrics.json "
                  "para la ventana (cruce task×modelo vacío)",
    })
    if not ctx.task_reason:
        nulls.append({
            "field": "cost_per_accepted_task",
            "reason": "sin tareas de este modelo en la ventana",
        })
    return None, None


def _scorecard_task_metrics(ctx, fam_ver, window, nulls):
    # --- cruce coffe-dnx.3: coste por tarea / tarea aceptada --------------
    tlist = _task_window_list(ctx, fam_ver, window)
    if not tlist:
        return _task_metrics_empty(ctx, nulls)
    n_tasks = len(tlist)
    acc = [t for t in tlist if t[2]]
    cost_per_task = round(sum(t[1] for t in tlist) / n_tasks, 8) if n_tasks else None
    cost_per_acc = (round(sum(t[1] for t in acc) / len(acc), 8)
                    if acc else None)
    if cost_per_acc is None:
        nulls.append({
            "field": "cost_per_accepted_task",
            "reason": "sin tareas aceptadas en la ventana "
                      "(proxy: f sin bits aborted|error)",
        })
    return cost_per_task, cost_per_acc


def _scorecard_latency(rows, nulls):
    # --- coffe-dnx.2: latencia (medida in-corpus sobre las filas) ---------
    lat_rows = sorted(r["duration_ms"] for r in rows
                      if r.get("duration_ms") is not None)
    if lat_rows:
        return {
            "p50_ms": _pctl(lat_rows, 0.5),
            "p90_ms": _pctl(lat_rows, 0.9),
            "coverage_pct": round(len(lat_rows) / len(rows) * 100, 2),
        }
    nulls.append({
        "field": "latency",
        "reason": "sin turnos con durationMs en la ventana "
                  "(cobertura por fuente en "
                  "metadata.latency_coverage)",
    })
    return None


def _scorecard_session_mine(s, wkey):
    """True si la sesión resuelve al modelo y cae en la ventana.
    wkey = (fam, ver, since, until_prefix)."""
    fam, ver, since, until_prefix = wkey
    chain = any(model_details(mid) == (fam, ver)
                for mid in (s.get("model_chain") or []))
    return chain and _in_scorecard_window(s.get("day") or "", since,
                                          until_prefix)


def _scorecard_spend_stats(ctx, fam_ver, window):
    """(switches, api_cost, sub_cost) de las sesiones que resuelven al
    modelo y caen en la ventana. window = (since, until_prefix)."""
    mine = [s for s in ctx.spend_rows
            if _scorecard_session_mine(
                s, (*fam_ver, *window))]
    switches = sum(s["model_switches"] for s in mine)
    api_cost = sum(s.get("cost_effective") or 0 for s in mine)
    sub_cost = sum(
        max(0.0, (s.get("cost_real") or 0) - (s.get("cost_effective") or 0))
        for s in mine)
    return switches, api_cost, sub_cost


def _scorecard_tokens_only(rows):
    return {
        "in": sum(r.get("input_tokens", 0) or 0 for r in rows),
        "out": sum(r.get("output_tokens", 0) or 0 for r in rows),
        "cache_read": sum(r.get("cache_read_tokens", 0) or 0 for r in rows),
        "cache_write": sum(r.get("cache_write_tokens", 0) or 0 for r in rows),
    }


def _scorecard_data_core(rows):
    stop_reasons = dict(Counter(
        r["stop_reason"] for r in rows if r.get("stop_reason")))
    errs = sum(r.get("tool_errors") or 0 for r in rows)
    return (_scorecard_tokens_only(rows), stop_reasons,
            stop_reasons.get("aborted", 0), len(rows), errs)


def _scorecard_prov(cf_prov, latency):
    return {
        "interactions": "reported",
        "cost_effective": "estimated",
        "cost_weighted_per_1m": "estimated",
        "cost_per_1m_total_tokens": "estimated",
        "cache_factor": cf_prov,
        "cache_share": "reported",
        "tokens": "reported",
        "latency": "reported" if latency is not None else None,
        "stop_reasons": "reported",
        "aborts_per_100_turns": "reported",
        "tool_errors_per_100_turns": "reported",
    }

usage_state._link(globals())
