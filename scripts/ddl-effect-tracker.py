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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import schema_validate  # noqa: E402

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

DDL_SCHEMA = (Path(__file__).resolve().parent.parent / "specs"
              / "ddl-effect.schema.json")


# ---------------------------------------------------------------------------
# Reducers puros
# ---------------------------------------------------------------------------

def _alias_to_repo(repo_aliases):
    """{repo: [alias]} → {alias: repo} — matching explícito, sin fuzzy."""
    out = {}
    for repo, aliases in (repo_aliases or {}).items():
        for alias in aliases:
            out[alias] = repo
    return out


def _first_ddl_by_repo(commits):
    """Primer commit que toca `.ddl/` por repo (fecha mínima)."""
    first = {}
    for c in commits:
        if not any(".ddl/" in f for f in c["files"]):
            continue
        repo = c["repo"]
        if repo not in first or c["date"] < first[repo]:
            first[repo] = c["date"]
    return first


def _first_use_by_key(uses, registry, alias_to_repo):
    """Primer uso por (repo, tool); usos de binarios no gestionados se ignoran."""
    first = {}
    for u in uses:
        tool = _repo_of_tool(u["tool"], registry)
        if tool is None:
            continue
        repo = alias_to_repo.get(u["repo"], u["repo"])
        key = (repo, tool)
        if key not in first or u["date"] < first[key]:
            first[key] = u["date"]
    return first


def _adoption_row(first_ddl, first_use, repo, registry):
    """Fila de adopción de un repo; sin señal → None con provenance missing."""
    row = {
        "first_ddl_commit": first_ddl.get(repo),
        "tools_first_use": {tool: first_use.get((repo, tool))
                            for tool in registry},
        "provenance": {"first_ddl_commit": "missing",
                       "tools_first_use": "missing"},
    }
    if repo in first_ddl:
        row["provenance"]["first_ddl_commit"] = "reported"
    if any((repo, tool) in first_use for tool in registry):
        row["provenance"]["tools_first_use"] = "reported"
    return row


def extract_adoption(commits, uses, registry=MANAGED_TOOLS, **opts):
    """Línea de adopción por repo: primer commit de `.ddl/` + primer uso por tool.

    commits: [{repo, date, files}]; uses: [{repo, tool, date}] — `repo` de uses
    puede ser un cwd crudo; solo matchea vía repo_aliases explícito (sin fuzzy).
    opts: repos=FLEET_REPOS, repo_aliases=None. Devuelve {repo:
    {first_ddl_commit, tools_first_use, provenance}}; sin señal → None con
    provenance "missing" (nunca fecha imputada).
    """
    alias_to_repo = _alias_to_repo(opts.get("repo_aliases"))
    first_ddl = _first_ddl_by_repo(commits)
    first_use = _first_use_by_key(uses, registry, alias_to_repo)
    return {repo: _adoption_row(first_ddl, first_use, repo, registry)
            for repo in opts.get("repos", FLEET_REPOS)}


def _repo_of_tool(tool, registry):
    """Un uso cuenta solo si el binario es una herramienta gestionada."""
    return tool if tool in registry else None


def _dose_from_status(status, registry):
    """Dosis reported desde `ddl status`: sanas / registry."""
    healthy = [t for t in registry if t in set(status.get("healthy", []))]
    present = [t for t in registry
               if t in set(status.get("present", [])) | set(healthy)]
    detail = {"present": present, "healthy": healthy,
              "missing": [t for t in registry if t not in set(present)]}
    return len(healthy) / len(registry), detail, "reported"


def _dose_from_derived(canary_paths, session_tools, registry):
    """Dosis derived: canarios git, luego usos de sesión; sin señal → missing."""
    detail = {"present": [], "healthy": [], "missing": list(registry)}
    via_canary = sorted({p.split("/")[1] for p in canary_paths
                         if p.startswith(".ddl/") and p.split("/")[1] in registry})
    found = via_canary or sorted(session_tools)
    if not found:
        return 0.0, detail, "missing"
    detail = {"present": found, "healthy": [],
              "missing": [t for t in registry if t not in set(found)]}
    return len(found) / len(registry), detail, "derived"


def _bind_args(args, opts, names, defaults):
    """Liga argumentos posicionales + kwargs a `names` (forma estable con
    ≤4 parámetros formales). Extra posicional → TypeError."""
    if len(args) > len(names):
        raise TypeError(
            f"se esperaban a lo sumo {len(names)} argumentos posicionales")
    bound = dict(zip(names, args))
    for name in names[len(args):]:
        bound[name] = opts.get(name, defaults.get(name))
    return bound


def compute_dose(*args, **opts):
    """Dosis por repo: fracción de `registry` presentes y sanas.

    Posicionales: ddl_status, canaries, session_uses; kwargs: registry
    (MANAGED_TOOLS), repos (FLEET_REPOS). Jerarquía: ddl_status (reported) →
    canarios git (derived) → usos de sesión (derived) → 0.0 con provenance
    "missing". ddl_status: {repo: {present, healthy}} (o None); canaries:
    {repo: [paths bajo .ddl/]}; session_uses: {repo: set(tools)}.
    """
    b = _bind_args(args, opts,
                   ("ddl_status", "canaries", "session_uses", "registry",
                    "repos"),
                   {"registry": MANAGED_TOOLS, "repos": FLEET_REPOS})
    out = {}
    for repo in b["repos"]:
        status = (b["ddl_status"] or {}).get(repo)
        if status:
            dose, detail, prov = _dose_from_status(status, b["registry"])
        else:
            dose, detail, prov = _dose_from_derived(
                (b["canaries"] or {}).get(repo) or [],
                (b["session_uses"] or {}).get(repo) or set(), b["registry"])
        out[repo] = {"dose": dose, "dose_detail": detail,
                     "provenance": {"dose": prov}}
    return out


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


