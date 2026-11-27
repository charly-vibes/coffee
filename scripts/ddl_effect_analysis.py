"""ITS, DiD, dosis-respuesta y validación contra schema (ddl-effect-tracker)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import schema_validate  # noqa: E402

from ddl_effect_core import DDL_SCHEMA

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
