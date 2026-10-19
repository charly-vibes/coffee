#!/usr/bin/env python3
"""ddl-effect-tracker — valida si el toolset de dulce-de-leche mejora outcomes (v1).

Lee `data/usage_report_v3.json` (agregados por sesión), walk de
`~/.pi/agent/sessions` (recuperación por evento), git de la flota, gh api
(cache), `ddl status` y cache de terapia → `data/ddl_effect.json`
(schema: `specs/ddl-effect.schema.json`, change add-ddl-effectiveness-analysis).

Reducers puros arriba (sin disk I/O, testeados en tests/test_ddl_effect.py);
la capa de extracción (git/gh/walk) llega en tickets posteriores.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 13 herramientas del registry gestionado (MANAGED-TOOLS en dulce-de-leche/README.md;
# drift-tested allá por tests/tool_registry_drift.rs — no duplicamos el check aquí).
MANAGED_TOOLS = (
    "wai", "testaruda",          # Core
    "turu", "dont", "ah",        # Recommended
    "pretender", "vampiro", "incitaciones", "bd", "openspec", "specodelic",
    "funkalization", "wild",     # Extension
)
assert len(MANAGED_TOOLS) == 13, "registry gestionado incompleto"

# Repos canónicos de la flota que evalúa este análisis.
FLEET_REPOS = (
    "wai", "testaruda", "turu", "dont", "ah", "pretender", "vampiro",
    "incitaciones", "bd", "openspec", "specodelic", "funkalization", "wild",
    "dulce-de-leche", "coffee",
)
DDL_OUTLIER_REPO = "charly-dulce-de-leche"


# ---------------------------------------------------------------------------
# Reducers puros
# ---------------------------------------------------------------------------

def extract_adoption(commits, uses, registry=MANAGED_TOOLS, repos=FLEET_REPOS,
                     repo_aliases=None):
    """Línea de adopción por repo: primer commit de `.ddl/` + primer uso por tool.

    commits: [{repo, date, files}]; uses: [{repo, tool, date}] — `repo` de uses
    puede ser un cwd crudo; solo matchea vía repo_aliases explícito (sin fuzzy).
    Devuelve {repo: {first_ddl_commit, tools_first_use, provenance}}; sin señal →
    None con provenance "missing" (nunca fecha imputada).
    """
    repo_aliases = repo_aliases or {}
    alias_to_repo = {}
    for repo, aliases in repo_aliases.items():
        for alias in aliases:
            alias_to_repo[alias] = repo

    def _repo_of(key):
        return alias_to_repo.get(key, key)

    out = {
        repo: {
            "first_ddl_commit": None,
            "tools_first_use": {tool: None for tool in registry},
            "provenance": {"first_ddl_commit": "missing",
                           "tools_first_use": "missing"},
        }
        for repo in repos
    }
    first_ddl = {}
    first_use = {}
    for c in commits:
        if not any(".ddl/" in f for f in c["files"]):
            continue
        repo = c["repo"]
        if repo not in first_ddl or c["date"] < first_ddl[repo]:
            first_ddl[repo] = c["date"]
    for u in uses:
        tool = _repo_of_tool(u["tool"], registry)
        if tool is None:
            continue
        repo = _repo_of(u["repo"])
        key = (repo, tool)
        if key not in first_use or u["date"] < first_use[key]:
            first_use[key] = u["date"]
    for repo in repos:
        if repo in first_ddl:
            out[repo]["first_ddl_commit"] = first_ddl[repo]
            out[repo]["provenance"]["first_ddl_commit"] = "reported"
        for tool in registry:
            if (repo, tool) in first_use:
                out[repo]["tools_first_use"][tool] = first_use[(repo, tool)]
                out[repo]["provenance"]["tools_first_use"] = "reported"
    return out


def _repo_of_tool(tool, registry):
    """Un uso cuenta solo si el binario es una herramienta gestionada."""
    return tool if tool in registry else None


def compute_dose(ddl_status, canaries, session_uses, registry=MANAGED_TOOLS,
                 repos=FLEET_REPOS):
    """Dosis por repo: fracción de `registry` presentes y sanas.

    Jerarquía: ddl_status (reported) → canarios git (derived) → usos de sesión
    (derived) → 0.0 con provenance "missing". ddl_status: {repo: {present,
    healthy}} (o None); canaries: {repo: [paths bajo .ddl/]}; session_uses:
    {repo: set(tools)}.
    """
    out = {}
    for repo in repos:
        detail = {"present": [], "healthy": [], "missing": list(registry)}
        prov = "missing"
        dose = 0.0
        status = (ddl_status or {}).get(repo)
        if status:
            healthy = [t for t in registry if t in set(status.get("healthy", []))]
            present = [t for t in registry if t in set(status.get("present", [])) | set(healthy)]
            detail = {"present": present, "healthy": healthy,
                      "missing": [t for t in registry if t not in set(present)]}
            dose = len(healthy) / len(registry)
            prov = "reported"
        else:
            canary_paths = (canaries or {}).get(repo) or []
            via_canary = sorted({p.split("/")[1] for p in canary_paths
                                 if p.startswith(".ddl/") and p.split("/")[1] in registry})
            via_session = sorted((session_uses or {}).get(repo) or set())
            found = via_canary or via_session
            if found:
                detail = {"present": found, "healthy": [],
                          "missing": [t for t in registry if t not in set(found)]}
                dose = len(found) / len(registry)
                prov = "derived"
        out[repo] = {"dose": dose, "dose_detail": detail,
                     "provenance": {"dose": prov}}
    return out


def recovery_metrics(sessions):
    """Recuperación: interacciones (mensajes de assistant) entre un error de tool
    (toolResult isError) y la siguiente invocación exitosa del MISMO tool.

    sessions: [[event, ...]] con event = ("assistant",) | ("result", tool, ok).
    Errores sin éxito posterior del mismo tool quedan censurados por fin de
    sesión: fuera de la mediana, contados en recovery_censored_rate. Cero
    asistentes entre error y éxito cuenta como recuperación 0 (retry inmediato).
    """
    import statistics
    values = []
    n_errors = censored = 0
    for events in sessions:
        pending = {}  # tool -> [assistants_since_error, ...]
        for ev in events:
            if ev[0] == "assistant":
              for tool in pending:
                pending[tool] = [c + 1 for c in pending[tool]]
            elif ev[0] == "result":
              _, tool, ok = ev
              if ok:
                values.extend(pending.pop(tool, []))
              else:
                pending.setdefault(tool, []).append(0)
        for counts in pending.values():
            censored += len(counts)
    n_errors = len(values) + censored
    return {
        "recovery_p50_interactions": (statistics.median(values)
                                       if values else None),
        "recovery_censored_rate": (censored / n_errors if n_errors else 0.0),
        "recovery_n": n_errors,
    }


def _parse_ts(ts):
    """ISO-8601 (incl. 'Z') → datetime UTC-aware; None si no parsea."""
    from datetime import datetime
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def commit_latency(session_ts, commits):
    """Latencia sesión→commit: para cada commit posterior al timestamp de
    sesión, horas transcurridas; p50 sobre esos pares (un commit ancla 1 par).

    Bias declarado (design D4): sobrestima cuando hay trabajo manual entre la
    sesión y el commit. Latencias negativas (skew de reloj) se clampean a 0 y
    se cuentan en `clamped_negative` — visibles, nunca descartadas.
    """
    import statistics
    t0 = _parse_ts(session_ts)
    if t0 is None:
        return {"session_commit_latency_p50_h": None, "latency_n": 0,
                "clamped_negative": 0}
    SKEW_WINDOW_H = 1.0  # commit hasta 1h "antes" de la sesión = skew de reloj
    hours, clamped = [], 0
    for c in commits:
        ts = _parse_ts(c["date"])
        if ts is None:
            continue
        if ts < t0:
            neg_h = (ts - t0).total_seconds() / 3600.0
            if neg_h >= -SKEW_WINDOW_H:  # skew: se clampea, no se descarta
                clamped += 1
                hours.append(0.0)
            continue  # commit previo genuino: no es par
        hours.append((ts - t0).total_seconds() / 3600.0)
    return {
        "session_commit_latency_p50_h": (statistics.median(hours)
                                         if hours else None),
        "latency_n": len(hours),
        "clamped_negative": clamped,
    }


def _is_revert(message):
    m = message.strip().lower()
    return m.startswith("revert") or "revert \"" in m


def _is_fix(message):
    return message.strip().lower().startswith("fix")


def _is_test(message, files):
    if message.strip().lower().startswith("test"):
        return True
    return any("test" in f.lower() for f in (files or []))


def git_proxies(commits, releases):
    """Proxies de calidad desde git (design D6): revert_ratio, fix_ratio,
    test_commit_ratio. Denominador 0 → None con provenance "missing"
    (nunca 0 fake). releases: lista de timestamps de release en el período.
    """
    total = len(commits)
    reverts = sum(1 for c in commits if _is_revert(c["message"]))
    fixes = sum(1 for c in commits if _is_fix(c["message"]))
    tests = sum(1 for c in commits if _is_test(c["message"], c.get("files")))
    n_releases = len(releases)

    def _ratio(num, den, key):
        prov = "reported" if den else "missing"
        return (num / den if den else None), prov

    rev_r, rev_p = _ratio(reverts, total, "revert_ratio")
    fix_r, fix_p = _ratio(fixes, n_releases, "fix_ratio")
    tst_r, tst_p = _ratio(tests, total, "test_commit_ratio")
    return {
        "revert_ratio": rev_r,
        "fix_ratio": fix_r,
        "test_commit_ratio": tst_r,
        "provenance": {"revert_ratio": rev_p, "fix_ratio": fix_p,
                       "test_commit_ratio": tst_p},
    }


def error_rates(interactions, api_errors, terapia):
    """Tasas de error por 100 interacciones (design D2/spec): errores de API
    (fuente usage_report) y desglose por clase de terapia. El denominador
    (`interactions`) va siempre en el row — cualquier tasa es recalculable.
    Sin interacciones → null con provenance missing (patrón 'missing').
    """
    if not interactions:
        return {
            "interactions": interactions or 0,
            "errors_api_per_100": None,
            "errors_terapia_per_100": None,
            "provenance": {"errors_api_per_100": "missing",
                           "errors_terapia_per_100": "missing"},
        }
    return {
        "interactions": interactions,
        "errors_api_per_100": api_errors * 100.0 / interactions,
        "errors_terapia_per_100": {k: v * 100.0 / interactions
                                   for k, v in (terapia or {}).items()},
        "provenance": {"errors_api_per_100": "reported",
                       "errors_terapia_per_100": "reported"},
    }


def gh_runs_incremental(repo, fetch_fn, cache, ttl_days=7, now=None):
    """Runs de GitHub Actions por repo con cache incremental (design D4/D8).

    fetch_fn(repo, since_iso|None) -> [run] — inyectable: tests no tocan red.
    cache: {runs, fetched_until} o None. Dentro del TTL no hay red
    (provenance 'cached'); stale → refetch solo runs posteriores a
    fetched_until, merge dedupe por id. Red muerta → cache stale conservado
    con provenance 'missing'+reason; 404 → reason 'repo_gone'.
    """
    if cache and now:
        fetched = _parse_ts(cache.get("fetched_until"))
        current = _parse_ts(now)
        if fetched and current and (current - fetched).days < ttl_days:
            out = dict(cache)
            out["provenance"] = "cached"
            return out
    since = cache.get("fetched_until") if cache else None
    try:
        fresh = fetch_fn(repo, since)
    except Exception as e:  # noqa: BLE001 — red muerta/404/auth: degradar, no romper
        out = {"runs": (cache or {}).get("runs", []),
               "fetched_until": (cache or {}).get("fetched_until"),
               "provenance": "missing"}
        msg = str(e)
        out["reason"] = "repo_gone" if "404" in msg else msg[:120]
        return out
    by_id = {r["id"]: r for r in (cache or {}).get("runs", [])}
    for r in fresh:
        by_id[r["id"]] = r
    runs = sorted(by_id.values(), key=lambda r: r["created_at"])
    return {
        "runs": runs,
        "fetched_until": runs[-1]["created_at"] if runs else since,
        "provenance": "reported",
    }


def ci_fail_rate(runs, month):
    """Runs fallidos / completados del mes, excluyendo dependabot (spec).
    Sin runs completados en el mes → None con provenance missing.
    """
    completed = [r for r in runs
                 if r.get("status") == "completed"
                 and not (r.get("head_branch") or "").startswith("dependabot/")
                 and (r.get("created_at") or "")[:7] == month]
    failures = sum(1 for r in completed if r.get("conclusion") == "failure")
    if not completed:
        return {"ci_fail_rate": None, "runs_total": 0,
                "runs_failed": 0, "provenance": "missing"}
    return {
        "ci_fail_rate": failures / len(completed),
        "runs_total": len(completed),
        "runs_failed": failures,
        "provenance": "reported",
    }


def classify_control(repos_info, outlier=DDL_OUTLIER_REPO):
    """control = ausencia estructural de ddl (sin `.ddl/` y sin topic ddl GitHub).

    NUNCA por dosis 0 (adoptar y migrar a 0 sanas sigue siendo tratamiento).
    repos_info: {repo: {has_ddl, topic_ddl}}; el outlier declarado va marcado.
    """
    out = {}
    for repo, info in repos_info.items():
        is_control = not info["has_ddl"] and not info["topic_ddl"]
        out[repo] = {
            "is_control": is_control,
            "is_outlier": repo == outlier,
        }
    return out


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--output", default="data/ddl_effect.json")
    ap.add_argument("--usage-report", default="data/usage_report_v3.json")
    args = ap.parse_args(argv)
    out_path = Path(args.output)

    # Guard (patrón usage-tracker): sin insumos locales no hay datos reales.
    if not Path(args.usage_report).exists():
        ap.error(
            f"sin insumo {args.usage_report} — el tracker requiere el reporte de "
            "usage; los logs fuente (~/.pi/agent/sessions, git, gh api) no están "
            "versionados (ver README 'Los JSON en data/ son derivados')"
        )

    report = {
        "metadata": {
            "generated_at": None,  # se completa cuando aterrice la capa de extracción
            "seed_bootstrap": 42,
            "managed_tools": list(MANAGED_TOOLS),
            "fleet_definition": "repos charly-vibes con actividad en sesiones in-scope",
        },
        "repos": [],
        "monthly": [],
        "analysis": {"its": [], "did": [], "dose_response": []},
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, sort_keys=False) + "\n")
    print(f"ddl-effect: esqueleto escrito en {out_path} "
          "(reducers en tickets coffe-51p..; extracción gh/git/walk pendiente)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
