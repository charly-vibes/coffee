"""Recolección (git/beads), filas por (modelo, carril) y agregación del reporte."""

import datetime as dt
import glob
import json
import os
import subprocess
from pathlib import Path

from model_cost_core import (
    CONFIG,
    EXCLUDED_MODELS,
    REPO,
    THRESHOLDS,
    assign_units,
    beads_freshness,
    bounded_window,
    cluster_bootstrap_ci,
    is_comparable,
    lane_for,
    pctl,
    wilson_lower,
)

def local_day(iso_utc, tz_hours=-3):
    d = dt.datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
    return (d.astimezone(dt.timezone(dt.timedelta(hours=tz_hours)))).date().isoformat()

def _exact_match(proj, root, orgs):
    """Ruta exacta org/rest si existe con .git; None si no."""
    org, _, rest = proj.partition("-")
    if org in orgs and rest and (Path(root) / org / rest / ".git").is_dir():
        return str(Path(root) / org / rest)
    return None

def _fuzzy_hit(rest, root):
    """Único repo <org>/<rest> bajo root con .git; None si 0 o ambiguo."""
    if not rest:
        return None
    hits = [p for p in glob.glob(os.path.join(root, "*", glob.escape(rest)))
            if (Path(p) / ".git").is_dir()]
    return hits[0] if len(hits) == 1 else None

def resolve_projects(projects, root):
    """proyecto → ruta del repo (EDGE-003: assert del org, no a ciegas)."""
    orgs = {p.name for p in Path(root).iterdir() if p.is_dir()}
    out = {}
    for proj in projects:
        _, _, rest = proj.partition("-")
        hit = _exact_match(proj, root, orgs) or _fuzzy_hit(rest, root)
        if hit:
            out[proj] = hit
    return out

def _git(path, *args):
    try:
        r = subprocess.run(["git", "-C", path, *args], capture_output=True, text=True)
        return r.stdout
    except OSError:
        return ""

def commit_times(path):
    out = _git(path, "log", "--no-merges", "--format=%at")
    return sorted(int(x) for x in out.split())

def last_commit_ts(path):
    out = _git(path, "log", "-1", "--format=%at").strip()
    return int(out) if out.isdigit() else None

def beads_closes(path):
    """[(id, ts)] de .beads/issues.jsonl; vacío si el repo no trackea beads."""
    f = Path(path) / ".beads" / "issues.jsonl"
    if not f.is_file():
        return None
    out = []
    for line in f.read_text().splitlines():
        try:
            o = json.loads(line)
        except ValueError:
            continue
        ts = o.get("closed_at")
        if not ts:
            continue
        d = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        out.append((f"{path}|{o.get('id')}", d.timestamp()))
    return sorted(out, key=lambda x: x[1])

def load_sessions(report_path, since=None, until=None, tz_hours=-3):
    rep = json.load(open(report_path))
    subs = json.load(open(CONFIG)).get("subscriptions", {})
    sessions = []
    for r in rep["session_spend"]:
        if r.get("cost_effective") is None:
            continue
        day = local_day(r["start"], tz_hours)
        if (since and day < since) or (until and day > until):
            continue
        sessions.append(_session_row(r, day))
    return sessions, subs, rep.get("metadata", {})

def _session_row(r, day):
    return {
        "session_id": r["session_id"], "model": r["model"], "source": r["source"],
        "project": r["project"], "day": day,
        "cost_effective": r.get("cost_effective"), "cost_real": r.get("cost_real"),
        "start": dt.datetime.fromisoformat(r["start"].replace("Z", "+00:00")).timestamp(),
        "end": dt.datetime.fromisoformat(r["end"].replace("Z", "+00:00")).timestamp(),
    }


def _bead_events(path):
    closes = beads_closes(path)
    if closes is None:
        return [], "sin_beads"
    export = Path(path) / ".beads" / "issues.jsonl"
    fresh = beads_freshness(export.stat().st_mtime if export.is_file() else None,
                            last_commit_ts(path), THRESHOLDS["stale_export_days"])
    if fresh != "fresh":
        return [], f"beads_{fresh}"
    return closes, None

def _project_events(proj, path, unit):
    """(eventos, exclusión). exclusión = (proj, razón, freshness|None) o None."""
    if unit != "bead":
        return [(f"{path}|{t}", t) for t in commit_times(path)], None
    closes, reason = _bead_events(path)
    if not reason:
        return closes, None
    fresh = reason[len("beads_"):] if reason.startswith("beads_") else None
    return [], (proj, reason, fresh)

def _gather_events(projects, unit):
    events, excluded_repos, stale = [], {}, []
    for proj, path in projects.items():
        ev, excl = _project_events(proj, path, unit)
        if excl:
            excluded_repos[excl[0]] = excl[1]
            if excl[2] is not None:
                stale.append({"project": proj, "freshness": excl[2]})
        events.extend(ev)
    return events, excluded_repos, stale

def _windows(sessions):
    out = []
    for s in sessions:
        a, b, amb = bounded_window(s["start"], s["end"], THRESHOLDS["max_window_s"])
        s["ambiguous"] = amb
        out.append({"id": s["session_id"], "start": a, "end": b + THRESHOLDS["slack_s"]})
    return out

def _coverage_meta(sessions, projects, spend):
    """Cobertura de proyectos: resueltos y % del gasto real."""
    resolved = sum(float(s.get("cost_real") or 0)
                   for s in sessions if s["project"] in projects)
    return {
        "projects_resolved": len(projects),
        "projects_total": len({s["project"] for s in sessions}),
        "spend_resolved_pct": round(100 * resolved / spend, 1) if spend else None,
    }

def _events_meta(events, units):
    """Crédito de eventos uno-a-uno sobre las unidades asignadas."""
    credited = sum(1 for v in units.values() if v)
    return {
        "total": len(events), "credited": credited,
        "distinct_units": len({k for k, v in units.items() if v}),
        "credit_rate": round(credited / len(events), 3) if events else None,
    }

def collect(report_path, root, unit, window):
    """(sesiones, unidades, proyectos, suscripciones, meta) de la ventana."""
    since, until, tz_hours = window
    sessions, subs, rep_meta = load_sessions(report_path, since, until, tz_hours)
    projects = resolve_projects({s["project"] for s in sessions}, root)
    events, excluded_repos, stale = _gather_events(projects, unit)
    units = assign_units(events, _windows(sessions))
    spend = sum(float(s.get("cost_real") or 0) for s in sessions)
    p = str(report_path)
    meta = {
        "generated": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "window": {"since": since, "until": until, "timezone": tz_hours},
        "unit": unit,
        "report": p[len(str(REPO)) + 1:] if p.startswith(str(REPO) + os.sep) else p,
        "report_date_range": rep_meta.get("date_range"),
        "sessions": len(sessions),
        "cost_real_total": round(spend, 2),
        "coverage": _coverage_meta(sessions, projects, spend),
        "events": _events_meta(events, units),
        "excluded_repos": excluded_repos,
        "stale_beads_repos": stale,
        "note": "estimación de orden de magnitud; no es la cohorte de .9 ni el scorecard de .8",
    }
    return sessions, units, projects, subs, meta


from model_cost_rows import aggregate_report, build_rows  # noqa: E402,F401 — re-export tests/CLI
