#!/usr/bin/env python3
"""
viz_ctx_views.py — secciones HTML de viz-ctx.py (epic
add-context-dashboard, ctx-viz)

Purpose: las seis vistas de la página de contexto (coffe-r3c 2.8):
Distribución, Composición, Tiempo, Coste, Ley de crecimiento y
Patologías — cada una como tarjeta <div class="card"> lista para la
página con tabs.

Responsibilities:
- sections(model): dict con las seis vistas envueltas en <section>
  (_sec) + counts (contadores compartidos con la ficha Procedencia).
- Builders puros por vista; sin datos → "" (la página muestra la
  tarjeta "sin datos" del placeholder _sec).

Rationale: HTML autocontenido stdlib-only con SVG inline de
viz_ctx_svg.py. Split de viz-ctx.py (coffe-v5f): módulo aparte para que
el archivo principal quede bajo el límite de líneas.
"""

import html

from viz_ctx_stats import _fmt_k, _fmt_human
from viz_ctx_svg import SEG_COLORS, _hist_svg, _stacked_svg


def _assumed_tag(model):
    """Tag del factor chars/chars_per_token, escapado ('assumed' default)."""
    meta = model.get("meta") or {}
    return html.escape(str(meta.get("chars_per_token_tag") or "assumed"))


def _counts(model):
    """Contadores compartidos por el título dist y la ficha Procedencia."""
    d = model.get("dist") or {}
    meta = model.get("meta") or {}
    exc = d.get("excluded_pathological", 0)
    stubs = d.get("stubs", 0)
    title_extra = []
    if exc:
        title_extra.append(f"{exc} excluida" + ("s" if exc != 1 else "")
                           + " (patológicas)")
    if stubs:
        title_extra.append(f"{stubs} stubs")
    vr = d.get("validation_ratio_median")
    return {
        "exc": exc,
        "stubs": stubs,
        "n_clean": d.get("n_clean", 0),
        "cpt": meta.get("chars_per_token"),
        "vr": vr,
        "ratio": f"{vr:.2f}" if vr else "n/a",
        "bias": html.escape(str(meta.get("chars_per_token_bias") or "")),
        "extra": (" · " + " · ".join(title_extra)) if title_extra else "",
    }


def _sec(sec_id, inner):
    if not inner.strip():
        inner = ('<div class="card"><h2>sin datos</h2>'
                 '<p class="muted">esta vista no tiene filas en el '
                 'reporte actual</p></div>')
    return (f'<section id="{sec_id}" class="ctx-view">{inner}</section>')


def sections(model):
    """Las seis vistas + counts para la página con tabs (coffe-r3c 2.8)."""
    counts = _counts(model)
    return {
        "dist": _sec("dist", _dist_card(model, counts)),
        "comp": _sec("comp", _comp_html(model)),
        "time": _sec("time", _time_html(model)),
        "cost": _sec("cost", _cost_html(model)),
        "ley": _sec("ley", _ley_html(model)),
        "pat": _sec("pat", _pat_html(model)),
        "counts": counts,
    }


def _dist_card(model, counts):
    """Vista Distribución: histograma + factores de estimación."""
    d = model.get("dist") or {}
    hist_html = _hist_svg(d["histogram"], d["band"])
    tag = _assumed_tag(model)
    return f"""
<div class="card">
  <h2>Pico de contexto por sesión — {counts["n_clean"]} sesiones limpias{counts["extra"]}</h2>
  {hist_html}
  <p class="muted">Estimación: chars / chars_per_token
     (<span class="tag">{tag}</span> = {counts["cpt"]})
     — {counts["bias"]} · ratio de validación vs usage real: {counts["ratio"]} (mediana).</p>
</div>"""


