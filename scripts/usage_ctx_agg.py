#!/usr/bin/env python3
"""aggregate_context + extract_context_metrics (extraído de usage-tracker.py).

Parte de la descomposición de scripts/usage-tracker.py (coffe-kav.1):
cada módulo conserva el código original verbatim; los nombres compartidos
viajan por _link() (mismo patrón que viz_fpa_common) y el estado mutable
por usage_state.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import usage_state

def _pctl(sorted_vals, p):
    """Percentil p (0..1) con interpolación lineal sobre lista YA ordenada
    (stdlib-only, determinista)."""
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    idx = (len(sorted_vals) - 1) * p
    lo = int(idx)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = idx - lo
    return sorted_vals[lo] + frac * (sorted_vals[hi] - sorted_vals[lo])


def _ctx_source_sums(rows, cpt):
    """tokens estimados por fuente (chars/cpt) sumados sobre las filas."""
    by_source = {}
    for r in rows:
        for src, n in r.get("chars_by_source", {}).items():
            by_source[src] = by_source.get(src, 0) + n / cpt
    return by_source


def _ctx_month_spend(rows):
    """spend_* por componente (sumas sobre los turnos con coste)."""
    def s(key):
        return round(sum(r[key] for r in rows
                         if r.get(key) is not None), 2)
    return {"spend_in": s("cost_in"), "spend_out": s("cost_out"),
            "spend_cr": s("cost_cr"), "spend_cw": s("cost_cw")}


def _ctx_month_row(key, rows, th, max_month):
    month, model = key
    cpt = th["chars_per_token"]
    peaks = sorted(r["peak_ctx_est"] for r in rows)
    total_turns = sum(r["turns"] for r in rows)
    by_source = _ctx_source_sums(rows, cpt)
    row = {
        "month": month,
        "model": model,
        "sessions": len(rows),
        "peak_p50": _pctl(peaks, 0.5),
        "peak_p90": _pctl(peaks, 0.9),
        "pct_big_session": round(100.0 * sum(
            1 for r in rows
            if r["peak_ctx_est"] > th["big_session_tokens"]) / len(rows), 2),
        "tokens_per_turn_by_source": {
            src: round(t / total_turns, 2)
            for src, t in sorted(by_source.items())},
        "partial": month == max_month,
    }
    row.update(_ctx_month_spend(rows))
    return row


def _ctx_validation_ratio(clean):
    # ratio de validación: tokens del ÚLTIMO turno con usage / final_ctx_est
    # (el último turno ≈ contexto final real; la suma acumulada de la sesión
    # no es comparable con el contexto de un punto en el tiempo)
    return sorted(
        r["final_turn_tokens"] / r["final_ctx_est"]
        for r in clean
        if r.get("context_source") == "pi" and r.get("final_turn_tokens")
        and r["final_ctx_est"] > 0)


def _ctx_meta(th, ratios, stubs, excluded_pathological):
    return {
        "chars_per_token": th["chars_per_token"],
        "chars_per_token_tag": "assumed",
        "chars_per_token_bias": (
            "subestima el contexto real (~40%): español/código ~3 "
            "chars/token y overhead de serialización; consistente "
            "entre eras — ver ratio de validación"),
        "validation_ratio_median": _pctl(ratios, 0.5) if ratios else None,
        "thresholds": th,
        "stubs": stubs,
        "excluded_pathological": excluded_pathological,
    }


def _ctx_clean(session_rows):
    clean = [r for r in session_rows
             if r.get("turns", 0) > 0 and not r.get("pathological")]
    stubs = sum(1 for r in session_rows if r.get("turns", 0) == 0)
    excluded = sum(1 for r in session_rows if r.get("pathological"))
    return clean, stubs, excluded


def _ctx_groups(clean):
    # agrupar por (mes, modelo dominante)
    groups = {}
    for r in clean:
        groups.setdefault((r.get("month"), r.get("model")), []).append(r)
    max_month = max((r["month"] for r in clean if r.get("month")),
                    default=None)
    return groups, max_month


def aggregate_context(session_rows, config=None):
    """Agregados de contexto por mes × modelo dominante + meta (coffe-2xj,
    tasks 1.6+1.7). Pure: session_rows son las filas que emite
    reduce_context() (+ month + tokens_reported); nada de disco.

    Set limpio: turns > 0 y no pathological — stubs y patológicas quedan
    fuera de los agregados pero contadas en meta (loss visible).
    """
    th = dict(DEFAULT_CONTEXT_THRESHOLDS)
    if config:
        th.update(config)

    clean, stubs, excluded = _ctx_clean(session_rows)
    groups, max_month = _ctx_groups(clean)
    monthly_model = [
        _ctx_month_row(key, rows, th, max_month)
        for key, rows in sorted(groups.items(),
                                key=lambda kv: (str(kv[0][0]), str(kv[0][1])))]

    return {
        "monthly_model": monthly_model,
        "meta": _ctx_meta(th, _ctx_validation_ratio(clean), stubs, excluded),
    }

_CTX_UNAVAILABLE = {
    "available": False, "reason": None,
    "sessions": [], "monthly_model": [], "meta": {},
}


def _ctx_unavailable(reason):
    out = dict(_CTX_UNAVAILABLE)
    out["reason"] = reason
    return out


def _ctx_window_ok(fdate):
    # ventana --since/--until (coffe-snj): solape por fecha del filename
    # (los filenames de sesión de Pi empiezan con la fecha)
    if not fdate:
        return True
    if SINCE and fdate < SINCE.isoformat():
        return False
    return not (UNTIL and fdate > UNTIL.isoformat())


def _ctx_file_entries(f):
    try:
        return [json.loads(line) for line in open(f) if line.strip()]
    except (json.JSONDecodeError, OSError) as e:
        _debug_skip(f"ctx {f.name}: {e}")
        return None


def _ctx_files(sd):
    return [f for f in sorted(sd.glob("*.jsonl"))
            if _ctx_window_ok(f.name[:10])]


def _ctx_project_rows(sd, th):
    """(rows, n_stubs, n_files) de un proyecto (dir de sesiones de Pi)."""
    rows = []
    n_stubs = 0
    n_files = 0
    proj = sd.name
    if not in_scope(proj):
        return rows, n_stubs, n_files
    for f in _ctx_files(sd):
        entries = _ctx_file_entries(f)
        if entries is None:
            continue
        n_files += 1
        row = reduce_context(entries, config=th, month=f.name[:7])
        row["project"] = proj
        if row["turns"] > 0:
            rows.append(row)
        else:
            n_stubs += 1
    return rows, n_stubs, n_files


def _ctx_walk(sessions_dir, th):
    """(rows, n_stubs, n_files) del walk de sesiones de Pi."""
    rows = []
    n_stubs = 0
    n_files = 0
    for sd in sorted(sessions_dir.iterdir()):
        if not sd.is_dir():
            continue
        p_rows, p_stubs, p_files = _ctx_project_rows(sd, th)
        rows += p_rows
        n_stubs += p_stubs
        n_files += p_files
    return rows, n_stubs, n_files


def extract_context_metrics(sessions_dir=None):
    """coffe-kup (tasks 1.8): camina los JSONL de Pi (mismo patrón que
    extract_pi_sessions: 1 archivo = 1 sesión, scope in_scope, month desde
    el FILENAME del archivo) y reduce_context() por sesión.

    Emite la sección context_metrics del reporte: sessions[] (filas con
    turns > 0 — stubs fuera, contados en meta.stubs), monthly_model[] y
    meta desde aggregate_context(). Si el dir de sesiones no existe emite
    available=False con razón (missing honesto, no array fantasma).

    Segundo walk aceptado por diseño (design.md del epic: "el walk extra
    agrega duración al nightly — se mide y, si pesa, --since").
    """
    sessions_dir = sessions_dir or (PI_DIR / "sessions")
    if not sessions_dir.exists():
        return _ctx_unavailable(
            f"unavailable: no existe {sessions_dir} "
            "(máquina sin logs de Pi)")
    cfg = load_config() or {}
    th = dict(DEFAULT_CONTEXT_THRESHOLDS)
    th.update(cfg.get("context_thresholds") or {})

    rows, n_stubs, n_files = _ctx_walk(sessions_dir, th)
    if not n_files:
        return _ctx_unavailable(
            "unavailable: sin archivos de sesión de Pi in-scope")
    agg = aggregate_context(rows, config=th)
    agg["meta"]["stubs"] = n_stubs  # del walk completo (los rows ya vienen limpios)
    agg["available"] = True
    agg["reason"] = None
    agg["sessions"] = rows
    return agg

# --- fin bloque ctx-tracker --------------------------------------------------

usage_state._link(globals())