# ---------------------------------------------------------------------------
# Panel repo × mes (design D2)
# ---------------------------------------------------------------------------

# Campos de métrica admitidos en una fila de monthly[] (D2 + n visibles de
# recovery y CI — spec 'recuperación con censura visible' y denominadores).
PANEL_METRICS = (
    "sessions", "interactions",
    "errors_api_per_100", "errors_terapia_per_100",
    "recovery_p50_interactions", "recovery_censored_rate", "recovery_n",
    "session_commit_latency_p50_h",
    "ci_fail_rate", "runs_total", "runs_failed",
    "revert_ratio", "fix_ratio", "test_commit_ratio",
    "ah_check_findings", "dose",
)

# Claves de emisor que no son métricas del panel: provenance y audit metadata
# (latency_n/clamped_negative de commit_latency, dose_detail es per-repo,
# no per-mes).
_PANEL_META_KEYS = frozenset(
    {"provenance", "reason", "dose_detail", "latency_n", "clamped_negative"})

_PANEL_TAGS = frozenset({"reported", "derived", "cached", "missing"})


def _field_tag(prov, field):
    """Tag de provenance: {campo: tag} o tag escalar del emisor."""
    return prov.get(field) if isinstance(prov, dict) else prov


def _collect_reason(reason, field, reasons):
    """Reason: {campo: texto} (si el campo tiene razón) o texto del emisor."""
    if reason is None:
        return
    if isinstance(reason, dict):
        if field in reason:
            reasons[field] = reason[field]
    else:
        reasons[field] = reason


def _merge_emitter(name, em, acc):
    """Valida y mezcla un emisor en la celda; duplicados/campos unknown fallan.

    acc: {"repo", "month", "metrics", "src", "reasons"} acumuladores de celda.
    """
    repo, month = acc["repo"], acc["month"]
    metrics, src, reasons = acc["metrics"], acc["src"], acc["reasons"]
    prov = em.get("provenance")
    reason = em.get("reason")
    for field in (k for k in em if k not in _PANEL_META_KEYS):
        if field not in PANEL_METRICS:
            raise ValueError(
                f"{repo} {month}: campo '{field}' del emisor '{name}' no "
                "está en PANEL_METRICS")
        if field in metrics:
            raise ValueError(
                f"{repo} {month}: campo '{field}' emitido por más de un "
                "emisor")
        tag = _field_tag(prov, field)
        metrics[field] = None if tag == "missing" else em[field]
        src[field] = tag
        _collect_reason(reason, field, reasons)


def cell_from_emitters(repo, month, emitters):
    """Normaliza los emisores de un (repo, mes) a una celda del panel.

    emitters: {nombre: dict de reducer} — cada dict tiene métricas planas +
    'provenance' ({campo: tag} o tag escalar que aplica a todas las métricas
    del emisor) y opcionalmente 'reason' ({campo: texto} o texto). El tag
    'missing' normaliza el valor a None (nunca 0 fake, p.ej. dose 0.0 sin
    señal). Campos fuera de PANEL_METRICS o duplicados entre emisores fallan
    loud: el schema (coffe-nea) no puede derivar de un panel que mintió.
    """
    metrics, src, reasons = {}, {}, {}
    acc = {"repo": repo, "month": month, "metrics": metrics,
           "src": src, "reasons": reasons}
    for name, em in (emitters or {}).items():
        _merge_emitter(name, em, acc)
    out = {"repo": repo, "month": month}
    out.update(metrics)
    out["src"] = src
    if reasons:
        out["reasons"] = reasons
    return out


def _validate_cell_metrics(cell, metrics, src):
    """Invariantes loud de provenance: tag válido y None ⇔ 'missing'."""
    for field, value in metrics.items():
        tag = src.get(field)
        if tag not in _PANEL_TAGS:
            raise ValueError(
                f"{cell['repo']} {cell['month']}: '{field}' sin tag de "
                f"provenance válido (tiene {tag!r})")
        if (value is None) != (tag == "missing"):
            raise ValueError(
                f"{cell['repo']} {cell['month']}: '{field}' valor {value!r} "
                f"inconsistente con tag {tag!r} (None ⇔ missing)")


def build_panel(cells):
    """Ensambla monthly[] (design D2): filas repo × mes con provenance en toda
    cifra. Invariantes loud: toda métrica lleva tag de _PANEL_TAGS; None ⇔ tag
    'missing' (null con tag real o valor con tag missing mintirían el origen).
    Meses sin señal (todas las métricas None) quedan afuera del array — no
    nulls-fantasma. Salida ordenada por (repo, month), reproducible.
    """
    rows, seen = [], set()
    for cell in cells:
        metrics = {k: cell[k] for k in PANEL_METRICS if k in cell}
        src = cell.get("src", {})
        _validate_cell_metrics(cell, metrics, src)
        if all(v is None for v in metrics.values()):
            continue  # mes sin señal: afuera del array
        key = (cell["repo"], cell["month"])
        if key in seen:
            raise ValueError(
                f"{cell['repo']} {cell['month']}: celda duplicada — el panel "
                "es repo × mes")
        seen.add(key)
        row = {"repo": cell["repo"], "month": cell["month"]}
        row.update(metrics)
        row["src"] = dict(src)
        if cell.get("reasons"):
            row["reasons"] = dict(cell["reasons"])
        rows.append(row)
    rows.sort(key=lambda r: (r["repo"], r["month"]))
    return rows


