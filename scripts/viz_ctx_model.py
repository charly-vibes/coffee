#!/usr/bin/env python3
"""
viz_ctx_model.py — pre-cálculo del modelo de viz-ctx.py (epic
add-context-dashboard, ctx-viz)

Purpose: build_model(report) y sus vistas intermedias — distribución
(histograma del pico sobre el set limpio + banda de percentiles),
composición por fuente, ley de crecimiento (OLS), coste (split
in/out/cache, subscription aparte) y tiempo (mes × modelo). Con la
sección ausente o unavailable devuelve available=False con la razón
(missing honesto); pure, no toca disco ni imprime.

Rationale: split de viz-ctx.py (coffe-v5f) — módulo aparte para que el
archivo principal quede bajo el límite de líneas.
"""

from viz_ctx_stats import N_BINS, PCT_KEYS, _fmt_human, _month_days
from viz_ctx_stats import _month_elapsed, _ols, _pctl


def _unavailable_model(cm):
    """Modelo unavailable con la razón del reporte (missing honesto)."""
    return {"available": False, "reason": cm.get("reason")
            or "unavailable: context_metrics ausente del reporte",
            "scope_note": "sesiones de Pi únicamente"}


def _clean_sessions(sessions):
    """Set limpio: sesiones con turnos y sin patológicas."""
    return [s for s in sessions
            if s.get("turns", 0) > 0 and not s.get("pathological")]


def build_model(report):
    """Pre-cálculo de la vista Distribución (+ tags assumed) desde el
    reporte. Pure: no toca disco ni imprime (missing honesto arriba)."""
    cm = report.get("context_metrics") or {}
    if not cm.get("available"):
        return _unavailable_model(cm)
    sessions = cm.get("sessions") or []
    meta = cm.get("meta") or {}
    clean = _clean_sessions(sessions)
    end = (report.get("metadata") or {}).get("date_range", {}).get("end")
    return {
        "available": True,
        "reason": None,
        "scope_note": "sesiones de Pi únicamente",
        "dist": _build_dist(clean, meta, sessions),
        "meta": _build_meta(meta),
        "comp": _build_comp(clean, meta.get("thresholds", {})),
        "growth": _build_growth(clean),
        "cost": _build_cost(sessions),
        "pathological_list": _pathological_rows(sessions, meta),
        "time": _build_time(cm.get("monthly_model") or [], end),
        "monthly_model": cm.get("monthly_model") or [],
    }


def _build_dist(clean, meta, sessions):
    """Histograma + banda de percentiles + conteos de excluidas/stubs."""
    peaks = sorted(s["peak_ctx_est"] for s in clean)
    return {
        "histogram": _histogram(peaks),
        "band": {k: _pctl(peaks, p) for k, p in
                 zip(PCT_KEYS, (0.5, 0.75, 0.9, 0.95, 0.99))},
        "n_clean": len(clean),
        "excluded_pathological": sum(1 for s in sessions
                                     if s.get("pathological")),
        "stubs": meta.get("stubs",
                          sum(1 for s in sessions
                              if s.get("turns", 0) == 0)),
        "thresholds": meta.get("thresholds", {}),
        "validation_ratio_median": meta.get("validation_ratio_median"),
    }


def _histogram(peaks):
    """Histograma de N_BINS bins sobre los picos."""
    if not peaks:
        return []
    vmax = peaks[-1]
    step = max(vmax / N_BINS, 1)
    edges = [i * step for i in range(N_BINS + 1)]
    counts = [0] * N_BINS
    for p in peaks:
        counts[min(int(p / step), N_BINS - 1)] += 1
    return [{"from": edges[i], "to": edges[i + 1], "count": counts[i]}
            for i in range(N_BINS)]


def _build_meta(meta):
    """Tag assumed del factor chars/chars_per_token, bias y ratio."""
    return {
        "chars_per_token": meta.get("chars_per_token"),
        "chars_per_token_tag": meta.get("chars_per_token_tag"),
        "chars_per_token_bias": meta.get("chars_per_token_bias"),
        "validation_ratio_median": meta.get("validation_ratio_median"),
    }


def _pathological_rows(sessions, meta):
    """Filas de la vista Patologías (FPA-008): outlier con umbral y razón."""
    th = (meta.get("thresholds") or {}).get("pathological_chars")
    return [{
        "project": s.get("project"),
        "month": s.get("month"),
        "model": s.get("model"),
        "max_assistant_block_chars": s.get("max_assistant_block_chars"),
        "threshold": th,
        "reason": _path_reason(s.get("max_assistant_block_chars") or 0, th),
    } for s in sessions if s.get("pathological")]


def _path_reason(n, th):
    """Razón de exclusión patológica con tamaños y umbral visibles."""
    return (f"bloque assistant de {_fmt_human(n)} "
            f"> umbral patológico de {_fmt_human(th or 0)} "
            "(config) — excluida de percentiles")


