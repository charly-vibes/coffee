"""Módulo extraído de viz-fpa.py (decomposición, comportamiento idéntico)."""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import schema_validate  # noqa: E402

def _session_buckets(raw):
    """FPA-116: buckets 1–10/11–50/51–100 más un bucket 100+ catch-all."""
    counts = {label: raw.get(key, 0) for key, label in _SESSION_BUCKET_LABELS}
    counts["100+"] = sum(v for k, v in raw.items()
                         if k not in {key for key, _ in _SESSION_BUCKET_LABELS})
    return [{"label": label, "count": counts[label]}
            for label in ("1–10", "11–50", "51–100", "100+")]
def _clear_per_100(by_tool):
    """coffe-i31: clear_per_100 con denominador claude-only (el /clear solo
    existe en Claude; dividir por el total global sesga el ratio)."""
    claude = by_tool.get("claude-cli", {}) or {}
    claude_total = claude.get("total") or 0
    clears = ((claude.get("resets") or {}).get("count"))
    if claude_total and clears is not None:
        return 100.0 * clears / claude_total, None
    return None, ("sin sesiones claude en la ventana o sin /clear "
                  "emitido (FPA-118); el ratio es claude-only")
def build_usage_sessions(report, cfg):
    """FPA-116…118: buckets de longitud de sesión (1–10, 11–50, 51–100,
    100+), sesiones más largas, coste/mediana/p90 (n/a sin coste por
    sesión) y resets por 100 sesiones con provenance por tool (coffe-i31:
    /clear es una métrica claude-only; en pi el reset es /new = archivo
    nuevo, el denominador del ratio es claude-only y se declara)."""
    sessions = report.get("sessions", {}) or {}
    raw = sessions.get("length_distribution", {}) or {}
    cost_reason = "el tracker no emite coste por sesión (FPA-116/117)"
    longest = [{"turns": s.get("turns"), "date": s.get("date"),
                "project": s.get("project"), "cost": None,
                "cost_reason": cost_reason}
               for s in sessions.get("top_longest_by_turns", [])]
    clear_per_100, clear_scope_reason = _clear_per_100(
        sessions.get("by_tool", {}) or {})
    resets_by_tool = {t: (v.get("resets") or {})
                      for t, v in (sessions.get("by_tool", {}) or {}).items()}
    monthly_trend, monthly_reason = _na(
        "el tracker no emite /clear ni sesiones por mes (FPA-118)")
    long_turns = _cfg_int(cfg, "sessions", "long_turns", 100)
    no_clear, no_clear_reason = _na(
        "el tracker no marca /clear por sesión (FPA-118)")
    return {
        "buckets": _session_buckets(raw),
        "longest": longest,
        "cost_by_bucket": None, "cost_by_bucket_reason": cost_reason,
        "median_p90": None, "median_p90_reason": cost_reason,
        "clear_per_100": clear_per_100,
        "clear_scope_reason": clear_scope_reason,
        "resets_by_tool": resets_by_tool,
        "total_sessions": sessions.get("total_sessions") or 0,
        "clear_monthly": monthly_trend,
        "clear_monthly_reason": monthly_reason,
        "long_no_clear": no_clear,
        "long_no_clear_reason": no_clear_reason,
        "long_turns": long_turns,
    }
def build_timeline(report, cfg):
    """FPA-119: primera/última actividad por tool y por model, con gaps
    > gap_days (config, default 7) marcados. Vista de día → hereda el
    flag unverified de timezone (FPA-142)."""
    gap_days = _cfg_int(cfg, "timeline", "gap_days", 7)
    tools, models = {}, {}
    for ts in sorted(report.get("hourly", {})):
        d = ts[:10]
        entry = report["hourly"][ts]
        for tool in entry.get("tools", {}):
            tools.setdefault(tool, set()).add(d)
        for model in entry.get("models", {}):
            models.setdefault(model, set()).add(d)

    def _series(days):
        days = sorted(days)
        gaps = []
        for prev, cur in zip(days, days[1:]):
            gap = (date.fromisoformat(cur) - date.fromisoformat(prev)).days
            if gap > gap_days:
                gaps.append({"from": prev, "to": cur, "days": gap})
        return {
            "first": days[0], "last": days[-1],
            "gaps": gaps,
            "max_gap_days": max((g["days"] for g in gaps), default=0),
            "flagged": bool(gaps),
        }

    tz = report["metadata"].get("timezone")
    return {
        "verified": tz is not None,
        "unverified_reason": (None if tz else
                              "sin timezone del tracker, fechas por día no "
                              "verificadas (FPA-142)"),
        "gap_days": gap_days,
        "tools": [_series(v) | {"name": k}
                  for k, v in sorted(tools.items())],
        "models": [_series(v) | {"name": k}
                   for k, v in sorted(models.items())],
    }
