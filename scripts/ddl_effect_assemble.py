"""Secciones ITS/DiD/dosis-respuesta y ensamblado del reporte (ddl-effect-tracker)."""
from __future__ import annotations

from ddl_effect_adoption import extract_adoption
from ddl_effect_core import FLEET_REPOS, MANAGED_TOOLS
from ddl_effect_analysis import did_2x2, dose_response, its_pre_post, repo_deltas
from ddl_effect_emitters import (ANALYSIS_METRICS, _activity_months,  # noqa: F401
                                _control_flags, _current_dose, _first_use_index,
                                _month_cell, _repos_out, _sessions_per_month)
from ddl_effect_panel import build_panel
from ddl_effect_sessions import (events_by_repo_month, uses_rows)

def _its_section(repos_out, rows_by_repo):
    """ITS pre/post por repo-métrica con mes de adopción propio."""
    its = []
    for entry in repos_out:
        adm = entry["adoption"]["first_ddl_commit"]
        if not adm:
            continue
        for metric in ANALYSIS_METRICS:
            res = its_pre_post(rows_by_repo.get(entry["repo"], []), metric,
                               str(adm)[:7])
            its.append({"repo": entry["repo"], "metric": metric,
                        "adoption_month": str(adm)[:7], **res})
    return its


def _did_section(repos_out, rows_by_repo):
    """DiD 2×2 por métrica contra la frontera de adopción más temprana."""
    frontiers = sorted(str(e["adoption"]["first_ddl_commit"])[:7]
                       for e in repos_out
                       if e["adoption"]["first_ddl_commit"])
    did = []
    if not frontiers:
        return did
    frontier = frontiers[0]
    control_repos = {e["repo"] for e in repos_out if e["is_control"]}
    for metric in ANALYSIS_METRICS:
        deltas = repo_deltas(rows_by_repo, metric, frontier)
        res = did_2x2(deltas, control_repos)
        did.append({"metric": metric, "adoption_frontier": frontier,
                    **res})
    return did


def _dose_points(repos_out, rows_by_repo, metric):
    """Pares (dosis, outcome medio) por repo para dosis-respuesta."""
    import statistics
    points, scatter = [], []
    for entry in repos_out:
        vals = [r[metric] for r in rows_by_repo.get(entry["repo"], [])
                if r.get(metric) is not None]
        if not vals:
            continue
        mean = statistics.fmean(vals)
        points.append({"repo": entry["repo"],
                       "dose": entry["dose"],
                       metric: mean})
        scatter.append({"repo": entry["repo"],
                        "dose": entry["dose"], "outcome": mean})
    return points, scatter


def _dose_response_section(repos_out, rows_by_repo):
    """Spearman dosis × outcome por métrica, con scatter visible."""
    out = []
    for metric in ANALYSIS_METRICS:
        points, scatter = _dose_points(repos_out, rows_by_repo, metric)
        out.append({"metric": metric,
                    **dose_response(points, metric),
                    "points": scatter})
    return out


def _fleet_provenance(fleet, git, gh, sessions_per_month):
    """Presencia de insumos por repo + conteo total de sesiones pi."""
    prov = {}
    for repo in fleet:
        prov[repo] = {
            "git": repo in git,
            "gh": repo in gh,
            "sessions": sessions_per_month.get((repo, ""), 0)
            or sum(n for (r, _m), n in sessions_per_month.items()
                   if r == repo),
        }
    return prov


def _metadata(now, fleet, insumos, ctx):
    """metadata del reporte: seeds, flota, provenance por fuente, bias.

    ctx: acumuladores del panel (sessions_per_month, clamped_total).
    """
    git = insumos.get("git") or {}
    gh = insumos.get("gh") or {}
    ah = insumos.get("ah") or {}
    sessions_per_month = ctx["sessions_per_month"]
    return {
        "generated_at": now,
        "seed_bootstrap": 42,
        "managed_tools": list(MANAGED_TOOLS),
        "fleet_definition": "repos charly-vibes con actividad en "
                            "sesiones in-scope (walk pi) o interacciones "
                            "en project_monthly",
        "provenance": {
            "fleet": _fleet_provenance(fleet, git, gh, sessions_per_month),
            "gh": {repo: (gh.get(repo) or {}).get("provenance", "missing")
                   for repo in fleet},
            "terapia": insumos.get("terapia") or {
                "data": None, "provenance": "missing",
                "reason": "sin insumo terapia"},
            "ah": {repo: (ah.get(repo) or {}).get("provenance", "missing")
                   for repo in fleet},
            "sessions_bias": "sessions cuenta sólo sesiones pi (las "
                             "otras fuentes no exponen cwd por sesión; "
                             "coffe-i31)",
        },
        "clamped_negative_latencies": ctx["clamped_total"],
    }


