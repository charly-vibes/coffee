"""Emisores por (repo, mes) y celda del panel (ddl-effect-tracker)."""
from __future__ import annotations

from ddl_effect_adoption import compute_dose
from ddl_effect_metrics import (ci_fail_rate, classify_control, commit_latency,
                                git_proxies, recovery_metrics)
from ddl_effect_panel import cell_from_emitters
from ddl_effect_sessions import monthly_dose

# ---------------------------------------------------------------------------
# Ensamblado (puro: consume insumos ya extraídos)
# ---------------------------------------------------------------------------

# Métricas del panel que alimentan ITS/DiD/dosis-respuesta (D5)
ANALYSIS_METRICS = (
    "errors_api_per_100", "recovery_p50_interactions",
    "session_commit_latency_p50_h", "ci_fail_rate",
    "revert_ratio", "fix_ratio", "test_commit_ratio",
)


def _usage_emitter(usage_report, repo, month, n_sessions):
    """Emisor usage (D1 fuente 1): interactions de project_monthly
    (reported), sessions del walk pi (reported, bias pi-only documentado en
    metadata). API errors por repo-mes y terapia por repo-mes no existen:
    missing con razón, nunca 0 fake."""
    proj_rows = (usage_report.get("project_monthly") or {})
    project = next((p for p in proj_rows
                    if p.split("-", 1)[-1] == repo), None)
    interactions = None
    if project and month in proj_rows[project]:
        interactions = proj_rows[project][month].get("interactions")
    return {
        "sessions": n_sessions,
        "interactions": interactions,
        "errors_api_per_100": None,
        "errors_terapia_per_100": None,
        "provenance": {
            "sessions": "reported" if n_sessions else "missing",
            "interactions": "reported" if interactions is not None
                            else "missing",
            "errors_api_per_100": "missing",
            "errors_terapia_per_100": "missing",
        },
        "reason": {
            "errors_api_per_100": "el reporte de usage no desglosa errores "
                                  "API por repo-mes",
            "errors_terapia_per_100": "terapia es una query global (sin "
                                      "filtro por proyecto); desglose en "
                                      "metadata",
            "interactions": None if interactions is not None
                            else "sin interacciones in-scope del proyecto",
        },
    }


def _recovery_emitter(events):
    """recovery_metrics con provenance honesto: p50 None (sin errores) es
    missing, no derived — None ⇔ missing en build_panel."""
    m = recovery_metrics([events])
    return {
        "recovery_p50_interactions": m["recovery_p50_interactions"],
        "recovery_censored_rate": m["recovery_censored_rate"],
        "recovery_n": m["recovery_n"],
        "provenance": {
            "recovery_p50_interactions": ("derived"
                                          if m["recovery_p50_interactions"]
                                          is not None else "missing"),
            "recovery_censored_rate": "derived",
            "recovery_n": "derived",
        },
    }


def _latency_emitter(anchor_ts, commits):
    m = commit_latency(anchor_ts, commits)
    return {
        "session_commit_latency_p50_h": m["session_commit_latency_p50_h"],
        "latency_n": m["latency_n"],
        "clamped_negative": m["clamped_negative"],
        "provenance": {
            "session_commit_latency_p50_h":
                ("derived" if m["session_commit_latency_p50_h"] is not None
                 else "missing"),
        },
        "reason": {
            "session_commit_latency_p50_h": None
            if m["session_commit_latency_p50_h"] is not None
            else "sin commit posterior al ancla de sesión del mes",
        },
    }


def _canary_paths(git, fleet):
    """Paths bajo `.ddl/` conocidos por git, por repo (fuente derived de dosis)."""
    return {repo: sorted({f for c in (git.get(repo) or {}).get("commits") or []
                          for f in c.get("files") or []
                          if f.startswith(".ddl/")})
            for repo in fleet}


def _session_uses(use_rows):
    """Filas de uso → {repo: set(tools)}."""
    uses = {}
    for row in use_rows:
        uses.setdefault(row["repo"], set()).add(row["tool"])
    return uses


def _current_dose(insumos, git, use_rows, fleet):
    """Dosis corriente: ddl status reported sólo en repos con `.ddl/` hoy;
    si no, canarios git y usos de sesión (derived)."""
    canaries = _canary_paths(git, fleet)
    session_uses = _session_uses(use_rows)
    status = insumos.get("ddl_status")
    ddl_status_per_repo = {}
    if status:
        ddl_status_per_repo = {repo: status for repo in fleet
                               if (git.get(repo) or {}).get("has_ddl_now")}
    return compute_dose(ddl_status_per_repo, canaries, session_uses,
                        repos=fleet)


def _control_flags(git, topics, fleet):
    """Control estructural por repo (sin `.ddl/` y sin topic ddl GitHub)."""
    repos_info = {repo: {"has_ddl": bool((git.get(repo) or {}).get("has_ddl_now")),
                         "topic_ddl": bool((topics.get(repo) or {}).get("topic_ddl"))}
                  for repo in fleet}
    return classify_control(repos_info)