def _boot_mean_deltas(pre_ma, post_ma, seed, n_boot):
    """IC bootstrap de la diferencia de medias móviles (seed fija)."""
    import random
    import statistics
    rng = random.Random(seed)
    deltas = []
    for _ in range(n_boot):
        bp = [pre_ma[rng.randrange(len(pre_ma))] for _ in pre_ma]
        bq = [post_ma[rng.randrange(len(post_ma))] for _ in post_ma]
        deltas.append(statistics.fmean(bq) - statistics.fmean(bp))
    deltas.sort()
    return deltas[int(0.025 * (n_boot - 1))], deltas[int(0.975 * (n_boot - 1))]


def _moving_avg3(values):
    """Media móvil trimestral (ventana 3)."""
    import statistics
    return [statistics.fmean(values[i:i + 3])
            for i in range(len(values) - 2)]


def its_pre_post(rows, metric, adoption_month, **opts):
    """ITS simple (design D5, 'honestos antes que elegantes'): media móvil
    trimestral pre/post del corte de adopción, delta post−pre e IC 95% por
    bootstrap (10k resamples, seed fija para reproducibilidad).

    rows: filas del panel de UN repo, orden libre (se ordenan por month).
    opts: seed=42, min_pre=3, n_boot=10_000. Ventana pre mínima: min_pre
    meses — menos que eso omite el ITS con 'reason' explícita y delta None,
    nunca delta 0 disfrazado (spec). Meses con la métrica None (missing) no
    cuentan en ninguna ventana.
    """
    import statistics
    min_pre = opts.get("min_pre", 3)
    n_boot = opts.get("n_boot", 10_000)
    seed = opts.get("seed", 42)
    pre, post = _pre_post_series(rows, metric, adoption_month)
    if len(pre) < min_pre:
        return {"delta": None, "n_pre": len(pre), "n_post": len(post),
                "reason": "ventana_pre_insuficiente"}
    if not post:
        return {"delta": None, "n_pre": len(pre), "n_post": 0,
                "reason": "sin_meses_post"}
    pre_ma, post_ma = _moving_avg3(pre), _moving_avg3(post)
    if not pre_ma or not post_ma:
        return {"delta": None, "n_pre": len(pre), "n_post": len(post),
                "reason": "ventana_ma_trimestral_insuficiente"}
    lo, hi = _boot_mean_deltas(pre_ma, post_ma, seed, n_boot)
    return {
        "delta": statistics.fmean(post_ma) - statistics.fmean(pre_ma),
        "pre_mean": statistics.fmean(pre_ma),
        "post_mean": statistics.fmean(post_ma),
        "ci_low": lo, "ci_high": hi,
        "n_pre": len(pre), "n_post": len(post),
    }


def _pre_post_series(rows, metric, adoption_month):
    """Valores de la métrica separados en ventanas pre/post (None excluido)."""
    series = sorted((r for r in rows if r.get(metric) is not None),
                    key=lambda r: r["month"])
    pre = [r[metric] for r in series if r["month"] < adoption_month]
    post = [r[metric] for r in series if r["month"] >= adoption_month]
    return pre, post


def repo_deltas(rows_by_repo, metric, adoption_month):
    """Delta pre→post por repo (insumo de DiD y de la vista pre/post).

    rows_by_repo: {repo: filas del panel}; la métrica None (missing) no
    cuenta en ninguna ventana. Repo sin pre o sin post → delta None (con
    pre/post mean None), nunca 0 disfrazado.
    """
    import statistics
    out = {}
    for repo, rows in rows_by_repo.items():
        pre, post = _pre_post_series(rows, metric, adoption_month)
        out[repo] = {
            "pre_mean": statistics.fmean(pre) if pre else None,
            "post_mean": statistics.fmean(post) if post else None,
            "delta": (statistics.fmean(post) - statistics.fmean(pre)
                      if pre and post else None),
        }
    return out


def _group_deltas(deltas, control_repos):
    """Deltas pre→post por grupo: (tratamiento, control); None excluido."""
    ctrl = set(control_repos)
    dt = [d["delta"] for r, d in deltas.items()
          if r not in ctrl and d["delta"] is not None]
    dc = [d["delta"] for r, d in deltas.items()
          if r in ctrl and d["delta"] is not None]
    return dt, dc


def did_2x2(deltas, control_repos, min_n=3):
    """DiD simple 2 períodos × 2 grupos (design D5): delta pre→post del grupo
    tratamiento menos el delta del control (resta tendencia global). N de cada
    celda visible; regla dura: n < min_n en cualquiera de los dos grupos →
    did None con reason 'n_insuficiente' — el punto no se grafica y el JSON
    lo marca (anti-anécdota).
    """
    import statistics
    dt, dc = _group_deltas(deltas, control_repos)
    out = {
        "n_treatment": len(dt),
        "n_control": len(dc),
        "delta_treatment": statistics.fmean(dt) if dt else None,
        "delta_control": statistics.fmean(dc) if dc else None,
    }
    if len(dt) < min_n or len(dc) < min_n:
        out["did"] = None
        out["reason"] = "n_insuficiente"
        return out
    out["did"] = statistics.fmean(dt) - statistics.fmean(dc)
    return out


