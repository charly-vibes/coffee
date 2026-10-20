#!/usr/bin/env python3
"""
viz-ctx.py — dashboard de contexto (epic add-context-dashboard, ctx-viz)

Purpose: renderizar la página nueva data/context-dashboard.html desde la
sección context_metrics de data/usage_report_v3.json (Pi-only).

Responsibilities:
- build_model(report): pre-cálculo en Python (FPA-145: el JS solo
  re-escala — aquí, ni JS: SVG estático) — histograma del pico de contexto
  por sesión sobre el SET LIMPIO (turns>0, sin patológicas), banda de
  percentiles p50/p75/p90/p95/p99, conteo de excluidas/stubs junto al
  título, umbral patológico de config, tags *assumed* del factor
  chars/chars_per_token con su bias y ratio de validación
- render(model): HTML autocontenido stdlib-only, SVG inline, tema
  compartido scripts/site_theme.py — sin CDN ni librerías externas
  (anti-goal FPA-145), header con el alcance Pi-only declarado
- main(): lee el reporte y escribe el HTML; reporte sin sección o
  unavailable → placeholder honesto con la razón (nunca vacío sin valor,
  patrón FPA-108)

Rationale: página nueva (decisión D1 del design.md) — las métricas de
contexto son por-sesión y Pi-only, granularidad distinta a fpa-dashboard;
el repo ya tiene el patrón viz secundaria que lee el mismo reporte
(viz-gantt.py). El cálculo vive en el tracker (reducer puro); esta viz
solo pre-calcula presentación y renderiza.
"""

import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import site_theme  # noqa: E402

REPORT = Path("data/usage_report_v3.json")
OUT = Path("data/context-dashboard.html")

PCT_KEYS = ("p50", "p75", "p90", "p95", "p99")
N_BINS = 10


def _month_days(ym):
    """Días del mes YYYY-MM (FPA-041: denominador de días)."""
    import calendar
    y, m = int(ym[:4]), int(ym[5:7])
    return calendar.monthrange(y, m)[1]


def _month_elapsed(ym, end_date):
    """Días transcurridos del mes: los completados toman el total; el mes
    del fin de rango toma el día del fin (FPA-041)."""
    if not end_date or ym != end_date[:7]:
        return _month_days(ym)
    return int(end_date[8:10])


def _pctl(sorted_vals, p):
    """Percentil p (0..1) con interpolación lineal sobre lista ordenada."""
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    idx = (len(sorted_vals) - 1) * p
    lo = int(idx)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = idx - lo
    return sorted_vals[lo] + frac * (sorted_vals[hi] - sorted_vals[lo])


def _fmt_k(x):
    return f"{x/1000:.0f}k" if x >= 1000 else f"{x:.0f}"


