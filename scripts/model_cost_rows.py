"""Filas por (modelo, carril): construcción, finalización y agregación."""

from model_cost_core import (
    EXCLUDED_MODELS,
    THRESHOLDS,
    cluster_bootstrap_ci,
    is_comparable,
    lane_for,
    pctl,
    wilson_lower,
)

def _empty_row(model, lane):
    return {
        "model": model, "lane": lane, "n_sessions": 0, "cost": 0.0,
        "cost_imputed": 0.0, "units": 0, "n_productive": 0, "n_ambiguous": 0,
        "cost_ambiguous": 0.0, "units_ambiguous": 0, "unit_costs": [],
        "unit_costs_strict": [], "unit_clusters": [], "session_costs": [],
    }

def _units_by_session(units):
    out = {}
    for key, sid in units.items():
        if sid:
            out[sid] = out.get(sid, 0) + 1
    return out

def _lane_key(s, projects, subscriptions):
    if s["model"] in EXCLUDED_MODELS:
        return None
    if s["project"] not in projects:
        return ("unattributed", "n/a")
    return (s["model"], lane_for(s["source"], subscriptions))

def _absorb(row, s, n_units):
    cost = s.get("cost_real")
    cost = 0.0 if cost is None else float(cost)
    row["n_sessions"] += 1
    row["cost"] += cost
    row["cost_imputed"] += float(s.get("cost_effective") or 0.0)
    row["units"] += n_units
    row["session_costs"].append(cost)
    cluster = f"{s['project']}|{s.get('day', '')}"
    if n_units:
        row["n_productive"] += 1
        row["unit_costs"].append(cost / n_units)
        row["unit_clusters"].append(cluster)
        if not s.get("ambiguous"):
            row["unit_costs_strict"].append(cost / n_units)
    if s.get("ambiguous"):
        row["n_ambiguous"] += 1
        row["cost_ambiguous"] += cost
        row["units_ambiguous"] += n_units

def _collect_rows(sessions, units, projects, subscriptions):
    rows, by_session = {}, _units_by_session(units)
    for s in sessions:
        key = _lane_key(s, projects, subscriptions)
        if key is None:
            continue
        row = rows.setdefault(key, _empty_row(*key))
        _absorb(row, s, by_session.get(s["session_id"], 0))
    return rows

def _finalize_row(row):
    ucl, uc, ucs, sc = (row.pop(k) for k in
                        ("unit_clusters", "unit_costs", "unit_costs_strict", "session_costs"))
    med = halfwidth = lo = hi = None
    if uc:
        med = pctl(uc, 0.5)
        lo, hi = cluster_bootstrap_ci(uc, ucl, B=THRESHOLDS["bootstrap_B"],
                                      seed=THRESHOLDS["bootstrap_seed"])
        if lo is not None and med:
            halfwidth = (hi - lo) / 2 / med
    ok, reason = (is_comparable(row["n_productive"], halfwidth) if uc
                  else (False, "sin_unidades"))
    row.update({
        "unit": "unit",
        "n_sessions": row["n_sessions"],
        "yield": (row["n_productive"] / row["n_sessions"]) if row["n_sessions"] else None,
        "yield_lb": wilson_lower(row["n_productive"], row["n_sessions"]),
        "p50_unit_cost": med,
        "p50_unit_cost_strict": pctl(ucs, 0.5),
        "n_strict": len(ucs),
        "ci90": [lo, hi],
        "ci_halfwidth_rel": halfwidth,
        "p50_session_cost": pctl(sc, 0.5),
        "p99_session_cost": pctl(sc, 0.99),
        "comparable": ok,
        "comparable_reason": reason,
        "units_ambiguous_share": (row["units_ambiguous"] / row["units"])
        if row["units"] else None,
    })
    return row

def build_rows(sessions, units, projects, subscriptions):
    """Filas por (modelo, carril). `units` ya viene asignado uno-a-uno."""
    rows = _collect_rows(sessions, units, projects, subscriptions)
    return [_finalize_row(row) for row in rows.values()]

def _excluded_mixed(sessions):
    acc = {"n_sessions": 0, "cost": 0.0}
    for s in sessions:
        if s["model"] in EXCLUDED_MODELS:
            acc["n_sessions"] += 1
            acc["cost"] += float(s.get("cost_real") or 0.0)
    return acc

def aggregate_report(sessions, rows, meta):
    excluded = _excluded_mixed(sessions)
    unattributed = [r for r in rows if r["model"] == "unattributed"]
    payload = dict(meta)
    payload.update({
        "unit": meta.get("unit", "unit"),
        "thresholds": THRESHOLDS,
        "dedupe_rule": "uno-a-uno: cada evento a la sesión de inicio más cercano",
        "cost_basis": "cost = cost_real; cost_imputed = cost_effective (solo notional)",
        "excluded": excluded,
        "unattributed": unattributed[0] if unattributed else None,
        "rows": sorted([r for r in rows if r["model"] != "unattributed"],
                       key=lambda r: (r["lane"], -(r["p50_unit_cost"] or 0))),
    })
    return payload
