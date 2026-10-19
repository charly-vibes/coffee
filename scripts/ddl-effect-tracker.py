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


def cwd_to_repo(cwd, alias_map=None):
    """cwd de sesión → repo canónico de la flota, o None.

    1) alias explícito exacto ('org/dir' derivado de la convención de path,
    o dir suelto); 2) convención /para/areas/dev/gh/<org>/<dir> con mapping
    DEFAULT_REPO_DIRS; fuera de la flota → None (nunca fuzzy).
    """
    if not cwd:
        return None
    am = dict(DEFAULT_REPO_DIRS)
    am.update(alias_map or {})
    key = None
    if PATH_MARKER in cwd:
        rest = cwd.split(PATH_MARKER, 1)[1].strip("/").split("/")
        if len(rest) >= 2 and rest[1]:
            key = f"{rest[0]}/{rest[1]}"
        elif rest and rest[0]:
            key = rest[0]
    if key is None:
        return None
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


def parse_pi_session(path, registry=MANAGED_TOOLS):
    """1 archivo JSONL de pi → {cwd, events, uses} o None sin señal.

    events: [(kind, tool, ok, ts)] con kind 'assistant' (marcador para
    recovery) o 'result' (toolResult con isError → ok). uses: {tool: ts}
    del PRIMER uso de cada binario gestionado (via bash, token inicial del
    comando; design D3 fuente 3).
    """
    cwd, events, uses = None, [], {}
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = entry.get("timestamp")
                if entry.get("type") == "session":
                    cwd = (entry.get("cwd") or None) if cwd is None else cwd
                    continue
                if entry.get("type") != "message":
                    continue
                msg = entry.get("message", {}) or {}
                role = msg.get("role")
                if role == "assistant":
                    events.append(("assistant", None, None, ts))
                    for part in (msg.get("content") or msg.get("parts") or []):
                        if not (isinstance(part, dict)
                                and str(part.get("type")) == "toolCall"):
                            continue
                        name = part.get("name") or part.get("toolName")
                        if name in registry and name not in uses:
                            uses[name] = ts
                        if name == "bash":
                            for tok in _bash_tokens(
                                    (part.get("arguments") or {}).get("command")):
                                if tok in registry and tok not in uses:
                                    uses[tok] = ts
                elif role == "toolResult":
                    tool = str(msg.get("toolName") or "tool")
                    ok = not bool(msg.get("isError"))
                    events.append(("result", tool, ok, ts))
    except OSError:
        return None
    if not events and not uses:
        return None
    return {"cwd": cwd, "events": events, "uses": uses}


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
    present, healthy = [], []
    for section in (envelope.get("data") or {}).get("sections") or []:
        for item in section.get("items") or []:
            label = item.get("label")
            level = item.get("level")
            if not label:
                continue
            if level != "error":
                present.append(label)
            if level == "healthy":
                healthy.append(label)
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
    cache = None
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text())
        except (json.JSONDecodeError, OSError):
            cache = None
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
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps({"data": data,
                                          "fetched_until": fetched},
                                         indent=2) + "\n")
    except OSError:
        pass
    return {"data": data, "provenance": "reported", "fetched_until": fetched}


def ah_findings_cached(repos, cache_path, ttl_days=7, now=None, runner=None):
    """findings de `ah check` por repo (D6: findings por corrida, serie desde
    cache data/cache/ah_findings.json). Repos sin setup espectacular →
    missing con razón. runner(repo) → (exit, stdout) inyectable."""
    cache_path = Path(cache_path)
    cache = {}
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text())
        except (json.JSONDecodeError, OSError):
            cache = {}
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
        try:
            _exit, stdout = runner(repo)
            env = json.loads(stdout)
            findings = (env.get("data") or {}).get("findings")
        except Exception:  # noqa: BLE001 — sin setup / stdout no-JSON
            findings = None
        entry = {"fetched_until": now or datetime_now_iso()}
        if findings is not None:
            entry.update({"findings": len(findings), "provenance": "reported"})
        else:
            entry.update({"findings": None, "provenance": "missing",
                          "reason": "sin setup espectacular o salida no-JSON"})
        out[repo] = entry
        cache[repo] = entry
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache, indent=2) + "\n")
    except OSError:
        pass
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


