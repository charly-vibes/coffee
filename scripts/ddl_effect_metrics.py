"""Reducers de recovery, latencia, proxies git, tasas, gh y CI (ddl-effect-tracker)."""
from __future__ import annotations

from ddl_effect_core import DDL_OUTLIER_REPO

def _tick_pending(pending):
    """Avanza 1 assistant los contadores de cada tool con error pendiente."""
    for tool in pending:
        pending[tool] = [c + 1 for c in pending[tool]]


def _result_values(ev, pending):
    """toolResult ok → valores de recuperación cerrados; error → abre contador."""
    _, tool, ok = ev
    if ok:
        return pending.pop(tool, [])
    pending.setdefault(tool, []).append(0)
    return []


def _session_recovery(events):
    """1 sesión → (valores de recuperación, censurados por fin de sesión)."""
    values = []
    pending = {}  # tool -> [assistants_since_error, ...]
    for ev in events:
        if ev[0] == "assistant":
            _tick_pending(pending)
        elif ev[0] == "result":
            values.extend(_result_values(ev, pending))
    censored = sum(len(counts) for counts in pending.values())
    return values, censored


def recovery_metrics(sessions):
    """Recuperación: interacciones (mensajes de assistant) entre un error de tool
    (toolResult isError) y la siguiente invocación exitosa del MISMO tool.

    sessions: [[event, ...]] con event = ("assistant",) | ("result", tool, ok).
    Errores sin éxito posterior del mismo tool quedan censurados por fin de
    sesión: fuera de la mediana, contados en recovery_censored_rate. Cero
    asistentes entre error y éxito cuenta como recuperación 0 (retry inmediato).
    """
    import statistics
    values, censored = [], 0
    for events in sessions:
        vals, cens = _session_recovery(events)
        values.extend(vals)
        censored += cens
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
            "provenance": {"interactions": "missing",
                           "errors_api_per_100": "missing",
                           "errors_terapia_per_100": "missing"},
        }
    return {
        "interactions": interactions,
        "errors_api_per_100": api_errors * 100.0 / interactions,
        "errors_terapia_per_100": {k: v * 100.0 / interactions
                                   for k, v in (terapia or {}).items()},
        "provenance": {"interactions": "reported",
                       "errors_api_per_100": "reported",
                       "errors_terapia_per_100": "reported"},
    }


def gh_token(env=None, gh_auth=None):
    """Credencial gh: GH_TOKEN de env primero, `gh auth token` como fallback.
    Los user-timers del nightly no cargan la auth de la sesión interactiva
    (design Riesgos). Sin credencial → (None, None): el llamador emite
    provenance missing con razón explícita en metadata, nunca 0.
    """
    import os
    env = os.environ if env is None else env
    tok_env = env.get("GH_TOKEN") or env.get("GITHUB_TOKEN")
    if tok_env:
        return tok_env, "GH_TOKEN"
    if gh_auth is None:
        import subprocess
        def gh_auth():
            return subprocess.run(["gh", "auth", "token"], capture_output=True,
                                  text=True, timeout=10).stdout.strip() or None
    try:
        tok_cli = gh_auth()
    except Exception as _err:  # noqa: BLE001 — gh ausente/fallando: (None, None)
        return None, None
    return (tok_cli, "gh auth token") if tok_cli else (None, None)


def _runs_cache_fresh(cache, now, ttl_days):
    """Cache dentro del TTL → copia con provenance 'cached'; si no, None."""
    if not (cache and now):
        return None
    fetched = _parse_ts(cache.get("fetched_until"))
    current = _parse_ts(now)
    if fetched and current and (current - fetched).days < ttl_days:
        out = dict(cache)
        out["provenance"] = "cached"
        return out
    return None


def _merge_runs(cache, fresh, since):
    """Merge dedupe por id del cache stale con los runs frescos."""
    by_id = {r["id"]: r for r in (cache or {}).get("runs", [])}
    for r in fresh:
        by_id[r["id"]] = r
    runs = sorted(by_id.values(), key=lambda r: r["created_at"])
    return {
        "runs": runs,
        "fetched_until": runs[-1]["created_at"] if runs else since,
        "provenance": "reported",
    }


def gh_runs_incremental(repo, fetch_fn, cache=None, **opts):
    """Runs de GitHub Actions por repo con cache incremental (design D4/D8).

    fetch_fn(repo, since_iso|None) -> [run] — inyectable: tests no tocan red.
    cache: {runs, fetched_until} o None; opts: ttl_days=7, now=None. Dentro
    del TTL no hay red (provenance 'cached'); stale → refetch solo runs
    posteriores a fetched_until, merge dedupe por id. Red muerta → cache
    stale conservado con provenance 'missing'+reason; 404 → 'repo_gone'.
    """
    ttl_days = opts.get("ttl_days", 7)
    cached = _runs_cache_fresh(cache, opts.get("now"), ttl_days)
    if cached:
        return cached
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
    return _merge_runs(cache, fresh, since)


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