def _comp_html(model):
    """Vista Composición (coffe-opo 2.3): stacked global y grandes."""
    comp = model.get("comp") or {}
    if not comp:
        return ""
    legend = comp.get("legend") or []
    stack_g = _stacked_svg(comp["global"], legend)
    stack_b = _stacked_svg(comp["big"], legend)
    n_big = comp.get("n_big", 0)
    n_clean = (model.get("dist") or {}).get("n_clean", 0)
    tag = _assumed_tag(model)
    return f"""
<div class="card">
  <h2>Composición por fuente</h2>
  <p>Global ({n_clean} sesiones)</p>
  {stack_g}
  <p>Sesiones grandes > {_fmt_k(comp['big_threshold'])} tok
     ({n_big} sesiones) — {tag}</p>
  {stack_b}
  <p class="legend">{"".join(
      f'<span class="lg"><span class="sw {SEG_COLORS[i % len(SEG_COLORS)]}"></span>'
      f'{html.escape(src)}</span>' for i, src in enumerate(legend))}</p>
</div>"""


def _cost_html(model):
    """Vista Coste (coffe-dv6 2.5): spend por modelo y mensual, pagas."""
    cost = model.get("cost") or {}
    if not cost:
        return ""
    rows_m = "".join(
        f'<tr><td>{html.escape(str(m["model"]))}</td>'
        f'<td>{m["sessions"]}</td>'
        f'<td>${m["spend_in"]:.2f}</td><td>${m["spend_out"]:.2f}</td>'
        f'<td>${m["spend_cr"]:.2f}</td><td>${m["spend_cw"]:.2f}</td>'
        f'<td>${m["spend_per_session"]:.2f}</td></tr>'
        for m in cost["by_model"])
    rows_mm = "".join(
        f'<tr><td>{html.escape(str(r["month"]))}</td>'
        f'<td>${r["spend_in"]:.2f}</td><td>${r["spend_out"]:.2f}</td>'
        f'<td>${r["spend_cr"]:.2f}</td><td>${r["spend_cw"]:.2f}</td></tr>'
        for r in cost["by_month"])
    sub = cost["subscription"]
    null_line = (f'<p class="muted">{cost["n_null_cost"]} sesiones sin '
                 f'coste (null con razón): '
                 f'{html.escape(str(cost["null_reason_sample"]))}</p>'
                 if cost["n_null_cost"] else "")
    return f"""
<div class="card">
  <h2>Coste por sesión (pagas)</h2>
  <table class="ctx-table">
    <thead><tr><th>modelo</th><th>sesiones</th><th>in</th><th>out</th>
      <th>cache read</th><th>cache write</th><th>$/sesión</th></tr></thead>
    <tbody>{rows_m}</tbody>
  </table>
  <table class="ctx-table">
    <thead><tr><th>mes</th><th>in</th><th>out</th><th>cr</th><th>cw</th></tr></thead>
    <tbody>{rows_mm}</tbody>
  </table>
  <p>Sesiones claude-cli: <strong>{sub["sessions"]}</strong> —
     <span class="tag">subscription</span> (sin billing por token):
     NUNCA se suma al spend pagó (${cost['spend_total_in']:.2f} in +
     ${cost['spend_total_out']:.2f} out pagas).</p>
  {null_line}
</div>"""


def _pat_html(model):
    """Vista Patologías (coffe-dv6 2.7): outliers con tamaño y razón."""
    pats = model.get("pathological_list") or []
    if not pats:
        return ""
    pat_rows = "".join(
        f'<tr><td>{html.escape(str(p["project"]))}</td>'
        f'<td>{html.escape(str(p["month"]))}</td>'
        f'<td>{html.escape(str(p["model"]))}</td>'
        f'<td>{_fmt_human(p["max_assistant_block_chars"])}</td>'
        f'<td>{html.escape(p["reason"])}</td></tr>'
        for p in pats)
    return f"""
<div class="card">
  <h2>Patologías ({len(pats)} excluidas de percentiles)</h2>
  <table class="ctx-table">
    <thead><tr><th>proyecto</th><th>mes</th><th>modelo</th>
      <th>bloque máx</th><th>razón</th></tr></thead>
    <tbody>{pat_rows}</tbody>
  </table>
  <p class="muted">nada silencioso (FPA-008): cada exclusión lleva su
     tamaño y razón — el umbral viene de config.</p>
</div>"""


