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
    except Exception:  # noqa: BLE001 — gh ausente/fallando: (None, None)
        return None, None
    return (tok_cli, "gh auth token") if tok_cli else (None, None)


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
    for name, em in (emitters or {}).items():
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
            tag = prov.get(field) if isinstance(prov, dict) else prov
            if tag == "missing":
                metrics[field] = None
            else:
                metrics[field] = em[field]
            src[field] = tag
            if reason is not None:
                if isinstance(reason, dict):
                    if field in reason:
                        reasons[field] = reason[field]
                else:
                    reasons[field] = reason
    out = {"repo": repo, "month": month}
    out.update(metrics)
    out["src"] = src
    if reasons:
        out["reasons"] = reasons
    return out


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


def its_pre_post(rows, metric, adoption_month, seed=42, min_pre=3,
                 n_boot=10_000):
    """ITS simple (design D5, 'honestos antes que elegantes'): media móvil
    trimestral pre/post del corte de adopción, delta post−pre e IC 95% por
    bootstrap (10k resamples, seed fija para reproducibilidad).

    rows: filas del panel de UN repo, orden libre (se ordenan por month).
    Ventana pre mínima: min_pre meses — menos que eso omite el ITS con
    'reason' explícita y delta None, nunca delta 0 disfrazado (spec). Meses
    con la métrica None (missing) no cuentan en ninguna ventana.
    """
    import random
    import statistics
    series = sorted((r for r in rows if r.get(metric) is not None),
                    key=lambda r: r["month"])
    pre = [r[metric] for r in series if r["month"] < adoption_month]
    post = [r[metric] for r in series if r["month"] >= adoption_month]
    if len(pre) < min_pre:
        return {"delta": None, "n_pre": len(pre), "n_post": len(post),
                "reason": "ventana_pre_insuficiente"}
    if not post:
        return {"delta": None, "n_pre": len(pre), "n_post": 0,
                "reason": "sin_meses_post"}

    def _ma3(values):
        return [statistics.fmean(values[i:i + 3])
                for i in range(len(values) - 2)]

    pre_ma, post_ma = _ma3(pre), _ma3(post)
    if not pre_ma or not post_ma:
        return {"delta": None, "n_pre": len(pre), "n_post": len(post),
                "reason": "ventana_ma_trimestral_insuficiente"}
    delta = statistics.fmean(post_ma) - statistics.fmean(pre_ma)
    rng = random.Random(seed)
    deltas = []
    for _ in range(n_boot):
        bp = [pre_ma[rng.randrange(len(pre_ma))] for _ in pre_ma]
        bq = [post_ma[rng.randrange(len(post_ma))] for _ in post_ma]
        deltas.append(statistics.fmean(bq) - statistics.fmean(bp))
    deltas.sort()
    lo = deltas[int(0.025 * (n_boot - 1))]
    hi = deltas[int(0.975 * (n_boot - 1))]
    return {
        "delta": delta,
        "pre_mean": statistics.fmean(pre_ma),
        "post_mean": statistics.fmean(post_ma),
        "ci_low": lo, "ci_high": hi,
        "n_pre": len(pre), "n_post": len(post),
    }


def repo_deltas(rows_by_repo, metric, adoption_month):
    """Delta pre→post por repo (insumo de DiD y de la vista pre/post).

    rows_by_repo: {repo: filas del panel}; la métrica None (missing) no
    cuenta en ninguna ventana. Repo sin pre o sin post → delta None (con
    pre/post mean None), nunca 0 disfrazado.
    """
    import statistics
    out = {}
    for repo, rows in rows_by_repo.items():
        series = sorted((r for r in rows if r.get(metric) is not None),
                        key=lambda r: r["month"])
        pre = [r[metric] for r in series if r["month"] < adoption_month]
        post = [r[metric] for r in series if r["month"] >= adoption_month]
        out[repo] = {
            "pre_mean": statistics.fmean(pre) if pre else None,
            "post_mean": statistics.fmean(post) if post else None,
            "delta": (statistics.fmean(post) - statistics.fmean(pre)
                      if pre and post else None),
        }
    return out


def did_2x2(deltas, control_repos, min_n=3):
    """DiD simple 2 períodos × 2 grupos (design D5): delta pre→post del grupo
    tratamiento menos el delta del control (resta tendencia global). N de cada
    celda visible; regla dura: n < min_n en cualquiera de los dos grupos →
    did None con reason 'n_insuficiente' — el punto no se grafica y el JSON
    lo marca (anti-anécdota).
    """
    import statistics
    ctrl = set(control_repos)
    dt = [d["delta"] for r, d in deltas.items()
          if r not in ctrl and d["delta"] is not None]
    dc = [d["delta"] for r, d in deltas.items()
          if r in ctrl and d["delta"] is not None]
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


