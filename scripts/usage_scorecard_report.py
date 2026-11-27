#!/usr/bin/env python3
"""Scorecard: ensamblado de entradas y veredicto (extraído de usage-tracker.py).

Parte de la descomposición de scripts/usage-tracker.py (coffe-kav.1):
cada módulo conserva el código original verbatim; los nombres compartidos
viajan por _link() (mismo patrón que viz_fpa_common) y el estado mutable
por usage_state.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import usage_state

def _scorecard_data(rows, metrics, nulls):
    """Bloque data del entry (sin el cruce de tareas/sesiones, que se
    inserta después). metrics = (cost, total_tok, cost_weighted,
    cost_naive, cache_factor, cf_prov)."""
    cost, total_tok, cost_weighted, cost_naive, cache_factor, cf_prov = metrics
    tokens, stop_reasons, aborts, turns, t_errs = _scorecard_data_core(rows)
    latency = _scorecard_latency(rows, nulls)
    return {
        "interactions": turns,
        "cost_effective": round(cost, 8),
        "cost_weighted_per_1m": cost_weighted,
        "cost_per_1m_total_tokens": cost_naive,
        "cache_factor": cache_factor,
        "cache_share": (round(tokens["cache_read"] / total_tok, 4)
                        if total_tok else None),
        "tokens": tokens,
        "latency": latency,
        "stop_reasons": stop_reasons,
        "aborts_per_100_turns": round(aborts / turns * 100, 4) if turns else None,
        "tool_errors_per_100_turns": (round(t_errs / turns * 100, 4)
                                      if turns else None),
        "provenance": _scorecard_prov(cf_prov, latency),
        "nulls": nulls,
    }


def _scorecard_unresolved_entry(name, fam, ver, win):
    return {"model": name, "family": fam, "version": ver,
            "window": win, "data": None,
            "reason": ("modelo de la cohorte sin match en "
                       "model_mapping (coffe-dnx.1)"), "nulls": []}


def _scorecard_window_entry(ctx, name, wkey):
    """Entrada de scorecard para 1 modelo × ventana. wkey = (win, since,
    until_prefix)."""
    win, since, until_prefix = wkey
    entry = {"model": name, "family": None, "version": None,
             "window": win, "data": None, "reason": None, "nulls": []}
    fam, ver = model_details(name)
    entry["family"] = fam
    entry["version"] = ver
    if name in ctx.unresolved:
        return _scorecard_unresolved_entry(name, fam, ver, win)
    rows = [r for r in ctx.interactions
            if (r.get("model_family"), r.get("model_version")) == (fam, ver)
            and _in_scorecard_window(r["timestamp"], since, until_prefix)]
    if not rows:
        entry["reason"] = (
            "sin interacciones de este modelo en la ventana "
            f"(mapeo {fam}/{ver}) — patrón FPA-008: null con razón, "
            "nunca 0")
        return entry
    nulls = []
    tokens, cost, total_tok, when = _scorecard_tokens(rows)
    cost_weighted, cost_naive, cache_factor, cf_prov = _scorecard_cost_weighted(
        (fam, ver, when), tokens, cost, nulls)
    cost_per_task, cost_per_acc = _scorecard_task_metrics(
        ctx, (fam, ver), (since, until_prefix), nulls)
    data = _scorecard_data(rows, (cost, total_tok, cost_weighted, cost_naive,
                                  cache_factor, cf_prov), nulls)
    switches, api_cost, sub_cost = _scorecard_spend_stats(
        ctx, (fam, ver), (since, until_prefix))
    data["cost_per_task"] = cost_per_task
    data["cost_per_accepted_task"] = cost_per_acc
    data["model_switches"] = switches
    data["subscription_vs_api"] = {
        "subscription": round(sub_cost, 8),
        "api": round(api_cost, 8),
        "provenance": "estimated",
    }
    data["provenance"]["cost_per_task"] = "estimated"
    data["provenance"]["cost_per_accepted_task"] = "estimated"
    data["provenance"]["model_switches"] = "estimated"
    data["provenance"]["subscription_vs_api"] = "estimated"
    entry["data"] = data
    return entry


def _scorecard_meta_base(cohort, rule):
    return {
        "cohort_source": CONFIG_SOURCE or "builtin-fallback",
        "cohort_name": cohort.get("name"),
        "models": list(cohort.get("models") or []),
        "reference": cohort.get("reference"),
        "cache_factor_note": (
            "cost_weighted_per_1m = cost / (input_fresh + output + "
            "cache_read × cache_factor) × 1e6; cache_factor = "
            "cache_read_rate / input_rate del pricing vigente en config — "
            "nunca un 1.0 silencioso"),
        "reason": None,
        "rule": rule,
        "rule_verdict": None,
        "rule_verdict_reason": None,
        "rule_verdict_per_model": None,
    }


def _scorecard_entries(ctx, cohort, windows):
    entries = []
    todo_entries = []
    for name in cohort["models"]:
        for win, (since, until_prefix) in (windows.items() if windows
                                           else (("todo", (None, None)),)):
            entry = _scorecard_window_entry(ctx, name, (win, since, until_prefix))
            entries.append(entry)
            if win == "todo":
                todo_entries.append(entry)
    return entries, todo_entries


def _model_scorecard(interactions, spend_rows):
    """coffe-dnx.8: bloque model_scorecard[] del reporte.

    Para cada modelo de benchmark_cohort (config/fpa.json) × ventana
    (todo/ytd/mes): cost_weighted_per_1m = cost / (input_fresh + output +
    cache_read × cache_factor) — normalizado por COMPOSICIÓN de tokens, no
    sobre tokens totales (CORR-003: con 90% de cache_read a ~0.1× del
    input, $/Mtok-naive invierte el ranking). cache_factor es explícito del
    pricing config. Cruce con coffe-dnx.3 (cost_per_task /
    cost_per_accepted_task) y coffe-dnx.2 (latencia, stop_reasons, abortos,
    errores de tool, switches, cache_share). Toda cifra lleva provenance
    (reported|estimated|assumed); lo ausente → null + razón en nulls[]
    (FPA-008, nunca 0 fake). Devuelve (entries, metadata_scorecard)."""
    cfg = _FPA_CONFIG or {}
    cohort = cfg.get("benchmark_cohort") or _FALLBACK_BENCHMARK_COHORT
    rule = cfg.get("model_decision_rule") or _FALLBACK_DECISION_RULE
    meta = _scorecard_meta_base(cohort, rule)
    if not cohort.get("models"):
        meta["reason"] = "benchmark_cohort sin modelos en config"
        return [], meta

    # Resolución cohorte → (family, version) vía el MISMO mapeo config-driven
    # que el resto del reporte (coffe-dnx.1).
    unresolved = [name for name in cohort["models"]
                  if model_details(name) == ("other", name[:20])]
    task_costs, task_reason = _scorecard_task_costs()
    windows = _scorecard_window_filters(interactions)
    ctx = _ScorecardCtx((interactions, spend_rows, task_costs, task_reason,
                         set(unresolved)))
    entries, todo_entries = _scorecard_entries(ctx, cohort, windows)

    verdict, v_reason, per_model = _scorecard_rule_verdict(
        todo_entries, cohort, rule)
    meta["rule_verdict"] = verdict
    meta["rule_verdict_reason"] = v_reason
    meta["rule_verdict_per_model"] = per_model
    meta["task_costs_reason"] = task_reason
    return entries, meta

usage_state._link(globals())
