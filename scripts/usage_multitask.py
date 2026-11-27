#!/usr/bin/env python3
"""Métricas de multitasking (extraído de usage-tracker.py).

Parte de la descomposición de scripts/usage-tracker.py (coffe-kav.1):
cada módulo conserva el código original verbatim; los nombres compartidos
viajan por _link() (mismo patrón que viz_fpa_common) y el estado mutable
por usage_state.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import usage_state

def _context_switches(interactions):
    """Cambios de proyecto entre requests consecutivos (por día)."""
    switches_by_day = Counter()
    prev = None
    for r in sorted(interactions, key=lambda x: x["timestamp"]):
        d = r["timestamp"][:10]
        p = clean_proj_name(r.get("project", "unknown"))
        if prev and prev[1] != p:
            switches_by_day[d] += 1
        prev = (d, p)
    return switches_by_day


def _project_daily(by_project, day_projects, proj_day):
    """Matriz densa proyecto × día para el Gantt."""
    from datetime import date as _date, timedelta as _td
    all_days = []
    if day_projects:
        d0 = _date.fromisoformat(min(day_projects))
        d1 = _date.fromisoformat(max(day_projects))
        cur = d0
        while cur <= d1:
            all_days.append(cur.isoformat())
            cur += _td(days=1)
    return {
        "days": all_days,
        "matrix": {
            p: [proj_day[p].get(d, 0) for d in all_days]
            for p in sorted(by_project, key=lambda x: -sum(proj_day[x].values()))
        },
    }


def _mt_dist(mt_hour_counts):
    mt_dist = Counter()
    for n in mt_hour_counts.values():
        if n == 1:
            mt_dist["1"] += 1
        elif n == 2:
            mt_dist["2"] += 1
        elif n == 3:
            mt_dist["3"] += 1
        else:
            mt_dist["4+"] += 1
    return mt_dist


def _mt_stats(counts):
    """(n_multi, max_n, max_keys) de un conteo {clave: proyectos}."""
    n_multi = sum(1 for n in counts.values() if n >= 2)
    max_n = max(counts.values()) if counts else 0
    max_keys = ([k for k, n in counts.items() if n == max_n]
                if counts else [])
    return n_multi, max_n, max_keys


def _mt_hourly(hour_projects, mt_hour_counts):
    total_active_hours = len(hour_projects)
    mt_hours_n, max_n, max_hours = _mt_stats(mt_hour_counts)
    top_mt_hours = sorted(mt_hour_counts.items(), key=lambda x: -x[1])[:10]
    return {
        "total_active_hours": total_active_hours,
        "hours_with_multiple_projects": mt_hours_n,
        "pct_hours_multitasking": round(100 * mt_hours_n / total_active_hours, 1) if total_active_hours else 0,
        "avg_projects_per_active_hour": round(sum(mt_hour_counts.values()) / total_active_hours, 2) if total_active_hours else 0,
        "distribution": dict(_mt_dist(mt_hour_counts).most_common()),
        "max_projects_in_one_hour": {
            "count": max_n,
            "hours": max_hours[:5],
            "projects": sorted(hour_projects[max_hours[0]]) if max_hours else [],
        },
        "top_10_hours": [
            {"hour": h, "projects": n, "list": sorted(hour_projects[h])}
            for h, n in top_mt_hours
        ],
    }


def _mt_daily(day_projects, daily):
    active_days = len(day_projects)
    mt_days = {d: len(ps) for d, ps in day_projects.items()}
    mt_days_n = sum(1 for n in mt_days.values() if n >= 2)
    top_mt_days = sorted(mt_days.items(), key=lambda x: -x[1])[:10]
    return {
        "total_active_days": active_days,
        "days_with_multiple_projects": mt_days_n,
        "pct_days_multitasking": round(100 * mt_days_n / active_days, 1) if active_days else 0,
        "avg_projects_per_active_day": round(sum(mt_days.values()) / active_days, 2) if active_days else 0,
        "top_10_days": [
            {"date": d, "projects": n, "list": sorted(day_projects[d]),
             "interactions": daily[d]["interactions"] if d in daily else 0}
            for d, n in top_mt_days
        ],
    }


def _mt_switches(switches_by_day, active_days):
    total_switches = sum(switches_by_day.values())
    top_switch_days = sorted(switches_by_day.items(), key=lambda x: -x[1])[:10]
    return {
        "total": total_switches,
        "avg_per_active_day": round(total_switches / active_days, 1) if active_days else 0,
        "max_in_one_day": top_switch_days[0][1] if top_switch_days else 0,
        "top_10_days": dict(top_switch_days),
    }


def _multitasking_block(hour_projects, day_projects, daily, switches_by_day):
    mt_hour_counts = {h: len(ps) for h, ps in hour_projects.items()}
    return {
        "description": (
            "Proyectos distintos con actividad en la misma ventana. "
            "'context_switches' cuenta cambios de proyecto entre requests "
            "consecutivos (puede inflarse por agentes paralelos en el mismo minuto)."
        ),
        "hourly": _mt_hourly(hour_projects, mt_hour_counts),
        "daily": _mt_daily(day_projects, daily),
        "context_switches": _mt_switches(switches_by_day, len(day_projects)),
    }


_LENGTH_BUCKETS = ((10, "1-10"), (50, "11-50"), (100, "51-100"),
                   (300, "101-300"), (500, "301-500"))

usage_state._link(globals())