def _avg_ranks(values):
    """Rangos de Spearman con empates promediados (estándar)."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _pearson_on_ranks(doses, outcomes):
    """Correlación de Pearson sobre rangos (= Spearman); None sin varianza."""
    import statistics
    mean_x, mean_y = statistics.fmean(doses), statistics.fmean(outcomes)
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(doses, outcomes))
    var_x = sum((x - mean_x) ** 2 for x in doses)
    var_y = sum((y - mean_y) ** 2 for y in outcomes)
    if var_x == 0 or var_y == 0:
        return None
    return cov / (var_x * var_y) ** 0.5


def dose_response(points, metric, min_n=3):
    """Spearman dosis × outcome (design D5): correlación de rangos por métrica,
    con n de la celda visible. Pares con dosis u outcome None no cuentan.
    Regla dura: n < min_n → rho None con reason 'n_insuficiente'. Dosis sin
    varianza (toda la flota ~1, design Riesgos) → rho None con reason
    'sin_varianza' — degenerada declarada, nunca correlación fake.
    """
    pairs = [(p["dose"], p[metric]) for p in points
             if p.get("dose") is not None and p.get(metric) is not None]
    n = len(pairs)
    if n < min_n:
        return {"spearman_rho": None, "n": n, "reason": "n_insuficiente"}
    doses = _avg_ranks([d for d, _ in pairs])
    outcomes = _avg_ranks([v for _, v in pairs])
    rho = _pearson_on_ranks(doses, outcomes)
    if rho is None:
        return {"spearman_rho": None, "n": n, "reason": "sin_varianza"}
    return {"spearman_rho": rho, "n": n}


def validate_report(report):
    """Valida contra specs/ddl-effect.schema.json (coffe-nea); lista de
    errores ('$ruta: mensaje') — vacía = válido. Stdlib-only vía
    scripts/schema_validate.py (el nightly no garantiza jsonschema).
    """
    schema = json.loads(DDL_SCHEMA.read_text())
    return schema_validate.validate_against_schema(report, schema)


validate_against_schema = schema_validate.validate_against_schema


def _boot_group_deltas(dt, dc, seed, n_boot):
    """Distribución bootstrap de la diferencia de deltas por grupo."""
    import random
    import statistics
    rng = random.Random(seed)
    boots = []
    for _ in range(n_boot):
        bt = [dt[rng.randrange(len(dt))] for _ in dt]
        bc = [dc[rng.randrange(len(dc))] for _ in dc]
        boots.append(statistics.fmean(bt) - statistics.fmean(bc))
    boots.sort()
    return boots


def did_bootstrap(deltas, control_repos, **opts):
    """DiD 2×2 con IC 95% bootstrap (ticket coffe-470): punto idéntico a
    did_2x2; el IC re-muestrea los deltas por repo con reemplazo dentro de