def build_model(report):
    """Pre-cálculo de la vista Distribución (+ tags assumed) desde el
    reporte. Pure: no toca disco ni imprime. Con la sección ausente o
    unavailable devuelve available=False con la razón (missing honesto)."""
    cm = report.get("context_metrics") or {}
    if not cm.get("available"):
        return {"available": False, "reason": cm.get("reason")
                or "unavailable: context_metrics ausente del reporte",
                "scope_note": "sesiones de Pi únicamente"}

    sessions = cm.get("sessions") or []
    meta = cm.get("meta") or {}
    clean = [s for s in sessions
             if s.get("turns", 0) > 0 and not s.get("pathological")]
    excluded = sum(1 for s in sessions if s.get("pathological"))
    stubs = meta.get("stubs",
                     sum(1 for s in sessions if s.get("turns", 0) == 0))

    peaks = sorted(s["peak_ctx_est"] for s in clean)
    band = {k: _pctl(peaks, p) for k, p in
            zip(PCT_KEYS, (0.5, 0.75, 0.9, 0.95, 0.99))}

    th = meta.get("thresholds", {})
    hist = []
    if peaks:
        vmax = peaks[-1]
        step = max(vmax / N_BINS, 1)
        edges = [i * step for i in range(N_BINS + 1)]
        counts = [0] * N_BINS
        for p in peaks:
            b = min(int(p / step), N_BINS - 1)
            counts[b] += 1
        hist = [{"from": edges[i], "to": edges[i + 1], "count": counts[i]}
                for i in range(N_BINS)]

    return {
        "available": True,
        "reason": None,
        "scope_note": "sesiones de Pi únicamente",
        "dist": {
            "histogram": hist,
            "band": band,
            "n_clean": len(clean),
            "excluded_pathological": excluded,
            "stubs": stubs,
            "thresholds": th,
            "validation_ratio_median": meta.get("validation_ratio_median"),
        },
        "meta": {
            "chars_per_token": meta.get("chars_per_token"),
            "chars_per_token_tag": meta.get("chars_per_token_tag"),
            "chars_per_token_bias": meta.get("chars_per_token_bias"),
            "validation_ratio_median": meta.get("validation_ratio_median"),
        },
        "comp": _build_comp(clean, th),
        "growth": _build_growth(clean),
        "cost": _build_cost(sessions),
        "pathological_list": [{
            "project": s.get("project"),
            "month": s.get("month"),
            "model": s.get("model"),
            "max_assistant_block_chars": s.get("max_assistant_block_chars"),
            "threshold": (meta.get("thresholds") or {})
                         .get("pathological_chars"),
            "reason": (
                f"bloque assistant de "
                f"{_fmt_human(s.get('max_assistant_block_chars') or 0)} "
                f"> umbral patológico de "
                f"{_fmt_human((meta.get('thresholds') or {})
                             .get('pathological_chars') or 0)} "
                "(config) — excluida de percentiles"),
        } for s in sessions if s.get("pathological")],
        "time": _build_time(cm.get("monthly_model") or [],
                            (report.get("metadata") or {})
                            .get("date_range", {}).get("end")),
        "monthly_model": cm.get("monthly_model") or [],
    }


def _ols(xs, ys):
    """OLS simple: pendiente e intersección de y = a + b·x (stdlib)."""
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None, my  # sin varianza en x: pendiente indefinida
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    b = sxy / sxx
    return b, my - b * mx


def _build_growth(clean):
    """Vista Ley de crecimiento (coffe-r3c 2.6): dispersivo pico vs turnos
    por modelo con pendiente OLS pre-calculada en Python (hallazgo del
    epic: ~850 tok/turno constante entre eras). Sin varianza en turnos →
    pendiente null con razón (nada silencioso)."""
    by_model = []
    for m in sorted({r.get("model") for r in clean}, key=str):
        rows = [r for r in clean if r.get("model") == m]
        xs = [r["turns"] for r in rows]
        ys = [r["peak_ctx_est"] for r in rows]
        b, a = _ols(xs, ys)
        by_model.append({
            "model": m,
            "n": len(rows),
            "slope": b,
            "intercept": a,
            "slope_reason": None if b is not None
                            else "sin varianza en turnos (n=1 o todos iguales)"
                                 " — pendiente indefinida",
            "points": list(zip(xs, ys)),
        })
    return {"by_model": by_model}


def _fmt_human(n):
    """16_000_000 → '16.0M', 500_000 → '500.0k', 999 → '999'."""
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1000:
        return f"{n/1000:.1f}k"
    return str(int(n))


def _build_cost(sessions):
    """Vista Coste (coffe-dv6 2.5): spend por modelo con split
    in/out/cache, spend mensual y $/sesión — SOLO sesiones pagas (pi con
    usage.cost). claude-cli emite fila aparte con provenancia
    "subscription" y jamás se suma al total pagó (sin suma combinada).
    Coste null con razón visible (n + muestra de la razón)."""
    paid = [s for s in sessions
            if s.get("context_source") == "pi" and s.get("cost_in") is not None]
    sub = [s for s in sessions if s.get("context_source") == "claude-cli"]
    nulls = [s for s in sessions if s.get("cost_in") is None
             and s.get("context_source") != "claude-cli"]
    null_reasons = [s.get("cost_null_reason") for s in nulls
                    if s.get("cost_null_reason")]

    def _spend(rows, key=None):
        rows = [r for r in rows if key is None or r.get("model") == key]
        return {"spend_in": round(sum(r["cost_in"] for r in rows), 2),
                "spend_out": round(sum(r["cost_out"] for r in rows), 2),
                "spend_cr": round(sum(r["cost_cr"] for r in rows), 2),
                "spend_cw": round(sum(r["cost_cw"] for r in rows), 2)}

    by_model = []
    for m in sorted({r.get("model") for r in paid}, key=str):
        sp = _spend(paid, m)
        n = sum(1 for r in paid if r.get("model") == m)
        by_model.append(dict(model=m, sessions=n, **sp,
                             spend_per_session=round(
                                 (sp["spend_in"] + sp["spend_out"]
                                  + sp["spend_cr"] + sp["spend_cw"]) / n, 4)
                             if n else 0.0))
    by_month = []
    for ym in sorted({r.get("month") for r in paid}, key=str):
        by_month.append(dict(month=ym, **_spend([r for r in paid
                                                 if r.get("month") == ym])))
    tot = _spend(paid)
    return {
        "by_model": by_model,
        "by_month": by_month,
        "spend_total_in": tot["spend_in"],
        "spend_total_out": tot["spend_out"],
        "spend_total_cr": tot["spend_cr"],
        "spend_total_cw": tot["spend_cw"],
        "subscription": {
            "sessions": len(sub),
            "provenance": "subscription",
            "spend_in": 0.0, "spend_out": 0.0,
            "spend_cr": 0.0, "spend_cw": 0.0,
        },
        "n_null_cost": len(nulls),
        "null_reason_sample": null_reasons[0] if null_reasons else None,
    }