def dose_response(points, metric, min_n=3):
    """Spearman dosis × outcome (design D5): correlación de rangos por métrica,
    con n de la celda visible. Pares con dosis u outcome None no cuentan.
    Regla dura: n < min_n → rho None con reason 'n_insuficiente'. Dosis sin
    varianza (toda la flota ~1, design Riesgos) → rho None con reason
    'sin_varianza' — degenerada declarada, nunca correlación fake.
    """
    import statistics
    pairs = [(p["dose"], p[metric]) for p in points
             if p.get("dose") is not None and p.get(metric) is not None]
    n = len(pairs)
    if n < min_n:
        return {"spearman_rho": None, "n": n, "reason": "n_insuficiente"}
    doses = _avg_ranks([d for d, _ in pairs])
    outcomes = _avg_ranks([v for _, v in pairs])
    mean_x, mean_y = statistics.fmean(doses), statistics.fmean(outcomes)
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(doses, outcomes))
    var_x = sum((x - mean_x) ** 2 for x in doses)
    var_y = sum((y - mean_y) ** 2 for y in outcomes)
    if var_x == 0 or var_y == 0:
        return {"spearman_rho": None, "n": n, "reason": "sin_varianza"}
    return {"spearman_rho": cov / (var_x * var_y) ** 0.5, "n": n}


def validate_report(report):
    """Valida contra specs/ddl-effect.schema.json (coffe-nea); lista de
    errores ('$ruta: mensaje') — vacía = válido. Stdlib-only vía
    scripts/schema_validate.py (el nightly no garantiza jsonschema).
    """
    schema = json.loads(DDL_SCHEMA.read_text())
    return schema_validate.validate_against_schema(report, schema)


validate_against_schema = schema_validate.validate_against_schema


def did_bootstrap(deltas, control_repos, seed=42, min_n=3, n_boot=10_000):
    """DiD 2×2 con IC 95% bootstrap (ticket coffe-470): punto idéntico a
    did_2x2; el IC re-muestrea los deltas por repo con reemplazo dentro de
cada grupo (10k resamples, seed fija). Con n ~3 por grupo el IC es grueso —
es la honestidad del diseño (D5), no un bug. Regla dura n < min_n → None con
    reason 'n_insuficiente', nunca IC inventado.
    """
    import random
    import statistics
    ctrl = set(control_repos)
    dt = [d["delta"] for r, d in deltas.items()
          if r not in ctrl and d["delta"] is not None]
    dc = [d["delta"] for r, d in deltas.items()
          if r in ctrl and d["delta"] is not None]
    if len(dt) < min_n or len(dc) < min_n:
        return {"did": None, "ci_low": None, "ci_high": None,
                "delta_treatment": (statistics.fmean(dt) if dt else None),
                "delta_control": (statistics.fmean(dc) if dc else None),
                "n_treatment": len(dt), "n_control": len(dc),
                "reason": "n_insuficiente"}
    rng = random.Random(seed)
    boots = []
    for _ in range(n_boot):
        bt = [dt[rng.randrange(len(dt))] for _ in dt]
        bc = [dc[rng.randrange(len(dc))] for _ in dc]
        boots.append(statistics.fmean(bt) - statistics.fmean(bc))
    boots.sort()
    return {
        "did": statistics.fmean(dt) - statistics.fmean(dc),
        "ci_low": boots[int(0.025 * (n_boot - 1))],
        "ci_high": boots[int(0.975 * (n_boot - 1))],
        "delta_treatment": statistics.fmean(dt),
        "delta_control": statistics.fmean(dc),
        "n_treatment": len(dt), "n_control": len(dc),
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--output", default="data/ddl_effect.json")
    ap.add_argument("--usage-report", default="data/usage_report_v3.json")
    ap.add_argument("--validate", metavar="ARCHIVO", default=None,
                    help="valida el JSON dado contra specs/ddl-effect.schema.json "
                         "y sale (0 válido / 1 inválido) sin regenerar")
    args = ap.parse_args(argv)

    if args.validate:
        errors = validate_report(json.loads(Path(args.validate).read_text()))
        if errors:
            print("ddl-effect: schema inválido:", file=sys.stderr)
            for e in errors:
                print(f"  {e}", file=sys.stderr)
            return 1
        print(f"ddl-effect: {args.validate} válido contra {DDL_SCHEMA.name}")
        return 0

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
    errors = validate_report(report)
    if errors:  # fail loud: el tracker no escribe un reporte inválido
        ap.error("reporte inválido contra " + DDL_SCHEMA.name + ": "
                 + "; ".join(errors))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, sort_keys=False) + "\n")
    print(f"ddl-effect: esqueleto escrito en {out_path} "
          "(reducers en tickets coffe-51p..; extracción gh/git/walk pendiente)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
