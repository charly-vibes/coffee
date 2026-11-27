"""Módulo extraído de agent-metrics-tracker.py (decomposición, comportamiento idéntico).

Agregaciones puras: métricas por día, series compactas y sesiones.
"""
from __future__ import annotations

from agent_metrics_common import task_day
from agent_metrics_tasks import task_flags


# ----------------------------------------------------------------------
# Agregaciones puras
# ----------------------------------------------------------------------

def daily_from_tasks(tasks, tz=None):
    """Tareas → métricas por día (día de la TZ del reporte)."""
    daily = {}
    for t in tasks:
        day = task_day(t, tz)
        d = daily.setdefault(day, {"prompts": 0, "tasks": 0, "turns": 0,
                                   "agent_s": 0.0, "aborted": 0, "errors": 0,
                                   "tool_errors": 0, "retry": 0})
        d["prompts"] += 1
        d["tasks"] += 1
        d["turns"] += t["turns"]
        d["agent_s"] += t["busy_s"]
        d["aborted"] += t["aborted"]
        d["errors"] += t["errors"]
        d["tool_errors"] += t["tool_errors"]
        d["retry"] += t["retry"]
    for d in daily.values():
        d["agent_s"] = round(d["agent_s"], 1)
    return daily


def _series_axes(days, sources, models):
    """Índices de día/fuente/modelo para las series compactas."""
    return {"day": {d: i for i, d in enumerate(days)},
            "src": {s: i for i, s in enumerate(sources)},
            "model": {m: i for i, m in enumerate(models)}}


def _series_row(out, t, ix, tz):
    day = task_day(t, tz)
    if day not in ix["day"]:
        return
    out["day"].append(ix["day"][day])
    out["s"].append(int(round(t["busy_s"])))
    out["sp"].append(int(round(t.get("span_s") or 0)))
    out["src"].append(ix["src"].get(t.get("source"), 0))
    out["model"].append(ix["model"].get(t.get("model") or "?", 0))
    out["f"].append(task_flags(t))
    out["sid"].append(t.get("_sid"))
    out["cost"].append(round(float(t.get("cost") or 0.0), 8))
    tok = t.get("tokens") or {}
    out["tokens"].append({k: int(tok.get(k) or 0)
                          for k in ("in", "out", "cache_read",
                                    "cache_write")})


def series(tasks, *axes, tz=None):
    """Series compactas para percentiles exactos por ventana en el viz.

    `day` = índice en `days` (día local), `s` = segundos ocupados (int),
    `sp` = segundos de reloj de la tarea (incluye esperas),
    `src`/`model` = índice, `f` = bitmask de flags.
    coffe-dnx.3: `sid` = índice en `sessions[]` (null si la tarea no conoce
    su sesión), `cost` = coste de los turnos assistant de la tarea, `tokens`
    = {in, out, cache_read, cache_write} acumulados.

    `axes` = (days, sources, models), en ese orden.
    """
    days, sources, models = axes
    ix = _series_axes(days, sources, models)
    out = {"day": [], "s": [], "sp": [], "src": [], "model": [], "f": [],
           "sid": [], "cost": [], "tokens": []}
    for t in tasks:
        _series_row(out, t, ix, tz)
    return out


def _sessions_from_tasks(tasks):
    """coffe-dnx.3: sessions[] = sesiones con al menos una tarea, en orden de
    primera aparición; cada tarea lleva `_sid` = índice en esa lista. Una
    sesión sin tareas (p. ej. un prompt abortado antes de la respuesta) no
    aparece. `None` como session_id se desambigua por identidad del dict."""
    seen = {}
    sessions = []
    for t in tasks:
        ses = t.get("_ses")
        if not ses:
            continue
        key = ("__id__", id(ses)) if ses.get("session_id") is None else (
            ses.get("source"), ses.get("session_id"))
        if key not in seen:
            seen[key] = len(sessions)
            sessions.append({"source": ses.get("source"),
                             "project": ses.get("project"),
                             "session_id": ses.get("session_id")})
        t["_sid"] = seen[key]
    return sessions
