"""Módulo extraído de viz-ddl.py (decomposición, comportamiento idéntico):
las 6 vistas del dashboard y su dispatcher.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from viz_ddl_common import (VIEW_TITLES, _cell, _estimator_table, _fmt,
                            _monthly_rows, did_rows_html, esc, fig,
                            its_rows_html, placeholder, prov_tag)


def view_adopcion(report):
    """Vista línea de adopción (coffe-het 5.2): timeline por repo, fechas
    null visibles como 'sin señal' (patrón FPA-008) — nunca fecha imputada."""
    repos = report.get("repos") or []
    if not repos:
        return (f'<h2>{esc(VIEW_TITLES["adopcion"])}</h2>'
                f'<p class="placeholder">Adopción: sin datos</p>')
    ordenados = sorted(
        repos,
        key=lambda r: (r.get("adoption", {}).get("first_ddl_commit") is None,
                       r.get("adoption", {}).get("first_ddl_commit") or ""))
    rows = []
    for r in ordenados:
        a = r.get("adoption", {})
        fecha = a.get("first_ddl_commit")
        badge = ""
        if r.get("is_outlier"):
            badge = ' <span class="badge">outlier</span>'
        elif r.get("is_control"):
            badge = ' <span class="badge">control</span>'
        dose = a.get("dose")
        dose_cell = (fig(_fmt(dose), "reported") if dose is not None
                     else fig("sin señal", "missing"))
        first = (esc(fecha) if fecha
                 else fig("sin señal", "missing"))
        rows.append(
            f'<tr data-repo="{esc(r.get("repo", "?"))}">'
            f'<td>{esc(r.get("repo", "?"))}{badge}</td>'
            f"<td>{first}</td>"
            f"<td>{dose_cell}</td></tr>")
    return (f'<h2>{esc(VIEW_TITLES["adopcion"])}</h2>'
            "<p>Primer commit de <code>.ddl/</code> por repo (fechas null "
            "visibles — sin imputación):</p>"
            f'<table><thead><tr><th>repo</th><th>primer commit .ddl/</th>'
            f'<th>dosis</th></tr></thead><tbody>{"".join(rows)}</tbody></table>')


def _scatter_svg(points):
    """Scatter dosis × outcome en SVG inline, sin librerías. Ejes fijos:
    dosis 0–1, outcome auto-escala al rango de los puntos."""
    W, H, PAD = 320, 180, 24
    outcomes = [p["outcome"] for p in points]
    if not outcomes:  # rho sin puntos: placeholder, nunca crash (regen real)
        return ('<p class="placeholder">Scatter sin puntos graficables'
                "(outcome/dose null)</p>")
    lo, hi = min(outcomes), max(outcomes)
    span = (hi - lo) or 1.0
    circles = []
    for p in points:
        x = PAD + p["dose"] * (W - 2 * PAD)
        y = H - PAD - (p["outcome"] - lo) / span * (H - 2 * PAD)
        circles.append(f'<circle class="scatter-pt" cx="{x:.1f}" '
                       f'cy="{y:.1f}" r="4" '
                       f'title="{esc(p["repo"])}"/>')
    return (f'<svg class="ddl-chart" width="{W}" height="{H}" '
            f'role="img" aria-label="scatter dosis-respuesta">'
            f'{"".join(circles)}</svg>')


def _dose_block(e):
    """Un bloque de dosis-respuesta: placeholder si n<3 o sin rho (regla dura
    anti-anécdota), scatter + rho visibles si se grafica."""
    label = esc(e.get("metric", "?"))
    rho = e.get("spearman_rho")
    if rho is None or (e.get("n") or 0) < 3:
        reason = esc(e.get("reason", "n_insuficiente"))
        return (f'<p class="placeholder">{label}: no graficada ({reason}), '
                f'n = {e.get("n", "?")}</p>')
    points = [p for p in (e.get("points") or [])
              if p.get("dose") is not None
              and p.get("outcome") is not None]
    return (f'<div class="scatter-block"><h3>{label}</h3>'
            f"{fig(f'ρ = {rho:.2f} · n = {e["n"]}', 'reported')}"
            f"{_scatter_svg(points)}</div>")


def view_dosis_respuesta(report):
    """Vista dosis-respuesta (coffe-het 5.3): scatter con n por punto;
    regla dura — celdas con n < 3 o sin rho no se grafican, quedan marcadas
    (anti-anécdota, spec)."""
    entries = (report.get("analysis") or {}).get("dose_response") or []
    blocks = [_dose_block(e) for e in entries]
    if not blocks:
        blocks = ['<p class="placeholder">Dosis-respuesta: sin datos</p>']
    return (f'<h2>{esc(VIEW_TITLES["dosis-respuesta"])}</h2>'
            + "\n".join(blocks))


def view_pre_post(report):
    """Vista pre/post + DiD (coffe-470): ITS por repo (coffe-ktu) arriba,
    DiD por métrica con IC bootstrap abajo. Provenance derived en toda cifra."""
    analysis = report.get("analysis") or {}
    its = _estimator_table(
        ["repo · métrica", "delta", "IC bootstrap", "n"],
        its_rows_html(analysis.get("its") or []),
        "ITS: sin datos")
    did = _estimator_table(
        ["métrica", "DiD", "IC bootstrap", "deltas", "n"],
        did_rows_html(analysis.get("did") or []),
        "DiD: sin datos")
    return (f'<h2>{esc(VIEW_TITLES["pre-post"])}</h2>'
            f"<p>Serie interrumpida por repo (media móvil trimestral, "
            f"IC bootstrap seed fija):</p>{its}"
            f"<p>Diferencia-en-diferencias 2×2 contra control "
            f"(IC bootstrap sobre deltas por repo):</p>{did}")


def _recovery_cell(r):
    """Celda de recuperación: p50 con n y tasa de censura visibles; None
    delega a _cell (razón visible — nunca 0 fake, spec)."""
    p50 = r.get("recovery_p50_interactions")
    if p50 is None:
        return _cell(r, "recovery_p50_interactions")
    tag = (r.get("src") or {}).get("recovery_p50_interactions", "missing")
    n = r.get("recovery_n")
    cens = r.get("recovery_censored_rate")
    det = (" (n: " + (str(n) if n is not None else "?")
           + ", censura "
           + (f"{cens:.0%}" if cens is not None else "n/a") + ")")
    return fig(f"{p50:.2f} inter{esc(det)}", tag)


def _ci_cell(r):
    """Celda de CI fallida: la tasa con runs f/t entre paréntesis cuando hay
    denominador; None delega a _cell (razón visible)."""
    if r.get("ci_fail_rate") is None:
        return _cell(r, "ci_fail_rate")
    runs_t, runs_f = r.get("runs_total"), r.get("runs_failed")
    runs = (f" ({int(runs_f)}/{int(runs_t)})"
            if runs_t is not None and runs_f is not None else "")
    return fig(f"{r['ci_fail_rate']:.2f}{esc(runs)}",
               (r.get("src") or {}).get("ci_fail_rate", "missing"))


def view_lazo_feedback(report):
    """Vista lazo de feedback (coffe-46h 5.5): recovery (p50 con n y tasa de
    censura visibles — spec 'recuperación con censura visible'), latencia
    sesión→commit y CI fallida con runs; null con razón, nunca 0 fake."""
    rows = _monthly_rows(report)
    if not rows:
        return (f'<h2>{esc(VIEW_TITLES["lazo-feedback"])}</h2>'
                '<p class="placeholder">Lazo de feedback: sin datos'
                f'{prov_tag("missing")}</p>')
    trs = []
    for r in rows:
        trs.append(
            f'<tr data-repo="{esc(r.get("repo", "?"))}">'
            f'<td>{esc(r.get("repo", "?"))}</td><td>{esc(r.get("month", ""))}</td>'
            f"<td>{_recovery_cell(r)}</td>"
            f"<td>{_cell(r, 'session_commit_latency_p50_h', suffix=' h')}</td>"
            f"<td>{_ci_cell(r)}</td></tr>")
    return (f'<h2>{esc(VIEW_TITLES["lazo-feedback"])}</h2>'
            "<p>Ciclo error→recuperación, sesión→commit y CI por repo × mes "
            "(null con razón — nunca 0 fake):</p>"
            + _estimator_table(["repo", "mes", "recuperación (p50 inter)",
                            "sesión→commit", "CI fallida"],
                           trs, "Lazo de feedback: sin datos"))


def view_errores(report):
    """Vista errores (coffe-46h 5.5): tasas por 100 interacciones con el
    denominador en el mismo row (spec 'denominador siempre presente') y
    clases de terapia desglosadas."""
    rows = _monthly_rows(report)
    if not rows:
        return (f'<h2>{esc(VIEW_TITLES["errores"])}</h2>'
                '<p class="placeholder">Errores: sin datos'
                f'{prov_tag("missing")}</p>')
    trs = []
    for r in rows:
        trs.append(
            f'<tr data-repo="{esc(r.get("repo", "?"))}">'
            f'<td>{esc(r.get("repo", "?"))}</td><td>{esc(r.get("month", ""))}</td>'
            f'<td>{esc(str(r.get("interactions")))}</td>'
            f"<td>{_cell(r, 'errors_api_per_100')}</td>"
            f"<td>{_cell(r, 'errors_terapia_per_100')}</td></tr>")
    return (f'<h2>{esc(VIEW_TITLES["errores"])}</h2>'
            "<p>Errores por 100 interacciones (el denominador va en el row):</p>"
            + _estimator_table(["repo", "mes", "interacciones", "errores API/100",
                            "clases terapia/100"],
                           trs, "Errores: sin datos"))


def view_calidad(report):
    """Vista calidad (coffe-46h 5.5): los 4 proxies lado a lado — revert,
    fix, test/commit y ah_check — sin score compuesto ponderado (spec
    'sin score inventado'). Cada bias está declarado en la guía."""
    rows = _monthly_rows(report)
    if not rows:
        return (f'<h2>{esc(VIEW_TITLES["calidad"])}</h2>'
                '<p class="placeholder">Calidad: sin datos'
                f'{prov_tag("missing")}</p>')
    trs = []
    for r in rows:
        trs.append(
            f'<tr data-repo="{esc(r.get("repo", "?"))}">'
            f'<td>{esc(r.get("repo", "?"))}</td><td>{esc(r.get("month", ""))}</td>'
            f"<td>{_cell(r, 'revert_ratio')}</td>"
            f"<td>{_cell(r, 'fix_ratio')}</td>"
            f"<td>{_cell(r, 'test_commit_ratio')}</td>"
            f"<td>{_cell(r, 'ah_check_findings', nd=0)}</td></tr>")
    return (f'<h2>{esc(VIEW_TITLES["calidad"])}</h2>'
            "<p>Cuatro proxies separados — <strong>sin score compuesto</strong> "
            "(combinarlos inventaría una métrica que no medimos):</p>"
            + _estimator_table(["repo", "mes", "revert_ratio", "fix_ratio",
                            "test_commit_ratio", "ah_check_findings"],
                           trs, "Calidad: sin datos"))


def view_html(view, report):
    """Dispatcher de vistas: las implementadas renderizan su contenido, el
    resto cae al placeholder del esqueleto (los tickets het/46h/8yf van
    enchufando las suyas acá)."""
    if view == "pre-post":
        return view_pre_post(report)
    if view == "adopcion":
        return view_adopcion(report)
    if view == "dosis-respuesta":
        return view_dosis_respuesta(report)
    if view == "lazo-feedback":
        return view_lazo_feedback(report)
    if view == "errores":
        return view_errores(report)
    if view == "calidad":
        return view_calidad(report)
    return placeholder(view, report)


def sections_html(report):
    from viz_ddl_common import VIEWS
    out = []
    for i, v in enumerate(VIEWS):
        hidden = "" if i == 0 else " hidden"
        out.append(f'<section class="view" id="{v}" data-view="{v}"'
                   f'{hidden}>{view_html(v, report)}</section>')
    return "\n".join(out)