def _repos_out(fleet, adoption, control, dose_current):
    """repos[] del reporte: adopción + dosis corriente + flags control."""
    repos = []
    for repo in fleet:
        adv = adoption[repo]
        repos.append({
            "repo": repo,
            "is_control": control[repo]["is_control"],
            "is_outlier": control[repo]["is_outlier"],
            "adoption": {
                "first_ddl_commit": adv["first_ddl_commit"],
                "tools_first_use": adv["tools_first_use"],
                "provenance": adv["provenance"],
            },
            "dose": dose_current[repo]["dose"],
            "dose_detail": dose_current[repo]["dose_detail"],
            "dose_provenance": dose_current[repo]["provenance"]["dose"],
        })
    return repos


def _sessions_per_month(sessions):
    """Sesiones pi activas por (repo, mes): cuenta 1 por sesión-mes."""
    per_month = {}
    for s in sessions:
        months = {str(ev[3])[:7] for ev in (s.get("events") or []) if ev[3]}
        for m in months:
            per_month[(s["repo"], m)] = per_month.get((s["repo"], m), 0) + 1
    return per_month


def _first_use_index(repos_out, use_rows):
    """Índice first_use para monthly_dose: sesiones + tools_first_use."""
    first_use = {r["repo"]: {} for r in repos_out}
    for row in use_rows:
        first_use[row["repo"]][row["tool"]] = row["date"]
    # extract_adoption también aporta first_use (fuente git-irrelevante acá:
    # sólo sesiones)
    for r in repos_out:
        for t, d in (r["adoption"]["tools_first_use"] or {}).items():
            if d:
                first_use.setdefault(r["repo"], {}).setdefault(t, d)
    return first_use


def _activity_months(buckets, sessions_per_month, usage, fleet):
    """Todo (repo, mes) con señal en buckets, sesiones pi o project_monthly."""
    months = set(buckets) | set(sessions_per_month)
    proj_rows = (usage or {}).get("project_monthly") or {}
    for project, ms in proj_rows.items():
        repo = project.split("-", 1)[-1]
        if repo in fleet:
            for m in ms:
                months.add((repo, m))
    return months


def _anchor_ts(sessions, repo, month):
    """Último timestamp de evento del repo en el mes (ancla de latencia)."""
    return max((ev[3] for s in sessions if s["repo"] == repo
                for ev in (s.get("events") or [])
                if ev[3] and str(ev[3])[:7] == month), default=None)


def _month_git_emitter(git_repo, month):
    """git_proxies limitado al mes (commits y releases)."""
    month_commits = [c for c in git_repo.get("commits") or []
                     if str(c["date"])[:7] == month]
    month_releases = [d for d in git_repo.get("releases") or []
                      if str(d)[:7] == month]
    return git_proxies(month_commits, month_releases)


def _month_ci_emitter(gh_repo, month):
    """ci_fail_rate del mes; runs de cache gh → tag cached (D7)."""
    ci = ci_fail_rate(gh_repo["runs"], month)
    # D7: los runs vienen del cache gh → tag cached, no reported
    # (el reducer no sabe de dónde salieron los runs).
    if gh_repo.get("provenance") == "cached" \
            and ci["provenance"] == "reported":
        ci["provenance"] = "cached"
    return ci


def _month_ah_emitter(entry, month):
    """findings `ah check` si la entrada es del mes; None si no aplica."""
    if entry.get("findings") is None \
            or str(entry.get("fetched_until") or "")[:7] != month:
        return None
    return {"ah_check_findings": entry["findings"],
            "provenance": {"ah_check_findings": entry["provenance"]}}


def _month_cell(repo, month, ctx):
    """1 celda del panel: emisores usage/recovery/latency/git/ci/ah/dose."""
    emitters = {"usage": _usage_emitter(
        ctx["usage"], repo, month,
        ctx["sessions_per_month"].get((repo, month), 0))}
    if ctx["buckets"].get((repo, month)):
        emitters["recovery"] = _recovery_emitter(ctx["buckets"][(repo, month)])
    anchor = _anchor_ts(ctx["sessions"], repo, month)
    git_repo = ctx["git"].get(repo)
    if anchor and git_repo:
        emitters["latency"] = _latency_emitter(
            anchor, git_repo.get("commits") or [])
        ctx["clamped_total"] += emitters["latency"]["clamped_negative"]
    if git_repo:
        emitters["git"] = _month_git_emitter(git_repo, month)
    gh_repo = ctx["gh"].get(repo)
    if gh_repo is not None and gh_repo.get("runs") is not None:
        emitters["ci"] = _month_ci_emitter(gh_repo, month)
    ah_emitter = _month_ah_emitter(ctx["ah"].get(repo) or {}, month)
    if ah_emitter:
        emitters["ah"] = ah_emitter
    emitters["dose"] = monthly_dose(repo, month, ctx["first_use"])
    return cell_from_emitters(repo, month, emitters)