def _build_comp(clean, th):
    """Share por fuente global y en sesiones grandes (> big_session_tokens
    de config) — coffe-opo 2.3. Leyenda = unión de fuentes en ambas."""
    big_th = th.get("big_session_tokens") or 100_000
    def _shares(rows):
        totals = {}
        for r in rows:
            for src, n in (r.get("chars_by_source") or {}).items():
                totals[src] = totals.get(src, 0) + n
        total = sum(totals.values()) or 1
        return {k: v / total for k, v in totals.items()}
    big = [s for s in clean if s.get("peak_ctx_est", 0) > big_th]
    glob = _shares(clean)
    big_sh = _shares(big)
    return {
        "global": glob,
        "big": big_sh,
        "big_threshold": big_th,
        "n_big": len(big),
        "legend": sorted(set(glob) | set(big_sh)),
    }


def _build_time(monthly_model, end_date):
    """Tabla mes × modelo dominante — coffe-opo 2.4. Meses parciales
    marcados con días transcurridos/total (FPA-041)."""
    rows = []
    for r in sorted(monthly_model,
                    key=lambda x: (str(x.get("month")), str(x.get("model")))):
        ym = r.get("month")
        partial = r.get("partial")
        if ym:
            elapsed = _month_elapsed(ym, end_date)
            days = _month_days(ym)
            days_label = f"{elapsed}/{days}" if partial else f"{days}/{days}"
        else:
            days_label = "n/a"
        rows.append(dict(r, days_label=days_label))
    return {"rows": rows}


def _stacked_svg(shares, legend, colors, w=360, h=60):
    """Barra stacked (100%) con tooltips nativos; leyenda aparte."""
    parts = [f'<svg viewBox="0 0 {w} {h}" class="stack" role="img" '
             f'aria-label="share por fuente">']
    x = 0.0
    order = [s for s in legend if s in shares]
    for i, src in enumerate(order):
        sw = shares[src] * w
        cls = colors[i % len(colors)]
        parts.append(f'<rect x="{x:.1f}" y="4" width="{max(sw, 0):.1f}" '
                     f'height="{h - 20}" class="seg {cls}">'
                     f'<title>{html.escape(src)}: {shares[src]*100:.1f}%</title>'
                     f'</rect>')
        x += sw
    parts.append("</svg>")
    return "\n".join(parts)