def _build_growth(clean):
    """Vista Ley de crecimiento (coffe-r3c 2.6): dispersivo pico vs turnos
    por modelo con pendiente OLS pre-calculada en Python (hallazgo del
    epic: ~850 tok/turno constante entre eras). Sin varianza en turnos →
    pendiente null con razón."""
    by_model = []
    for m in sorted({r.get("model") for r in clean}, key=str):
        rows = [r for r in clean if r.get("model") == m]
        xs = [r["turns"] for r in rows]
        ys = [r["peak_ctx_est"] for r in rows]
        b, a = _ols(xs, ys)
        by_model.append({
            "model": m,
            "n": len(rows),
            "slope": b,
            "intercept": a,
            "slope_reason": None if b is not None
                            else "sin varianza en turnos (n=1 o todos iguales)"
                                 " — pendiente indefinida",
            "points": list(zip(xs, ys)),
        })
    return {"by_model": by_model}


def _cost_rows(sessions):
    """Split para la vista Coste: pagas (pi con usage.cost), claude-cli
    (provenancia subscription, jamás sumada) y sin coste."""
    paid = [s for s in sessions
            if s.get("context_source") == "pi" and s.get("cost_in") is not None]
    sub = [s for s in sessions if s.get("context_source") == "claude-cli"]
    nulls = [s for s in sessions if s.get("cost_in") is None
             and s.get("context_source") != "claude-cli"]
    return paid, sub, nulls


def _spend(rows, key=None):
    """Spend por split in/out/cache de unas filas (o un modelo)."""
    sel = [r for r in rows if key is None or r.get("model") == key]
    return {"spend_in": round(sum(r["cost_in"] for r in sel), 2),
            "spend_out": round(sum(r["cost_out"] for r in sel), 2),
            "spend_cr": round(sum(r["cost_cr"] for r in sel), 2),
            "spend_cw": round(sum(r["cost_cw"] for r in sel), 2)}


def _cost_by_model(paid):
    """Spend por modelo con $/sesión (pagas)."""
    by_model = []
    for m in sorted({r.get("model") for r in paid}, key=str):
        sp = _spend(paid, m)
        n = sum(1 for r in paid if r.get("model") == m)
        by_model.append(dict(model=m, sessions=n, **sp,
                             spend_per_session=round(
                                 (sp["spend_in"] + sp["spend_out"]
                                  + sp["spend_cr"] + sp["spend_cw"]) / n, 4)
                             if n else 0.0))
    return by_model


def _cost_by_month(paid):
    """Spend mensual de las sesiones pagas."""
    by_month = []
    for ym in sorted({r.get("month") for r in paid}, key=str):
        by_month.append(dict(month=ym, **_spend([r for r in paid
                                                 if r.get("month") == ym])))
    return by_month


def _build_cost(sessions):
    """Vista Coste (coffe-dv6 2.5): spend por modelo con split
    in/out/cache, spend mensual y $/sesión — SOLO sesiones pagas (pi con
    usage.cost). claude-cli emite fila aparte con provenancia
    "subscription" y jamás se suma al total pagó. Coste null con razón."""
    paid, sub, nulls = _cost_rows(sessions)
    null_reasons = [s.get("cost_null_reason") for s in nulls
                    if s.get("cost_null_reason")]
    tot = _spend(paid)
    return {
        "by_model": _cost_by_model(paid),
        "by_month": _cost_by_month(paid),
        "spend_total_in": tot["spend_in"],
        "spend_total_out": tot["spend_out"],
        "spend_total_cr": tot["spend_cr"],
        "spend_total_cw": tot["spend_cw"],
        "subscription": {
            "sessions": len(sub),
            "provenance": "subscription",
            "spend_in": 0.0, "spend_out": 0.0,
            "spend_cr": 0.0, "spend_cw": 0.0,
        },
        "n_null_cost": len(nulls),
        "null_reason_sample": null_reasons[0] if null_reasons else None,
    }


def _shares(rows):
    """Share de chars_by_source sobre un conjunto de sesiones."""
    totals = {}
    for r in rows:
        for src, n in (r.get("chars_by_source") or {}).items():
            totals[src] = totals.get(src, 0) + n
    total = sum(totals.values()) or 1
    return {k: v / total for k, v in totals.items()}


def _build_comp(clean, th):
    """Share por fuente global y en sesiones grandes (> big_session_tokens
    de config) — coffe-opo 2.3. Leyenda = unión de fuentes en ambas."""
    big_th = th.get("big_session_tokens") or 100_000
    big = [s for s in clean if s.get("peak_ctx_est", 0) > big_th]
    glob = _shares(clean)
    big_sh = _shares(big)
    return {
        "global": glob,
        "big": big_sh,
        "big_threshold": big_th,
        "n_big": len(big),
        "legend": sorted(set(glob) | set(big_sh)),
    }


def _build_time(monthly_model, end_date):
    """Tabla mes × modelo dominante (coffe-opo 2.4); meses parciales con
    días transcurridos/total (FPA-041)."""
    rows = []
    for r in sorted(monthly_model,
                    key=lambda x: (str(x.get("month")), str(x.get("model")))):
        ym = r.get("month")
        partial = r.get("partial")
        if ym:
            elapsed = _month_elapsed(ym, end_date)
            days = _month_days(ym)
            days_label = f"{elapsed}/{days}" if partial else f"{days}/{days}"
        else:
            days_label = "n/a"
        rows.append(dict(r, days_label=days_label))
    return {"rows": rows}