cada grupo (10k resamples, seed fija). Con n ~3 por grupo el IC es grueso —
es la honestidad del diseño (D5), no un bug. Regla dura n < min_n → None con
    reason 'n_insuficiente', nunca IC inventado. opts: seed=42, min_n=3,
    n_boot=10_000.
    """
    import statistics
    seed = opts.get("seed", 42)
    min_n = opts.get("min_n", 3)
    n_boot = opts.get("n_boot", 10_000)
    dt, dc = _group_deltas(deltas, control_repos)
    if len(dt) < min_n or len(dc) < min_n:
        return {"did": None, "ci_low": None, "ci_high": None,
                "delta_treatment": (statistics.fmean(dt) if dt else None),
                "delta_control": (statistics.fmean(dc) if dc else None),
                "n_treatment": len(dt), "n_control": len(dc),
                "reason": "n_insuficiente"}
    boots = _boot_group_deltas(dt, dc, seed, n_boot)
    return {
        "did": statistics.fmean(dt) - statistics.fmean(dc),
        "ci_low": boots[int(0.025 * (n_boot - 1))],
        "ci_high": boots[int(0.975 * (n_boot - 1))],
        "delta_treatment": statistics.fmean(dt),
        "delta_control": statistics.fmean(dc),
        "n_treatment": len(dt), "n_control": len(dc),
    }


# ---------------------------------------------------------------------------
# Extracción (capa de I/O con dependencias inyectables; coffe-q9z)
# ---------------------------------------------------------------------------

# Dir local → repo canónico (cuando el nombre del dir no es el repo):
# ah vive en espectacular/, turu en whisper/ (workspace con 3 bins),
# coffee se llamaba coffe antes del rename.
DEFAULT_REPO_DIRS = {"espectacular": "ah", "whisper": "turu",
                     "coffe": "coffee"}
FLEET_GH_ROOT = Path.home() / "para" / "areas" / "dev" / "gh"
PATH_MARKER = "/para/areas/dev/gh/"


def build_alias_map(repo_aliases):
    """config ddl_effect.repo_aliases: {repo: [alias]} → {alias: repo}.
    Los alias son explícitos (design Riesgos): sin fuzzy matching. Forma de
    alias: 'org/dir' (la convención de path de la flota) o dir suelto."""
    out = {}
    for repo, aliases in (repo_aliases or {}).items():
        for a in aliases or []:
            out[a] = repo
    return out


def _cwd_path_key(cwd):
    """cwd → clave de mapeo según la convención /para/areas/dev/gh/<org>/<dir>.

    'org/dir' si hay ≥2 segmentos, dir suelto si hay 1; None fuera de la
    convención.
    """
    if PATH_MARKER not in cwd:
        return None
    rest = cwd.split(PATH_MARKER, 1)[1].strip("/").split("/")
    if len(rest) >= 2 and rest[1]:
        return f"{rest[0]}/{rest[1]}"
    if rest and rest[0]:
        return rest[0]
    return None


def cwd_to_repo(cwd, alias_map=None):
    """cwd de sesión → repo canónico de la flota, o None.

    1) alias explícito exacto ('org/dir' derivado de la convención de path,
    o dir suelto); 2) convención /para/areas/dev/gh/<org>/<dir> con mapping
    DEFAULT_REPO_DIRS; fuera de la flota → None (nunca fuzzy).
    """
    if not cwd:
        return None
    key = _cwd_path_key(cwd)
    if key is None:
        return None
    am = dict(DEFAULT_REPO_DIRS)
    am.update(alias_map or {})
    if key in am:
        return am[key]
    dir_name = key.split("/", 1)[1] if "/" in key else key
    if dir_name in am:
        return am[dir_name]
    return dir_name if dir_name in FLEET_REPOS else None


def _bash_tokens(command):
    """Primer token de cada segmento del comando (cd /x && bd list →
    ['cd', 'bd']) — suficiente para detectar binarios gestionados."""
    tokens = []
    for seg in str(command or "").replace("&&", "\n").replace(";", "\n").split("\n"):
        parts = seg.strip().split()
        if parts:
            tokens.append(parts[0])
    return tokens


def _collect_uses_from_call(part, ts, state, registry):
    """1 toolCall → uso directo del binario + usos via bash (primer token)."""
    name = part.get("name") or part.get("toolName")
    if name in registry and name not in state["uses"]:
        state["uses"][name] = ts
    if name == "bash":
        for tok in _bash_tokens((part.get("arguments") or {}).get("command")):
            if tok in registry and tok not in state["uses"]:
                state["uses"][tok] = ts


def _handle_assistant_message(msg, ts, state, registry):
    """Mensaje assistant: marcador de recovery + usos de toolCalls."""
    state["events"].append(("assistant", None, None, ts))
    for part in (msg.get("content") or msg.get("parts") or []):
        if not (isinstance(part, dict)
                and str(part.get("type")) == "toolCall"):
            continue
        _collect_uses_from_call(part, ts, state, registry)


def _handle_message_entry(msg, ts, state, registry):
    """1 mensaje pi → eventos de recovery y usos de binarios gestionados."""
    role = msg.get("role")
    if role == "assistant":
        _handle_assistant_message(msg, ts, state, registry)
    elif role == "toolResult":
        tool = str(msg.get("toolName") or "tool")
        ok = not bool(msg.get("isError"))
        state["events"].append(("result", tool, ok, ts))


def _feed_entry(entry, state, registry):
    """1 entrada JSONL → cwd de sesión o manejo del mensaje."""
    if entry.get("type") == "session":
        if state["cwd"] is None:
            state["cwd"] = entry.get("cwd") or None
        return
    if entry.get("type") != "message":
        return
    _handle_message_entry(entry.get("message", {}) or {},
                          entry.get("timestamp"), state, registry)


def _iter_session_entries(path):
    """JSONL → entradas parseadas; líneas corruptas se omiten."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def parse_pi_session(path, registry=MANAGED_TOOLS):
    """1 archivo JSONL de pi → {cwd, events, uses} o None sin señal.

    events: [(kind, tool, ok, ts)] con kind 'assistant' (marcador para
    recovery) o 'result' (toolResult con isError → ok). uses: {tool: ts}
    del PRIMER uso de cada binario gestionado (via bash, token inicial del
    comando; design D3 fuente 3).
    """
    state = {"cwd": None, "events": [], "uses": {}}
    try:
        for entry in _iter_session_entries(path):
            _feed_entry(entry, state, registry)
    except OSError:
        return None
    if not state["events"] and not state["uses"]:
        return None
    return {"cwd": state["cwd"], "events": state["events"],
            "uses": state["uses"]}


def walk_pi_sessions(sessions_dir=None, alias_map=None, fleet=FLEET_REPOS):
    """Walk de ~/.pi/agent/sessions (design D1, patrón coffe-i31): sólo
    sesiones con cwd mapeable a la flota. Sessions sin eventos ni usos se
    descartan (parse_pi_session ya devuelve None para ellas)."""
    out = []
    sessions_dir = Path(sessions_dir or Path.home() / ".pi" / "agent"
                        / "sessions")
    if not sessions_dir.exists():
        return out
    for sd in sorted(p for p in sessions_dir.iterdir() if p.is_dir()):
        for f in sorted(sd.glob("*.jsonl")):
            parsed = parse_pi_session(f)
            if parsed is None:
                continue
            repo = cwd_to_repo(parsed["cwd"], alias_map)
            if repo not in fleet:
                continue
            parsed["repo"] = repo
            out.append(parsed)
    return out


def uses_rows(sessions):
    """{tool: ts} por sesión → [{repo, tool, date}] del primer uso por
    (repo, tool) — insumo de extract_adoption (fuente 3 de dosis, D3)."""
    first = {}
    for s in sessions:
        for tool, ts in (s.get("uses") or {}).items():
            if ts is None:
                continue
            key = (s["repo"], tool)
            cur = first.get(key)
            if cur is None or _parse_ts(ts) < _parse_ts(cur):
                first[key] = ts
    return [{"repo": r, "tool": t, "date": ts}
            for (r, t), ts in sorted(first.items())]


def events_by_repo_month(sessions):
    """Slice del stream de eventos por (repo, mes) para recovery_metrics:
    cada (sesión, mes) mantiene el orden de eventos; grupos vacíos no
    existen (sin eventos en el mes → sin bucket)."""
    buckets = {}
    for s in sessions:
        by_month = {}
        for ev in s.get("events") or []:
            ts = ev[3]
            if not ts:
                continue
            by_month.setdefault(str(ts)[:7], []).append(ev)
        for month, evs in by_month.items():
            buckets.setdefault((s["repo"], month), []).extend(
                [(e[0], e[1], e[2]) for e in evs])
    return buckets