def assemble_report(insumos):
    """Ensambla el reporte completo (D2) desde insumos extraídos — puro,
    testeable con sintéticos. Insumos: usage_report, sessions (walk), git
    {repo: {commits, releases, has_ddl_now}}, gh {repo: {runs, provenance}},
    topics {repo: {topic_ddl}}, ddl_status, terapia, ah {repo: entry}, now."""
    fleet = FLEET_REPOS
    sessions = insumos.get("sessions") or []
    git = insumos.get("git") or {}
    gh = insumos.get("gh") or {}
    topics = insumos.get("topics") or {}
    now = insumos.get("now")

    # --- repos[]: adopción + dosis corriente + control (D3, D6) ---
    commit_rows = [dict(c, repo=repo)
                   for repo, g in git.items()
                   for c in (g.get("commits") or [])]
    use_rows = uses_rows(sessions)
    adoption = extract_adoption(commit_rows, use_rows, repos=fleet)
    canaries = {repo: sorted({f for c in (git.get(repo) or {}).get("commits") or []
                              for f in c.get("files") or []
                              if f.startswith(".ddl/")})
                for repo in fleet}
    session_uses = {}
    for row in use_rows:
        session_uses.setdefault(row["repo"], set()).add(row["tool"])
    status = insumos.get("ddl_status")
    ddl_status_per_repo = {}
    if status:
        ddl_status_per_repo = {repo: status for repo in fleet
                               if (git.get(repo) or {}).get("has_ddl_now")}
    dose_current = compute_dose(ddl_status_per_repo, canaries, session_uses,
                                repos=fleet)
    repos_info = {repo: {"has_ddl": bool((git.get(repo) or {}).get("has_ddl_now")),
                         "topic_ddl": bool((topics.get(repo) or {}).get("topic_ddl"))}
                  for repo in fleet}
    control = classify_control(repos_info)

    repos_out = []
    for repo in fleet:
        adv = adoption[repo]
        repos_out.append({
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

    # --- monthly[]: panel repo × mes (D2) ---
    buckets = events_by_repo_month(sessions)
    sessions_per_month = {}
    for s in sessions:
        months = {str(ev[3])[:7] for ev in (s.get("events") or []) if ev[3]}
        for m in months:
            sessions_per_month[(s["repo"], m)] = \
                sessions_per_month.get((s["repo"], m), 0) + 1
    first_use = {r["repo"]: {} for r in repos_out}
    for row in use_rows:
        first_use[row["repo"]][row["tool"]] = row["date"]
    # extract_adoption también aporta first_use (fuente git-irrelevante acá:
    # sólo sesiones)
    for r in repos_out:
        for t, d in (r["adoption"]["tools_first_use"] or {}).items():
            if d:
                first_use.setdefault(r["repo"], {}).setdefault(t, d)

    activity_months = set(buckets) | set(sessions_per_month)
    usage = insumos.get("usage_report") or {}
    proj_rows = usage.get("project_monthly") or {}
    for project, months in proj_rows.items():
        repo = project.split("-", 1)[-1]
        if repo in fleet:
            for m in months:
                activity_months.add((repo, m))

    cells, clamped_total = [], 0
    ah = insumos.get("ah") or {}
    for repo, month in sorted(activity_months):
        emitters = {"usage": _usage_emitter(
            usage, repo, month, sessions_per_month.get((repo, month), 0))}
        if (repo, month) in buckets and buckets[(repo, month)]:
            emitters["recovery"] = _recovery_emitter(buckets[(repo, month)])
        anchor = max((ev[3] for s in sessions if s["repo"] == repo
                      for ev in (s.get("events") or [])
                      if ev[3] and str(ev[3])[:7] == month), default=None)
        if anchor and git.get(repo):
            emitters["latency"] = _latency_emitter(
                anchor, git[repo].get("commits") or [])
            clamped_total += emitters["latency"]["clamped_negative"]
        if git.get(repo):
            month_commits = [c for c in git[repo].get("commits") or []
                             if str(c["date"])[:7] == month]
            month_releases = [d for d in git[repo].get("releases") or []
                              if str(d)[:7] == month]
            emitters["git"] = git_proxies(month_commits, month_releases)
        if repo in gh and gh[repo].get("runs") is not None:
            emitters["ci"] = ci_fail_rate(gh[repo]["runs"], month)
            # D7: los runs vienen del cache gh → tag cached, no reported
            # (el reducer no sabe de dónde salieron los runs).
            if gh[repo].get("provenance") == "cached" \
                    and emitters["ci"]["provenance"] == "reported":
                emitters["ci"]["provenance"] = "cached"
        entry = ah.get(repo) or {}
        if entry.get("findings") is not None \
                and str(entry.get("fetched_until") or "")[:7] == month:
            emitters["ah"] = {"ah_check_findings": entry["findings"],
                              "provenance": {"ah_check_findings":
                                             entry["provenance"]}}
        emitters["dose"] = monthly_dose(repo, month, first_use)
        cells.append(cell_from_emitters(repo, month, emitters))
    panel = build_panel(cells)

    # --- analysis: ITS / DiD / dosis-respuesta (D5) ---
    rows_by_repo = {}
    for r in panel:
        rows_by_repo.setdefault(r["repo"], []).append(r)
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
    frontiers = sorted(str(e["adoption"]["first_ddl_commit"])[:7]
                       for e in repos_out
                       if e["adoption"]["first_ddl_commit"])
    did = []
    if frontiers:
        frontier = frontiers[0]
        control_repos = {e["repo"] for e in repos_out if e["is_control"]}
        for metric in ANALYSIS_METRICS:
            deltas = repo_deltas(rows_by_repo, metric, frontier)
            res = did_2x2(deltas, control_repos)
            did.append({"metric": metric, "adoption_frontier": frontier,
                        **res})
    dose_response_out = []
    for metric in ANALYSIS_METRICS:
        points = []
        scatter = []
        for entry in repos_out:
            vals = [r[metric] for r in rows_by_repo.get(entry["repo"], [])
                    if r.get(metric) is not None]
            if vals:
                import statistics
                mean = statistics.fmean(vals)
                points.append({"repo": entry["repo"],
                               "dose": entry["dose"],
                               metric: mean})
                scatter.append({"repo": entry["repo"],
                                "dose": entry["dose"], "outcome": mean})
        dose_response_out.append({"metric": metric,
                                  **dose_response(points, metric),
                                  "points": scatter})

    fleet_prov = {}
    for repo in fleet:
        fleet_prov[repo] = {
            "git": repo in git,
            "gh": repo in gh,
            "sessions": sessions_per_month.get((repo, ""), 0)
            or sum(n for (r, _m), n in sessions_per_month.items()
                   if r == repo),
        }
    report = {
        "metadata": {
            "generated_at": now,
            "seed_bootstrap": 42,
            "managed_tools": list(MANAGED_TOOLS),
            "fleet_definition": "repos charly-vibes con actividad en "
                                "sesiones in-scope (walk pi) o interacciones "
                                "en project_monthly",
            "provenance": {
                "fleet": fleet_prov,
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
            "clamped_negative_latencies": clamped_total,
        },
        "repos": repos_out,
        "monthly": panel,
        "analysis": {"its": its, "did": did,
                     "dose_response": dose_response_out},
    }
    return report


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main(argv=None):
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
    report_path = Path(args.usage_report)
    usage_report = json.loads(report_path.read_text())
    config = {}
    if Path(args.config).exists():
        config = json.loads(Path(args.config).read_text())
    ddl_cfg = config.get("ddl_effect") or {}
    alias_map = build_alias_map(ddl_cfg.get("repo_aliases"))
    repo_paths = ddl_cfg.get("repo_paths") or {}
    owners = ddl_cfg.get("repo_owners") or {"*": "charly-vibes"}
    cache_dir = Path(args.cache_dir)

    sessions = walk_pi_sessions(args.sessions_dir, alias_map)
    print(f"ddl-effect: {len(sessions)} sesiones pi de la flota")

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

    def _owner(repo):
        """Slug de GitHub del repo: repo_owners puede traer owner suelto
        ('charly-vibes') o slug completo ('charly-vibes/whisper' cuando el
        repo canónico y el de GitHub no comparten nombre)."""
        val = owners.get(repo) or owners.get("*")
        return val if "/" in val else f"{val}/{repo}"

    token, token_src = gh_token()
    gh_insumos = {}
    for repo in FLEET_REPOS:
        if repo not in git_insumos:  # gh sólo para repos locales (charly-vibes)
            continue
        cache = None
        cache_file = cache_dir / "gh_runs" / f"{repo}.json"
        if cache_file.exists():
            try:
                cache = json.loads(cache_file.read_text())
            except (json.JSONDecodeError, OSError):
                cache = None
        owner_repo = _owner(repo)

        def _fetch(_repo, _since, owner_repo=owner_repo):
            runs = gh_runs_fetch(owner_repo)
            if runs is None:  # red muerta/404: degradar, no romper
                raise RuntimeError("gh api sin red o repo_gone")
            return runs

        merged = gh_runs_incremental(repo, _fetch, cache,
                                     now=datetime_now_iso())
        if merged is None:
            continue
        prov = merged.get("provenance", "reported")
        gh_insumos[repo] = {"runs": merged.get("runs") or [],
                            "provenance": prov}
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(
                {"runs": merged.get("runs") or [],
                 "fetched_until": merged.get("fetched_until")}, indent=2) + "\n")
        except OSError:
            pass

    topics = {}
    topics_cache = cache_dir / "gh_topics.json"
    topics_cache_data = {}
    if topics_cache.exists():
        try:
            topics_cache_data = json.loads(topics_cache.read_text())
        except (json.JSONDecodeError, OSError):
            topics_cache_data = {}
    for repo in FLEET_REPOS:
        if repo not in git_insumos:
            continue
        entry = topics_cache_data.get(repo)
        if _cache_fresh({"fetched_until": (entry or {}).get("fetched_until")},
                        datetime_now_iso(), 7):
            topics[repo] = entry
            continue
        res = gh_topics(_owner(repo))
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
    try:
        topics_cache.parent.mkdir(parents=True, exist_ok=True)
        topics_cache.write_text(json.dumps(topics_cache_data, indent=2) + "\n")
    except OSError:
        pass

    terapia = terapia_errors_global(cache_dir / "terapia_errors.json",
                                    ttl_days=7, now=datetime_now_iso())

    import subprocess

    def _ah_runner(repo):
        cwd = repo_local_path(repo, repo_paths)
        r = subprocess.run(["ah", "check"], cwd=str(cwd),
                           capture_output=True, text=True, timeout=120)
        return r.returncode, r.stdout

    ah = ah_findings_cached([r for r in FLEET_REPOS if r in git_insumos],
                            cache_dir / "ah_findings.json", ttl_days=7,
                            now=datetime_now_iso(), runner=_ah_runner)

    insumos = {
        "usage_report": usage_report,
        "sessions": sessions,
        "git": git_insumos,
        "gh": gh_insumos,
        "topics": topics,
        "ddl_status": ddl_status_global(),
        "terapia": terapia,
        "ah": ah,
        "now": datetime_now_iso(),
    }
    report = assemble_report(insumos)
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
    return 0

    sys.exit(main())
if __name__ == "__main__":
    sys.exit(main())