def _hist_svg(hist, band, w=760, h=240):
    """Histograma del pico como SVG estático con tooltips nativos."""
    if not hist:
        return '<p class="muted">sin sesiones limpias: nada que histogramar</p>'
    max_c = max(hc["count"] for hc in hist) or 1
    pad_l, pad_b, pad_t = 56, 30, 12
    plot_w, plot_h = w - pad_l - 16, h - pad_b - pad_t
    bw = plot_w / len(hist)
    parts = [f'<svg viewBox="0 0 {w} {h}" role="img" '
             f'aria-label="Histograma del pico de contexto" '
             f'class="ctx-hist">']
    parts.append(f'<line x1="{pad_l}" y1="{h - pad_b}" x2="{w - 16}" '
                 f'y2="{h - pad_b}" class="axis"/>')
    for i, b in enumerate(hist):
        x = pad_l + i * bw
        bh = (b["count"] / max_c) * plot_h if b["count"] else 0
        y = h - pad_b - bh
        parts.append(f'<rect x="{x + 2:.1f}" y="{y:.1f}" '
                     f'width="{bw - 4:.1f}" height="{bh:.1f}" '
                     f'class="bar"><title>{_fmt_k(b["from"])}–'
                     f'{_fmt_k(b["to"])}: {b["count"]} sesiones</title></rect>')
        if b["from"] % max(1, int(hist[-1]["to"] / 5)) < b["to"] - b["from"]:
            parts.append(f'<text x="{x + bw / 2:.1f}" y="{h - pad_b + 14}" '
                         f'text-anchor="middle" class="tick">'
                         f'{_fmt_k(b["from"])}</text>')
    # banda de percentiles: líneas verticales con etiqueta
    colors = {"p50": "acc", "p75": "good", "p90": "acc2", "p95": "navy",
              "p99": "bad"}
    for k in PCT_KEYS:
        v = band.get(k)
        if v is None:
            continue
        x = pad_l + (v / (hist[-1]["to"])) * plot_w
        parts.append(f'<line x1="{x:.1f}" y1="{pad_t}" x2="{x:.1f}" '
                     f'y2="{h - pad_b}" class="pctl pctl-{k}">'
                     f'<title>{k}: {_fmt_k(v)} tok</title></line>')
        parts.append(f'<text x="{x:.1f}" y="{pad_t + 2}" '
                     f'text-anchor="middle" class="tick {colors.get(k, "acc")}"'
                     f'>{k}</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def render(model):
    """HTML autocontenido (sin dependencias externas) con SVG inline."""
    t = site_theme.TOKENS
    v = site_theme.css_vars()
    base = site_theme.base_css()
    scope = html.escape(model.get("scope_note", ""))
    if not model.get("available"):
        reason = html.escape(model.get("reason") or "unavailable")
        return f"""<!DOCTYPE html>
<html lang="es">
<head><meta charset="utf-8"><title>Contexto — sin datos</title>
<style>{v}{base}
body {{ margin: 0; padding: 18px; background: {t['--bg']}; color: {t['--fg']};
        font-size: 13px; font-family: Arial, sans-serif; }}
</style></head>
<body>
<h1>Contexto de sesiones (Pi)</h1>
<p class="placeholder">sin datos — {reason}</p>
</body></html>"""
    d = model["dist"]
    meta = model["meta"]
    n_clean = d.get("n_clean", 0)
    vr = d.get("validation_ratio_median")
    tag = html.escape(str(meta.get("chars_per_token_tag") or "assumed"))
    hist_html = _hist_svg(d["histogram"], d["band"])
    comp = model.get("comp") or {}
    time_rows = (model.get("time") or {}).get("rows", [])
    seg_colors = ("c1", "c2", "c3", "c4", "c5", "c6")
    legend = comp.get("legend") or []
    comp_html = ""
    if comp:
        stack_g = _stacked_svg(comp["global"], legend, seg_colors)
        stack_b = _stacked_svg(comp["big"], legend, seg_colors)
        n_big = comp.get("n_big", 0)
        comp_html = f"""
<div class="card">
  <h2>Composición por fuente</h2>
  <p>Global ({n_clean} sesiones)</p>
  {stack_g}
  <p>Sesiones grandes > {_fmt_k(comp['big_threshold'])} tok
     ({n_big} sesiones) — {tag}</p>
  {stack_b}
  <p class="legend">{"".join(
      f'<span class="lg"><span class="sw {seg_colors[i % len(seg_colors)]}"></span>'
      f'{html.escape(src)}</span>' for i, src in enumerate(legend))}</p>
</div>"""
    cost = model.get("cost") or {}
    cost_html = ""
    if cost:
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
        cost_html = f"""
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
    pats = model.get("pathological_list") or []
    pat_html = ""
    if pats:
        pat_rows = "".join(
            f'<tr><td>{html.escape(str(p["project"]))}</td>'
            f'<td>{html.escape(str(p["month"]))}</td>'
            f'<td>{html.escape(str(p["model"]))}</td>'
            f'<td>{_fmt_human(p["max_assistant_block_chars"])}</td>'
            f'<td>{html.escape(p["reason"])}</td></tr>'
            for p in pats)
        pat_html = f"""
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
    time_html = ""
    if time_rows:
        rows_html = ""
        for r in time_rows:
            tpt = ", ".join(f"{html.escape(k)}: {_fmt_k(v)}"
                            for k, v in (r.get("tokens_per_turn_by_source")
                                         or {}).items())
            badge = (" <span class=tag>parcial "
                     + html.escape(r["days_label"]) + "</span>"
                     if r["partial"] else "")
            rows_html += (
                f'<tr><td>{html.escape(str(r["month"]))}{badge}</td>'
                f'<td>{html.escape(str(r["model"]))}</td>'
                f'<td>{r["sessions"]}</td>'
                f'<td>{_fmt_k(r["peak_p50"] or 0)}</td>'
                f'<td>{r["pct_big_session"]:.1f}%</td>'
                f'<td>{tpt or "n/a"}</td></tr>')
        time_html = f"""
<div class="card">
  <h2>Tiempo — mes × modelo dominante</h2>
  <table class="ctx-table">
    <thead><tr><th>mes</th><th>modelo</th><th>sesiones</th>
      <th>pico mediano (tok)</th><th>% > {_fmt_k(comp.get('big_threshold', 0)) if comp else '—'}</th>
      <th>tok/turno por fuente</th></tr></thead>
    <tbody>{rows_html}</tbody>
  </table>
  <p class="muted">meses parciales marcados con días transcurridos/total
     (FPA-041); pico mediano y % sobre el set limpio de cada grupo.</p>
</div>"""
    bias = html.escape(str(meta.get("chars_per_token_bias") or ""))
    cpt = meta.get("chars_per_token")
    ratio = f"{vr:.2f}" if vr else "n/a"
    exc = d.get("excluded_pathological", 0)
    stubs = d.get("stubs", 0)
    title_extra = []
    if exc:
        title_extra.append(f"{exc} excluida" + ("s" if exc != 1 else "")
                           + " (patológicas)")
    if stubs:
        title_extra.append(f"{stubs} stubs")
    extra = (" · " + " · ".join(title_extra)) if title_extra else ""

    # vista Ley de crecimiento (coffe-r3c 2.6): scatter pico vs turnos por
    # modelo + pendiente OLS pre-calculada
    growth = model.get("growth") or {"by_model": []}
    ley_html = ""
    if growth["by_model"]:
        rows_g = ""
        for g in growth["by_model"]:
            slope = (f"~{g['slope']:.0f} tok/turno"
                     if g["slope"] is not None else
                     html.escape(g["slope_reason"] or "indefinida"))
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
            rows_g += f"""
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
        ley_html = f"""
<div class="card">
  <h2>Ley de crecimiento — pico vs turnos</h2>
  {rows_g}
  <p class="muted">pendiente OLS pre-calculada en Python; hallazgo del
     epic: ~850 tok/turno constante entre eras → la pendiente traduce
     higiene de sesiones a presupuesto.</p>
</div>"""
    else:
        ley_html = '<div class="card"><h2>Ley de crecimiento</h2>' \
                   '<p class="muted">sin sesiones limpias: sin ajuste</p></div>'

    tabs = (("dist", "Distribución"), ("comp", "Composición"),
            ("time", "Tiempo"), ("cost", "Coste"), ("ley", "Ley"),
            ("pat", "Patologías"))
    views_json = json.dumps([k for k, _ in tabs])
    nav = "".join(f'<a class="tab" role="tab" href="#{k}" '
                  f'data-view="{k}">{label}</a>'
                  for k, label in tabs)

    def _sec(sec_id, inner):
        if not inner.strip():
            inner = ('<div class="card"><h2>sin datos</h2>'
                     '<p class="muted">esta vista no tiene filas en el '
                     'reporte actual</p></div>')
        return (f'<section id="{sec_id}" class="ctx-view">{inner}</section>')

    comp_html = _sec("comp", comp_html)
    time_html = _sec("time", time_html)
    cost_html = _sec("cost", cost_html)
    ley_html = _sec("ley", ley_html)
    pat_html = _sec("pat", pat_html)
    dist_html = _sec("dist", f"""
<div class="card">
  <h2>Pico de contexto por sesión — {n_clean} sesiones limpias{extra}</h2>
  {hist_html}
  <p class="muted">Estimación: chars / chars_per_token
     (<span class="tag">{tag}</span> = {cpt})
     — {bias} · ratio de validación vs usage real: {ratio} (mediana).</p>
</div>""")
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>Contexto — distribución del pico por sesión (Pi)</title>
<style>
{v}{base}
  body {{ margin: 0; padding: 18px; background: var(--bg); color: var(--fg);
          font-size: 13px; font-family: Arial, sans-serif; }}
  h1 {{ font-size: 16px; margin: 0 0 4px; }}
  .scope {{ color: var(--muted); margin: 0 0 12px; }}
  .card {{ background: var(--panel); border: 1px solid var(--line);
           padding: 12px; margin-bottom: 12px; }}
  .muted {{ color: var(--muted); }}
  .ctx-hist {{ width: 100%; height: auto; }}
  .axis {{ stroke: var(--grid); }}
  .bar {{ fill: var(--accent2); }}
  .bar:hover {{ fill: var(--accent); }}
  .tick {{ font-size: 10px; fill: var(--muted); }}
  .pctl-acc {{ stroke: var(--accent); }}
  .pctl-good {{ stroke: var(--good); }}
  .pctl-acc2, .pctl-navy {{ stroke: var(--navy); }}
  .pctl-bad {{ stroke: var(--bad); }}
  .pctl.acc {{ fill: var(--accent); }}
  .pctl.good {{ fill: var(--good); }}
  .pctl.acc2, .pctl.navy {{ fill: var(--navy); }}
  .pctl.bad {{ fill: var(--bad); }}
  .tag {{ background: var(--yellow); font-size: 10px; padding: 1px 4px;
          border: 1px solid var(--line); }}
  dl {{ display: grid; grid-template-columns: max-content 1fr; gap: 4px 12px; }}
  dt {{ color: var(--muted); }}  dd {{ margin: 0; }}
  .stack .seg.c1 {{ fill: var(--accent); }}
  .stack .seg.c2 {{ fill: var(--accent2); }}
  .stack .seg.c3 {{ fill: var(--good); }}
  .stack .seg.c4 {{ fill: var(--navy); }}
  .stack .seg.c5 {{ fill: var(--yellow); }}
  .stack .seg.c6 {{ fill: var(--muted); }}
  .legend .lg {{ margin-right: 10px; font-size: 11px; }}
  .legend .sw {{ display: inline-block; width: 10px; height: 10px;
                 border: 1px solid var(--line); vertical-align: -1px;
                 margin-right: 3px; }}
  .legend .sw.c1 {{ background: var(--accent); }}
  .legend .sw.c2 {{ background: var(--accent2); }}
  .legend .sw.c3 {{ background: var(--good); }}
  .legend .sw.c4 {{ background: var(--navy); }}
  .legend .sw.c5 {{ background: var(--yellow); }}
  .legend .sw.c6 {{ background: var(--muted); }}
  .ctx-table {{ border-collapse: collapse; width: 100%; }}
  .ctx-table th, .ctx-table td {{ border: 1px solid var(--line);
                                  padding: 3px 8px; text-align: left; }}
  .ctx-table th {{ background: var(--panel); color: var(--muted); }}
  /* tabs una vista a la vez a todo ancho (coffe-r3c 2.8, patrón fpa) */
  .topbar {{ display: flex; gap: 4px; flex-wrap: wrap; margin: 0 0 12px;
             border: 1px solid var(--line); background: var(--panel);
             padding: 4px; }}
  .tab {{ display: inline-block; padding: 6px 12px; min-height: 44px;
          line-height: 32px; text-decoration: none; color: var(--fg);
          border: 1px solid var(--line); background: var(--card);
          font-size: .85rem; }}
  .tab[aria-current="true"] {{ background: var(--accent); color: #fff; }}
  .ctx-view {{ display: none; }}
  body[data-view="dist"] #dist,
  body[data-view="comp"] #comp,
  body[data-view="time"] #time,
  body[data-view="cost"] #cost,
  body[data-view="ley"] #ley,
  body[data-view="pat"] #pat {{ display: block; }}
  .scatter {{ width: 340px; max-width: 100%; height: auto; }}
  .scatter .axis {{ stroke: var(--grid); }}
  .scatter .dot {{ fill: var(--accent2); }}
  .scatter .dot:hover {{ fill: var(--accent); }}
  .sc-wrap {{ margin-bottom: 10px; }}
  .export-csv, .dl-svg {{ font-size: .8rem; background: var(--card);
                          border: 1px solid var(--line); padding: 4px 8px;
                          margin: 4px 4px 8px 0; cursor: pointer; }}
</style>
</head>
<body data-view="dist">
<h1>Contexto de sesiones (Pi)</h1>
<p class="scope">{scope}</p>
<nav class="topbar" role="tablist">{nav}</nav>
{dist_html}
{comp_html}
{time_html}
{cost_html}
{ley_html}
{pat_html}
<div class="card">
  <h2>Procedencia</h2>
  <dl>
    <dt>Set limpio</dt><dd>{n_clean} sesiones (turns > 0, sin
      patológicas) — base de histograma y percentiles</dd>
    <dt>Excluidas</dt><dd>{exc} patológicas (bloque assistant > umbral
      de config), visibles en la vista Patologías</dd>
    <dt>Stubs</dt><dd>{stubs} sesiones sin turnos — nunca sumadas como 0
      (loss visible)</dd>
    <dt>Factor</dt><dd>chars_per_token {cpt} — <span class="tag">assumed</span>,
      dirección de bias documentada</dd>
  </dl>
</div>
<script>
(function () {{
  var VIEWS = {views_json};
  function setView(key) {{
    if (VIEWS.indexOf(key) < 0) return;
    document.body.setAttribute("data-view", key);
    document.querySelectorAll(".tab").forEach(function (t) {{
      if (t.getAttribute("data-view") === key)
        t.setAttribute("aria-current", "true");
      else t.removeAttribute("aria-current");
    }});
  }}
  function fromHash() {{
    var h = (location.hash || "").replace("#", "");
    setView(h || "dist");
  }}
  document.querySelectorAll(".tab").forEach(function (t) {{
    t.addEventListener("click", function () {{
      setView(t.getAttribute("data-view"));
    }});
  }});
  window.addEventListener("hashchange", fromHash);
  fromHash();
  // export CSV (tablas) / SVG (charts) — sin librerías (FPA-169 patrón)
  function tableToCSV(table) {{
    return Array.prototype.slice.call(table.querySelectorAll("tr"))
      .map(function (tr) {{
        return Array.prototype.slice.call(tr.querySelectorAll("th,td"))
          .map(function (c) {{
            var t = c.textContent.replace(/\\s+/g, " ").trim();
            return /[",\\n]/.test(t) ? '"' + t.replace(/"/g, '""') + '"' : t;
          }}).join(",");
      }}).join("\\n");
  }}
  function downloadText(name, text, mime) {{
    var a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], {{type: mime}}));
    a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
  }}
  document.querySelectorAll(".ctx-table").forEach(function (t) {{
    var b = document.createElement("button");
    b.className = "export-csv"; b.type = "button";
    b.textContent = "Exportar CSV";
    b.addEventListener("click", function () {{
      downloadText("ctx-tabla.csv", tableToCSV(t), "text/csv");
    }});
    t.parentNode.insertBefore(b, t);
  }});
  document.querySelectorAll(".ctx-hist, .scatter, .stack").forEach(function (s) {{
    var b = document.createElement("button");
    b.className = "dl-svg"; b.type = "button";
    b.textContent = "Descargar SVG";
    b.addEventListener("click", function () {{
      downloadText("ctx-chart.svg",
        '<?xml version="1.0" encoding="UTF-8"?>\\n' + s.outerHTML,
        "image/svg+xml");
    }});
    s.parentNode.insertBefore(b, s);
  }});
  window.__ctx = {{ setView: setView }};
}})();
</script>
</body></html>"""


def main():
    report = json.loads(REPORT.read_text())
    model = build_model(report)
    OUT.write_text(render(model))
    if not model.get("available"):
        print(f"viz-ctx: data/context-dashboard.html — placeholder: "
              f"{model.get('reason')}")
        return
    d = model["dist"]
    print(f"viz-ctx: data/context-dashboard.html — {d['n_clean']} sesiones "
          f"limpias, {d['excluded_pathological']} excluidas, "
          f"{d['stubs']} stubs")


if __name__ == "__main__":
    main()