def monthly_dose(repo, month, first_use, registry=MANAGED_TOOLS):
    """Dosis como serie temporal (D3b): fracción de herramientas gestionadas
    con primer uso ≤ mes, derivada hacia atrás desde tools_first_use. El
    snapshot corriente lo valida compute_dose con ddl status (reported)."""
    fu = (first_use or {}).get(repo) or {}
    n = sum(1 for d in fu.values()
            if d and str(d)[:7] <= month)
    return {"dose": n / len(registry), "provenance": "derived"}


def git_commits(repo_path, run=None):
    """git log de un repo de la flota → [{date, message, files}] o None si
    el repo no es local (sin git). run(cmd, cwd) inyectable para tests."""
    if run is None:
        import subprocess
        def run(cmd, cwd):
            r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                               timeout=120)
            if r.returncode != 0:
                raise RuntimeError(r.stderr.strip() or "git falló")
            return r.stdout
    try:
        out = run(["git", "log", "--all", "--name-only",
                   "--pretty=format:%x1e%H|%cI|%s"], str(repo_path))
    except Exception:  # noqa: BLE001 — repo ausente/dañado → missing
        return None
    commits = []
    for block in out.split("\x1e"):
        lines = [l for l in block.strip("\n").split("\n") if l.strip()]
        if not lines or "|" not in lines[0]:
            continue
        _h, date, message = lines[0].split("|", 2)
        commits.append({"date": date, "message": message, "files": lines[1:]})
    return commits


def git_releases(repo_path, run=None):
    """Fechas de tags = releases (insumo de fix_ratio, D6)."""
    if run is None:
        import subprocess
        def run(cmd, cwd):
            r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                               timeout=30)
            return r.stdout
    try:
        out = run(["git", "for-each-ref", "refs/tags",
                   "--format=%(creatordate:iso-strict)"], str(repo_path))
    except Exception:  # noqa: BLE001
        return None
    return [l.strip() for l in out.splitlines() if l.strip()]


def repo_local_path(repo, repo_paths=None, root=None):
    """Repo canónico → path local (~/para/areas/dev/gh/charly/<dir> con
    DEFAULT_REPO_DIRS); config ddl_effect.repo_paths sobreescribe. None si
    el repo no es local (bd vive upstream, sin checkout)."""
    paths = dict(DEFAULT_REPO_DIRS)
    for repo_key, p in (repo_paths or {}).items():
        paths[repo_key] = p
    if repo in paths:
        cand = Path(paths[repo])
        if not cand.is_absolute():
            cand = (root or FLEET_GH_ROOT) / "charly" / cand
        return cand
    cand = (root or FLEET_GH_ROOT) / "charly" / repo
    return cand


def _ddl_item_status(item, present, healthy):
    """1 item de `ddl status`: label presente salvo level error; healthy aparte."""
    label = item.get("label")
    if not label:
        return
    level = item.get("level")
    if level != "error":
        present.append(label)
    if level == "healthy":
        healthy.append(label)


def _ddl_status_items(envelope):
    """Secciones del envelope `ddl status --json` → (present, healthy).

    'not installed' (level error) no es presente; 'healthy' también es presente.
    """
    present, healthy = [], []
    for section in (envelope.get("data") or {}).get("sections") or []:
        for item in section.get("items") or []:
            _ddl_item_status(item, present, healthy)
    return present, healthy


def ddl_status_global(run=None):
    """`ddl status --json` → {present, healthy} por herramienta (D3 fuente 1).
    'not installed' (level error) no es presente. Binario ausente → None."""
    if run is None:
        import subprocess
        def run(cmd):
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if r.returncode != 0:
                raise RuntimeError(r.stderr.strip() or "ddl status falló")
            return r.stdout
    try:
        envelope = json.loads(run(["ddl", "status", "--json"]))
    except Exception:  # noqa: BLE001
        return None
    present, healthy = _ddl_status_items(envelope)
    return {"present": present, "healthy": healthy}


def gh_runs_fetch(owner_repo, run=None):
    """Runs de GitHub Actions paginados (D4). run(cmd) inyectable. Red
    muerta / repo 404 → None (gh_runs_incremental conserva cache stale)."""
    if run is None:
        import subprocess
        def run(cmd):
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if r.returncode != 0:
                raise RuntimeError(r.stderr.strip() or "gh api falló")
            return r.stdout
    runs = []
    page = 1
    while True:
        try:
            out = run(["gh", "api",
                       f"repos/{owner_repo}/actions/runs?per_page=100&page={page}"])
            data = json.loads(out)
        except Exception:  # noqa: BLE001 — sin red/token/repo_gone
            return None
        batch = data.get("workflow_runs") or []
        runs.extend(batch)
        if len(batch) < 100:
            return runs
        page += 1


def gh_topics(owner_repo, run=None):
    """Topics del repo (insumo de classify_control: topic ddl). None sin red."""
    if run is None:
        import subprocess
        def run(cmd):
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if r.returncode != 0:
                raise RuntimeError(r.stderr.strip() or "gh api falló")
            return r.stdout
    try:
        data = json.loads(run(["gh", "api", f"repos/{owner_repo}"]))
    except Exception:  # noqa: BLE001
        return None
    return {"topics": data.get("topics") or [],
            "created_at": data.get("created_at")}


def _cache_fresh(cache, now, ttl_days):
    fetched = _parse_ts((cache or {}).get("fetched_until"))
    cur = _parse_ts(now)
    return bool(cache and fetched and cur
                and (cur - fetched).days < ttl_days)


