"""Núcleo puro de model-cost-estimate-v0: constantes, estadística y render.

Separado del CLI para que el script principal quede como fachada de re-export
(y los tests puedan seguir cargando `model-cost-estimate-v0.py` de pie).
"""

import json
import random
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REPORT = REPO / "data" / "usage_report_v3.json"
CONFIG = REPO / "config" / "fpa.json"

# Fuentes que el ledger factura por token (no están en config→subscriptions hoy).
KNOWN_PAY_PER_TOKEN = {
    "openrouter", "zai", "openai", "omp", "kimi-coding", "moonshot",
    "minimax", "mistral", "copilot", "github-copilot",
    "qwen-token-plan-individual", "recursive", "poolside", "nvidia",
}

# Umbrales: provisionales por diseño (EXCL-003). Se declaran en el reporte.
THRESHOLDS = {
    "provisional": True,
    "min_productive": 10,          # muestra mínima de sesiones con ≥1 unidad
    "max_ci_halfwidth_rel": 0.25,  # IC90 relativo a la mediana
    "stale_export_days": 30,       # export de beads más viejo que el último commit
    "max_window_s": 3600,          # ventana de acreditación por sesión
    "slack_s": 600,                # margen posterior al fin de sesión
    "bootstrap_B": 2000,
    "bootstrap_seed": 7,
}

EXCLUDED_MODELS = {"mixed"}


# --- carriles de coste (CORR-001) -------------------------------------------

def lane_for(source, subscriptions):
    """'notional' si la fuente es suscripción (config), 'real' si paga por token."""
    if source in subscriptions:
        return "notional"
    if source in KNOWN_PAY_PER_TOKEN:
        return "real"
    return "unknown"


# --- asignación de unidades (CORR-002) --------------------------------------

def _best_window(ordered, ts):
    """Ventana contenedora con inicio más cercano (empate → id menor); None si no hay."""
    best = None
    for w in ordered:
        if w["start"] > ts:
            break
        if w["end"] < ts:
            continue
        dist = abs(ts - w["start"])
        if best is None or dist < best[0] or (dist == best[0] and w["id"] < best[1]):
            best = (dist, w["id"])
    return best[1] if best else None


def assign_units(events, windows):
    """events: [(clave, ts)]; windows: [{id,start,end}].

    Cada evento se acredita a UNA sola ventana: la de inicio más cercano entre
    las que lo contienen (empate → id menor). Eventos repetidos cuentan una vez.
    """
    ordered = sorted(windows, key=lambda w: w["start"])
    out = {}
    for key, ts in events:
        out.setdefault(key, _best_window(ordered, ts))
    return out


def bounded_window(start, end, max_span_s):
    """Acota [start, end] a max_span_s desde el inicio; 3er valor = ambigua."""
    if end - start > max_span_s:
        return start, start + max_span_s, True
    return start, end, False


# --- estadística ------------------------------------------------------------

def pctl(values, p):
    """Cuantil con interpolación lineal; None si no hay muestra."""
    vals = sorted(values)
    n = len(vals)
    if not n:
        return None
    k = (n - 1) * p
    f = int(k)
    if f + 1 >= n:
        return float(vals[f])
    return vals[f] + (vals[f + 1] - vals[f]) * (k - f)


def wilson_lower(k, n, z=1.96):
    """Cota inferior del IC95 de una proporción (muestra chica no miente)."""
    if n <= 0:
        return None
    p = k / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return max(0.0, (centre - margin) / denom)


def _bootstrap_quantiles(groups, p, B, seed):
    """Mediana remuestreada B veces por cluster; [] si no alcanza la muestra."""
    keys = list(groups)
    rnd = random.Random(seed)
    meds = []
    for _ in range(B):
        pooled = []
        for _ in range(len(keys)):
            pooled.extend(groups[keys[rnd.randrange(len(keys))]])
        q = pctl(pooled, p)
        if q is not None:
            meds.append(q)
    return meds


def cluster_bootstrap_ci(values, clusters, B=2000, seed=7):
    """IC90 de la mediana remuestreando clusters completos (repo día)."""
    groups = {}
    for v, c in zip(values, clusters):
        groups.setdefault(c, []).append(v)
    if len(groups) < 2:
        return None, None
    meds = _bootstrap_quantiles(groups, 0.5, B, seed)
    if len(meds) < 10:
        return None, None
    return pctl(meds, 0.05), pctl(meds, 0.95)


def is_comparable(n_productive, ci_halfwidth_rel):
    """Comparabilidad derivada de la muestra (EXCL-003), no de un corte inventado."""
    if n_productive < THRESHOLDS["min_productive"]:
        return False, f"n_productivas<{THRESHOLDS['min_productive']}"
    if ci_halfwidth_rel is None:
        return False, "ci_indefinido"
    if ci_halfwidth_rel > THRESHOLDS["max_ci_halfwidth_rel"]:
        return False, f"ci_ancho({ci_halfwidth_rel:.2f})"
    return True, "comparable"


# --- frescura del export de beads (EDGE-001) --------------------------------