def _time_row(r):
    """Fila <tr> de la tabla Tiempo con badge parcial y tok/turno."""
    tpt = ", ".join(f"{html.escape(k)}: {_fmt_k(v)}"
                    for k, v in (r.get("tokens_per_turn_by_source")
                                 or {}).items())
    badge = (" <span class=tag>parcial "
             + html.escape(r["days_label"]) + "</span>"
             if r["partial"] else "")
    return (f'<tr><td>{html.escape(str(r["month"]))}{badge}</td>'
            f'<td>{html.escape(str(r["model"]))}</td>'
            f'<td>{r["sessions"]}</td>'
            f'<td>{_fmt_k(r["peak_p50"] or 0)}</td>'
            f'<td>{r["pct_big_session"]:.1f}%</td>'
            f'<td>{tpt or "n/a"}</td></tr>')


def _time_html(model):
    """Vista Tiempo (coffe-opo 2.4): tabla mes × modelo dominante."""
    time_rows = (model.get("time") or {}).get("rows", [])
    if not time_rows:
        return ""
    comp = model.get("comp") or {}
    rows_html = "".join(_time_row(r) for r in time_rows)
    big_col = (_fmt_k(comp.get('big_threshold', 0)) if comp else '—')
    return f"""
<div class="card">
  <h2>Tiempo — mes × modelo dominante</h2>
  <table class="ctx-table">
    <thead><tr><th>mes</th><th>modelo</th><th>sesiones</th>
      <th>pico mediano (tok)</th><th>% > {big_col}</th>
      <th>tok/turno por fuente</th></tr></thead>
    <tbody>{rows_html}</tbody>
  </table>
  <p class="muted">meses parciales marcados con días transcurridos/total
     (FPA-041); pico mediano y % sobre el set limpio de cada grupo.</p>
</div>"""


def _scatter(g, slope):
    """SVG scatter + fila de un modelo (ley de crecimiento)."""
    pts = " ".join(f"{t},{p}" for t, p in g["points"][:50])
    xs = [t for t, _ in g["points"]] or [0]
    ys = [p for _, p in g["points"]] or [0]
    xmax, ymax = max(xs), max(ys) or 1
    dots = "".join(
        f'<circle cx="{20 + (t / xmax) * 300:.1f}" '
        f'cy="{110 - (p / ymax) * 90:.1f}" r="2.5" '
        f'class="dot"/><title>{g["model"]}: {t} turnos → '
        f'{_fmt_k(p)} tok</title>'
        for t, p in g["points"])
    return f"""
    <div class="sc-wrap">
      <svg viewBox="0 0 340 130" class="scatter" role="img"
           aria-label="pico vs turnos {html.escape(str(g['model']))}">
        <line x1="20" y1="110" x2="320" y2="110" class="axis"/>
        <line x1="20" y1="10" x2="20" y2="110" class="axis"/>
        {dots}
      </svg>
      <p><strong>{html.escape(str(g["model"]))}</strong> ({g["n"]} sesiones)
         — pendiente OLS: {slope} · pico = a + b·turnos (tok estimados)</p>
    </div>"""


def _ley_rows(by_model):
    """Filas del scatter pico vs turnos por modelo (coffe-r3c 2.6)."""
    rows_g = ""
    for g in by_model:
        slope = (f"~{g['slope']:.0f} tok/turno"
                 if g["slope"] is not None else
                 html.escape(g["slope_reason"] or "indefinida"))
        rows_g += _scatter(g, slope)
    return rows_g


def _ley_html(model):
    """Vista Ley de crecimiento: scatter + pendiente OLS pre-calculada."""
    growth = model.get("growth") or {"by_model": []}
    if growth["by_model"]:
        return f"""
<div class="card">
  <h2>Ley de crecimiento — pico vs turnos</h2>
  {_ley_rows(growth["by_model"])}
  <p class="muted">pendiente OLS pre-calculada en Python; hallazgo del
     epic: ~850 tok/turno constante entre eras → la pendiente traduce
     higiene de sesiones a presupuesto.</p>
</div>"""
    return ('<div class="card"><h2>Ley de crecimiento</h2>'
            '<p class="muted">sin sesiones limpias: sin ajuste</p></div>')