def terapia_errors_global(cache_path, ttl_days=7, now=None, run=None):
    """Errores por clase de terapia (cache data/cache/terapia_errors.json,
    TTL semanal — mismo patrón que gh, design D1). Terapia es una query
    GLOBAL (el CLI no filtra por proyecto): su desglose queda en metadata
    como contexto, no en filas por repo-mes (atribución imposible sin
    inventar). Sin binario → missing con razón, cache stale conservado."""
    cache_path = Path(cache_path)
    cache = _load_json_cache(cache_path, None)
    if _cache_fresh(cache, now, ttl_days):
        return {"data": cache["data"], "provenance": "cached",
                "fetched_until": cache["fetched_until"]}
    if run is None:
        import subprocess
        def run(cmd):
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if r.returncode != 0:
                raise RuntimeError(r.stderr.strip() or "terapia falló")
            return r.stdout
    try:
        data = json.loads(run(["terapia", "errors", "--all",
                               "--group-by", "class"]))
    except Exception as e:  # noqa: BLE001
        return {"data": None, "provenance": "missing", "reason": str(e)}
    fetched = now or datetime_now_iso()
    _write_json(cache_path, {"data": data, "fetched_until": fetched})
    return {"data": data, "provenance": "reported", "fetched_until": fetched}


def _load_json_cache(cache_path, default):
    """Cache JSON en disco → objeto; corrupto/ilegible → default."""
    if not cache_path.exists():
        return default
    try:
        return json.loads(cache_path.read_text())
    except (json.JSONDecodeError, OSError):
        return default