def beads_freshness(export_mtime, last_commit_ts, stale_after_days):
    """fresh | stale | no-data | unknown — un export viejo NO es 'sin cierres'."""
    if export_mtime is None:
        return "no-data"
    if last_commit_ts is None:
        return "unknown"
    if export_mtime >= last_commit_ts - stale_after_days * 86400:
        return "fresh"
    return "stale"


# --- render -----------------------------------------------------------------

def _fmt(v, spec="{:.2f}", dash="n/a"):
    return dash if v is None else spec.format(v)


def _header_lines(payload):
    since = payload["window"]["since"] or "inicio del reporte"
    until = payload["window"]["until"] or "fin del reporte"
    return [
        "# Estimador v0 — coste por unidad aceptada (spike coffe-dnx.10)",
        "",
        f"Ventana: {since} → {until} "
        f"(tz {payload['window']['timezone']}) · unidad: `{payload['unit']}` · "
        f"sesiones: {payload['sessions']} · gasto real: ${payload['cost_real_total']}",
        "",
        f"Cobertura: {payload['coverage']['projects_resolved']}/"
        f"{payload['coverage']['projects_total']} proyectos, "
        f"{payload['coverage']['spend_resolved_pct']}% del gasto. "
        f"Créditos: {payload['events']['credited']}/{payload['events']['total']} eventos "
        f"({payload['events']['distinct_units']} unidades distintas). "
        f"Regla: {payload['dedupe_rule']}. Base: {payload['cost_basis']}.",
        "",
        "Carril `real` = pay-per-token (dinero del ledger). Carril `notional` = "
        "suscripción: `cost` es el dinero real desembolsado, `imputado` el valor "
        "equivalente por token. **No se comparan entre sí.**",
        "",
    ]


def _verdict_lines(payload):
    comparable = [r for r in payload["rows"] if r["comparable"]]
    if not comparable:
        return []
    lines = ["## Veredicto (solo filas comparables)", ""]
    for r in comparable:
        lines.append(f"- **{r['model']}** ({r['lane']}): "
                     f"${_fmt(r['p50_unit_cost'])}/unidad "
                     f"(estricto ${_fmt(r['p50_unit_cost_strict'])}, n={r['n_strict']}; "
                     f"IC90 {_fmt(r['ci90'][0])}–{_fmt(r['ci90'][1])}), "
                     f"yield LB95 {_fmt(r['yield_lb'], '{:.0%}')}, "
                     f"n={r['n_sessions']}.")
    lines.append("")
    return lines


def _tail_ratio(r):
    if r["p50_session_cost"] and r["p99_session_cost"]:
        return r["p99_session_cost"] / r["p50_session_cost"]
    return None


def _table_row(r):
    ic = "n/a" if r["ci90"][0] is None else f"{r['ci90'][0]:.2f}–{r['ci90'][1]:.2f}"
    tail = _tail_ratio(r)
    return (
        f"| `{r['model']}` | {r['lane']} | {r['n_sessions']} | "
        f"{_fmt(r['yield_lb'], '{:.0%}')} | {_fmt(r['p50_unit_cost'])} | "
        f"{_fmt(r['p50_unit_cost_strict'])} ({r['n_strict']}) | {ic} | "
        f"{r['n_productive']} | {_fmt(tail, '{:.1f}×')} | "
        f"{r['n_ambiguous']} ({_fmt(r['units_ambiguous_share'], '{:.0%}')} de las u.) | "
        f"{'sí' if r['comparable'] else r['comparable_reason']} |"
    )


_TABLE_HEADER = (
    "## Tabla completa (n ≥ 10; el resto queda en el JSON)",
    "",
    "| modelo | carril | n | yield (LB95) | p50 $/unidad | estricto (n) | IC90 | n prod | "
    "p99/p50 | ambigua (unidades) | ¿comparable? |",
    "|---|---|---|---|---|---|---|---|---|---|---|",
)


def _table_lines(payload):
    shown = [r for r in payload["rows"] if r["n_sessions"] >= 10]
    lines = list(_TABLE_HEADER) + [_table_row(r) for r in shown]
    omitted = len(payload["rows"]) - len(shown)
    if omitted:
        lines += ["", f"{omitted} modelos con n<10 omitidos de la tabla (ver JSON)."]
    return lines


def _note_lines(payload):
    lines = []
    un = payload.get("unattributed")
    if un:
        lines += ["", f"Sin atribuir (proyecto sin repo local): {un['n_sessions']} sesiones, "
                      f"${un['cost']:.2f}."]
    ex = payload["excluded"]["mixed"]
    if ex["n_sessions"]:
        lines += [f"Excluido `mixed` (sesiones con varios modelos): {ex['n_sessions']} "
                  f"sesiones, ${ex['cost']:.2f}."]
    if payload["stale_beads_repos"]:
        lines += ["", "Repos con export de beads no fiable (excluidos): "
                  + ", ".join(f"`{s['project']}` ({s['freshness']})"
                              for s in payload["stale_beads_repos"])]
    return lines


def render_markdown(payload):
    lines = _header_lines(payload)
    lines += _verdict_lines(payload)
    lines += _table_lines(payload)
    lines += _note_lines(payload)
    lines += ["", f"Umbrales provisionales: `{json.dumps(payload['thresholds'])}`", ""]
    return "\n".join(lines)
