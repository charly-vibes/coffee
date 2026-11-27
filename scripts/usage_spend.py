#!/usr/bin/env python3
"""session_spend + cobertura de model_mapping (extraído de usage-tracker.py).

Parte de la descomposición de scripts/usage-tracker.py (coffe-kav.1):
cada módulo conserva el código original verbatim; los nombres compartidos
viajan por _link() (mismo patrón que viz_fpa_common) y el estado mutable
por usage_state.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import usage_state
from usage_state import _REPO_ROOT  # noqa: F401  (constante de módulo, antes de _link)


def _spend_groups(interactions):
    groups = defaultdict(list)
    for r in interactions:
        groups[(r["tool"], clean_proj_name(r.get("project", "unknown")),
                r.get("session_file"))].append(r)
    return groups


def _spend_identity(tool, proj, sfile, rows):
    """(session_id, fallback): el archivo ES la sesión; sin session_file
    (claude_cache_only) → id de reserva (hash)."""
    import hashlib
    if sfile:
        return Path(sfile).stem, False
    seed = f"{tool}|{proj}|{rows[0]['timestamp']}"
    return ("cache-only-"
            + hashlib.sha1(seed.encode()).hexdigest()[:12], True)


def _spend_tokens(rows):
    return {
        "in": sum(r.get("input_tokens", 0) or 0 for r in rows),
        "out": sum(r.get("output_tokens", 0) or 0 for r in rows),
        "cache_read": sum(r.get("cache_read_tokens", 0) or 0 for r in rows),
        "cache_write": sum(r.get("cache_write_tokens", 0) or 0 for r in rows),
    }


def _spend_costs(rows):
    """(cost_effective, cost_real, cost_reason)."""
    costs = [r.get("cost_effective", 0) or 0 for r in rows]
    if all(c == 0 for c in costs):
        return None, None, ("sin usage.cost en ningún turno (turnos abortados "
                            "o proveedor sin billing por token)")
    cost_eff = round(sum(costs), 8)
    cost_real = round(sum(
        get_sub_cost(r["tool"], r.get("timestamp", ""),
                     r.get("cost_effective", 0) or 0)[0]
        for r in rows), 8)
    return cost_eff, cost_real, None


def _spend_provenance(rows):
    # reported SOLO si todos los turnos traen cost.totalFromProvider;
    # mixed si conviven con estimaciones (corpus real: ~1,6% reported).
    reported_flags = [r.get("cost_reported") for r in rows]
    if all(v is True for v in reported_flags):
        return "reported"
    if any(v is True for v in reported_flags):
        return "mixed"
    return "estimated"


def _spend_duration(rows):
    lat_rows = [r for r in rows if r.get("duration_ms") is not None]
    if lat_rows:
        durs = sorted(r["duration_ms"] for r in lat_rows)
        return {"n": len(durs), "p50": _pctl(durs, 0.5),
                "p90": _pctl(durs, 0.9)}, None
    if any("duration_ms" in r for r in rows):
        reason = ("proveedor reporta durationMs pero ningún turno lo trajo "
                  "(turnos abortados o cobertura parcial de latencia)")
    else:
        reason = ("fuente sin telemetría de latencia (durationMs solo existe "
                  "en logs de Pi)")
    return None, reason


def _spend_tool_errors(rows):
    t_calls = sum(r.get("tool_calls") or 0 for r in rows)
    t_errs = sum(r.get("tool_errors") or 0 for r in rows)
    tool_durs = sorted(d for r in rows
                       for d in (r.get("tool_durations_ms") or []))
    return {"calls": t_calls, "errors": t_errs,
            "p50_ms": _pctl(tool_durs, 0.5) if tool_durs else None}


def _spend_quality(rows):
    """coffe-dnx.2: señales de calidad (Grupo A) — latencia, stop_reasons
    y errores de tool. Ausente → null + razón, NUNCA 0 fake."""
    duration_ms, duration_reason = _spend_duration(rows)
    stop_reasons = dict(Counter(
        r["stop_reason"] for r in rows if r.get("stop_reason")))
    return {"duration_ms": duration_ms, "duration_reason": duration_reason,
            "stop_reasons": stop_reasons,
            "tool_errors": _spend_tool_errors(rows)}


def _spend_routing(rows):
    """coffe-dnx.2: enrutamiento (Grupo B) — (model_chain, switches).

    model_change_idx cuenta TODOS los model_change (el 1ro fija el
    modelo inicial y NO es un cambio): switches = idx - 1. Contador
    ingenuo reportaba 100% de sesiones con cambio (100% tienen el
    model_change inicial); sesión de un solo model_change → 0.
    """
    chains = [r["model_chain"] for r in rows if r.get("model_chain")]
    chain = list(chains[-1]) if chains else list(
        dict.fromkeys(r["model_raw"] for r in rows))
    idxs = [r["model_change_idx"] for r in rows
            if r.get("model_change_idx") is not None]
    return chain, (max(0, max(idxs) - 1) if idxs else 0)


def _spend_row(key, rows):
    tool, proj, sfile = key
    rows.sort(key=lambda r: r["timestamp"])
    session_id, fallback = _spend_identity(tool, proj, sfile, rows)
    tokens_sum = _spend_tokens(rows)
    total_tok = sum(tokens_sum.values())
    row = {
        "session_id": session_id,
        "session_id_fallback": fallback,
        "project": proj,
        "day": rows[0]["hour"][:10],  # día local (misma TZ que los buckets)
        "start": rows[0]["timestamp"],
        "end": max(r["timestamp"] for r in rows),
        "crosses_midnight": len({r["hour"][:10] for r in rows}) > 1,
        "source": tool,
        "model": ({r["model_raw"] for r in rows}.pop()
                  if len({r["model_raw"] for r in rows}) == 1 else "mixed"),
        "n_interactions": len(rows),
        "tokens": tokens_sum,
        "cache_share": (round(tokens_sum["cache_read"] / total_tok, 4)
                        if total_tok else None),
    }
    cost_eff, cost_real, cost_reason = _spend_costs(rows)
    row.update({"cost_effective": cost_eff, "cost_real": cost_real,
                "cost_reason": cost_reason,
                "cost_provenance": _spend_provenance(rows)})
    row.update(_spend_quality(rows))
    chain, model_switches = _spend_routing(rows)
    row.update({"model_chain": chain, "model_switches": model_switches})
    return row


def _session_spend(interactions):
    """add-session-spend-report: filas por sesión con coste y tokens.

    Agrupa rows por (tool, project, session_file) — el archivo ES la sesión
    en pi/claude/codex (stem = uuid nativo) y el hilo en amp. Rows sin
    session_file (claude_cache_only) → session_id de reserva (hash),
    session_id_fallback: true. Sesión atribuida al día local de su primera
    interacción (los buckets diarios reparten por interacción — asimetría
    documentada en README). Coste ausente (todos los turnos a 0.0 sin
    tokens) → null con cost_reason, mismo patrón que context_metrics.
    """
    out = [_spend_row(key, rows) for key, rows in _spend_groups(interactions).items()]
    out.sort(key=lambda r: (r["day"], r["project"], r["session_id"]))
    return out


# coffe-dnx.2 (CORR-005): cobertura de latencia por fuente — MEDIDA sobre
# muestra (~400 archivos/provider, nov 2026), nunca extrapolada ni
# maquillada. Los sources sin durationMs (codex/claude-cli/gemini) reportan
# 0: session_spend.duration_ms queda null con duration_reason, no 0 fake.
LATENCY_COVERAGE_MEASURED = {
    "zai": 100.0, "minimax": 100.0, "qwen-token-plan": 100.0, "kimi": 100.0,
    "openrouter": 7.5,
    "openai-codex": 0.0, "claude-cli": 0.0, "google-gemini-cli": 0.0,
}


def _coverage_unpriced(r, fam, ver, priced_cache):
    """True si la versión no tiene tarifa en la fecha de la fila
    (determinista por (fam, ver, día), con cache por corrida)."""
    day = (r.get("timestamp") or "")[:10]
    key = (fam, ver, day)
    if key not in priced_cache:
        try:
            when = date.fromisoformat(day) if len(day) == 10 else None
        except ValueError:
            when = None
        priced_cache[key] = ver not in _pricing_for(when).get(fam, {})
    return priced_cache[key]


def _coverage_row(r, by_family, state):
    fam = r.get("model_family") or "other"
    ver = r.get("model_version") or "unknown"
    toks = ((r.get("input_tokens") or 0) + (r.get("output_tokens") or 0)
            + (r.get("cache_read_tokens") or 0) + (r.get("cache_write_tokens") or 0))
    by_family[fam]["tokens"] += toks
    by_family[fam]["rows"] += 1
    state["tokens_total"] += toks
    if fam == "other":
        state["tokens_other"] += toks
    # familia "other" = fallback del mapeo (sin match), no una versión
    # sin tarifa — se cuenta en tokens_other, no aquí.
    if fam != "other" and _coverage_unpriced(r, fam, ver, state["priced_cache"]):
        state["fallback_rows"] += 1
    return state


def _model_mapping_coverage(interactions):
    """coffe-dnx.1: cobertura del mapeo modelo → familia en esta corrida.

    Por familia: tokens (in+out+cache_read+cache_write, misma contabilidad
    de metadata.token_accounting) y filas. tokens_other_pct = % de tokens
    clasificados ("other", ...) — el KPI del ticket: antes del fix era
    ~56,8% (z-ai/glm-5.3-flash caía al fallback). default_rates_fallbacks
    se cuenta POR FILA (versión sin tarifa en model_pricing en la fecha de
    la fila): determinista para tests/golden, independiente del orden en
    que estimate_cost() fue llamada durante la extracción. Las filas
    ("other", ...) — fallback del mapeo, sin match — no cuentan como
    default_rates_fallbacks: no son una versión sin tarifa sino un modelo
    sin mapear, visible y contado en tokens_other_pct. Las llamadas a
    estimate_cost() que usaron default_rates quedan aparte en el contador
    default_rates_fallbacks() (y en el warning stderr).
    """
    by_family = defaultdict(lambda: {"tokens": 0, "rows": 0})
    state = {"tokens_total": 0, "tokens_other": 0, "fallback_rows": 0,
             "priced_cache": {}}
    for r in interactions:
        _coverage_row(r, by_family, state)
    tokens_total = state["tokens_total"]
    tokens_other = state["tokens_other"]
    return {
        "by_family": {fam: dict(st) for fam, st in sorted(by_family.items())},
        "tokens_total": tokens_total,
        "tokens_other": tokens_other,
        "tokens_other_pct": round(tokens_other / tokens_total * 100, 2)
        if tokens_total else 0.0,
        "default_rates_fallbacks": state["fallback_rows"],
        "mapping_source": CONFIG_SOURCE or "builtin-fallback",
    }


# ----------------------------------------------------------------------
# coffe-dnx.8: scorecard de cohorte (benchmark_cohort en config/fpa.json)
# ----------------------------------------------------------------------

# Umbrales de reserva (solo si config/fpa.json no está). EXCL-003:
# provisionales; los de config mandan y el reporte declara provisional.
_FALLBACK_BENCHMARK_COHORT = {
    "name": "cohorte-flash-2026",
    "models": ["glm-5.3-flash", "deepseek-v4-flash", "qwen3.8-flash",
               "qwen3.8-max", "claude-opus-4.5"],
    "reference": "claude-opus-4.5",
}
_FALLBACK_DECISION_RULE = {
    "provisional": True,
    "metric": "cost_per_accepted_task",
    "thresholds": {"cambiar_max_ratio_vs_reference": 0.8,
                   "mantener_max_ratio_vs_reference": 1.0},
}
SCORECARD_AGENT_METRICS_PATH = _REPO_ROOT / "data" / "agent_metrics.json"
# task_flags() de scripts/agent-metrics-tracker.py: bit 1 = aborted, bit 2 =
# error. Tarea "aceptada" = ni abortada ni con error (proxy de resultado —
# provenance estimated; la señal positiva de aceptación no existe todavía).

usage_state._link(globals())