def _write_json(path, data):
    """Escritura best-effort de cache JSON (mkdir -p; OSError se ignora)."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n")
    except OSError:
        pass


def _fresh_ah_entry(runner, repo, now):
    """Entrada de un repo recién corrido: findings o missing con razón."""
    try:
        _exit, stdout = runner(repo)
        env = json.loads(stdout)
        findings = (env.get("data") or {}).get("findings")
    except Exception as _err:  # noqa: BLE001 — sin setup / stdout no-JSON
        findings = None
    entry = {"fetched_until": now or datetime_now_iso()}
    if findings is not None:
        entry.update({"findings": len(findings), "provenance": "reported"})
    else:
        entry.update({"findings": None, "provenance": "missing",
                      "reason": "sin setup espectacular o salida no-JSON"})
    return entry


def ah_findings_cached(repos, cache_path, ttl_days=7, **opts):
    """findings de `ah check` por repo (D6: findings por corrida, serie desde
    cache data/cache/ah_findings.json). Repos sin setup espectacular →
    missing con razón. runner(repo) → (exit, stdout) inyectable."""
    now = opts.get("now")
    runner = opts.get("runner")
    cache_path = Path(cache_path)
    cache = _load_json_cache(cache_path, {})
    if runner is None:
        import subprocess
        def runner(repo):
            r = subprocess.run(["ah", "check"], cwd=repo, capture_output=True,
                               text=True, timeout=120)
            return r.returncode, r.stdout
    out = {}
    for repo in repos:
        entry = cache.get(repo)
        if _cache_fresh({"fetched_until": (entry or {}).get("fetched_until")},
                        now, ttl_days):
            out[repo] = dict(entry)
            out[repo]["provenance"] = "cached"
            continue
        out[repo] = cache[repo] = _fresh_ah_entry(runner, repo, now)
    _write_json(cache_path, cache)
    return out


def datetime_now_iso():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def _build_arg_parser():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--output", default="data/ddl_effect.json")
    ap.add_argument("--usage-report", default="data/usage_report_v3.json")
    ap.add_argument("--config", default="config/fpa.json")
    ap.add_argument("--cache-dir", default="data/cache")
    ap.add_argument("--sessions-dir", default=None,
                    help="walk de sesiones pi (default ~/.pi/agent/sessions)")
    ap.add_argument("--validate", metavar="ARCHIVO", default=None,
                    help="valida el JSON dado contra specs/ddl-effect.schema.json "
                         "y sale (0 válido / 1 inválido) sin regenerar")
    return ap


def _validate_file(args):
    """Modo --validate: 0 válido / 1 inválido, sin regenerar."""
    errors = validate_report(json.loads(Path(args.validate).read_text()))
    if errors:
        print("ddl-effect: schema inválido:", file=sys.stderr)
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        return 1
    print(f"ddl-effect: {args.validate} válido contra {DDL_SCHEMA.name}")
    return 0


def _owner_fn(owners):
    """Slug de GitHub por repo: repo_owners trae owner suelto ('charly-vibes')
    o slug completo ('charly-vibes/whisper' cuando el repo canónico y el de
    GitHub no comparten nombre)."""
    def _owner(repo):
        val = owners.get(repo) or owners.get("*")
        return val if "/" in val else f"{val}/{repo}"
    return _owner


def _git_insumos(repo_paths):
    """git de la flota local: commits, releases y presencia de `.ddl/` hoy."""
    git_insumos = {}
    for repo in FLEET_REPOS:
        path = repo_local_path(repo, repo_paths)
        if path is None or not path.exists():
            continue
        commits = git_commits(path)
        if commits is None:
            continue
        git_insumos[repo] = {
            "commits": commits,
            "releases": git_releases(path) or [],
            "has_ddl_now": (path / ".ddl").is_dir(),
        }
    return git_insumos


def _gh_insumos(git_insumos, cache_dir, owner):
    """Runs de gh api por repo local, con cache incremental y write-through."""
    gh_insumos = {}
    for repo in FLEET_REPOS:
        if repo not in git_insumos:  # gh sólo para repos locales (charly-vibes)
            continue
        cache_file = cache_dir / "gh_runs" / f"{repo}.json"
        cache = _load_json_cache(cache_file, None)
        owner_repo = owner(repo)

        def _fetch(_repo, _since, owner_repo=owner_repo):
            runs = gh_runs_fetch(owner_repo)
            if runs is None:  # red muerta/404: degradar, no romper
                raise RuntimeError("gh api sin red o repo_gone")
            return runs

        merged = gh_runs_incremental(repo, _fetch, cache,
                                     now=datetime_now_iso())
        if merged is None:
            continue
        gh_insumos[repo] = {"runs": merged.get("runs") or [],
                            "provenance": merged.get("provenance", "reported")}
        _write_json(cache_file, {"runs": merged.get("runs") or [],
                                 "fetched_until": merged.get("fetched_until")})
    return gh_insumos


def _topics_insumos(git_insumos, cache_dir, owner):
    """Topics ddl por repo local con cache semanal (insumo classify_control)."""
    topics = {}
    topics_cache = cache_dir / "gh_topics.json"
    topics_cache_data = _load_json_cache(topics_cache, {})
    for repo in FLEET_REPOS:
        if repo not in git_insumos:
            continue
        entry = topics_cache_data.get(repo)
        if _cache_fresh({"fetched_until": (entry or {}).get("fetched_until")},
                        datetime_now_iso(), 7):
            topics[repo] = entry
            continue
        res = gh_topics(owner(repo))
        if res is None:
            if entry:
                topics[repo] = {**entry, "provenance": "cached"}
            continue
        entry = {"topic_ddl": "ddl" in (res.get("topics") or []),
                 "provenance": "reported",
                 "created_at": res.get("created_at"),
                 "fetched_until": datetime_now_iso()}
        topics[repo] = entry
        topics_cache_data[repo] = entry
    _write_json(topics_cache, topics_cache_data)
    return topics


def _ah_runner(repo_paths):
    """runner(repo) → (exit, stdout) de `ah check` en el checkout local."""
    import subprocess

    def runner(repo):
        cwd = repo_local_path(repo, repo_paths)
        r = subprocess.run(["ah", "check"], cwd=str(cwd),
                           capture_output=True, text=True, timeout=120)
        return r.returncode, r.stdout

    return runner


def _load_config(args):
    """Config opcional (config/fpa.json) → sección ddl_effect."""
    if not Path(args.config).exists():
        return {}
    config = json.loads(Path(args.config).read_text())
    return config.get("ddl_effect") or {}


def _fleet_insumos(args, ddl_cfg, cache_dir):
    """Sesiones, git, gh, topics, ddl status, terapia y ah → insumos del
    reporte (toda la capa de extracción I/O)."""
    alias_map = build_alias_map(ddl_cfg.get("repo_aliases"))
    repo_paths = ddl_cfg.get("repo_paths") or {}
    sessions = walk_pi_sessions(args.sessions_dir, alias_map)
    print(f"ddl-effect: {len(sessions)} sesiones pi de la flota")
    git_insumos = _git_insumos(repo_paths)
    owner = _owner_fn(ddl_cfg.get("repo_owners") or {"*": "charly-vibes"})
    return {
        "sessions": sessions,
        "git": git_insumos,
        "gh": _gh_insumos(git_insumos, cache_dir, owner),
        "topics": _topics_insumos(git_insumos, cache_dir, owner),
        "ddl_status": ddl_status_global(),
        "terapia": terapia_errors_global(cache_dir / "terapia_errors.json",
                                         ttl_days=7,
                                         now=datetime_now_iso()),
        "ah": ah_findings_cached(
            [r for r in FLEET_REPOS if r in git_insumos],
            cache_dir / "ah_findings.json", ttl_days=7,
            now=datetime_now_iso(), runner=_ah_runner(repo_paths)),
        "now": datetime_now_iso(),
    }


def _write_report(out_path, report, ap):
    """Valida (fail loud), escribe y resume el reporte."""
    errors = validate_report(report)
    if errors:  # fail loud: el tracker no escribe un reporte inválido
        ap.error("reporte inválido contra " + DDL_SCHEMA.name + ": "
                 + "; ".join(errors))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, sort_keys=False) + "\n")
    repos_n = len(report["repos"])
    rows_n = len(report["monthly"])
    print(f"ddl-effect: {out_path} — {repos_n} repos, {rows_n} filas de "
          "panel, "
          f"{len(report['analysis']['its'])} ITS, "
          f"{len(report['analysis']['did'])} DiD")


def main(argv=None):
    ap = _build_arg_parser()
    args = ap.parse_args(argv)

    if args.validate:
        return _validate_file(args)

    out_path = Path(args.output)

    # Guard (patrón usage-tracker): sin insumos locales no hay datos reales.
    if not Path(args.usage_report).exists():
        ap.error(
            f"sin insumo {args.usage_report} — el tracker requiere el reporte de "
            "usage; los logs fuente (~/.pi/agent/sessions, git, gh api) no están "
            "versionados (ver README 'Los JSON en data/ son derivados')"
        )
    usage_report = json.loads(Path(args.usage_report).read_text())
    ddl_cfg = _load_config(args)
    cache_dir = Path(args.cache_dir)

    insumos = _fleet_insumos(args, ddl_cfg, cache_dir)
    insumos["usage_report"] = usage_report
    report = assemble_report(insumos)
    _write_report(out_path, report, ap)
    return 0

if __name__ == "__main__":
    sys.exit(main())