def build_usage_concurrency(report):
    """FPA-120: (a) proyectos distintos/hora y (b) pico de sesiones o
    agentes simultáneos etiquetados parallel-agent; (c) switches de
    proyecto/hora activa etiquetado human-context-switching."""
    emitted = report.get("concurrency")
    if emitted:  # el tracker emite las medidas etiquetadas
        return {
            "projects_per_hour": emitted.get("distinct_projects_per_hour",
                                             {"measure": "parallel-agent"}),
            "peak_simultaneous_sessions": emitted.get(
                "peak_simultaneous_sessions",
                {"measure": "parallel-agent"}),
            "switches_per_hour": emitted.get(
                "project_switches_per_active_hour",
                {"measure": "human-context-switching"}),
        }
    active = [h.get("projects_active", 0)
              for h in report.get("hourly", {}).values()
              if h.get("interactions", 0)]
    if active:
        pph = {"measure": "parallel-agent",
               "peak": max(active),
               "avg": sum(active) / len(active)}
    else:
        pph = {"measure": "parallel-agent", "peak": None,
               "avg": None, "reason": "sin horas activas en hourly"}
    peak, peak_reason = _na("el tracker no emite sesiones/agentes "
                            "simultáneos (FPA-120b)")
    mt = report.get("multitasking", {}) or {}
    switches_total = (mt.get("context_switches", {}) or {}).get("total")
    active_hours = (mt.get("hourly", {}) or {}).get("total_active_hours")
    if switches_total is not None and active_hours:
        switches = {"measure": "human-context-switching",
                    "value": switches_total / active_hours}
    else:
        switches = {"measure": "human-context-switching", "value": None,
                    "reason": "el tracker no emite switches de proyecto "
                              "(FPA-120c)"}
    return {
        "projects_per_hour": pph,
        "peak_simultaneous_sessions": {
            "measure": "parallel-agent", "peak": peak,
            "reason": peak_reason},
        "switches_per_hour": switches,
    }
def _agent_tool_shares(by_tool):
    """FPA-121: share con Agent por tool (None + reason sin sesiones)."""
    shares = {}
    for t, v in by_tool.items():
        t_total = v.get("total") or 0
        shares[t] = {
            "share": (v.get("with_agent", 0) / t_total) if t_total else None,
            "reason": None if t_total else "sin sesiones de esta tool",
        }
    return shares
def _agent_monthly(monthly_raw):
    """FPA-121: trend mensual del share con Agent (o n/a del tracker)."""
    if monthly_raw:
        return ([{"ym": ym,
                  "share": (v.get("with_agent", 0) / v["total"]
                            if v.get("total") else None)}
                 for ym, v in sorted(monthly_raw.items())], None)
    return _na("el tracker no emite sesiones por mes (FPA-121)")
def build_agent_share(report):
    """FPA-121: share de sesiones con Agent, total y trend mensual si el
    tracker emite sesiones por mes. coffe-i31: share por tool — la
    semántica de Agent difiere por harness (agent_semantics)."""
    sessions = report.get("sessions", {}) or {}
    total = sessions.get("total_sessions") or 0
    with_agent = sessions.get("with_agent") or 0
    monthly, monthly_reason = _agent_monthly(
        report.get("sessions_monthly"))
    return {"share": with_agent / total if total else None,
            "monthly": monthly,
            "monthly_reason": monthly_reason,
            "by_tool_shares": _agent_tool_shares(
                sessions.get("by_tool", {}) or {}),
            "agent_semantics": sessions.get("agent_semantics")}
def _project_status(p, end, new_days, dormant_days):
    """FPA-122: estado new/active/dormant de un proyecto; None sin fechas."""
    first, last = p.get("first_seen"), p.get("last_seen")
    if not first or not last:
        return None
    d_first = (end - date.fromisoformat(first)).days
    d_last = (end - date.fromisoformat(last)).days
    if d_first <= new_days:
        return "new"
    if d_last > dormant_days:
        return "dormant"
    return "active"