def _insumo(insumos, key, default):
    """insumos.get(key) con default explícito (None → default)."""
    val = insumos.get(key)
    return default if val is None else val


def _commit_rows(git):
    """git {repo: {commits}} → filas planas [{repo, date, message, files}]."""
    return [dict(c, repo=repo)
            for repo, g in git.items()
            for c in (g.get("commits") or [])]


def _rows_by_repo(panel):
    """panel → {repo: filas ordenadas} (insumo de ITS/DiD/dosis-respuesta)."""
    rows = {}
    for r in panel:
        rows.setdefault(r["repo"], []).append(r)
    return rows


def _panel_cells(activity, ctx):
    """1 celda por (repo, mes) con actividad, en orden reproducible."""
    return [_month_cell(repo, month, ctx)
            for repo, month in sorted(activity)]


def _repos_section(insumos, fleet):
    """Sección repos[]: adopción + dosis corriente + control (D3, D6).
    Devuelve (repos_out, use_rows, git)."""
    git = _insumo(insumos, "git", {})
    use_rows = uses_rows(_insumo(insumos, "sessions", []))
    adoption = extract_adoption(_commit_rows(git), use_rows, repos=fleet)
    dose_current = _current_dose(insumos, git, use_rows, fleet)
    control = _control_flags(git, _insumo(insumos, "topics", {}), fleet)
    return _repos_out(fleet, adoption, control, dose_current), use_rows, git


def _monthly_panel(insumos, fleet, repos_section):
    """Sección monthly[]: panel repo × mes (D2).

    repos_section: (repos_out, use_rows, git) de _repos_section.
    Devuelve (panel, ctx).
    """
    repos_out, use_rows, git = repos_section
    sessions = _insumo(insumos, "sessions", [])
    buckets = events_by_repo_month(sessions)
    sessions_per_month = _sessions_per_month(sessions)
    usage = _insumo(insumos, "usage_report", {})
    first_use = _first_use_index(repos_out, use_rows)
    activity = _activity_months(buckets, sessions_per_month, usage, fleet)
    ctx = {"usage": usage, "sessions": sessions,
           "buckets": buckets, "sessions_per_month": sessions_per_month,
           "first_use": first_use, "git": git,
           "gh": _insumo(insumos, "gh", {}), "ah": _insumo(insumos, "ah", {}),
           "clamped_total": 0}
    return build_panel(_panel_cells(activity, ctx)), ctx


def assemble_report(insumos):
    """Ensambla el reporte completo (D2) desde insumos extraídos — puro,
    testeable con sintéticos. Insumos: usage_report, sessions (walk), git
    {repo: {commits, releases, has_ddl_now}}, gh {repo: {runs, provenance}},
    topics {repo: {topic_ddl}}, ddl_status, terapia, ah {repo: entry}, now."""
    fleet = FLEET_REPOS
    repos_section = _repos_section(insumos, fleet)
    panel, ctx = _monthly_panel(insumos, fleet, repos_section)
    repos_out = repos_section[0]
    rows_by_repo = _rows_by_repo(panel)
    return {
        "metadata": _metadata(insumos.get("now"), fleet, insumos, ctx),
        "repos": repos_out,
        "monthly": panel,
        "analysis": {
            "its": _its_section(repos_out, rows_by_repo),
            "did": _did_section(repos_out, rows_by_repo),
            "dose_response": _dose_response_section(repos_out, rows_by_repo),
        },
    }