def _active_by_month(project_monthly):
    """FPA-123: proyectos con interacciones > 0 por mes (o n/a tracker)."""
    if not project_monthly:
        return _na("el tracker no emite actividad de proyectos por mes "
                   "(FPA-123)")
    months_acc = {}
    for pms in project_monthly.values():  # {proyecto: {ym: metrics}}
        for ym, v in pms.items():
            if v.get("interactions", 0) > 0:
                months_acc[ym] = months_acc.get(ym, 0) + 1
    return ([{"ym": ym, "count": months_acc[ym]}
             for ym in sorted(months_acc)], None)
def build_lifecycle(report, cfg):
    """FPA-122/123: clasificación de proyectos new/active/dormant contra
    la fecha fin del periodo (determinista), coste efectivo de dormantes
    (total y por proyecto) y activos por mes (si el tracker emite
    project_monthly)."""
    lifecycle = cfg.get("lifecycle", {})
    new_days = int(lifecycle.get("new_days", 30))
    dormant_days = int(lifecycle.get("dormant_days", 30))
    end = date.fromisoformat(report["metadata"]["date_range"]["end"])
    projects, dormant_cost = [], 0.0
    for name, p in sorted((report.get("projects", {}) or {}).items()):
        status = _project_status(p, end, new_days, dormant_days)
        if status is None:
            continue
        if status == "dormant":
            dormant_cost += p.get("cost_effective", 0.0)
        projects.append({"name": name, "status": status,
                         "first_seen": p.get("first_seen"),
                         "last_seen": p.get("last_seen")})
    n_dormant = sum(1 for p in projects if p["status"] == "dormant")
    by_month, month_reason = _active_by_month(report.get("project_monthly"))
    return {
        "reference": end.isoformat(), "new_days": new_days,
        "dormant_days": dormant_days,
        "projects": projects,
        "dormant": {"count": n_dormant, "cost_total": dormant_cost,
                    "cost_per_project": (dormant_cost / n_dormant
                                         if n_dormant else None)},
        "active_by_month": by_month,
        "active_by_month_reason": month_reason,
    }

_PARETO_TOP = 10
def _pareto_rows(ordered, total):
    """FPA-028: filas del top N con share y share acumulado."""
    rows, cum = [], 0.0
    for name, cost in ordered[:_PARETO_TOP]:
        cum += cost / total if total else 0.0
        rows.append({"name": name, "cost": cost,
                     "share": cost / total if total else None,
                     "cumulative_share": cum})
    return rows
def _pareto_tail(ordered, total):
    """FPA-028: la cola (fuera del top N) agrupada en una fila."""
    tail_items = ordered[_PARETO_TOP:]
    if not tail_items:
        return None
    tail_cost = sum(c for _, c in tail_items)
    return {"count": len(tail_items), "cost": tail_cost,
            "share": tail_cost / total if total else None,
            "cumulative_share": 1.0}
def build_pareto(report):
    """FPA-028/036: proyectos por coste efectivo con share y share
    acumulado; la cola (fuera del top N) se agrupa en una fila. Top-3
    como concentración del coste efectivo (FPA-036)."""
    costs = {name: p.get("cost_effective", 0.0)
             for name, p in (report.get("projects", {}) or {}).items()
             if p.get("cost_effective", 0.0) > 0}
    total = sum(costs.values())
    ordered = sorted(costs.items(), key=lambda x: (-x[1], x[0]))
    top3 = sum(c for _, c in ordered[:3])
    return {"rows": _pareto_rows(ordered, total),
            "tail": _pareto_tail(ordered, total), "total": total,
            "top3_share": top3 / total if total else None}
def build_usage_patterns(report, cfg):
    """Modelo F5 completo (va embebido en el JSON del dashboard)."""
    return {
        "heatmap": build_heatmap(report),
        "rhythm": build_rhythm(report, cfg),
        "skills": build_usage_skills(report),
        "commands": build_usage_commands(report),
        "sessions": build_usage_sessions(report, cfg),
        "timeline": build_timeline(report, cfg),
        "concurrency": build_usage_concurrency(report),
        "agent_share": build_agent_share(report),
        "lifecycle": build_lifecycle(report, cfg),
        "pareto": build_pareto(report),
        "repo_overlay": {"available": False,
                         "reason": "el tracker no emite fechas de creación "
                                   "de repos (FPA-098, condicional)"},
    }

# Validación de schema stdlib-only (FPA-006) — única fuente compartida
# en scripts/schema_validate.py (extraído en coffe-nea para ddl-effect)
validate_against_schema = schema_validate.validate_against_schema
from viz_fpa_common import _link  # noqa: E402

_link(globals())
